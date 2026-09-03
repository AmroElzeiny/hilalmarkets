"""`/dashboard/subscription` must render, and must answer what the live page did not.

Every check is tied to a finding scored against `/dashboard/subscription` in
`docs/ACCOUNT_PAGES_REPORT.md`. A finding with no check is a claim nobody can verify,
which is the same as an unfixed finding.

The rules this page is held to are in `docs/dashboard-test-account-rules.md`.
"""

from __future__ import annotations

import json
import re

import lxml.html
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PLAN_OFFERS,
    visible_public_plan_codes,
)
from tests.integration.test_dashboard_web import _signup_and_verify

SUBSCRIPTION = "/dashboard/subscription"


async def _page(test_context, email: str) -> str:
    await _signup_and_verify(test_context, email=email)
    response = await test_context["client"].get(SUBSCRIPTION)
    assert response.status_code == 200, response.text[:800]
    return response.text


def _words(markup: str) -> str:
    """Only what a person actually reads, never the markup around it."""

    document = lxml.html.fromstring(markup)
    for node in document.xpath("//script | //style | //template"):
        node.getparent().remove(node)
    return " ".join(document.text_content().split())


async def test_the_page_renders(test_context):
    page = await _page(test_context, "sub-render@example.com")

    assert "hm-subscription-test.js" in page
    assert "hm-account-test.css" in page
    assert "Your plan" in page


async def test_the_first_thing_is_what_you_have_not_what_you_could_buy(test_context):
    """Finding 1. The live page's first block named the plan and then went straight to
    the price grid, so the question it answered first was "what would I buy" — asked of
    somebody who has not yet been told what their plan already allows."""

    page = await _page(test_context, "sub-order@example.com")
    document = lxml.html.fromstring(page)

    now = document.xpath('//section[@id="s-now"]')
    plans = document.xpath('//section[@id="s-plans"]')
    assert now and plans
    # Source order is reading order here: both are ordinary blocks in one column.
    assert page.index('id="s-now"') < page.index('id="s-plans"')


async def test_the_page_says_what_your_plan_lets_you_do(test_context):
    """Finding 2: the live page named the plan and its "access source" and stopped.
    Neither tells a beginner what the plan actually allows."""

    page = await _page(test_context, "sub-allowance@example.com")

    assert "data-s-allowance" in page
    # "Monitor" is what the product calls the thing that runs. This row called it a
    # Watchlist, which is the word for a saved list of coins — a different object, and
    # the same confusion that had the Monitors page answering at `/dashboard/watchlists`.
    assert "Monitors running at once" in page
    assert "Watchlists running at once" not in page


async def test_an_allowance_of_none_is_never_drawn_as_a_zero(test_context):
    """Rule F4. The free plan allows no market checks a month and keeps no history, and
    both are stored as `0`. A tile with a large "0" on it reads as something running
    out, not as something the plan never included — and the plan's own card already
    says in words what is and is not in it."""

    page = await _page(test_context, "sub-zero@example.com")
    document = lxml.html.fromstring(page)

    for tile in document.xpath("//div[@data-s-allowance]"):
        value = tile.xpath('.//p[contains(@class,"s-allowance-value")]')[0]
        assert value.text_content().strip() != "0", tile.text_content()


async def test_only_the_message_cap_that_really_bites_is_shown(test_context):
    """The free plan allows two messages a day *and* two a week. Printing "2 a day"
    beside a weekly cap of 2 tells somebody they can have fourteen, which is the
    expensive direction to be wrong in."""

    page = await _page(test_context, "sub-messages@example.com")
    words = _words(page)

    assert not ("Messages a day" in words and "Messages a week" in words)


async def test_how_the_plan_ends_is_one_sentence_not_three_tiles(test_context):
    """Finding 3: "Access source", "Trial access until" and "Renewal" were three tiles
    saying one thing, in a vocabulary nobody uses out loud."""

    page = await _page(test_context, "sub-renewal@example.com")
    words = _words(page)

    assert "Access source" not in words
    assert re.search(r"(Free, with no end date|renews by itself|does not renew by itself)", words)


@pytest.mark.parametrize(
    "code",
    [code for code in visible_public_plan_codes(billing_enabled=True)],
)
async def test_every_visible_plan_has_a_card(test_context, code):
    page = await _page(test_context, f"sub-card-{code}@example.com")

    assert f'data-plan="{code}"' in page
    assert PLAN_DEFINITIONS[code].name in page


