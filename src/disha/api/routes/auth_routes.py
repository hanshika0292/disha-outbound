"""User auth routes: login, me, change password. No signup — invite-only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from disha.api.deps import AdminSession, CurrentPrincipal
from disha.api.tokens import issue_token
from disha.auth_users import authenticate, hash_password, verify_password
from disha.db.models import Tenant, User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/login")
async def login(session: AdminSession, payload: LoginRequest) -> dict[str, Any]:
    result = await authenticate(session, payload.email, payload.password)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user, tenant_id = result

    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one()
    if not tenant.active:
        raise HTTPException(status_code=403, detail="Account suspended")

    return {
        "token": issue_token(user.id, tenant_id, user.is_admin),
        "user": {
            "email": user.email,
            "name": user.name,
            "is_admin": user.is_admin,
            "must_change_password": user.must_change_password,
        },
        "tenant": {"slug": tenant.slug, "name": tenant.name},
        "must_change_password": user.must_change_password,
    }


@router.get("/me")
async def me(session: AdminSession, principal: CurrentPrincipal) -> dict[str, Any]:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == principal.tenant_id))
    ).scalar_one_or_none()
    user = None
    if principal.user_id is not None:
        user = (
            await session.execute(select(User).where(User.id == principal.user_id))
        ).scalar_one_or_none()
    return {
        "is_admin": principal.is_admin,
        "user": (
            {
                "email": user.email,
                "name": user.name,
                "must_change_password": user.must_change_password,
            }
            if user
            else None
        ),
        "must_change_password": bool(user and user.must_change_password),
        "tenant": {"slug": tenant.slug, "name": tenant.name} if tenant else None,
    }


class ChangePasswordRequest(BaseModel):
    new_password: str
    # Optional during a forced first-login reset (the user is already
    # authenticated by token and the temp password was system-generated).
    current_password: str | None = None


@router.post("/change-password", status_code=204)
async def change_password(
    session: AdminSession, principal: CurrentPrincipal, payload: ChangePasswordRequest
) -> None:
    if principal.user_id is None:
        raise HTTPException(status_code=400, detail="Master token has no password")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be 8+ characters")
    user = (
        await session.execute(select(User).where(User.id == principal.user_id))
    ).scalar_one()
    # Forced first-login reset skips the current-password check; a normal
    # voluntary change still requires it.
    if not user.must_change_password:
        if not user.password_hash or not verify_password(
            payload.current_password or "", user.password_hash
        ):
            raise HTTPException(status_code=401, detail="Current password incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
