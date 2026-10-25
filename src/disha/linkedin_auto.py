"""LinkedIn automation service — send queued invites via Unipile.

Shared by the API ("Send via LinkedIn" button) and the worker (capped
auto-send). All sends are best-effort and respect:
  • Unipile configured + a connected account_id for the tenant
  • the global pause switch
  • a daily invite cap (default 20) to protect the LinkedIn account

A sent invite emits a `linkedin.invite_sent` Event, which both feeds the
activity log and is how we count the daily budget.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from disha.adapters.unipile import (
    UnipileClient,
    UnipileError,
    linkedin_identifier_from_url,
    unipile_configured,
)
from disha.db.models import Event, Lead, ManualAction, TenantSettings

logger = logging.getLogger(__name__)

DEFAULT_DAILY_CAP = 20


async def _config(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    return dict(row.config) if row and row.config else {}


def account_id_of(config: dict[str, Any]) -> str | None:
    val = config.get("linkedin_account_id")
    return str(val) if val else None


def daily_cap_of(config: dict[str, Any]) -> int:
    try:
        return max(1, int(config.get("linkedin_daily_cap", DEFAULT_DAILY_CAP)))
    except (ValueError, TypeError):
        return DEFAULT_DAILY_CAP


async def invites_sent_today(session: AsyncSession, tenant_id: UUID) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.tenant_id == tenant_id,
                    Event.event_type == "linkedin.invite_sent",
                    Event.created_at >= start,
                )
            )
        ).scalar_one()
    )


async def set_connected_account(
    session: AsyncSession, tenant_id: UUID, account_id: str
) -> None:
    """Store the Unipile account_id on the tenant (called from the webhook)."""
    row = (
        await session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = TenantSettings(tenant_id=tenant_id, config={})
        session.add(row)
        await session.flush()
    config = dict(row.config or {})
    config["linkedin_account_id"] = account_id
    row.config = config


async def send_invite_for_action(
    session: AsyncSession, tenant_id: UUID, action: ManualAction
) -> dict[str, Any]:
    """Send the queued LinkedIn invitation via Unipile. Marks the action done.
    Returns {ok, reason?}. Raises nothing the caller must catch — failures come
    back as {ok: False, reason}."""
    if not unipile_configured():
        return {"ok": False, "reason": "unipile_not_configured"}

    config = await _config(session, tenant_id)
    account_id = account_id_of(config)
    if not account_id:
        return {"ok": False, "reason": "linkedin_not_connected"}

    lead = (
        await session.execute(select(Lead).where(Lead.id == action.lead_id))
    ).scalar_one_or_none()
    if lead is None:
        return {"ok": False, "reason": "lead_missing"}
    identifier = linkedin_identifier_from_url(lead.linkedin_url)
    if not identifier:
        return {"ok": False, "reason": "no_linkedin_url"}

    # Parse the invite note (content is JSON {invite_note, connected_note}).
    invite_note = ""
    try:
        d = json.loads(action.content or "")
        invite_note = str(d.get("invite_note", "")) if isinstance(d, dict) else ""
    except (json.JSONDecodeError, TypeError):
        invite_note = action.content or ""

    try:
        client = UnipileClient()
        provider_id = await client.resolve_provider_id(account_id, identifier)
        await client.send_invitation(account_id, provider_id, invite_note)
    except UnipileError as exc:
        logger.warning("linkedin invite failed for lead %s: %s", lead.id, exc)
        return {"ok": False, "reason": "send_failed", "detail": str(exc)}

    action.status = "done"
    action.completed_at = datetime.now(UTC)
    action.notes = (action.notes or "") + " [sent via LinkedIn/Unipile]"
    session.add(
        Event(
            id=str(ULID()),
            tenant_id=tenant_id,
            entity_type="lead",
            entity_id=lead.id,
            event_type="linkedin.invite_sent",
            payload={"action_id": str(action.id), "provider_id": provider_id},
            actor="unipile",
        )
    )
    return {"ok": True, "lead_id": str(lead.id)}


async def auto_send_pending(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    """Worker entry: send pending LinkedIn invites up to the remaining daily cap.
    No-op unless linkedin_auto is on, Unipile is configured, and not paused."""
    config = await _config(session, tenant_id)
    if config.get("paused"):
        return {"sent": 0, "reason": "paused"}
    if not config.get("linkedin_auto"):
        return {"sent": 0, "reason": "auto_off"}
    if not unipile_configured() or not account_id_of(config):
        return {"sent": 0, "reason": "not_connected"}

    cap = daily_cap_of(config)
    already = await invites_sent_today(session, tenant_id)
    budget = cap - already
    if budget <= 0:
        return {"sent": 0, "reason": "daily_cap_reached"}

    pending = list(
        (
            await session.execute(
                select(ManualAction)
                .where(
                    ManualAction.tenant_id == tenant_id,
                    ManualAction.status == "pending",
                    ManualAction.action_type == "linkedin_connect",
                )
                .order_by(ManualAction.created_at)
                .limit(budget)
            )
        ).scalars()
    )

    sent = 0
    for action in pending:
        result = await send_invite_for_action(session, tenant_id, action)
        if result.get("ok"):
            sent += 1
        # A profile we can't resolve (no URL) shouldn't burn the loop; skip it.
    return {"sent": sent, "cap": cap, "already": already}