@pytest.mark.parametrize(
    "code",
    [code for code, offer in PLAN_OFFERS.items() if not offer.monthly_available],
)
async def test_a_plan_nobody_can_buy_shows_no_price(test_context, code):
    """Rule G2. A number beside "Soon" reads as a charge somebody is about to face.

    The figure is not merely hidden by CSS — it is never rendered, so it is not in the
    page source for anyone to read either.
    """

    page = await _page(test_context, f"sub-soon-{code}@example.com")
    document = lxml.html.fromstring(page)

    (card,) = document.xpath(f'//li[@data-plan="{code}"]')
    price = card.xpath('.//p[contains(@class,"s-price")]')[0]
    assert "Not open yet" in price.text_content()
    assert "$" not in price.text_content()


async def test_a_plan_that_cannot_be_chosen_says_why(test_context):
    """Rule G2 and D10. "Disabled" on its own is a state, not an answer."""

    page = await _page(test_context, "sub-blocked@example.com")

    assert "s-blocked" in page
    assert re.search(
        r"(not open for new subscriptions yet|Every account already includes it"
        r"|Paid subscriptions are switched off)",
        page,
    )


async def test_the_free_plan_is_never_described_as_switched_off(test_context):
    """A free plan is not "switched off" and it is not "coming soon". Saying either
    about it would be wrong in a way a beginner cannot check."""

    page = await _page(test_context, "sub-free@example.com")
    document = lxml.html.fromstring(page)

    (card,) = document.xpath('//li[@data-plan="demo"]')
    blocked = card.xpath('.//p[contains(@class,"s-blocked")]')
    if blocked:
        assert "switched off" not in blocked[0].text_content()


async def test_the_checkout_shows_the_exact_charge_before_the_button_that_leaves(
    test_context,
):
    """Rule G5 and G6. The live popup stated the price once, at the top, above a scroll
    of address fields — so the last thing somebody read before leaving the site was a
    postcode."""

    page = await _page(test_context, "sub-checkout@example.com")

    assert "data-s-order-total" in page
    assert "data-s-order-when" in page
    # Three named steps, so a person always knows how far through they are.
    assert page.count('data-s-step="') == 3
    assert "Step 1 of 3" in page


async def test_the_checkout_says_who_takes_the_card_details(test_context):
    """Rule F5. Hilal Markets never receives them, and the page has to say so where the
    person is about to type them."""

    page = await _page(test_context, "sub-safe@example.com")

    assert "Hilal Markets never sees them" in page


async def test_the_checkout_popup_is_a_real_dialog(test_context):
    """Rule D5: focus trapped, Escape closes, focus returns. A real <dialog> opened with
    showModal() is what the browser gives for free; a div cannot."""

    page = await _page(test_context, "sub-dialog@example.com")
    document = lxml.html.fromstring(page)

    assert document.xpath("//dialog[@data-s-dialog]")


async def test_payment_history_says_what_happened_in_words(test_context):
    """Finding 6: the live page printed the stored status with underscores swapped for
    spaces — "Provider Unavailable" — which is a machine's word for something that
    happened to a person's money."""

    page = await _page(test_context, "sub-history@example.com")
    words = _words(page)

    assert "Provider Unavailable" not in words
    # With no payments yet, the empty state has to be a sentence rather than a blank.
    assert "You have not paid for anything yet" in words


@pytest.mark.parametrize(
    "jargon",
    [
        "Access source",
        "Billing interval",
        "billing cycle",
        "Provider Unavailable",
        "entitlement",
        "Secure checkout",
        "Complete your billing details",
    ],
)
async def test_no_word_from_inside_the_machine_reaches_the_page(test_context, jargon):
    """Rule E2. The live page said all of these to a beginner."""

    page = await _page(test_context, f"sub-plain-{abs(hash(jargon))}@example.com")
    assert jargon.lower() not in _words(page).lower(), jargon


@pytest.mark.parametrize(
    "claim",
    ["100% halal", "guaranteed", "risk-free", "buy now", "AI trades for you"],
)
async def test_no_forbidden_claim_reaches_the_page(test_context, claim):
    """`brand guide.md` section 17, enforced rather than reviewed."""

    page = await _page(test_context, f"sub-claim-{abs(hash(claim))}@example.com")
    assert claim.lower() not in _words(page).lower(), claim


async def test_nothing_on_the_page_implies_a_plan_buys_a_trading_outcome(test_context):
    """Rule G9 and F2."""

    page = await _page(test_context, "sub-outcome@example.com")
    words = _words(page).lower()

    for phrase in ["profit", "returns", "winning", "trades for you"]:
        assert phrase not in words, phrase


