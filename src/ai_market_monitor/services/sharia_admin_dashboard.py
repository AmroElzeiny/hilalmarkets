from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AIAnalysisSnapshot,
    AssetResearchDossier,
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
    ShariaMonitoringRun,
    ShariaReviewAssignmentEvent,
    SourceSnapshot,
    TelegramNotificationAttempt,
    User,
)
from ai_market_monitor.db.models.enums import (
    ReviewCaseType,
    ShariaMethodologyStatus,
    UserRole,
)
from ai_market_monitor.schemas.sharia_methodology import MethodologyRulesDefinition


class ShariaAdminDashboardService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def overview(self) -> dict:
        now = datetime.now(UTC)
        open_cases = list(
            (
                await self.session.scalars(
                    select(ReviewCase).where(ReviewCase.done_at.is_(None))
                )
            ).all()
        )
        publications = list(
            (
                await self.session.scalars(select(PublishedAssetAssessment))
            ).all()
        )
        decisions = list(
            (
                await self.session.scalars(
                    select(ReviewDecision).order_by(ReviewDecision.created_at.asc())
                )
            ).all()
        )
        runs = list(
            (
                await self.session.scalars(
                    select(ShariaMonitoringRun)
                    .order_by(ShariaMonitoringRun.created_at.desc())
                    .limit(500)
                )
            ).all()
        )
        ai_rows = list(
            (
                await self.session.scalars(
                    select(AIAnalysisSnapshot)
                    .order_by(AIAnalysisSnapshot.created_at.desc())
                    .limit(500)
                )
            ).all()
        )
        snapshots = list(
            (
                await self.session.scalars(
                    select(SourceSnapshot)
                    .where(SourceSnapshot.fetch_status == "success")
                    .order_by(SourceSnapshot.retrieved_at.desc())
                    .limit(1000)
                )
            ).all()
        )
        completed = [
            row
            for row in decisions
            if row.decision
            in {
                "approve_and_publish",
                "approved",
                "approved_with_qualifications",
                "reject_and_store",
            }
        ]
        case_by_id = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(ReviewCase).where(
                        ReviewCase.id.in_([item.review_case_id for item in completed])
                    )
                )
            ).all()
        } if completed else {}
        turnaround = [
            (
                _aware(item.created_at)
                - _aware(case_by_id[item.review_case_id].created_at)
            ).total_seconds()
            for item in completed
            if item.review_case_id in case_by_id
        ]
        state_counts = Counter(row.state for row in open_cases)
        publication_counts = Counter(row.publication_state for row in publications)
        run_status = Counter(row.status for row in runs)
        ai_outcomes = Counter(
            str((row.output or {}).get("potential_impact_severity") or row.status)
            for row in ai_rows
        )
        weekly = Counter(
            _aware(row.created_at).strftime("%Y-W%W") for row in decisions
        )
        severity = Counter(row.risk_severity for row in open_cases)
        oldest = sorted(open_cases, key=lambda row: row.created_at)[:10]
        last_fresh = max((_aware(row.retrieved_at) for row in snapshots), default=None)
        return {
            "generated_at": now,
            "metrics": {
                "pending_initial_reviews": sum(
                    row.case_type == ReviewCaseType.INITIAL_ASSET_REVIEW
                    for row in open_cases
                ),
                "waiting_over_six_hours": sum(
                    _aware(row.created_at) <= now - timedelta(hours=6) for row in open_cases
                ),
                "approved_published": sum(row.is_active for row in publications),
                "rejected_retained": await self.session.scalar(
                    select(func.count(ReviewCase.id)).where(ReviewCase.state == "rejected")
                )
                or 0,
                "published_under_monitoring": sum(
                    row.is_active and row.publication_state == "published" for row in publications
                ),
                "material_source_changes": sum(
                    row.case_type == ReviewCaseType.MATERIAL_SOURCE_CHANGE
                    for row in open_cases
                ),
                "ai_human_reviews": sum(
                    bool((row.output or {}).get("human_review_required")) for row in ai_rows
                ),
                "scraper_failures": sum(row.status == "failed" for row in runs),
                "average_review_hours": round(
                    (sum(turnaround) / len(turnaround) / 3600) if turnaround else 0, 2
                ),
                "evidence_freshness_hours": round(
                    (now - last_fresh).total_seconds() / 3600 if last_fresh else 0, 2
                ),
                "overdue_reviews": sum(
                    row.due_at is not None
                    and _aware(row.due_at) < now
                    and row.done_at is None
                    for row in open_cases
                ),
                "official_rows_imported": await self.session.scalar(
                    select(func.count(ExternalAssessment.id))
                )
                or 0,
                "import_runs": sum(row.run_kind == "sc_import" for row in runs),
            },
            "charts": {
                "pipeline": _chart(state_counts),
                "decisions_by_week": _chart(weekly),
                "published_distribution": _chart(publication_counts),
                "source_health": _chart(run_status),
                "change_severity": _chart(severity),
                "ai_outcomes": _chart(ai_outcomes),
                "open_case_age": [
                    {
                        "label": row.case_reference,
                        "value": round((now - _aware(row.created_at)).total_seconds() / 3600, 1),
                    }
                    for row in oldest
                ],
            },
            "recent_cases": await self.list_cases(limit=8),
        }

    async def reviewer_overview(self) -> dict:
        """Return only evidence that can change an administrator's next action."""
        now = datetime.now(UTC)
        cases = await self.list_cases(limit=100)
        open_cases = [item for item in cases if item["done_at"] is None]
        runs = list(
            (
                await self.session.scalars(
                    select(ShariaMonitoringRun)
                    .order_by(ShariaMonitoringRun.created_at.desc())
                    .limit(150)
                )
            ).all()
        )
        snapshots = list(
            (
                await self.session.scalars(
                    select(SourceSnapshot)
                    .order_by(SourceSnapshot.retrieved_at.desc())
                    .limit(150)
                )
            ).all()
        )
        analyses = list(
            (
                await self.session.scalars(
                    select(AIAnalysisSnapshot)
                    .order_by(AIAnalysisSnapshot.created_at.desc())
                    .limit(150)
                )
            ).all()
        )
        deliveries = list(
            (
                await self.session.scalars(
                    select(TelegramNotificationAttempt)
                    .order_by(TelegramNotificationAttempt.created_at.desc())
                    .limit(150)
                )
            ).all()
        )
        publications = list(
            (
                await self.session.scalars(
                    select(PublishedAssetAssessment)
                    .order_by(PublishedAssetAssessment.published_at.desc())
                    .limit(150)
                )
            ).all()
        )
        attention = sorted(
            open_cases,
            key=lambda item: (
                item["priority"] != "urgent",
                not item["overdue"],
                item["created_at"],
            ),
        )
        urgent = [
            item
            for item in open_cases
            if item["priority"] == "urgent" or item["overdue"]
        ]
        incomplete = [
            item
            for item in open_cases
            if item["evidence_state"] in {"stale", "incomplete", "unavailable"}
        ]
        failed_count = (
            sum(item.status == "failed" for item in runs)
            + sum(item.status in {"failed", "invalid"} for item in analyses)
            + sum(
                item.status in {"failed", "retryable", "permanent_failure"}
                for item in deliveries
            )
            + sum(item.publication_state == "failed" for item in publications)
        )
        safety_holds = [
            item for item in open_cases if item["state"] == "safety_hold"
        ]
        last_source_success = next(
            (
                _aware(item.retrieved_at)
                for item in snapshots
                if item.fetch_status == "success"
            ),
            None,
        )
        last_pipeline_success = next(
            (
                _aware(item.completed_at)
                for item in runs
                if item.status in {"completed", "success"} and item.completed_at
            ),
            None,
        )
        overall_status = (
            "critical"
            if safety_holds
            else "degraded"
            if failed_count or urgent
            else "healthy"
        )
        return {
            "generated_at": now,
            "overall_status": overall_status,
            "status_message": _overview_message(
                overall_status=overall_status,
                review_count=len(attention),
                safety_hold_count=len(safety_holds),
            ),
            "last_official_source_check": last_source_success,
            "last_review_pipeline_run": last_pipeline_success,
            "active_critical_hold": safety_holds[0] if safety_holds else None,
            "action_metrics": [
                {
                    "key": "urgent",
                    "label": "Urgent reviews",
                    "count": len(urgent),
                    "detail": "Overdue or explicitly urgent cases",
                },
                {
                    "key": "evidence",
                    "label": "Evidence needs attention",
                    "count": len(incomplete),
                    "detail": "Stale, incomplete, or unavailable evidence",
                },
                {
                    "key": "failures",
                    "label": "Failed operations",
                    "count": failed_count,
                    "detail": "Jobs, research, publication, or delivery failures",
                },
                {
                    "key": "holds",
                    "label": "Safety holds",
                    "count": len(safety_holds),
                    "detail": "Assets blocked pending human review",
                },
            ],
            "needs_attention": attention[:30],
            "health": [
                _health_row(
                    "Official sources",
                    last_success=last_source_success,
                    failures=sum(item.fetch_status != "success" for item in snapshots),
                    has_records=bool(snapshots),
                ),
                _health_row(
                    "Research pipeline",
                    last_success=last_pipeline_success,
                    failures=sum(item.status == "failed" for item in runs),
                    has_records=bool(runs),
                ),
                _health_row(
                    "Market-data providers",
                    last_success=next(
                        (
                            _aware(item.completed_at)
                            for item in runs
                            if item.completed_at
                            and "market" in item.run_kind.casefold()
                            and item.status in {"completed", "success"}
                        ),
                        None,
                    ),
                    failures=sum(
                        item.status == "failed"
                        and "market" in item.run_kind.casefold()
                        for item in runs
                    ),
                    has_records=any("market" in item.run_kind.casefold() for item in runs),
                ),
                _health_row(
                    "AI research service",
                    last_success=next(
                        (
                            _aware(item.completed_at or item.created_at)
                            for item in analyses
                            if item.status in {"completed", "success", "validated"}
                        ),
                        None,
                    ),
                    failures=sum(
                        item.status in {"failed", "invalid"} for item in analyses
                    ),
                    has_records=bool(analyses),
                ),
                _health_row(
                    "Workers and scheduler",
                    last_success=last_pipeline_success,
                    failures=sum(item.status == "failed" for item in runs),
                    has_records=bool(runs),
                ),
                _health_row(
                    "Publication service",
                    last_success=next(
                        (
                            _aware(item.published_at)
                            for item in publications
                            if item.published_at
                            and item.publication_state == "published"
                        ),
                        None,
                    ),
                    failures=sum(
                        item.publication_state == "failed" for item in publications
                    ),
                    has_records=bool(publications),
                ),
                _health_row(
                    "Admin notifications",
                    last_success=next(
                        (
                            _aware(item.delivered_at)
                            for item in deliveries
                            if item.delivered_at
                        ),
                        None,
                    ),
                    failures=sum(
                        item.status in {"failed", "retryable", "permanent_failure"}
                        for item in deliveries
                    ),
                    has_records=bool(deliveries),
                ),
            ],
        }

    async def list_cases(
        self,
        *,
        state: str | None = None,
        case_type: str | None = None,
        priority: str | None = None,
        assignee_id: UUID | None = None,
        deadline: str | None = None,
        asset_query: str | None = None,
        limit: int = 100,
        include_published: bool = False,
    ) -> list[dict]:
        query = select(ReviewCase).order_by(
            ReviewCase.done_at.is_not(None), ReviewCase.created_at.desc()
        )
        if state:
            query = query.where(ReviewCase.state == state)
        elif not include_published:
            # A completed publication belongs to the immutable case registry and
            # audit trail, not the human-attention queue. Keep it available when
            # an operator explicitly asks for the published state.
            query = query.where(ReviewCase.publication_state != "published")
        if case_type:
            query = query.where(ReviewCase.case_type == case_type)
        if priority:
            query = query.where(ReviewCase.priority == priority)
        if assignee_id:
            query = query.where(ReviewCase.assigned_reviewer_id == assignee_id)
        now = datetime.now(UTC)
        if deadline == "overdue":
            query = query.where(
                ReviewCase.done_at.is_(None),
                ReviewCase.due_at.is_not(None),
                ReviewCase.due_at < now,
            )
        elif deadline == "due_soon":
            query = query.where(
                ReviewCase.done_at.is_(None),
                ReviewCase.due_at.is_not(None),
                ReviewCase.due_at >= now,
                ReviewCase.due_at <= now + timedelta(hours=24),
            )
        rows = list((await self.session.scalars(query.limit(limit))).all())
        asset_ids = {row.canonical_asset_id for row in rows if row.canonical_asset_id}
        assets = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(CanonicalAsset).where(CanonicalAsset.id.in_(asset_ids))
                )
            ).all()
        } if asset_ids else {}
        assignee_ids = {row.assigned_reviewer_id for row in rows if row.assigned_reviewer_id}
        assignees = {
            row.id: row
            for row in (
                await self.session.scalars(select(User).where(User.id.in_(assignee_ids)))
            ).all()
        } if assignee_ids else {}
        dossier_ids = {row.dossier_id for row in rows if row.dossier_id}
        dossiers = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(AssetResearchDossier).where(
                        AssetResearchDossier.id.in_(dossier_ids)
                    )
                )
            ).all()
        } if dossier_ids else {}
        methodology_ids = {row.methodology_id for row in rows if row.methodology_id}
        methodologies = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(ShariaMethodology).where(
                        ShariaMethodology.id.in_(methodology_ids)
                    )
                )
            ).all()
        } if methodology_ids else {}
        publication_rows = list(
            (
                await self.session.scalars(
                    select(PublishedAssetAssessment).where(
                        PublishedAssetAssessment.canonical_asset_id.in_(asset_ids)
                    )
                )
            ).all()
        ) if asset_ids else []
        publications: dict[UUID, PublishedAssetAssessment] = {}
        for publication in publication_rows:
            current = publications.get(publication.canonical_asset_id)
            if current is None or publication.version > current.version:
                publications[publication.canonical_asset_id] = publication
        symbols = {
            assets[row.canonical_asset_id].symbol
            for row in rows
            if row.canonical_asset_id in assets
        }
        impact_rows = list(
            (
                await self.session.scalars(
                    select(MonitorShariaAssetState).where(
                        MonitorShariaAssetState.canonical_asset.in_(symbols)
                    )
                )
            ).all()
        ) if symbols else []
        impacts: dict[str, dict[str, set[UUID]]] = {}
        for impact in impact_rows:
            bucket = impacts.setdefault(
                impact.canonical_asset, {"users": set(), "strategies": set()}
            )
            bucket["users"].add(impact.user_id)
            bucket["strategies"].add(impact.strategy_id)
        result = [
            {
                "id": row.id,
                "reference": row.case_reference,
                "case_type": _label(row.case_type),
                "state": row.state,
                "publication_state": row.publication_state,
                "asset_name": assets[row.canonical_asset_id].name
                if row.canonical_asset_id in assets
                else row.title,
                "symbol": assets[row.canonical_asset_id].symbol
                if row.canonical_asset_id in assets
                else "Unresolved",
                "title": row.title,
                "priority": row.priority,
                "risk": row.risk_severity,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "done_at": row.done_at,
                "review_reason": row.human_review_reason,
                "age_hours": round(
                    (datetime.now(UTC) - _aware(row.created_at)).total_seconds() / 3600, 1
                ),
                "stage_hours": round(
                    (datetime.now(UTC) - _aware(row.updated_at)).total_seconds() / 3600, 1
                ),
                "assigned_reviewer_id": row.assigned_reviewer_id,
                "assigned_reviewer": (
                    assignees[row.assigned_reviewer_id].display_name
                    or str(row.assigned_reviewer_id)
                    if row.assigned_reviewer_id in assignees
                    else "Unassigned"
                ),
                "due_at": row.due_at,
                "source_freshness_deadline": row.source_freshness_deadline,
                "evidence_state": _evidence_state(
                    row=row,
                    dossier=dossiers.get(row.dossier_id) if row.dossier_id else None,
                    now=now,
                ),
                "evidence_completeness": round(
                    float(dossiers[row.dossier_id].evidence_completeness) * 100
                    if row.dossier_id in dossiers
                    else 0,
                    1,
                ),
                "methodology_name": (
                    methodologies[row.methodology_id].name
                    if row.methodology_id in methodologies
                    else "Not assigned"
                ),
                "methodology_version": (
                    methodologies[row.methodology_id].version
                    if row.methodology_id in methodologies
                    else None
                ),
                "publication_version": (
                    publications[row.canonical_asset_id].version
                    if row.canonical_asset_id in publications
                    else None
                ),
                "evidence_version": (
                    dossiers[row.dossier_id].evidence_package_hash[:12]
                    if row.dossier_id in dossiers
                    else None
                ),
                "next_action": _next_case_action(row.state),
                "overdue": bool(
                    row.done_at is None
                    and row.due_at is not None
                    and _aware(row.due_at) < datetime.now(UTC)
                ),
                "affected_users": len(
                    impacts.get(
                        assets[row.canonical_asset_id].symbol
                        if row.canonical_asset_id in assets
                        else "",
                        {"users": set()},
                    )["users"]
                ),
                "affected_watch_plans": len(
                    impacts.get(
                        assets[row.canonical_asset_id].symbol
                        if row.canonical_asset_id in assets
                        else "",
                        {"strategies": set()},
                    )["strategies"]
                ),
            }
            for row in rows
        ]
        if asset_query:
            needle = asset_query.strip().casefold()
            result = [
                item
                for item in result
                if any(
                    needle in str(value or "").casefold()
                    for value in (
                        item["id"],
                        item["reference"],
                        item["asset_name"],
                        item["symbol"],
                        item["title"],
                        item["state"],
                        item["publication_state"],
                        item["assigned_reviewer"],
                        item["methodology_name"],
                        item["methodology_version"],
                        item["publication_version"],
                        item["evidence_version"],
                    )
                )
            ]
        return result

    async def case_detail(self, case_id: UUID) -> dict:
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            raise LookupError("Review case not found")
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
        external_snapshot = (
            await self.session.get(
                SourceSnapshot,
                external.source_snapshot_id,
            )
            if external is not None
            else None
        )
        methodology = (
            await self.session.get(ShariaMethodology, case.methodology_id)
            if case.methodology_id
            else await self.session.scalar(
                select(ShariaMethodology)
                .where(ShariaMethodology.status == ShariaMethodologyStatus.ACTIVE)
                .order_by(ShariaMethodology.effective_from.desc())
                .limit(1)
            )
        )
        ai = (
            await self.session.scalar(
                select(AIAnalysisSnapshot)
                .where(AIAnalysisSnapshot.dossier_id == dossier.id)
                .order_by(AIAnalysisSnapshot.analysis_version.desc())
                .limit(1)
            )
            if dossier
            else None
        )
        snapshot_ids = [UUID(value) for value in (dossier.source_snapshot_ids if dossier else [])]
        snapshots = list(
            (
                await self.session.scalars(
                    select(SourceSnapshot)
                    .where(SourceSnapshot.id.in_(snapshot_ids))
                    .order_by(SourceSnapshot.retrieved_at.asc())
                )
            ).all()
        ) if snapshot_ids else []
        source_ids = {row.official_source_id for row in snapshots if row.official_source_id}
        sources = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(OfficialSource).where(OfficialSource.id.in_(source_ids))
                )
            ).all()
        } if source_ids else {}
        decisions = list(
            (
                await self.session.scalars(
                    select(ReviewDecision)
                    .where(ReviewDecision.review_case_id == case.id)
                    .order_by(ReviewDecision.created_at.asc())
                )
            ).all()
        )
        assignments = list(
            (
                await self.session.scalars(
                    select(ShariaReviewAssignmentEvent)
                    .where(ShariaReviewAssignmentEvent.review_case_id == case.id)
                    .order_by(ShariaReviewAssignmentEvent.created_at.asc())
                )
            ).all()
        )
        reviewer_grants = list(
            (
                await self.session.scalars(
                    select(ShariaGovernanceRoleGrant).where(
                        ShariaGovernanceRoleGrant.role.in_({"REVIEWER", "SYSTEM_ADMIN"}),
                        ShariaGovernanceRoleGrant.revoked_at.is_(None),
                    )
                )
            ).all()
        )
        reviewer_ids = {row.user_id for row in reviewer_grants}
        if not reviewer_ids:
            reviewer_ids = set(
                (
                    await self.session.scalars(
                        select(User.id).where(User.role == UserRole.ADMIN)
                    )
                ).all()
            )
        reviewers = list(
            (
                await self.session.scalars(
                    select(User).where(User.id.in_(reviewer_ids)).order_by(User.display_name)
                )
            ).all()
        ) if reviewer_ids else []
        audits = list(
            (
                await self.session.scalars(
                    select(AuditEvent)
                    .where(
                        AuditEvent.target_id.in_(
                            {str(case.id), *(str(item.id) for item in decisions)}
                        )
                    )
                    .order_by(AuditEvent.created_at.asc())
                )
            ).all()
        )
        output = dict(ai.output or {}) if ai else {}
        ai_profile = dict(output.get("profile") or {})
        retained_profile = dict((dossier.factual_profile if dossier else {}) or {})
        profile = dict(retained_profile or ai_profile)
        source_fields = dict(
            (
                (external.structured_facts or {}).get("source_fields")
                if external is not None
                else {}
            )
            or {}
        )
        source_supported_explanation = next(
            (
                str(source_fields[key]).strip()
                for key in (
                    "passport_source_statement",
                    "reason_summary_paraphrased",
                    "status_detail",
                    "attribution_note",
                )
                if source_fields.get(key)
            ),
            None,
        )
        missing_evidence = list(
            output.get("missing_evidence")
            or (retained_profile.get("missing_information") or [])
            or case.requested_evidence
            or []
        )
        contradictions = list(
            output.get("contradictions")
            or (retained_profile.get("contradictions") or [])
            or []
        )
        publication = await self.session.scalar(
            select(PublishedAssetAssessment)
            .where(
                PublishedAssetAssessment.canonical_asset_id == case.canonical_asset_id,
                PublishedAssetAssessment.is_active.is_(True),
            )
            .order_by(PublishedAssetAssessment.version.desc())
            .limit(1)
        ) if case.canonical_asset_id else None
        impact_rows = list(
            (
                await self.session.scalars(
                    select(MonitorShariaAssetState).where(
                        MonitorShariaAssetState.canonical_asset == asset.symbol
                    )
                )
            ).all()
        ) if asset else []
        latest_decision = decisions[-1] if decisions else None
        field_reviews = {
            str(item.get("field_key")): item
            for item in list(case.admin_notes or [])
            if item.get("entry_type") == "ai_field_review" and item.get("field_key")
        }
        methodology_contract_error = None
        try:
            methodology_rules = (
                MethodologyRulesDefinition.model_validate(methodology.rules_json)
                if methodology
                else None
            )
        except ValidationError:
            methodology_rules = None
            methodology_contract_error = (
                "This methodology has no valid required-criteria contract. Approval is blocked."
            )
        review_criteria = (
            list(latest_decision.criterion_decisions or [])
            if case.state != "ready_for_review"
            and latest_decision
            and latest_decision.criterion_decisions
            else [
                {
                    **item.model_dump(mode="json"),
                    "outcome": "",
                    "reviewer_explanation": "",
                }
                for item in (methodology_rules.required_criteria if methodology_rules else [])
            ]
        )
        review_use_cases = (
            list(latest_decision.use_case_decisions or [])
            if case.state != "ready_for_review"
            and latest_decision
            and latest_decision.use_case_decisions
            else [
                {
                    **item.model_dump(mode="json"),
                    "decision": "",
                    "reason": "",
                    "scope": item.default_scope,
                }
                for item in (methodology_rules.use_cases if methodology_rules else [])
            ]
        )
        return {
            "case": case,
            "asset": asset,
            "external": external,
            "external_snapshot": external_snapshot,
            "source_supported_explanation": (
                source_supported_explanation
            ),
            "dossier": dossier,
            "methodology": methodology,
            "ai": ai,
            "profile_sections": _profile_sections(profile),
            "review_fields": _review_fields(
                retained_profile=retained_profile,
                ai_profile=ai_profile,
                output=output,
                field_reviews=field_reviews,
                snapshot_ids=[str(item.id) for item in snapshots],
            ),
            "why_case": {
                "trigger": case.human_review_reason,
                "assessment_area": (
                    ", ".join(
                        str(item)
                        for item in output.get(
                            "potentially_affected_methodology_areas", []
                        )[:3]
                    )
                    or _label(case.case_type)
                ),
                "severity": case.risk_severity,
                "required_action": _next_case_action(case.state),
            },
            "evidence_changes": [
                {
                    "source": (
                        sources[row.official_source_id].title
                        if row.official_source_id in sources
                        else row.title or "Official source"
                    ),
                    "added": list((row.meaningful_diff or {}).get("added") or []),
                    "removed": list((row.meaningful_diff or {}).get("removed") or []),
                    "material": row.is_material_change,
                    "snapshot_id": str(row.id),
                }
                for row in snapshots
                if (row.meaningful_diff or {}).get("added")
                or (row.meaningful_diff or {}).get("removed")
            ],
            "missing_evidence": missing_evidence,
            "contradictions": contradictions,
            "methodology_areas": list(
                output.get("potentially_affected_methodology_areas") or []
            ),
            "limitations": list(output.get("explicit_limitations") or []),
            "ai_review_cue": {
                "recommended_next_action": output.get("recommended_next_action"),
                "human_review_reason": output.get("human_review_reason"),
                "confidence": output.get("confidence"),
            }
            if ai
            else None,
            "snapshots": [
                {
                    "id": row.id,
                    "title": sources[row.official_source_id].title
                    if row.official_source_id in sources
                    else row.title or "SC Malaysia source",
                    "category": sources[row.official_source_id].category
                    if row.official_source_id in sources
                    else "official_reference",
                    "url": row.source_url,
                    "retrieved_at": row.retrieved_at,
                    "source_date": row.source_published_at,
                    "status": row.fetch_status,
                    "freshness": (
                        "stale"
                        if case.source_freshness_deadline
                        and _aware(case.source_freshness_deadline) < datetime.now(UTC)
                        else "current"
                        if case.source_freshness_deadline
                        else "not recorded"
                    ),
                    "completeness": (
                        "available"
                        if row.fetch_status == "success" and bool(row.normalized_text.strip())
                        else "unavailable"
                    ),
                    "contradiction_status": (
                        f"{len(output.get('contradictions') or [])} unresolved at case level"
                        if output.get("contradictions")
                        else "No contradiction recorded for this case"
                    ),
                    "hash": row.content_hash,
                    "summary": _snapshot_summary(row),
                    "diff_added": list((row.meaningful_diff or {}).get("added") or []),
                    "diff_removed": list((row.meaningful_diff or {}).get("removed") or []),
                }
                for row in snapshots
            ],
            "decisions": decisions,
            "review_criteria": review_criteria,
            "review_use_cases": review_use_cases,
            "methodology_contract_error": methodology_contract_error,
            "reviewers": reviewers,
            "assignments": assignments,
            "active_publication": publication,
            "case_age_hours": round(
                (datetime.now(UTC) - _aware(case.created_at)).total_seconds() / 3600,
                1,
            ),
            "latest_evidence_at": (
                max((_aware(item.retrieved_at) for item in snapshots), default=None)
            ),
            "current_published_status": (
                str(
                    (publication.passport_snapshot or {}).get("status")
                    or publication.publication_state
                )
                if publication
                else "No published assessment"
            ),
            "affected_users": len({row.user_id for row in impact_rows}),
            "affected_watch_plans": len({row.strategy_id for row in impact_rows}),
            "impact_preview": {
                "current_state": case.state,
                "approval_state": (
                    "A separate immutable approval record will be created."
                    if case.state == "ready_for_review"
                    else "No approval is created by the currently available operational actions."
                ),
                "publication": (
                    "Approving publishes the Passport in the same step, unless a second "
                    "reviewer is required or written permission is still missing."
                ),
                "previous_version": (
                    f"Current public version {publication.version} remains active until "
                    "publication."
                    if publication
                    else "There is no active public version to replace."
                ),
                "evidence_snapshot_count": len(snapshots),
                "customer_visibility": (
                    f"{len({row.strategy_id for row in impact_rows})} Watchlists and "
                    f"{len({row.user_id for row in impact_rows})} users may be affected "
                    "once this is published."
                ),
                "second_approval": (
                    "A second reviewer is required by current policy."
                    if case.publication_state == "awaiting_second_approval"
                    else "No second approval is currently recorded as required."
                ),
                "notifications": (
                    "Customer status notifications follow publication and safety-hold "
                    "workflows, which record every delivery."
                ),
            },
            "overdue": bool(
                case.done_at is None
                and case.due_at is not None
                and _aware(case.due_at) < datetime.now(UTC)
            ),
            "timeline": sorted(
                [
                    {
                        "at": case.created_at,
                        "label": "Review case created",
                        "detail": case.human_review_reason,
                    },
                    *[
                        {
                            "at": item.created_at,
                            "label": _label(item.decision),
                            "detail": item.reason,
                        }
                        for item in decisions
                    ],
                    *[
                        {
                            "at": item.created_at,
                            "label": _label(item.action),
                            "detail": "Audited system event",
                        }
                        for item in audits
                    ],
                    *[
                        {
                            "at": item.created_at,
                            "label": _label(item.action),
                            "detail": item.reason,
                        }
                        for item in assignments
                    ],
                ],
                key=lambda value: value["at"],
            ),
        }

    async def section(self, name: str) -> dict:
        if name == "published-assets":
            published_rows = list(
                (
                    await self.session.execute(
                        select(PublishedAssetAssessment, CanonicalAsset)
                        .join(
                            CanonicalAsset,
                            CanonicalAsset.id == PublishedAssetAssessment.canonical_asset_id,
                        )
                        .order_by(PublishedAssetAssessment.published_at.desc())
                    )
                ).all()
            )
            return {
                "published": [
                    {"publication": publication, "asset": asset}
                    for publication, asset in published_rows
                ]
            }
        if name == "rejected-assets":
            return {"cases": await self.list_cases(state="rejected")}
        if name == "methodologies":
            return {
                "methodologies": list(
                    (
                        await self.session.scalars(
                            select(ShariaMethodology).order_by(
                                ShariaMethodology.code, ShariaMethodology.created_at.desc()
                            )
                        )
                    ).all()
                )
            }
        if name == "source-registry":
            source_rows = list(
                (
                    await self.session.execute(
                        select(OfficialSource, CanonicalAsset)
                        .join(
                            CanonicalAsset,
                            CanonicalAsset.id == OfficialSource.canonical_asset_id,
                        )
                        .order_by(CanonicalAsset.symbol, OfficialSource.priority)
                    )
                ).all()
            )
            return {
                "sources": [
                    {"source": source, "asset": asset}
                    for source, asset in source_rows
                ]
            }
        if name == "scraper-runs":
            return {
                "runs": list(
                    (
                        await self.session.scalars(
                            select(ShariaMonitoringRun)
                            .order_by(ShariaMonitoringRun.created_at.desc())
                            .limit(300)
                        )
                    ).all()
                )
            }
        if name == "ai-assessments":
            return {
                "analyses": list(
                    (
                        await self.session.scalars(
                            select(AIAnalysisSnapshot)
                            .order_by(AIAnalysisSnapshot.created_at.desc())
                            .limit(300)
                        )
                    ).all()
                )
            }
        if name == "delivery-health":
            return {
                "deliveries": list(
                    (
                        await self.session.scalars(
                            select(TelegramNotificationAttempt)
                            .order_by(TelegramNotificationAttempt.created_at.desc())
                            .limit(300)
                        )
                    ).all()
                )
            }
        if name == "audit-history":
            return {
                "audits": list(
                    (
                        await self.session.scalars(
                            select(AuditEvent)
                            .where(AuditEvent.action.like("sharia.%"))
                            .order_by(AuditEvent.created_at.desc())
                            .limit(500)
                        )
                    ).all()
                )
            }
        return {}

    async def workspace_section(self, name: str) -> dict:
        if name == "operations":
            sources = await self.section("source-registry")
            runs = await self.section("scraper-runs")
            analyses = await self.section("ai-assessments")
            deliveries = await self.section("delivery-health")
            return {**sources, **runs, **analyses, **deliveries}
        if name == "governance":
            methodologies = await self.section("methodologies")
            published = await self.section("published-assets")
            return {**methodologies, **published}
        if name == "audit-settings":
            audits = await self.section("audit-history")
            grants = list(
                (
                    await self.session.scalars(
                        select(ShariaGovernanceRoleGrant)
                        .order_by(ShariaGovernanceRoleGrant.created_at.desc())
                        .limit(300)
                    )
                ).all()
            )
            return {**audits, "governance_grants": grants}
        return {}


