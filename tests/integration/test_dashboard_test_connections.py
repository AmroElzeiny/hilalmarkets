"""`/dashboard/connections` must render, and must answer what the live page did not.

Every check here is tied to a finding scored against `/dashboard/connections` in
`docs/CONNECTIONS_AND_EMAIL_REPORT.md`. A finding with no check is a claim nobody can
verify, which is the same as an unfixed finding.

The rules this page is held to are in
`docs/dashboard-test-connections-and-email-rules.md`.
"""

from __future__ import annotations

import re

import lxml.html
import pytest
from sqlalchemy import select

from ai_market_monitor.db.models import TelegramConnection, User
from ai_market_monitor.db.models.enums import ConnectionStatus
from ai_market_monitor.services.product_language import every_message_kind
from tests.integration.test_dashboard_web import _signup_and_verify

CONNECTIONS = "/dashboard/connections"


async def _page(test_context, email: str) -> str:
    await _signup_and_verify(test_context, email=email)
    response = await test_context["client"].get(CONNECTIONS)
    assert response.status_code == 200, response.text[:800]
    return response.text


def _words(markup: str) -> str:
    """Only what a person actually reads.

    Not the markup. `data-dashboard-page="integrations"` is a machine identifier that
    highlights the right menu item, and a check that cannot tell it apart from a word on
    screen either fails for nothing or gets weakened until it stops catching anything.
    """

    document = lxml.html.fromstring(markup)
    for node in document.xpath("//script | //style | //template"):
        node.getparent().remove(node)
    return " ".join(document.text_content().split())


async def test_the_page_renders(test_context):
    page = await _page(test_context, "connections-render@example.com")

    assert "hm-connections-test.js" in page
    assert "hm-connections-test.css" in page
    assert "Connections" in page


async def test_every_channel_appears_including_email(test_context):
    """Finding 5: the live page offered no email at all — the one channel every person
    already has. It is here now, beside the other three."""

    page = await _page(test_context, "connections-channels@example.com")

    for channel in ("web", "email", "telegram", "whatsapp"):
        assert f'data-channel="{channel}"' in page, channel


async def test_the_page_says_what_will_actually_arrive(test_context):
    """Finding 4: nothing on the live page said what a channel would bring you. A
    person could switch one on with no idea what it was for.

    The list is built from the product's own set of message kinds, so a kind added to
    the product later cannot go missing from the page that promises it.
    """

    page = await _page(test_context, "connections-kinds@example.com")

    assert "What you will be told about" in page
    for kind in every_message_kind():
        assert kind.label in page, kind.label


async def test_a_channel_that_is_not_available_says_why_and_what_would_change_it(
    test_context,
):
    """Finding 3: the live page said "Disabled" and stopped. That is a state, not an
    answer — it tells somebody nothing about whether to wait, upgrade, or give up."""

    page = await _page(test_context, "connections-locked@example.com")

    assert "c-locked" in page
    assert re.search(r"WhatsApp (notices are not open yet|is not part of your plan)", page)
    assert "Nothing for you to do" in page or "Change your plan" in page


@pytest.mark.parametrize(
    "jargon",
    [
        "Delivery channels",
        "integration",
        "opt-in",
        "Last delivery",
        "No delivery recorded",
        "Clear error",
        "service window",
    ],
)
async def test_no_word_from_inside_the_machine_reaches_the_page(test_context, jargon):
    """Findings 2, 6, 7 and 8: the live page called itself "Delivery channels", said
    "No delivery recorded", grouped messages under "Evidence" and "Compliance", and
    offered a customer a button labelled "Clear error"."""

    page = await _page(test_context, f"connections-plain-{abs(hash(jargon))}@example.com")
    assert jargon.lower() not in _words(page).lower(), jargon


async def test_the_state_of_a_channel_is_never_colour_alone(test_context):
    """Every state carries its colour, its word and its icon. `brand guide.md` section
    10 and WCAG 1.4.1: never colour on its own."""

    page = await _page(test_context, "connections-colour@example.com")

    assert "data-c-state-label" in page
    assert "data-c-state-meaning" in page
    # The tone attribute drives the colour; it must never appear without words beside it.
    for match in re.finditer(r'<p class="c-state" data-tone="[a-z]+">(.*?)</p>', page, re.S):
        block = match.group(1)
        assert "data-icon=" in block
        assert "<strong" in block


