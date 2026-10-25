"""Verify SAEnum values_callable + migration 0004 produce lowercase storage."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from disha.db import admin_session
from disha.db.models import LeadStateRow, Tenant, Touch
from disha.db.models.enums import LeadState
from tests._factories import ensure_demo_tenant, insert_fixture_lead, load_fixture_leads

pytestmark = pytest.mark.asyncio


async def test_tenant_plan_stored_lowercase() -> None:
    await ensure_demo_tenant()
    async with admin_session() as session:
        rows = list((await session.execute(text("SELECT plan, status FROM tenants"))).all())
        assert rows, "Demo tenant must exist after seed"
        for plan, status in rows:
            assert plan == plan.lower(), f"plan stored uppercase: {plan!r}"
            assert status == status.lower(), f"status stored uppercase: {status!r}"


async def test_lead_state_stored_lowercase(fixtures_dir) -> None:
    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    async with admin_session() as session:
        session.add(
            LeadStateRow(
                lead_id=lead_id,
                tenant_id=tenant_id,
                state=LeadState.APPROVED,
            )
        )

    async with admin_session() as session:
        raw_state = (
            await session.execute(
                text("SELECT state FROM lead_states WHERE lead_id = :lid"),
                {"lid": str(lead_id)},
            )
        ).scalar_one()
        assert raw_state == "approved", f"expected lowercase 'approved' in DB, got {raw_state!r}"

        # Round-trip via SAEnum returns the enum member
        state_row = (
            await session.execute(select(LeadStateRow).where(LeadStateRow.lead_id == lead_id))
        ).scalar_one()
        assert state_row.state == LeadState.APPROVED
        # And the .value matches the stored string
        assert state_row.state.value == raw_state


async def test_check_constraint_rejects_uppercase_state(fixtures_dir) -> None:
    """Migration 0004's CHECK should reject a manual uppercase insert."""
    from sqlalchemy.exc import IntegrityError

    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    with pytest.raises(IntegrityError):
        async with admin_session() as session:
            await session.execute(
                text(
                    "INSERT INTO lead_states (lead_id, tenant_id, state) "
                    "VALUES (:lid, :tid, 'APPROVED')"
                ),
                {"lid": str(lead_id), "tid": str(tenant_id)},
            )


async def test_touch_status_check_includes_pending_approval(fixtures_dir) -> None:
    """Migration 0004 widens touches.status CHECK to include 'pending_approval'."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from disha.db.models import Thread
    from disha.db.models.enums import Channel, Direction

    tenant_id = await ensure_demo_tenant()
    fixture = next(L for L in load_fixture_leads(fixtures_dir) if L["id_tag"] == "A_IN_founder")
    lead_id = await insert_fixture_lead(tenant_id, fixture)

    async with admin_session() as session:
        thread = Thread(tenant_id=tenant_id, lead_id=lead_id, channel=Channel.EMAIL, status="open")
        session.add(thread)
        await session.flush()
        session.add(
            Touch(
                id=uuid4(),
                tenant_id=tenant_id,
                thread_id=thread.id,
                step_number=0,
                direction=Direction.OUTBOUND,
                channel=Channel.EMAIL,
                status="pending_approval",
                body="awaiting review",
                drafted_at=datetime.now(UTC),
            )
        )
    # If we got here, the new status value is accepted by the CHECK.


# unused, kept for forward references in test discovery
_ = Tenant
