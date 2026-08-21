import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AIAnalysisSnapshot,
    AssetResearchDossier,
    AssetShariaAssessment,
    CanonicalAsset,
    ExternalAssessment,
    OfficialSource,
    PublishedAssetAssessment,
    ReviewCase,
    ShariaMonitoringRun,
    SourceChangeEvent,
    SourceSnapshot,
)
from ai_market_monitor.db.models.enums import ComplianceChangeSeverity, ReviewCaseType
from ai_market_monitor.schemas.sharia import ComplianceChangeIngestRequest
from ai_market_monitor.services import sharia_dossier_state as dossier_state
from ai_market_monitor.services.compliance_watch import ComplianceWatchService
from ai_market_monitor.services.sharia_governance import (
    ShariaAdminTelegramService,
    ShariaGovernanceService,
)
from ai_market_monitor.services.sharia_research import (
    AI_PROMPT_VERSION,
    ShariaAIResearchClient,
    ShariaResearchError,
    ShariaResearchPipeline,
)


@dataclass(frozen=True, slots=True)
class _MonitoringTarget:
    scope_key: str
    canonical_asset_id: UUID
    external_assessment_id: UUID
    methodology_id: UUID | None
    publication: PublishedAssetAssessment | None
    baseline_dossier_id: UUID | None


