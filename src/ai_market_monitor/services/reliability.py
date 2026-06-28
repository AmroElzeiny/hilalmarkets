from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AlertDelivery,
    AuditEvent,
    Incident,
    IncidentImpact,
    IncidentUpdate,
    IntegrationHealth,
    MarketDataHealth,
    OperationalMetric,
    ScanJob,
    Strategy,
)
from ai_market_monitor.db.models.enums import (
    DeliveryStatus,
    HealthStatus,
    IncidentSeverity,
    IncidentStatus,
    ScanJobStatus,
    StrategyStatus,
)


class ReliabilityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class HealthSummary:
    overall_status: HealthStatus
    market_data: dict[str, int]
    integrations: dict[str, int]
    open_incidents: int
    failed_jobs: int
    generated_at: datetime


class ReliabilityService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_market_data_health(
        self,
        *,
        provider: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        market_type: str = "spot",
        latest_candle_at: datetime | None,
        retrieved_at: datetime,
        candle_count: int,
        expected_candle_count: int | None = None,
        duplicate_candle_count: int = 0,
        out_of_order_candle_count: int = 0,
        source_status: str = "ok",
        details: dict | None = None,
    ) -> MarketDataHealth:
        now = datetime.now(UTC)
        latest = self._aware(latest_candle_at) if latest_candle_at else None
        retrieved = self._aware(retrieved_at)
        data_age_seconds = int((retrieved - latest).total_seconds()) if latest else None
        missing = max(0, (expected_candle_count or candle_count) - candle_count)
        status = self._market_status(
            source_status=source_status,
            data_age_seconds=data_age_seconds,
            missing=missing,
            duplicates=duplicate_candle_count,
            out_of_order=out_of_order_candle_count,
        )
        health = await self.session.scalar(
            select(MarketDataHealth).where(
                MarketDataHealth.provider == provider,
                MarketDataHealth.exchange == exchange,
                MarketDataHealth.symbol == symbol,
                MarketDataHealth.timeframe == timeframe,
            )
        )
        if health is None:
            health = MarketDataHealth(
                provider=provider,
                exchange=exchange,
                symbol=symbol,
                market_type=market_type,
                timeframe=timeframe,
            )
            self.session.add(health)
        health.status = status
        health.last_candle_at = latest
        health.retrieved_at = retrieved
        health.data_age_seconds = data_age_seconds
        health.candle_count = candle_count
        health.lag_seconds = data_age_seconds
        health.missing_candle_count = missing
        health.duplicate_candle_count = duplicate_candle_count
        health.out_of_order_candle_count = out_of_order_candle_count
        health.source_status = source_status
        health.details = details or {}
        health.checked_at = now
        await self.session.flush()
        return health

    async def assert_confirmation_allowed(
        self,
        *,
        provider: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        is_candle_complete: bool,
        max_data_age_seconds: int = 300,
        allow_stale_data: bool = False,
        allow_incomplete_candle: bool = False,
    ) -> None:
        health = await self.session.scalar(
            select(MarketDataHealth).where(
                MarketDataHealth.provider == provider,
                MarketDataHealth.exchange == exchange,
                MarketDataHealth.symbol == symbol,
                MarketDataHealth.timeframe == timeframe,
            )
        )
        if health is None:
            raise ReliabilityError(
                "market_health_missing",
                "Market data health has not been recorded for this scan.",
            )
        if not allow_incomplete_candle and not is_candle_complete:
            raise ReliabilityError(
                "incomplete_candle",
                "Confirmed alerts are blocked while the strategy candle is incomplete.",
            )
        too_old = (
            health.data_age_seconds is not None and health.data_age_seconds > max_data_age_seconds
        )
        if not allow_stale_data and (too_old or health.status in {HealthStatus.DOWN}):
            raise ReliabilityError(
                "stale_market_data",
                "Confirmed alerts are blocked because market data is stale or unavailable.",
            )
        if not allow_stale_data and health.missing_candle_count > 0:
            raise ReliabilityError(
                "missing_candles",
                "Confirmed alerts are blocked because required candle intervals are missing.",
            )

    async def record_integration_health(
        self,
        *,
        integration: str,
        scope_key: str,
        status: HealthStatus,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> IntegrationHealth:
        health = await self.session.scalar(
            select(IntegrationHealth).where(
                IntegrationHealth.integration == integration,
                IntegrationHealth.scope_key == scope_key,
            )
        )
        now = datetime.now(UTC)
        if health is None:
            health = IntegrationHealth(integration=integration, scope_key=scope_key)
            self.session.add(health)
        health.status = status
        health.latency_ms = latency_ms
        health.last_error_code = error_code
        health.checked_at = now
        if status == HealthStatus.HEALTHY:
            health.consecutive_failures = 0
            health.last_success_at = now
        else:
            health.consecutive_failures += 1
            health.last_failure_at = now
        await self.session.flush()
        return health

    async def record_metric(
        self,
        *,
        component: str,
        metric_name: str,
        status: HealthStatus,
        value: float | int | Decimal | None = None,
        unit: str | None = None,
        dimensions: dict | None = None,
        measured_at: datetime | None = None,
    ) -> OperationalMetric:
        metric = OperationalMetric(
            component=component,
            metric_name=metric_name,
            status=status,
            value=Decimal(str(value)) if value is not None else None,
            unit=unit,
            dimensions=dimensions or {},
            measured_at=measured_at or datetime.now(UTC),
        )
        self.session.add(metric)
        await self.session.flush()
        return metric

    async def mark_delivery_failure(
        self,
        delivery: AlertDelivery,
        *,
        retryable: bool,
        error_code: str,
        detail: str | None = None,
    ) -> AlertDelivery:
        delivery.status = (
            DeliveryStatus.FAILED_RETRYABLE if retryable else DeliveryStatus.FAILED_PERMANENT
        )
        delivery.last_error_code = error_code
        delivery.last_error_detail = detail
        delivery.last_attempt_at = datetime.now(UTC)
        if not retryable:
            delivery.next_retry_at = None
        await self.session.flush()
        return delivery

    async def open_incident(
        self,
        *,
        actor_user_id: UUID | None,
        title: str,
        description: str,
        incident_type: str,
        severity: IncidentSeverity,
        affected_users: list[UUID] | None = None,
        affected_strategy_ids: list[UUID] | None = None,
        metadata: dict | None = None,
    ) -> Incident:
        now = datetime.now(UTC)
        incident = Incident(
            status=IncidentStatus.INVESTIGATING,
            severity=severity,
            incident_type=incident_type,
            title=title,
            description=description,
            affected_users_count=len(set(affected_users or [])),
            affected_strategies_count=len(set(affected_strategy_ids or [])),
            detected_at=now,
            started_at=now,
            material_user_impact="yes" if affected_users else "unknown",
            metadata_json=metadata or {},
        )
        self.session.add(incident)
        await self.session.flush()
        for user_id in affected_users or []:
            self.session.add(
                IncidentImpact(
                    incident_id=incident.id,
                    user_id=user_id,
                    impact_type="user",
                    details={},
                    created_at=now,
                )
            )
        for strategy_id in affected_strategy_ids or []:
            self.session.add(
                IncidentImpact(
                    incident_id=incident.id,
                    strategy_id=strategy_id,
                    impact_type="strategy",
                    details={},
                    created_at=now,
                )
            )
        self.session.add(
            IncidentUpdate(
                incident_id=incident.id,
                author_user_id=actor_user_id,
                update_type="opened",
                message=description,
                visible_to_users=severity in {IncidentSeverity.MAJOR, IncidentSeverity.CRITICAL},
                created_at=now,
            )
        )
        self._audit(
            actor_user_id,
            "incident.opened",
            "incident",
            incident.id,
            {"severity": severity.value, "incident_type": incident_type},
        )
        await self.session.flush()
        return incident

    async def resolve_incident(
        self,
        *,
        incident_id: UUID,
        actor_user_id: UUID,
        resolution: str,
        notify_users: bool = True,
    ) -> Incident:
        incident = await self.session.get(Incident, incident_id)
        if incident is None:
            raise ReliabilityError("incident_missing", "Incident not found.")
        now = datetime.now(UTC)
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = now
        incident.mitigation_status = "resolved"
        self.session.add(
            IncidentUpdate(
                incident_id=incident.id,
                author_user_id=actor_user_id,
                update_type="resolved",
                message=resolution,
                visible_to_users=notify_users,
                created_at=now,
            )
        )
        self._audit(
            actor_user_id,
            "incident.resolved",
            "incident",
            incident.id,
            {"notify_users": notify_users},
        )
        await self.session.flush()
        return incident

    async def pause_strategies_for_incident(
        self, *, incident: Incident, strategy_ids: list[UUID], actor_user_id: UUID | None
    ) -> list[Strategy]:
        strategies = (
            await self.session.scalars(
                select(Strategy).where(
                    Strategy.id.in_(strategy_ids),
                    Strategy.status == StrategyStatus.ACTIVE,
                )
            )
        ).all()
        now = datetime.now(UTC)
        for strategy in strategies:
            strategy.status = StrategyStatus.PAUSED
            strategy.paused_at = now
            self._audit(
                actor_user_id,
                "strategy.paused_for_incident",
                "strategy",
                strategy.id,
                {"incident_id": str(incident.id)},
            )
        await self.session.flush()
        return list(strategies)

    async def status_summary(self) -> HealthSummary:
        market_rows = (
            await self.session.execute(
                select(MarketDataHealth.status, func.count(MarketDataHealth.id)).group_by(
                    MarketDataHealth.status
                )
            )
        ).all()
        integration_rows = (
            await self.session.execute(
                select(IntegrationHealth.status, func.count(IntegrationHealth.id)).group_by(
                    IntegrationHealth.status
                )
            )
        ).all()
        open_incidents = await self.session.scalar(
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
        )
        failed_jobs = await self.session.scalar(
            select(func.count(ScanJob.id)).where(ScanJob.status == ScanJobStatus.FAILED)
        )
        market_data = {status.value: int(count) for status, count in market_rows}
        integrations = {status.value: int(count) for status, count in integration_rows}
        overall = HealthStatus.HEALTHY
        if open_incidents or market_data.get("down") or integrations.get("down"):
            overall = HealthStatus.DOWN
        elif failed_jobs or market_data.get("degraded") or integrations.get("degraded"):
            overall = HealthStatus.DEGRADED
        return HealthSummary(
            overall_status=overall,
            market_data=market_data,
            integrations=integrations,
            open_incidents=int(open_incidents or 0),
            failed_jobs=int(failed_jobs or 0),
            generated_at=datetime.now(UTC),
        )

    async def check_database(self) -> HealthStatus:
        try:
            await self.session.execute(text("select 1"))
        except Exception:
            return HealthStatus.DOWN
        return HealthStatus.HEALTHY

    @staticmethod
    def _market_status(
        *,
        source_status: str,
        data_age_seconds: int | None,
        missing: int,
        duplicates: int,
        out_of_order: int,
    ) -> HealthStatus:
        if source_status not in {"ok", "healthy"}:
            return HealthStatus.DOWN
        if missing or duplicates or out_of_order:
            return HealthStatus.DEGRADED
        if data_age_seconds is not None and data_age_seconds > 300:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    def _audit(
        self,
        actor_user_id: UUID | None,
        action: str,
        target_type: str,
        target_id: UUID,
        metadata: dict,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="admin" if actor_user_id else "system",
                action=action,
                target_type=target_type,
                target_id=str(target_id),
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )
