"""Week 6 multi-tenant + API: api_keys, tenant active/suspended_at, widen plan CHECK.

Revision ID: 0006_api
Revises: 0005_ingestion
Create Date: 2026-05-27

api_keys table is intentionally NOT in RLS_TABLES — auth lookups happen before
tenant context is established, so it's read by disha_admin (BYPASSRLS) only.

Widening the existing tenants.plan CHECK (added in 0004 with 4 values) to include
the new tier names from Week 6's pricing (growth, scale). Keeping the older
agency/enterprise values for backwards compat — they'll be deprecated separately.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0006_api"
down_revision: str | None = "0005_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PLAN_VALUES = ("starter", "growth", "pro", "scale", "agency", "enterprise")


def upgrade() -> None:
    # --- tenants: active + suspended_at; widen plan CHECK ---
    op.add_column(
        "tenants",
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "tenants",
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Drop existing plan CHECK (added in 0004 as 'ck_tenants_plan' via naming convention)
    # then re-add with widened value set.
    op.drop_constraint("plan", "tenants", type_="check")
    op.create_check_constraint(
        "plan",
        "tenants",
        "plan IN (" + ", ".join(f"'{v}'" for v in _PLAN_VALUES) + ")",
    )

    # --- api_keys: NO RLS, intentional ---
    op.create_table(
        "api_keys",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE", name="fk_api_keys_tenant"),
            nullable=False,
        ),
        sa.Column("key_prefix", sa.String(8), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False, server_default="default"),
        sa.Column("plan", sa.String(16), nullable=False, server_default="starter"),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_api_keys_prefix_active",
        "api_keys",
        ["key_prefix"],
        postgresql_where="active",
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_prefix_active", table_name="api_keys")
    op.drop_table("api_keys")

    # Restore the narrower CHECK from 0004
    op.drop_constraint("plan", "tenants", type_="check")
    op.create_check_constraint(
        "plan",
        "tenants",
        "plan IN ('starter', 'pro', 'agency', 'enterprise')",
    )
    op.drop_column("tenants", "suspended_at")
    op.drop_column("tenants", "active")
