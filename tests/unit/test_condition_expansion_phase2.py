import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models.enums import ConditionType, LogicalOperator
from ai_market_monitor.engine.builder_templates import condition_template
from ai_market_monitor.engine.candle_patterns import detect_candle_pattern
from ai_market_monitor.engine.capabilities import (
    all_capabilities,
    executable_capabilities,
    unsupported_capabilities,
)
from ai_market_monitor.engine.condition_registry import condition_registry_payload
from ai_market_monitor.engine.context_conditions import evaluate_time_condition
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.indicators import IndicatorRegistry, IndicatorWarmupError
from ai_market_monitor.engine.models import EvaluationState
from ai_market_monitor.engine.price_action import evaluate_price_action
from ai_market_monitor.provider_context import ProviderContextService
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import (
    Comparator,
    ConditionGroup,
    ConditionRule,
    Operand,
    OperandKind,
)
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from tests.factories import candle_sets, load_strategy, market


def _candle(
    index: int,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1000,
    start: datetime | None = None,
) -> Candle:
    start = start or datetime(2026, 1, 1, tzinfo=UTC)
    return Candle(
        timestamp=start + timedelta(minutes=index * 15),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        is_closed=True,
    )


def _trend_history(count: int = 160) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        _candle(
            index,
            open_price=100 + index * 0.1,
            high=101 + index * 0.1,
            low=99 + index * 0.1,
            close=100.5 + index * 0.1,
            volume=1000 + (index % 7) * 50,
            start=start,
        )
        for index in range(count)
    ]


def test_reference_period_sweep_uses_completed_previous_week_levels():
    start = datetime(2026, 6, 1, tzinfo=UTC)  # Monday
    candles = [
        Candle(
            timestamp=start + timedelta(hours=index),
            open=100,
            high=110,
            low=90,
            close=100,
            volume=1000,
            is_closed=True,
        )
        for index in range(168)
    ]
    candles.append(
        Candle(
            timestamp=start + timedelta(days=7),
            open=100,
            high=111,
            low=89,
            close=100,
            volume=1200,
            is_closed=True,
        )
    )

    assert evaluate_price_action(
        "reference_period_sweep",
        candles,
        {"lookback": 20, "reference_period": "week", "side": "low", "timezone": "UTC"},
    )
    assert evaluate_price_action(
        "reference_period_sweep",
        candles,
        {"lookback": 20, "reference_period": "week", "side": "high", "timezone": "UTC"},
    )


@pytest.mark.parametrize(
    "name",
    [
        "historical_volatility",
        "choppiness_index",
        "ulcer_index",
        "on_balance_volume",
        "chaikin_money_flow",
        "force_index",
        "volume_oscillator",
        "volume_profile_proxy",
        "pivot_points",
    ],
)
def test_new_indicator_families_have_explicit_warmup_failures(name):
    with pytest.raises(IndicatorWarmupError):
        IndicatorRegistry().calculate(name, _trend_history(1))


def test_candle_pattern_library_positive_negative_and_insufficient_data():
    bullish = [
        _candle(0, open_price=10, high=10.2, low=8.8, close=9),
        _candle(1, open_price=8.8, high=10.4, low=8.7, close=10.3),
    ]
    negative = [
        _candle(0, open_price=10, high=10.2, low=9.5, close=10.1),
        _candle(1, open_price=10.1, high=10.3, low=9.8, close=10.2),
    ]
    assert detect_candle_pattern("bullish_engulfing", bullish) is True
    assert detect_candle_pattern("bullish_engulfing", negative) is False
    with pytest.raises(IndicatorWarmupError, match="requires 3 candles"):
        detect_candle_pattern("morning_star", bullish)


def test_price_action_breakout_and_fvg_are_deterministic():
    history = _trend_history(25)
    prior_high = max(candle.high for candle in history[-21:-1])
    last = history[-1]
    history[-1] = _candle(
        24,
        open_price=prior_high - 0.2,
        high=prior_high + 1,
        low=prior_high - 0.5,
        close=prior_high + 0.5,
        volume=2000,
    )
    assert evaluate_price_action("breaks_n_candle_high", history, {"lookback": 20}) is True
    history[-1] = last
    assert evaluate_price_action("breaks_n_candle_high", history, {"lookback": 20}) is False

    gap = [
        _candle(0, open_price=9.5, high=10, low=9, close=9.8),
        _candle(1, open_price=10.2, high=11, low=10, close=10.8),
        _candle(2, open_price=11.2, high=12, low=11, close=11.8),
    ]
    assert evaluate_price_action("bullish_fair_value_gap", gap, {"lookback": 2}) is True


