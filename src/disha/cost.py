"""Cost queries + forecast model + renderer.

Pulls from `llm_calls` (Week 2's audit log) to produce a forecast at gemini-2.5-flash
pricing. Phase categorization is by purpose prefix: score.* → scoring,
draft.* → drafting, classify.* → classifying, brief.* → briefing, analyze.* → reporting.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.db.models import LLMCall

_FORECAST_LEAD_COUNTS = (10, 25, 50)


class CostBreakdown(BaseModel):
    rows: list[tuple[str, Decimal, int]] = Field(default_factory=list)


class CostForecast(BaseModel):
    observed_days: int
    observed_calls: int
    observed_unique_leads: int
    observed_total_usd: Decimal
    avg_daily_cost_usd: Decimal
    avg_cost_per_lead_lifecycle_usd: Decimal
    projected_weekly_at_n_leads: dict[int, Decimal] = Field(default_factory=dict)
    projected_monthly_at_n_leads: dict[int, Decimal] = Field(default_factory=dict)
    breakdown_by_phase: dict[str, Decimal] = Field(default_factory=dict)


def _purpose_phase(purpose: str) -> str:
    if purpose.startswith("score."):
        return "scoring"
    if purpose.startswith("draft."):
        return "drafting"
    if purpose.startswith("classify."):
        return "classifying"
    if purpose.startswith("brief."):
        return "briefing"
    if purpose.startswith("analyze."):
        return "reporting"
    return "other"


async def get_cost_forecast(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    lookback_days: int = 7,
) -> CostForecast:
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

    aggregate = (
        await session.execute(
            select(
                func.count().label("calls"),
                func.coalesce(func.sum(LLMCall.cost_usd), 0).label("total_cost"),
                func.count(func.distinct(LLMCall.entity_id)).label("unique_leads"),
            ).where(
                LLMCall.tenant_id == tenant_id,
                LLMCall.created_at >= cutoff,
                LLMCall.success.is_(True),
            )
        )
    ).one()
    calls = int(aggregate.calls or 0)
    total_cost = Decimal(str(aggregate.total_cost or 0))
    unique_leads = int(aggregate.unique_leads or 0)

    avg_daily = (
        (total_cost / Decimal(lookback_days)).quantize(Decimal("0.000001"))
        if lookback_days > 0
        else Decimal("0")
    )
    avg_per_lead = (
        (total_cost / Decimal(unique_leads)).quantize(Decimal("0.000001"))
        if unique_leads > 0
        else Decimal("0")
    )

    # Per-purpose breakdown rolled up to phase.
    purpose_rows = (
        await session.execute(
            select(LLMCall.purpose, func.coalesce(func.sum(LLMCall.cost_usd), 0))
            .where(
                LLMCall.tenant_id == tenant_id,
                LLMCall.created_at >= cutoff,
                LLMCall.success.is_(True),
            )
            .group_by(LLMCall.purpose)
        )
    ).all()
    by_phase: dict[str, Decimal] = {}
    for purpose, cost in purpose_rows:
        phase = _purpose_phase(str(purpose))
        by_phase[phase] = by_phase.get(phase, Decimal("0")) + Decimal(str(cost or 0))

    # Projections: assume avg_per_lead represents one lead's full lifecycle.
    weekly = {
        n: (avg_per_lead * Decimal(n)).quantize(Decimal("0.01")) for n in _FORECAST_LEAD_COUNTS
    }
    monthly = {n: (weekly[n] * Decimal("4.33")).quantize(Decimal("0.01")) for n in weekly}

    return CostForecast(
        observed_days=lookback_days,
        observed_calls=calls,
        observed_unique_leads=unique_leads,
        observed_total_usd=total_cost.quantize(Decimal("0.000001")),
        avg_daily_cost_usd=avg_daily,
        avg_cost_per_lead_lifecycle_usd=avg_per_lead,
        projected_weekly_at_n_leads=weekly,
        projected_monthly_at_n_leads=monthly,
        breakdown_by_phase=by_phase,
    )


def render_cost_forecast(forecast: CostForecast, console: Console) -> None:
    header = (
        f"Observed: {forecast.observed_calls} calls across "
        f"{forecast.observed_unique_leads} leads over {forecast.observed_days} days "
        f"(total ${forecast.observed_total_usd:.6f})"
    )
    averages = (
        f"avg/day ${forecast.avg_daily_cost_usd:.6f}  |  "
        f"avg/lead ${forecast.avg_cost_per_lead_lifecycle_usd:.6f}"
    )

    proj = Table(title="Projection", box=box.SIMPLE)
    proj.add_column("Leads/wk", justify="right")
    proj.add_column("Weekly $", justify="right")
    proj.add_column("Monthly $", justify="right")
    for n in sorted(forecast.projected_weekly_at_n_leads):
        proj.add_row(
            str(n),
            f"${forecast.projected_weekly_at_n_leads[n]:.2f}",
            f"${forecast.projected_monthly_at_n_leads[n]:.2f}",
        )

    breakdown = Table(title="Cost by phase", box=box.SIMPLE)
    breakdown.add_column("Phase")
    breakdown.add_column("Cost $", justify="right")
    breakdown.add_column("Share %", justify="right")
    total = sum(forecast.breakdown_by_phase.values()) or Decimal("1")
    for phase, cost in sorted(forecast.breakdown_by_phase.items(), key=lambda kv: -kv[1]):
        share = (cost / total * 100).quantize(Decimal("0.1"))
        breakdown.add_row(phase, f"${cost:.6f}", f"{share}%")

    console.print(Panel.fit(f"{header}\n{averages}", title="Cost Forecast"))
    console.print(proj)
    console.print(breakdown)
    console.print(
        "[dim]Projections at gemini-2.5-flash pricing. Pro model ~17x cost multiplier.[/dim]"
    )


async def get_cost_summary_by(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    days: int,
    by: str,
) -> list[dict[str, object]]:
    """Used by `disha cost summary`. Kept here so cli.py imports from one place."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    group_col = {
        "model": LLMCall.model,
        "prompt": LLMCall.purpose,
        "day": func.date_trunc("day", LLMCall.created_at),
    }.get(by)
    if group_col is None:
        raise ValueError(f"--by must be one of: model, prompt, day (got {by!r})")
    stmt: Any = (
        select(
            group_col.label("group"),
            func.count().label("calls"),
            func.sum(LLMCall.tokens_in).label("tokens_in"),
            func.sum(LLMCall.tokens_out).label("tokens_out"),
            func.sum(LLMCall.cost_usd).label("cost_usd"),
        )
        .where(
            LLMCall.tenant_id == tenant_id,
            LLMCall.created_at >= cutoff,
            LLMCall.success.is_(True),
        )
        .group_by("group")
        .order_by(func.sum(LLMCall.cost_usd).desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "group": str(row.group),
            "calls": int(row.calls),
            "tokens_in": int(row.tokens_in or 0),
            "tokens_out": int(row.tokens_out or 0),
            "cost_usd": float(row.cost_usd or 0),
        }
        for row in rows
    ]
