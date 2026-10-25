"""Pipeline, activity, funnel reports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from ulid import ULID

from disha.db import admin_session
from disha.db.models import Event, LeadStateRow, LLMCall, Thread, Touch
from disha.db.models.enums import Channel, Direction, Tier
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.reports import (
    get_activity,
    get_funnel,
    get_pipeline_snapshot,
)
from tests._factories import ensure_demo_tenant, insert_fixture_lead, load_fixture_leads

pytestmark = pytest.mark.asyncio


async def _add_state(
    tenant_id: Any, lead_id: Any, state: LeadStateEnum, tier: Tier | None = None
) -> None:
    async with admin_session() as session:
        session.add(LeadStateRow(lead_id=lead_id, tenant_id=tenant_id, state=state, tier=tier))


async def test_pipeline_snapshot_counts_by_state(fixtures_dir: Any) -> None:
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)
    # 3 leads at varied states
    a = await insert_fixture_lead(tenant_id, leads[0])
    b = await insert_fixture_lead(tenant_id, leads[1])
    c = await insert_fixture_lead(tenant_id, leads[2])
    await _add_state(tenant_id, a, LeadStateEnum.SCORED, Tier.A)
    await _add_state(tenant_id, b, LeadStateEnum.SCORED, Tier.B)
    await _add_state(tenant_id, c, LeadStateEnum.IN_SEQUENCE, Tier.A)

    async with admin_session() as session:
        snap = await get_pipeline_snapshot(session, tenant_id)
    assert snap.by_state["scored"] == 2
    assert snap.by_state["in_sequence"] == 1
    assert snap.active_in_sequence == 1
    assert snap.pending_approval == 2


async def test_activity_counts_touches_sent(fixtures_dir: Any) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    now = datetime.now(UTC)
    period_start = now - timedelta(days=1)
    period_end = now + timedelta(hours=1)

    async with admin_session() as session:
        thread = Thread(tenant_id=tenant_id, lead_id=lead_id, channel=Channel.EMAIL, status="open")
        session.add(thread)
        await session.flush()
        # 2 in window, 1 outside
        for ts in (now - timedelta(hours=2), now - timedelta(hours=1)):
            session.add(
                Touch(
                    tenant_id=tenant_id,
                    thread_id=thread.id,
                    step_number=0,
                    direction=Direction.OUTBOUND,
                    channel=Channel.EMAIL,
                    status="sent",
                    body="b",
                    drafted_at=ts,
                    sent_at=ts,
                )
            )
        session.add(
            Touch(
                tenant_id=tenant_id,
                thread_id=thread.id,
                step_number=1,
                direction=Direction.OUTBOUND,
                channel=Channel.EMAIL,
                status="sent",
                body="b",
                drafted_at=now - timedelta(days=10),
                sent_at=now - timedelta(days=10),
            )
        )

    async with admin_session() as session:
        activity = await get_activity(session, tenant_id, period_start, period_end)
    assert activity.touches_sent == 2


async def test_activity_aggregates_llm_cost(fixtures_dir: Any) -> None:
    tenant_id = await ensure_demo_tenant()
    now = datetime.now(UTC)
    period_start = now - timedelta(hours=2)
    period_end = now + timedelta(hours=1)

    async with admin_session() as session:
        for cost in (Decimal("0.000010"), Decimal("0.000020"), Decimal("0.000030")):
            session.add(
                LLMCall(
                    tenant_id=tenant_id,
                    purpose="score.role_fit",
                    prompt_version=1,
                    provider="mock",
                    model="mock-model",
                    tokens_in=10,
                    tokens_out=5,
                    cost_usd=cost,
                    latency_ms=1,
                    success=True,
                )
            )

    async with admin_session() as session:
        activity = await get_activity(session, tenant_id, period_start, period_end)
    assert activity.llm_calls == 3
    assert activity.llm_cost_usd == Decimal("0.000060")
    assert activity.llm_cost_by_purpose["score.role_fit"] == Decimal("0.000060")


async def test_funnel_handles_zero_division() -> None:
    tenant_id = await ensure_demo_tenant()
    async with admin_session() as session:
        funnel = await get_funnel(session, tenant_id)
    assert funnel.sourced_to_scored == 0.0
    assert funnel.overall_sourced_to_booked == 0.0


# unused import keepers
_ = Event
_ = ULID
