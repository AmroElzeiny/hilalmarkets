from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import AuditEvent, Strategy
from ai_market_monitor.db.models.enums import StrategyStatus


class MonitorOperationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class MonitorOperationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def pause(
        self,
        *,
        user_id: UUID,
        strategy_id: UUID,
        actor_type: str,
    ) -> Strategy:
        strategy = await self._strategy(user_id, strategy_id)
        strategy.status = StrategyStatus.PAUSED
        strategy.paused_at = datetime.now(UTC)
        self._audit(user_id, strategy, actor_type, "strategy.paused")
        await self.session.flush()
        return strategy

    async def resume(
        self,
        *,
        user_id: UUID,
        strategy_id: UUID,
        actor_type: str,
    ) -> Strategy:
        strategy = await self._strategy(user_id, strategy_id)
        strategy.status = StrategyStatus.ACTIVE
        strategy.paused_at = None
        self._audit(user_id, strategy, actor_type, "strategy.resumed")
        await self.session.flush()
        return strategy

    async def delete(
        self,
        *,
        user_id: UUID,
        strategy_id: UUID,
        actor_type: str,
    ) -> Strategy:
        strategy = await self._strategy(user_id, strategy_id)
        now = datetime.now(UTC)
        strategy.status = StrategyStatus.ARCHIVED
        strategy.archived_at = now
        strategy.paused_at = now
        strategy.active_version_id = None
        self._audit(user_id, strategy, actor_type, "strategy.deleted")
        await self.session.flush()
        return strategy

    async def _strategy(self, user_id: UUID, strategy_id: UUID) -> Strategy:
        strategy = await self.session.get(Strategy, strategy_id)
        if strategy is None or strategy.user_id != user_id:
            raise MonitorOperationError("strategy_not_found", "Monitor was not found.")
        if strategy.archived_at is not None or strategy.status == StrategyStatus.ARCHIVED:
            raise MonitorOperationError("strategy_not_found", "Monitor was not found.")
        return strategy

    def _audit(self, user_id: UUID, strategy: Strategy, actor_type: str, action: str) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=user_id,
                actor_type=actor_type,
                action=action,
                target_type="strategy",
                target_id=str(strategy.id),
                metadata_redacted={"status": strategy.status.value},
                created_at=datetime.now(UTC),
            )
        )
