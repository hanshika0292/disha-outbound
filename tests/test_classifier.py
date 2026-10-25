from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from disha.db import admin_session
from disha.db.models import PlatformSuppression, Reply
from disha.intelligence import classify_reply
from disha.llm import LLMRouter
from tests._factories import (
    ensure_demo_tenant,
    insert_fixture_lead,
    insert_fixture_reply,
    load_fixture_leads,
    load_fixture_replies,
)

pytestmark = pytest.mark.asyncio


def _lead_for_geo(leads: list[dict], geo: str) -> dict:
    return next(L for L in leads if L["geo"] == geo)


async def test_each_fixture_reply_classifies_to_expected_intent(
    fixtures_dir: Path,
) -> None:
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)
    replies = load_fixture_replies(fixtures_dir)

    router = LLMRouter(tenant_id)
    for reply_fix in replies:
        lead_fix = _lead_for_geo(leads, reply_fix["lead_geo"])
        lead_id = await insert_fixture_lead(tenant_id, lead_fix)
        reply_id = await insert_fixture_reply(tenant_id, lead_id, reply_fix)

        async with admin_session() as session:
            result = await classify_reply(session, tenant_id, reply_id, router)

        assert result.intent == reply_fix["expected_intent"], (
            f"{reply_fix['id_tag']}: expected {reply_fix['expected_intent']!r} "
            f"got {result.intent!r}"
        )


async def test_unsubscribe_adds_platform_suppression(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)
    replies = load_fixture_replies(fixtures_dir)

    reply_fix = next(R for R in replies if R["id_tag"] == "unsubscribe")
    lead_fix = _lead_for_geo(leads, reply_fix["lead_geo"])
    lead_id = await insert_fixture_lead(tenant_id, lead_fix)
    reply_id = await insert_fixture_reply(tenant_id, lead_id, reply_fix)

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        await classify_reply(session, tenant_id, reply_id, router)

    async with admin_session() as session:
        suppressions = list((await session.execute(select(PlatformSuppression))).scalars())
        assert len(suppressions) == 1
        assert suppressions[0].value_type.value == "email"


async def test_classifier_writes_classification_jsonb(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)
    replies = load_fixture_replies(fixtures_dir)
    reply_fix = next(R for R in replies if R["id_tag"] == "meeting_request")
    lead_fix = _lead_for_geo(leads, reply_fix["lead_geo"])
    lead_id = await insert_fixture_lead(tenant_id, lead_fix)
    reply_id = await insert_fixture_reply(tenant_id, lead_id, reply_fix)

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        await classify_reply(session, tenant_id, reply_id, router)

    async with admin_session() as session:
        reply = (await session.execute(select(Reply).where(Reply.id == reply_id))).scalar_one()
        assert reply.classification is not None
        assert reply.classification["intent"] == "meeting_request"
        assert "preferred_times" in reply.classification["extracted_entities"]
