from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    AuditEvent,
    BillingEvent,
    Incident,
    MarketDataHealth,
    ScanJob,
    Strategy,
    StrategyVersion,
    Subscription,
    SupportRequest,
    TelegramConnection,
    Trial,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    IncidentSeverity,
    IncidentStatus,
    ScanJobStatus,
    StrategyStatus,
    SupportRequestStatus,
    UserRole,
)
from ai_market_monitor.observability.alerts import evaluate_alert_rules
from ai_market_monitor.observability.issues import OperationalIssueService
from ai_market_monitor.observability.metrics import get_metrics_recorder
from ai_market_monitor.observability.slos import SLO_DEFINITION_VERSION, evaluate_all_slos
from ai_market_monitor.services.admin import AdminCommercialService
from ai_market_monitor.services.billing import BillingService, BillingWebhookResult
from ai_market_monitor.services.interfaces import RecentMarketPreviewer
from ai_market_monitor.services.reliability import HealthSummary, ReliabilityService
from ai_market_monitor.services.strategy import StrategyGateError, StrategyService
from ai_market_monitor.services.support import SupportEscalationService
from ai_market_monitor.services.trials import TrialLifecycleService


class AdminDashboardError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AdminDashboardService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def overview(self) -> dict:
        return {
            "users": await self.session.scalar(select(func.count(User.id))),
            "active_strategies": await self.session.scalar(
                select(func.count(Strategy.id)).where(Strategy.status == StrategyStatus.ACTIVE)
            ),
            "failed_scan_jobs": await self.session.scalar(
                select(func.count(ScanJob.id)).where(ScanJob.status == ScanJobStatus.FAILED)
            ),
            "open_support_requests": await self.session.scalar(
                select(func.count(SupportRequest.id)).where(
                    SupportRequest.status.in_(
                        [
                            SupportRequestStatus.OPEN,
                            SupportRequestStatus.IN_PROGRESS,
                            SupportRequestStatus.PENDING_USER,
                        ]
                    )
                )
            ),
            "failed_billing_events": await self.session.scalar(
                select(func.count(BillingEvent.id)).where(
                    BillingEvent.processing_status == "failed"
                )
            ),
            "open_incidents": await self.session.scalar(
                select(func.count(Incident.id)).where(
                    Incident.status.in_(
                        [
                            IncidentStatus.OPEN,
                            IncidentStatus.INVESTIGATING,
                            IncidentStatus.MITIGATING,
                            IncidentStatus.MONITORING,
                        ]
                    )
                )
            ),
        }

    async def user_search(self, query: str | None = None, *, limit: int = 25) -> list[dict]:
        statement = (
            select(User)
            .outerjoin(UserIdentity, UserIdentity.user_id == User.id)
            .order_by(User.created_at.desc())
            .limit(limit)
        )
        if query:
            like = f"%{query.casefold()}%"
            statement = statement.where(
                or_(
                    func.lower(User.display_name).like(like),
                    func.lower(UserIdentity.display_identifier).like(like),
                    func.lower(UserIdentity.normalized_identifier).like(like),
                    UserIdentity.provider_subject.like(f"%{query}%"),
                )
            )
        users = (await self.session.scalars(statement)).unique().all()
        return [
            {
                "id": str(user.id),
                "display_name": user.display_name,
                "role": user.role.value,
                "status": user.status.value,
                "created_at": user.created_at,
            }
            for user in users
        ]

    async def user_detail(self, user_id: UUID) -> dict:
        user = await self.session.get(User, user_id)
        if user is None:
            raise AdminDashboardError("user_missing", "User not found.")
        subscriptions = (
            await self.session.scalars(select(Subscription).where(Subscription.user_id == user_id))
        ).all()
        trials = (await self.session.scalars(select(Trial).where(Trial.user_id == user_id))).all()
        strategies = (
            await self.session.scalars(select(Strategy).where(Strategy.user_id == user_id))
        ).all()
        telegram = await self.session.scalar(
            select(TelegramConnection).where(TelegramConnection.user_id == user_id)
        )
        return {
            "id": str(user.id),
            "display_name": user.display_name,
            "role": user.role.value,
            "status": user.status.value,
            "subscriptions": [
                {
                    "id": str(row.id),
                    "status": row.status.value,
                    "provider": row.provider,
                    "current_period_end": row.current_period_end,
                }
                for row in subscriptions
            ],
            "trials": [
                {"id": str(row.id), "status": row.status.value, "ends_at": row.ends_at}
                for row in trials
            ],
            "strategies": [
                {"id": str(row.id), "name": row.name, "status": row.status.value}
                for row in strategies
            ],
            "telegram": bool(telegram),
        }

    async def health_dashboard(self) -> dict:
        summary: HealthSummary = await ReliabilityService(self.session).status_summary()
        recent_market = (
            await self.session.scalars(
                select(MarketDataHealth).order_by(MarketDataHealth.checked_at.desc()).limit(25)
            )
        ).all()
        # Objectives, firing alerts and the issue queue are added to the dashboard the
        # operator already opens, rather than to a second console. The existing keys
        # are untouched: anything reading this endpoint today keeps working.
        recorder = get_metrics_recorder()
        evaluations = evaluate_all_slos(recorder)
        fired = evaluate_alert_rules(recorder)
        issues = await OperationalIssueService(self.session).summary()
        return {
            "overall_status": summary.overall_status.value,
            "market_data": summary.market_data,
            "integrations": summary.integrations,
            "open_incidents": summary.open_incidents,
            "failed_jobs": summary.failed_jobs,
            "generated_at": summary.generated_at,
            "slo_definition_version": SLO_DEFINITION_VERSION,
            "service_level_objectives": [
                {
                    "name": item.slo.name,
                    "service": item.slo.service,
                    "owner": item.slo.owner,
                    "objective": item.slo.objective,
                    "comparison": item.slo.comparison,
                    "unit": item.slo.unit,
                    "measured": item.measured,
                    # "no_data" is its own state, never folded into "met". An
                    # objective with no traffic behind it has not been tested.
                    "state": item.state,
                    "launch_blocking": item.slo.launch_blocking,
                    "severity_on_breach": item.slo.severity_on_breach,
                    "runbook_anchor": item.slo.runbook_anchor,
                }
                for item in evaluations
            ],
            "firing_alerts": [
                {
                    "name": item.rule.name,
                    "severity": item.rule.severity,
                    "what_broke": item.rule.what_broke,
                    "blast_radius": item.rule.blast_radius,
                    "first_mitigation": item.rule.first_mitigation,
                    "runbook_anchor": item.rule.runbook_anchor,
                    "delivery_route": item.rule.delivery_route,
                    "measured": item.measured,
                }
                for item in fired
            ],
            "operational_issues": {
                "open": issues.open,
                "acknowledged": issues.acknowledged,
                "mitigated": issues.mitigated,
                "suppressed": issues.suppressed,
                "resolved": issues.resolved,
                "needs_attention": issues.needs_attention,
            },
            "recent_market_data": [
                {
                    "provider": row.provider,
                    "exchange": row.exchange,
                    "symbol": row.symbol,
                    "timeframe": row.timeframe,
                    "status": row.status.value,
                    "data_age_seconds": row.data_age_seconds,
                    "missing_candle_count": row.missing_candle_count,
                    "checked_at": row.checked_at,
                }
                for row in recent_market
            ],
        }

    async def recent_activity(self, *, limit: int = 50) -> dict:
        return {
            "scan_jobs": [
                {
                    "id": str(row.id),
                    "status": row.status.value,
                    "scheduled_for": row.scheduled_for,
                    "error_code": row.error_code,
                }
                for row in (
                    await self.session.scalars(
                        select(ScanJob).order_by(ScanJob.created_at.desc()).limit(limit)
                    )
                ).all()
            ],
            "alerts": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "alert_type": row.alert_type.value,
                    "created_at": row.created_at,
                }
                for row in (
                    await self.session.scalars(
                        select(Alert).order_by(Alert.created_at.desc()).limit(limit)
                    )
                ).all()
            ],
            "deliveries": [
                {
                    "id": str(row.id),
                    "channel": row.channel.value,
                    "status": row.status.value,
                    "attempt_count": row.attempt_count,
                    "last_error_code": row.last_error_code,
                }
                for row in (
                    await self.session.scalars(
                        select(AlertDelivery).order_by(AlertDelivery.updated_at.desc()).limit(limit)
                    )
                ).all()
            ],
            "billing_events": [
                {
                    "id": str(row.id),
                    "provider_event_id": row.provider_event_id,
                    "event_type": row.event_type,
                    "processing_status": row.processing_status,
                    "error_code": row.error_code,
                }
                for row in (
                    await self.session.scalars(
                        select(BillingEvent).order_by(BillingEvent.created_at.desc()).limit(limit)
                    )
                ).all()
            ],
        }

    async def pause_strategy(
        self, *, strategy_id: UUID, admin_user_id: UUID, reason: str
    ) -> Strategy:
        strategy = await self.session.get(Strategy, strategy_id)
        if strategy is None:
            raise AdminDashboardError("strategy_missing", "Strategy not found.")
        strategy.status = StrategyStatus.PAUSED
        strategy.paused_at = datetime.now(UTC)
        self._audit(
            admin_user_id,
            "admin.strategy_paused",
            "strategy",
            strategy.id,
            {"reason": reason},
        )
        await self.session.flush()
        return strategy

    async def resume_strategy(
        self,
        *,
        strategy_id: UUID,
        admin_user_id: UUID,
        reason: str,
        previewer: RecentMarketPreviewer,
    ) -> Strategy:
        strategy = await self.session.get(Strategy, strategy_id)
        if strategy is None:
            raise AdminDashboardError("strategy_missing", "Strategy not found.")
        if strategy.active_version_id is None:
            raise AdminDashboardError(
                "active_version_missing",
                "The monitor has no approved version to resume.",
            )
        version = await self.session.get(StrategyVersion, strategy.active_version_id)
        if version is None:
            raise AdminDashboardError(
                "active_version_missing",
                "The approved monitor version is unavailable.",
            )
        service = StrategyService(
            self.session,
            self.settings.disclaimer_version,
            self.settings,
        )
        try:
            await service.run_preview(
                version,
                user_id=strategy.user_id,
                previewer=previewer,
            )
            resumed = await service.activate(
                version,
                user_id=strategy.user_id,
                strategy_name=strategy.name,
                resume=True,
                actor_user_id=admin_user_id,
                actor_type="admin",
                reason=reason,
            )
        except StrategyGateError as exc:
            raise AdminDashboardError(exc.code, str(exc)) from exc
        await self.session.flush()
        return resumed

    async def extend_trial(self, *, user_id: UUID, admin_user_id: UUID, days: int, reason: str):
        return await AdminCommercialService(self.session).grant_trial_extension(
            admin_user_id=admin_user_id,
            target_user_id=user_id,
            days=days,
            reason=reason,
            trial_service=TrialLifecycleService(self.session, self.settings),
        )

    async def open_incident(
        self,
        *,
        admin_user_id: UUID,
        title: str,
        description: str,
        incident_type: str,
        severity: IncidentSeverity,
        affected_users: list[UUID] | None = None,
        affected_strategy_ids: list[UUID] | None = None,
    ) -> Incident:
        return await ReliabilityService(self.session).open_incident(
            actor_user_id=admin_user_id,
            title=title,
            description=description,
            incident_type=incident_type,
            severity=severity,
            affected_users=affected_users,
            affected_strategy_ids=affected_strategy_ids,
        )

    async def resolve_incident(
        self, *, incident_id: UUID, admin_user_id: UUID, resolution: str
    ) -> Incident:
        return await ReliabilityService(self.session).resolve_incident(
            incident_id=incident_id,
            actor_user_id=admin_user_id,
            resolution=resolution,
        )

    async def resolve_support(
        self, *, support_request_id: UUID, admin_user_id: UUID, resolution: str
    ) -> SupportRequest:
        return await SupportEscalationService(self.session).resolve(
            support_request_id=support_request_id,
            actor_user_id=admin_user_id,
            resolution=resolution,
        )

    async def reprocess_webhook(self, *, provider_event_id: str) -> BillingWebhookResult:
        return await BillingService(self.session, self.settings).reprocess_failed_event(
            provider_event_id
        )

    async def make_admin(self, *, target_user_id: UUID, admin_user_id: UUID) -> User:
        target = await self.session.get(User, target_user_id)
        if target is None:
            raise AdminDashboardError("user_missing", "User not found.")
        target.role = UserRole.ADMIN
        self._audit(admin_user_id, "admin.role_granted", "user", target.id, {"role": "admin"})
        await self.session.flush()
        return target

    def _audit(
        self,
        actor_user_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID,
        metadata: dict,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="admin",
                action=action,
                target_type=target_type,
                target_id=str(target_id),
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )
