"""Reply handler: classify a new reply and apply the corresponding state transition.

Called by the Gmail poller after a new reply row is inserted. Mapping:

  interested / meeting_request → lead_states.state='replied'
  out_of_office                → pause 5 days
  not_now                      → pause 14 days
  referral                     → no state change, add signal + event
  unsubscribe_or_not_interested → state='unsubscribed' (classifier already
                                   added platform_suppressions entry)
  low confidence (<0.5)        → no state change, emit reply.low_confidence
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from disha.db.models import Event, LeadStateRow, Reply, Signal, Thread, Touch
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.intelligence import classify_reply
from disha.llm import LLMRouter

logger = logging.getLogger(__name__)

_PAUSE_DAYS = {
    "out_of_office": 5,
    "not_now": 14,
}

_REPLIED_INTENTS = {"interested", "meeting_request", "positive_vague"}

_LOW_CONFIDENCE_THRESHOLD = 0.5


async def _emit(
    session: AsyncSession,
    tenant_id: UUID,
    event_type: str,
    entity_id: UUID,
    payload: dict[str, Any],
) -> None:
    session.add(
        Event(
            id=str(ULID()),
            tenant_id=tenant_id,
            entity_type="lead",
            entity_id=entity_id,
            event_type=event_type,
            payload=payload,
            actor="reply_handler",
        )
    )


async def _load_lead_id_for_reply(session: AsyncSession, reply: Reply) -> UUID:
    touch = (await session.execute(select(Touch).where(Touch.id == reply.touch_id))).scalar_one()
    thread = (
        await session.execute(select(Thread).where(Thread.id == touch.thread_id))
    ).scalar_one()
    return thread.lead_id


async def _load_or_create_state(
    session: AsyncSession, tenant_id: UUID, lead_id: UUID
) -> LeadStateRow:
    state = (
        await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
    ).scalar_one_or_none()
    if state is None:
        state = LeadStateRow(
            lead_id=lead_id,
            tenant_id=tenant_id,
            state=LeadStateEnum.IN_SEQUENCE,
        )
        session.add(state)
        await session.flush()
    return state


async def _notify_user(
    session: AsyncSession, tenant_id: UUID, reply: Reply, intent: str
) -> None:
    """Email the user the moment a positive reply lands. Speed-to-response is
    the single biggest lever on booking rate — the user isn't living in this
    app, so we go to their inbox. Best-effort: a notification failure must
    never break reply handling."""
    try:
        from disha.adapters.gmail import GmailAdapter
        from disha.config import get_settings
        from disha.db.models import TenantSettings

        settings_row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        config = dict(settings_row.config) if settings_row else {}
        notify_to = config.get("report_email")
        if not notify_to:
            return

        ui_url = f"{get_settings().disha_ui_url.rstrip('/')}/inbox"
        sender = reply.sender_email or "a prospect"
        body = (
            f"{sender} replied to your outreach — intent: {intent}.\n\n"
            f"Subject: {reply.subject or '(no subject)'}\n\n"
            f"{reply.body[:600]}\n\n"
            f"---\n"
            f"Reply fast — open your inbox to send an AI-drafted response:\n{ui_url}\n"
        )
        await GmailAdapter().send_raw(
            session,
            tenant_id,
            to_addr=str(notify_to),
            subject=f"Reply from {sender} ({intent.replace('_', ' ')})",
            body=body,
        )
    except Exception:  # noqa: BLE001
        logger.exception("reply notification failed (non-fatal)")


async def handle_new_reply(
    session: AsyncSession,
    tenant_id: UUID,
    reply_id: UUID,
    *,
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """Classify the reply (idempotent — classifier sets replies.classification) and
    apply the state transition. Returns a small summary for logging/CLI display.
    """
    used_router = router or LLMRouter(tenant_id)
    reply = (await session.execute(select(Reply).where(Reply.id == reply_id))).scalar_one()

    # Classify only if not already classified (idempotent re-runs).
    if reply.classification is None:
        await classify_reply(session, tenant_id, reply_id, used_router)
        # Reload to get the freshly-written classification jsonb.
        reply = (await session.execute(select(Reply).where(Reply.id == reply_id))).scalar_one()

    classification = reply.classification or {}
    intent = str(classification.get("intent", "")) if classification else ""
    confidence = float(classification.get("confidence", 0.0)) if classification else 0.0
    lead_id = await _load_lead_id_for_reply(session, reply)
    state = await _load_or_create_state(session, tenant_id, lead_id)

    summary: dict[str, Any] = {
        "reply_id": str(reply_id),
        "lead_id": str(lead_id),
        "intent": intent,
        "confidence": confidence,
        "action": "no_change",
    }

    if confidence < _LOW_CONFIDENCE_THRESHOLD and intent not in {
        "unsubscribe_or_not_interested",
    }:
        await _emit(
            session,
            tenant_id,
            "reply.low_confidence",
            lead_id,
            {"intent": intent, "confidence": confidence, "reply_id": str(reply_id)},
        )
        summary["action"] = "low_confidence_review"
        return summary

    if intent in _REPLIED_INTENTS:
        state.state = LeadStateEnum.REPLIED
        await _emit(
            session,
            tenant_id,
            "lead.replied",
            lead_id,
            {
                "intent": intent,
                "next_action": "book_call" if intent == "meeting_request" else "engage",
            },
        )
        summary["action"] = "state=replied"
        await _notify_user(session, tenant_id, reply, intent)
        return summary

    if intent in _PAUSE_DAYS:
        days = _PAUSE_DAYS[intent]
        state.sequence_paused_until = datetime.now(UTC) + timedelta(days=days)
        await _emit(
            session,
            tenant_id,
            "lead.sequence_paused",
            lead_id,
            {"reason": intent, "days": days},
        )
        summary["action"] = f"paused_{days}d"
        return summary

    if intent == "referral":
        session.add(
            Signal(
                tenant_id=tenant_id,
                lead_id=lead_id,
                type="referral_signal",
                source="reply_classification",
                payload={"text": reply.body[:500], "strength": 0.8},
                observed_at=datetime.now(UTC),
            )
        )
        await _emit(
            session,
            tenant_id,
            "lead.referral_received",
            lead_id,
            {"body_preview": reply.body[:200]},
        )
        summary["action"] = "referral_signal_added"
        return summary

    if intent == "unsubscribe_or_not_interested":
        if state.state != LeadStateEnum.UNSUBSCRIBED:
            state.state = LeadStateEnum.UNSUBSCRIBED
        summary["action"] = "state=unsubscribed"
        return summary

    if intent == "gatekeeper":
        # No state change; the classifier already extracted alternative_contact.
        summary["action"] = "gatekeeper_no_change"
        return summary

    return summary
