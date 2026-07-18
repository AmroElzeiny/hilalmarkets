from dataclasses import dataclass
from typing import Literal

from ai_market_monitor.db.models.enums import (
    ConditionType,
    LogicalOperator,
    MarketType,
    TriggerMode,
)
from ai_market_monitor.schemas.strategy import (
    AlertPolicy,
    Comparator,
    ConditionGroup,
    ConditionRule,
    EntryPolicy,
    Operand,
    OperandKind,
    RiskPolicy,
    StrategyDefinition,
    StrategyDirection,
    TargetPolicy,
    UniverseDefinition,
)


@dataclass(frozen=True, slots=True)
class StrategyTemplateSpec:
    key: str
    label: str
    description: str
    setup_text: str
    category: str
    tags: tuple[str, ...]
    condition_specs: tuple[dict, ...]
    direction: StrategyDirection = StrategyDirection.LONG
    base_timeframe: str = "15m"
    best_timeframe: str = "15m"
    difficulty: Literal["beginner", "intermediate", "advanced"] = "intermediate"
    required_data: tuple[str, ...] = ("ohlcv",)
    free_light_compatible: bool = True
    risk_assumptions: str = "Structure stop, maximum 2% stop distance, minimum 2R target."
    minimum_reward_to_risk: float = 2.0
    maximum_stop_percent: float = 2.0

    def definition(self) -> StrategyDefinition:
        conditions: list[ConditionRule | ConditionGroup] = [
            _condition_from_spec(spec, self.base_timeframe) for spec in self.condition_specs
        ]
        supporting = sorted(
            {
                condition.timeframe
                for condition in conditions
                if isinstance(condition, ConditionRule)
                and condition.timeframe != self.base_timeframe
            }
        )
        return StrategyDefinition(
            name=self.label,
            description=self.description,
            direction=self.direction,
            base_timeframe=self.base_timeframe,
            supporting_timeframes=supporting,
            trigger_mode=TriggerMode.CANDLE_CLOSE,
            universe=UniverseDefinition(
                exchange="binance",
                market_type=MarketType.SPOT,
                quote_currencies=["USDT"],
                min_quote_volume_24h=1_000_000,
                min_listing_age_days=30,
                max_spread_bps=25,
                min_historical_candles=200,
                exclude_stablecoins=True,
                exclude_leveraged_tokens=True,
            ),
            conditions=ConditionGroup(
                key="entry_conditions",
                operator=LogicalOperator.AND,
                children=conditions,
            ),
            entry=EntryPolicy(calculation="signal_close", expires_after_candles=3),
            targets=[
                TargetPolicy(
                    label="T1",
                    method="risk_multiple",
                    value=max(2.0, self.minimum_reward_to_risk),
                )
            ],
            risk=RiskPolicy(
                stop_method="structure",
                maximum_stop_percent=self.maximum_stop_percent,
                target_method="risk_multiple",
                target_value=max(2.0, self.minimum_reward_to_risk),
                minimum_reward_to_risk=self.minimum_reward_to_risk,
                estimated_fee_bps=10,
                estimated_slippage_bps=5,
            ),
            alerts=AlertPolicy(
                forming_alerts=True,
                near_miss_threshold=70,
                channels=["telegram"],
                maximum_alerts_per_hour=50,
            ),
        )


def _indicator(
    key: str,
    label: str,
    indicator: str,
    comparator: Comparator,
    value: float | None = None,
    *,
    timeframe: str = "15m",
    parameters: dict | None = None,
    right_indicator: str | None = None,
    right_parameters: dict | None = None,
    weight: float = 1,
    forming_tolerance_percent: float | None = None,
    required: bool = True,
) -> dict:
    return {
        "kind": "indicator",
        "key": key,
        "label": label,
        "indicator": indicator,
        "parameters": parameters or {},
        "comparator": comparator,
        "value": value,
        "right_indicator": right_indicator,
        "right_parameters": right_parameters or {},
        "timeframe": timeframe,
        "weight": weight,
        "forming_tolerance_percent": forming_tolerance_percent,
        "required": required,
    }


def _price_vs_indicator(
    key: str,
    label: str,
    indicator: str,
    comparator: Comparator,
    *,
    timeframe: str = "15m",
    price_field: str = "close",
    parameters: dict | None = None,
    weight: float = 1,
    forming_tolerance_percent: float | None = None,
) -> dict:
    return {
        "kind": "price_vs_indicator",
        "key": key,
        "label": label,
        "indicator": indicator,
        "parameters": parameters or {},
        "price_field": price_field,
        "comparator": comparator,
        "timeframe": timeframe,
        "weight": weight,
        "forming_tolerance_percent": forming_tolerance_percent,
    }


