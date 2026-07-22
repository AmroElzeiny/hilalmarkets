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
from ai_market_monitor.db.models.enums import ShariaMethodologyStatus, UserRole
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
                    row.case_type == "initial_asset_review" for row in open_cases
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
                    row.case_type == "material_source_change" for row in open_cases
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
    ) -> list[dict]:
        query = select(ReviewCase).order_by(
            ReviewCase.done_at.is_not(None), ReviewCase.created_at.desc()
        )
        if state:
            query = query.where(ReviewCase.state == state)
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
        if asset_query:
            needle = asset_query.strip().casefold()
            rows = [
                row
                for row in rows
                if needle in row.title.casefold()
                or (
                    row.canonical_asset_id in assets
                    and (
                        needle in assets[row.canonical_asset_id].name.casefold()
                        or needle in assets[row.canonical_asset_id].symbol.casefold()
                    )
                )
            ]
        assignee_ids = {row.assigned_reviewer_id for row in rows if row.assigned_reviewer_id}
        assignees = {
            row.id: row
            for row in (
                await self.session.scalars(select(User).where(User.id.in_(assignee_ids)))
            ).all()
        } if assignee_ids else {}
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
        return [
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
        profile = dict(output.get("profile") or (dossier.factual_profile if dossier else {}) or {})
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
            "dossier": dossier,
            "methodology": methodology,
            "ai": ai,
            "profile_sections": [
                {"label": _label(key), "value": value or "Not established from current evidence."}
                for key, value in profile.items()
            ],
            "missing_evidence": list(output.get("missing_evidence") or []),
            "contradictions": list(output.get("contradictions") or []),
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
                    "status": row.fetch_status,
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
            "affected_users": len({row.user_id for row in impact_rows}),
            "affected_watch_plans": len({row.strategy_id for row in impact_rows}),
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
    return (
        f"Captured {len(row.normalized_text):,} characters of accessible official-source text. "
        f"Content fingerprint {row.content_hash[:12]}."
    )
