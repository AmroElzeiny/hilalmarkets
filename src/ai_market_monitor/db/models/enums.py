from enum import StrEnum


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class UserRole(StrEnum):
    USER = "user"
    SUPPORT = "support"
    ADMIN = "admin"


class IdentityProvider(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    DISCORD = "discord"


class ConnectionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"
    ERROR = "error"


class SubscriptionStatus(StrEnum):
    PENDING = "pending"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"


class TrialStatus(StrEnum):
    ELIGIBLE = "eligible"
    ACTIVATED = "activated"
    ACTIVE = "active"
    ENDING_SOON = "ending_soon"
    CONVERTED = "converted"
    EXPIRED = "expired"
    CANCELED = "canceled"
    BLOCKED = "blocked"
    MANUALLY_EXTENDED = "manually_extended"


class StrategyStatus(StrEnum):
    DRAFT = "draft"
    FORWARD_TEST = "forward_test"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class StrategyVersionStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_CLARIFICATION = "needs_clarification"
    APPROVED = "approved"
    PREVIEWING = "previewing"
    READY = "ready"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class MarketType(StrEnum):
    SPOT = "spot"


class TriggerMode(StrEnum):
    CANDLE_CLOSE = "candle_close"
    INTRABAR = "intrabar"


class LogicalOperator(StrEnum):
    AND = "and"
    OR = "or"
    NOT = "not"
    SEQUENCE = "sequence"
    WITHIN_LAST = "within_last"
    PERSISTED_FOR = "persisted_for"
    COUNT_OF = "count_of"
    COOLDOWN_CONDITION = "cooldown_condition"
    FIRST_TIME_TRUE = "first_time_true"
    CHANGED_STATE = "changed_state"
    CROSS_WITH_CONFIRMATION = "cross_with_confirmation"
    CONDITIONAL_BRANCH = "conditional_branch"


class ConditionType(StrEnum):
    INDICATOR = "indicator"
    PRICE_ACTION = "price_action"
    CANDLE_PATTERN = "candle_pattern"
    MARKET_FILTER = "market_filter"
    RISK = "risk"


class ScanJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELED = "canceled"


class ScanOutcome(StrEnum):
    CONFIRMED = "confirmed"
    FORMING = "forming"
    NEAR_MISS = "near_miss"
    INVALID = "invalid"
    SKIPPED = "skipped"
    EXPIRED = "expired"
    ERROR = "error"


class ConditionOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class SetupLifecycleState(StrEnum):
    CANDIDATE_DETECTED = "candidate_detected"
    DETECTED = "detected"
    FORMING = "forming"
    NEAR_CONFIRMATION = "near_confirmation"
    ARMED = "armed"
    CONFIRMED = "confirmed"
    ALERT_SENT = "alert_sent"
    SUPPRESSED = "suppressed"
    BLOCKED = "blocked"
    DATA_UNAVAILABLE = "data_unavailable"
    ENTRY_ACTIVE = "entry_active"
    ENTRY_ZONE_ACTIVE = "entry_zone_active"
    ENTRY_TOUCHED = "entry_touched"
    ENTRY_ZONE_MISSED = "entry_zone_missed"
    ENTRY_MISSED = "entry_missed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"
    TARGET_1_REACHED = "target_1_reached"
    TARGET_2_REACHED = "target_2_reached"
    TARGET_REACHED = "target_reached"
    STOP_REACHED = "stop_reached"
    STOP_LEVEL_REACHED = "stop_level_reached"
    MANUALLY_CLOSED = "manually_closed"
    COMPLETED = "completed"
    CLOSED = "closed"


TERMINAL_SETUP_STATES = {
    SetupLifecycleState.ENTRY_ZONE_MISSED,
    SetupLifecycleState.ENTRY_MISSED,
    SetupLifecycleState.INVALIDATED,
    SetupLifecycleState.EXPIRED,
    SetupLifecycleState.TARGET_REACHED,
    SetupLifecycleState.STOP_REACHED,
    SetupLifecycleState.STOP_LEVEL_REACHED,
    SetupLifecycleState.MANUALLY_CLOSED,
    SetupLifecycleState.COMPLETED,
    SetupLifecycleState.CLOSED,
}


ALLOWED_SETUP_TRANSITIONS: dict[SetupLifecycleState, set[SetupLifecycleState]] = {
    SetupLifecycleState.CANDIDATE_DETECTED: {
        SetupLifecycleState.FORMING,
        SetupLifecycleState.NEAR_CONFIRMATION,
        SetupLifecycleState.ARMED,
        SetupLifecycleState.CONFIRMED,
        SetupLifecycleState.BLOCKED,
        SetupLifecycleState.DATA_UNAVAILABLE,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
    },
    SetupLifecycleState.DETECTED: {
        SetupLifecycleState.FORMING,
        SetupLifecycleState.NEAR_CONFIRMATION,
        SetupLifecycleState.ARMED,
        SetupLifecycleState.CONFIRMED,
        SetupLifecycleState.BLOCKED,
        SetupLifecycleState.DATA_UNAVAILABLE,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
    },
    SetupLifecycleState.FORMING: {
        SetupLifecycleState.NEAR_CONFIRMATION,
        SetupLifecycleState.ARMED,
        SetupLifecycleState.CONFIRMED,
        SetupLifecycleState.BLOCKED,
        SetupLifecycleState.DATA_UNAVAILABLE,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
    },
    SetupLifecycleState.NEAR_CONFIRMATION: {
        SetupLifecycleState.FORMING,
        SetupLifecycleState.ARMED,
        SetupLifecycleState.CONFIRMED,
        SetupLifecycleState.BLOCKED,
        SetupLifecycleState.DATA_UNAVAILABLE,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
    },
    SetupLifecycleState.ARMED: {
        SetupLifecycleState.FORMING,
        SetupLifecycleState.CONFIRMED,
        SetupLifecycleState.BLOCKED,
        SetupLifecycleState.DATA_UNAVAILABLE,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
    },
    SetupLifecycleState.CONFIRMED: {
        SetupLifecycleState.ALERT_SENT,
        SetupLifecycleState.SUPPRESSED,
        SetupLifecycleState.ENTRY_ZONE_ACTIVE,
        SetupLifecycleState.ENTRY_ZONE_MISSED,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
        SetupLifecycleState.ENTRY_ACTIVE,
    },
    SetupLifecycleState.ALERT_SENT: {
        SetupLifecycleState.ENTRY_ZONE_ACTIVE,
        SetupLifecycleState.ENTRY_ACTIVE,
        SetupLifecycleState.ENTRY_ZONE_MISSED,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
    },
    SetupLifecycleState.SUPPRESSED: {
        SetupLifecycleState.ALERT_SENT,
        SetupLifecycleState.ENTRY_ZONE_ACTIVE,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
    },
    SetupLifecycleState.BLOCKED: {
        SetupLifecycleState.FORMING,
        SetupLifecycleState.NEAR_CONFIRMATION,
        SetupLifecycleState.ARMED,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
    },
    SetupLifecycleState.DATA_UNAVAILABLE: {
        SetupLifecycleState.FORMING,
        SetupLifecycleState.NEAR_CONFIRMATION,
        SetupLifecycleState.ARMED,
        SetupLifecycleState.BLOCKED,
        SetupLifecycleState.EXPIRED,
    },
    SetupLifecycleState.ENTRY_ACTIVE: {
        SetupLifecycleState.ENTRY_TOUCHED,
        SetupLifecycleState.ENTRY_MISSED,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
    },
    SetupLifecycleState.ENTRY_TOUCHED: {
        SetupLifecycleState.TARGET_1_REACHED,
        SetupLifecycleState.TARGET_REACHED,
        SetupLifecycleState.STOP_REACHED,
        SetupLifecycleState.MANUALLY_CLOSED,
        SetupLifecycleState.COMPLETED,
    },
    SetupLifecycleState.ENTRY_ZONE_ACTIVE: {
        SetupLifecycleState.ENTRY_ZONE_MISSED,
        SetupLifecycleState.ENTRY_TOUCHED,
        SetupLifecycleState.INVALIDATED,
        SetupLifecycleState.EXPIRED,
        SetupLifecycleState.TARGET_REACHED,
        SetupLifecycleState.STOP_LEVEL_REACHED,
        SetupLifecycleState.CLOSED,
    },
    SetupLifecycleState.TARGET_1_REACHED: {
        SetupLifecycleState.TARGET_2_REACHED,
        SetupLifecycleState.TARGET_REACHED,
        SetupLifecycleState.STOP_REACHED,
        SetupLifecycleState.STOP_LEVEL_REACHED,
        SetupLifecycleState.MANUALLY_CLOSED,
        SetupLifecycleState.COMPLETED,
        SetupLifecycleState.CLOSED,
    },
    SetupLifecycleState.TARGET_2_REACHED: {
        SetupLifecycleState.TARGET_REACHED,
        SetupLifecycleState.STOP_REACHED,
        SetupLifecycleState.STOP_LEVEL_REACHED,
        SetupLifecycleState.MANUALLY_CLOSED,
        SetupLifecycleState.COMPLETED,
        SetupLifecycleState.CLOSED,
    },
}


class AlertType(StrEnum):
    FORMING = "forming"
    NEAR_MISS = "near_miss"
    CONFIRMED = "confirmed"
    LIFECYCLE = "lifecycle"
    FAILURE = "failure"
    TRIAL = "trial"


class DeliveryChannel(StrEnum):
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WEB = "web"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_PERMANENT = "failed_permanent"
    SUPPRESSED = "suppressed"
    CANCELED = "cancelled"


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class OnboardingStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class OnboardingStep(StrEnum):
    INTRODUCTION = "introduction"
    ACCOUNT = "account"
    DISCLAIMER = "disclaimer"
    TRIAL = "trial"
    GUIDED_SETUP = "guided_setup"
    INTERPRETATION = "interpretation"
    APPROVAL = "approval"
    VALIDATION = "validation"
    ACTIVATION = "activation"
    COMPLETE = "complete"


class SupportRequestStatus(StrEnum):
    OPEN = "open"
    PENDING_USER = "pending_user"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATING = "mitigating"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    CANCELED = "cancelled"


class IncidentSeverity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"
