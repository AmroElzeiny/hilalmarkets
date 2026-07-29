"""The typed state must reach the compiler, not just the session row.

Run 20260725T122105Z exercised `repeated_correction_cycles` and `revert_correction`.
The accumulated setup text keeps every superseded statement, so a compiler that
re-parses that text lets the corrected value lose to the wording it replaced. These
cases compile through the real interpreter with a resolved state attached.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.strategy_state import (
    StrategyDraftState,
    patches_for_turn,
    revert_patches,
)
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


def _state(*turns: str) -> StrategyDraftState:
    state = StrategyDraftState()
    for text in turns:
        state = state.apply(patches_for_turn(text, state))
    return state


def _resolved(state: StrategyDraftState) -> dict:
    out: dict = {}
    for name, value in state.resolved().items():
        if hasattr(value, "value"):
            out[name] = value.value
        elif isinstance(value, tuple):
            out[name] = list(value)
        else:
            out[name] = value
    return out


async def _compile(*turns: str, revert: bool = False) -> StrategyDefinition:
    state = _state(*turns)
    if revert:
        state = state.apply(revert_patches(state))
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        # The guided default is deliberately wrong so only the resolved state can
        # produce the expected timeframe.
        timeframe="30m",
        setup_mode="free_text",
        setup_text="\n".join(turns),
        trigger_mode="candle_close",
        delivery_channels=["web"],
        resolved_state=_resolved(state),
    )
    preview = await RuleBasedStrategyInterpreter().interpret(request)
    return preview.strategy


async def test_the_latest_timeframe_correction_is_what_compiles() -> None:
    strategy = await _compile(
        "watch BTCUSDT on the 15m when RSI drops below 30",
        "actually make it the 1h",
    )
    assert strategy.base_timeframe == "1h"


async def test_a_third_correction_still_wins() -> None:
    strategy = await _compile(
        "watch BTCUSDT on the 15m when RSI drops below 30",
        "make it the 1h",
        "no, the 4h",
    )
    assert strategy.base_timeframe == "4h"


async def test_a_reversion_compiles_the_exact_previous_value() -> None:
    strategy = await _compile(
        "watch BTCUSDT on the 15m when RSI drops below 30",
        "make it the 1h",
        revert=True,
    )
    assert strategy.base_timeframe == "15m"


async def test_the_latest_direction_correction_is_what_compiles() -> None:
    strategy = await _compile(
        "long BTCUSDT on the 1h when RSI drops below 30",
        "actually make it short",
    )
    assert strategy.direction.value == "short"


async def test_typed_formula_compiles_with_the_latest_bound() -> None:
    strategy = await _compile(
        (
            "short BTCUSDT on a 15m trigger where percent_change = "
            "(close_now - close_prev) / close_prev * 100 and operator lte 0.5%"
        ),
        "change only the bound to operator gte 1%",
    )
    conditions = [
        child
        for child in strategy.conditions.children
        if getattr(child, "node_type", "") == "condition"
    ]
    percentage = next(
        condition for condition in conditions if condition.left.name == "percentage_change"
    )
    assert percentage.left.parameters["formula"] == "close_to_close"
    assert percentage.left.parameters["reference_field"] == "close"
    assert percentage.left.parameters["current_field"] == "close"
    assert percentage.comparator.value == "gte"
    assert percentage.right is not None
    assert percentage.right.value == pytest.approx(1)


async def test_an_exclusion_survives_many_later_turns() -> None:
    strategy = await _compile(
        "scan all coins but exclude BTCUSDT on the 1h",
        "make it short",
        "RSI below 30",
        "add a volume filter above 2x average",
    )
    assert "BTC/USDT" in strategy.universe.exclude_symbols


async def test_an_excluded_symbol_never_stays_in_the_universe() -> None:
    strategy = await _compile(
        "watch BTCUSDT and ETHUSDT on the 1h when RSI drops below 30",
        "drop ETHUSDT",
    )
    assert "ETH/USDT" not in strategy.universe.include_symbols
    assert "ETH/USDT" in strategy.universe.exclude_symbols
    assert set(strategy.universe.include_symbols).isdisjoint(strategy.universe.exclude_symbols)


async def test_narrowing_the_universe_replaces_it() -> None:
    strategy = await _compile(
        "watch BTCUSDT and ETHUSDT on the 1h when RSI drops below 30",
        "only SOLUSDT now",
    )
    assert strategy.universe.include_symbols == ["SOL/USDT"]


async def test_added_symbols_widen_the_universe() -> None:
    strategy = await _compile(
        "watch BTCUSDT on the 1h when RSI drops below 30",
        "also add SOLUSDT",
    )
    assert set(strategy.universe.include_symbols) == {"BTC/USDT", "SOL/USDT"}


async def test_a_context_timeframe_settled_earlier_is_still_supporting() -> None:
    strategy = await _compile(
        "use 4h context and a 1h trigger for BTCUSDT",
        "RSI below 30",
    )
    assert strategy.base_timeframe == "1h"
    assert "4h" in strategy.supporting_timeframes


async def test_an_unsupported_settled_timeframe_is_ignored_not_forced() -> None:
    """A value the schema cannot represent must never reach the definition."""
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text="watch BTCUSDT when RSI drops below 30",
        trigger_mode="candle_close",
        delivery_channels=["web"],
        resolved_state={"base_timeframe": "7m", "include_symbols": ["BTCUSDT"]},
    )
    preview = await RuleBasedStrategyInterpreter().interpret(request)
    assert preview.strategy.base_timeframe == "15m"
    assert preview.strategy.universe.include_symbols == ["BTC/USDT"]


async def test_a_junk_resolved_state_cannot_corrupt_the_draft() -> None:
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text="watch BTCUSDT on the 15m when RSI drops below 30",
        trigger_mode="candle_close",
        delivery_channels=["web"],
        resolved_state={
            "base_timeframe": 42,
            "direction": "sideways",
            "include_symbols": ["", None, 7],
            "exclude_symbols": "ETHUSDT",
        },
    )
    preview = await RuleBasedStrategyInterpreter().interpret(request)
    strategy = preview.strategy
    assert strategy.base_timeframe == "15m"
    assert strategy.direction.value in {"long", "short", "both"}
    assert all(isinstance(item, str) and item for item in strategy.universe.include_symbols)


async def test_no_resolved_state_leaves_behaviour_unchanged() -> None:
    """Single-turn compiles must be unaffected by the new field."""
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text="watch BTCUSDT only on the 1h when RSI drops below 30",
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )
    preview = await RuleBasedStrategyInterpreter().interpret(request)
    assert preview.strategy.base_timeframe == "1h"
    assert "BTC/USDT" in preview.strategy.universe.include_symbols


@pytest.mark.parametrize(
    ("turns", "expected"),
    [
        (("BTCUSDT on the 15m, RSI below 30", "switch to the 1h"), "1h"),
        (("BTCUSDT on the 15m, RSI below 30", "change it to the 4h"), "4h"),
        (("BTCUSDT on the 1d, RSI below 30", "use the 12h instead"), "12h"),
    ],
)
async def test_correction_wording_variants_all_take_effect(
    turns: tuple[str, ...], expected: str
) -> None:
    strategy = await _compile(*turns)
    assert strategy.base_timeframe == expected
