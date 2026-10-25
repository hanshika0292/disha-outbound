"""LinkedIn manual-action queue: list pending, mark done / skip.

Each LinkedIn "send" is a ManualAction row (action_type='linkedin_connect')
created by the sequencer when a LinkedIn step fires. The user copies the
content, sends it on LinkedIn manually, then comes back and marks done.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from disha.api.auth import require_auth
from disha.api.deps import TenantSession
from disha.db.models import Account, Lead, ManualAction
from disha.sequencer.engine import mark_manual_action_done, mark_manual_action_skipped

router = APIRouter(prefix="/linkedin", tags=["linkedin"], dependencies=[Depends(require_auth)])


@router.get("/status")
async def linkedin_status(deps: TenantSession) -> dict[str, Any]:
    """Whether Unipile is available, LinkedIn connected, and auto-send state."""
    from disha.adapters.unipile import unipile_configured
    from disha.linkedin_auto import (
        account_id_of,
        daily_cap_of,
        invites_sent_today,
    )
    from disha.db.models import TenantSettings

    session, tenant_id = deps
    row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    config = dict(row.config) if row and row.config else {}
    connected = bool(account_id_of(config))
    return {
        "available": unipile_configured(),
        "connected": connected,
        "auto": bool(config.get("linkedin_auto")),
        "daily_cap": daily_cap_of(config),
        "sent_today": await invites_sent_today(session, tenant_id) if connected else 0,
    }


@router.post("/connect")
async def linkedin_connect(deps: TenantSession) -> dict[str, str]:
    """Generate a Unipile Hosted Auth link for the user to connect LinkedIn."""
    from datetime import UTC, datetime, timedelta

    from disha.adapters.unipile import UnipileClient, UnipileError, unipile_configured
    from disha.config import get_settings

    _, tenant_id = deps
    if not unipile_configured():
        raise HTTPException(
            status_code=503,
            detail="LinkedIn automation isn't enabled — Unipile is not configured.",
        )
    s = get_settings()
    expires = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    try:
        url = await UnipileClient().hosted_auth_link(
            name=str(tenant_id),
            notify_url=f"{s.disha_api_url}/api/v1/public/unipile/webhook",
            success_redirect_url=f"{s.disha_ui_url}/settings?linkedin=connected",
            failure_redirect_url=f"{s.disha_ui_url}/settings?linkedin=failed",
            expires_on=expires,
        )
    except UnipileError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"url": url}


@router.post("/{action_id}/send")
async def linkedin_send(deps: TenantSession, action_id: UUID) -> dict[str, Any]:
    """Send a queued LinkedIn invitation via Unipile (manual trigger)."""
    from disha.linkedin_auto import send_invite_for_action

    session, tenant_id = deps
    action = (
        await session.execute(
            select(ManualAction).where(
                ManualAction.id == action_id, ManualAction.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if action is None:
        raise HTTPException(status_code=404, detail="Manual action not found")
    result = await send_invite_for_action(session, tenant_id, action)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "send_failed"))
    return result


def _parse_messages(content: str) -> tuple[str, str]:
    """Stored content may be JSON {invite_note, connected_note} (new) or a plain
    single message (legacy). Return (invite_note, connected_note)."""
    if not content:
        return "", ""
    try:
        d = json.loads(content)
        if isinstance(d, dict):
            return str(d.get("invite_note", "")), str(d.get("connected_note", ""))
    except (json.JSONDecodeError, TypeError):
        pass
    return content, ""  # legacy single message → treat as the invite note


class DoneBody(BaseModel):
    notes: str | None = None


class SkipBody(BaseModel):
    reason: str | None = None


@router.get("/queue")
async def queue(
    deps: TenantSession,
    status: str = Query("pending"),
) -> list[dict[str, Any]]:
    session, tenant_id = deps
    actions = list(
        (
            await session.execute(
                select(ManualAction)
                .where(
                    ManualAction.tenant_id == tenant_id,
                    ManualAction.status == status,
                )
                .order_by(ManualAction.created_at.desc())
            )
        ).scalars()
    )
    out: list[dict[str, Any]] = []
    for a in actions:
        lead = (
            await session.execute(select(Lead).where(Lead.id == a.lead_id))
        ).scalar_one_or_none()
        lead_name = ""
        lead_title = ""
        company = ""
        linkedin_url: str | None = None
        if lead is not None:
            lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
            lead_title = lead.role_title or ""
            linkedin_url = lead.linkedin_url
            if lead.account_id is not None:
                account = (
                    await session.execute(
                        select(Account).where(Account.id == lead.account_id)
                    )
                ).scalar_one_or_none()
                company = account.name if account else ""

        # Fallback when we don't have the profile URL: a LinkedIn people-search
        # link pre-filled with their name + company.
        search_url = (
            "https://www.linkedin.com/search/results/people/?keywords="
            + quote(f"{lead_name} {company}".strip())
            if not linkedin_url and lead_name
            else None
        )

        invite_note, connected_note = _parse_messages(a.content or "")

        out.append(
            {
                "id": str(a.id),
                "lead_id": str(a.lead_id),
                "lead_name": lead_name,
                "lead_title": lead_title,
                "company": company,
                "linkedin_url": linkedin_url,
                "linkedin_search_url": search_url,
                "action_type": a.action_type,
                "invite_note": invite_note,
                "connected_note": connected_note,
                "has_messages": bool(invite_note or connected_note),
                "status": a.status,
                "created_at": a.created_at.isoformat(),
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "notes": a.notes,
            }
        )
    return out


@router.post("/{action_id}/generate")
async def generate(deps: TenantSession, action_id: UUID) -> dict[str, str]:
    """Generate (or regenerate) the invite note + post-accept message for this
    action and persist them on the manual action."""
    from disha.intelligence.linkedin_messages import generate_linkedin_messages

    session, tenant_id = deps
    action = (
        await session.execute(
            select(ManualAction).where(
                ManualAction.id == action_id, ManualAction.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if action is None:
        raise HTTPException(status_code=404, detail="Manual action not found")

    messages = await generate_linkedin_messages(session, tenant_id, action.lead_id)
    action.content = json.dumps(messages)
    return messages


@router.post("/{action_id}/done")
async def done(
    deps: TenantSession,
    action_id: UUID,
    payload: DoneBody | None = None,
) -> dict[str, str]:
    session, tenant_id = deps
    notes = payload.notes if payload else None
    try:
        action = await mark_manual_action_done(session, tenant_id, action_id, notes=notes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Manual action not found: {exc}") from exc
    return {"id": str(action.id), "status": action.status}


@router.post("/{action_id}/skip")
async def skip(
    deps: TenantSession,
    action_id: UUID,
    payload: SkipBody | None = None,
) -> dict[str, str]:
    session, tenant_id = deps
    reason = (payload.reason if payload else None) or "not_relevant"
    try:
        action = await mark_manual_action_skipped(
            session, tenant_id, action_id, reason=reason
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Manual action not found: {exc}") from exc
    return {"id": str(action.id), "status": action.status}
