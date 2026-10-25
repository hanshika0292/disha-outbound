"""
Fly.io DB initialisation script — used as the release_command.

Fully idempotent: safe to run on every deploy whether the schema is brand-new
or already fully set up.

Steps:
  1. create_all(checkfirst=True)     — skips tables that already exist
  2. Enable RLS on all RLS tables    — skips policies that already exist
  3. Grant disha_app permissions     — GRANT is idempotent in Postgres
  4. Stamp alembic_version to head   — skips if already at head

Run as: /app/.venv/bin/python /app/scripts/fly_init_db.py   (Fly release_command)
   or:  uv run python scripts/fly_init_db.py                    (local, from repo root)
"""
import asyncio
import sys
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

# Repo root: /app inside the image, the checkout when run locally.
ROOT = Path(__file__).resolve().parent.parent

# Bootstrap PYTHONPATH (same as Dockerfile ENV)
sys.path.insert(0, str(ROOT / "src"))

from disha.config import get_settings  # noqa: E402
from disha.db.models import metadata as target_metadata  # noqa: E402
from disha.db.rls import RLS_TABLES, enable_rls_sql  # noqa: E402


async def _enable_rls_idempotent(conn: AsyncConnection) -> None:
    """Enable RLS + create tenant-isolation policies, skipping any that exist."""
    for table in RLS_TABLES:
        stmts = enable_rls_sql(table)
        for stmt in stmts:
            try:
                # Use a savepoint so a DuplicateObject error on the policy
                # doesn't abort the whole transaction.
                sp = await conn.begin_nested()
                await conn.execute(text(stmt))
                await sp.commit()
            except ProgrammingError as exc:
                await sp.rollback()
                msg = str(exc).lower()
                if "already exists" in msg or "duplicate" in msg:
                    pass  # policy or RLS already set — fine
                else:
                    raise


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url_admin, echo=False)

    async with engine.begin() as conn:
        print("→ create_all (checkfirst=True) …")
        await conn.run_sync(lambda c: target_metadata.create_all(c, checkfirst=True))

        print("→ enabling RLS (idempotent) …")
        await _enable_rls_idempotent(conn)

        print("→ additive column migrations (idempotent) …")
        # create_all only creates missing TABLES — new columns on existing
        # tables need explicit ALTERs. ADD COLUMN IF NOT EXISTS is idempotent.
        for ddl in (
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash BYTEA",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE waitlist_entries ADD COLUMN IF NOT EXISTS answers JSONB",
            "ALTER TABLE waitlist_entries ADD COLUMN IF NOT EXISTS invited_at TIMESTAMPTZ",
        ):
            await conn.execute(text(ddl))

        print("→ granting disha_app permissions …")
        await conn.execute(text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO disha_app"
        ))
        await conn.execute(text(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO disha_app"
        ))

        print("→ stamping alembic_version to head …")
        alembic_cfg = Config(str(ROOT / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(ROOT / "alembic"))
        script = ScriptDirectory.from_config(alembic_cfg)
        head_rev = script.get_current_head()

        def _stamp(sync_conn):  # type: ignore[no-untyped-def]
            ctx = MigrationContext.configure(sync_conn)
            ctx.stamp(script, head_rev)

        await conn.run_sync(_stamp)
        print(f"   stamped to {head_rev}")

    await engine.dispose()
    print("✓ DB initialisation complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"✗ DB initialisation failed: {exc}", file=sys.stderr)
        sys.exit(1)
