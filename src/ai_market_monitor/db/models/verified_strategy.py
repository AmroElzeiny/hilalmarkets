from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StrategyInterpretationStatement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_interpretation_statements"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id", "position", name="uq_interpretation_statement_position"
        ),
        Index("ix_interpretation_statement_user_version", "user_id", "strategy_version_id"),
        Index("ix_interpretation_statement_status", "strategy_version_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    strategy_id: Mapped[UUID] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    original_phrase: Mapped[str] = mapped_column(Text, nullable=False)
    structured_interpretation: Mapped[str] = mapped_column(Text, nullable=False)
    rule_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    mechanics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    resolution_status: Mapped[str] = mapped_column(
        String(24), default="unresolved", nullable=False
    )
    resolution_text: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StrategyTestCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_test_cases"
    __table_args__ = (
        Index("ix_strategy_test_case_user_strategy", "user_id", "strategy_id"),
        Index("ix_strategy_test_case_active", "strategy_id", "active"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    strategy_id: Mapped[UUID] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    case_type: Mapped[str] = mapped_column(String(24), nullable=False)
    expected_result: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    evaluation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StrategyTestRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "strategy_test_runs"
    __table_args__ = (
        UniqueConstraint(
            "test_case_id", "strategy_version_id", "schema_hash", name="uq_test_case_version_hash"
        ),
        Index("ix_strategy_test_run_version_status", "strategy_version_id", "status"),
        Index("ix_strategy_test_run_case_run", "test_case_id", "run_at"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    test_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_test_cases.id", ondelete="CASCADE")
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE")
    )
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    expected_result: Mapped[str] = mapped_column(String(32), nullable=False)
    actual_result: Mapped[str] = mapped_column(String(32), nullable=False)
    condition_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    mismatch_reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    data_source: Mapped[str | None] = mapped_column(String(80))
    candle_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StrategyVersionVerification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "strategy_version_verifications"
    __table_args__ = (
        UniqueConstraint("strategy_version_id", name="uq_strategy_version_verification"),
        Index("ix_version_verification_user_updated", "user_id", "updated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    strategy_id: Mapped[UUID] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE")
    )
    interpretation_status: Mapped[str] = mapped_column(
        String(24), default="needs_review", nullable=False
    )
    tests_status: Mapped[str] = mapped_column(String(24), default="not_run", nullable=False)
    historical_status: Mapped[str] = mapped_column(
        String(24), default="not_run", nullable=False
    )
    historical_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("backtest_jobs.id", ondelete="SET NULL")
    )
    historical_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    semantic_diff: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    test_effects: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    historical_effects: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    contract_hash: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForensicInvestigation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "forensic_investigations"
    __table_args__ = (
        Index("ix_forensic_user_created", "user_id", "created_at"),
        Index("ix_forensic_strategy_time", "strategy_id", "requested_time"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    strategy_id: Mapped[UUID] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    setup_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="SET NULL")
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_availability: Mapped[str] = mapped_column(String(24), nullable=False)
    primary_category: Mapped[str] = mapped_column(String(40), nullable=False)
    conclusion: Mapped[str] = mapped_column(Text, nullable=False)
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    system_diagnostics: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    delivery_diagnostics: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutcomeReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outcome_reviews"
    __table_args__ = (
        UniqueConstraint("alert_id", "horizon_minutes", name="uq_outcome_alert_horizon"),
        Index("ix_outcome_user_created", "user_id", "created_at"),
        Index("ix_outcome_strategy_classification", "strategy_id", "classification"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    strategy_id: Mapped[UUID] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE")
    )
    setup_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="SET NULL")
    )
    alert_id: Mapped[UUID] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"))
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    classification: Mapped[str | None] = mapped_column(String(24))
    classification_rules: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    outcome_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    price_path: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