def _chart(counter: Counter) -> list[dict]:
    return [
        {"label": _label(str(label)), "value": int(value)}
        for label, value in sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    ]


def _label(value: str) -> str:
    return value.replace("sharia.", "").replace("_", " ").replace(".", " ").title()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _snapshot_summary(row: SourceSnapshot) -> str:
    if row.fetch_status != "success":
        return row.error_detail or "Source retrieval failed."
    excerpt = " ".join(row.normalized_text.split())[:320]
    if excerpt:
        return excerpt
    return (
        "The source was captured successfully, but no accessible text excerpt was retained. "
        f"Content fingerprint {row.content_hash[:12]}."
    )


def _evidence_state(
    *,
    row: ReviewCase,
    dossier: AssetResearchDossier | None,
    now: datetime,
) -> str:
    # Most actionable fact first. "Stale" only means the sources are worth re-checking;
    # missing or unreadable evidence is the thing that actually stops a decision, and
    # showing the reminder over it hid the real problem behind a softer word.
    if dossier is None:
        return "unavailable"
    if dossier.missing_information_count or dossier.evidence_completeness < 1:
        return "incomplete"
    if (
        row.source_freshness_deadline is not None
        and _aware(row.source_freshness_deadline) < now
    ):
        return "stale"
    return "current"


