"""Leads routes: list/detail/import/add/approve/reject/score."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from disha.api.auth import require_auth
from disha.api.deps import TenantSession
from disha.db.models import Account, Lead, LeadStateRow, Signal, Thread, Touch
from disha.ingestion import ImportResult, ImportRow, add_single_lead, import_from_csv
from disha.intelligence import score_lead
from disha.lead_approval import (
    LeadReviewItem,
    approve_lead,
    list_leads_awaiting_approval,
    reject_lead,
)
from disha.llm import LLMRouter

router = APIRouter(prefix="/leads", tags=["leads"], dependencies=[Depends(require_auth)])


@router.get("")
async def lead_list(
    deps: TenantSession,
    state: str | None = Query(None),
    tier: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict[str, Any]:
    session, tenant_id = deps
    stmt = (
        select(LeadStateRow, Lead, Account)
        .join(Lead, Lead.id == LeadStateRow.lead_id)
        .outerjoin(Account, Account.id == Lead.account_id)
        .where(LeadStateRow.tenant_id == tenant_id)
    )
    if state:
        stmt = stmt.where(LeadStateRow.state == state)
    if tier:
        stmt = stmt.where(LeadStateRow.tier == tier)
    stmt = stmt.order_by(desc(LeadStateRow.updated_at))

    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).all()

    counts_by_state: dict[str, int] = {}
    state_count_rows = (
        await session.execute(
            select(LeadStateRow.state, func.count())
            .where(LeadStateRow.tenant_id == tenant_id)
            .group_by(LeadStateRow.state)
        )
    ).all()
    for s, n in state_count_rows:
        counts_by_state[s.value if hasattr(s, "value") else str(s)] = int(n)

    items: list[dict[str, Any]] = []
    for state_row, lead, account in rows:
        last_touch_at = (
            await session.execute(
                select(func.max(Touch.sent_at))
                .join(Thread, Thread.id == Touch.thread_id)
                .where(Touch.tenant_id == tenant_id, Thread.lead_id == lead.id)
            )
        ).scalar_one_or_none()
        items.append(
            {
                "lead_id": str(lead.id),
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "title": lead.role_title,
                "company": account.name if account else "",
                "country": (account.hq_country if account else "") or "",
                "tier": state_row.tier.value if state_row.tier else None,
                "score": float(state_row.score) if state_row.score is not None else None,
                "state": state_row.state.value
                if hasattr(state_row.state, "value")
                else str(state_row.state),
                "last_touch_at": last_touch_at.isoformat() if last_touch_at else None,
            }
        )

    return {
        "items": items,
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "counts_by_state": counts_by_state,
    }


@router.get("/awaiting/approval")
async def leads_awaiting(deps: TenantSession) -> list[LeadReviewItem]:
    session, tenant_id = deps
    return await list_leads_awaiting_approval(session, tenant_id)


def _safe_decrypt_email(lead: Lead) -> str | None:
    from disha.crypto import decrypt

    try:
        return decrypt(lead.email_encrypted).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


@router.get("/{lead_id}")
async def lead_detail(deps: TenantSession, lead_id: UUID) -> dict[str, Any]:
    session, tenant_id = deps
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    account = None
    if lead.account_id is not None:
        account = (
            await session.execute(select(Account).where(Account.id == lead.account_id))
        ).scalar_one_or_none()
    state = (
        await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead.id))
    ).scalar_one_or_none()
    signals = list(
        (
            await session.execute(
                select(Signal)
                .where(Signal.tenant_id == tenant_id, Signal.lead_id == lead.id)
                .order_by(Signal.observed_at)
            )
        )
        .scalars()
        .all()
    )
    touches = list(
        (
            await session.execute(
                select(Touch)
                .join(Thread, Thread.id == Touch.thread_id)
                .where(Touch.tenant_id == tenant_id, Thread.lead_id == lead.id)
                .order_by(Touch.step_number.asc().nulls_last(), Touch.drafted_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "lead": {
            "id": str(lead.id),
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "title": lead.role_title,
            "linkedin_url": lead.linkedin_url,
            "source": lead.source,
            "notes": lead.notes,
            "email_confidence": (lead.raw or {}).get("email_confidence"),
            "email": _safe_decrypt_email(lead),
            "enrichment": (lead.raw or {}).get("enrichment"),
        },
        "account": {
            "id": str(account.id) if account else None,
            "name": account.name if account else None,
            "country": account.hq_country if account else None,
            "industry": account.industry if account else None,
            "funding_stage": account.funding_stage if account else None,
            "employee_count": account.employee_count if account else None,
            "website": (account.raw or {}).get("website"),
        }
        if account
        else None,
        "state": {
            "state": state.state.value if state and hasattr(state.state, "value") else None,
            "tier": state.tier.value if state and state.tier else None,
            "score": float(state.score) if state and state.score is not None else None,
            "sequence_started_at": state.sequence_started_at.isoformat()
            if state and state.sequence_started_at
            else None,
            "sequence_paused_until": state.sequence_paused_until.isoformat()
            if state and state.sequence_paused_until
            else None,
        }
        if state
        else None,
        "signals": [
            {
                "type": s.type,
                "source": s.source,
                "payload": s.payload,
                "observed_at": s.observed_at.isoformat(),
            }
            for s in signals
        ],
        "touches": [
            {
                "id": str(t.id),
                "step_number": t.step_number,
                "channel": t.channel.value if hasattr(t.channel, "value") else str(t.channel),
                "status": t.status,
                "subject": t.subject,
                "body": t.body,
                "drafted_at": t.drafted_at.isoformat() if t.drafted_at else None,
                "sent_at": t.sent_at.isoformat() if t.sent_at else None,
            }
            for t in touches
        ],
    }


@router.post("")
async def lead_add(
    deps: TenantSession,
    row: ImportRow,
    auto_score: bool = Query(False),
) -> JSONResponse:
    session, tenant_id = deps
    lead_id = await add_single_lead(session, tenant_id, row, auto_score=auto_score)
    if lead_id is None:
        return JSONResponse(
            status_code=409,
            content={"detail": "Lead with that email already exists", "lead_id": None},
        )
    return JSONResponse(status_code=201, content={"lead_id": str(lead_id)})


@router.post("/import")
async def lead_import(
    deps: TenantSession,
    file: UploadFile = File(...),
    auto_score: bool = Query(False),
    dry_run: bool = Query(False),
) -> ImportResult:
    session, tenant_id = deps
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload must be a .csv file")
    tmp_path = Path(tempfile.gettempdir()) / f"disha-upload-{uuid4().hex}.csv"
    try:
        tmp_path.write_bytes(await file.read())
        result = await import_from_csv(
            session, tenant_id, tmp_path, auto_score=auto_score, dry_run=dry_run
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    return result


@router.post("/{lead_id}/approve", status_code=204)
async def lead_approve_route(deps: TenantSession, lead_id: UUID) -> None:
    session, tenant_id = deps
    await approve_lead(session, tenant_id, lead_id)


@router.post("/{lead_id}/reject", status_code=204)
async def lead_reject_route(
    deps: TenantSession, lead_id: UUID, payload: dict[str, str] | None = None
) -> None:
    session, tenant_id = deps
    reason = (payload or {}).get("reason", "manual_reject")
    await reject_lead(session, tenant_id, lead_id, reason=reason)


@router.post("/{lead_id}/score")
async def lead_rescore(deps: TenantSession, lead_id: UUID) -> dict[str, Any]:
    session, tenant_id = deps
    router_ = LLMRouter(tenant_id)
    result = await score_lead(session, tenant_id, lead_id, router_)
    return result.model_dump(mode="json")


@router.post("/reverify-emails")
async def reverify_emails(deps: TenantSession) -> dict[str, Any]:
    """Rescue 'guessed' emails: swap in enrichment-resolved real domains and
    MX-check them. Upgraded leads become sendable ('risky'); domains with no
    mail setup stay blocked."""
    from disha.email_verify import reverify_guessed_leads

    session, tenant_id = deps
    return await reverify_guessed_leads(session, tenant_id)


@router.post("/{lead_id}/verify-email")
async def verify_email(deps: TenantSession, lead_id: UUID) -> dict[str, Any]:
    """Verify this lead's email via Hunter (if a key/quota is available)."""
    from disha.email_verify import verify_one_lead

    session, tenant_id = deps
    return await verify_one_lead(session, tenant_id, lead_id)


class SetEmailRequest(BaseModel):
    email: str


@router.post("/{lead_id}/email")
async def set_email(
    deps: TenantSession, lead_id: UUID, payload: SetEmailRequest
) -> dict[str, Any]:
    """Manually set a confirmed email — marks it verified (user vouches)."""
    from disha.email_verify import set_lead_email

    session, tenant_id = deps
    result = await set_lead_email(session, tenant_id, lead_id, payload.email)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "failed"))
    return result


@router.post("/{lead_id}/enrich")
async def lead_enrich(deps: TenantSession, lead_id: UUID) -> dict[str, Any]:
    """Deep-research this lead: resolve the company website and generate a
    fit brief (why they fit YOUR profile, why reach out now, opening hook).
    The brief also feeds the email drafter's personalization context."""
    from disha.enrichment import enrich_lead

    session, tenant_id = deps
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    try:
        return await enrich_lead(session, tenant_id, lead_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
