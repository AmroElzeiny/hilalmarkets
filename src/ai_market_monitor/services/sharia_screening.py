import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    AssetShariaStatusHistory,
    AuditEvent,
    ComplianceChange,
    PublishedAssetAssessment,
    ShariaEvidenceSource,
    ShariaMethodology,
)
from ai_market_monitor.db.models.enums import (
    ComplianceChangeSeverity,
    ComplianceChangeStatus,
    ShariaAssetStatus,
    ShariaMethodologyStatus,
)
from ai_market_monitor.schemas.sharia import (
    AssessmentCreateRequest,
    AssetAssessmentSummary,
    AssetPassportResponse,
    EvidenceSourceResponse,
    MethodologyComparisonItem,
    MethodologyComparisonResponse,
    MethodologyCreateRequest,
    MethodologyDetail,
    MethodologySummary,
    ScreenedAssetListResponse,
    StatusHistoryResponse,
)
from ai_market_monitor.schemas.sharia_methodology import (
    MethodologyEvidenceRequirements,
    MethodologyRulesDefinition,
)

DEFAULT_ALLOWED_STATUSES = {
    ShariaAssetStatus.ELIGIBLE,
    ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
}

STATUS_LABELS = {
    ShariaAssetStatus.ELIGIBLE: "Eligible",
    ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS: "Eligible with qualifications",
    ShariaAssetStatus.DISPUTED: "Disputed",
    ShariaAssetStatus.UNDER_REVIEW: "Under review",
    ShariaAssetStatus.EXCLUDED: "Excluded",
    ShariaAssetStatus.INSUFFICIENT_INFORMATION: "Insufficient information",
}

DEVELOPMENT_METHODOLOGY_PREFIX = "TRACEDGE_DEV_TEST_"
AGGREGATE_METHODOLOGY_CODE = "ALL_APPROVED_METHODOLOGIES"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ShariaScreeningError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_asset(value: str) -> str:
    symbol = value.upper().replace("-", "/").strip().split(":", 1)[0]
    return symbol.partition("/")[0]


def canonical_symbol(value: str) -> str:
    return value.upper().replace("-", "/").strip().split(":", 1)[0]


def methodology_is_development_only(methodology: ShariaMethodology) -> bool:
    return methodology.code.startswith(DEVELOPMENT_METHODOLOGY_PREFIX)


def sharia_evidence_from_proof(proof: object) -> dict[str, object]:
    """Read immutable screening evidence from current and legacy proof layouts."""
    if not isinstance(proof, dict):
        return {}
    direct = proof.get("sharia_screening")
    if isinstance(direct, dict):
        return dict(direct)
    scan_context = proof.get("scan_context")
    if isinstance(scan_context, dict):
        nested = scan_context.get("sharia_screening")
        if isinstance(nested, dict):
            return dict(nested)

    # Early Sharia-layer builds wrote flattened fields. Keep those receipts readable.
    legacy_asset = {
        "status": proof.get("sharia_status_at_scan"),
        "assessment_id": proof.get("sharia_assessment_id"),
        "reviewed_at": proof.get("sharia_reviewed_at"),
        "canonical_asset": proof.get("sharia_canonical_asset"),
    }
    legacy = {
        "methodology_id": proof.get("sharia_methodology_id"),
        "methodology_code": proof.get("sharia_methodology_code"),
        "methodology_version": proof.get("sharia_methodology_version"),
        "asset": {key: value for key, value in legacy_asset.items() if value is not None},
    }
    return {key: value for key, value in legacy.items() if value is not None and value != {}}