def test_time_conditions_use_timezone_aware_calendar_rules():
    timestamp = datetime(2026, 1, 2, 23, 30, tzinfo=UTC)
    candles = [
        Candle(
            timestamp=timestamp,
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1000,
            is_closed=True,
        )
    ]
    assert (
        evaluate_time_condition(
            "weekend_filter",
            candles,
            {"timezone": "Asia/Tokyo"},
            {},
        )
        is True
    )
    assert (
        evaluate_time_condition(
            "weekday_only",
            candles,
            {"timezone": "UTC"},
            {},
        )
        is True
    )


async def test_new_prompt_aliases_map_to_visual_condition_keys():
    preview = await RuleBasedStrategyInterpreter().interpret(
        GuidedSetupRequest(
            exchange="binance",
            quote_currency="USDT",
            timeframe="15m",
            setup_mode="free_text",
            setup_text=(
                "OBV rising and CMF above zero with a bullish fair value gap, weekdays only"
            ),
            trigger_mode="candle_close",
            delivery_channels=["web"],
        )
    )
    keys = {condition.key for condition in preview.strategy.conditions.children}
    assert preview.activation_blocked is False
    assert {
        "on_balance_volume",
        "chaikin_money_flow",
        "bullish_fair_value_gap",
        "weekday_only",
    }.issubset(keys)


async def test_clarification_provenance_does_not_block_prompt_coverage():
    preview = await RuleBasedStrategyInterpreter().interpret(
        GuidedSetupRequest(
            exchange="binance",
            quote_currency="USDT",
            timeframe="1d",
            setup_mode="free_text",
            setup_text=(
                "RSI must be above 50 when the trigger occurs\n"
                "Clarification answer for rsi_period: 14 (default)\n"
                "Clarification answer for rsi_timeframe: Use the trigger timeframe"
            ),
            trigger_mode="candle_close",
            delivery_channels=["telegram"],
        )
    )
    rsi_rule = next(
        condition
        for condition in preview.strategy.conditions.children
        if condition.left.name == "rsi"
    )
    assert rsi_rule.timeframe == "1d"
    assert not any(
        issue.code == "prompt_fragment_unclassified"
        for issue in preview.unsupported_conditions
    )
    assert preview.raw_metadata["prompt_coverage_report"]["activation_blocked"] is False


async def test_vague_aliases_are_deterministic_and_do_not_false_match_macro_terms():
    interpreter = RuleBasedStrategyInterpreter()
    guided = dict(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )
    trending = await interpreter.interpret(
        GuidedSetupRequest(
            **guided,
            setup_text="trending market with a strong candle and volume burst",
        )
    )
    operands = {condition.left.name for condition in trending.strategy.conditions.children}
    assert {"choppiness_index", "wide_range_candle", "volume_ratio"}.issubset(operands)
    choppiness = next(
        condition
        for condition in trending.strategy.conditions.children
        if condition.left.name == "choppiness_index"
    )
    assert choppiness.comparator == Comparator.LESS_THAN_OR_EQUAL
    assert choppiness.right.value == 38.2

    golden_cross = await interpreter.interpret(
        GuidedSetupRequest(**guided, setup_text="EMA 20 golden cross above EMA 50")
    )
    assert not any(
        issue.message.startswith("Gold Trend Filter")
        for issue in golden_cross.unsupported_conditions
    )


def test_provider_bound_condition_is_unavailable_not_guessed():
    strategy = load_strategy()
    strategy.risk.enabled = False
    strategy.universe.min_historical_candles = 1
    strategy.conditions = ConditionGroup(
        key="context",
        operator=LogicalOperator.AND,
        children=[
            ConditionRule(
                key="btc_context",
                label="BTC trend context",
                condition_type=ConditionType.MARKET_FILTER,
                timeframe="15m",
                left=Operand(
                    kind=OperandKind.MARKET_METRIC,
                    name="btc_usdt_trend_filter",
                    parameters={
                        "provider": "cross_market",
                        "context_category": "cross_market",
                    },
                ),
                comparator=Comparator.IS_TRUE,
            )
        ],
    )
    history = _trend_history(40)
    result = StrategyRuleEngine().evaluate(
        strategy,
        market(),
        {"15m": history},
        evaluation_time=history[-1].timestamp,
        strategy_version="provider-unavailable",
    )
    assert result.conditions[0].state == EvaluationState.UNAVAILABLE
    assert result.conditions[0].actual_value is None
    assert "unavailable" in result.conditions[0].explanation