def _next_case_action(state: str) -> str:
    return {
        "draft": "Start research",
        "researching": "Check research progress",
        "research_failed": "Retry failed research",
        "needs_evidence": "Collect requested evidence",
        "ready_for_review": "Review evidence and decide",
        "approved": "Review publication impact",
        "published": "No action required",
        "rejected": "No action required",
        "stored": "No action required",
        "superseded": "Open current version",
        "safety_hold": "Review the safety hold",
    }.get(state, "Inspect case")


def _health_row(
    label: str,
    *,
    last_success: datetime | None,
    failures: int,
    has_records: bool,
) -> dict:
    status = "unavailable" if not has_records else "degraded" if failures else "healthy"
    return {
        "label": label,
        "status": status,
        "last_success": last_success,
        "active_failures": int(failures),
    }


def _overview_message(
    *,
    overall_status: str,
    review_count: int,
    safety_hold_count: int,
) -> str:
    state = {
        "healthy": "Critical systems show no active recorded failures.",
        "degraded": "Recorded exceptions need attention.",
        "critical": "A safety hold requires human review.",
    }[overall_status]
    review_word = "case" if review_count == 1 else "cases"
    review_verb = "needs" if review_count == 1 else "need"
    hold_text = (
        f" {safety_hold_count} asset is under a safety hold."
        if safety_hold_count == 1
        else f" {safety_hold_count} assets are under safety hold."
        if safety_hold_count
        else ""
    )
    return f"{state} {review_count} {review_word} {review_verb} review.{hold_text}"


