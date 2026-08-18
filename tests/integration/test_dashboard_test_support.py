"""`/dashboard/support` must render, and must answer before it asks.

Every check is tied to a finding scored against `/dashboard/support` in
`docs/ACCOUNT_PAGES_REPORT.md`. The rules are in
`docs/dashboard-test-account-rules.md`, section I.
"""

from __future__ import annotations

import lxml.html
import pytest
from sqlalchemy import select

from ai_market_monitor.api.routers.dashboard_test import SUPPORT_TOPICS
from ai_market_monitor.core.csrf import csrf_token
from ai_market_monitor.db.models import SupportRequest, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider
from tests.integration.test_dashboard_web import _signup_and_verify

SUPPORT = "/dashboard/support"
TICKETS = "/api/v1/dashboard/support/tickets"


async def _account(test_context, email: str):
    await _signup_and_verify(test_context, email=email)
    async with test_context["session_factory"]() as session:
        user_id = await session.scalar(
            select(UserIdentity.user_id).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == email,
            )
        )
    assert user_id is not None
    return user_id, csrf_token(test_context["settings"], user_id)


async def _page(test_context, email: str) -> str:
    await _signup_and_verify(test_context, email=email)
    response = await test_context["client"].get(SUPPORT)
    assert response.status_code == 200, response.text[:800]
    return response.text


def _words(markup: str) -> str:
    document = lxml.html.fromstring(markup)
    for node in document.xpath("//script | //style | //template"):
        node.getparent().remove(node)
    return " ".join(document.text_content().split())


# ── The page ────────────────────────────────────────────────────────────────


async def test_the_page_renders(test_context):
    page = await _page(test_context, "sup-render@example.com")

    assert "hm-support-test.js" in page
    assert "hm-account-test.css" in page
    assert "Get help" in page


async def test_the_page_tries_to_answer_before_it_asks(test_context):
    """Rule I1 and finding 1. The live page opened with an empty form. Most people
    writing in about messages can fix it themselves in two presses."""

    page = await _page(test_context, "sup-selfhelp@example.com")
    document = lxml.html.fromstring(page)

    cards = document.xpath('//a[@data-h-help]')
    assert len(cards) >= 3
    # And it comes first, before the form.
    assert page.index("data-h-help") < page.index("data-h-form")


async def test_every_self_help_card_goes_somewhere_real(test_context):
    """A card pointing at a page that does not exist is worse than no card."""

    page = await _page(test_context, "sup-links@example.com")
    document = lxml.html.fromstring(page)

    for card in document.xpath("//a[@data-h-help]"):
        target = card.get("href")
        assert target and target.startswith("/dashboard")
        response = await test_context["client"].get(target)
        assert response.status_code == 200, target


async def test_the_page_never_asks_for_a_subject_line(test_context):
    """Finding 2: "Subject" was the first box on the live page. Summarising a problem
    in a few words is the hardest question you can ask somebody who is stuck.

    Picking a topic writes it for them, and the topic is a real stored category rather
    than a label invented for the page.
    """

    page = await _page(test_context, "sup-subject@example.com")
    document = lxml.html.fromstring(page)
    words = _words(page)

    assert not document.xpath('//input[@name="subject"]')
    for code, label, _icon, _hint in SUPPORT_TOPICS:
        assert f'data-h-topic="{code}"' in page
        # Read from the text a person sees, not the markup: an apostrophe is escaped in
        # the source and a check against the source would be testing Jinja, not the page.
        assert label in words, label


async def test_the_page_says_what_happens_after_you_send(test_context):
    """Rule I3 and finding 3. The live page's only promise was a toast that vanished."""

    page = await _page(test_context, "sup-next@example.com")
    words = _words(page)

    assert "We reply by email" in words


async def test_the_upload_limits_are_stated_before_they_are_met(test_context):
    """Rule I5. The live page named its limits in small print under the control and gave
    no way at all to take a chosen file back out."""

    page = await _page(test_context, "sup-files@example.com")
    words = _words(page)

    assert "Up to 3 pictures" in words
    assert "under 5 MB" in words
    assert "data-h-file-list" in page


