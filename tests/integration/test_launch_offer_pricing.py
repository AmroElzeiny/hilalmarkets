"""The launch offer, rendered. Landing page, public pricing page and dashboard.

Three surfaces show prices. Three surfaces is three chances to disagree, so each rule is
asserted on every one of them: the same struck-out price, the same new price, the same
"Soon", the same deadline.
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
    annual_saving,
    effective_monthly_price,
    maximum_annual_saving,
    original_monthly_price,
    plan_offer,
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

    by_code = {plan["code"]: plan for plan in commerce["plans"]}
    monitor = by_code["trader"]
    assert monitor["monthlyPrice"] == float(effective_monthly_price("trader"))
    assert monitor["originalMonthlyPrice"] == float(original_monthly_price("trader"))
    assert monitor["monthlyAvailable"] is True

    assert by_code["pro"]["monthlyAvailable"] is False
    assert by_code["pro"]["originalMonthlyPrice"] is None
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
    # The old price is crossed out and the new one stands next to it.
    assert '<s class="price-original"' in body
    assert f"${int(original_monthly_price('trader'))}" in body
    assert f"<strong>${int(effective_monthly_price('trader'))}</strong>" in body
    # The countdown is rendered with the server's own deadline.
    assert f'data-offer-countdown="{PROMOTION_ENDS_AT.isoformat()}"' in body


@pytest.mark.anyio
async def test_the_public_pricing_page_says_soon_without_a_price(
    test_context: dict,
) -> None:
    """A number beside "Soon" reads as a charge the visitor is about to face."""

    response = await test_context["client"].get("/pricing")
    body = response.text
    assert COMING_SOON_LABEL in body
    assert "is coming soon" in body
    # The Pro monthly price must not appear anywhere on the page.
    from ai_market_monitor.core.plans import PLAN_DEFINITIONS

    assert f"<strong>${int(PLAN_DEFINITIONS['pro'].monthly_price)}</strong>" not in body


@pytest.mark.anyio
async def test_the_pricing_page_offers_no_annual_checkout(test_context: dict) -> None:
    response = await test_context["client"].get("/pricing")
    body = response.text
    assert "billing_interval=annual" not in body
    assert "Annual billing: soon." in body


async def _signup(test_context: dict, email: str) -> None:
    response = await test_context["client"].post(
        "/signup",
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
    assert f"${int(original_monthly_price('trader'))}" in body
    assert f"${int(effective_monthly_price('trader'))}" in body
    assert 'class="price-original"' in body
    assert f'data-offer-countdown="{PROMOTION_ENDS_AT.isoformat()}"' in body
    # No price anywhere for a plan nobody can buy yet.
    from ai_market_monitor.core.plans import PLAN_DEFINITIONS

    assert f"${int(PLAN_DEFINITIONS['pro'].monthly_price)}" not in body
    assert "Pro is coming soon" in body


@pytest.mark.anyio
async def test_every_pricing_surface_agrees_on_what_is_for_sale() -> None:
    """One definition, so the three surfaces cannot drift apart."""

    assert plan_offer("trader").monthly_available is True
    assert plan_offer("pro").monthly_available is False
    assert all(not plan_offer(code).annual_available for code in PUBLIC_PLAN_CODES)


def test_the_annual_saving_is_computed_from_the_prices_beside_it() -> None:
    """A saving typed out by hand survives a price change and starts lying."""

    presentation = PUBLIC_PLAN_PRESENTATIONS["trader"]
    expected = (effective_monthly_price("trader") * 12) - presentation.annual_price
    assert annual_saving("trader") == max(expected, Decimal("0.00"))
    # Nothing is on annual sale yet, so there is no saving anyone can buy.
    assert maximum_annual_saving() == Decimal("0.00")
