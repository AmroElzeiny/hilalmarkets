from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CapabilityExtension(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capability_extensions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "request_fingerprint",
            name="uq_capability_extension_user_request",
        ),
        UniqueConstraint(
            "capability_key",
            "capability_version",
            name="uq_capability_extension_key_version",
        ),
        Index("ix_capability_extension_status_updated", "status", "updated_at"),
        Index("ix_capability_extension_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    chat_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="SET NULL")
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    pending_strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_key: Mapped[str] = mapped_column(String(120), nullable=False)
    capability_version: Mapped[str] = mapped_column(String(32), default="0.1.0", nullable=False)
    registry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    source_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    stage: Mapped[str] = mapped_column(String(48), default="requested", nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    expression: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    generated_code: Mapped[str | None] = mapped_column(Text)
    build_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validation_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    ai_review: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    failure_classification: Mapped[str | None] = mapped_column(String(40))
    scan_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    empty_scan_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    no_notification_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    symbols_scanned_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candidates_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notifications_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repair_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    certified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapabilityExtensionAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "capability_extension_attempts"
    __table_args__ = (
        UniqueConstraint(
            "extension_id",
            "attempt_number",
            name="uq_capability_extension_attempt_number",
        ),
        Index("ix_capability_extension_attempt_stage", "extension_id", "operation"),
    )

    extension_id: Mapped[UUID] = mapped_column(
        ForeignKey("capability_extensions.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(20), nullable=False)
    service_tier: Mapped[str] = mapped_column(String(20), default="default", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="started", nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapabilityExtensionScan(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "capability_extension_scans"
    __table_args__ = (
        Index("ix_capability_extension_scan_cycle", "extension_id", "phase", "cycle_number"),
    )

    extension_id: Mapped[UUID] = mapped_column(
        ForeignKey("capability_extensions.id", ondelete="CASCADE"), nullable=False
    )
    scan_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scan_jobs.id", ondelete="SET NULL")
    )
    phase: Mapped[str] = mapped_column(String(24), nullable=False)
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), default="bybit", nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    symbols_planned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candidates_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notifications_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candidate_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapabilityClarificationEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "capability_clarification_evidence"
    __table_args__ = (
        UniqueConstraint("evidence_fingerprint", name="uq_capability_clarification_evidence"),
        Index("ix_capability_clarification_key_created", "capability_key", "created_at"),
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    chat_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_setup_chat_sessions.id", ondelete="SET NULL")
    )
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    clarification_question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    capability_key: Mapped[str | None] = mapped_column(String(120))
    successful: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CapabilityRegistryArtifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "capability_registry_artifacts"
    __table_args__ = (
        UniqueConstraint("registry_hash", name="uq_capability_registry_artifact_hash"),
        Index("ix_capability_registry_active_created", "active", "created_at"),
    )

    registry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    registry_version: Mapped[str] = mapped_column(String(80), nullable=False)
    aliases: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict, nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embeddings: Mapped[dict[str, list[float]]] = mapped_column(JSON, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
