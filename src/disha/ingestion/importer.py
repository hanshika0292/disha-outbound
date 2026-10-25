"""Lead import: CSV parser + dedup + signal seeding + optional auto-score.

Per-lead failures during auto-score are logged + counted but never abort the
batch — one bad LLM call shouldn't roll back 50 successfully imported leads.

Dedup uses the same `hash_email(email, tenant.email_hash_salt)` Week 1 helper
(sha256(salt || email_normalized)) — switching to HMAC would invalidate every
hash already in the leads table, so we stay with the existing scheme.
"""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, EmailStr, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from disha.crypto import encrypt, hash_email
from disha.db.models import Account, Lead, Signal, Tenant
from disha.intelligence import score_lead
from disha.llm import LLMRouter

logger = logging.getLogger(__name__)


_VALID_FUNDING_STAGES = {
    "pre_seed",
    "seed",
    "series_a",
    "series_b",
    "series_c",
    "growth",
    "public",
    "bootstrapped",
    "unknown",
}


class ImportRow(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    title: str
    company: str
    country: str
    linkedin_url: str | None = None
    notes: str | None = None
    source_url: str | None = None
    funding_stage: str | None = None
    employee_count: int | None = None
    # verified | risky | guessed — set by sourcing providers; the sequencer
    # uses it to gate sends (guessed emails bounce and damage sender rep).
    email_confidence: str | None = None

    @field_validator("funding_stage")
    @classmethod
    def _validate_funding(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if v not in _VALID_FUNDING_STAGES:
            raise ValueError(f"unrecognized funding_stage={v!r}")
        return v


class ImportResult(BaseModel):
    total_rows: int = 0
    created: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    errors: list[str] = []
    lead_ids: list[UUID] = []


def _normalize_company(name: str) -> str:
    return name.strip().lower()


async def _get_tenant_salt(session: AsyncSession, tenant_id: UUID) -> bytes:
    salt: bytes = (
        await session.execute(select(Tenant.email_hash_salt).where(Tenant.id == tenant_id))
    ).scalar_one()
    return salt


async def _lead_exists(session: AsyncSession, tenant_id: UUID, email: str, salt: bytes) -> bool:
    h = hash_email(email, salt)
    found = (
        await session.execute(
            select(Lead.id).where(Lead.tenant_id == tenant_id, Lead.email_hash == h).limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


async def _find_or_create_account(
    session: AsyncSession,
    tenant_id: UUID,
    company: str,
    funding_stage: str | None,
    employee_count: int | None,
    hq_country: str,
) -> UUID:
    """Lookup by (tenant_id, lower(trim(name))); fill in missing optional fields."""
    existing = (
        await session.execute(
            select(Account).where(
                Account.tenant_id == tenant_id,
                func.lower(func.trim(Account.name)) == _normalize_company(company),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Backfill optional fields if they were null before.
        if existing.funding_stage is None and funding_stage:
            existing.funding_stage = funding_stage
        if existing.employee_count is None and employee_count is not None:
            existing.employee_count = employee_count
        if existing.hq_country is None and hq_country:
            existing.hq_country = hq_country
        await session.flush()
        return existing.id

    account = Account(
        tenant_id=tenant_id,
        domain=company.lower().replace(" ", "") + ".unknown",  # placeholder; user can edit later
        name=company,
        funding_stage=funding_stage,
        employee_count=employee_count,
        hq_country=hq_country,
    )
    session.add(account)
    try:
        await session.flush()
    except IntegrityError:
        # Race or domain collision; fall back to re-select on (tenant, normalized name).
        await session.rollback()
        existing = (
            await session.execute(
                select(Account).where(
                    Account.tenant_id == tenant_id,
                    func.lower(func.trim(Account.name)) == _normalize_company(company),
                )
            )
        ).scalar_one()
        return existing.id
    return account.id


def _seed_signal_rows(
    tenant_id: UUID,
    lead_id: UUID,
    account_id: UUID,
    row: ImportRow,
    now: datetime,
    source: str = "csv_import",
) -> list[Signal]:
    """Fits Week 5's signal shape into existing (type, source, payload) schema."""
    out: list[Signal] = []
    out.append(
        Signal(
            tenant_id=tenant_id,
            lead_id=lead_id,
            account_id=account_id,
            type="role_signal",
            source=source,
            payload={"text": f"{row.title} at {row.company}", "strength": 0.7},
            observed_at=now,
        )
    )
    if row.notes:
        out.append(
            Signal(
                tenant_id=tenant_id,
                lead_id=lead_id,
                account_id=account_id,
                type="pain_signal",
                source=source,
                payload={"text": row.notes, "strength": 0.5},
                observed_at=now,
            )
        )
    if row.funding_stage:
        out.append(
            Signal(
                tenant_id=tenant_id,
                lead_id=lead_id,
                account_id=account_id,
                type="funding_signal",
                source=source,
                payload={
                    "text": f"{row.company} is {row.funding_stage} stage",
                    "strength": 0.6,
                },
                observed_at=now,
            )
        )
    if row.linkedin_url:
        out.append(
            Signal(
                tenant_id=tenant_id,
                lead_id=lead_id,
                account_id=account_id,
                type="linkedin_signal",
                source=source,
                payload={"text": row.linkedin_url, "strength": 0.3},
                observed_at=now,
            )
        )
    return out


async def add_single_lead(
    session: AsyncSession,
    tenant_id: UUID,
    row: ImportRow,
    *,
    auto_score: bool = True,
    router: LLMRouter | None = None,
    source: str = "csv_import",
) -> UUID | None:
    """Add one lead. Returns new lead_id or None if duplicate by email.

    `source` labels the lead + its signals with provenance (e.g. 'apollo',
    'mock', 'web' for sourced leads); defaults to 'csv_import'.
    """
    salt = await _get_tenant_salt(session, tenant_id)
    if await _lead_exists(session, tenant_id, row.email, salt):
        return None

    account_id = await _find_or_create_account(
        session,
        tenant_id,
        row.company,
        row.funding_stage,
        row.employee_count,
        row.country,
    )
    now = datetime.now(UTC)
    lead = Lead(
        tenant_id=tenant_id,
        account_id=account_id,
        email_hash=hash_email(row.email, salt),
        email_encrypted=encrypt(row.email),
        first_name=row.first_name,
        last_name=row.last_name,
        role_title=row.title,
        linkedin_url=row.linkedin_url,
        source=source,
        notes=row.notes,
        raw=(
            {
                k: v
                for k, v in {
                    "source_url": row.source_url,
                    "email_confidence": row.email_confidence,
                }.items()
                if v
            }
            or None
        ),
    )
    session.add(lead)
    await session.flush()

    for sig in _seed_signal_rows(tenant_id, lead.id, account_id, row, now, source):
        session.add(sig)

    logger.info(
        "lead imported",
        extra={
            "lead_id": str(lead.id),
            "tenant_id": str(tenant_id),
            "email_domain": row.email.split("@", 1)[1] if "@" in row.email else "?",
            "company": row.company,
        },
    )

    if auto_score:
        await _try_score(session, tenant_id, lead.id, router)
    return lead.id


async def _try_score(
    session: AsyncSession,
    tenant_id: UUID,
    lead_id: UUID,
    router: LLMRouter | None,
) -> None:
    """Auto-score a freshly imported lead. Per-lead errors are non-fatal."""
    used_router = router or LLMRouter(tenant_id)
    try:
        await score_lead(session, tenant_id, lead_id, used_router)
    except Exception:
        logger.exception(
            "auto-score failed",
            extra={"lead_id": str(lead_id), "tenant_id": str(tenant_id)},
        )


async def import_from_csv(
    session: AsyncSession,
    tenant_id: UUID,
    file_path: Path,
    *,
    auto_score: bool = True,
    dry_run: bool = False,
    router: LLMRouter | None = None,
) -> ImportResult:
    """Parse CSV → validate → dedup → insert. dry_run wraps in a savepoint that rolls back."""
    result = ImportResult()
    salt = await _get_tenant_salt(session, tenant_id)

    rows: list[dict[str, str]] = []
    with file_path.open() as fh:
        reader = csv.DictReader(fh)
        for raw_row in reader:
            rows.append({k: (v or "").strip() for k, v in raw_row.items()})
    result.total_rows = len(rows)

    savepoint = await session.begin_nested() if dry_run else None
    used_router = router or LLMRouter(tenant_id)

    try:
        for idx, raw in enumerate(rows, start=2):  # row 1 = header
            try:
                parsed = ImportRow.model_validate(
                    {
                        **raw,
                        "employee_count": (
                            int(raw["employee_count"]) if raw.get("employee_count") else None
                        ),
                    }
                )
            except (ValidationError, ValueError) as exc:
                result.skipped_invalid += 1
                result.errors.append(f"row {idx}: {type(exc).__name__}: {exc}")
                continue

            if await _lead_exists(session, tenant_id, parsed.email, salt):
                result.skipped_duplicate += 1
                continue

            lead_id = await add_single_lead(
                session,
                tenant_id,
                parsed,
                auto_score=False,  # batch-score after all inserts
                router=used_router,
            )
            if lead_id is None:
                # Race-condition guard (someone inserted between our check and add)
                result.skipped_duplicate += 1
                continue
            result.created += 1
            result.lead_ids.append(lead_id)
    finally:
        if savepoint is not None:
            await savepoint.rollback()

    if auto_score and not dry_run:
        for lead_id in result.lead_ids:
            await _try_score(session, tenant_id, lead_id, used_router)

    logger.info(
        "import complete",
        extra={
            "tenant_id": str(tenant_id),
            "n_total": result.total_rows,
            "n_created": result.created,
            "n_duplicates": result.skipped_duplicate,
            "n_invalid": result.skipped_invalid,
            "dry_run": dry_run,
        },
    )
    return result
