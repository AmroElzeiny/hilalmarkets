from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.plans import PLAN_DEFINITIONS, PlanDefinition, timeframe_to_minutes
from ai_market_monitor.db.models import (
    AuditEvent,
    EntitlementSnapshot,
    Plan,
    Strategy,
    Subscription,
    Trial,
    UsageRecord,
)
from ai_market_monitor.db.models.enums import StrategyStatus, SubscriptionStatus, TrialStatus
from ai_market_monitor.schemas.strategy import StrategyDefinition


class EntitlementError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EntitlementContext:
    plan: PlanDefinition
    source: str
    source_id: UUID | None
    ends_at: datetime | None

    def feature_enabled(self, key: str) -> bool:
        return bool(self.plan.features.get(key, False))

    def limit(self, key: str) -> int | float | str | None:
        return self.plan.limits.get(key)


class PlanCatalogService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def sync_defaults(self) -> None:
        for definition in PLAN_DEFINITIONS.values():
            plan = await self.session.scalar(select(Plan).where(Plan.code == definition.code))
            payload = {
                "name": definition.name,
                "description": definition.description,
                "price_monthly": definition.monthly_price,
                "currency": definition.currency,
                "max_active_strategies": int(definition.limits.get("active_strategies", 0) or 0),
                "max_symbols_per_strategy": int(
                    definition.limits.get("symbols_per_strategy", 0) or 0
                ),
                "minimum_scan_interval_seconds": int(
                    max(60, int(definition.limits.get("minimum_timeframe_minutes", 1) or 1) * 60)
                ),
                "telegram_enabled": bool(definition.features.get("telegram", False)),
                "discord_enabled": bool(definition.features.get("discord", False)),
                "backtest_enabled": bool(definition.features.get("advanced_forensics", False)),
                "features": {
                    "limits": definition.limits,
                    "features": definition.features,
                },
                "is_active": True,
            }
            if plan is None:
                self.session.add(Plan(code=definition.code, **payload))
            else:
                for key, value in payload.items():
                    setattr(plan, key, value)
        await self.session.flush()

    async def get_or_sync(self, code: str) -> Plan:
        await self.sync_defaults()
        plan = await self.session.scalar(select(Plan).where(Plan.code == code))
        if plan is None:
            raise EntitlementError("plan_missing", f"Plan {code} is not configured")
        return plan


