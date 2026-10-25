"""Cost forecast: averages + per-lead projection + phase breakdown."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from disha.cost import get_cost_forecast
from disha.db import admin_session
from disha.db.models import LLMCall
from tests._factories import ensure_demo_tenant

pytestmark = pytest.mark.asyncio


async def test_forecast_zero_calls_returns_zeros() -> None:
    tenant_id = await ensure_demo_tenant()
    async with admin_session() as session:
        forecast = await get_cost_forecast(session, tenant_id, lookback_days=7)
    assert forecast.observed_calls == 0
    assert forecast.observed_unique_leads == 0
    assert forecast.avg_daily_cost_usd == Decimal("0")
    assert forecast.avg_cost_per_lead_lifecycle_usd == Decimal("0")
    assert forecast.projected_weekly_at_n_leads[10] == Decimal("0.00")


async def test_forecast_computes_daily_average_and_per_lead() -> None:
    tenant_id = await ensure_demo_tenant()
    now = datetime.now(UTC)
    lead_a = uuid4()
    lead_b = uuid4()

    # 4 calls in last 7 days; 2 for lead_a (cost 0.001 each) + 2 for lead_b (0.002 each)
    async with admin_session() as session:
        for entity_id, cost, purpose in [
            (lead_a, Decimal("0.001000"), "score.role_fit"),
            (lead_a, Decimal("0.001000"), "score.pain_signal"),
            (lead_b, Decimal("0.002000"), "score.role_fit"),
            (lead_b, Decimal("0.002000"), "draft.first_touch.IN"),
        ]:
            session.add(
                LLMCall(
                    tenant_id=tenant_id,
                    purpose=purpose,
                    prompt_version=1,
                    provider="mock",
                    model="mock-model",
                    tokens_in=50,
                    tokens_out=10,
                    cost_usd=cost,
                    latency_ms=10,
                    entity_type="lead",
                    entity_id=entity_id,
                    success=True,
                )
            )
        # Backdate to 2 days ago
        await session.execute(
            text("UPDATE llm_calls SET created_at = :ts WHERE tenant_id = :tid"),
            {"ts": now - timedelta(days=2), "tid": str(tenant_id)},
        )

    async with admin_session() as session:
        forecast = await get_cost_forecast(session, tenant_id, lookback_days=7)

    assert forecast.observed_calls == 4
    assert forecast.observed_unique_leads == 2
    # total $0.006, /7 days
    assert forecast.observed_total_usd == Decimal("0.006000")
    expected_daily = (Decimal("0.006") / Decimal("7")).quantize(Decimal("0.000001"))
    assert forecast.avg_daily_cost_usd == expected_daily
    # per lead: $0.006 / 2 = $0.003
    assert forecast.avg_cost_per_lead_lifecycle_usd == Decimal("0.003000")
    # 10 leads/week projection = $0.003 * 10 = $0.03
    assert forecast.projected_weekly_at_n_leads[10] == Decimal("0.03")


async def test_forecast_phase_breakdown_categorizes_purposes() -> None:
    tenant_id = await ensure_demo_tenant()

    async with admin_session() as session:
        for purpose, cost in [
            ("score.role_fit", Decimal("0.001")),
            ("draft.first_touch.IN", Decimal("0.002")),
            ("classify.reply", Decimal("0.003")),
        ]:
            session.add(
                LLMCall(
                    tenant_id=tenant_id,
                    purpose=purpose,
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
        forecast = await get_cost_forecast(session, tenant_id, lookback_days=7)

    assert forecast.breakdown_by_phase["scoring"] == Decimal("0.001")
    assert forecast.breakdown_by_phase["drafting"] == Decimal("0.002")
    assert forecast.breakdown_by_phase["classifying"] == Decimal("0.003")
