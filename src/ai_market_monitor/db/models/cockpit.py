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


class EdgeHealthSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "edge_health_snapshots"
    __table_args__ = (
        Index("ix_edge_health_strategy_calculated", "strategy_id", "calculated_at"),
        Index("ix_edge_health_user_calculated", "user_id", "calculated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    score: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    grade: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    components: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    main_issue: Mapped[str | None] = mapped_column(String(500))
    suggested_action: Mapped[str | None] = mapped_column(String(500))
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConditionBottleneckAggregate(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "condition_bottleneck_aggregates"
    __table_args__ = (
        Index(
            "ix_bottleneck_version_calculated",
            "strategy_version_id",
            "calculated_at",
        ),
        Index(
            "ix_bottleneck_strategy_impact",
            "strategy_id",
            "blocking_rate",
        ),
    )

    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    condition_key: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_label: Mapped[str] = mapped_column(String(240), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pending_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unavailable_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocking_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pass_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    blocking_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MissedMoveAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "missed_move_analyses"
    __table_args__ = (
        Index("ix_missed_move_user_status", "user_id", "status", "created_at"),
        Index("ix_missed_move_strategy_time", "strategy_id", "approximate_time"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    replay_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_replay_jobs.id", ondelete="SET NULL")
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    approximate_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_move_threshold: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    user_question: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StrategyExperiment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_experiments"
    __table_args__ = (
        Index("ix_experiment_strategy_status", "strategy_id", "status"),
        Index("ix_experiment_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    mode: Mapped[str] = mapped_column(String(30), default="dry_run", nullable=False)
    version_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    comparison: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    promoted_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertFrequencyForecast(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "alert_frequency_forecasts"
    __table_args__ = (
        Index("ix_frequency_forecast_strategy_calculated", "strategy_id", "calculated_at"),
    )

    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    estimated_min_per_week: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    estimated_max_per_week: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    classification: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    suggestions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UniverseOptimizationSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "universe_optimization_snapshots"
    __table_args__ = (Index("ix_universe_snapshot_strategy_created", "strategy_id", "created_at"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    included_symbols: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    excluded_symbols: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="market_provider", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StrategyValidationRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "strategy_validation_records"
    __table_args__ = (
        Index("ix_validation_strategy_created", "strategy_id", "created_at"),
        Index("ix_validation_schema_hash", "schema_hash"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE")
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    critical_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    info_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StrategySuggestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_suggestions"
    __table_args__ = (
        Index("ix_suggestion_strategy_status", "strategy_id", "status"),
        Index("ix_suggestion_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="deterministic", nullable=False)
    before_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    proposed_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    diff: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    outcome_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    historical_effect: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), default="low", nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    applied_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserStrategyPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_strategy_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_strategy_preference"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_derived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertInboxItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "alert_inbox_items"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "item_type",
            "source_type",
            "source_id",
            name="uq_inbox_source",
        ),
        Index("ix_inbox_user_created", "user_id", "created_at"),
        Index("ix_inbox_user_state", "user_id", "state", "archived_at"),
        Index("ix_inbox_strategy_created", "strategy_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL")
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    setup_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="SET NULL")
    )
    alert_id: Mapped[UUID | None] = mapped_column(ForeignKey("alerts.id", ondelete="SET NULL"))
    symbol: Mapped[str | None] = mapped_column(String(40))
    timeframe: Mapped[str | None] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    health_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    proof_reference: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StrategyDecayEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_decay_events"
    __table_args__ = (
        Index("ix_decay_strategy_status", "strategy_id", "status", "detected_at"),
        Index("ix_decay_user_detected", "user_id", "detected_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    baseline: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    current: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_actions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
