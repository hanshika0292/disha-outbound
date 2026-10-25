"""Lead scorer: combines rule-based + LLM-scored dimensions per the ICP rubric.

A dimension's `scoring` is "categorical" (rule-table lookup) or "llm" (router call).
Final score is sum of weight*raw_score. Tier comes from the rubric's tiers table.
Idempotent at the (rubric_version, prompt_version, lead_data) granularity:
re-scoring with the same versions yields the same number (LLM temperature is 0.0).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.db.models import Account, GeoProfile, IcpRubric, Lead, LeadStateRow, PromptPack
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.db.models.enums import Tier
from disha.intelligence.context import build_lead_context
from disha.llm import LLMRequest, LLMRouter


class ScoreBreakdown(BaseModel):
    dimension: str
    raw_score: float
    weight: float
    weighted_score: float
    reasoning: str | None = None


class ScoreResult(BaseModel):
    lead_id: UUID
    total_score: float
    tier: Literal["A", "B", "C", "skip"]
    breakdown: list[ScoreBreakdown]
    rubric_version: int
    prompt_version: int
    scored_at: datetime


def _categorical_score(rules: dict[str, float], value: Any) -> float:
    if value is None:
        return 0.0
    key = str(value)
    if key in rules:
        return float(rules[key])
    return float(rules.get("other", 0.0))


def _categorical_value_for(dim_key: str, ctx: dict[str, Any]) -> Any:
    mapping = {
        "fit_funding": ctx.get("funding_stage"),
        "fit_size": ctx.get("employee_count_bucket"),
        "fit_geo": ctx.get("hq_country"),
    }
    return mapping.get(dim_key)


def _tier_for(score: float, tiers: list[dict[str, Any]]) -> Tier:
    for entry in sorted(tiers, key=lambda t: -float(t["min_score"])):
        if score >= float(entry["min_score"]):
            label = entry["label"]
            return Tier(label)
    return Tier.SKIP


async def _load_lead(session: AsyncSession, lead_id: UUID) -> tuple[Lead, Account | None]:
    lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()
    account = None
    if lead.account_id is not None:
        account = (
            await session.execute(select(Account).where(Account.id == lead.account_id))
        ).scalar_one_or_none()
    return lead, account


async def _load_rubric(session: AsyncSession, tenant_id: UUID) -> IcpRubric:
    result = await session.execute(
        select(IcpRubric).where(IcpRubric.tenant_id == tenant_id, IcpRubric.is_active.is_(True))
    )
    return result.scalar_one()


async def _load_prompts(session: AsyncSession, tenant_id: UUID) -> PromptPack:
    result = await session.execute(
        select(PromptPack).where(PromptPack.tenant_id == tenant_id, PromptPack.is_active.is_(True))
    )
    return result.scalar_one()


async def _load_geo_profile(session: AsyncSession, tenant_id: UUID) -> GeoProfile | None:
    result = await session.execute(
        select(GeoProfile).where(GeoProfile.tenant_id == tenant_id, GeoProfile.is_active.is_(True))
    )
    return result.scalar_one_or_none()


def _is_blocked_country(geo: GeoProfile | None, country: str | None) -> bool:
    if geo is None or country is None:
        return False
    blocked = geo.config.get("blocked", [])
    return country in blocked


# Tier ranking for the auto-approve bar (A is best). SKIP never qualifies.
_TIER_RANK = {"A": 3, "B": 2, "C": 1, "skip": 0}


async def _maybe_auto_approve(
    session: AsyncSession, tenant_id: UUID, state_row: LeadStateRow, tier: Tier
) -> None:
    """Move a freshly-scored lead to APPROVED if the tenant enabled hands-off
    lead approval and the lead's tier meets their configured bar."""
    from disha.db.models import TenantSettings

    if state_row.state != LeadStateEnum.SCORED:
        return  # only auto-approve from the scored gate, never override states

    row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    config = dict(row.config) if row else {}
    if not config.get("auto_approve_leads"):
        return

    bar = str(config.get("auto_approve_tier", "B")).upper()
    if _TIER_RANK.get(tier.value, 0) >= _TIER_RANK.get(bar, 2):
        state_row.state = LeadStateEnum.APPROVED


