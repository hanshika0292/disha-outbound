"""Lead approval service: list, approve, reject, bulk."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from disha.db import admin_session
from disha.db.models import LeadStateRow
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.db.models.enums import Tier
from disha.lead_approval import (
    approve_lead,
    bulk_approve,
    list_leads_awaiting_approval,
    reject_lead,
)
from tests._factories import ensure_demo_tenant, insert_fixture_lead, load_fixture_leads

pytestmark = pytest.mark.asyncio


async def _seed_scored(
    tenant_id: Any, fixtures_dir: Path, id_tag: str, *, tier: Tier, score: Decimal
) -> Any:
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == id_tag)
    lead_id = await insert_fixture_lead(tenant_id, fixture)
    async with admin_session() as session:
        session.add(
            LeadStateRow(
                lead_id=lead_id,
                tenant_id=tenant_id,
                state=LeadStateEnum.SCORED,
                score=score,
                tier=tier,
            )
        )
    return lead_id


async def test_review_lists_scored_leads_ordered_by_tier(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    a = await _seed_scored(
        tenant_id, fixtures_dir, "A_IN_founder", tier=Tier.A, score=Decimal("0.85")
    )
    b = await _seed_scored(
        tenant_id, fixtures_dir, "B_IN_seniorpm", tier=Tier.B, score=Decimal("0.6")
    )
    c = await _seed_scored(
        tenant_id, fixtures_dir, "C_IN_ic_eng", tier=Tier.C, score=Decimal("0.4")
    )

    async with admin_session() as session:
        items = await list_leads_awaiting_approval(session, tenant_id)
    assert [it.lead_id for it in items] == [a, b, c]


async def test_review_filters_by_min_tier(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    await _seed_scored(tenant_id, fixtures_dir, "A_IN_founder", tier=Tier.A, score=Decimal("0.9"))
    await _seed_scored(tenant_id, fixtures_dir, "B_IN_seniorpm", tier=Tier.B, score=Decimal("0.6"))
    await _seed_scored(tenant_id, fixtures_dir, "C_IN_ic_eng", tier=Tier.C, score=Decimal("0.4"))

    async with admin_session() as session:
        items = await list_leads_awaiting_approval(session, tenant_id, min_tier="B")
    tiers = {it.tier for it in items}
    assert "C" not in tiers
    assert tiers == {"A", "B"}


async def test_approve_transitions_state(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    lid = await _seed_scored(
        tenant_id, fixtures_dir, "A_IN_founder", tier=Tier.A, score=Decimal("0.9")
    )

    async with admin_session() as session:
        await approve_lead(session, tenant_id, lid)

    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lid))
        ).scalar_one()
        assert state.state == LeadStateEnum.APPROVED


async def test_reject_transitions_and_records_reason(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    lid = await _seed_scored(
        tenant_id, fixtures_dir, "A_IN_founder", tier=Tier.A, score=Decimal("0.9")
    )

    async with admin_session() as session:
        await reject_lead(session, tenant_id, lid, reason="wrong_industry")

    async with admin_session() as session:
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lid))
        ).scalar_one()
        assert state.state == LeadStateEnum.REJECTED


async def test_bulk_approve_filters_by_tier(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    a = await _seed_scored(
        tenant_id, fixtures_dir, "A_IN_founder", tier=Tier.A, score=Decimal("0.9")
    )
    b = await _seed_scored(
        tenant_id, fixtures_dir, "B_IN_seniorpm", tier=Tier.B, score=Decimal("0.6")
    )
    c = await _seed_scored(
        tenant_id, fixtures_dir, "C_IN_ic_eng", tier=Tier.C, score=Decimal("0.4")
    )

    async with admin_session() as session:
        count = await bulk_approve(session, tenant_id, min_tier="B")
    assert count == 2

    async with admin_session() as session:
        rows = {
            lid: state.state
            for state, lid in [
                (
                    (
                        await session.execute(
                            select(LeadStateRow).where(LeadStateRow.lead_id == lid)
                        )
                    ).scalar_one(),
                    lid,
                )
                for lid in (a, b, c)
            ]
        }
    assert rows[a] == LeadStateEnum.APPROVED
    assert rows[b] == LeadStateEnum.APPROVED
    assert rows[c] == LeadStateEnum.SCORED  # C-tier untouched
