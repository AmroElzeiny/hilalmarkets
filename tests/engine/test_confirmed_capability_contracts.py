from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.engine.builder_operations import (
    BuilderActionError,
    _probe_values,
    build_condition,
    describe_condition,
    mechanic_catalog,
)
from ai_market_monitor.engine.capabilities import capability_by_key
from ai_market_monitor.engine.capability_compatibility import compatibility_by_key
from ai_market_monitor.engine.condition_registry import condition_registry_payload
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.indicators import IndicatorWarmupError
from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    FormulaKind,
    OperandV2,
    StrategyDraftV2,
)
from ai_market_monitor.services.interfaces import Candle

# The 41 generated A-M price-action cards named by the defect report, plus the
# existing Bollinger re-entry card named in the same lookback audit.
IN_SCOPE_PRICE_ACTION_A_M = frozenset(
    {
        "above_range",
        "all_time_high_breakout",
        "auto_channel_breakdown",
        "auto_channel_breakout",
        "auto_channel_lower_touch",
        "auto_channel_upper_touch",
        "below_range",
        "bollinger_reentry",
        "break_and_retest_confirmed",
        "breakdown_from_consolidation",
        "breakout_from_consolidation",
        "breakout_with_volume_confirmation",
        "breakout_without_volume_confirmation",
        "breaks_n_candle_high",
        "breaks_n_candle_low",
        "close_above_previous_day_high",
        "close_above_previous_week_high",
        "close_below_previous_day_low",
        "close_below_previous_week_low",
        "closes_above_n_candle_high",
        "closes_below_n_candle_low",
        "compression_before_breakout",
        "consecutive_inside_bars",
        "correction_leg_detected",
        "daily_high_swept",
        "daily_low_swept",
        "deep_pullback",
        "displacement_candle_bearish",
        "displacement_candle_bullish",
        "dynamic_trendline",
        "failed_breakdown",
        "failed_breakout",
        "impulse_leg_detected",
        "inside_range",
        "large_body_relative_to_atr",
        "last_down_before_bullish_displacement",
        "last_up_before_bearish_displacement",
        "level_distance_percent",
        "level_strength_score",
        "linear_regression_channel_breakout",
        "linear_regression_channel_touch",
        "multiple_touches_of_level",
    }
)

WARMUP_ONLY_LOOKBACK = frozenset(
    {
        "all_time_high_breakout",
        "bollinger_reentry",
        "close_above_previous_day_high",
        "close_above_previous_week_high",
        "close_below_previous_day_low",
        "close_below_previous_week_low",
        "consecutive_inside_bars",
        "correction_leg_detected",
        "daily_high_swept",
        "daily_low_swept",
        "displacement_candle_bearish",
        "displacement_candle_bullish",
        "impulse_leg_detected",
        "large_body_relative_to_atr",
    }
)

TOLERANCE_CONSUMERS = frozenset(
    {
        "break_and_retest_confirmed",
        "level_strength_score",
        "multiple_touches_of_level",
    }
)

LOOKBACK_EFFECT_SEEDS = {
    "above_range": 42,
    "auto_channel_breakdown": 1,
    "auto_channel_breakout": 6,
    "auto_channel_lower_touch": 1,
    "auto_channel_upper_touch": 1,
    "below_range": 3,
    "break_and_retest_confirmed": 0,
    "breakdown_from_consolidation": 25,
    "breakout_from_consolidation": 40,
    "breakout_with_volume_confirmation": 80,
    "breakout_without_volume_confirmation": 42,
    "breaks_n_candle_high": 42,
    "breaks_n_candle_low": 3,
    "closes_above_n_candle_high": 42,
    "closes_below_n_candle_low": 3,
    "compression_before_breakout": 40,
    "deep_pullback": 9,
    "dynamic_trendline": 0,
    "failed_breakdown": 7,
    "failed_breakout": 31,
    "inside_range": 3,
    "last_down_before_bullish_displacement": 1568,
    "last_up_before_bearish_displacement": 524,
    "level_distance_percent": 1,
    "level_strength_score": 1,
    "linear_regression_channel_breakout": 6,
    "linear_regression_channel_touch": 1,
    "multiple_touches_of_level": 24,
}

PERIOD_REFERENCE_KEYS = frozenset(
    {"previous_daily_high_sweep", "previous_daily_low_sweep", "reference_period_sweep"}
)


def _impulse_history(close_position: float) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        Candle(
            timestamp=start + timedelta(minutes=15 * index),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1000,
            is_closed=True,
        )
        for index in range(20)
    ]
    rows.append(
        Candle(
            timestamp=start + timedelta(minutes=300),
            open=100,
            high=104,
            low=96,
            close=96 + 8 * close_position,
            volume=1000,
            is_closed=True,
        )
    )
    return rows


