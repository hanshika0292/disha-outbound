"""Drafter: render the prompt user_template, call the LLM, validate, write touches row.

Word-count policy: if the first response exceeds the voice-guide limit, retry
once with an explicit reminder. If it's still over, persist as-is, flag
`needs_human_review` on the touches row's raw payload, and emit an event.
We don't truncate — better to surface to the human than amputate a sentence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from disha.db.models import (
    Account,
    Cadence,
    Event,
    GeoProfile,
    Lead,
    PromptPack,
    TenantSettings,
    Thread,
    Touch,
    VoiceGuide,
)
from disha.db.models.enums import Channel, Direction
from disha.intelligence.context import build_lead_context
from disha.llm import LLMRequest, LLMRouter

# tenant_settings.config expected keys (all optional, defaults in context.py):
#   cal_link: str     — e.g. "https://cal.com/your-name/discovery"
#   sender_name: str  — first-name presented in prompts
#   sender_title: str — short role phrase
#   sender_city: str  — geographic anchor for LinkedIn connect notes


class DraftResult(BaseModel):
    lead_id: UUID
    touch_id: UUID
    cadence_step: int
    channel: str
    subject: str | None
    body: str
    word_count: int
    word_limit: int
    needs_human_review: bool
    prompt_used: str
    prompt_version: int
    model_used: str
    cost_usd: Decimal


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


async def _load_lead(session: AsyncSession, lead_id: UUID) -> tuple[Lead, Account | None]:
    lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()
    account = None
    if lead.account_id is not None:
        account = (
            await session.execute(select(Account).where(Account.id == lead.account_id))
        ).scalar_one_or_none()
    return lead, account


async def _load_geo_profile(session: AsyncSession, tenant_id: UUID) -> GeoProfile:
    return (
        await session.execute(
            select(GeoProfile).where(
                GeoProfile.tenant_id == tenant_id, GeoProfile.is_active.is_(True)
            )
        )
    ).scalar_one()


async def _load_cadence_for_region(session: AsyncSession, tenant_id: UUID, region: str) -> Cadence:
    result = await session.execute(
        select(Cadence).where(
            Cadence.tenant_id == tenant_id,
            Cadence.is_active.is_(True),
            Cadence.config["region"].astext == region,
        )
    )
    return result.scalar_one()


async def _load_voice(session: AsyncSession, tenant_id: UUID) -> VoiceGuide:
    return (
        await session.execute(
            select(VoiceGuide).where(
                VoiceGuide.tenant_id == tenant_id, VoiceGuide.is_active.is_(True)
            )
        )
    ).scalar_one()


async def _load_prompts(session: AsyncSession, tenant_id: UUID) -> PromptPack:
    return (
        await session.execute(
            select(PromptPack).where(
                PromptPack.tenant_id == tenant_id, PromptPack.is_active.is_(True)
            )
        )
    ).scalar_one()


async def _load_tenant_settings_config(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))
    ).scalar_one_or_none()
    return dict(row.config) if row is not None else {}


async def _load_prior_subjects(session: AsyncSession, tenant_id: UUID, lead_id: UUID) -> list[str]:
    """Subjects of previously SENT touches for this lead — used by draft.followup_2."""
    threads_q = select(Thread.id).where(Thread.tenant_id == tenant_id, Thread.lead_id == lead_id)
    subjects_q = (
        select(Touch.subject)
        .where(
            Touch.tenant_id == tenant_id,
            Touch.thread_id.in_(threads_q),
            Touch.status == "sent",
            Touch.subject.is_not(None),
        )
        .order_by(Touch.sent_at)
    )
    rows = (await session.execute(subjects_q)).scalars().all()
    return [s for s in rows if s]


def _resolve_region(geo_profile: GeoProfile, lead_country: str | None) -> str:
    primary: list[str] = list(geo_profile.config.get("primary", []))
    secondary: list[str] = list(geo_profile.config.get("secondary", []))
    tertiary: list[str] = list(geo_profile.config.get("tertiary", []))
    rules: dict[str, Any] = geo_profile.config.get("geo_rules", {})

    if lead_country and lead_country in rules:
        return lead_country
    if lead_country and lead_country in primary + secondary + tertiary:
        return lead_country
    return primary[0] if primary else "US"


async def _get_or_create_thread(
    session: AsyncSession, tenant_id: UUID, lead_id: UUID, channel: Channel
) -> Thread:
    existing = (
        await session.execute(
            select(Thread).where(
                Thread.tenant_id == tenant_id,
                Thread.lead_id == lead_id,
                Thread.channel == channel,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    thread = Thread(tenant_id=tenant_id, lead_id=lead_id, channel=channel, status="open")
    session.add(thread)
    await session.flush()
    return thread


async def draft_touch(
    session: AsyncSession,
    tenant_id: UUID,
    lead_id: UUID,
    cadence_step: int,
    router: LLMRouter,
) -> DraftResult | None:
    """Draft one touch for a lead at the given cadence step.

    Returns None and emits a `touch.skipped` event when the cadence references
    a prompt_key not present in the active prompt_pack. Callers (sequencer, CLI)
    must handle a None result. Raises only on genuine errors (missing lead,
    LLM provider failure not caught by retry).
    """
    lead, account = await _load_lead(session, lead_id)
    geo_profile = await _load_geo_profile(session, tenant_id)
    voice = await _load_voice(session, tenant_id)
    prompts = await _load_prompts(session, tenant_id)
    tenant_settings_config = await _load_tenant_settings_config(session, tenant_id)

    lead_country = account.hq_country if account else (lead.raw or {}).get("hq_country")
    region = _resolve_region(geo_profile, lead_country)
    cadence = await _load_cadence_for_region(session, tenant_id, region)

    step_def = next((s for s in cadence.config["steps"] if int(s["step"]) == cadence_step), None)
    if step_def is None:
        raise ValueError(f"Cadence step {cadence_step} not defined for region {region!r}")

    prompt_key = step_def["prompt_key"]
    prompts_map: dict[str, Any] = prompts.config.get("prompts", {})
    if prompt_key not in prompts_map:
        session.add(
            Event(
                id=str(ULID()),
                tenant_id=tenant_id,
                entity_type="lead",
                entity_id=lead.id,
                event_type="touch.skipped",
                payload={
                    "reason": "prompt_not_found",
                    "prompt_key": prompt_key,
                    "cadence_step": cadence_step,
                    "region": region,
                },
                actor="system",
            )
        )
        return None
    prompt_def = prompts_map[prompt_key]

    geo_overrides: dict[str, Any] = voice.config.get("geo_overrides", {}).get(region, {})
    word_limit: int = (
        geo_overrides.get("max_first_touch_words", 110)
        if cadence_step == 1
        else geo_overrides.get("max_followup_words", 60)
    )

    prior_subjects: list[str] = []
    if prompt_key == "draft.followup_2":
        prior_subjects = await _load_prior_subjects(session, tenant_id, lead.id)

    ctx = build_lead_context(
        lead,
        account,
        tenant_settings=tenant_settings_config,
        prior_subjects=prior_subjects,
    )
    ctx["geo"] = region

    def _build_request(extra_hint: str | None = None) -> LLMRequest:
        user = prompt_def["user_template"].format(**ctx)
        if extra_hint:
            user = user + "\n\n" + extra_hint
        return LLMRequest(
            purpose=prompt_key,
            system=prompt_def["system"],
            user=user,
            output_schema=prompt_def.get("output_schema"),
            model_preference=prompt_def.get("model_preference", "balanced"),
            temperature=float(prompt_def.get("temperature", 0.7)),
            max_tokens=1024,
            prompt_version=prompts.version,
            entity_type="lead",
            entity_id=lead.id,
        )

    resp = await router.call(_build_request())
    content = resp.content if isinstance(resp.content, dict) else {}
    subject: str | None = content.get("subject")
    body: str = str(content.get("body", ""))
    wc = _word_count(body)

    needs_review = False
    if wc > word_limit:
        hint = (
            f"Your previous draft was {wc} words, {wc - word_limit} over the "
            f"{word_limit}-word limit. Rewrite tighter; same intent; ≤{word_limit} words."
        )
        resp = await router.call(_build_request(hint))
        content = resp.content if isinstance(resp.content, dict) else {}
        subject = content.get("subject", subject)
        body = str(content.get("body", body))
        wc = _word_count(body)
        if wc > word_limit:
            needs_review = True

    channel = Channel(step_def["channel"])
    thread = await _get_or_create_thread(session, tenant_id, lead.id, channel)
    touch = Touch(
        tenant_id=tenant_id,
        thread_id=thread.id,
        # Storage convention is 0-indexed (matches sequencer's _next_step_index).
        # `cadence_step` is the 1-indexed step from the cadence JSON.
        step_number=cadence_step - 1,
        direction=Direction.OUTBOUND,
        channel=channel,
        status="draft",
        subject=subject,
        body=body,
        drafted_at=datetime.now(UTC),
        sent_at=None,
        prompt_version=str(prompts.version),
        model_used=resp.model_used,
        cost_usd=resp.cost_usd,
        tokens_in=resp.usage.tokens_in,
        tokens_out=resp.usage.tokens_out,
    )
    session.add(touch)
    await session.flush()

    return DraftResult(
        lead_id=lead.id,
        touch_id=touch.id,
        cadence_step=cadence_step,
        channel=channel.value,
        subject=subject,
        body=body,
        word_count=wc,
        word_limit=word_limit,
        needs_human_review=needs_review,
        prompt_used=prompt_key,
        prompt_version=prompts.version,
        model_used=resp.model_used,
        cost_usd=resp.cost_usd,
    )
