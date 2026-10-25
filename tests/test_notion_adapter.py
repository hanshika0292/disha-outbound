"""Notion adapter tests with mocked notion_client.AsyncClient."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from ulid import ULID

from disha.adapters.notion import NotionAdapter, NotionError
from disha.crypto import encrypt
from disha.db import admin_session
from disha.db.models import Credential, Event, Lead, TenantSettings
from disha.db.models.enums import CredentialStatus
from tests._factories import ensure_demo_tenant, insert_fixture_lead, load_fixture_leads

pytestmark = pytest.mark.asyncio


def _install_notion_stub(
    monkeypatch: pytest.MonkeyPatch, *, return_page_id: str = "page-new"
) -> MagicMock:
    """Install a fake notion_client module exposing an AsyncClient."""
    mock_client = MagicMock(name="AsyncClient")
    mock_client.pages = MagicMock()
    mock_client.pages.create = AsyncMock(return_value={"id": return_page_id})
    mock_client.pages.update = AsyncMock(return_value={"id": return_page_id})

    class _AsyncClientFactory:
        def __call__(self, auth: str) -> MagicMock:
            del auth
            return mock_client

    fake_module = MagicMock()
    fake_module.AsyncClient = _AsyncClientFactory()
    monkeypatch.setitem(sys.modules, "notion_client", fake_module)
    return mock_client


async def _seed_notion_credential(tenant_id: UUID) -> None:
    async with admin_session() as session:
        session.add(
            Credential(
                tenant_id=tenant_id,
                provider="notion",
                encrypted_payload=encrypt(json.dumps({"token": "secret_test"})),
                status=CredentialStatus.ACTIVE,
            )
        )


async def _set_database_id(tenant_id: UUID, database_id: str) -> None:
    async with admin_session() as session:
        row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if row is None:
            row = TenantSettings(tenant_id=tenant_id, config={})
            session.add(row)
            await session.flush()
        cfg = dict(row.config or {})
        cfg["notion_database_id"] = database_id
        row.config = cfg


async def test_sync_lead_creates_page_when_no_notion_page_id(
    fixtures_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)
    await _seed_notion_credential(tenant_id)
    await _set_database_id(tenant_id, "db-test")
    client = _install_notion_stub(monkeypatch, return_page_id="page-created-123")

    adapter = NotionAdapter()
    async with admin_session() as session:
        page_id = await adapter.sync_lead(session, tenant_id, lead_id)

    assert page_id == "page-created-123"
    client.pages.create.assert_awaited_once()
    client.pages.update.assert_not_awaited()

    async with admin_session() as session:
        lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()
        assert lead.notion_page_id == "page-created-123"


async def test_sync_lead_updates_existing_page(
    fixtures_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)
    await _seed_notion_credential(tenant_id)
    await _set_database_id(tenant_id, "db-test")

    async with admin_session() as session:
        lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()
        lead.notion_page_id = "page-existing-555"

    client = _install_notion_stub(monkeypatch, return_page_id="page-existing-555")

    adapter = NotionAdapter()
    async with admin_session() as session:
        page_id = await adapter.sync_lead(session, tenant_id, lead_id)

    assert page_id == "page-existing-555"
    client.pages.update.assert_awaited_once()
    client.pages.create.assert_not_awaited()


async def test_sync_lead_raises_when_no_credential(fixtures_dir: Any) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    adapter = NotionAdapter()
    async with admin_session() as session:
        with pytest.raises(NotionError, match="No active notion credentials"):
            await adapter.sync_lead(session, tenant_id, lead_id)


async def test_sync_all_incremental_respects_since(
    fixtures_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await ensure_demo_tenant()
    leads = load_fixture_leads(fixtures_dir)
    a = await insert_fixture_lead(tenant_id, leads[0])
    b = await insert_fixture_lead(tenant_id, leads[1])
    _c = await insert_fixture_lead(tenant_id, leads[2])

    await _seed_notion_credential(tenant_id)
    await _set_database_id(tenant_id, "db-test")
    _install_notion_stub(monkeypatch, return_page_id="page-x")

    # Insert one event AFTER the cutoff for lead_a, one BEFORE for lead_b
    cutoff = datetime.now(UTC)
    async with admin_session() as session:
        # Note: events.created_at uses server_default=now() so we have to insert
        # explicitly with a past timestamp via direct SQL for the "before" case.
        from sqlalchemy import text as _t

        session.add(
            Event(
                id=str(ULID()),
                tenant_id=tenant_id,
                entity_type="lead",
                entity_id=a,
                event_type="lead.scored",
                payload={},
                actor="test",
            )
        )
        # past event for b
        await session.execute(
            _t(
                "INSERT INTO events (id, tenant_id, entity_type, entity_id, event_type, "
                "payload, actor, created_at) VALUES (:id, :tid, 'lead', :eid, "
                "'lead.scored', '{}'::jsonb, 'test', :ts)"
            ),
            {
                "id": str(ULID()),
                "tid": str(tenant_id),
                "eid": str(b),
                "ts": cutoff - timedelta(minutes=5),
            },
        )

    adapter = NotionAdapter()
    async with admin_session() as session:
        result = await adapter.sync_all(session, tenant_id, since=cutoff)
    assert result.synced == 1  # only lead_a is past cutoff


async def test_store_token_encrypts_and_upserts() -> None:
    tenant_id = await ensure_demo_tenant()
    async with admin_session() as session:
        await NotionAdapter.store_token(session, tenant_id, "secret_abc")

    async with admin_session() as session:
        cred = (
            await session.execute(
                select(Credential).where(
                    Credential.tenant_id == tenant_id, Credential.provider == "notion"
                )
            )
        ).scalar_one()
        from disha.crypto import decrypt as _decrypt

        payload = json.loads(_decrypt(cred.encrypted_payload).decode("utf-8"))
        assert payload["token"] == "secret_abc"


# Keep import alive
_ = uuid4