def _price_action(
    key: str,
    label: str,
    name: str,
    *,
    timeframe: str = "15m",
    parameters: dict | None = None,
    weight: float = 1,
    forming_tolerance_percent: float | None = None,
) -> dict:
    return {
        "kind": "price_action",
        "key": key,
        "label": label,
        "name": name,
        "parameters": parameters or {"lookback": 20},
        "timeframe": timeframe,
        "weight": weight,
        "forming_tolerance_percent": forming_tolerance_percent,
    }


def _candle(
    key: str,
    label: str,
    name: str,
    *,
    timeframe: str = "15m",
    parameters: dict | None = None,
    weight: float = 1,
) -> dict:
    return {
        "kind": "candle",
        "key": key,
        "label": label,
        "name": name,
        "parameters": parameters or {},
        "timeframe": timeframe,
        "weight": weight,
    }


def _condition_from_spec(spec: dict, fallback_timeframe: str) -> ConditionRule:
    timeframe = spec.get("timeframe", fallback_timeframe)
    kind = spec["kind"]
    if kind == "indicator":
        right = (
            Operand(
                kind=OperandKind.INDICATOR,
                name=spec["right_indicator"],
                parameters=spec.get("right_parameters", {}),
            )
            if spec.get("right_indicator")
            else Operand(kind=OperandKind.CONSTANT, value=spec["value"])
        )
        return ConditionRule(
            key=spec["key"],
            label=spec["label"],
            condition_type=ConditionType.INDICATOR,
            timeframe=timeframe,
            left=Operand(
                kind=OperandKind.INDICATOR,
                name=spec["indicator"],
                parameters=spec.get("parameters", {}),
            ),
            comparator=spec["comparator"],
            right=right,
            required=spec.get("required", True),
            weight=spec.get("weight", 1),
            forming_tolerance_percent=spec.get("forming_tolerance_percent"),
            required_data=["ohlcv"],
        )
    if kind == "price_vs_indicator":
        return ConditionRule(
            key=spec["key"],
            label=spec["label"],
            condition_type=ConditionType.INDICATOR,
            timeframe=timeframe,
            left=Operand(kind=OperandKind.PRICE, field=spec.get("price_field", "close")),
            comparator=spec["comparator"],
            right=Operand(
                kind=OperandKind.INDICATOR,
                name=spec["indicator"],
                parameters=spec.get("parameters", {}),
            ),
            weight=spec.get("weight", 1),
            forming_tolerance_percent=spec.get("forming_tolerance_percent"),
            required_data=["ohlcv"],
        )
    if kind == "price_action":
        return ConditionRule(
            key=spec["key"],
            label=spec["label"],
            condition_type=ConditionType.PRICE_ACTION,
            timeframe=timeframe,
            left=Operand(
                kind=OperandKind.PRICE_ACTION,
                name=spec["name"],
                parameters=spec.get("parameters", {}),
            ),
            comparator=Comparator.IS_TRUE,
            weight=spec.get("weight", 1),
            forming_tolerance_percent=spec.get("forming_tolerance_percent"),
            required_data=["ohlcv"],
        )
    if kind == "candle":
        return ConditionRule(
            key=spec["key"],
            label=spec["label"],
            condition_type=ConditionType.CANDLE_PATTERN,
            timeframe=timeframe,
            left=Operand(
                kind=OperandKind.CANDLE_PATTERN,
                name=spec["name"],
                parameters=spec.get("parameters", {}),
            ),
            comparator=Comparator.IS_TRUE,
            weight=spec.get("weight", 1),
            required_data=["ohlcv"],
        )
    raise ValueError(f"Unsupported template condition kind: {kind}")


EMA_200_4H = _price_vs_indicator(
    "price_above_4h_ema_200",
    "Price above four-hour EMA 200",
    "ema",
    Comparator.GREATER_THAN,
    timeframe="4h",
    parameters={"period": 200, "field": "close"},
    weight=2,
)

VOLUME_15 = _indicator(
    "relative_volume",
    "Volume at least 1.5x average",
    "volume_ratio",
    Comparator.GREATER_THAN_OR_EQUAL,
    1.5,
    parameters={"period": 20},
    forming_tolerance_percent=15,
)


