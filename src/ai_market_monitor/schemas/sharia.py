from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    ComplianceChangeSeverity,
    ComplianceReviewDecision,
    ShariaAssetStatus,
    ShariaMethodologyStatus,
    ShariaUniverseMode,
)

DEFAULT_ALLOWED_SHARIA_STATUSES = [
    ShariaAssetStatus.ELIGIBLE,
    ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
]


class MethodologySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    version: str
    description: str
    status: ShariaMethodologyStatus
    governing_body: str | None
    reviewer_group: str | None
    published_at: datetime | None
    effective_from: datetime | None
    effective_to: datetime | None
    is_development_only: bool = False


class MethodologyDetail(MethodologySummary):
    rules: dict[str, Any]
    evidence_requirements: dict[str, Any]


class AssetAssessmentSummary(BaseModel):
    id: UUID
    canonical_asset: str
    asset_name: str | None
    methodology_id: UUID
    methodology_name: str
    methodology_version: str
    status: ShariaAssetStatus
    status_label: str
    summary: str
    qualifications: list[str]
    reviewed_by: str
    reviewed_at: datetime
    valid_from: datetime
    valid_until: datetime | None
    approved_status: ShariaAssetStatus | None = None
    safety_hold: bool = False


class EvidenceSourceResponse(BaseModel):
    id: UUID
    source_type: str
    title: str
    publisher: str
    source_url: str
    published_at: datetime | None
    retrieved_at: datetime
    evidence_category: str
    evidence_summary: str
    source_hash: str


class StatusHistoryResponse(BaseModel):
    id: UUID
    canonical_asset: str
    methodology_id: UUID
    previous_status: ShariaAssetStatus | None
    new_status: ShariaAssetStatus
    reason_code: str
    reason_summary: str
    changed_at: datetime
    approved_by: str


class PassportExchangeMarket(BaseModel):
    exchange: str
    market_symbol: str
    quote_asset: str
    market_type: str
    is_active: bool


class PassportIdentity(BaseModel):
    canonical_asset_id: UUID | None = None
    name: str
    symbol: str
    network: str | None = None
    asset_type: str = "unknown"
    native_asset: bool | None = None
    contract_addresses: dict[str, str] = Field(default_factory=dict)
    official_website: str | None = None
    official_documentation: str | None = None
    provider_ids: dict[str, str] = Field(default_factory=dict)
    logo_module_url: str | None = None
    exchange_markets: list[PassportExchangeMarket] = Field(default_factory=list)
    identity_state: str = "unavailable"
    identity_verified_at: datetime | None = None
    aliases: list[str] = Field(default_factory=list)


class PassportUseCoverage(BaseModel):
    key: str
    label: str
    status: str
    reason: str
    supporting_reference: str | None = None
    last_verified_at: datetime | None = None
    source_ids: list[str] = Field(default_factory=list)
    criterion_ids: list[str] = Field(default_factory=list)
    scope: str | None = None
    reviewer_user_id: UUID | None = None


class PassportCriterionOutcome(BaseModel):
    key: str
    label: str
    outcome: str
    evidence: list[str] = Field(default_factory=list)
    reviewer_explanation: str | None = None
    ai_factual_summary: str | None = None
    known_gaps: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    verified_at: datetime | None = None


class PassportEvidenceDetail(EvidenceSourceResponse):
    snapshot_id: UUID | None = None
    content_hash: str | None = None
    parser_version: str | None = None
    ai_extraction_version: str | None = None
    availability: str = "available"
    supports_criteria: list[str] = Field(default_factory=list)


class PassportDecisionRecord(BaseModel):
    review_case_id: UUID | None = None
    decision_id: UUID | None = None
    reviewer_user_id: UUID | None = None
    reviewer_display_name: str
    actor_role: str = "REVIEWER"
    methodology_version: str | None = None
    methodology_criteria_version: str | None = None
    methodology_criteria_hash: str | None = None
    decision: str
    reason: str
    qualifications: list[str] = Field(default_factory=list)
    evidence_snapshot_ids: list[str] = Field(default_factory=list)
    criterion_decisions: list[dict[str, Any]] = Field(default_factory=list)
    use_case_decisions: list[dict[str, Any]] = Field(default_factory=list)
    acknowledged_gaps: list[str] = Field(default_factory=list)
    decided_at: datetime | None = None
    published_by_user_id: UUID | None = None
    published_at: datetime | None = None
    integrity_hash: str | None = None


class PassportTimelineEntry(BaseModel):
    action: str
    actor: str
    occurred_at: datetime
    reason: str
    previous_state: str | None = None
    new_state: str | None = None
    related_source_ids: list[str] = Field(default_factory=list)
    passport_version_id: UUID | None = None