def _lookback_effect_history(seed: int) -> list[Candle]:
    generator = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    price = 100.0
    rows: list[Candle] = []
    for index in range(45):
        change = generator.uniform(-3, 3)
        open_price = price
        close = max(1, open_price + change)
        spread = generator.uniform(0.2, 3)
        rows.append(
            Candle(
                timestamp=start + timedelta(minutes=15 * index),
                open=open_price,
                high=max(open_price, close) + spread * generator.random(),
                low=max(0.1, min(open_price, close) - spread * generator.random()),
                close=close,
                volume=generator.uniform(200, 3000),
                is_closed=True,
            )
        )
        price = close
    return rows


def _tolerance_effect_history(key: str) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)

    def candle(index: int, high: float, low: float, close: float) -> Candle:
        return Candle(
            timestamp=start + timedelta(minutes=15 * index),
            open=close,
            high=high,
            low=low,
            close=close,
            volume=1000,
            is_closed=True,
        )

    if key == "break_and_retest_confirmed":
        return [
            candle(0, 100, 98, 99),
            candle(1, 100, 98, 99),
            candle(2, 102, 99, 101),
            candle(3, 101, 99, 100),
            candle(4, 101, 99, 100),
            candle(5, 102, 100.5, 101),
        ]
    return [
        candle(0, 100, 99, 99.5),
        candle(1, 99.5, 99, 99.25),
        candle(2, 99.5, 99, 99.25),
        candle(3, 99.5, 99, 99.25),
        candle(4, 99.5, 99, 99.25),
        candle(5, 100, 99, 99.5),
    ]


def test_undefined_daily_and_monthly_high_low_are_not_published_or_selectable() -> None:
    hidden = {"daily_high_low", "monthly_high_low"}
    registry = {item["key"] for item in condition_registry_payload()["items"]}
    compatibility = compatibility_by_key()
    mechanics = {item.capability_key: item for item in mechanic_catalog() if item.capability_key}

    assert hidden.isdisjoint(registry)
    for key in hidden:
        assert compatibility[key].availability != "available"
        assert key not in mechanics or mechanics[key].available is False

    for mechanic in mechanic_catalog():
        if mechanic.available and mechanic.label in {"Daily high/low", "Monthly high/low"}:
            node, _ = build_condition(
                mechanic_key=mechanic.key,
                values=_probe_values(mechanic),
                source_turn_id="wrong-target-regression",
            )
            compiled = compile_strategy_draft_v2(StrategyDraftV2(condition_ast=node))
            assert compiled.conditions.children[0].left.name != "higher_high"


def test_existing_period_reference_capabilities_keep_their_exact_runtime_contracts() -> None:
    specs = capability_by_key()
    published = {item["key"] for item in condition_registry_payload()["items"]}
    assert published >= PERIOD_REFERENCE_KEYS
    assert specs["previous_daily_high_sweep"].operand_name == "daily_high_swept"
    assert specs["previous_daily_low_sweep"].operand_name == "daily_low_swept"
    assert specs["reference_period_sweep"].operand_name == "reference_period_sweep"


def test_impulse_card_compiles_to_the_directional_impulse_reader() -> None:
    mechanic = next(
        item for item in mechanic_catalog() if item.key == "capability:impulse_candle"
    )
    assert mechanic.available
    assert {choice.value for choice in mechanic.directions} == {"up", "down"}
    assert mechanic.parameter("direction") is not None

    for direction, close_position in (("up", 0.875), ("down", 0.125)):
        node, _ = build_condition(
            mechanic_key=mechanic.key,
            values={**_probe_values(mechanic), "direction": direction},
            source_turn_id=f"impulse-{direction}",
        )
        saved = StrategyDraftV2.model_validate_json(
            StrategyDraftV2(condition_ast=node).model_dump_json()
        )
        view = describe_condition(saved.condition_ast)
        assert view.editable
        assert view.values["direction"] == direction
        compiled = compile_strategy_draft_v2(saved)
        operand = compiled.conditions.children[0].left
        assert operand.name == "impulse_candle"
        assert operand.parameters["direction"] == direction
        assert StrategyRuleEngine._price_action(operand, _impulse_history(close_position))


