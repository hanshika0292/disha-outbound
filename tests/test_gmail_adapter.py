"""Gmail adapter tests with mocked google libs.

We inject lightweight stubs into sys.modules for google.* names that the
adapter lazily imports. Real google-auth etc. is installed at this point
(deps were added in Week 3), but we want the adapter's behavior — credential
loading, token refresh persistence, error mapping — testable without OAuth.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from disha.adapters.gmail import GmailAdapter, GmailError
from disha.crypto import encrypt
from disha.db import admin_session
from disha.db.models import Credential, Thread, Touch
from disha.db.models.enums import Channel, CredentialStatus, Direction
from tests._factories import ensure_demo_tenant, insert_fixture_lead, load_fixture_leads

pytestmark = pytest.mark.asyncio


def _install_google_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expired: bool = False,
    send_raises: Exception | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Patch the google.* names the adapter imports lazily."""
    creds_obj = MagicMock(name="Credentials")
    creds_obj.token = "fresh_token" if not expired else "old_token"
    creds_obj.refresh_token = "refresh_xyz"
    creds_obj.expired = expired
    refreshed_token = "newly_refreshed_token"

    def _refresh(_request: Any) -> None:
        creds_obj.token = refreshed_token
        creds_obj.expired = False

    creds_obj.refresh = MagicMock(side_effect=_refresh)

    credentials_module = MagicMock()
    credentials_module.Credentials.return_value = creds_obj

    request_module = MagicMock()
    request_module.Request = MagicMock()

    discovery_module = MagicMock()
    send_call = MagicMock()
    if send_raises is not None:
        send_call.execute.side_effect = send_raises
    else:
        send_call.execute.return_value = {"id": "msg-12345", "threadId": "thread-99"}
    service_chain = MagicMock()
    service_chain.users.return_value.messages.return_value.send.return_value = send_call
    discovery_module.build.return_value = service_chain

    errors_module = MagicMock()
    errors_module.HttpError = type("HttpError", (Exception,), {})

    google = MagicMock()
    google.oauth2 = MagicMock()
    google.oauth2.credentials = credentials_module
    google.auth = MagicMock()
    google.auth.transport = MagicMock()
    google.auth.transport.requests = request_module

    googleapiclient = MagicMock()
    googleapiclient.discovery = discovery_module
    googleapiclient.errors = errors_module

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.oauth2", google.oauth2)
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", credentials_module)
    monkeypatch.setitem(sys.modules, "google.auth", google.auth)
    monkeypatch.setitem(sys.modules, "google.auth.transport", google.auth.transport)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", request_module)
    monkeypatch.setitem(sys.modules, "googleapiclient", googleapiclient)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery_module)
    monkeypatch.setitem(sys.modules, "googleapiclient.errors", errors_module)

    return creds_obj, errors_module


async def _seed_gmail_credential(tenant_id: UUID, *, sender_email: str) -> None:
    payload = {
        "token": "old_token",
        "refresh_token": "refresh_xyz",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client123",
        "client_secret": "secret",
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        "sender_email": sender_email,
    }
    async with admin_session() as session:
        session.add(
            Credential(
                tenant_id=tenant_id,
                provider="gmail",
                encrypted_payload=encrypt(json.dumps(payload)),
                status=CredentialStatus.ACTIVE,
            )
        )


async def _seed_draft_touch(tenant_id: UUID, lead_id: UUID) -> UUID:
    async with admin_session() as session:
        thread = Thread(tenant_id=tenant_id, lead_id=lead_id, channel=Channel.EMAIL, status="open")
        session.add(thread)
        await session.flush()
        touch = Touch(
            tenant_id=tenant_id,
            thread_id=thread.id,
            step_number=0,
            direction=Direction.OUTBOUND,
            channel=Channel.EMAIL,
            status="draft",
            subject="Test subject",
            body="Hi there,\n\nThis is the body.",
            drafted_at=datetime.now(UTC),
        )
        session.add(touch)
        await session.flush()
        return touch.id


