"""Discount codes: the rule, across the whole family, not the reported case.

Four separate things have to agree about a discount, and each of them used to be free to
decide for itself:

* what a code is worth (`services/discount_codes.py`),
* what the plan costs without one (`core/plans.py`),
* which ways of paying accept one (`services/billing.py`),
* what the payment company is then asked for (`BillingService.prepare_checkout`).

Every test here is parametrised across a family — every percentage, every payment method,
every provider, every shape of refusal Creem can return — so a fix that only helps one
code, one method or one price fails.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import (
    LAUNCH_DISCOUNT_CODE,
    PLAN_DEFINITIONS,
    PLAN_OFFERS,
    PROMOTION_ENDS_AT,
    PUBLIC_PLAN_CODES,
    PURCHASABLE_PLAN_CODES,
    PlanOffer,
    coded_monthly_price,
    effective_monthly_price,
    launch_discount_percent,
    original_monthly_price,
    plan_offer,
    plan_offer_payload,
    price_after_percent,
    promotion_is_active,
)
from ai_market_monitor.services.billing import (
    DISCOUNT_CODE_METHODS,
    PAYMENT_METHODS,
    method_takes_discount_code,
    provider_method,
    provider_takes_discount_code,
)
from ai_market_monitor.services.discount_codes import (
    DiscountCodeError,
    DiscountCodeService,
    normalize_discount_code,
)

BEFORE_THE_END = PROMOTION_ENDS_AT - timedelta(days=1)
AFTER_THE_END = PROMOTION_ENDS_AT


# ---------------------------------------------------------------------------
# The arithmetic. One owner, so no two discounts round differently.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["20.00", "15.00", "22.00", "79.00", "0.99", "299.00"])
@pytest.mark.parametrize("percent", ["1", "10", "25", "33.33", "50", "99", "100"])
def test_a_discount_never_raises_the_price_and_never_goes_below_nothing(
    amount: str, percent: str
) -> None:
    """Across every price this product sells and every percentage it could offer."""

    full = Decimal(amount)
    final = price_after_percent(full, Decimal(percent))
    assert final <= full, "a discount made the price larger"
    assert final >= Decimal("0.00"), "a discount took the price below nothing"
    assert final == final.quantize(Decimal("0.01")), "a price with more than cents in it"


@pytest.mark.parametrize(
    ("amount", "percent", "expected"),
    [
        ("20.00", "25", "15.00"),
        ("20.00", "50", "10.00"),
        ("20.00", "100", "0.00"),
        ("20.00", "0", "20.00"),
        ("15.00", "25", "11.25"),
        ("22.00", "10", "19.80"),
        ("0.99", "50", "0.50"),
    ],
)
def test_the_stated_arithmetic(amount: str, percent: str, expected: str) -> None:
    """The example from the request, and its neighbours: $20 less 25% is $15."""

    assert price_after_percent(Decimal(amount), Decimal(percent)) == Decimal(expected)


@pytest.mark.parametrize("percent", ["-5", "-0.01"])
def test_a_negative_discount_is_not_an_increase(percent: str) -> None:
    """Never invert. A meaningless percentage leaves the price alone rather than raising
    it — the opposite of a discount is not a discount."""

    assert price_after_percent(Decimal("20.00"), Decimal(percent)) == Decimal("20.00")


# ---------------------------------------------------------------------------
# What somebody typed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("HILAL25", "HILAL25"),
        ("hilal25", "HILAL25"),
        ("  Hilal25  ", "HILAL25"),
        ("HILAL 25", "HILAL25"),
        ("hi-lal_25", "HI-LAL_25"),
    ],
)
def test_a_code_is_read_the_same_however_it_is_typed(typed: str, expected: str) -> None:
    assert normalize_discount_code(typed) == expected


@pytest.mark.parametrize(
    "typed",
    ["", "   ", None, "A", "-LEAD", "code!", "code;drop", "<script>", "x" * 41],
)
def test_a_thing_that_is_not_a_code_is_refused(typed: str | None) -> None:
    with pytest.raises(DiscountCodeError):
        normalize_discount_code(typed)


# ---------------------------------------------------------------------------
# The code is the only route to the launch price.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", PUBLIC_PLAN_CODES)
@pytest.mark.parametrize("when", [BEFORE_THE_END, AFTER_THE_END])
def test_a_checkout_with_no_code_charges_the_normal_price(code: str, when: datetime) -> None:
    """The whole point of a code-gated offer.

    `effective_monthly_price` is what a payment attempt is opened at, so if it ever
    carried the launch price the offer would apply to everybody with nothing typed — and
    the sentence on every pricing card would be false.
    """

    assert effective_monthly_price(code, now=when) == PLAN_DEFINITIONS[code].monthly_price


@pytest.mark.parametrize("code", PUBLIC_PLAN_CODES)
def test_the_coded_price_is_derived_from_the_percentage(code: str) -> None:
    """One number describes the offer, so "25% off" and "$15" cannot drift apart."""

    percent = launch_discount_percent(code, now=BEFORE_THE_END)
    coded = coded_monthly_price(code, now=BEFORE_THE_END)
    if percent is None:
        assert coded is None
        return
    assert coded == price_after_percent(PLAN_DEFINITIONS[code].monthly_price, percent)


@pytest.mark.parametrize("code", PUBLIC_PLAN_CODES)
def test_the_offer_stops_with_the_clock(code: str) -> None:
    """One deadline decides the code, the crossed-out price and the countdown."""

    assert promotion_is_active(BEFORE_THE_END) is True
    assert promotion_is_active(AFTER_THE_END) is False
    assert launch_discount_percent(code, now=AFTER_THE_END) is None
    assert coded_monthly_price(code, now=AFTER_THE_END) is None
    assert original_monthly_price(code, now=AFTER_THE_END) is None


@pytest.mark.parametrize("code", PUBLIC_PLAN_CODES)
@pytest.mark.parametrize("when", [BEFORE_THE_END, AFTER_THE_END])
def test_the_card_payload_is_coherent(code: str, when: datetime) -> None:
    """A crossed-out price, a code and a "without it" figure appear together or not at all.

    Any one of them without the others is a card that either promises a discount nobody
    is told how to get, or names a code that changes nothing.
    """

    payload = plan_offer_payload(code, now=when)
    if not plan_offer(code).monthly_available:
        # Nothing quotable for a plan nobody can buy — not even the code.
        assert payload["monthlyPrice"] is None
        assert payload["fullMonthlyPrice"] is None
        assert payload["discountCode"] is None
        return
    full = effective_monthly_price(code, now=when)
    coded = coded_monthly_price(code, now=when)
    assert payload["fullMonthlyPrice"] == float(full)
    assert payload["monthlyPrice"] == float(coded if coded is not None else full)
    has_code = payload["discountCode"] is not None
    assert has_code is (coded is not None)
    assert has_code is (payload["originalMonthlyPrice"] is not None)
    assert has_code is (payload["discountPercent"] is not None)
    if has_code:
        assert payload["discountCode"] == LAUNCH_DISCOUNT_CODE
        assert payload["monthlyPrice"] < payload["fullMonthlyPrice"]


# ---------------------------------------------------------------------------
# Which ways of paying take a code.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", PAYMENT_METHODS)
def test_only_crypto_takes_a_code_typed_on_our_pages(method: str) -> None:
    """The card route ends on Creem's page, which has a discount box of its own and
    decides the amount itself. A box on our side would quote a price Creem would not
    charge."""

    assert method_takes_discount_code(method) is (method in DISCOUNT_CODE_METHODS)


@pytest.mark.parametrize("provider", ["creem", "stripe", "static", "nowpayments"])
def test_the_provider_answer_matches_the_method_answer(provider: str) -> None:
    """Two questions, one answer. A provider table that drifted from the method table is
    how a page comes to offer a box the route refuses."""

    assert provider_takes_discount_code(provider) is method_takes_discount_code(
        provider_method(provider)
    )


def test_an_unknown_payment_route_takes_no_code() -> None:
    """Fails closed: something nobody described cannot accept a discount by accident."""

    assert method_takes_discount_code(None) is False
    assert method_takes_discount_code("bank_transfer") is False
    assert provider_takes_discount_code("some_new_company") is False


# ---------------------------------------------------------------------------
# Resolving a code.
# ---------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_the_launch_code_works_without_being_written_into_an_env_file() -> None:
    """`core/plans.py` owns the launch offer. A second number for it in an env file is
    exactly the drift this module exists to stop."""

    settings = _settings(creem_api_key=None, billing_discount_codes={})
    service = DiscountCodeService(settings)
    percent = launch_discount_percent("trader", now=BEFORE_THE_END)
    assert percent is not None
    offer = service.local_offer(
        LAUNCH_DISCOUNT_CODE, plan_code="trader", now=BEFORE_THE_END
    )
    assert offer is not None
    assert offer.percent == percent
    assert offer.source == "launch"


@pytest.mark.anyio
async def test_the_launch_code_stops_working_when_the_offer_ends() -> None:
    settings = _settings(creem_api_key=None, billing_discount_codes={})
    service = DiscountCodeService(settings)
    assert (
        service.local_offer(LAUNCH_DISCOUNT_CODE, plan_code="trader", now=AFTER_THE_END)
        is None
    )


@pytest.mark.parametrize("wrong", ["1", "10", "24", "26", "30", "50", "90", "100"])
def test_an_env_file_cannot_give_the_launch_code_a_different_number(wrong: str) -> None:
    """Two owners for one number is how a card advertises a discount checkout refuses.

    The launch code *may* be written in the env list, so one line can name every code a
    deployment honours. It may not be written there at a different percentage: the pricing
    pages quote `core/plans.py` and the checkout would charge this list. Refused at
    startup, across every wrong number rather than the one somebody happened to type.
    """

    with pytest.raises(Exception) as raised:  # noqa: PT011 - pydantic wraps the message
        _settings(billing_discount_codes=f"{LAUNCH_DISCOUNT_CODE}={wrong}")
    assert LAUNCH_DISCOUNT_CODE in str(raised.value)


def test_retiring_the_launch_offer_hands_the_code_to_the_env_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agreement check must not block the way the offer is meant to be retired.

    Retiring means `core/plans.py` stops advertising the code and the env list keeps it
    working, at whatever number is wanted. A check that refused any number once there was
    no launch offer left would make the last step impossible — a guard that fires when
    there is nothing left to disagree with.
    """

    import ai_market_monitor.core.config as config_module

    retired = {
        name: PlanOffer(
            monthly_available=offer.monthly_available,
            annual_available=offer.annual_available,
            launch_discount_percent=None,
        )
        for name, offer in PLAN_OFFERS.items()
    }
    monkeypatch.setattr(config_module, "PLAN_OFFERS", retired)
    settings = _settings(billing_discount_codes=f"{LAUNCH_DISCOUNT_CODE}=40")
    assert settings.billing_discount_codes[LAUNCH_DISCOUNT_CODE] == Decimal("40")


