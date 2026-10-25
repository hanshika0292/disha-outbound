"""Public (unauthenticated) routes — the waitlist signup from the landing page.

Kept deliberately tiny: one POST, upsert-by-email, no data returned that could
enumerate signups. Lightly rate-limited in-process per IP.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from disha.api.deps import AdminSession
from disha.db.models import WaitlistEntry

router = APIRouter(prefix="/public", tags=["public"])

# crude in-process limiter: max 5 signups / 10 min / IP
_BUCKET: dict[str, list[float]] = defaultdict(list)
_WINDOW, _MAX = 600.0, 5


def _rate_ok(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _BUCKET[ip] if now - t < _WINDOW]
    _BUCKET[ip] = hits
    if len(hits) >= _MAX:
        return False
    hits.append(now)
    return True


class WaitlistRequest(BaseModel):
    email: EmailStr
    name: str = Field("", max_length=200)
    note: str = Field("", max_length=1000)
    source: str = Field("landing", max_length=100)
    # Qualification answers — phrased on the form as invite-personalisation.
    stage: str = Field("", max_length=100)         # journey stage
    acquisition: str = Field("", max_length=100)   # how they get clients today
    pipeline_goal: str = Field("", max_length=100) # conversations wanted / month
    challenge: str = Field("", max_length=1000)    # hardest part, their words


@router.post("/waitlist", status_code=201)
async def join_waitlist(
    session: AdminSession, request: Request, payload: WaitlistRequest
) -> dict[str, Any]:
    ip = request.client.host if request.client else "unknown"
    if not _rate_ok(ip):
        raise HTTPException(status_code=429, detail="Too many requests — try later")

    answers = {
        k: v
        for k, v in {
            "stage": payload.stage.strip(),
            "acquisition": payload.acquisition.strip(),
            "pipeline_goal": payload.pipeline_goal.strip(),
            "challenge": payload.challenge.strip(),
        }.items()
        if v
    }

    email = payload.email.strip().lower()
    existing = (
        await session.execute(select(WaitlistEntry).where(WaitlistEntry.email == email))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            WaitlistEntry(
                email=email,
                name=payload.name or None,
                note=payload.note or None,
                source=payload.source or None,
                answers=answers or None,
            )
        )
    elif answers and not existing.answers:
        # Returning visitor who filled more detail this time — keep it.
        existing.answers = answers
    # Idempotent + non-enumerating: same response either way.
    return {"ok": True}


class UnipileWebhook(BaseModel):
    status: str = ""
    account_id: str = ""
    name: str = ""  # our internal id — the tenant_id we passed to hosted auth


@router.post("/unipile/webhook")
async def unipile_webhook(session: AdminSession, payload: UnipileWebhook) -> dict[str, Any]:
    """Unipile notify_url callback: a tenant just connected their LinkedIn.
    `name` carries the tenant_id; store the returned account_id on that tenant."""
    from uuid import UUID

    from disha.linkedin_auto import set_connected_account

    if payload.status not in ("CREATION_SUCCESS", "RECONNECTED"):
        return {"ok": True}  # ignore non-success callbacks
    if not (payload.account_id and payload.name):
        return {"ok": True}
    try:
        tenant_id = UUID(payload.name)
    except (ValueError, TypeError):
        return {"ok": True}  # unknown/foreign name — ignore silently
    await set_connected_account(session, tenant_id, payload.account_id)
    return {"ok": True}
