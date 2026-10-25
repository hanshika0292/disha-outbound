from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from disha.db import admin_session
from disha.db.models import LeadStateRow, LLMCall, PromptPack
from disha.intelligence import score_lead
from disha.llm import LLMRouter
from tests._factories import (
    ensure_demo_tenant,
    insert_fixture_lead,
    load_fixture_leads,
)

pytestmark = pytest.mark.asyncio


async def test_each_fixture_lead_scores_to_expected_tier(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)

    router = LLMRouter(tenant_id)
    for fixture in leads:
        lead_id = await insert_fixture_lead(tenant_id, fixture)
        async with admin_session() as session:
            result = await score_lead(session, tenant_id, lead_id, router)
        assert result.tier == fixture["expected_tier"], (
            f"{fixture['id_tag']}: expected tier={fixture['expected_tier']} "
            f"got tier={result.tier} score={result.total_score:.3f}\n"
            f"breakdown={[(b.dimension, b.raw_score) for b in result.breakdown]}"
        )


async def test_scorer_writes_lead_state_and_llm_calls(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)
    fixture = next(L for L in leads if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        await score_lead(session, tenant_id, lead_id, router)

    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
        prompts = (
            await session.execute(
                select(PromptPack).where(
                    PromptPack.tenant_id == tenant_id, PromptPack.is_active.is_(True)
                )
            )
        ).scalar_one()
        assert state.score is not None
        assert state.tier is not None
        assert state.scored_with_prompt_version == prompts.version

        calls = list(
            (await session.execute(select(LLMCall).where(LLMCall.entity_id == lead_id))).scalars()
        )
        purposes = {c.purpose for c in calls}
        assert {"score.role_fit", "score.pain_signal"}.issubset(purposes)
        assert all(c.success for c in calls)


async def test_blocked_geo_short_circuits_to_skip(fixtures_dir: Path) -> None:
    """Leads in geo_profile.blocked must short-circuit to tier=skip without any LLM call.

    This is a real safety control — even if ingestion misses a blocked country,
    scoring won't waste tokens evaluating an unmailable lead.
    """
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)
    fixture = next(L for L in leads if L["id_tag"] == "skip_blocked_geo")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        result = await score_lead(session, tenant_id, lead_id, router)

    assert result.tier == "skip"
    assert result.total_score == 0.0
    assert len(result.breakdown) == 1
    assert result.breakdown[0].dimension == "_skip"
    assert "blocked_geo" in (result.breakdown[0].reasoning or "")

    async with admin_session() as session:
        calls = list(
            (await session.execute(select(LLMCall).where(LLMCall.entity_id == lead_id))).scalars()
        )
    assert calls == [], f"blocked-geo lead should not trigger LLM calls; got {len(calls)}"
