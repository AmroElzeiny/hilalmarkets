import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    Alert,
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    AssetShariaStatusHistory,
    AuditEvent,
    ComplianceChange,
    ComplianceDriftNotification,
    ComplianceReview,
    DashboardNotification,
    MonitorShariaAssetState,
    ShariaUniverseSnapshot,
    Strategy,
    StrategyUniverse,
    StrategyVersion,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ComplianceChangeBehavior,
    ComplianceChangeSeverity,
    ComplianceChangeStatus,
    ComplianceReviewDecision,
    DeliveryChannel,
    MonitorShariaAssetStatus,
    ShariaAssetStatus,
    ShariaPolicyDecision,
    StrategyStatus,
)
from ai_market_monitor.schemas.sharia import (
    AssessmentCreateRequest,
    ComplianceChangeIngestRequest,
    ComplianceReviewRequest,
)
from ai_market_monitor.services.notification_preferences import (
    NotificationPreference,
    NotificationPreferenceService,
)
from ai_market_monitor.services.notifications import NotificationDispatcher
from ai_market_monitor.services.sharia_screening import (
    ShariaScreeningError,
    ShariaScreeningService,
    canonical_asset,
)


class ComplianceWatchError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ComplianceWatchService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings
        self.screening = ShariaScreeningService(session, settings)

    async def ingest_change(
        self,
        payload: ComplianceChangeIngestRequest,
        *,
        actor_user_id: UUID | None,
    ) -> tuple[ComplianceChange, bool]:
        idempotency_key = payload.idempotency_key or self._change_key(payload)
        existing = await self.session.scalar(
            select(ComplianceChange).where(
                ComplianceChange.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            return existing, False
        status = (
            ComplianceChangeStatus.AWAITING_REVIEW
            if payload.severity
            in {
                ComplianceChangeSeverity.REVIEW_REQUIRED,
                ComplianceChangeSeverity.CRITICAL,
            }
            else ComplianceChangeStatus.DETECTED
        )
        change = ComplianceChange(
            canonical_asset=canonical_asset(payload.canonical_asset),
            change_type=payload.change_type,
            severity=payload.severity,
            source_reference=payload.source_reference,
            title=payload.title,
            summary=payload.summary,
            structured_change=payload.structured_change,
            detected_at=payload.detected_at,
            effective_at=payload.effective_at,
            status=status,
            detection_method=payload.detection_method,
            confidence_label=payload.confidence_label,
            idempotency_key=idempotency_key,
        )
        self.session.add(change)
        await self.session.flush()
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="admin",
                action="sharia.compliance_change_detected",
                target_type="compliance_change",
                target_id=str(change.id),
                metadata_redacted={
                    "asset": change.canonical_asset,
                    "change_type": change.change_type,
                    "severity": change.severity.value,
                    "status": change.status.value,
                    "detection_method": change.detection_method,
                },
                created_at=datetime.now(UTC),
            )
        )
        if (
            self.settings.sharia_compliance_safety_under_review
            and change.status == ComplianceChangeStatus.AWAITING_REVIEW
        ):
            await self._apply_pending_safety_hold(change)
        return change, True

    async def review_change(
        self,
        change_id: UUID,
        payload: ComplianceReviewRequest,
        *,
        reviewer_user_id: UUID | None,
    ) -> tuple[ComplianceReview, UUID | None, int]:
        change = await self.session.get(ComplianceChange, change_id)
        if change is None:
            raise ComplianceWatchError("change_not_found", "Compliance change not found.")
        if change.status in {
            ComplianceChangeStatus.APPROVED,
            ComplianceChangeStatus.DISMISSED,
        }:
            raise ComplianceWatchError(
                "change_already_decided", "This compliance change already has a final decision."
            )
        try:
            methodology = await self.screening.methodology(
                payload.methodology_id,
                require_active=True,
            )
        except ShariaScreeningError as exc:
            raise ComplianceWatchError(exc.code, str(exc)) from exc
        current = await self.screening.effective_assessment(
            methodology.id,
            change.canonical_asset,
        )
        decision_version = int(
            await self.session.scalar(
                select(func.coalesce(func.max(ComplianceReview.decision_version), 0)).where(
                    ComplianceReview.compliance_change_id == change.id
                )
            )
            or 0
        ) + 1
        now = datetime.now(UTC)
        review = ComplianceReview(
            compliance_change_id=change.id,
            methodology_id=methodology.id,
            previous_status=current.status if current else None,
            proposed_status=payload.proposed_status,
            final_status=(
                payload.proposed_status
                if payload.decision == ComplianceReviewDecision.APPROVED
                else None
            ),
            decision=payload.decision,
            reviewer_id=reviewer_user_id,
            reviewer_identity=payload.reviewed_by,
            reviewer_notes=payload.reviewer_notes,
            reviewed_at=now,
            decision_version=decision_version,
        )
        self.session.add(review)
        await self.session.flush()
        assessment_id: UUID | None = None
        affected_count = 0
        if payload.decision == ComplianceReviewDecision.MORE_EVIDENCE_REQUIRED:
            change.status = ComplianceChangeStatus.AWAITING_REVIEW
        elif payload.decision == ComplianceReviewDecision.DISMISSED:
            change.status = ComplianceChangeStatus.DISMISSED
            if self.settings.sharia_compliance_safety_under_review:
                await self._release_pending_safety_hold(change, released_at=now)
        else:
            assert payload.proposed_status is not None
            assessment_payload = AssessmentCreateRequest(
                canonical_asset=change.canonical_asset,
                methodology_id=methodology.id,
                status=payload.proposed_status,
                summary=payload.assessment_summary or change.summary,
                qualifications=payload.qualifications,
                exclusion_reasons=payload.exclusion_reasons,
                evidence_snapshot={
                    **payload.evidence_snapshot,
                    "compliance_change_id": str(change.id),
                    "review_decision_version": decision_version,
                },
                evidence_sources=payload.evidence_sources,
                reviewed_by=payload.reviewed_by,
                reviewed_at=now,
                valid_from=change.effective_at or now,
                reason_code=change.change_type,
                reason_summary=payload.reviewer_notes,
            )
            assessment = await self.screening.create_assessment(
                assessment_payload,
                actor_user_id=reviewer_user_id,
                triggering_change_id=change.id,
            )
            assessment_id = assessment.id
            change.status = ComplianceChangeStatus.APPROVED
            await self._invalidate_snapshots(
                methodology.id,
                reason=f"approved compliance change {change.id}",
            )
            affected_count = await self._apply_status_change(
                change=change,
                methodology_id=methodology.id,
                methodology_name=methodology.name,
                methodology_version=methodology.version,
                previous_status=current.status if current else None,
                new_status=assessment.status,
                assessment_id=assessment.id,
                reviewed_at=assessment.reviewed_at,
            )
        self.session.add(
            AuditEvent(
                actor_user_id=reviewer_user_id,
                actor_type="reviewer",
                action=f"sharia.compliance_review_{payload.decision.value}",
                target_type="compliance_change",
                target_id=str(change.id),
                metadata_redacted={
                    "methodology_id": str(methodology.id),
                    "decision_version": decision_version,
                    "previous_status": current.status.value if current else None,
                    "final_status": review.final_status.value if review.final_status else None,
                    "affected_watch_plans": affected_count,
                    "review_note_recorded": True,
                },
                created_at=now,
            )
        )
        await self.session.flush()
        return review, assessment_id, affected_count

    async def overview(self, *, queue_limit: int = 100) -> dict:
        pending = list(
            (
                await self.session.scalars(
                    select(ComplianceChange)
                    .where(
                        ComplianceChange.status.in_(
                            [
                                ComplianceChangeStatus.DETECTED,
                                ComplianceChangeStatus.TRIAGED,
                                ComplianceChangeStatus.AWAITING_REVIEW,
                            ]
                        )
                    )
                    .order_by(
                        ComplianceChange.severity.desc(),
                        ComplianceChange.detected_at.desc(),
                    )
                    .limit(queue_limit)
                )
            ).all()
        )
        under_review = await self.session.scalar(
            select(func.count(func.distinct(ComplianceChange.canonical_asset))).where(
                ComplianceChange.status == ComplianceChangeStatus.AWAITING_REVIEW
            )
        )
        impacted = await self.session.scalar(
            select(func.count(func.distinct(MonitorShariaAssetState.strategy_id))).where(
                MonitorShariaAssetState.state == MonitorShariaAssetStatus.PAUSED
            )
        )
        status_changes = await self.session.scalar(
            select(func.count(AssetShariaStatusHistory.id)).where(
                AssetShariaStatusHistory.changed_at
                >= datetime.now(UTC) - timedelta(days=30)
            )
        )
        methodologies = await self.screening.methodologies(include_non_active=False)
        default_methodology = await self.screening.default_methodology()
        pending_assets = {row.canonical_asset for row in pending}
        current_assessments = (
            await self.screening.effective_assessments(
                default_methodology.id,
                assets=pending_assets,
            )
            if default_methodology is not None and pending_assets
            else {}
        )
        impacted_rows = (
            await self.session.execute(
                select(
                    MonitorShariaAssetState.canonical_asset,
                    func.count(func.distinct(MonitorShariaAssetState.strategy_id)),
                )
                .where(MonitorShariaAssetState.canonical_asset.in_(pending_assets))
                .group_by(MonitorShariaAssetState.canonical_asset)
            )
        ).all() if pending_assets else []
        impacted_by_asset = {asset: int(count or 0) for asset, count in impacted_rows}
        return {
            "awaiting_triage": sum(
                row.status == ComplianceChangeStatus.DETECTED for row in pending
            ),
            "reviews_pending": sum(
                row.status == ComplianceChangeStatus.AWAITING_REVIEW for row in pending
            ),
            "assets_under_review": int(under_review or 0),
            "status_changes_30d": int(status_changes or 0),
            "monitors_affected": int(impacted or 0),
            "methodologies": [
                {
                    "id": str(row.id),
                    "name": row.name,
                    "version": row.version,
                    "code": row.code,
                    "is_default": bool(
                        default_methodology and row.id == default_methodology.id
                    ),
                }
                for row in methodologies
            ],
            "queue": [
                {
                    "id": str(row.id),
                    "asset": row.canonical_asset,
                    "change_type": row.change_type,
                    "severity": row.severity.value,
                    "detected_at": row.detected_at,
                    "source": row.source_reference,
                    "status": row.status.value,
                    "title": row.title,
                    "summary": row.summary,
                    "structured_change": row.structured_change,
                    "effective_at": row.effective_at,
                    "confidence_label": row.confidence_label,
                    "current_status": (
                        current_assessments[row.canonical_asset].status.value
                        if row.canonical_asset in current_assessments
                        else "insufficient_information"
                    ),
                    "affected_watch_plans": impacted_by_asset.get(
                        row.canonical_asset, 0
                    ),
                    "suggested_action": (
                        "Review now"
                        if row.severity == ComplianceChangeSeverity.CRITICAL
                        else "Review evidence"
                        if row.status == ComplianceChangeStatus.AWAITING_REVIEW
                        else "Triage"
                    ),
                    "passport_path": (
                        f"/dashboard/market/{row.canonical_asset.lower()}"
                    ),
                }
                for row in pending
            ],
        }

    async def _apply_status_change(
        self,
        *,
        change: ComplianceChange,
        methodology_id: UUID,
        methodology_name: str,
        methodology_version: str,
        previous_status: ShariaAssetStatus | None,
        new_status: ShariaAssetStatus,
        assessment_id: UUID,
        reviewed_at: datetime,
        event_source: str = "approved_methodology_review",
        provisional_safety_hold: bool = False,
    ) -> int:
        state_rows = (
            await self.session.execute(
                select(
                    MonitorShariaAssetState,
                    StrategyUniverse,
                    Strategy,
                    StrategyVersion,
                )
                .join(Strategy, Strategy.id == MonitorShariaAssetState.strategy_id)
                .join(
                    StrategyVersion,
                    StrategyVersion.id == MonitorShariaAssetState.strategy_version_id,
                )
                .join(
                    StrategyUniverse,
                    StrategyUniverse.strategy_version_id == StrategyVersion.id,
                )
                .where(
                    MonitorShariaAssetState.canonical_asset == change.canonical_asset,
                    MonitorShariaAssetState.methodology_id == methodology_id,
                )
            )
        ).all()
        notified_scopes: set[tuple[UUID, UUID | None]] = set()
        for state, universe, strategy, version in state_rows:
            allowed = new_status.value in set(universe.allowed_sharia_statuses or [])
            if (
                new_status == ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
                and universe.qualification_policy == "exclude"
            ):
                allowed = False
            behavior = universe.compliance_change_behavior or ComplianceChangeBehavior.PAUSE_ASSET
            state.last_assessment_id = assessment_id
            state.sharia_status = new_status
            state.changed_at = reviewed_at
            if allowed:
                state.state = MonitorShariaAssetStatus.ACTIVE
                state.policy_decision = ShariaPolicyDecision.INCLUDED
                state.policy_reason = "The asset meets the current screening policy."
            else:
                state.state = (
                    MonitorShariaAssetStatus.REMOVED
                    if behavior == ComplianceChangeBehavior.REMOVE_ASSET
                    else MonitorShariaAssetStatus.PAUSED
                )
                state.policy_decision = ShariaPolicyDecision.PAUSED_FOR_COMPLIANCE
                state.policy_reason = (
                    f"Screening status changed to {new_status.value}; the asset no longer "
                    "meets this Watchlist's selected policy."
                )
                if behavior == ComplianceChangeBehavior.PAUSE_MONITOR_IF_ANY_ASSET_CHANGES:
                    strategy.status = StrategyStatus.PAUSED
                    strategy.paused_at = reviewed_at
            await self._create_drift_notification(
                user_id=strategy.user_id,
                strategy=strategy,
                version=version,
                change=change,
                methodology_id=methodology_id,
                methodology_name=methodology_name,
                methodology_version=methodology_version,
                previous_status=previous_status,
                new_status=new_status,
                behavior=behavior,
                assessment_id=assessment_id,
                reviewed_at=reviewed_at,
                monitor_impact=state.state.value,
                event_source=event_source,
                provisional_safety_hold=provisional_safety_hold,
            )
            notified_scopes.add((strategy.user_id, strategy.id))

        watchlist_users = list(
            (
                await self.session.scalars(
                    select(ApprovedWatchlist.user_id)
                    .join(
                        ApprovedWatchlistAsset,
                        ApprovedWatchlistAsset.watchlist_id == ApprovedWatchlist.id,
                    )
                    .where(
                        ApprovedWatchlistAsset.canonical_asset == change.canonical_asset
                    )
                    .distinct()
                )
            ).all()
        )
        for user_id in watchlist_users:
            if any(scope[0] == user_id for scope in notified_scopes):
                continue
            await self._create_drift_notification(
                user_id=user_id,
                strategy=None,
                version=None,
                change=change,
                methodology_id=methodology_id,
                methodology_name=methodology_name,
                methodology_version=methodology_version,
                previous_status=previous_status,
                new_status=new_status,
                behavior=ComplianceChangeBehavior.NOTIFY_ONLY,
                assessment_id=assessment_id,
                reviewed_at=reviewed_at,
                monitor_impact="watchlist_only",
                event_source=event_source,
                provisional_safety_hold=provisional_safety_hold,
            )
        return len({scope[1] for scope in notified_scopes if scope[1] is not None})

    async def _apply_pending_safety_hold(self, change: ComplianceChange) -> None:
        for methodology in await self.screening.executable_methodologies():
            current = await self.screening.effective_assessment(
                methodology.id,
                change.canonical_asset,
            )
            if current is None:
                continue
            await self._invalidate_snapshots(
                methodology.id,
                reason=f"pending compliance safety hold {change.id}",
            )
            await self._apply_status_change(
                change=change,
                methodology_id=methodology.id,
                methodology_name=methodology.name,
                methodology_version=methodology.version,
                previous_status=current.status,
                new_status=ShariaAssetStatus.UNDER_REVIEW,
                assessment_id=current.id,
                reviewed_at=change.detected_at,
                event_source="configured_pending_review_safety_policy",
                provisional_safety_hold=True,
            )

    async def _release_pending_safety_hold(
        self,
        change: ComplianceChange,
        *,
        released_at: datetime,
    ) -> None:
        for methodology in await self.screening.executable_methodologies():
            current = await self.screening.effective_assessment(
                methodology.id,
                change.canonical_asset,
            )
            if current is None:
                continue
            await self._invalidate_snapshots(
                methodology.id,
                reason=f"dismissed compliance safety hold {change.id}",
            )
            await self._apply_status_change(
                change=change,
                methodology_id=methodology.id,
                methodology_name=methodology.name,
                methodology_version=methodology.version,
                previous_status=ShariaAssetStatus.UNDER_REVIEW,
                new_status=current.status,
                assessment_id=current.id,
                reviewed_at=released_at,
                event_source="configured_pending_review_safety_policy_released",
                provisional_safety_hold=False,
            )

    async def _create_drift_notification(
        self,
        *,
        user_id: UUID,
        strategy: Strategy | None,
        version: StrategyVersion | None,
        change: ComplianceChange,
        methodology_id: UUID,
        methodology_name: str,
        methodology_version: str,
        previous_status: ShariaAssetStatus | None,
        new_status: ShariaAssetStatus,
        behavior: ComplianceChangeBehavior,
        assessment_id: UUID,
        reviewed_at: datetime,
        monitor_impact: str,
        event_source: str,
        provisional_safety_hold: bool,
    ) -> None:
        key_payload = {
            "user_id": str(user_id),
            "strategy_id": str(strategy.id) if strategy else None,
            "change_id": str(change.id),
            "methodology_id": str(methodology_id),
            "new_status": new_status.value,
        }
        key = hashlib.sha256(
            json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if await self.session.scalar(
            select(ComplianceDriftNotification.id).where(
                ComplianceDriftNotification.idempotency_key == key
            )
        ):
            return
        status_label = new_status.value.replace("_", " ").title()
        title = f"{change.canonical_asset} moved to {status_label}"
        automatic_action = monitor_impact.replace("_", " ")
        affected_watch_plans = [strategy.name] if strategy else []
        next_user_action = (
            "Review the updated Passport and the affected Watchlist before resuming."
            if new_status
            in {
                ShariaAssetStatus.UNDER_REVIEW,
                ShariaAssetStatus.DISPUTED,
                ShariaAssetStatus.EXCLUDED,
            }
            else "Review the updated Passport before relying on the restored status."
        )
        body = (
            f"Status: {(previous_status.value if previous_status else 'not recorded')} -> "
            f"{new_status.value}\n"
            f"Methodology: {methodology_name} v{methodology_version}\n"
            f"Reason: {change.summary}\n"
            f"Review state: {change.status.value}\n"
            f"Automatic Watchlist action: {automatic_action}\n"
            f"Affected Watchlists: {', '.join(affected_watch_plans) or 'none'}\n"
            f"Next: {next_user_action}"
        )
        proof = {
            "event_type": "sharia.status_changed",
            "canonical_asset": change.canonical_asset,
            "strategy_id": str(strategy.id) if strategy else None,
            "strategy_name": strategy.name if strategy else None,
            "strategy_version_id": str(version.id) if version else None,
            "methodology_id": str(methodology_id),
            "methodology_name": methodology_name,
            "methodology_version": methodology_version,
            "previous_status": previous_status.value if previous_status else None,
            "new_status": new_status.value,
            "assessment_id": str(assessment_id),
            "compliance_change_id": str(change.id),
            "change_type": change.change_type,
            "reviewed_at": reviewed_at.isoformat(),
            "behavior": behavior.value,
            "monitor_impact": monitor_impact,
            "automatic_watch_plan_action": automatic_action,
            "affected_watch_plans": affected_watch_plans,
            "review_state": change.status.value,
            "reason": change.summary,
            "next_user_action": next_user_action,
            "evidence_passport_path": f"/dashboard/market/{change.canonical_asset.lower()}",
            "authoritative_source": event_source,
            "provisional_safety_hold": provisional_safety_hold,
            "ai_generated_ruling": False,
        }
        now = datetime.now(UTC)
        preference = await NotificationPreferenceService(self.session).current(user_id)
        external_enabled = self._external_alert_enabled(
            preference,
            previous_status=previous_status,
            new_status=new_status,
        )
        urgent = (
            change.severity == ComplianceChangeSeverity.CRITICAL
            or new_status
            in {
                ShariaAssetStatus.UNDER_REVIEW,
                ShariaAssetStatus.DISPUTED,
                ShariaAssetStatus.EXCLUDED,
            }
        )
        defer_external = (
            external_enabled
            and preference.compliance_alert_digest == "daily"
            and not urgent
            and bool(
                (preference.compliance_alert_channels or set())
                - {DeliveryChannel.WEB}
            )
        )
        alert = Alert(
            user_id=user_id,
            strategy_version_id=version.id if version else None,
            setup_instance_id=None,
            alert_type=AlertType.COMPLIANCE,
            deduplication_key=f"sharia-drift:{key}",
            title=title,
            body=body,
            proof_receipt=proof,
            candle_timestamp=None,
        )
        self.session.add(alert)
        await self.session.flush()
        self.session.add(
            DashboardNotification(
                user_id=user_id,
                level=(
                    "danger"
                    if new_status == ShariaAssetStatus.EXCLUDED
                    else "warning"
                    if new_status
                    in {ShariaAssetStatus.UNDER_REVIEW, ShariaAssetStatus.DISPUTED}
                    else "info"
                ),
                title=title,
                body=body,
                action_label="View evidence",
                action_url=f"/dashboard/market/{change.canonical_asset.lower()}",
                created_at=now,
            )
        )
        channels = [DeliveryChannel.WEB]
        if external_enabled and not defer_external:
            channels.extend(
                [
                    DeliveryChannel.TELEGRAM,
                    DeliveryChannel.WHATSAPP,
                ]
            )
        await NotificationDispatcher(self.session).enqueue_user_alert(
            alert,
            channels=channels,
        )
        self.session.add(
            ComplianceDriftNotification(
                user_id=user_id,
                compliance_change_id=change.id,
                strategy_id=strategy.id if strategy else None,
                alert_id=alert.id,
                canonical_asset=change.canonical_asset,
                previous_status=previous_status,
                new_status=new_status,
                behavior=behavior,
                impact=proof,
                idempotency_key=key,
                created_at=now,
                digest_processed_at=None if defer_external else now,
            )
        )

    @staticmethod
    def _external_alert_enabled(
        preference: NotificationPreference,
        *,
        previous_status: ShariaAssetStatus | None,
        new_status: ShariaAssetStatus,
    ) -> bool:
        if not preference.compliance_alerts_enabled:
            return False
        if new_status == ShariaAssetStatus.EXCLUDED:
            return preference.exclusion_alerts
        if new_status in {ShariaAssetStatus.UNDER_REVIEW, ShariaAssetStatus.DISPUTED}:
            return preference.under_review_alerts
        qualification_changed = (
            previous_status != new_status
            and ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
            in {previous_status, new_status}
        )
        if qualification_changed:
            return preference.qualification_change_alerts
        return True

    async def _invalidate_snapshots(self, methodology_id: UUID, *, reason: str) -> int:
        rows = list(
            (
                await self.session.scalars(
                    select(ShariaUniverseSnapshot).where(
                        ShariaUniverseSnapshot.methodology_id == methodology_id,
                        ShariaUniverseSnapshot.invalidated_at.is_(None),
                    )
                )
            ).all()
        )
        now = datetime.now(UTC)
        for row in rows:
            row.invalidated_at = now
            row.invalidation_reason = reason[:160]
        return len(rows)

    @staticmethod
    def _change_key(payload: ComplianceChangeIngestRequest) -> str:
        data = {
            "asset": canonical_asset(payload.canonical_asset),
            "change_type": payload.change_type,
            "source_reference": payload.source_reference,
            "title": payload.title.strip(),
            "detected_at": payload.detected_at.isoformat(),
            "structured_change": payload.structured_change,
        }
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class ComplianceDigestService:
    """Release bounded daily external summaries while keeping in-app evidence immediate."""

    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def process_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 1000,
    ) -> dict[str, int]:
        now = now or datetime.now(UTC)
        rows = list(
            (
                await self.session.execute(
                    select(ComplianceDriftNotification, ComplianceChange)
                    .join(
                        ComplianceChange,
                        ComplianceChange.id
                        == ComplianceDriftNotification.compliance_change_id,
                    )
                    .where(ComplianceDriftNotification.digest_processed_at.is_(None))
                    .order_by(ComplianceDriftNotification.created_at.asc())
                    .limit(limit)
                )
            ).all()
        )
        grouped: dict[UUID, list[tuple[ComplianceDriftNotification, ComplianceChange]]] = (
            defaultdict(list)
        )
        for drift, change in rows:
            grouped[drift.user_id].append((drift, change))

        summaries = 0
        events = 0
        for user_id, user_rows in grouped.items():
            preference = await NotificationPreferenceService(self.session).current(user_id)
            if preference.compliance_alert_digest != "daily":
                for drift, _ in user_rows:
                    drift.digest_processed_at = now
                continue
            try:
                zone = ZoneInfo(preference.timezone)
            except ZoneInfoNotFoundError:
                zone = ZoneInfo("UTC")
            if now.astimezone(zone).hour != self.settings.sharia_compliance_digest_local_hour:
                continue

            external_channels = sorted(
                (preference.compliance_alert_channels or set())
                - {DeliveryChannel.WEB},
                key=lambda channel: channel.value,
            )
            if not external_channels:
                for drift, _ in user_rows:
                    drift.digest_processed_at = now
                events += len(user_rows)
                continue

            event_ids = [str(drift.id) for drift, _ in user_rows]
            digest_hash = hashlib.sha256("|".join(event_ids).encode()).hexdigest()
            deduplication_key = f"sharia-compliance-digest:{digest_hash}"
            alert = await self.session.scalar(
                select(Alert).where(Alert.deduplication_key == deduplication_key)
            )
            if alert is None:
                lines = [
                    f"- {drift.canonical_asset}: "
                    f"{drift.new_status.value.replace('_', ' ')}"
                    for drift, _ in user_rows[:10]
                ]
                remaining = len(user_rows) - len(lines)
                if remaining > 0:
                    lines.append(f"- {remaining} more update(s) are in Activity")
                alert = Alert(
                    user_id=user_id,
                    strategy_version_id=None,
                    setup_instance_id=None,
                    alert_type=AlertType.COMPLIANCE,
                    deduplication_key=deduplication_key,
                    title=f"{len(user_rows)} screening update(s) to review",
                    body=(
                        "Your daily Sharia screening summary:\n\n"
                        + "\n".join(lines)
                        + "\n\nOpen Activity to review the stored evidence and Watchlist impact."
                    ),
                    proof_receipt={
                        "event_type": "sharia.compliance_daily_digest",
                        "event_count": len(user_rows),
                        "drift_notification_ids": event_ids,
                        "evidence_path": "/dashboard/opportunities?tab=compliance_changes",
                        "generated_at": now.isoformat(),
                        "ai_generated_ruling": False,
                    },
                    candle_timestamp=None,
                )
                self.session.add(alert)
                await self.session.flush()
            deliveries = await NotificationDispatcher(self.session).enqueue_user_alert(
                alert,
                channels=external_channels,
            )
            if not deliveries:
                continue
            for drift, _ in user_rows:
                drift.digest_processed_at = now
            summaries += 1
            events += len(user_rows)
        return {
            "users_considered": len(grouped),
            "summaries_enqueued": summaries,
            "events_processed": events,
        }
