"""API auth — two paths on the same routes:

  Bearer <token>           → per-user signed token or the DISHA_UI_TOKEN master
                             token; both parsed by deps.get_principal(), which
                             fixes the tenant for get_session_for_tenant().

  X-API-Key <plaintext>     → multi-tenant Week 6 path. sha256(plaintext) →
                             api_keys lookup → tenant_id. Preserved for
                             future programmatic access.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from disha.api.deps import AdminSession, make_tenant_session_dep
from disha.api.rate_limit import check_rate_limit
from disha.config import get_settings
from disha.db.models import ApiKey, Tenant


async def require_auth(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Auth gate: accepts a per-user signed token OR the legacy master token.
    Tenant scoping happens in get_session_for_tenant() via the same principal
    parsing, so a user token always lands in its own tenant."""
    from disha.api.deps import get_principal

    await get_principal(authorization=authorization, x_api_key=x_api_key)


async def _verify_api_key(api_key: str, session: AsyncSession) -> tuple[UUID, str]:
    """Multi-tenant X-API-Key auth (Week 6). Returns (tenant_id, plan)."""
    if not api_key or len(api_key) < 16:
        raise HTTPException(status_code=401, detail="Missing or malformed API key")
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    row = (
        await session.execute(
            select(ApiKey, Tenant)
            .join(Tenant, Tenant.id == ApiKey.tenant_id)
            .where(ApiKey.key_hash == key_hash, ApiKey.active.is_(True))
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    api_row, tenant = row
    if not tenant.active:
        raise HTTPException(status_code=403, detail="Tenant suspended")
    await session.execute(
        update(ApiKey).where(ApiKey.id == api_row.id).values(last_used_at=datetime.now(UTC))
    )
    return tenant.id, api_row.plan


async def get_tenant_id(
    session: AdminSession,
    x_api_key: Annotated[str, Header(alias="X-API-Key")] = "",
) -> UUID:
    """Week 6 multi-tenant entry. Validates X-API-Key + enforces rate limit."""
    tenant_id, plan = await _verify_api_key(x_api_key, session)
    check_rate_limit(tenant_id, plan)
    return tenant_id


# Kept for Week 6 callers (test_api.py still uses these for X-API-Key tests).
TenantSessionDep = make_tenant_session_dep(get_tenant_id)