class ShariaScreeningService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None):
        self.session = session
        self.settings = settings

    async def methodologies(
        self,
        *,
        include_non_active: bool = False,
    ) -> list[ShariaMethodology]:
        query = select(ShariaMethodology)
        if not include_non_active:
            query = query.where(ShariaMethodology.status == ShariaMethodologyStatus.ACTIVE)
        rows = list(
            (
                await self.session.scalars(
                    query.order_by(
                        ShariaMethodology.effective_from.desc(),
                        ShariaMethodology.created_at.desc(),
                    )
                )
            ).all()
        )
        return sorted(
            rows,
            key=lambda row: (
                0 if row.code == AGGREGATE_METHODOLOGY_CODE else 1,
                -_as_utc(row.effective_from or row.created_at).timestamp(),
                row.name.casefold(),
            ),
        )

    async def executable_methodologies(
        self,
        *,
        as_of: datetime | None = None,
    ) -> list[ShariaMethodology]:
        """Return only methodologies that may power user-facing screening."""
        effective_at = _as_utc(as_of or datetime.now(UTC))
        rows = await self.methodologies(include_non_active=False)
        executable: list[ShariaMethodology] = []
        for row in rows:
            try:
                self._assert_effective(row, effective_at)
            except ShariaScreeningError:
                continue
            executable.append(row)
        return executable

    def development_methodologies_enabled(self) -> bool:
        """The former permissive Test methodology is retired from every customer view."""
        return False

    async def selectable_market_methodologies(self) -> list[ShariaMethodology]:
        """Return only executable, non-development methodologies."""
        return [
            item
            for item in await self.executable_methodologies()
            if not methodology_is_development_only(item)
        ]

    async def development_methodology(self) -> ShariaMethodology | None:
        return None

    async def methodology(
        self,
        methodology_id: UUID,
        *,
        require_active: bool = False,
        as_of: datetime | None = None,
    ) -> ShariaMethodology:
        methodology = await self.session.get(ShariaMethodology, methodology_id)
        if methodology is None:
            raise ShariaScreeningError("methodology_not_found", "Methodology not found.")
        if require_active:
            self._assert_effective(methodology, as_of or datetime.now(UTC))
        return methodology

    async def default_methodology(
        self,
        *,
        as_of: datetime | None = None,
    ) -> ShariaMethodology | None:
        as_of = as_of or datetime.now(UTC)
        query = select(ShariaMethodology).where(
            ShariaMethodology.status == ShariaMethodologyStatus.ACTIVE,
            ShariaMethodology.effective_from.is_not(None),
            ShariaMethodology.effective_from <= as_of,
            or_(
                ShariaMethodology.effective_to.is_(None),
                ShariaMethodology.effective_to > as_of,
            ),
            ShariaMethodology.code.not_like(f"{DEVELOPMENT_METHODOLOGY_PREFIX}%"),
        )
        configured_code = (
            (self.settings.sharia_default_methodology_code or "").strip() if self.settings else ""
        )
        if configured_code:
            query = query.where(ShariaMethodology.code == configured_code)
        methodology = await self.session.scalar(
            query.order_by(
                ShariaMethodology.effective_from.desc(),
                ShariaMethodology.created_at.desc(),
            ).limit(1)
        )
        if methodology is None:
            return None
        try:
            self._assert_effective(methodology, as_of)
        except ShariaScreeningError:
            return None
        return methodology

    async def resolve_methodology(
        self,
        methodology_id: UUID | None,
        *,
        as_of: datetime | None = None,
    ) -> ShariaMethodology:
        if methodology_id is not None:
            return await self.methodology(
                methodology_id,
                require_active=True,
                as_of=as_of,
            )
        methodology = await self.default_methodology(as_of=as_of)
        if methodology is None:
            raise ShariaScreeningError(
                "active_methodology_required",
                "No approved active screening methodology is configured. A qualified "
                "reviewer must activate one before screened scans can run.",
            )
        self._assert_effective(methodology, as_of or datetime.now(UTC))
        return methodology

    async def effective_assessments(
        self,
        methodology_id: UUID,
        *,
        assets: set[str] | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, AssetShariaAssessment]:
        as_of = as_of or datetime.now(UTC)
        methodology = await self.methodology(methodology_id, require_active=True, as_of=as_of)
        aggregate = methodology.code == AGGREGATE_METHODOLOGY_CODE
        source_methodologies: list[ShariaMethodology] = []
        methodology_ids = [methodology_id]
        if aggregate:
            source_methodologies = [
                row
                for row in await self.executable_methodologies(as_of=as_of)
                if row.code != AGGREGATE_METHODOLOGY_CODE
                and not methodology_is_development_only(row)
            ]
            methodology_ids = [row.id for row in source_methodologies]
            if not methodology_ids:
                return {}
        query = select(AssetShariaAssessment).where(
            AssetShariaAssessment.methodology_id.in_(methodology_ids),
            AssetShariaAssessment.valid_from <= as_of,
            or_(
                AssetShariaAssessment.valid_until.is_(None),
                AssetShariaAssessment.valid_until > as_of,
            ),
        )
        normalized_assets = {canonical_asset(asset) for asset in assets or set()}
        if normalized_assets:
            query = query.where(AssetShariaAssessment.canonical_asset.in_(normalized_assets))
        rows = (
            await self.session.scalars(
                query.order_by(
                    AssetShariaAssessment.canonical_asset.asc(),
                    AssetShariaAssessment.valid_from.desc(),
                    AssetShariaAssessment.reviewed_at.desc(),
                    AssetShariaAssessment.created_at.desc(),
                )
            )
        ).all()
        current: dict[str, AssetShariaAssessment] = {}
        priority_codes = list(
            methodology.rules_json.get(
                "source_priority_codes",
                ["SC_MALAYSIA_SAC_REFERENCE", "FASSET_SHARIAH_REPORTS"],
            )
        )
        priority_by_id = {
            row.id: (
                priority_codes.index(row.code)
                if row.code in priority_codes
                else len(priority_codes)
            )
            for row in source_methodologies
        }
        if aggregate:
            rows = sorted(
                rows,
                key=lambda row: (
                    row.canonical_asset,
                    0 if row.status in DEFAULT_ALLOWED_STATUSES else 1,
                    priority_by_id.get(row.methodology_id, len(priority_codes)),
                    -_as_utc(row.reviewed_at).timestamp(),
                    str(row.id),
                ),
            )
        for row in rows:
            current.setdefault(row.canonical_asset, row)
        return current

    async def effective_assessment(
        self,
        methodology_id: UUID,
        asset: str,
        *,
        as_of: datetime | None = None,
    ) -> AssetShariaAssessment | None:
        return (
            await self.effective_assessments(
                methodology_id,
                assets={canonical_asset(asset)},
                as_of=as_of,
            )
        ).get(canonical_asset(asset))

    async def safety_hold_assets(self, *, assets: set[str] | None = None) -> set[str]:
        if not self.settings or not self.settings.sharia_compliance_safety_under_review:
            return set()
        query = select(ComplianceChange.canonical_asset).where(
            ComplianceChange.severity.in_(
                [
                    ComplianceChangeSeverity.REVIEW_REQUIRED,
                    ComplianceChangeSeverity.CRITICAL,
                ]
            ),
            ComplianceChange.status.in_(
                [
                    ComplianceChangeStatus.DETECTED,
                    ComplianceChangeStatus.TRIAGED,
                    ComplianceChangeStatus.AWAITING_REVIEW,
                ]
            ),
        )
        normalized = {canonical_asset(asset) for asset in assets or set()}
        if assets is not None and not normalized:
            return set()
        if normalized:
            query = query.where(ComplianceChange.canonical_asset.in_(normalized))
        return set((await self.session.scalars(query.distinct())).all())

    async def list_screened_assets(
        self,
        *,
        methodology_id: UUID | None,
        statuses: set[ShariaAssetStatus] | None = None,
        search: str | None = None,
        asset_scope: set[str] | None = None,
        page: int = 1,
        limit: int = 30,
    ) -> ScreenedAssetListResponse:
        methodology: ShariaMethodology | None
        if methodology_id is None:
            methodology = await self.default_methodology()
        else:
            methodology = await self.methodology(methodology_id, require_active=True)
        if methodology is None:
            return ScreenedAssetListResponse(
                items=[],
                page=page,
                limit=limit,
                total=0,
                status_counts={},
                methodology=None,
                warning=(
                    "No approved active methodology is configured. The development seed is "
                    "not a religious ruling and is never shown as eligible market data."
                ),
            )
        assessments = await self.effective_assessments(methodology.id)
        readiness_warning = None
        safety_holds = await self.safety_hold_assets(assets=set(assessments))
        values = list(assessments.values())
        source_methodologies = {
            row.id: row
            for row in await self.methodologies(include_non_active=True)
            if row.id in {assessment.methodology_id for assessment in values}
        }
        if methodology.code == AGGREGATE_METHODOLOGY_CODE or (
            self.settings and self.settings.is_deployed
        ):
            assessment_ids = [row.id for row in values]
            if assessment_ids:
                published_ids = set(
                    (
                        await self.session.scalars(
                            select(PublishedAssetAssessment.asset_assessment_id).where(
                                PublishedAssetAssessment.asset_assessment_id.in_(assessment_ids),
                                PublishedAssetAssessment.is_active.is_(True),
                                PublishedAssetAssessment.publication_state == "published",
                            )
                        )
                    ).all()
                )
            else:
                published_ids = set()
            values = [row for row in values if row.id in published_ids]
        if not values:
            readiness_warning = (
                f"{methodology.name}, version {methodology.version}, has no active published "
                "Passports available for Halal Assets."
            )
        if asset_scope is not None:
            normalized_scope = {canonical_asset(asset) for asset in asset_scope}
            values = [row for row in values if row.canonical_asset in normalized_scope]
        count_values = list(values)
        if statuses:
            values = [
                row
                for row in values
                if (
                    ShariaAssetStatus.UNDER_REVIEW
                    if row.canonical_asset in safety_holds
                    else row.status
                )
                in statuses
            ]
        if search:
            needle = search.casefold().strip()
            searched_asset = canonical_asset(search).casefold()
            values = [
                row
                for row in values
                if needle in row.canonical_asset.casefold()
                or needle in (row.asset_name or "").casefold()
                or searched_asset == row.canonical_asset.casefold()
            ]
        values.sort(key=lambda row: (row.canonical_asset, row.reviewed_at), reverse=False)
        counts: dict[str, int] = {}
        for row in count_values:
            effective_status = (
                ShariaAssetStatus.UNDER_REVIEW
                if row.canonical_asset in safety_holds
                else row.status
            )
            counts[effective_status.value] = counts.get(effective_status.value, 0) + 1
        total = len(values)
        offset = (page - 1) * limit
        return ScreenedAssetListResponse(
            items=[
                self.assessment_summary(
                    row,
                    source_methodologies.get(row.methodology_id, methodology),
                    status_override=(
                        ShariaAssetStatus.UNDER_REVIEW
                        if row.canonical_asset in safety_holds
                        else None
                    ),
                    summary_override=(
                        "A configured safety policy has temporarily placed this asset under "
                        "review. The previous approved assessment remains in history until a "
                        "qualified reviewer records a decision."
                        if row.canonical_asset in safety_holds
                        else None
                    ),
                )
                for row in values[offset : offset + limit]
            ],
            page=page,
            limit=limit,
            total=total,
            status_counts=counts,
            methodology=self.methodology_summary(methodology),
            warning=readiness_warning,
        )

    async def passport(
        self,
        asset: str,
        *,
        methodology_id: UUID | None = None,
    ) -> AssetPassportResponse:
        methodology = await self.resolve_methodology(methodology_id)
        assessment = await self.effective_assessment(methodology.id, asset)
        if assessment is None:
            raise ShariaScreeningError(
                "assessment_not_found",
                "No current evidence-backed assessment exists for this asset and methodology.",
            )
        if assessment.methodology_id != methodology.id:
            methodology = await self.methodology(
                assessment.methodology_id,
                require_active=True,
            )
        evidence = list(
            (
                await self.session.scalars(
                    select(ShariaEvidenceSource)
                    .where(ShariaEvidenceSource.assessment_id == assessment.id)
                    .order_by(ShariaEvidenceSource.retrieved_at.desc())
                )
            ).all()
        )
        history = list(
            (
                await self.session.scalars(
                    select(AssetShariaStatusHistory)
                    .where(
                        AssetShariaStatusHistory.canonical_asset == assessment.canonical_asset,
                        AssetShariaStatusHistory.methodology_id == methodology.id,
                    )
                    .order_by(AssetShariaStatusHistory.changed_at.desc())
                )
            ).all()
        )
        snapshot = assessment.evidence_snapshot or {}
        safety_hold = canonical_asset(asset) in await self.safety_hold_assets(assets={asset})
        reviewed_dimensions = list(snapshot.get("reviewed_dimensions") or [])
        methodology_result = dict(snapshot.get("methodology_result") or {})
        official_reference = dict(
            snapshot.get("official_methodology_reference")
            or snapshot.get("official_sc_malaysia_reference")
            or snapshot.get("official_fasset_reference")
            or {}
        )
        factual_profile = dict(snapshot.get("hilalmarkets_factual_information_profile") or {})
        separate_use_status = {
            str(key): value
            for key, value in dict(snapshot.get("separate_use_status") or {}).items()
        }
        return AssetPassportResponse(
            assessment=self.assessment_summary(
                assessment,
                methodology,
                status_override=(ShariaAssetStatus.UNDER_REVIEW if safety_hold else None),
                summary_override=(
                    "A configured safety policy has temporarily placed this asset under "
                    "review while a qualified reviewer evaluates a material change."
                    if safety_hold
                    else None
                ),
            ),
            why_this_status=(
                "A configured safety policy has temporarily placed this asset under review "
                "while a qualified reviewer evaluates a material change."
                if safety_hold
                else assessment.summary
            ),
            official_methodology_reference=official_reference,
            official_sc_malaysia_reference=dict(
                snapshot.get("official_sc_malaysia_reference") or {}
            ),
            official_fasset_reference=dict(snapshot.get("official_fasset_reference") or {}),
            hilalmarkets_factual_information_profile=factual_profile,
            separate_use_status=separate_use_status,
            reviewed_dimensions=reviewed_dimensions,
            methodology_result=methodology_result,
            evidence_sources=[self.evidence_response(row) for row in evidence],
            status_history=[self.history_response(row) for row in history],
            evidence_available=bool(evidence),
            notice=(
                f"Operationally shown as {STATUS_LABELS[ShariaAssetStatus.UNDER_REVIEW].lower()} "
                f"under the configured safety policy; the last approved assessment was "
                f"{STATUS_LABELS[assessment.status].lower()} under {methodology.name}, version "
                f"{methodology.version}. Hilal Markets AI did not issue this status."
                if safety_hold
                else f"Screened as {STATUS_LABELS[assessment.status].lower()} under "
                f"{methodology.name}, version {methodology.version}, reviewed "
                f"{assessment.reviewed_at.date().isoformat()}. This records the selected "
                "methodology's conclusion; Hilal Markets AI does not issue religious rulings."
            ),
        )

    async def methodology_comparison(self, asset: str) -> MethodologyComparisonResponse:
        methodologies = await self.executable_methodologies()
        results: list[MethodologyComparisonItem] = []
        for methodology in methodologies:
            if methodology.code == AGGREGATE_METHODOLOGY_CODE:
                continue
            assessment = await self.effective_assessment(methodology.id, asset)
            if assessment is None:
                continue
            if self.settings and self.settings.is_deployed:
                publication_id = await self.session.scalar(
                    select(PublishedAssetAssessment.id).where(
                        PublishedAssetAssessment.asset_assessment_id == assessment.id,
                        PublishedAssetAssessment.is_active.is_(True),
                        PublishedAssetAssessment.publication_state == "published",
                    )
                )
                if publication_id is None:
                    continue
            evidence_count = len(
                (
                    await self.session.scalars(
                        select(ShariaEvidenceSource.id).where(
                            ShariaEvidenceSource.assessment_id == assessment.id
                        )
                    )
                ).all()
            )
            snapshot = assessment.evidence_snapshot or {}
            results.append(
                MethodologyComparisonItem(
                    methodology=self.methodology_summary(methodology),
                    status=assessment.status,
                    review_date=assessment.reviewed_at,
                    key_reasons=list((snapshot or {}).get("key_reasons") or []),
                    qualifications=list(assessment.qualifications),
                    evidence_completeness=(
                        "documented" if evidence_count > 0 else "insufficient_information"
                    ),
                    assessment_id=assessment.id,
                )
            )
        return MethodologyComparisonResponse(
            canonical_asset=canonical_asset(asset),
            results=results,
            notice=(
                "Different methodologies may apply different definitions, thresholds, or "
                "evidence requirements. Hilal Markets shows each approved result separately "
                "rather than presenting false consensus."
            ),
        )

    async def create_methodology(
        self,
        payload: MethodologyCreateRequest,
        *,
        actor_user_id: UUID | None,
        actor_identity: str,
    ) -> ShariaMethodology:
        existing = await self.session.scalar(
            select(ShariaMethodology.id).where(
                ShariaMethodology.code == payload.code,
                ShariaMethodology.version == payload.version,
            )
        )
        if existing is not None:
            raise ShariaScreeningError(
                "methodology_version_exists", "This methodology version already exists."
            )
        if payload.status == ShariaMethodologyStatus.ACTIVE:
            self.validate_methodology_contract(payload.rules, payload.evidence_requirements)
        now = datetime.now(UTC)
        methodology = ShariaMethodology(
            code=payload.code,
            name=payload.name,
            version=payload.version,
            description=payload.description,
            status=payload.status,
            governing_body=payload.governing_body,
            reviewer_group=payload.reviewer_group,
            published_at=now if payload.status == ShariaMethodologyStatus.ACTIVE else None,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            rules_json=payload.rules,
            evidence_requirements_json=payload.evidence_requirements,
        )
        self.session.add(methodology)
        await self.session.flush()
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="admin",
                action="sharia.methodology_created",
                target_type="sharia_methodology",
                target_id=str(methodology.id),
                metadata_redacted={
                    "code": methodology.code,
                    "version": methodology.version,
                    "status": methodology.status.value,
                    "actor_identity": actor_identity,
                },
                created_at=now,
            )
        )
        return methodology

    async def create_assessment(
        self,
        payload: AssessmentCreateRequest,
        *,
        actor_user_id: UUID | None,
        triggering_change_id: UUID | None = None,
    ) -> AssetShariaAssessment:
        methodology = await self.methodology(payload.methodology_id, require_active=True)
        previous = await self.effective_assessment(
            methodology.id,
            payload.canonical_asset,
            as_of=payload.valid_from,
        )
        valid_from = _as_utc(payload.valid_from)
        if previous is not None and _as_utc(previous.valid_from) >= valid_from:
            raise ShariaScreeningError(
                "assessment_time_conflict",
                "The new assessment must start after the current assessment.",
            )
        if previous is not None:
            previous.valid_until = valid_from
        assessment = AssetShariaAssessment(
            canonical_asset=canonical_asset(payload.canonical_asset),
            asset_name=payload.asset_name,
            methodology_id=methodology.id,
            status=payload.status,
            summary=payload.summary,
            qualifications=payload.qualifications,
            exclusion_reasons=payload.exclusion_reasons,
            evidence_snapshot=payload.evidence_snapshot,
            reviewed_by=payload.reviewed_by,
            reviewed_by_user_id=actor_user_id,
            reviewed_at=payload.reviewed_at,
            valid_from=valid_from,
            valid_until=payload.valid_until,
            supersedes_assessment_id=previous.id if previous else None,
        )
        self.session.add(assessment)
        await self.session.flush()
        for source in payload.evidence_sources:
            source_payload = source.model_dump(mode="json")
            source_hash = hashlib.sha256(
                json.dumps(source_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            self.session.add(
                ShariaEvidenceSource(
                    assessment_id=assessment.id,
                    source_type=source.source_type,
                    title=source.title,
                    publisher=source.publisher,
                    source_url=str(source.source_url),
                    published_at=source.published_at,
                    retrieved_at=source.retrieved_at,
                    evidence_category=source.evidence_category,
                    evidence_summary=source.evidence_summary,
                    source_hash=source_hash,
                )
            )
        history = AssetShariaStatusHistory(
            canonical_asset=assessment.canonical_asset,
            methodology_id=methodology.id,
            previous_status=previous.status if previous else None,
            new_status=assessment.status,
            reason_code=payload.reason_code,
            reason_summary=payload.reason_summary,
            triggering_change_id=triggering_change_id,
            assessment_id=assessment.id,
            changed_at=valid_from,
            approved_by_user_id=actor_user_id,
            approved_by=payload.reviewed_by,
        )
        self.session.add(history)
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="reviewer",
                action="sharia.assessment_approved",
                target_type="asset_sharia_assessment",
                target_id=str(assessment.id),
                metadata_redacted={
                    "asset": assessment.canonical_asset,
                    "methodology_id": str(methodology.id),
                    "methodology_version": methodology.version,
                    "previous_status": previous.status.value if previous else None,
                    "new_status": assessment.status.value,
                    "evidence_count": len(payload.evidence_sources),
                },
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return assessment

    @staticmethod
    def methodology_summary(methodology: ShariaMethodology) -> MethodologySummary:
        return MethodologySummary(
            id=methodology.id,
            code=methodology.code,
            name=methodology.name,
            version=methodology.version,
            description=methodology.description,
            status=methodology.status,
            governing_body=methodology.governing_body,
            reviewer_group=methodology.reviewer_group,
            published_at=methodology.published_at,
            effective_from=methodology.effective_from,
            effective_to=methodology.effective_to,
            is_development_only=methodology_is_development_only(methodology),
        )

    @classmethod
    def methodology_detail(cls, methodology: ShariaMethodology) -> MethodologyDetail:
        return MethodologyDetail(
            **cls.methodology_summary(methodology).model_dump(),
            rules=methodology.rules_json,
            evidence_requirements=methodology.evidence_requirements_json,
        )

    @staticmethod
    def assessment_summary(
        assessment: AssetShariaAssessment,
        methodology: ShariaMethodology,
        *,
        status_override: ShariaAssetStatus | None = None,
        summary_override: str | None = None,
    ) -> AssetAssessmentSummary:
        effective_status = status_override or assessment.status
        passport = dict(assessment.evidence_snapshot or {})
        factual_profile = dict(passport.get("hilalmarkets_factual_information_profile") or {})
        identity = dict(factual_profile.get("canonical_asset_identity") or {})
        provider_ids = dict(identity.get("provider_ids") or {})
        logo_url = str(provider_ids.get("logo_url") or "").strip() or None
        return AssetAssessmentSummary(
            id=assessment.id,
            canonical_asset=assessment.canonical_asset,
            asset_name=assessment.asset_name,
            methodology_id=methodology.id,
            methodology_name=methodology.name,
            methodology_version=methodology.version,
            status=effective_status,
            status_label=STATUS_LABELS[effective_status],
            summary=summary_override or assessment.summary,
            qualifications=assessment.qualifications,
            reviewed_by=assessment.reviewed_by,
            reviewed_at=assessment.reviewed_at,
            valid_from=assessment.valid_from,
            valid_until=assessment.valid_until,
            approved_status=assessment.status if status_override else None,
            safety_hold=status_override is not None,
            logo_url=logo_url,
        )

    @staticmethod
    def evidence_response(row: ShariaEvidenceSource) -> EvidenceSourceResponse:
        return EvidenceSourceResponse(
            id=row.id,
            source_type=row.source_type,
            title=row.title,
            publisher=row.publisher,
            source_url=row.source_url,
            published_at=row.published_at,
            retrieved_at=row.retrieved_at,
            evidence_category=row.evidence_category,
            evidence_summary=row.evidence_summary,
            source_hash=row.source_hash,
        )

    @staticmethod
    def history_response(row: AssetShariaStatusHistory) -> StatusHistoryResponse:
        return StatusHistoryResponse(
            id=row.id,
            canonical_asset=row.canonical_asset,
            methodology_id=row.methodology_id,
            previous_status=row.previous_status,
            new_status=row.new_status,
            reason_code=row.reason_code,
            reason_summary=row.reason_summary,
            changed_at=row.changed_at,
            approved_by=row.approved_by,
        )

    @staticmethod
    def _assert_effective(methodology: ShariaMethodology, as_of: datetime) -> None:
        if methodology.status != ShariaMethodologyStatus.ACTIVE:
            raise ShariaScreeningError(
                "methodology_not_active", "The selected methodology is not active."
            )
        if methodology.effective_from is None or _as_utc(methodology.effective_from) > _as_utc(
            as_of
        ):
            raise ShariaScreeningError(
                "methodology_not_effective", "The selected methodology is not yet effective."
            )
        if methodology.effective_to is not None and _as_utc(methodology.effective_to) <= _as_utc(
            as_of
        ):
            raise ShariaScreeningError(
                "methodology_expired",
                "The selected methodology version has expired and cannot power screening.",
            )
        if methodology_is_development_only(methodology):
            raise ShariaScreeningError(
                "development_methodology_not_executable",
                "The development/test methodology is not a religious ruling and cannot power "
                "production screened scans.",
            )
        ShariaScreeningService.validate_methodology_contract(
            methodology.rules_json,
            methodology.evidence_requirements_json,
        )

    @staticmethod
    def validate_methodology_contract(
        rules_json: dict,
        evidence_requirements_json: dict,
    ) -> tuple[MethodologyRulesDefinition, MethodologyEvidenceRequirements]:
        try:
            rules = MethodologyRulesDefinition.model_validate(rules_json)
            requirements = MethodologyEvidenceRequirements.model_validate(
                evidence_requirements_json
            )
        except ValidationError as exc:
            raise ShariaScreeningError(
                "methodology_contract_invalid",
                "The methodology does not define a complete versioned review contract.",
            ) from exc
        if not rules.executable:
            raise ShariaScreeningError(
                "methodology_not_executable",
                "The methodology contract is not approved for screening execution.",
            )
        criterion_categories = {
            category
            for criterion in rules.required_criteria
            if criterion.required
            for category in criterion.evidence_categories
        }
        missing_categories = criterion_categories.difference(
            requirements.mandatory_source_categories
        )
        if missing_categories:
            raise ShariaScreeningError(
                "methodology_evidence_contract_incomplete",
                "Mandatory evidence categories do not cover every required criterion.",
            )
        return rules, requirements
