"""Test fixtures.

Tests run against the same Postgres instance as dev (the docker-compose one).
Each test session truncates all tables before running, so tests start clean.
RLS isolation tests use real `disha_app` sessions.

Mock-by-default for LLM: an autouse fixture patches LLMRouter._resolve to
always return MockProvider. The real provider classes never instantiate during
tests. If a test path slips past the mock and tries to hit a real provider,
the patched _resolve still returns MockProvider — so by construction no real
network calls happen in the test suite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from disha.db import admin_session, tenant_session
from disha.db.rls import RLS_TABLES
from disha.llm.mock import MockProvider
from disha.llm.router import LLMRouter
from disha.llm.schemas import LLMRequest

PLATFORM_TABLES = (
    "tenant_memberships",
    "platform_suppressions",
    "users",
    "tenants",
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
MOCK_RESPONSES_PATH = FIXTURES_DIR / "mock_llm_responses.json"


@pytest_asyncio.fixture(autouse=True)
async def _clean_db() -> AsyncIterator[None]:
    async with admin_session() as session:
        all_tables = ", ".join(RLS_TABLES + PLATFORM_TABLES)
        await session.execute(text(f"TRUNCATE TABLE {all_tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch: pytest.MonkeyPatch) -> MockProvider:
    """Force every LLMRouter call through MockProvider regardless of model_preference.

    Returns the MockProvider so tests can sanity-check it or inspect rules.
    Also stubs resolve_api_key so credential lookups don't hit the credentials table.
    """
    mock = MockProvider(MOCK_RESPONSES_PATH)

    def _patched_resolve(self: LLMRouter, request: LLMRequest) -> tuple[Any, str, str]:
        del self, request
        return mock, "mock", "mock-model"

    async def _patched_api_key(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "mock"

    monkeypatch.setattr(LLMRouter, "_resolve", _patched_resolve)
    monkeypatch.setattr("disha.llm.router.resolve_api_key", _patched_api_key)
    return mock


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def admin_session_factory():  # type: ignore[no-untyped-def]
    return admin_session


@pytest.fixture
def tenant_session_factory():  # type: ignore[no-untyped-def]
    return tenant_session


__all__ = [
    "AsyncSession",
    "admin_session_factory",
    "fixtures_dir",
    "tenant_session_factory",
]
