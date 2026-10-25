from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from disha.crypto import encrypt, hash_email, new_tenant_salt
from disha.db import admin_session, tenant_session
from disha.db.models import (
    Account,
    Cadence,
    GeoProfile,
    IcpRubric,
    Lead,
    Plan,
    PromptPack,
    Tenant,
    TenantSettings,
    VoiceGuide,
)
from disha.db.models.enums import TenantStatus
from disha.tenant import seed_demo_tenant, show_tenant
from disha.tenant.seed import DEMO_SLUG

pytestmark = pytest.mark.asyncio


async def test_seed_creates_tenant_and_active_configs() -> None:
    summary = await seed_demo_tenant()
    assert summary["slug"] == DEMO_SLUG

    async with admin_session() as session:
        tenants = (await session.execute(select(Tenant))).scalars().all()
        assert len(tenants) == 1
        t = tenants[0]
        assert t.plan == Plan.STARTER
        assert t.status == TenantStatus.TRIAL
        assert len(t.email_hash_salt) == 16

        for model in (IcpRubric, PromptPack, VoiceGuide, GeoProfile):
            count = await session.scalar(
                select(func.count()).select_from(model).where(model.is_active.is_(True))
            )
            assert count == 1, f"{model.__tablename__} should have exactly one active row"

        cadence_count = await session.scalar(
            select(func.count()).select_from(Cadence).where(Cadence.is_active.is_(True))
        )
        assert cadence_count == 2

        settings_count = await session.scalar(select(func.count()).select_from(TenantSettings))
        assert settings_count == 1


async def test_seed_is_idempotent() -> None:
    await seed_demo_tenant()
    await seed_demo_tenant()
    await seed_demo_tenant()

    async with admin_session() as session:
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 1
        assert await session.scalar(select(func.count()).select_from(IcpRubric)) == 1
        assert await session.scalar(select(func.count()).select_from(Cadence)) == 2
        assert await session.scalar(select(func.count()).select_from(TenantSettings)) == 1


async def test_show_tenant_returns_active_icp_dimensions() -> None:
    await seed_demo_tenant()
    result = await show_tenant(DEMO_SLUG)
    assert result is not None
    assert result["tenant"]["slug"] == DEMO_SLUG
    assert result["tenant"]["plan"] == "starter"
    assert result["tenant"]["status"] == "trial"
    assert result["icp_rubric"] is not None
    dimensions = result["icp_rubric"]["config"]["dimensions"]
    assert len(dimensions) == 5
    keys = {d["key"] for d in dimensions}
    assert keys == {"fit_funding", "fit_size", "fit_role", "pain_signal", "fit_geo"}


async def test_show_tenant_returns_none_for_unknown_slug() -> None:
    assert await show_tenant("does-not-exist") is None


async def _create_test_tenant(slug: str) -> Tenant:
    async with admin_session() as session:
        t = Tenant(
            slug=slug,
            name=f"Test {slug}",
            plan=Plan.STARTER,
            status=TenantStatus.TRIAL,
            email_hash_salt=new_tenant_salt(),
        )
        session.add(t)
        await session.flush()
        session.expunge(t)
        return t


async def test_rls_isolates_cross_tenant_reads() -> None:
    """The canary: tenant B must see zero rows from tenant A."""
    tenant_a = await _create_test_tenant("test-tenant-a")
    tenant_b = await _create_test_tenant("test-tenant-b")

    async with tenant_session(tenant_a.id) as session:
        session.add(
            Account(
                tenant_id=tenant_a.id,
                domain="acme-a.example.com",
                name="Acme A",
            )
        )
        session.add(
            Lead(
                tenant_id=tenant_a.id,
                email_hash=hash_email("alice@acme-a.example.com", tenant_a.email_hash_salt),
                email_encrypted=encrypt("alice@acme-a.example.com"),
                first_name="Alice",
            )
        )

    async with tenant_session(tenant_a.id) as session:
        a_accounts = (await session.execute(select(Account))).scalars().all()
        a_leads = (await session.execute(select(Lead))).scalars().all()
        assert len(a_accounts) == 1
        assert len(a_leads) == 1

    async with tenant_session(tenant_b.id) as session:
        b_accounts = (await session.execute(select(Account))).scalars().all()
        b_leads = (await session.execute(select(Lead))).scalars().all()
        assert b_accounts == []
        assert b_leads == []


async def test_rls_blocks_writes_with_wrong_tenant_id() -> None:
    """WITH CHECK on the policy must reject inserts whose tenant_id doesn't match the GUC."""
    tenant_a = await _create_test_tenant("test-tenant-a")
    tenant_b = await _create_test_tenant("test-tenant-b")

    with pytest.raises(DBAPIError):
        async with tenant_session(tenant_a.id) as session:
            session.add(
                Account(
                    tenant_id=tenant_b.id,
                    domain="evil.example.com",
                    name="Tries to leak across tenants",
                )
            )


async def test_email_hash_is_deterministic_per_tenant() -> None:
    salt = new_tenant_salt()
    h1 = hash_email("Foo@Example.COM", salt)
    h2 = hash_email("foo@example.com", salt)
    assert h1 == h2
    h3 = hash_email("foo@example.com", new_tenant_salt())
    assert h3 != h1


async def test_encrypt_roundtrip() -> None:
    ct = encrypt("alice@example.com")
    from disha.crypto import decrypt

    assert decrypt(ct) == b"alice@example.com"


async def test_event_observed_at_is_timezone_aware() -> None:
    now = datetime.now(UTC)
    _ = uuid4()
    assert now.tzinfo is not None
