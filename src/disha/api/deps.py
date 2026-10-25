"""FastAPI dependencies: authentication principal, sessions, tenant context.

Auth model (Stage 1 multi-user):

  Bearer <user-token>       → signed per-user token (api/tokens.py). Resolves
                              to that user's tenant; is_admin from the token.
  Bearer <DISHA_UI_TOKEN>   → platform master token. Resolves to the
                              platform tenant (slug 'demo', created by
                              `disha tenant seed`) with admin rights. Kept for
                              bootstrap/ops; the UI no longer ships it.

Every authenticated route receives a Principal; tenant scoping always comes
from the principal — never from a client-supplied slug.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.api.tokens import verify_token
from disha.config import get_settings
from disha.db.models import Tenant
from disha.db.session import admin_session, tenant_session

_PLATFORM_TENANT_SLUG = "demo"


@dataclass(frozen=True)
class Principal:
    tenant_id: UUID
    user_id: UUID | None  # None for the legacy master token
    is_admin: bool


async def get_admin_session() -> AsyncGenerator[AsyncSession, None]:
    """disha_admin role — bypasses RLS. For auth lookups and platform ops only."""
    async with admin_session() as session:
        yield session


AdminSession = Annotated[AsyncSession, Depends(get_admin_session)]


async def _master_principal() -> Principal:
    async with admin_session() as session:
        tenant = (
            await session.execute(
                select(Tenant).where(Tenant.slug == _PLATFORM_TENANT_SLUG)
            )
        ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=500, detail="Platform tenant missing")
    return Principal(tenant_id=tenant.id, user_id=None, is_admin=True)


async def get_principal(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    token: str | None = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    elif x_api_key:
        token = x_api_key.strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    master = get_settings().disha_ui_token.get_secret_value()
    if master and token == master:
        return await _master_principal()

    payload = verify_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return Principal(
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        is_admin=payload.is_admin,
    )


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def require_admin(principal: CurrentPrincipal) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return principal


AdminPrincipal = Annotated[Principal, Depends(require_admin)]


async def get_session_for_tenant(
    principal: CurrentPrincipal,
) -> AsyncGenerator[tuple[AsyncSession, UUID], None]:
    """Tenant-scoped session for the AUTHENTICATED principal's tenant."""
    async with tenant_session(principal.tenant_id) as session:
        yield session, principal.tenant_id


TenantSession = Annotated[
    tuple[AsyncSession, UUID], Depends(get_session_for_tenant)
]


async def get_tenant_id(principal: CurrentPrincipal) -> UUID:
    """Tenant id only — for endpoints that manage their own transactions."""
    return principal.tenant_id


TenantId = Annotated[UUID, Depends(get_tenant_id)]


def make_tenant_session_dep(
    get_tenant_id_dep: Callable[..., Any],
) -> Callable[..., AsyncGenerator[tuple[AsyncSession, UUID], None]]:
    """Builds a tenant_session dependency. Defined as a factory to defer
    importing auth.get_tenant_id until route modules pull it in."""

    async def _dep(
        tenant_id: UUID = Depends(get_tenant_id_dep),
    ) -> AsyncGenerator[tuple[AsyncSession, UUID], None]:
        async with tenant_session(tenant_id) as session:
            yield session, tenant_id

    return _dep
