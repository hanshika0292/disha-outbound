"""End-to-end: CSV import → score → approve → sequence → reply → state=replied.

Exercises the full pipeline with MockProvider for LLM (autouse fixture) and
a patched GmailAdapter.send_touch for outbound. The reply leg is simulated
by inserting a Reply row with a pre-populated classification — that's what
the Gmail poller would have done.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from disha.adapters.gmail import GmailSendResult
from disha.db import admin_session
from disha.db.models import LeadStateRow, Reply, Signal, Thread, Touch
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.ingestion import import_from_csv
from disha.lead_approval import approve_lead
from disha.llm import LLMRouter
from disha.reply_handler import handle_new_reply
from disha.sequencer.engine import run_sequence
from tests._factories import ensure_demo_tenant

pytestmark = pytest.mark.asyncio


_CSV_HEADER = (
    "first_name,last_name,email,title,company,country,"
    "linkedin_url,notes,source_url,funding_stage,employee_count\n"
)


def _wed_in_window() -> datetime:
    """Wed 2026-05-27 11:00 IST = 05:30 UTC — inside cadence first range [10, 12]."""
    return datetime(2026, 5, 27, 5, 30, tzinfo=UTC)


async def test_full_pipeline_lead_to_replied(tmp_path: Path) -> None:
    tenant_id = await ensure_demo_tenant()

    # --- Step 1: write CSV ---
    csv = tmp_path / "leads.csv"
    csv.write_text(
        _CSV_HEADER + "Aditya,Kumar,aditya@techstack.example,VP Engineering,Quillfern,IN,"
        ",AI infra bottlenecks,,series_a,28\n"
    )

    # --- Step 2: import + auto-score (MockProvider via autouse fixture) ---
    async with admin_session() as session:
        result = await import_from_csv(session, tenant_id, csv, auto_score=True)
    assert result.created == 1
    lead_id = result.lead_ids[0]

    # --- Step 3: verify signals were seeded ---
    async with admin_session() as session:
        signal_types = set(
            (
                await session.execute(
                    select(Signal.type).where(
                        Signal.tenant_id == tenant_id, Signal.lead_id == lead_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {"role_signal", "pain_signal", "funding_signal"} <= signal_types

    # --- Step 4: verify lead was scored (MockProvider returns A-tier for Aditya) ---
    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
    assert state.state == LeadStateEnum.SCORED
    assert state.tier is not None

    # --- Step 5: approve the lead ---
    async with admin_session() as session:
        await approve_lead(session, tenant_id, lead_id)

    # --- Step 6: run sequencer with patched Gmail send ---
    mock_message_id = "msg_abc123"
    mock_thread_id = "thread_xyz789"

    sent_at = datetime.now(UTC)

    async def fake_send_touch(self, session, touch_id):
        touch = (await session.execute(select(Touch).where(Touch.id == touch_id))).scalar_one()
        touch.status = "sent"
        touch.gmail_message_id = mock_message_id
        touch.gmail_thread_id = mock_thread_id
        touch.sent_at = sent_at
        return GmailSendResult(
            message_id=mock_message_id, thread_id=mock_thread_id, sent_at=sent_at
        )

    router = LLMRouter(tenant_id)
    with patch("disha.adapters.gmail.GmailAdapter.send_touch", new=fake_send_touch):
        async with admin_session() as session:
            seq = await run_sequence(session, tenant_id, router, now_utc=_wed_in_window())
    assert seq.sent >= 1

    # --- Step 7: touch is persisted with gmail_thread_id ---
    async with admin_session() as session:
        touch = (
            await session.execute(
                select(Touch)
                .join(Thread, Thread.id == Touch.thread_id)
                .where(Thread.lead_id == lead_id, Touch.status == "sent")
            )
        ).scalar_one()
    assert touch.gmail_thread_id == mock_thread_id
    assert touch.gmail_message_id == mock_message_id

    # --- Step 8: simulate an inbound reply (what the poller would insert) ---
    async with admin_session() as session:
        reply = Reply(
            tenant_id=tenant_id,
            touch_id=touch.id,
            received_at=datetime.now(UTC),
            body="Sure, happy to chat. Does Thursday 2pm PT work?",  # matches mock classify rule
            sender_email="aditya@techstack.example",
            subject="Re: Quick question",
            gmail_message_id="reply_msg_999",
            raw_headers={
                "Message-ID": "reply_msg_999",
                "In-Reply-To": mock_message_id,
            },
        )
        session.add(reply)
        await session.flush()
        reply_id = reply.id

    # --- Step 9: classify + state transition ---
    async with admin_session() as session:
        summary = await handle_new_reply(session, tenant_id, reply_id, router=router)

    # --- Step 10: lead state = replied ---
    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
        reply = (await session.execute(select(Reply).where(Reply.id == reply_id))).scalar_one()
    assert state.state == LeadStateEnum.REPLIED
    assert reply.classification is not None
    assert reply.classification["intent"] == "meeting_request"
    assert summary["action"] == "state=replied"

    # --- Step 11: sequencer no longer processes this lead ---
    async with admin_session() as session:
        seq2 = await run_sequence(session, tenant_id, router, now_utc=_wed_in_window())
    assert all(r.lead_id != lead_id for r in seq2.results), (
        "REPLIED lead should be filtered out of eligible list, not processed again"
    )


async def test_e2e_not_now_reply_pauses_sequence(tmp_path: Path) -> None:
    """A 'not_now' reply pauses the sequence for 14 days. Sequencer respects the pause."""
    tenant_id = await ensure_demo_tenant()

    # Set up an approved lead with a sent touch
    csv = tmp_path / "leads.csv"
    csv.write_text(
        _CSV_HEADER + "Marcus,Brennan,marcus@routewisp.example,Director of Product,"
        "Routewisp,US,,one ML job posting,,series_b,65\n"
    )
    async with admin_session() as session:
        result = await import_from_csv(session, tenant_id, csv, auto_score=True)
    lead_id = result.lead_ids[0]
    async with admin_session() as session:
        await approve_lead(session, tenant_id, lead_id)

    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
        state.state = LeadStateEnum.IN_SEQUENCE
        state.sequence_started_at = _wed_in_window() - timedelta(days=1)
        state.geo_assigned = "US"
        thread = Thread(
            tenant_id=tenant_id,
            lead_id=lead_id,
            channel=__import__("disha.db.models.enums", fromlist=["Channel"]).Channel.EMAIL,
            status="open",
        )
        session.add(thread)
        await session.flush()
        from disha.db.models.enums import Channel, Direction

        touch = Touch(
            tenant_id=tenant_id,
            thread_id=thread.id,
            step_number=0,
            direction=Direction.OUTBOUND,
            channel=Channel.EMAIL,
            status="sent",
            subject="initial",
            body="body",
            drafted_at=_wed_in_window() - timedelta(days=1),
            sent_at=_wed_in_window() - timedelta(days=1),
            gmail_thread_id="t_marcus",
            gmail_message_id="m_marcus",
        )
        session.add(touch)
        await session.flush()
        touch_id = touch.id

    # Inbound reply pre-classified as not_now (skip the LLM call by setting
    # classification directly).
    async with admin_session() as session:
        reply = Reply(
            tenant_id=tenant_id,
            touch_id=touch_id,
            received_at=datetime.now(UTC),
            body="Not now, maybe Q3.",
            sender_email="marcus@routewisp.example",
            subject="Re: initial",
            classification={
                "intent": "not_now",
                "confidence": 0.9,
                "reasoning": "test",
                "suggested_response_type": "wait",
                "extracted_entities": {},
            },
        )
        session.add(reply)
        await session.flush()
        reply_id = reply.id

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        await handle_new_reply(session, tenant_id, reply_id, router=router)

    # Sequencer should now defer this lead with reason='sequence_paused'.
    async with admin_session() as session:
        seq = await run_sequence(session, tenant_id, router, now_utc=_wed_in_window())
    paused = [r for r in seq.results if r.reason == "sequence_paused"]
    assert len(paused) == 1, [r.model_dump() for r in seq.results]