class PassportHistoricalContext(BaseModel):
    is_historical: bool = False
    event_time: datetime | None = None
    passport_version_id: UUID | None = None
    passport_version: int | None = None
    current_status: ShariaAssetStatus | None = None
    current_reviewed_at: datetime | None = None
    current_passport_url: str | None = None


class PassportHistoricalReference(BaseModel):
    reference_type: Literal["alert", "opportunity"]
    reference_id: UUID
    label: str
    event_time: datetime
    url: str
    strategy_version_id: UUID | None = None


class AssetPassportResponse(BaseModel):
    assessment: AssetAssessmentSummary
    why_this_status: str
    official_methodology_reference: dict[str, Any] = Field(default_factory=dict)
    official_sc_malaysia_reference: dict[str, Any] = Field(default_factory=dict)
    official_fasset_reference: dict[str, Any] = Field(default_factory=dict)
    hilalmarkets_factual_information_profile: dict[str, Any] = Field(default_factory=dict)
    separate_use_status: dict[str, Any] = Field(default_factory=dict)
    reviewed_dimensions: list[dict[str, Any]]
    methodology_result: dict[str, Any]
    evidence_sources: list[EvidenceSourceResponse]
    status_history: list[StatusHistoryResponse]
    evidence_available: bool
    notice: str
    identity: PassportIdentity | None = None
    freshness: str = "current"
    next_review_at: datetime | None = None
    evidence_expires_at: datetime | None = None
    source_scan_frequency_hours: int | None = None
    next_source_scan_at: datetime | None = None
    decision_date: datetime | None = None
    publication_date: datetime | None = None
    last_verified_at: datetime | None = None
    main_reasons: list[str] = Field(default_factory=list)
    main_qualification: str | None = None
    use_coverage: list[PassportUseCoverage] = Field(default_factory=list)
    criteria: list[PassportCriterionOutcome] = Field(default_factory=list)
    evidence_details: list[PassportEvidenceDetail] = Field(default_factory=list)
    decision_record: PassportDecisionRecord | None = None
    timeline: list[PassportTimelineEntry] = Field(default_factory=list)
    historical_references: list[PassportHistoricalReference] = Field(default_factory=list)
    historical: PassportHistoricalContext = Field(default_factory=PassportHistoricalContext)
    passport_version_id: UUID | None = None
    passport_version: int | None = None
    integrity_hash: str | None = None
    official_source_url: str | None = None
    can_create_watch_plan: bool = False
    restriction_explanation: str | None = None


class PassportQuickViewResponse(BaseModel):
    identity: PassportIdentity
    assessment: AssetAssessmentSummary
    primary_wording: str
    main_reasons: list[str]
    main_qualification: str | None = None
    freshness: str
    next_review_at: datetime | None = None
    evidence_expires_at: datetime | None = None
    source_scan_frequency_hours: int | None = None
    next_source_scan_at: datetime | None = None
    review_authority: str
    decision_date: datetime | None = None
    publication_date: datetime | None = None
    use_coverage: list[PassportUseCoverage]
    historical: PassportHistoricalContext
    passport_version_id: UUID | None = None
    passport_version: int | None = None
    official_source_url: str | None = None
    full_passport_url: str
    evidence_reference: str
    can_create_watch_plan: bool
    watchlist_action_url: str | None = None
    compliance_change_url: str | None = None
    restriction_explanation: str | None = None


class PassportProblemReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_type: Literal[
        "wrong_asset_identity",
        "broken_source",
        "outdated_evidence",
        "incorrect_qualification",
        "status_disagreement",
        "other",
    ]
    details: str = Field(min_length=20, max_length=5000)
    passport_version_id: UUID | None = None


class PassportProblemReportResponse(BaseModel):
    id: UUID
    state: str
    created_at: datetime
    message: str


class MethodologyComparisonItem(BaseModel):
    methodology: MethodologySummary
    status: ShariaAssetStatus | None
    review_date: datetime | None
    key_reasons: list[str]
    qualifications: list[str]
    evidence_completeness: str
    assessment_id: UUID | None


class MethodologyComparisonResponse(BaseModel):
    canonical_asset: str
    results: list[MethodologyComparisonItem]
    notice: str


class ScreenedAssetListResponse(BaseModel):
    items: list[AssetAssessmentSummary]
    page: int
    limit: int
    total: int
    status_counts: dict[str, int]
    methodology: MethodologySummary | None
    warning: str | None = None


class LiveMarketMethodologySummary(BaseModel):
    id: UUID | None = None
    code: str
    name: str
    version: str
    development_only: bool = False
    notice: str


