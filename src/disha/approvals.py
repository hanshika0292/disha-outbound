"""Draft approval service: list, approve, reject, regenerate touches in pending_approval.

When tenant_settings.config["require_draft_approval"] is true, the sequencer
parks new drafts at status='pending_approval'. A human reviews via the CLI:
- approve  → status='queued'; sequencer sends on next run
- reject   → status='skipped' with rejection reason
- regenerate → existing skipped, new draft created at the same step
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from disha.db import admin_session
from disha.db.models import Account, Event, Lead, Thread, Touch
from disha.intelligence import draft_touch
from disha.llm import LLMRouter


class DraftReviewItem(BaseModel):
    touch_id: UUID
    lead_id: UUID
    lead_name: str
    lead_title: str
    account_name: str
    step_number: int
    channel: str
    subject: str | None
    body_preview: str
    drafted_at: datetime | None


async def list_pending_drafts(session: AsyncSession, tenant_id: UUID) -> list[DraftReviewItem]:
    rows = (
        await session.execute(
            select(Touch, Lead, Account)
            .join(Thread, Thread.id == Touch.thread_id)
            .join(Lead, Lead.id == Thread.lead_id)
            .outerjoin(Account, Account.id == Lead.account_id)
            .where(
                Touch.tenant_id == tenant_id,
                Touch.status == "pending_approval",
            )
            .order_by(Touch.drafted_at.asc().nulls_last())
        )
    ).all()
    return [
        DraftReviewItem(
            touch_id=touch.id,
            lead_id=lead.id,
            lead_name=f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
            lead_title=lead.role_title or "",
            account_name=account.name if account else "",
            step_number=touch.step_number if touch.step_number is not None else -1,
            channel=touch.channel.value if hasattr(touch.channel, "value") else str(touch.channel),
            subject=touch.subject,
            body_preview=(touch.body or "")[:120],
            drafted_at=touch.drafted_at,
        )
        for touch, lead, account in rows
    ]


async def _emit(
    session: AsyncSession,
    tenant_id: UUID,
    event_type: str,
    entity_id: UUID,
    payload: dict[str, str] | None = None,
) -> None:
    session.add(
        Event(
            id=str(ULID()),
            tenant_id=tenant_id,
            entity_type="touch",
            entity_id=entity_id,
            event_type=event_type,
            payload=payload or {},
            actor="cli",
        )
    )


async def _load_pending_touch(session: AsyncSession, tenant_id: UUID, touch_id: UUID) -> Touch:
    touch = (
        await session.execute(
            select(Touch).where(Touch.id == touch_id, Touch.tenant_id == tenant_id)
        )
    ).scalar_one()
    if touch.status != "pending_approval":
        raise ValueError(f"touch {touch_id} is in status={touch.status!r}, not pending_approval")
    return touch


async def approve_draft(session: AsyncSession, tenant_id: UUID, touch_id: UUID) -> None:
    touch = await _load_pending_touch(session, tenant_id, touch_id)
    touch.status = "queued"
    await _emit(session, tenant_id, "touch.approved", touch_id)


async def reject_draft(
    session: AsyncSession,
    tenant_id: UUID,
    touch_id: UUID,
    *,
    reason: str = "rejected",
) -> None:
    touch = await _load_pending_touch(session, tenant_id, touch_id)
    touch.status = "skipped"
    touch.error_message = reason
    await _emit(session, tenant_id, "touch.rejected", touch_id, {"reason": reason})


async def regenerate_draft(
    session: AsyncSession,
    tenant_id: UUID,
    touch_id: UUID,
    router: LLMRouter,
) -> UUID | None:
    """Mark old draft as skipped, generate a fresh draft at the same step.

    Returns the new touch_id, or None if the cadence references an unknown prompt
    (drafter returns None in that case).
    """
    old = await _load_pending_touch(session, tenant_id, touch_id)
    thread = (await session.execute(select(Thread).where(Thread.id == old.thread_id))).scalar_one()
    lead_id = thread.lead_id
    step_number = old.step_number if old.step_number is not None else 0
    cadence_step = step_number + 1  # 0-indexed → 1-indexed for drafter

    old.status = "skipped"
    old.error_message = "regenerated"
    await _emit(session, tenant_id, "touch.regenerated", touch_id)
    await session.flush()

    result = await draft_touch(session, tenant_id, lead_id, cadence_step, router)
    if result is None:
        return None

    new_touch = (
        await session.execute(select(Touch).where(Touch.id == result.touch_id))
    ).scalar_one()
    new_touch.status = "pending_approval"
    new_touch.drafted_at = datetime.now(UTC)
    return result.touch_id


async def approve_draft_by_id(tenant_id: UUID, touch_id: UUID) -> None:
    """CLI helper that opens its own admin_session."""
    async with admin_session() as session:
        await approve_draft(session, tenant_id, touch_id)


async def reject_draft_by_id(tenant_id: UUID, touch_id: UUID, reason: str) -> None:
    async with admin_session() as session:
        await reject_draft(session, tenant_id, touch_id, reason=reason)


async def regenerate_draft_by_id(tenant_id: UUID, touch_id: UUID, router: LLMRouter) -> UUID | None:
    async with admin_session() as session:
        return await regenerate_draft(session, tenant_id, touch_id, router)
