"""Settings routes: tenant_settings config + BYOK provider status + health.

GET /settings           → {tenant, config{}, byok{provider: {configured, ...}}}
PATCH /settings         → upsert one or many config keys (autosave-friendly)
GET /settings/health    → wrap run_health_checks for the UI status dot
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from disha.api.auth import require_auth
from disha.api.deps import AdminSession, TenantSession
from disha.db.models import Tenant, TenantSettings
from disha.health import run_health_checks
from disha.tenant.byok import get_byok_status
from disha.tenant.service import (
    SUPPORTED_SETTINGS_KEYS,
    _coerce_value,
    _validate_setting,
    get_tenant_by_slug,
)

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])


@router.get("")
async def get_settings_route(deps: TenantSession) -> dict[str, Any]:
    session, tenant_id = deps

    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    settings_row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    byok = await get_byok_status(session, tenant_id)

    return {
        "tenant": {
            "id": str(tenant_id),
            "slug": tenant.slug if tenant else None,
            "name": tenant.name if tenant else None,
            "plan": tenant.plan.value if tenant else None,
            "status": tenant.status.value if tenant else None,
        },
        "config": dict(settings_row.config) if settings_row and settings_row.config else {},
        "updated_at": settings_row.updated_at.isoformat()
        if settings_row and settings_row.updated_at
        else None,
        "byok": byok,
        "supported_keys": sorted(SUPPORTED_SETTINGS_KEYS),
    }


@router.patch("")
async def patch_settings(
    deps: TenantSession,
    payload: dict[str, str | bool | None],
) -> dict[str, Any]:
    """Upsert one or many keys in tenant_settings.config.

    Body is a flat dict like {"sender_name": "Meera", "require_draft_approval": true}.
    Pass each value as a string (or bool for require_draft_approval) — values are
    coerced via _coerce_value to match the CLI path.
    """
    session, tenant_id = deps
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(status_code=400, detail="Body must be a non-empty object")

    row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = TenantSettings(tenant_id=tenant_id, config={})
        session.add(row)
        await session.flush()

    new_config = dict(row.config or {})
    updated: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            # explicit clear
            new_config.pop(key, None)
            updated[key] = None
            continue
        # Validate + coerce using the same rules as the CLI
        string_value = str(value).lower() if isinstance(value, bool) else str(value)
        try:
            _validate_setting(key, string_value)
            coerced = _coerce_value(key, string_value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        new_config[key] = coerced
        updated[key] = coerced

    row.config = new_config
    await session.flush()
    # After flush, server_default'd columns (updated_at) are expired —
    # refresh explicitly so the response can serialize without a sync lazy load.
    await session.refresh(row, attribute_names=["updated_at"])

    return {
        "config": new_config,
        "updated": updated,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/health")
async def settings_health(session: AdminSession) -> dict[str, Any]:
    """Run health checks for the UI status dot (DB, migrations, prompts, env)."""
    report = await run_health_checks(session)
    return report.model_dump(mode="json")


# ── BYOK key management ──────────────────────────────────────────────────────


class ByokRequest(BaseModel):
    provider: str
    api_key: str


@router.post("/byok", status_code=204)
async def set_byok(deps: TenantSession, payload: ByokRequest) -> None:
    """Store an encrypted per-tenant LLM key. Validated against the provider
    before saving, so a typo'd key fails loudly here rather than silently at
    draft time."""
    from disha.tenant.byok import set_byok_key

    session, tenant_id = deps
    try:
        await set_byok_key(
            session, tenant_id, provider=payload.provider, api_key=payload.api_key
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── ICP wizard ────────────────────────────────────────────────────────────────


class IcpInterview(BaseModel):
    expertise: str
    problems_solved: str
    past_clients: str = ""
    outcomes: str = ""
    target_countries: str = ""   # free text, e.g. "India, US"
    target_stages: str = ""      # free text, e.g. "seed to series B"
    target_sizes: str = ""       # free text, e.g. "10-200 people"


_ICP_SYSTEM = """\
You turn a consultant's self-description into an ICP scoring rubric.

