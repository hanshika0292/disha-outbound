from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from disha.db import admin_session
from disha.db.models import Touch
from disha.intelligence import draft_touch
from disha.llm import LLMRouter
from tests._factories import (
    ensure_demo_tenant,
    insert_fixture_lead,
    load_fixture_leads,
)

pytestmark = pytest.mark.asyncio


async def test_draft_first_touch_in_geo_uses_hi_greeting(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)
    fixture = next(L for L in leads if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        result = await draft_touch(session, tenant_id, lead_id, 1, router)

    assert result.body.startswith("Hi Aditya"), result.body
    assert result.channel == "email"
    assert result.word_count <= 110
    assert not result.needs_human_review


async def test_draft_first_touch_us_geo_uses_hey_greeting(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)
    fixture = next(L for L in leads if L["id_tag"] == "A_US_vpe")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        result = await draft_touch(session, tenant_id, lead_id, 1, router)

    assert result.body.startswith("Hey Jordan"), result.body
    assert result.channel == "email"
    assert result.word_count <= 90


async def test_draft_persists_touch_row(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)
    fixture = next(L for L in leads if L["id_tag"] == "A_US_vpe")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        result = await draft_touch(session, tenant_id, lead_id, 1, router)

    async with admin_session() as session:
        touch = (
            await session.execute(select(Touch).where(Touch.id == result.touch_id))
        ).scalar_one()
        assert touch.direction.value == "outbound"
        assert touch.body == result.body
        assert touch.cost_usd is not None