async def test_the_checkout_never_opens_over_a_plan_that_has_no_button(test_context):
    """Arriving with `?plan=trader` opens the popup only when that plan really carries a
    button on its own card.

    "This account does not already hold the plan" is a different question from "this
    plan can be bought right now", and it stays true while paid checkout is switched off
    altogether. Asking the first one would open a paying popup on a page that offers no
    way to press it.
    """

    await _signup_and_verify(test_context, email="sub-autoopen@example.com")
    response = await test_context["client"].get(f"{SUBSCRIPTION}?plan=trader")
    assert response.status_code == 200
    document = lxml.html.fromstring(response.text)

    (root,) = document.xpath("//div[@data-subscription-root]")
    buyable = document.xpath('//li[@data-plan="trader"]//button[@data-s-choose]')
    assert bool(root.get("data-open-for")) == bool(buyable)


@pytest.mark.parametrize("junk", ["nonsense", "demo", ""])
async def test_a_plan_nobody_named_never_opens_the_checkout(test_context, junk):
    await _signup_and_verify(test_context, email=f"sub-junk-{abs(hash(junk))}@example.com")
    response = await test_context["client"].get(f"{SUBSCRIPTION}?plan={junk}")

    document = lxml.html.fromstring(response.text)
    (root,) = document.xpath("//div[@data-subscription-root]")
    assert not root.get("data-open-for")


async def test_no_passport_popup_is_on_a_page_with_no_coin_on_it(test_context):
    page = await _page(test_context, "sub-popup@example.com")

    assert "data-passport-quick-view-dialog" not in page


async def test_the_live_page_still_works(test_context):
    """The design path is a parallel copy. `/dashboard/subscription` is not changed."""

    await _signup_and_verify(test_context, email="sub-live@example.com")
    response = await test_context["client"].get("/dashboard/subscription")
    assert response.status_code == 200


# ── Ways of paying ───────────────────────────────────────────────────────────────
#
# The popup drew "Card" and "Crypto" side by side whatever the settings said. Card was
# switched off on the live site, so anybody who chose it filled in three steps of a form,
# pressed pay, and read "That payment method is not currently available. Nothing was
# charged." — a dead end with nothing to try next.


def _crypto_only(test_context):
    """The live shape on 1 September 2026: crypto open, card switched off."""

    settings = test_context["settings"].model_copy(
        update={
            "billing_enabled": True,
            "billing_provider": "nowpayments",
            "billing_card_provider": "disabled",
            "billing_crypto_provider": "nowpayments",
            "nowpayments_api_key": SecretStr("nowpayments-key"),
            "nowpayments_ipn_secret": SecretStr("nowpayments-ipn-secret"),
        }
    )
    test_context["app"].dependency_overrides[get_settings] = lambda: settings
    return settings


async def test_a_way_of_paying_that_is_switched_off_is_drawn_switched_off(test_context):
    await _signup_and_verify(test_context, email="sub-methods-off@example.com")
    _crypto_only(test_context)

    response = await test_context["client"].get(SUBSCRIPTION)
    assert response.status_code == 200
    document = lxml.html.fromstring(response.text)

    (card,) = document.xpath('//label[@data-s-method="card"]//input')
    (crypto,) = document.xpath('//label[@data-s-method="crypto"]//input')
    assert card.get("disabled") is not None, "card was offered while it was switched off"
    assert crypto.get("disabled") is None, "crypto was refused while it was open"

    (card_note,) = document.xpath('//label[@data-s-method="card"]//small')
    (crypto_note,) = document.xpath('//label[@data-s-method="crypto"]//small')
    assert card_note.text_content().strip() == "Paying by card is switched off just now."
    assert crypto_note.text_content().strip() == "Handled by NOWPayments."


async def test_each_plan_carries_the_ways_of_paying_that_really_work_for_it(test_context):
    """The popup is one popup for every plan, so the answer travels with the plan."""

    await _signup_and_verify(test_context, email="sub-methods-facts@example.com")
    _crypto_only(test_context)

    response = await test_context["client"].get(SUBSCRIPTION)
    document = lxml.html.fromstring(response.text)
    (facts_node,) = document.xpath('//script[@id="s-plan-facts"]')
    cards = json.loads(facts_node.text_content())

    for card in cards:
        methods = card["pay_methods"]
        assert methods["card"]["available"] is False
        # Only a plan that is really on sale can be paid for at all.
        assert methods["crypto"]["available"] is (card["code"] == "trader")
        # A plan that offers a Pay button must have some way of paying behind it.
        if card["buyable"]:
            assert any(choice["available"] for choice in methods.values()), card["code"]


