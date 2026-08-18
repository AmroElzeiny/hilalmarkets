"""One universe size must never be able to blank every quote.

Binance puts the requested symbols in the query string. Asking for a whole USDT spot
universe in one request built an 8.3 KB URL, Binance answered HTTP 414, and the single
`except` around that call left *every* symbol with no ticker at all. Bybit hid the fault
for months because ccxt sends it no symbol list.

These tests assert the rule rather than that one case: whatever the universe size, and
whatever subset of requests the exchange refuses, every symbol the exchange can still
serve keeps its verified values, and a symbol nobody could serve keeps none.
"""

import pytest

from ai_market_monitor.services.market_preview import (
    _TICKER_BATCH_SIZE,
    _TICKER_PROBE_LIMIT,
    CcxtMarketDataProvider,
)

UNIVERSE_SIZES = [1, 2, 19, 20, 21, 99, 100, 101, 250, 490, 1200]


def universe(size: int) -> list[str]:
    """A symbol universe with a stable, unique name per slot."""
    return [f"A{index:04d}/USDT" for index in range(size)]


class FakeExchange:
    """A ccxt-shaped client that refuses over-long symbol queries, as Binance does.

    `url_limit` is the number of symbols one request may name before the exchange
    rejects it, mirroring Binance's URI length ceiling.
    """

    def __init__(
        self,
        symbols: list[str],
        *,
        url_limit: int = _TICKER_BATCH_SIZE,
        unfiltered_works: bool = True,
        single_works: bool = True,
        offline: set[str] | None = None,
    ) -> None:
        self.symbols = list(symbols)
        self.url_limit = url_limit
        self.unfiltered_works = unfiltered_works
        self.single_works = single_works
        self.offline = offline or set()
        self.batch_sizes: list[int] = []
        self.unfiltered_calls = 0
        self.single_calls: list[str] = []

    async def load_markets(self) -> dict[str, dict]:
        return {
            symbol: {"symbol": symbol, "base": symbol.partition("/")[0], "active": True}
            for symbol in self.symbols
        }

    def _ticker(self, symbol: str) -> dict:
        return {"bid": 10.0, "ask": 10.5, "last": 10.25, "quoteVolume": 1_000.0, "percentage": 1.5}

    async def fetch_tickers(self, symbols: list[str] | None = None) -> dict[str, dict]:
        if symbols is None:
            self.unfiltered_calls += 1
            if not self.unfiltered_works:
                raise RuntimeError("unfiltered ticker read refused")
            served = self.symbols
        else:
            self.batch_sizes.append(len(symbols))
            if len(symbols) > self.url_limit:
                # Binance answers 414 with an HTML body; ccxt then fails while parsing it.
                raise AttributeError("'str' object has no attribute 'keys'")
            served = symbols
        return {
            symbol: self._ticker(symbol)
            for symbol in served
            if symbol in self.symbols and symbol not in self.offline
        }

    async def fetch_ticker(self, symbol: str) -> dict:
        self.single_calls.append(symbol)
        if not self.single_works or symbol in self.offline:
            raise RuntimeError("single ticker read refused")
        return self._ticker(symbol)


def provider_for(exchange: FakeExchange) -> CcxtMarketDataProvider:
    provider = CcxtMarketDataProvider(None)
    provider._clients["binance"] = exchange
    return provider


@pytest.mark.parametrize("size", UNIVERSE_SIZES)
async def test_every_universe_size_returns_a_verified_quote_for_every_symbol(size):
    symbols = universe(size)
    exchange = FakeExchange([*symbols, "BTC/USDT"])

    metadata = await provider_for(exchange).fetch_universe_metadata("binance", symbols)

    assert set(metadata) == set(symbols)
    assert all(metadata[symbol]["data_quality_ok"] for symbol in symbols)
    assert all(metadata[symbol]["bid"] == 10.0 for symbol in symbols)


@pytest.mark.parametrize("size", UNIVERSE_SIZES)
async def test_no_single_request_names_more_symbols_than_the_batch_size(size):
    symbols = universe(size)
    exchange = FakeExchange([*symbols, "BTC/USDT"])

    await provider_for(exchange).fetch_universe_metadata("binance", symbols)

    assert exchange.batch_sizes, "the universe must be read through at least one request"
    assert max(exchange.batch_sizes) <= _TICKER_BATCH_SIZE


@pytest.mark.parametrize("url_limit", [1, 20, 50, 99])
async def test_one_refused_batch_never_costs_the_symbols_of_another(url_limit):
    """A tighter ceiling than we batch for still leaves the fallback reads to cover it."""
    symbols = universe(250)
    exchange = FakeExchange([*symbols, "BTC/USDT"], url_limit=url_limit)

    metadata = await provider_for(exchange).fetch_universe_metadata("binance", symbols)

    assert exchange.unfiltered_calls == 1
    assert all(metadata[symbol]["data_quality_ok"] for symbol in symbols)


async def test_symbols_missed_by_batches_are_recovered_without_naming_them():
    symbols = universe(250)
    # Every batched read is refused, so only the unfiltered read can serve the universe.
    exchange = FakeExchange([*symbols, "BTC/USDT"], url_limit=0)

    metadata = await provider_for(exchange).fetch_universe_metadata("binance", symbols)

    assert exchange.unfiltered_calls == 1
    assert exchange.single_calls == []
    assert all(metadata[symbol]["data_quality_ok"] for symbol in symbols)


async def test_a_small_universe_still_falls_back_to_one_request_per_symbol():
    symbols = universe(5)
    exchange = FakeExchange([*symbols, "BTC/USDT"], url_limit=0, unfiltered_works=False)

    metadata = await provider_for(exchange).fetch_universe_metadata("binance", symbols)

    assert set(exchange.single_calls) >= set(symbols)
    assert all(metadata[symbol]["data_quality_ok"] for symbol in symbols)


async def test_the_per_symbol_probe_stays_bounded_on_a_large_outage():
    symbols = universe(490)
    exchange = FakeExchange([*symbols, "BTC/USDT"], url_limit=0, unfiltered_works=False)

    await provider_for(exchange).fetch_universe_metadata("binance", symbols)

    assert len(exchange.single_calls) == _TICKER_PROBE_LIMIT


async def test_a_symbol_no_read_can_serve_carries_no_invented_values():
    symbols = universe(120)
    dark = {symbols[3], symbols[77]}
    exchange = FakeExchange([*symbols, "BTC/USDT"], offline=dark, single_works=False)

    metadata = await provider_for(exchange).fetch_universe_metadata("binance", symbols)

    for symbol in dark:
        assert metadata[symbol]["data_quality_ok"] is False
        assert metadata[symbol]["bid"] is None
        assert metadata[symbol]["ask"] is None
        assert metadata[symbol]["percentage_24h"] is None
    for symbol in set(symbols) - dark:
        assert metadata[symbol]["data_quality_ok"] is True


async def test_a_total_provider_outage_reports_no_data_rather_than_a_price():
    symbols = universe(300)
    exchange = FakeExchange(
        [*symbols, "BTC/USDT"],
        url_limit=0,
        unfiltered_works=False,
        single_works=False,
    )

    metadata = await provider_for(exchange).fetch_universe_metadata("binance", symbols)

    assert set(metadata) == set(symbols)
    assert not any(metadata[symbol]["data_quality_ok"] for symbol in symbols)
    assert not any(metadata[symbol]["bid"] for symbol in symbols)
