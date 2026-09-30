"""The HILAL30 offer, price printing, the Back button, and card-price drift.

Each test asserts a rule for a whole family, not the case that was reported:

* every paid plan's offer price is the one percentage in ``CURRENT_OFFER`` — no plan is
  discounted by a hand-written figure;
* an offer is only valid when it can be represented (code shape, percentage range, a time
  zone on its end);
* every template prints money through ``| usd`` — ``| int`` cut $17.50 down to $17;
* every countdown a template draws is guarded by a real deadline — a countdown with no
  date hides the crossed-out price on an offer still being charged;
* every dashboard page reloads when the browser restores it from memory — a restored
  page kept the payment button locked, so "Go to the card payment page" did nothing;
* every configured Creem product is compared with the website's price, and the running
  offer's code must not be an active Creem discount;
* "same website" means the same host with or without ``www.``, and nothing looser.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core import plans
from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.money import display_usd
from ai_market_monitor.core.official_hosts import same_official_host, site_host
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PUBLIC_PLAN_CODES,
    PURCHASABLE_PLAN_CODES,
    PriceOffer,
    current_offer,
    effective_monthly_price,
    plan_offer,
    plan_offer_payload,
    price_after_percent,
    promotion_ends_at,
)
from ai_market_monitor.services.card_price_check import (
    check_card_prices,
    expected_card_price,
    offer_code_problem,
)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "ai_market_monitor" / "templates"
STATIC = ROOT / "src" / "ai_market_monitor" / "static"


# ---------------------------------------------------------------------------
# The offer itself.
# ---------------------------------------------------------------------------


def test_the_running_offer_is_hilal30_at_thirty_percent_with_no_end() -> None:
    """The owner's decision of 28 September 2026, written down once."""

    offer = current_offer()
    assert offer is not None
    assert offer.code == "HILAL30"
    assert offer.percent == Decimal("30")
    assert offer.ends_at is None
    assert promotion_ends_at() is None


@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
def test_every_paid_plan_costs_the_offer_percentage_off_its_normal_price(code: str) -> None:
    offer = current_offer()
    assert offer is not None
    normal = PLAN_DEFINITIONS[code].monthly_price
    assert plan_offer(code).takes_offer is True
    assert effective_monthly_price(code) == price_after_percent(normal, offer.percent)
    payload = plan_offer_payload(code)
    assert payload["offerCode"] == offer.code
    assert payload["offerPercent"] == 30
    assert payload["originalMonthlyPrice"] == normal
    assert payload["promotionEndsAt"] is None


def test_the_owners_numbers() -> None:
    """$15 → $10.50 and $25 → $17.50, stated plainly so a changed rounding rule shows."""

    assert effective_monthly_price("trader") == Decimal("10.50")
    assert effective_monthly_price("pro") == Decimal("17.50")


@pytest.mark.parametrize("code", sorted(set(PUBLIC_PLAN_CODES) - set(PURCHASABLE_PLAN_CODES)))
def test_a_free_plan_carries_no_offer(code: str) -> None:
    payload = plan_offer_payload(code)
    assert payload["offerCode"] is None
    assert payload["originalMonthlyPrice"] is None


@pytest.mark.parametrize("code", ["", "H", "hilal 30", "HILAL30!", "A" * 41])
def test_an_offer_code_that_is_not_code_shaped_is_refused(code: str) -> None:
    with pytest.raises(ValueError):
        PriceOffer(code=code, percent=Decimal("30"))


@pytest.mark.parametrize("percent", ["0", "-5", "100", "150"])
def test_an_offer_percentage_outside_the_open_range_is_refused(percent: str) -> None:
    with pytest.raises(ValueError):
        PriceOffer(code="HILAL30", percent=Decimal(percent))


def test_an_offer_end_without_a_time_zone_is_refused() -> None:
    with pytest.raises(ValueError):
        PriceOffer(code="HILAL30", percent=Decimal("30"), ends_at=datetime(2031, 1, 1))


def test_no_offer_means_normal_prices_and_nothing_crossed_out() -> None:
    from tests.offer_support import installed_offer

    with installed_offer(None):
        for code in PURCHASABLE_PLAN_CODES:
            payload = plan_offer_payload(code)
            assert effective_monthly_price(code) == PLAN_DEFINITIONS[code].monthly_price
            assert payload["originalMonthlyPrice"] is None
            assert payload["offerCode"] is None
            assert payload["promotionRunning"] is False


# ---------------------------------------------------------------------------
# Printing money.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "shown"),
    [
        (Decimal("15.00"), "$15"),
        (Decimal("17.50"), "$17.50"),
        (Decimal("10.5"), "$10.50"),
        (Decimal("0"), "$0"),
        (Decimal("0.99"), "$0.99"),
        (Decimal("9.995"), "$10"),
        (Decimal("220"), "$220"),
        (25, "$25"),
        ("17.50", "$17.50"),
    ],
)
def test_display_usd_prints_cents_only_when_there_are_cents(amount: object, shown: str) -> None:
    assert display_usd(amount) == shown


