"""Combined dashboard endpoint — minimize round-trips on page load."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from disha.api.auth import require_auth
from disha.api.deps import TenantSession
from disha.db.models import Event, Lead, LeadStateRow, ManualAction, Reply, Thread, Touch
from disha.db.models.enums import Direction
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.reports import ActivityWindow, PipelineSnapshot, get_activity, get_pipeline_snapshot

router = APIRouter(tags=["dashboard"])

# Funnel stage membership, expressed over LeadState. Each stage counts leads
# that reached *at least* that point (cumulative), so the funnel only narrows.
_SCORED_PLUS = {
    "scored", "drafted", "skipped", "pending_review", "approved", "rejected",
    "in_sequence", "sent", "replied", "booked", "discovery_held",
    "proposal_sent", "won", "lost", "nurture", "stale", "closed", "unsubscribed",
}
_APPROVED_PLUS = {
    "approved", "in_sequence", "sent", "replied", "booked", "discovery_held",
    "proposal_sent", "won", "lost", "nurture", "stale", "closed", "unsubscribed",
}
_BOOKED_PLUS = {"booked", "discovery_held", "proposal_sent", "won"}
# Reply intents that count as a warm/interested response.
_INTERESTED_INTENTS = ("meeting_request", "positive_vague")


class DashboardData(BaseModel):
    pipeline: PipelineSnapshot
    today: ActivityWindow
    pending_approvals: int = 0
    pending_drafts: int = 0
    pending_linkedin: int = 0
    recent_events: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/dashboard", dependencies=[Depends(require_auth)])
async def dashboard(deps: TenantSession) -> DashboardData:
    session, tenant_id = deps
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Per-query try/except so one slow/broken query doesn't break the dashboard.
    try:
        pipeline = await get_pipeline_snapshot(session, tenant_id)
    except Exception:  # noqa: BLE001
        pipeline = PipelineSnapshot()
    try:
        today = await get_activity(session, tenant_id, today_start, now)
    except Exception:  # noqa: BLE001
        today = ActivityWindow(period_start=today_start, period_end=now)

    try:
        pending_approvals = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(LeadStateRow)
                    .where(
                        LeadStateRow.tenant_id == tenant_id,
                        LeadStateRow.state == LeadStateEnum.SCORED,
                    )
                )
            ).scalar_one()
        )
    except Exception:  # noqa: BLE001
        pending_approvals = 0
    try:
        pending_drafts = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Touch)
                    .where(
                        Touch.tenant_id == tenant_id,
                        Touch.status == "pending_approval",
                    )
                )
            ).scalar_one()
        )
    except Exception:  # noqa: BLE001
        pending_drafts = 0
    try:
        pending_linkedin = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ManualAction)
                    .where(
                        ManualAction.tenant_id == tenant_id,
                        ManualAction.status == "pending",
                    )
                )
            ).scalar_one()
        )
    except Exception:  # noqa: BLE001
        pending_linkedin = 0

    recent_events: list[dict[str, Any]] = []
    try:
        rows = list(
            (
                await session.execute(
                    select(Event)
                    .where(Event.tenant_id == tenant_id)
                    .order_by(desc(Event.created_at))
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        recent_events = [
            {
                "id": e.id,
                "event_type": e.event_type,
                "entity_type": e.entity_type,
                "entity_id": str(e.entity_id),
                "payload": e.payload or {},
                "created_at": e.created_at.isoformat(),
            }
            for e in rows
        ]
    except Exception:  # noqa: BLE001
        pass

    return DashboardData(
        pipeline=pipeline,
        today=today,
        pending_approvals=pending_approvals,
        pending_drafts=pending_drafts,
        pending_linkedin=pending_linkedin,
        recent_events=recent_events,
    )


class FunnelStage(BaseModel):
    key: str
    label: str
    count: int


class TrendPoint(BaseModel):
    date: str  # YYYY-MM-DD
    sent: int
    replies: int


class AnalyticsData(BaseModel):
    funnel: list[FunnelStage] = Field(default_factory=list)
    emails_sent: int = 0
    replies_total: int = 0
    interested_replies: int = 0
    meetings: int = 0
    contacted_leads: int = 0
    replied_leads: int = 0
    reply_rate: float = 0.0  # replied_leads / contacted_leads
    interested_rate: float = 0.0  # interested_replies / replies_total
    email_health: dict[str, int] = Field(default_factory=dict)
    sendable_leads: int = 0
    trend: list[TrendPoint] = Field(default_factory=list)


async def _scalar(session: Any, stmt: Any, default: int = 0) -> int:
    try:
        return int((await session.execute(stmt)).scalar_one())
    except Exception:  # noqa: BLE001
        await session.rollback()  # reset txn so a bad read can't poison the rest
        return default


@router.get("/analytics", dependencies=[Depends(require_auth)])
async def analytics(deps: TenantSession, days: int = 30) -> AnalyticsData:
    """Outbound performance analytics: funnel, KPIs, email health, daily trend."""
    session, tenant_id = deps
    now = datetime.now(UTC)
    window_start = now - timedelta(days=max(1, min(days, 90)))

    # ── pipeline by_state → top-of-funnel cumulative counts ──
    try:
        snap = await get_pipeline_snapshot(session, tenant_id)
        by_state = snap.by_state
    except Exception:  # noqa: BLE001
        by_state = {}
    total_leads = sum(by_state.values())
    scored = sum(n for s, n in by_state.items() if s in _SCORED_PLUS)
    approved = sum(n for s, n in by_state.items() if s in _APPROVED_PLUS)
    booked_state = sum(n for s, n in by_state.items() if s in _BOOKED_PLUS)

    # ── fact-based send / reply counts ──
    sent_join = (
        select(func.count(func.distinct(Thread.lead_id)))
        .select_from(Touch)
        .join(Thread, Thread.id == Touch.thread_id)
        .where(
            Touch.tenant_id == tenant_id,
            Touch.direction == Direction.OUTBOUND,
            Touch.status == "sent",
        )
    )
    contacted_leads = await _scalar(session, sent_join)

    # Exclude bounce-tagged replies (delivery failures) from reply metrics.
    # is_distinct_from keeps NULL (unclassified real replies) in the count.
    not_bounce = Reply.classification["intent"].astext.is_distinct_from("bounce")
    replied_join = (
        select(func.count(func.distinct(Thread.lead_id)))
        .select_from(Reply)
        .join(Touch, Touch.id == Reply.touch_id)
        .join(Thread, Thread.id == Touch.thread_id)
        .where(Reply.tenant_id == tenant_id, not_bounce)
    )
    replied_leads = await _scalar(session, replied_join)

    emails_sent = await _scalar(
        session,
        select(func.count()).select_from(Touch).where(
            Touch.tenant_id == tenant_id,
            Touch.direction == Direction.OUTBOUND,
            Touch.status == "sent",
        ),
    )
    replies_total = await _scalar(
        session,
        select(func.count())
        .select_from(Reply)
        .where(Reply.tenant_id == tenant_id, not_bounce),
    )
    interested_replies = await _scalar(
        session,
        select(func.count()).select_from(Reply).where(
            Reply.tenant_id == tenant_id,
            Reply.classification["intent"].astext.in_(_INTERESTED_INTENTS),
        ),
    )
    meeting_replies = await _scalar(
        session,
        select(func.count()).select_from(Reply).where(
            Reply.tenant_id == tenant_id,
            Reply.classification["intent"].astext == "meeting_request",
        ),
    )
    # Meetings = booked leads if any state tracking exists, else meeting-request replies.
    meetings = max(booked_state, meeting_replies)

    funnel = [
        FunnelStage(key="sourced", label="Sourced", count=total_leads),
        FunnelStage(key="scored", label="Scored", count=scored),
        FunnelStage(key="approved", label="Approved", count=approved),
        FunnelStage(key="contacted", label="Contacted", count=contacted_leads),
        FunnelStage(key="replied", label="Replied", count=replied_leads),
        FunnelStage(key="meetings", label="Meetings", count=meetings),
    ]

    # ── email health (leads by confidence) ──
    email_health: dict[str, int] = {}
    try:
        # Bind the JSONB expression once so SELECT and GROUP BY render identically
        # (Postgres rejects two separately-built copies of the same expression).
        conf_expr = Lead.raw["email_confidence"].astext.label("conf")
        rows = (
            await session.execute(
                select(conf_expr, func.count())
                .where(Lead.tenant_id == tenant_id)
                .group_by(conf_expr)
            )
        ).all()
        for conf, n in rows:
            email_health[conf or "unknown"] = int(n)
    except Exception:  # noqa: BLE001
        await session.rollback()  # reset an aborted txn so later queries survive
    sendable_leads = email_health.get("verified", 0)

    # ── daily trend (sent + replies) over the window ──
    trend: list[TrendPoint] = []
    try:
        sent_by_day = {
            str(d): int(n)
            for d, n in (
                await session.execute(
                    select(func.date(Touch.sent_at), func.count())
                    .where(
                        Touch.tenant_id == tenant_id,
                        Touch.direction == Direction.OUTBOUND,
                        Touch.status == "sent",
                        Touch.sent_at >= window_start,
                    )
                    .group_by(func.date(Touch.sent_at))
                )
            ).all()
        }
        replies_by_day = {
            str(d): int(n)
            for d, n in (
                await session.execute(
                    select(func.date(Reply.received_at), func.count())
                    .where(
                        Reply.tenant_id == tenant_id,
                        Reply.received_at >= window_start,
                        not_bounce,
                    )
                    .group_by(func.date(Reply.received_at))
                )
            ).all()
        }
        day = window_start.date()
        end = now.date()
        while day <= end:
            key = day.isoformat()
            trend.append(
                TrendPoint(
                    date=key,
                    sent=sent_by_day.get(key, 0),
                    replies=replies_by_day.get(key, 0),
                )
            )
            day = day + timedelta(days=1)
    except Exception:  # noqa: BLE001
        await session.rollback()
        trend = []

    reply_rate = (replied_leads / contacted_leads) if contacted_leads else 0.0
    interested_rate = (interested_replies / replies_total) if replies_total else 0.0

    return AnalyticsData(
        funnel=funnel,
        emails_sent=emails_sent,
        replies_total=replies_total,
        interested_replies=interested_replies,
        meetings=meetings,
        contacted_leads=contacted_leads,
        replied_leads=replied_leads,
        reply_rate=round(reply_rate, 4),
        interested_rate=round(interested_rate, 4),
        email_health=email_health,
        sendable_leads=sendable_leads,
        trend=trend,
    )