BUILTIN_STRATEGY_TEMPLATES: dict[str, StrategyTemplateSpec] = {
    "liquidity_sweep": StrategyTemplateSpec(
        key="liquidity_sweep",
        label="Liquidity Sweep Continuation",
        description="Bullish liquidity sweep with trend, volume and risk validation.",
        category="price_action",
        tags=("liquidity", "trend", "volume"),
        condition_specs=(
            EMA_200_4H,
            _price_action(
                "bullish_liquidity_sweep",
                "Bullish liquidity sweep",
                "bullish_liquidity_sweep",
                weight=2,
                forming_tolerance_percent=15,
            ),
            VOLUME_15,
        ),
        setup_text=(
            "Find bullish liquidity sweeps on Binance spot USDT pairs. Price should be above "
            "the four-hour 200 EMA, volume should be at least 1.5 times average, stop distance "
            "must be under 2%, and target should offer at least 2.5R."
        ),
        minimum_reward_to_risk=2.5,
    ),
    "rsi_pullback": StrategyTemplateSpec(
        key="rsi_pullback",
        label="RSI Pullback",
        description="Trend pullback where RSI recovers after weakness.",
        category="indicator",
        tags=("rsi", "trend", "pullback"),
        condition_specs=(
            EMA_200_4H,
            _indicator(
                "rsi_exits_oversold",
                "RSI exits oversold above 30",
                "rsi",
                Comparator.CROSSES_ABOVE,
                30,
                parameters={"period": 14, "field": "close"},
                forming_tolerance_percent=10,
            ),
            _indicator(
                "relative_volume_above_average",
                "Volume above average",
                "volume_ratio",
                Comparator.GREATER_THAN_OR_EQUAL,
                1.0,
                parameters={"period": 20},
            ),
        ),
        setup_text=(
            "Scan USDT spot coins on Binance where RSI crosses back above 30, price is above "
            "4h EMA 200, volume is at least average, stop max 2%, target at least 2R."
        ),
    ),
    "vwap_reclaim": StrategyTemplateSpec(
        key="vwap_reclaim",
        label="VWAP Reclaim",
        description="Price crosses back above VWAP while trend remains positive.",
        category="indicator",
        tags=("vwap", "reclaim", "trend"),
        condition_specs=(
            EMA_200_4H,
            _price_vs_indicator(
                "price_reclaims_vwap",
                "Price reclaims VWAP",
                "vwap",
                Comparator.CROSSES_ABOVE,
                parameters={"period": 20},
            ),
        ),
        setup_text="Find pullbacks to VWAP in an uptrend and alert when price reclaims VWAP.",
    ),
    "ema_trend_continuation": StrategyTemplateSpec(
        key="ema_trend_continuation",
        label="EMA Trend Continuation",
        description="Trend continuation with EMA slope and higher-low structure.",
        category="trend",
        tags=("ema", "trend", "continuation"),
        condition_specs=(
            _price_vs_indicator(
                "price_above_ema_50",
                "Price above EMA 50",
                "ema",
                Comparator.GREATER_THAN,
                parameters={"period": 50, "field": "close"},
            ),
            _indicator(
                "ema_50_slope_up",
                "EMA 50 slope up",
                "ema_slope",
                Comparator.GREATER_THAN,
                0,
                parameters={"period": 50, "field": "close"},
            ),
            _price_action("higher_low", "Higher low", "higher_low"),
        ),
        setup_text="Find EMA trend continuation setups with EMA 50 rising and a higher low.",
    ),
    "ema_crossover": StrategyTemplateSpec(
        key="ema_crossover",
        label="EMA Crossover",
        description="Fast EMA crosses above slow EMA with higher-timeframe trend confirmation.",
        category="trend",
        tags=("ema", "crossover"),
        condition_specs=(
            EMA_200_4H,
            _indicator(
                "ema_20_crosses_ema_50",
                "EMA 20 crosses above EMA 50",
                "ema",
                Comparator.CROSSES_ABOVE,
                right_indicator="ema",
                parameters={"period": 20, "field": "close"},
                right_parameters={"period": 50, "field": "close"},
                value=None,
            ),
        ),
        setup_text="Find EMA crossover setups where EMA 20 crosses above EMA 50 in an uptrend.",
    ),
    "macd_momentum_shift": StrategyTemplateSpec(
        key="macd_momentum_shift",
        label="MACD Momentum Shift",
        description="MACD histogram turns positive while price is in an uptrend.",
        category="momentum",
        tags=("macd", "momentum"),
        condition_specs=(
            EMA_200_4H,
            _indicator(
                "macd_histogram_turns_positive",
                "MACD histogram turns positive",
                "macd",
                Comparator.CROSSES_ABOVE,
                0,
                parameters={"component": "histogram"},
            ),
        ),
        setup_text="Only alert if 1h MACD histogram turns positive and price is above 200 EMA.",
    ),
    "bollinger_squeeze_breakout": StrategyTemplateSpec(
        key="bollinger_squeeze_breakout",
        label="Bollinger Squeeze Breakout",
        description="Consolidation and Bollinger squeeze followed by range breakout.",
        category="volatility",
        tags=("bollinger", "squeeze", "breakout"),
        condition_specs=(
            _price_action(
                "bollinger_squeeze",
                "Bollinger squeeze",
                "bollinger_squeeze",
                parameters={"lookback": 20, "period": 20, "max_bandwidth_percent": 5},
            ),
            _price_action(
                "range_breakout", "Range breakout", "range_breakout", parameters={"lookback": 40}
            ),
            VOLUME_15,
        ),
        setup_text="Show coins near breakout from consolidation with Bollinger squeeze.",
    ),
    "range_breakout_retest": StrategyTemplateSpec(
        key="range_breakout_retest",
        label="Range Breakout Retest",
        description="Breakout from a range followed by a retest of the breakout level.",
        category="price_action",
        tags=("breakout", "retest"),
        condition_specs=(
            _price_action(
                "range_breakout",
                "Range breakout",
                "range_breakout",
                parameters={"lookback": 40},
                weight=2,
            ),
            _price_action(
                "breakout_retest",
                "Breakout retest holds",
                "breakout_retest",
                parameters={"lookback": 40},
            ),
        ),
        setup_text="Find range breakouts that retest the breakout level and hold.",
    ),
    "breakout_volume": StrategyTemplateSpec(
        key="breakout_volume",
        label="Volume Breakout",
        description="Recent resistance breakout confirmed by relative volume.",
        category="breakout",
        tags=("breakout", "volume", "resistance"),
        condition_specs=(
            _price_action(
                "range_breakout",
                "Price breaks recent resistance",
                "range_breakout",
                parameters={"lookback": 40},
                weight=2,
            ),
            _indicator(
                "relative_volume_18",
                "Volume at least 1.8x average",
                "volume_ratio",
                Comparator.GREATER_THAN_OR_EQUAL,
                1.8,
                parameters={"period": 20},
                forming_tolerance_percent=15,
            ),
        ),
        setup_text=(
            "Find bullish range breakouts on Binance spot USDT pairs. Price closes above recent "
            "resistance on the 15-minute candle and volume is at least 1.8 times average."
        ),
    ),
    "support_retest_bounce": StrategyTemplateSpec(
        key="support_retest_bounce",
        label="Support Retest Bounce",
        description="Price retests support and closes green.",
        category="price_action",
        tags=("support", "bounce"),
        condition_specs=(
            _price_action("support_retest", "Support retest", "support_retest"),
            _candle("green_candle", "Candle closes green", "green_candle"),
        ),
        setup_text="Find support retest bounces where the candle closes green.",
    ),
    "resistance_rejection_short": StrategyTemplateSpec(
        key="resistance_rejection_short",
        label="Resistance Rejection Short",
        description="Bearish rejection from resistance with overbought RSI.",
        category="price_action",
        tags=("resistance", "short", "rsi"),
        direction=StrategyDirection.SHORT,
        condition_specs=(
            _price_action("resistance_retest", "Resistance rejection", "resistance_retest"),
            _indicator(
                "rsi_overbought",
                "RSI overbought",
                "rsi",
                Comparator.GREATER_THAN_OR_EQUAL,
                70,
                parameters={"period": 14},
            ),
            _candle("red_candle", "Candle closes red", "red_candle"),
        ),
        setup_text="Look for bearish rejection from resistance with RSI overbought.",
    ),
    "higher_low_continuation": StrategyTemplateSpec(
        key="higher_low_continuation",
        label="Higher-Low Continuation",
        description="Higher low after liquidity sweep in an uptrend.",
        category="price_action",
        tags=("higher_low", "liquidity", "continuation"),
        condition_specs=(
            EMA_200_4H,
            _price_action(
                "bullish_liquidity_sweep", "Bullish liquidity sweep", "bullish_liquidity_sweep"
            ),
            _price_action("higher_low", "Higher low", "higher_low"),
        ),
        setup_text="Find coins making higher low after liquidity sweep.",
    ),
    "previous_high_breakout": StrategyTemplateSpec(
        key="previous_high_breakout",
        label="Previous High Breakout",
        description="Price breaks a previous high with volume confirmation.",
        category="breakout",
        tags=("high", "breakout"),
        condition_specs=(
            _price_action(
                "previous_high_breakout",
                "Previous high breakout",
                "higher_high",
                parameters={"lookback": 96},
                weight=2,
            ),
            VOLUME_15,
        ),
        setup_text="Find coins breaking above their previous high with volume confirmation.",
    ),
    "previous_low_sweep_reversal": StrategyTemplateSpec(
        key="previous_low_sweep_reversal",
        label="Previous Low Sweep Reversal",
        description="Previous low sweep followed by bullish candle close.",
        category="liquidity",
        tags=("sweep", "reversal"),
        condition_specs=(
            _price_action(
                "previous_low_sweep", "Previous low sweep", "previous_low_sweep", weight=2
            ),
            _candle("green_candle", "Candle closes green", "green_candle"),
        ),
        setup_text=(
            "Alert me when SOL sweeps the previous low and reclaims it, but only if "
            "the candle closes green."
        ),
    ),
    "low_volume_pullback": StrategyTemplateSpec(
        key="low_volume_pullback",
        label="Low-Volume Pullback",
        description="Pullback in an uptrend where volume dries up.",
        category="volume",
        tags=("pullback", "volume"),
        condition_specs=(
            EMA_200_4H,
            _indicator(
                "volume_dry_up",
                "Volume dry-up below 0.8x average",
                "volume_ratio",
                Comparator.LESS_THAN_OR_EQUAL,
                0.8,
                parameters={"period": 20},
            ),
            _price_action("support_retest", "Support retest", "support_retest"),
        ),
        setup_text="Find low volume pullbacks in an uptrend.",
    ),
    "strong_close_momentum": StrategyTemplateSpec(
        key="strong_close_momentum",
        label="Strong Close Momentum",
        description="Strong close near high after volume expansion.",
        category="candle",
        tags=("momentum", "candle", "volume"),
        condition_specs=(
            _candle("strong_close_near_high", "Strong close near high", "strong_close_near_high"),
            VOLUME_15,
        ),
        setup_text="Scan for strong close near high after volume expansion.",
    ),
    "atr_volatility_expansion": StrategyTemplateSpec(
        key="atr_volatility_expansion",
        label="ATR Volatility Expansion",
        description="ATR percent and range expansion confirm volatility expansion.",
        category="volatility",
        tags=("atr", "volatility"),
        condition_specs=(
            _indicator(
                "atr_percent_above_1",
                "ATR percent above 1%",
                "atr_percent",
                Comparator.GREATER_THAN_OR_EQUAL,
                1,
                parameters={"period": 14},
            ),
            _candle(
                "range_expansion_candle",
                "Range expansion candle",
                "range_expansion_candle",
                parameters={"period": 20, "range_multiplier": 1.5},
            ),
        ),
        setup_text="Find ATR volatility expansion with a range expansion candle.",
    ),
    "stochastic_pullback": StrategyTemplateSpec(
        key="stochastic_pullback",
        label="Stochastic Pullback",
        description="Stochastic exits oversold while higher-timeframe trend is positive.",
        category="momentum",
        tags=("stochastic", "pullback"),
        condition_specs=(
            EMA_200_4H,
            _indicator(
                "stochastic_k_exits_oversold",
                "Stochastic K exits oversold",
                "stochastic",
                Comparator.CROSSES_ABOVE,
                20,
                parameters={"component": "k"},
            ),
            _indicator(
                "stochastic_k_crosses_d",
                "Stochastic K crosses D",
                "stochastic",
                Comparator.CROSSES_ABOVE,
                right_indicator="stochastic",
                parameters={"component": "k"},
                right_parameters={"component": "d"},
                value=None,
            ),
        ),
        setup_text="Find stochastic pullbacks where K crosses D after oversold in an uptrend.",
    ),
    "six_month_high_breakout": StrategyTemplateSpec(
        key="six_month_high_breakout",
        label="Six-Month High Breakout",
        description="Pairs breaking their highest high from the last six months.",
        category="breakout",
        tags=("ath", "high", "momentum"),
        condition_specs=(
            _price_action(
                "six_month_high_breakout",
                "Price breaks the six-month high",
                "higher_high",
                parameters={"lookback": 17280},
                weight=2,
            ),
        ),
        setup_text=(
            "Find Binance spot USDT pairs where price breaks the highest high from the last six "
            "months on the 15-minute timeframe."
        ),
    ),
    "btc_trend_filter_altcoin": StrategyTemplateSpec(
        key="btc_trend_filter_altcoin",
        label="BTC Trend Filter Altcoin Scanner",
        description=(
            "Altcoin breakout template with BTC trend filter noted for cross-market support."
        ),
        category="advanced",
        tags=("btc", "altcoins", "trend"),
        condition_specs=(
            _price_action(
                "range_breakout",
                "Altcoin range breakout",
                "range_breakout",
                parameters={"lookback": 40},
                weight=2,
            ),
            VOLUME_15,
        ),
        setup_text=(
            "Find alts breaking above their 20-day high with volume spike and BTC above its "
            "4h EMA 200. BTC cross-market filtering is recognized and requires benchmark data."
        ),
        difficulty="advanced",
        risk_assumptions=(
            "Uses executable altcoin breakout rules now. BTC trend filter is recognized but "
            "requires cross-market benchmark support before live gating."
        ),
    ),
}


