from __future__ import annotations

import math
from datetime import UTC, datetime
from statistics import fmean, pstdev
from typing import Any

from ai_market_monitor.engine.models import ensure_aware
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import Candle, MarketDataProvider


class MarketRegimeAnalyzer:
    """Deterministic benchmark context for monitor-health diagnostics."""

    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    async def evaluate(
        self,
        definition: StrategyDefinition,
        *,
        evaluated_at: datetime | None = None,
    ) -> dict[str, Any]:
        evaluated_at = ensure_aware(evaluated_at or datetime.now(UTC))
        quote = definition.universe.quote_currencies[0].upper()
        benchmarks = [f"BTC/{quote}", f"ETH/{quote}"]
        timeframe = "1h"
        histories: dict[str, list[Candle]] = {}
        errors: list[dict[str, str]] = []
        for symbol in benchmarks:
            try:
                candles = await self.provider.fetch_ohlcv(
                    definition.universe.exchange,
                    symbol,
                    timeframe,
                    240,
                )
                histories[symbol] = [
                    candle
                    for candle in candles
                    if candle.is_closed and ensure_aware(candle.timestamp) <= evaluated_at
                ]
            except Exception as exc:
                errors.append({"symbol": symbol, "error": type(exc).__name__})

        usable = {symbol: rows for symbol, rows in histories.items() if len(rows) >= 50}
        if not usable:
            return {
                "classification": "unavailable",
                "fit_score": 50.0,
                "status": "partial",
                "timeframe": timeframe,
                "benchmarks": benchmarks,
                "metrics": {},
                "errors": errors,
                "evaluated_at": evaluated_at.isoformat(),
                "explanation": (
                    "Benchmark candles were unavailable, so a neutral regime-fit score was used."
                ),
            }

        metrics: dict[str, dict[str, float]] = {}
        trend_votes: list[int] = []
        volatility_values: list[float] = []
        returns_24h: list[float] = []
        for symbol, candles in usable.items():
            closes = [float(candle.close) for candle in candles]
            returns = [
                (current / previous) - 1
                for previous, current in zip(closes[:-1], closes[1:], strict=False)
                if previous
            ]
            ema_fast = _ema(closes, 50)
            ema_slow = _ema(closes, 200 if len(closes) >= 200 else len(closes))
            return_24h = ((closes[-1] / closes[-25]) - 1) * 100 if len(closes) >= 25 else 0
            realized_volatility = (
                pstdev(returns[-48:]) * math.sqrt(24 * 365) * 100
                if len(returns) >= 2
                else 0
            )
            trend_vote = 1 if ema_fast > ema_slow else -1 if ema_fast < ema_slow else 0
            trend_votes.append(trend_vote)
            volatility_values.append(realized_volatility)
            returns_24h.append(return_24h)
            metrics[symbol] = {
                "close": round(closes[-1], 8),
                "ema_50": round(ema_fast, 8),
                "ema_200": round(ema_slow, 8),
                "return_24h_percent": round(return_24h, 4),
                "realized_volatility_percent": round(realized_volatility, 4),
            }

        average_return = fmean(returns_24h)
        average_volatility = fmean(volatility_values)
        trend_balance = sum(trend_votes) / len(trend_votes)
        if average_volatility >= 100:
            volatility_regime = "high_volatility"
        elif average_volatility <= 35:
            volatility_regime = "low_volatility"
        else:
            volatility_regime = "normal_volatility"
        if trend_balance >= 0.5 and average_return > 0.5:
            direction_regime = "trending_up"
        elif trend_balance <= -0.5 and average_return < -0.5:
            direction_regime = "trending_down"
        else:
            direction_regime = "ranging"
        classification = f"{direction_regime}:{volatility_regime}"
        fit_score = _strategy_regime_fit(definition, direction_regime, volatility_regime)
        return {
            "classification": classification,
            "fit_score": fit_score,
            "status": (
                "healthy"
                if fit_score >= 75
                else "partial"
                if fit_score >= 50
                else "needs_attention"
            ),
            "timeframe": timeframe,
            "benchmarks": list(usable),
            "metrics": metrics,
            "errors": errors,
            "evaluated_at": evaluated_at.isoformat(),
            "explanation": _regime_explanation(
                definition,
                direction_regime,
                volatility_regime,
                fit_score,
            ),
        }


def _ema(values: list[float], period: int) -> float:
    period = max(1, min(period, len(values)))
    multiplier = 2 / (period + 1)
    current = fmean(values[:period])
    for value in values[period:]:
        current = value * multiplier + current * (1 - multiplier)
    return current


def _strategy_regime_fit(
    definition: StrategyDefinition,
    direction_regime: str,
    volatility_regime: str,
) -> float:
    direction = definition.direction.value
    condition_text = " ".join(
        f"{item.get('key', '')} {item.get('label', '')} {item.get('condition_type', '')}"
        for item in _condition_rows(definition.conditions.model_dump(mode="json"))
    ).lower()
    mean_reversion = any(
        token in condition_text
        for token in ("mean_reversion", "oversold", "overbought", "bollinger", "vwap_reclaim")
    )
    breakout = any(
        token in condition_text
        for token in ("breakout", "new_high", "new_low", "momentum", "trend")
    )
    score = 75.0
    if direction_regime == "trending_up":
        score += 15 if direction == "long" else -25
    elif direction_regime == "trending_down":
        score += 15 if direction == "short" else -25
    elif mean_reversion:
        score += 10
    if breakout and volatility_regime == "low_volatility":
        score -= 10
    if mean_reversion and volatility_regime == "high_volatility":
        score -= 15
    return round(max(0, min(100, score)), 2)


def _condition_rows(node: dict[str, Any]) -> list[dict[str, Any]]:
    if "condition_type" in node:
        return [node]
    rows: list[dict[str, Any]] = []
    for child in node.get("children", []):
        if isinstance(child, dict):
            rows.extend(_condition_rows(child))
    return rows


def _regime_explanation(
    definition: StrategyDefinition,
    direction_regime: str,
    volatility_regime: str,
    fit_score: float,
) -> str:
    return (
        f"BTC and ETH benchmarks classify the market as {direction_regime.replace('_', ' ')} "
        f"with {volatility_regime.replace('_', ' ')}. The deterministic fit score for this "
        f"{definition.direction.value} monitor is {fit_score:.0f}/100."
    )
