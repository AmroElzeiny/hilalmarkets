import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_market_monitor.engine.models import MarketSnapshot
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import Candle


def load_strategy(name: str = "liquidity_sweep_continuation.json") -> StrategyDefinition:
    path = Path("samples/strategies") / name
    return StrategyDefinition.model_validate(json.loads(path.read_text()))


def candles(
    count: int,
    *,
    start: datetime,
    minutes: int,
    close: float = 100,
    volume: float = 1000,
) -> list[Candle]:
    return [
        Candle(
            timestamp=start + timedelta(minutes=minutes * index),
            open=close - 0.2,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=volume,
            is_closed=True,
        )
        for index in range(count)
    ]


def market() -> MarketSnapshot:
    return MarketSnapshot(
        exchange="binance",
        symbol="SOL/USDT",
        base_asset="SOL",
        quote_asset="USDT",
        quote_volume_24h=5_000_000,
        average_candle_volume=2_000,
        spread_bps=5,
        listed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def candle_sets(volume_multiplier: float = 1.42, include_active_lookahead: bool = False):
    start_15m = datetime(2026, 6, 14, 0, 0, tzinfo=UTC)
    base = candles(30, start=start_15m, minutes=15, close=100, volume=1000)
    base[-1] = Candle(
        timestamp=base[-1].timestamp,
        open=100,
        high=102,
        low=98,
        close=100.5,
        volume=1000 * volume_multiplier,
        is_closed=True,
    )
    if include_active_lookahead:
        base.append(
            Candle(
                timestamp=base[-1].timestamp + timedelta(minutes=15),
                open=100,
                high=106,
                low=90,
                close=103,
                volume=3000,
                is_closed=False,
            )
        )
    start_4h = datetime(2026, 1, 1, tzinfo=UTC)
    higher = candles(220, start=start_4h, minutes=240, close=100, volume=5000)
    higher[-1] = Candle(
        timestamp=higher[-1].timestamp,
        open=100,
        high=104,
        low=99,
        close=103,
        volume=5000,
        is_closed=True,
    )
    return {"15m": base, "4h": higher}
