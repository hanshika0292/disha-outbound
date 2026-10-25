"""Sourcing data shapes.

SourceQuery   — ICP expressed as provider-agnostic search filters.
SourceCandidate — one prospect a provider returns; maps onto ImportRow.
SourcingResult — outcome of a source_leads() run.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SourceQuery(BaseModel):
    """What 'a right lead' looks like, as filters every provider can honor.

    Built from the ICP rubric (see translate.icp_to_query) and optionally
    overridden by the caller (UI fields / saved settings).
    """

    titles: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    funding_stages: list[str] = Field(default_factory=list)
    employee_min: int | None = None
    employee_max: int | None = None
    keywords: list[str] = Field(default_factory=list)
    limit: int = 25


class SourceCandidate(BaseModel):
    """A single prospect from a provider. `email` is optional because some
    providers (web research especially) can't always resolve one — the
    orchestrator skips email-less candidates since dedup keys on email."""

    first_name: str
    last_name: str
    email: str | None = None
    title: str
    company: str
    country: str
    linkedin_url: str | None = None
    industry: str | None = None
    funding_stage: str | None = None
    employee_count: int | None = None
    source_url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SourcingResult(BaseModel):
    provider: str
    found: int = 0
    added: int = 0
    duplicates: int = 0
    skipped_no_email: int = 0
    skipped_invalid: int = 0
    scored: int = 0
    lead_ids: list[UUID] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
