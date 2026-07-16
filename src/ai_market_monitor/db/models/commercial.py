from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from ai_market_monitor.db.models.enums import SubscriptionStatus, TrialStatus


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    price_monthly: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    max_active_strategies: Mapped[int] = mapped_column(Integer, nullable=False)
    max_symbols_per_strategy: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_scan_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    discord_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    backtest_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subscription_id", name="uq_provider_subscription"),
        Index("ix_subscription_user_status", "user_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        enum_type(SubscriptionStatus, name="subscription_status"), nullable=False
    )
    provider: Mapped[str | None] = mapped_column(String(40))
    provider_customer_id: Mapped[str | None] = mapped_column(String(255), index=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(255))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Trial(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trials"
    __table_args__ = (Index("ix_trial_user_status", "user_id", "status"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[TrialStatus] = mapped_column(
        enum_type(TrialStatus, name="trial_status"), default=TrialStatus.ACTIVE, nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    converted_subscription_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL")
    )
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrialCycle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trial_cycles"
    __table_args__ = (
        UniqueConstraint("trial_id", "cycle_number", name="uq_trial_cycle_number"),
        Index("ix_trial_cycle_status_ends", "status", "ends_at"),
        Index("ix_trial_cycle_trial_status", "trial_id", "status"),
    )

    trial_id: Mapped[UUID] = mapped_column(
        ForeignKey("trials.id", ondelete="CASCADE"), nullable=False
    )
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    qualifying_alerts_generated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    qualifying_alerts_delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    suppressed_alert_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_monitor_duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notification_ready_duration_seconds: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    successful_scan_coverage: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    renewal_decision: Mapped[str | None] = mapped_column(String(64))
    renewal_reason: Mapped[str | None] = mapped_column(String(80))
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrialAlertAttribution(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "trial_alert_attributions"
    __table_args__ = (
        UniqueConstraint("trial_cycle_id", "alert_id", name="uq_trial_cycle_alert"),
        Index("ix_trial_alert_status", "qualification_status", "attributed_at"),
    )

    trial_cycle_id: Mapped[UUID] = mapped_column(
        ForeignKey("trial_cycles.id", ondelete="CASCADE"), nullable=False
    )
    alert_id: Mapped[UUID] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )
    first_successful_delivery_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("alert_deliveries.id", ondelete="SET NULL")
    )
    qualification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    qualification_reason: Mapped[str] = mapped_column(String(120), nullable=False)
    attributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "billing_events"
    __table_args__ = (Index("ix_billing_user_created", "user_id", "created_at"),)

    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingCheckoutAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_checkout_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_billing_checkout_idempotency"),
        Index("ix_billing_checkout_user_status", "user_id", "status", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    billing_cycle: Mapped[str] = mapped_column(String(20), default="monthly", nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_session_id: Mapped[str | None] = mapped_column(String(255), index=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(255), index=True)
    checkout_url: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(32), default="creating", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    terms_version: Mapped[str] = mapped_column(String(80), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))


class PaymentEmailDelivery(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payment_email_deliveries"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_payment_email_event_key"),
        Index("ix_payment_email_due", "status", "next_retry_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    billing_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("billing_events.id", ondelete="CASCADE"), nullable=False
    )
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(50), nullable=False)
    billing_frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    renewal_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_url: Mapped[str | None] = mapped_column(String(2000))
    plan_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntitlementSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "entitlement_snapshots"
    __table_args__ = (
        Index("ix_entitlement_user_status", "user_id", "status"),
        Index("ix_entitlement_user_period", "user_id", "starts_at", "ends_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    plan_code: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UsageRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_usage_idempotency_key"),
        Index("ix_usage_user_metric_period", "user_id", "metric", "period_start"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(60))
    subject_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReferralRelationship(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "referral_relationships"
    __table_args__ = (
        UniqueConstraint("referred_user_id", name="uq_referral_referred_user"),
        Index("ix_referral_referrer_status", "referrer_user_id", "status"),
    )

    referrer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    referred_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    referral_code_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("referral_codes.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), default="trial_activated", nullable=False)
    reward_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    reward_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class AdminOverride(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "admin_overrides"
    __table_args__ = (
        Index("ix_admin_override_target", "target_user_id", "override_type"),
        Index("ix_admin_override_admin_created", "admin_user_id", "created_at"),
    )

    admin_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    target_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    override_type: Mapped[str] = mapped_column(String(60), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