@pytest.mark.parametrize("value", ["Soon", None, "NaN"])
def test_display_usd_never_invents_a_price(value: object) -> None:
    assert display_usd(value) == str(value)


def _templates() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


#: A money-looking expression piped through `int`: `offer.monthlyPrice | int`,
#: `plan.was_price | int`, `(x * 12 - y) | int` next to a price, and so on.
_MONEY_THROUGH_INT = re.compile(
    r"(?i)(price|amount|saving|annual|monthly|was_)[^{}|]*\|\s*int\b"
)


@pytest.mark.parametrize("template", _templates(), ids=lambda path: path.name)
def test_no_template_prints_money_through_int(template: Path) -> None:
    """`| int` turns $17.50 into $17. Money goes through `| usd`."""

    text = template.read_text(encoding="utf-8")
    offenders = [match.group(0) for match in _MONEY_THROUGH_INT.finditer(text)]
    assert not offenders, offenders


@pytest.mark.parametrize("template", _templates(), ids=lambda path: path.name)
def test_every_countdown_is_drawn_only_for_a_real_deadline(template: Path) -> None:
    """A countdown element must sit inside an `{% if %}` that names the deadline.

    The countdown script treats a missing date as "already over" and hides the
    crossed-out price, so an unguarded countdown on an offer with no end date would erase
    the very discount the page is meant to show.
    """

    lines = template.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if "data-offer-countdown=" not in line:
            continue
        above = "\n".join(lines[max(0, index - 4) : index])
        assert re.search(r"{%-?\s*if[^%]*(ends_at|EndsAt)", above), (
            f"{template.name}:{index + 1} draws a countdown without checking for a deadline"
        )


def test_the_offer_note_names_the_code_and_asks_nobody_to_type_it() -> None:
    note = (TEMPLATES / "hilal" / "partials" / "offer_code_note.html").read_text(
        encoding="utf-8"
    )
    assert "hm-code-chip" in note
    assert "offer.offerCode" in note
    assert "You do not need to type it" in note


def test_the_offer_chip_says_it_is_applied_automatically_on_every_surface() -> None:
    """The dashboard partial and the landing React card both say it, in the same words."""
    note = (TEMPLATES / "hilal" / "partials" / "offer_code_note.html").read_text(
        encoding="utf-8"
    )
    landing = (
        Path(__file__).resolve().parents[2]
        / "Hilal-Markets-Website" / "src" / "components" / "Pricing.tsx"
    ).read_text(encoding="utf-8")
    for source in (note, landing):
        assert "price-code-auto" in source
        assert "(Applied Automatically)" in source


# ---------------------------------------------------------------------------
# The Back button.
# ---------------------------------------------------------------------------


def test_a_dashboard_page_restored_by_back_is_loaded_fresh() -> None:
    """The fix for "Go to the card payment page" doing nothing after Back.

    Loaded from the one base every dashboard page extends, before any page script, and
    it reloads exactly when the browser says the page came back from memory.
    """

    base = (TEMPLATES / "hilal" / "base_dashboard.html").read_text(encoding="utf-8")
    request_at = base.index("/hm-request.js")
    restored_at = base.index("/hm-restored-page.js")
    assert request_at < restored_at
    first_other_script = min(
        base.index(marker, restored_at + 1)
        for marker in ("<script type=\"module\"", "hilalmarkets-icons.js")
        if marker in base[restored_at + 1 :]
    )
    assert restored_at < first_other_script
    script = (STATIC / "hm-restored-page.js").read_text(encoding="utf-8")
    assert '"pageshow"' in script
    assert "event.persisted" in script
    assert "location.reload()" in script


@pytest.mark.parametrize(
    "template",
    [
        TEMPLATES / "hilal" / "dashboard" / "billing.html",
        TEMPLATES / "hilal" / "dashboard" / "checkout.html",
        TEMPLATES / "hilal" / "dashboard_test" / "subscription.html",
    ],
    ids=lambda path: path.name,
)
def test_every_page_with_a_payment_button_gets_the_back_button_fix(template: Path) -> None:
    text = template.read_text(encoding="utf-8")
    assert '{% extends "hilal/base_dashboard.html" %}' in text


def test_a_second_press_on_pay_says_something() -> None:
    """The popup's busy guard tells the person what is happening instead of staying silent."""

    script = (STATIC / "hm-subscription-test.js").read_text(encoding="utf-8")
    busy_guard = script[script.index('pay?.dataset.busy === "true"') :][:300]
    assert "say(" in busy_guard


# ---------------------------------------------------------------------------
# Card prices at Creem.
# ---------------------------------------------------------------------------


def _creem_settings(products: dict[str, str]) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        creem_api_key=SecretStr("test-key"),
        creem_product_ids=products,
    )


