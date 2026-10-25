"""Sourcing: ICP→query translation, MockSource, orchestrator dedup/score, API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from disha.api.app import app
from disha.config import get_settings
from disha.db import admin_session
from disha.sourcing import get_source, icp_to_query, source_leads
from disha.sourcing.schemas import SourceCandidate
from disha.tenant.provisioner import provision_tenant

pytestmark = pytest.mark.asyncio

UI_TOKEN = "test-ui-token-sourcing"
BEARER = {"Authorization": f"Bearer {UI_TOKEN}"}

_ICP = {
    "name": "Test ICP",
    "dimensions": [
        {
            "key": "fit_funding",
            "scoring": "categorical",
            "rules": {"seed": 0.4, "series_a": 1.0, "series_b": 0.9, "series_c": 0.5},
        },
        {
            "key": "fit_size",
            "scoring": "categorical",
            "rules": {"1-10": 0.3, "11-50": 1.0, "51-200": 0.9, "500+": 0.1},
        },
        {
            "key": "fit_geo",
            "scoring": "categorical",
            "rules": {"IN": 1.0, "US": 0.9, "UK": 0.5, "other": 0.2},
        },
        {"key": "fit_role", "scoring": "llm"},
    ],
}


# ---- unit: translator ----


def test_icp_to_query_keeps_high_value_categoricals() -> None:
    q = icp_to_query(_ICP)
    # funding >= 0.6 → series_a, series_b (not seed/series_c)
    assert set(q.funding_stages) == {"series_a", "series_b"}
    # geo >= 0.5 → IN, US, UK (not 'other')
    assert set(q.countries) == {"IN", "US", "UK"}
    # size buckets >= 0.8 → 11-50, 51-200 → min 11, max 200
    assert q.employee_min == 11
    assert q.employee_max == 200
    # titles/industries default when not in rubric
    assert q.titles and q.industries


def test_icp_to_query_overrides_win() -> None:
    q = icp_to_query(_ICP, overrides={"countries": ["DE"], "limit": 5})
    assert q.countries == ["DE"]
    assert q.limit == 5


def test_icp_to_query_empty_config_is_safe() -> None:
    q = icp_to_query({})
    assert q.countries == []
    assert q.funding_stages == []
    assert q.titles  # still has defaults


# ---- unit: MockSource ----


async def test_mock_source_honors_filters() -> None:
    q = icp_to_query(_ICP, overrides={"countries": ["IN"], "limit": 50})
    cands = await get_source("mock").search(q)
    assert len(cands) > 0
    assert all(c.country == "IN" for c in cands)
    assert all(c.email for c in cands)  # mock always provides emails


async def test_mock_source_respects_limit() -> None:
    q = icp_to_query({}, limit=2)
    cands = await get_source("mock").search(q)
    assert len(cands) <= 2


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError):
        get_source("nope")


# ---- orchestrator: add + dedup ----


async def _provision(slug: str) -> UUID:
    async with admin_session() as session:
        result = await provision_tenant(
            session, slug=slug, name="X", email="x@x.co", plan="starter"
        )
    return result.tenant_id


def _candidate(email: str) -> SourceCandidate:
    return SourceCandidate(
        first_name="Sam",
        last_name="Lee",
        email=email,
        title="CTO",
        company="Acme AI",
        country="IN",
        funding_stage="series_a",
        employee_count=40,
    )


async def test_source_leads_adds_and_dedupes() -> None:
    tenant_id = await _provision("src-orch")
    cands = [_candidate("a@acme.example"), _candidate("b@acme.example")]

    async with admin_session() as session:
        first = await source_leads(
            session, tenant_id, candidates=cands, auto_score=False
        )
    assert first.added == 2
    assert first.found == 2

    # Re-run same candidates → both dedupe
    async with admin_session() as session:
        second = await source_leads(
            session, tenant_id, candidates=cands, auto_score=False
        )
    assert second.added == 0
    assert second.duplicates == 2


async def test_source_leads_skips_no_email() -> None:
    tenant_id = await _provision("src-noemail")
    cand = _candidate("c@acme.example")
    cand.email = None
    async with admin_session() as session:
        res = await source_leads(session, tenant_id, candidates=[cand], auto_score=False)
    assert res.skipped_no_email == 1
    assert res.added == 0


async def test_source_leads_dry_run_writes_nothing() -> None:
    tenant_id = await _provision("src-dry")
    async with admin_session() as session:
        res = await source_leads(
            session,
            tenant_id,
            candidates=[_candidate("d@acme.example")],
            auto_score=False,
            dry_run=True,
        )
        assert res.added == 1  # counted, but rolled back
    # Verify nothing persisted: a fresh non-dry run should add it again
    async with admin_session() as session:
        res2 = await source_leads(
            session, tenant_id, candidates=[_candidate("d@acme.example")], auto_score=False
        )
    assert res2.added == 1
    assert res2.duplicates == 0


# ---- API ----


@pytest.fixture(autouse=True)
def _set_ui_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISHA_UI_TOKEN", UI_TOKEN)
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def demo_tenant() -> UUID:
    async with admin_session() as session:
        result = await provision_tenant(
            session, slug="demo", name="Demo", email="h@x.co", plan="pro"
        )
    return result.tenant_id


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_sourcing_preview_endpoint(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.post(
        "/api/v1/sourcing/preview", headers=BEARER, json={"limit": 5}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "query" in body and "candidates" in body
    assert len(body["candidates"]) <= 5


async def test_sourcing_run_endpoint_adds_leads(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    # Preview first, then run the returned candidates
    pv = await client.post("/api/v1/sourcing/preview", headers=BEARER, json={"limit": 3})
    candidates = pv.json()["candidates"]
    assert len(candidates) > 0

    resp = await client.post(
        "/api/v1/sourcing/run",
        headers=BEARER,
        json={"candidates": candidates, "auto_score": False},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["added"] == len(candidates)
    assert result["provider"] == "mock"


async def test_sourcing_run_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/v1/sourcing/run", json={})
    assert resp.status_code == 401