async def test_the_switch_is_a_real_switch(test_context):
    """A `role="switch"` with `aria-checked`, so its state is in the accessibility tree
    and not only in which side the knob sits on."""

    page = await _page(test_context, "connections-switch@example.com")

    assert 'role="switch"' in page
    assert "aria-checked=" in page


async def test_nothing_on_the_page_is_a_browser_confirm_box(test_context):
    """Unlinking asks first, in a real dialog that traps focus and returns it."""

    page = await _page(test_context, "connections-ask@example.com")

    assert "data-c-ask-dialog" in page
    assert "Unlink Telegram?" in page
    assert "Keep it" in page


async def test_no_passport_popup_is_on_a_page_with_no_coin_on_it(test_context):
    page = await _page(test_context, "connections-popup@example.com")

    assert "data-passport-quick-view-dialog" not in page


# ---------------------------------------------------------------------------
# Linking and unlinking Telegram. One rule, in every state a connection can be in.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,alerts_enabled,chat_id",
    [
        (ConnectionStatus.ACTIVE, True, "5551"),
        # Every state that is *not* ready to receive. Each one used to show a person
        # "Link Telegram" for an account that was already linked to them — so pressing it
        # failed, and Unlink was never offered at all. No way forward, no way back.
        (ConnectionStatus.ACTIVE, False, "5551"),
        (ConnectionStatus.ACTIVE, True, None),
        (ConnectionStatus.PENDING, True, "5551"),
        (ConnectionStatus.REVOKED, True, "5551"),
    ],
    ids=["ready", "alerts-off", "no-chat", "pending", "revoked"],
)
async def test_a_linked_telegram_can_always_be_unlinked(
    test_context, status, alerts_enabled, chat_id
):
    """If there is an account on record, Unlink is offered. In every state, not one.

    The page used to decide this from "would a message actually arrive", which is a
    different question from "is there an account on record". Four of the five rows here
    are the gap between those two questions.
    """

    await _signup_and_verify(test_context, email=f"tg-{status.value}-{alerts_enabled}@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User).order_by(User.created_at.desc()))
        session.add(
            TelegramConnection(
                user_id=user.id,
                telegram_user_id=f"tg-{status.value}-{alerts_enabled}-{chat_id}",
                chat_id=chat_id,
                username="tester",
                status=status,
                alerts_enabled=alerts_enabled,
            )
        )
        await session.commit()

    response = await test_context["client"].get(CONNECTIONS)
    assert response.status_code == 200
    page = response.text

    assert "data-c-unlink-telegram" in page
    assert "data-c-connect-telegram" not in page


async def test_an_unlinked_telegram_is_offered_the_link_flow(test_context):
    """The other half of the same rule. No account on record means Link, never Unlink."""

    page = await _page(test_context, "connections-no-telegram@example.com")

    assert "data-c-connect-telegram" in page
    assert "data-c-unlink-telegram" not in page


async def test_unlinking_refuses_a_request_with_no_form_token(test_context):
    """It deletes the connection, the sign-in identity and the bot conversation.

    It was the one write on this page that read no form token at all, while every one of
    its neighbours did.
    """

    await _signup_and_verify(test_context, email="tg-csrf@example.com")
    response = await test_context["client"].delete(
        "/api/v1/dashboard/integrations/telegram",
        headers={"X-CSRF-Token": "not-the-token"},
    )

    assert response.status_code == 403


async def test_the_page_can_be_asked_whether_telegram_is_linked_yet(test_context):
    """The link popup promises "this page shows Telegram as linked", and something has
    to be able to notice. This is the address the page asks."""

    await _signup_and_verify(test_context, email="tg-status@example.com")
    response = await test_context["client"].get("/api/v1/dashboard/integrations")

    assert response.status_code == 200
    assert response.json()["telegram"] is None


@pytest.mark.parametrize(
    "claim",
    ["100% halal", "guaranteed", "risk-free", "buy now", "AI trades for you"],
)
async def test_no_forbidden_claim_reaches_the_page(test_context, claim):
    page = await _page(test_context, f"connections-claim-{abs(hash(claim))}@example.com")
    assert claim.lower() not in _words(page).lower(), claim


async def test_the_live_page_still_works(test_context):
    """The design path is a parallel copy. `/dashboard/connections` is not changed."""

    await _signup_and_verify(test_context, email="connections-live@example.com")
    response = await test_context["client"].get("/dashboard/connections")
    assert response.status_code == 200