class LiveSpotMarketQuote(BaseModel):
    symbol: str
    canonical_asset: str
    asset_name: str
    exchange: str
    quote_asset: str
    methodology_id: UUID | None = None
    methodology_name: str = "Unavailable"
    methodology_version: str = "unavailable"
    status: str = "unavailable"
    status_label: str = "Unavailable"
    reviewed_at: datetime | None = None
    passport_url: str | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    spread_bps: float | None = None
    percentage_24h: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    base_volume_24h: float | None = None
    quote_volume_24h: float | None = None
    logo_module_url: str | None = None
    data_available: bool
    updated_at: datetime


class LiveSpotMarketResponse(BaseModel):
    methodology: LiveMarketMethodologySummary
    items: list[LiveSpotMarketQuote]
    total: int
    exchange: str
    quote_asset: str
    provider: str
    captured_at: datetime
    refresh_after_ms: int = 1000
    stale: bool = False
    warning: str | None = None


class ShariaUniverseExclusion(BaseModel):
    symbol: str
    canonical_asset: str
    reason_code: str
    reason: str
    status: ShariaAssetStatus | None = None
    assessment_id: UUID | None = None


class ShariaUniverseInclusion(BaseModel):
    symbol: str
    canonical_asset: str
    canonical_asset_id: UUID | None = None
    exchange_market_id: UUID | None = None
    status: ShariaAssetStatus
    assessment_id: UUID
    passport_version_id: UUID | None = None
    qualification: list[str] = Field(default_factory=list)
    reviewed_at: datetime


class ShariaUniverseResolutionResponse(BaseModel):
    included_symbols: list[str]
    included: list[ShariaUniverseInclusion]
    excluded: list[ShariaUniverseExclusion]
    considered_count: int
    included_count: int
    excluded_by_policy_count: int
    insufficient_information_count: int
    methodology_id: UUID | None
    methodology_code: str | None
    methodology_version: str | None
    universe_mode: ShariaUniverseMode | None
    policy_hash: str | None
    snapshot_hash: str | None
    snapshot_id: UUID | None
    snapshot_version: int | None
    resolved_at: datetime
    legacy_local_bypass: bool = False
    monitor_paused_for_compliance: bool = False


class MethodologyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=3, max_length=80, pattern=r"^[A-Z0-9][A-Z0-9_-]+$")
    name: str = Field(min_length=3, max_length=180)
    version: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=20, max_length=5000)
    governing_body: str | None = Field(default=None, max_length=240)
    reviewer_group: str | None = Field(default=None, max_length=240)
    rules: dict[str, Any] = Field(default_factory=dict)
    evidence_requirements: dict[str, Any] = Field(default_factory=dict)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    status: ShariaMethodologyStatus = ShariaMethodologyStatus.DRAFT

    @model_validator(mode="after")
    def validate_activation_governance(self) -> "MethodologyCreateRequest":
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to <= self.effective_from
        ):
            raise ValueError("methodology effective_to must be after effective_from")
        if self.status == ShariaMethodologyStatus.ACTIVE and (
            not self.governing_body
            or not self.reviewer_group
            or not self.rules
            or not self.evidence_requirements
            or self.effective_from is None
        ):
            raise ValueError(
                "active methodologies require governing body, reviewer group, rules, "
                "evidence requirements, and an effective date"
            )
        return self


class EvidenceSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(min_length=2, max_length=60)
    title: str = Field(min_length=2, max_length=300)
    publisher: str = Field(min_length=2, max_length=200)
    source_url: HttpUrl
    published_at: datetime | None = None
    retrieved_at: datetime
    evidence_category: str = Field(min_length=2, max_length=80)
    evidence_summary: str = Field(min_length=5, max_length=5000)


class AssessmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_asset: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9._-]+$")
    asset_name: str | None = Field(default=None, max_length=160)
    methodology_id: UUID
    status: ShariaAssetStatus
    summary: str = Field(min_length=20, max_length=5000)
    qualifications: list[str] = Field(default_factory=list, max_length=30)
    exclusion_reasons: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)
    evidence_sources: list[EvidenceSourceInput] = Field(min_length=1, max_length=100)
    reviewed_by: str = Field(min_length=3, max_length=240)
    reviewed_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    reason_code: str = Field(default="manual_review", min_length=2, max_length=100)
    reason_summary: str = Field(min_length=10, max_length=2000)

    @field_validator("canonical_asset", mode="before")
    @classmethod
    def normalize_asset(cls, value: str) -> str:
        return str(value).strip().upper()


class ComplianceChangeIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_asset: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9._-]+$")
    change_type: Literal[
        "lending_or_borrowing_added",
        "interest_bearing_product_added",
        "staking_model_changed",
        "yield_structure_changed",
        "derivatives_or_leverage_added",
        "gambling_or_prohibited_integration",
        "treasury_or_reserve_changed",
        "tokenomics_changed",
        "governance_proposal",
        "primary_business_changed",
        "insufficient_disclosure",
        "methodology_rule_changed",
        "official_source_material_change",
    ]
    severity: ComplianceChangeSeverity
    source_reference: str | None = Field(default=None, max_length=1000)
    title: str = Field(min_length=3, max_length=300)
    summary: str = Field(min_length=10, max_length=5000)
    structured_change: dict[str, Any]
    detected_at: datetime
    effective_at: datetime | None = None
    detection_method: str = Field(min_length=2, max_length=80)
    confidence_label: Literal["low", "medium", "high", "verified_source"]
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=64)

    @field_validator("canonical_asset", mode="before")
    @classmethod
    def normalize_change_asset(cls, value: str) -> str:
        return str(value).strip().upper()


class ComplianceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    methodology_id: UUID
    decision: ComplianceReviewDecision
    proposed_status: ShariaAssetStatus | None = None
    reviewer_notes: str = Field(min_length=10, max_length=5000)
    reviewed_by: str = Field(min_length=3, max_length=240)
    assessment_summary: str | None = Field(default=None, min_length=20, max_length=5000)
    qualifications: list[str] = Field(default_factory=list, max_length=30)
    exclusion_reasons: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)
    evidence_sources: list[EvidenceSourceInput] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_approved_review(self) -> "ComplianceReviewRequest":
        if self.decision == ComplianceReviewDecision.APPROVED and (
            self.proposed_status is None or not self.assessment_summary or not self.evidence_sources
        ):
            raise ValueError(
                "approving a status requires the final status, assessment summary, "
                "and at least one evidence source"
            )
        return self


class WatchlistCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    assets: list[str] = Field(default_factory=list, max_length=10000)
    is_default: bool = False

    @field_validator("assets")
    @classmethod
    def normalize_assets(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip().upper() for value in values if value.strip()))


class WatchlistResponse(BaseModel):
    id: UUID
    name: str
    is_default: bool
    assets: list[str]
    created_at: datetime
    updated_at: datetime


class WatchlistAffectedPlan(BaseModel):
    strategy_id: UUID
    name: str
    status: str
    strategy_version_id: UUID
    strategy_version_number: int


class WatchlistAssetRemovalImpact(BaseModel):
    watchlist_id: UUID
    watchlist_name: str
    canonical_asset: str
    affected_watch_plans: list[WatchlistAffectedPlan]
    requires_confirmation: bool


def _default_compliance_channels() -> list[Literal["telegram", "web"]]:
    return ["web"]


class ShariaPreferenceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_methodology_id: UUID | None = None
    allowed_statuses: list[ShariaAssetStatus] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_SHARIA_STATUSES),
        min_length=1,
        max_length=6,
    )
    compliance_change_behavior: ComplianceChangeBehavior = ComplianceChangeBehavior.PAUSE_ASSET
    compliance_alerts_enabled: bool = True
    compliance_alert_channels: list[Literal["telegram", "web"]] = Field(
        default_factory=_default_compliance_channels, max_length=2
    )
    compliance_alert_digest: Literal["immediate", "daily"] = "immediate"
    qualification_change_alerts: bool = True
    under_review_alerts: bool = True
    exclusion_alerts: bool = True
    advanced_override_acknowledged: bool = False

    @model_validator(mode="after")
    def validate_preference_override(self) -> "ShariaPreferenceUpdateRequest":
        if (
            not set(self.allowed_statuses).issubset(set(DEFAULT_ALLOWED_SHARIA_STATUSES))
            and not self.advanced_override_acknowledged
        ):
            raise ValueError("advanced Sharia-status inclusion requires acknowledgement")
        return self


class ActivityItem(BaseModel):
    id: str
    item_type: Literal["opportunity", "alert", "ended", "compliance_change", "investigation"]
    occurred_at: datetime
    canonical_asset: str | None
    symbol: str | None
    monitor_id: UUID | None
    monitor_name: str | None
    opportunity_state: str | None
    sharia_status_at_event: ShariaAssetStatus | None
    current_sharia_status: ShariaAssetStatus | None
    methodology_id: UUID | None
    methodology_version: str | None
    title: str
    summary: str
    evidence_reference: str | None
    delivery_status: str | None
    requires_attention: bool


class ActivityResponse(BaseModel):
    items: list[ActivityItem]
    total: int
    page: int
    limit: int
