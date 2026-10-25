"""BYOK (bring-your-own-key) LLM credential management.

Tenants on Starter plan provide their own Gemini/Anthropic/OpenAI API key.
Stored encrypted (Fernet) in the existing credentials table, provider field
matches the LLM router's lookup. Validation calls are patchable from tests.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from disha.crypto import encrypt
from disha.db.models import Credential, Event
from disha.db.models.enums import CredentialStatus

logger = logging.getLogger(__name__)


SUPPORTED_PROVIDERS = ("gemini", "anthropic", "openai")


def _validate_provider(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider {provider!r}. Allowed: {SUPPORTED_PROVIDERS}")


def _validate_gemini_key(api_key: str) -> None:
    """Make a minimal Gemini API call to confirm the key works.

    Tests should patch this function rather than supplying real keys.
    """
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        # list models is the cheapest sanity call
        _ = list(client.models.list())
    except Exception as exc:
        raise ValueError(f"gemini key validation failed: {exc}") from exc


def _validate_anthropic_key(api_key: str) -> None:
    try:
        import anthropic

        anthropic.Anthropic(api_key=api_key).models.list()
    except Exception as exc:
        raise ValueError(f"anthropic key validation failed: {exc}") from exc


def _validate_openai_key(api_key: str) -> None:
    try:
        import openai

        openai.OpenAI(api_key=api_key).models.list()
    except Exception as exc:
        raise ValueError(f"openai key validation failed: {exc}") from exc


_VALIDATOR_FUNCS = {
    "gemini": "_validate_gemini_key",
    "anthropic": "_validate_anthropic_key",
    "openai": "_validate_openai_key",
}


def _dispatch_validation(provider: str, api_key: str) -> None:
    """Resolve validator by name on the module so tests can monkey-patch it."""
    import sys as _sys

    me = _sys.modules[__name__]
    getattr(me, _VALIDATOR_FUNCS[provider])(api_key)


async def _emit(
    session: AsyncSession,
    tenant_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        Event(
            id=str(ULID()),
            tenant_id=tenant_id,
            entity_type="tenant",
            entity_id=tenant_id,
            event_type=event_type,
            payload=payload,
            actor="byok",
        )
    )


async def set_byok_key(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    provider: str,
    api_key: str,
    validate: bool = True,
) -> None:
    """Upsert encrypted BYOK API key. Deactivates the previous one for that provider."""
    _validate_provider(provider)
    if validate:
        _dispatch_validation(provider, api_key)

    payload = json.dumps({"api_key": api_key})

    await session.execute(
        update(Credential)
        .where(
            Credential.tenant_id == tenant_id,
            Credential.provider == provider,
            Credential.status == CredentialStatus.ACTIVE,
        )
        .values(status=CredentialStatus.REVOKED)
    )

    session.add(
        Credential(
            tenant_id=tenant_id,
            provider=provider,
            encrypted_payload=encrypt(payload),
            status=CredentialStatus.ACTIVE,
        )
    )
    await _emit(
        session,
        tenant_id,
        "byok.configured",
        {"provider": provider, "validated": validate},
    )
    logger.info(
        "byok configured",
        extra={
            "tenant_id": str(tenant_id),
            "provider": provider,
            "validated": validate,
        },
    )


async def get_byok_status(session: AsyncSession, tenant_id: UUID) -> dict[str, dict[str, Any]]:
    """Returns one entry per supported provider; never includes plaintext keys."""
    rows = (
        (
            await session.execute(
                select(Credential).where(
                    Credential.tenant_id == tenant_id,
                    Credential.provider.in_(SUPPORTED_PROVIDERS),
                )
            )
        )
        .scalars()
        .all()
    )

    by_provider: dict[str, list[Credential]] = {p: [] for p in SUPPORTED_PROVIDERS}
    for c in rows:
        by_provider.setdefault(c.provider, []).append(c)

    result: dict[str, dict[str, Any]] = {}
    for provider in SUPPORTED_PROVIDERS:
        creds = by_provider.get(provider, [])
        active = next((c for c in creds if c.status == CredentialStatus.ACTIVE), None)
        if active is None:
            result[provider] = {"configured": False}
        else:
            result[provider] = {
                "configured": True,
                "active": True,
                "configured_at": active.created_at.isoformat(),
                "updated_at": active.updated_at.isoformat(),
            }
    return result


async def revoke_byok_key(session: AsyncSession, tenant_id: UUID, provider: str) -> None:
    _validate_provider(provider)
    await session.execute(
        update(Credential)
        .where(
            Credential.tenant_id == tenant_id,
            Credential.provider == provider,
            Credential.status == CredentialStatus.ACTIVE,
        )
        .values(status=CredentialStatus.REVOKED)
    )
    await _emit(
        session,
        tenant_id,
        "byok.revoked",
        {"provider": provider, "at": datetime.now(UTC).isoformat()},
    )
