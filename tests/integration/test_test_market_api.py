from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ai_market_monitor.api.dependencies import get_market_data_provider
from ai_market_monitor.db.models import ShariaMethodology, User
from ai_market_monitor.db.models.enums import ShariaAssetStatus, ShariaMethodologyStatus
from ai_market_monitor.schemas.sharia import (
    AssessmentCreateRequest,
    EvidenceSourceInput,
    MethodologyCreateRequest,
)
from ai_market_monitor.services.sharia_screening import ShariaScreeningService
from tests.factories import methodology_evidence_requirements, methodology_rules


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


async def test_retired_test_market_route_stays_unavailable(test_context):
    await _signup(test_context, "live-test-market@example.com")

    test_context["app"].dependency_overrides[get_market_data_provider] = ApiQuoteProvider
    response = await test_context["client"].get(
        "/api/v1/sharia/test-market?exchange=bybit&quote_asset=USDT"
    )
    page = await test_context["client"].get("/dashboard/market?exchange=bybit")

    assert response.status_code == 404
    assert page.status_code == 200
    assert "/api/v1/sharia/test-market" not in page.text
    assert "Test methodology only" not in page.text
    assert "data-passport-quick-dialog" in page.text


async def test_development_methodology_is_never_selectable_over_approved_methodology(
    test_context,
):
    await _signup(test_context, "coexisting-methodologies@example.com")
    test_context["app"].dependency_overrides[get_market_data_provider] = ApiQuoteProvider
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        assert user is not None
        screening = ShariaScreeningService(session, test_context["settings"])
        malaysia = await screening.create_methodology(
            MethodologyCreateRequest(
                code="SC_MALAYSIA_SELECTION_TEST",
                name="SC Malaysia selection test",
                version="2026.03-test",
                description=(
                    "Evidence-backed active methodology used to verify explicit market selection."
                ),
                status=ShariaMethodologyStatus.ACTIVE,
                governing_body="Qualified test governance",
                reviewer_group="Qualified test reviewers",
                effective_from=now - timedelta(days=1),
                rules=methodology_rules(source_family="sc_malaysia_selection_test"),
                evidence_requirements=methodology_evidence_requirements(),
            ),
            actor_user_id=user.id,
            actor_identity="test-admin",
        )
        assessment = await screening.create_assessment(
            AssessmentCreateRequest(
                canonical_asset="SOL",
                asset_name="Solana",
                methodology_id=malaysia.id,
                status=ShariaAssetStatus.ELIGIBLE,
                summary="A qualified reviewer approved this test evidence package.",
                evidence_sources=[
                    EvidenceSourceInput(
                        source_type="official_regulator",
                        title="Official regulator reference",
                        publisher="Test regulator",
                        source_url="https://example.com/sc-reference",
                        retrieved_at=now,
                        evidence_category="external_status",
                        evidence_summary="Explicit status evidence retained for route testing.",
                    )
                ],
                reviewed_by="Qualified reviewer",
                reviewed_at=now,
                valid_from=now,
                reason_code="route_test",
                reason_summary="Qualified review completed for route selection testing.",
            ),
            actor_user_id=user.id,
        )
        test_methodology = ShariaMethodology(
            code="TRACEDGE_DEV_TEST_ROUTE",
            name="Development Test route",
            version="1.0-test",
            description="Development-only permissive market used for local product testing.",
            status=ShariaMethodologyStatus.DRAFT,
            rules_json={"development_only": True},
            evidence_requirements_json={"live_quotes": True},
        )
        session.add(test_methodology)
        await session.commit()

    default_page = await test_context["client"].get("/dashboard/market?view=assets")
    test_page = await test_context["client"].get(
        f"/dashboard/market?methodology_id={test_methodology.id}&exchange=bybit"
    )
    malaysia_page = await test_context["client"].get(
        f"/dashboard/market?methodology_id={malaysia.id}&view=assets"
    )
    malaysia_quotes = await test_context["client"].get(
        "/api/v1/sharia/market-quotes",
        params={
            "methodology_id": str(malaysia.id),
            "exchange": "bybit",
            "quote_asset": "USDT",
        },
    )
    exact_pair_search = await test_context["client"].get(
        "/api/v1/sharia/assets",
        params={"methodology": str(malaysia.id), "search": "SOL/USDT"},
    )
    empty_methodology = await test_context["client"].get(
        "/dashboard/market?methodology_id=&view=assets"
    )

    assert default_page.status_code == 200
    assert 'data-endpoint="/api/v1/sharia/market-quotes"' in default_page.text
    assert "Test methodology only" not in default_page.text
    assert test_page.status_code == 200
    assert "SC Malaysia selection test" in test_page.text
    assert "Development Test route" not in test_page.text
    assert malaysia_page.status_code == 200
    assert 'data-endpoint="/api/v1/sharia/market-quotes"' in malaysia_page.text
    assert "live-market-table" in malaysia_page.text
    assert str(assessment.id) not in malaysia_page.text
    assert malaysia_quotes.status_code == 200
    malaysia_payload = malaysia_quotes.json()
    assert malaysia_payload["methodology"]["id"] == str(malaysia.id)
    assert malaysia_payload["methodology"]["name"] == "SC Malaysia selection test"
    assert [item["symbol"] for item in malaysia_payload["items"]] == ["SOL/USDT"]
    assert malaysia_payload["items"][0]["status_label"] == "Eligible"
    assert malaysia_payload["items"][0]["passport_url"] == (
        f"/dashboard/market/SOL?methodology_id={malaysia.id}"
    )
    assert exact_pair_search.status_code == 200
    assert exact_pair_search.json()["total"] == 1
    assert exact_pair_search.json()["items"][0]["canonical_asset"] == "SOL"
    assert empty_methodology.status_code == 200


