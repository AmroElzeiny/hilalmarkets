"""The public Market page, its price feed, and the assistant, for everybody alike.

What is asserted, as rules across the whole family:

* the feed sends every screened coin, in the dashboard's own order, on both exchanges —
  the same list to a visitor without an account as to a member (a visitor is treated
  like a member on the free plan; the page used to stop after twenty coins);
* the page shows the dashboard's list with search and sorting for everybody; only what
  is stored on an account — following a coin, Favorites — asks a visitor to sign up and
  brings them back to what they asked for;
* "See the evidence" opens the dashboard's own Passport popup for everybody, read from
  an open feed that answers for every screened coin exactly as the dashboard does;
* a signed-in member who opens the page stays on it — the "Markets" link must not drop
  them in the dashboard;
* the page answers at ``/markets``; the first address, ``/market``, forwards there;
* every public page carries "Markets" in its header and footer, and none does while the
  launch stage hides the page;
* the assistant talks about every coin to everybody once the site has launched, and
  about none to a visitor before launch — never "sign in first" because of a list limit.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from ai_market_monitor.api.dependencies import get_market_data_provider
from ai_market_monitor.core.site_content import PUBLIC_PAGES
from ai_market_monitor.db.models import ApprovedWatchlist, ApprovedWatchlistAsset, User
from ai_market_monitor.db.models.enums import ShariaAssetStatus, ShariaMethodologyStatus
from ai_market_monitor.schemas.public_chat import PublicChatAnswerRequest
from ai_market_monitor.schemas.sharia import (
    AssessmentCreateRequest,
    EvidenceSourceInput,
    MethodologyCreateRequest,
)
from ai_market_monitor.services.public_chat import PublicChatService
from ai_market_monitor.services.public_market import (
    MARKET_EXCHANGES,
    PublicMarketService,
)
from ai_market_monitor.services.sharia_screening import ShariaScreeningService
from tests.factories import methodology_evidence_requirements, methodology_rules

#: 26 screened coins — more than the twenty the page used to stop at, so a list that
#: still stopped there would fail. Named so no symbol is a substring of another — "is
#: this coin anywhere in the response?" must be an exact question.
COINS = [f"Q{chr(65 + index)}{chr(65 + index)}X" for index in range(26)]
CONDITIONAL = set(COINS[::4])


class VolumeProvider:
    """Every coin listed, with volumes that differ by exchange so the order does too.

    Bybit reverses the first twenty, so the two exchanges show the same coins in
    different orders.
    """

    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        assert exchange in MARKET_EXCHANGES
        return [f"{coin}/USDT" for coin in COINS]

    async def fetch_universe_metadata(self, exchange, symbols, *, include_listing_dates=False):
        del include_listing_dates
        values = {}
        limit = 20
        for position, symbol in enumerate(symbols):
            rank = (
                position
                if exchange == "binance" or position >= limit
                else limit - 1 - position
            )
            values[symbol] = {
                "bid": 1.0,
                "ask": 1.01,
                "last": 1.005,
                "quote_volume_24h": 1_000_000.0 - rank * 1000,
                "data_quality_ok": True,
            }
        return values


async def _screen_every_coin(test_context) -> str:
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        actor = User(display_name="Reviewer")
        session.add(actor)
        await session.flush()
        screening = ShariaScreeningService(session, test_context["settings"])
        methodology = await screening.create_methodology(
            MethodologyCreateRequest(
                code="PUBLIC_MARKET_TEST",
                name="Public market test standard",
                version="2026.09-test",
                description="Active standard used to check the public Market page.",
                status=ShariaMethodologyStatus.ACTIVE,
                governing_body="Qualified test governance",
                reviewer_group="Qualified test reviewers",
                effective_from=now - timedelta(days=1),
                rules=methodology_rules(source_family="public_market_test"),
                evidence_requirements=methodology_evidence_requirements(),
            ),
            actor_user_id=actor.id,
            actor_identity="test-admin",
        )
        for coin in COINS:
            await screening.create_assessment(
                AssessmentCreateRequest(
                    canonical_asset=coin,
                    asset_name=f"{coin} Network",
                    methodology_id=methodology.id,
                    status=(
                        ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
                        if coin in CONDITIONAL
                        else ShariaAssetStatus.ELIGIBLE
                    ),
                    summary="A qualified reviewer approved this test evidence package.",
                    qualifications=(["Read the condition first."] if coin in CONDITIONAL else []),
                    evidence_sources=[
                        EvidenceSourceInput(
                            source_type="official_regulator",
                            title="Official regulator reference",
                            publisher="Test regulator",
                            source_url="https://example.com/public-market",
                            retrieved_at=now,
                            evidence_category="external_status",
                            evidence_summary="Status evidence retained for the page test.",
                        )
                    ],
                    reviewed_by="Qualified reviewer",
                    reviewed_at=now,
                    valid_from=now,
                    reason_code="page_test",
                    reason_summary="Qualified review completed for the page test.",
                ),
                actor_user_id=actor.id,
            )
        await session.commit()
        return str(methodology.id)


@pytest.fixture(autouse=True)
def _fresh_caches():
    PublicMarketService.clear_cache()
    yield
    PublicMarketService.clear_cache()


async def _signup(test_context, email: str) -> None:
    client = test_context["client"]
    response = await client.post(
        "/signup/password",
        data={
            "email": email,
            "display_name": "Market member",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await client.post(
        "/signup/verify", data={"email": email, "code": code}, follow_redirects=False
    )
    assert verified.status_code == 303


@pytest.mark.parametrize("exchange", MARKET_EXCHANGES)
async def test_the_feed_sends_every_coin_in_the_dashboards_order(test_context, exchange):
    methodology_id = await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    client = test_context["client"]

    public = await client.get(
        "/api/v1/public-market/quotes",
        params={"methodology_id": methodology_id, "exchange": exchange},
    )
    assert public.status_code == 200, public.text
    payload = public.json()

    await _signup(test_context, f"member-{exchange}@example.com")
    member = await client.get(
        "/api/v1/sharia/market-quotes",
        params={"methodology_id": methodology_id, "exchange": exchange, "quote_asset": "USDT"},
    )
    assert member.status_code == 200, member.text
    everything = [item["canonical_asset"] for item in member.json()["items"]]

    assert len(everything) == len(COINS)
    assert [item["canonical_asset"] for item in payload["items"]] == everything
    assert payload["total"] == len(COINS)
    # Nothing in the answer describes a line any more.
    for gone in ("visible_limit", "hidden_count", "status_counts"):
        assert gone not in payload, gone


async def test_the_feed_opens_on_the_default_standard_and_refuses_one_it_does_not_offer(
    test_context,
):
    await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    client = test_context["client"]

    default = await client.get("/api/v1/public-market/quotes")
    assert default.status_code == 200
    assert len(default.json()["items"]) == len(COINS)

    unknown = await client.get(
        "/api/v1/public-market/quotes",
        params={"methodology_id": "00000000-0000-0000-0000-000000000001"},
    )
    assert unknown.status_code == 404
    bad_exchange = await client.get("/api/v1/public-market/quotes", params={"exchange": "kraken"})
    assert bad_exchange.status_code == 422


async def test_a_visitor_gets_the_whole_list_with_search_and_sorting(test_context):
    methodology_id = await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider

    page = await test_context["client"].get("/markets")
    assert page.status_code == 200
    html = page.text

    assert 'data-audience="public"' in html
    assert 'data-endpoint="/api/v1/public-market/quotes"' in html
    assert f'data-methodology-id="{methodology_id}"' in html
    assert "<h1>Halal Crypto Screener</h1>" in html
    # No dashboard chrome, and the search a member on the free plan has.
    assert "hm-top" not in html
    assert "dashboard-sidebar" not in html
    assert "data-search" in html
    # Every column heading sorts, as in the dashboard.
    headings = re.findall(r"<button[^>]*data-sort=\"[a-z0-9]+\"[^>]*>", html)
    assert len(headings) == 6
    assert not any("disabled" in heading for heading in headings)
    # Nothing is locked, and no limit is announced.
    assert "data-locked" not in html
    assert "data-visible-limit" not in html
    assert "more screened coins are waiting" not in html
    # Only what is stored on an account — following a coin, Favorites — asks for one.
    assert 'data-account-gate="favorites"' in html
    assert 'data-account-gate="follow"' in html
    assert "data-account-dialog" in html
    # Sign-up and sign-in bring the visitor back to the full list.
    assert "/signup?next=%2Fdashboard%2Fmarket" in html
    assert "/signin?next=%2Fdashboard%2Fmarket" in html
    # The one Ask AI button opens the public assistant.
    assert "data-public-chat-open" in html


async def test_a_signed_in_member_stays_on_the_public_page(test_context):
    await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    await _signup(test_context, "member-public-page@example.com")

    response = await test_context["client"].get(
        "/markets?exchange=bybit", follow_redirects=False
    )
    assert response.status_code == 200
    html = response.text
    # The public page, in the public chrome — not the dashboard.
    assert 'data-audience="public"' in html
    assert 'data-signed-in="true"' in html
    assert "<h1>Halal Crypto Screener</h1>" in html
    assert 'id="hm-site-footer"' in html
    assert "hm-top" not in html
    assert "dashboard-sidebar" not in html
    # The same page a visitor gets: search, and headings that sort.
    assert "data-search" in html
    headings = re.findall(r"<button[^>]*data-sort=\"[a-z0-9]+\"[^>]*>", html)
    assert headings and not any("disabled" in heading for heading in headings)
    # Nothing asks a member to open an account.
    assert "data-account-gate" not in html
    assert "data-account-dialog" not in html
    assert "/signup?" not in html
    # Favorites open in the dashboard, where following a coin happens.
    assert 'href="/dashboard/market?saved_assets=1"' in html


#: The address the public page's Passport popup reads from.
POPUP_FEED = "/api/v1/public-market/passports/{asset}/quick-view"


@pytest.mark.parametrize("signed_in", [False, True], ids=["visitor", "member"])
async def test_see_the_evidence_opens_the_dashboards_popup_on_the_public_page(
    test_context, signed_in
):
    """The dashboard's own Passport popup is on the page, reading the open feed."""

    await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    if signed_in:
        await _signup(test_context, "member-popup@example.com")

    html = (await test_context["client"].get("/markets")).text
    dialog = re.search(r"<dialog[^>]*data-passport-dialog[^>]*>", html, re.S)
    assert dialog, "the Passport popup is missing from the public page"
    assert f'data-endpoint="{POPUP_FEED}"' in dialog.group(0)
    assert "/hm-dialogs-test.js" in html
    # The popup's script runs before the list's, so the list finds it ready.
    assert html.index("/hm-dialogs-test.js") < html.index("/hm-market-test.js")


