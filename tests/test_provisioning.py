"""Tenant provisioning lifecycle: create, list, suspend, reactivate, rotate."""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import select

from disha.db import admin_session
from disha.db.models import ApiKey, PromptPack, Tenant, VoiceGuide
from disha.tenant.provisioner import (
    list_tenants,
    provision_tenant,
    reactivate_tenant,
    rotate_api_key,
    suspend_tenant,
    upgrade_plan,
)

pytestmark = pytest.mark.asyncio


async def test_provision_creates_tenant_and_api_key() -> None:
    async with admin_session() as session:
        result = await provision_tenant(
            session,
            slug="alice-ux",
            name="Alice Chen",
            email="alice@ux-studio.example",
            plan="growth",
        )
    assert result.slug == "alice-ux"
    assert result.plan == "growth"
    assert len(result.api_key) >= 32
    assert result.api_key_prefix == result.api_key[:8]

    async with admin_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == "alice-ux"))
        ).scalar_one()
        keys = list(
            (await session.execute(select(ApiKey).where(ApiKey.tenant_id == tenant.id)))
            .scalars()
            .all()
        )
    assert tenant.active
    assert tenant.plan.value == "growth"
    assert len(keys) == 1
    assert keys[0].active
    assert keys[0].key_hash == hashlib.sha256(result.api_key.encode()).hexdigest()


async def test_provision_with_template_seeds_prompts() -> None:
    async with admin_session() as session:
        await provision_tenant(
            session,
            slug="bob-uxd",
            name="Bob",
            email="bob@x.co",
            plan="starter",
            template="ux_design",
            sender_name="Bob Designer",
            sender_title="UX Design Consultant",
            sender_city="Mumbai",
        )
    async with admin_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == "bob-uxd"))
        ).scalar_one()
        prompts = (
            await session.execute(
                select(PromptPack).where(
                    PromptPack.tenant_id == tenant.id, PromptPack.is_active.is_(True)
                )
            )
        ).scalar_one()
        voice = (
            await session.execute(
                select(VoiceGuide).where(
                    VoiceGuide.tenant_id == tenant.id, VoiceGuide.is_active.is_(True)
                )
            )
        ).scalar_one()
    prompt_keys = list(prompts.config["prompts"].keys())
    assert len(prompt_keys) >= 8
    # No placeholder leakage anywhere in prompt strings
    blob = json.dumps(prompts.config) + json.dumps(voice.config)
    assert "{{SENDER_NAME}}" not in blob
    assert "{{SENDER_TITLE}}" not in blob
    assert "{{SENDER_CITY}}" not in blob
    assert "Bob Designer" in blob
    assert "UX Design Consultant" in blob


async def test_provision_template_vars_replaced_in_ai_consulting() -> None:
    async with admin_session() as session:
        await provision_tenant(
            session,
            slug="carol-ai",
            name="Carol",
            email="carol@x.co",
            plan="pro",
            template="ai_consulting",
            sender_name="Carol Smith",
            sender_title="AI Strategy Consultant",
            sender_city="Bangalore",
        )
    async with admin_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == "carol-ai"))
        ).scalar_one()
        prompts = (
            await session.execute(select(PromptPack).where(PromptPack.tenant_id == tenant.id))
        ).scalar_one()
    for _, prompt_def in prompts.config["prompts"].items():
        for field in ("system", "user_template"):
            s = prompt_def.get(field, "")
            assert "{{SENDER_NAME}}" not in s
            assert "{{SENDER_TITLE}}" not in s
            assert "{{SENDER_CITY}}" not in s


async def test_duplicate_slug_raises() -> None:
    async with admin_session() as session:
        await provision_tenant(session, slug="dupe", name="D", email="d@x.co")
    with pytest.raises(ValueError, match="already exists"):
        async with admin_session() as session:
            await provision_tenant(session, slug="dupe", name="D", email="d@x.co")


async def test_invalid_slug_raises() -> None:
    with pytest.raises(ValueError, match="Invalid slug"):
        async with admin_session() as session:
            await provision_tenant(session, slug="Bad_Slug!", name="X", email="x@x.co")


async def test_suspend_deactivates_api_keys() -> None:
    async with admin_session() as session:
        result = await provision_tenant(session, slug="eve", name="Eve", email="e@x.co")
    async with admin_session() as session:
        await suspend_tenant(session, "eve")

    async with admin_session() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "eve"))).scalar_one()
        keys = list(
            (await session.execute(select(ApiKey).where(ApiKey.tenant_id == tenant.id)))
            .scalars()
            .all()
        )
    assert not tenant.active
    assert tenant.suspended_at is not None
    assert all(not k.active for k in keys)
    # The key still exists in DB — just inactive
    assert any(k.key_hash == hashlib.sha256(result.api_key.encode()).hexdigest() for k in keys)


async def test_reactivate_restores_keys() -> None:
    async with admin_session() as session:
        await provision_tenant(session, slug="frank", name="F", email="f@x.co")
    async with admin_session() as session:
        await suspend_tenant(session, "frank")
    async with admin_session() as session:
        await reactivate_tenant(session, "frank")
    async with admin_session() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "frank"))).scalar_one()
        keys = list(
            (await session.execute(select(ApiKey).where(ApiKey.tenant_id == tenant.id)))
            .scalars()
            .all()
        )
    assert tenant.active
    assert tenant.suspended_at is None
    assert all(k.active for k in keys)


async def test_upgrade_updates_plan() -> None:
    async with admin_session() as session:
        await provision_tenant(session, slug="gina", name="G", email="g@x.co", plan="starter")
    async with admin_session() as session:
        await upgrade_plan(session, "gina", "scale")
    async with admin_session() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "gina"))).scalar_one()
        keys = list(
            (await session.execute(select(ApiKey).where(ApiKey.tenant_id == tenant.id)))
            .scalars()
            .all()
        )
    assert tenant.plan.value == "scale"
    assert all(k.plan == "scale" for k in keys)


async def test_rotate_api_key_deactivates_old() -> None:
    async with admin_session() as session:
        result = await provision_tenant(session, slug="hank", name="H", email="h@x.co")
    old_hash = hashlib.sha256(result.api_key.encode()).hexdigest()

    async with admin_session() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "hank"))).scalar_one()
        new_key = await rotate_api_key(session, tenant.id)
    new_hash = hashlib.sha256(new_key.encode()).hexdigest()

    async with admin_session() as session:
        keys = list(
            (await session.execute(select(ApiKey).where(ApiKey.tenant_id == tenant.id)))
            .scalars()
            .all()
        )
    by_hash = {k.key_hash: k for k in keys}
    assert by_hash[old_hash].active is False
    assert by_hash[new_hash].active is True


async def test_list_tenants_returns_summary() -> None:
    async with admin_session() as session:
        await provision_tenant(session, slug="iris", name="Iris", email="i@x.co")
    async with admin_session() as session:
        rows = await list_tenants(session)
    slugs = {r.slug for r in rows}
    assert "iris" in slugs
    iris = next(r for r in rows if r.slug == "iris")
    assert iris.lead_count == 0
    assert iris.in_sequence_count == 0
    assert iris.api_key_prefix is not None and len(iris.api_key_prefix) == 8