_REVIEW_FIELD_LABELS = {
    "canonical_asset_identity": "Canonical asset identity",
    "identity": "Canonical asset identity",
    "project_identity": "Canonical asset identity",
    "project_purpose": "Project purpose",
    "primary_activity": "Project purpose",
    "token_utility": "Token utility",
    "token_role": "Token utility",
    "token_role_and_utility": "Token role and utility",
    "asset_type": "Native asset or token",
    "native_chain_or_contract": "Native chain and contracts",
    "data_structure": "Protocol data structure",
    "smart_contract_capability": "Smart-contract capability",
    "transaction_validation": "Transaction validation",
    "consensus_mechanism": "Consensus mechanism",
    "governance_model": "Governance",
    "tokenomics": "Tokenomics",
    "revenue_model": "Revenue or value-generation mechanism",
    "value_generation_mechanism": "Revenue or value-generation mechanism",
    "treasury_usage": "Treasury usage",
    "governance_rights": "Governance rights",
    "treasury_and_governance": "Treasury usage and governance rights",
    "lending_exposure": "Lending or interest exposure",
    "interest_exposure": "Lending or interest exposure",
    "lending_and_yield": "Lending or interest exposure",
    "lending_borrowing": "Lending and borrowing",
    "interest_or_yield": "Interest or yield exposure",
    "staking_mechanism": "Staking mechanism",
    "staking": "Staking mechanism",
    "staking_and_rewards": "Staking and rewards",
    "derivative_exposure": "Derivative or synthetic exposure",
    "synthetic_exposure": "Derivative or synthetic exposure",
    "derivatives": "Derivative or synthetic exposure",
    "derivatives_and_prediction_products": "Derivatives and prediction products",
    "treasury_and_revenue": "Treasury and revenue",
    "backing_redemption_or_collateral": "Backing, redemption, or collateral",
    "official_source_registry": "Official source registry",
    "missing_information": "Missing information",
    "contradictions": "Contradictions",
    "risk_flags": "Risk flags",
    "plain_language_profile": "Plain-language factual profile",
    "wrapped_token_dependency": "Wrapped-token dependency",
    "restricted_business_exposure": "Restricted business exposure",
    "tokenomics_and_backing": "Tokenomics and backing",
    "evidence_sufficiency": "Evidence sufficiency",
    "material_changes": "Material changes since publication",
    "required_product_exclusions": "Required product-level exclusions",
    "reviewer_notes": "Reviewer notes",
}


