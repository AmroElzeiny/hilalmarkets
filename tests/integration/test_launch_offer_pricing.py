"""The launch offer, rendered. Landing page, public pricing page and dashboard.

Three surfaces show prices. Three surfaces is three chances to disagree, so each rule is
asserted on every one of them, and on **every plan that is on sale** rather than on one
named plan: the same struck-out price, the same new price, the same deadline.

The offer needs no code typed in. It used to: a customer had to enter ``HILAL25`` to reach
the lower price, so every card carried a code beside the crossed-out figure. Now the lower
price simply *is* the price until the deadline, which means one rule covers the card, the
checkout and the crypto invoice — and there is no code left to leak into a page.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from ai_market_monitor.core.plans import (
    COMING_SOON_LABEL,
    PROMOTION_ENDS_AT,
    PUBLIC_PLAN_CODES,
    PUBLIC_PLAN_PRESENTATIONS,
    PURCHASABLE_PLAN_CODES,
    RETIRED_DISCOUNT_CODES,
    annual_saving,
    effective_monthly_price,
    maximum_annual_saving,
    original_monthly_price,
    plan_offer,
    promotion_is_active,
)
from tests.support.billing_config import configure_live_billing


def headline_price(code: str) -> Decimal:
    """The number a pricing card puts in large type.

    The launch price while the offer runs, the normal price once it has ended. One call,
    because that is also the number checkout charges and the number the crypto invoice is
    written for.
    """

    return effective_monthly_price(code)


@pytest.fixture(autouse=True)
def _open_for_business(test_context: dict) -> None:
    """Every test here describes the site once the product is open for sale.

    Two switches, not one. Turning waitlist mode off puts the pricing page and the plan
    cards back on the site; configuring the payment companies is what makes the cards say
    a price rather than "coming soon". A test that only did the first was reading a server
    that could not have taken a single payment.
    """

    test_context["settings"].public_waitlist_mode = False
    configure_live_billing(test_context["settings"])


def _struck_price_marks(body: str) -> tuple[bool, bool]:
    """Whether the page drew a crossed-out price and a countdown.

    The pages render against the real clock, so these tests must describe both states:
    while an offer runs, and after it has ended. They used to describe only the first,
    which made the day an offer expired the day three tests broke — and a test that
    breaks on a date says nothing about whether the page is right.
    """

    return (
        '<s class="price-original"' in body or 'class="price-original"' in body,
        "data-offer-countdown=" in body,
    )


def _runtime_commerce(html: str) -> dict:
    """The commerce block the landing page hands to the React app."""

    match = re.search(
        r"window\.HilalMarketsRuntimeConfig = (\{.*?\});", html, re.DOTALL
    )
    assert match, "the landing page did not publish a runtime config"
    return json.loads(match.group(1))["commerce"]


@pytest.mark.anyio
async def test_the_landing_page_still_lists_every_plan(test_context: dict) -> None:
    """Checkout being switched off changes the button, never whether a price is shown."""

    response = await test_context["client"].get("/")
    assert response.status_code == 200
    commerce = _runtime_commerce(response.text)
    assert [plan["code"] for plan in commerce["plans"]] == list(PUBLIC_PLAN_CODES)
    # And the comparison table keeps one column per plan, so the React table's
    # four-item rows still destructure.
    assert all(len(row) == 4 for row in commerce["comparisonRows"])


@pytest.mark.anyio
async def test_the_landing_page_carries_the_offer_and_its_deadline(
    test_context: dict,
) -> None:
    response = await test_context["client"].get("/")
    commerce = _runtime_commerce(response.text)
    assert commerce["promotionEndsAt"] == PROMOTION_ENDS_AT.isoformat()

    assert commerce["promotionActive"] is promotion_is_active()

    by_code = {plan["code"]: plan for plan in commerce["plans"]}
    for code in PURCHASABLE_PLAN_CODES:
        plan = by_code[code]
        assert plan["monthlyPrice"] == float(headline_price(code)), code
        # What a checkout charges travels with the card, so the card never works the
        # figure out for itself.
        assert plan["fullMonthlyPrice"] == float(effective_monthly_price(code)), code
        was = original_monthly_price(code)
        # While the offer runs there is a price to cross out; once it ends there is not,
        # and the page must carry nothing rather than an old number.
        assert plan["originalMonthlyPrice"] == (float(was) if was is not None else None)
        assert (was is not None) is promotion_is_active(), code
        assert plan["monthlyAvailable"] is True, code
        # Nothing has to be typed to reach the price, so no code may travel with a plan.
        assert "discountCode" not in plan, code
        assert "discountPercent" not in plan, code

    for plan in commerce["plans"]:
        assert plan["annualAvailable"] is False, plan["code"]
        assert plan["comingSoonLabel"] == COMING_SOON_LABEL


@pytest.mark.anyio
async def test_the_public_pricing_page_shows_the_struck_price_and_the_timer(
    test_context: dict,
) -> None:
    response = await test_context["client"].get("/pricing")
    assert response.status_code == 200
    body = response.text
    for code in PURCHASABLE_PLAN_CODES:
        # Today's price always stands on the card.
        assert f"<strong>${int(headline_price(code))}</strong>" in body, code
    struck, countdown = _struck_price_marks(body)
    was = original_monthly_price("trader")
    if promotion_is_active():
        # The old price is crossed out and the new one stands next to it.
        assert struck and was is not None
        for code in PURCHASABLE_PLAN_CODES:
            assert f"${int(original_monthly_price(code))}" in body, code
        # The countdown is rendered with the server's own deadline.
        assert f'data-offer-countdown="{PROMOTION_ENDS_AT.isoformat()}"' in body
    else:
        # An offer that ended leaves no trace: no crossed-out price and no timer.
        assert not struck and not countdown and was is None
    # No withdrawn code may appear on a page, in either state. A code a customer types
    # and the checkout refuses is worse than no code at all.
    for retired in RETIRED_DISCOUNT_CODES:
        assert retired not in body


@pytest.mark.anyio
async def test_every_plan_on_sale_shows_a_price_rather_than_soon(
    test_context: dict,
) -> None:
    """A plan somebody can buy must show what it costs.

    Pro carried "Soon" and no price for months after its product existed in Creem. The
    rule is the general one: the word and the price are decided by the same offer.
    """

    response = await test_context["client"].get("/pricing")
    body = response.text
    for code in PURCHASABLE_PLAN_CODES:
        assert f"<strong>${int(headline_price(code))}</strong>" in body, code
        assert f"{PUBLIC_PLAN_PRESENTATIONS[code].cta_label}" in body, code
    assert "is coming soon" not in body


@pytest.mark.anyio
async def test_the_pricing_page_offers_no_annual_checkout(test_context: dict) -> None:
    response = await test_context["client"].get("/pricing")
    body = response.text
    assert "billing_interval=annual" not in body
    assert "Annual billing: soon." in body


async def _signup(test_context: dict, email: str) -> None:
    response = await test_context["client"].post(
        "/signup/password",
        data={
            "email": email,
            "display_name": "Launch Offer Test",
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


@pytest.mark.anyio
async def test_the_dashboard_shows_the_same_offer_as_the_public_page(
    test_context: dict,
) -> None:
    """Signed in or signed out, the price and the deadline are the same numbers."""

    await _signup(test_context, "launch-offer@example.com")
    response = await test_context["client"].get("/dashboard/billing")
    assert response.status_code == 200
    body = response.text
    for code in PURCHASABLE_PLAN_CODES:
        assert f"${int(headline_price(code))}" in body, code
    struck, countdown = _struck_price_marks(body)
    was = original_monthly_price("trader")
    if promotion_is_active():
        assert struck and was is not None
        for code in PURCHASABLE_PLAN_CODES:
            assert f"${int(original_monthly_price(code))}" in body, code
        assert f'data-offer-countdown="{PROMOTION_ENDS_AT.isoformat()}"' in body
    else:
        assert not struck and not countdown and was is None
    for retired in RETIRED_DISCOUNT_CODES:
        assert retired not in body


@pytest.mark.anyio
async def test_every_pricing_surface_agrees_on_what_is_for_sale() -> None:
    """One definition, so the three surfaces cannot drift apart."""

    for code in PURCHASABLE_PLAN_CODES:
        assert plan_offer(code).monthly_available is True, code
    assert all(not plan_offer(code).annual_available for code in PUBLIC_PLAN_CODES)


def test_the_annual_saving_is_computed_from_the_prices_beside_it() -> None:
    """A saving typed out by hand survives a price change and starts lying.

    Measured against what somebody really pays month by month, which is the launch price
    while the offer runs. Comparing a year against the normal monthly price would
    advertise a saving nobody can actually get.
    """

    for code in PURCHASABLE_PLAN_CODES:
        presentation = PUBLIC_PLAN_PRESENTATIONS[code]
        expected = (headline_price(code) * 12) - presentation.annual_price
        assert annual_saving(code) == max(expected, Decimal("0.00")), code
    # Nothing is on annual sale yet, so there is no saving anyone can buy.
    assert maximum_annual_saving() == Decimal("0.00")
