"""source_leads(): provider.search → dedup → insert → optional score.

Reuses the existing ingestion path (add_single_lead: dedup + signal seeding)
and scorer, so sourced leads land in the same 'Scored · awaiting approval'
gate as imported ones — no parallel pipeline.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.db.models import IcpRubric
from disha.db.session import tenant_session
from disha.ingestion import ImportRow, add_single_lead
from disha.intelligence import score_lead
from disha.llm import LLMRouter
from disha.sourcing.base import DEFAULT_PROVIDER, get_source
from disha.sourcing.schemas import SourceCandidate, SourceQuery, SourcingResult
from disha.sourcing.translate import icp_to_query

logger = logging.getLogger(__name__)


async def _active_icp_config(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            select(IcpRubric).where(
                IcpRubric.tenant_id == tenant_id, IcpRubric.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    return dict(row.config) if row and row.config else {}


async def build_query(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    overrides: dict[str, Any] | None = None,
    limit: int = 25,
) -> SourceQuery:
    config = await _active_icp_config(session, tenant_id)
    return icp_to_query(config, overrides=overrides, limit=limit)


async def preview_candidates(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    provider: str = DEFAULT_PROVIDER,
    overrides: dict[str, Any] | None = None,
    limit: int = 25,
) -> tuple[SourceQuery, list[SourceCandidate]]:
    """Dry run: build the query from the ICP and return what we'd pull. No writes."""
    query = await build_query(session, tenant_id, overrides=overrides, limit=limit)
    candidates = await get_source(provider, tenant_id=tenant_id).search(query)
    return query, candidates


def _candidate_to_row(c: SourceCandidate) -> ImportRow:
    raw = c.raw if isinstance(c.raw, dict) else {}
    return ImportRow(
        first_name=c.first_name,
        last_name=c.last_name,
        email=c.email or "",
        title=c.title,
        company=c.company,
        country=c.country,
        linkedin_url=c.linkedin_url,
        notes=raw.get("headline"),
        source_url=c.source_url,
        funding_stage=c.funding_stage,
        employee_count=c.employee_count,
        email_confidence=raw.get("email_confidence"),
    )


async def source_leads(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    provider: str = DEFAULT_PROVIDER,
    overrides: dict[str, Any] | None = None,
    limit: int = 25,
    candidates: list[SourceCandidate] | None = None,
    auto_score: bool = True,
    dry_run: bool = False,
    router: LLMRouter | None = None,
) -> SourcingResult:
    """Source and insert leads. If `candidates` is given (a curated subset from a
    prior preview), use those; otherwise search the provider with the ICP query.
    dry_run wraps inserts in a savepoint that rolls back."""
    result = SourcingResult(provider=provider)
    used_router = router or LLMRouter(tenant_id)

    if candidates is None:
        _, candidates = await preview_candidates(
            session, tenant_id, provider=provider, overrides=overrides, limit=limit
        )
    result.found = len(candidates)

    savepoint = await session.begin_nested() if dry_run else None
    try:
        for c in candidates:
            if not c.email:
                result.skipped_no_email += 1
                continue
            try:
                row = _candidate_to_row(c)
            except ValidationError as exc:
                result.skipped_invalid += 1
                result.errors.append(f"{c.first_name} {c.last_name}: {exc}")
                continue

            lead_id = await add_single_lead(
                session,
                tenant_id,
                row,
                auto_score=False,
                router=used_router,
                source=provider,
            )
            if lead_id is None:
                result.duplicates += 1
                continue
            result.added += 1
            result.lead_ids.append(lead_id)
    finally:
        if savepoint is not None:
            await savepoint.rollback()

    if auto_score and not dry_run:
        for lead_id in result.lead_ids:
            try:
                await score_lead(session, tenant_id, lead_id, used_router)
                result.scored += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "sourcing auto-score failed",
                    extra={"lead_id": str(lead_id), "tenant_id": str(tenant_id)},
                )

    logger.info(
        "sourcing complete",
        extra={
            "tenant_id": str(tenant_id),
            "provider": provider,
            "found": result.found,
            "added": result.added,
            "duplicates": result.duplicates,
            "scored": result.scored,
            "dry_run": dry_run,
        },
    )
    return result


async def source_and_score(
    tenant_id: UUID,
    *,
    provider: str = DEFAULT_PROVIDER,
    overrides: dict[str, Any] | None = None,
    limit: int = 25,
    candidates: list[SourceCandidate] | None = None,
    auto_score: bool = True,
    dry_run: bool = False,
    router: LLMRouter | None = None,
) -> SourcingResult:
    """Resilient entrypoint that OWNS its database transactions.

    Why this exists: the naive path adds every lead AND scores every lead inside
    one transaction. Scoring N leads means N sequential LLM calls (seconds each);
    the DB connection sits idle-in-transaction the whole time and Fly Postgres
    eventually kills it (`ConnectionDoesNotExistError`) → the entire batch rolls
    back and *nothing* is saved.

    Fix: two phases, each in short-lived transactions —
      1. Add all leads in ONE transaction and commit. Leads persist immediately.
      2. Score each lead in its OWN fresh transaction. A slow or failed score
         never rolls back the leads, and no transaction spans multiple LLM calls.

    Use this from request handlers / CLI. The session-bound `source_leads` above
    remains for callers that already manage a short transaction (tests).
    """
    used_router = router or LLMRouter(tenant_id)

    # Resolve candidates up front (read-only) if not supplied.
    if candidates is None:
        async with tenant_session(tenant_id) as s:
            _, candidates = await preview_candidates(
                s, tenant_id, provider=provider, overrides=overrides, limit=limit
            )

    # ── Phase 1: add + commit (no scoring inside this transaction) ──────────
    async with tenant_session(tenant_id) as session:
        result = await source_leads(
            session,
            tenant_id,
            provider=provider,
            candidates=candidates,
            auto_score=False,
            dry_run=dry_run,
            router=used_router,
        )
    # Leads are committed here (context-manager commit). For dry_run the inner
    # savepoint already rolled the inserts back, so nothing persists.

    # ── Phase 2: score each lead in its OWN short transaction ───────────────
    if auto_score and not dry_run:
        for lead_id in list(result.lead_ids):
            try:
                async with tenant_session(tenant_id) as score_session:
                    await score_lead(score_session, tenant_id, lead_id, used_router)
                result.scored += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "sourcing auto-score failed",
                    extra={"lead_id": str(lead_id), "tenant_id": str(tenant_id)},
                )

    logger.info(
        "source_and_score complete",
        extra={
            "tenant_id": str(tenant_id),
            "provider": provider,
            "found": result.found,
            "added": result.added,
            "scored": result.scored,
            "dry_run": dry_run,
        },
    )
    return result
