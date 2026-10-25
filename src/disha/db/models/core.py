from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from disha.db.models.base import Base, TimestampMixin, uuid_pk
from disha.db.models.enums import (
    Channel,
    Direction,
    ExperimentEntityType,
    ExperimentStatus,
    LeadState,
    OpportunityStage,
    Tier,
    enum_values,
)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (Index("ix_accounts_tenant_domain", "tenant_id", "domain", unique=True),)

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(200), nullable=True)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    funding_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hq_country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    hq_city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AccountAlias(Base):
    __tablename__ = "account_aliases"
    __table_args__ = (
        Index(
            "uq_account_aliases_tenant_alias",
            "tenant_id",
            "alias_domain",
            unique=True,
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    alias_domain: Mapped[str] = mapped_column(String(255), nullable=False)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index(
            "uq_leads_tenant_email_hash",
            "tenant_id",
            "email_hash",
            unique=True,
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    account_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )
    email_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    email_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    seniority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True, server_default="manual")
    source_lead_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notion_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LeadStateRow(Base):
    __tablename__ = "lead_states"

    lead_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("leads.id"), primary_key=True
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    state: Mapped[LeadState] = mapped_column(
        SAEnum(
            LeadState,
            native_enum=False,
            length=32,
            name="lead_state",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    tier: Mapped[Tier | None] = mapped_column(
        SAEnum(Tier, native_enum=False, length=8, name="tier", values_callable=enum_values),
        nullable=True,
    )
    scored_with_prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequence_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sequence_paused_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    geo_assigned: Mapped[str | None] = mapped_column(String(8), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_tenant_observed_at", "tenant_id", "observed_at"),
        Index("ix_signals_tenant_account", "tenant_id", "account_id"),
        Index("ix_signals_tenant_lead", "tenant_id", "lead_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    account_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )
    lead_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("leads.id"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (Index("ix_threads_tenant_lead", "tenant_id", "lead_id"),)

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    lead_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(
        SAEnum(Channel, native_enum=False, length=16, name="channel", values_callable=enum_values),
        nullable=False,
    )
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Touch(Base):
    __tablename__ = "touches"
    __table_args__ = (
        Index("ix_touches_tenant_thread", "tenant_id", "thread_id"),
        Index("ix_touches_tenant_thread_step", "tenant_id", "thread_id", "step_number"),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    thread_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("threads.id"), nullable=False
    )
    step_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    direction: Mapped[Direction] = mapped_column(
        SAEnum(
            Direction,
            native_enum=False,
            length=16,
            name="direction",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    channel: Mapped[Channel] = mapped_column(
        SAEnum(Channel, native_enum=False, length=16, name="channel", values_callable=enum_values),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft", default="draft"
    )
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    drafted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gmail_message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    experiment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=True
    )
    variant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ManualAction(Base):
    __tablename__ = "manual_actions"
    __table_args__ = (
        Index(
            "ix_manual_actions_tenant_status_created",
            "tenant_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    touch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("touches.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending", default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Reply(Base):
    __tablename__ = "replies"
    __table_args__ = (
        Index("ix_replies_tenant_touch", "tenant_id", "touch_id"),
        Index("ix_replies_tenant_received_at", "tenant_id", "received_at"),
        Index(
            "uq_replies_tenant_gmail_message_id",
            "tenant_id",
            "gmail_message_id",
            unique=True,
            postgresql_where="gmail_message_id IS NOT NULL",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    touch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("touches.id"), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    gmail_message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Event(Base):
    """Append-only audit log. `id` is a ULID stored as a 26-char string."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_tenant_entity", "tenant_id", "entity_type", "entity_id"),
        Index("ix_events_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Opportunity(Base, TimestampMixin):
    __tablename__ = "opportunities"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    lead_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False
    )
    stage: Mapped[OpportunityStage] = mapped_column(
        SAEnum(
            OpportunityStage,
            native_enum=False,
            length=32,
            name="opportunity_stage",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    value_estimate_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[ExperimentStatus] = mapped_column(
        SAEnum(
            ExperimentStatus,
            native_enum=False,
            length=16,
            name="experiment_status",
            values_callable=enum_values,
        ),
        nullable=False,
        default=ExperimentStatus.RUNNING,
    )


class ExperimentAssignment(Base):
    __tablename__ = "experiment_assignments"
    __table_args__ = (
        Index(
            "ix_experiment_assignments_tenant_experiment",
            "tenant_id",
            "experiment_id",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    experiment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False
    )
    entity_type: Mapped[ExperimentEntityType] = mapped_column(
        SAEnum(
            ExperimentEntityType,
            native_enum=False,
            length=16,
            name="experiment_entity_type",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    entity_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    variant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
