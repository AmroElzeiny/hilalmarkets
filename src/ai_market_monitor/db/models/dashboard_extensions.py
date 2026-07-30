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


class StrategyTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_templates"
    __table_args__ = (
        Index("ix_strategy_template_user_category", "user_id", "category"),
        Index("ix_strategy_template_user_archived", "user_id", "archived_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_strategy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL")
    )
    source_strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(60), default="custom", nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    shared_scope: Mapped[str] = mapped_column(String(40), default="private", nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SetupReplayJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "setup_replay_jobs"
    __table_args__ = (
        Index("ix_replay_user_status_created", "user_id", "status", "created_at"),
        Index("ix_replay_strategy_requested", "strategy_id", "requested_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL")
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    exchange: Mapped[str] = mapped_column(String(40), default="binance", nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    approximate_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_before_minutes: Mapped[int] = mapped_column(Integer, default=240, nullable=False)
    window_after_minutes: Mapped[int] = mapped_column(Integer, default=240, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SetupReplayResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "setup_replay_results"
    __table_args__ = (Index("ix_replay_result_job", "replay_job_id"),)

    replay_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("setup_replay_jobs.id", ondelete="CASCADE"), nullable=False
    )
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    timeline_points: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    candle_proofs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    suggested_adjustments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BacktestJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "backtest_jobs"
    __table_args__ = (
        Index("ix_backtest_user_status_created", "user_id", "status", "created_at"),
        Index("ix_backtest_strategy_created", "strategy_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL")
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    exchange: Mapped[str] = mapped_column(String(40), default="binance", nullable=False)
    symbols: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at_range: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at_range: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class BacktestResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "backtest_results"
    __table_args__ = (Index("ix_backtest_result_job", "backtest_job_id"),)

    backtest_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("backtest_jobs.id", ondelete="CASCADE"), nullable=False
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    equity_curve: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    setup_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChartSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chart_snapshots"
    __table_args__ = (
        Index("ix_chart_snapshot_user_created", "user_id", "created_at"),
        Index("ix_chart_snapshot_subject", "subject_type", "subject_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(80), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1000))
    chart_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    proof_reference: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class UserExportJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_export_jobs"
    __table_args__ = (Index("ix_user_export_status_created", "user_id", "status", "created_at"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    export_type: Mapped[str] = mapped_column(String(40), nullable=False)
    format: Mapped[str] = mapped_column(String(20), default="json", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    file_url: Mapped[str | None] = mapped_column(String(1000))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SupportTicketMessage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "support_ticket_messages"
    __table_args__ = (
        Index("ix_support_message_ticket_created", "support_request_id", "created_at"),
    )

    support_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("support_requests.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    author_type: Mapped[str] = mapped_column(String(30), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    internal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntegrationTestResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_test_results"
    __table_args__ = (
        Index("ix_integration_test_user_created", "user_id", "created_at"),
        Index("ix_integration_test_connection", "integration", "connection_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration: Mapped[str] = mapped_column(String(40), nullable=False)
    connection_id: Mapped[str | None] = mapped_column(String(80))
    destination: Mapped[str | None] = mapped_column(String(160))
    provider_message_id: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class AISetupChatSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_setup_chat_sessions"
    __table_args__ = (
        Index("ix_ai_setup_chat_user_status_updated", "user_id", "status", "updated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="interviewing", nullable=False)
    title: Mapped[str] = mapped_column(String(160), default="New monitor", nullable=False)
    original_idea: Mapped[str | None] = mapped_column(Text)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    draft_schema_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    translation_sheet: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    lint_warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    rule_confidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    ambiguities: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    unsupported_conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    approved_strategy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL")
    )
    approved_strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AISetupChatMessage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_setup_chat_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_ai_setup_chat_message_sequence"),
        UniqueConstraint("session_id", "client_message_id", name="uq_ai_setup_chat_client_message"),
        Index("ix_ai_setup_chat_message_session_created", "session_id", "created_at"),
    )

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    message_type: Mapped[str] = mapped_column(String(40), default="text", nullable=False)
    client_message_id: Mapped[str | None] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SetupChatTurn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "setup_chat_turns"
    __table_args__ = (
        UniqueConstraint(
            "chat_session_id",
            "client_message_id",
            name="uq_setup_chat_turn_session_client_message",
        ),
        Index("ix_setup_chat_turn_session_status", "chat_session_id", "status"),
    )

    chat_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_message_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_setup_chat_messages.id", ondelete="SET NULL")
    )
    assistant_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_setup_chat_messages.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), default="RECEIVED", nullable=False)
    planner_model: Mapped[str | None] = mapped_column(String(120))
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    execution_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reply_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    mutation_committed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    executable_version_before: Mapped[int | None] = mapped_column(Integer)
    executable_version_after: Mapped[int | None] = mapped_column(Integer)
    workflow_revision_before: Mapped[int | None] = mapped_column(Integer)
    workflow_revision_after: Mapped[int | None] = mapped_column(Integer)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_stage: Mapped[str | None] = mapped_column(String(80))
    failure_retryable: Mapped[bool | None] = mapped_column(Boolean)
    failure_details_json: Mapped[list[str] | None] = mapped_column(JSON)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SetupChatDraftSnapshot(UUIDPrimaryKeyMixin, Base):
    """Immutable executable draft state owned by one user and chat session."""

    __tablename__ = "setup_chat_draft_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "chat_session_id",
            "executable_version",
            "executable_hash",
            name="uq_setup_chat_snapshot_identity",
        ),
        Index(
            "ix_setup_chat_snapshot_owner_version",
            "user_id",
            "chat_session_id",
            "executable_version",
        ),
    )

    chat_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_turn_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_setup_chat_messages.id", ondelete="SET NULL")
    )
    executable_version: Mapped[int] = mapped_column(Integer, nullable=False)
    executable_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