Return JSON exactly in this shape (weights must sum to 1.0):
{
  "name": "<short ICP name>",
  "dimensions": [
    {"key": "fit_funding", "label": "Funding stage fit", "weight": <0-1>,
     "scoring": "categorical",
     "rules": {"<stage>": <0-1>, ...}},          // stages: bootstrapped, seed, series_a, series_b, series_c
    {"key": "fit_size", "label": "Team size fit", "weight": <0-1>,
     "scoring": "categorical",
     "rules": {"1-10": <0-1>, "11-50": <0-1>, "51-200": <0-1>, "201-500": <0-1>, "500+": <0-1>}},
    {"key": "fit_role", "label": "Role fit", "weight": <0-1>,
     "scoring": "llm", "prompt_key": "score.role_fit"},
    {"key": "pain_signal", "label": "Pain signal strength", "weight": <0-1>,
     "scoring": "llm", "prompt_key": "score.pain_signal"},
    {"key": "fit_geo", "label": "Geo fit", "weight": <0-1>,
     "scoring": "categorical",
     "rules": {"<ISO-2>": <0-1>, ..., "other": <0-1>}}
  ]
}

Keep the two LLM dimensions and their prompt_key values EXACTLY as shown.
Derive the categorical rules from the consultant's answers; sensible defaults
where they were vague. Return ONLY JSON.
"""

_PERSONA_SYSTEM = """\
You extract a consultant's email-signature identity from their resume or
LinkedIn profile text.

Return JSON exactly:
{"sender_name": "<full name, or empty if unknown>",
 "sender_title": "<short role phrase, e.g. 'AI strategy consultant'>",
 "sender_credentials": "<one line, e.g. '10 yrs in product & AI · ex-Acme Corp, Globex'>"}

