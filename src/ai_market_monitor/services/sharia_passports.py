from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AIAnalysisSnapshot,
    Alert,
    AssetResearchDossier,
    AssetShariaAssessment,
    AssetShariaStatusHistory,
    AuditEvent,
    CanonicalAsset,
    DashboardPreference,
    ExchangeMarket,
    PublishedAssetAssessment,
    ReviewCase,
    ReviewDecision,
    SetupInstance,
    ShariaEvidenceSource,
    ShariaMethodology,
    ShariaPassportProblemReport,
    SourceSnapshot,
    User,
)
from ai_market_monitor.db.models.enums import ShariaAssetStatus
from ai_market_monitor.schemas.sharia import (
    AssetPassportResponse,
    PassportCriterionOutcome,
    PassportDecisionRecord,
    PassportEvidenceDetail,
    PassportExchangeMarket,
    PassportHistoricalContext,
    PassportHistoricalReference,
    PassportIdentity,
    PassportProblemReportRequest,
    PassportProblemReportResponse,
    PassportQuickViewResponse,
    PassportTimelineEntry,
    PassportUseCoverage,
)
from ai_market_monitor.services.sharia_screening import (
    DEFAULT_ALLOWED_STATUSES,
    STATUS_LABELS,
    ShariaScreeningError,
    ShariaScreeningService,
)


