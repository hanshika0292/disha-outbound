"""Helpers for enabling RLS in migrations.

Policy uses `NULLIF(current_setting('app.tenant_id', true), '')::uuid` so that an
unset GUC produces NULL (not a cast error), and the comparison `tenant_id = NULL`
is unknown — which filters out every row. This is the desired failure mode: forget
to set the tenant context, see no data.
"""

from __future__ import annotations


def enable_rls_sql(table: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;",
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """.strip(),
    ]


def disable_rls_sql(table: str) -> list[str]:
    return [
        f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;",
    ]


RLS_TABLES: tuple[str, ...] = (
    # Group B (per-tenant configuration)
    "icp_rubrics",
    "prompt_packs",
    "voice_guides",
    "cadences",
    "geo_profiles",
    "tenant_settings",
    "credentials",
    # Group C (core business data)
    "accounts",
    "account_aliases",
    "leads",
    "lead_states",
    "signals",
    "threads",
    "touches",
    "replies",
    "events",
    "opportunities",
    "experiments",
    "experiment_assignments",
    "llm_calls",
    "manual_actions",
)
