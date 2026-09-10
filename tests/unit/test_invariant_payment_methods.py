"""A way of paying that is offered is a way of paying that works.

The reported symptom was one sentence on the plan page: *"That payment method is not
currently available. Nothing was charged."* Somebody picked **Card**, filled in three
steps of a form, pressed pay, and was refused — because card payments were switched off
in the settings the whole time and the popup drew the choice anyway.

The rule this file holds is the general one, not that one case:

    for every plan, every billing period and every way of paying,
    what the product *offers* and what checkout *accepts* are the same answer.

Both sides are run for real. The offer side is
:func:`ai_market_monitor.services.billing.payment_method_offers`, which every page asks.
The acceptance side runs the actual checkout chain — the provider lookup, the billing
period rule, and the payment company's own product lookup — stopping at the moment it
would reach the network. Nothing here re-implements either side, because a test that
re-states the rule cannot catch the rule being wrong.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES
from ai_market_monitor.services import billing as billing_module
from ai_market_monitor.services.billing import (
    PAYMENT_METHODS,
    BillingError,
    BillingService,
    annual_billing_available,
    configured_billing_provider,
    payment_method_available,
    payment_method_offers,
    payment_method_payload,
    payment_method_refusal,
)

BILLING_CYCLES = ("monthly", "annual")
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every plan a page can ask about, not only the ones on sale. "demo" is the free plan —
#: it reads as "available monthly" in the offer table, because it is, at no charge — and
#: a plan nobody sells must still answer "no way to pay" rather than "every way".
EVERY_PLAN_CODE = (*PURCHASABLE_PLAN_CODES, "demo")


class _ReachedThePaymentCompany(Exception):
    """Raised in place of the outbound request, so no test ever calls a provider."""


def _settings(**overrides: Any) -> Settings:
    base = {
        "_env_file": None,
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "billing_enabled": True,
        "billing_provider": "static",
        "billing_card_provider": "disabled",
        "billing_crypto_provider": "disabled",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


#: The settings shapes worth checking: every provider for each method, and for each one
#: both the complete configuration and the half-configured state that is far more common
#: on a real server — keys present, products not created yet.
SETTINGS_CASES: dict[str, Settings] = {
    "everything_off": _settings(),
    "billing_switched_off": _settings(
        billing_enabled=False,
        billing_card_provider="creem",
        billing_crypto_provider="nowpayments",
        creem_api_key=SecretStr("creem-key"),
        creem_webhook_secret=SecretStr("creem-webhook"),
        creem_product_ids={
            "trader_monthly": "prod_trader_monthly",
            "trader_annual": "prod_trader_annual",
            "pro_monthly": "prod_pro_monthly",
            "pro_annual": "prod_pro_annual",
        },
        nowpayments_api_key=SecretStr("nowpayments-key"),
        nowpayments_ipn_secret=SecretStr("nowpayments-ipn"),
    ),
    # The live shape on 1 September 2026: crypto on, card off, one Creem product.
    "crypto_only": _settings(
        billing_provider="nowpayments",
        billing_crypto_provider="nowpayments",
        nowpayments_api_key=SecretStr("nowpayments-key"),
        nowpayments_ipn_secret=SecretStr("nowpayments-ipn"),
        creem_api_key=SecretStr("creem-key"),
        creem_webhook_secret=SecretStr("creem-webhook"),
        creem_product_ids={"trader_monthly": "prod_trader_monthly"},
    ),
    "creem_monthly_only": _settings(
        billing_card_provider="creem",
        creem_api_key=SecretStr("creem-key"),
        creem_webhook_secret=SecretStr("creem-webhook"),
        creem_product_ids={"trader_monthly": "prod_trader_monthly"},
    ),
    "creem_every_product": _settings(
        billing_card_provider="creem",
        creem_api_key=SecretStr("creem-key"),
        creem_webhook_secret=SecretStr("creem-webhook"),
        creem_product_ids={
            "trader_monthly": "prod_trader_monthly",
            "trader_annual": "prod_trader_annual",
            "pro_monthly": "prod_pro_monthly",
            "pro_annual": "prod_pro_annual",
        },
    ),
    "creem_keys_but_no_products": _settings(
        billing_card_provider="creem",
        creem_api_key=SecretStr("creem-key"),
        creem_webhook_secret=SecretStr("creem-webhook"),
        creem_product_ids={},
    ),
    "creem_products_but_no_keys": _settings(
        billing_card_provider="creem",
        creem_product_ids={"trader_monthly": "prod_trader_monthly"},
    ),
    "stripe_priced": _settings(
        billing_card_provider="stripe",
        stripe_secret_key=SecretStr("stripe-key"),
        billing_webhook_secret=SecretStr("shared-webhook-secret"),
        stripe_price_ids={"trader_monthly": "price_trader", "pro_monthly": "price_pro"},
    ),
    "stripe_without_prices": _settings(
        billing_card_provider="stripe",
        stripe_secret_key=SecretStr("stripe-key"),
        billing_webhook_secret=SecretStr("shared-webhook-secret"),
        stripe_price_ids={},
    ),
    "nowpayments_ready": _settings(
        billing_crypto_provider="nowpayments",
        nowpayments_api_key=SecretStr("nowpayments-key"),
        nowpayments_ipn_secret=SecretStr("nowpayments-ipn"),
    ),
    # A crypto payment nobody could confirm: the key to create the invoice is there, and
    # no secret to prove the "they paid" message is genuine. Taking money here means the
    # customer pays and the plan never starts.
    "nowpayments_cannot_confirm": _settings(
        billing_crypto_provider="nowpayments",
        nowpayments_api_key=SecretStr("nowpayments-key"),
    ),
    # The same company, confirmed through the shared webhook secret instead of its own.
    "nowpayments_shared_secret": _settings(
        billing_crypto_provider="nowpayments",
        nowpayments_api_key=SecretStr("nowpayments-key"),
        billing_webhook_secret=SecretStr("shared-webhook-secret"),
    ),
    # Card products and keys in place, and no way to confirm a Creem payment.
    "creem_cannot_confirm": _settings(
        billing_card_provider="creem",
        creem_api_key=SecretStr("creem-key"),
        creem_product_ids={"trader_monthly": "prod_trader_monthly"},
    ),
    # Stripe with a price but no secret to verify its callback.
    "stripe_cannot_confirm": _settings(
        billing_card_provider="stripe",
        stripe_secret_key=SecretStr("stripe-key"),
        stripe_price_ids={"trader_monthly": "price_trader"},
    ),
    "legacy_provider_only": _settings(billing_provider="creem"),
    "both_ways_open": _settings(
        billing_card_provider="creem",
        billing_crypto_provider="nowpayments",
        creem_api_key=SecretStr("creem-key"),
        creem_webhook_secret=SecretStr("creem-webhook"),
        creem_product_ids={"trader_monthly": "prod_trader_monthly"},
        nowpayments_api_key=SecretStr("nowpayments-key"),
        nowpayments_ipn_secret=SecretStr("nowpayments-ipn"),
    ),
}

EVERY_COMBINATION = [
    pytest.param(name, method, plan_code, cycle, id=f"{name}-{method}-{plan_code}-{cycle}")
    for name in SETTINGS_CASES
    for method in PAYMENT_METHODS
    for plan_code in EVERY_PLAN_CODE
    for cycle in BILLING_CYCLES
]


async def _checkout_accepts(
    settings: Settings,
    *,
    method: str,
    plan_code: str,
    billing_cycle: str,
    monkeypatch: pytest.MonkeyPatch,
) -> bool:
    """Run the real checkout chain and report whether it would have taken the money.

    Every step is the product's own code. The only substitution is the outbound request:
    reaching it means the payment company was about to be asked for a payment page, which
    is exactly the point at which this way of paying counts as working.
    """

    async def _never_call_out(*args: Any, **kwargs: Any) -> Any:
        raise _ReachedThePaymentCompany

    monkeypatch.setattr(billing_module, "provider_request", _never_call_out)

    if not settings.billing_enabled:
        return False
    if plan_code not in PURCHASABLE_PLAN_CODES:
        # `prepare_checkout` refuses these outright, before any provider is consulted.
        return False
    try:
        provider_name = configured_billing_provider(settings, method)
    except BillingError:
        return False
    service = BillingService(
        cast(Any, None), settings, provider_name=provider_name
    )
    try:
        normalized = service._normalize_billing_cycle(
            plan_code=plan_code, requested=billing_cycle
        )
        service.ensure_payment_can_be_confirmed()
    except BillingError:
        return False
    try:
        await service.provider.create_checkout_session(
            user_id=uuid4(),
            checkout_attempt_id=uuid4(),
            plan_code=plan_code,
            plan_name="Plan",
            amount=Decimal("15.00"),
            currency="USD",
            billing_cycle=normalized,
            customer_email="buyer@example.com",
            success_url="https://hilalmarkets.com/billing/success",
            cancel_url="https://hilalmarkets.com/billing/cancel",
        )
    except _ReachedThePaymentCompany:
        return True
    except BillingError:
        return False
    # The local adapter answers without going out at all, and answering is selling.
    return True


@pytest.mark.parametrize(("case", "method", "plan_code", "cycle"), EVERY_COMBINATION)
async def test_an_offered_way_of_paying_is_a_way_of_paying_that_works(
    case: str,
    method: str,
    plan_code: str,
    cycle: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SETTINGS_CASES[case]
    offered = {
        offer.method: offer.available
        for offer in payment_method_offers(
            settings, plan_codes=(plan_code,), billing_cycle=cycle
        )
    }[method]
    accepted = await _checkout_accepts(
        settings,
        method=method,
        plan_code=plan_code,
        billing_cycle=cycle,
        monkeypatch=monkeypatch,
    )
    assert offered == accepted, (
        f"{case}: the product offers {method} for {plan_code} {cycle} = {offered}, "
        f"but checkout accepts it = {accepted}"
    )


@pytest.mark.parametrize(("case", "method", "plan_code", "cycle"), EVERY_COMBINATION)
def test_the_single_answer_matches_the_list_of_offers(
    case: str, method: str, plan_code: str, cycle: str
) -> None:
    """The two ways of asking the question never disagree."""

    settings = SETTINGS_CASES[case]
    listed = {
        offer.method: offer.available
        for offer in payment_method_offers(
            settings, plan_codes=(plan_code,), billing_cycle=cycle
        )
    }
    assert listed[method] == payment_method_available(
        settings, method=method, plan_code=plan_code, billing_cycle=cycle
    )


@pytest.mark.parametrize("case", sorted(SETTINGS_CASES))
@pytest.mark.parametrize("plan_code", EVERY_PLAN_CODE)
def test_the_payload_a_page_draws_from_matches_the_same_answer(
    case: str, plan_code: str
) -> None:
    """What the browser is handed is the decision, not something to recompute."""

    settings = SETTINGS_CASES[case]
    payload = payment_method_payload(settings, plan_code=plan_code)
    for cycle, methods in payload.items():
        for method, decision in methods.items():
            assert decision["available"] == payment_method_available(
                settings, method=method, plan_code=plan_code, billing_cycle=cycle
            )
            assert isinstance(decision["note"], str)
            assert decision["note"].strip()


@pytest.mark.parametrize(("case", "method", "plan_code", "cycle"), EVERY_COMBINATION)
def test_a_refusal_is_a_plain_sentence_that_names_what_else_to_try(
    case: str, method: str, plan_code: str, cycle: str
) -> None:
    """A beginner is never told only that something is "not available".

    The old sentence named no method, gave no reason and offered nothing to do next. Every
    refusal now says which way of paying was refused, and either names the one that works
    or says plainly that there is none.
    """

    settings = SETTINGS_CASES[case]
    sentence = payment_method_refusal(
        settings, method=method, plan_code=plan_code, billing_cycle=cycle
    )
    assert sentence.endswith(".")
    assert "_" not in sentence, "an internal field name reached a person"
    others = [
        offer.method
        for offer in payment_method_offers(
            settings, plan_codes=(plan_code,), billing_cycle=cycle
        )
        if offer.available and offer.method != method
    ]
    if others:
        for other in others:
            assert other in sentence, "a working way of paying was not named"
    else:
        assert "no other way to pay" in sentence


@pytest.mark.parametrize("case", sorted(SETTINGS_CASES))
def test_a_note_is_written_for_a_person_not_for_the_code(case: str) -> None:
    settings = SETTINGS_CASES[case]
    for cycle in BILLING_CYCLES:
        for offer in payment_method_offers(
            settings, plan_codes=EVERY_PLAN_CODE, billing_cycle=cycle
        ):
            assert offer.note.endswith(".")
            assert "_" not in offer.note
            assert "disabled" not in offer.note.casefold()
            assert "unavailable" not in offer.note.casefold()


@pytest.mark.parametrize("case", sorted(SETTINGS_CASES))
def test_yearly_billing_is_open_only_when_every_paid_plan_can_be_bought_that_way(
    case: str,
) -> None:
    """The landing page's yearly switch and the dashboard's agree, by construction."""

    settings = SETTINGS_CASES[case]
    assert annual_billing_available(settings) == all(
        any(
            payment_method_available(
                settings, method=method, plan_code=code, billing_cycle="annual"
            )
            for method in PAYMENT_METHODS
        )
        for code in PURCHASABLE_PLAN_CODES
    )


def test_the_live_shape_offers_crypto_and_refuses_card() -> None:
    """The exact configuration that produced the reported sentence.

    Card off, crypto on, one Creem product. The plan page must draw card as unavailable
    and crypto as the way to pay — never the other way round, and never both as normal.
    """

    settings = SETTINGS_CASES["crypto_only"]
    offers = {
        offer.method: offer
        for offer in payment_method_offers(
            settings, plan_codes=("trader",), billing_cycle="monthly"
        )
    }
    assert offers["card"].available is False
    assert offers["card"].note == "Paying by card is switched off just now."
    assert offers["crypto"].available is True
    assert offers["crypto"].note == "Handled by NOWPayments."
    assert payment_method_refusal(
        settings, method="card", plan_code="trader", billing_cycle="monthly"
    ) == (
        "Paying by card is switched off just now. You can pay with crypto instead."
    )


# ── What the checkout promises about money that repeats ──────────────────────────
#
# The same rule as the rest of this file, one step further on. It is not enough that a way
# of paying which is *offered* works; what the checkout says that way of paying will *do*
# must be true of it as well.
#
# Both popups had one sentence for every method — "$17 today, then $17 every month until
# you stop it" — written into their own JavaScript. NOWPayments takes one payment and
# stops. So every crypto buyer was promised a monthly subscription that could not exist,
# on the same screen where the landing page promises the checkout will show "whether it
# renews by itself".


#: Every checkout script that draws the sentence under the price.
_CHECKOUT_SCRIPTS = (
    "hm-subscription-test.js",
    "hilalmarkets-billing.js",
)

#: Words that only belong in a promise about a charge that comes back on its own.
_REPEAT_WORDS = ("every month", "every year", "until you stop it", "each month")


@pytest.mark.parametrize("case", sorted(SETTINGS_CASES))
@pytest.mark.parametrize("plan_code", EVERY_PLAN_CODE)
def test_only_a_company_that_can_charge_again_promises_a_charge_again(
    case: str, plan_code: str
) -> None:
    """A repeat is promised for exactly the companies that can take one.

    Read from the capability contract rather than from a list of company names, so a
    company added tomorrow is covered by this test on the day it is added.
    """

    settings = SETTINGS_CASES[case]
    for cycle in BILLING_CYCLES:
        for offer in payment_method_offers(
            settings, plan_codes=(plan_code,), billing_cycle=cycle
        ):
            story = offer.charge_story.casefold()
            promises_repeat = any(word in story for word in _REPEAT_WORDS)
            assert promises_repeat == offer.renews_by_itself, (
                f"{case}/{plan_code}/{cycle}/{offer.method}: the checkout sentence "
                f"{offer.charge_story!r} does not match what "
                f"{offer.provider!r} can really do"
            )
            if not offer.renews_by_itself and offer.provider is not None:
                assert "does not renew by itself" in story, (
                    "a one-off payment must say so out loud"
                )


@pytest.mark.parametrize("case", sorted(SETTINGS_CASES))
@pytest.mark.parametrize("plan_code", EVERY_PLAN_CODE)
def test_the_page_is_handed_the_sentence_and_the_amount_separately(
    case: str, plan_code: str
) -> None:
    """The browser fills in a number. It never chooses which sentence.

    ``{amount}`` stays a placeholder in the payload because a discount code changes the
    figure after the server has written the words. Everything else — whether it repeats,
    how often, and what to call that — is already decided here.
    """

    settings = SETTINGS_CASES[case]
    payload = payment_method_payload(settings, plan_code=plan_code)
    for cycle, methods in payload.items():
        for method, decision in methods.items():
            story = decision["story"]
            assert isinstance(story, str) and story.strip()
            assert isinstance(decision["terms"], str) and decision["terms"].strip()
            assert isinstance(decision["renews"], bool)
            offer = next(
                item
                for item in payment_method_offers(
                    settings, plan_codes=(plan_code,), billing_cycle=cycle
                )
                if item.method == method
            )
            assert decision["renews"] == offer.renews_by_itself
            if offer.provider is not None:
                assert "{amount}" in story, (
                    f"{case}/{plan_code}/{cycle}/{method}: the sentence carries no "
                    "place for the amount, so the page cannot show what is charged"
                )
            # A figure baked in on the server is a figure a discount code cannot correct.
            assert "$" not in story


def test_no_checkout_script_writes_its_own_renewal_promise() -> None:
    """The reported defect class: the same rule written a second time, in the browser.

    A sentence about repeating money living in a script is a sentence nothing on the
    server can correct and no test of the payment rules can see.
    """

    static = REPO_ROOT / "src" / "ai_market_monitor" / "static"
    for name in _CHECKOUT_SCRIPTS:
        source = (static / name).read_text(encoding="utf-8")
        # Comments explain why the sentence left this file; only real code is checked.
        code = "\n".join(
            line
            for line in source.splitlines()
            if not line.lstrip().startswith(("*", "//", "/*"))
        )
        for word in _REPEAT_WORDS:
            assert word not in code, (
                f"{name} still writes its own promise about a repeating charge "
                f"({word!r}). The sentence belongs to the payment company, and comes "
                "from payment_method_payload."
            )