async def test_the_popup_feed_shows_every_coin_exactly_as_the_dashboard_does(test_context):
    """Every screened coin — shown or behind the line — opens for a visitor, and the
    popup a visitor reads is the one a member reads in the dashboard, word for word.

    A Passport is public (`/passports/<coin>`), so its short version is too.
    """

    methodology_id = await _screen_every_coin(test_context)
    client = test_context["client"]
    visitor: dict[str, dict] = {}
    for coin in COINS:
        response = await client.get(
            POPUP_FEED.format(asset=coin), params={"methodology": methodology_id}
        )
        assert response.status_code == 200, (coin, response.text)
        visitor[coin] = response.json()
        assert visitor[coin]["assessment"]["canonical_asset"] == coin
        assert visitor[coin]["full_passport_url"].startswith(f"/passports/{coin.lower()}")

    await _signup(test_context, "member-popup-compare@example.com")
    for coin in COINS:
        member = await client.get(
            f"/api/v1/sharia/assets/{coin}/passport/quick-view",
            params={"methodology": methodology_id},
        )
        assert member.status_code == 200, (coin, member.text)
        assert member.json() == visitor[coin], coin


async def test_the_popup_feed_refuses_a_coin_with_no_passport_and_invents_nothing(test_context):
    await _screen_every_coin(test_context)
    response = await test_context["client"].get(POPUP_FEED.format(asset="NOPASSPORTX"))
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] and detail["message"]
    assert "assessment" not in response.json()


