"""BYOK credential management — store, status, revoke. Validation is patched."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from disha.crypto import decrypt
from disha.db import admin_session
from disha.db.models import Credential
from disha.db.models.enums import CredentialStatus
from disha.tenant.byok import get_byok_status, revoke_byok_key, set_byok_key
from disha.tenant.provisioner import provision_tenant

pytestmark = pytest.mark.asyncio


async def _make_tenant(slug: str = "byok-test"):  # type: ignore[no-untyped-def]
    async with admin_session() as session:
        await provision_tenant(session, slug=slug, name="X", email="x@x.co")
        from disha.db.models import Tenant

        tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one()
        return tenant.id


async def test_set_byok_stores_encrypted_credential() -> None:
    tenant_id = await _make_tenant()
    async with admin_session() as session:
        await set_byok_key(
            session, tenant_id, provider="gemini", api_key="sk-test-not-a-real-key", validate=False
        )
    async with admin_session() as session:
        cred = (
            await session.execute(
                select(Credential).where(
                    Credential.tenant_id == tenant_id,
                    Credential.provider == "gemini",
                    Credential.status == CredentialStatus.ACTIVE,
                )
            )
        ).scalar_one()
        payload = json.loads(decrypt(cred.encrypted_payload).decode("utf-8"))
    assert payload["api_key"] == "sk-test-not-a-real-key"


async def test_byok_status_shows_configured_provider() -> None:
    tenant_id = await _make_tenant()
    async with admin_session() as session:
        await set_byok_key(session, tenant_id, provider="anthropic", api_key="fake", validate=False)
    async with admin_session() as session:
        status = await get_byok_status(session, tenant_id)
    assert status["anthropic"]["configured"] is True
    assert status["gemini"]["configured"] is False
    assert status["openai"]["configured"] is False


async def test_byok_revoke_deactivates_credential() -> None:
    tenant_id = await _make_tenant()
    async with admin_session() as session:
        await set_byok_key(session, tenant_id, provider="openai", api_key="fake", validate=False)
    async with admin_session() as session:
        await revoke_byok_key(session, tenant_id, "openai")
    async with admin_session() as session:
        status = await get_byok_status(session, tenant_id)
    assert status["openai"]["configured"] is False


async def test_set_byok_with_validate_invokes_validator() -> None:
    tenant_id = await _make_tenant()
    with patch("disha.tenant.byok._validate_gemini_key") as mock_validate:
        async with admin_session() as session:
            await set_byok_key(session, tenant_id, provider="gemini", api_key="kk", validate=True)
    mock_validate.assert_called_once_with("kk")


async def test_set_byok_validation_failure_raises() -> None:
    tenant_id = await _make_tenant()
    with patch(
        "disha.tenant.byok._validate_gemini_key",
        side_effect=ValueError("bad key"),
    ):
        with pytest.raises(ValueError, match="bad key"):
            async with admin_session() as session:
                await set_byok_key(
                    session, tenant_id, provider="gemini", api_key="kk", validate=True
                )


async def test_set_byok_replaces_previous_active() -> None:
    tenant_id = await _make_tenant()
    async with admin_session() as session:
        await set_byok_key(session, tenant_id, provider="gemini", api_key="k1", validate=False)
    async with admin_session() as session:
        await set_byok_key(session, tenant_id, provider="gemini", api_key="k2", validate=False)
    async with admin_session() as session:
        rows = list(
            (
                await session.execute(
                    select(Credential).where(
                        Credential.tenant_id == tenant_id,
                        Credential.provider == "gemini",
                    )
                )
            )
            .scalars()
            .all()
        )
    active = [r for r in rows if r.status == CredentialStatus.ACTIVE]
    revoked = [r for r in rows if r.status == CredentialStatus.REVOKED]
    assert len(active) == 1
    assert len(revoked) == 1
    payload = json.loads(decrypt(active[0].encrypted_payload).decode("utf-8"))
    assert payload["api_key"] == "k2"
