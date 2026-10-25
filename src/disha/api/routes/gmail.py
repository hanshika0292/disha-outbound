"""Gmail routes: status, OAuth connect flow, manual poll.

Web OAuth flow (the UI's "Connect Gmail" button):

  1. POST /gmail/connect   (auth)    → {auth_url} — Google consent screen URL.
  2. Browser visits auth_url, user approves.
  3. GET  /gmail/callback  (no auth) → Google redirects here with ?code=&state=.
     We verify the HMAC state, exchange the code for tokens, look up the
     user's email, persist the encrypted credential, then bounce the browser
     back to the UI settings page (?gmail=connected | ?gmail=error).

The callback can't carry our bearer token (Google redirects a plain browser),
so `state` is an HMAC over a timestamp keyed with DISHA_UI_TOKEN — unforgeable
without the token, valid for 10 minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from disha.adapters.gmail import GmailAdapter, _decrypt_payload
from disha.adapters.gmail_poller import GmailPoller, PollResult
from disha.api.auth import require_auth
from uuid import UUID

from disha.api.deps import TenantId, TenantSession
from disha.config import get_settings
from disha.db.models import Credential
from disha.db.models.enums import CredentialStatus
from disha.db.session import tenant_session

# Note: no router-level auth — /callback must be reachable by a bare browser
# redirect from Google. Auth is applied per-route instead.
router = APIRouter(prefix="/gmail", tags=["gmail"])

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
_STATE_TTL_SECONDS = 600


def _state_key() -> bytes:
    return get_settings().disha_ui_token.get_secret_value().encode()


def _make_state(tenant_id: UUID) -> str:
    """State = ts.tenant.sig — carries WHICH tenant initiated the flow, so each
    user's Gmail lands in their own account."""
    ts = str(int(time.time()))
    msg = f"{ts}.{tenant_id}"
    sig = hmac.new(_state_key(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}.{sig}"


def _verify_state(state: str) -> UUID | None:
    """Returns the tenant_id the flow was started for, or None if invalid."""
    try:
        ts, tid, sig = state.split(".", 2)
        ts_int = int(ts)
        tenant_id = UUID(tid)
    except ValueError:
        return None
    expected = hmac.new(_state_key(), f"{ts}.{tid}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if (time.time() - ts_int) >= _STATE_TTL_SECONDS:
        return None
    return tenant_id


def _redirect_uri() -> str:
    return f"{get_settings().disha_api_url}/api/v1/gmail/callback"


async def _active_gmail_credential(session, tenant_id) -> Credential | None:  # type: ignore[no-untyped-def]
    return (
        await session.execute(
            select(Credential).where(
                Credential.tenant_id == tenant_id,
                Credential.provider == "gmail",
                Credential.status == CredentialStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()


@router.get("/status", dependencies=[Depends(require_auth)])
async def status(deps: TenantSession) -> dict[str, Any]:
    session, tenant_id = deps
    settings = get_settings()
    cred = await _active_gmail_credential(session, tenant_id)
    sender_email: str | None = None
    if cred is not None:
        try:
            sender_email = _decrypt_payload(cred).get("sender_email")
        except Exception:  # noqa: BLE001 — undecryptable cred = treat as broken
            pass
    return {
        "connected": cred is not None,
        "sender_email": sender_email,
        "oauth_configured": bool(
            settings.gmail_client_id
            and settings.gmail_client_secret.get_secret_value()
        ),
    }


@router.post("/connect")
async def connect(tenant_id: TenantId) -> dict[str, str]:
    settings = get_settings()
    if not settings.gmail_client_id:
        return {
            "error": "GMAIL_CLIENT_ID is not configured on the server. "
            "Create a Google OAuth app and set GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET."
        }
    params = {
        "client_id": settings.gmail_client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",   # ensures a refresh_token
        "prompt": "consent",        # re-issues refresh_token on reconnect
        "state": _make_state(tenant_id),
    }
    return {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    }


@router.get("/callback")
async def callback(
    code: str | None = Query(None),
    state: str = Query(""),
    error: str | None = Query(None),
) -> RedirectResponse:
    settings = get_settings()
    ui_settings_url = f"{settings.disha_ui_url}/settings"

    def fail(reason: str) -> RedirectResponse:
        return RedirectResponse(f"{ui_settings_url}?gmail=error&reason={reason}")

    if error:
        return fail(error)
    tenant_id = _verify_state(state)
    if not code or tenant_id is None:
        return fail("invalid_state")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.gmail_client_id,
                "client_secret": settings.gmail_client_secret.get_secret_value(),
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        if token_resp.status_code != 200:
            return fail("token_exchange_failed")
        tokens = token_resp.json()

        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            timeout=10,
        )
        sender_email = (
            userinfo_resp.json().get("email", "")
            if userinfo_resp.status_code == 200
            else ""
        )

    if not tokens.get("refresh_token"):
        return fail("no_refresh_token")

    async with tenant_session(tenant_id) as session:
        await GmailAdapter.from_oauth_callback(
            session,
            tenant_id,
            token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret.get_secret_value(),
            scopes=_SCOPES,
            sender_email=sender_email,
        )

    return RedirectResponse(f"{ui_settings_url}?gmail=connected")


@router.post("/poll", dependencies=[Depends(require_auth)])
async def poll(
    deps: TenantSession,
    max_threads: int = Query(50, ge=1, le=200),
) -> PollResult:
    session, tenant_id = deps
    return await GmailPoller().poll(session, tenant_id, max_threads=max_threads)
