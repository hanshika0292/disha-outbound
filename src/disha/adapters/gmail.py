"""Gmail send adapter.

Sends email via the Gmail API using OAuth credentials stored encrypted in the
`credentials` table (provider='gmail'). Lazy-imports google libs inside method
bodies so tests don't require the SDK — if no credential row exists, this
adapter raises GmailError before touching any google.* import.

Retries are intentionally NOT done here — the sequencer re-attempts on the next
run if a touch ends up in status='failed'. tenacity is only used at the LLM
router boundary.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.crypto import decrypt, encrypt
from disha.db.models import Credential, Lead, Touch
from disha.db.models.enums import CredentialStatus


class GmailError(Exception):
    """Base for all Gmail adapter failures."""


class GmailSendResult(BaseModel):
    message_id: str
    thread_id: str
    sent_at: datetime


async def _load_active_credential(session: AsyncSession, tenant_id: UUID) -> Credential:
    cred = (
        await session.execute(
            select(Credential).where(
                Credential.tenant_id == tenant_id,
                Credential.provider == "gmail",
                Credential.status == CredentialStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if cred is None:
        raise GmailError(
            "No active gmail credentials for this tenant. "
            "Run `disha gmail auth --tenant <slug>` first."
        )
    return cred


def _decrypt_payload(cred: Credential) -> dict[str, Any]:
    raw = decrypt(cred.encrypted_payload).decode("utf-8")
    payload: dict[str, Any] = json.loads(raw)
    return payload


async def _persist_refreshed_token(
    session: AsyncSession, cred: Credential, payload: dict[str, Any]
) -> None:
    cred.encrypted_payload = encrypt(json.dumps(payload))


async def load_gmail_service(session: AsyncSession, tenant_id: UUID) -> Any:
    """Shared helper for adapter + poller: load creds, refresh if stale, build service."""
    cred = await _load_active_credential(session, tenant_id)
    payload = _decrypt_payload(cred)

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise GmailError(f"google libraries not installed: {exc}") from exc

    gc = Credentials(  # type: ignore[no-untyped-call]
        token=payload.get("token"),
        refresh_token=payload.get("refresh_token"),
        token_uri=payload.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=payload.get("client_id"),
        client_secret=payload.get("client_secret"),
        scopes=payload.get("scopes", ["https://www.googleapis.com/auth/gmail.readonly"]),
    )
    if gc.expired and gc.refresh_token:
        try:
            gc.refresh(Request())  # type: ignore[no-untyped-call]
        except Exception as exc:
            raise GmailError(f"oauth refresh failed: {exc}") from exc
        payload["token"] = gc.token
        await _persist_refreshed_token(session, cred, payload)

    return build("gmail", "v1", credentials=gc, cache_discovery=False)


def _build_mime(
    *,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    in_reply_to: str | None = None,
) -> str:
    if html_body:
        # multipart/alternative — clients that can render HTML use it; the rest
        # fall back to the plain part. Keeps paragraph breaks + the signature.
        msg: Any = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    if in_reply_to:
        # RFC 2822 threading — keeps our reply in the prospect's email thread.
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


async def _load_settings_config(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    from disha.db.models import TenantSettings

    row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    return dict(row.config) if row and row.config else {}


class GmailAdapter:
    async def send_touch(self, session: AsyncSession, touch_id: UUID) -> GmailSendResult:
        touch = (await session.execute(select(Touch).where(Touch.id == touch_id))).scalar_one()
        # "queued" = approved draft handed back by the sequencer for sending.
        if touch.status not in ("draft", "failed", "queued"):
            raise GmailError(
                f"touch {touch_id} is in status={touch.status!r}; expected draft/failed/queued"
            )

        cred = await _load_active_credential(session, touch.tenant_id)
        payload = _decrypt_payload(cred)

        # Find recipient: load lead via thread.lead_id, decrypt the email.
        from disha.db.models import Thread

        thread = (
            await session.execute(select(Thread).where(Thread.id == touch.thread_id))
        ).scalar_one()
        lead = (await session.execute(select(Lead).where(Lead.id == thread.lead_id))).scalar_one()
        to_addr = decrypt(lead.email_encrypted).decode("utf-8")
        from_addr = payload.get("sender_email") or payload.get("client_id", "me")

        # Lazy import the google libs so tests don't need the SDK.
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
        except ImportError as exc:
            raise GmailError(f"google libraries not installed: {exc}") from exc

        gc = Credentials(  # type: ignore[no-untyped-call]
            token=payload.get("token"),
            refresh_token=payload.get("refresh_token"),
            token_uri=payload.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=payload.get("client_id"),
            client_secret=payload.get("client_secret"),
            scopes=payload.get("scopes", ["https://www.googleapis.com/auth/gmail.send"]),
        )

        if gc.expired and gc.refresh_token:
            try:
                gc.refresh(Request())  # type: ignore[no-untyped-call]
            except Exception as exc:
                raise GmailError(f"oauth refresh failed: {exc}") from exc
            payload["token"] = gc.token
            await _persist_refreshed_token(session, cred, payload)

        service = build("gmail", "v1", credentials=gc, cache_discovery=False)
        # Render a clean HTML version with a branded signature. Only outbound
        # EMAIL touches get this; LinkedIn/manual channels never reach here.
        from disha.email_format import render_email

        settings_config = await _load_settings_config(session, touch.tenant_id)
        plain_body, html_body = render_email(
            touch.body, settings_config, from_email=from_addr
        )
        raw_msg = _build_mime(
            from_addr=from_addr,
            to_addr=to_addr,
            subject=touch.subject or "(no subject)",
            body=plain_body,
            html_body=html_body,
        )

        try:
            sent = service.users().messages().send(userId="me", body={"raw": raw_msg}).execute()
        except HttpError as exc:
            raise GmailError(f"gmail send failed: {exc.status_code} {exc.reason}") from exc
        except Exception as exc:
            raise GmailError(f"gmail send failed: {exc}") from exc

        sent_at = datetime.now(UTC)
        touch.gmail_message_id = sent["id"]
        touch.gmail_thread_id = sent.get("threadId")
        touch.sent_at = sent_at
        touch.status = "sent"
        touch.error_message = None

        return GmailSendResult(
            message_id=sent["id"],
            thread_id=sent.get("threadId", ""),
            sent_at=sent_at,
        )

    async def send_raw(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        *,
        to_addr: str,
        subject: str,
        body: str,
        html_body: str | None = None,
        in_reply_to: str | None = None,
        gmail_thread_id: str | None = None,
    ) -> str:
        """Send an ad-hoc email (no touch row). Used by the weekly digest,
        notifications, welcome emails, and Inbox reply-sends.

        Pass `html_body` to send multipart/alternative (plain + HTML). Pass
        `in_reply_to` (the inbound message's Message-ID header) and
        `gmail_thread_id` to keep the send in the same conversation thread.
        Returns the Gmail message_id.
        """
        cred = await _load_active_credential(session, tenant_id)
        payload = _decrypt_payload(cred)
        from_addr = payload.get("sender_email") or payload.get("client_id", "me")

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
        except ImportError as exc:
            raise GmailError(f"google libraries not installed: {exc}") from exc

        gc = Credentials(  # type: ignore[no-untyped-call]
            token=payload.get("token"),
            refresh_token=payload.get("refresh_token"),
            token_uri=payload.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=payload.get("client_id"),
            client_secret=payload.get("client_secret"),
            scopes=payload.get("scopes", ["https://www.googleapis.com/auth/gmail.send"]),
        )

        if gc.expired and gc.refresh_token:
            try:
                gc.refresh(Request())  # type: ignore[no-untyped-call]
            except Exception as exc:
                raise GmailError(f"oauth refresh failed: {exc}") from exc
            payload["token"] = gc.token
            await _persist_refreshed_token(session, cred, payload)

        service = build("gmail", "v1", credentials=gc, cache_discovery=False)
        raw_msg = _build_mime(
            from_addr=from_addr,
            to_addr=to_addr,
            subject=subject,
            body=body,
            html_body=html_body,
            in_reply_to=in_reply_to,
        )
        send_body: dict[str, Any] = {"raw": raw_msg}
        if gmail_thread_id:
            send_body["threadId"] = gmail_thread_id
        try:
            sent = service.users().messages().send(userId="me", body=send_body).execute()
        except HttpError as exc:
            raise GmailError(f"gmail send failed: {exc.status_code} {exc.reason}") from exc
        except Exception as exc:
            raise GmailError(f"gmail send failed: {exc}") from exc
        message_id: str = sent["id"]
        return message_id

    @classmethod
    async def from_oauth_callback(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        *,
        token: str,
        refresh_token: str,
        token_uri: str,
        client_id: str,
        client_secret: str,
        scopes: list[str],
        sender_email: str,
    ) -> None:
        """Upsert encrypted gmail credentials for the tenant."""
        payload = {
            "token": token,
            "refresh_token": refresh_token,
            "token_uri": token_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": scopes,
            "sender_email": sender_email,
        }
        existing = (
            await session.execute(
                select(Credential).where(
                    Credential.tenant_id == tenant_id,
                    Credential.provider == "gmail",
                    Credential.status == CredentialStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.encrypted_payload = encrypt(json.dumps(payload))
        else:
            session.add(
                Credential(
                    tenant_id=tenant_id,
                    provider="gmail",
                    encrypted_payload=encrypt(json.dumps(payload)),
                    status=CredentialStatus.ACTIVE,
                )
            )
