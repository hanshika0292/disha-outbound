"""Classify inbound replies; update replies.classification; on unsubscribe → suppression."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.db.models import Lead, PlatformSuppression, PromptPack, Reply, Touch
from disha.db.models.enums import SuppressionType
from disha.llm import LLMRequest, LLMRouter

Intent = Literal[
    "meeting_request",
    "objection",
    "positive_vague",
    "unsubscribe_or_not_interested",
    "gatekeeper",
    "auto_reply",
]


class ClassificationResult(BaseModel):
    reply_id: UUID
    intent: Intent
    confidence: float
    reasoning: str
    suggested_response_type: str
    extracted_entities: dict[str, Any]
    model_used: str
    cost_usd: Decimal


async def _load_reply(session: AsyncSession, reply_id: UUID) -> tuple[Reply, Touch, Lead]:
    from disha.db.models import Thread

    reply = (await session.execute(select(Reply).where(Reply.id == reply_id))).scalar_one()
    touch = (await session.execute(select(Touch).where(Touch.id == reply.touch_id))).scalar_one()
    thread = (
        await session.execute(select(Thread).where(Thread.id == touch.thread_id))
    ).scalar_one()
    lead = (await session.execute(select(Lead).where(Lead.id == thread.lead_id))).scalar_one()
    return reply, touch, lead


async def classify_reply(
    session: AsyncSession,
    tenant_id: UUID,
    reply_id: UUID,
    router: LLMRouter,
) -> ClassificationResult:
    reply, touch, lead = await _load_reply(session, reply_id)
    prompts = (
        await session.execute(
            select(PromptPack).where(
                PromptPack.tenant_id == tenant_id, PromptPack.is_active.is_(True)
            )
        )
    ).scalar_one()

    prompt_def = prompts.config["prompts"]["classify.reply"]
    user = prompt_def["user_template"].format(
        original_subject=touch.subject or "(no subject)",
        original_body=touch.body,
        reply_body=reply.body,
    )

    req = LLMRequest(
        purpose="classify.reply",
        system=prompt_def["system"],
        user=user,
        output_schema=prompt_def.get("output_schema"),
        model_preference=prompt_def.get("model_preference", "fast"),
        temperature=float(prompt_def.get("temperature", 0.0)),
        prompt_version=prompts.version,
        entity_type="reply",
        entity_id=reply.id,
    )
    resp = await router.call(req)
    content = resp.content if isinstance(resp.content, dict) else {}

    intent: Intent = content.get("intent", "positive_vague")
    confidence = float(content.get("confidence", 0.0))
    reasoning = content.get("reasoning", "")
    suggested = content.get("suggested_response_type", "wait")
    entities = content.get("extracted_entities", {}) or {}

    reply.classification = {
        "intent": intent,
        "confidence": confidence,
        "reasoning": reasoning,
        "suggested_response_type": suggested,
        "extracted_entities": entities,
        "model_used": resp.model_used,
        "prompt_version": prompts.version,
    }

    if intent == "unsubscribe_or_not_interested":
        await _add_suppression_from_lead(session, lead)

    return ClassificationResult(
        reply_id=reply.id,
        intent=intent,
        confidence=confidence,
        reasoning=reasoning,
        suggested_response_type=suggested,
        extracted_entities=entities,
        model_used=resp.model_used,
        cost_usd=resp.cost_usd,
    )


async def _add_suppression_from_lead(session: AsyncSession, lead: Lead) -> None:
    """Hash the lead's email_hash as the suppression value (platform-level, global)."""
    value_hash = hashlib.sha256(lead.email_hash).digest()
    existing = (
        await session.execute(
            select(PlatformSuppression).where(PlatformSuppression.value_hash == value_hash)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(
        PlatformSuppression(
            value_hash=value_hash,
            value_type=SuppressionType.EMAIL,
            reason="reply intent = unsubscribe_or_not_interested",
        )
    )
