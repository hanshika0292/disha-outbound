"""Week 4 cockpit prep: enum data normalization + new states + leads.notion_page_id.

Revision ID: 0004_cockpit
Revises: 0003_engagement
Create Date: 2026-05-27

Carries over Week 1/2/3 SAEnum bug: every enum-backed VARCHAR stored Python
attribute names (uppercase like "SCORED") instead of the intended .value
(lowercase like "scored"). Models were updated in the same change to add
values_callable=enum_values. THIS migration is what makes that switch safe:

1. Lowercase every existing enum-typed cell (idempotent: only updates where
   value != LOWER(value)).
2. Add CHECK constraints with the full lowercase value sets, so any future
   bypass of the ORM is rejected at the DB boundary.
3. Add Week 4 schema bits: leads.notion_page_id + partial index, plus widened
   touches.status CHECK to include 'pending_approval'.

Data normalization is NOT reversible — downgrade only undoes structural changes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_cockpit"
down_revision: str | None = "0003_engagement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Full enum value sets (must match enums.py exactly).
_LEAD_STATES = (
    "sourced",
    "enriched",
    "scored",
    "drafted",
    "skipped",
    "pending_review",
    "approved",
    "rejected",
    "in_sequence",
    "sent",
    "replied",
    "booked",
    "discovery_held",
    "proposal_sent",
    "won",
    "lost",
    "nurture",
    "stale",
    "closed",
    "unsubscribed",
)
_TOUCH_STATUSES = ("draft", "pending_approval", "queued", "sent", "failed", "skipped")
_TIERS = ("A", "B", "C", "skip")
_PLANS = ("starter", "pro", "agency", "enterprise")
_TENANT_STATUSES = ("trial", "active", "paused", "churned")
_MEMBERSHIP_ROLES = ("owner", "operator", "viewer")
_SUPPRESSION_TYPES = ("email", "domain")
_CREDENTIAL_STATUSES = ("active", "expired", "revoked")
_CHANNELS = ("email", "linkedin", "calendar")
_DIRECTIONS = ("outbound", "inbound")
_OPPORTUNITY_STAGES = ("discovery", "proposal", "negotiation", "won", "lost")
_EXPERIMENT_STATUSES = ("running", "paused", "concluded")
_EXPERIMENT_ENTITY_TYPES = ("lead", "account")


# (table, column) pairs that need a LOWER() backfill. Note: tier values
# A/B/C are uppercase in the enum AND lowercase forms match nothing — skip
# columns where lowercasing would corrupt valid data. Tier's `skip` member
# already lowercases cleanly because A/B/C lowered to a/b/c is the same as
# their original .value (no — they aren't). Special-case below.
_LOWERCASE_BACKFILL = [
    ("tenants", "plan"),
    ("tenants", "status"),
    ("tenant_memberships", "role"),
    ("platform_suppressions", "value_type"),
    ("credentials", "status"),
    ("lead_states", "state"),
    ("threads", "channel"),
    ("touches", "direction"),
    ("touches", "channel"),
    ("opportunities", "stage"),
    ("experiments", "status"),
    ("experiment_assignments", "entity_type"),
]

# Tier needs a custom mapping (A/B/C stay uppercase as .value, only SKIP → skip).
# Existing data may have "SKIP" or already "skip". Normalize.
_TIER_FIXUPS = [("SKIP", "skip")]


def _check_in(col: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({rendered})"


def upgrade() -> None:
    # --- 1. Data normalization (idempotent) ---
    for table, column in _LOWERCASE_BACKFILL:
        op.execute(
            f"UPDATE {table} SET {column} = LOWER({column}) "
            f"WHERE {column} IS NOT NULL AND {column} != LOWER({column})"
        )

    # Tier special-case
    for old, new in _TIER_FIXUPS:
        op.execute(f"UPDATE lead_states SET tier = '{new}' WHERE tier = '{old}'")

    # --- 2. Drop existing constraints we're about to widen, then re-add ---
    # touches.status (added in 0003 with only 5 values; widen to 6).
    # NOTE: pass the unprefixed label — the naming_convention adds `ck_<table>_`.
    op.drop_constraint("status", "touches", type_="check")
    op.create_check_constraint(
        "status",  # naming_convention prepends ck_touches_
        "touches",
        _check_in("status", _TOUCH_STATUSES),
    )

    # lead_states.state (deferred from 0003 due to existing uppercase data;
    # safe now after the backfill above)
    op.create_check_constraint(
        "state",
        "lead_states",
        _check_in("state", _LEAD_STATES),
    )

    # Defense-in-depth CHECKs for the other enum-backed columns. Future bypass
    # of the ORM (raw SQL, replication) is now rejected.
    op.create_check_constraint("tier", "lead_states", _check_in("tier", _TIERS))
    op.create_check_constraint("plan", "tenants", _check_in("plan", _PLANS))
    op.create_check_constraint("status", "tenants", _check_in("status", _TENANT_STATUSES))
    op.create_check_constraint("role", "tenant_memberships", _check_in("role", _MEMBERSHIP_ROLES))
    op.create_check_constraint(
        "value_type",
        "platform_suppressions",
        _check_in("value_type", _SUPPRESSION_TYPES),
    )
    op.create_check_constraint("status", "credentials", _check_in("status", _CREDENTIAL_STATUSES))
    op.create_check_constraint("channel", "threads", _check_in("channel", _CHANNELS))
    op.create_check_constraint("channel", "touches", _check_in("channel", _CHANNELS))
    op.create_check_constraint("direction", "touches", _check_in("direction", _DIRECTIONS))
    op.create_check_constraint("stage", "opportunities", _check_in("stage", _OPPORTUNITY_STAGES))
    op.create_check_constraint("status", "experiments", _check_in("status", _EXPERIMENT_STATUSES))
    op.create_check_constraint(
        "entity_type",
        "experiment_assignments",
        _check_in("entity_type", _EXPERIMENT_ENTITY_TYPES),
    )

    # --- 3. New schema bits ---
    op.add_column(
        "leads",
        sa.Column("notion_page_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_leads_notion_page_id",
        "leads",
        ["notion_page_id"],
        postgresql_where="notion_page_id IS NOT NULL",
    )


def downgrade() -> None:
    # Structural reverse only. Data normalization (uppercase → lowercase) is
    # NOT reversed because the original .name → .value mapping isn't bijective
    # for Tier.SKIP and there's no reason to put uppercase back.
    op.drop_index("ix_leads_notion_page_id", table_name="leads")
    op.drop_column("leads", "notion_page_id")

    for table, col_label in [
        ("experiment_assignments", "entity_type"),
        ("experiments", "status"),
        ("opportunities", "stage"),
        ("touches", "direction"),
        ("touches", "channel"),
        ("threads", "channel"),
        ("credentials", "status"),
        ("platform_suppressions", "value_type"),
        ("tenant_memberships", "role"),
        ("tenants", "status"),
        ("tenants", "plan"),
        ("lead_states", "tier"),
        ("lead_states", "state"),
    ]:
        op.drop_constraint(col_label, table, type_="check")

    op.drop_constraint("status", "touches", type_="check")
    op.create_check_constraint(
        "status",
        "touches",
        _check_in("status", ("draft", "queued", "sent", "failed", "skipped")),
    )
