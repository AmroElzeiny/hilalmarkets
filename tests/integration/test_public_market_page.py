"""The public Market page, its price feed, and the assistant's line at the same place.

What is asserted, as rules across the whole family:

* the feed sends exactly the first 20 coins, in the dashboard's own order, on both
  exchanges — and nothing about the rest except counts;
* the page keeps the dashboard's list and drops only what a visitor cannot use: no
  search, no sorting, no dashboard top bar; hearts, Favorites and Passports ask for an
  account and bring the visitor back to what they asked for;
* every public page carries "Markets" in its header and footer, and none does while the
  launch stage hides the page;
* the assistant asks a visitor to sign in before it says anything about a coin the page
  does not show — however the coin was named — and never for one it does show.
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
from ai_market_monitor.db.models import User
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
    PUBLIC_MARKET_VISIBLE_COUNT,
    PublicMarketService,
)
from ai_market_monitor.services.sharia_screening import ShariaScreeningService
from tests.factories import methodology_evidence_requirements, methodology_rules

#: 26 screened coins, so six sit behind the line. Named so no symbol is a substring of
#: another — "is this coin anywhere in the response?" must be an exact question.
COINS = [f"Q{chr(65 + index)}{chr(65 + index)}X" for index in range(26)]
CONDITIONAL = set(COINS[::4])


class VolumeProvider:
    """Every coin listed, with volumes that differ by exchange so the order does too.

    Bybit reverses the first twenty, so the two exchanges show the same twenty coins in
    different orders and the last six are behind the line on both.
    """

    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        assert exchange in MARKET_EXCHANGES
        return [f"{coin}/USDT" for coin in COINS]

    async def fetch_universe_metadata(self, exchange, symbols, *, include_listing_dates=False):
        del include_listing_dates
        values = {}
        limit = PUBLIC_MARKET_VISIBLE_COUNT
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
async def test_the_feed_sends_the_first_twenty_in_the_dashboards_order(test_context, exchange):
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

    shown = [item["canonical_asset"] for item in payload["items"]]
    assert shown == everything[:PUBLIC_MARKET_VISIBLE_COUNT]
    assert payload["visible_limit"] == PUBLIC_MARKET_VISIBLE_COUNT
    assert payload["total"] == len(COINS)
    assert payload["hidden_count"] == len(COINS) - PUBLIC_MARKET_VISIBLE_COUNT
    assert payload["status_counts"] == {
        "eligible": len(COINS) - len(CONDITIONAL),
        "eligible_with_qualifications": len(CONDITIONAL),
    }
    # Nothing about a coin behind the line is sent — not even its name.
    for coin in everything[PUBLIC_MARKET_VISIBLE_COUNT:]:
        assert coin not in public.text


async def test_the_feed_opens_on_the_default_standard_and_refuses_one_it_does_not_offer(
    test_context,
):
    await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    client = test_context["client"]

    default = await client.get("/api/v1/public-market/quotes")
    assert default.status_code == 200
    assert len(default.json()["items"]) == PUBLIC_MARKET_VISIBLE_COUNT

    unknown = await client.get(
        "/api/v1/public-market/quotes",
        params={"methodology_id": "00000000-0000-0000-0000-000000000001"},
    )
    assert unknown.status_code == 404
    bad_exchange = await client.get("/api/v1/public-market/quotes", params={"exchange": "kraken"})
    assert bad_exchange.status_code == 422


async def test_the_page_keeps_the_list_and_drops_what_a_visitor_cannot_use(test_context):
    methodology_id = await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider

    page = await test_context["client"].get("/market")
    assert page.status_code == 200
    html = page.text

    assert 'data-audience="public"' in html
    assert 'data-endpoint="/api/v1/public-market/quotes"' in html
    assert f'data-methodology-id="{methodology_id}"' in html
    assert "<h1>Market</h1>" in html
    # No dashboard chrome and no search.
    assert "hm-top" not in html
    assert "dashboard-sidebar" not in html
    assert "data-search" not in html
    # Every column heading keeps its look and does not sort.
    headings = re.findall(r"<button[^>]*data-sort=\"[a-z0-9]+\"[^>]*>", html)
    assert len(headings) == 6
    assert all("disabled" in heading for heading in headings)
    # Hearts, Favorites and the follow counter ask for an account instead.
    assert 'data-account-gate="favorites"' in html
    assert 'data-account-gate="follow"' in html
    assert "data-account-dialog" in html
    # Sign-up and sign-in bring the visitor back to the full list.
    assert "/signup?next=%2Fdashboard%2Fmarket" in html
    assert "/signin?next=%2Fdashboard%2Fmarket" in html
    # The locked rows are drawn by the script: no coin is written into the page.
    for coin in COINS:
        assert coin not in html
    # The one Ask AI button opens the public assistant.
    assert "data-public-chat-open" in html


async def test_a_signed_in_member_is_sent_to_the_full_list(test_context):
    await _screen_every_coin(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider
    await _signup(test_context, "member-redirect@example.com")

    response = await test_context["client"].get("/market?exchange=bybit", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/market?exchange=bybit"


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
        assert config["chrome"]["marketHref"] == "/market"
        footer = [item for group in config["chrome"]["footerGroups"] for item in group["items"]]
        assert {"label": "Markets", "href": "/market"} in footer
    else:
        # The Jinja pages write the address through `url_for`, which may be absolute.
        assert re.search(
            r'<nav class="public-nav".*?href="[^"]*/market"[^>]*>Markets</a>', html, re.S
        )
        assert re.search(
            r'<footer class="site-footer.*?<a href="[^"]*/market">Markets</a>', html, re.S
        )


async def test_before_launch_the_page_its_feed_and_its_links_are_closed(waitlist_context):
    client = waitlist_context["client"]
    waitlist_context["app"].dependency_overrides[get_market_data_provider] = VolumeProvider

    page = await client.get("/market", follow_redirects=False)
    assert page.status_code == 303
    feed = await client.get("/api/v1/public-market/quotes")
    assert feed.status_code == 404
    help_page = await client.get("/help")
    assert 'href="/market"' not in help_page.text
    landing = await client.get("/")
    assert '"marketHref": null' in landing.text


# --------------------------------------------------------------------------------
# The assistant, at the same line.
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
            session, settings, ai_client=fake, market_provider=VolumeProvider()
        ).answer(
            PublicChatAnswerRequest(
                question=question,
                session_id=f"public_market_gate_{slug}_0001",
                client_message_id=f"public-market-gate-{slug}-1",
                source_page="/market",
            ),
            user_id=user_id,
        )
        await session.commit()
    return result


async def _hidden_and_shown(test_context) -> tuple[str, str]:
    """One coin behind the line on every exchange, and one on the page."""

    methodology_id = await _screen_every_coin(test_context)
    shown: set[str] = set()
    async with test_context["session_factory"]() as session:
        service = PublicMarketService(session, test_context["settings"], VolumeProvider())
        for exchange in MARKET_EXCHANGES:
            view = await service.view(methodology_id=UUID(methodology_id), exchange=exchange)
            shown |= {item.canonical_asset for item in view.items}
    hidden = [coin for coin in COINS if coin not in shown]
    assert hidden, "the fixture must leave a coin behind the line on every exchange"
    return hidden[0], sorted(shown)[0]


@pytest.mark.parametrize("spelling", ["{coin}", "${coin}"])
async def test_a_marked_hidden_coin_is_answered_with_sign_in_and_no_model_call(
    test_context, spelling
):
    hidden, _shown = await _hidden_and_shown(test_context)
    fake = _FakeAI([])
    # Asked for the record, not for a ruling: "is X halal?" is answered by the
    # assistant's no-religious-rulings rule before anything else, which is right.
    result = await _ask(
        test_context, f"What does the review of {spelling.format(coin=hidden)} say?", fake
    )

    assert fake.payloads == []
    assert result.intent == "account_needed"
    assert hidden in result.message
    assert "free account" in result.message
    assert result.account_prompt is not None
    assert "next=%2Fdashboard%2Fmarket" in result.account_prompt.signup_href
    assert result.sources == []


@pytest.mark.parametrize("spelling", ["{lower} network", "{lower}"])
async def test_a_hidden_coin_named_in_words_is_refused_by_the_passport_lookup(
    test_context, spelling
):
    """Written in lower case, so it is not a marked symbol and reaches the model."""

    hidden, _shown = await _hidden_and_shown(test_context)
    question = f"what does the review say about {spelling.format(lower=hidden.lower())}"
    fake = _FakeAI([_ai("Let me check.", tools=["public_passport"])])
    result = await _ask(test_context, question, fake)

    # One model call only: the lookup refused the coin, so there is no second call and
    # nothing the model could say about it.
    assert len(fake.payloads) == 1
    assert result.intent == "account_needed"
    assert result.account_prompt is not None
    assert "Let me check." not in result.message


async def test_the_model_flagging_a_hidden_coin_replaces_its_answer(test_context):
    await _hidden_and_shown(test_context)
    fake = _FakeAI(
        [
            _ai(
                "Some words about a coin.",
                intent="coin_needs_account",
                mode="PRODUCT_CONVERSATION",
            )
        ]
    )
    result = await _ask(test_context, "tell me about some coin", fake)
    assert result.intent == "account_needed"
    assert "Some words about a coin." not in result.message


async def test_a_coin_on_the_page_is_answered_normally(test_context):
    _hidden, shown = await _hidden_and_shown(test_context)
    fake = _FakeAI(
        [
            _ai("Let me check.", tools=["public_passport"]),
            _ai(f"{shown} has a recorded review."),
        ]
    )
    result = await _ask(test_context, f"What does the review of {shown} say?", fake)
    assert result.intent != "account_needed"
    assert result.account_prompt is None
    # The model was told which coins it may talk about.
    evidence = json.dumps(fake.payloads[0])
    assert "coins_open_without_an_account" in evidence


async def test_a_signed_in_member_is_never_asked_to_sign_in(test_context):
    hidden, _shown = await _hidden_and_shown(test_context)
    async with test_context["session_factory"]() as session:
        member = await session.scalar(select(User))
    result = await _ask(
        test_context, f"What does the review of {hidden} say?", None, user_id=member.id
    )
    assert result.intent != "account_needed"
    assert result.account_prompt is None


@pytest.mark.parametrize(
    "question",
    ["Why can I not sign in?", "How much does Hilal Markets cost?", "What is RSI?"],
)
async def test_a_question_that_names_no_coin_is_never_gated(test_context, question):
    await _hidden_and_shown(test_context)
    result = await _ask(test_context, question, None)
    assert result.intent != "account_needed"
