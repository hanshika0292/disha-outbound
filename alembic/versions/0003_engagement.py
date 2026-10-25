"""Engagement layer: touches send/status overhaul + manual_actions + lead_states fields.

Revision ID: 0003_engagement
Revises: 0002_llm_calls
Create Date: 2026-05-26

Schema changes:
- touches:
    + drafted_at (nullable timestamptz)
    + status varchar(16) NOT NULL DEFAULT 'draft' + CHECK
    + gmail_message_id varchar(256)
    + error_message text
    + step_number integer
    * sent_at: NOT NULL → nullable (Week 2 wrongly set this at draft time)
- lead_states:
    + sequence_started_at timestamptz
    + geo_assigned varchar(8)
    * state CHECK constraint: add in_sequence + stale
- manual_actions: new table for LinkedIn manual queue, with RLS
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op
from disha.db.rls import disable_rls_sql, enable_rls_sql

revision: str = "0003_engagement"
down_revision: str | None = "0002_llm_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TOUCH_STATUS_VALUES = ("draft", "queued", "sent", "failed", "skipped")
_MANUAL_ACTION_STATUS_VALUES = ("pending", "done", "skipped")


def _values_in(col: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({rendered})"


def upgrade() -> None:
    # --- touches: add columns ---
    op.add_column("touches", sa.Column("drafted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "touches",
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
    )
    op.add_column("touches", sa.Column("gmail_message_id", sa.String(256), nullable=True))
    op.add_column("touches", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("touches", sa.Column("step_number", sa.Integer(), nullable=True))

    # Backfill BEFORE relaxing sent_at, so existing rows get drafted_at populated.
    op.execute(
        "UPDATE touches SET drafted_at = sent_at WHERE sent_at IS NOT NULL AND drafted_at IS NULL"
    )
    op.execute("UPDATE touches SET status = 'draft' WHERE status IS NULL")

    op.alter_column("touches", "sent_at", nullable=True)

    # Constraint name omits the "ck_" prefix because the naming_convention
    # `ck_%(table_name)s_%(constraint_name)s` re-adds it.
    op.create_check_constraint(
        "status",
        "touches",
        _values_in("status", _TOUCH_STATUS_VALUES),
    )
    op.create_index(
        "ix_touches_tenant_thread_step",
        "touches",
        ["tenant_id", "thread_id", "step_number"],
    )

    # --- lead_states: add columns ---
    op.add_column(
        "lead_states",
        sa.Column("sequence_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("lead_states", sa.Column("geo_assigned", sa.String(8), nullable=True))
    # TODO(followup): SAEnum without `values_callable` stores .name (uppercase)
    # instead of .value (lowercase) for every enum column. Future migration:
    # (1) backfill every enum-typed column with lowercase values,
    # (2) add values_callable=lambda obj: [e.value for e in obj] to all SAEnum cols,
    # (3) then add a CHECK constraint on lead_states.state.
    # See the wrap-up notes for context.

    # --- manual_actions table ---
    op.create_table(
        "manual_actions",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE", name="fk_manual_actions_tenant"),
            nullable=False,
        ),
        sa.Column(
            "touch_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("touches.id", ondelete="CASCADE", name="fk_manual_actions_touch"),
            nullable=False,
        ),
        sa.Column("lead_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "status",
        "manual_actions",
        _values_in("status", _MANUAL_ACTION_STATUS_VALUES),
    )
    op.create_index(
        "ix_manual_actions_tenant_status_created",
        "manual_actions",
        ["tenant_id", "status", "created_at"],
    )

    for stmt in enable_rls_sql("manual_actions"):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in disable_rls_sql("manual_actions"):
        op.execute(stmt)
    op.drop_index("ix_manual_actions_tenant_status_created", table_name="manual_actions")
    op.drop_constraint("ck_manual_actions_status", "manual_actions", type_="check")
    op.drop_table("manual_actions")

    op.drop_column("lead_states", "geo_assigned")
    op.drop_column("lead_states", "sequence_started_at")

    op.drop_index("ix_touches_tenant_thread_step", table_name="touches")
    op.drop_constraint("ck_touches_status", "touches", type_="check")
    op.alter_column("touches", "sent_at", nullable=False)
    op.drop_column("touches", "step_number")
    op.drop_column("touches", "error_message")
    op.drop_column("touches", "gmail_message_id")
    op.drop_column("touches", "status")
    op.drop_column("touches", "drafted_at")
