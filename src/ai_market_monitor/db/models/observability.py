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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MonitorEvaluationCycle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "monitor_evaluation_cycles"
    __table_args__ = (
        UniqueConstraint("scan_job_id", name="uq_monitor_cycle_scan_job"),
        Index("ix_monitor_cycle_user_started", "user_id", "started_at"),
        Index("ix_monitor_cycle_strategy_started", "strategy_id", "started_at"),
        Index("ix_monitor_cycle_status_heartbeat", "status", "heartbeat_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    scan_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_jobs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_expected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    symbols_expected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stale_candles: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_candles: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rate_limit_incidents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delayed_evaluations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scanner_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class CandidateReadinessSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "candidate_readiness_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id",
            "exchange",
            "symbol",
            "timeframe",
            "direction",
            name="uq_candidate_readiness_market",
        ),
        Index("ix_candidate_readiness_user_state", "user_id", "lifecycle_state"),
        Index("ix_candidate_readiness_strategy_updated", "strategy_id", "updated_at"),
        Index("ix_candidate_readiness_rank_updated", "stage_rank", "updated_at"),
        Index("ix_candidate_readiness_data_health", "data_health", "last_evaluated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    setup_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="SET NULL")
    )
    scan_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_results.id", ondelete="CASCADE"), nullable=False
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(40), nullable=False)
    stage_rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    required_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    required_passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    optional_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    optional_passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocker_key: Mapped[str | None] = mapped_column(String(100))
    blocker_label: Mapped[str | None] = mapped_column(String(240))
    blocker_outcome: Mapped[str | None] = mapped_column(String(32))
    blocker_actual: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    blocker_required: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    blocker_distance: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    blocker_unit: Mapped[str | None] = mapped_column(String(32))
    most_recent_change: Mapped[str] = mapped_column(String(300), nullable=False)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_freshness_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    data_health: Mapped[str] = mapped_column(String(32), default="healthy", nullable=False)
    next_candle_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notification_status: Mapped[str] = mapped_column(
        String(32), default="not_attempted", nullable=False
    )
    condition_tree: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    latest_values: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)


class MonitorHealthSummary(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "monitor_health_summaries"
    __table_args__ = (
        UniqueConstraint("strategy_version_id", name="uq_monitor_health_version"),
        Index("ix_monitor_health_user_calculated", "user_id", "calculated_at"),
        Index("ix_monitor_health_strategy_calculated", "strategy_id", "calculated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    technical_status: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_status: Mapped[str] = mapped_column(String(32), nullable=False)
    technical_causes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    strategy_causes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConditionObservabilityAggregate(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "condition_observability_aggregates"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id",
            "condition_key",
            "window_started_at",
            "window_ended_at",
            name="uq_condition_observability_window",
        ),
        Index("ix_condition_observability_user_window", "user_id", "window_ended_at"),
        Index(
            "ix_condition_observability_version_blocker",
            "strategy_version_id",
            "final_blocker_count",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    strategy_condition_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_conditions.id", ondelete="SET NULL")
    )
    condition_key: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_label: Mapped[str] = mapped_column(String(240), nullable=False)
    rule_role: Mapped[str] = mapped_column(String(40), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    timeframe: Mapped[str | None] = mapped_column(String(16))
    evaluation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pass_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pending_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unavailable_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    final_blocker_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    near_miss_blocker_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalidation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_actual: Mapped[Decimal | None] = mapped_column(Numeric(30, 10))
    median_actual_when_blocked: Mapped[Decimal | None] = mapped_column(Numeric(30, 10))
    average_required: Mapped[Decimal | None] = mapped_column(Numeric(30, 10))
    average_distance: Mapped[Decimal | None] = mapped_column(Numeric(30, 10))
    co_occurrence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    previous_version_delta: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    counterfactual_preview: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    sample_status: Mapped[str] = mapped_column(String(24), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ObservabilityExplanation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "observability_explanations"
    __table_args__ = (
        Index("ix_observability_explanation_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    setup_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="CASCADE")
    )
    explanation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    grounded_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
