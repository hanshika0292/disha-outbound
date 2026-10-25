"""Initial schema: platform + per-tenant config + core business tables, with RLS.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from disha.db.models import metadata as target_metadata
from disha.db.rls import RLS_TABLES, disable_rls_sql, enable_rls_sql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    target_metadata.create_all(bind=bind)

    for table in RLS_TABLES:
        for stmt in enable_rls_sql(table):
            op.execute(stmt)


def downgrade() -> None:
    bind = op.get_bind()

    for table in RLS_TABLES:
        for stmt in disable_rls_sql(table):
            op.execute(stmt)

    target_metadata.drop_all(bind=bind)
