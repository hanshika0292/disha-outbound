"""Drafts routes: pending, approve (with optional edit), reject, regenerate.

The edit-then-approve flow is the heart of the UI:

  POST /drafts/{touch_id}/approve  body: {subject?, body?}
    → If subject or body supplied, write them onto the touch row FIRST.
    → Then call approve_draft() to flip status to 'queued'.
    → If the update commits but approve fails, status stays pending_approval
      so the user can re-approve. (Intended behavior — no rollback needed.)
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from disha.api.auth import require_auth
from disha.api.deps import TenantSession
from disha.approvals import (
    DraftReviewItem,
    approve_draft,
    list_pending_drafts,
    regenerate_draft,
    reject_draft,
)
from disha.db.models import Account, Lead, Thread, Touch
from disha.llm import LLMRouter

router = APIRouter(prefix="/drafts", tags=["drafts"], dependencies=[Depends(require_auth)])


class ApproveBody(BaseModel):
    subject: str | None = None
    body: str | None = None


class RejectBody(BaseModel):
    reason: str | None = None


@router.get("/pending")
async def pending(deps: TenantSession) -> list[DraftReviewItem]:
    session, tenant_id = deps
    return await list_pending_drafts(session, tenant_id)


@router.get("/pending/full")
async def pending_full(deps: TenantSession) -> list[dict[str, object]]:
    """Same set as /pending but returns the FULL body + lead/account context
    so the Drafts page can render the side-by-side editor without N+1 fetches."""
    session, tenant_id = deps
    rows = (
        await session.execute(
            select(Touch, Lead, Account)
            .join(Thread, Thread.id == Touch.thread_id)
            .join(Lead, Lead.id == Thread.lead_id)
            .outerjoin(Account, Account.id == Lead.account_id)
            .where(Touch.tenant_id == tenant_id, Touch.status == "pending_approval")
            .order_by(Touch.drafted_at.asc().nulls_last())
        )
    ).all()
    return [
        {
            "touch_id": str(touch.id),
            "lead_id": str(lead.id),
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
            "lead_title": lead.role_title or "",
            "account_name": account.name if account else "",
            "step_number": touch.step_number,
            "channel": touch.channel.value if hasattr(touch.channel, "value") else str(touch.channel),
            "subject": touch.subject,
            "body": touch.body,
            "drafted_at": touch.drafted_at.isoformat() if touch.drafted_at else None,
        }
        for touch, lead, account in rows
    ]


@router.post("/{touch_id}/approve", status_code=204)
async def approve(
    deps: TenantSession,
    touch_id: UUID,
    payload: ApproveBody | None = None,
) -> None:
    session, tenant_id = deps
    payload = payload or ApproveBody()

    if payload.subject is not None or payload.body is not None:
        touch = (
            await session.execute(
                select(Touch).where(Touch.id == touch_id, Touch.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if touch is None:
            raise HTTPException(status_code=404, detail="Touch not found")
        if touch.status != "pending_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Touch is in status={touch.status!r}, not pending_approval",
            )
        if payload.subject is not None:
            touch.subject = payload.subject
        if payload.body is not None:
            touch.body = payload.body
        await session.flush()

    await approve_draft(session, tenant_id, touch_id)


@router.post("/{touch_id}/reject", status_code=204)
async def reject(
    deps: TenantSession,
    touch_id: UUID,
    payload: RejectBody | None = None,
) -> None:
    session, tenant_id = deps
    reason = (payload.reason if payload else None) or "rejected"
    await reject_draft(session, tenant_id, touch_id, reason=reason)


@router.post("/{touch_id}/regenerate")
async def regenerate(deps: TenantSession, touch_id: UUID) -> dict[str, str | None]:
    session, tenant_id = deps
    router_ = LLMRouter(tenant_id)
    new_id = await regenerate_draft(session, tenant_id, touch_id, router_)
    return {"new_touch_id": str(new_id) if new_id else None}
