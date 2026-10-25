"""Meta-test: every table with a tenant_id column must have RLS enabled with a policy.

Catches the most common Week N+1 regression: adding a new tenant-scoped table
and forgetting to enable RLS in the migration. Failure here means a tenant-scoped
table is currently readable cross-tenant — fix the migration before merging.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from disha.db import admin_session

pytestmark = pytest.mark.asyncio


_PLATFORM_TABLES_ALLOWLIST = {
    "tenants",
    "users",
    "tenant_memberships",
    "platform_suppressions",
    "alembic_version",
    # api_keys has a tenant_id column but is INTENTIONALLY not RLS-protected:
    # auth lookups (sha256(plaintext) → tenant_id) happen BEFORE tenant context
    # exists, so the row must be readable by disha_admin only.
    "api_keys",
}


async def test_every_tenant_scoped_table_has_rls_with_policy() -> None:
    async with admin_session() as session:
        rows = list(
            (
                await session.execute(
                    text(
                        """
                        SELECT
                            c.relname AS table_name,
                            c.relrowsecurity AS rls_enabled,
                            EXISTS (
                                SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid
                            ) AS has_policy
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relkind = 'r'
                          AND EXISTS (
                              SELECT 1 FROM pg_attribute a
                              WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                          )
                        ORDER BY c.relname;
                        """
                    )
                )
            ).all()
        )

    violators: list[str] = []
    for r in rows:
        if r.table_name in _PLATFORM_TABLES_ALLOWLIST:
            continue
        if not r.rls_enabled:
            violators.append(f"{r.table_name}: RLS not enabled")
        if not r.has_policy:
            violators.append(f"{r.table_name}: no policy attached")

    assert not violators, "Tables with a tenant_id column but no RLS policy:\n  " + "\n  ".join(
        violators
    )