sender_title: under 6 words. sender_credentials: under 18 words, use ' · ' as
separators, lead with years of experience if stated, then notable companies.
Return ONLY JSON.
"""


def _strip_code_fence(raw: str) -> str:
    import re as _re

    return _re.sub(r"^```[a-z]*\n?", "", raw.strip()).rstrip("` \n")


async def _activate_rubric(session: Any, tenant_id: Any, config: dict[str, Any]) -> int:
    """Deactivate the current ICP rubric and insert `config` as the new active
    one. Returns the new version number."""
    from disha.db.models import IcpRubric

    current = (
        await session.execute(
            select(IcpRubric).where(
                IcpRubric.tenant_id == tenant_id, IcpRubric.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    next_version = (current.version + 1) if current else 1
    if current is not None:
        current.is_active = False
        await session.flush()
    session.add(
        IcpRubric(
            tenant_id=tenant_id, version=next_version, is_active=True, config=config
        )
    )
    return next_version


@router.post("/icp/generate")
async def generate_icp(deps: TenantSession, payload: IcpInterview) -> dict[str, Any]:
    """LLM-generate an ICP rubric from interview answers and activate it.

    The previous rubric is kept (is_active=False) so this is reversible
    from the DB if needed; versions increment monotonically.
    """
    import json as _json

    from disha.llm import LLMRequest, LLMRouter

    session, tenant_id = deps

    user_text = (
        f"Expertise: {payload.expertise}\n"
        f"Problems I solve: {payload.problems_solved}\n"
        f"Past clients: {payload.past_clients or 'none yet — first-time consultant'}\n"
        f"Outcomes delivered: {payload.outcomes or 'n/a'}\n"
        f"Target countries: {payload.target_countries or 'no preference'}\n"
        f"Target funding stages: {payload.target_stages or 'no preference'}\n"
        f"Target company sizes: {payload.target_sizes or 'no preference'}\n"
    )

    resp = await LLMRouter(tenant_id).call(
        LLMRequest(
            purpose="icp_generate",
            prompt_version=1,
            system=_ICP_SYSTEM,
            user=user_text,
            max_tokens=2048,
            temperature=0.2,
        )
    )
    try:
        config = _json.loads(_strip_code_fence(resp.content))
    except _json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502, detail="ICP generation returned invalid JSON — try again"
        ) from exc

    dims = config.get("dimensions") or []
    keys = {d.get("key") for d in dims}
    if not {"fit_role", "pain_signal"} <= keys:
        raise HTTPException(
            status_code=502, detail="Generated rubric is missing required dimensions — try again"
        )

    next_version = await _activate_rubric(session, tenant_id, config)
    return {"saved": True, "version": next_version, "config": config}


class IcpFromProfile(BaseModel):
    profile_text: str = ""        # pasted resume or LinkedIn profile text
    linkedin_url: str = ""        # optional — saved to the email signature too
    target_countries: str = ""
    target_stages: str = ""
    target_sizes: str = ""


async def _fill_blank_settings(
    session: Any, tenant_id: Any, values: dict[str, str]
) -> dict[str, str]:
    """Set tenant_settings keys only where currently blank (non-destructive).
    Returns the keys actually written."""
    row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = TenantSettings(tenant_id=tenant_id, config={})
        session.add(row)
        await session.flush()
    config = dict(row.config or {})
    written: dict[str, str] = {}
    for key, val in values.items():
        if val and not str(config.get(key) or "").strip():
            config[key] = val
            written[key] = val
    if written:
        row.config = config
    return written


@router.post("/icp/generate-from-profile")
async def generate_icp_from_profile(
    deps: TenantSession, payload: IcpFromProfile
) -> dict[str, Any]:
    """Auto-generate the ICP from a pasted resume / LinkedIn profile, and
    auto-fill the email persona (name, title, credentials) from the same text."""
    import json as _json

    from disha.llm import LLMRequest, LLMRouter

    session, tenant_id = deps
    profile = payload.profile_text.strip()
    if len(profile) < 40:
        raise HTTPException(
            status_code=400,
            detail="Paste a bit more of your resume or LinkedIn profile (a few sentences).",
        )

    targets = (
        f"\n\nTargeting preferences (optional):\n"
        f"Countries: {payload.target_countries or 'no preference'}\n"
        f"Funding stages: {payload.target_stages or 'no preference'}\n"
        f"Company sizes: {payload.target_sizes or 'no preference'}\n"
    )
    user_text = (
        "Here is the consultant's resume / LinkedIn profile. Infer their "
        "expertise, the problems they solve, who they've worked with, and the "
        "outcomes they deliver, then derive the ICP.\n\n"
        f"PROFILE:\n{profile}{targets}"
    )

    router = LLMRouter(tenant_id)
    resp = await router.call(
        LLMRequest(
            purpose="icp_generate",
            prompt_version=1,
            system=_ICP_SYSTEM,
            user=user_text,
            max_tokens=2048,
            temperature=0.2,
        )
    )
    try:
        config = _json.loads(_strip_code_fence(resp.content))
    except _json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502, detail="ICP generation returned invalid JSON — try again"
        ) from exc
    keys = {d.get("key") for d in (config.get("dimensions") or [])}
    if not {"fit_role", "pain_signal"} <= keys:
        raise HTTPException(
            status_code=502,
            detail="Generated rubric is missing required dimensions — try again",
        )
    next_version = await _activate_rubric(session, tenant_id, config)

    # Best-effort persona auto-fill from the same profile (never blocks the ICP).
    persona: dict[str, str] = {}
    try:
        presp = await router.call(
            LLMRequest(
                purpose="icp_generate",
                prompt_version=1,
                system=_PERSONA_SYSTEM,
                user=profile,
                max_tokens=300,
                temperature=0.1,
            )
        )
        extracted = _json.loads(_strip_code_fence(presp.content))
        to_fill = {
            "sender_name": str(extracted.get("sender_name") or "").strip(),
            "sender_title": str(extracted.get("sender_title") or "").strip(),
            "sender_credentials": str(extracted.get("sender_credentials") or "").strip(),
        }
        if payload.linkedin_url.strip():
            to_fill["linkedin_url"] = payload.linkedin_url.strip()
        persona = await _fill_blank_settings(session, tenant_id, to_fill)
    except Exception:  # noqa: BLE001 — persona is a bonus, ICP already saved
        if payload.linkedin_url.strip():
            persona = await _fill_blank_settings(
                session, tenant_id, {"linkedin_url": payload.linkedin_url.strip()}
            )

    return {
        "saved": True,
        "version": next_version,
        "config": config,
        "persona_filled": persona,
    }


@router.get("/icp")
async def get_icp(deps: TenantSession) -> dict[str, Any]:
    """Current active ICP rubric (for display in the wizard card)."""
    from disha.db.models import IcpRubric

    session, tenant_id = deps
    row = (
        await session.execute(
            select(IcpRubric).where(
                IcpRubric.tenant_id == tenant_id, IcpRubric.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    return {
        "version": row.version if row else None,
        "config": dict(row.config) if row and row.config else None,
    }


# Multi-tenant slug helper kept for parity with CLI (`disha tenant show`)
@router.get("/by-slug/{slug}")
async def settings_by_slug(session: AdminSession, slug: str) -> dict[str, Any]:
    tenant = await get_tenant_by_slug(session, slug)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant {slug!r} not found")
    row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
        )
    ).scalar_one_or_none()
    return {
        "tenant": tenant.slug,
        "config": dict(row.config) if row else {},
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }
