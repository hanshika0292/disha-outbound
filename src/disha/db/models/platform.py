from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, LargeBinary, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from disha.db.models.base import Base, TimestampMixin, uuid_pk
from disha.db.models.enums import MembershipRole, Plan, SuppressionType, TenantStatus, enum_values


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    plan: Mapped[Plan] = mapped_column(
        SAEnum(Plan, native_enum=False, length=32, name="plan", values_callable=enum_values),
        nullable=False,
        default=Plan.STARTER,
    )
    status: Mapped[TenantStatus] = mapped_column(
        SAEnum(
            TenantStatus,
            native_enum=False,
            length=32,
            name="tenant_status",
            values_callable=enum_values,
        ),
        nullable=False,
        default=TenantStatus.TRIAL,
    )
    email_hash_salt: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiKey(Base):
    """API keys for HTTP auth. INTENTIONALLY NO RLS.

    Auth lookups happen before tenant context is established, so this table
    is only ever read by `disha_admin` (BYPASSRLS). Never add to RLS_TABLES.
    Plaintext is shown once at provisioning; we store sha256(plaintext).
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        Index(
            "ix_api_keys_prefix_active",
            "key_prefix",
            postgresql_where="active",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="default", default="default"
    )
    plan: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="starter", default="starter"
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # scrypt: b"salt(16)||hash(64)". Nullable so pre-auth-era rows stay valid.
    password_hash: Mapped[bytes | None] = mapped_column(LargeBinary(96), nullable=True)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    # True for admin-provisioned accounts → forces a password reset at first login.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WaitlistEntry(Base):
    """Public interest signups from the landing page. Platform table, no RLS."""

    __tablename__ = "waitlist_entries"

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Qualification answers from the signup form (stage, acquisition channel,
    # pipeline goal, biggest challenge, …) — flexible JSONB so the form can
    # evolve without migrations.
    answers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Set when an admin provisions an account from this entry.
    invited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[MembershipRole] = mapped_column(
        SAEnum(
            MembershipRole,
            native_enum=False,
            length=32,
            name="membership_role",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlatformSuppression(Base):
    __tablename__ = "platform_suppressions"

    id: Mapped[UUID] = uuid_pk()
    value_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    value_type: Mapped[SuppressionType] = mapped_column(
        SAEnum(
            SuppressionType,
            native_enum=False,
            length=16,
            name="suppression_type",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
