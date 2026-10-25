"""Sourcing routes: preview (dry-run discovery) + run (add + score).

GET  /sourcing/preview  → derive query from ICP, return {query, candidates}. No writes.
POST /sourcing/run      → add candidates (curated subset or fresh search) + score.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from disha.api.auth import require_auth
from disha.api.deps import TenantId, TenantSession
from disha.llm import LLMRouter
from disha.sourcing import (
    DEFAULT_PROVIDER,
    SourceCandidate,
    SourcingResult,
    preview_candidates,
    source_and_score,
)

router = APIRouter(prefix="/sourcing", tags=["sourcing"], dependencies=[Depends(require_auth)])


class PreviewRequest(BaseModel):
    provider: str = DEFAULT_PROVIDER
    overrides: dict[str, Any] = Field(default_factory=dict)
    limit: int = 25


class RunRequest(BaseModel):
    provider: str = DEFAULT_PROVIDER
    overrides: dict[str, Any] = Field(default_factory=dict)
    limit: int = 25
    auto_score: bool = True
    dry_run: bool = False
    # Curated subset from a prior preview. If omitted, the server re-searches.
    candidates: list[SourceCandidate] | None = None


@router.post("/preview")
async def sourcing_preview(deps: TenantSession, payload: PreviewRequest | None = None) -> dict[str, Any]:
    session, tenant_id = deps
    payload = payload or PreviewRequest()
    query, candidates = await preview_candidates(
        session,
        tenant_id,
        provider=payload.provider,
        overrides=payload.overrides,
        limit=payload.limit,
    )
    return {
        "provider": payload.provider,
        "query": query.model_dump(mode="json"),
        "candidates": [c.model_dump(mode="json") for c in candidates],
    }


@router.post("/run")
async def sourcing_run(
    tenant_id: TenantId, payload: RunRequest | None = None
) -> SourcingResult:
    # Uses TenantId (not TenantSession): source_and_score owns its own
    # transactions — it commits the added leads first, then scores each lead in
    # a separate short transaction. This prevents the long single-transaction
    # path that Fly Postgres kills mid-scoring (ConnectionDoesNotExistError),
    # which previously rolled back the entire batch.
    payload = payload or RunRequest()
    router_ = LLMRouter(tenant_id)
    return await source_and_score(
        tenant_id,
        provider=payload.provider,
        overrides=payload.overrides,
        limit=payload.limit,
        candidates=payload.candidates,
        auto_score=payload.auto_score,
        dry_run=payload.dry_run,
        router=router_,
    )
