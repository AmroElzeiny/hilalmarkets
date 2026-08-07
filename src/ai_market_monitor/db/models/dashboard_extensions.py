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
        # One mutating turn may own a session at a time, enforced by the database and
        # not only by a process lock. ``session_claim`` carries the session id while the
        # turn is in flight and NULL once it settles, so the unique index is what makes
        # a second concurrent turn impossible even across two web workers.
        #
        # A partial index would express the same rule, but NULL-with-unique works on
        # both PostgreSQL and SQLite without dialect-specific clauses, and the test
        # suite must be able to prove the constraint it ships.
        UniqueConstraint("session_claim", name="uq_setup_chat_turn_active_claim"),
        Index("ix_setup_chat_turn_lease", "status", "lease_expires_at"),
    )

    chat_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_message_id: Mapped[str] = mapped_column(String(80), nullable=False)
    #: What this request actually asked for. Reusing an id with different content is a
    #: client bug, and answering it from the old record would show a reply to a message
    #: the user never sent, so the mismatch is refused instead.
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    #: The session id while this turn holds it, NULL once it settles. See the unique
    #: constraint above — this column *is* the lock.
    session_claim: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="CASCADE")
    )
    #: False for a read-only turn, which may run beside a mutating one.
    is_mutating: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
    telemetry_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
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
    #: The exact draft identity this turn planned against. Versions alone could not tell
    #: "unchanged" from "changed and changed back", so the hashes travel with them.
    executable_hash_before: Mapped[str | None] = mapped_column(String(64))
    workflow_state_hash_before: Mapped[str | None] = mapped_column(String(64))
    #: Which prompt and schema produced the plan. A plan built by an older prompt must
    #: not be executed by a newer server that means something different by the same word.
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    schema_version: Mapped[str | None] = mapped_column(String(40))
    #: What the planner call actually cost and which provider request it was. Kept
    #: separate from recovery usage so a replay can never overwrite the paid original.
    planner_usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    provider_request_id: Mapped[str | None] = mapped_column(String(120))
    #: When each lifecycle stage was entered. The recovery worker reads this to tell a
    #: slow turn from a dead one.
    stage_timestamps_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: Recovery bookkeeping. One owner at a time, holding a lease that expires.
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recovery_disposition: Mapped[str | None] = mapped_column(String(48))
    #: Model calls made while recovering, counted apart from the original turn so the
    #: cost report never merges a paid plan with a free deterministic replay.
    recovery_usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SetupChatOperationalIssue(UUIDPrimaryKeyMixin, Base):
    """Deduplicated admin queue for compiler faults and repeated customer loops."""

    __tablename__ = "setup_chat_operational_issues"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_setup_chat_operational_issue_fingerprint"),
        Index("ix_setup_chat_operational_issue_status_seen", "status", "last_seen_at"),
        Index("ix_setup_chat_operational_issue_chat", "chat_session_id", "last_seen_at"),
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    chat_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="SET NULL"), index=True
    )
    setup_chat_turn_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_chat_turns.id", ondelete="SET NULL"), index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    failure_class: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    semantic_paths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    safe_source_excerpt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    support_reference: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    failure_proof: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)


class SetupChatPendingChange(UUIDPrimaryKeyMixin, Base):
    """A destructive change that is proposed and not applied.

    This is deliberately its own table rather than a field on the session. A proposal
    is not draft state — nothing in the canonical draft moves while one exists — and
    storing it beside the draft is how the two would start to be confused for each
    other. It also has to outlive a crashed turn, so it cannot live only in memory.
    """

    __tablename__ = "setup_chat_pending_changes"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_setup_chat_pending_change_proposal"),
        Index(
            "ix_setup_chat_pending_change_owner",
            "chat_session_id",
            "status",
            "expires_at",
        ),
    )

    chat_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    proposal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_turn_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_chat_turns.id", ondelete="SET NULL")
    )
    client_message_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    #: The draft this was built against. Confirming against any other draft is refused.
    executable_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    executable_version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The authorized operations exactly as they were built. Confirming replays these;
    #: it never re-reads the user's words and never calls a model.
    operations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    operation_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    summary_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    invalidates_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    governance_notes_json: Mapped[list[str] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
