"""Reply intent → state transition behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from disha.db import admin_session
from disha.db.models import LeadStateRow, Reply, Signal, Thread, Touch
from disha.db.models.enums import Channel, Direction
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.llm import LLMRouter
from disha.reply_handler import handle_new_reply
from disha.sequencer.engine import run_sequence
from tests._factories import ensure_demo_tenant, insert_fixture_lead, load_fixture_leads

pytestmark = pytest.mark.asyncio


async def _setup_lead_with_sent_touch(
    fixtures_dir: Any, id_tag: str = "A_IN_founder"
) -> tuple[Any, Any, Any]:
    """Returns (tenant_id, lead_id, touch_id) with a 'sent' touch ready for reply."""
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == id_tag)
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    async with admin_session() as session:
        session.add(
            LeadStateRow(
                lead_id=lead_id,
                tenant_id=tenant_id,
                state=LeadStateEnum.IN_SEQUENCE,
                geo_assigned="IN",
                sequence_started_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
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
            gmail_thread_id="t_abc",
            gmail_message_id="m_abc",
        )
        session.add(touch)
        await session.flush()
        return tenant_id, lead_id, touch.id


async def _insert_reply_with_classification(
    tenant_id: Any,
    touch_id: Any,
    *,
    intent: str,
    confidence: float = 0.95,
    body: str = "...",
    gmail_msg: str | None = None,
) -> Any:
    async with admin_session() as session:
        reply = Reply(
            tenant_id=tenant_id,
            touch_id=touch_id,
            received_at=datetime.now(UTC),
            body=body,
            sender_email="lead@example.com",
            subject="Re: initial",
            gmail_message_id=gmail_msg,
            classification={
                "intent": intent,
                "confidence": confidence,
                "reasoning": "test",
                "suggested_response_type": "wait",
                "extracted_entities": {},
            },
        )
        session.add(reply)
        await session.flush()
        return reply.id


async def test_interested_reply_sets_state_replied(fixtures_dir: Any) -> None:
    tenant_id, lead_id, touch_id = await _setup_lead_with_sent_touch(fixtures_dir)
    reply_id = await _insert_reply_with_classification(tenant_id, touch_id, intent="interested")

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        await handle_new_reply(session, tenant_id, reply_id, router=router)

    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
    assert state.state == LeadStateEnum.REPLIED


async def test_meeting_request_sets_state_replied(fixtures_dir: Any) -> None:
    tenant_id, lead_id, touch_id = await _setup_lead_with_sent_touch(fixtures_dir)
    reply_id = await _insert_reply_with_classification(
        tenant_id, touch_id, intent="meeting_request"
    )

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        await handle_new_reply(session, tenant_id, reply_id, router=router)
    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
    assert state.state == LeadStateEnum.REPLIED


async def test_not_now_sets_14_day_pause(fixtures_dir: Any) -> None:
    tenant_id, lead_id, touch_id = await _setup_lead_with_sent_touch(fixtures_dir)
    reply_id = await _insert_reply_with_classification(tenant_id, touch_id, intent="not_now")

    router = LLMRouter(tenant_id)
    before = datetime.now(UTC)
    async with admin_session() as session:
        await handle_new_reply(session, tenant_id, reply_id, router=router)

    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()

    assert state.sequence_paused_until is not None
    delta = state.sequence_paused_until - before
    assert timedelta(days=13, hours=23) < delta < timedelta(days=14, hours=1)


async def test_out_of_office_sets_5_day_pause(fixtures_dir: Any) -> None:
    tenant_id, lead_id, touch_id = await _setup_lead_with_sent_touch(fixtures_dir)
    reply_id = await _insert_reply_with_classification(tenant_id, touch_id, intent="out_of_office")

    router = LLMRouter(tenant_id)
    before = datetime.now(UTC)
    async with admin_session() as session:
        await handle_new_reply(session, tenant_id, reply_id, router=router)

    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
    assert state.sequence_paused_until is not None
    delta = state.sequence_paused_until - before
    assert timedelta(days=4, hours=23) < delta < timedelta(days=5, hours=1)


async def test_referral_adds_signal_no_state_change(fixtures_dir: Any) -> None:
    tenant_id, lead_id, touch_id = await _setup_lead_with_sent_touch(fixtures_dir)
    reply_id = await _insert_reply_with_classification(
        tenant_id, touch_id, intent="referral", body="Try our COO instead, ops@x.com"
    )

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        await handle_new_reply(session, tenant_id, reply_id, router=router)

    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
        sigs = list(
            (
                await session.execute(
                    select(Signal).where(
                        Signal.tenant_id == tenant_id,
                        Signal.lead_id == lead_id,
                        Signal.type == "referral_signal",
                    )
                )
            )
            .scalars()
            .all()
        )

    assert state.state == LeadStateEnum.IN_SEQUENCE  # unchanged
    assert len(sigs) == 1


async def test_unsubscribe_confirms_state(fixtures_dir: Any) -> None:
    tenant_id, lead_id, touch_id = await _setup_lead_with_sent_touch(fixtures_dir)
    reply_id = await _insert_reply_with_classification(
        tenant_id,
        touch_id,
        intent="unsubscribe_or_not_interested",
        body="Please remove me",
    )

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        await handle_new_reply(session, tenant_id, reply_id, router=router)

    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
    assert state.state == LeadStateEnum.UNSUBSCRIBED


async def test_low_confidence_emits_review_event_no_state_change(fixtures_dir: Any) -> None:
    tenant_id, lead_id, touch_id = await _setup_lead_with_sent_touch(fixtures_dir)
    reply_id = await _insert_reply_with_classification(
        tenant_id, touch_id, intent="positive_vague", confidence=0.3
    )

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        summary = await handle_new_reply(session, tenant_id, reply_id, router=router)

    assert summary["action"] == "low_confidence_review"
    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
    assert state.state == LeadStateEnum.IN_SEQUENCE


async def test_sequencer_skips_paused_lead(fixtures_dir: Any) -> None:
    tenant_id, lead_id, _touch_id = await _setup_lead_with_sent_touch(fixtures_dir)
    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
        state.sequence_paused_until = datetime.now(UTC) + timedelta(days=7)

    router = LLMRouter(tenant_id)
    now = datetime(2026, 5, 27, 5, 30, tzinfo=UTC)  # 11:00 IST, inside window
    async with admin_session() as session:
        run = await run_sequence(session, tenant_id, router, now_utc=now)

    assert run.deferred == 1
    assert run.results[0].reason == "sequence_paused"


async def test_sequencer_resumes_after_pause_expires(fixtures_dir: Any) -> None:
    """sequence_paused_until in the past → lead is processed normally (defer guard
    uses strictly-greater-than, so an expired pause does not defer)."""
    tenant_id, lead_id, _touch_id = await _setup_lead_with_sent_touch(fixtures_dir)
    now = datetime(2026, 5, 27, 5, 30, tzinfo=UTC)  # 11:00 IST
    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
        state.sequence_paused_until = now - timedelta(days=1)

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        run = await run_sequence(session, tenant_id, router, now_utc=now)

    # The lead should be processed (not skipped by pause). The step 0 touch is
    # already 'sent' from setup, so the next step (linkedin at day 3) is the
    # next candidate. Either deferred (not due yet) or queued — either way, NOT
    # deferred with reason 'sequence_paused'.
    assert all(r.reason != "sequence_paused" for r in run.results)


async def test_sequencer_skips_replied_lead(fixtures_dir: Any) -> None:
    tenant_id, lead_id, _touch_id = await _setup_lead_with_sent_touch(fixtures_dir)
    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
        state.state = LeadStateEnum.REPLIED

    router = LLMRouter(tenant_id)
    now = datetime(2026, 5, 27, 5, 30, tzinfo=UTC)
    async with admin_session() as session:
        run = await run_sequence(session, tenant_id, router, now_utc=now)
    assert run.processed == 0  # filtered out by eligibility query
