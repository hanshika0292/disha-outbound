"""Factory helpers for inserting fixture leads, accounts, and replies via admin_session.

Used by intelligence tests. Centralized to keep test files focused on assertions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from disha.crypto import encrypt, hash_email
from disha.db import admin_session
from disha.db.models import Account, Lead, Reply, Thread, Touch
from disha.db.models.enums import Channel, Direction
from disha.tenant import seed_demo_tenant


async def ensure_demo_tenant() -> UUID:
    summary = await seed_demo_tenant()
    return UUID(summary["tenant_id"])


async def _get_salt(tenant_id: UUID) -> bytes:
    from sqlalchemy import select

    from disha.db.models import Tenant

    async with admin_session() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
        return tenant.email_hash_salt


async def insert_fixture_lead(tenant_id: UUID, fixture: dict[str, Any]) -> UUID:
    """Lookup-or-create on (tenant_id, domain) for accounts and (tenant_id, email_hash) for leads.

    Idempotent so the classifier test can call this multiple times for replies
    that share a lead.
    """
    from sqlalchemy import select

    salt = await _get_salt(tenant_id)
    acct_data = fixture["account"]
    email = f"{fixture['first_name'].lower()}@{acct_data['domain']}"
    email_hash = hash_email(email, salt)

    async with admin_session() as session:
        existing_lead = (
            await session.execute(
                select(Lead).where(Lead.tenant_id == tenant_id, Lead.email_hash == email_hash)
            )
        ).scalar_one_or_none()
        if existing_lead is not None:
            return existing_lead.id

        account = (
            await session.execute(
                select(Account).where(
                    Account.tenant_id == tenant_id,
                    Account.domain == acct_data["domain"],
                )
            )
        ).scalar_one_or_none()
        if account is None:
            account = Account(
                tenant_id=tenant_id,
                domain=acct_data["domain"],
                name=acct_data["name"],
                industry=acct_data["industry"],
                employee_count=acct_data["employee_count"],
                funding_stage=acct_data["funding_stage"],
                hq_country=acct_data["hq_country"],
                hq_city=acct_data["hq_city"],
            )
            session.add(account)
            await session.flush()

        lead = Lead(
            id=uuid4(),
            tenant_id=tenant_id,
            account_id=account.id,
            email_hash=email_hash,
            email_encrypted=encrypt(email),
            first_name=fixture["first_name"],
            last_name=fixture["last_name"],
            role_title=fixture["role_title"],
            seniority=fixture["seniority"],
            raw=fixture.get("raw", {}),
        )
        session.add(lead)
        await session.flush()
        return lead.id


async def insert_fixture_reply(
    tenant_id: UUID, lead_id: UUID, reply_fixture: dict[str, Any]
) -> UUID:
    from datetime import UTC, datetime

    async with admin_session() as session:
        thread = Thread(
            tenant_id=tenant_id,
            lead_id=lead_id,
            channel=Channel.EMAIL,
            status="open",
        )
        session.add(thread)
        await session.flush()

        touch = Touch(
            tenant_id=tenant_id,
            thread_id=thread.id,
            direction=Direction.OUTBOUND,
            channel=Channel.EMAIL,
            subject=reply_fixture["original_subject"],
            body=reply_fixture["original_body"],
            sent_at=datetime.now(UTC),
        )
        session.add(touch)
        await session.flush()

        reply = Reply(
            tenant_id=tenant_id,
            touch_id=touch.id,
            received_at=datetime.now(UTC),
            body=reply_fixture["reply_body"],
        )
        session.add(reply)
        await session.flush()
        return reply.id


def load_fixture_leads(fixtures_dir: Path) -> list[dict[str, Any]]:
    return json.loads((fixtures_dir / "fake_leads.json").read_text())["leads"]


def load_fixture_replies(fixtures_dir: Path) -> list[dict[str, Any]]:
    return json.loads((fixtures_dir / "fake_replies.json").read_text())["replies"]
