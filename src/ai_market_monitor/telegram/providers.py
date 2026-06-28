from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    NearMissSnapshot,
    ScanResult,
    Strategy,
    StrategyVersion,
)
from ai_market_monitor.telegram.types import NearMissListItem


class DatabaseNearMissProvider:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def top(
        self,
        user_id: UUID,
        *,
        strategy_id: UUID | None,
        limit: int,
        minimum_score: float,
    ) -> list[NearMissListItem]:
        query = (
            select(NearMissSnapshot, ScanResult)
            .join(
                StrategyVersion,
                StrategyVersion.id == NearMissSnapshot.strategy_version_id,
            )
            .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
            .join(ScanResult, ScanResult.id == NearMissSnapshot.scan_result_id)
            .where(
                Strategy.user_id == user_id,
                NearMissSnapshot.completion_score >= minimum_score,
                NearMissSnapshot.completion_score < 100,
            )
            .order_by(
                NearMissSnapshot.completion_score.desc(),
                NearMissSnapshot.captured_at.desc(),
            )
            .limit(max(limit * 5, limit))
        )
        if strategy_id is not None:
            query = query.where(Strategy.id == strategy_id)
        rows = (await self.session.execute(query)).all()
        items: list[NearMissListItem] = []
        seen: set[tuple[str, str, str]] = set()
        for snapshot, scan_result in rows:
            key = (snapshot.exchange, snapshot.symbol, snapshot.timeframe)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                NearMissListItem(
                    symbol=snapshot.symbol,
                    exchange=snapshot.exchange,
                    timeframe=snapshot.timeframe,
                    score=float(snapshot.completion_score),
                    trend=snapshot.trend,
                    passed=snapshot.passed_condition_keys,
                    missing=[
                        str(item.get("name") or item.get("condition_id") or "condition")
                        for item in snapshot.missing_conditions
                    ],
                    chart_reference=scan_result.proof_summary.get("chart_reference"),
                    metadata={
                        "scan_result_id": str(scan_result.id),
                        "captured_at": snapshot.captured_at.isoformat(),
                    },
                )
            )
            if len(items) == limit:
                break
        return items