def test_the_launch_code_may_be_written_down_at_the_number_it_already_has() -> None:
    """Restating it is allowed, so the env file can list every code in one place."""

    percent = plan_offer("trader").launch_discount_percent
    assert percent is not None
    settings = _settings(billing_discount_codes=f"{LAUNCH_DISCOUNT_CODE}={percent}")
    assert settings.billing_discount_codes[LAUNCH_DISCOUNT_CODE] == percent


@pytest.mark.anyio
async def test_the_launch_offer_still_wins_if_a_wrong_number_ever_reached_the_list() -> (
    None
):
    """Defence in depth: the resolver prefers the launch offer on its own.

    The check above stops a wrong number being written. This proves the reader does not
    depend on that check having run — the two guards fail in the same direction.
    """

    settings = _settings(creem_api_key=None, billing_discount_codes={})
    settings.billing_discount_codes[LAUNCH_DISCOUNT_CODE] = Decimal("90")
    service = DiscountCodeService(settings)
    offer = service.local_offer(
        LAUNCH_DISCOUNT_CODE, plan_code="trader", now=BEFORE_THE_END
    )
    assert offer is not None
    assert offer.percent == launch_discount_percent("trader", now=BEFORE_THE_END)
    assert offer.source == "launch"


@pytest.mark.parametrize(
    "written",
    [
        "HILAL25=25,TINYTALES=30",
        "hilal25=25, tinytales=30",
        "HILAL25=25%,TINYTALES=30%",
        "HILAL25:25;TINYTALES:30",
        '{"HILAL25": 25, "TINYTALES": 30}',
        "HILAL25=25\nTINYTALES=30",
    ],
)
def test_every_way_of_writing_the_deployed_list_reads_the_same(written: str) -> None:
    """The line an operator types by hand, in each form the loader promises to accept.

    A deployment that writes it one way and a test that only ever checks another is how a
    live env file turns out to hold a value nothing has read.
    """

    settings = _settings(billing_discount_codes=written)
    assert settings.billing_discount_codes == {
        "HILAL25": Decimal("25"),
        "TINYTALES": Decimal("30"),
    }