TEMPLATE_UI_CATEGORIES: dict[str, tuple[str, ...]] = {
    "liquidity_sweep": (
        "Liquidity sweep",
        "Trend continuation",
        "Volume spike",
    ),
    "rsi_pullback": (
        "Momentum pullback",
        "RSI oversold/overbought",
        "Trend continuation",
    ),
    "vwap_reclaim": (
        "Momentum pullback",
        "Trend continuation",
    ),
    "ema_trend_continuation": (
        "Trend continuation",
        "Moving average trend filter",
    ),
    "ema_crossover": (
        "Trend continuation",
        "Moving average trend filter",
    ),
    "macd_momentum_shift": (
        "Momentum pullback",
        "Trend continuation",
    ),
    "bollinger_squeeze_breakout": (
        "Breakout confirmation",
        "Volatility expansion",
        "Volume spike",
    ),
    "range_breakout_retest": ("Breakout confirmation",),
    "breakout_volume": (
        "Breakout confirmation",
        "Volume spike",
    ),
    "support_retest_bounce": (
        "Reversal setup",
        "Candle pattern",
    ),
    "resistance_rejection_short": (
        "Reversal setup",
        "RSI oversold/overbought",
        "Candle pattern",
    ),
    "higher_low_continuation": (
        "Liquidity sweep",
        "Trend continuation",
    ),
    "previous_high_breakout": (
        "Breakout confirmation",
        "Volume spike",
    ),
    "previous_low_sweep_reversal": (
        "Liquidity sweep",
        "Reversal setup",
        "Candle pattern",
    ),
    "low_volume_pullback": (
        "Momentum pullback",
        "Trend continuation",
    ),
    "strong_close_momentum": (
        "Volume spike",
        "Candle pattern",
    ),
    "atr_volatility_expansion": (
        "Volatility expansion",
        "Candle pattern",
    ),
    "stochastic_pullback": (
        "Momentum pullback",
        "RSI oversold/overbought",
        "Trend continuation",
    ),
    "six_month_high_breakout": ("Breakout confirmation",),
    "btc_trend_filter_altcoin": (
        "BTC market-context filter",
        "Breakout confirmation",
    ),
}


def builtin_template_payloads() -> list[dict]:
    return [
        {
            "key": template.key,
            "name": template.label,
            "description": template.description,
            "category": template.category,
            "tags": list(template.tags),
            "schema_json": template.definition().model_dump(mode="json"),
            "setup_text": template.setup_text,
            "shared_scope": "builtin",
            "best_timeframe": template.best_timeframe,
            "difficulty": template.difficulty,
            "required_data": list(template.required_data),
            "free_light_compatible": template.free_light_compatible,
            "risk_assumptions": template.risk_assumptions,
            "example_prompt": template.setup_text,
            "ui_categories": list(
                TEMPLATE_UI_CATEGORIES.get(
                    template.key,
                    (template.category.replace("_", " ").title(),),
                )
            ),
        }
        for template in BUILTIN_STRATEGY_TEMPLATES.values()
    ]