class EntitlementService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def current(self, user_id: UUID) -> EntitlementContext:
        now = datetime.now(UTC)
        subscription = await self.session.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]),
                (Subscription.current_period_end.is_(None))
                | (Subscription.current_period_end > now),
            )
            .order_by(Subscription.updated_at.desc())
        )
        if subscription:
            plan = await self.session.get(Plan, subscription.plan_id)
            code = plan.code if plan else "demo"
            return EntitlementContext(
                plan=PLAN_DEFINITIONS.get(code, PLAN_DEFINITIONS["demo"]),
                source="subscription",
                source_id=subscription.id,
                ends_at=subscription.current_period_end,
            )
        trial = await self.session.scalar(
            select(Trial)
            .where(
                Trial.user_id == user_id,
                or_(
                    Trial.status == TrialStatus.ELIGIBLE,
                    and_(
                        Trial.status.in_(
                            [
                                TrialStatus.ACTIVE,
                                TrialStatus.ACTIVATED,
                                TrialStatus.ENDING_SOON,
                                TrialStatus.MANUALLY_EXTENDED,
                            ]
                        ),
                        Trial.starts_at <= now,
                        Trial.ends_at > now,
                    ),
                ),
            )
            .order_by(Trial.ends_at.desc())
        )
        if trial:
            return EntitlementContext(
                plan=PLAN_DEFINITIONS["pro_trial"],
                source="trial",
                source_id=trial.id,
                ends_at=trial.ends_at,
            )
        return EntitlementContext(
            plan=PLAN_DEFINITIONS["demo"], source="default", source_id=None, ends_at=None
        )

    async def snapshot(self, user_id: UUID) -> EntitlementSnapshot:
        context = await self.current(user_id)
        plan = await PlanCatalogService(self.session).get_or_sync(context.plan.code)
        snapshot = EntitlementSnapshot(
            user_id=user_id,
            plan_id=plan.id,
            plan_code=context.plan.code,
            source=context.source,
            source_id=str(context.source_id) if context.source_id else None,
            status="active",
            limits=context.plan.limits,
            features=context.plan.features,
            starts_at=datetime.now(UTC),
            ends_at=context.ends_at,
            created_at=datetime.now(UTC),
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def require_feature(self, user_id: UUID, feature: str) -> EntitlementContext:
        context = await self.current(user_id)
        if not context.feature_enabled(feature):
            raise EntitlementError("feature_not_available", f"{feature} is not included")
        return context

    async def enforce_strategy_activation(
        self,
        user_id: UUID,
        definition: StrategyDefinition,
        *,
        strategy_id: UUID | None = None,
    ) -> EntitlementContext:
        context = await self.current(user_id)
        active_query: Select[tuple[int]] = select(func.count(Strategy.id)).where(
            Strategy.user_id == user_id,
            Strategy.status == StrategyStatus.ACTIVE,
        )
        if strategy_id is not None:
            active_query = active_query.where(Strategy.id != strategy_id)
        active_count = await self.session.scalar(active_query)
        limit = int(context.limit("active_strategies") or 0)
        if active_count is not None and active_count >= limit:
            raise EntitlementError(
                "active_strategy_limit",
                f"Plan allows {limit} active monitor(s). Pause one before activating another.",
            )
        symbol_limit = int(context.limit("symbols_per_strategy") or 0)
        requested_symbols = len(_unique_symbols(definition.universe.include_symbols)) or (
            definition.universe.max_symbols or symbol_limit
        )
        if requested_symbols > symbol_limit:
            raise EntitlementError(
                "symbol_limit",
                f"Plan allows up to {symbol_limit} symbols per strategy.",
            )
        minimum_minutes = int(context.limit("minimum_timeframe_minutes") or 1)
        all_timeframes = [definition.base_timeframe, *definition.supporting_timeframes]
        if any(timeframe_to_minutes(timeframe) < minimum_minutes for timeframe in all_timeframes):
            raise EntitlementError(
                "timeframe_not_allowed",
                f"Plan supports {minimum_minutes}-minute timeframe or higher.",
            )
        if "discord" in definition.alerts.channels and not context.feature_enabled("discord"):
            raise EntitlementError("discord_not_included", "Discord alerts are not included.")
        return context

    async def pause_excess_after_downgrade(self, user_id: UUID) -> list[Strategy]:
        context = await self.current(user_id)
        limit = int(context.limit("active_strategies") or 0)
        strategies = (
            await self.session.scalars(
                select(Strategy)
                .where(Strategy.user_id == user_id, Strategy.status == StrategyStatus.ACTIVE)
                .order_by(Strategy.activated_at.asc().nulls_last(), Strategy.created_at.asc())
            )
        ).all()
        excess = strategies[limit:]
        now = datetime.now(UTC)
        for strategy in excess:
            strategy.status = StrategyStatus.PAUSED
            strategy.paused_at = now
            self.session.add(
                AuditEvent(
                    actor_user_id=None,
                    actor_type="system",
                    action="strategy.paused_after_downgrade",
                    target_type="strategy",
                    target_id=str(strategy.id),
                    metadata_redacted={"plan": context.plan.code},
                    created_at=now,
                )
            )
        await self.session.flush()
        return list(excess)


def _unique_symbols(symbols: list[str]) -> set[str]:
    return {
        symbol.upper().replace("-", "/").strip() for symbol in symbols if symbol and symbol.strip()
    }


class UsageService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        user_id: UUID,
        metric: str,
        *,
        quantity: int = 1,
        period_start: datetime,
        period_end: datetime,
        idempotency_key: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
        metadata: dict | None = None,
    ) -> UsageRecord:
        existing = await self.session.scalar(
            select(UsageRecord).where(UsageRecord.idempotency_key == idempotency_key)
        )
        if existing:
            return existing
        record = UsageRecord(
            user_id=user_id,
            metric=metric,
            quantity=quantity,
            period_start=period_start,
            period_end=period_end,
            subject_type=subject_type,
            subject_id=subject_id,
            idempotency_key=idempotency_key,
            metadata_json=metadata or {},
            created_at=datetime.now(UTC),
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def summary(self, user_id: UUID, period_start: datetime, period_end: datetime) -> dict:
        rows = (
            await self.session.execute(
                select(UsageRecord.metric, func.sum(UsageRecord.quantity))
                .where(
                    UsageRecord.user_id == user_id,
                    UsageRecord.period_start >= period_start,
                    UsageRecord.period_end <= period_end,
                )
                .group_by(UsageRecord.metric)
            )
        ).all()
        return {metric: int(total or 0) for metric, total in rows}
