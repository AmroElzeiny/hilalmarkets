from ai_market_monitor.api.dependencies import get_market_data_provider


class ApiQuoteProvider:
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        assert exchange in {"binance", "bybit"}
        assert quote_currencies == ["USDT"]
        return ["BTC/USDT", "SOL/USDT"]

    async def fetch_universe_metadata(
        self,
        exchange: str,
        symbols: list[str],
        *,
        include_listing_dates: bool = False,
    ) -> dict[str, dict[str, object]]:
        del exchange, include_listing_dates
        return {
            symbol: {
                "bid": 10.0,
                "ask": 10.1,
                "last": 10.05,
                "quote_volume_24h": 100_000.0,
                "data_quality_ok": True,
            }
            for symbol in symbols
        }


async def _signup(test_context, email: str) -> None:
    response = await test_context["client"].post(
        "/signup",
        data={
            "email": email,
            "display_name": "Live market user",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def test_test_market_api_and_dashboard_are_gated_and_provider_backed(test_context):
    await _signup(test_context, "live-test-market@example.com")

    disabled = await test_context["client"].get("/api/v1/sharia/test-market")
    assert disabled.status_code == 404

    test_context["settings"].sharia_test_market_enabled = True
    test_context["app"].dependency_overrides[get_market_data_provider] = ApiQuoteProvider

    response = await test_context["client"].get(
        "/api/v1/sharia/test-market?exchange=bybit&quote_asset=USDT"
    )
    page = await test_context["client"].get("/dashboard/market?exchange=bybit")

    assert response.status_code == 200
    payload = response.json()
    assert payload["methodology"]["name"] == "Test"
    assert payload["methodology"]["development_only"] is True
    assert payload["total"] == 2
    assert all(item["status_label"] == "Halal (test)" for item in payload["items"])
    assert all(item["bid"] == 10.0 and item["ask"] == 10.1 for item in payload["items"])
    assert page.status_code == 200
    assert 'data-endpoint="/api/v1/sharia/test-market"' in page.text
    assert "Test methodology only" in page.text
    assert "data-market-passport-dialog" in page.text
    assert "sharia-market.js" in page.text


async def test_watchlists_saved_assets_and_market_scanner_are_distinct_routes(test_context):
    await _signup(test_context, "distinct-watch-routes@example.com")

    watchlists = await test_context["client"].get("/dashboard/watchlists")
    saved_assets = await test_context["client"].get("/dashboard/saved-assets")
    scanner = await test_context["client"].get(
        "/dashboard/check-market",
        follow_redirects=False,
    )

    assert watchlists.status_code == 200
    assert "Your monitoring systems" in watchlists.text
    assert saved_assets.status_code == 200
    assert "Your approved assets, kept together" in saved_assets.text
    assert scanner.status_code == 303
    assert scanner.headers["location"] == "/dashboard/strategies/new?mode=scanner"
