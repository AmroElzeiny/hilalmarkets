import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import HttpUrl
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AIAnalysisSnapshot,
    AssetResearchDossier,
    AuditEvent,
    CanonicalAsset,
    ExternalAssessment,
    OfficialSource,
    PublishedAssetAssessment,
    ReviewCase,
    ReviewDecision,
    ShariaMethodology,
    ShariaUniverseSnapshot,
    SourceSnapshot,
    TelegramNotificationAttempt,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    IdentityProvider,
    ShariaAssetStatus,
    ShariaMethodologyStatus,
    UserRole,
)
from ai_market_monitor.schemas.sharia import AssessmentCreateRequest, EvidenceSourceInput
from ai_market_monitor.services.sharia_screening import ShariaScreeningService
from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter
from ai_market_monitor.telegram.types import TelegramButton, TelegramOutboundMessage

SC_METHODOLOGY_CODE = "SC_MALAYSIA_SAC_REFERENCE"
TERMINAL_CASE_STATES = {"approved", "rejected"}
OPEN_REMINDER_STATES = {"ready_for_review", "needs_evidence"}


class ShariaGovernanceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ShariaGovernanceService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def approve_and_publish(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> PublishedAssetAssessment:
        admin = await self._require_admin(admin_user_id)
        case = await self._open_case(case_id)
        if case.state != "ready_for_review":
            raise ShariaGovernanceError(
                "case_not_ready", "Only a research-complete case can be approved."
            )
        if len(reason.strip()) < 10:
            raise ShariaGovernanceError(
                "decision_reason_required", "Provide a clear decision reason."
            )
        if not case.canonical_asset_id or not case.external_assessment_id or not case.dossier_id:
            raise ShariaGovernanceError(
                "case_evidence_incomplete", "Canonical identity and research evidence are required."
            )
        asset = await self.session.get(CanonicalAsset, case.canonical_asset_id)
        external = await self.session.get(ExternalAssessment, case.external_assessment_id)
        dossier = await self.session.get(AssetResearchDossier, case.dossier_id)
        if asset is None or external is None or dossier is None:
            raise ShariaGovernanceError("case_evidence_missing", "Review evidence is missing.")
        if asset.mapping_state != "verified" or external.mapping_state != "mapped":
            raise ShariaGovernanceError(
                "identity_not_verified", "Canonical identity must be verified before publication."
            )
        analysis = await self.session.scalar(
            select(AIAnalysisSnapshot)
            .where(
                AIAnalysisSnapshot.dossier_id == dossier.id,
                AIAnalysisSnapshot.status == "completed",
            )
            .order_by(AIAnalysisSnapshot.analysis_version.desc())
            .limit(1)
        )
        if analysis is None:
            raise ShariaGovernanceError(
                "validated_analysis_missing", "A schema-valid factual dossier is required."
            )
        methodology = await self._sc_methodology()
        now = datetime.now(UTC)
        evidence_ids = [str(value) for value in dossier.source_snapshot_ids]
        decision = await self._decision(
            case,
            admin_user_id=admin.id,
            methodology_id=methodology.id,
            decision="approve_and_publish",
            reason=reason,
            evidence_snapshot_ids=evidence_ids,
        )
        passport = await self._passport_snapshot(
            asset=asset,
            external=external,
            dossier=dossier,
            analysis=analysis,
            methodology=methodology,
            decision=decision,
            published_at=now,
        )
        evidence_sources = await self._assessment_sources(external, dossier)
        reviewer = await self._admin_label(admin)
        assessment = await ShariaScreeningService(
            self.session, self.settings
        ).create_assessment(
            AssessmentCreateRequest(
                canonical_asset=asset.symbol,
                asset_name=asset.name,
                methodology_id=methodology.id,
                status=ShariaAssetStatus.ELIGIBLE,
                summary=(
                    "SC Malaysia SAC reference: Shariah-compliant. This is scoped to the "
                    "Securities Commission Malaysia regulated digital-assets framework."
                ),
                qualifications=[
                    "Spot ownership and monitoring only; related financial products and uses "
                    "are assessed separately."
                ],
                exclusion_reasons=[],
                evidence_snapshot=passport,
                evidence_sources=evidence_sources,
                reviewed_by=reviewer,
                reviewed_at=now,
                valid_from=now,
                reason_code="sc_malaysia_admin_publication",
                reason_summary=reason,
            ),
            actor_user_id=admin.id,
        )
        prior = list(
            (
                await self.session.scalars(
                    select(PublishedAssetAssessment).where(
                        PublishedAssetAssessment.canonical_asset_id == asset.id,
                        PublishedAssetAssessment.is_active.is_(True),
                    )
                )
            ).all()
        )
        for row in prior:
            row.is_active = False
            row.withdrawn_at = now
        version = int(
            await self.session.scalar(
                select(func.coalesce(func.max(PublishedAssetAssessment.version), 0)).where(
                    PublishedAssetAssessment.canonical_asset_id == asset.id
                )
            )
            or 0
        ) + 1
        integrity_hash = _hash_json(
            {
                "asset_id": str(asset.id),
                "assessment_id": str(assessment.id),
                "decision_id": str(decision.id),
                "version": version,
                "passport": passport,
            }
        )
        publication = PublishedAssetAssessment(
            canonical_asset_id=asset.id,
            external_assessment_id=external.id,
            dossier_id=dossier.id,
            review_decision_id=decision.id,
            asset_assessment_id=assessment.id,
            version=version,
            publication_state="published",
            passport_snapshot=passport,
            integrity_hash=integrity_hash,
            is_active=True,
            published_by_user_id=admin.id,
            published_at=now,
        )
        self.session.add(publication)
        case.state = "approved"
        case.publication_state = "published"
        case.methodology_id = methodology.id
        case.done_at = now
        case.next_reminder_at = None
        await self.session.execute(
            update(ShariaUniverseSnapshot)
            .where(
                ShariaUniverseSnapshot.methodology_id == methodology.id,
                ShariaUniverseSnapshot.invalidated_at.is_(None),
            )
            .values(
                invalidated_at=now,
                invalidation_reason=f"published SC assessment {assessment.id}",
            )
        )
        self._audit(
            admin.id,
            "sharia.asset_approved_and_published",
            "published_asset_assessment",
            str(publication.id),
            {
                "case_id": str(case.id),
                "asset": asset.symbol,
                "version": version,
                "assessment_id": str(assessment.id),
                "decision_reason_recorded": True,
            },
        )
        await self.session.flush()
        await ShariaAdminTelegramService(self.session, self.settings).enqueue(
            case,
            notification_type="publication_success",
            idempotency_key=f"publication:{publication.id}",
        )
        return publication

    async def reject_and_store(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> ReviewDecision:
        admin = await self._require_admin(admin_user_id)
        case = await self._open_case(case_id)
        if len(reason.strip()) < 10:
            raise ShariaGovernanceError(
                "decision_reason_required", "Provide a clear decision reason."
            )
        decision = await self._decision(
            case,
            admin_user_id=admin.id,
            methodology_id=case.methodology_id,
            decision="reject_and_store",
            reason=reason,
            evidence_snapshot_ids=await self._case_evidence_ids(case),
        )
        now = datetime.now(UTC)
        case.state = "rejected"
        case.publication_state = "stored_not_published"
        case.done_at = now
        case.next_reminder_at = None
        self._audit(
            admin.id,
            "sharia.asset_rejected_and_stored",
            "sharia_review_case",
            str(case.id),
            {
                "decision_id": str(decision.id),
                "decision_reason_recorded": True,
                "public_assessment_created": False,
            },
        )
        await ShariaAdminTelegramService(self.session, self.settings).enqueue(
            case,
            notification_type="rejection_stored",
            idempotency_key=f"rejection:{decision.id}",
        )
        await self.session.flush()
        return decision

    async def request_more_evidence(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
        requested_evidence: list[str],
    ) -> ReviewDecision:
        admin = await self._require_admin(admin_user_id)
        case = await self._open_case(case_id)
        if len(reason.strip()) < 10 or not requested_evidence:
            raise ShariaGovernanceError(
                "evidence_request_required",
                "Provide a reason and at least one specific evidence request.",
            )
        decision = await self._decision(
            case,
            admin_user_id=admin.id,
            methodology_id=case.methodology_id,
            decision="request_more_evidence",
            reason=reason,
            evidence_snapshot_ids=await self._case_evidence_ids(case),
        )
        case.state = "needs_evidence"
        case.requested_evidence = requested_evidence
        case.next_reminder_at = datetime.now(UTC) + timedelta(
            hours=self.settings.sharia_review_reminder_hours
        )
        self._audit(
            admin.id,
            "sharia.more_evidence_requested",
            "sharia_review_case",
            str(case.id),
            {"decision_id": str(decision.id), "request_count": len(requested_evidence)},
        )
        await self.session.flush()
        return decision

    async def return_to_research(
        self, case_id: UUID, *, admin_user_id: UUID, reason: str
    ) -> ReviewDecision:
        admin = await self._require_admin(admin_user_id)
        case = await self._open_case(case_id)
        if len(reason.strip()) < 10:
            raise ShariaGovernanceError(
                "decision_reason_required", "Provide a clear return-to-research reason."
            )
        decision = await self._decision(
            case,
            admin_user_id=admin.id,
            methodology_id=case.methodology_id,
            decision="return_to_research",
            reason=reason,
            evidence_snapshot_ids=await self._case_evidence_ids(case),
        )
        case.state = "researching"
        case.publication_state = "unpublished"
        case.next_reminder_at = None
        await self.session.flush()
        return decision

    async def add_admin_note(
        self, case_id: UUID, *, admin_user_id: UUID, note: str
    ) -> ReviewCase:
        admin = await self._require_admin(admin_user_id)
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        if len(note.strip()) < 3:
            raise ShariaGovernanceError("note_required", "Enter a useful note.")
        notes = list(case.admin_notes or [])
        notes.append(
            {
                "admin_user_id": str(admin.id),
                "note": note.strip(),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        case.admin_notes = notes
        self._audit(
            admin.id,
            "sharia.admin_note_added",
            "sharia_review_case",
            str(case.id),
            {"note_recorded": True},
        )
        await self.session.flush()
        return case

    async def _require_admin(self, user_id: UUID) -> User:
        user = await self.session.get(User, user_id)
        if user is None or user.role != UserRole.ADMIN:
            raise ShariaGovernanceError("admin_required", "Administrator role required.")
        return user

    async def _open_case(self, case_id: UUID) -> ReviewCase:
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        if case.state in TERMINAL_CASE_STATES or case.done_at is not None:
            raise ShariaGovernanceError(
                "case_already_decided", "This review already has a terminal decision."
            )
        return case

    async def _sc_methodology(self) -> ShariaMethodology:
        methodology = await self.session.scalar(
            select(ShariaMethodology)
            .where(
                ShariaMethodology.code == SC_METHODOLOGY_CODE,
                ShariaMethodology.status == ShariaMethodologyStatus.ACTIVE,
            )
            .order_by(ShariaMethodology.effective_from.desc())
            .limit(1)
        )
        if methodology is None:
            raise ShariaGovernanceError(
                "sc_methodology_inactive",
                "The versioned SC Malaysia reference methodology is not active.",
            )
        return methodology

    async def _decision(
        self,
        case: ReviewCase,
        *,
        admin_user_id: UUID,
        methodology_id: UUID | None,
        decision: str,
        reason: str,
        evidence_snapshot_ids: list[str],
    ) -> ReviewDecision:
        version = int(
            await self.session.scalar(
                select(func.coalesce(func.max(ReviewDecision.decision_version), 0)).where(
                    ReviewDecision.review_case_id == case.id
                )
            )
            or 0
        ) + 1
        row = ReviewDecision(
            review_case_id=case.id,
            admin_user_id=admin_user_id,
            methodology_id=methodology_id,
            decision=decision,
            reason=reason.strip(),
            evidence_snapshot_ids=evidence_snapshot_ids,
            decision_version=version,
            created_at=datetime.now(UTC),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def _case_evidence_ids(self, case: ReviewCase) -> list[str]:
        if case.dossier_id is None:
            return []
        dossier = await self.session.get(AssetResearchDossier, case.dossier_id)
        return list(dossier.source_snapshot_ids) if dossier else []

    async def _assessment_sources(
        self, external: ExternalAssessment, dossier: AssetResearchDossier
    ) -> list[EvidenceSourceInput]:
        sources = [
            EvidenceSourceInput(
                source_type="official_regulator",
                title="SC Malaysia Digital Assets",
                publisher="Securities Commission Malaysia",
                source_url=HttpUrl(external.source_url),
                published_at=datetime.combine(
                    external.decision_date, datetime.min.time(), tzinfo=UTC
                ),
                retrieved_at=external.retrieval_date,
                evidence_category="official_sc_reference",
                evidence_summary=(
                    f"Exact imported wording: {external.exact_status_wording}; "
                    f"{external.sac_meeting_number} SAC Meeting, "
                    f"{external.decision_date.isoformat()}."
                ),
            )
        ]
        snapshot_ids = [UUID(value) for value in dossier.source_snapshot_ids]
        if snapshot_ids:
            snapshots = list(
                (
                    await self.session.scalars(
                        select(SourceSnapshot).where(SourceSnapshot.id.in_(snapshot_ids))
                    )
                ).all()
            )
            official_ids = {
                row.official_source_id for row in snapshots if row.official_source_id is not None
            }
            official = {
                row.id: row
                for row in (
                    await self.session.scalars(
                        select(OfficialSource).where(OfficialSource.id.in_(official_ids))
                    )
                ).all()
            }
            for snapshot in snapshots:
                source = (
                    official.get(snapshot.official_source_id)
                    if snapshot.official_source_id is not None
                    else None
                )
                if source is None or snapshot.fetch_status != "success":
                    continue
                sources.append(
                    EvidenceSourceInput(
                        source_type="official_project_source",
                        title=source.title,
                        publisher="Official project source",
                        source_url=HttpUrl(snapshot.source_url),
                        retrieved_at=snapshot.retrieved_at,
                        evidence_category=source.category,
                        evidence_summary=(
                            "Official-source factual information captured for the HilalMarkets "
                            "profile; it is not SC Malaysia's unpublished reasoning."
                        ),
                    )
                )
        return sources

    async def _passport_snapshot(
        self,
        *,
        asset: CanonicalAsset,
        external: ExternalAssessment,
        dossier: AssetResearchDossier,
        analysis: AIAnalysisSnapshot,
        methodology: ShariaMethodology,
        decision: ReviewDecision,
        published_at: datetime,
    ) -> dict:
        output = dict(analysis.output or {})
        profile = dict(output.get("profile") or dossier.factual_profile or {})
        return {
            "passport_version": 1,
            "official_sc_malaysia_reference": {
                "label": "SC Malaysia SAC reference: Shariah-compliant",
                "exact_wording": external.exact_status_wording,
                "authority": external.source_authority,
                "sac_meeting_number": external.sac_meeting_number,
                "decision_date": external.decision_date.isoformat(),
                "source_url": external.source_url,
                "retrieval_date": external.retrieval_date.isoformat(),
                "regulatory_scope": external.regulatory_scope,
                "limitations": [
                    "Coin-specific detailed reasoning was not publicly provided by this source.",
                    "This reference does not automatically apply to staking, lending, yield, "
                    "leveraged, derivative, wrapped, or bridged uses.",
                ],
            },
            "hilalmarkets_factual_information_profile": {
                **profile,
                "official_source_snapshot_ids": list(dossier.source_snapshot_ids),
                "missing_information": output.get("missing_evidence") or [],
                "contradictions": output.get("contradictions") or [],
                "last_evidence_verification": dossier.completed_at.isoformat()
                if dossier.completed_at
                else None,
                "next_review_date": (
                    published_at + timedelta(hours=self.settings.sharia_source_scan_interval_hours)
                ).isoformat(),
                "notice": (
                    "HilalMarkets factual research is not SC Malaysia's unpublished reasoning "
                    "and is not an independent religious ruling."
                ),
            },
            "separate_use_status": {
                "asset_level_sc_reference": "shariah_compliant",
                "spot_ownership_and_monitoring": "included",
                "native_staking": "information_only_separate_review",
                "third_party_lending": "not_covered_by_asset_reference",
                "yield_products": "not_covered_by_asset_reference",
                "leveraged_products": "outside_hilalmarkets_spot_scope",
                "futures_perpetuals_derivatives": "outside_hilalmarkets_spot_scope",
                "wrapped_bridged_representations": "separate_identity_and_review_required",
            },
            "reviewed_dimensions": [
                {"label": "Asset identity", "result": asset.mapping_state},
                {"label": "Official SC reference", "result": external.exact_status_wording},
                {"label": "Official-source evidence", "result": dossier.state},
                {"label": "Human publication review", "result": decision.decision},
            ],
            "methodology_result": {
                "methodology_code": methodology.code,
                "methodology_version": methodology.version,
                "result": "SC Malaysia SAC reference: Shariah-compliant",
            },
            "publication": {
                "review_case_id": str(decision.review_case_id),
                "decision_id": str(decision.id),
                "published_at": published_at.isoformat(),
            },
        }

    async def _admin_label(self, admin: User) -> str:
        identity = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.user_id == admin.id,
                UserIdentity.provider == IdentityProvider.EMAIL,
            )
        )
        return (
            identity.display_identifier
            if identity and identity.display_identifier
            else admin.display_name or f"admin:{admin.id}"
        )

    def _audit(
        self,
        actor_user_id: UUID,
        action: str,
        target_type: str,
        target_id: str,
        metadata: dict,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="admin",
                action=action,
                target_type=target_type,
                target_id=target_id,
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )


class ShariaAdminTelegramService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        adapter: TelegramHttpAdapter | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.adapter = adapter

    async def enqueue(
        self,
        case: ReviewCase,
        *,
        notification_type: str,
        idempotency_key: str,
    ) -> TelegramNotificationAttempt | None:
        chat_id = (self.settings.sharia_admin_telegram_chat_id or "").strip()
        if not chat_id:
            return None
        existing = await self.session.scalar(
            select(TelegramNotificationAttempt).where(
                TelegramNotificationAttempt.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        attempt = TelegramNotificationAttempt(
            review_case_id=case.id,
            notification_type=notification_type,
            idempotency_key=idempotency_key,
            chat_id=chat_id,
            status="pending",
            next_retry_at=now,
            created_at=now,
        )
        self.session.add(attempt)
        if notification_type == "new_review_required":
            case.last_reminder_at = now
            case.next_reminder_at = now + timedelta(
                hours=self.settings.sharia_review_reminder_hours
            )
        await self.session.flush()
        return attempt

    async def enqueue_due_reminders(self) -> int:
        now = datetime.now(UTC)
        cases = list(
            (
                await self.session.scalars(
                    select(ReviewCase).where(
                        ReviewCase.state.in_(OPEN_REMINDER_STATES),
                        ReviewCase.done_at.is_(None),
                        ReviewCase.next_reminder_at.is_not(None),
                        ReviewCase.next_reminder_at <= now,
                    )
                )
            ).all()
        )
        created = 0
        window_seconds = self.settings.sharia_review_reminder_hours * 3600
        window = int(now.timestamp() // window_seconds)
        for case in cases:
            key = f"review-reminder:{case.id}:{window}"
            existing = await self.session.scalar(
                select(TelegramNotificationAttempt).where(
                    TelegramNotificationAttempt.idempotency_key == key
                )
            )
            attempt = await self.enqueue(
                case,
                notification_type=(
                    "new_review_required"
                    if case.last_reminder_at is None
                    else "review_reminder"
                ),
                idempotency_key=key,
            )
            if attempt is not None and existing is None:
                created += 1
            case.last_reminder_at = now
            case.next_reminder_at = now + timedelta(
                hours=self.settings.sharia_review_reminder_hours
            )
        await self.session.flush()
        return created

    async def process_due(self, *, limit: int = 50) -> int:
        if (
            not self.settings.telegram_enabled
            or self.settings.telegram_adapter != "http"
            or self.settings.telegram_bot_token is None
        ):
            return 0
        now = datetime.now(UTC)
        rows = list(
            (
                await self.session.scalars(
                    select(TelegramNotificationAttempt)
                    .where(
                        TelegramNotificationAttempt.status.in_({"pending", "retryable"}),
                        TelegramNotificationAttempt.next_retry_at <= now,
                    )
                    .order_by(TelegramNotificationAttempt.created_at.asc())
                    .limit(limit)
                )
            ).all()
        )
        adapter = self.adapter or TelegramHttpAdapter(self.settings)
        processed = 0
        for row in rows:
            case = (
                await self.session.get(ReviewCase, row.review_case_id)
                if row.review_case_id
                else None
            )
            if case is None:
                row.status = "permanent_failure"
                row.last_error_code = "case_missing"
                processed += 1
                continue
            if row.notification_type in {"new_review_required", "review_reminder"} and (
                case.done_at is not None or case.state in TERMINAL_CASE_STATES
            ):
                row.status = "cancelled"
                row.next_retry_at = None
                processed += 1
                continue
            row.attempt_count += 1
            row.last_attempt_at = now
            try:
                message = await self._message(case, row.notification_type)
                result = await adapter.deliver(message)
                if not result.message_ids:
                    raise TelegramDeliveryError(
                        "telegram_message_id_missing",
                        "Telegram did not return a message ID.",
                        retryable=True,
                    )
                row.status = "sent"
                row.provider_message_id = result.message_ids[0]
                row.delivered_at = now
                row.next_retry_at = None
                row.last_error_code = None
                row.last_error_detail = None
            except TelegramDeliveryError as exc:
                permanent = not exc.retryable or row.attempt_count >= 5
                row.status = "permanent_failure" if permanent else "retryable"
                row.last_error_code = exc.code
                row.last_error_detail = str(exc)[:500]
                row.next_retry_at = (
                    None
                    if permanent
                    else now
                    + timedelta(
                        seconds=exc.retry_after_seconds
                        or min(3600, 30 * (2 ** max(0, row.attempt_count - 1)))
                    )
                )
            processed += 1
        await self.session.flush()
        return processed

    async def _message(
        self, case: ReviewCase, notification_type: str
    ) -> TelegramOutboundMessage:
        asset = (
            await self.session.get(CanonicalAsset, case.canonical_asset_id)
            if case.canonical_asset_id
            else None
        )
        external = (
            await self.session.get(ExternalAssessment, case.external_assessment_id)
            if case.external_assessment_id
            else None
        )
        dossier = (
            await self.session.get(AssetResearchDossier, case.dossier_id)
            if case.dossier_id
            else None
        )
        title = {
            "new_review_required": "New Sharia asset review required",
            "review_reminder": "Sharia review reminder",
            "publication_success": "Sharia asset publication completed",
            "rejection_stored": "Sharia asset review stored without publication",
            "material_change": "Material Sharia source change requires review",
        }.get(notification_type, "Sharia review update")
        lines = [
            title,
            f"Case: {case.case_reference}",
            f"Asset: {(asset.name + ' (' + asset.symbol + ')') if asset else case.title}",
            f"State: {case.state.replace('_', ' ').title()}",
        ]
        if external:
            lines.extend(
                [
                    f"SC reference: {external.exact_status_wording}",
                    (
                        f"Meeting/date: {external.sac_meeting_number} | "
                        f"{external.decision_date.isoformat()}"
                    ),
                ]
            )
        if dossier:
            lines.extend(
                [
                    f"Evidence completeness: {round(dossier.evidence_completeness * 100)}%",
                    (
                        f"Missing/contradictory: {dossier.missing_information_count}/"
                        f"{dossier.contradiction_count}"
                    ),
                ]
            )
        lines.extend(
            [
                f"Human review: {case.human_review_reason[:220]}",
                f"Risk: {case.risk_severity.upper()}",
                f"Created: {case.created_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            ]
        )
        url = (
            f"{str(self.settings.public_base_url).rstrip('/')}/system-brain/reviews/{case.id}"
        )
        return TelegramOutboundMessage(
            chat_id=self.settings.sharia_admin_telegram_chat_id or "",
            text="\n".join(lines)[:3000],
            buttons=[TelegramButton("Open secure review", "open_review", url=url)],
        )


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