def _profile_sections(profile: dict) -> list[dict[str, str]]:
    return [
        {
            "label": _REVIEW_FIELD_LABELS.get(
                str(key),
                _label(str(key)),
            ),
            "value": _display_value(value),
        }
        for key, value in profile.items()
        if not str(key).startswith("_")
        and key
        not in {
            "provenance",
            "manual_verification_required",
        }
    ]


def _display_value(value: object) -> str:
    if value is None or value == "":
        return "Not established from current evidence."
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, dict):
        parts = [
            f"{_label(str(key))}: {_display_value(item)}"
            for key, item in value.items()
            if item is not None
            and item != ""
            and item != []
            and item != {}
        ]
        return "; ".join(parts) or "No verified details retained."
    if isinstance(value, list):
        if not value:
            return "None recorded."
        if all(isinstance(item, dict) for item in value):
            return f"{len(value)} official source record(s) retained."
        return "; ".join(str(item) for item in value)
    return str(value)


def _review_fields(
    *,
    retained_profile: dict,
    ai_profile: dict,
    output: dict,
    field_reviews: dict[str, dict],
    snapshot_ids: list[str],
) -> list[dict]:
    keys = list(dict.fromkeys([*retained_profile, *ai_profile]))
    if not keys:
        keys = ["canonical_asset_identity", "evidence_sufficiency"]
    field_confidence = dict(output.get("field_confidence") or {})
    field_sources = dict(output.get("field_source_references") or {})
    overall_confidence = output.get("confidence")
    missing = [str(item) for item in output.get("missing_evidence") or []]
    contradictions = [str(item) for item in output.get("contradictions") or []]
    result: list[dict] = []
    for key in keys:
        if key.startswith("_"):
            continue
        review = field_reviews.get(str(key))
        suggestion = ai_profile.get(key)
        retained = retained_profile.get(key)
        current = (
            review.get("reviewer_value")
            if review
            and review.get("disposition") in {"accepted", "edited"}
            else retained
        )
        source_refs = field_sources.get(key) or snapshot_ids
        if not isinstance(source_refs, list):
            source_refs = [str(source_refs)]
        result.append(
            {
                "key": str(key),
                "label": _REVIEW_FIELD_LABELS.get(str(key), _label(str(key))),
                "current_value": (
                    _display_value(current)
                    if current is not None
                    else None
                ),
                "source_status": "retained" if retained is not None else "not reviewed",
                "ai_suggestion": (
                    _display_value(suggestion)
                    if suggestion is not None
                    else None
                ),
                "confidence": field_confidence.get(key, overall_confidence),
                "source_refs": [str(item) for item in source_refs[:8]],
                "uncertainty": output.get("human_review_reason"),
                "missing_evidence": missing[:3],
                "contradictions": contradictions[:3],
                "review": review,
            }
        )
    return result
