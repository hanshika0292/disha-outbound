"""Gmail reply poller: unit tests for quoted-text stripping + DB integration tests."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy import select

from disha.adapters.gmail_poller import GmailPoller, _extract_plain_text_body, _strip_quoted_text
from disha.db import admin_session
from disha.db.models import Reply, Thread, Touch
from disha.db.models.enums import Channel, Direction
from tests._factories import ensure_demo_tenant, insert_fixture_lead, load_fixture_leads


def test_strip_quoted_text_handles_on_wrote() -> None:
    body = (
        "Thanks for reaching out, this looks great!\n\n"
        "On Mon, May 27, 2026 at 2:30 PM, Meera <meera@example.com> wrote:\n"
        "> Original message\n> More"
    )
    assert _strip_quoted_text(body) == "Thanks for reaching out, this looks great!"


def test_strip_quoted_text_handles_gt_prefix() -> None:
    body = "Looks interesting.\n\n> Hi Aditya,\n> Your post resonated"
    assert _strip_quoted_text(body) == "Looks interesting."


def test_strip_quoted_text_caps_length() -> None:
    body = "x" * 5000
    assert len(_strip_quoted_text(body)) == 2000


def test_extract_plain_text_body_walks_multipart() -> None:
    text = "Hello! This is the body."
    encoded = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")
    payload = {
        "mimeType": "multipart/alternative",
        "body": {},
        "parts": [
            {"mimeType": "text/html", "body": {"data": "PHA+SVI="}},
            {"mimeType": "text/plain", "body": {"data": encoded}},
        ],
    }
    assert _extract_plain_text_body(payload) == text


async def _setup_sent_touch(
    fixtures_dir: Any, *, thread_id: str = "t_xyz"
) -> tuple[UUID, UUID, UUID]:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

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
            status="sent",
            subject="initial",
            body="body",
            drafted_at=datetime.now(UTC),
            sent_at=datetime.now(UTC),
            gmail_thread_id=thread_id,
            gmail_message_id="m_own_1",
        )
        session.add(touch)
        await session.flush()
        return tenant_id, lead_id, touch.id


def _mock_gmail_service(reply_messages: list[dict[str, Any]]) -> MagicMock:
    service = MagicMock(name="gmail_service")
    thread_resp = {"messages": reply_messages}
    service.users.return_value.threads.return_value.get.return_value.execute.return_value = (
        thread_resp
    )
    return service


def _build_gmail_message(
    msg_id: str,
    *,
    sender: str,
    subject: str,
    body_text: str,
    date_str: str = "Wed, 27 May 2026 14:30:00 +0530",
) -> dict[str, Any]:
    encoded = base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii")
    return {
        "id": msg_id,
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": encoded},
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date_str},
                {"name": "Message-ID", "value": f"<{msg_id}@mail>"},
                {"name": "In-Reply-To", "value": "<m_own_1@mail>"},
            ],
        },
    }


_async_tests = pytest.mark.asyncio


@_async_tests
async def test_poller_creates_reply_on_new_message(fixtures_dir: Any) -> None:
    tenant_id, _lead_id, touch_id = await _setup_sent_touch(fixtures_dir)
    own_msg = _build_gmail_message(
        "m_own_1", sender="demo@example.com", subject="initial", body_text="hi"
    )
    reply_msg = _build_gmail_message(
        "m_reply_1",
        sender="lead@example.com",
        subject="Re: initial",
        body_text="Thanks, sounds interesting!",
    )
    service = _mock_gmail_service([own_msg, reply_msg])

    with patch(
        "disha.adapters.gmail_poller.load_gmail_service",
        new=AsyncMock(return_value=service),
    ):
        async with admin_session() as session:
            result = await GmailPoller().poll(session, tenant_id)

    assert result.threads_checked == 1
    assert result.new_replies == 1
    assert result.already_seen == 0
    async with admin_session() as session:
        reply = (
            await session.execute(select(Reply).where(Reply.touch_id == touch_id))
        ).scalar_one()
    assert reply.gmail_message_id == "m_reply_1"
    assert reply.sender_email == "lead@example.com"
    assert "Thanks, sounds interesting!" in reply.body


@_async_tests
async def test_poller_does_not_duplicate_existing_reply(fixtures_dir: Any) -> None:
    """A touch with an existing reply is filtered out of the eligible set entirely —
    so the poller never tries to re-process it. UNIQUE(tenant_id, gmail_message_id)
    is the final safety net for race conditions; this test verifies the primary
    eligibility-query guard.
    """
    tenant_id, _lead_id, touch_id = await _setup_sent_touch(fixtures_dir)

    async with admin_session() as session:
        session.add(
            Reply(
                tenant_id=tenant_id,
                touch_id=touch_id,
                received_at=datetime.now(UTC),
                body="previously recorded",
                gmail_message_id="m_already_seen",
            )
        )

    service = _mock_gmail_service([])  # won't be called

    with patch(
        "disha.adapters.gmail_poller.load_gmail_service",
        new=AsyncMock(return_value=service),
    ):
        async with admin_session() as session:
            result = await GmailPoller().poll(session, tenant_id)

    # Touch was filtered out by the eligibility query (already has a reply with gmail_message_id).
    assert result.threads_checked == 0
    assert result.new_replies == 0

    async with admin_session() as session:
        n_replies = len(
            list(
                (
                    await session.execute(
                        select(Reply).where(
                            Reply.tenant_id == tenant_id, Reply.touch_id == touch_id
                        )
                    )
                ).scalars()
            )
        )
    assert n_replies == 1, "must not insert a duplicate"


@_async_tests
async def test_poller_filters_out_own_sent_messages(fixtures_dir: Any) -> None:
    """The thread always contains our sent message; we must skip it."""
    tenant_id, _lead_id, _touch_id = await _setup_sent_touch(fixtures_dir)
    own_msg = _build_gmail_message("m_own_1", sender="me@x", subject="s", body_text="b")
    service = _mock_gmail_service([own_msg])  # no replies

    with patch(
        "disha.adapters.gmail_poller.load_gmail_service",
        new=AsyncMock(return_value=service),
    ):
        async with admin_session() as session:
            result = await GmailPoller().poll(session, tenant_id)
    assert result.threads_checked == 1
    assert result.new_replies == 0


@_async_tests
async def test_poller_handles_api_error_per_thread(fixtures_dir: Any) -> None:
    tenant_id, _, _ = await _setup_sent_touch(fixtures_dir, thread_id="t_one")
    _, _, _ = await _setup_sent_touch(fixtures_dir, thread_id="t_two")

    service = MagicMock(name="gmail_service")
    call_count = {"n": 0}

    def _execute_side_effect():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        return {
            "messages": [
                _build_gmail_message("m_own_1", sender="me@x", subject="s", body_text="b"),
                _build_gmail_message(
                    "m_reply_other",
                    sender="lead@x",
                    subject="Re",
                    body_text="ok",
                ),
            ]
        }

    service.users.return_value.threads.return_value.get.return_value.execute.side_effect = (
        _execute_side_effect
    )

    with patch(
        "disha.adapters.gmail_poller.load_gmail_service",
        new=AsyncMock(return_value=service),
    ):
        async with admin_session() as session:
            result = await GmailPoller().poll(session, tenant_id)

    assert result.errors >= 1
    assert result.new_replies == 1
