from enum import StrEnum


def enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Helper for SAEnum's values_callable. Returns lowercase .value list.

    Without this, SAEnum(native_enum=False) stores the Python attribute name
    (uppercase) instead of the intended .value. Use as:
        SAEnum(MyEnum, native_enum=False, values_callable=enum_values)
    """
    return [e.value for e in enum_cls]


class Plan(StrEnum):
    STARTER = "starter"
    GROWTH = "growth"
    PRO = "pro"
    SCALE = "scale"
    AGENCY = "agency"
    ENTERPRISE = "enterprise"


class TenantStatus(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAUSED = "paused"
    CHURNED = "churned"


class MembershipRole(StrEnum):
    OWNER = "owner"
    OPERATOR = "operator"
    VIEWER = "viewer"


class SuppressionType(StrEnum):
    EMAIL = "email"
    DOMAIN = "domain"


class CredentialStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class LeadState(StrEnum):
    SOURCED = "sourced"
    ENRICHED = "enriched"
    SCORED = "scored"
    DRAFTED = "drafted"
    SKIPPED = "skipped"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_SEQUENCE = "in_sequence"
    SENT = "sent"
    REPLIED = "replied"
    BOOKED = "booked"
    DISCOVERY_HELD = "discovery_held"
    PROPOSAL_SENT = "proposal_sent"
    WON = "won"
    LOST = "lost"
    NURTURE = "nurture"
    STALE = "stale"
    CLOSED = "closed"
    UNSUBSCRIBED = "unsubscribed"


class TouchStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class ManualActionType(StrEnum):
    LINKEDIN_CONNECT = "linkedin_connect"
    LINKEDIN_MESSAGE = "linkedin_message"


class ManualActionStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"


class Tier(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    SKIP = "skip"


class Channel(StrEnum):
    EMAIL = "email"
    LINKEDIN = "linkedin"
    CALENDAR = "calendar"


class Direction(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class OpportunityStage(StrEnum):
    DISCOVERY = "discovery"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


class ExperimentStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    CONCLUDED = "concluded"


class ExperimentEntityType(StrEnum):
    LEAD = "lead"
    ACCOUNT = "account"
