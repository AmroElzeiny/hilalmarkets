"""A page must not ask for prices faster than prices can change.

The Market tab refreshes itself on a timer the server sets. Those were two unrelated
numbers: the server sent `refresh_after_ms = 1000`, the browser applied its own floor of
2000, and the snapshot behind them was rebuilt every 0.75 seconds. So one open Market tab
asked the API thirty times a minute, and almost every ask did a full round trip to the
exchange for every symbol — while also, until the same day's other fix, reading every
stored Shariah review.

That is the classic shape of a defect in this codebase: a number written down twice, in
two files, with nothing keeping them equal. The refresh hint is now derived from the cache
that decides when there can be anything new, so there is one number and it cannot drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.services.live_market_quotes import LiveMarketQuoteService

ROOT = Path(__file__).resolve().parents[2]
MARKET_SCRIPTS = (
    ROOT / "src" / "ai_market_monitor" / "static" / "hm-market-test.js",
    ROOT / "src" / "ai_market_monitor" / "static" / "sharia-market.js",
)


def service(**overrides: object) -> LiveMarketQuoteService:
    settings: Settings = get_settings().model_copy(update=overrides)
    return LiveMarketQuoteService(provider=object(), settings=settings)  # type: ignore[arg-type]


@pytest.mark.parametrize("seconds", [0.5, 0.75, 1.0, 2.5, 5.0, 10.0])
def test_the_refresh_hint_follows_the_cache(seconds: float) -> None:
    """Whatever the cache is set to, the page is told the same thing.

    Parametrised across the whole allowed range rather than the one value in use, so the
    two can never be separated by changing the setting.
    """
    told = service(sharia_live_quote_cache_seconds=seconds).refresh_after_ms()
    assert told >= seconds * 1000, (
        f"the cache holds a snapshot for {seconds}s but the page is told to come back "
        f"after {told}ms. Every ask inside the cache window is answered with bytes the "
        "page already has, at the cost of a full request."
    )


def test_the_page_is_never_told_to_hammer() -> None:
    """A floor, so a mistaken tiny cache setting cannot turn into a request storm."""
    assert service(sharia_live_quote_cache_seconds=0.5).refresh_after_ms() >= 1000


def test_the_configured_cache_is_long_enough_to_be_a_cache() -> None:
    """0.75 seconds against a browser floor of 2 seconds meant it never served anybody.

    This is a monitoring product for beginners, not a trading terminal. There is no order
    entry and no leverage; the page shows the time its prices were taken. Refreshing a few
    seconds apart is honest, and it is the difference between one open tab costing thirty
    requests a minute and costing twelve.
    """
    seconds = get_settings().sharia_live_quote_cache_seconds
    assert seconds >= 2.0, (
        f"SHARIA_LIVE_QUOTE_CACHE_SECONDS is {seconds}. The Market page cannot ask more "
        "often than every 2 seconds, so anything below that is a cache that is always "
        "expired: every single request goes to the exchange for every symbol."
    )


@pytest.mark.parametrize("path", MARKET_SCRIPTS, ids=lambda p: p.name)
def test_the_page_obeys_the_server_rather_than_its_own_number(path: Path) -> None:
    """The browser may set a floor, but the interval itself comes from the answer.

    A hard-coded interval in the page would be a third copy of the number, and the one the
    server could not change without a rebuild of the front end.
    """
    text = path.read_text(encoding="utf-8")
    assert "refresh_after_ms" in text, (
        f"{path.name} no longer reads refresh_after_ms from the response, so the server "
        "can no longer slow the page down when it needs to."
    )