async def test_a_visitor_and_a_member_are_sent_the_same_list(test_context):
    methodology_id = await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    client = test_context["client"]

    async def feed(exchange: str) -> list[str]:
        response = await client.get(
            "/api/v1/public-market/quotes",
            params={"methodology_id": methodology_id, "exchange": exchange},
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        return [item["canonical_asset"] for item in response.json()["items"]]

    as_visitor = {exchange: await feed(exchange) for exchange in MARKET_EXCHANGES}
    await _signup(test_context, "member-full-feed@example.com")
    as_member = {exchange: await feed(exchange) for exchange in MARKET_EXCHANGES}
    assert as_visitor == as_member
    for exchange in MARKET_EXCHANGES:
        assert sorted(as_visitor[exchange]) == sorted(COINS), exchange


async def test_both_pages_mark_the_same_followed_coins(test_context):
    """The public page and the dashboard read "which coins do I follow" from one place.

    Two default lists — which the schema allows — is the case where two readers could
    pick different ones; the first by name is the one both must show.
    """

    await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    await _signup(test_context, "member-follows@example.com")
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        member = await session.scalar(select(User).where(User.display_name == "Market member"))
        first = ApprovedWatchlist(user_id=member.id, name="A favorites", is_default=True)
        second = ApprovedWatchlist(user_id=member.id, name="B favorites", is_default=True)
        session.add_all([first, second])
        await session.flush()
        session.add_all(
            [
                ApprovedWatchlistAsset(watchlist_id=listed.id, canonical_asset=coin, added_at=now)
                for listed, coin in ((first, COINS[3]), (first, COINS[1]), (second, COINS[5]))
            ]
        )
        await session.commit()
        first_id = str(first.id)

    expected = json.dumps(sorted([COINS[1], COINS[3]]))
    client = test_context["client"]
    for path in ("/markets", "/dashboard/market"):
        html = (await client.get(path)).text
        assert f"data-favorite-assets='{expected}'" in html, path
        assert f'data-favorite-watchlist-id="{first_id}"' in html, path
        assert re.search(r'data-count="following">\s*2\s*<', html), path


@pytest.mark.parametrize("query", ["", "?exchange=bybit", "?exchange=bybit&methodology_id=x"])
async def test_the_first_address_forwards_to_the_page_with_its_query(test_context, query):
    response = await test_context["client"].get(f"/market{query}", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == f"/markets{query}"


async def test_the_page_is_never_kept_by_a_shared_cache(test_context):
    await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    page = await test_context["client"].get("/markets")
    assert page.headers["cache-control"] == "private, no-store"
    assert "Cookie" in page.headers["vary"].split(", ")


def _public_paths() -> list[str]:
    return ["/", *(page.path for page in PUBLIC_PAGES if page.page != "how_we_screen")]


@pytest.mark.parametrize("path", _public_paths())
async def test_every_public_page_offers_markets_in_its_header_and_footer(test_context, path):
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    response = await test_context["client"].get(path)
    assert response.status_code == 200, path
    html = response.text
    if 'id="root"' in html:
        # A React page: the header and footer are drawn from the runtime config.
        config = json.loads(
            re.search(r"window\.HilalMarketsRuntimeConfig = (\{.*?\});\n", html, re.S).group(1)
        )
        assert config["chrome"]["marketHref"] == "/markets"
        footer = [item for group in config["chrome"]["footerGroups"] for item in group["items"]]
        assert {"label": "Markets", "href": "/markets"} in footer
    else:
        # The Jinja pages write the address through `url_for`, which may be absolute.
        assert re.search(
            r'<nav class="public-nav".*?href="[^"]*/markets"[^>]*>Markets</a>', html, re.S
        )
        assert re.search(
            r'<footer class="site-footer.*?<a href="[^"]*/markets">Markets</a>', html, re.S
        )


async def test_before_launch_the_page_its_feed_and_its_links_are_closed(waitlist_context):
    client = waitlist_context["client"]
    waitlist_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider

    page = await client.get("/markets", follow_redirects=False)
    assert page.status_code == 303
    feed = await client.get("/api/v1/public-market/quotes")
    assert feed.status_code == 404
    popup = await client.get(POPUP_FEED.format(asset="BTC"))
    assert popup.status_code == 404
    help_page = await client.get("/help")
    assert 'href="/markets"' not in help_page.text
    landing = await client.get("/")
    assert '"marketHref": null' in landing.text


# --------------------------------------------------------------------------------
# The assistant: every coin, for everybody, once the site has launched.
# --------------------------------------------------------------------------------


class _FakeAI:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    async def create(self, payload, *, timeout_seconds):
        del timeout_seconds
        self.payloads.append(payload)
        return self.responses.pop(0)


def _ai(
    answer: str,
    *,
    tools: list[str] | None = None,
    intent: str = "product_help",
    mode: str = "PRODUCT_FACT",
) -> dict:
    return {
        "output_text": json.dumps(
            {
                "stage": "PUBLIC_PASSPORT_LOOKUP" if tools else "ANSWER",
                "mode": mode,
                "intent": intent,
                "answer": answer,
                "clarification_question": None,
                "source_ids": [],
                "related_route_ids": [],
                "requested_tools": tools or [],
                "confidence": 0.9,
                "answer_complete": True,
                "support_handoff_available": False,
                "support_handoff_reason": None,
                "safety_boundary": "product_scope_only",
                "suggested_follow_ups": [],
            }
        ),
        "output": [],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


async def _ask(test_context, question: str, fake: _FakeAI | None = None, *, user_id=None):
    settings = test_context["settings"]
    settings.public_chat_ai_enabled = fake is not None
    settings.opencode_go_api_key = SecretStr("test-opencode-key")
    slug = re.sub(r"[^A-Za-z0-9]", "_", question)[:40]
    async with test_context["session_factory"]() as session:
        result = await PublicChatService(
            session, settings, ai_client=fake
        ).answer(
            PublicChatAnswerRequest(
                question=question,
                session_id=f"public_market_gate_{slug}_0001",
                client_message_id=f"public-market-gate-{slug}-1",
                source_page="/markets",
            ),
            user_id=user_id,
        )
        await session.commit()
    return result


async def _first_and_last(test_context) -> tuple[str, str]:
    """The first coin of the list, and the last one — far past the old line of twenty."""

    methodology_id = await _screen_every_coin(test_context)
    async with test_context["session_factory"]() as session:
        service = PublicMarketService(session, test_context["settings"], VolumeProvider())
        view = await service.view(methodology_id=UUID(methodology_id), exchange="binance")
    order = [item.canonical_asset for item in view.items]
    assert len(order) > 20
    return order[0], order[-1]


async def _member_id(test_context):
    async with test_context["session_factory"]() as session:
        return (await session.scalar(select(User))).id


#: Each coin, asked about by each kind of reader. Every one gets the same treatment.
READERS = ["visitor", "member"]
POSITIONS = ["first", "last"]


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize("spelling", ["{coin}", "${coin}"])
async def test_a_marked_coin_is_never_answered_with_sign_in_first(
    test_context, reader, position, spelling
):
    first, last = await _first_and_last(test_context)
    coin = first if position == "first" else last
    user_id = await _member_id(test_context) if reader == "member" else None
    result = await _ask(
        test_context, f"What does the review of {spelling.format(coin=coin)} say?", None,
        user_id=user_id,
    )
    assert result.intent != "account_needed"
    assert result.account_prompt is None
    assert "free account" not in result.message


@pytest.mark.parametrize("position", POSITIONS)
async def test_a_visitor_gets_the_passport_lookup_for_any_coin(test_context, position):
    first, last = await _first_and_last(test_context)
    coin = first if position == "first" else last
    fake = _FakeAI(
        [
            _ai("Let me check.", tools=["public_passport"]),
            _ai(f"{coin} has a recorded review."),
        ]
    )
    result = await _ask(test_context, f"what does the review say about {coin.lower()}", fake)

    # Two model calls: the lookup ran and its result went back to the model.
    assert len(fake.payloads) == 2
    assert result.intent != "account_needed"
    assert result.account_prompt is None
    # The model is no longer handed a list of coins it may talk about. (The words stay in
    # its fixed instructions, for the time before launch; only the state would carry it.)
    assert not re.search(r'coins_open_without_an_account\\*"\s*:', json.dumps(fake.payloads[0]))


async def test_after_launch_the_model_cannot_turn_an_answer_into_sign_in_first(test_context):
    await _first_and_last(test_context)
    fake = _FakeAI(
        [_ai("Some words about a coin.", intent="coin_needs_account", mode="PRODUCT_CONVERSATION")]
    )
    result = await _ask(test_context, "tell me about some coin", fake)
    assert result.intent != "account_needed"
    assert result.account_prompt is None


async def test_before_launch_a_visitor_hears_about_no_coin(waitlist_context):
    """The launch stage, not sign-in, is what still closes coins: nothing is public yet."""

    await _screen_every_coin(waitlist_context)
    fake = _FakeAI([])
    result = await _ask(waitlist_context, f"What does the review of {COINS[0]} say?", fake)
    assert fake.payloads == []
    assert result.intent == "account_needed"
    assert "opens up at launch" in result.message
    assert result.account_prompt is None
    assert result.sources == []


@pytest.mark.parametrize(
    "question",
    ["Why can I not sign in?", "How much does Hilal Markets cost?", "What is RSI?"],
)
async def test_a_question_that_names_no_coin_is_never_gated(test_context, question):
    await _first_and_last(test_context)
    result = await _ask(test_context, question, None)
    assert result.intent != "account_needed"


# --------------------------------------------------------------------------------
# "Is this coin halal?" — never a flat refusal, always where to check it.
# --------------------------------------------------------------------------------

#: Every way the question is asked. Each one used to be refused, or reach the model.
HALAL_QUESTIONS = [
    "is {coin} halal?",
    "Is {coin} haram",
    "is ${coin} halal",
    "is {coin} shariah compliant?",
    "Is {coin} Sharia-compliant",
    "is it permissible to hold {coin}?",
]


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize("question", HALAL_QUESTIONS)
async def test_anyone_asking_if_any_coin_is_halal_is_sent_to_its_passport(
    test_context, question, position, reader
):
    first, last = await _first_and_last(test_context)
    coin = first if position == "first" else last
    user_id = await _member_id(test_context) if reader == "member" else None
    fake = _FakeAI([])
    result = await _ask(test_context, question.format(coin=coin), fake, user_id=user_id)

    assert fake.payloads == [], "a fixed answer needs no model call"
    assert result.status == "answered"
    assert result.intent == "coin_shariah_question"
    assert result.mode != "SAFETY_REFUSAL"
    assert result.message.startswith(f"I can't tell you myself that {coin} is halal.")
    assert "different Shariah screening standards" in result.message
    assert "free account" not in result.message
    assert [card.key for card in result.sources] == ["passport"]
    assert result.sources[0].url.endswith(f"/passports/{coin.lower()}")


async def test_advice_still_wins_over_the_halal_question(test_context):
    first, _last = await _first_and_last(test_context)
    result = await _ask(test_context, f"Should I buy {first}, is it halal?", _FakeAI([]))
    assert result.status == "refused"
    assert result.intent == "investment_advice"


async def test_a_halal_question_with_no_coin_offers_to_look_one_up(test_context):
    await _first_and_last(test_context)
    result = await _ask(test_context, "is crypto halal?", _FakeAI([]))
    assert result.intent == "religious_ruling"
    assert "does not issue religious rulings" not in result.message
    assert "Tell me which coin" in result.message
