from ai_market_monitor.engine.indicators import IndicatorRegistry, IndicatorWarmupError
from ai_market_monitor.engine.models import RiskCalculation
from ai_market_monitor.schemas.strategy import StrategyDefinition, StrategyDirection
from ai_market_monitor.services.interfaces import Candle


class RiskCalculationError(ValueError):
    pass


class RiskCalculator:
    def __init__(self, indicators: IndicatorRegistry | None = None) -> None:
        self.indicators = indicators or IndicatorRegistry()

    def calculate(
        self,
        strategy: StrategyDefinition,
        candles: list[Candle],
        *,
        account_balance: float | None = None,
        enforce_limits: bool = True,
    ) -> RiskCalculation:
        if not candles:
            raise RiskCalculationError("Cannot calculate risk without candles")
        latest = candles[-1]
        direction = strategy.direction
        if direction == StrategyDirection.BOTH:
            raise RiskCalculationError(
                "direction_ambiguous: evaluate a both-direction strategy for one side at a time"
            )
        entry_price = latest.close
        zone_percent = strategy.entry.zone_percent or 0
        entry_zone_low = entry_price * (1 - zone_percent / 100)
        entry_zone_high = entry_price * (1 + zone_percent / 100)
        stop_price = self._stop_price(strategy, candles, entry_price)
        if direction == StrategyDirection.LONG and stop_price >= entry_price:
            raise RiskCalculationError("stop_direction_invalid: stop must be below long entry")
        if direction == StrategyDirection.SHORT and stop_price <= entry_price:
            raise RiskCalculationError("stop_direction_invalid: stop must be above short entry")
        stop_distance = abs(entry_price - stop_price)
        stop_distance_percent = (stop_distance / entry_price) * 100
        if (
            enforce_limits
            and
            strategy.risk.maximum_stop_percent is not None
            and stop_distance_percent > strategy.risk.maximum_stop_percent
        ):
            raise RiskCalculationError("stop_distance_exceeded: stop distance exceeds maximum")
        targets: list[dict[str, float | str | None]] = []
        target_prices: list[float] = []
        for target in strategy.targets:
            if target.method == "risk_multiple":
                price = (
                    entry_price + stop_distance * target.value
                    if direction == StrategyDirection.LONG
                    else entry_price - stop_distance * target.value
                )
            elif target.method == "fixed_percent":
                price = (
                    entry_price * (1 + target.value / 100)
                    if direction == StrategyDirection.LONG
                    else entry_price * (1 - target.value / 100)
                )
            else:
                price = latest.high if direction == StrategyDirection.LONG else latest.low
            if direction == StrategyDirection.LONG and price <= entry_price:
                raise RiskCalculationError(
                    "target_direction_invalid: long target must exceed entry"
                )
            if direction == StrategyDirection.SHORT and price >= entry_price:
                raise RiskCalculationError(
                    "target_direction_invalid: short target must be below entry"
                )
            targets.append(
                {
                    "label": target.label,
                    "method": target.method,
                    "value": target.value,
                    "price": price,
                    "size_percent": target.size_percent,
                }
            )
            target_prices.append(price)
        first_target_price = target_prices[0] if target_prices else entry_price
        reward_to_risk = abs(first_target_price - entry_price) / stop_distance
        if (
            enforce_limits
            and
            strategy.risk.minimum_reward_to_risk is not None
            and reward_to_risk < strategy.risk.minimum_reward_to_risk
        ):
            raise RiskCalculationError("reward_to_risk_below_minimum: reward-to-risk below minimum")
        position_size = None
        risk_percent = (
            strategy.position_sizing.account_risk_percent or strategy.risk.account_risk_percent
        )
        if account_balance is not None and risk_percent is not None:
            risk_amount = account_balance * (risk_percent / 100)
            per_unit_risk = stop_distance
            position_size = risk_amount / per_unit_risk if per_unit_risk else None
        return RiskCalculation(
            direction=direction.value,
            entry_price=entry_price,
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            invalidation_level=stop_price,
            stop_price=stop_price,
            stop_distance_percent=stop_distance_percent,
            targets=targets,
            reward_to_risk=reward_to_risk,
            estimated_fee_bps=strategy.risk.estimated_fee_bps,
            estimated_slippage_bps=strategy.risk.estimated_slippage_bps,
            position_size=position_size,
        )

    def _stop_price(
        self, strategy: StrategyDefinition, candles: list[Candle], entry_price: float
    ) -> float:
        stop = strategy.stop
        method = stop.method or strategy.risk.stop_method
        if method == "fixed_percent":
            value = stop.value or strategy.risk.stop_value
            if value is None:
                raise RiskCalculationError("fixed_percent stop requires value")
            multiplier = (
                1 - value / 100 if strategy.direction == StrategyDirection.LONG else 1 + value / 100
            )
            return entry_price * multiplier
        if method == "atr":
            try:
                atr_value = self.indicators.calculate("atr", candles, period=stop.atr_period)
            except IndicatorWarmupError as exc:
                raise RiskCalculationError(str(exc)) from exc
            adjustment = atr_value * stop.atr_multiplier
            return (
                entry_price - adjustment
                if strategy.direction == StrategyDirection.LONG
                else entry_price + adjustment
            )
        lookback = min(stop.swing_lookback, len(candles))
        recent = candles[-lookback:]
        if method == "structure":
            method = "swing_low" if strategy.direction == StrategyDirection.LONG else "swing_high"
        if method == "technical_invalidation":
            method = "swing_low" if strategy.direction == StrategyDirection.LONG else "swing_high"
        if method == "swing_low":
            return min(candle.low for candle in recent)
        if method == "swing_high":
            return max(candle.high for candle in recent)
        raise RiskCalculationError(f"Unsupported stop method: {method}")
