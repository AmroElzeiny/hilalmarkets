from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from ai_market_monitor.db.models.enums import (
    IncidentSeverity,
    IncidentStatus,
    SupportRequestStatus,
)


class UserFeedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_feedback"
    __table_args__ = (Index("ix_feedback_user_created", "user_id", "created_at"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    alert_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="SET NULL"), index=True
    )
    setup_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="SET NULL")
    )
    feedback_type: Mapped[str] = mapped_column(String(40), nullable=False)
    rating: Mapped[int | None]
    comment: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_target", "target_type", "target_id"),
        Index("ix_audit_request", "request_id"),
    )

    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    metadata_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SupportRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "support_requests"
    __table_args__ = (
        Index("ix_support_status_priority", "status", "priority"),
        Index("ix_support_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    assigned_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    status: Mapped[SupportRequestStatus] = mapped_column(
        enum_type(SupportRequestStatus, name="support_request_status"),
        default=SupportRequestStatus.OPEN,
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Incident(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incident_status_severity", "status", "severity"),
        Index("ix_incident_detected", "detected_at"),
    )

    status: Mapped[IncidentStatus] = mapped_column(
        enum_type(IncidentStatus, name="incident_status"),
        default=IncidentStatus.OPEN,
        nullable=False,
    )
    severity: Mapped[IncidentSeverity] = mapped_column(
        enum_type(IncidentSeverity, name="incident_severity"),
        default=IncidentSeverity.MINOR,
        nullable=False,
    )
    incident_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    affected_users_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    affected_strategies_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    material_user_impact: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    mitigation_status: Mapped[str] = mapped_column(
        String(80), default="not_started", nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class IncidentImpact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "incident_impacts"
    __table_args__ = (
        Index("ix_incident_impact_incident", "incident_id"),
        Index("ix_incident_impact_user", "user_id"),
        Index("ix_incident_impact_strategy", "strategy_id"),
    )

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    strategy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL")
    )
    scan_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scan_jobs.id", ondelete="SET NULL")
    )
    alert_id: Mapped[UUID | None] = mapped_column(ForeignKey("alerts.id", ondelete="SET NULL"))
    integration: Mapped[str | None] = mapped_column(String(40))
    scope_key: Mapped[str | None] = mapped_column(String(160))
    impact_type: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IncidentUpdate(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "incident_updates"
    __table_args__ = (Index("ix_incident_update_created", "incident_id", "created_at"),)

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    update_type: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    visible_to_users: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
