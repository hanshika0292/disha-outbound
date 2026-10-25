from __future__ import annotations

import pytest
from sqlalchemy import select

from disha.crypto import new_tenant_salt
from disha.db import admin_session, tenant_session
from disha.db.models import LLMCall, Plan, Tenant
from disha.db.models.enums import TenantStatus

pytestmark = pytest.mark.asyncio


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


async def test_llm_calls_isolated_across_tenants() -> None:
    """A's llm_calls must be invisible from B's tenant_session — RLS canary."""
    from uuid import uuid4

    tenant_a = await _create_test_tenant("test-llm-a")
    tenant_b = await _create_test_tenant("test-llm-b")

    from decimal import Decimal

    async with admin_session() as session:
        session.add(
            LLMCall(
                id=uuid4(),
                tenant_id=tenant_a.id,
                purpose="score.role_fit",
                prompt_version=1,
                provider="mock",
                model="mock-model",
                tokens_in=10,
                tokens_out=5,
                cost_usd=Decimal("0.000001"),
                latency_ms=1,
                success=True,
            )
        )

    async with tenant_session(tenant_a.id) as session:
        a_rows = list((await session.execute(select(LLMCall))).scalars())
        assert len(a_rows) == 1

    async with tenant_session(tenant_b.id) as session:
        b_rows = list((await session.execute(select(LLMCall))).scalars())
        assert b_rows == []
