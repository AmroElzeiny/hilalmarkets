"""Operator, threshold and grouping fidelity through the real compiler.

Evaluator run 20260725T122105Z left `operator_mapping`, `threshold_mapping` and
`nested_boolean_logic` unmeasured because almost no case produced a structured
object at all. These cases compile the wording directly and assert the mapping,
without naming any scenario from the run.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import (
    Comparator,
    ConditionGroup,
    ConditionRule,
    OperandKind,
    StrategyDefinition,
)
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


async def _compile(prompt: str, timeframe: str = "1h") -> StrategyDefinition:
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe=timeframe,
        setup_mode="free_text",
        setup_text=prompt,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )
    preview = await RuleBasedStrategyInterpreter().interpret(request)
    return preview.strategy


def _leaves(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionGroup):
        return [leaf for child in node.children for leaf in _leaves(child)]
    return [node]


def _groups(node: ConditionRule | ConditionGroup) -> list[ConditionGroup]:
    if not isinstance(node, ConditionGroup):
        return []
    return [node, *(g for child in node.children for g in _groups(child))]


def _thresholds(strategy: StrategyDefinition) -> list[float]:
    values: list[float] = []
    for leaf in _leaves(strategy.conditions):
        right = leaf.right
        if right is None or right.kind is not OperandKind.CONSTANT:
            continue
        if isinstance(right.value, int | float) and not isinstance(right.value, bool):
            values.append(float(right.value))
    return values


@pytest.mark.parametrize(
    ("wording", "comparator"),
    [
        ("RSI below 30", Comparator.LESS_THAN),
        ("RSI above 70", Comparator.GREATER_THAN),
        ("RSI at least 70", Comparator.GREATER_THAN_OR_EQUAL),
        ("RSI at most 30", Comparator.LESS_THAN_OR_EQUAL),
        ("RSI no more than 30", Comparator.LESS_THAN_OR_EQUAL),
        ("RSI no less than 70", Comparator.GREATER_THAN_OR_EQUAL),
        ("RSI greater than 70", Comparator.GREATER_THAN),
        ("RSI less than 30", Comparator.LESS_THAN),
    ],
)
async def test_operator_wording_compiles_to_the_exact_comparator(
    wording: str, comparator: Comparator
) -> None:
    strategy = await _compile(f"watch BTCUSDT on the 1h when {wording}")
    comparators = {leaf.comparator for leaf in _leaves(strategy.conditions)}
    assert comparator in comparators, comparators


@pytest.mark.parametrize(
    ("wording", "comparator"),
    [
        ("price crosses above the 20 EMA", Comparator.CROSSES_ABOVE),
        ("price crosses below the 20 EMA", Comparator.CROSSES_BELOW),
    ],
)
async def test_crossing_wording_never_degrades_to_a_plain_comparison(
    wording: str, comparator: Comparator
) -> None:
    """`crosses above` is a transition, not `above`. Collapsing the two changes when
    the alert fires."""
    strategy = await _compile(f"watch BTCUSDT on the 1h when {wording}")
    comparators = {leaf.comparator for leaf in _leaves(strategy.conditions)}
    assert comparator in comparators, comparators


@pytest.mark.parametrize(
    ("wording", "value"),
    [
        ("RSI below 30", 30.0),
        ("RSI below 29.5", 29.5),
        ("RSI below 0.5", 0.5),
        ("RSI above 70", 70.0),
    ],
)
async def test_decimal_precision_is_preserved_exactly(wording: str, value: float) -> None:
    strategy = await _compile(f"watch BTCUSDT on the 1h when {wording}")
    assert any(item == pytest.approx(value) for item in _thresholds(strategy)), _thresholds(
        strategy
    )


async def test_a_threshold_is_never_rounded_to_a_whole_number() -> None:
    strategy = await _compile("watch BTCUSDT on the 1h when RSI below 29.5")
    assert 29.0 not in _thresholds(strategy)
    assert 30.0 not in _thresholds(strategy)


async def test_a_timeframe_number_is_not_read_as_a_threshold() -> None:
    """`15m` carries a 15; it is a timeframe, not a level."""
    strategy = await _compile("watch BTCUSDT on the 15m when RSI below 30", timeframe="15m")
    assert strategy.base_timeframe == "15m"
    assert 15.0 not in _thresholds(strategy)


async def test_the_condition_tree_is_always_a_group_with_a_declared_operator() -> None:
    strategy = await _compile("watch BTCUSDT on the 1h when RSI below 30")
    assert isinstance(strategy.conditions, ConditionGroup)
    assert strategy.conditions.operator is not None
    assert strategy.conditions.children


async def test_every_group_declares_an_operator_and_has_children() -> None:
    strategy = await _compile(
        "watch BTCUSDT on the 1h when RSI below 30 and volume above 2x average "
        "and price crosses above the 20 EMA"
    )
    groups = _groups(strategy.conditions)
    assert groups
    for group in groups:
        assert group.operator is not None
        assert group.children, group.key


async def test_multiple_requirements_all_survive_compilation() -> None:
    """Dropping one clause silently changes what the monitor fires on."""
    strategy = await _compile(
        "watch BTCUSDT on the 1h when RSI below 30 and volume above 2x average"
    )
    assert len(_leaves(strategy.conditions)) >= 2


async def test_condition_keys_are_unique_within_the_tree() -> None:
    """Duplicate keys make a node unaddressable on the Canvas and in the contract."""
    strategy = await _compile(
        "watch BTCUSDT on the 1h when RSI below 30 and volume above 2x average "
        "and price crosses above the 20 EMA and RSI above 20"
    )
    keys = [leaf.key for leaf in _leaves(strategy.conditions)]
    assert len(keys) == len(set(keys)), keys
    group_keys = [group.key for group in _groups(strategy.conditions)]
    assert len(group_keys) == len(set(group_keys)), group_keys


async def test_every_leaf_carries_the_wording_it_came_from() -> None:
    """A rule with no source fragment cannot be traced back or explained."""
    strategy = await _compile(
        "watch BTCUSDT on the 1h when RSI below 30 and volume above 2x average"
    )
    leaves = _leaves(strategy.conditions)
    assert leaves
    assert all(leaf.source_fragment for leaf in leaves), [leaf.key for leaf in leaves]


async def test_an_indicator_named_without_a_level_is_not_given_one() -> None:
    """The old default turned a bare `RSI` mention into `RSI >= 50`, and
    `RSI at most 30` into its opposite. Inventing a level invents a rule."""
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="1h",
        setup_mode="free_text",
        setup_text="watch BTCUSDT on the 1h using RSI",
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )
    preview = await RuleBasedStrategyInterpreter().interpret(request)
    thresholds = _thresholds(preview.strategy)
    assert 50.0 not in thresholds
    codes = {issue.code for issue in preview.unsupported_conditions}
    assert "rsi_level_required" in codes or "no_supported_monitor_condition" in codes


async def test_an_inclusive_bound_and_a_strict_bound_stay_distinct() -> None:
    """At exactly the level, `below 30` must not fire while `at most 30` does."""
    strict = await _compile("watch BTCUSDT on the 1h when RSI below 30")
    inclusive = await _compile("watch BTCUSDT on the 1h when RSI at most 30")
    strict_comparators = {leaf.comparator for leaf in _leaves(strict.conditions)}
    inclusive_comparators = {leaf.comparator for leaf in _leaves(inclusive.conditions)}
    assert Comparator.LESS_THAN in strict_comparators
    assert Comparator.LESS_THAN_OR_EQUAL in inclusive_comparators
    assert strict_comparators != inclusive_comparators


async def test_an_indicator_period_is_not_read_as_the_level() -> None:
    strategy = await _compile("watch BTCUSDT on the 1h when RSI(14) below 30")
    assert 14.0 not in _thresholds(strategy)
    assert any(item == pytest.approx(30.0) for item in _thresholds(strategy))


async def test_the_operator_is_read_from_the_indicator_s_own_clause() -> None:
    """`above` in a later clause must not flip an earlier `below`."""
    strategy = await _compile(
        "watch BTCUSDT on the 1h when RSI below 30 and volume above 2x average"
    )
    rsi = [leaf for leaf in _leaves(strategy.conditions) if "rsi" in leaf.key]
    assert rsi
    assert all(leaf.comparator is Comparator.LESS_THAN for leaf in rsi)


async def test_every_leaf_names_a_supported_timeframe() -> None:
    strategy = await _compile("watch BTCUSDT with a 1h trigger and 4h context when RSI below 30")
    timeframes = {leaf.timeframe for leaf in _leaves(strategy.conditions)}
    allowed = {strategy.base_timeframe, *strategy.supporting_timeframes}
    assert timeframes <= allowed, (timeframes, allowed)
