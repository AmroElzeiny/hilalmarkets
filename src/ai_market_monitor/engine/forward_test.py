from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ai_market_monitor.db.models.enums import ScanOutcome
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import EvaluationResult, MarketSnapshot
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import Candle


@dataclass(frozen=True, slots=True)
class ForwardTestRecord:
    symbol: str
    exchange: str
    evaluated_at: datetime
    outcome: ScanOutcome
    hypothetical_entry: float | None
    hypothetical_stop: float | None
    hypothetical_targets: list[dict[str, Any]]
    estimated_fee_bps: float
    estimated_slippage_bps: float
    proof: dict[str, Any]


class ForwardTestEngine:
    def __init__(self, engine: StrategyRuleEngine | None = None) -> None:
        self.engine = engine or StrategyRuleEngine()

    def evaluate_live_tick(
        self,
        strategy: StrategyDefinition,
        market: MarketSnapshot,
        candle_sets: dict[str, list[Candle]],
        *,
        evaluation_time: datetime,
        strategy_version: str,
        previous_score: float | None = None,
    ) -> ForwardTestRecord | None:
        if not strategy.forward_test.enabled:
            return None
        result: EvaluationResult = self.engine.evaluate(
            strategy,
            market,
            candle_sets,
            evaluation_time=evaluation_time,
            strategy_version=strategy_version,
            previous_score=previous_score,
        )
        return ForwardTestRecord(
            symbol=market.symbol,
            exchange=market.exchange,
            evaluated_at=evaluation_time,
            outcome=result.outcome,
            hypothetical_entry=result.risk.entry_price if result.risk else None,
            hypothetical_stop=result.risk.stop_price if result.risk else None,
            hypothetical_targets=result.risk.targets if result.risk else [],
            estimated_fee_bps=strategy.forward_test.estimated_fee_bps
            or strategy.risk.estimated_fee_bps,
            estimated_slippage_bps=strategy.forward_test.estimated_slippage_bps
            or strategy.risk.estimated_slippage_bps,
            proof=result.proof_receipt(),
        )
