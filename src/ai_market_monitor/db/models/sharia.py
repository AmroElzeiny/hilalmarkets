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

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    ComplianceChangeSeverity,
    ComplianceChangeStatus,
    ComplianceReviewDecision,
    MonitorShariaAssetStatus,
    ShariaAssetStatus,
    ShariaMethodologyStatus,
    ShariaPolicyDecision,
    ShariaUniverseMode,
)


class ShariaMethodology(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sharia_methodologies"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_sharia_methodology_code_version"),
        Index("ix_sharia_methodology_status_effective", "status", "effective_from"),
    )

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    family_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_methodology_families.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ShariaMethodologyStatus] = mapped_column(
        enum_type(ShariaMethodologyStatus, name="sharia_methodology_status"),
        default=ShariaMethodologyStatus.DRAFT,
        nullable=False,
    )
    governing_body: Mapped[str | None] = mapped_column(String(240))
    reviewer_group: Mapped[str | None] = mapped_column(String(240))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rules_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_requirements_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class AssetShariaAssessment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "asset_sharia_assessments"
    __table_args__ = (
        Index(
            "ix_sharia_assessment_asset_methodology_valid",
            "canonical_asset",
            "methodology_id",
            "valid_from",
        ),
        Index("ix_sharia_assessment_status_reviewed", "status", "reviewed_at"),
    )

    canonical_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_name: Mapped[str | None] = mapped_column(String(160))
    methodology_id: Mapped[UUID] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[ShariaAssetStatus] = mapped_column(
        enum_type(ShariaAssetStatus, name="sharia_asset_status"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    qualifications: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    exclusion_reasons: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    reviewed_by: Mapped[str] = mapped_column(String(240), nullable=False)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_sharia_assessments.id", ondelete="SET NULL")
    )


class ShariaEvidenceSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sharia_evidence_sources"
    __table_args__ = (
        UniqueConstraint("assessment_id", "source_hash", name="uq_sharia_evidence_hash"),
        Index("ix_sharia_evidence_assessment_category", "assessment_id", "evidence_category"),
    )

    assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_sharia_assessments.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    publisher: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_category: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AssetShariaStatusHistory(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "asset_sharia_status_history"
    __table_args__ = (
        Index(
            "ix_sharia_status_history_asset_changed",
            "canonical_asset",
            "methodology_id",
            "changed_at",
        ),
    )

    canonical_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    methodology_id: Mapped[UUID] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="RESTRICT"), nullable=False
    )
    previous_status: Mapped[ShariaAssetStatus | None] = mapped_column(
        enum_type(ShariaAssetStatus, name="sharia_previous_asset_status")
    )
    new_status: Mapped[ShariaAssetStatus] = mapped_column(
        enum_type(ShariaAssetStatus, name="sharia_new_asset_status"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    reason_summary: Mapped[str] = mapped_column(Text, nullable=False)
    triggering_change_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("compliance_changes.id", ondelete="SET NULL")
    )
    assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_sharia_assessments.id", ondelete="RESTRICT"), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_by: Mapped[str] = mapped_column(String(240), nullable=False)


class ApprovedWatchlist(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approved_watchlists"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_approved_watchlist_user_name"),
        Index("ix_approved_watchlist_user_default", "user_id", "is_default"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ApprovedWatchlistAsset(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "approved_watchlist_assets"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "canonical_asset", name="uq_watchlist_asset"),
        Index("ix_watchlist_asset_asset", "canonical_asset"),
    )

    watchlist_id: Mapped[UUID] = mapped_column(
        ForeignKey("approved_watchlists.id", ondelete="CASCADE"), nullable=False
    )
    canonical_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShariaUniverseSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sharia_universe_snapshots"
    __table_args__ = (
        Index("ix_sharia_universe_methodology_resolved", "methodology_id", "resolved_at"),
        Index("ix_sharia_universe_user_resolved", "user_id", "resolved_at"),
        Index("ix_sharia_universe_hash", "snapshot_hash"),
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    methodology_id: Mapped[UUID] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="RESTRICT"), nullable=False
    )
    methodology_code: Mapped[str] = mapped_column(String(80), nullable=False)
    methodology_version: Mapped[str] = mapped_column(String(32), nullable=False)
    universe_mode: Mapped[ShariaUniverseMode] = mapped_column(
        enum_type(ShariaUniverseMode, name="sharia_universe_snapshot_mode"), nullable=False
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    quote_currencies: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    allowed_statuses: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    qualification_policy: Mapped[str] = mapped_column(String(40), nullable=False)
    disputed_asset_policy: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    considered_symbols: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    included_symbols: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    excluded_assets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(String(160))


class MonitorShariaAssetState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "monitor_sharia_asset_states"
    __table_args__ = (
        UniqueConstraint("strategy_version_id", "symbol", name="uq_monitor_sharia_asset"),
        Index("ix_monitor_sharia_state_strategy_status", "strategy_id", "state"),
        Index("ix_monitor_sharia_state_asset", "canonical_asset", "methodology_id"),
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
    methodology_id: Mapped[UUID] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="RESTRICT"), nullable=False
    )
    canonical_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    last_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_sharia_assessments.id", ondelete="SET NULL")
    )
    sharia_status: Mapped[ShariaAssetStatus] = mapped_column(
        enum_type(ShariaAssetStatus, name="monitor_sharia_asset_screening_status"),
        nullable=False,
    )
    state: Mapped[MonitorShariaAssetStatus] = mapped_column(
        enum_type(MonitorShariaAssetStatus, name="monitor_sharia_asset_state"),
        nullable=False,
    )
    policy_decision: Mapped[ShariaPolicyDecision] = mapped_column(
        enum_type(ShariaPolicyDecision, name="monitor_sharia_policy_decision"),
        nullable=False,
    )
    policy_reason: Mapped[str] = mapped_column(String(300), nullable=False)
    universe_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_universe_snapshots.id", ondelete="SET NULL")
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ComplianceChange(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "compliance_changes"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_compliance_change_idempotency"),
        Index("ix_compliance_change_status_detected", "status", "detected_at"),
        Index("ix_compliance_change_asset_detected", "canonical_asset", "detected_at"),
    )

    canonical_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    change_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[ComplianceChangeSeverity] = mapped_column(
        enum_type(ComplianceChangeSeverity, name="compliance_change_severity"),
        nullable=False,
    )
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_evidence_sources.id", ondelete="SET NULL")
    )
    source_reference: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    structured_change: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ComplianceChangeStatus] = mapped_column(
        enum_type(ComplianceChangeStatus, name="compliance_change_status"), nullable=False
    )
    detection_method: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_label: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)


