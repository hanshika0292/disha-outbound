"""Reports routes: pipeline, daily, weekly, cost."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query

from disha.api.auth import require_auth
from disha.api.deps import TenantSession
from disha.cost import CostForecast, get_cost_forecast
from disha.reports import (
    ActivityWindow,
    FunnelMetrics,
    PipelineSnapshot,
    get_activity,
    get_funnel,
    get_pipeline_snapshot,
)

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_auth)])


@router.get("/pipeline")
async def pipeline(deps: TenantSession) -> PipelineSnapshot:
    session, tenant_id = deps
    return await get_pipeline_snapshot(session, tenant_id)


@router.get("/daily")
async def daily(deps: TenantSession) -> ActivityWindow:
    session, tenant_id = deps
    now = datetime.now(UTC)
    return await get_activity(session, tenant_id, now - timedelta(days=1), now)


@router.get("/weekly")
async def weekly(deps: TenantSession) -> dict[str, Any]:
    session, tenant_id = deps
    now = datetime.now(UTC)
    activity = await get_activity(session, tenant_id, now - timedelta(days=7), now)
    funnel = await get_funnel(session, tenant_id)
    return {
        "activity": activity.model_dump(mode="json"),
        "funnel": funnel.model_dump(mode="json"),
    }


@router.get("/cost")
async def cost(
    deps: TenantSession,
    days: int = Query(7, ge=1, le=90),
) -> CostForecast:
    session, tenant_id = deps
    return await get_cost_forecast(session, tenant_id, lookback_days=days)


# Avoid unused-import warning if FunnelMetrics isn't referenced elsewhere
_ = FunnelMetrics