async def test_send_touch_raises_when_no_credential(fixtures_dir: Any) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)
    touch_id = await _seed_draft_touch(tenant_id, lead_id)

    adapter = GmailAdapter()
    async with admin_session() as session:
        with pytest.raises(GmailError, match="No active gmail credentials"):
            await adapter.send_touch(session, touch_id)


async def test_send_touch_success(fixtures_dir: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)
    touch_id = await _seed_draft_touch(tenant_id, lead_id)
    await _seed_gmail_credential(tenant_id, sender_email="demo@example.com")

    _install_google_stubs(monkeypatch, expired=False)

    adapter = GmailAdapter()
    async with admin_session() as session:
        result = await adapter.send_touch(session, touch_id)

    assert result.message_id == "msg-12345"
    assert result.thread_id == "thread-99"

    async with admin_session() as session:
        touch = (await session.execute(select(Touch).where(Touch.id == touch_id))).scalar_one()
        assert touch.status == "sent"
        assert touch.gmail_message_id == "msg-12345"
        assert touch.sent_at is not None


async def test_send_touch_refreshes_stale_token(
    fixtures_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)
    touch_id = await _seed_draft_touch(tenant_id, lead_id)
    await _seed_gmail_credential(tenant_id, sender_email="demo@example.com")

    creds_obj, _ = _install_google_stubs(monkeypatch, expired=True)

    adapter = GmailAdapter()
    async with admin_session() as session:
        await adapter.send_touch(session, touch_id)

    creds_obj.refresh.assert_called_once()

    async with admin_session() as session:
        cred = (
            await session.execute(select(Credential).where(Credential.tenant_id == tenant_id))
        ).scalar_one()
        from disha.crypto import decrypt as _decrypt

        payload = json.loads(_decrypt(cred.encrypted_payload).decode("utf-8"))
        assert payload["token"] == "newly_refreshed_token"


async def test_send_touch_raises_gmail_error_on_http_error(
    fixtures_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)
    touch_id = await _seed_draft_touch(tenant_id, lead_id)
    await _seed_gmail_credential(tenant_id, sender_email="demo@example.com")

    class FakeHttpError(Exception):
        status_code = 403
        reason = "Forbidden"

    _, errors_module = _install_google_stubs(
        monkeypatch, expired=False, send_raises=FakeHttpError()
    )
    # The adapter checks isinstance(exc, HttpError); we need the raised exception
    # to match the class the adapter imports.
    monkeypatch.setattr(errors_module, "HttpError", FakeHttpError, raising=False)

    adapter = GmailAdapter()
    async with admin_session() as session:
        with pytest.raises(GmailError, match="gmail send failed"):
            await adapter.send_touch(session, touch_id)

        touch = (await session.execute(select(Touch).where(Touch.id == touch_id))).scalar_one()
        # Adapter does NOT update touch on error — the sequencer is responsible
        # for flipping the touch to 'failed' with the error message.
        assert touch.status == "draft"


async def test_from_oauth_callback_persists_encrypted_payload() -> None:
    tenant_id = await ensure_demo_tenant()

    async with admin_session() as session:
        await GmailAdapter.from_oauth_callback(
            session,
            tenant_id,
            token="t0",
            refresh_token="r0",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="c",
            client_secret="s",
            scopes=["https://www.googleapis.com/auth/gmail.send"],
            sender_email="me@example.com",
        )

    async with admin_session() as session:
        cred = (
            await session.execute(
                select(Credential).where(
                    Credential.tenant_id == tenant_id,
                    Credential.provider == "gmail",
                )
            )
        ).scalar_one()
        from disha.crypto import decrypt as _decrypt

        payload = json.loads(_decrypt(cred.encrypted_payload).decode("utf-8"))
        assert payload["sender_email"] == "me@example.com"
        assert payload["token"] == "t0"


# unused but keeps the import for static analyzers
_ = uuid4
