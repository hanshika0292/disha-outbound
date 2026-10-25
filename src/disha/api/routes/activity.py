"""Activity log + daily worklist — plain-English transparency.

GET /activity         → unified timeline of what the machine did and what's
                        queued next, in human language.
GET /activity/worklist → what needs the user TODAY, with target routes.

Built by merging real rows (sent touches, replies, manual actions) rather than
re-instrumenting the whole app — so it reflects reality, not a parallel log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from disha.api.auth import require_auth
from disha.api.deps import TenantSession
from disha.db.models import (
    Account,
    Lead,
    LeadStateRow,
    ManualAction,
    Reply,
    TenantSettings,
    Thread,
    Touch,
)
from disha.db.models.enums import Channel

router = APIRouter(prefix="/activity", tags=["activity"], dependencies=[Depends(require_auth)])


def _name(first: str | None, last: str | None) -> str:
    return f"{first or ''} {last or ''}".strip() or "a lead"


# Plain-English labels for reply intents.
_INTENT_TEXT = {
    "interested": "is interested",
    "meeting_request": "wants to meet",
    "positive_vague": "replied positively",
    "not_now": "said not now",
    "out_of_office": "is out of office",
    "referral": "referred someone",
    "unsubscribe_or_not_interested": "asked to stop",
    "gatekeeper": "is a gatekeeper",
}


@router.get("")
async def activity(
    deps: TenantSession, limit: int = Query(40, ge=1, le=100)
) -> dict[str, Any]:
    session, tenant_id = deps
    items: list[dict[str, Any]] = []

    # 1. Sent + failed email touches
    sent = (
        await session.execute(
            select(Touch, Lead)
            .join(Thread, Thread.id == Touch.thread_id)
            .join(Lead, Lead.id == Thread.lead_id)
            .where(
                Touch.tenant_id == tenant_id,
                Touch.channel == Channel.EMAIL,
                Touch.status.in_(("sent", "failed")),
                Touch.sent_at.is_not(None),
            )
            .order_by(Touch.sent_at.desc())
            .limit(limit)
        )
    ).all()
    for touch, lead in sent:
        nm = _name(lead.first_name, lead.last_name)
        if touch.status == "sent":
            items.append({
                "ts": touch.sent_at.isoformat(),
                "kind": "sent",
                "text": f"Emailed {nm}",
                "detail": touch.subject or "",
                "lead_id": str(lead.id),
            })
        else:
            items.append({
                "ts": touch.sent_at.isoformat(),
                "kind": "failed",
                "text": f"Send to {nm} failed",
                "detail": (touch.error_message or "")[:120],
                "lead_id": str(lead.id),
            })

    # 2. Replies received
    replies = (
        await session.execute(
            select(Reply, Lead)
            .join(Touch, Touch.id == Reply.touch_id)
            .join(Thread, Thread.id == Touch.thread_id)
            .join(Lead, Lead.id == Thread.lead_id)
            .where(Reply.tenant_id == tenant_id)
            .order_by(Reply.received_at.desc())
            .limit(limit)
        )
    ).all()
    for reply, lead in replies:
        nm = _name(lead.first_name, lead.last_name)
        intent = (reply.classification or {}).get("intent", "")
        items.append({
            "ts": reply.received_at.isoformat(),
            "kind": "reply",
            "text": f"{nm} {_INTENT_TEXT.get(intent, 'replied')}",
            "detail": (reply.subject or reply.body or "")[:120],
            "lead_id": str(lead.id),
        })

    # 3. LinkedIn touches queued / done
    actions = (
        await session.execute(
            select(ManualAction, Lead)
            .join(Lead, Lead.id == ManualAction.lead_id)
            .where(ManualAction.tenant_id == tenant_id)
            .order_by(ManualAction.created_at.desc())
            .limit(limit)
        )
    ).all()
    for a, lead in actions:
        nm = _name(lead.first_name, lead.last_name)
        when = a.completed_at or a.created_at
        verb = {
            "done": f"Sent LinkedIn note to {nm}",
            "skipped": f"Skipped {nm} on LinkedIn",
            "pending": f"Queued LinkedIn note for {nm}",
        }.get(a.status, f"LinkedIn: {nm}")
        items.append({
            "ts": when.isoformat(),
            "kind": "linkedin",
            "text": verb,
            "detail": "",
            "lead_id": str(lead.id),
        })

    items.sort(key=lambda x: x["ts"], reverse=True)
    return {"items": items[:limit]}


@router.get("/worklist")
async def worklist(deps: TenantSession) -> dict[str, Any]:
    """What needs the user today — each item is a one-click jump."""
    session, tenant_id = deps

    async def _count(stmt) -> int:  # type: ignore[no-untyped-def]
        return int((await session.execute(stmt)).scalar_one() or 0)

    leads_to_review = await _count(
        select(func.count()).select_from(LeadStateRow).where(
            LeadStateRow.tenant_id == tenant_id, LeadStateRow.state == "scored"
        )
    )
    drafts_to_approve = await _count(
        select(func.count()).select_from(Touch).where(
            Touch.tenant_id == tenant_id, Touch.status == "pending_approval"
        )
    )
    linkedin_to_send = await _count(
        select(func.count()).select_from(ManualAction).where(
            ManualAction.tenant_id == tenant_id, ManualAction.status == "pending"
        )
    )
    # Replies still in 'replied' state = waiting for the user to respond.
    replies_waiting = await _count(
        select(func.count()).select_from(LeadStateRow).where(
            LeadStateRow.tenant_id == tenant_id, LeadStateRow.state == "replied"
        )
    )

    settings_row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    config = dict(settings_row.config) if settings_row else {}
    paused = bool(config.get("paused"))
    cal_set = bool(config.get("cal_link"))

    items = []
    if replies_waiting:
        items.append({
            "label": f"{replies_waiting} repl{'y' if replies_waiting == 1 else 'ies'} to respond to",
            "sub": "Send a reply toward a call — fastest path to a booking.",
            "href": "/inbox",
            "priority": 1,
            "count": replies_waiting,
        })
    if drafts_to_approve:
        items.append({
            "label": f"{drafts_to_approve} draft{'s' if drafts_to_approve != 1 else ''} to approve",
            "sub": "Review and approve to send.",
            "href": "/drafts",
            "priority": 2,
            "count": drafts_to_approve,
        })
    if linkedin_to_send:
        items.append({
            "label": f"{linkedin_to_send} LinkedIn invite{'s' if linkedin_to_send != 1 else ''} to send",
            "sub": "Copy the note, send the connection request.",
            "href": "/linkedin",
            "priority": 3,
            "count": linkedin_to_send,
        })
    if leads_to_review:
        items.append({
            "label": f"{leads_to_review} lead{'s' if leads_to_review != 1 else ''} to review",
            "sub": "Approve the good ones to start outreach.",
            "href": "/leads?state=scored",
            "priority": 4,
            "count": leads_to_review,
        })

    # Setup nudges
    if not cal_set:
        items.append({
            "label": "Add your booking link",
            "sub": "Replies can't offer a call without it.",
            "href": "/settings",
            "priority": 0,
            "count": 0,
        })

    items.sort(key=lambda x: x["priority"])
    return {"items": items, "paused": paused}
