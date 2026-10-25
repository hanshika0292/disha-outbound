"""Resolve API keys: tenant BYOK if active, else platform fallback.

Hard-fail policy: if a tenant has a credential row for the provider with status
in {'expired', 'revoked'}, raise CredentialError. Never silently fall back to
the platform key — that would silently switch billing source.
"""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select

from disha.config import get_settings
from disha.crypto import decrypt
from disha.db import admin_session
from disha.db.models import Credential
from disha.db.models.enums import CredentialStatus
from disha.llm.errors import AuthError, CredentialError

_PLATFORM_KEY_MAP = {
    "gemini": "gemini_api_key",
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
}


def _unwrap_key(plaintext: str) -> str:
    """tenant.byok.set_byok_key stores {"api_key": "..."}; accept a bare key too."""
    try:
        payload = json.loads(plaintext)
    except ValueError:
        return plaintext
    if isinstance(payload, dict) and payload.get("api_key"):
        return str(payload["api_key"])
    return plaintext


async def resolve_api_key(tenant_id: UUID, provider: str) -> str:
    """Return a usable API key for (tenant, provider), or raise.

    Order:
    1. Active tenant credential (decrypt and return).
    2. Stale (expired/revoked) tenant credential → CredentialError. Never fall back.
    3. No tenant credential → platform .env key. Empty platform key → AuthError.
    """
    async with admin_session() as session:
        result = await session.execute(
            select(Credential).where(
                Credential.tenant_id == tenant_id,
                Credential.provider == provider,
            )
        )
        creds = list(result.scalars().all())

    active = [c for c in creds if c.status == CredentialStatus.ACTIVE]
    stale = [c for c in creds if c.status != CredentialStatus.ACTIVE]

    if active:
        return _unwrap_key(decrypt(active[0].encrypted_payload).decode("utf-8"))

    if stale:
        statuses = ", ".join(sorted({c.status.value for c in stale}))
        raise CredentialError(
            f"Tenant has {provider} credential(s) with status={statuses}. "
            f"Rotate via `disha tenant byok set` before retrying. "
            f"Refusing to fall back to platform key to avoid surprise billing."
        )

    platform_attr = _PLATFORM_KEY_MAP.get(provider)
    if platform_attr is None:
        raise AuthError(f"Unknown provider: {provider!r}")
    settings = get_settings()
    secret = getattr(settings, platform_attr).get_secret_value()
    if not secret:
        raise AuthError(
            f"No tenant credential for {provider!r} and platform "
            f"{platform_attr.upper()} is unset in .env."
        )
    return str(secret)
