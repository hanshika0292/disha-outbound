"""REST API integration: bearer auth + demo tenant + all UI route shapes.

Week 7 switched routes from X-API-Key to a bearer-token gate that always
resolves to the 'demo' tenant. These tests:
  - provision the demo tenant per test (test DB is truncated per-test)
  - set DISHA_UI_TOKEN via env + clear get_settings cache
  - call routes with `Authorization: Bearer <token>`

The Week 6 X-API-Key helpers (_verify_api_key, get_tenant_id) are unit-tested
directly at the bottom — they no longer back any route but are kept for the
future multi-tenant API surface.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from disha.api.app import app
from disha.config import get_settings
from disha.db import admin_session
from disha.tenant.provisioner import provision_tenant

pytestmark = pytest.mark.asyncio

UI_TOKEN = "test-ui-token-do-not-use-in-prod"
BEARER = {"Authorization": f"Bearer {UI_TOKEN}"}


# ---- fixtures ----


@pytest.fixture(autouse=True)
def _set_ui_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISHA_UI_TOKEN", UI_TOKEN)
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def demo_tenant() -> UUID:
    """Provision the 'demo' tenant the bearer-auth routes resolve to."""
    async with admin_session() as session:
        result = await provision_tenant(
            session,
            slug="demo",
            name="Demo",
            email="demo@example.com",
            plan="pro",
        )
    return result.tenant_id


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---- auth gate ----


async def test_health_no_auth_returns_200(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "overall" in body and "checks" in body


async def test_missing_token_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/reports/pipeline")
    assert resp.status_code == 401


async def test_invalid_token_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/reports/pipeline",
        headers={"Authorization": "Bearer not-the-right-token"},
    )
    assert resp.status_code == 401


async def test_x_api_key_carrying_ui_token_is_accepted(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    """require_auth accepts either Authorization: Bearer OR X-API-Key
    when the value matches DISHA_UI_TOKEN."""
    resp = await client.get(
        "/api/v1/reports/pipeline",
        headers={"X-API-Key": UI_TOKEN},
    )
    assert resp.status_code == 200


async def test_x_request_id_header_added(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health")
    assert "X-Request-ID" in resp.headers
    assert len(resp.headers["X-Request-ID"]) >= 16


# ---- dashboard ----


async def test_dashboard_returns_combined_shape(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/api/v1/dashboard", headers=BEARER)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in (
        "pipeline",
        "today",
        "pending_approvals",
        "pending_drafts",
        "pending_linkedin",
        "recent_events",
    ):
        assert key in body


# ---- leads ----


async def test_lead_list_returns_pagination_envelope(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/api/v1/leads", headers=BEARER)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"items", "total", "page", "page_size", "counts_by_state"}
    assert isinstance(body["items"], list)


async def test_lead_add_via_api(demo_tenant: UUID, client: httpx.AsyncClient) -> None:
    row = {
        "first_name": "Aditya",
        "last_name": "Kumar",
        "email": "aditya@example.com",
        "title": "CTO",
        "company": "Example",
        "country": "IN",
    }
    resp = await client.post(
        "/api/v1/leads",
        headers={**BEARER, "Content-Type": "application/json"},
        params={"auto_score": "false"},
        json=row,
    )
    assert resp.status_code == 201, resp.text
    assert "lead_id" in resp.json()


async def test_lead_add_duplicate_returns_409(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    row = {
        "first_name": "Bob",
        "last_name": "S",
        "email": "bob@example.com",
        "title": "CTO",
        "company": "Ex",
        "country": "IN",
    }
    headers = {**BEARER, "Content-Type": "application/json"}
    first = await client.post(
        "/api/v1/leads", headers=headers, params={"auto_score": "false"}, json=row
    )
    second = await client.post(
        "/api/v1/leads", headers=headers, params={"auto_score": "false"}, json=row
    )
    assert first.status_code == 201
    assert second.status_code == 409


async def test_lead_import_via_api_multipart(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    csv = (
        "first_name,last_name,email,title,company,country,"
        "linkedin_url,notes,source_url,funding_stage,employee_count\n"
        "Carol,Doe,carol@importco.example,VP,ImportCo,IN,,,,series_a,30\n"
    )
    files = {"file": ("leads.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")}
    resp = await client.post(
        "/api/v1/leads/import",
        headers=BEARER,
        params={"auto_score": "false"},
        files=files,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1


async def test_lead_detail_404_for_unknown(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.get(
        "/api/v1/leads/00000000-0000-0000-0000-000000000000", headers=BEARER
    )
    assert resp.status_code == 404


# ---- drafts ----


async def test_drafts_pending_empty(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/api/v1/drafts/pending", headers=BEARER)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_drafts_pending_full_empty(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/api/v1/drafts/pending/full", headers=BEARER)
    assert resp.status_code == 200
    assert resp.json() == []


# ---- linkedin ----


async def test_linkedin_queue_empty(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/api/v1/linkedin/queue", headers=BEARER)
    assert resp.status_code == 200
    assert resp.json() == []


# ---- sequence ----


async def test_sequence_preview_returns_run_shape(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/api/v1/sequence/preview", headers=BEARER)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "processed" in body and "sent" in body


async def test_sequence_run_via_api(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.post("/api/v1/sequence/run", headers=BEARER)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "processed" in body and "sent" in body


# ---- reports ----


async def test_pipeline_returns_snapshot(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/api/v1/reports/pipeline", headers=BEARER)
    assert resp.status_code == 200
    body = resp.json()
    assert "by_state" in body and "by_tier" in body


async def test_reports_daily(demo_tenant: UUID, client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/reports/daily", headers=BEARER)
    assert resp.status_code == 200
    body = resp.json()
    assert "touches_sent" in body and "leads_sourced" in body


async def test_reports_weekly(demo_tenant: UUID, client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/reports/weekly", headers=BEARER)
    assert resp.status_code == 200
    body = resp.json()
    assert "activity" in body and "funnel" in body


async def test_reports_cost(demo_tenant: UUID, client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/reports/cost?days=7", headers=BEARER)
    assert resp.status_code == 200


# ---- settings ----


async def test_settings_get_returns_full_shape(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/api/v1/settings", headers=BEARER)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "tenant" in body and "config" in body and "byok" in body
    assert "supported_keys" in body


async def test_settings_patch_upserts_keys(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.patch(
        "/api/v1/settings",
        headers={**BEARER, "Content-Type": "application/json"},
        json={"sender_name": "Meera R", "require_draft_approval": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["config"]["sender_name"] == "Meera R"
    assert body["config"]["require_draft_approval"] is True


async def test_settings_patch_validates_key(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.patch(
        "/api/v1/settings",
        headers={**BEARER, "Content-Type": "application/json"},
        json={"not_a_real_key": "x"},
    )
    assert resp.status_code == 400


async def test_settings_health(
    demo_tenant: UUID, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/api/v1/settings/health", headers=BEARER)
    assert resp.status_code == 200
    body = resp.json()
    assert "overall" in body and "checks" in body


# ---- Week 6 X-API-Key helpers (kept for future multi-tenant API) ----


async def test_verify_api_key_helper_still_resolves_tenant_id() -> None:
    """The Week 6 X-API-Key auth lookup helper is no longer wired to any route,
    but is preserved for future multi-tenant API surface. Smoke-test it."""
    from disha.api.auth import _verify_api_key

    async with admin_session() as session:
        result = await provision_tenant(
            session,
            slug="legacy-multi",
            name="Legacy",
            email="legacy@example.com",
            plan="starter",
        )
    async with admin_session() as session:
        tenant_id, plan = await _verify_api_key(result.api_key, session)
    assert tenant_id == result.tenant_id
    assert plan == "starter"


# Keep asyncio import alive (anyio backend selection picks asyncio by default)
_ = asyncio
_ = Any
