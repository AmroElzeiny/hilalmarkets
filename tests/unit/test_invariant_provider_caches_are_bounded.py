"""Anything a long-lived process keeps in memory must have a limit.

The API holds one `CcxtMarketDataProvider` for the whole life of the process —
`api/dependencies.get_market_data_provider` is wrapped in `@lru_cache`. So every
dictionary on that object is process-lifetime state, and one without a bound is a leak
that only shows up after the server has been running for a while.

That is the shape of the fault that took the site down twice on 22 August 2026: the API
process grew until Docker killed it (`uvicorn`, 694 MB, 17:57). The worker never had this
particular problem because all seven of its call sites close their provider in a
``finally`` block — which is exactly why a rule that only checked the worker would have
proved nothing.

These tests are about the *shape*: a store that grows per symbol must evict. They do not
depend on which symbol, how many, or what the numbers are.
"""

from __future__ import annotations

from ai_market_monitor.services.market_preview import CcxtMarketDataProvider


def test_the_order_book_store_stops_growing() -> None:
    """Feed it far more symbols than the cap and it must stay at the cap."""
    provider = CcxtMarketDataProvider()
    cap = CcxtMarketDataProvider._MAX_ORDER_BOOK_SNAPSHOTS

    for index in range(cap * 2):
        provider._remember_order_book_snapshot(("binance", f"SYM{index}/USDT"), {"n": index})

    assert len(provider._order_book_snapshots) == cap, (
        "the order-book store grew past its cap. In the API this object lives for the "
        "whole life of the process, so an unbounded store is a leak that ends in the "
        "container being killed."
    )


def test_it_evicts_the_oldest_and_keeps_the_newest() -> None:
    """Evicting the *newest* would be worse than not evicting at all.

    Each snapshot exists to be compared against the next reading of the same symbol. A
    store that threw away the most recent one would stay small and answer wrongly for
    ever, which is harder to notice than running out of memory.
    """
    provider = CcxtMarketDataProvider()
    cap = CcxtMarketDataProvider._MAX_ORDER_BOOK_SNAPSHOTS

    for index in range(cap + 10):
        provider._remember_order_book_snapshot(("binance", f"SYM{index}/USDT"), {"n": index})

    kept = provider._order_book_snapshots
    assert ("binance", "SYM0/USDT") not in kept, "the oldest entry should have been evicted"
    assert ("binance", f"SYM{cap + 9}/USDT") in kept, "the newest entry must always survive"
    assert kept[("binance", f"SYM{cap + 9}/USDT")] == {"n": cap + 9}


def test_writing_the_same_symbol_again_replaces_it() -> None:
    """A symbol read a thousand times is one entry, not a thousand."""
    provider = CcxtMarketDataProvider()

    for index in range(1000):
        provider._remember_order_book_snapshot(("binance", "BTC/USDT"), {"n": index})

    assert len(provider._order_book_snapshots) == 1
    assert provider._order_book_snapshots[("binance", "BTC/USDT")] == {"n": 999}


def test_a_rewritten_symbol_is_not_the_next_one_evicted() -> None:
    """Re-writing a symbol must refresh its place in the queue.

    Otherwise the symbol the product looks at most often is the first one thrown away.
    """
    provider = CcxtMarketDataProvider()
    cap = CcxtMarketDataProvider._MAX_ORDER_BOOK_SNAPSHOTS

    for index in range(cap):
        provider._remember_order_book_snapshot(("binance", f"SYM{index}/USDT"), {"n": index})

    # SYM0 is the oldest. Write it again: it must now be the newest, and SYM1 the oldest.
    provider._remember_order_book_snapshot(("binance", "SYM0/USDT"), {"n": "refreshed"})
    provider._remember_order_book_snapshot(("binance", "NEW/USDT"), {"n": "new"})

    kept = provider._order_book_snapshots
    assert len(kept) == cap
    assert ("binance", "SYM0/USDT") in kept, "a symbol written again must not be evicted first"
    assert ("binance", "SYM1/USDT") not in kept, "the genuinely oldest entry should have gone"
