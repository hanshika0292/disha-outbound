"""Health route — no auth, no /api/v1 prefix."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from disha.api.deps import AdminSession
from disha.health import run_health_checks

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(session: AdminSession) -> JSONResponse:
    report = await run_health_checks(session)
    status_code = 503 if report.overall == "fail" else 200
    return JSONResponse(status_code=status_code, content=report.model_dump(mode="json"))
