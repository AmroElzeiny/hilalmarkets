"""One stuck task must never stop the whole background worker.

On 5 October 2026 the daily authority import waited for ever on a browser page whose
process had died. The worker runs one child, so alerts, Telegram, scans and the new-coin
reading all queued behind it from 00:30 until 14:36 — 33,381 messages. Three rules close
that class:

* every browser visit has one deadline end to end, and a browser that misses it is
  thrown away;
* every task has a hard time limit below the broker's redelivery window;
* a repeating task that waited longer than its own interval is dropped, so a busy worker
  never comes back to hours of stale copies.

And the CoinMarketCap rule found on the same day: one symbol CoinMarketCap cannot parse
refuses the whole call, so symbol lists are cleaned in one place.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services import sharia_page_render
from ai_market_monitor.services.coinmarketcap import CoinMarketCapClient, cmc_symbols
from ai_market_monitor.services.sharia_page_render import BrowserPageRenderer

# --------------------------------------------------------------------------------
# The browser
# --------------------------------------------------------------------------------

STEPS = ("new_context", "new_page", "goto", "content", "close")


class _Hang:
    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.Event().wait()


class _Page:
    def __init__(self, hang: str) -> None:
        self.hang = hang

    async def goto(self, *args: Any, **kwargs: Any) -> None:
        if self.hang == "goto":
            await _Hang()()

    async def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def content(self) -> str:
        if self.hang == "content":
            await _Hang()()
        return "<html>ok</html>"


class _Context:
    def __init__(self, hang: str) -> None:
        self.hang = hang

    async def new_page(self) -> _Page:
        if self.hang == "new_page":
            await _Hang()()
        return _Page(self.hang)

    async def close(self) -> None:
        if self.hang == "close":
            await _Hang()()


class _Browser:
    def __init__(self, hang: str) -> None:
        self.hang = hang
        self.closed = False

    async def new_context(self, **kwargs: Any) -> _Context:
        if self.hang == "new_context":
            await _Hang()()
        return _Context(self.hang)

    async def close(self) -> None:
        # A browser whose page process died can hang on close as well.
        await _Hang()()


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(sharia_page_render, "_CLOSE_SECONDS", 0.05)
    monkeypatch.setattr(sharia_page_render, "render_engine_available", lambda: True)
    monkeypatch.setattr(
        BrowserPageRenderer, "_page_deadline_seconds", lambda self: 0.3
    )


@pytest.mark.parametrize("step", STEPS)
async def test_a_browser_that_stops_answering_at_any_step_cannot_hold_the_worker(
    quick, step
):
    renderer = BrowserPageRenderer(
        Settings(_env_file=None, sharia_source_browser_render_enabled=True)
    )
    renderer._browser = _Browser(step)
    page = await asyncio.wait_for(renderer.render("https://example.com/blog"), timeout=5)
    if step == "close":
        # The page was read; only closing it hung, and closing has its own deadline.
        assert page.ok is True
    else:
        assert page.ok is False
        assert page.unavailable_reason
        # The dead browser is gone; the next page starts a new one.
        assert renderer._browser is None


def test_the_page_deadline_covers_loading_settling_and_closing():
    settings = Settings(_env_file=None, sharia_source_browser_render_timeout_seconds=25)
    renderer = BrowserPageRenderer(settings)
    # goto (1x) + the network-idle wait (0.5x) + two closes.
    assert renderer._page_deadline_seconds() >= 25 * 1.5


# --------------------------------------------------------------------------------
# The worker
# --------------------------------------------------------------------------------


def test_every_task_has_a_hard_limit_below_the_redelivery_window():
    from ai_market_monitor import worker

    limit = worker.app.conf.task_time_limit
    assert limit is not None
    assert limit < worker.BROKER_REDELIVERY_SECONDS
    # The screening sweep stops starting coins at its budget; it must fit under the limit.
    assert limit > worker.SCREEN_SWEEP_BUDGET_SECONDS


def test_every_repeating_task_expires_after_its_own_interval():
    from ai_market_monitor import worker

    for name, entry in worker.app.conf.beat_schedule.items():
        interval = entry["schedule"]
        seconds = (
            interval.total_seconds() if isinstance(interval, timedelta) else float(interval)
        )
        assert entry.get("options", {}).get("expires") == seconds, name


# --------------------------------------------------------------------------------
# CoinMarketCap symbols
# --------------------------------------------------------------------------------

#: Measured against the live API on 5 October 2026.
REFUSED_BY_CMC = ("BASE_NETWORK", "A.B", "A B", "A/B", "A$B", "A+B", "ÄBC", "A,B")
ACCEPTED_BY_CMC = ("BTC", "1INCH", "A-B", "ZZZZQQ", "btc")


@pytest.mark.parametrize("symbol", REFUSED_BY_CMC)
def test_a_symbol_cmc_cannot_parse_is_never_sent(symbol):
    assert cmc_symbols(["BTC", symbol]) == ["BTC"]


@pytest.mark.parametrize("symbol", ACCEPTED_BY_CMC)
def test_a_symbol_cmc_accepts_is_kept_once_in_upper_case(symbol):
    assert cmc_symbols([symbol, symbol.lower()]) == [symbol.upper()]


def _client(seen: list[str]) -> CoinMarketCapClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("symbol", ""))
        return httpx.Response(200, json={"status": {"error_code": 0}, "data": {}})

    settings = Settings(
        _env_file=None,
        coinmarketcap_enabled=True,
        coinmarketcap_api_key="test-key",
    )
    return CoinMarketCapClient(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.parametrize("method", ["quotes", "coin_links"])
@pytest.mark.parametrize("symbol", REFUSED_BY_CMC)
async def test_every_symbol_call_sends_only_symbols_cmc_accepts(method, symbol):
    seen: list[str] = []
    client = _client(seen)
    if not client.carries("quotes" if method == "quotes" else "metadata"):
        pytest.skip("the default plan does not carry this endpoint")
    await getattr(client, method)(["BTC", symbol, "ETH"])
    assert seen
    for sent in seen:
        assert set(sent.split(",")) <= {"BTC", "ETH"}
