"""Lead approval service: list, approve, reject, bulk_approve.

Bridges the scorer's output (state='scored', tier set) and the sequencer's
input (state='approved'). Rejection reasons live in the events table to
avoid an extra column on lead_states.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from disha.db.models import Account, Event, Lead, LeadStateRow
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.db.models.enums import Tier

# Tier ordering used by min_tier filters. Higher index = lower priority.
_TIER_ORDER = ["A", "B", "C", "skip"]


def _tiers_at_or_above(min_tier: str) -> list[str]:
    if min_tier not in _TIER_ORDER:
        raise ValueError(f"min_tier must be one of {_TIER_ORDER}, got {min_tier!r}")
    floor = _TIER_ORDER.index(min_tier)
    return _TIER_ORDER[: floor + 1]


class LeadReviewItem(BaseModel):
    lead_id: UUID
    full_name: str
    title: str
    account_name: str
    account_country: str
    tier: str
    fit_score: Decimal | None
    scored_at: datetime | None
    pain_signal_summary: str | None


async def list_leads_awaiting_approval(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    min_tier: str | None = None,
) -> list[LeadReviewItem]:
    """Scored leads not yet approved/rejected. Ordered: A first, then by score desc."""
    stmt = (
        select(LeadStateRow, Lead, Account)
        .join(Lead, Lead.id == LeadStateRow.lead_id)
        .outerjoin(Account, Account.id == Lead.account_id)
        .where(
            LeadStateRow.tenant_id == tenant_id,
            LeadStateRow.state == LeadStateEnum.SCORED,
        )
        .order_by(LeadStateRow.tier.asc(), LeadStateRow.score.desc().nulls_last())
    )
    if min_tier is not None:
        allowed = _tiers_at_or_above(min_tier)
        stmt = stmt.where(LeadStateRow.tier.in_(allowed))

    rows = (await session.execute(stmt)).all()
    return [
        LeadReviewItem(
            lead_id=lead.id,
            full_name=f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
            title=lead.role_title or "",
            account_name=account.name if account else "",
            account_country=(account.hq_country if account else "") or "",
            tier=state.tier.value if state.tier else "skip",
            fit_score=state.score,
            scored_at=state.updated_at,
            pain_signal_summary=(lead.raw or {}).get("pain_signal"),
        )
        for state, lead, account in rows
    ]


async def _emit_lead_event(
    session: AsyncSession,
    tenant_id: UUID,
    event_type: str,
    lead_id: UUID,
    payload: dict[str, str] | None = None,
) -> None:
    session.add(
        Event(
            id=str(ULID()),
            tenant_id=tenant_id,
            entity_type="lead",
            entity_id=lead_id,
            event_type=event_type,
            payload=payload or {},
            actor="cli",
        )
    )


async def approve_lead(session: AsyncSession, tenant_id: UUID, lead_id: UUID) -> None:
    state = (
        await session.execute(
            select(LeadStateRow).where(
                LeadStateRow.lead_id == lead_id, LeadStateRow.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    if state.state != LeadStateEnum.SCORED:
        raise ValueError(f"lead {lead_id} is in state={state.state!r}; must be 'scored' to approve")
    state.state = LeadStateEnum.APPROVED
    await _emit_lead_event(session, tenant_id, "lead.approved", lead_id)


async def reject_lead(
    session: AsyncSession,
    tenant_id: UUID,
    lead_id: UUID,
    *,
    reason: str = "manual_reject",
) -> None:
    state = (
        await session.execute(
            select(LeadStateRow).where(
                LeadStateRow.lead_id == lead_id, LeadStateRow.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    state.state = LeadStateEnum.REJECTED
    await _emit_lead_event(session, tenant_id, "lead.rejected", lead_id, {"reason": reason})


async def bulk_approve(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    min_tier: str = "B",
) -> int:
    """Bulk transition scored→approved for leads at or above min_tier. Returns count."""
    allowed_tiers = [Tier(t) for t in _tiers_at_or_above(min_tier)]
    candidates = (
        (
            await session.execute(
                select(LeadStateRow.lead_id).where(
                    LeadStateRow.tenant_id == tenant_id,
                    LeadStateRow.state == LeadStateEnum.SCORED,
                    LeadStateRow.tier.in_(allowed_tiers),
                )
            )
        )
        .scalars()
        .all()
    )

    if not candidates:
        return 0

    await session.execute(
        update(LeadStateRow)
        .where(LeadStateRow.lead_id.in_(list(candidates)))
        .values(state=LeadStateEnum.APPROVED)
    )
    for lid in candidates:
        await _emit_lead_event(session, tenant_id, "lead.approved", lid, {"via": "bulk_approve"})
    return len(candidates)
