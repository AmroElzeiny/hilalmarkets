import hashlib
import json
from datetime import UTC, datetime
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
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConditionOutcome,
    DeliveryChannel,
    DeliveryStatus,
    HealthStatus,
    ScanJobStatus,
    ScanOutcome,
    SetupLifecycleState,
)


class ScanJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scan_jobs"
    __table_args__ = (
        Index("ix_scan_job_status_scheduled", "status", "scheduled_for"),
        Index("ix_scan_job_version_created", "strategy_version_id", "created_at"),
        Index("ix_scan_job_status_retry", "status", "next_retry_at"),
        Index("ix_scan_job_worker_heartbeat", "worker_id", "heartbeat_at"),
    )

    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    job_type: Mapped[str] = mapped_column(String(32), default="live", nullable=False)
    status: Mapped[ScanJobStatus] = mapped_column(
        enum_type(ScanJobStatus, name="scan_job_status"), nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(120))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    symbols_planned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matches_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ScanResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "scan_results"
    __table_args__ = (
        UniqueConstraint(
            "scan_job_id",
            "exchange",
            "symbol",
            "timeframe",
            "direction",
            name="uq_scan_result_market",
        ),
        Index("ix_scan_result_version_candle", "strategy_version_id", "candle_closed_at"),
        Index("ix_scan_result_outcome_score", "outcome", "completion_score"),
    )

    scan_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_jobs.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), default="long", nullable=False)
    outcome: Mapped[ScanOutcome] = mapped_column(
        enum_type(ScanOutcome, name="scan_outcome"), nullable=False
    )
    completion_score: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    candle_closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_freshness_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    is_candle_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(160))
    error_code: Mapped[str | None] = mapped_column(String(80))
    proof_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sharia_methodology_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="SET NULL")
    )
    sharia_methodology_version: Mapped[str | None] = mapped_column(String(32))
    sharia_status_at_scan: Mapped[str | None] = mapped_column(String(40))
    sharia_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_sharia_assessments.id", ondelete="SET NULL")
    )
    sharia_passport_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("published_asset_assessments.id", ondelete="SET NULL")
    )
    sharia_universe_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_universe_snapshots.id", ondelete="SET NULL")
    )
    sharia_policy_decision: Mapped[str | None] = mapped_column(String(60))
    sharia_policy_reason: Mapped[str | None] = mapped_column(String(300))


class ConditionRuntimeState(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "condition_runtime_states"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id",
            "exchange",
            "symbol",
            "timeframe",
            "direction",
            "condition_key",
            name="uq_condition_runtime_market_key",
        ),
        Index(
            "ix_condition_runtime_version_market",
            "strategy_version_id",
            "exchange",
            "symbol",
            "timeframe",
        ),
    )

    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), default="long", nullable=False)
    condition_key: Mapped[str] = mapped_column(String(100), nullable=False)
    last_outcome: Mapped[ConditionOutcome] = mapped_column(
        enum_type(ConditionOutcome, name="condition_runtime_outcome"),
        nullable=False,
    )
    first_true_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_true_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consecutive_true_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actual_value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SetupInstance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "setup_instances"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id",
            "exchange",
            "symbol",
            "timeframe",
            "setup_key",
            name="uq_setup_instance_key",
        ),
        Index("ix_setup_strategy_state", "strategy_version_id", "state"),
        Index("ix_setup_user_market", "user_id", "exchange", "symbol"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    latest_scan_result_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scan_results.id", ondelete="SET NULL")
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), default="long", nullable=False)
    setup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    state: Mapped[SetupLifecycleState] = mapped_column(
        enum_type(SetupLifecycleState, name="setup_lifecycle_state"), nullable=False
    )
    completion_score: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_zone_low: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    entry_zone_high: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    target_levels: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    targets_reached: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lifecycle_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(160))
    sharia_methodology_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="SET NULL")
    )
    sharia_methodology_version: Mapped[str | None] = mapped_column(String(32))
    sharia_status_at_detection: Mapped[str | None] = mapped_column(String(40))
    sharia_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_sharia_assessments.id", ondelete="SET NULL")
    )
    sharia_passport_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("published_asset_assessments.id", ondelete="SET NULL")
    )
    sharia_universe_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_universe_snapshots.id", ondelete="SET NULL")
    )
    sharia_policy_decision: Mapped[str | None] = mapped_column(String(60))

    __mapper_args__ = {"version_id_col": lifecycle_version}

    condition_results: Mapped[list["SetupConditionResult"]] = relationship(
        back_populates="setup_instance", cascade="all, delete-orphan"
    )
    lifecycle_events: Mapped[list["SetupLifecycleEvent"]] = relationship(
        back_populates="setup_instance", cascade="all, delete-orphan"
    )


class SetupLifecycleEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "setup_lifecycle_events"
    __table_args__ = (Index("ix_lifecycle_setup_occurred", "setup_instance_id", "occurred_at"),)

    setup_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[SetupLifecycleState | None] = mapped_column(
        enum_type(SetupLifecycleState, name="setup_from_state")
    )
    to_state: Mapped[SetupLifecycleState] = mapped_column(
        enum_type(SetupLifecycleState, name="setup_to_state"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    setup_instance: Mapped[SetupInstance] = relationship(back_populates="lifecycle_events")


class SetupConditionResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "setup_condition_results"
    __table_args__ = (
        UniqueConstraint(
            "setup_instance_id", "scan_result_id", "condition_key", name="uq_setup_condition_proof"
        ),
        Index("ix_condition_result_setup_evaluated", "setup_instance_id", "evaluated_at"),
    )

    setup_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="CASCADE"), nullable=False
    )
    scan_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_results.id", ondelete="CASCADE"), nullable=False
    )
    strategy_condition_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_conditions.id", ondelete="RESTRICT"), nullable=False
    )
    condition_key: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[ConditionOutcome] = mapped_column(
        enum_type(ConditionOutcome, name="condition_outcome"), nullable=False
    )
    required_value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    actual_value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    distance_to_pass: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    contribution_score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    candle_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_freshness_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation_code: Mapped[str | None] = mapped_column(String(100))

    setup_instance: Mapped[SetupInstance] = relationship(back_populates="condition_results")


class NearMissSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "near_miss_snapshots"
    __table_args__ = (
        UniqueConstraint("scan_result_id", name="uq_near_miss_scan_result"),
        Index("ix_near_miss_version_score", "strategy_version_id", "completion_score"),
        Index("ix_near_miss_market_captured", "exchange", "symbol", "captured_at"),
    )

    scan_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_results.id", ondelete="CASCADE"), nullable=False
    )
    setup_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="CASCADE")
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    completion_score: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    previous_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    trend: Mapped[str] = mapped_column(String(16), nullable=False)
    passed_condition_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    missing_conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Alert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("deduplication_key", name="uq_alert_deduplication_key"),
        Index("ix_alert_user_created", "user_id", "created_at"),
        Index("ix_alert_setup_type", "setup_instance_id", "alert_type"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=True
    )
    setup_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="SET NULL")
    )
    alert_type: Mapped[AlertType] = mapped_column(
        enum_type(AlertType, name="alert_type"), nullable=False
    )
    deduplication_key: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    proof_receipt: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    proof_hash: Mapped[str | None] = mapped_column(String(64))
    proof_schema_version: Mapped[str] = mapped_column(
        String(20), default="1.0", nullable=False
    )
    proof_sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chart_snapshot_url: Mapped[str | None] = mapped_column(String(1000))
    candle_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suppressed_reason: Mapped[str | None] = mapped_column(String(160))
    sharia_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_sharia_assessments.id", ondelete="SET NULL")
    )
    sharia_passport_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("published_asset_assessments.id", ondelete="SET NULL")
    )
    sharia_methodology_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="SET NULL")
    )
    sharia_universe_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_universe_snapshots.id", ondelete="SET NULL")
    )
    sharia_policy_decision: Mapped[str | None] = mapped_column(String(60))


def canonical_alert_proof_hash(proof_receipt: dict[str, Any]) -> str:
    encoded = json.dumps(
        proof_receipt,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@event.listens_for(Alert, "before_insert")
def _seal_alert_proof_before_insert(_mapper: Any, _connection: Any, alert: Alert) -> None:
    digest = canonical_alert_proof_hash(alert.proof_receipt or {})
    if alert.proof_hash and alert.proof_hash != digest:
        raise ValueError("Alert proof does not match its integrity hash.")
    alert.proof_hash = digest
    alert.proof_schema_version = alert.proof_schema_version or "1.0"
    alert.proof_sealed_at = alert.proof_sealed_at or datetime.now(UTC)


@event.listens_for(Alert, "before_update")
def _verify_alert_proof_before_update(_mapper: Any, _connection: Any, alert: Alert) -> None:
    digest = canonical_alert_proof_hash(alert.proof_receipt or {})
    if alert.proof_hash and alert.proof_hash != digest:
        raise ValueError("Immutable alert proof was modified after it was sealed.")
    # Seal legacy rows the first time they are safely updated.
    alert.proof_hash = alert.proof_hash or digest
    alert.proof_schema_version = alert.proof_schema_version or "1.0"
    alert.proof_sealed_at = alert.proof_sealed_at or datetime.now(UTC)


class AlertDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alert_deliveries"
    __table_args__ = (
        UniqueConstraint("alert_id", "channel", "destination_key", name="uq_alert_destination"),
        Index("ix_delivery_status_retry", "status", "next_retry_at"),
    )

    alert_id: Mapped[UUID] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[DeliveryChannel] = mapped_column(
        enum_type(DeliveryChannel, name="delivery_channel"), nullable=False
    )
    destination_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(
        enum_type(DeliveryStatus, name="delivery_status"), nullable=False
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_error_detail: Mapped[str | None] = mapped_column(String(500))


class MarketDataHealth(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "market_data_health"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "exchange",
            "symbol",
            "timeframe",
            name="uq_market_health_scope",
        ),
        Index("ix_market_health_status_checked", "status", "checked_at"),
        Index("ix_market_health_market_checked", "exchange", "symbol", "timeframe", "checked_at"),
    )

    provider: Mapped[str] = mapped_column(String(40), default="ccxt", nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), default="*", nullable=False)
    market_type: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[HealthStatus] = mapped_column(
        enum_type(HealthStatus, name="market_health_status"), nullable=False
    )
    last_candle_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_age_seconds: Mapped[int | None] = mapped_column(Integer)
    candle_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lag_seconds: Mapped[int | None] = mapped_column(Integer)
    missing_candle_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_candle_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    out_of_order_candle_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_status: Mapped[str] = mapped_column(String(40), default="unknown", nullable=False)
    error_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntegrationHealth(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "integration_health"
    __table_args__ = (
        UniqueConstraint("integration", "scope_key", name="uq_integration_health_scope"),
        Index("ix_integration_health_status_checked", "status", "checked_at"),
    )

    integration: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[HealthStatus] = mapped_column(
        enum_type(HealthStatus, name="integration_health_status"), nullable=False
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OperationalMetric(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "operational_metrics"
    __table_args__ = (
        Index("ix_operational_metric_component", "component", "metric_name", "measured_at"),
        Index("ix_operational_metric_status", "status", "measured_at"),
    )

    component: Mapped[str] = mapped_column(String(60), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[HealthStatus] = mapped_column(
        enum_type(HealthStatus, name="operational_metric_status"), nullable=False
    )
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(String(30))
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
