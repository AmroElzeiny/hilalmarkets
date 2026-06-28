from datetime import UTC, datetime, timedelta

from ai_market_monitor.market_context import MarketRegimeAnalyzer
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.market_preview import MarketPreviewService
from tests.factories import candle_sets, load_strategy


class HistoricalProvider:
    def __init__(self, sets):
        self.sets = sets

    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return ["SOL/USDT"]

    async def fetch_ohlcv(self, exchange: str, symbol: str, timeframe: str, limit: int):
        return self.sets[timeframe][-limit:]


async def test_market_preview_uses_live_rule_engine_proof_shape():
    strategy = load_strategy()
    strategy.universe.include_symbols = ["SOL/USDT"]
    preview = await MarketPreviewService(
        HistoricalProvider(candle_sets(volume_multiplier=1.6)),
        candle_limit=300,
        symbol_limit=1,
    ).run(strategy)
    assert preview.status == "succeeded"
    assert preview.sample_matches
    proof = preview.sample_matches[-1]["proof"]
    assert proof["strategy_schema_hash"] == strategy.canonical_hash()
    assert proof["condition_tree"]["operator"] == "and"
    assert proof["risk_validation"]["state"] == "passed"


class RisingBenchmarkProvider:
    async def fetch_ohlcv(self, exchange: str, symbol: str, timeframe: str, limit: int):
        start = datetime(2026, 6, 1, tzinfo=UTC)
        return [
            Candle(
                timestamp=start + timedelta(hours=index),
                open=100 + index,
                high=101 + index,
                low=99 + index,
                close=100 + index,
                volume=1000,
                is_closed=True,
            )
            for index in range(limit)
        ]


async def test_market_regime_analyzer_scores_long_monitor_against_benchmarks():
    result = await MarketRegimeAnalyzer(RisingBenchmarkProvider()).evaluate(
        load_strategy(),
        evaluated_at=datetime(2026, 6, 15, tzinfo=UTC),
    )

    assert result["classification"].startswith("trending_up:")
    assert result["fit_score"] >= 75
    assert result["metrics"]["BTC/USDT"]["ema_50"] > result["metrics"]["BTC/USDT"]["ema_200"]
