from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from disha.db import admin_session
from disha.db.models import (
    Cadence,
    Event,
    GeoProfile,
    IcpRubric,
    PromptPack,
    Tenant,
    TenantSettings,
    VoiceGuide,
)

SUPPORTED_SETTINGS_KEYS = {
    # Week 2 — drafter context
    "cal_link",
    "sender_name",
    "sender_title",
    "sender_city",
    "sender_credentials",  # signature line, e.g. "ex-Acme Corp · 10 yrs in product"
    "linkedin_url",        # signature LinkedIn link
    # Week 4 — approval, reports, Notion
    "require_draft_approval",
    "report_email",
    "notion_database_id",
    "notion_last_synced",
    # Automation engine — JSON-string configs managed by the worker/UI
    "auto_source",   # saved weekly sourcing search
    "optimizer",     # weekly optimization tick state
    "daily_send_cap", # flat daily email cap (overrides warm-up ramp)
    "paused",         # global kill switch — pause all sending/sourcing
    "auto_approve_leads",   # auto-approve scored leads (hands-off)
    "auto_approve_tier",    # min tier for auto-approval (A/B/C)
    "auto_approve_outreach",# send drafts without manual approval
    # LinkedIn automation via Unipile
    "linkedin_account_id",  # connected Unipile LinkedIn account id
    "linkedin_auto",        # auto-send queued LinkedIn invites (capped)
    "linkedin_daily_cap",   # max LinkedIn invites/day (default 20)
}

_BOOL_KEYS = {
    "require_draft_approval", "paused", "auto_approve_leads",
    "auto_approve_outreach", "linkedin_auto",
}


async def get_tenant_by_slug(session: AsyncSession, slug: str) -> Tenant | None:
    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()


async def _active_singleton(
    session: AsyncSession, model: type[Any], tenant_id: UUID
) -> dict[str, Any] | None:
    result: Any = await session.execute(
        select(model).where(model.tenant_id == tenant_id, model.is_active.is_(True))
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return {"version": row.version, "config": row.config}


async def _active_cadences(session: AsyncSession, tenant_id: UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        select(Cadence).where(Cadence.tenant_id == tenant_id, Cadence.is_active.is_(True))
    )
    return [{"version": c.version, "config": c.config} for c in result.scalars().all()]


async def show_tenant(slug: str) -> dict[str, Any] | None:
    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, slug)
        if tenant is None:
            return None

        settings_result = await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
        )
        settings_row = settings_result.scalar_one_or_none()

        return {
            "tenant": {
                "id": str(tenant.id),
                "slug": tenant.slug,
                "name": tenant.name,
                "plan": tenant.plan.value,
                "status": tenant.status.value,
                "created_at": tenant.created_at.isoformat(),
            },
            "icp_rubric": await _active_singleton(session, IcpRubric, tenant.id),
            "prompt_pack": await _active_singleton(session, PromptPack, tenant.id),
            "voice_guide": await _active_singleton(session, VoiceGuide, tenant.id),
            "geo_profile": await _active_singleton(session, GeoProfile, tenant.id),
            "cadences": await _active_cadences(session, tenant.id),
            "tenant_settings": settings_row.config if settings_row else None,
        }


def _validate_setting(key: str, value: str) -> None:
    if key not in SUPPORTED_SETTINGS_KEYS:
        raise ValueError(
            f"Unsupported settings key: {key!r}. Allowed: {sorted(SUPPORTED_SETTINGS_KEYS)}"
        )
    if key == "cal_link" and value and not value.startswith("https://"):
        raise ValueError("cal_link must start with https://")


def _coerce_value(key: str, value: str) -> Any:
    if key in _BOOL_KEYS:
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"{key} expects a boolean (true/false), got {value!r}")
    return value


async def set_tenant_setting(slug: str, key: str, value: str) -> dict[str, Any]:
    """Upsert one key in tenant_settings.config; emit settings.updated event."""
    _validate_setting(key, value)
    coerced = _coerce_value(key, value)
    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, slug)
        if tenant is None:
            raise ValueError(f"No tenant with slug={slug!r}")
        row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
            )
        ).scalar_one_or_none()
        if row is None:
            row = TenantSettings(tenant_id=tenant.id, config={})
            session.add(row)
            await session.flush()
        new_config = dict(row.config or {})
        previous = new_config.get(key)
        new_config[key] = coerced
        row.config = new_config
        session.add(
            Event(
                id=str(ULID()),
                tenant_id=tenant.id,
                entity_type="tenant",
                entity_id=tenant.id,
                event_type="settings.updated",
                payload={"key": key, "previous": str(previous), "new": str(coerced)},
                actor="cli",
            )
        )
        return {"tenant": tenant.slug, "key": key, "previous": previous, "new": coerced}


async def get_tenant_settings(slug: str) -> dict[str, Any]:
    async with admin_session() as session:
        tenant = await get_tenant_by_slug(session, slug)
        if tenant is None:
            raise ValueError(f"No tenant with slug={slug!r}")
        row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
            )
        ).scalar_one_or_none()
        return {
            "tenant": tenant.slug,
            "config": dict(row.config) if row else {},
            "updated_at": row.updated_at.isoformat() if row else None,
        }
