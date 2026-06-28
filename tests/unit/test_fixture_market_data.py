from datetime import UTC, datetime

from ai_market_monitor.services.fixture_market_data import FixtureMarketDataProvider


async def test_fixture_provider_returns_deterministic_ohlcv_and_metadata():
    provider = FixtureMarketDataProvider()

    first = await provider.fetch_ohlcv("binance", "PUMP5/USDT", "15m", 50)
    second = await provider.fetch_ohlcv("binance", "PUMP5/USDT", "15m", 50)
    metadata = await provider.fetch_universe_metadata("binance", ["PUMP5/USDT"])

    assert first == second
    assert len(first) == 50
    assert first[-1].close > first[-2].close * 1.05
    assert metadata["PUMP5/USDT"]["fixture"] is True
    assert metadata["PUMP5/USDT"]["provider"] == "fixture"


async def test_fixture_provider_range_fetch_filters_by_time_window():
    provider = FixtureMarketDataProvider()
    rows = await provider.fetch_ohlcv_range(
        "binance",
        "RSI30/USDT",
        "1h",
        datetime(2026, 6, 20, tzinfo=UTC),
        datetime(2026, 6, 21, tzinfo=UTC),
        100,
    )

    assert rows
    assert all(
        datetime(2026, 6, 20, tzinfo=UTC)
        <= row.timestamp
        <= datetime(2026, 6, 21, tzinfo=UTC)
        for row in rows
    )
    assert rows[-1].close < rows[0].close