async def test_the_page_says_never_to_send_a_secret(test_context):
    """Rule I6."""

    page = await _page(test_context, "sup-secret@example.com")
    words = _words(page)

    assert "No passwords" in words
    assert "We will never ask you for one" in words


async def test_the_file_picker_can_take_the_keyboard_visibly(test_context):
    """Rule D4. A visually hidden input still takes focus, and a focus ring nobody can
    see leaves a keyboard user with no idea where they are. The label wears it."""

    page = await _page(test_context, "sup-focus@example.com")
    document = lxml.html.fromstring(page)

    (drop,) = document.xpath('//label[@data-h-drop]')
    assert drop.xpath('.//input[@type="file"]'), "the picker must sit inside its label"


@pytest.mark.parametrize(
    "jargon",
    ["ticket", "escalat", "Recent conversations", "Send support request", "diagnostic"],
)
async def test_no_word_from_inside_the_machine_reaches_the_page(test_context, jargon):
    """Rule E2. "Your tickets" and "Send support request" were both on the live page."""

    page = await _page(test_context, f"sup-plain-{abs(hash(jargon))}@example.com")
    assert jargon.lower() not in _words(page).lower(), jargon


@pytest.mark.parametrize(
    "claim",
    ["100% halal", "guaranteed", "risk-free", "buy now", "AI trades for you"],
)
async def test_no_forbidden_claim_reaches_the_page(test_context, claim):
    page = await _page(test_context, f"sup-claim-{abs(hash(claim))}@example.com")
    assert claim.lower() not in _words(page).lower(), claim


async def test_an_account_with_nothing_sent_says_so_in_a_sentence(test_context):
    page = await _page(test_context, "sup-empty@example.com")
    words = _words(page)

    assert "You have not asked us anything yet" in words


# ── Sending, and reading it back ────────────────────────────────────────────


async def test_a_sent_message_appears_with_your_own_words_in_it(test_context):
    """Rule I7 and finding 4. The live page listed a subject and a status badge, so
    nobody could see what they had actually asked us."""

    email = "sup-readback@example.com"
    _user_id, token = await _account(test_context, email)

    sent = await test_context["client"].post(
        TICKETS,
        headers={"X-CSRF-Token": token},
        json={
            "category": "missing_alert",
            "email": email,
            "subject": "I was not told about something",
            "description": "My BTC watchlist matched on Tuesday and no message arrived.",
            "context": {"source": "dashboard"},
            "screenshots": [],
        },
    )
    assert sent.status_code == 201, sent.text[:400]

    response = await test_context["client"].get(SUPPORT)
    words = _words(response.text)
    assert "My BTC watchlist matched on Tuesday and no message arrived." in words
    assert "Waiting for us" in words


@pytest.mark.parametrize("code", [code for code, _l, _i, _h in SUPPORT_TOPICS])
async def test_every_topic_the_page_offers_is_one_the_server_accepts(test_context, code):
    """A topic the page shows and the endpoint refuses is a dead button. Parametrised
    across the whole set, so adding a topic without wiring it fails here."""

    email = f"sup-topic-{code}@example.com"
    _user_id, token = await _account(test_context, email)

    response = await test_context["client"].post(
        TICKETS,
        headers={"X-CSRF-Token": token},
        json={
            "category": code,
            "email": email,
            "subject": "A question",
            "description": "Something happened and I would like some help with it.",
            "context": {"source": "dashboard"},
            "screenshots": [],
        },
    )

    assert response.status_code == 201, response.text[:400]
    async with test_context["session_factory"]() as session:
        stored = await session.scalar(
            select(SupportRequest.category).where(SupportRequest.subject == "A question")
        )
    assert stored == code


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("missing_alert", "high"),
        ("billing", "high"),
        ("bug_report", "high"),
        ("screening", "normal"),
        ("general", "normal"),
    ],
)
async def test_what_it_is_about_decides_how_urgent_it_is(test_context, code, expected):
    """One owner for urgency, across the whole family of topics.

    There are two ways into this product's support queue and they had two different
    answers: `SupportEscalationService` raised three categories to high, and this
    endpoint stored everything as "normal" whatever it said it was about. Somebody
    reporting that no message had reached them was queued behind a general question.
    """

    email = f"sup-priority-{code}@example.com"
    _user_id, token = await _account(test_context, email)

    response = await test_context["client"].post(
        TICKETS,
        headers={"X-CSRF-Token": token},
        json={
            "category": code,
            "email": email,
            "subject": "A question",
            "description": "Something happened and I would like some help with it.",
            "context": {},
            "screenshots": [],
        },
    )
    assert response.status_code == 201

    async with test_context["session_factory"]() as session:
        stored = await session.scalar(
            select(SupportRequest.priority).where(SupportRequest.category == code)
        )
    assert stored == expected