@pytest.mark.parametrize("plan_code", PURCHASABLE_PLAN_CODES)
@pytest.mark.parametrize("when", [BEFORE_THE_END, AFTER_THE_END])
@pytest.mark.anyio
async def test_a_code_from_the_deployment_list_prices_every_plan(
    plan_code: str, when: datetime
) -> None:
    """A listed code works on any plan and does not stop with the launch window.

    `core/plans.py` stops offering the launch code at ``PROMOTION_ENDS_AT``; this list has
    no end date, which is the whole reason a code is put in it.
    """

    settings = _settings(creem_api_key=None, billing_discount_codes="TINYTALES=30")
    service = DiscountCodeService(settings)
    full = PLAN_DEFINITIONS[plan_code].monthly_price
    priced = await service.price_for(
        "tinytales", plan_code=plan_code, full_amount=full, currency="USD", now=when
    )
    assert priced.code == "TINYTALES"
    assert priced.percent == Decimal("30")
    assert priced.source == "settings"
    assert priced.final == price_after_percent(full, Decimal("30"))
    assert priced.saving == full - priced.final


@pytest.mark.parametrize(
    "written",
    ["A=10", "-START=10", "_UNDER=10", "=10", "TOO" + "X" * 40 + "=10", "WITH.DOT=10"],
)
def test_a_code_nobody_could_type_cannot_be_configured(written: str) -> None:
    """Every layer applies the same shape rule, so none can offer what another refuses.

    The settings loader used to have no shape rule at all. A one-letter code loaded
    happily and was then refused on every single attempt, because the reader wants at
    least two characters — a discount that existed in the configuration and could never
    be used. Both now ask `core/plans.py`.
    """

    with pytest.raises(Exception) as raised:  # noqa: PT011 - pydantic wraps the message
        _settings(billing_discount_codes=written)
    assert "BILLING_DISCOUNT_CODES" in str(raised.value)


