"""Reports: pipeline snapshot, activity window, funnel — query + render.

All queries use the admin_session (sequencer-style: full-tenant scope). Periods
are UTC throughout — local-timezone conversion is a send-window concern only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.db.models import Event, LeadStateRow, LLMCall, Reply, TenantSettings, Touch


class PipelineSnapshot(BaseModel):
    by_state: dict[str, int] = Field(default_factory=dict)
    by_tier: dict[str, int] = Field(default_factory=dict)
    pending_approval: int = 0
    pending_draft_review: int = 0
    active_in_sequence: int = 0


class ActivityWindow(BaseModel):
    period_start: datetime
    period_end: datetime
    leads_sourced: int = 0
    leads_scored: int = 0
    leads_approved: int = 0
    leads_rejected: int = 0
    leads_entered_sequence: int = 0
    leads_replied: int = 0
    leads_booked: int = 0
    leads_stale: int = 0
    touches_sent: int = 0
    touches_queued_linkedin: int = 0
    touches_deferred: int = 0
    touches_failed: int = 0
    replies_received: int = 0
    reply_intent_breakdown: dict[str, int] = Field(default_factory=dict)
    llm_calls: int = 0
    llm_cost_usd: Decimal = Decimal("0")
    llm_cost_by_purpose: dict[str, Decimal] = Field(default_factory=dict)


class FunnelMetrics(BaseModel):
    sourced_to_scored: float = 0.0
    scored_to_approved: float = 0.0
    approved_to_in_sequence: float = 0.0
    in_sequence_to_replied: float = 0.0
    replied_to_booked: float = 0.0
    overall_sourced_to_booked: float = 0.0


def _pct(num: int, denom: int) -> float:
    if denom <= 0:
        return 0.0
    return round((num / denom) * 100.0, 1)


async def get_pipeline_snapshot(session: AsyncSession, tenant_id: UUID) -> PipelineSnapshot:
    state_rows = (
        await session.execute(
            select(LeadStateRow.state, func.count())
            .where(LeadStateRow.tenant_id == tenant_id)
            .group_by(LeadStateRow.state)
        )
    ).all()
    tier_rows = (
        await session.execute(
            select(LeadStateRow.tier, func.count())
            .where(LeadStateRow.tenant_id == tenant_id, LeadStateRow.tier.is_not(None))
            .group_by(LeadStateRow.tier)
        )
    ).all()
    pending_review = (
        await session.execute(
            select(func.count())
            .select_from(Touch)
            .where(Touch.tenant_id == tenant_id, Touch.status == "pending_approval")
        )
    ).scalar_one()

    by_state: dict[str, int] = {}
    in_sequence = 0
    awaiting_approval = 0
    for s, n in state_rows:
        v = s.value if hasattr(s, "value") else str(s)
        by_state[v] = int(n)
        if v == "in_sequence":
            in_sequence = int(n)
        if v == "scored":
            awaiting_approval = int(n)
    by_tier = {(t.value if hasattr(t, "value") else str(t)): int(n) for t, n in tier_rows}
    return PipelineSnapshot(
        by_state=by_state,
        by_tier=by_tier,
        pending_approval=awaiting_approval,
        pending_draft_review=int(pending_review),
        active_in_sequence=in_sequence,
    )


async def get_activity(
    session: AsyncSession,
    tenant_id: UUID,
    period_start: datetime,
    period_end: datetime,
) -> ActivityWindow:
    # Counts from the events log.
    event_count_rows = (
        await session.execute(
            select(Event.event_type, func.count())
            .where(
                Event.tenant_id == tenant_id,
                Event.created_at >= period_start,
                Event.created_at < period_end,
            )
            .group_by(Event.event_type)
        )
    ).all()
    event_counts: dict[str, int] = {str(k): int(v) for k, v in event_count_rows}

    touches_sent = (
        await session.execute(
            select(func.count())
            .select_from(Touch)
            .where(
                Touch.tenant_id == tenant_id,
                Touch.status == "sent",
                Touch.sent_at >= period_start,
                Touch.sent_at < period_end,
            )
        )
    ).scalar_one()
    touches_queued = (
        await session.execute(
            select(func.count())
            .select_from(Touch)
            .where(
                Touch.tenant_id == tenant_id,
                Touch.status == "queued",
                Touch.drafted_at >= period_start,
                Touch.drafted_at < period_end,
            )
        )
    ).scalar_one()
    touches_failed = (
        await session.execute(
            select(func.count())
            .select_from(Touch)
            .where(
                Touch.tenant_id == tenant_id,
                Touch.status == "failed",
                Touch.drafted_at >= period_start,
                Touch.drafted_at < period_end,
            )
        )
    ).scalar_one()

    replies_received = (
        await session.execute(
            select(func.count())
            .select_from(Reply)
            .where(
                Reply.tenant_id == tenant_id,
                Reply.received_at >= period_start,
                Reply.received_at < period_end,
            )
        )
    ).scalar_one()
    intent_rows = (
        await session.execute(
            select(
                Reply.classification["intent"].astext.label("intent"),
                func.count(),
            )
            .where(
                Reply.tenant_id == tenant_id,
                Reply.received_at >= period_start,
                Reply.received_at < period_end,
                Reply.classification.is_not(None),
            )
            .group_by("intent")
        )
    ).all()
    intent_breakdown = {str(intent): int(n) for intent, n in intent_rows if intent}

    llm_total = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(LLMCall.cost_usd), 0)).where(
                LLMCall.tenant_id == tenant_id,
                LLMCall.created_at >= period_start,
                LLMCall.created_at < period_end,
                LLMCall.success.is_(True),
            )
        )
    ).one()
    llm_count = int(llm_total[0])
    llm_sum = Decimal(str(llm_total[1] or 0))

    llm_by_purpose = {
        str(purpose): Decimal(str(cost or 0))
        for purpose, cost in (
            await session.execute(
                select(LLMCall.purpose, func.coalesce(func.sum(LLMCall.cost_usd), 0))
                .where(
                    LLMCall.tenant_id == tenant_id,
                    LLMCall.created_at >= period_start,
                    LLMCall.created_at < period_end,
                    LLMCall.success.is_(True),
                )
                .group_by(LLMCall.purpose)
            )
        ).all()
    }

    return ActivityWindow(
        period_start=period_start,
        period_end=period_end,
        leads_sourced=int(event_counts.get("lead.created", 0)),
        leads_scored=int(event_counts.get("lead.scored", 0)),
        leads_approved=int(event_counts.get("lead.approved", 0)),
        leads_rejected=int(event_counts.get("lead.rejected", 0)),
        leads_entered_sequence=int(event_counts.get("lead.entered_sequence", 0)),
        leads_replied=int(event_counts.get("lead.replied", 0)),
        leads_booked=int(event_counts.get("lead.booked", 0)),
        leads_stale=int(event_counts.get("lead.stale", 0)),
        touches_sent=int(touches_sent),
        touches_queued_linkedin=int(touches_queued),
        touches_deferred=0,  # deferred events not currently emitted; sequencer logs in-memory
        touches_failed=int(touches_failed),
        replies_received=int(replies_received),
        reply_intent_breakdown=intent_breakdown,
        llm_calls=llm_count,
        llm_cost_usd=llm_sum,
        llm_cost_by_purpose=llm_by_purpose,
    )


async def get_funnel(session: AsyncSession, tenant_id: UUID, *, days: int = 30) -> FunnelMetrics:
    """Funnel uses a snapshot of current state counts (cumulative). days param is reserved."""
    del days
    raw_rows = (
        await session.execute(
            select(LeadStateRow.state, func.count())
            .where(LeadStateRow.tenant_id == tenant_id)
            .group_by(LeadStateRow.state)
        )
    ).all()
    rows: dict[str, int] = {
        (k.value if hasattr(k, "value") else str(k)): int(v) for k, v in raw_rows
    }

    def _n(state_value: str) -> int:
        return rows.get(state_value, 0)

    # Cumulative counts at or past each gate. A lead currently 'in_sequence' has
    # also passed through scored/approved, etc. — sum downstream states.
    funnel_order = [
        "sourced",
        "scored",
        "approved",
        "in_sequence",
        "replied",
        "booked",
    ]
    cumulative: dict[str, int] = {}
    running = 0
    for state in reversed(funnel_order):
        running += _n(state)
        cumulative[state] = running

    return FunnelMetrics(
        sourced_to_scored=_pct(cumulative["scored"], cumulative["sourced"]),
        scored_to_approved=_pct(cumulative["approved"], cumulative["scored"]),
        approved_to_in_sequence=_pct(cumulative["in_sequence"], cumulative["approved"]),
        in_sequence_to_replied=_pct(cumulative["replied"], cumulative["in_sequence"]),
        replied_to_booked=_pct(cumulative["booked"], cumulative["replied"]),
        overall_sourced_to_booked=_pct(cumulative["booked"], cumulative["sourced"]),
    )


# --- Rendering ---


def render_pipeline(snapshot: PipelineSnapshot, console: Console) -> None:
    state_table = Table(title="By state", box=box.SIMPLE)
    state_table.add_column("State")
    state_table.add_column("Count", justify="right")
    for state, count in sorted(snapshot.by_state.items()):
        state_table.add_row(state, str(count))

    tier_table = Table(title="By tier", box=box.SIMPLE)
    tier_table.add_column("Tier")
    tier_table.add_column("Count", justify="right")
    for tier, count in sorted(snapshot.by_tier.items()):
        tier_table.add_row(tier, str(count))

    console.print(Columns([state_table, tier_table], expand=False))
    console.print(
        f"[bold]{snapshot.pending_approval}[/bold] awaiting approval  |  "
        f"[bold]{snapshot.pending_draft_review}[/bold] drafts pending review  |  "
        f"[bold]{snapshot.active_in_sequence}[/bold] in sequence"
    )


def render_activity(window: ActivityWindow, console: Console) -> None:
    title = f"Activity: {window.period_start.date()} → {window.period_end.date()}"
    lead_table = Table(title="Lead funnel events", box=box.SIMPLE)
    for col in [
        "Sourced",
        "Scored",
        "Approved",
        "Rejected",
        "InSeq",
        "Replied",
        "Booked",
        "Stale",
    ]:
        lead_table.add_column(col, justify="right")
    lead_table.add_row(
        str(window.leads_sourced),
        str(window.leads_scored),
        str(window.leads_approved),
        str(window.leads_rejected),
        str(window.leads_entered_sequence),
        str(window.leads_replied),
        str(window.leads_booked),
        str(window.leads_stale),
    )

    touch_table = Table(title="Touches + LLM", box=box.SIMPLE)
    for col in ["Sent", "LinkedIn Q", "Failed", "LLM Calls", "Cost USD"]:
        touch_table.add_column(col, justify="right")
    touch_table.add_row(
        str(window.touches_sent),
        str(window.touches_queued_linkedin),
        str(window.touches_failed),
        str(window.llm_calls),
        f"${window.llm_cost_usd:.6f}",
    )

    console.print(Panel.fit(Columns([lead_table, touch_table]), title=title))
    if window.reply_intent_breakdown:
        console.print("[bold]Replies by intent:[/bold]")
        for intent, n in sorted(window.reply_intent_breakdown.items(), key=lambda kv: -kv[1]):
            console.print(f"  {intent:<32}  {n}")


def render_funnel(funnel: FunnelMetrics, console: Console) -> None:
    lines = [
        f"  Sourced  →  Scored       {funnel.sourced_to_scored:>5.1f}%",
        f"  Scored   →  Approved     {funnel.scored_to_approved:>5.1f}%",
        f"  Approved →  In Sequence  {funnel.approved_to_in_sequence:>5.1f}%",
        f"  In Seq   →  Replied      {funnel.in_sequence_to_replied:>5.1f}%",
        f"  Replied  →  Booked       {funnel.replied_to_booked:>5.1f}%",
    ]
    console.print(Panel("\n".join(lines), title="Funnel", expand=False))
    console.print(f"[bold]Overall Sourced → Booked: {funnel.overall_sourced_to_booked:.1f}%[/bold]")


# --- Email digest ---


async def send_weekly_digest(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    gmail_adapter: Any,
    sender_slug: str,
) -> str:
    """Build a plain-text weekly digest and send via Gmail. Returns the gmail message_id."""
    settings_row = (
        await session.execute(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))
    ).scalar_one_or_none()
    config = (settings_row.config or {}) if settings_row else {}
    to_addr = config.get("report_email")
    if not to_addr:
        raise ValueError("report_email not configured in tenant_settings")

    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    pipeline = await get_pipeline_snapshot(session, tenant_id)
    activity = await get_activity(session, tenant_id, week_ago, now)
    funnel = await get_funnel(session, tenant_id)

    subject = f"Disha Weekly — {week_ago.date()} to {now.date()}"
    lines = [
        f"Disha weekly digest for {sender_slug}",
        f"Week: {week_ago.date()} → {now.date()}",
        "",
        f"Pipeline: {sum(pipeline.by_state.values())} total leads",
        f"  Pending approval: {pipeline.pending_approval}",
        f"  Drafts to review: {pipeline.pending_draft_review}",
        f"  In sequence: {pipeline.active_in_sequence}",
        "",
        "Activity (last 7 days):",
        f"  Leads sourced: {activity.leads_sourced}",
        f"  Leads scored: {activity.leads_scored}",
        f"  Touches sent: {activity.touches_sent}",
        f"  Replies received: {activity.replies_received}",
        f"  LLM calls: {activity.llm_calls}  (${activity.llm_cost_usd:.4f})",
        "",
        f"Funnel: Sourced → Booked = {funnel.overall_sourced_to_booked:.1f}%",
    ]
    body = "\n".join(lines)
    message_id: str = await gmail_adapter.send_raw(
        session, tenant_id, to_addr=to_addr, subject=subject, body=body
    )
    return message_id


# Avoid an unused-import warning on `case` for future extensions
_ = case
