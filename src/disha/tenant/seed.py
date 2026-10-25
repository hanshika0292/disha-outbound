import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.config import get_settings
from disha.crypto import new_tenant_salt
from disha.db import admin_session
from disha.db.models import (
    Cadence,
    GeoProfile,
    IcpRubric,
    Plan,
    PromptPack,
    Tenant,
    TenantSettings,
    VoiceGuide,
)
from disha.db.models.enums import TenantStatus

DEMO_SLUG = "demo"
DEMO_NAME = "Demo — AI Consulting"

DEFAULT_TENANT_SETTINGS: dict[str, Any] = {
    "daily_send_cap": 30,
    "send_windows": {
        "IN": {
            "weekdays": ["mon", "tue", "wed", "thu", "fri"],
            "hours_ist": [[10, 12], [15, 18]],
        },
        "US": {
            "weekdays": ["mon", "tue", "wed", "thu"],
            "hours_pst": [[8, 11]],
        },
    },
    "quiet_hours_local": [[22, 8]],
    "festival_blocks": ["diwali_2026", "holi_2027"],
    "approval_required": True,
    "auto_send_tiers": [],
}


def _load_seed(name: str) -> dict[str, Any]:
    path: Path = get_settings().seeds_dir / name
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def _configs_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Normalized comparison: ignore JSONB round-trip key ordering."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


async def _get_or_create_tenant(session: AsyncSession) -> Tenant:
    result = await session.execute(select(Tenant).where(Tenant.slug == DEMO_SLUG))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    tenant = Tenant(
        slug=DEMO_SLUG,
        name=DEMO_NAME,
        plan=Plan.STARTER,
        status=TenantStatus.TRIAL,
        email_hash_salt=new_tenant_salt(),
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def _upsert_singleton_config(
    session: AsyncSession,
    model: type[Any],
    tenant_id: Any,
    seed_file: str,
) -> str:
    """Upsert one active row per tenant.

    Returns: 'created' | 'unchanged' | 'bumped:vN->vN+1'.
    On content change: deactivates the old row, inserts a new one with version+1.
    The flush between the two is required because of the partial unique index
    on (tenant_id) WHERE is_active.
    """
    config = _load_seed(seed_file)
    result: Any = await session.execute(
        select(model).where(model.tenant_id == tenant_id, model.is_active.is_(True))
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        session.add(model(tenant_id=tenant_id, version=1, is_active=True, config=config))
        return "created"

    if _configs_equal(existing.config, config):
        return "unchanged"

    old_version = existing.version
    existing.is_active = False
    await session.flush()
    session.add(model(tenant_id=tenant_id, version=old_version + 1, is_active=True, config=config))
    return f"bumped:v{old_version}->v{old_version + 1}"


async def _upsert_cadence(
    session: AsyncSession,
    tenant_id: Any,
    seed_file: str,
    region_key: str,
) -> str:
    """Upsert active cadence keyed by (tenant_id, region). Multiple actives across regions OK."""
    config = _load_seed(seed_file)
    config.setdefault("region", region_key)
    result = await session.execute(
        select(Cadence).where(
            Cadence.tenant_id == tenant_id,
            Cadence.is_active.is_(True),
            Cadence.config["region"].astext == region_key,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        session.add(Cadence(tenant_id=tenant_id, version=1, is_active=True, config=config))
        return "created"

    if _configs_equal(existing.config, config):
        return "unchanged"

    old_version = existing.version
    existing.is_active = False
    await session.flush()
    session.add(
        Cadence(tenant_id=tenant_id, version=old_version + 1, is_active=True, config=config)
    )
    return f"bumped:v{old_version}->v{old_version + 1}"


async def _ensure_tenant_settings(session: AsyncSession, tenant_id: Any) -> str:
    """Create-if-missing. Settings are user-editable so we never overwrite on re-seed."""
    result = await session.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    if result.scalar_one_or_none() is not None:
        return "unchanged"
    session.add(TenantSettings(tenant_id=tenant_id, config=DEFAULT_TENANT_SETTINGS))
    return "created"


async def seed_demo_tenant() -> dict[str, Any]:
    """Idempotent re-seed. On unchanged JSON: no-op. On changed JSON: bumps to v+1."""
    async with admin_session() as session:
        tenant = await _get_or_create_tenant(session)
        actions: dict[str, str] = {
            "icp_rubric": await _upsert_singleton_config(
                session, IcpRubric, tenant.id, "demo_icp.json"
            ),
            "prompt_pack": await _upsert_singleton_config(
                session, PromptPack, tenant.id, "demo_prompts.json"
            ),
            "voice_guide": await _upsert_singleton_config(
                session, VoiceGuide, tenant.id, "demo_voice.json"
            ),
            "geo_profile": await _upsert_singleton_config(
                session, GeoProfile, tenant.id, "demo_geo.json"
            ),
            "cadence_in": await _upsert_cadence(
                session, tenant.id, "demo_cadence_in.json", "IN"
            ),
            "cadence_us": await _upsert_cadence(
                session, tenant.id, "demo_cadence_us.json", "US"
            ),
            "tenant_settings": await _ensure_tenant_settings(session, tenant.id),
        }
        return {
            "tenant_id": str(tenant.id),
            "slug": tenant.slug,
            "name": tenant.name,
            "actions": actions,
        }
