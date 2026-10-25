"""Inbox: captured replies with AI classification, joined to their leads.

GET /inbox          → newest-first reply list (paginated)
GET /inbox/summary  → counts by classification label (for nav badge / tabs)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from disha.api.auth import require_auth
from disha.api.deps import TenantSession
from disha.crypto import decrypt
from disha.db.models import Account, Lead, LeadStateRow, Reply, Thread, Touch
from disha.db.models.enums import Channel, Direction

router = APIRouter(prefix="/inbox", tags=["inbox"], dependencies=[Depends(require_auth)])


def _label(classification: dict[str, Any] | None) -> str | None:
    if not classification:
        return None
    return classification.get("label") or classification.get("classification")


@router.get("")
async def list_inbox(
    deps: TenantSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    session, tenant_id = deps

    stmt = (
        select(Reply, Touch, Lead, Account, LeadStateRow)
        .join(Touch, Touch.id == Reply.touch_id)
        .join(Thread, Thread.id == Touch.thread_id)
        .join(Lead, Lead.id == Thread.lead_id)
        .outerjoin(Account, Account.id == Lead.account_id)
        .outerjoin(LeadStateRow, LeadStateRow.lead_id == Lead.id)
        .where(Reply.tenant_id == tenant_id)
        .order_by(Reply.received_at.desc())
    )

    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    rows = (
        await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).all()

    items = []
    for reply, touch, lead, account, state_row in rows:
        items.append(
            {
                "reply_id": str(reply.id),
                "lead_id": str(lead.id),
                "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
                "lead_title": lead.role_title,
                "company": account.name if account else "",
                "lead_state": state_row.state.value
                if state_row and hasattr(state_row.state, "value")
                else (str(state_row.state) if state_row else None),
                "subject": reply.subject or touch.subject,
                "body": reply.body,
                "classification": reply.classification,
                "label": _label(reply.classification),
                "received_at": reply.received_at.isoformat(),
                "our_touch_subject": touch.subject,
                "our_touch_step": touch.step_number,
            }
        )

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/summary")
async def inbox_summary(deps: TenantSession) -> dict[str, Any]:
    session, tenant_id = deps
    rows = (
        await session.execute(
            select(Reply.classification).where(Reply.tenant_id == tenant_id)
        )
    ).scalars().all()
    counts: dict[str, int] = {}
    for c in rows:
        label = _label(c) or "unclassified"
        counts[label] = counts.get(label, 0) + 1
    return {"total": len(rows), "by_label": counts}


# ── Reply assistant ──────────────────────────────────────────────────────────


@router.post("/{reply_id}/suggest")
async def suggest(deps: TenantSession, reply_id: UUID) -> dict[str, Any]:
    """AI-draft a response to this reply, aimed at booking a call."""
    from disha.intelligence.reply_assistant import suggest_reply

    session, tenant_id = deps
    exists = (
        await session.execute(
            select(Reply.id).where(Reply.id == reply_id, Reply.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Reply not found")
    return await suggest_reply(session, tenant_id, reply_id)


class SendReplyRequest(BaseModel):
    subject: str
    body: str


@router.post("/{reply_id}/send")
async def send_reply(
    deps: TenantSession, reply_id: UUID, payload: SendReplyRequest
) -> dict[str, Any]:
    """Send the (user-edited) response via Gmail, threaded into the same
    conversation, and record it as an outbound touch."""
    from disha.adapters.gmail import GmailAdapter, GmailError

    session, tenant_id = deps

    reply = (
        await session.execute(
            select(Reply).where(Reply.id == reply_id, Reply.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if reply is None:
        raise HTTPException(status_code=404, detail="Reply not found")

    touch = (
        await session.execute(select(Touch).where(Touch.id == reply.touch_id))
    ).scalar_one()
    thread = (
        await session.execute(select(Thread).where(Thread.id == touch.thread_id))
    ).scalar_one()
    lead = (
        await session.execute(select(Lead).where(Lead.id == thread.lead_id))
    ).scalar_one()

    to_addr = reply.sender_email or decrypt(lead.email_encrypted).decode("utf-8")
    in_reply_to = (reply.raw_headers or {}).get("Message-ID") or (
        reply.raw_headers or {}
    ).get("Message-Id")

    try:
        message_id = await GmailAdapter().send_raw(
            session,
            tenant_id,
            to_addr=to_addr,
            subject=payload.subject,
            body=payload.body,
            in_reply_to=in_reply_to,
            gmail_thread_id=touch.gmail_thread_id,
        )
    except GmailError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Record the send in the same thread so the conversation history is complete.
    now = datetime.now(UTC)
    session.add(
        Touch(
            tenant_id=tenant_id,
            thread_id=thread.id,
            direction=Direction.OUTBOUND,
            channel=Channel.EMAIL,
            status="sent",
            subject=payload.subject,
            body=payload.body,
            sent_at=now,
            gmail_message_id=message_id,
            gmail_thread_id=touch.gmail_thread_id,
        )
    )

    return {"sent": True, "message_id": message_id, "to": to_addr}