def test_registry_categories_provider_badges_and_builder_markup():
    payload = condition_registry_payload()
    audit_payload = condition_registry_payload(include_provider_required=True)
    categories = {item["key"] for item in payload["categories"]}
    keys = [item["key"] for item in payload["items"]]
    assert len(keys) == len(set(keys))
    assert {
        "price",
        "candle_pattern",
        "market_structure",
        "news_events",
        "order_book_liquidity",
        "ranking_universe",
        "advanced_logic",
    }.issubset(categories)
    assert payload["hidden_provider_required"]["count"] > 0
    assert "cpi_event_window" not in keys
    event = next(item for item in audit_payload["items"] if item["key"] == "cpi_event_window")
    assert event["implementation_status"] == "provider_required"
    assert event["provider_badge"] == "event_feed"
    assert event["prompt_aliases"]
    pattern = next(item for item in payload["items"] if item["key"] == "morning_star")
    assert {"min_body_percent", "confirmation_required"}.issubset(
        parameter["name"] for parameter in pattern["parameters"]
    )
    dashboard = Path("src/ai_market_monitor/templates/dashboard.html").read_text()
    script = Path("src/ai_market_monitor/static/dashboard.js").read_text()
    assert "Search condition library" in dashboard
    assert "Advanced raw condition" in dashboard
    assert "Explain This Rule" in script


class _ContextProvider:
    async def list_symbols(self, exchange, quote_currencies):
        return ["BTC/USDT", "ETH/USDT", "SOL/USDT", "LINK/USDT"]

    async def fetch_ohlcv(self, exchange, symbol, timeframe, limit):
        return self._candles(symbol, limit)

    async def fetch_ohlcv_range(self, exchange, symbol, timeframe, start, end, limit):
        return self._candles(symbol, limit, start=start)

    async def fetch_universe_metadata(
        self,
        exchange,
        symbols,
        *,
        include_listing_dates=False,
    ):
        volumes = {
            "BTC/USDT": 1_000_000_000,
            "ETH/USDT": 500_000_000,
            "SOL/USDT": 250_000_000,
            "LINK/USDT": 50_000_000,
        }
        return {
            symbol: {
                "quote_volume_24h": volumes[symbol],
                "relative_strength_btc": 4 if symbol == "SOL/USDT" else 1,
            }
            for symbol in symbols
        }

    async def fetch_order_book_context(self, exchange, symbol, *, depth=50):
        return {
            "spread_bps": 3,
            "total_depth_quote": 2_000_000,
            "depth_imbalance": 0.4,
            "large_wall_above": False,
            "large_wall_below": True,
            "liquidity_wall_pulled": False,
            "liquidity_wall_added": True,
            "approaching_liquidity_wall": True,
            "slippage_bps": 2,
            "trade_count_ratio": 2,
            "average_trade_size_ratio": 2,
            "buy_volume_ratio": 0.7,
            "sell_volume_ratio": 0.3,
            "trade_imbalance": 0.4,
            "recent_trade_volume": 50_000,
        }

    async def fetch_derivatives_context(self, exchange, symbol):
        return {
            "funding_rate": 0.0001,
            "open_interest": 120,
            "previous_open_interest": 100,
        }

    @staticmethod
    def _candles(symbol, limit, *, start=None):
        start = start or datetime(2026, 1, 1, tzinfo=UTC)
        slope = 0.4 if symbol == "SOL/USDT" else 0.2 if symbol == "ETH/USDT" else 0.1
        return [
            Candle(
                timestamp=start + timedelta(hours=index),
                open=100 + slope * index,
                high=101 + slope * index,
                low=99 + slope * index,
                close=100.5 + slope * index,
                volume=1000 + index * (4 if symbol == "SOL/USDT" else 1),
                is_closed=True,
            )
            for index in range(limit)
        ]


def _provider_condition(
    key: str,
    category: str,
    *,
    parameters: dict | None = None,
) -> ConditionRule:
    return ConditionRule(
        key=key,
        label=key.replace("_", " ").title(),
        condition_type=ConditionType.MARKET_FILTER,
        timeframe="1h",
        left=Operand(
            kind=OperandKind.MARKET_METRIC,
            name=key,
            parameters={
                "provider": category,
                "context_category": category,
                **(parameters or {}),
            },
        ),
        comparator=Comparator.IS_TRUE,
    )


