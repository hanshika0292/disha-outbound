"""Week 5 ingestion + reply loop: thread_id, reply dedup, lead source/notes, pause field.

Revision ID: 0005_ingestion
Revises: 0004_cockpit
Create Date: 2026-05-27

- touches.gmail_thread_id + partial index for poller
- replies: gmail_message_id (UNIQUE per tenant), sender_email, subject, raw_headers
- lead_states.sequence_paused_until (used by reply handler for not_now / OOO)
- leads.notes + leads.source default 'manual'
- accounts: functional UNIQUE index on (tenant_id, lower(trim(name))) for import dedup

The `signals` table already exists from migration 0001 with shape
(type, source, payload jsonb, observed_at). Importer will fit the new signal
types into that schema rather than reshape it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_ingestion"
down_revision: str | None = "0004_cockpit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- touches ---
    op.add_column(
        "touches",
        sa.Column("gmail_thread_id", sa.String(256), nullable=True),
    )
    op.create_index(
        "ix_touches_tenant_gmail_thread",
        "touches",
        ["tenant_id", "gmail_thread_id"],
        postgresql_where="gmail_thread_id IS NOT NULL",
    )

    # --- replies ---
    op.add_column("replies", sa.Column("gmail_message_id", sa.String(256), nullable=True))
    op.add_column("replies", sa.Column("sender_email", sa.String(256), nullable=True))
    op.add_column("replies", sa.Column("subject", sa.String(512), nullable=True))
    op.add_column(
        "replies",
        sa.Column("raw_headers", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    op.create_index(
        "ix_replies_tenant_received_at",
        "replies",
        ["tenant_id", "received_at"],
    )
    # Partial UNIQUE: protects against duplicate poll inserts in the face of
    # concurrent runs. NULL gmail_message_id is allowed (e.g. manual reply imports).
    op.create_index(
        "uq_replies_tenant_gmail_message_id",
        "replies",
        ["tenant_id", "gmail_message_id"],
        unique=True,
        postgresql_where="gmail_message_id IS NOT NULL",
    )

    # --- lead_states ---
    op.add_column(
        "lead_states",
        sa.Column("sequence_paused_until", sa.DateTime(timezone=True), nullable=True),
    )

    # --- leads ---
    op.add_column(
        "leads",
        sa.Column("notes", sa.Text(), nullable=True),
    )
    # source already exists from 0001 (String(64), nullable). Backfill defaults
    # and set server_default for future inserts.
    op.execute("UPDATE leads SET source = 'manual' WHERE source IS NULL")
    op.alter_column("leads", "source", server_default="manual")

    # --- accounts: dedup-friendly functional unique index ---
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_accounts_normalized_name "
        "ON accounts (tenant_id, lower(trim(name)))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_accounts_normalized_name")
    op.alter_column("leads", "source", server_default=None)
    op.drop_column("leads", "notes")
    op.drop_column("lead_states", "sequence_paused_until")
    op.drop_index("uq_replies_tenant_gmail_message_id", table_name="replies")
    op.drop_index("ix_replies_tenant_received_at", table_name="replies")
    op.drop_column("replies", "raw_headers")
    op.drop_column("replies", "subject")
    op.drop_column("replies", "sender_email")
    op.drop_column("replies", "gmail_message_id")
    op.drop_index("ix_touches_tenant_gmail_thread", table_name="touches")
    op.drop_column("touches", "gmail_thread_id")