class ShariaPassportReadService:
    """Build the single customer-facing Passport read model from immutable records."""

    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings
        self.screening = ShariaScreeningService(session, settings)

    async def current(
        self,
        asset: str,
        *,
        methodology_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> AssetPassportResponse:
        base = await self.screening.passport(asset, methodology_id=methodology_id)
        publication_query = select(PublishedAssetAssessment).where(
            PublishedAssetAssessment.asset_assessment_id == base.assessment.id
        )
        if self.settings.is_deployed:
            publication_query = publication_query.where(
                or_(
                    and_(
                        PublishedAssetAssessment.is_active.is_(True),
                        PublishedAssetAssessment.publication_state == "published",
                    ),
                    PublishedAssetAssessment.publication_state == "safety_hold",
                )
            )
        publication = await self.session.scalar(
            publication_query.order_by(PublishedAssetAssessment.version.desc()).limit(1)
        )
        if self.settings.is_deployed and publication is None:
            raise ShariaScreeningError(
                "passport_not_published",
                "No published Passport record is available for this asset and methodology.",
            )
        return await self._enrich(base, publication=publication, user_id=user_id)

    async def historical(
        self,
        *,
        canonical_asset_id: UUID,
        passport_version_id: UUID,
        event_time: datetime | None = None,
        user_id: UUID | None = None,
    ) -> AssetPassportResponse:
        publication = await self.session.get(PublishedAssetAssessment, passport_version_id)
        if publication is None or publication.canonical_asset_id != canonical_asset_id:
            raise ShariaScreeningError(
                "passport_version_not_found",
                "The requested historical Passport version was not found.",
            )
        assessment = await self.session.get(
            AssetShariaAssessment, publication.asset_assessment_id
        )
        if assessment is None:
            raise ShariaScreeningError(
                "historical_assessment_missing",
                "The historical assessment reference is unavailable.",
            )
        methodology = await self.session.get(ShariaMethodology, assessment.methodology_id)
        if methodology is None:
            raise ShariaScreeningError(
                "historical_methodology_missing",
                "The historical methodology reference is unavailable.",
            )
        base = await self._base_from_assessment(
            assessment=assessment,
            methodology=methodology,
            snapshot=dict(publication.passport_snapshot or {}),
        )
        current = await self.screening.effective_assessment(
            assessment.methodology_id, assessment.canonical_asset
        )
        current_publication = None
        if current is not None:
            current_publication = await self.session.scalar(
                select(PublishedAssetAssessment)
                .where(
                    PublishedAssetAssessment.asset_assessment_id == current.id,
                    PublishedAssetAssessment.is_active.is_(True),
                )
                .limit(1)
            )
        base.historical = PassportHistoricalContext(
            is_historical=True,
            event_time=event_time,
            passport_version_id=publication.id,
            passport_version=publication.version,
            current_status=current.status if current else None,
            current_reviewed_at=current.reviewed_at if current else None,
            current_passport_url=(
                f"/dashboard/market/{current.canonical_asset}"
                if current is not None and current_publication is not None
                else None
            ),
        )
        enriched = await self._enrich(
            base,
            publication=publication,
            user_id=user_id,
        )
        current_status = enriched.historical.current_status
        current_passport = (
            await self.current(
                current.canonical_asset,
                methodology_id=current.methodology_id,
                user_id=user_id,
            )
            if current is not None and current_publication is not None
            else None
        )
        enriched.can_create_watch_plan = bool(
            current_status in DEFAULT_ALLOWED_STATUSES
            and current_passport is not None
            and current_passport.can_create_watch_plan
        )
        if not enriched.can_create_watch_plan:
            enriched.restriction_explanation = (
                "The historical version remains available as evidence, but a new Watch Plan "
                "cannot use it because the current status is unavailable or restricted."
            )
        return enriched

    async def quick_view(
        self,
        asset: str,
        *,
        methodology_id: UUID | None = None,
        passport_version_id: UUID | None = None,
        canonical_asset_id: UUID | None = None,
        event_time: datetime | None = None,
        user_id: UUID | None = None,
    ) -> PassportQuickViewResponse:
        if passport_version_id is not None:
            if canonical_asset_id is None:
                publication = await self.session.get(
                    PublishedAssetAssessment, passport_version_id
                )
                if publication is None:
                    raise ShariaScreeningError(
                        "passport_version_not_found",
                        "The requested historical Passport version was not found.",
                    )
                canonical_asset_id = publication.canonical_asset_id
            passport = await self.historical(
                canonical_asset_id=canonical_asset_id,
                passport_version_id=passport_version_id,
                event_time=event_time,
                user_id=user_id,
            )
        else:
            passport = await self.current(
                asset, methodology_id=methodology_id, user_id=user_id
            )
        if passport.identity is None:
            raise ShariaScreeningError(
                "identity_evidence_unavailable",
                "Verified canonical identity evidence is unavailable for this Passport.",
            )
        historical_path = (
            f"/passports/{passport.identity.canonical_asset_id}/versions/"
            f"{passport.passport_version_id}"
            if passport.historical.is_historical
            and passport.identity.canonical_asset_id
            and passport.passport_version_id
            else f"/dashboard/market/{passport.assessment.canonical_asset}"
        )
        return PassportQuickViewResponse(
            identity=passport.identity,
            assessment=passport.assessment,
            primary_wording=(
                f"Screened as {passport.assessment.status_label} under "
                f"{passport.assessment.methodology_name}, version "
                f"{passport.assessment.methodology_version}, reviewed "
                f"{passport.assessment.reviewed_at.date().isoformat()}."
            ),
            main_reasons=passport.main_reasons[:4],
            main_qualification=passport.main_qualification,
            freshness=passport.freshness,
            next_review_at=passport.next_review_at,
            evidence_expires_at=passport.evidence_expires_at,
            source_scan_frequency_hours=passport.source_scan_frequency_hours,
            review_authority=passport.assessment.reviewed_by,
            decision_date=passport.decision_date,
            publication_date=passport.publication_date,
            use_coverage=passport.use_coverage[:5],
            historical=passport.historical,
            passport_version_id=passport.passport_version_id,
            passport_version=passport.passport_version,
            official_source_url=passport.official_source_url,
            full_passport_url=historical_path,
            evidence_reference=(
                passport.integrity_hash
                or f"assessment:{passport.assessment.id}"
            ),
            can_create_watch_plan=passport.can_create_watch_plan,
            watchlist_action_url=(
                f"/dashboard/market/{passport.assessment.canonical_asset}/watchlist"
                if passport.can_create_watch_plan
                else None
            ),
            compliance_change_url=(
                "/dashboard/compliance?asset="
                f"{passport.assessment.canonical_asset}"
                if passport.historical.is_historical
                and passport.historical.current_status is not None
                and passport.historical.current_status != passport.assessment.status
                else None
            ),
            restriction_explanation=passport.restriction_explanation,
        )

    async def report_problem(
        self,
        *,
        user_id: UUID,
        canonical_asset_id: UUID,
        payload: PassportProblemReportRequest,
    ) -> PassportProblemReportResponse:
        asset = await self.session.get(CanonicalAsset, canonical_asset_id)
        if asset is None:
            raise ShariaScreeningError("asset_not_found", "The asset was not found.")
        publication = None
        if payload.passport_version_id:
            publication = await self.session.get(
                PublishedAssetAssessment, payload.passport_version_id
            )
            if publication is None or publication.canonical_asset_id != asset.id:
                raise ShariaScreeningError(
                    "passport_version_not_found",
                    "The Passport version does not belong to this asset.",
                )
        else:
            publication = await self.session.scalar(
                select(PublishedAssetAssessment)
                .where(
                    PublishedAssetAssessment.canonical_asset_id == asset.id,
                    PublishedAssetAssessment.is_active.is_(True),
                )
                .order_by(PublishedAssetAssessment.version.desc())
                .limit(1)
            )
        assessment = (
            await self.session.get(AssetShariaAssessment, publication.asset_assessment_id)
            if publication
            else None
        )
        row = ShariaPassportProblemReport(
            reporter_user_id=user_id,
            canonical_asset_id=asset.id,
            asset_assessment_id=(publication.asset_assessment_id if publication else None),
            passport_version_id=publication.id if publication else None,
            report_type=payload.report_type,
            details=payload.details.strip(),
            state="open",
        )
        self.session.add(row)
        await self.session.flush()
        now = datetime.now(UTC)
        case = ReviewCase(
            case_reference=f"USR-{asset.symbol[:8]}-{str(row.id)[:8].upper()}",
            case_type="user_factual_report",
            state="ready_for_review",
            publication_state="published_unchanged",
            canonical_asset_id=asset.id,
            external_assessment_id=(publication.external_assessment_id if publication else None),
            dossier_id=publication.dossier_id if publication else None,
            methodology_id=assessment.methodology_id if assessment else None,
            title=f"Factual Passport report: {asset.name} ({asset.symbol})",
            priority="normal",
            risk_severity=(
                "high"
                if payload.report_type in {"wrong_asset_identity", "broken_source"}
                else "normal"
            ),
            human_review_reason=payload.details.strip(),
            requested_evidence=[payload.report_type],
            admin_notes=[],
            idempotency_key=f"passport-problem:{row.id}",
            due_at=now + timedelta(hours=self.settings.sharia_review_sla_hours),
            next_reminder_at=now
            + timedelta(hours=self.settings.sharia_review_reminder_hours),
        )
        self.session.add(case)
        await self.session.flush()
        row.review_case_id = case.id
        self.session.add(
            AuditEvent(
                actor_user_id=user_id,
                actor_type="user",
                action="sharia.passport_problem_reported",
                target_type="sharia_review_case",
                target_id=str(case.id),
                metadata_redacted={
                    "report_id": str(row.id),
                    "canonical_asset_id": str(asset.id),
                    "passport_version_id": str(publication.id) if publication else None,
                    "report_type": payload.report_type,
                },
                created_at=now,
            )
        )
        from ai_market_monitor.services.sharia_governance import (
            ShariaAdminTelegramService,
        )

        await ShariaAdminTelegramService(self.session, self.settings).enqueue(
            case,
            notification_type="user_factual_report",
            idempotency_key=f"passport-problem:{row.id}",
        )
        return PassportProblemReportResponse(
            id=row.id,
            state=row.state,
            created_at=row.created_at,
            message=(
                "Your factual report was recorded for review. The published status was not "
                "changed automatically."
            ),
        )

    async def _base_from_assessment(
        self,
        *,
        assessment: AssetShariaAssessment,
        methodology: ShariaMethodology,
        snapshot: dict,
    ) -> AssetPassportResponse:
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
                        AssetShariaStatusHistory.canonical_asset
                        == assessment.canonical_asset,
                        AssetShariaStatusHistory.methodology_id == methodology.id,
                    )
                    .order_by(AssetShariaStatusHistory.changed_at.desc())
                )
            ).all()
        )
        return AssetPassportResponse(
            assessment=self.screening.assessment_summary(assessment, methodology),
            why_this_status=assessment.summary,
            official_sc_malaysia_reference=dict(
                snapshot.get("official_sc_malaysia_reference") or {}
            ),
            hilalmarkets_factual_information_profile=dict(
                snapshot.get("hilalmarkets_factual_information_profile") or {}
            ),
            separate_use_status={
                str(key): value
                for key, value in dict(snapshot.get("separate_use_status") or {}).items()
            },
            reviewed_dimensions=list(snapshot.get("reviewed_dimensions") or []),
            methodology_result=dict(snapshot.get("methodology_result") or {}),
            evidence_sources=[self.screening.evidence_response(row) for row in evidence],
            status_history=[self.screening.history_response(row) for row in history],
            evidence_available=bool(evidence),
            notice=(
                f"Screened as {STATUS_LABELS[assessment.status].lower()} under "
                f"{methodology.name}, version {methodology.version}, reviewed "
                f"{assessment.reviewed_at.date().isoformat()}. This status is specific to "
                "the stated methodology and evidence date."
            ),
        )

    async def _enrich(
        self,
        base: AssetPassportResponse,
        *,
        publication: PublishedAssetAssessment | None,
        user_id: UUID | None = None,
    ) -> AssetPassportResponse:
        snapshot = dict(
            publication.passport_snapshot
            if publication is not None
            else base.assessment.model_dump().get("evidence_snapshot") or {}
        )
        if not snapshot:
            snapshot = {
                "official_sc_malaysia_reference": base.official_sc_malaysia_reference,
                "hilalmarkets_factual_information_profile": (
                    base.hilalmarkets_factual_information_profile
                ),
                "separate_use_status": base.separate_use_status,
                "reviewed_dimensions": base.reviewed_dimensions,
            }
        asset = None
        markets: list[ExchangeMarket] = []
        decision = None
        dossier = None
        analysis = None
        source_snapshots: list[SourceSnapshot] = []
        publisher = None
        if publication is not None:
            asset = await self.session.get(CanonicalAsset, publication.canonical_asset_id)
            markets = list(
                (
                    await self.session.scalars(
                        select(ExchangeMarket)
                        .where(ExchangeMarket.canonical_asset_id == publication.canonical_asset_id)
                        .order_by(ExchangeMarket.exchange, ExchangeMarket.market_symbol)
                    )
                ).all()
            )
            decision = await self.session.get(ReviewDecision, publication.review_decision_id)
            dossier = await self.session.get(AssetResearchDossier, publication.dossier_id)
            publisher = await self.session.get(User, publication.published_by_user_id)
            if dossier is not None:
                analysis = await self.session.scalar(
                    select(AIAnalysisSnapshot)
                    .where(
                        AIAnalysisSnapshot.dossier_id == dossier.id,
                        AIAnalysisSnapshot.status == "completed",
                    )
                    .order_by(AIAnalysisSnapshot.analysis_version.desc())
                    .limit(1)
                )
                snapshot_ids = []
                for value in dossier.source_snapshot_ids:
                    try:
                        snapshot_ids.append(UUID(str(value)))
                    except ValueError:
                        continue
                if snapshot_ids:
                    source_snapshots = list(
                        (
                            await self.session.scalars(
                                select(SourceSnapshot).where(SourceSnapshot.id.in_(snapshot_ids))
                            )
                        ).all()
                    )

        identity = self._identity(base, asset, markets)
        profile = dict(base.hilalmarkets_factual_information_profile or {})
        official = dict(base.official_sc_malaysia_reference or {})
        next_review = _parse_datetime(
            profile.get("next_governance_review_at") or profile.get("next_review_date")
        )
        evidence_expires_at = _parse_datetime(profile.get("evidence_expires_at"))
        source_scan_frequency_hours = profile.get("source_monitor_scan_frequency_hours")
        last_verified = _parse_datetime(profile.get("last_evidence_verification"))
        freshness = _freshness(
            status=base.assessment.status,
            next_review_at=min(
                [value for value in (next_review, evidence_expires_at) if value is not None],
                default=None,
            ),
            valid_until=base.assessment.valid_until,
        )
        reasons = list(snapshot.get("key_reasons") or [])
        if not reasons:
            reasons = [base.why_this_status]
        reasons = [str(item).strip() for item in reasons if str(item).strip()][:4]
        qualification = (
            base.assessment.qualifications[0]
            if base.assessment.qualifications
            else None
        )
        use_coverage = self._use_coverage(base, last_verified)
        criteria = self._criteria(base, decision, last_verified)
        evidence_details = self._evidence_details(base, source_snapshots, analysis)
        decision_record = await self._decision_record(
            base=base,
            publication=publication,
            decision=decision,
            publisher=publisher,
        )
        timeline = self._timeline(base, publication, decision_record)
        references = await self._historical_references(publication, user_id=user_id)
        spot_scope = dict(base.separate_use_status or {}).get(
            "spot_ownership_and_monitoring"
        )
        spot_scope_decision = (
            str(spot_scope.get("decision") or "")
            if isinstance(spot_scope, dict)
            else ""
        )
        user_allowed_statuses = await self._user_allowed_statuses(user_id)
        active_spot_market = any(
            row.is_active and row.market_type.casefold() == "spot" for row in markets
        )
        identity_verified = bool(identity and identity.identity_state == "verified")
        can_create = bool(
            base.assessment.status in DEFAULT_ALLOWED_STATUSES
            and base.assessment.status in user_allowed_statuses
            and publication is not None
            and publication.is_active
            and identity_verified
            and active_spot_market
            and spot_scope_decision in {"covered", "qualified"}
        )
        base.identity = identity
        base.freshness = freshness
        base.next_review_at = next_review
        base.evidence_expires_at = evidence_expires_at
        base.source_scan_frequency_hours = (
            int(source_scan_frequency_hours)
            if isinstance(source_scan_frequency_hours, int | float)
            else None
        )
        base.last_verified_at = last_verified or base.assessment.reviewed_at
        base.decision_date = (
            _parse_datetime(official.get("decision_date"))
            or (decision.created_at if decision else base.assessment.reviewed_at)
        )
        base.publication_date = publication.published_at if publication else None
        base.main_reasons = reasons
        base.main_qualification = qualification
        base.use_coverage = use_coverage
        base.criteria = criteria
        base.evidence_details = evidence_details
        base.decision_record = decision_record
        base.timeline = timeline
        base.historical_references = references
        base.passport_version_id = publication.id if publication else None
        base.passport_version = publication.version if publication else None
        base.integrity_hash = publication.integrity_hash if publication else None
        base.official_source_url = str(official.get("source_url") or "") or None
        base.can_create_watch_plan = can_create
        base.restriction_explanation = (
            None
            if can_create
            else (
                "A Watch Plan requires an allowed current status, an active exact spot-market "
                "mapping, explicit reviewed spot-use coverage, permission under your selected "
                "screening policy, and no safety hold."
            )
        )
        return base

    async def _user_allowed_statuses(
        self,
        user_id: UUID | None,
    ) -> set[ShariaAssetStatus]:
        if user_id is None:
            return set(DEFAULT_ALLOWED_STATUSES)
        preference = await self.session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user_id)
        )
        if preference is None:
            return set(DEFAULT_ALLOWED_STATUSES)
        values = dict(preference.notification_preferences or {})
        nested = values.get("sharia")
        policy = nested if isinstance(nested, dict) else {}
        raw = policy.get("allowed_statuses", values.get("allowed_sharia_statuses"))
        if raw is None:
            return set(DEFAULT_ALLOWED_STATUSES)
        if not isinstance(raw, list):
            return set()
        allowed: set[ShariaAssetStatus] = set()
        for value in raw:
            try:
                allowed.add(ShariaAssetStatus(str(value)))
            except ValueError:
                continue
        return allowed

    async def _historical_references(
        self,
        publication: PublishedAssetAssessment | None,
        *,
        user_id: UUID | None,
    ) -> list[PassportHistoricalReference]:
        if publication is None or user_id is None:
            return []
        alerts = list(
            (
                await self.session.scalars(
                    select(Alert)
                    .where(
                        Alert.user_id == user_id,
                        Alert.sharia_passport_version_id == publication.id,
                    )
                    .order_by(Alert.created_at.desc())
                    .limit(25)
                )
            ).all()
        )
        opportunities = list(
            (
                await self.session.scalars(
                    select(SetupInstance)
                    .where(
                        SetupInstance.user_id == user_id,
                        SetupInstance.sharia_passport_version_id == publication.id,
                    )
                    .order_by(SetupInstance.last_evaluated_at.desc())
                    .limit(25)
                )
            ).all()
        )
        rows = [
            PassportHistoricalReference(
                reference_type="alert",
                reference_id=row.id,
                label=row.title,
                event_time=row.candle_timestamp or row.created_at,
                url=f"/dashboard/alerts/{row.id}/proof",
                strategy_version_id=row.strategy_version_id,
            )
            for row in alerts
        ]
        rows.extend(
            PassportHistoricalReference(
                reference_type="opportunity",
                reference_id=row.id,
                label=f"{row.symbol} · {row.state.value.replace('_', ' ').title()}",
                event_time=row.last_evaluated_at,
                url=f"/dashboard/activity?setup={row.id}",
                strategy_version_id=row.strategy_version_id,
            )
            for row in opportunities
        )
        return sorted(rows, key=lambda item: item.event_time, reverse=True)

    @staticmethod
    def _identity(
        base: AssetPassportResponse,
        asset: CanonicalAsset | None,
        markets: list[ExchangeMarket],
    ) -> PassportIdentity:
        if asset is None:
            return PassportIdentity(
                name=base.assessment.asset_name or base.assessment.canonical_asset,
                symbol=base.assessment.canonical_asset,
                identity_state="historical_mapping_unavailable",
            )
        evidence = dict(asset.mapping_evidence or {})
        aliases = [str(value) for value in evidence.get("aliases", []) if value]
        return PassportIdentity(
            canonical_asset_id=asset.id,
            name=asset.name,
            symbol=asset.symbol,
            network=asset.native_chain,
            asset_type=asset.asset_type,
            native_asset=asset.asset_type.casefold() in {"native", "coin", "native_asset"},
            contract_addresses=dict(asset.contract_addresses or {}),
            official_website=asset.official_website,
            official_documentation=asset.official_documentation,
            provider_ids=dict(asset.provider_ids or {}),
            exchange_markets=[
                PassportExchangeMarket(
                    exchange=row.exchange,
                    market_symbol=row.market_symbol,
                    quote_asset=row.quote_asset,
                    market_type=row.market_type,
                    is_active=row.is_active,
                )
                for row in markets
            ],
            identity_state=asset.mapping_state,
            identity_verified_at=_parse_datetime(evidence.get("verified_at")),
            aliases=aliases,
        )

    @staticmethod
    def _use_coverage(
        base: AssetPassportResponse, verified_at: datetime | None
    ) -> list[PassportUseCoverage]:
        raw = dict(base.separate_use_status or {})
        rows: list[PassportUseCoverage] = []
        for key, value in raw.items():
            normalized_key = str(key)
            supporting_reference: str | None
            if isinstance(value, dict):
                status = str(value.get("decision") or "under_review")
                reason = str(value.get("reason") or "No reviewer reason was retained.")
                label = str(
                    value.get("label")
                    or normalized_key.replace("_", " ").title()
                )
                supporting_reference = "Reviewer-approved use decision"
                row_verified_at = _parse_datetime(value.get("verified_at"))
                source_ids = [str(item) for item in value.get("source_snapshot_ids", [])]
                criterion_ids = [str(item) for item in value.get("criterion_keys", [])]
                scope = str(value.get("scope") or "") or None
                try:
                    reviewer_user_id = UUID(str(value["reviewer_user_id"]))
                except (KeyError, TypeError, ValueError):
                    reviewer_user_id = None
            else:
                status, reason = _coverage_status(
                    str(value or "not_covered_by_this_decision")
                )
                label = normalized_key.replace("_", " ").title()
                supporting_reference = "Legacy retained use decision"
                row_verified_at = None
                source_ids = []
                criterion_ids = []
                scope = None
                reviewer_user_id = None
            rows.append(
                PassportUseCoverage(
                    key=normalized_key,
                    label=label,
                    status=status,
                    reason=reason,
                    supporting_reference=supporting_reference,
                    last_verified_at=(
                        row_verified_at or verified_at or base.assessment.reviewed_at
                    ),
                    source_ids=source_ids,
                    criterion_ids=criterion_ids,
                    scope=scope,
                    reviewer_user_id=reviewer_user_id,
                )
            )
        return rows

    @staticmethod
    def _criteria(
        base: AssetPassportResponse,
        decision: ReviewDecision | None,
        verified_at: datetime | None,
    ) -> list[PassportCriterionOutcome]:
        source = list(decision.criterion_decisions or []) if decision else []
        if not source:
            source = list(base.reviewed_dimensions or [])
        rows: list[PassportCriterionOutcome] = []
        for index, item in enumerate(source):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("name") or f"Criterion {index + 1}")
            rows.append(
                PassportCriterionOutcome(
                    key=str(item.get("key") or label.casefold().replace(" ", "_")),
                    label=label,
                    outcome=str(item.get("outcome") or item.get("result") or "under_review"),
                    evidence=[str(value) for value in item.get("evidence", [])],
                    reviewer_explanation=item.get("reviewer_explanation")
                    or item.get("summary"),
                    ai_factual_summary=item.get("ai_factual_summary"),
                    known_gaps=[str(value) for value in item.get("known_gaps", [])],
                    contradictions=[str(value) for value in item.get("contradictions", [])],
                    verified_at=verified_at or base.assessment.reviewed_at,
                )
            )
        return rows

    @staticmethod
    def _evidence_details(
        base: AssetPassportResponse,
        snapshots: list[SourceSnapshot],
        analysis: AIAnalysisSnapshot | None,
    ) -> list[PassportEvidenceDetail]:
        by_url = {row.source_url: row for row in snapshots}
        rows = []
        for source in base.evidence_sources:
            snapshot = by_url.get(source.source_url)
            rows.append(
                PassportEvidenceDetail(
                    **source.model_dump(),
                    snapshot_id=snapshot.id if snapshot else None,
                    content_hash=(snapshot.content_hash if snapshot else source.source_hash),
                    parser_version=snapshot.scraper_version if snapshot else None,
                    ai_extraction_version=(
                        f"{analysis.model}:{analysis.prompt_version}" if analysis else None
                    ),
                    availability=(
                        "available"
                        if snapshot is None or snapshot.fetch_status == "success"
                        else "live_link_unavailable_snapshot_preserved"
                    ),
                    supports_criteria=[source.evidence_category],
                )
            )
        return rows

    async def _decision_record(
        self,
        *,
        base: AssetPassportResponse,
        publication: PublishedAssetAssessment | None,
        decision: ReviewDecision | None,
        publisher: User | None,
    ) -> PassportDecisionRecord | None:
        if publication is None or decision is None:
            return None
        reviewer = await self.session.get(User, decision.admin_user_id)
        return PassportDecisionRecord(
            review_case_id=decision.review_case_id,
            decision_id=decision.id,
            reviewer_user_id=decision.admin_user_id,
            reviewer_display_name=(
                reviewer.display_name
                if reviewer and reviewer.display_name
                else base.assessment.reviewed_by
            ),
            actor_role=decision.actor_role,
            methodology_version=decision.methodology_version,
            methodology_criteria_version=decision.methodology_criteria_version,
            methodology_criteria_hash=decision.methodology_criteria_hash,
            decision=decision.decision,
            reason=decision.reason,
            qualifications=list(decision.qualifications or base.assessment.qualifications),
            evidence_snapshot_ids=list(decision.evidence_snapshot_ids or []),
            criterion_decisions=list(decision.criterion_decisions or []),
            use_case_decisions=list(decision.use_case_decisions or []),
            acknowledged_gaps=list(decision.acknowledged_gaps or []),
            decided_at=decision.created_at,
            published_by_user_id=publication.published_by_user_id,
            published_at=publication.published_at,
            integrity_hash=decision.integrity_hash or publication.integrity_hash,
        )

    @staticmethod
    def _timeline(
        base: AssetPassportResponse,
        publication: PublishedAssetAssessment | None,
        decision: PassportDecisionRecord | None,
    ) -> list[PassportTimelineEntry]:
        rows = [
            PassportTimelineEntry(
                action="status_changed",
                actor=item.approved_by,
                occurred_at=item.changed_at,
                reason=item.reason_summary,
                previous_state=item.previous_status.value if item.previous_status else None,
                new_state=item.new_status.value,
                passport_version_id=(publication.id if publication else None),
            )
            for item in base.status_history
        ]
        if decision and decision.decided_at:
            rows.append(
                PassportTimelineEntry(
                    action="reviewer_decision",
                    actor=decision.reviewer_display_name,
                    occurred_at=decision.decided_at,
                    reason=decision.reason,
                    new_state=decision.decision,
                    related_source_ids=decision.evidence_snapshot_ids,
                    passport_version_id=publication.id if publication else None,
                )
            )
        if publication:
            rows.append(
                PassportTimelineEntry(
                    action="published",
                    actor=str(publication.published_by_user_id),
                    occurred_at=publication.published_at,
                    reason="The reviewed decision was published as an immutable Passport version.",
                    new_state=publication.publication_state,
                    passport_version_id=publication.id,
                )
            )
        return sorted(
            rows,
            key=lambda item: _parse_datetime(item.occurred_at)
            or datetime.min.replace(tzinfo=UTC),
        )


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _freshness(
    *,
    status: ShariaAssetStatus,
    next_review_at: datetime | None,
    valid_until: datetime | None,
) -> str:
    if status == ShariaAssetStatus.UNDER_REVIEW:
        return "under_review"
    now = datetime.now(UTC)
    deadline = next_review_at or valid_until
    if deadline is None:
        return "current"
    deadline = deadline if deadline.tzinfo else deadline.replace(tzinfo=UTC)
    if deadline < now:
        return "stale"
    if (deadline - now).total_seconds() <= 7 * 86400:
        return "review_due"
    return "current"


def _coverage_status(value: str) -> tuple[str, str]:
    normalized = value.casefold()
    if normalized in {"included", "covered", "shariah_compliant"}:
        return "covered", "Included within the recorded decision scope."
    if (
        normalized == "qualified"
        or "qualification" in normalized
        or "information_only" in normalized
    ):
        return "covered_with_qualification", "Separate terms or evidence still require review."
    if "under_review" in normalized:
        return "under_review", "This use is currently under review."
    if "outside" in normalized or "excluded" in normalized:
        return "excluded", "This use is outside HilalMarkets' crypto spot monitoring scope."
    if "not_applicable" in normalized:
        return "not_applicable", "This use does not apply to this asset identity."
    return "not_covered", "The asset-level decision does not approve this separate use."