async def test_active_methodology_without_publications_has_clear_readiness_state(test_context):
    await _signup(test_context, "empty-methodology@example.com")
    test_context["app"].dependency_overrides[get_market_data_provider] = ApiQuoteProvider
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        assert user is not None
        screening = ShariaScreeningService(session, test_context["settings"])
        methodology = await screening.create_methodology(
            MethodologyCreateRequest(
                code="EMPTY_SC_MALAYSIA_TEST",
                name="Empty SC Malaysia test",
                version="2026.03-test",
                description="Active methodology awaiting authenticated asset publication reviews.",
                status=ShariaMethodologyStatus.ACTIVE,
                governing_body="Qualified test governance",
                reviewer_group="Qualified test reviewers",
                effective_from=now - timedelta(days=1),
                rules=methodology_rules(source_family="empty_sc_malaysia_test"),
                evidence_requirements=methodology_evidence_requirements(),
            ),
            actor_user_id=user.id,
            actor_identity="test-admin",
        )
        await session.commit()

    page = await test_context["client"].get(
        f"/dashboard/market?methodology_id={methodology.id}&view=assets"
    )
    settings_page = await test_context["client"].get("/dashboard/settings")
    saved = await test_context["client"].post(
        "/dashboard/settings",
        data={
            "timezone": "UTC",
            "default_sharia_methodology_id": str(methodology.id),
        },
        follow_redirects=False,
    )
    default_market = await test_context["client"].get("/dashboard/market?view=assets")

    assert page.status_code == 200
    assert "No Halal Market assets are available yet." in page.text
    assert "Open governance reviews" not in page.text
    assert "live-market-table" in page.text
    assert settings_page.status_code == 200
    assert "Empty SC Malaysia test" not in settings_page.text
    assert saved.status_code == 303
    assert "settings_saved" in saved.headers["location"]
    assert default_market.status_code == 200
    assert "Empty SC Malaysia test" in default_market.text


async def test_saved_assets_are_consolidated_into_market_while_scanner_stays_distinct(
    test_context,
):
    await _signup(test_context, "distinct-watch-routes@example.com")

    watchlists = await test_context["client"].get("/dashboard/watchlists")
    saved_assets = await test_context["client"].get("/dashboard/saved-assets")
    scanner = await test_context["client"].get(
        "/dashboard/check-market",
        follow_redirects=False,
    )

    assert watchlists.status_code == 200
    assert ">Watchlists<" in watchlists.text
    assert saved_assets.status_code == 303
    assert saved_assets.headers["location"] == "/dashboard/market?saved_assets=1"
    market = await test_context["client"].get(saved_assets.headers["location"])
    assert market.status_code == 200
    assert "Halal Market" in market.text
    assert "data-saved-assets-dialog" in market.text
    assert scanner.status_code == 303
    assert scanner.headers["location"] == "/dashboard/strategies/new?mode=scanner"