async def test_public_provider_context_executes_all_local_families():
    definition = load_strategy().model_copy(deep=True)
    definition.base_timeframe = "1h"
    definition.supporting_timeframes = []
    definition.risk.enabled = False
    definition.conditions = ConditionGroup(
        key="provider_context",
        operator=LogicalOperator.AND,
        children=[
            _provider_condition("btc_usdt_trend_filter", "cross_market"),
            _provider_condition("symbol_outperforming_btc", "cross_market"),
            _provider_condition("market_breadth_improving", "market_breadth"),
            _provider_condition(
                "top_percent_24h_volume",
                "universe_ranking",
                parameters={"percentile": 75},
            ),
            _provider_condition(
                "spread_below_threshold",
                "order_book",
                parameters={"threshold": 10},
            ),
            _provider_condition("funding_rate_positive", "derivatives"),
        ],
    )
    provider = _ContextProvider()
    evaluated_at = datetime(2026, 6, 1, tzinfo=UTC)
    symbol_candles = await provider.fetch_ohlcv("binance", "SOL/USDT", "1h", 220)
    context = await ProviderContextService(
        provider,
        Settings(
            app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
            market_breadth_max_symbols=10,
            binance_derivatives_enabled=True,
        ),
    ).build(
        definition,
        "SOL/USDT",
        {"1h": symbol_candles},
        evaluated_at,
    )

    assert context["cross_market"]["btc_usdt_trend_filter"] is True
    assert context["cross_market"]["symbol_outperforming_btc"] is True
    assert context["market_breadth"]["market_breadth_improving"] is True
    assert context["universe_ranking"]["top_percent_24h_volume"] is True
    assert context["order_book"]["spread_below_threshold"] is True
    assert context["derivatives"]["funding_rate_positive"] is True


async def test_derivatives_context_is_disabled_by_default_for_spot_mode():
    definition = load_strategy().model_copy(deep=True)
    definition.base_timeframe = "1h"
    definition.supporting_timeframes = []
    definition.risk.enabled = False
    definition.conditions = ConditionGroup(
        key="derivatives_disabled",
        operator=LogicalOperator.AND,
        children=[_provider_condition("funding_rate_positive", "derivatives")],
    )
    provider = _ContextProvider()
    evaluated_at = datetime(2026, 6, 1, tzinfo=UTC)
    symbol_candles = await provider.fetch_ohlcv("binance", "SOL/USDT", "1h", 220)
    context = await ProviderContextService(
        provider,
        Settings(app_secret_key="test-secret-key-with-at-least-thirty-two-characters"),
    ).build(
        definition,
        "SOL/USDT",
        {"1h": symbol_candles},
        evaluated_at,
    )

    assert context["derivatives"]["_metadata"]["status"] == "disabled"
    assert "funding_rate_positive" not in context["derivatives"]


async def test_external_context_contract_supplies_only_requested_values():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["category"] == "event_feed"
        assert payload["requested_keys"] == ["cpi_event_window"]
        assert request.headers["Authorization"] == "Bearer test-event-key"
        return httpx.Response(
            200,
            json={
                "values": {"cpi_event_window": True, "ignored": "not requested"},
                "as_of": "2026-06-25T12:00:00Z",
            },
        )

    definition = load_strategy().model_copy(deep=True)
    definition.risk.enabled = False
    definition.conditions = ConditionGroup(
        key="event",
        operator=LogicalOperator.AND,
        children=[_provider_condition("cpi_event_window", "event_feed")],
    )
    settings = Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        event_feed_api_url="https://events.example.test/context",
        event_feed_api_key=SecretStr("test-event-key"),
    )
    context = await ProviderContextService(
        _ContextProvider(),
        settings,
        transport=httpx.MockTransport(handler),
    ).build(
        definition,
        "SOL/USDT",
        candle_sets(),
        datetime(2026, 6, 25, 12, tzinfo=UTC),
    )

    assert context["event_feed"]["cpi_event_window"] is True
    assert "ignored" not in context["event_feed"]


