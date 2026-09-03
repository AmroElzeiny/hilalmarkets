"""The company named on a checkout screen is the company that will take the money.

Three screens ask "card or crypto": the plan popup on `/dashboard/billing`, the review
page on `/dashboard/billing/checkout`, and the "How you pay" step on
`/dashboard/subscription`. Each now shows a mark under the choice — *Payments secured by
Creem* — and the rule this file holds is the general one, not that one company:

    for every way of paying, on every screen that offers it,
    the company named is the company the server is really configured to use.

That was already broken before the mark existed. One page told everybody their card
details go to "Creem or NOWPayments" whatever the settings said, the popup's own button
promised "Continue to Creem" the same way, and three more places each kept their own list
turning `creem` into "Creem". A name written into a page is a name that keeps being shown
after the setting behind it has moved.

So the name has one owner, `services/billing.py`, and every screen reads it from there.
The tests below run the real offer builder and the real template environment.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.templating import Jinja2Templates

from ai_market_monitor.api.template_env import register
from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.billing import (
    PAYMENT_METHODS,
    billing_method_provider,
    payment_method_offers_by_method,
    payment_method_payload,
    provider_method,
    provider_site,
    provider_word,
)
from tests.unit.test_invariant_payment_methods import SETTINGS_CASES

TEMPLATE_ROOT = Path("src/ai_market_monitor/templates")
MACRO = TEMPLATE_ROOT / "hilal/macros/payment_provider_badge.html"

#: Every screen that asks a person to choose card or crypto, and the wrapper each one
#: puts around a single choice. The wrapper is what keeps the two choices the same size
#: once a mark sits under one of them.
CHECKOUT_SCREENS: dict[Path, str] = {
    TEMPLATE_ROOT / "hilal/dashboard_test/subscription.html": "s-method-choice",
    TEMPLATE_ROOT / "hilal/dashboard/billing.html": "billing-method-option",
    TEMPLATE_ROOT / "hilal/dashboard/checkout.html": "billing-method-option",
}

#: The stylesheet that lays each of those wrappers out, and the wrapper it draws.
CHECKOUT_STYLES: dict[Path, str] = {
    Path("src/ai_market_monitor/static/hm-account-test.css"): "s-method-choice",
    Path("src/ai_market_monitor/static/hilalmarkets-dashboard-v2.css"): (
        "billing-method-option"
    ),
}

#: Every payment company this product can be configured to use, taken from the settings
#: themselves rather than listed again here.
CONFIGURABLE_PROVIDERS = sorted(
    {
        value
        for field in ("billing_card_provider", "billing_crypto_provider")
        for value in Settings.model_fields[field].annotation.__args__  # type: ignore[union-attr]
        if value != "disabled"
    }
)

#: The names a page must never write for itself. `static` is this product's own local
#: test page, so it is not in here — nothing is ever told to a buyer about it.
COMPANY_NAMES = ("Creem", "NOWPayments", "Stripe")

EVERY_CASE_AND_METHOD = [
    pytest.param(case, method, id=f"{case}-{method}")
    for case in sorted(SETTINGS_CASES)
    for method in PAYMENT_METHODS
]


def _environment() -> Jinja2Templates:
    """The real template environment, built the way every router builds it."""

    return register(Jinja2Templates(directory=str(TEMPLATE_ROOT)))


def _render_badge(offer: object) -> str:
    template = _environment().env.get_template("hilal/macros/payment_provider_badge.html")
    return str(template.module.payment_provider_badge(offer)).strip()  # type: ignore[attr-defined]


@pytest.mark.parametrize(("case", "method"), EVERY_CASE_AND_METHOD)
def test_the_company_on_the_offer_is_the_company_the_server_would_use(
    case: str, method: str
) -> None:
    settings = SETTINGS_CASES[case]
    offer = payment_method_offers_by_method(
        settings, plan_codes=("trader",), billing_cycle="monthly"
    )[method]
    provider = billing_method_provider(settings, method)

    assert offer.company == provider_word(provider)
    assert offer.company_site == provider_site(provider)


@pytest.mark.parametrize(("case", "method"), EVERY_CASE_AND_METHOD)
def test_a_way_of_paying_that_is_switched_off_names_no_company(
    case: str, method: str
) -> None:
    """No company behind it means no mark, rather than a mark naming nobody."""

    settings = SETTINGS_CASES[case]
    offer = payment_method_offers_by_method(
        settings, plan_codes=("trader",), billing_cycle="monthly"
    )[method]
    if billing_method_provider(settings, method) is not None:
        return
    assert offer.company is None
    assert offer.company_site is None
    assert _render_badge(offer) == ""


@pytest.mark.parametrize(("case", "method"), EVERY_CASE_AND_METHOD)
def test_the_mark_and_the_sentence_under_a_choice_name_the_same_company(
    case: str, method: str
) -> None:
    """Two lines sit under one choice. They cannot name two different companies."""

    settings = SETTINGS_CASES[case]
    offer = payment_method_offers_by_method(
        settings, plan_codes=("trader",), billing_cycle="monthly"
    )[method]
    if not offer.available or offer.company is None:
        return
    assert offer.note == f"Handled by {offer.company}."


@pytest.mark.parametrize("provider", CONFIGURABLE_PROVIDERS)
def test_every_company_that_can_take_money_has_a_name_and_a_page(provider: str) -> None:
    """A buyer can always read who is being paid, and go and check them.

    `static` is the local test page, which no buyer ever reaches: it has a name for the
    code and deliberately no website, so it draws no mark.
    """

    name = provider_word(provider)
    assert name, provider
    assert "_" not in name
    assert provider_method(provider) in PAYMENT_METHODS

    site = provider_site(provider)
    if provider == "static":
        assert site is None
        return
    assert site is not None and site.startswith("https://"), provider


@pytest.mark.parametrize(("case", "method"), EVERY_CASE_AND_METHOD)
def test_the_mark_links_to_that_company_and_names_it(case: str, method: str) -> None:
    """The rendered mark, from the real environment, for every configuration."""

    settings = SETTINGS_CASES[case]
    offer = payment_method_offers_by_method(
        settings, plan_codes=("trader",), billing_cycle="monthly"
    )[method]
    drawn = _render_badge(offer)

    if offer.company_site is None:
        assert drawn == ""
        return
    assert f'href="{offer.company_site}"' in drawn
    assert "Payments secured by" in drawn
    assert offer.company is not None and offer.company in drawn
    assert 'target="_blank"' in drawn and 'rel="noopener"' in drawn
    # Every other company borrows this product's own shield rather than a logo we do
    # not have. Only Creem's mark is drawn, because only Creem's mark was given to us.
    if offer.provider == "creem":
        assert "svg" in drawn
    else:
        assert 'data-icon="shield"' in drawn


@pytest.mark.parametrize("path", sorted(CHECKOUT_SCREENS))
def test_every_screen_that_asks_card_or_crypto_draws_the_shared_mark(path: Path) -> None:
    """Every choice drawn sits in a cell of its own and carries the mark.

    Counted rather than eyeballed: one screen writes the two choices out and another
    draws them in a loop, so what has to match is *one mark for each choice drawn*, not
    a fixed number of either.
    """

    markup = path.read_text(encoding="utf-8")
    wrapper = CHECKOUT_SCREENS[path]
    choices = markup.count('name="payment_method"')

    assert "hilal/macros/payment_provider_badge.html" in markup, path
    assert choices >= 1, path
    assert markup.count(f'class="{wrapper}"') == choices, path
    assert markup.count("payment_provider_badge(") == choices, path


@pytest.mark.parametrize("path", sorted(CHECKOUT_STYLES))
def test_both_choices_stay_the_same_size_once_a_mark_sits_under_one(path: Path) -> None:
    """The choice takes the room left over and the mark sits on the line below it.

    Without this the two choices are sized by their own contents, so a mark under one of
    them — or a note that runs onto a second line — leaves Card and Crypto uneven.
    """

    sheet = path.read_text(encoding="utf-8")
    block = re.search(
        rf"\.{CHECKOUT_STYLES[path]}\s*\{{(.*?)\}}", sheet, re.DOTALL
    )
    assert block is not None, path
    rules = block.group(1)
    assert "display: grid" in rules
    assert "grid-template-rows: 1fr auto" in rules


def test_no_page_writes_a_payment_company_name_for_itself() -> None:
    """Every name shown to a person is read from the billing service.

    The one exception is the mark itself, which has to know whose logo it is drawing.
    """

    offenders: dict[str, list[str]] = {}
    for path in sorted(TEMPLATE_ROOT.rglob("*.html")):
        if path == MACRO:
            continue
        # A note to the next reader is not something anybody is shown, so the comments
        # are taken out before looking. What is left is the page itself.
        text = re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        found = [name for name in COMPANY_NAMES if name in text]
        if found:
            offenders[str(path)] = found

    assert offenders == {}


def test_the_popup_script_is_told_the_company_rather_than_knowing_it() -> None:
    """The browser draws the name it was sent, and holds no list of its own."""

    script = Path("src/ai_market_monitor/static/hilalmarkets-billing.js").read_text(
        encoding="utf-8"
    )
    code = "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("//")
    )
    for name in COMPANY_NAMES:
        assert name not in code, name
    assert "cardDecision.company" in code
    assert "cryptoDecision.company" in code


@pytest.mark.parametrize("case", sorted(SETTINGS_CASES))
def test_what_the_browser_is_handed_carries_the_same_company(case: str) -> None:
    settings = SETTINGS_CASES[case]
    payload = payment_method_payload(settings, plan_code="trader")
    for cycle, methods in payload.items():
        offers = payment_method_offers_by_method(
            settings, plan_codes=("trader",), billing_cycle=cycle
        )
        for method, decision in methods.items():
            assert decision["company"] == offers[method].company
