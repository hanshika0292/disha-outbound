"""Health check coverage."""

from __future__ import annotations

import pytest

from disha.db import admin_session
from disha.health import run_health_checks
from tests._factories import ensure_demo_tenant

pytestmark = pytest.mark.asyncio


async def test_health_overall_ok_on_clean_state() -> None:
    await ensure_demo_tenant()
    async with admin_session() as session:
        report = await run_health_checks(session)

    by_name = {c.name: c for c in report.checks}
    assert by_name["database"].status == "ok"
    assert by_name["migrations"].status == "ok"
    assert by_name["prompt_packs"].status == "ok"
    assert by_name["env_vars"].status == "ok"
    assert report.overall == "ok"


async def test_health_json_output_parseable() -> None:
    await ensure_demo_tenant()
    async with admin_session() as session:
        report = await run_health_checks(session)
    payload = report.model_dump(mode="json")
    assert payload["overall"] in ("ok", "warn", "fail")
    assert isinstance(payload["checks"], list)
    assert payload["checks"]
    for c in payload["checks"]:
        assert {"name", "status", "detail"} <= c.keys()


async def test_health_warns_when_no_active_prompts() -> None:
    """Truncate prompts → prompt_pack check should warn."""
    from sqlalchemy import text

    await ensure_demo_tenant()
    async with admin_session() as session:
        await session.execute(text("TRUNCATE TABLE prompt_packs CASCADE"))
    async with admin_session() as session:
        report = await run_health_checks(session)

    pp = next(c for c in report.checks if c.name == "prompt_packs")
    assert pp.status == "warn"
    assert report.overall in ("warn", "fail")