@pytest.mark.parametrize(
    ("direction", "good_position", "bad_position"),
    (("up", 0.875, 0.5), ("down", 0.125, 0.5)),
)
def test_impulse_reader_requires_large_range_and_directional_close_location(
    direction: str,
    good_position: float,
    bad_position: float,
) -> None:
    mechanic = next(
        item for item in mechanic_catalog() if item.key == "capability:impulse_candle"
    )
    node, _ = build_condition(
        mechanic_key=mechanic.key,
        values={**_probe_values(mechanic), "direction": direction},
        source_turn_id="impulse-semantics",
    )
    operand = compile_strategy_draft_v2(
        StrategyDraftV2(condition_ast=node)
    ).conditions.children[0].left

    assert StrategyRuleEngine._price_action(operand, _impulse_history(good_position))
    assert not StrategyRuleEngine._price_action(operand, _impulse_history(bad_position))
    with pytest.raises(IndicatorWarmupError):
        StrategyRuleEngine._price_action(operand, _impulse_history(good_position)[:-1])


def test_a_m_price_action_parameters_are_published_only_when_the_signal_reads_them() -> None:
    items = {item["key"]: item for item in condition_registry_payload()["items"]}
    mechanics = {item.capability_key: item for item in mechanic_catalog() if item.capability_key}
    assert len(IN_SCOPE_PRICE_ACTION_A_M) == 42
    assert set(items) >= IN_SCOPE_PRICE_ACTION_A_M

    for key in IN_SCOPE_PRICE_ACTION_A_M:
        properties = items[key]["parameter_schema"]["properties"]
        expected_lookback = key not in WARMUP_ONLY_LOOKBACK
        expected_tolerance = key in TOLERANCE_CONSUMERS
        assert ("lookback" in properties) is expected_lookback, key
        assert (mechanics[key].parameter("lookback") is not None) is expected_lookback, key
        assert ("tolerance_percent" in properties) is expected_tolerance, key
        assert (
            mechanics[key].parameter("tolerance_percent") is not None
        ) is expected_tolerance, key


@pytest.mark.parametrize("key", sorted(LOOKBACK_EFFECT_SEEDS))
def test_each_published_a_m_lookback_survives_compile_and_changes_the_signal(key: str) -> None:
    assert set(LOOKBACK_EFFECT_SEEDS) == IN_SCOPE_PRICE_ACTION_A_M - WARMUP_ONLY_LOOKBACK
    mechanic = next(item for item in mechanic_catalog() if item.key == f"capability:{key}")
    results: list[bool | float] = []
    for lookback in (5, 20):
        node, _ = build_condition(
            mechanic_key=mechanic.key,
            values={**_probe_values(mechanic), "lookback": lookback},
            source_turn_id=f"lookback-{key}-{lookback}",
        )
        reloaded = StrategyDraftV2.model_validate_json(
            StrategyDraftV2(condition_ast=node).model_dump_json()
        )
        assert reloaded.condition_ast is not None
        view = describe_condition(reloaded.condition_ast)
        assert view.editable
        assert view.values["lookback"] == lookback
        operand = compile_strategy_draft_v2(reloaded).conditions.children[0].left
        assert operand.parameters["lookback"] == lookback
        results.append(
            StrategyRuleEngine._price_action(
                operand,
                _lookback_effect_history(LOOKBACK_EFFECT_SEEDS[key]),
            )
        )
    assert results[0] != results[1], key


@pytest.mark.parametrize("key", sorted(TOLERANCE_CONSUMERS))
def test_each_published_a_m_tolerance_survives_compile_and_changes_the_signal(key: str) -> None:
    mechanic = next(item for item in mechanic_catalog() if item.key == f"capability:{key}")
    results: list[bool | float] = []
    for tolerance in (0.1, 1.0):
        node, _ = build_condition(
            mechanic_key=mechanic.key,
            values={
                **_probe_values(mechanic),
                "lookback": 5,
                "tolerance_percent": tolerance,
            },
            source_turn_id=f"tolerance-{key}-{tolerance}",
        )
        reloaded = StrategyDraftV2.model_validate_json(
            StrategyDraftV2(condition_ast=node).model_dump_json()
        )
        assert reloaded.condition_ast is not None
        view = describe_condition(reloaded.condition_ast)
        assert view.editable
        assert view.values["tolerance_percent"] == tolerance
        operand = compile_strategy_draft_v2(reloaded).conditions.children[0].left
        assert operand.parameters["tolerance_percent"] == tolerance
        results.append(
            StrategyRuleEngine._price_action(operand, _tolerance_effect_history(key))
        )
    assert results[0] != results[1], key


def test_old_accepted_unused_parameters_reload_without_becoming_editable_settings() -> None:
    node = ConditionNodeV2(
        node_id="legacy-all-time-high",
        node_type=ConditionNodeType.CONDITION,
        source_turn_id="legacy",
        source_fragment="Legacy saved rule",
        formula=FormulaKind.CAPABILITY,
        operands=[OperandV2(role="value", kind="market_metric", name="all_time_high_breakout")],
        operator="is_true",
        trigger_timeframe="15m",
        capability_key="all_time_high_breakout",
        capability_version="1.0",
        capability_parameters={"lookback": 7, "tolerance_percent": 0.5},
    )
    reloaded = StrategyDraftV2.model_validate_json(
        StrategyDraftV2(condition_ast=node).model_dump_json()
    )
    assert reloaded.condition_ast is not None
    assert reloaded.condition_ast.capability_parameters == {
        "lookback": 7,
        "tolerance_percent": 0.5,
    }
    assert describe_condition(reloaded.condition_ast).editable is False
    compiled = compile_strategy_draft_v2(reloaded)
    assert compiled.conditions.children[0].left.parameters["lookback"] == 7


