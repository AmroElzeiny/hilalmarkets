"""INV-05: a symbol is never both watched and excluded.

`threshold_mapping-001` compiled `include_symbols = ['ETH/USDT', 'BTC/USDT']` after the
trader had said to exclude BTCUSDT. Exclusion only removed a symbol from the watch list
when it had already been settled by an earlier turn, so an exclusion stated in the
current turn had no effect at all.

Exclusion always wins. Monitoring an asset the trader ruled out is the unsafe
direction, and for a Halal product an excluded asset may be excluded for a reason the
compiler must never override.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


async def _universe(text: str, resolved: dict | None = None):
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text=text,
        trigger_mode="candle_close",
        delivery_channels=["web"],
        resolved_state=resolved or {},
    )
    preview = await RuleBasedStrategyInterpreter().interpret(request)
    return preview.strategy.universe, preview


@pytest.mark.parametrize(
    ("text", "excluded"),
    [
        ("watch ETHUSDT and BTCUSDT on the 15m, exclude BTCUSDT", "BTC/USDT"),
        ("watch ETHUSDT on the 15m but never include BTCUSDT", "BTC/USDT"),
        ("monitor SOLUSDT and XRPUSDT on the 1h, XRPUSDT must be excluded", "XRP/USDT"),
        ("watch ADAUSDT and LTCUSDT on the 5m, omit LTCUSDT", "LTC/USDT"),
    ],
)
async def test_an_excluded_symbol_never_stays_in_the_watch_list(
    text: str, excluded: str
) -> None:
    universe, _ = await _universe(text)
    assert excluded not in universe.include_symbols, universe.include_symbols
    assert excluded in universe.exclude_symbols


async def test_the_lists_are_always_disjoint() -> None:
    universe, _ = await _universe(
        "watch BTCUSDT ETHUSDT SOLUSDT on the 15m, exclude BTCUSDT and SOLUSDT"
    )
    assert not set(universe.include_symbols) & set(universe.exclude_symbols)


async def test_exclusion_settled_in_an_earlier_turn_still_wins() -> None:
    universe, _ = await _universe(
        "also watch BTCUSDT on the 15m",
        {"include_symbols": ["ETH/USDT", "BTC/USDT"], "exclude_symbols": ["BTC/USDT"]},
    )
    assert "BTC/USDT" not in universe.include_symbols
    assert "ETH/USDT" in universe.include_symbols


async def test_a_settled_contradiction_is_reported_not_silently_resolved() -> None:
    """When both sides were settled by the conversation, the trader contradicted
    themselves and must be told — INV-11 forbids silently choosing a side."""
    universe, preview = await _universe(
        "watch the 15m",
        {"include_symbols": ["BTC/USDT"], "exclude_symbols": ["BTC/USDT"]},
    )
    assert "BTC/USDT" not in universe.include_symbols
    codes = {issue.code for issue in preview.unsupported_conditions}
    assert "universe_include_exclude_conflict" in codes


async def test_a_symbol_merely_mentioned_does_not_raise_a_conflict() -> None:
    """"Watch ETH, ignore BTC" names BTC only to exclude it. That is not a
    contradiction and must not block the setup."""
    universe, preview = await _universe("watch ETHUSDT on the 15m, ignore BTCUSDT")
    codes = {issue.code for issue in preview.unsupported_conditions}
    assert "universe_include_exclude_conflict" not in codes
    assert "BTC/USDT" not in universe.include_symbols


async def test_an_ordinary_watch_list_is_untouched() -> None:
    universe, _ = await _universe("watch ETHUSDT and SOLUSDT on the 15m")
    assert set(universe.include_symbols) == {"ETH/USDT", "SOL/USDT"}
    assert universe.exclude_symbols == []
