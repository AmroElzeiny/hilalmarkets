"""INV-06: the rule fires on the trigger timeframe, never on a context timeframe.

`contradiction_resolution-001` compiled its +5% rule on `1d` after the trader wrote
"trigger evaluated on 15m only" (`timeframe_inversion_rate: 1.0`). The condition's
timeframe had been inferred from another part of the message, and that inference
silently outranked the role the trader had stated.

A stated role always beats an inferred timeframe. A timeframe the trader also named as
context is left alone, because filtering on one timeframe and firing on another is a
normal multi-timeframe setup.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import ConditionGroup, ConditionRule
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


def _leaves(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionGroup):
        return [leaf for child in node.children for leaf in _leaves(child)]
    return [node]


async def _timeframes(text: str) -> list[str]:
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text=text,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )
    preview = await RuleBasedStrategyInterpreter().interpret(request)
    return [leaf.timeframe for leaf in _leaves(preview.strategy.conditions)]


@pytest.mark.parametrize(
    ("text", "trigger"),
    [
        (
            "watch SOLUSDT, use 5m as context and 15m as the trigger, "
            "bearish move of at least 1% today",
            "15m",
        ),
        (
            "use the 1d for context, the 1h is the trigger, "
            "bullish move of at least 2% today",
            "1h",
        ),
        (
            "4h context, 5m trigger, price rises at least 3% today",
            "5m",
        ),
    ],
)
async def test_rules_fire_on_the_stated_trigger_timeframe(text: str, trigger: str) -> None:
    timeframes = await _timeframes(text)
    assert timeframes, text
    assert all(item == trigger for item in timeframes), timeframes


async def test_a_context_timeframe_is_not_used_as_the_trigger() -> None:
    text = "use 5m as the context and 15m as the trigger, bearish move of at least 1% today"
    assert "5m" not in await _timeframes(text)


async def test_a_setup_without_a_stated_trigger_is_left_alone() -> None:
    """With no role stated there is nothing to enforce, so the normal timeframe
    reading must keep working."""
    timeframes = await _timeframes("watch BTCUSDT on the 4h when RSI is below 30")
    assert timeframes == ["4h"]


async def test_the_roles_survive_being_written_in_reverse_order() -> None:
    """`trigger 15m, context 5m` states the same thing as `context 5m, trigger 15m`."""
    forward = await _timeframes(
        "5m context, 15m trigger, bullish move of at least 2% today"
    )
    reverse = await _timeframes(
        "15m trigger, 5m context, bullish move of at least 2% today"
    )
    assert forward == reverse == ["15m"]