@pytest.mark.parametrize(
    "code", ["HILAL25", "TINYTALES", "AB", "A1", "WELCOME-10", "NEW_YEAR", "X" * 40]
)
def test_a_configured_code_can_always_be_typed(code: str) -> None:
    """The other direction of the same rule: anything the settings accept, the box takes.

    Asserted across the family rather than for one code, because a shape rule that is
    merely *similar* in two places is the drift this pairing exists to remove.
    """

    # The launch code is the one entry whose number is not free, so it is written at the
    # number it already has. Shape is what is being tested here, not the percentage.
    launch = plan_offer("trader").launch_discount_percent
    percent = launch if code == LAUNCH_DISCOUNT_CODE else Decimal("10")
    settings = _settings(billing_discount_codes=f"{code}={percent}")
    assert code in settings.billing_discount_codes
    assert normalize_discount_code(code.lower()) == code


@pytest.mark.parametrize("typed", ["TINY TALES", " tinytales ", "Tiny Tales", "tinyTALES"])
def test_spaces_and_case_are_dropped_the_same_way_in_both_layers(typed: str) -> None:
    """People paste ``HILAL 25``. Both layers have to read that as one code.

    If only the reader dropped inner spaces, a deployment could configure ``TINY TALES``
    and every person typing it would be refused. If only the loader did, the opposite.
    """

    settings = _settings(billing_discount_codes=f"{typed}=30")
    assert settings.billing_discount_codes == {"TINYTALES": Decimal("30")}
    assert normalize_discount_code(typed) == "TINYTALES"


@pytest.mark.anyio
async def test_a_code_that_is_on_no_list_is_refused() -> None:
    settings = _settings(creem_api_key=None, billing_discount_codes="TINYTALES=30")
    service = DiscountCodeService(settings)
    with pytest.raises(DiscountCodeError) as raised:
        await service.price_for(
            "TINYTAILS",  # one letter out
            plan_code="trader",
            full_amount=Decimal("20.00"),
            currency="USD",
            now=BEFORE_THE_END,
        )
    assert raised.value.code == "discount_code_unknown"


@pytest.mark.anyio
async def test_a_code_from_the_env_file_is_honoured() -> None:
    settings = _settings(
        creem_api_key=None, billing_discount_codes={"WELCOME10": Decimal("10")}
    )
    offer = await DiscountCodeService(settings).offer_for(
        "welcome10", plan_code="trader", now=BEFORE_THE_END
    )
    assert offer.percent == Decimal("10")
    assert offer.source == "settings"


