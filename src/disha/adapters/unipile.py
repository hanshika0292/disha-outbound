"""Unipile adapter — LinkedIn outreach via the Unipile messaging API.

Unipile drives a *user's own* connected LinkedIn account. Each tenant connects
their LinkedIn once (hosted auth wizard) → we store the returned `account_id`
in tenant_settings. Then Disha can, on their behalf:
  • send connection invitations with the AI-written note
  • send messages to connections
  • read messages (reply capture)

Auth: header `X-API-KEY`; base URL is the per-account DSN Unipile assigns
(e.g. https://api12.unipile.com:13443), configured via UNIPILE_DSN.

Everything is best-effort and gated: if Unipile isn't configured, callers get a
clear UnipileError and the manual LinkedIn queue keeps working unchanged.

Docs: https://developer.unipile.com/docs/invite-users
"""

from __future__ import annotations

import logging
import re
from typing import Any

from disha.config import get_settings

logger = logging.getLogger(__name__)


class UnipileError(Exception):
    """Any Unipile request failure or missing configuration."""


def unipile_configured() -> bool:
    s = get_settings()
    return bool(s.unipile_api_key.get_secret_value() and s.unipile_dsn)


def linkedin_identifier_from_url(url: str | None) -> str | None:
    """Extract the public identifier from a LinkedIn profile URL.
    https://www.linkedin.com/in/jane-doe-123/  →  'jane-doe-123'
    """
    if not url:
        return None
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url, re.IGNORECASE)
    return m.group(1) if m else None


class UnipileClient:
    def __init__(self) -> None:
        s = get_settings()
        self._key = s.unipile_api_key.get_secret_value()
        self._base = s.unipile_dsn.rstrip("/")
        if not (self._key and self._base):
            raise UnipileError(
                "Unipile is not configured (set UNIPILE_API_KEY and UNIPILE_DSN)."
            )

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-KEY": self._key,
            "accept": "application/json",
            "content-type": "application/json",
        }

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: Any = None
    ) -> dict[str, Any]:
        import httpx

        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.request(
                    method, url, headers=self._headers(), json=json, params=params
                )
        except Exception as exc:  # noqa: BLE001
            raise UnipileError(f"Unipile request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise UnipileError(
                f"Unipile {method} {path} → {resp.status_code}: {resp.text[:300]}"
            )
        try:
            return resp.json()
        except Exception:  # noqa: BLE001 — some 2xx have empty bodies
            return {}

    # ── account connection ──────────────────────────────────────────────
    async def hosted_auth_link(
        self,
        *,
        name: str,
        notify_url: str,
        success_redirect_url: str,
        failure_redirect_url: str,
        expires_on: str,
    ) -> str:
        """Create a Hosted Auth Wizard link for the user to connect LinkedIn.
        `name` is our internal id (tenant_id) echoed back on the notify webhook."""
        body = {
            "type": "create",
            "providers": ["LINKEDIN"],
            "api_url": self._base,
            "expiresOn": expires_on,
            "name": name,
            "notify_url": notify_url,
            "success_redirect_url": success_redirect_url,
            "failure_redirect_url": failure_redirect_url,
        }
        data = await self._request("POST", "/api/v1/hosted/accounts/link", json=body)
        url = data.get("url")
        if not url:
            raise UnipileError(f"hosted auth link missing url: {data}")
        return str(url)

    async def account_status(self, account_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/accounts/{account_id}")

    # ── profile resolution ──────────────────────────────────────────────
    async def resolve_provider_id(self, account_id: str, identifier: str) -> str:
        """Resolve a LinkedIn public identifier (or URL) to the provider_id
        required for invitations/messages."""
        ident = linkedin_identifier_from_url(identifier) or identifier
        data = await self._request(
            "GET", f"/api/v1/users/{ident}", params={"account_id": account_id}
        )
        # Field name varies across API versions — try the common ones.
        for key in ("provider_id", "id", "member_id", "public_identifier"):
            val = data.get(key)
            if val:
                return str(val)
        raise UnipileError(f"could not resolve provider_id for {ident!r}: {data}")

    # ── outreach ────────────────────────────────────────────────────────
    async def send_invitation(
        self, account_id: str, provider_id: str, message: str
    ) -> dict[str, Any]:
        body = {
            "account_id": account_id,
            "provider_id": provider_id,
            "message": message[:300],  # LinkedIn invite-note hard limit
        }
        return await self._request("POST", "/api/v1/users/invite", json=body)

    async def send_message(
        self, account_id: str, provider_id: str, text: str
    ) -> dict[str, Any]:
        """Start (or reuse) a chat with a connection and send `text`."""
        body = {
            "account_id": account_id,
            "attendees_ids": [provider_id],
            "text": text,
        }
        return await self._request("POST", "/api/v1/chats", json=body)

    # ── reply capture ───────────────────────────────────────────────────
    async def list_chats(self, account_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", "/api/v1/chats", params={"account_id": account_id, "limit": limit}
        )
        items = data.get("items") if isinstance(data, dict) else None
        return list(items or [])

    async def list_messages(
        self, account_id: str, chat_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            f"/api/v1/chats/{chat_id}/messages",
            params={"account_id": account_id, "limit": limit},
        )
        items = data.get("items") if isinstance(data, dict) else None
        return list(items or [])
