"""LLM audit log table + scored_with_prompt_version on lead_states.

Revision ID: 0002_llm_calls
Revises: 0001_initial
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op
from disha.db.rls import disable_rls_sql, enable_rls_sql

revision: str = "0002_llm_calls"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", name="fk_llm_calls_tenant_id_tenants"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entity_type", sa.String(32), nullable=True),
        sa.Column("entity_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_llm_calls_tenant_created", "llm_calls", ["tenant_id", "created_at"])
    op.create_index(
        "ix_llm_calls_tenant_purpose_created",
        "llm_calls",
        ["tenant_id", "purpose", "created_at"],
    )

    op.add_column(
        "lead_states",
        sa.Column("scored_with_prompt_version", sa.Integer(), nullable=True),
    )

    for stmt in enable_rls_sql("llm_calls"):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in disable_rls_sql("llm_calls"):
        op.execute(stmt)

    op.drop_column("lead_states", "scored_with_prompt_version")
    op.drop_index("ix_llm_calls_tenant_purpose_created", table_name="llm_calls")
    op.drop_index("ix_llm_calls_tenant_created", table_name="llm_calls")
    op.drop_table("llm_calls")