async def test_the_topic_a_person_picked_is_shown_back_to_them_in_their_words(
    test_context,
):
    """The stored category is a machine word. The page must show the words the person
    actually pressed."""

    email = "sup-topicwords@example.com"
    _user_id, token = await _account(test_context, email)
    await test_context["client"].post(
        TICKETS,
        headers={"X-CSRF-Token": token},
        json={
            "category": "billing",
            "email": email,
            "subject": "Something about paying",
            "description": "I was charged twice for the same month.",
            "context": {},
            "screenshots": [],
        },
    )

    document = lxml.html.fromstring((await test_context["client"].get(SUPPORT)).text)
    (card,) = document.xpath("//li[@data-h-ticket]")
    said = " ".join(card.text_content().split())

    assert "Something about paying" in said
    # Scoped to the card itself. "Billing" is a word in the side menu, and a check over
    # the whole page would be measuring the menu rather than this card.
    assert "billing" not in said.lower()


async def test_you_can_read_your_own_messages_back_through_the_api(test_context):
    """The redesigned page shows what you wrote. That needed a way to read it, and the
    live path had none — only a write endpoint existed."""

    email = "sup-api@example.com"
    _user_id, token = await _account(test_context, email)
    await test_context["client"].post(
        TICKETS,
        headers={"X-CSRF-Token": token},
        json={
            "category": "general",
            "email": email,
            "subject": "Something else",
            "description": "Just checking that I can read this back.",
            "context": {},
            "screenshots": [],
        },
    )

    response = await test_context["client"].get(TICKETS)

    assert response.status_code == 200
    tickets = response.json()["tickets"]
    assert len(tickets) == 1
    assert tickets[0]["subject"] == "Something else"
    assert tickets[0]["messages"][0]["from_you"] is True
    assert "read this back" in tickets[0]["messages"][0]["body"]


async def test_you_only_ever_see_your_own_messages(test_context):
    """Scoped by the query itself, so there is no path where one person's request
    reaches another."""

    first = "sup-mine@example.com"
    _first_id, first_token = await _account(test_context, first)
    await test_context["client"].post(
        TICKETS,
        headers={"X-CSRF-Token": first_token},
        json={
            "category": "general",
            "email": first,
            "subject": "Something else",
            "description": "This belongs to the first person only.",
            "context": {},
            "screenshots": [],
        },
    )

    await _account(test_context, "sup-theirs@example.com")
    response = await test_context["client"].get(TICKETS)

    assert response.status_code == 200
    assert response.json()["tickets"] == []


async def test_no_passport_popup_is_on_a_page_with_no_coin_on_it(test_context):
    page = await _page(test_context, "sup-popup@example.com")

    assert "data-passport-quick-view-dialog" not in page


async def test_the_live_page_still_works(test_context):
    """The design path is a parallel copy. `/dashboard/support` is not changed."""

    await _signup_and_verify(test_context, email="sup-live@example.com")
    response = await test_context["client"].get("/dashboard/support")
    assert response.status_code == 200