class ShariaSourceMonitoringService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        research_pipeline: ShariaResearchPipeline | None = None,
        ai_client: ShariaAIResearchClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.pipeline = research_pipeline or ShariaResearchPipeline(session, settings)
        self.ai = ai_client or self.pipeline.ai

    async def run_due(self) -> dict[str, int]:
        publications = list(
            (
                await self.session.scalars(
                    select(PublishedAssetAssessment).where(
                        PublishedAssetAssessment.is_active.is_(True),
                        PublishedAssetAssessment.publication_state == "published",
                    )
                )
            ).all()
        )
        published_external_ids = {
            row.external_assessment_id
            for row in publications
        }
        internal_cases = list(
            (
                await self.session.scalars(
                    select(ReviewCase).where(
                        ReviewCase.state == "stored",
                        ReviewCase.publication_state
                        == "approved_internal_only",
                        ReviewCase.canonical_asset_id.is_not(None),
                        ReviewCase.external_assessment_id.is_not(None),
                    )
                )
            ).all()
        )
        targets = [
            _MonitoringTarget(
                scope_key=f"publication:{publication.id}",
                canonical_asset_id=publication.canonical_asset_id,
                external_assessment_id=publication.external_assessment_id,
                methodology_id=None,
                publication=publication,
                baseline_dossier_id=publication.dossier_id,
            )
            for publication in publications
        ]
        targets.extend(
            _MonitoringTarget(
                scope_key=f"internal-approval:{case.id}",
                canonical_asset_id=case.canonical_asset_id,
                external_assessment_id=case.external_assessment_id,
                methodology_id=case.methodology_id,
                publication=None,
                baseline_dossier_id=case.dossier_id,
            )
            for case in internal_cases
            if case.external_assessment_id not in published_external_ids
            and case.canonical_asset_id is not None
            and case.external_assessment_id is not None
        )
        monitored = 0
        changed = 0
        cases = 0
        failures = 0
        for target in targets:
            result = await self._monitor_one(target)
            if result == "not_due":
                continue
            monitored += 1
            changed += result in {"changed", "review_created"}
            cases += result == "review_created"
            failures += result == "failed"
        return {
            "published_assets_considered": len(publications),
            "approved_internal_assets_considered": len(internal_cases),
            "assets_monitored": monitored,
            "assets_changed": changed,
            "review_cases_created": cases,
            "failures": failures,
        }

    async def _monitor_one(self, target: _MonitoringTarget) -> str:
        now = datetime.now(UTC)
        window_seconds = self.settings.sharia_source_scan_interval_hours * 3600
        window = int(now.timestamp() // window_seconds)
        run_key = f"source-monitor:{target.scope_key}:{window}"
        if await self.session.scalar(
            select(ShariaMonitoringRun.id).where(
                ShariaMonitoringRun.idempotency_key == run_key
            )
        ):
            return "not_due"
        asset = await self.session.get(
            CanonicalAsset,
            target.canonical_asset_id,
        )
        external = await self.session.get(
            ExternalAssessment,
            target.external_assessment_id,
        )
        if asset is None or external is None:
            return "failed"
        sources = list(
            (
                await self.session.scalars(
                    select(OfficialSource)
                    .where(
                        OfficialSource.canonical_asset_id == asset.id,
                        OfficialSource.verification_state == "verified",
                        OfficialSource.is_active.is_(True),
                    )
                    .order_by(OfficialSource.priority.asc(), OfficialSource.source_url.asc())
                )
            ).all()
        )
        run = ShariaMonitoringRun(
            run_kind="source_monitor",
            canonical_asset_id=asset.id,
            idempotency_key=run_key,
            status="running",
            started_at=now,
            next_due_at=now
            + timedelta(hours=self.settings.sharia_source_scan_interval_hours),
            items_attempted=len(sources),
        )
        self.session.add(run)
        await self.session.flush()
        snapshots: list[SourceSnapshot] = []
        for source in sources:  # Sequential by design.
            snapshots.append(await self.pipeline._fetch_source(run, source))
        successful = [row for row in snapshots if row.fetch_status == "success"]
        changed = [row for row in successful if row.is_material_change]
        sources_by_id = {row.id: row for row in sources}
        project_source_changed = any(
            snapshot.official_source_id is not None
            and sources_by_id.get(snapshot.official_source_id) is not None
            and sources_by_id[snapshot.official_source_id].category
            != "external_methodology_reference"
            for snapshot in changed
        )
        baseline_dossier = (
            await self.session.get(
                AssetResearchDossier,
                target.baseline_dossier_id,
            )
            if target.baseline_dossier_id is not None
            else None
        )
        baseline_profile = dict(
            (baseline_dossier.factual_profile if baseline_dossier else {})
            or {}
        )
        run.items_succeeded = len(successful)
        run.items_failed = len(snapshots) - len(successful)
        if not changed:
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            run.result_summary = {
                "material_changes": 0,
                "ai_calls": 0,
                "reason": "No meaningful official-source changes detected.",
            }
            await self.session.flush()
            return "unchanged"

        # What the reviewer is asked to look at is the changed pages **and** the retained
        # authority row that carries the published status. Leaving the authority snapshot
        # out is what made every change review undecidable: approval requires the imported
        # authority snapshot to be among the reviewed evidence, and a dossier listing only
        # the changed pages never contained it. The same list is what the factual analysis
        # reads, so the two can never describe different evidence.
        authority_snapshot = await self.session.get(
            SourceSnapshot, external.source_snapshot_id
        )
        reviewed = list(changed)
        if (
            authority_snapshot is not None
            and authority_snapshot.fetch_status == "success"
            and all(row.id != authority_snapshot.id for row in reviewed)
        ):
            reviewed.insert(0, authority_snapshot)
        evidence_hash = _hash_json(
            [{"id": str(row.id), "hash": row.content_hash} for row in reviewed]
        )
        dossier = AssetResearchDossier(
            canonical_asset_id=asset.id,
            external_assessment_id=external.id,
            monitoring_run_id=run.id,
            run_key=f"material-change:{asset.id}:{evidence_hash}",
            state=dossier_state.RESEARCHING,
            source_snapshot_ids=[str(row.id) for row in reviewed],
            evidence_completeness=(len(successful) / len(sources) if sources else 0),
            evidence_package_hash=evidence_hash,
            factual_profile={},
            limitations=[],
        )
        self.session.add(dossier)
        await self.session.flush()
        ai_row = AIAnalysisSnapshot(
            dossier_id=dossier.id,
            analysis_version=1,
            model=self.settings.sharia_ai_model,
            reasoning_effort=self.settings.sharia_ai_reasoning_effort,
            requested_service_tier=self.settings.sharia_ai_service_tier,
            prompt_version=AI_PROMPT_VERSION,
            input_snapshot_ids=[str(row.id) for row in reviewed],
            input_hash=evidence_hash,
            output={},
            usage={},
            status="running",
            retry_count=0,
            started_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        self.session.add(ai_row)
        await self.session.flush()
        try:
            result = await self.ai.analyze(
                await self._change_package(asset, external, reviewed)
            )
        except ShariaResearchError as exc:
            ai_row.status = "failed"
            ai_row.error_code = exc.code
            ai_row.error_detail = str(exc)[:2000]
            ai_row.completed_at = datetime.now(UTC)
            dossier.state = dossier_state.NEEDS_EVIDENCE
            dossier.limitations = [str(exc)]
            run.status = "failed"
            run.last_error_code = exc.code
            run.last_error_detail = str(exc)[:2000]
            run.completed_at = datetime.now(UTC)
            await self.session.flush()
            return "failed"

        analysis = result.analysis
        ai_row.output = analysis.model_dump(mode="json")
        ai_row.usage = result.usage
        ai_row.returned_service_tier = result.returned_service_tier
        ai_row.retry_count = result.retry_count
        ai_row.status = "completed"
        ai_row.completed_at = datetime.now(UTC)
        dossier.state = dossier_state.COMPLETE
        dossier.factual_profile = (
            analysis.profile.model_dump(mode="json")
            if project_source_changed
            else baseline_profile
        )
        dossier.missing_information_count = len(analysis.missing_evidence)
        dossier.contradiction_count = len(analysis.contradictions)
        dossier.limitations = analysis.explicit_limitations
        dossier.completed_at = datetime.now(UTC)
        self.pipeline._record_usage(result)

        review_required = analysis.human_review_required or (
            analysis.potential_impact_severity in {"high", "critical"}
        )
        review_case: ReviewCase | None = None
        for snapshot in changed:
            official_source = await self.session.get(
                OfficialSource, snapshot.official_source_id
            )
            if official_source is None:
                continue
            change_hash = _hash_json(
                {
                    "asset": str(asset.id),
                    "source": str(official_source.id),
                    "previous": str(snapshot.previous_snapshot_id),
                    "current": str(snapshot.id),
                }
            )
            event = await self.session.scalar(
                select(SourceChangeEvent).where(
                    SourceChangeEvent.change_hash == change_hash
                )
            )
            if event is None:
                event = SourceChangeEvent(
                    canonical_asset_id=asset.id,
                    official_source_id=official_source.id,
                    previous_snapshot_id=snapshot.previous_snapshot_id,
                    current_snapshot_id=snapshot.id,
                    change_type=analysis.change_type,
                    severity=analysis.potential_impact_severity,
                    summary=analysis.human_review_reason,
                    meaningful_diff=snapshot.meaningful_diff,
                    potentially_material=review_required,
                    status="awaiting_review" if review_required else "recorded",
                    change_hash=change_hash,
                )
                self.session.add(event)
                await self.session.flush()
            if review_required and review_case is None:
                review_case = await self._material_review_case(
                    asset,
                    external,
                    dossier,
                    target,
                    analysis.human_review_reason,
                    analysis.potential_impact_severity,
                )
            if review_case is not None:
                event.review_case_id = review_case.id

        if review_case is not None:
            if target.publication is not None:
                severity = (
                    ComplianceChangeSeverity.CRITICAL
                    if analysis.potential_impact_severity
                    in {"high", "critical"}
                    else ComplianceChangeSeverity.REVIEW_REQUIRED
                )
                await ComplianceWatchService(
                    self.session,
                    self.settings,
                ).ingest_change(
                    ComplianceChangeIngestRequest(
                        canonical_asset=asset.symbol,
                        change_type="official_source_material_change",
                        severity=severity,
                        source_reference=(
                            changed[0].source_url if changed else None
                        ),
                        title=f"Official source change for {asset.name}",
                        summary=analysis.human_review_reason,
                        structured_change={
                            "review_case_id": str(review_case.id),
                            "source_snapshot_ids": [
                                str(row.id) for row in changed
                            ],
                            "ai_does_not_change_status": True,
                        },
                        detected_at=datetime.now(UTC),
                        detection_method=(
                            "sequential_source_hash_and_bounded_ai_review"
                        ),
                        confidence_label="verified_source",
                        idempotency_key=(
                            f"source-change-review:{review_case.id}"
                        ),
                    ),
                    actor_user_id=None,
                )
            await ShariaAdminTelegramService(self.session, self.settings).enqueue(
                review_case,
                notification_type="material_change",
                idempotency_key=f"material-change:{review_case.id}",
            )
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.result_summary = {
            "material_changes": len(changed),
            "ai_calls": 1,
            "review_case_id": str(review_case.id) if review_case else None,
            "published_status_changed_by_ai": False,
            "project_source_changed": project_source_changed,
            "external_methodology_source_changed": any(
                snapshot.official_source_id is not None
                and sources_by_id.get(snapshot.official_source_id) is not None
                and sources_by_id[
                    snapshot.official_source_id
                ].category
                == "external_methodology_reference"
                for snapshot in changed
            ),
        }
        await self.session.flush()
        return "review_created" if review_case else "changed"

    async def _material_review_case(
        self,
        asset: CanonicalAsset,
        external: ExternalAssessment,
        dossier: AssetResearchDossier,
        target: _MonitoringTarget,
        reason: str,
        severity: str,
    ) -> ReviewCase:
        key = (
            f"material-review:{target.scope_key}:"
            f"{dossier.evidence_package_hash}"
        )
        existing = await self.session.scalar(
            select(ReviewCase).where(ReviewCase.idempotency_key == key)
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        assessment = (
            await self.session.get(
                AssetShariaAssessment,
                target.publication.asset_assessment_id,
            )
            if target.publication is not None
            else None
        )
        case = ReviewCase(
            case_reference=f"CHG-{asset.symbol}-{str(dossier.id)[:8].upper()}",
            case_type=ReviewCaseType.MATERIAL_SOURCE_CHANGE,
            state="ready_for_review",
            publication_state=(
                "change_under_review"
                if target.publication is not None
                else "internal_change_under_review"
            ),
            canonical_asset_id=asset.id,
            external_assessment_id=external.id,
            dossier_id=dossier.id,
            methodology_id=(
                assessment.methodology_id
                if assessment is not None
                else target.methodology_id
            ),
            title=f"Material source change: {asset.name} ({asset.symbol})",
            priority="urgent" if severity == "critical" else "high",
            risk_severity=severity,
            human_review_reason=reason,
            requested_evidence=[],
            idempotency_key=key,
            due_at=now + timedelta(hours=self.settings.sharia_review_sla_hours),
            source_freshness_deadline=now
            + timedelta(hours=self.settings.sharia_source_scan_interval_hours),
            next_reminder_at=now,
        )
        self.session.add(case)
        await self.session.flush()
        # Same rule as the initial review: a case is only called ready when the approval
        # path would accept it. Asked of that path, never listed again here.
        blocker = await ShariaGovernanceService(
            self.session, self.settings
        ).review_blocker(case)
        if blocker is not None:
            case.state = "needs_evidence"
            case.requested_evidence = sorted(
                {*(case.requested_evidence or []), str(blocker)}
            )
            await self.session.flush()
        return case

    async def _change_package(
        self,
        asset: CanonicalAsset,
        external: ExternalAssessment,
        changed: list[SourceSnapshot],
    ) -> dict:
        previous_ids = [
            row.previous_snapshot_id for row in changed if row.previous_snapshot_id is not None
        ]
        previous = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(SourceSnapshot).where(SourceSnapshot.id.in_(previous_ids))
                )
            ).all()
        }
        source_ids = {
            row.official_source_id
            for row in changed
            if row.official_source_id is not None
        }
        sources = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(OfficialSource).where(
                        OfficialSource.id.in_(source_ids)
                    )
                )
            ).all()
        }
        changed_sources = []
        for row in changed:
            source = (
                sources.get(row.official_source_id)
                if row.official_source_id is not None
                else None
            )
            category = (
                source.category
                if source is not None
                else "unknown_official_source"
            )
            rights_restricted = (
                category == "external_methodology_reference"
                and not external.commercial_display_allowed
            )
            changed_sources.append(
                {
                    "snapshot_id": str(row.id),
                    "url": row.source_url,
                    "source_category": category,
                    "previous_text": (
                        ""
                        if rights_restricted
                        else previous[
                            row.previous_snapshot_id
                        ].normalized_text[:60_000]
                        if row.previous_snapshot_id in previous
                        else ""
                    ),
                    "current_text": (
                        ""
                        if rights_restricted
                        else row.normalized_text[:60_000]
                    ),
                    "deterministic_diff": row.meaningful_diff,
                    "rights_restricted_content_withheld": (
                        rights_restricted
                    ),
                }
            )
        return {
            "asset": {"id": str(asset.id), "name": asset.name, "symbol": asset.symbol},
            "external_authority_reference": {
                "source_family": external.source_family,
                "authority": external.source_authority,
                "exact_status_wording": external.exact_status_wording,
                "source_reference": external.source_reference,
                "meeting": external.sac_meeting_number,
                "decision_date": (
                    external.decision_date.isoformat()
                    if external.decision_date
                    else None
                ),
                "scope": external.regulatory_scope,
                "published_profile_facts": (
                    {}
                    if external.source_row_id is not None
                    else dict(external.structured_facts or {})
                ),
                "rights_state": external.rights_state,
            },
            "changed_sources": changed_sources,
            "instruction": (
                "Assess factual change impact and human-review need only. "
                "External-methodology source text is not project factual "
                "evidence and must never be used to create project facts or "
                "reconstruct authority reasoning. Do not alter or issue a "
                "Sharia status."
            ),
        }


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