def test_pivot_component_is_one_canonical_choice_in_registry_and_builder() -> None:
    choices = ["pivot", "r1", "s1", "r2", "s2"]
    item = next(
        item for item in condition_registry_payload()["items"] if item["key"] == "pivot_points"
    )
    assert item["parameter_schema"]["properties"]["component"]["enum"] == choices
    mechanic = next(item for item in mechanic_catalog() if item.key == "capability:pivot_points")
    parameter = mechanic.parameter("component")
    assert parameter is not None
    assert parameter.kind == "choice"
    assert [choice.value for choice in parameter.choices] == choices
    assert parameter.default == "r1"

    with pytest.raises(BuilderActionError) as raised:
        build_condition(
            mechanic_key=mechanic.key,
            values={**_probe_values(mechanic), "component": "not-a-pivot"},
            source_turn_id="invalid-pivot",
        )
    assert raised.value.code == "VALUE_NOT_OFFERED"


def test_non_default_pivot_survives_draft_reload_compile_and_evaluate() -> None:
    mechanic = next(item for item in mechanic_catalog() if item.key == "capability:pivot_points")
    node, _ = build_condition(
        mechanic_key=mechanic.key,
        values={**_probe_values(mechanic), "component": "s2"},
        source_turn_id="pivot-s2",
    )
    reloaded = StrategyDraftV2.model_validate_json(
        StrategyDraftV2(condition_ast=node).model_dump_json()
    )
    assert reloaded.condition_ast is not None
    view = describe_condition(reloaded.condition_ast)
    assert view.editable
    assert view.values["component"] == "s2"
    operand = compile_strategy_draft_v2(reloaded).conditions.children[0].left
    assert operand.parameters["component"] == "s2"
    candles = _impulse_history(0.5)
    assert StrategyRuleEngine().indicators.calculate(
        operand.name or "", candles, **operand.parameters
    ) == pytest.approx(98.0)


@pytest.mark.parametrize(
    ("component", "expected"),
    (("pivot", 100.0), ("r1", 101.0), ("s1", 99.0), ("r2", 102.0), ("s2", 98.0)),
)
def test_every_published_pivot_component_has_the_classic_deterministic_value(
    component: str,
    expected: float,
) -> None:
    candles = _impulse_history(0.5)
    assert StrategyRuleEngine().indicators.calculate(
        "pivot_points", candles, lookback=1, component=component
    ) == pytest.approx(expected)


def test_formed_head_and_shoulders_cards_do_not_publish_confirmation_buffer() -> None:
    items = {item["key"]: item for item in condition_registry_payload()["items"]}
    mechanics = {item.capability_key: item for item in mechanic_catalog() if item.capability_key}
    formed = {"head_and_shoulders_formed", "inverse_head_and_shoulders_formed"}
    confirmed = {
        "head_and_shoulders_neckline_break",
        "inverse_head_and_shoulders_neckline_break",
    }
    for key in formed:
        assert "breakout_buffer_percent" not in items[key]["default_parameters"]
        assert "breakout_buffer_percent" not in items[key]["parameter_schema"]["properties"]
        assert mechanics[key].parameter("breakout_buffer_percent") is None
    for key in confirmed:
        assert items[key]["default_parameters"]["breakout_buffer_percent"] == 0.0
        assert "breakout_buffer_percent" in items[key]["parameter_schema"]["properties"]
        assert mechanics[key].parameter("breakout_buffer_percent") is not None


def test_available_builder_capabilities_and_effective_registry_have_the_same_keys() -> None:
    registry = {item["key"] for item in condition_registry_payload()["items"]}
    builder = {
        item.capability_key
        for item in mechanic_catalog()
        if item.capability_key is not None and item.available
    }
    scope = IN_SCOPE_PRICE_ACTION_A_M | PERIOD_REFERENCE_KEYS | {
        "daily_high_low",
        "monthly_high_low",
        "impulse_candle",
        "pivot_points",
        "head_and_shoulders_formed",
        "head_and_shoulders_neckline_break",
        "inverse_head_and_shoulders_formed",
        "inverse_head_and_shoulders_neckline_break",
    }
    assert not builder - registry
    assert registry & scope == builder & scope
