"""Hilal knows the whole product, wherever it is opened — asserted for every page.

The report was "I asked where the FAQ is and it did not know, although it exists". The
cause was not the FAQ: Hilal was only ever told about the page it was opened on, so it
could not know where *any* other page was. These tests hold the rule for every page the
menus list, every Help Center answer, and every reviewed coin a question names:

* every page is in the evidence, named the way its menu names it, with where it is;
* every page in the evidence leads to a card that opens it;
* the Help Center carries its other name, "FAQ";
* a question naming a reviewed coin always ends with that coin's Passport card, even when
  the model forgets to ask for one — the promise behind the new "is it halal?" answer.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from ai_market_monitor.core.site_content import (
    ACCOUNT_MENU,
    DASHBOARD_NAVIGATION,
    PUBLIC_PAGE_BY_PAGE,
    public_help_categories,
)
from ai_market_monitor.db.models import User
from ai_market_monitor.schemas.hilal_chat import HilalChatAsk, HilalChatReply, HilalChatView
from ai_market_monitor.services.hilal_chat import HilalChatService, hilal_source_cards
from ai_market_monitor.services.hilal_chat_agent import HilalChatCall, _instructions
from ai_market_monitor.services.hilal_chat_knowledge import HilalChatKnowledge
from ai_market_monitor.services.source_previews import SOURCE_PAGES
from tests.integration.test_dashboard_web import _signup_and_verify
from tests.integration.test_hilal_chat_knowledge import _seed

MENU_ITEMS = [item for group in DASHBOARD_NAVIGATION for item in group.items]


async def _gather(test_context, message: str = "hello", *, view=None, earlier=None):
    async with test_context["session_factory"]() as session:
        knowledge = HilalChatKnowledge(session, test_context["settings"])
        return await knowledge.gather(message=message, view=view, earlier=earlier)


def _cards_for(evidence, ids: list[str], settings) -> list[dict]:
    return hilal_source_cards(ids, known=evidence.ids, settings=settings)


# --------------------------------------------------------------------------------
# Every page, wherever Hilal is opened.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("item", [*MENU_ITEMS, *ACCOUNT_MENU], ids=lambda item: item.label)
async def test_every_menu_address_is_the_route_it_names(test_context, item):
    """The menu writes its addresses down so the assistant can name them without a
    request in hand. Written down is how they drift, so each is checked against the
    route its own endpoint resolves to."""

    assert item.path, f"{item.label} has no address"
    assert test_context["app"].url_path_for(item.endpoint) == item.path


@pytest.mark.parametrize("item", MENU_ITEMS, ids=lambda item: item.label)
def test_every_menu_page_says_what_it_is_for_in_plain_words(item):
    assert item.about.strip(), f"{item.label} has no description"
    assert len(item.about) <= 200, "a description is one plain sentence, not a page"
    for word in ("_", "{", "http"):
        assert word not in item.about


@pytest.mark.parametrize("item", MENU_ITEMS, ids=lambda item: item.page)
async def test_every_menu_page_is_known_with_where_it_is_and_a_card(test_context, item):
    evidence = await _gather(test_context)
    rows = [row for row in evidence.pages if row["name"] == item.label]
    assert len(rows) == 1, f"{item.label} is not in what Hilal knows"
    row = rows[0]
    assert row["where"].startswith("In the dashboard's side menu")
    assert row["what_it_is_for"] == item.about and item.about
    cards = _cards_for(evidence, [row["id"]], test_context["settings"])
    assert len(cards) == 1, f"{item.label} has no card"
    assert cards[0]["url"].endswith(item.path)


@pytest.mark.parametrize("item", ACCOUNT_MENU, ids=lambda item: item.label)
async def test_every_account_menu_entry_is_named_where_the_side_menu_entry_is(
    test_context, item
):
    evidence = await _gather(test_context)
    row = next(
        row
        for row in evidence.pages
        if row["kind"] == "dashboard_page"
        and _cards_for(evidence, [row["id"]], test_context["settings"])[0]["url"].endswith(
            item.path
        )
    )
    assert "account menu" in row["where"]
    assert item.label in row["where"] or item.label == row["name"]


@pytest.mark.parametrize(
    "key",
    sorted(key for key in PUBLIC_PAGE_BY_PAGE if key in SOURCE_PAGES),
)
async def test_every_public_page_with_a_card_is_known(test_context, key):
    evidence = await _gather(test_context)
    page = PUBLIC_PAGE_BY_PAGE[key]
    rows = [row for row in evidence.pages if row["id"] == f"page:{key}"]
    if key in test_context["settings"].stage_exposure.hidden_pages:
        assert rows == [], f"{key} is hidden by the launch stage and must not be offered"
        return
    assert len(rows) == 1, f"{page.title} is not in what Hilal knows"
    assert rows[0]["name"] == page.title
    assert rows[0]["where"].startswith("On the public website")
    cards = _cards_for(evidence, [rows[0]["id"]], test_context["settings"])
    assert cards and cards[0]["url"].endswith(page.path)


async def test_the_faq_is_the_help_center(test_context):
    """The reported question, answered from the evidence: the FAQ exists, and where."""

    evidence = await _gather(test_context, "where is the FAQ?")
    help_row = next(row for row in evidence.pages if row["id"] == "page:help")
    assert "FAQ" in help_row["also_called"]
    assert help_row["name"] == "Help Center"


async def test_every_help_center_answer_is_known_and_leads_to_the_help_center(test_context):
    evidence = await _gather(test_context)
    settings = test_context["settings"]
    expected = [
        article["question"]
        for category in public_help_categories(waitlist_mode=settings.waitlist_mode)
        for article in category["articles"]
    ]
    assert [row["question"] for row in evidence.help_answers] == expected
    for row in evidence.help_answers:
        cards = _cards_for(evidence, [row["id"]], settings)
        assert cards and cards[0]["key"] == "help"


@pytest.mark.parametrize("item", MENU_ITEMS, ids=lambda item: item.page)
async def test_hilal_knows_which_page_they_are_on(test_context, item):
    evidence = await _gather(test_context, view=HilalChatView(page=item.page))
    here = [row["name"] for row in evidence.pages if row.get("they_are_on_it_now")]
    assert here == [item.label]


async def test_no_page_row_carries_an_address(test_context):
    """Hilal may not write a link; the card is the link. No address reaches the model."""

    evidence = await _gather(test_context)
    for row in [*evidence.pages, *evidence.help_answers]:
        for value in row.values():
            text = str(value)
            assert "http" not in text and "/dashboard" not in text, row


def test_the_instructions_send_people_to_pages_and_passports():
    text = _instructions()
    assert "pages_in_this_product" in text and "help_center_answers" in text
    assert "the FAQ is the Help Center" in text
    assert "do not refuse" in text
    assert "Use mode ANSWER, never REFUSAL" in text
    # The old wording, which closed the door on every "is it halal?" question.
    assert "only the review process decides that" not in text


# --------------------------------------------------------------------------------
# "Is it halal?" ends with the coin's Passport, every time.
# --------------------------------------------------------------------------------


class _ForgetfulAgent:
    """Answers without naming any record — the model forgetting the Passport id."""

    def model_for_turn(self) -> tuple[str, str]:
        return "muse-spark-1.3-contributor", "low"

    async def answer(self, **_: object) -> HilalChatCall:
        return HilalChatCall(
            reply=HilalChatReply(
                mode="ANSWER",
                reply="I can't tell you myself that it is halal. Its Passport is below.",
                language="English",
                grounded_in=[],
            ),
            model="muse-spark-1.3-contributor",
            reasoning_effort="low",
            input_tokens=10,
            output_tokens=10,
            estimated_cost_usd=0.0,
            latency_ms=1,
        )


async def _ask(test_context, message: str, email: str) -> list[dict]:
    await _signup_and_verify(test_context, email=email)
    async with test_context["session_factory"]() as session:
        # One person per test app, exactly as `_signed_in` in test_hilal_chat.py reads it.
        user = await session.scalar(select(User))
        assert user is not None
        service = HilalChatService(
            session, test_context["settings"], agent=_ForgetfulAgent()  # type: ignore[arg-type]
        )
        turn = await service.ask(user=user, ask=HilalChatAsk(message=message))
        await session.commit()
        return list(turn.sources)


QUESTIONS = [
    "is LTC halal?",
    "Is litecoin halal",
    "is $LTC haram?",
    "Is LTCUSDT shariah compliant?",
    "can a muslim hold litecoin, is it permissible?",
]


@pytest.mark.parametrize("question", QUESTIONS)
async def test_a_reviewed_coin_always_ends_with_its_passport(test_context, question):
    async with test_context["session_factory"]() as session:
        await _seed(
            session, symbol="LTC", name="Litecoin", pairs=("LTC/USDT",), reviewed=True
        )
    sources = await _ask(test_context, question, email=f"p{uuid4().hex[:8]}@example.com")
    assert sources, "no Passport card under the answer"
    assert sources[0]["key"] == "passport"
    assert sources[0]["url"].endswith("/passports/ltc")


async def test_a_coin_with_no_review_gets_no_passport_card(test_context):
    async with test_context["session_factory"]() as session:
        await _seed(
            session, symbol="LTC", name="Litecoin", pairs=("LTC/USDT",), reviewed=False
        )
    sources = await _ask(test_context, "is LTC halal?", email="noreview@example.com")
    assert all(card["key"] != "passport" for card in sources)


async def test_a_question_that_names_no_coin_gets_no_passport_card(test_context):
    async with test_context["session_factory"]() as session:
        await _seed(
            session, symbol="LTC", name="Litecoin", pairs=("LTC/USDT",), reviewed=True
        )
    sources = await _ask(test_context, "where is the FAQ?", email="nocoin@example.com")
    assert all(card["key"] != "passport" for card in sources)
