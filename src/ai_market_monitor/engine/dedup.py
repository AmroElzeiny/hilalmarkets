import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import Alert
from ai_market_monitor.db.models.enums import AlertType
from ai_market_monitor.engine.models import EvaluationResult


def stable_event_hash(parts: dict) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def provider_neutral_symbol(symbol: str) -> str:
    return symbol.upper().replace("-", "/").strip().split(":", 1)[0]


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    allowed: bool
    deduplication_key: str
    reason: str | None = None


class AlertFatigueGuard:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def check(
        self,
        result: EvaluationResult,
        *,
        user_id,
        strategy_version_id,
        alert_type: AlertType,
        cooldown_seconds: int,
        maximum_alerts_per_hour: int,
        daily_alert_budget: int | None,
    ) -> DuplicateDecision:
        key = stable_event_hash(
            {
                "strategy_version_id": str(strategy_version_id),
                "symbol": provider_neutral_symbol(result.symbol),
                "timeframe": result.timeframe,
                "alert_type": alert_type.value,
                "setup_state": result.setup_state.value if result.setup_state else None,
                "market_data_timestamp": result.market_data_timestamp,
            }
        )
        existing = await self.session.scalar(select(Alert.id).where(Alert.deduplication_key == key))
        if existing:
            return DuplicateDecision(False, key, "duplicate_event_hash")
        now = datetime.now(UTC)
        if cooldown_seconds:
            recent_alerts = (
                await self.session.scalars(
                    select(Alert)
                    .where(
                        Alert.user_id == user_id,
                        Alert.strategy_version_id == strategy_version_id,
                        Alert.created_at >= now - timedelta(seconds=cooldown_seconds),
                    )
                    .limit(200)
                )
            ).all()
            current_symbol = provider_neutral_symbol(result.symbol)
            recent_same_symbol = next(
                (
                    alert
                    for alert in recent_alerts
                    if _proof_symbol(alert.proof_receipt) == current_symbol
                ),
                None,
            )
            if recent_same_symbol:
                return DuplicateDecision(False, key, "symbol_cooldown")
        hourly_count = await self.session.scalar(
            select(func.count(Alert.id)).where(
                Alert.user_id == user_id,
                Alert.strategy_version_id == strategy_version_id,
                Alert.created_at >= now - timedelta(hours=1),
            )
        )
        if hourly_count and hourly_count >= maximum_alerts_per_hour:
            return DuplicateDecision(False, key, "maximum_alerts_per_hour")
        if daily_alert_budget is not None:
            daily_count = await self.session.scalar(
                select(func.count(Alert.id)).where(
                    Alert.user_id == user_id,
                    Alert.strategy_version_id == strategy_version_id,
                    Alert.created_at >= now - timedelta(days=1),
                )
            )
            if daily_count and daily_count >= daily_alert_budget:
                return DuplicateDecision(False, key, "daily_alert_budget")
        return DuplicateDecision(True, key)


def _proof_symbol(proof_receipt: Any) -> str | None:
    if not isinstance(proof_receipt, dict):
        return None
    symbol = proof_receipt.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    return provider_neutral_symbol(symbol)
