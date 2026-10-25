"""Multi-tenant provisioning + lifecycle.

provision_tenant(): creates tenant + tenant_settings + (optional) template seeding +
fresh API key. The plaintext key is returned ONCE — caller must surface it.
list_tenants(): admin view across the platform (no RLS).
upgrade_plan / suspend_tenant / reactivate_tenant / rotate_api_key: lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from disha.crypto import new_tenant_salt
from disha.db import admin_session
from disha.db.models import (
    ApiKey,
    Cadence,
    Event,
    GeoProfile,
    IcpRubric,
    Lead,
    LeadStateRow,
    Plan,
    PromptPack,
    Tenant,
    TenantSettings,
    VoiceGuide,
)
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.db.models.enums import TenantStatus

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")
_VALID_PLANS = {"starter", "growth", "pro", "scale", "agency", "enterprise"}

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "seeds" / "templates"


class ProvisionResult(BaseModel):
    tenant_id: UUID
    slug: str
    plan: str
    api_key: str  # plaintext — shown once
    api_key_prefix: str


class TenantSummary(BaseModel):
    tenant_id: UUID
    slug: str
    name: str
    plan: str
    active: bool
    suspended_at: datetime | None
    created_at: datetime
    lead_count: int
    in_sequence_count: int
    api_key_prefix: str | None


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid slug {slug!r}. Must be 3-32 chars, lowercase alphanumeric + hyphens, "
            f"start/end alphanumeric."
        )


def _validate_plan(plan: str) -> None:
    if plan not in _VALID_PLANS:
        raise ValueError(f"Invalid plan {plan!r}. Allowed: {sorted(_VALID_PLANS)}")


def _apply_template_vars(obj: Any, vars_: dict[str, str]) -> Any:
    """Recursively replace {{KEY}} placeholders in all string values."""
    if isinstance(obj, str):
        result = obj
        for k, v in vars_.items():
            result = result.replace(f"{{{{{k}}}}}", v)
        return result
    if isinstance(obj, dict):
        return {k: _apply_template_vars(v, vars_) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_apply_template_vars(item, vars_) for item in obj]
    return obj


def _load_template(name: str) -> dict[str, Any]:
    path = _TEMPLATES_DIR / f"{name}.json"
    if not path.exists():
        raise ValueError(
            f"Template {name!r} not found. Available: "
            f"{sorted(p.stem for p in _TEMPLATES_DIR.glob('*.json'))}"
        )
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def _generate_api_key() -> tuple[str, str, str]:
    """Returns (plaintext, key_prefix, sha256_hex)."""
    plaintext = secrets.token_urlsafe(32)
    key_prefix = plaintext[:8]
    key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, key_prefix, key_hash


async def _emit(
    session: AsyncSession,
    tenant_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    actor: str = "provisioner",
) -> None:
    session.add(
        Event(
            id=str(ULID()),
            tenant_id=tenant_id,
            entity_type="tenant",
            entity_id=tenant_id,
            event_type=event_type,
            payload=payload,
            actor=actor,
        )
    )


async def provision_tenant(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    email: str,
    plan: str = "starter",
    template: str | None = None,
    sender_name: str | None = None,
    sender_title: str | None = None,
    sender_city: str | None = None,
) -> ProvisionResult:
    """Create tenant + settings + (optional template-driven) config + first API key."""
    _validate_slug(slug)
    _validate_plan(plan)

    existing = (
        await session.execute(select(Tenant).where(Tenant.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"Tenant with slug={slug!r} already exists")

    tenant = Tenant(
        slug=slug,
        name=name,
        plan=Plan(plan),
        status=TenantStatus.ACTIVE,
        email_hash_salt=new_tenant_salt(),
        active=True,
    )
    session.add(tenant)
    await session.flush()

    resolved_sender_name = sender_name or name
    resolved_sender_title = sender_title or "Consultant"
    resolved_sender_city = sender_city or ""

    settings_config: dict[str, Any] = {
        "sender_name": resolved_sender_name,
        "sender_title": resolved_sender_title,
        "sender_city": resolved_sender_city,
        "owner_email": email,
        "require_draft_approval": True,
    }
    session.add(TenantSettings(tenant_id=tenant.id, config=settings_config))

    if template:
        tpl = _load_template(template)
        vars_ = {
            "SENDER_NAME": resolved_sender_name,
            "SENDER_TITLE": resolved_sender_title,
            "SENDER_CITY": resolved_sender_city,
        }
        rendered = _apply_template_vars(tpl, vars_)

        session.add(
            IcpRubric(tenant_id=tenant.id, version=1, is_active=True, config=rendered["icp_rubric"])
        )
        session.add(
            PromptPack(
                tenant_id=tenant.id,
                version=1,
                is_active=True,
                config={"prompts": rendered["prompts"]},
            )
        )
        session.add(
            VoiceGuide(tenant_id=tenant.id, version=1, is_active=True, config=rendered["voice"])
        )
        session.add(
            Cadence(tenant_id=tenant.id, version=1, is_active=True, config=rendered["cadence_in"])
        )
        session.add(
            Cadence(tenant_id=tenant.id, version=1, is_active=True, config=rendered["cadence_us"])
        )
        session.add(
            GeoProfile(tenant_id=tenant.id, version=1, is_active=True, config=rendered["geo"])
        )

    plaintext, key_prefix, key_hash = _generate_api_key()
    session.add(
        ApiKey(
            tenant_id=tenant.id,
            key_prefix=key_prefix,
            key_hash=key_hash,
            name="default",
            plan=plan,
            active=True,
        )
    )

    await _emit(
        session,
        tenant.id,
        "tenant.provisioned",
        {"slug": slug, "plan": plan, "template": template or "none", "email": email},
    )

    return ProvisionResult(
        tenant_id=tenant.id,
        slug=slug,
        plan=plan,
        api_key=plaintext,
        api_key_prefix=key_prefix,
    )


async def list_tenants(session: AsyncSession) -> list[TenantSummary]:
    """Admin view — all tenants. Uses disha_admin (no RLS filtering)."""
    rows = (
        (await session.execute(select(Tenant).order_by(Tenant.created_at.desc()))).scalars().all()
    )
    out: list[TenantSummary] = []
    for t in rows:
        n_leads = (
            await session.execute(
                select(func.count()).select_from(Lead).where(Lead.tenant_id == t.id)
            )
        ).scalar_one()
        n_in_seq = (
            await session.execute(
                select(func.count())
                .select_from(LeadStateRow)
                .where(
                    LeadStateRow.tenant_id == t.id,
                    LeadStateRow.state == LeadStateEnum.IN_SEQUENCE,
                )
            )
        ).scalar_one()
        latest_key = (
            await session.execute(
                select(ApiKey.key_prefix)
                .where(ApiKey.tenant_id == t.id, ApiKey.active.is_(True))
                .order_by(ApiKey.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        out.append(
            TenantSummary(
                tenant_id=t.id,
                slug=t.slug,
                name=t.name,
                plan=t.plan.value if hasattr(t.plan, "value") else str(t.plan),
                active=t.active,
                suspended_at=t.suspended_at,
                created_at=t.created_at,
                lead_count=int(n_leads),
                in_sequence_count=int(n_in_seq),
                api_key_prefix=latest_key,
            )
        )
    return out


async def upgrade_plan(session: AsyncSession, slug: str, new_plan: str) -> None:
    _validate_plan(new_plan)
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one()
    tenant.plan = Plan(new_plan)
    await session.execute(update(ApiKey).where(ApiKey.tenant_id == tenant.id).values(plan=new_plan))
    await _emit(session, tenant.id, "tenant.plan_changed", {"slug": slug, "new_plan": new_plan})


async def suspend_tenant(session: AsyncSession, slug: str, *, reason: str = "manual") -> None:
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one()
    tenant.active = False
    tenant.suspended_at = datetime.now(UTC)
    await session.execute(update(ApiKey).where(ApiKey.tenant_id == tenant.id).values(active=False))
    await _emit(session, tenant.id, "tenant.suspended", {"slug": slug, "reason": reason})


async def reactivate_tenant(session: AsyncSession, slug: str) -> None:
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one()
    tenant.active = True
    tenant.suspended_at = None
    await session.execute(update(ApiKey).where(ApiKey.tenant_id == tenant.id).values(active=True))
    await _emit(session, tenant.id, "tenant.reactivated", {"slug": slug})


async def rotate_api_key(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    name: str = "default",
) -> str:
    """Deactivate existing keys with same name; mint a new one. Returns plaintext."""
    await session.execute(
        update(ApiKey)
        .where(ApiKey.tenant_id == tenant_id, ApiKey.name == name)
        .values(active=False)
    )
    tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    plaintext, key_prefix, key_hash = _generate_api_key()
    session.add(
        ApiKey(
            tenant_id=tenant_id,
            key_prefix=key_prefix,
            key_hash=key_hash,
            name=name,
            plan=tenant.plan.value if hasattr(tenant.plan, "value") else str(tenant.plan),
            active=True,
        )
    )
    await _emit(
        session, tenant_id, "tenant.api_key_rotated", {"name": name, "key_prefix": key_prefix}
    )
    return plaintext


async def provision_tenant_via_admin(
    *,
    slug: str,
    name: str,
    email: str,
    plan: str = "starter",
    template: str | None = None,
    sender_name: str | None = None,
    sender_title: str | None = None,
    sender_city: str | None = None,
) -> ProvisionResult:
    """CLI/API helper: opens admin_session for the caller."""
    async with admin_session() as session:
        return await provision_tenant(
            session,
            slug=slug,
            name=name,
            email=email,
            plan=plan,
            template=template,
            sender_name=sender_name,
            sender_title=sender_title,
            sender_city=sender_city,
        )
