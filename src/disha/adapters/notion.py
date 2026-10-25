"""Notion adapter: mirror leads to a Notion database via internal integration token.

Same lazy-import pattern as gmail.py — tests don't need notion_client installed.
Token storage uses the credentials table (provider='notion'); database_id lives
in tenant_settings.config["notion_database_id"].
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from disha.crypto import decrypt, encrypt
from disha.db.models import Account, Credential, Event, Lead, LeadStateRow, TenantSettings, Touch
from disha.db.models.enums import CredentialStatus

if TYPE_CHECKING:
    # notion_client is lazy-imported inside methods; this is purely for typing
    pass


class NotionError(Exception):
    """Base for Notion adapter failures."""


class NotionSyncResult(BaseModel):
    synced: int = 0
    created: int = 0
    updated: int = 0
    errors: int = 0
    skipped: int = 0
    duration_ms: int = 0


def _import_notion_client() -> Any:
    try:
        from notion_client import AsyncClient

        return AsyncClient
    except ImportError as exc:
        raise NotionError(f"notion-client not installed: {exc}") from exc


async def _load_token(session: AsyncSession, tenant_id: UUID) -> str:
    cred = (
        await session.execute(
            select(Credential).where(
                Credential.tenant_id == tenant_id,
                Credential.provider == "notion",
                Credential.status == CredentialStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if cred is None:
        raise NotionError(
            "No active notion credentials for this tenant. "
            "Run `disha notion auth --tenant <slug>` first."
        )
    payload = json.loads(decrypt(cred.encrypted_payload).decode("utf-8"))
    token: str = payload["token"]
    return token


async def _load_database_id(session: AsyncSession, tenant_id: UUID) -> str:
    row = (
        await session.execute(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))
    ).scalar_one_or_none()
    config = dict(row.config) if row else {}
    db_id = config.get("notion_database_id")
    if not db_id:
        raise NotionError(
            "notion_database_id not configured. Run `disha notion config <database_id>` first."
        )
    return str(db_id)


def _build_properties(
    lead: Lead,
    account: Account | None,
    state: LeadStateRow | None,
    last_touch_at: datetime | None,
    cal_link: str,
) -> dict[str, Any]:
    """Map Disha data → Notion property payloads. All keys are Notion property NAMES."""
    full_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
    props: dict[str, Any] = {
        "Name": {"title": [{"text": {"content": full_name or "(unknown)"}}]},
        "Company": {"rich_text": [{"text": {"content": (account.name if account else "") or ""}}]},
        "Role": {"rich_text": [{"text": {"content": lead.role_title or ""}}]},
    }
    if state and state.geo_assigned:
        props["Country"] = {"select": {"name": state.geo_assigned}}
    elif account and account.hq_country:
        props["Country"] = {"select": {"name": account.hq_country}}
    if state and state.tier:
        props["Tier"] = {"select": {"name": state.tier.value}}
    if state:
        props["State"] = {"select": {"name": state.state.value}}
        if state.score is not None:
            props["Score"] = {"number": float(state.score)}
    if last_touch_at is not None:
        props["Last Touch"] = {"date": {"start": last_touch_at.isoformat()}}
    if cal_link:
        props["Cal Link"] = {"url": cal_link}
    if lead.linkedin_url:
        props["LinkedIn"] = {"url": lead.linkedin_url}
    return props


class NotionAdapter:
    async def _client(self, session: AsyncSession, tenant_id: UUID) -> Any:
        token = await _load_token(session, tenant_id)
        async_client_cls = _import_notion_client()
        return async_client_cls(auth=token)

    async def sync_lead(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        lead_id: UUID,
    ) -> str:
        lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()
        account = None
        if lead.account_id is not None:
            account = (
                await session.execute(select(Account).where(Account.id == lead.account_id))
            ).scalar_one_or_none()
        state = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead.id))
        ).scalar_one_or_none()

        # Last sent touch across any thread for this lead.
        last_touch_at_row = (
            await session.execute(
                select(func.max(Touch.sent_at)).where(
                    Touch.tenant_id == tenant_id,
                    Touch.status == "sent",
                )
            )
        ).scalar_one_or_none()

        settings_row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        cal_link = (settings_row.config or {}).get("cal_link", "") if settings_row else ""

        props = _build_properties(lead, account, state, last_touch_at_row, cal_link)
        client = await self._client(session, tenant_id)
        database_id = await _load_database_id(session, tenant_id)

        if lead.notion_page_id is None:
            response = await client.pages.create(
                parent={"database_id": database_id},
                properties=props,
            )
            page_id: str = response["id"]
            lead.notion_page_id = page_id
        else:
            response = await client.pages.update(
                page_id=lead.notion_page_id,
                properties=props,
            )
            page_id = response.get("id", lead.notion_page_id)

        return page_id

    async def sync_all(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        *,
        since: datetime | None = None,
        limit: int = 50,
    ) -> NotionSyncResult:
        started = time.perf_counter()

        if since is None:
            lead_ids = list(
                (
                    await session.execute(
                        select(Lead.id).where(Lead.tenant_id == tenant_id).limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        else:
            # Incremental: leads that had any event since `since`.
            lead_ids = list(
                (
                    await session.execute(
                        select(Lead.id)
                        .join(Event, Event.entity_id == Lead.id)
                        .where(
                            Lead.tenant_id == tenant_id,
                            Event.tenant_id == tenant_id,
                            Event.entity_type == "lead",
                            Event.created_at >= since,
                        )
                        .distinct()
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )

        result = NotionSyncResult()
        for lid in lead_ids:
            try:
                was_new = (
                    await session.execute(select(Lead.notion_page_id).where(Lead.id == lid))
                ).scalar_one() is None
                await self.sync_lead(session, tenant_id, lid)
                result.synced += 1
                if was_new:
                    result.created += 1
                else:
                    result.updated += 1
            except Exception:
                result.errors += 1

        # Persist last-synced marker in tenant_settings.
        settings_row = (
            await session.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if settings_row is not None:
            cfg = dict(settings_row.config or {})
            cfg["notion_last_synced"] = datetime.now(UTC).isoformat()
            settings_row.config = cfg

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    @classmethod
    async def store_token(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        token: str,
    ) -> None:
        payload = json.dumps({"token": token})
        existing = (
            await session.execute(
                select(Credential).where(
                    Credential.tenant_id == tenant_id,
                    Credential.provider == "notion",
                    Credential.status == CredentialStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.encrypted_payload = encrypt(payload)
        else:
            session.add(
                Credential(
                    tenant_id=tenant_id,
                    provider="notion",
                    encrypted_payload=encrypt(payload),
                    status=CredentialStatus.ACTIVE,
                )
            )
