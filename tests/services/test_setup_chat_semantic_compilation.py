"""Semantic compilation regressions from evaluator run 20260725T122105Z.

That run captured exactly one structured strategy object, and it was wrong in four
independent ways for a requested "SOLUSDT only, exclude BTCUSDT, 4h context / 1h
trigger, bearish move >= 7.5%, short" setup:

* ``include_symbols`` was ``[]`` — the requested symbol vanished
* ``exclude_symbols`` was ``["BTCUSDT/USDT"]`` — a quote appended to a full symbol
* ``base_timeframe`` was ``4h`` with ``supporting_timeframes: []`` — context and
  trigger were inverted and the trigger was lost
* the conditions were generic 4h ``range_breakdown`` nodes sourced from the
  fragment ``"We'll use **4h context**"``

None of the assertions below name that scenario; they assert the general mapping.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import (
    ConditionGroup,
    ConditionRule,
    StrategyDefinition,
)
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


def _request(prompt: str, timeframe: str = "15m") -> GuidedSetupRequest:
    return GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe=timeframe,
        setup_mode="free_text",
        setup_text=prompt,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )


async def _compile(prompt: str, timeframe: str = "15m") -> StrategyDefinition:
    preview = await RuleBasedStrategyInterpreter().interpret(_request(prompt, timeframe))
    return preview.strategy


def _leaves(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionGroup):
        return [leaf for child in node.children for leaf in _leaves(child)]
    return [node]


LONG_CONTEXT_PROMPT = (
    "watchlist for SOLUSDT only, explicitly exclude BTCUSDT. "
    "We'll use 4h context and a 1h trigger with a short bias: "
    "bearish move of at least 7.5%"
)


async def test_requested_symbol_is_included_not_dropped() -> None:
    strategy = await _compile(LONG_CONTEXT_PROMPT)
    assert "SOL/USDT" in strategy.universe.include_symbols


async def test_excluded_symbol_is_excluded_and_not_corrupted() -> None:
    strategy = await _compile(LONG_CONTEXT_PROMPT)
    assert "BTC/USDT" in strategy.universe.exclude_symbols
    assert "BTCUSDT/USDT" not in strategy.universe.exclude_symbols


@pytest.mark.parametrize(
    "symbol",
    ["BTC/USDT", "SOL/USDT", "ADA/USDT", "XRP/USDT", "ETH/USDT"],
)
async def test_no_symbol_carries_a_doubled_quote(symbol: str) -> None:
    base = symbol.split("/")[0]
    strategy = await _compile(f"watch {base}USDT only")
    for value in strategy.universe.include_symbols + strategy.universe.exclude_symbols:
        assert value.count("/") == 1, value
        assert not value.endswith("USDT/USDT"), value


async def test_include_and_exclude_never_overlap() -> None:
    strategy = await _compile("BTCUSDT only, exclude ETHUSDT")
    include = set(strategy.universe.include_symbols)
    exclude = set(strategy.universe.exclude_symbols)
    assert include.isdisjoint(exclude)
    assert "BTC/USDT" in include
    assert "ETH/USDT" in exclude


async def test_trigger_timeframe_becomes_the_base_timeframe() -> None:
    strategy = await _compile(LONG_CONTEXT_PROMPT)
    assert strategy.base_timeframe == "1h"


async def test_context_timeframe_is_retained_as_supporting() -> None:
    strategy = await _compile(LONG_CONTEXT_PROMPT)
    assert "4h" in strategy.supporting_timeframes
    assert strategy.base_timeframe not in strategy.supporting_timeframes


async def test_context_and_trigger_are_not_swapped_in_either_order() -> None:
    forward = await _compile("watch BTCUSDT with 15m trigger and 1m context")
    reversed_order = await _compile("watch BTCUSDT with 1m context and 15m trigger")
    assert forward.base_timeframe == "15m"
    assert reversed_order.base_timeframe == "15m"
    assert "1m" in forward.supporting_timeframes
    assert "1m" in reversed_order.supporting_timeframes


async def test_direction_is_preserved() -> None:
    strategy = await _compile(LONG_CONTEXT_PROMPT)
    assert strategy.direction.value == "short"


async def test_no_generic_capability_is_substituted_for_unrepresentable_meaning() -> None:
    """Fail closed. A timeframe phrase must never become a price-action mechanic.

    The bogus node in the run carried ``source_fragment`` "We'll use **4h context**":
    a timeframe declaration fuzzy-matched into ``range_breakdown``.
    """
    strategy = await _compile(LONG_CONTEXT_PROMPT)
    keys = [leaf.capability_key for leaf in _leaves(strategy.conditions)]
    assert "range_breakdown" not in keys


async def test_a_timeframe_declaration_alone_compiles_no_condition() -> None:
    strategy = await _compile("use 4h context and a 1h trigger")
    for leaf in _leaves(strategy.conditions):
        assert leaf.capability_key != "range_breakdown"
        if leaf.capability_key is not None:
            assert "context" not in (leaf.source_fragment or "").casefold()


async def test_unrepresentable_meaning_is_flagged_rather_than_guessed() -> None:
    strategy = await _compile(LONG_CONTEXT_PROMPT)
    leaves = _leaves(strategy.conditions)
    assert leaves, "a draft must still be produced"
    unresolved = [leaf for leaf in leaves if leaf.capability_key is None]
    assert unresolved, "meaning that cannot be represented must surface, not vanish"
