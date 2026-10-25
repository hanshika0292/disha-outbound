"""Sequencer routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from disha.api.auth import require_auth
from disha.api.deps import TenantSession
from disha.db.models import TenantSettings
from disha.llm import LLMRouter
from disha.sequencer.engine import SequenceRun, run_sequence

router = APIRouter(prefix="/sequence", tags=["sequence"], dependencies=[Depends(require_auth)])


async def _paused(session, tenant_id) -> bool:  # type: ignore[no-untyped-def]
    row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    return bool(row and (row.config or {}).get("paused"))


@router.get("/preview")
async def sequence_preview(deps: TenantSession) -> SequenceRun:
    """Dry-run sequencer — show what would happen, don't write."""
    session, tenant_id = deps
    router_ = LLMRouter(tenant_id)
    return await run_sequence(session, tenant_id, router_, dry_run=True)


# Alias kept for Week 6 callers
@router.get("/status")
async def sequence_status(deps: TenantSession) -> SequenceRun:
    session, tenant_id = deps
    router_ = LLMRouter(tenant_id)
    return await run_sequence(session, tenant_id, router_, dry_run=True)


@router.post("/run")
async def sequence_run(deps: TenantSession) -> SequenceRun:
    session, tenant_id = deps
    if await _paused(session, tenant_id):
        raise HTTPException(
            status_code=409, detail="Sending is paused. Resume it in the sidebar to run."
        )
    router_ = LLMRouter(tenant_id)
    return await run_sequence(session, tenant_id, router_, dry_run=False)
