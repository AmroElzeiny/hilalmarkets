"""Number and operator reading, asserted across indicators rather than one case.

The reported defect was `RSI at most 30` compiling as `RSI >= 50`. That was one
symptom of a defect class every level-taking indicator shared: a four-phrase
operator vocabulary, no clause boundary, and a hardcoded fallback level. These cases
assert the *rule* — stated operator honoured, decimals preserved, nothing invented —
against several indicators, so a fix that only helps one of them fails here.
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


async def _compile(prompt: str) -> StrategyDefinition:
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="1h",
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


def _pairs(strategy: StrategyDefinition) -> list[tuple[Comparator, float]]:
    out: list[tuple[Comparator, float]] = []
    for leaf in _leaves(strategy.conditions):
        right = leaf.right
        if right is None or right.kind is not OperandKind.CONSTANT:
            continue
        if isinstance(right.value, int | float) and not isinstance(right.value, bool):
            out.append((leaf.comparator, float(right.value)))
    return out


def _has(strategy: StrategyDefinition, comparator: Comparator, value: float) -> bool:
    return any(c is comparator and abs(v - value) < 1e-9 for c, v in _pairs(strategy))


LTE = Comparator.LESS_THAN_OR_EQUAL
GTE = Comparator.GREATER_THAN_OR_EQUAL
LT = Comparator.LESS_THAN
GT = Comparator.GREATER_THAN

#: (template, low value, high value). One template per indicator family that takes a
#: level, so the operator cases below run against all of them.
INDICATORS = [
    ("watch BTCUSDT on the 1h when RSI {op} {v}", 30.0, 70.0),
    ("watch BTCUSDT on the 1h when volume {op} {v}x average", 0.8, 2.5),
    ("watch BTCUSDT on the 1h with atr percent {op} {v}%", 0.75, 3.25),
    ("bring me coins with price {op} {v}$", 1000.0, 2500.0),
]

#: Decimal levels each indicator can actually take. RSI is bounded to 0-100, so a
#: level outside that range is wording we have not understood and must be refused
#: rather than clamped; the other families are unbounded.
DECIMALS = [
    ("watch BTCUSDT on the 1h when RSI {op} {v}", (0.5, 1.25, 29.5, 99.75)),
    ("watch BTCUSDT on the 1h when volume {op} {v}x average", (0.5, 1.25, 29.5, 999.75)),
    ("watch BTCUSDT on the 1h with atr percent {op} {v}%", (0.5, 1.25, 29.5, 99.75)),
    ("bring me coins with price {op} {v}$", (0.5, 1.25, 29.5, 999.75)),
]


@pytest.mark.parametrize(("template", "low", "high"), INDICATORS)
@pytest.mark.parametrize(
    ("phrase", "comparator", "use_high"),
    [
        ("at most", LTE, False),
        ("no more than", LTE, False),
        ("at least", GTE, True),
        ("no less than", GTE, True),
    ],
)
async def test_inclusive_wording_is_honoured_for_every_indicator(
    template: str, low: float, high: float, phrase: str, comparator: Comparator, use_high: bool
) -> None:
    value = high if use_high else low
    strategy = await _compile(template.format(op=phrase, v=f"{value:g}"))
    assert _has(strategy, comparator, value), _pairs(strategy)


@pytest.mark.parametrize(("template", "low", "high"), INDICATORS)
@pytest.mark.parametrize(
    ("phrase", "comparator", "use_high"),
    [("below", LT, False), ("above", GT, True)],
)
async def test_strict_wording_is_honoured_for_every_indicator(
    template: str, low: float, high: float, phrase: str, comparator: Comparator, use_high: bool
) -> None:
    value = high if use_high else low
    strategy = await _compile(template.format(op=phrase, v=f"{value:g}"))
    assert _has(strategy, comparator, value), _pairs(strategy)


@pytest.mark.parametrize(("template", "values"), DECIMALS)
async def test_decimals_survive_compilation_for_every_indicator(
    template: str, values: tuple[float, ...]
) -> None:
    for value in values:
        strategy = await _compile(template.format(op="at least", v=f"{value:g}"))
        compiled = [v for _c, v in _pairs(strategy)]
        assert any(abs(v - value) < 1e-9 for v in compiled), (value, compiled)


async def test_a_level_outside_an_indicator_s_range_is_refused_not_clamped() -> None:
    """RSI cannot be 999. Clamping it to 100 would invent a rule; refusing it keeps
    the misunderstanding visible."""
    strategy = await _compile("watch BTCUSDT on the 1h when RSI at least 999")
    assert not any(abs(v - 999.0) < 1e-9 for _c, v in _pairs(strategy))
    assert not any(abs(v - 100.0) < 1e-9 for _c, v in _pairs(strategy))


@pytest.mark.parametrize(("template", "low", "_high"), INDICATORS)
async def test_an_inclusive_bound_never_compiles_as_its_opposite(
    template: str, low: float, _high: float
) -> None:
    """`at most 30` becoming `>= 50` was the reported defect. Assert the shape of it:
    a stated upper bound must never compile as a lower bound."""
    strategy = await _compile(template.format(op="at most", v=f"{low:g}"))
    lower_bounds = [(c, v) for c, v in _pairs(strategy) if c in {GT, GTE} and abs(v - low) > 1e-9]
    assert not lower_bounds, lower_bounds


@pytest.mark.parametrize(
    "prompt",
    [
        "watch BTCUSDT on the 1h using RSI",
        "watch BTCUSDT on the 1h using ATR",
        "watch BTCUSDT on the 1h with momentum",
    ],
)
async def test_an_indicator_without_a_level_gets_no_invented_level(prompt: str) -> None:
    strategy = await _compile(prompt)
    invented = [v for _c, v in _pairs(strategy) if v not in (0.0,)]
    assert not invented, invented


async def test_two_indicators_in_one_prompt_keep_their_own_operators() -> None:
    """`above` in the volume clause must not flip the RSI clause, and vice versa."""
    strategy = await _compile(
        "watch BTCUSDT on the 1h when RSI at most 30 and volume above 2x average"
    )
    assert _has(strategy, LTE, 30.0), _pairs(strategy)
    assert _has(strategy, GT, 2.0), _pairs(strategy)


async def test_the_order_of_two_indicators_does_not_change_their_operators() -> None:
    forward = await _compile(
        "watch BTCUSDT on the 1h when RSI at most 30 and volume at least 2x average"
    )
    reversed_order = await _compile(
        "watch BTCUSDT on the 1h when volume at least 2x average and RSI at most 30"
    )
    for strategy in (forward, reversed_order):
        assert _has(strategy, LTE, 30.0), _pairs(strategy)
        assert _has(strategy, GTE, 2.0), _pairs(strategy)


async def test_a_timeframe_is_never_compiled_as_a_level() -> None:
    strategy = await _compile("watch BTCUSDT when RSI at most 30 on the 15m")
    values = [v for _c, v in _pairs(strategy)]
    assert 15.0 not in values, values
    assert _has(strategy, LTE, 30.0), _pairs(strategy)


async def _issue_codes(prompt: str) -> set[str]:
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="1h",
        setup_mode="free_text",
        setup_text=prompt,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )
    preview = await RuleBasedStrategyInterpreter().interpret(request)
    return {issue.code for issue in preview.unsupported_conditions}


def _percentage_bounds(strategy: StrategyDefinition) -> list[tuple[Comparator, float]]:
    out: list[tuple[Comparator, float]] = []
    for leaf in _leaves(strategy.conditions):
        if leaf.left.name != "percentage_change":
            continue
        right = leaf.right
        if (
            right is not None
            and right.kind is OperandKind.CONSTANT
            and isinstance(right.value, int | float)
            and not isinstance(right.value, bool)
        ):
            out.append((leaf.comparator, float(right.value)))
    return out


@pytest.mark.parametrize(
    "prompt",
    [
        "coins up at least 5% today on the 1h",
        "coins up 7.5% today on the 1h",
        "coins that dropped no less than 2.5% today on the 1h",
        "coins that fell at least 7.5% today on the 1h",
    ],
)
async def test_a_minimum_percentage_move_compiles_with_its_exact_value(prompt: str) -> None:
    strategy = await _compile(prompt)
    stated = float(next(part for part in prompt.replace("%", " ").split() if _looks_numeric(part)))
    assert any(
        comparator is GTE and abs(value - stated) < 1e-9
        for comparator, value in _percentage_bounds(strategy)
    ), _percentage_bounds(strategy)
    assert all(
        leaf.left.parameters.get("formula") == "reference_to_current"
        for leaf in _leaves(strategy.conditions)
        if leaf.left.name == "percentage_change"
    )


def _looks_numeric(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize(
    "prompt",
    [
        "coins up no more than 5% today on the 1h",
        "coins that dropped at most 2.5% today on the 1h",
        "coins up at or below 3% today on the 1h",
    ],
)
async def test_a_maximum_percentage_move_uses_an_explicit_upper_bound(prompt: str) -> None:
    strategy = await _compile(prompt)
    stated = float(next(part for part in prompt.replace("%", " ").split() if _looks_numeric(part)))
    assert any(
        comparator is LTE and abs(value - stated) < 1e-9
        for comparator, value in _percentage_bounds(strategy)
    ), _percentage_bounds(strategy)
