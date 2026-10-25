"""System health checks: db, migrations, prompts, env vars.

Returns a HealthReport with per-check status. Used by `disha health` for both
human-readable output and `--json` for monitoring.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from disha.config import get_settings
from disha.db.models import PromptPack

HealthStatus = Literal["ok", "warn", "fail"]


class HealthCheck(BaseModel):
    name: str
    status: HealthStatus
    detail: str


class HealthReport(BaseModel):
    overall: HealthStatus
    checks: list[HealthCheck]
    checked_at: datetime


def _alembic_config_path() -> Path:
    # src/disha/health.py → .../disha/alembic.ini
    return Path(__file__).resolve().parent.parent.parent / "alembic.ini"


async def _check_database(session: AsyncSession) -> HealthCheck:
    try:
        await session.execute(text("SELECT 1"))
        return HealthCheck(name="database", status="ok", detail="connected")
    except Exception as exc:
        return HealthCheck(name="database", status="fail", detail=str(exc)[:200])


async def _check_migrations(session: AsyncSession) -> HealthCheck:
    cfg_path = _alembic_config_path()
    if not cfg_path.exists():
        return HealthCheck(
            name="migrations",
            status="warn",
            detail=f"alembic.ini not found at {cfg_path}",
        )
    try:
        alembic_cfg = AlembicConfig(str(cfg_path))
        script = ScriptDirectory.from_config(alembic_cfg)
        head = script.get_current_head()

        def _read_current(sync_conn):  # type: ignore[no-untyped-def]
            mc = MigrationContext.configure(sync_conn)
            return mc.get_current_revision()

        conn = await session.connection()
        current = await conn.run_sync(_read_current)
    except Exception as exc:
        return HealthCheck(name="migrations", status="warn", detail=f"introspection failed: {exc}")

    if current == head:
        return HealthCheck(name="migrations", status="ok", detail=f"at head ({head})")
    return HealthCheck(
        name="migrations",
        status="warn",
        detail=f"behind: current={current} head={head}",
    )


async def _check_prompt_packs(session: AsyncSession) -> HealthCheck:
    try:
        n = (
            await session.execute(
                select(func.count()).select_from(PromptPack).where(PromptPack.is_active.is_(True))
            )
        ).scalar_one()
    except Exception as exc:
        return HealthCheck(name="prompt_packs", status="warn", detail=str(exc)[:200])
    if int(n) >= 1:
        return HealthCheck(
            name="prompt_packs", status="ok", detail=f"{int(n)} active prompt pack(s)"
        )
    return HealthCheck(name="prompt_packs", status="warn", detail="no active prompt pack")


def _check_env_vars() -> HealthCheck:
    s = get_settings()
    required = {
        "DATABASE_URL_ADMIN": bool(s.database_url_admin),
        "DATABASE_URL_APP": bool(s.database_url_app),
        "DISHA_FERNET_KEY": bool(s.disha_fernet_key.get_secret_value()),
        "DISHA_ENV": bool(s.disha_env),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        return HealthCheck(name="env_vars", status="fail", detail=f"missing: {', '.join(missing)}")
    return HealthCheck(
        name="env_vars",
        status="ok",
        detail=f"all required vars set ({len(required)})",
    )


def _overall(checks: list[HealthCheck]) -> HealthStatus:
    if any(c.status == "fail" for c in checks):
        return "fail"
    if any(c.status == "warn" for c in checks):
        return "warn"
    return "ok"


async def run_health_checks(session: AsyncSession) -> HealthReport:
    checks: list[HealthCheck] = []
    checks.append(await _check_database(session))
    if checks[-1].status != "fail":
        checks.append(await _check_migrations(session))
        checks.append(await _check_prompt_packs(session))
    checks.append(_check_env_vars())
    return HealthReport(
        overall=_overall(checks),
        checks=checks,
        checked_at=datetime.now(UTC),
    )
