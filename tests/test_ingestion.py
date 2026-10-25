"""CSV import: parsing, dedup, account reuse, signal seeding."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from disha.db import admin_session
from disha.db.models import Account, Lead, Signal
from disha.ingestion import ImportRow, add_single_lead, import_from_csv
from tests._factories import ensure_demo_tenant

pytestmark = pytest.mark.asyncio


_VALID_HEADER = (
    "first_name,last_name,email,title,company,country,"
    "linkedin_url,notes,source_url,funding_stage,employee_count\n"
)


def _csv(tmp_path: Path, rows: list[str]) -> Path:
    p = tmp_path / "leads.csv"
    p.write_text(_VALID_HEADER + "".join(rows))
    return p


async def test_csv_parses_all_optional_fields(tmp_path: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    path = _csv(
        tmp_path,
        [
            "Aditya,Kumar,aditya@techstack.example,VP Engineering,TechStack,IN,"
            "https://linkedin.com/in/aditya,AI infra bottlenecks,"
            "https://news.example/aditya,series_b,200\n"
        ],
    )
    async with admin_session() as session:
        result = await import_from_csv(session, tenant_id, path, auto_score=False)
    assert result.created == 1
    assert result.skipped_invalid == 0


async def test_csv_skips_row_on_invalid_email(tmp_path: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    path = _csv(
        tmp_path,
        [
            "Aditya,Kumar,aditya@techstack.example,VP,TechStack,IN,,,,series_b,200\n",
            "Bad,Row,not-an-email,VP,X,IN,,,,seed,10\n",
        ],
    )
    async with admin_session() as session:
        result = await import_from_csv(session, tenant_id, path, auto_score=False)
    assert result.created == 1
    assert result.skipped_invalid == 1
    assert any("row 3" in e for e in result.errors)


async def test_csv_skips_row_on_invalid_employee_count(tmp_path: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    path = _csv(
        tmp_path,
        ["Bad,Emp,bad@example.com,VP,X,IN,,,,seed,notanint\n"],
    )
    async with admin_session() as session:
        result = await import_from_csv(session, tenant_id, path, auto_score=False)
    assert result.created == 0
    assert result.skipped_invalid == 1


async def test_dedup_returns_none_on_repeat_email(tmp_path: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    path = _csv(
        tmp_path,
        [
            "Aditya,Kumar,aditya@techstack.example,VP,TechStack,IN,,,,series_b,200\n",
            "Aditya,Kumar,Aditya@TechStack.example,VP,TechStack,IN,,,,series_b,200\n",
        ],
    )
    async with admin_session() as session:
        result = await import_from_csv(session, tenant_id, path, auto_score=False)
    assert result.created == 1
    assert result.skipped_duplicate == 1  # case-insensitive dedup via lowered email


async def test_account_reused_on_same_company_name(tmp_path: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    path = _csv(
        tmp_path,
        [
            "Aditya,Kumar,aditya@techstack.example,VP,TechStack,IN,,,,series_b,200\n",
            "Maya,Patel,maya@techstack.example,PM,  techstack  ,IN,,,,series_b,200\n",
        ],
    )
    async with admin_session() as session:
        result = await import_from_csv(session, tenant_id, path, auto_score=False)
        assert result.created == 2
        account_count = await session.scalar(
            select(func.count()).select_from(Account).where(Account.tenant_id == tenant_id)
        )
    assert account_count == 1


async def test_signals_seeded_with_notes_and_funding(tmp_path: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    path = _csv(
        tmp_path,
        [
            "Aditya,Kumar,aditya@techstack.example,VP Engineering,TechStack,IN,"
            "https://li/aditya,AI infra bottlenecks,,series_b,200\n"
        ],
    )
    async with admin_session() as session:
        result = await import_from_csv(session, tenant_id, path, auto_score=False)
        lead_id = result.lead_ids[0]
        signal_types = list(
            (
                await session.execute(
                    select(Signal.type).where(
                        Signal.tenant_id == tenant_id, Signal.lead_id == lead_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "role_signal" in signal_types
    assert "pain_signal" in signal_types
    assert "funding_signal" in signal_types
    assert "linkedin_signal" in signal_types


async def test_import_dry_run_writes_nothing(tmp_path: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    path = _csv(
        tmp_path,
        ["Aditya,Kumar,aditya@techstack.example,VP,TechStack,IN,,,,series_b,200\n"],
    )
    async with admin_session() as session:
        result = await import_from_csv(session, tenant_id, path, auto_score=False, dry_run=True)
        actual_leads = await session.scalar(
            select(func.count()).select_from(Lead).where(Lead.tenant_id == tenant_id)
        )
    assert result.created == 1
    assert actual_leads == 0


async def test_add_single_lead_returns_none_on_duplicate() -> None:
    tenant_id = await ensure_demo_tenant()
    row = ImportRow(
        first_name="Aditya",
        last_name="Kumar",
        email="aditya@techstack.example",
        title="VP",
        company="TechStack",
        country="IN",
    )
    async with admin_session() as session:
        first = await add_single_lead(session, tenant_id, row, auto_score=False)
        second = await add_single_lead(session, tenant_id, row, auto_score=False)
    assert first is not None
    assert second is None


async def test_import_result_counts_match_actual_db(tmp_path: Path) -> None:
    tenant_id = await ensure_demo_tenant()
    path = _csv(
        tmp_path,
        [
            "A,One,a@x.com,VP,Acme,IN,,,,seed,10\n",
            "B,Two,b@x.com,PM,Acme,IN,,,,seed,10\n",
            "C,Three,c@x.com,Eng,Beta,IN,,,,seed,5\n",
        ],
    )
    async with admin_session() as session:
        result = await import_from_csv(session, tenant_id, path, auto_score=False)
        n_leads = await session.scalar(
            select(func.count()).select_from(Lead).where(Lead.tenant_id == tenant_id)
        )
    assert result.created == n_leads == 3