def test_risk_and_persisted_runtime_conditions_execute_inside_same_tree():
    definition = load_strategy().model_copy(deep=True)
    definition.conditions = ConditionGroup(
        key="risk_runtime",
        operator=LogicalOperator.AND,
        children=[
            ConditionRule(
                key="net_rr",
                label="Net reward to risk",
                condition_type=ConditionType.RISK,
                timeframe="15m",
                left=Operand(
                    kind=OperandKind.RISK_METRIC,
                    name="reward_to_risk_after_fees",
                ),
                comparator=Comparator.GREATER_THAN_OR_EQUAL,
                right=Operand(kind=OperandKind.CONSTANT, value=1.5),
            ),
            _provider_condition(
                "same_symbol_alert_cooldown",
                "alert_behavior",
                parameters={"cooldown_minutes": 60},
            ).model_copy(update={"timeframe": "15m"}),
            ConditionRule(
                key="setup_age",
                label="Setup age",
                condition_type=ConditionType.MARKET_FILTER,
                timeframe="15m",
                left=Operand(
                    kind=OperandKind.MARKET_METRIC,
                    name="setup_age_minutes",
                    parameters={"context_category": "setup_lifecycle"},
                ),
                comparator=Comparator.LESS_THAN_OR_EQUAL,
                right=Operand(kind=OperandKind.CONSTANT, value=120),
            ),
        ],
    )
    history = candle_sets(volume_multiplier=1.6)
    evaluated_at = history["15m"][-1].timestamp
    result = StrategyRuleEngine().evaluate(
        definition,
        market(),
        history,
        evaluation_time=evaluated_at,
        strategy_version="runtime-context",
        condition_context={
            "last_symbol_triggered_at": evaluated_at - timedelta(hours=2),
            "setup_first_detected_at": evaluated_at - timedelta(minutes=30),
        },
    )

    assert all(item.state == EvaluationState.PASSED for item in result.conditions)
    assert result.risk_validation.state == EvaluationState.PASSED


def test_all_registered_capabilities_are_executable_and_schema_valid():
    assert len(all_capabilities()) == 502
    assert len(executable_capabilities()) == 502
    assert unsupported_capabilities() == ()
    for capability in executable_capabilities():
        ConditionRule.model_validate(condition_template(capability))


def test_every_executable_capability_reaches_a_non_error_engine_state():
    history = candle_sets(volume_multiplier=1.6)
    evaluated_at = history["15m"][-1].timestamp
    for capability in executable_capabilities():
        rule = ConditionRule.model_validate(condition_template(capability, timeframe="15m"))
        definition = load_strategy().model_copy(deep=True)
        definition.universe.min_historical_candles = 1
        definition.conditions = ConditionGroup(
            key="single_capability",
            operator=LogicalOperator.AND,
            children=[rule],
        )
        category = str(
            rule.left.parameters.get("context_category")
            or rule.left.parameters.get("provider")
            or ""
        )
        context = {category: {rule.left.name: True}} if category else {}
        context.update(
            {
                "evaluation_time": evaluated_at,
                "last_symbol_triggered_at": None,
                "last_strategy_triggered_at": None,
                "alerts_last_hour": 0,
                "alerts_last_day": 0,
                "setup_state": "forming",
                "setup_first_detected_at": evaluated_at,
                "setup_entry_zone_active": True,
                "setup_state_changed": True,
            }
        )
        result = StrategyRuleEngine().evaluate(
            definition,
            market(),
            history,
            evaluation_time=evaluated_at,
            strategy_version="all-capabilities",
            condition_context=context,
        )
        assert result.conditions[0].state != EvaluationState.ERROR, capability.key


def test_every_provider_condition_has_pass_fail_and_unavailable_proof_states():
    provider_categories = {
        "cross_market",
        "crypto_index",
        "macro_market",
        "market_breadth",
        "token_categories",
        "event_feed",
        "order_book",
        "derivatives",
        "universe_ranking",
    }
    history = candle_sets(volume_multiplier=1.6)
    evaluated_at = history["15m"][-1].timestamp
    for capability in executable_capabilities():
        if capability.provider_required not in provider_categories:
            continue
        rule = ConditionRule.model_validate(condition_template(capability, timeframe="15m"))
        definition = load_strategy().model_copy(deep=True)
        definition.risk.enabled = False
        definition.universe.min_historical_candles = 1
        definition.conditions = ConditionGroup(
            key="provider_capability",
            operator=LogicalOperator.AND,
            children=[rule],
        )
        category = str(rule.left.parameters["context_category"])
        states = []
        for supplied in (True, False, None):
            context = {} if supplied is None else {category: {rule.left.name: supplied}}
            result = StrategyRuleEngine().evaluate(
                definition,
                market(),
                history,
                evaluation_time=evaluated_at,
                strategy_version="provider-states",
                condition_context=context,
            )
            states.append(result.conditions[0].state)
        assert states == [
            EvaluationState.PASSED,
            EvaluationState.FAILED,
            EvaluationState.UNAVAILABLE,
        ], capability.key