class ComplianceReview(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "compliance_reviews"
    __table_args__ = (
        Index("ix_compliance_review_change_reviewed", "compliance_change_id", "reviewed_at"),
    )

    compliance_change_id: Mapped[UUID] = mapped_column(
        ForeignKey("compliance_changes.id", ondelete="CASCADE"), nullable=False
    )
    methodology_id: Mapped[UUID] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="RESTRICT"), nullable=False
    )
    previous_status: Mapped[ShariaAssetStatus | None] = mapped_column(
        enum_type(ShariaAssetStatus, name="compliance_review_previous_status")
    )
    proposed_status: Mapped[ShariaAssetStatus | None] = mapped_column(
        enum_type(ShariaAssetStatus, name="compliance_review_proposed_status")
    )
    final_status: Mapped[ShariaAssetStatus | None] = mapped_column(
        enum_type(ShariaAssetStatus, name="compliance_review_final_status")
    )
    decision: Mapped[ComplianceReviewDecision] = mapped_column(
        enum_type(ComplianceReviewDecision, name="compliance_review_decision"), nullable=False
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewer_identity: Mapped[str] = mapped_column(String(240), nullable=False)
    reviewer_notes: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision_version: Mapped[int] = mapped_column(Integer, nullable=False)


class ComplianceDriftNotification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "compliance_drift_notifications"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_compliance_drift_idempotency"),
        Index("ix_compliance_drift_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    compliance_change_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("compliance_changes.id", ondelete="SET NULL")
    )
    strategy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE")
    )
    alert_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="SET NULL")
    )
    canonical_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_status: Mapped[ShariaAssetStatus | None] = mapped_column(
        enum_type(ShariaAssetStatus, name="compliance_drift_previous_status")
    )
    new_status: Mapped[ShariaAssetStatus] = mapped_column(
        enum_type(ShariaAssetStatus, name="compliance_drift_new_status"), nullable=False
    )
    behavior: Mapped[ComplianceChangeBehavior] = mapped_column(
        enum_type(ComplianceChangeBehavior, name="compliance_drift_behavior"), nullable=False
    )
    impact: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    digest_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShariaMonitorMigrationRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sharia_monitor_migration_records"
    __table_args__ = (
        UniqueConstraint("strategy_id", name="uq_sharia_monitor_migration_strategy"),
        Index("ix_sharia_monitor_migration_action", "action", "created_at"),
    )

    strategy_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    prior_status: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
