from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
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


class SystemBrainAuthChallenge(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "system_brain_auth_challenges"
    __table_args__ = (Index("ix_system_brain_challenge_email_created", "email", "created_at"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    requested_ip_hash: Mapped[str | None] = mapped_column(String(64))


class SystemBrainSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "system_brain_sessions"
    __table_args__ = (
        UniqueConstraint("session_digest", name="uq_system_brain_session_digest"),
        Index("ix_system_brain_session_expires", "expires_at"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    session_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))


class SystemBrainLoginAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "system_brain_login_attempts"
    __table_args__ = (Index("ix_system_brain_attempt_ip_created", "ip_hash", "created_at"),)

    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    username_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    successful: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CapabilityResolutionEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "capability_resolution_events"
    __table_args__ = (
        UniqueConstraint("event_fingerprint", name="uq_capability_resolution_fingerprint"),
        Index("ix_capability_resolution_status_created", "status", "created_at"),
        Index("ix_capability_resolution_chat_created", "chat_session_id", "created_at"),
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    chat_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="SET NULL"), index=True
    )
    event_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="setup_chat", nullable=False)
    source_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_fragment: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_capability_key: Mapped[str | None] = mapped_column(String(120), index=True)
    selection_source: Mapped[str | None] = mapped_column(String(40))
    selected_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    parameters_validated: Mapped[bool | None] = mapped_column(Boolean)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    unknown_terms: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    top_confidence: Mapped[float | None] = mapped_column(Float)
    provider_requirement: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CapabilityAliasProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capability_alias_proposals"
    __table_args__ = (
        UniqueConstraint(
            "normalized_alias",
            "capability_key",
            name="uq_capability_alias_proposal_target",
        ),
        Index("ix_capability_alias_status_created", "status", "created_at"),
    )

    alias: Mapped[str] = mapped_column(String(240), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(240), nullable=False)
    capability_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_event_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIUsageEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (
        Index("ix_ai_usage_model_created", "model", "created_at"),
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    chat_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="SET NULL"), index=True
    )
    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), default="openai", nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(20), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), default=Decimal("0"), nullable=False
    )
    pricing_source: Mapped[str] = mapped_column(String(200), nullable=False)
    raw_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_run_user_started", "user_id", "started_at"),
        Index("ix_agent_run_status_started", "status", "started_at"),
        Index("ix_agent_run_correlation", "correlation_id"),
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    chat_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="SET NULL"), index=True
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    step_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), default=Decimal("0"), nullable=False
    )
    timeout_outcome: Mapped[str | None] = mapped_column(String(40))
    budget_outcome: Mapped[str | None] = mapped_column(String(40))
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(100))
    shadow_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    final_intent: Mapped[str | None] = mapped_column(String(40))
    final_response_status: Mapped[str | None] = mapped_column(String(40))
    comparison: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class AgentToolCall(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "openai_call_id", name="uq_agent_tool_call_openai"),
        Index("ix_agent_tool_run_created", "agent_run_id", "created_at"),
        Index("ix_agent_tool_name_status", "tool_name", "result_status"),
    )

    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    openai_call_id: Mapped[str] = mapped_column(String(160), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    argument_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redacted_arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    policy_decision: Mapped[str] = mapped_column(String(60), nullable=False)
    result_status: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SystemBrainConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent administrator-owned System Brain conversation."""

    __tablename__ = "system_brain_conversations"
    __table_args__ = (
        Index("ix_system_brain_conversation_admin_updated", "admin_user_id", "updated_at"),
        Index("ix_system_brain_conversation_admin_archived", "admin_user_id", "archived_at"),
    )

    admin_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemBrainMessage(UUIDPrimaryKeyMixin, Base):
    """One persisted message; browser state is never the conversation authority."""

    __tablename__ = "system_brain_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_system_brain_message_sequence"),
        UniqueConstraint(
            "conversation_id", "client_message_id", name="uq_system_brain_message_client_id"
        ),
        Index("ix_system_brain_message_conversation_created", "conversation_id", "created_at"),
        Index("ix_system_brain_message_status_created", "status", "created_at"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("system_brain_conversations.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    client_message_id: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="completed", nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), default=Decimal("0"), nullable=False
    )
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metadata_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerConversationEvent(Base):
    """Monotonic privacy-safe cursor for the admin conversation explorer."""

    __tablename__ = "customer_conversation_events"
    __table_args__ = (
        Index("ix_customer_conversation_event_source_cursor", "source_type", "id"),
        Index("ix_customer_conversation_event_conversation_cursor", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    message_id: Mapped[UUID | None] = mapped_column()
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SystemBrainArtifact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Safe internal output created by an authorized read/analysis turn."""

    __tablename__ = "system_brain_artifacts"
    __table_args__ = (
        Index("ix_system_brain_artifact_admin_kind", "admin_user_id", "artifact_kind"),
        Index("ix_system_brain_artifact_conversation_created", "conversation_id", "created_at"),
    )

    admin_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("system_brain_conversations.id", ondelete="SET NULL")
    )
    artifact_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemBrainActionProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Exact consequential action awaiting a separately authenticated confirmation."""

    __tablename__ = "system_brain_action_proposals"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_system_brain_action_proposal_key"),
        Index("ix_system_brain_action_proposal_admin_status", "admin_user_id", "status"),
    )

    admin_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("system_brain_conversations.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str] = mapped_column(String(120), nullable=False)
    exact_changes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expected_effect: Mapped[str] = mapped_column(Text, nullable=False)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    rollback_path: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    confirmation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_result_redacted: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class RepositoryEvidenceIndex(UUIDPrimaryKeyMixin, Base):
    """Incremental, sensitivity-filtered repository evidence index."""

    __tablename__ = "repository_evidence_index"
    __table_args__ = (
        UniqueConstraint("path", name="uq_repository_evidence_path"),
        Index("ix_repository_evidence_hash", "content_hash"),
        Index("ix_repository_evidence_sensitivity", "sensitivity_classification"),
    )

    path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_commit: Mapped[str | None] = mapped_column(String(64))
    symbol_names: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    searchable_text: Mapped[str] = mapped_column(Text, nullable=False)
    line_references: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    sensitivity_classification: Mapped[str] = mapped_column(
        String(32), default="internal", nullable=False
    )
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
