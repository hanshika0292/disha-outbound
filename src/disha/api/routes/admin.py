"""Admin console API: tenant analytics, account creation/suspension, waitlist.

All routes require an admin principal (is_admin token or the master token).
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select

from disha.api.deps import AdminPrincipal, AdminSession
from disha.auth_users import create_user
from disha.config import get_settings
from disha.db.models import (
    Credential,
    Lead,
    Reply,
    Tenant,
    TenantMembership,
    Touch,
    User,
    WaitlistEntry,
)
from disha.db.models.enums import CredentialStatus
from disha.tenant.provisioner import provision_tenant
from disha.welcome_email import send_welcome_email


def _login_url() -> str:
    """Login page of the web UI, built from DISHA_UI_URL."""
    return f"{get_settings().disha_ui_url.rstrip('/')}/login"


router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview")
async def overview(session: AdminSession, _: AdminPrincipal) -> dict[str, Any]:
    """Cross-tenant analytics for the invite-only fleet."""
    week_ago = datetime.now(UTC) - timedelta(days=7)
    tenants = (
        (await session.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all()
    )

    # Bulk aggregates keyed by tenant (4 queries total, not 4×N)
    leads_by = dict(
        (await session.execute(
            select(Lead.tenant_id, func.count()).group_by(Lead.tenant_id)
        )).all()
    )
    sends_by = dict(
        (await session.execute(
            select(Touch.tenant_id, func.count())
            .where(Touch.sent_at >= week_ago)
            .group_by(Touch.tenant_id)
        )).all()
    )
    replies_by = dict(
        (await session.execute(
            select(Reply.tenant_id, func.count())
            .where(Reply.received_at >= week_ago)
            .group_by(Reply.tenant_id)
        )).all()
    )
    gmail_by = {
        row[0]
        for row in (
            await session.execute(
                select(Credential.tenant_id).where(
                    Credential.provider == "gmail",
                    Credential.status == CredentialStatus.ACTIVE,
                )
            )
        ).all()
    }
    users_by: dict[UUID, list[str]] = {}
    for tid, email in (
        await session.execute(
            select(TenantMembership.tenant_id, User.email).join(
                User, User.id == TenantMembership.user_id
            )
        )
    ).all():
        users_by.setdefault(tid, []).append(email)

    # LLM cost (7d) per tenant
    from disha.db.models import LLMCall

    cost_by: dict[UUID, float] = {
        tid: float(cost or 0)
        for tid, cost in (
            await session.execute(
                select(LLMCall.tenant_id, func.coalesce(func.sum(LLMCall.cost_usd), 0))
                .where(LLMCall.created_at >= week_ago)
                .group_by(LLMCall.tenant_id)
            )
        ).all()
    }

    waitlist_count = (
        await session.execute(select(func.count()).select_from(WaitlistEntry))
    ).scalar_one()

    return {
        "tenants": [
            {
                "id": str(t.id),
                "slug": t.slug,
                "name": t.name,
                "plan": t.plan.value if hasattr(t.plan, "value") else str(t.plan),
                "active": t.active,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "users": users_by.get(t.id, []),
                "gmail_connected": t.id in gmail_by,
                "leads_total": int(leads_by.get(t.id, 0)),
                "sends_7d": int(sends_by.get(t.id, 0)),
                "replies_7d": int(replies_by.get(t.id, 0)),
                "llm_cost_7d_usd": round(cost_by.get(t.id, 0.0), 4),
            }
            for t in tenants
        ],
        "waitlist_count": int(waitlist_count),
    }


class CreateTenantRequest(BaseModel):
    slug: str
    name: str
    email: EmailStr
    password: str
    sender_name: str | None = None
    sender_title: str | None = None
    plan: str = "starter"


@router.post("/tenants")
async def create_tenant(
    session: AdminSession, principal: AdminPrincipal, payload: CreateTenantRequest
) -> dict[str, Any]:
    """Provision a tenant (ICP/prompts/cadences seeded) + its login user."""
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be 8+ characters")
    existing = (
        await session.execute(select(Tenant).where(Tenant.slug == payload.slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Slug {payload.slug!r} taken")

    result = await provision_tenant(
        session,
        slug=payload.slug,
        name=payload.name,
        email=payload.email,
        plan=payload.plan,
        sender_name=payload.sender_name or payload.name,
        sender_title=payload.sender_title,
    )
    user = await create_user(
        session,
        tenant_id=result.tenant_id,
        email=payload.email,
        password=payload.password,
        name=payload.name,
        must_change_password=True,  # force reset of the admin-set password
    )
    # Welcome email from the operator's Gmail — best-effort, never blocks.
    email_sent = await send_welcome_email(
        session,
        principal.tenant_id,
        to_email=payload.email,
        login_url=_login_url(),
        name=payload.name,
        password=payload.password,
    )
    return {
        "tenant_id": str(result.tenant_id),
        "slug": payload.slug,
        "user_email": user.email,
        "login_url": _login_url(),
        "welcome_email_sent": email_sent,
    }


class CreateUserRequest(BaseModel):
    tenant_slug: str
    email: EmailStr
    password: str
    name: str | None = None
    is_admin: bool = False


@router.post("/users")
async def create_user_route(
    session: AdminSession, _: AdminPrincipal, payload: CreateUserRequest
) -> dict[str, Any]:
    """Attach a login user to an EXISTING tenant (bootstrap / extra seats)."""
    tenant = (
        await session.execute(select(Tenant).where(Tenant.slug == payload.tenant_slug))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    try:
        user = await create_user(
            session,
            tenant_id=tenant.id,
            email=payload.email,
            password=payload.password,
            name=payload.name,
            is_admin=payload.is_admin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"user_id": str(user.id), "email": user.email, "is_admin": user.is_admin}


@router.post("/tenants/{tenant_id}/toggle")
async def toggle_tenant(
    session: AdminSession, _: AdminPrincipal, tenant_id: UUID
) -> dict[str, Any]:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant.active = not tenant.active
    tenant.suspended_at = None if tenant.active else datetime.now(UTC)
    return {"slug": tenant.slug, "active": tenant.active}


@router.get("/waitlist")
async def waitlist(session: AdminSession, _: AdminPrincipal) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(WaitlistEntry).order_by(WaitlistEntry.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "email": w.email,
            "name": w.name,
            "note": w.note,
            "source": w.source,
            "answers": w.answers or {},
            "invited_at": w.invited_at.isoformat() if w.invited_at else None,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in rows
    ]


def _slugify(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug[:40] or "user"


class InviteRequest(BaseModel):
    email: EmailStr


@router.post("/waitlist/invite")
async def invite_from_waitlist(
    session: AdminSession, principal: AdminPrincipal, payload: InviteRequest
) -> dict[str, Any]:
    """One-click: waitlist entry → fully provisioned account + login user.

    Slug derives from their name (unique-suffixed if taken); password is
    generated and returned ONCE for the admin to send along with the login
    URL. Marks the entry invited so the button can't double-fire."""
    entry = (
        await session.execute(
            select(WaitlistEntry).where(WaitlistEntry.email == payload.email.lower())
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Waitlist entry not found")
    if entry.invited_at is not None:
        raise HTTPException(status_code=409, detail="Already invited")

    # Unique slug from name (fallback: email local part)
    base = _slugify(entry.name or entry.email.split("@", 1)[0])
    slug = base
    for i in range(2, 50):
        taken = (
            await session.execute(select(Tenant.id).where(Tenant.slug == slug))
        ).scalar_one_or_none()
        if taken is None:
            break
        slug = f"{base}-{i}"

    password = secrets.token_urlsafe(9)
    note = (entry.answers or {}).get("challenge") or entry.note

    result = await provision_tenant(
        session,
        slug=slug,
        name=entry.name or entry.email,
        email=entry.email,
        plan="starter",
        sender_name=entry.name,
        sender_title=entry.note,  # "what do you consult on" doubles as a starter title
    )
    await create_user(
        session,
        tenant_id=result.tenant_id,
        email=entry.email,
        password=password,
        name=entry.name,
        must_change_password=True,  # force reset of the emailed temp password
    )
    entry.invited_at = datetime.now(UTC)

    # Welcome email with their generated credentials — best-effort.
    email_sent = await send_welcome_email(
        session,
        principal.tenant_id,
        to_email=entry.email,
        login_url=_login_url(),
        name=entry.name,
        password=password,
    )

    return {
        "slug": slug,
        "email": entry.email,
        "password": password,
        "login_url": _login_url(),
        "context_note": note,
        "welcome_email_sent": email_sent,
    }
