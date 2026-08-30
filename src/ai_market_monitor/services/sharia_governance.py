import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import HttpUrl, ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AIAnalysisSnapshot,
    AssetResearchDossier,
    AssetShariaAssessment,
    AuditEvent,
    CanonicalAsset,
    ExternalAssessment,
    MonitorShariaAssetState,
    OfficialSource,
    PublishedAssetAssessment,
    ReviewCase,
    ReviewDecision,
    ShariaGovernanceRoleGrant,
    ShariaMethodology,
    ShariaReviewAssignmentEvent,
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
from ai_market_monitor.schemas.sharia_methodology import (
    CriterionDecisionInput,
    MethodologyEvidenceRequirements,
    MethodologyRulesDefinition,
    UseCoverageDecisionInput,
)
from ai_market_monitor.services import sharia_dossier_state as dossier_state
from ai_market_monitor.services.sharia_screening import (
    ShariaScreeningError,
    ShariaScreeningService,
    methodology_is_development_only,
)
from ai_market_monitor.services.sharia_source_catalog import VERIFIED, normalized_url
from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter
from ai_market_monitor.telegram.types import TelegramButton, TelegramOutboundMessage

SC_METHODOLOGY_CODE = "SC_MALAYSIA_SAC_REFERENCE"
FASSET_METHODOLOGY_CODE = "FASSET_SHARIAH_REPORTS"
SRB_METHODOLOGY_CODE = "SHARIAH_REVIEW_BUREAU"
TERMINAL_CASE_STATES = {"published", "rejected", "stored", "superseded"}
#: Terminal states kept out of the review queue's default view.
#:
#: A superseded case has been replaced by a newer version of itself. It is a signpost,
#: not work — its own suggested next action is "open the current version". On 30 August
#: 2026 the live queue held 213 cases and 192 of them were these, so the 21 that were
#: real work sat behind ten screens of finished ones, every one of them still carrying a
#: priority and a waiting clock.
#:
#: Deliberately narrower than ``TERMINAL_CASE_STATES``. Rejected and stored cases stay in
#: the default view because a reviewer still looks for those in the days after deciding
#: them; only the replaced ones are noise. Asking for a state explicitly is untouched —
#: the State filter still reaches every state, this one included.
DEFAULT_QUEUE_HIDDEN_STATES = {"superseded"}
#: The states an undo may put a case back into.
#:
#: Only states a case can legitimately be *waiting* in. Undo is a way back from a
#: decision that was taken too quickly, so restoring a case into another finished state
#: — published, superseded — is never what it means, and listing them here would let one
#: mistake be corrected into a different one.
ALLOWED_UNDO_STATES = {
    "draft",
    "researching",
    "research_failed",
    "ready_for_review",
    "needs_evidence",
}
OPEN_REMINDER_STATES = {
    "draft",
    "researching",
    "research_failed",
    "ready_for_review",
    "needs_evidence",
}
GOVERNANCE_ROLES = {"SYSTEM_ADMIN", "RESEARCHER", "REVIEWER", "PUBLISHER"}
EXPLANATION_REQUIRED_OUTCOMES = {
    "qualification",
    "fail",
    "not_applicable",
    "needs_evidence",
}


@dataclass(frozen=True, slots=True)
class GovernanceReviewContext:
    asset: CanonicalAsset
    external: ExternalAssessment
    dossier: AssetResearchDossier
    analysis: AIAnalysisSnapshot
    methodology: ShariaMethodology
    rules: MethodologyRulesDefinition
    evidence_requirements: MethodologyEvidenceRequirements
    snapshots: tuple[SourceSnapshot, ...]
    evidence_snapshot_ids: tuple[str, ...]
    available_evidence_categories: frozenset[str]


@dataclass(frozen=True, slots=True)
class CaseDecisionOutcome:
    """What one Approve press actually did.

    Approving and publishing are still two governed steps, but a reviewer asks for one
    thing — put this in front of customers — so the two run together. When the second
    step legitimately waits (a second reviewer is required, or a rights clearance is
    still missing), the approval is kept and ``publication_pending_reason`` says in plain
    words what the Passport is waiting for. It is never an error: the decision was made
    and recorded.
    """

    decision: ReviewDecision
    publication: PublishedAssetAssessment | None = None
    publication_pending_reason: str | None = None

    @property
    def published(self) -> bool:
        return self.publication is not None


class ShariaGovernanceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ShariaGovernanceService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def approve_for_publication(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
        with_qualifications: bool = False,
        criterion_decisions: list[dict] | None = None,
        use_case_decisions: list[dict] | None = None,
        qualifications: list[str] | None = None,
        acknowledged_gaps: list[str] | None = None,
        internal_only: bool = False,
    ) -> ReviewDecision:
        admin = await self._require_permission(admin_user_id, "REVIEWER")
        case = await self._open_case(case_id)
        if case.state != "ready_for_review":
            raise ShariaGovernanceError(
                "case_not_ready", "Only a research-complete case can be approved."
            )
        if len(reason.strip()) < 10:
            raise ShariaGovernanceError(
                "decision_reason_required", "Provide a clear decision reason."
            )
        context = await self._review_context(case)
        criteria = self._validate_criterion_decisions(
            criterion_decisions,
            rules=context.rules,
            evidence_snapshot_ids=list(context.evidence_snapshot_ids),
            available_evidence_categories=context.available_evidence_categories,
        )
        use_decisions = self._validate_use_case_decisions(
            use_case_decisions,
            rules=context.rules,
            evidence_snapshot_ids=list(context.evidence_snapshot_ids),
            available_evidence_categories=context.available_evidence_categories,
            reviewer_user_id=admin.id,
        )
        if any(row["outcome"] == "qualification" for row in criteria):
            with_qualifications = True
        qualification_rows = [item.strip() for item in qualifications or [] if item.strip()]
        if with_qualifications and not qualification_rows:
            raise ShariaGovernanceError(
                "qualification_required",
                "Approval with qualification requires at least one written qualification.",
            )
        decision_name = (
            "approved_internal_only"
            if internal_only
            else (
                "approved_with_qualifications"
                if with_qualifications
                else "approved"
            )
        )
        decision = await self._decision(
            case,
            admin_user_id=admin.id,
            methodology_id=context.methodology.id,
            methodology_version=context.methodology.version,
            methodology_criteria_version=context.rules.criteria_version,
            methodology_criteria_hash=context.rules.criteria_hash,
            decision=decision_name,
            reason=reason,
            evidence_snapshot_ids=list(context.evidence_snapshot_ids),
            criterion_decisions=criteria,
            use_case_decisions=use_decisions,
            qualifications=qualification_rows,
            acknowledged_gaps=acknowledged_gaps or [],
            ai_analysis_snapshot_id=context.analysis.id,
            actor_role="REVIEWER",
        )
        now = datetime.now(UTC)
        case.state = "stored" if internal_only else "approved"
        case.publication_state = (
            "approved_internal_only"
            if internal_only
            else (
                "awaiting_second_approval"
                if self.settings.require_second_reviewer
                else "approved_not_published"
            )
        )
        case.methodology_id = context.methodology.id
        case.next_reminder_at = None
        case.done_at = now if internal_only else None
        methodology_source = await self._ensure_approved_monitoring_source(
            context,
            admin_user_id=admin.id,
        )
        self._audit(
            admin.id,
            "sharia.review_decision_approved",
            "sharia_review_decision",
            str(decision.id),
            {
                "case_id": str(case.id),
                "decision": decision.decision,
                "criterion_count": len(criteria),
                "qualification_count": len(qualification_rows),
                "methodology_monitoring_source_id": str(
                    methodology_source.id
                ),
                "occurred_at": now.isoformat(),
            },
        )
        await self.session.flush()
        return decision

    async def approve_internally(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
        criterion_decisions: list[dict] | None = None,
        use_case_decisions: list[dict] | None = None,
        qualifications: list[str] | None = None,
        acknowledged_gaps: list[str] | None = None,
    ) -> ReviewDecision:
        return await self.approve_for_publication(
            case_id,
            admin_user_id=admin_user_id,
            reason=reason,
            criterion_decisions=criterion_decisions,
            use_case_decisions=use_case_decisions,
            qualifications=qualifications,
            acknowledged_gaps=acknowledged_gaps,
            internal_only=True,
        )

    async def approve_and_publish(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
        with_qualifications: bool = False,
        criterion_decisions: list[dict] | None = None,
        use_case_decisions: list[dict] | None = None,
        qualifications: list[str] | None = None,
        acknowledged_gaps: list[str] | None = None,
    ) -> CaseDecisionOutcome:
        """Approve this case and put the Passport in front of customers.

        One reviewer action, two recorded governed steps — the approval and the
        publication — each with its own audit entry, each validated exactly as it is when
        taken separately. Nothing here skips a check: it removes a second click, not a
        rule.

        The approval is kept whatever happens next. If publication cannot go ahead — a
        second reviewer is required, written permission is not yet recorded — the reason
        comes back in the outcome and the case waits at "approved, not published". A
        publication that refuses must never throw away the decision a person just made,
        so the attempt runs inside its own savepoint.
        """

        decision = await self.approve_for_publication(
            case_id,
            admin_user_id=admin_user_id,
            reason=reason,
            with_qualifications=with_qualifications,
            criterion_decisions=criterion_decisions,
            use_case_decisions=use_case_decisions,
            qualifications=qualifications,
            acknowledged_gaps=acknowledged_gaps,
        )
        if self.settings.require_second_reviewer:
            return CaseDecisionOutcome(
                decision=decision,
                publication_pending_reason=(
                    "Approved. A second reviewer has to publish it, as your settings require."
                ),
            )
        try:
            async with self.session.begin_nested():
                publication = await self.publish_approved(
                    case_id,
                    admin_user_id=admin_user_id,
                    reason=reason,
                )
        except (ShariaGovernanceError, ShariaScreeningError) as exc:
            # ``ShariaScreeningError`` is caught here too. It comes from building the
            # published assessment, and letting it out turned a publication problem into
            # a server error page with the reviewer's decision rolled back with it.
            return CaseDecisionOutcome(
                decision=decision,
                publication_pending_reason=f"Approved, but not published yet: {exc}",
            )
        return CaseDecisionOutcome(decision=decision, publication=publication)

    async def auto_publish_external_reference(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
    ) -> PublishedAssetAssessment:
        """Publish a provider's explicit compliant row without creating an AI ruling."""

        if not self.settings.sharia_import_auto_publish:
            raise ShariaGovernanceError(
                "automatic_publication_disabled",
                "External-reference automatic publication is disabled.",
            )
        if self.settings.sharia_import_require_admin_review:
            raise ShariaGovernanceError(
                "human_review_required",
                "The configured policy still requires an individual admin review.",
            )
        if self.settings.require_second_reviewer:
            raise ShariaGovernanceError(
                "second_reviewer_policy_active",
                "Automatic publication cannot bypass the configured second-reviewer policy.",
            )
        admin = await self._require_permission(admin_user_id, "SYSTEM_ADMIN")
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        existing = await self.session.scalar(
            select(PublishedAssetAssessment)
            .join(
                ReviewDecision,
                ReviewDecision.id
                == PublishedAssetAssessment.review_decision_id,
            )
            .where(
                ReviewDecision.review_case_id == case.id,
                PublishedAssetAssessment.is_active.is_(True),
                PublishedAssetAssessment.publication_state == "published",
            )
            .limit(1)
        )
        if existing is not None:
            return existing
        if case.state != "ready_for_review":
            raise ShariaGovernanceError(
                "case_not_ready",
                "Canonical identity and a completed factual dossier are required.",
            )
        context = await self._review_context(
            case,
            allow_factual_uncertainty=True,
        )
        external = context.external
        if (
            external.source_row_id is None
            or external.normalized_status != "ELIGIBLE_EXTERNAL_REFERENCE"
        ):
            raise ShariaGovernanceError(
                "external_reference_not_eligible",
                "Only an imported provider row explicitly marked compliant can use this path.",
            )
        criteria = self._validate_criterion_decisions(
            [
                {
                    "key": definition.key,
                    "outcome": "pass",
                    "reviewer_explanation": "",
                }
                for definition in context.rules.required_criteria
            ],
            rules=context.rules,
            evidence_snapshot_ids=list(context.evidence_snapshot_ids),
            available_evidence_categories=context.available_evidence_categories,
        )
        use_decisions = self._validate_use_case_decisions(
            [
                {
                    "key": definition.key,
                    "decision": (
                        "covered"
                        if definition.key.startswith("asset_level_")
                        or definition.key == "spot_ownership_and_monitoring"
                        else "not_covered"
                    ),
                    "reason": (
                        "Automated publication retains only the external asset-level reference "
                        "and Hilal Markets spot monitoring scope. Other product uses remain "
                        "separate and are not covered by this automation."
                    ),
                    "scope": definition.default_scope,
                }
                for definition in context.rules.use_cases
            ],
            rules=context.rules,
            evidence_snapshot_ids=list(context.evidence_snapshot_ids),
            available_evidence_categories=context.available_evidence_categories,
            reviewer_user_id=admin.id,
        )
        reason = (
            f"Automatically published the exact compliant asset-level reference from "
            f"{external.source_authority}; AI factual research remains separately labelled "
            "and does not determine the status."
        )
        decision = await self._decision(
            case,
            admin_user_id=admin.id,
            methodology_id=context.methodology.id,
            methodology_version=context.methodology.version,
            methodology_criteria_version=context.rules.criteria_version,
            methodology_criteria_hash=context.rules.criteria_hash,
            decision="approved",
            reason=reason,
            evidence_snapshot_ids=list(context.evidence_snapshot_ids),
            criterion_decisions=criteria,
            use_case_decisions=use_decisions,
            acknowledged_gaps=list(context.dossier.limitations or []),
            ai_analysis_snapshot_id=context.analysis.id,
            actor_role="EXTERNAL_REFERENCE_AUTOMATION",
            security_metadata={
                "source": "scheduled_external_authority_import",
                "external_source_row_id": external.source_row_id,
                "status_authority": external.source_authority,
                "ai_controls_status": False,
                "metadata_only_when_rights_restricted": True,
            },
        )
        now = datetime.now(UTC)
        case.state = "approved"
        case.publication_state = "approved_not_published"
        case.methodology_id = context.methodology.id
        case.next_reminder_at = None
        external.manual_verification_required = False
        await self._ensure_approved_monitoring_source(
            context,
            admin_user_id=admin.id,
        )
        self._audit(
            admin.id,
            "sharia.external_reference_auto_approved",
            "sharia_review_decision",
            str(decision.id),
            {
                "case_id": str(case.id),
                "source_row_id": external.source_row_id,
                "methodology_id": str(context.methodology.id),
                "ai_controls_status": False,
                "occurred_at": now.isoformat(),
            },
        )
        await self.session.flush()
        return await self.publish_approved(
            case.id,
            admin_user_id=admin.id,
            reason=reason,
        )

    async def record_rights_clearance(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        clearance_reference: str,
    ) -> ExternalAssessment:
        admin = await self._require_permission(admin_user_id, "SYSTEM_ADMIN")
        case = await self.session.get(ReviewCase, case_id)
        if case is None or case.external_assessment_id is None:
            raise ShariaGovernanceError(
                "case_external_assessment_missing",
                "The review case has no external assessment.",
            )
        reference = clearance_reference.strip()
        if len(reference) < 10:
            raise ShariaGovernanceError(
                "rights_clearance_reference_required",
                "Record the written permission or legal-clearance reference.",
            )
        external = await self.session.get(
            ExternalAssessment,
            case.external_assessment_id,
        )
        if external is None:
            raise ShariaGovernanceError(
                "external_assessment_missing",
                "The external assessment is unavailable.",
            )
        previous = external.rights_state
        now = datetime.now(UTC)
        external.rights_state = "COMMERCIAL_DISPLAY_CLEARED"
        external.commercial_display_allowed = True
        external.rights_clearance_reference = reference
        external.rights_cleared_by_user_id = admin.id
        external.rights_cleared_at = now
        self._audit(
            admin.id,
            "sharia.external_rights_clearance_recorded",
            "external_assessment",
            str(external.id),
            {
                "case_id": str(case.id),
                "previous_state": previous,
                "new_state": external.rights_state,
                "clearance_reference_recorded": True,
            },
        )
        await self.session.flush()
        return external

    async def publish_approved(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> PublishedAssetAssessment:
        admin = await self._require_permission(admin_user_id, "PUBLISHER")
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        decision = await self.session.scalar(
            select(ReviewDecision)
            .where(
                ReviewDecision.review_case_id == case.id,
                ReviewDecision.decision.in_({"approved", "approved_with_qualifications"}),
            )
            .order_by(ReviewDecision.decision_version.desc())
            .limit(1)
        )
        if decision is not None:
            existing_publication = await self.session.scalar(
                select(PublishedAssetAssessment).where(
                    PublishedAssetAssessment.review_decision_id == decision.id
                )
            )
            if existing_publication is not None:
                return existing_publication
        if case.state != "approved" or case.publication_state not in {
            "approved_not_published",
            "awaiting_second_approval",
        }:
            raise ShariaGovernanceError(
                "case_not_approved", "Record a human approval before publication."
            )
        if len(reason.strip()) < 10:
            raise ShariaGovernanceError(
                "publication_reason_required", "Record a clear publication reason."
            )
        if decision is None:
            raise ShariaGovernanceError(
                "approval_record_missing", "The human approval record is missing."
            )
        if self.settings.require_second_reviewer and decision.admin_user_id == admin.id:
            raise ShariaGovernanceError(
                "second_reviewer_required",
                "Awaiting second approval: the publisher must differ from the reviewer.",
            )
        context = await self._review_context(
            case,
            allow_factual_uncertainty=(
                decision.actor_role == "EXTERNAL_REFERENCE_AUTOMATION"
            ),
        )
        self._validate_recorded_decision(decision, context)
        asset = context.asset
        external = context.external
        dossier = context.dossier
        analysis = context.analysis
        methodology = context.methodology
        # The rights question is about **what is published**, not about who pressed the
        # button. Metadata-only publication reproduces none of the provider's protected
        # content, so where that policy is on, it is on for every decision. Keying the
        # exemption on the actor meant the scheduled import could publish an asset while
        # a human approving the same asset, producing the same page, was refused.
        if (
            self.settings.sharia_external_rights_enforcement
            and external.source_row_id is not None
            and not external.commercial_display_allowed
            and not self.settings.sharia_import_metadata_only_publication
        ):
            raise ShariaGovernanceError(
                "external_rights_clearance_required",
                "Public display is blocked until written permission or legal rights "
                "clearance is recorded.",
            )
        if (
            external.normalized_status is not None
            and external.normalized_status != "ELIGIBLE_EXTERNAL_REFERENCE"
        ):
            raise ShariaGovernanceError(
                "external_status_not_publishable",
                "This external source row is not eligible for publication.",
            )
        now = datetime.now(UTC)
        passport = await self._passport_snapshot(
            asset=asset,
            external=external,
            dossier=dossier,
            analysis=analysis,
            methodology=methodology,
            decision=decision,
            published_at=now,
        )
        evidence_sources = await self._assessment_sources(methodology, external, dossier)
        reviewer_user = await self.session.get(User, decision.admin_user_id)
        reviewer = (
            "External authority reference automation"
            if decision.actor_role == "EXTERNAL_REFERENCE_AUTOMATION"
            else await self._admin_label(reviewer_user or admin)
        )
        qualifications = list(decision.qualifications or [])
        summary, reason_code = self._publication_copy(methodology, external)
        assessment = await ShariaScreeningService(
            self.session, self.settings
        ).create_assessment(
            AssessmentCreateRequest(
                canonical_asset=asset.symbol,
                asset_name=asset.name,
                methodology_id=methodology.id,
                status=(
                    ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
                    if decision.decision == "approved_with_qualifications"
                    else ShariaAssetStatus.ELIGIBLE
                ),
                summary=summary,
                qualifications=qualifications,
                exclusion_reasons=[],
                evidence_snapshot=passport,
                evidence_sources=evidence_sources,
                reviewed_by=reviewer,
                reviewed_at=now,
                valid_from=now,
                reason_code=reason_code,
                reason_summary=reason,
            ),
            actor_user_id=admin.id,
        )
        active_publications = list(
            (
                await self.session.scalars(
                    select(PublishedAssetAssessment)
                    .join(
                        AssetShariaAssessment,
                        AssetShariaAssessment.id
                        == PublishedAssetAssessment.asset_assessment_id,
                    )
                    .where(
                        PublishedAssetAssessment.canonical_asset_id == asset.id,
                        PublishedAssetAssessment.is_active.is_(True),
                        or_(
                            AssetShariaAssessment.methodology_id == methodology.id,
                            PublishedAssetAssessment.external_assessment_id
                            == external.id,
                        ),
                    )
                    .order_by(PublishedAssetAssessment.version.desc())
                )
            ).all()
        )
        latest_publication = await self.session.scalar(
            select(PublishedAssetAssessment)
            .join(
                AssetShariaAssessment,
                AssetShariaAssessment.id
                == PublishedAssetAssessment.asset_assessment_id,
            )
            .where(
                PublishedAssetAssessment.canonical_asset_id == asset.id,
                or_(
                    AssetShariaAssessment.methodology_id == methodology.id,
                    PublishedAssetAssessment.external_assessment_id == external.id,
                ),
            )
            .order_by(PublishedAssetAssessment.version.desc())
            .limit(1)
        )
        for row in active_publications:
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
            supersedes_publication_id=(latest_publication.id if latest_publication else None),
            publication_state="published",
            passport_snapshot=passport,
            integrity_hash=integrity_hash,
            is_active=True,
            published_by_user_id=admin.id,
            published_at=now,
        )
        self.session.add(publication)
        case.state = "published"
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
                invalidation_reason=f"published assessment {assessment.id}",
            )
        )
        self._audit(
            admin.id,
            "sharia.publication_completed",
            "published_asset_assessment",
            str(publication.id),
            {
                "case_id": str(case.id),
                "asset": asset.symbol,
                "version": version,
                "assessment_id": str(assessment.id),
                "decision_reason_recorded": True,
                "reviewer_user_id": str(decision.admin_user_id),
                "publisher_user_id": str(admin.id),
                "publication_reason": reason.strip(),
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
        admin = await self._require_permission(admin_user_id, "REVIEWER")
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
        admin = await self._require_permission(admin_user_id, "REVIEWER")
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
        admin = await self._require_permission(admin_user_id, "RESEARCHER")
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
        case.next_reminder_at = datetime.now(UTC) + timedelta(
            hours=self.settings.sharia_review_reminder_hours
        )
        await self.session.flush()
        return decision

    async def add_admin_note(
        self, case_id: UUID, *, admin_user_id: UUID, note: str
    ) -> ReviewCase:
        admin = await self._require_permission(admin_user_id, "REVIEWER")
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

    async def record_ai_field_review(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        field_key: str,
        disposition: str,
        reviewer_value: str,
        original_ai_suggestion: str,
        reason: str,
        source_references: list[str],
    ) -> ReviewCase:
        admin = await self._require_permission(admin_user_id, "REVIEWER")
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        if disposition not in {"accepted", "edited", "rejected", "irrelevant"}:
            raise ShariaGovernanceError(
                "invalid_ai_field_disposition",
                "Choose accept, edit, reject, or irrelevant.",
            )
        normalized_key = field_key.strip()
        if not normalized_key or len(normalized_key) > 120:
            raise ShariaGovernanceError(
                "invalid_review_field",
                "The review field is unavailable.",
            )
        normalized_value = reviewer_value.strip()
        if disposition in {"accepted", "edited"} and len(normalized_value) < 2:
            raise ShariaGovernanceError(
                "reviewer_value_required",
                "Enter the independently reviewed value before accepting it.",
            )
        normalized_reason = reason.strip()
        if len(normalized_reason) < 3:
            raise ShariaGovernanceError(
                "field_review_reason_required",
                "Record why this suggestion was accepted, changed, or rejected.",
            )
        now = datetime.now(UTC)
        entry = {
            "entry_type": "ai_field_review",
            "field_key": normalized_key,
            "disposition": disposition,
            "original_ai_suggestion": original_ai_suggestion[:4000],
            "reviewer_value": normalized_value[:4000],
            "reason": normalized_reason[:1200],
            "source_references": [str(item)[:160] for item in source_references[:12]],
            "admin_user_id": str(admin.id),
            "created_at": now.isoformat(),
        }
        notes = list(case.admin_notes or [])
        notes.append(entry)
        case.admin_notes = notes
        self._audit(
            admin.id,
            "sharia.ai_field_suggestion_reviewed",
            "sharia_review_case",
            str(case.id),
            {
                "field_key": normalized_key,
                "disposition": disposition,
                "original_ai_suggestion": original_ai_suggestion[:2000],
                "final_reviewer_value": normalized_value[:2000],
                "reason": normalized_reason[:500],
                "source_references": entry["source_references"],
            },
        )
        await self.session.flush()
        return case

    async def assign_case(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        assigned_reviewer_id: UUID | None,
        reason: str,
        priority: str | None = None,
    ) -> ReviewCase:
        admin = await self._require_permission(admin_user_id, "SYSTEM_ADMIN")
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        if len(reason.strip()) < 5:
            raise ShariaGovernanceError(
                "assignment_reason_required", "Record a reason for this assignment."
            )
        if assigned_reviewer_id is not None:
            await self._require_permission(assigned_reviewer_id, "REVIEWER")
        now = datetime.now(UTC)
        previous = case.assigned_reviewer_id
        case.assigned_reviewer_id = assigned_reviewer_id
        case.assigned_at = now if assigned_reviewer_id else None
        case.due_at = now + timedelta(hours=self.settings.sharia_review_sla_hours)
        if priority:
            if priority not in {"low", "normal", "high", "urgent"}:
                raise ShariaGovernanceError("invalid_priority", "Unknown review priority.")
            case.priority = priority
        self.session.add(
            ShariaReviewAssignmentEvent(
                review_case_id=case.id,
                actor_user_id=admin.id,
                previous_assignee_id=previous,
                assigned_reviewer_id=assigned_reviewer_id,
                action="assigned" if assigned_reviewer_id else "unassigned",
                priority=case.priority,
                reason=reason.strip(),
                created_at=now,
            )
        )
        self._audit(
            admin.id,
            "sharia.review_assignment_changed",
            "sharia_review_case",
            str(case.id),
            {
                "previous_assignee_id": str(previous) if previous else None,
                "assigned_reviewer_id": (
                    str(assigned_reviewer_id) if assigned_reviewer_id else None
                ),
                "priority": case.priority,
            },
        )
        await self.session.flush()
        return case

    async def reopen_case(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> ReviewCase:
        admin = await self._require_permission(admin_user_id, "REVIEWER")
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        if len(reason.strip()) < 10:
            raise ShariaGovernanceError(
                "reopen_reason_required", "Record why this review is being reopened."
            )
        previous_state = case.state
        now = datetime.now(UTC)
        case.state = "ready_for_review"
        case.publication_state = (
            "change_under_review"
            if previous_state in {"published", "approved"}
            else "unpublished"
        )
        case.done_at = None
        case.due_at = now + timedelta(hours=self.settings.sharia_review_sla_hours)
        case.next_reminder_at = now + timedelta(
            hours=self.settings.sharia_review_reminder_hours
        )
        self.session.add(
            ShariaReviewAssignmentEvent(
                review_case_id=case.id,
                actor_user_id=admin.id,
                previous_assignee_id=case.assigned_reviewer_id,
                assigned_reviewer_id=case.assigned_reviewer_id,
                action="reopened",
                priority=case.priority,
                reason=reason.strip(),
                created_at=now,
            )
        )
        self._audit(
            admin.id,
            "sharia.review_reopened",
            "sharia_review_case",
            str(case.id),
            {"previous_state": previous_state, "new_state": case.state},
        )
        await self.session.flush()
        return case

    async def place_safety_hold(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> ReviewCase:
        admin = await self._require_permission(admin_user_id, "PUBLISHER")
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        if len(reason.strip()) < 10:
            raise ShariaGovernanceError(
                "safety_hold_reason_required", "Record why the public Passport must be held."
            )
        if case.canonical_asset_id is None:
            raise ShariaGovernanceError(
                "asset_identity_missing", "A safety hold requires a canonical asset."
            )
        publication_query = (
            select(PublishedAssetAssessment)
            .join(
                AssetShariaAssessment,
                AssetShariaAssessment.id
                == PublishedAssetAssessment.asset_assessment_id,
            )
            .where(
                PublishedAssetAssessment.canonical_asset_id == case.canonical_asset_id,
                PublishedAssetAssessment.is_active.is_(True),
                PublishedAssetAssessment.publication_state == "published",
            )
        )
        if case.methodology_id is not None:
            publication_query = publication_query.where(
                AssetShariaAssessment.methodology_id == case.methodology_id
            )
        publication = await self.session.scalar(
            publication_query.order_by(PublishedAssetAssessment.version.desc()).limit(1)
        )
        if publication is None:
            if case.state == "safety_hold":
                return case
            raise ShariaGovernanceError(
                "active_publication_missing", "No active publication is available to hold."
            )
        now = datetime.now(UTC)
        previous_state = case.state
        publication.is_active = False
        publication.publication_state = "safety_hold"
        publication.paused_at = now
        case.state = "safety_hold"
        case.publication_state = "safety_hold"
        case.done_at = None
        case.due_at = now + timedelta(hours=self.settings.sharia_review_sla_hours)
        assessment = await self.session.get(
            AssetShariaAssessment, publication.asset_assessment_id
        )
        if assessment is not None:
            snapshots = list(
                (
                    await self.session.scalars(
                        select(ShariaUniverseSnapshot).where(
                            ShariaUniverseSnapshot.methodology_id
                            == assessment.methodology_id,
                            ShariaUniverseSnapshot.invalidated_at.is_(None),
                        )
                    )
                ).all()
            )
            for snapshot in snapshots:
                snapshot.invalidated_at = now
                snapshot.invalidation_reason = f"manual safety hold {case.id}"
        self._audit(
            admin.id,
            "sharia.publication_safety_hold_placed",
            "published_asset_assessment",
            str(publication.id),
            {
                "review_case_id": str(case.id),
                "previous_state": previous_state,
                "new_state": case.state,
                "reason": reason.strip(),
            },
        )
        await self.session.flush()
        return case

    async def request_safety_hold_removal(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> ReviewCase:
        admin = await self._require_permission(admin_user_id, "REVIEWER")
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        if case.state != "safety_hold":
            raise ShariaGovernanceError(
                "safety_hold_not_active", "This case does not have an active safety hold."
            )
        if len(reason.strip()) < 10:
            raise ShariaGovernanceError(
                "safety_hold_review_reason_required",
                "Record why removal should proceed through a fresh review.",
            )
        now = datetime.now(UTC)
        case.state = "ready_for_review"
        case.publication_state = "safety_hold_pending_review"
        case.done_at = None
        case.due_at = now + timedelta(hours=self.settings.sharia_review_sla_hours)
        case.next_reminder_at = now
        self._audit(
            admin.id,
            "sharia.safety_hold_removal_review_requested",
            "sharia_review_case",
            str(case.id),
            {
                "previous_state": "safety_hold",
                "new_state": case.state,
                "reason": reason.strip(),
                "publication_reactivated": False,
            },
        )
        await self.session.flush()
        return case

    async def dismiss_false_positive(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> ReviewCase:
        admin = await self._require_permission(admin_user_id, "REVIEWER")
        case = await self._open_case(case_id)
        if case.case_type not in {"material_source_change", "user_factual_report"}:
            raise ShariaGovernanceError(
                "dismissal_not_available",
                "Only a change or factual-report review can be dismissed as unsupported.",
            )
        if case.state not in {"ready_for_review", "needs_evidence"}:
            raise ShariaGovernanceError(
                "dismissal_not_reviewable",
                "Complete or pause research before dismissing this review.",
            )
        if len(reason.strip()) < 10:
            raise ShariaGovernanceError(
                "dismissal_reason_required", "Record why the reported change is not material."
            )
        previous = case.state
        now = datetime.now(UTC)
        case.state = "superseded"
        case.publication_state = "published_unchanged"
        case.done_at = now
        case.due_at = None
        case.next_reminder_at = None
        self._audit(
            admin.id,
            "sharia.review_false_positive_dismissed",
            "sharia_review_case",
            str(case.id),
            {
                "previous_state": previous,
                "new_state": case.state,
                "reason": reason.strip(),
                "published_assessment_changed": False,
            },
        )
        await self.session.flush()
        return case

    async def start_research(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> ReviewCase:
        admin = await self._require_permission(admin_user_id, "RESEARCHER")
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        # ``ready_for_review`` belongs here too. A case waiting for a reviewer whose
        # evidence has aged out, or whose official source failed to load, needs its
        # sources fetched again — and without this it had no action at all: approval
        # refused it and research would not take it back.
        if case.state not in {
            "draft",
            "research_failed",
            "needs_evidence",
            "researching",
            "ready_for_review",
        }:
            raise ShariaGovernanceError(
                "invalid_research_transition",
                f"Research cannot start while the case is {case.state.replace('_', ' ')}.",
            )
        if len(reason.strip()) < 5:
            raise ShariaGovernanceError(
                "research_reason_required", "Record why research is being started or retried."
            )
        previous = case.state
        case.state = "researching"
        case.publication_state = "unpublished"
        case.done_at = None
        now = datetime.now(UTC)
        case.due_at = now + timedelta(
            hours=self.settings.sharia_review_sla_hours
        )
        case.next_reminder_at = now + timedelta(
            hours=self.settings.sharia_review_reminder_hours
        )
        self._audit(
            admin.id,
            "sharia.research_started" if previous != "researching" else "sharia.research_retried",
            "sharia_review_case",
            str(case.id),
            {"previous_state": previous, "new_state": case.state, "reason": reason.strip()},
        )
        await self.session.flush()
        return case

    async def mark_ready_for_review(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> ReviewCase:
        admin = await self._require_permission(admin_user_id, "RESEARCHER")
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        if case.state not in {"researching", "needs_evidence", "ready_for_review"}:
            raise ShariaGovernanceError(
                "invalid_ready_transition",
                f"This case cannot enter review from {case.state.replace('_', ' ')}.",
            )
        if len(reason.strip()) < 5:
            raise ShariaGovernanceError(
                "readiness_reason_required", "Record why the evidence package is ready."
            )
        # "Ready for review" means exactly one thing: a reviewer pressing Approve would be
        # accepted. Asking the approval path itself is what keeps that true. The shorter
        # list this used to check — identity, a dossier, an analysis — let cases through
        # that approval then refused for evidence the methodology also requires.
        blocker = await self.review_blocker(case)
        if blocker is not None:
            raise blocker
        previous = case.state
        case.state = "ready_for_review"
        case.next_reminder_at = datetime.now(UTC) + timedelta(
            hours=self.settings.sharia_review_reminder_hours
        )
        self._audit(
            admin.id,
            "sharia.case_marked_ready_for_review",
            "sharia_review_case",
            str(case.id),
            {"previous_state": previous, "new_state": case.state, "reason": reason.strip()},
        )
        await self.session.flush()
        return case

    async def retry_notification(
        self,
        attempt_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> TelegramNotificationAttempt:
        admin = await self._require_permission(admin_user_id, "SYSTEM_ADMIN")
        attempt = await self.session.get(TelegramNotificationAttempt, attempt_id)
        if attempt is None:
            raise ShariaGovernanceError(
                "notification_not_found", "Notification attempt not found."
            )
        if attempt.status == "sent":
            return attempt
        if len(reason.strip()) < 5:
            raise ShariaGovernanceError(
                "retry_reason_required", "Record why this delivery is being retried."
            )
        previous = attempt.status
        attempt.status = "pending"
        attempt.next_retry_at = datetime.now(UTC)
        attempt.last_error_code = None
        attempt.last_error_detail = None
        self._audit(
            admin.id,
            "sharia.notification_retry_requested",
            "sharia_telegram_notification_attempt",
            str(attempt.id),
            {"previous_status": previous, "reason": reason.strip()},
        )
        await self.session.flush()
        return attempt

    async def _require_permission(self, user_id: UUID, permission: str) -> User:
        if permission not in GOVERNANCE_ROLES:
            raise ShariaGovernanceError("unknown_permission", "Unknown governance role.")
        user = await self.session.get(User, user_id)
        if user is None or user.role != UserRole.ADMIN:
            raise ShariaGovernanceError("admin_required", "Administrator role required.")
        grants = set(
            (
                await self.session.scalars(
                    select(ShariaGovernanceRoleGrant.role).where(
                        ShariaGovernanceRoleGrant.user_id == user.id,
                        ShariaGovernanceRoleGrant.revoked_at.is_(None),
                    )
                )
            ).all()
        )
        if not grants and self.settings.app_env in {"staging", "production"}:
            raise ShariaGovernanceError(
                "governance_grant_required",
                "An explicit governance role grant is required in this environment.",
            )
        if grants and permission not in grants and "SYSTEM_ADMIN" not in grants:
            raise ShariaGovernanceError(
                "governance_permission_required",
                f"The {permission.replace('_', ' ').title()} permission is required.",
            )
        return user

    async def require_permission(self, user_id: UUID, permission: str) -> User:
        """Authorize a governance route without duplicating grant semantics."""

        return await self._require_permission(user_id, permission)

    async def _ensure_approved_monitoring_source(
        self,
        context: GovernanceReviewContext,
        *,
        admin_user_id: UUID,
    ) -> OfficialSource:
        normalized = normalized_url(context.external.source_url)
        source = await self.session.scalar(
            select(OfficialSource).where(
                OfficialSource.canonical_asset_id == context.asset.id,
                OfficialSource.normalized_url == normalized,
            )
        )
        if source is None:
            source = OfficialSource(
                canonical_asset_id=context.asset.id,
                category="external_methodology_reference",
                title=(
                    f"{context.methodology.name} source for "
                    f"{context.asset.name}"
                ),
                source_url=context.external.source_url,
                normalized_url=normalized,
                priority=5,
                verification_state=VERIFIED,
                verified_by_user_id=admin_user_id,
                verified_at=datetime.now(UTC),
                is_active=True,
            )
            self.session.add(source)
            await self.session.flush()
            self._audit(
                admin_user_id,
                "sharia.methodology_source_monitoring_registered",
                "official_source",
                str(source.id),
                {
                    "canonical_asset_id": str(context.asset.id),
                    "external_assessment_id": str(context.external.id),
                    "methodology_id": str(context.methodology.id),
                    "source_category": source.category,
                },
            )
        return source

    async def _review_context(
        self,
        case: ReviewCase,
        *,
        allow_factual_uncertainty: bool = False,
    ) -> GovernanceReviewContext:
        if not case.canonical_asset_id or not case.external_assessment_id or not case.dossier_id:
            raise ShariaGovernanceError(
                "case_evidence_incomplete",
                "Canonical identity, external assessment, and research dossier are required.",
            )
        asset = await self.session.get(CanonicalAsset, case.canonical_asset_id)
        external = await self.session.get(ExternalAssessment, case.external_assessment_id)
        dossier = await self.session.get(AssetResearchDossier, case.dossier_id)
        if asset is None or external is None or dossier is None:
            raise ShariaGovernanceError("case_evidence_missing", "Review evidence is missing.")
        if (
            external.canonical_asset_id != asset.id
            or dossier.canonical_asset_id != asset.id
            or dossier.external_assessment_id != external.id
        ):
            raise ShariaGovernanceError(
                "case_evidence_mismatch",
                "The case, asset identity, external assessment, and dossier do not match.",
            )
        if asset.mapping_state != "verified" or external.mapping_state != "mapped":
            raise ShariaGovernanceError(
                "identity_not_verified", "Canonical identity must be verified before approval."
            )
        if not dossier_state.is_complete(dossier.state) or dossier.completed_at is None:
            raise ShariaGovernanceError(
                "dossier_not_complete", "The factual research dossier is not complete."
            )

        methodology, rules, requirements = await self._case_methodology(case, external)
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

        try:
            snapshot_ids = tuple(UUID(str(value)) for value in dossier.source_snapshot_ids)
        except (TypeError, ValueError) as exc:
            raise ShariaGovernanceError(
                "evidence_snapshot_ids_invalid",
                "The dossier contains an invalid evidence snapshot reference.",
            ) from exc
        if not snapshot_ids or external.source_snapshot_id not in snapshot_ids:
            raise ShariaGovernanceError(
                "official_snapshot_not_reviewed",
                "The imported external assessment snapshot is not in the reviewed dossier.",
            )
        snapshots = tuple(
            (
                await self.session.scalars(
                    select(SourceSnapshot).where(SourceSnapshot.id.in_(snapshot_ids))
                )
            ).all()
        )
        if len(snapshots) != len(set(snapshot_ids)):
            raise ShariaGovernanceError(
                "evidence_snapshot_missing", "One or more reviewed evidence snapshots are missing."
            )
        successful_reviewed_ids = {
            str(row.id)
            for row in snapshots
            if row.fetch_status == "success"
        }
        if (
            set(analysis.input_snapshot_ids or [])
            != successful_reviewed_ids
        ):
            raise ShariaGovernanceError(
                "analysis_evidence_version_mismatch",
                "The factual analysis was not produced from the current "
                "successful evidence snapshots.",
            )
        # How old the evidence may be has exactly one owner: the methodology's
        # ``maximum_source_age_days``, checked immediately below. ``source_freshness_deadline``
        # is an operational reminder to re-check the sources — it is derived from a
        # configuration interval, not from the methodology, and refusing a decision on it
        # meant a second, stricter, ungoverned age rule silently overrode the governed one.
        # It stays on the case to drive reminders and the evidence badge; it never refuses.
        stale_before = datetime.now(UTC) - timedelta(
            days=requirements.maximum_source_age_days
        )
        stale = [
            row
            for row in snapshots
            if row.fetch_status != "success" or _as_utc(row.retrieved_at) < stale_before
        ]
        blocking_stale = (
            stale
            if not allow_factual_uncertainty
            else [
                row
                for row in stale
                if row.id == external.source_snapshot_id
            ]
        )
        if blocking_stale:
            raise ShariaGovernanceError(
                "required_evidence_stale",
                "Required evidence is unavailable or older than the methodology permits.",
            )
        if (
            not allow_factual_uncertainty
            and dossier.evidence_completeness
            < requirements.minimum_evidence_completeness
        ):
            raise ShariaGovernanceError(
                "evidence_completeness_below_threshold",
                "Evidence completeness is below the methodology threshold.",
            )
        analysis_output = dict(analysis.output or {})
        contradictions = list(analysis_output.get("contradictions") or [])
        if (
            not allow_factual_uncertainty
            and (dossier.contradiction_count > 0 or contradictions)
        ):
            raise ShariaGovernanceError(
                "critical_contradiction_unresolved",
                "A critical contradiction remains unresolved in the reviewed evidence.",
            )

        values = {
            "canonical_asset": asset,
            "external_assessment": external,
            "dossier": dossier,
            "factual_profile": dict(dossier.factual_profile or {}),
        }
        missing_fields = [
            path
            for path in requirements.critical_missing_fields
            if not _value_at_path(values, path)
        ]
        if missing_fields:
            raise ShariaGovernanceError(
                "critical_evidence_field_missing",
                "Critical methodology evidence fields are missing: "
                + ", ".join(missing_fields),
            )

        categories = await self._evidence_categories(
            asset=asset,
            external=external,
            dossier=dossier,
            snapshots=snapshots,
            rules=rules,
        )
        missing_categories = set(requirements.mandatory_source_categories) - categories
        if missing_categories:
            raise ShariaGovernanceError(
                "mandatory_evidence_category_missing",
                "Required evidence categories are missing: "
                + ", ".join(sorted(missing_categories)),
            )
        return GovernanceReviewContext(
            asset=asset,
            external=external,
            dossier=dossier,
            analysis=analysis,
            methodology=methodology,
            rules=rules,
            evidence_requirements=requirements,
            snapshots=snapshots,
            evidence_snapshot_ids=tuple(str(value) for value in snapshot_ids),
            available_evidence_categories=frozenset(categories),
        )

    async def _case_methodology(
        self,
        case: ReviewCase,
        external: ExternalAssessment,
    ) -> tuple[
        ShariaMethodology,
        MethodologyRulesDefinition,
        MethodologyEvidenceRequirements,
    ]:
        if case.methodology_id is None:
            raise ShariaGovernanceError(
                "case_methodology_required", "The review case has no methodology."
            )
        methodology = await self.session.get(ShariaMethodology, case.methodology_id)
        if methodology is None:
            raise ShariaGovernanceError(
                "case_methodology_missing", "The review methodology no longer exists."
            )
        now = datetime.now(UTC)
        if methodology.status != ShariaMethodologyStatus.ACTIVE:
            raise ShariaGovernanceError(
                "methodology_not_active", "The review methodology is not active."
            )
        if methodology.effective_from is None or _as_utc(methodology.effective_from) > now:
            raise ShariaGovernanceError(
                "methodology_not_effective", "The review methodology is not yet effective."
            )
        if methodology.effective_to is not None and _as_utc(methodology.effective_to) <= now:
            raise ShariaGovernanceError(
                "methodology_expired",
                "The review methodology has expired and cannot approve or publish a Passport.",
            )
        if methodology_is_development_only(methodology):
            raise ShariaGovernanceError(
                "development_methodology_not_publishable",
                "Development and test methodologies cannot publish customer Passports.",
            )
        if (
            external.methodology_id is not None
            and external.methodology_id != methodology.id
        ):
            raise ShariaGovernanceError(
                "methodology_source_mismatch",
                "The external assessment belongs to a different methodology version.",
            )
        try:
            rules, requirements = ShariaScreeningService.validate_methodology_contract(
                methodology.rules_json,
                methodology.evidence_requirements_json
            )
        except ShariaScreeningError as exc:
            raise ShariaGovernanceError(
                "methodology_contract_invalid",
                "The methodology does not define a valid criteria and evidence contract.",
            ) from exc
        self._validate_source_adapter(rules, external)
        return methodology, rules, requirements

    @staticmethod
    def _validate_source_adapter(
        rules: MethodologyRulesDefinition,
        external: ExternalAssessment,
    ) -> None:
        authority = external.source_authority.casefold()
        source_matches = False
        if rules.source_adapter == "sc_malaysia":
            source_matches = (
                rules.source_family == "sc_malaysia_sac"
                and external.source_family == "sc_malaysia_sac"
                and "securities commission malaysia" in authority
                and external.exact_status_wording.casefold() == "shariah-compliant"
                and external.sac_meeting_number is not None
                and external.decision_date is not None
            )
        elif rules.source_adapter == "fasset":
            source_matches = (
                rules.source_family == "fasset_shariah_reports"
                and external.source_family == "fasset_shariah_reports"
                and "fasset" in authority
                and external.exact_status_wording == "Shariah Compliant"
                and bool(external.structured_facts)
            )
        elif rules.source_adapter == "srb":
            source_matches = (
                rules.source_family == "shariah_review_bureau"
                and external.source_family == "shariah_review_bureau"
                and "shariyah review bureau" in authority
                and external.exact_status_wording == "Compliant"
                and external.source_row_id is not None
            )
        else:
            raise ShariaGovernanceError(
                "source_adapter_unsupported",
                "No reviewed import adapter is configured for this methodology.",
            )
        if not source_matches:
            raise ShariaGovernanceError(
                "methodology_source_mismatch",
                "The external assessment does not match the case methodology source family.",
            )

    @staticmethod
    def _publication_copy(
        methodology: ShariaMethodology,
        external: ExternalAssessment,
    ) -> tuple[str, str]:
        rules = MethodologyRulesDefinition.model_validate(methodology.rules_json)
        if rules.source_adapter == "sc_malaysia":
            return (
                "The official SC Malaysia asset-level reference records this asset as "
                f"{external.exact_status_wording}. Hilal Markets use-specific coverage is "
                "shown separately and does not infer unpublished SC reasoning.",
                "sc_malaysia_reviewed_publication",
            )
        if rules.source_adapter == "fasset":
            return (
                "The retained Fasset asset profile records the explicit verdict "
                f"{external.exact_status_wording}. Hilal Markets identity and use-specific "
                "coverage are reviewed separately and no missing source fact was inferred.",
                "fasset_reviewed_publication",
            )
        if rules.source_adapter == "srb":
            return (
                "The retained Shariah Review Bureau external reference records this asset "
                f"as {external.exact_status_wording}. Hilal Markets factual and use-specific "
                "review remains separate, and restricted source content is not reproduced.",
                "srb_reviewed_publication",
            )
        raise ShariaGovernanceError(
            "source_adapter_unsupported",
            "No publication wording adapter is configured for this methodology.",
        )

    async def _evidence_categories(
        self,
        *,
        asset: CanonicalAsset,
        external: ExternalAssessment,
        dossier: AssetResearchDossier,
        snapshots: tuple[SourceSnapshot, ...],
        rules: MethodologyRulesDefinition,
    ) -> set[str]:
        categories = {"source_snapshot"}
        if asset.mapping_state == "verified":
            categories.add("canonical_identity")
        if dossier_state.is_complete(dossier.state):
            categories.add("factual_dossier")
        external_snapshot_available = any(
            row.id == external.source_snapshot_id and row.fetch_status == "success"
            for row in snapshots
        )
        if external_snapshot_available:
            categories.add("official_external_reference")
        if rules.source_adapter == "sc_malaysia" and external_snapshot_available:
            categories.add("official_sc_reference")
        if rules.source_adapter == "fasset" and external_snapshot_available:
            categories.add("official_fasset_reference")
        official_ids = {
            row.official_source_id for row in snapshots if row.official_source_id is not None
        }
        if official_ids:
            categories.update(
                str(value)
                for value in (
                    await self.session.scalars(
                        select(OfficialSource.category).where(OfficialSource.id.in_(official_ids))
                    )
                ).all()
            )
        for row in snapshots:
            parser_categories = dict(row.parser_result or {}).get("evidence_categories") or []
            categories.update(str(value) for value in parser_categories)
        return categories

    @staticmethod
    def _validate_criterion_decisions(
        rows: list[dict] | None,
        *,
        rules: MethodologyRulesDefinition,
        evidence_snapshot_ids: list[str],
        available_evidence_categories: frozenset[str],
    ) -> list[dict]:
        if not rows:
            raise ShariaGovernanceError(
                "criterion_decisions_required",
                "Every required methodology criterion must be explicitly decided.",
            )
        try:
            parsed = [CriterionDecisionInput.model_validate(row) for row in rows]
        except ValidationError as exc:
            raise ShariaGovernanceError(
                "criterion_decision_invalid", "A criterion decision is malformed."
            ) from exc
        definitions = {item.key: item for item in rules.required_criteria}
        submitted = [item.key for item in parsed]
        if len(submitted) != len(set(submitted)):
            raise ShariaGovernanceError(
                "criterion_decision_duplicate", "A criterion was submitted more than once."
            )
        unknown = set(submitted) - set(definitions)
        if unknown:
            raise ShariaGovernanceError(
                "criterion_decision_unknown",
                "Unknown methodology criteria were submitted: " + ", ".join(sorted(unknown)),
            )
        missing = {
            item.key for item in rules.required_criteria if item.required
        } - set(submitted)
        if missing:
            raise ShariaGovernanceError(
                "criterion_decisions_incomplete",
                "Required methodology criteria are missing: " + ", ".join(sorted(missing)),
            )
        canonical = []
        for item in parsed:
            definition = definitions[item.key]
            if item.outcome not in definition.allowed_outcomes:
                raise ShariaGovernanceError(
                    "criterion_outcome_not_allowed",
                    f"{definition.label} does not allow the selected outcome.",
                )
            missing_evidence = set(definition.evidence_categories) - set(
                available_evidence_categories
            )
            if missing_evidence:
                raise ShariaGovernanceError(
                    "criterion_evidence_missing",
                    f"{definition.label} is missing required evidence categories.",
                )
            explanation = item.reviewer_explanation.strip()
            if item.outcome in EXPLANATION_REQUIRED_OUTCOMES and len(explanation) < 10:
                raise ShariaGovernanceError(
                    "criterion_reason_required",
                    f"{definition.label} requires written reasoning for this outcome.",
                )
            if item.outcome in definition.blocking_outcomes:
                raise ShariaGovernanceError(
                    "criteria_not_approvable",
                    f"{definition.label} has a blocking outcome: {item.outcome}.",
                )
            canonical.append(
                {
                    "key": definition.key,
                    "label": definition.label,
                    "description": definition.description,
                    "required": definition.required,
                    "outcome": item.outcome,
                    "evidence_categories": list(definition.evidence_categories),
                    "evidence": list(evidence_snapshot_ids),
                    "reviewer_explanation": explanation,
                    "criteria_version": rules.criteria_version,
                }
            )
        return canonical

    @staticmethod
    def _validate_use_case_decisions(
        rows: list[dict] | None,
        *,
        rules: MethodologyRulesDefinition,
        evidence_snapshot_ids: list[str],
        available_evidence_categories: frozenset[str],
        reviewer_user_id: UUID,
    ) -> list[dict]:
        if not rules.use_cases:
            return []
        if not rows:
            raise ShariaGovernanceError(
                "use_case_decisions_required",
                "Every methodology use scope must be explicitly reviewed.",
            )
        try:
            parsed = [UseCoverageDecisionInput.model_validate(row) for row in rows]
        except ValidationError as exc:
            raise ShariaGovernanceError(
                "use_case_decision_invalid", "A use-scope decision is malformed."
            ) from exc
        definitions = {item.key: item for item in rules.use_cases}
        submitted = [item.key for item in parsed]
        if len(submitted) != len(set(submitted)):
            raise ShariaGovernanceError(
                "use_case_decision_duplicate", "A use scope was submitted more than once."
            )
        unknown = set(submitted) - set(definitions)
        if unknown:
            raise ShariaGovernanceError(
                "use_case_decision_unknown",
                "Unknown use scopes were submitted: " + ", ".join(sorted(unknown)),
            )
        missing = {item.key for item in rules.use_cases if item.required} - set(submitted)
        if missing:
            raise ShariaGovernanceError(
                "use_case_decisions_incomplete",
                "Required use scopes are missing: " + ", ".join(sorted(missing)),
            )
        canonical = []
        verified_at = datetime.now(UTC).isoformat()
        for item in parsed:
            definition = definitions[item.key]
            if item.decision not in definition.allowed_decisions:
                raise ShariaGovernanceError(
                    "use_case_outcome_not_allowed",
                    f"{definition.label} does not allow the selected decision.",
                )
            if set(definition.evidence_categories) - set(available_evidence_categories):
                raise ShariaGovernanceError(
                    "use_case_evidence_missing",
                    f"{definition.label} is missing its reviewed evidence category.",
                )
            if item.decision in definition.execution_blocking_decisions:
                raise ShariaGovernanceError(
                    "use_scope_not_approvable",
                    f"{definition.label} has a blocking decision: {item.decision}.",
                )
            canonical.append(
                {
                    "key": definition.key,
                    "label": definition.label,
                    "decision": item.decision,
                    "reason": item.reason.strip(),
                    "source_snapshot_ids": list(evidence_snapshot_ids),
                    "criterion_keys": list(definition.criterion_keys),
                    "reviewer_user_id": str(reviewer_user_id),
                    "verified_at": verified_at,
                    "scope": (item.scope or definition.default_scope).strip(),
                    "execution_blocking": item.decision
                    in definition.execution_blocking_decisions,
                }
            )
        return canonical

    def _validate_recorded_decision(
        self,
        decision: ReviewDecision,
        context: GovernanceReviewContext,
    ) -> None:
        if (
            decision.methodology_id != context.methodology.id
            or decision.methodology_version != context.methodology.version
            or decision.methodology_criteria_version != context.rules.criteria_version
            or decision.methodology_criteria_hash != context.rules.criteria_hash
        ):
            raise ShariaGovernanceError(
                "approved_methodology_version_mismatch",
                "The approved methodology contract no longer matches the review case.",
            )
        if decision.ai_analysis_snapshot_id != context.analysis.id:
            raise ShariaGovernanceError(
                "approved_analysis_version_mismatch",
                "The approved factual analysis is not the current reviewed analysis.",
            )
        if set(decision.evidence_snapshot_ids or []) != set(context.evidence_snapshot_ids):
            raise ShariaGovernanceError(
                "approved_evidence_version_mismatch",
                "The approved evidence snapshots do not match the current review case.",
            )
        self._validate_criterion_decisions(
            [
                {
                    "key": row.get("key"),
                    "outcome": row.get("outcome"),
                    "reviewer_explanation": row.get("reviewer_explanation", ""),
                }
                for row in decision.criterion_decisions or []
            ],
            rules=context.rules,
            evidence_snapshot_ids=list(context.evidence_snapshot_ids),
            available_evidence_categories=context.available_evidence_categories,
        )
        self._validate_use_case_decisions(
            [
                {
                    "key": row.get("key"),
                    "decision": row.get("decision"),
                    "reason": row.get("reason", ""),
                    "scope": row.get("scope"),
                }
                for row in decision.use_case_decisions or []
            ],
            rules=context.rules,
            evidence_snapshot_ids=list(context.evidence_snapshot_ids),
            available_evidence_categories=context.available_evidence_categories,
            reviewer_user_id=decision.admin_user_id,
        )

    async def review_contract(self, case: ReviewCase) -> GovernanceReviewContext:
        """The exact contract an approval of this case will be validated against.

        Public so a caller that needs to know *which conditions this case has* — the
        Cases page's quick decision — reads the same methodology, the same criteria
        version and the same evidence categories that ``approve_for_publication`` will
        then check. A second resolver next to this one is how a page offers a condition
        list that the approval does not recognise.
        """

        return await self._review_context(case)

    async def review_blocker(self, case: ReviewCase) -> ShariaGovernanceError | None:
        """What stops this case being approved right now, or ``None`` if nothing does.

        The **one** answer to "can this be decided?". It runs the exact checks
        ``approve_for_publication`` runs, and returns the first refusal instead of
        raising it, so three different callers can ask the same question:

        * the research pipeline, before it marks a case ready for a reviewer;
        * ``mark_ready_for_review``, when a researcher hands a case over;
        * the review screens, to say plainly what is missing.

        Asking here rather than re-listing the conditions is the point. A queue that
        offers a decision the approval will then refuse is what produced a page full of
        errors: the pipeline had its own, shorter idea of "ready" and the approval had
        the real one.
        """

        try:
            await self._review_context(case)
        except ShariaGovernanceError as blocker:
            return blocker
        return None

    async def is_ready_for_review(self, case: ReviewCase) -> bool:
        """True when a reviewer pressing Approve on this case would be accepted."""

        return await self.review_blocker(case) is None

    async def undo_decision(
        self,
        case_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
        decision_id: UUID,
        previous_state: str,
        previous_publication_state: str,
    ) -> ReviewCase:
        """Put one case back where it was before a decision, and record that too.

        This is the way back from a mistake, not an eraser. The decision being undone
        stays in the review history for ever with its evidence and its reasoning; what
        changes is where the case sits, so it can be decided again properly.

        Three things make it refuse rather than guess, and each closes a way of quietly
        corrupting the record:

        * the decision being undone must still be the **latest** one on the case, so an
          undo cannot silently reverse somebody else's later work;
        * a **published** Passport is never undone here — it is already in front of
          customers, and the recorded way to withdraw it is a safety hold;
        * the state being restored must be one the case can really be in.
        """

        admin = await self._require_permission(admin_user_id, "REVIEWER")
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        if len(reason.strip()) < 10:
            raise ShariaGovernanceError(
                "undo_reason_required", "Record why this decision is being undone."
            )
        latest = await self.session.scalar(
            select(ReviewDecision)
            .where(ReviewDecision.review_case_id == case.id)
            .order_by(ReviewDecision.decision_version.desc())
            .limit(1)
        )
        if latest is None or latest.id != decision_id:
            raise ShariaGovernanceError(
                "undo_superseded",
                "A newer decision was recorded on this case, so the earlier one can no "
                "longer be undone. Reopen the case instead.",
            )
        if case.state == "published" or case.publication_state == "published":
            raise ShariaGovernanceError(
                "undo_published_blocked",
                "This Passport is already published to customers. Place a safety hold "
                "rather than undoing the decision.",
            )
        if previous_state not in ALLOWED_UNDO_STATES:
            raise ShariaGovernanceError(
                "undo_state_unknown",
                "The state this case came from cannot be restored automatically.",
            )
        now = datetime.now(UTC)
        restored_from = case.state
        case.state = previous_state
        case.publication_state = previous_publication_state
        case.done_at = None
        case.next_reminder_at = now + timedelta(
            hours=self.settings.sharia_review_reminder_hours
        )
        self.session.add(
            ShariaReviewAssignmentEvent(
                review_case_id=case.id,
                actor_user_id=admin.id,
                previous_assignee_id=case.assigned_reviewer_id,
                assigned_reviewer_id=case.assigned_reviewer_id,
                action="decision_undone",
                priority=case.priority,
                reason=reason.strip(),
                created_at=now,
            )
        )
        self._audit(
            admin.id,
            "sharia.review_decision_undone",
            "sharia_review_case",
            str(case.id),
            {
                "undone_decision_id": str(decision_id),
                "undone_decision": latest.decision,
                "restored_from": restored_from,
                "restored_to": previous_state,
                "decision_record_retained": True,
            },
        )
        await self.session.flush()
        return case

    async def _open_case(self, case_id: UUID) -> ReviewCase:
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise ShariaGovernanceError("case_not_found", "Review case not found.")
        if case.state in TERMINAL_CASE_STATES or case.done_at is not None:
            raise ShariaGovernanceError(
                "case_already_decided", "This review already has a terminal decision."
            )
        return case


    async def _decision(
        self,
        case: ReviewCase,
        *,
        admin_user_id: UUID,
        methodology_id: UUID | None,
        decision: str,
        reason: str,
        evidence_snapshot_ids: list[str],
        methodology_version: str | None = None,
        methodology_criteria_version: str | None = None,
        methodology_criteria_hash: str | None = None,
        criterion_decisions: list[dict] | None = None,
        use_case_decisions: list[dict] | None = None,
        qualifications: list[str] | None = None,
        acknowledged_gaps: list[str] | None = None,
        ai_analysis_snapshot_id: UUID | None = None,
        actor_role: str = "REVIEWER",
        security_metadata: dict[str, Any] | None = None,
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
            methodology_version=methodology_version,
            methodology_criteria_version=methodology_criteria_version,
            methodology_criteria_hash=methodology_criteria_hash,
            decision=decision,
            reason=reason.strip(),
            evidence_snapshot_ids=evidence_snapshot_ids,
            criterion_decisions=criterion_decisions or [],
            use_case_decisions=use_case_decisions or [],
            qualifications=qualifications or [],
            acknowledged_gaps=acknowledged_gaps or [],
            ai_analysis_snapshot_id=ai_analysis_snapshot_id,
            actor_role=actor_role,
            application_version=getattr(
                self.settings, "application_version", "development"
            ),
            security_metadata=security_metadata
            or {"source": "authenticated_system_brain"},
            decision_version=version,
            created_at=datetime.now(UTC),
        )
        self.session.add(row)
        await self.session.flush()
        row.integrity_hash = _hash_json(
            {
                "id": str(row.id),
                "case_id": str(case.id),
                "actor": str(admin_user_id),
                "role": actor_role,
                "methodology_id": str(methodology_id) if methodology_id else None,
                "methodology_version": methodology_version,
                "methodology_criteria_version": methodology_criteria_version,
                "methodology_criteria_hash": methodology_criteria_hash,
                "decision": decision,
                "reason": row.reason,
                "evidence_snapshot_ids": evidence_snapshot_ids,
                "criterion_decisions": row.criterion_decisions,
                "use_case_decisions": row.use_case_decisions,
                "qualifications": row.qualifications,
                "acknowledged_gaps": row.acknowledged_gaps,
                "decision_version": version,
                "created_at": row.created_at.isoformat(),
            }
        )
        await self.session.flush()
        return row

    async def _case_evidence_ids(self, case: ReviewCase) -> list[str]:
        if case.dossier_id is None:
            return []
        dossier = await self.session.get(AssetResearchDossier, case.dossier_id)
        return list(dossier.source_snapshot_ids) if dossier else []

    async def _assessment_sources(
        self,
        methodology: ShariaMethodology,
        external: ExternalAssessment,
        dossier: AssetResearchDossier,
    ) -> list[EvidenceSourceInput]:
        rules = MethodologyRulesDefinition.model_validate(methodology.rules_json)
        if rules.source_adapter == "sc_malaysia":
            if external.decision_date is None or external.sac_meeting_number is None:
                raise ShariaGovernanceError(
                    "sc_reference_incomplete",
                    "The SC meeting reference and decision date are required.",
                )
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
        elif rules.source_adapter == "fasset":
            sources = [
                EvidenceSourceInput(
                    source_type="published_authority_profile",
                    title=f"Fasset Shariah Report: {external.asset_name}",
                    publisher="Fasset",
                    source_url=HttpUrl(external.source_url),
                    retrieved_at=external.retrieval_date,
                    evidence_category="official_fasset_reference",
                    evidence_summary=(
                        f"Explicit retained verdict: {external.exact_status_wording}; "
                        f"source reference: {external.source_reference or 'asset profile'}."
                    ),
                )
            ]
        elif rules.source_adapter == "srb":
            sources = [
                EvidenceSourceInput(
                    source_type="external_preliminary_research_reference",
                    title=f"Shariah Review Bureau reference: {external.asset_name}",
                    publisher="Shariyah Review Bureau W.L.L.",
                    source_url=HttpUrl(external.source_url),
                    retrieved_at=external.retrieval_date,
                    evidence_category="official_external_reference",
                    evidence_summary=(
                        f"Retained status: {external.exact_status_wording}; "
                        f"source reference: {external.source_reference or 'not dated'}. "
                        "Restricted source report content is not reproduced."
                    ),
                )
            ]
        else:
            raise ShariaGovernanceError(
                "source_adapter_unsupported",
                "No evidence adapter is configured for this methodology.",
            )
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
                            "Official-source factual information captured for the Hilal Markets "
                            "profile; it is not unpublished authority reasoning."
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
        profile = dict(dossier.factual_profile or output.get("profile") or {})
        requirements = MethodologyEvidenceRequirements.model_validate(
            methodology.evidence_requirements_json
        )
        use_decisions = {
            str(item["key"]): dict(item) for item in decision.use_case_decisions or []
        }
        evidence_expires_at = _as_utc(external.retrieval_date) + timedelta(
            days=requirements.maximum_source_age_days
        )
        next_governance_review = published_at + timedelta(
            days=requirements.review_cadence_days
        )
        rules = MethodologyRulesDefinition.model_validate(methodology.rules_json)
        source_label = {
            "sc_malaysia": "SC Malaysia SAC reference",
            "fasset": "Fasset Shariah Report",
            "srb": "Shariah Review Bureau reference",
        }.get(rules.source_adapter, "external methodology reference")
        source_profile_facts: dict[str, Any] = {}
        if rules.source_adapter == "fasset":
            if external.source_row_id is None:
                source_profile_facts = dict(external.structured_facts or {})
            elif external.source_detail_extraction_state in {
                "FETCHED_AND_VERIFIED",
                "VERIFIED",
            }:
                source_profile_facts = dict(
                    external.source_detail_fields or {}
                )
        authority_limitations = [
            "The source verdict is retained exactly and is not expanded to an "
            "unreviewed use.",
            "Staking, lending, yield, leverage, derivatives, wrapped assets, "
            "and bridges remain separate use decisions.",
        ]
        authority_reference: dict[str, Any] = {
            "label": f"{source_label}: {external.exact_status_wording}",
            "exact_wording": external.exact_status_wording,
            "authority": external.source_authority,
            "source_family": external.source_family,
            "source_reference": external.source_reference,
            "source_url": external.source_url,
            "retrieval_date": external.retrieval_date.isoformat(),
            "regulatory_scope": external.regulatory_scope,
            "published_profile_facts": source_profile_facts,
            "rights_state": external.rights_state,
            "limitations": authority_limitations,
        }
        if rules.source_adapter == "sc_malaysia":
            authority_limitations.append(
                "The list page does not publish detailed coin-specific reasoning; none was "
                "reconstructed."
            )
        if rules.source_adapter == "srb":
            authority_limitations.append(
                "Source report wording and summaries remain withheld unless written permission "
                "or legal clearance explicitly permits commercial display."
            )
        if external.sac_meeting_number:
            authority_reference["sac_meeting_number"] = external.sac_meeting_number
        if external.decision_date:
            authority_reference["decision_date"] = external.decision_date.isoformat()
        return {
            "passport_version": 1,
            "key_reasons": [decision.reason],
            "official_methodology_reference": authority_reference,
            "official_sc_malaysia_reference": (
                authority_reference if rules.source_adapter == "sc_malaysia" else {}
            ),
            "official_fasset_reference": (
                authority_reference if rules.source_adapter == "fasset" else {}
            ),
            "hilalmarkets_factual_information_profile": {
                **profile,
                "official_source_snapshot_ids": list(dossier.source_snapshot_ids),
                "missing_information": output.get("missing_evidence") or [],
                "contradictions": output.get("contradictions") or [],
                "last_evidence_verification": dossier.completed_at.isoformat()
                if dossier.completed_at
                else None,
                "source_monitor_scan_frequency_hours": (
                    self.settings.sharia_source_scan_interval_hours
                ),
                "evidence_expires_at": evidence_expires_at.isoformat(),
                "next_governance_review_at": next_governance_review.isoformat(),
                "notice": (
                    "Hilal Markets factual research is not SC Malaysia's unpublished reasoning "
                    "and is not an independent religious ruling."
                    if rules.source_adapter == "sc_malaysia"
                    else (
                        "Hilal Markets factual research is not Fasset's unpublished reasoning "
                        "and is not an independent religious ruling."
                        if rules.source_adapter == "fasset"
                        else (
                            "Hilal Markets factual research is separate from the "
                            "Shariah Review Bureau reference and is not an independent "
                            "religious ruling."
                        )
                    )
                ),
            },
            "separate_use_status": use_decisions,
            "reviewed_dimensions": list(decision.criterion_decisions or []),
            "methodology_result": {
                "methodology_code": methodology.code,
                "methodology_version": methodology.version,
                "criteria_version": decision.methodology_criteria_version,
                "criteria_hash": decision.methodology_criteria_hash,
                "result": (
                    f"Official asset-level {source_label}: {external.exact_status_wording}"
                ),
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
        methodology = (
            await self.session.get(ShariaMethodology, case.methodology_id)
            if case.methodology_id
            else None
        )
        affected_watch_plans = 0
        affected_users = 0
        if asset is not None:
            impact_filters = [MonitorShariaAssetState.canonical_asset == asset.symbol]
            if methodology is not None:
                impact_filters.append(
                    MonitorShariaAssetState.methodology_id == methodology.id
                )
            impact = await self.session.execute(
                select(
                    func.count(func.distinct(MonitorShariaAssetState.strategy_id)),
                    func.count(func.distinct(MonitorShariaAssetState.user_id)),
                ).where(*impact_filters)
            )
            affected_watch_plans, affected_users = impact.one()
        title = {
            "new_review_required": "New Sharia asset review required",
            "review_reminder": "Sharia review reminder",
            "publication_success": "Sharia asset publication completed",
            "rejection_stored": "Sharia asset review stored without publication",
            "material_change": "Material Sharia source change requires review",
            "user_factual_report": "User Passport report requires review",
        }.get(notification_type, "Sharia review update")
        lines = [
            title,
            f"Case: {case.case_reference}",
            f"Asset: {(asset.name + ' (' + asset.symbol + ')') if asset else case.title}",
            f"State: {case.state.replace('_', ' ').title()}",
        ]
        if external:
            authority_details = [
                value
                for value in (
                    external.source_reference,
                    external.sac_meeting_number,
                    external.decision_date.isoformat()
                    if external.decision_date
                    else None,
                )
                if value
            ]
            lines.extend(
                [
                    f"Authority reference: {external.exact_status_wording}",
                    "Source detail: " + " | ".join(authority_details),
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
        if methodology:
            lines.append(f"Methodology: {methodology.name} v{methodology.version}")
        lines.append(
            f"Affected Watchlists/users: {int(affected_watch_plans or 0)}/"
            f"{int(affected_users or 0)}"
        )
        lines.extend(
            [
                f"Human review: {case.human_review_reason[:220]}",
                f"Risk: {case.risk_severity.upper()}",
                f"Created: {case.created_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            ]
        )
        url = (
            f"{str(self.settings.public_base_url).rstrip('/')}/dashboard/system-brain/cases/{case.id}"
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




def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _value_at_path(values: dict[str, object], path: str) -> object | None:
    current: object | None = values
    for part in path.split("."):
        current = (
            current.get(part)
            if isinstance(current, dict)
            else getattr(current, part, None)
        )
        if current is None:
            return None
    return current