async def _record_skip(
    *,
    session: AsyncSession,
    tenant_id: UUID,
    lead: Lead,
    prompt_version: int,
    rubric_version: int,
    reason: str,
) -> ScoreResult:
    """Persist a skipped lead with tier=skip and a reason; return a degenerate ScoreResult."""
    state_row = (
        await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead.id))
    ).scalar_one_or_none()
    if state_row is None:
        session.add(
            LeadStateRow(
                lead_id=lead.id,
                tenant_id=tenant_id,
                state=LeadStateEnum.SKIPPED,
                score=Decimal("0.000"),
                tier=Tier.SKIP,
                scored_with_prompt_version=prompt_version,
            )
        )
    else:
        state_row.state = LeadStateEnum.SKIPPED
        state_row.score = Decimal("0.000")
        state_row.tier = Tier.SKIP
        state_row.scored_with_prompt_version = prompt_version

    return ScoreResult(
        lead_id=lead.id,
        total_score=0.0,
        tier="skip",
        breakdown=[
            ScoreBreakdown(
                dimension="_skip",
                raw_score=0.0,
                weight=0.0,
                weighted_score=0.0,
                reasoning=f"short-circuited: {reason}",
            )
        ],
        rubric_version=rubric_version,
        prompt_version=prompt_version,
        scored_at=datetime.now(UTC),
    )


async def score_lead(
    session: AsyncSession,
    tenant_id: UUID,
    lead_id: UUID,
    router: LLMRouter,
) -> ScoreResult:
    lead, account = await _load_lead(session, lead_id)
    rubric = await _load_rubric(session, tenant_id)
    prompts = await _load_prompts(session, tenant_id)
    geo_profile = await _load_geo_profile(session, tenant_id)
    ctx = build_lead_context(lead, account)
    prompts_map: dict[str, Any] = prompts.config.get("prompts", {})

    if _is_blocked_country(geo_profile, ctx.get("hq_country")):
        return await _record_skip(
            session=session,
            tenant_id=tenant_id,
            lead=lead,
            prompt_version=prompts.version,
            rubric_version=rubric.version,
            reason="blocked_geo",
        )

    breakdown: list[ScoreBreakdown] = []
    total = 0.0
    for dim in rubric.config["dimensions"]:
        key: str = dim["key"]
        weight: float = float(dim["weight"])
        scoring: str = dim["scoring"]
        raw: float
        reasoning: str | None = None

        if scoring == "categorical":
            raw = _categorical_score(dim["rules"], _categorical_value_for(key, ctx))
        elif scoring == "llm":
            prompt_def = prompts_map[dim["prompt_key"]]
            user = prompt_def["user_template"].format(**ctx)
            req = LLMRequest(
                purpose=dim["prompt_key"],
                system=prompt_def["system"],
                user=user,
                output_schema=prompt_def.get("output_schema"),
                model_preference=prompt_def.get("model_preference", "fast"),
                temperature=float(prompt_def.get("temperature", 0.0)),
                # Headroom so the {score, reasoning} JSON never truncates mid-
                # string (the 1024 default cut off long reasoning → SchemaError
                # → unscored lead). 2048 is ample for a score + paragraph.
                max_tokens=int(prompt_def.get("max_tokens", 2048)),
                prompt_version=prompts.version,
                entity_type="lead",
                entity_id=lead.id,
            )
            resp = await router.call(req)
            content = resp.content if isinstance(resp.content, dict) else {}
            raw = float(content.get("score", 0.0))
            reasoning = content.get("reasoning")
        else:
            raise ValueError(f"Unknown scoring strategy: {scoring!r} for dim {key!r}")

        weighted = raw * weight
        total += weighted
        breakdown.append(
            ScoreBreakdown(
                dimension=key,
                raw_score=raw,
                weight=weight,
                weighted_score=weighted,
                reasoning=reasoning,
            )
        )

    tier = _tier_for(total, rubric.config["tiers"])
    scored_at = datetime.now(UTC)
    # (auto-approval helper defined at module level — see _maybe_auto_approve)

    state_row = (
        await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead.id))
    ).scalar_one_or_none()
    if state_row is None:
        session.add(
            LeadStateRow(
                lead_id=lead.id,
                tenant_id=tenant_id,
                state=LeadStateEnum.SCORED,
                score=Decimal(f"{total:.3f}"),
                tier=tier,
                scored_with_prompt_version=prompts.version,
            )
        )
        state_row = (
            await session.execute(
                select(LeadStateRow).where(LeadStateRow.lead_id == lead.id)
            )
        ).scalar_one()
    else:
        state_row.state = LeadStateEnum.SCORED
        state_row.score = Decimal(f"{total:.3f}")
        state_row.tier = tier
        state_row.scored_with_prompt_version = prompts.version

    # Auto-approval: if the tenant opted into hands-off lead approval and this
    # lead's tier meets their bar, move it straight to APPROVED (into the
    # sequence) instead of parking it in SCORED. Sending guardrails (verified
    # email only, daily ramp) still apply downstream, so this is safe.
    await _maybe_auto_approve(session, tenant_id, state_row, tier)

    return ScoreResult(
        lead_id=lead.id,
        total_score=round(total, 4),
        tier=tier.value,
        breakdown=breakdown,
        rubric_version=rubric.version,
        prompt_version=prompts.version,
        scored_at=scored_at,
    )
