from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, LargeBinary, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from disha.db.models.base import Base, TimestampMixin, uuid_pk
from disha.db.models.enums import CredentialStatus, enum_values


class _VersionedConfigMixin(TimestampMixin):
    """Common shape: versioned JSONB blob with an `is_active` flag."""

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class IcpRubric(Base, _VersionedConfigMixin):
    __tablename__ = "icp_rubrics"
    __table_args__ = (
        Index(
            "uq_icp_rubrics_tenant_active",
            "tenant_id",
            unique=True,
            postgresql_where="is_active",
        ),
    )


class PromptPack(Base, _VersionedConfigMixin):
    __tablename__ = "prompt_packs"
    __table_args__ = (
        Index(
            "uq_prompt_packs_tenant_active",
            "tenant_id",
            unique=True,
            postgresql_where="is_active",
        ),
    )


class VoiceGuide(Base, _VersionedConfigMixin):
    __tablename__ = "voice_guides"
    __table_args__ = (
        Index(
            "uq_voice_guides_tenant_active",
            "tenant_id",
            unique=True,
            postgresql_where="is_active",
        ),
    )


class GeoProfile(Base, _VersionedConfigMixin):
    __tablename__ = "geo_profiles"
    __table_args__ = (
        Index(
            "uq_geo_profiles_tenant_active",
            "tenant_id",
            unique=True,
            postgresql_where="is_active",
        ),
    )


class Cadence(Base, _VersionedConfigMixin):
    """Multiple active cadences allowed per tenant (e.g. one per region)."""

    __tablename__ = "cadences"
    __table_args__ = (Index("ix_cadences_tenant_active", "tenant_id", "is_active"),)


class TenantSettings(Base, TimestampMixin):
    __tablename__ = "tenant_settings"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), unique=True, nullable=False
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Credential(Base, TimestampMixin):
    __tablename__ = "credentials"
    __table_args__ = (
        Index(
            "uq_credentials_tenant_provider_active",
            "tenant_id",
            "provider",
            unique=True,
            postgresql_where="status = 'active'",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[CredentialStatus] = mapped_column(
        SAEnum(
            CredentialStatus,
            native_enum=False,
            length=16,
            name="credential_status",
            values_callable=enum_values,
        ),
        nullable=False,
        default=CredentialStatus.ACTIVE,
    )
