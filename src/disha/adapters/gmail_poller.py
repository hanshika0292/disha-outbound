"""Gmail reply poller.

Walks sent touches that have a gmail_thread_id but no replies, fetches each
thread, and inserts any messages we didn't send into the replies table.
Per-thread errors are logged + counted but never abort the batch.

Quoted-text stripping is best-effort — Gmail's reply quoting is unstable.
The classifier doesn't need the full thread; 2000 chars of fresh body is plenty.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from disha.adapters.gmail import GmailError, load_gmail_service
from disha.db.models import Event, Lead, PlatformSuppression, Reply, Thread, Touch
from disha.db.models.enums import SuppressionType

logger = logging.getLogger(__name__)


# Bounce / delivery-failure detection. These messages arrive in the same Gmail
# thread as the original send (from mailer-daemon/postmaster) and must NOT be
# treated as prospect replies — they pollute reply metrics, fire false "you got
# a reply" alerts, and leave a dead address in rotation.
_BOUNCE_SENDER_RE = re.compile(
    r"mailer-daemon|postmaster|mail delivery|delivery subsystem|no-?reply",
    re.IGNORECASE,
)
_BOUNCE_SUBJECT_RE = re.compile(
    r"delivery status notification|undeliverable|undelivered mail|"
    r"mail delivery (failed|subsystem)|failure notice|returned mail|"
    r"delivery (incomplete|has failed)",
    re.IGNORECASE,
)
_BOUNCE_BODY_RE = re.compile(
    r"address not found|wasn'?t delivered|couldn'?t be delivered|"
    r"delivery incomplete|delivery to the following|recipient.{0,20}not found|"
    r"undeliverable|mail delivery subsystem|550[ -]?5\.|status: 5\.\d\.\d|"
    r"does not exist|no such user|user unknown",
    re.IGNORECASE,
)


def _is_bounce(sender: str | None, subject: str | None, body: str | None) -> bool:
    if sender and _BOUNCE_SENDER_RE.search(sender):
        return True
    if subject and _BOUNCE_SUBJECT_RE.search(subject):
        return True
    if body and _BOUNCE_BODY_RE.search(body[:1500]):
        return True
    return False


_QUOTED_PATTERNS = [
    re.compile(r"\nOn .+ wrote:\s*\n", re.IGNORECASE),
    re.compile(r"\n-+ ?Original Message ?-+\n", re.IGNORECASE),
    re.compile(r"\n-+ ?Forwarded message ?-+\n", re.IGNORECASE),
    re.compile(r"\nFrom: .+\nSent: .+\n", re.IGNORECASE),
    re.compile(r"\n>+ ", re.MULTILINE),
]


_MAX_BODY_CHARS = 2000


class DiscoveredReply(BaseModel):
    touch_id: UUID
    gmail_message_id: str
    thread_id: str
    sender_email: str
    subject: str
    body: str
    received_at: datetime
    raw_headers: dict[str, str]


class PollResult(BaseModel):
    threads_checked: int = 0
    new_replies: int = 0
    already_seen: int = 0
    bounces: int = 0
    errors: int = 0
    reply_ids: list[UUID] = []
    duration_ms: int = 0


def _strip_quoted_text(body: str) -> str:
    """Truncate at the first occurrence of any common quoted-reply marker."""
    earliest: int | None = None
    for pattern in _QUOTED_PATTERNS:
        m = pattern.search(body)
        if m is not None and (earliest is None or m.start() < earliest):
            earliest = m.start()
    if earliest is not None:
        body = body[:earliest]
    return body.strip()[:_MAX_BODY_CHARS]


def _decode_part(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def _extract_plain_text_body(payload: dict[str, Any]) -> str:
    """Walk Gmail message payload recursively, prefer text/plain across the WHOLE
    tree before falling back to text/html. (A naive single-pass walker would
    return text/html when it appears before text/plain in the parts list.)
    """

    def _find(part: dict[str, Any], target_mime: str) -> str | None:
        if part.get("mimeType") == target_mime and (part.get("body") or {}).get("data"):
            return _decode_part(part["body"]["data"])
        for sub in part.get("parts", []) or []:
            found = _find(sub, target_mime)
            if found is not None:
                return found
        return None

    text = _find(payload, "text/plain")
    if text is None:
        html = _find(payload, "text/html")
        text = re.sub(r"<[^>]+>", "", html) if html else ""
    return _strip_quoted_text(text)


def _headers_to_dict(headers: list[dict[str, str]]) -> dict[str, str]:
    return {h.get("name", ""): h.get("value", "") for h in headers}


def _parse_received_at(date_header: str | None) -> datetime:
    if not date_header:
        return datetime.now(UTC)
    try:
        dt = parsedate_to_datetime(date_header)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (TypeError, ValueError):
        return datetime.now(UTC)


class GmailPoller:
    async def poll(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        *,
        max_threads: int = 50,
    ) -> PollResult:
        started = time.perf_counter()
        result = PollResult()

        # Touches sent via Gmail that don't yet have a reply (subquery for not exists).
        touches_query = (
            select(Touch)
            .where(
                Touch.tenant_id == tenant_id,
                Touch.status == "sent",
                Touch.gmail_thread_id.is_not(None),
                ~select(Reply.id)
                .where(
                    Reply.tenant_id == tenant_id,
                    Reply.touch_id == Touch.id,
                    Reply.gmail_message_id.is_not(None),
                )
                .exists(),
            )
            .order_by(Touch.sent_at.desc())
            .limit(max_threads)
        )
        touches = list((await session.execute(touches_query)).scalars().all())
        result.threads_checked = len(touches)

        if not touches:
            result.duration_ms = int((time.perf_counter() - started) * 1000)
            return result

        try:
            service = await load_gmail_service(session, tenant_id)
        except GmailError as exc:
            logger.warning("gmail unavailable", extra={"error": str(exc)})
            result.errors = len(touches)
            result.duration_ms = int((time.perf_counter() - started) * 1000)
            return result

        for touch in touches:
            try:
                await self._process_thread(session, tenant_id, touch, service, result)
            except Exception as exc:
                result.errors += 1
                logger.warning(
                    "poller thread failure",
                    extra={
                        "touch_id": str(touch.id),
                        "thread_id": touch.gmail_thread_id,
                        "error": str(exc),
                    },
                )

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    async def _process_thread(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        touch: Touch,
        service: Any,
        result: PollResult,
    ) -> None:
        thread_id = touch.gmail_thread_id
        assert thread_id is not None

        thread_resp = (
            service.users().threads().get(userId="me", id=thread_id, format="full").execute()
        )
        messages = thread_resp.get("messages", []) or []

        # All Disha-sent message IDs for this lead (across any thread) — exclusion set.
        thread_row = (
            await session.execute(select(Thread).where(Thread.id == touch.thread_id))
        ).scalar_one()
        own_ids = set(
            (
                await session.execute(
                    select(Touch.gmail_message_id)
                    .join(Thread, Thread.id == Touch.thread_id)
                    .where(
                        Touch.tenant_id == tenant_id,
                        Thread.lead_id == thread_row.lead_id,
                        Touch.gmail_message_id.is_not(None),
                    )
                )
            ).scalars()
        )

        for msg in messages:
            mid = msg.get("id")
            if not mid or mid in own_ids:
                continue
            await self._record_reply(
                session=session,
                tenant_id=tenant_id,
                touch=touch,
                thread_id=thread_id,
                msg=msg,
                result=result,
            )

    async def _record_reply(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        touch: Touch,
        thread_id: str,
        msg: dict[str, Any],
        result: PollResult,
    ) -> None:
        mid = msg["id"]

        # Pre-check existence (cheap; saves an IntegrityError-rollback path most of the time).
        existing = (
            await session.execute(
                select(Reply.id).where(Reply.tenant_id == tenant_id, Reply.gmail_message_id == mid)
            )
        ).scalar_one_or_none()
        if existing is not None:
            result.already_seen += 1
            return

        payload = msg.get("payload", {}) or {}
        headers = _headers_to_dict(payload.get("headers", []))
        body = _extract_plain_text_body(payload)
        received_at = _parse_received_at(headers.get("Date"))

        raw_headers = {
            "Message-ID": headers.get("Message-ID", ""),
            "In-Reply-To": headers.get("In-Reply-To", ""),
            "References": headers.get("References", ""),
        }

        sender = headers.get("From")
        subject = headers.get("Subject")
        bounce = _is_bounce(sender, subject, body)

        reply = Reply(
            tenant_id=tenant_id,
            touch_id=touch.id,
            received_at=received_at,
            body=body,
            sender_email=sender,
            subject=subject,
            gmail_message_id=mid,
            raw_headers=raw_headers,
            # Tag bounces so they're stored (for dedup) but excluded everywhere
            # a real reply matters (metrics, notifications, state transitions).
            classification={"intent": "bounce"} if bounce else None,
        )
        session.add(reply)
        try:
            await session.flush()
        except IntegrityError:
            # Race-safety net: UNIQUE(tenant_id, gmail_message_id) caught it.
            await session.rollback()
            result.already_seen += 1
            return

        if bounce:
            await self._handle_bounce(session, tenant_id, touch, reply)
            result.bounces += 1
            return

        result.new_replies += 1
        result.reply_ids.append(reply.id)
        session.add(
            Event(
                id=str(ULID()),
                tenant_id=tenant_id,
                entity_type="reply",
                entity_id=reply.id,
                event_type="reply.received",
                payload={
                    "touch_id": str(touch.id),
                    "thread_id": thread_id,
                    "sender": headers.get("From", ""),
                },
                actor="gmail_poller",
            )
        )

    async def _handle_bounce(
        self, session: AsyncSession, tenant_id: UUID, touch: Touch, reply: Reply
    ) -> None:
        """A delivery failure came back: mark the lead's email dead so we never
        send to it again, suppress it platform-wide, and log it. Best-effort."""
        import hashlib

        thread = (
            await session.execute(select(Thread).where(Thread.id == touch.thread_id))
        ).scalar_one_or_none()
        lead = (
            await session.execute(select(Lead).where(Lead.id == thread.lead_id))
        ).scalar_one_or_none() if thread else None
        if lead is not None:
            raw = dict(lead.raw or {})
            raw["email_confidence"] = "bounced"
            raw["email_verify"] = "bounced"
            lead.raw = raw
            if lead.email_hash:
                value_hash = hashlib.sha256(lead.email_hash).digest()
                exists = (
                    await session.execute(
                        select(PlatformSuppression.id).where(
                            PlatformSuppression.value_hash == value_hash
                        )
                    )
                ).scalar_one_or_none()
                if exists is None:
                    session.add(
                        PlatformSuppression(
                            value_hash=value_hash,
                            value_type=SuppressionType.EMAIL,
                            reason="hard bounce",
                        )
                    )
        session.add(
            Event(
                id=str(ULID()),
                tenant_id=tenant_id,
                entity_type="lead",
                entity_id=lead.id if lead else reply.id,
                event_type="email.bounced",
                payload={"touch_id": str(touch.id), "sender": reply.sender_email or ""},
                actor="gmail_poller",
            )
        )
