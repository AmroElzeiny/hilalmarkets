from tests.factories import candle_sets, load_strategy


async def test_why_no_alert_endpoint_returns_deterministic_reconstruction(test_context):
    strategy = load_strategy()
    sets = candle_sets(volume_multiplier=1.0)
    response = await test_context["client"].post(
        "/api/v1/investigations/why-no-alert",
        json={
            "user_id": "user-1",
            "strategy_id": "strategy-1",
            "strategy_version": "1",
            "strategy_status": "active",
            "symbol": "SOL/USDT",
            "approximate_timestamp": sets["15m"][-1].timestamp.isoformat(),
            "strategy": strategy.model_dump(mode="json"),
            "market": {
                "exchange": "binance",
                "symbol": "SOL/USDT",
                "quote_asset": "USDT",
                "base_asset": "SOL",
                "quote_volume_24h": 5000000,
                "average_candle_volume": 2000,
                "spread_bps": 5,
                "listed_at": "2024-01-01T00:00:00+00:00",
            },
            "candles": {
                timeframe: [
                    {
                        "timestamp": candle.timestamp.isoformat(),
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                        "is_closed": candle.is_closed,
                    }
                    for candle in candles
                ]
                for timeframe, candles in sets.items()
            },
            "subscription_allowed": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluated"] is True
    assert "relative_volume" in body["conditions_failed"]
    assert body["proof"]["conditions"][0]["actual_value"] is not None
