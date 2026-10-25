"""Draft approval flow: sequencer pause + approve/reject/regenerate."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from disha.approvals import (
    approve_draft,
    list_pending_drafts,
    regenerate_draft,
    reject_draft,
)
from disha.db import admin_session
from disha.db.models import LeadStateRow, TenantSettings, Thread, Touch
from disha.db.models.enums import Channel, Direction
from disha.db.models.enums import LeadState as LeadStateEnum
from disha.llm import LLMRouter
from disha.sequencer.engine import run_sequence
from tests._factories import ensure_demo_tenant, insert_fixture_lead, load_fixture_leads

pytestmark = pytest.mark.asyncio


async def _set_require_draft_approval(tenant_id: Any, *, value: bool) -> None:
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
        cfg["require_draft_approval"] = value
        row.config = cfg


def _wed_in_window() -> datetime:
    return datetime(2026, 5, 27, 5, 30, tzinfo=UTC)  # 11:00 IST


async def test_sequencer_sets_pending_approval_when_required(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    async with admin_session() as session:
        session.add(
            LeadStateRow(
                lead_id=lead_id,
                tenant_id=tenant_id,
                state=LeadStateEnum.APPROVED,
                geo_assigned="IN",
            )
        )
    await _set_require_draft_approval(tenant_id, value=True)

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        run = await run_sequence(session, tenant_id, router, now_utc=_wed_in_window())

    assert run.pending_approval == 1
    r = run.results[0]
    assert r.action == "pending_approval"

    async with admin_session() as session:
        touches = list(
            (
                await session.execute(
                    select(Touch)
                    .join(Thread, Thread.id == Touch.thread_id)
                    .where(Thread.lead_id == lead_id)
                )
            ).scalars()
        )
        assert len(touches) == 1
        assert touches[0].status == "pending_approval"


async def test_sequencer_skips_lead_with_pending_draft(fixtures_dir: Path) -> None:
    """Second sequence run shouldn't create a duplicate draft for a lead with pending_approval."""
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    async with admin_session() as session:
        session.add(
            LeadStateRow(
                lead_id=lead_id,
                tenant_id=tenant_id,
                state=LeadStateEnum.IN_SEQUENCE,
                geo_assigned="IN",
                sequence_started_at=_wed_in_window(),
            )
        )
        thread = Thread(tenant_id=tenant_id, lead_id=lead_id, channel=Channel.EMAIL, status="open")
        session.add(thread)
        await session.flush()
        session.add(
            Touch(
                tenant_id=tenant_id,
                thread_id=thread.id,
                step_number=0,
                direction=Direction.OUTBOUND,
                channel=Channel.EMAIL,
                status="pending_approval",
                body="awaiting review",
                drafted_at=_wed_in_window(),
            )
        )
    await _set_require_draft_approval(tenant_id, value=True)

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        run = await run_sequence(session, tenant_id, router, now_utc=_wed_in_window())

    assert run.deferred == 1
    assert "already_processed:pending_approval" in run.results[0].reason

    async with admin_session() as session:
        touches = list(
            (
                await session.execute(
                    select(Touch)
                    .join(Thread, Thread.id == Touch.thread_id)
                    .where(Thread.lead_id == lead_id)
                )
            ).scalars()
        )
        assert len(touches) == 1  # no duplicate


async def test_approve_draft_queues_for_send(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    async with admin_session() as session:
        thread = Thread(tenant_id=tenant_id, lead_id=lead_id, channel=Channel.EMAIL, status="open")
        session.add(thread)
        await session.flush()
        touch = Touch(
            tenant_id=tenant_id,
            thread_id=thread.id,
            step_number=0,
            direction=Direction.OUTBOUND,
            channel=Channel.EMAIL,
            status="pending_approval",
            body="review me",
            drafted_at=datetime.now(UTC),
        )
        session.add(touch)
        await session.flush()
        touch_id = touch.id

    async with admin_session() as session:
        await approve_draft(session, tenant_id, touch_id)

    async with admin_session() as session:
        t = (await session.execute(select(Touch).where(Touch.id == touch_id))).scalar_one()
        assert t.status == "queued"


async def test_reject_draft_marks_skipped(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    async with admin_session() as session:
        thread = Thread(tenant_id=tenant_id, lead_id=lead_id, channel=Channel.EMAIL, status="open")
        session.add(thread)
        await session.flush()
        touch = Touch(
            tenant_id=tenant_id,
            thread_id=thread.id,
            step_number=0,
            direction=Direction.OUTBOUND,
            channel=Channel.EMAIL,
            status="pending_approval",
            body="reject me",
            drafted_at=datetime.now(UTC),
        )
        session.add(touch)
        await session.flush()
        touch_id = touch.id

    async with admin_session() as session:
        await reject_draft(session, tenant_id, touch_id, reason="off_voice")

    async with admin_session() as session:
        t = (await session.execute(select(Touch).where(Touch.id == touch_id))).scalar_one()
        assert t.status == "skipped"
        assert t.error_message == "off_voice"


async def test_regenerate_draft_creates_new_pending(fixtures_dir: Path) -> None:
    """Regenerate: old → skipped, fresh draft created at same step + pending_approval."""
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    async with admin_session() as session:
        thread = Thread(tenant_id=tenant_id, lead_id=lead_id, channel=Channel.EMAIL, status="open")
        session.add(thread)
        await session.flush()
        touch = Touch(
            tenant_id=tenant_id,
            thread_id=thread.id,
            step_number=0,
            direction=Direction.OUTBOUND,
            channel=Channel.EMAIL,
            status="pending_approval",
            body="first draft",
            drafted_at=datetime.now(UTC),
        )
        session.add(touch)
        await session.flush()
        original_touch_id = touch.id

    router = LLMRouter(tenant_id)
    async with admin_session() as session:
        new_id = await regenerate_draft(session, tenant_id, original_touch_id, router)
    assert new_id is not None
    assert new_id != original_touch_id

    async with admin_session() as session:
        original = (
            await session.execute(select(Touch).where(Touch.id == original_touch_id))
        ).scalar_one()
        new_touch = (await session.execute(select(Touch).where(Touch.id == new_id))).scalar_one()
        assert original.status == "skipped"
        assert new_touch.status == "pending_approval"
        assert new_touch.step_number == original.step_number


async def test_list_pending_drafts_returns_only_pending(fixtures_dir: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    async with admin_session() as session:
        thread = Thread(tenant_id=tenant_id, lead_id=lead_id, channel=Channel.EMAIL, status="open")
        session.add(thread)
        await session.flush()
        # One pending, one already sent
        for step, status in [(0, "pending_approval"), (1, "sent")]:
            session.add(
                Touch(
                    tenant_id=tenant_id,
                    thread_id=thread.id,
                    step_number=step,
                    direction=Direction.OUTBOUND,
                    channel=Channel.EMAIL,
                    status=status,
                    subject=f"step {step}",
                    body="body",
                    drafted_at=datetime.now(UTC),
                    sent_at=datetime.now(UTC) if status == "sent" else None,
                )
            )

    async with admin_session() as session:
        items = await list_pending_drafts(session, tenant_id)
    assert len(items) == 1
    assert items[0].step_number == 0
