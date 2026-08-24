"""One place that decides which market data provider a process uses.

There were eight. ``api/dependencies.py`` had a factory that picked the fixture provider
when the settings asked for one; ``worker.py`` wrote ``CcxtMarketDataProvider(settings)``
by hand in seven places and so could not be put into fixture mode at all — the same
setting meant one thing to the API and nothing to the worker.

That is the shape this codebase keeps producing: two readers of one decision, each
understanding a different part of it. Anything that has to be true of *every* provider in
the product — the fixture switch, the shared cache in
:mod:`ai_market_monitor.services.market_cache` — has to be decided here, once, or the
next thing added will reach seven of the eight callers again.
"""

from __future__ import annotations

from typing import Any

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.market_cache import cache_market_data


def market_data_provider(settings: Settings) -> Any:
    """The provider this process should use, ready to hand to a service.

    Fixture data is never wrapped in the cache: it costs nothing to produce, and a test
    that had to reason about a shared cache would be testing the cache.
    """

    from ai_market_monitor.services.fixture_market_data import FixtureMarketDataProvider
    from ai_market_monitor.services.market_preview import CcxtMarketDataProvider

    if (
        settings.tracedge_market_data_mode == "fixture"
        or settings.tracedge_fixture_market_data_enabled
    ):
        return FixtureMarketDataProvider()
    return cache_market_data(CcxtMarketDataProvider(settings), settings)
