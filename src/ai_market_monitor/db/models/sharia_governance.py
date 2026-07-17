from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
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


class ShariaMethodologyFamily(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sharia_methodology_families"

    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    authority: Mapped[str] = mapped_column(String(240), nullable=False)
    regulatory_scope: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CanonicalAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "canonical_assets"
    __table_args__ = (
        UniqueConstraint("identity_hash", name="uq_canonical_asset_identity_hash"),
        Index("ix_canonical_asset_symbol_state", "symbol", "mapping_state"),
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    native_chain: Mapped[str | None] = mapped_column(String(120))
    contract_addresses: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    official_website: Mapped[str | None] = mapped_column(String(1000))
    official_documentation: Mapped[str | None] = mapped_column(String(1000))
    provider_ids: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_state: Mapped[str] = mapped_column(
        String(32), default="unresolved", nullable=False
    )
    mapping_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ExchangeMarket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "exchange_markets"
    __table_args__ = (
        UniqueConstraint("exchange", "market_symbol", name="uq_exchange_market_symbol"),
        Index("ix_exchange_market_asset_active", "canonical_asset_id", "is_active"),
    )

    canonical_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="CASCADE"), nullable=False
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    market_symbol: Mapped[str] = mapped_column(String(60), nullable=False)
    base_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(20), default="spot", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ShariaMonitoringRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sharia_monitoring_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_sharia_monitoring_run_key"),
        Index("ix_sharia_monitoring_run_kind_status", "run_kind", "status", "created_at"),
    )

    run_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    canonical_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_attempted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_detail: Mapped[str | None] = mapped_column(Text)


class OfficialSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "official_sources"
    __table_args__ = (
        UniqueConstraint(
            "canonical_asset_id", "normalized_url", name="uq_official_source_asset_url"
        ),
        Index("ix_official_source_asset_priority", "canonical_asset_id", "priority"),
    )

    canonical_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    verification_state: Mapped[str] = mapped_column(
        String(32), default="candidate", nullable=False
    )
    verified_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SourceSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "monitoring_run_id", "source_url", "content_hash", name="uq_source_snapshot_run_hash"
        ),
        Index("ix_source_snapshot_official_retrieved", "official_source_id", "retrieved_at"),
        Index("ix_source_snapshot_status_retrieved", "fetch_status", "retrieved_at"),
    )

    monitoring_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("sharia_monitoring_runs.id", ondelete="CASCADE"), nullable=False
    )
    official_source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("official_sources.id", ondelete="SET NULL")
    )
    previous_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="SET NULL")
    )
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(String(500))
    last_modified: Mapped[str | None] = mapped_column(String(500))
    response_headers: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    raw_content: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    headings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    meaningful_diff: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_material_change: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fetch_status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_detail: Mapped[str | None] = mapped_column(Text)
    scraper_version: Mapped[str] = mapped_column(String(80), nullable=False)
    parser_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ExternalAssessment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "external_assessments"
    __table_args__ = (
        UniqueConstraint("import_hash", name="uq_external_assessment_import_hash"),
        Index("ix_external_assessment_symbol_state", "asset_symbol", "mapping_state"),
    )

    canonical_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="SET NULL")
    )
    source_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    source_authority: Mapped[str] = mapped_column(String(300), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    asset_name: Mapped[str] = mapped_column(String(160), nullable=False)
    asset_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exact_status_wording: Mapped[str] = mapped_column(String(160), nullable=False)
    sac_meeting_number: Mapped[str] = mapped_column(String(80), nullable=False)
    decision_date: Mapped[date] = mapped_column(Date, nullable=False)
    regulatory_scope: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exact_row_text: Mapped[str] = mapped_column(Text, nullable=False)
    import_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_state: Mapped[str] = mapped_column(
        String(32), default="unresolved", nullable=False
    )
    mapping_notes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class AssetResearchDossier(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "asset_research_dossiers"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_asset_research_dossier_run_key"),
        Index("ix_asset_research_asset_state", "canonical_asset_id", "state", "created_at"),
    )

    canonical_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="CASCADE"), nullable=False
    )
    external_assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey("external_assessments.id", ondelete="RESTRICT"), nullable=False
    )
    monitoring_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("sharia_monitoring_runs.id", ondelete="RESTRICT"), nullable=False
    )
    run_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="researching", nullable=False)
    source_snapshot_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    evidence_completeness: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    missing_information_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    contradiction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evidence_package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    factual_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetEvidenceRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "asset_evidence_records"
    __table_args__ = (
        UniqueConstraint(
            "dossier_id", "source_snapshot_id", "evidence_hash", name="uq_asset_evidence_hash"
        ),
        Index("ix_asset_evidence_asset_category", "canonical_asset_id", "category"),
    )

    canonical_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="CASCADE"), nullable=False
    )
    dossier_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_research_dossiers.id", ondelete="CASCADE"), nullable=False
    )
    source_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    claim_summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    source_locator: Mapped[str | None] = mapped_column(String(500))
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AIAnalysisSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sharia_ai_analysis_snapshots"
    __table_args__ = (
        UniqueConstraint("dossier_id", "analysis_version", name="uq_sharia_ai_dossier_version"),
        Index("ix_sharia_ai_status_created", "status", "created_at"),
    )

    dossier_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_research_dossiers.id", ondelete="CASCADE"), nullable=False
    )
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_service_tier: Mapped[str] = mapped_column(String(20), nullable=False)
    returned_service_tier: Mapped[str | None] = mapped_column(String(20))
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_snapshot_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sharia_review_cases"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_sharia_review_case_key"),
        Index("ix_sharia_review_queue", "state", "case_type", "created_at"),
        Index("ix_sharia_review_asset", "canonical_asset_id", "created_at"),
    )

    case_reference: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    case_type: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    publication_state: Mapped[str] = mapped_column(
        String(32), default="unpublished", nullable=False
    )
    canonical_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="RESTRICT")
    )
    external_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("external_assessments.id", ondelete="RESTRICT")
    )
    dossier_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_research_dossiers.id", ondelete="RESTRICT")
    )
    methodology_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    risk_severity: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    human_review_reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_evidence: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    admin_notes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    assigned_reviewer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    source_freshness_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewDecision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sharia_review_decisions"
    __table_args__ = (
        UniqueConstraint("review_case_id", "decision_version", name="uq_review_decision_version"),
        UniqueConstraint(
            "integrity_hash", name="uq_sharia_review_decision_integrity_hash"
        ),
        Index("ix_review_decision_case_created", "review_case_id", "created_at"),
    )

    review_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("sharia_review_cases.id", ondelete="CASCADE"), nullable=False
    )
    admin_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    methodology_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_methodologies.id", ondelete="RESTRICT")
    )
    methodology_version: Mapped[str | None] = mapped_column(String(32))
    methodology_criteria_version: Mapped[str | None] = mapped_column(String(80))
    methodology_criteria_hash: Mapped[str | None] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_snapshot_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    criterion_decisions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    use_case_decisions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    qualifications: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    acknowledged_gaps: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    ai_analysis_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_ai_analysis_snapshots.id", ondelete="SET NULL")
    )
    actor_role: Mapped[str] = mapped_column(String(40), default="REVIEWER", nullable=False)
    application_version: Mapped[str | None] = mapped_column(String(80))
    security_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    integrity_hash: Mapped[str | None] = mapped_column(String(64))
    decision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublishedAssetAssessment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "published_asset_assessments"
    __table_args__ = (
        UniqueConstraint("canonical_asset_id", "version", name="uq_published_asset_version"),
        Index("ix_published_asset_active", "canonical_asset_id", "is_active"),
    )

    canonical_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="RESTRICT"), nullable=False
    )
    external_assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey("external_assessments.id", ondelete="RESTRICT"), nullable=False
    )
    dossier_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_research_dossiers.id", ondelete="RESTRICT"), nullable=False
    )
    review_decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("sharia_review_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    asset_assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_sharia_assessments.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_publication_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("published_asset_assessments.id", ondelete="SET NULL")
    )
    publication_state: Mapped[str] = mapped_column(String(32), nullable=False)
    passport_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    integrity_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    published_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceChangeEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_change_events"
    __table_args__ = (
        UniqueConstraint("change_hash", name="uq_source_change_hash"),
        Index("ix_source_change_asset_severity", "canonical_asset_id", "severity", "created_at"),
    )

    canonical_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="CASCADE"), nullable=False
    )
    official_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("official_sources.id", ondelete="RESTRICT"), nullable=False
    )
    previous_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="SET NULL")
    )
    current_snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    review_case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_review_cases.id", ondelete="SET NULL")
    )
    change_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    meaningful_diff: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    potentially_material: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="detected", nullable=False)
    change_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class TelegramNotificationAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sharia_telegram_notification_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_sharia_telegram_attempt_key"),
        Index("ix_sharia_telegram_due", "status", "next_retry_at"),
    )

    review_case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_review_cases.id", ondelete="CASCADE")
    )
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShariaGovernanceRoleGrant(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sharia_governance_role_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "role", name="uq_sharia_governance_user_role"),
        Index("ix_sharia_governance_role_active", "role", "revoked_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    granted_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ShariaReviewerProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sharia_reviewer_profiles"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    organization: Mapped[str | None] = mapped_column(String(240))
    authorization_role: Mapped[str] = mapped_column(String(160), nullable=False)
    qualifications: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ShariaReviewAssignmentEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sharia_review_assignment_events"
    __table_args__ = (
        Index("ix_sharia_assignment_case_created", "review_case_id", "created_at"),
    )

    review_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("sharia_review_cases.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    previous_assignee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_reviewer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShariaPassportProblemReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sharia_passport_problem_reports"
    __table_args__ = (
        UniqueConstraint(
            "review_case_id", name="uq_sharia_passport_problem_report_case"
        ),
        Index("ix_sharia_passport_report_state_created", "state", "created_at"),
        Index("ix_sharia_passport_report_asset", "canonical_asset_id", "created_at"),
    )

    reporter_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    canonical_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="RESTRICT"), nullable=False
    )
    asset_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_sharia_assessments.id", ondelete="SET NULL")
    )
    passport_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("published_asset_assessments.id", ondelete="SET NULL")
    )
    review_case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sharia_review_cases.id", ondelete="SET NULL")
    )
    report_type: Mapped[str] = mapped_column(String(60), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