@pytest.mark.anyio
async def test_an_unknown_code_is_refused() -> None:
    settings = _settings(creem_api_key=None, billing_discount_codes={})
    with pytest.raises(DiscountCodeError) as refusal:
        await DiscountCodeService(settings).offer_for(
            "NOT_A_REAL_CODE", plan_code="trader", now=BEFORE_THE_END
        )
    assert refusal.value.code == "discount_code_unknown"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"status": "expired", "type": "percentage", "percentage": 25}, "discount_code_expired"),
        ({"status": "draft", "type": "percentage", "percentage": 25}, "discount_code_unknown"),
        (
            {"status": "active", "type": "fixed", "amount": 5, "currency": "USD"},
            "discount_code_not_percentage",
        ),
        (
            {
                "status": "active",
                "type": "percentage",
                "percentage": 25,
                "max_redemptions": 10,
                "redeem_count": 10,
            },
            "discount_code_used_up",
        ),
        (
            {
                "status": "active",
                "type": "percentage",
                "percentage": 25,
                "applies_to_products": ["prod_somethingelse"],
            },
            "discount_code_wrong_plan",
        ),
        (
            {
                "status": "active",
                "type": "percentage",
                "percentage": 25,
                "expiry_date": "2020-01-01T00:00:00Z",
            },
            "discount_code_expired",
        ),
        ({"status": "active", "type": "percentage", "percentage": 0}, "discount_code_unknown"),
        ({"status": "active", "type": "percentage", "percentage": 150}, "discount_code_unknown"),
    ],
)
def test_every_refusal_the_payment_company_can_give_is_a_refusal_here(
    body: dict, expected: str
) -> None:
    """Fail closed, in the direction that matters.

    A code the payment company has *refused* must never fall through to the local list.
    Falling through would turn every refusal into "ask the other list instead", which is
    the opposite of a refusal.
    """

    service = DiscountCodeService(_settings(creem_api_key="creem_test_key"))
    with pytest.raises(DiscountCodeError) as refusal:
        service._offer_from_creem(
            body,
            code="HILAL25",
            creem_product_id="prod_ours",
            now=datetime.now(UTC),
        )
    assert refusal.value.code == expected


@pytest.mark.parametrize(
    "body",
    [
        {"status": "active", "type": "percentage", "percentage": 25},
        {"status": "active", "type": "percentage", "amount": 25},
        {
            "status": "active",
            "type": "percentage",
            "percentage": 25,
            "applies_to_products": ["prod_ours"],
        },
        {
            "status": "active",
            "type": "percentage",
            "percentage": 25,
            "applies_to_products": [{"id": "prod_ours"}],
        },
        {
            "status": "active",
            "type": "percentage",
            "percentage": 25,
            "max_redemptions": 100,
            "redeem_count": 3,
        },
        {
            "status": "active",
            "type": "percentage",
            "percentage": 25,
            "expiry_date": "2099-01-01T00:00:00Z",
        },
    ],
)
def test_every_shape_of_a_working_payment_company_code_is_accepted(body: dict) -> None:
    service = DiscountCodeService(_settings(creem_api_key="creem_test_key"))
    offer = service._offer_from_creem(
        body, code="HILAL25", creem_product_id="prod_ours", now=datetime.now(UTC)
    )
    assert offer.percent == Decimal("25")
    assert offer.source == "creem"


@pytest.mark.anyio
async def test_a_code_that_takes_off_everything_is_refused() -> None:
    """A payment page cannot ask for nothing, and a free month is a plan change rather
    than a discount. Refused here rather than by a payment company in words nobody could
    understand."""

    settings = _settings(
        creem_api_key=None, billing_discount_codes={"EVERYTHING": Decimal("100")}
    )
    with pytest.raises(DiscountCodeError) as refusal:
        await DiscountCodeService(settings).price_for(
            "EVERYTHING",
            plan_code="trader",
            full_amount=Decimal("20.00"),
            currency="USD",
            now=BEFORE_THE_END,
        )
    assert refusal.value.code == "discount_code_covers_everything"


@pytest.mark.anyio
@pytest.mark.parametrize("plan_code", PURCHASABLE_PLAN_CODES)
async def test_the_priced_answer_holds_together(plan_code: str) -> None:
    """What was, what is, and what was saved must be the same three numbers everywhere."""

    settings = _settings(
        creem_api_key=None, billing_discount_codes={"TEST40": Decimal("40")}
    )
    full = effective_monthly_price(plan_code)
    priced = await DiscountCodeService(settings).price_for(
        "TEST40",
        plan_code=plan_code,
        full_amount=full,
        currency="USD",
        now=BEFORE_THE_END,
    )
    assert priced.full == full
    assert priced.final == price_after_percent(full, Decimal("40"))
    assert priced.saving == full - priced.final
    assert priced.final < priced.full