def _creem_transport(
    prices: dict[str, int], *, discount_status: str | None = None, fail: bool = False
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503)
        if request.url.path.endswith("/v1/products"):
            product_id = request.url.params["product_id"]
            return httpx.Response(
                200,
                json={
                    "price": prices[product_id],
                    "currency": "USD",
                    "status": "active",
                    "mode": "prod",
                },
            )
        if request.url.path.endswith("/v1/discounts"):
            if discount_status is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"status": discount_status})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
def test_the_expected_card_price_is_what_a_checkout_charges(code: str) -> None:
    assert expected_card_price(f"{code}_monthly") == effective_monthly_price(code)


@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
@pytest.mark.anyio
async def test_a_creem_price_that_matches_is_not_a_problem(code: str) -> None:
    cents = int(effective_monthly_price(code) * 100)
    settings = _creem_settings({f"{code}_monthly": "prod_x"})
    async with httpx.AsyncClient(transport=_creem_transport({"prod_x": cents})) as client:
        [result] = await check_card_prices(settings, client)
    assert result.problem is None


@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
@pytest.mark.parametrize("off_by_cents", [-50, -1, 1, 800])
@pytest.mark.anyio
async def test_a_creem_price_that_differs_is_reported(code: str, off_by_cents: int) -> None:
    """The exact failure of 20 September 2026: Creem $17, the website another figure."""

    cents = int(effective_monthly_price(code) * 100) + off_by_cents
    settings = _creem_settings({f"{code}_monthly": "prod_x"})
    async with httpx.AsyncClient(transport=_creem_transport({"prod_x": cents})) as client:
        [result] = await check_card_prices(settings, client)
    assert result.problem is not None
    assert "Change the price of this product in Creem" in result.problem


@pytest.mark.anyio
async def test_an_unreachable_creem_is_reported_not_passed() -> None:
    settings = _creem_settings({"pro_monthly": "prod_x"})
    async with httpx.AsyncClient(transport=_creem_transport({}, fail=True)) as client:
        [result] = await check_card_prices(settings, client)
    assert result.problem is not None


@pytest.mark.parametrize(
    ("status", "is_problem"),
    [(None, False), ("inactive", False), ("expired", False), ("active", True)],
)
@pytest.mark.anyio
async def test_the_offer_code_must_not_be_an_active_creem_discount(
    status: str | None, is_problem: bool
) -> None:
    settings = _creem_settings({"pro_monthly": "prod_x"})
    transport = _creem_transport({}, discount_status=status)
    async with httpx.AsyncClient(transport=transport) as client:
        problem = await offer_code_problem(settings, client)
    assert (problem is not None) is is_problem


def test_the_card_price_check_runs_every_day() -> None:
    from ai_market_monitor.worker import app

    schedule = app.conf.beat_schedule
    entry = schedule["check-card-prices-daily"]
    assert entry["task"] == "ai_market_monitor.check_card_prices"
    assert entry["schedule"] == 24 * 60 * 60
    assert "ai_market_monitor.check_card_prices" in app.tasks


# ---------------------------------------------------------------------------
# Same website.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "final"),
    [
        ("https://www.fasset.com/shariah-reports", "https://fasset.com/shariah-reports/"),
        ("https://fasset.com/shariah-reports", "https://www.fasset.com/shariah-reports/"),
        ("https://WWW.Fasset.com/a", "https://fasset.com/b"),
        ("https://www.sc.com.my/x", "https://www.sc.com.my/y"),
    ],
)
def test_the_same_site_with_or_without_www_is_the_same_official_host(
    configured: str, final: str
) -> None:
    assert same_official_host(configured, final) is True


@pytest.mark.parametrize(
    ("configured", "final"),
    [
        ("https://www.fasset.com/a", "https://fasset.com.evil.io/a"),
        ("https://www.fasset.com/a", "https://evilfasset.com/a"),
        ("https://www.fasset.com/a", "https://blog.fasset.com/a"),
        ("https://www.fasset.com/a", "https://www.www.fasset.com/a"),
        ("https://www.fasset.com/a", "not a url"),
        ("", "https://fasset.com/a"),
    ],
)
def test_anything_else_is_a_different_site(configured: str, final: str) -> None:
    assert same_official_host(configured, final) is False


@pytest.mark.parametrize(
    ("host", "site"),
    [("www.Ethereum.org", "ethereum.org"), ("ethereum.org", "ethereum.org"), (None, "")],
)
def test_site_host_is_the_one_www_rule(host: str | None, site: str) -> None:
    assert site_host(host) == site


def test_offer_payload_survives_the_page_json_writer() -> None:
    """The offer fields reach a page as plain JSON: a string code and a whole number."""

    from ai_market_monitor.core.money import money_json_dumps

    written = json.loads(money_json_dumps(plan_offer_payload("pro")))
    assert written["offerCode"] == "HILAL30"
    assert written["offerPercent"] == 30
    assert written["promotionEndsAt"] is None


def test_plans_module_keeps_one_offer_owner() -> None:
    """Nothing else in `core/plans.py` hand-writes a discounted price."""

    source = Path(plans.__file__).read_text(encoding="utf-8")
    assert "promotional_monthly_price=" not in source