async def test_choosing_a_switched_off_way_of_paying_says_what_to_use_instead(test_context):
    """The reported failure, end to end.

    A form posted with `payment_method=card` — an old tab, a saved page, or the popup
    before this fix — is refused with a sentence that names the way of paying that works.
    """

    await _signup_and_verify(test_context, email="sub-methods-post@example.com")
    _crypto_only(test_context)

    page = await test_context["client"].get(SUBSCRIPTION)
    csrf = re.search(r'name="csrf_token" value="([a-f0-9]+)"', page.text)
    request_id = re.search(r'name="checkout_request_id" value="([a-f0-9]+)"', page.text)
    assert csrf is not None and request_id is not None

    refused = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data={
            "plan_code": "trader",
            "billing_cycle": "monthly",
            "payment_method": "card",
            "checkout_request_id": request_id.group(1),
            "terms_accepted": "true",
            "first_name": "Card",
            "last_name": "Buyer",
            "address_line1": "1 Market Street",
            "city": "Cairo",
            "country": "Egypt",
            "csrf_token": csrf.group(1),
        },
        headers={"accept": "application/json", "x-requested-with": "XMLHttpRequest"},
    )

    assert refused.status_code == 400
    body = refused.json()
    assert body["error"]["code"] == "payment_method_unavailable"
    assert body["error"]["message"] == (
        "Paying by card is switched off just now. You can pay with crypto instead."
    )


async def test_the_way_of_paying_that_is_open_can_still_be_chosen(test_context):
    """The fix must not close the door that works."""

    await _signup_and_verify(test_context, email="sub-methods-crypto@example.com")
    _crypto_only(test_context)

    page = await test_context["client"].get(SUBSCRIPTION)
    csrf = re.search(r'name="csrf_token" value="([a-f0-9]+)"', page.text)
    request_id = re.search(r'name="checkout_request_id" value="([a-f0-9]+)"', page.text)
    assert csrf is not None and request_id is not None

    accepted = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data={
            "plan_code": "trader",
            "billing_cycle": "monthly",
            "payment_method": "crypto",
            "checkout_request_id": request_id.group(1),
            "terms_accepted": "true",
            "first_name": "Crypto",
            "last_name": "Buyer",
            "address_line1": "1 Market Street",
            "city": "Cairo",
            "country": "Egypt",
            "csrf_token": csrf.group(1),
        },
        headers={"accept": "application/json", "x-requested-with": "XMLHttpRequest"},
    )

    # The payment company itself is not reachable from a test, so the only thing proved
    # here is that this way of paying was never refused as unavailable.
    assert accepted.status_code in {200, 400}
    if accepted.status_code == 400:
        assert accepted.json()["error"]["code"] != "payment_method_unavailable"


def _both_ways_open(test_context):
    """Card through Creem and crypto through NOWPayments — the shape on sale today."""

    settings = test_context["settings"].model_copy(
        update={
            "billing_enabled": True,
            "billing_provider": "nowpayments",
            "billing_card_provider": "creem",
            "billing_crypto_provider": "nowpayments",
            "creem_api_key": SecretStr("creem-key"),
            "creem_webhook_secret": SecretStr("creem-webhook-secret"),
            "creem_product_ids": {"trader_monthly": "prod_trader_monthly"},
            "nowpayments_api_key": SecretStr("nowpayments-key"),
            "nowpayments_ipn_secret": SecretStr("nowpayments-ipn-secret"),
        }
    )
    test_context["app"].dependency_overrides[get_settings] = lambda: settings
    return settings


async def test_each_way_of_paying_names_the_company_that_will_take_the_money(test_context):
    """The mark under each choice, on the real page, for the real settings.

    Somebody about to type a card number is told whose page they are going to, and can
    open that company before they do. The name is never written into the page: it is read
    from the settings the server is running on.
    """

    await _signup_and_verify(test_context, email="sub-methods-secured@example.com")
    _both_ways_open(test_context)

    response = await test_context["client"].get(SUBSCRIPTION)
    assert response.status_code == 200
    document = lxml.html.fromstring(response.text)

    for method, company, site in (
        ("card", "Creem", "https://www.creem.io/"),
        ("crypto", "NOWPayments", "https://nowpayments.io/"),
    ):
        (mark,) = document.xpath(
            f'//div[label[@data-s-method="{method}"]]/a[@class="hm-pay-secured"]'
        )
        assert mark.get("href") == site
        assert mark.get("target") == "_blank"
        assert "noopener" in (mark.get("rel") or "")
        words = " ".join(mark.text_content().split())
        assert words == f"Payments secured by {company}"


async def test_a_switched_off_way_of_paying_names_no_company(test_context):
    """No company can take the money, so no company is named beside it."""

    await _signup_and_verify(test_context, email="sub-methods-unsecured@example.com")
    _crypto_only(test_context)

    response = await test_context["client"].get(SUBSCRIPTION)
    document = lxml.html.fromstring(response.text)

    card_marks = document.xpath(
        '//div[label[@data-s-method="card"]]/a[@class="hm-pay-secured"]'
    )
    crypto_marks = document.xpath(
        '//div[label[@data-s-method="crypto"]]/a[@class="hm-pay-secured"]'
    )
    assert card_marks == []
    assert len(crypto_marks) == 1
