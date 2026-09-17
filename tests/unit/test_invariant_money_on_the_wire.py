"""Money must leave this system as the exact money that was decided here.

Three defects live in that one sentence, and this file pins all of them.

The first (D3 / former R2-7):
``NowPaymentsBillingProvider.create_checkout_session`` put ``float(amount)`` into the
invoice body. A ``Decimal`` through a binary float is not the same number — most
two-decimal amounts (``10.05``, ``6.03``) have no binary representation at all — and
even a whole-dollar price like ``9.00`` prints as ``9.0`` once it is a float.

The second (H-3, found by the adversarial review of the D3 fix): the D3 fix sent the
amount as a quoted JSON **string** (``"9.00"``), because one table in a NOWPayments
help-centre article types ``price_amount`` as String. That same article carries a
second table typing it as Number, while NOWPayments' published OpenAPI
(``type: number, format: double``), its official client library and its own request
examples (``"price_amount": 3999.5``) all send a **number**. So the shape pinned here
is both guarantees at once: the digits are the exact quantised decimal text *and* the
token is a bare JSON number — ``9.00`` unquoted, trailing zeros kept, no float and no
quotes anywhere.

The third (WP3 / R3 and R4): the same loss happened on the way to the **browser**, not
only to a payment company. ``core/plans.plan_offer_payload`` converted every price
through ``float`` before the landing page and the dashboard embedded it in JSON, and
five money paths rounded with decimal's default ``ROUND_HALF_EVEN`` instead of the
owner's ``ROUND_HALF_UP``. So the rules widened: ``float(`` on a money name is banned
in **every** module under ``src/ai_market_monitor`` (only an explicit, reasoned
allow-list of non-customer-money sites lifts a line — a market price or a model/token
spend figure is a metrics-layer float, never a charged price), every money
``.quantize(`` goes through :func:`ai_market_monitor.core.money.quantise_half_up`, and
every page JSON is written by :func:`ai_market_monitor.core.money.money_json_dumps`,
which keeps a ``Decimal`` as a bare JSON number with its exact digits.

The rule is asserted for the whole family, not one price:

* every catalogue price in both cycles — ``PLAN_DEFINITIONS`` monthly and
  ``PUBLIC_PLAN_PRESENTATIONS`` annual;
* the launch offers (``PLAN_OFFERS.promotional_monthly_price``) and the price a
  checkout really charges today (``effective_monthly_price``);
* every discount outcome — ``price_after_percent`` over every percent in
  ``settings.billing_discount_codes`` for each of those bases;

and by source scans over the whole of ``src/ai_market_monitor``: one for ``float(`` on
a money name, one for ``.quantize(`` on a money line. Each scan carries a self-check
proving it can see the defect shape it exists for, and one for leaving the
allow-listed, genuinely non-money uses alone.

Creem and Stripe are never handed an amount (their checkouts carry a ``product_id`` /
``price_id``; both ``del`` the ``amount`` argument), so NOWPayments is the provider
this wire test drives.

All requests are captured in process — the outbound door is patched, so no test here
can contact a payment company.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Final
from uuid import uuid4

import httpx
import pytest
from fastapi.templating import Jinja2Templates
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from ai_market_monitor.api.template_env import register
from ai_market_monitor.core import money as money_owner
from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.money import (
    minor_units,
    quantise,
    wire_json_body,
    wire_number,
    wire_text,
)
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PLAN_OFFERS,
    PROMOTION_ENDS_AT,
    PUBLIC_PLAN_CODES,
    PUBLIC_PLAN_PRESENTATIONS,
    PURCHASABLE_PLAN_CODES,
    effective_monthly_price,
    plan_offer_payload,
    price_after_percent,
)
from ai_market_monitor.db.models import User, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider, UserStatus
from ai_market_monitor.services import billing as billing_module
from ai_market_monitor.services.affiliate import AffiliateService
from ai_market_monitor.services.affiliate_attribution import ReferralAttributionService
from ai_market_monitor.services.billing import NowPaymentsBillingProvider

#: A discount table shaped like the real setting (code → percent, 0 < p ≤ 100).
#: Every percent here is exercised against every price base below, so the family
#: includes outcomes whose cents are odd, not just the launch prices.
DISCOUNT_TABLE: Final[dict[str, str]] = {
    "SAVE10": "10",
    "SAVE12HALF": "12.5",
    "TAKE33": "33",
    "HALFOFF": "50",
    "QUARTER": "75",
    "NINETY": "90",
    "ALMOST": "99",
}


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "billing_enabled": True,
        "billing_provider": "nowpayments",
        "billing_crypto_provider": "nowpayments",
        "nowpayments_api_key": SecretStr("test-only-not-a-real-key-000000"),
        "nowpayments_ipn_secret": SecretStr("test-only-not-a-real-key-000000"),
        "billing_discount_codes": DISCOUNT_TABLE,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


TEST_SETTINGS: Final[Settings] = _settings()


def _cents(amount: Decimal) -> Decimal:
    """The expected minor-unit value, worked out here in the test.

    Deliberately not ``core.money.quantise``: a wire test must not take its
    expectation from the same code it is testing.
    """

    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money_family() -> list[tuple[str, Decimal]]:
    """Every amount this product can put in front of a payment company."""

    family: list[tuple[str, Decimal]] = []
    for code, definition in PLAN_DEFINITIONS.items():
        family.append((f"{code}.monthly", definition.monthly_price))
    for code, presentation in PUBLIC_PLAN_PRESENTATIONS.items():
        family.append((f"{code}.annual", presentation.annual_price))
    for code in PURCHASABLE_PLAN_CODES:
        family.append((f"{code}.charged_today", effective_monthly_price(code)))
        promotional = PLAN_OFFERS[code].promotional_monthly_price
        if promotional is not None:
            family.append((f"{code}.launch_offer", promotional))
        for base_label, base in (
            ("charged_today", effective_monthly_price(code)),
            ("monthly", PLAN_DEFINITIONS[code].monthly_price),
            ("annual", PUBLIC_PLAN_PRESENTATIONS[code].annual_price),
        ):
            for discount_code, percent in TEST_SETTINGS.billing_discount_codes.items():
                family.append(
                    (
                        f"{code}.{base_label}.{discount_code}",
                        price_after_percent(base, percent),
                    )
                )
    return family


MONEY_FAMILY: Final[list[tuple[str, Decimal]]] = _money_family()
_FAMILY_IDS: Final[list[str]] = [
    f"{label}:{amount}" for label, amount in MONEY_FAMILY
]

#: What the raw ``price_amount`` value looks like in the serialised body, whether the
#: sender wrote it as a JSON string or as a JSON number.
_PRICE_AMOUNT_VALUE = re.compile(
    rb'"price_amount"\s*:\s*(?P<value>"[^"]*"|-?[0-9][0-9.eE+-]*)'
)


async def _nowpayments_invoice_body(
    monkeypatch: pytest.MonkeyPatch,
    amount: Decimal,
    currency: str = "USD",
) -> bytes:
    """Open one crypto checkout and return the exact bytes httpx would send."""

    captured: dict[str, bytes] = {}

    async def answer(
        settings: Settings,
        method: str,
        url: str,
        *,
        provider: str,
        operation: str = "",
        **kwargs: object,
    ) -> httpx.Response:
        assert provider == "nowpayments"
        # ``core.money.wire_json_body`` builds the body, so it reaches the outbound
        # door as ``content=`` bytes: a JSON *number* carrying a Decimal's exact
        # digits cannot travel through ``json=``, because the stdlib encoder would
        # have to turn the amount into a float (D3) or quote it (H-3).
        # ``httpx.Request(...).content`` runs the same encoder the real transport
        # runs, so this is byte-for-byte the outbound body.
        content = kwargs.get("content")
        assert isinstance(content, bytes), (
            "the NOWPayments invoice did not reach the outbound door as exact "
            f"pre-serialised bytes under ``content=`` (got {type(content).__name__} "
            f"in kwargs {sorted(kwargs)!r}). Build the body with "
            "core.money.wire_json_body and pass it as content=."
        )
        headers = kwargs.get("headers")
        assert isinstance(headers, dict), f"no headers on the invoice request: {headers!r}"
        assert headers.get("Content-Type") == "application/json", (
            f"a hand-built JSON body must still say what it is; got {headers!r}."
        )
        request = httpx.Request(method, url, content=content, headers=headers)
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={"id": "inv_test", "invoice_url": "https://nowpayments.io/payment/test"},
        )

    monkeypatch.setattr(billing_module, "provider_request", answer)
    session = await NowPaymentsBillingProvider(TEST_SETTINGS).create_checkout_session(
        user_id=uuid4(),
        checkout_attempt_id=uuid4(),
        plan_code="trader",
        plan_name="Plus",
        amount=amount,
        currency=currency,
        billing_cycle="one_time_30_day",
        customer_email=None,
        success_url="http://testserver/billing/success",
        cancel_url="http://testserver/billing/cancel",
    )
    assert session.checkout_url == "https://nowpayments.io/payment/test"
    return captured["body"]


# ── 1. Serialisation exactness across the whole family ─────────────────────────


@pytest.mark.parametrize(("label", "amount"), MONEY_FAMILY, ids=_FAMILY_IDS)
async def test_invoice_price_amount_is_the_exact_decimal_text(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    amount: Decimal,
) -> None:
    """The outbound invoice carries the amount as a bare JSON number, to the cent.

    ``str(float(Decimal("9.00")))`` is ``"9.0"`` — the cents digit is already gone —
    and the *value* of ``float`` is not exact for most cents at all, so the digits
    must be the quantised text with its trailing zeros. And the token must not be
    quoted: the provider's OpenAPI, client library and request examples all send
    ``price_amount`` as a JSON number, so a string is an unverified wire-type change
    on the money path (H-3). Both halves are checked: the exact bytes of the token,
    then what the finished body parses back to.
    """

    expected_text = str(_cents(amount))
    body = await _nowpayments_invoice_body(monkeypatch, amount)
    match = _PRICE_AMOUNT_VALUE.search(body)
    assert match is not None, f"the invoice body carries no price_amount at all: {body!r}"
    assert match.group("value") == expected_text.encode(), (
        f"{label}: the NOWPayments invoice body carries {match.group('value')!r} for "
        f"amount {amount}, not the exact amount written as a bare JSON number "
        f"({expected_text.encode()!r}). Quoted is the H-3 defect (the provider's spec "
        "types price_amount as a number); a float, or digits missing their trailing "
        "zero, is the D3 defect — float prints 9.00 as 9.0, and 10.05/6.03 as binary "
        "values that are not the money. core.money.wire_json_body is the one owner "
        "that gets both right."
    )
    assert b'"price_amount":"' not in body, f"the amount left quoted: {body!r}"
    # The body is still valid JSON, and the number inside it is the money decided
    # here — same digits (trailing zeros included), parsed with floats out of the way.
    parsed = json.loads(body, parse_float=Decimal, parse_int=Decimal)["price_amount"]
    assert isinstance(parsed, Decimal), f"price_amount parsed as {type(parsed)}: {body!r}"
    assert str(parsed) == expected_text
    assert parsed == _cents(amount)


def test_the_family_covers_both_ways_a_float_loses_the_amount() -> None:
    """Self-check: the family is not too easy.

    It must contain at least one amount whose float *text* differs from the cents
    text (a whole dollar prints without its trailing zero) and at least one whose
    float *value* is not the money at all (binary cannot hold most cents) —
    otherwise the test above could be passing on a family that never touches the
    defect.
    """

    text_losses = {
        str(amount)
        for _, amount in MONEY_FAMILY
        if str(float(_cents(amount))) != str(_cents(amount))
    }
    value_losses = {
        str(amount)
        for _, amount in MONEY_FAMILY
        if Decimal(float(_cents(amount))) != _cents(amount)
    }
    assert text_losses, "no family amount loses its cents text through float"
    assert value_losses, "no family amount loses its exact value through float"


# ── 2. The owner itself ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    (
        ("9.00", "USD", "9.00"),
        ("9", "USD", "9.00"),
        ("9.004", "USD", "9.00"),
        ("9.005", "USD", "9.01"),
        ("-9.005", "USD", "-9.01"),
        ("0", "usd", "0.00"),
        ("10.05", "EUR", "10.05"),
        ("2.5", "eur", "2.50"),
        ("7.25", "CHF", "7.25"),  # unmapped currency: the documented default (2)
    ),
)
def test_wire_text_is_the_quantised_decimal_with_no_float_anywhere(
    amount: str, currency: str, expected: str
) -> None:
    assert wire_text(Decimal(amount), currency) == expected


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    (
        ("9.00", "USD", "9.00"),
        ("9", "USD", "9.00"),
        ("9.004", "USD", "9.00"),
        ("9.005", "USD", "9.01"),
        ("-9.005", "USD", "-9.01"),
        ("0", "usd", "0.00"),
        ("10.05", "EUR", "10.05"),
        ("2.5", "eur", "2.50"),
        ("7.25", "CHF", "7.25"),  # unmapped currency: the documented default (2)
    ),
)
def test_wire_number_is_the_quantised_decimal_with_no_float_anywhere(
    amount: str, currency: str, expected: str
) -> None:
    """The number text is the same exact digits — quantised, trailing zeros kept.

    ``wire_number`` is what :func:`wire_json_body` puts on the wire as a bare JSON
    number; ``wire_text`` is the same text for a body that wants it quoted. Neither
    may reach a float, so both are pinned to the same table.
    """

    assert wire_number(Decimal(amount), currency) == expected
    assert wire_text(Decimal(amount), currency) == wire_number(Decimal(amount), currency)


def test_wire_json_body_emits_the_amount_as_a_bare_json_number() -> None:
    """The owner's own byte contract: valid JSON, number token, exact digits."""

    body = wire_json_body(
        {"price_currency": "usd", "order_id": "hm|abc|trader"},
        number_field="price_amount",
        amount=Decimal("9"),
        currency="USD",
    )

    assert body == b'{"price_currency":"usd","order_id":"hm|abc|trader","price_amount":9.00}'
    assert b'"price_amount":9.00' in body
    assert b'"price_amount":"' not in body
    assert json.loads(body, parse_float=Decimal)["price_amount"] == Decimal("9.00")


def test_wire_json_body_keeps_every_other_field_exactly_as_json_writes_it() -> None:
    """Replacing the amount token must not disturb the rest of the body."""

    body = wire_json_body(
        {
            "order_description": "Hilal Markets Plus 30-day access",
            "is_fee_paid_by_user": False,
            "success_url": "https://hilalmarkets.com/billing/success",
        },
        number_field="price_amount",
        amount=Decimal("10.05"),
        currency="EUR",
    )

    parsed = json.loads(body, parse_float=Decimal, parse_int=Decimal)
    assert parsed["is_fee_paid_by_user"] is False
    assert parsed["order_description"] == "Hilal Markets Plus 30-day access"
    assert parsed["success_url"] == "https://hilalmarkets.com/billing/success"
    assert str(parsed["price_amount"]) == "10.05"


@pytest.mark.parametrize("not_money", ("NaN", "-NaN", "sNaN", "Infinity", "-Infinity"))
def test_wire_number_refuses_an_amount_that_is_not_a_number(not_money: str) -> None:
    """Fail closed: something that cannot be written as a JSON number must never
    reach a payment company, and must never leave as the text ``NaN`` either."""

    with pytest.raises(ValueError):
        wire_number(Decimal(not_money), "USD")
    with pytest.raises(ValueError):
        wire_json_body({}, number_field="price_amount", amount=Decimal(not_money), currency="USD")


def test_wire_json_body_refuses_a_payload_that_already_carries_the_field() -> None:
    """The amount gets on the wire through this owner or not at all."""

    with pytest.raises(ValueError):
        wire_json_body(
            {"price_amount": "9.00"},
            number_field="price_amount",
            amount=Decimal("9"),
            currency="USD",
        )


def test_wire_json_body_refuses_a_placeholder_collision() -> None:
    """The placeholder is swapped by text; if it appears twice the swap would be a
    guess, so the owner refuses instead of shipping a body it cannot vouch for."""

    with pytest.raises(ValueError):
        wire_json_body(
            {"note": "__hm_wire_number__price_amount__"},
            number_field="price_amount",
            amount=Decimal("9"),
            currency="USD",
        )


def test_minor_units_map_and_default() -> None:
    assert minor_units("USD") == 2
    assert minor_units("eur") == 2
    assert minor_units("  Usd ") == 2
    assert minor_units("KWD") == 2  # the default, until named in the map on purpose


@pytest.mark.parametrize(("label", "amount"), MONEY_FAMILY, ids=_FAMILY_IDS)
def test_every_amount_already_in_minor_units_passes_through_unchanged(
    label: str, amount: Decimal
) -> None:
    """Catalogue, launch and discount outcomes are all born at the cent — so the
    wire owner must not move them, only write them down."""

    assert quantise(amount, "USD") == _cents(amount)


# ── 3. The rounding owner: one rounding statement, ROUND_HALF_UP for money ─────


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        # The reported case: a half cent. The default (banker's) mode would give 0.12.
        ("0.125", "0.13"),
        ("0.005", "0.01"),
        ("0.135", "0.14"),
        ("2.675", "2.68"),
        ("-0.125", "-0.13"),
        # Not a half: ordinary rounds must land where they always did.
        ("9.004", "9.00"),
        ("9.006", "9.01"),
        ("9.00", "9.00"),
    ),
)
def test_quantise_half_up_rounds_a_half_cent_away_from_zero(raw: str, expected: str) -> None:
    """Every money rounding goes through this one call, with this one mode."""

    assert money_owner.quantise_half_up(Decimal(raw), Decimal("0.01")) == Decimal(expected)


def test_quantise_half_up_disagrees_with_the_default_mode_on_a_half_cent() -> None:
    """The defect was silent because the default mode agrees except on the half.

    ``Decimal("0.125")`` is exactly a half cent, so ``ROUND_HALF_EVEN`` — decimal's
    default, and what the five un-routed money paths were using — records ``0.12``.
    The owner records ``0.13``. This test pins that the difference is real, so a
    "cleanup" that drops the explicit rounding mode cannot pass unnoticed.
    """

    half_cent = Decimal("1.00") * Decimal("12.5") / Decimal("100")
    assert half_cent.quantize(Decimal("0.01")) == Decimal("0.12")  # the default: half-even
    assert money_owner.quantise_half_up(half_cent, Decimal("0.01")) == Decimal("0.13")


async def _half_cent_commission(test_context) -> Decimal:
    """$1.00 paid once by a referred customer, at a 12.5% first-payment share.

    Exactly a half cent ($0.125) — the outcome is decided by the rounding mode, so
    this is the service-level proof that commission uses the owner and not decimal's
    default. Driven the same way
    ``tests/unit/test_invariant_affiliate_attribution.py`` drives it: through the
    single writer, ``ReferralAttributionService.record_payment``.
    """

    async with test_context["session_factory"]() as session:

        async def person(name: str, email: str) -> User:
            user = User(id=uuid4(), display_name=name, status=UserStatus.ACTIVE)
            session.add(user)
            session.add(
                UserIdentity(
                    user_id=user.id,
                    provider=IdentityProvider.EMAIL,
                    provider_subject=email,
                    normalized_identifier=email,
                    is_verified=True,
                    is_primary=True,
                )
            )
            await session.flush()
            return user

        admin = await person("Owner", "wp3-owner@example.test")
        affiliate = await person("Rounding", "wp3-affiliate@example.test")
        service = AffiliateService(session)
        await service.apply(
            user_id=affiliate.id,
            display_name="Rounding",
            social_links=["https://x.com/rounding"],
            requested_discount_code="HALFCENT",
        )
        application = await service.application_for(affiliate.id)
        assert application is not None
        await service.approve(
            application_id=application.id,
            admin_user_id=admin.id,
            discount_percent="10",
            commission_percent="12.5",
            subsequent_commission_percent=None,
        )
        customer = await person("Buyer", "wp3-buyer@example.test")
        attribution = ReferralAttributionService(session)
        await attribution.assign(user_id=customer.id, link_code="HALFCENT")
        commission = await attribution.record_payment(
            customer_user_id=customer.id,
            event_key="wp3-half-cent",
            paid_amount_usd=Decimal("1.00"),
        )
        assert commission is not None, "the $1.00 payment earned no commission row at all"
        return commission.commission_usd


async def test_a_half_cent_commission_is_recorded_with_the_owner_rounding(test_context) -> None:
    """Stored commission rows are never rewritten — only new ones follow one rule."""

    assert await _half_cent_commission(test_context) == Decimal("0.13")


# ── 4. The JSON boundary for pages: money_json_dumps / json_money_number ───────


def test_json_money_number_returns_the_minor_unit_decimal_not_a_float() -> None:
    """The value a page embeds is the *quantised amount*, still a ``Decimal``.

    The float was the defect; converting back at the payload would put it right
    back. The number stays a Decimal and the page's JSON writer turns it into a
    bare number with the exact digits.
    """

    value = money_owner.json_money_number(Decimal("9.004"))
    assert isinstance(value, Decimal)
    assert not isinstance(value, float)
    assert value == Decimal("9.00")
    assert str(value) == "9.00"  # the trailing zero survives, as ``9.0`` would not
    assert money_owner.json_money_number(Decimal("2.5"), "EUR") == Decimal("2.50")


@pytest.mark.parametrize(("label", "amount"), MONEY_FAMILY, ids=_FAMILY_IDS)
def test_the_page_json_writer_emits_the_exact_amount_as_a_bare_number(
    label: str, amount: Decimal
) -> None:
    """Every money number the browser will do arithmetic on must arrive exact.

    The React pricing card types the price ``number | null`` and adds it up. A
    ``Decimal`` through ``json.dumps`` either crashes (no ``Decimal`` encoder) or —
    the shipped defect — is turned into a float first, losing ``9.00``'s cents text
    and most two-decimal values entirely. ``money_json_dumps`` must write the exact
    quantised text, unquoted, and nothing else about the document may change.
    """

    expected_text = str(_cents(amount))
    out = money_owner.money_json_dumps({"v": money_owner.json_money_number(amount)})
    assert f'"v": {expected_text}' in out, (
        f"{label}: the page JSON carries {out!r} for amount {amount}, not the exact "
        f"cents text {expected_text!r} as a bare number (D3 shape again, this time "
        "on the way to the browser: float prints 9.00 as 9.0)."
    )
    assert '"v":"' not in out, f"{label}: the amount left quoted: {out!r}"
    parsed = json.loads(out, parse_float=Decimal, parse_int=Decimal)["v"]
    assert isinstance(parsed, Decimal), f"{label}: parsed back as {type(parsed)}: {out!r}"
    assert str(parsed) == expected_text
    assert parsed == _cents(amount)


def test_the_page_json_proof_covers_amounts_that_float_would_damage() -> None:
    """Self-check: at least one family amount loses its text through float while
    the owner keeps it — otherwise section 4 could pass on an over-easy family."""

    damaged = [
        (label, amount)
        for label, amount in MONEY_FAMILY
        if str(float(_cents(amount))) != str(_cents(amount))
    ]
    assert damaged, "no family amount loses its cents text through float"
    label, amount = damaged[0]
    out = money_owner.money_json_dumps({"v": money_owner.json_money_number(amount)})
    # The owner's exact text is there, and the float's shortened text is not even
    # present as a number token (9.0 is a prefix of 9.00, so match on the token).
    assert f'"v": {str(_cents(amount))}' in out, label
    float_text = str(float(_cents(amount)))
    assert re.search(rf'"v":\s*{re.escape(float_text)}(?![0-9])', out) is None, label


def test_money_json_dumps_passes_kwargs_through_and_keeps_other_values_untouched() -> None:
    """``json.dumps``'s own options (Jinja sets ``sort_keys=True``) still apply, and
    anything that is not a Decimal is written byte-for-byte as before."""

    payload = {
        "a": "text",
        "b": True,
        "nested": {"list": [1, "two"], "n": None},
    }
    plain = money_owner.money_json_dumps(payload)
    assert plain == json.dumps(payload)
    assert plain == json.dumps(payload, sort_keys=True)  # keys already in sorted order
    ordered = money_owner.money_json_dumps({"b": 1, "a": 2}, sort_keys=True)
    assert ordered == '{"a": 2, "b": 1}'


def test_money_json_dumps_writes_a_decimal_held_in_a_list_or_tuple() -> None:
    """Nested money is still money: the walk reaches inside sequences."""

    out = money_owner.money_json_dumps(
        {"xs": [Decimal("9.00")], "ys": (Decimal("10.05"), Decimal("11.00"))}
    )
    assert '"xs": [9.00]' in out
    assert '"ys": [10.05, 11.00]' in out
    parsed = json.loads(out, parse_float=Decimal, parse_int=Decimal)
    assert [str(v) for v in parsed["xs"] + parsed["ys"]] == ["9.00", "10.05", "11.00"]


def test_money_json_dumps_writes_the_same_amount_twice_as_two_exact_numbers() -> None:
    """Each Decimal occurrence gets its own placeholder, so repeats are exact too."""

    out = money_owner.money_json_dumps({"a": Decimal("9.00"), "b": Decimal("9.00")})
    assert out.count('"a": 9.00') == 1
    assert out.count('"b": 9.00') == 1
    assert json.loads(out, parse_float=Decimal, parse_int=Decimal) == {
        "a": Decimal("9.00"),
        "b": Decimal("9.00"),
    }


@pytest.mark.parametrize("not_money", ("NaN", "-NaN", "sNaN", "Infinity", "-Infinity"))
def test_money_json_dumps_refuses_a_decimal_that_is_not_a_number(not_money: str) -> None:
    """Fail closed: ``NaN`` is not money and is not a JSON number either."""

    with pytest.raises(ValueError):
        money_owner.money_json_dumps({"v": Decimal(not_money)})


def test_money_json_dumps_refuses_a_placeholder_collision() -> None:
    """A payload that already carries the token text would make the swap a guess,
    so the writer refuses rather than shipping JSON it cannot vouch for."""

    with pytest.raises(ValueError):
        money_owner.money_json_dumps({"a": Decimal("9.00"), "b": "__hm_decimal__0__"})


def test_the_owner_holds_exactly_one_rounding_statement() -> None:
    """``quantise`` is expressed through ``quantise_half_up``, so the module has one
    rounding call — a second one beside it is the drift this whole file exists for."""

    source = (_SRC_ROOT / "core" / "money.py").read_text(encoding="utf-8")
    assert source.count(".quantize(") == 1


# ── 5. Source invariant: no money through float anywhere under src ─────────────

_SRC_ROOT: Final[Path] = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: The modules that carry customer money or commission amounts. They no longer
#: bound the scan — the whole tree under ``src/ai_market_monitor`` is scanned — but
#: the invariant still fails loudly if one of them moves, and they are where the
#: scanned floats would hurt most.
_MONEY_MODULES: Final[tuple[str, ...]] = (
    "services/billing.py",
    "services/plan_replacements.py",
    "services/affiliate.py",
    "services/affiliate_attribution.py",
)

#: A ``float(`` call, plus the name being assigned to on the same line — the defect
#: can hide in either end: ``"price_amount": float(amount)`` or ``refund_usd =
#: float(raw)``.
_FLOAT_SITE = re.compile(
    r"(?:(?P<target>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)?(?<![\w.])float\s*\((?P<args>[^)]*)\)"
)
#: Money words, matched as substrings so underscore names are caught too
#: (``cost_usd``, ``refund_usd``). False positives are acceptable for a scan this
#: narrow — a non-money use of ``float`` in these four modules must be renamed,
#: not routed around the rule.
_MONEY_NAME = re.compile(
    r"(?i)(amount|price|refund|owed|commission|cents|subtotal|discount|usd|charge|fee)"
)

#: Every money-named ``float(`` the whole-``src`` scan is allowed to see, keyed by
#: ``(relative path, stripped line)``, with the reason it is not customer money.
#: A *new* money float anywhere else — in a money module or not — fails the scan,
#: which is the point: the old scan only watched four files, and the reported
#: defect (``plan_offer_payload``) lived in a fifth.
#:
#: Interpretation to surface in the report: "no float on a money value" is enforced
#: as "no float on a **customer** money value anywhere in src". Market prices and
#: model/token spend share the vocabulary words (price/amount/usd) but are not money
#: a person is charged; a float there is a metrics-layer type. Each entry below is
#: one of those two kinds, and says which.
_NON_CUSTOMER_MONEY_FLOAT: Final[dict[tuple[str, str], str]] = {
    # ── market data: a crypto/asset price or a displayed percentage, never a charge ──
    (
        "api/routers/dashboard.py",
        '"percent_off": float(priced.percent),',
    ): "a display percentage taken off a price, not an amount charged",
    (
        "api/routers/dashboard_api.py",
        '"stop_price": float(setup.stop_price) if setup.stop_price else None,',
    ): "a coin stop-price on a monitor (market data), not customer money",
    (
        "api/routers/dashboard_api.py",
        '"target_price": float(setup.target_price) if setup.target_price else None,',
    ): "a coin target-price on a monitor (market data), not customer money",
    (
        "engine/evaluator.py",
        "abs(current.close - float(trigger_price)) / float(trigger_price) * 100",
    ): "a candle close against a trigger price (market data), not customer money",
    (
        "engine/evaluator.py",
        "if float(trigger_price)",
    ): "the zero-guard on that same market trigger price",
    (
        "services/market_preview.py",
        "(float(price), float(amount))",
    ): "an order-book price/quantity pair in a market preview, not money charged",
    (
        "services/market_preview.py",
        "if float(price) > 0 and float(amount) > 0",
    ): "the zero-guards on that same order-book pair",
    (
        "services/market_preview.py",
        'or float(item.get("price") or 0) * float(item.get("amount") or 0),',
    ): "notional value of an order-book row (market data), not money charged",
    (
        "services/verified_strategy.py",
        "start_price = float(candles[0].close)",
    ): "the first candle price of a backtest window (market data)",
    (
        "services/verified_strategy.py",
        "end_price = float(candles[-1].close)",
    ): "the last candle price of a backtest window (market data)",
    (
        "telegram/rendering.py",
        "target_prices.append(float(price))",
    ): "an alert's coin target prices rendered for chat (market data), not money",
    # ── model/token spend telemetry: the metrics layer is float-typed by design ──
    (
        "services/ai_spend.py",
        "cost_usd=float(actual_cost_usd),",
    ): "model spend telemetry in dollars — a measured cost, not a customer charge",
    (
        "services/setup_chat_agent.py",
        '- float(plan_usage.get("_setup_reserved_cost_usd") or 0.0),',
    ): "reserved model cost for budget maths (token spend telemetry)",
    (
        "services/setup_chat_agent.py",
        'prior_reserved = float(prior_usage.get("_setup_reserved_cost_usd") or 0.0)',
    ): "reserved model cost for budget maths (token spend telemetry)",
    (
        "services/setup_chat_agent.py",
        'planner_reserved = float(planner_usage.get("_setup_reserved_cost_usd") or 0.0)',
    ): "reserved model cost for budget maths (token spend telemetry)",
    (
        "services/setup_chat_agent.py",
        'float(usage.get("_setup_reserved_cost_usd") or 0.0), 9',
    ): "reserved model cost rounded for a log line (token spend telemetry)",
    (
        "services/setup_chat_agent.py",
        'actual = float(usage.get("_setup_combined_actual_cost_usd") or 0.0)',
    ): "actual model cost for budget maths (token spend telemetry)",
    (
        "services/system_brain_agent.py",
        'float(total["estimated_cost_usd"])',
    ): "an estimated model-spend figure for the admin panel (token spend telemetry)",
}


def _money_float_lines(source: str) -> list[str]:
    """Lines where ``float(`` is applied to money — by argument or by destination."""

    hits: list[str] = []
    for number, line in enumerate(source.splitlines(), start=1):
        for match in _FLOAT_SITE.finditer(line):
            names = f"{match.group('target') or ''} {match.group('args')}"
            if _MONEY_NAME.search(names):
                hits.append(f"{number}: {line.strip()}")
    return hits


def _src_money_float_hits() -> list[tuple[str, str, str]]:
    """(relative path, line, hit-text) for every money-named ``float(`` under src."""

    hits: list[tuple[str, str, str]] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(_SRC_ROOT).as_posix()
        for hit in _money_float_lines(path.read_text(encoding="utf-8")):
            _, _, stripped = hit.partition(": ")
            hits.append((relative, stripped, hit))
    return hits


def test_no_money_amount_is_pushed_through_a_binary_float() -> None:
    """No **customer** money value anywhere in ``src`` may pass through ``float``.

    The scan used to watch four service modules; the reported defect lived outside
    them, in ``core/plans.py``, where every landing-page price went through
    ``float``. It now walks every ``.py`` under ``src/ai_market_monitor``. The only
    way a line is acceptable is by being named, with a reason, in
    ``_NON_CUSTOMER_MONEY_FLOAT`` — so a new money float anywhere fails, and so
    does the old defect shape in ``core/plans.py`` until it is fixed.
    """

    for relative in _MONEY_MODULES:
        assert (_SRC_ROOT / relative).exists(), (
            f"{relative} moved or vanished — update this invariant with it"
        )
    offenders = [
        f"{relative}:{hit}"
        for relative, stripped, hit in _src_money_float_hits()
        if (relative, stripped) not in _NON_CUSTOMER_MONEY_FLOAT
    ]
    assert not offenders, (
        "money passed through float():\n"
        + "\n".join(offenders)
        + "\nMoney goes to a provider through core.money.wire_json_body/wire_text and "
        "to a page through core.money.money_json_dumps (the one owners); "
        "float(Decimal) is not the same number. If this line is genuinely not "
        "customer money (market data, model spend), add it to "
        "_NON_CUSTOMER_MONEY_FLOAT with the reason."
    )


def test_the_float_scan_sees_the_defect_and_is_not_blind() -> None:
    """The scan must catch the exact shape it exists for, and leave alone the
    non-money uses of float that are correct."""

    # Positive sample: the reported defect, verbatim.
    assert _money_float_lines('            "price_amount": float(amount),') == [
        '1: "price_amount": float(amount),'
    ]
    # Positive sample: the same defect hiding in the name being assigned to.
    assert _money_float_lines("        refund_usd = float(value)") == [
        "1: refund_usd = float(value)"
    ]
    # Positive sample: the landing-page shape from core/plans.py.
    assert _money_float_lines('        "monthlyPrice": float(charged) if ok else None,') == [
        '1: "monthlyPrice": float(charged) if ok else None,'
    ]
    # Negative samples: correct, non-money float uses must not be flagged.
    assert _money_float_lines(
        "        deadline_seconds=float(self.settings.openai_timeout_seconds),"
    ) == []
    assert _money_float_lines('series.total = float("inf")') == []
    assert _money_float_lines("            threshold=float(threshold),") == []
    assert _money_float_lines("    return _float(value) if value is not None else None") == []


def test_the_float_allow_list_is_reasoned_current_and_never_money() -> None:
    """The allow-list is an explanation, not a hiding place.

    Every entry carries a reason; no entry sits in a money module or in
    ``core/plans.py`` (the reported defect must be *gone*, not excused); every entry
    still names a line that exists today (a stale entry would mask a future float
    wearing the same text); and the two shapes the scan is allowed to see through
    the list — a market price and a model cost — are still caught by the scanner
    itself, so it is the list doing the work, not a blind scan.
    """

    assert all(reason.strip() for reason in _NON_CUSTOMER_MONEY_FLOAT.values())
    for relative, stripped in _NON_CUSTOMER_MONEY_FLOAT:
        assert relative not in _MONEY_MODULES, f"{relative} is a money module"
        assert relative != "core/plans.py", (
            f"core/plans.py price text must be fixed, not allow-listed: {stripped!r}"
        )
        # The scanner still sees each allow-listed line — it is the list, not the
        # pattern, that keeps it.
        assert _money_float_lines(stripped), (
            f"allow-listed line the scanner cannot see: {stripped!r}"
        )
    present = {(relative, stripped) for relative, stripped, _ in _src_money_float_hits()}
    stale = sorted(set(_NON_CUSTOMER_MONEY_FLOAT) - present)
    assert not stale, f"stale allow-list entries (delete them): {stale}"
    # And the reported defect shapes are NOT in the list: a money float in the
    # offer payload or an invoice body always fails.
    assert not any("float(charged)" in stripped for _, stripped in _NON_CUSTOMER_MONEY_FLOAT)
    assert not any(
        "price_amount" in stripped for _, stripped in _NON_CUSTOMER_MONEY_FLOAT
    )


# ── 6. Source invariant: every money quantize goes through the owner ───────────

#: Money words for the rounding scan — a line that both calls ``.quantize(`` and
#: names money has bypassed the owner. ``percent``/``paid``/``total`` join the
#: float scan's words because a rounding site is identified by its line, not by a
#: call argument.
_MONEY_QUANTIZE_NAME = re.compile(
    r"(?i)(amount|price|refund|owed|commission|charge|fee|discount|usd|percent|paid|total)"
)


def _money_quantize_lines(source: str) -> list[str]:
    """Lines where ``.quantize(`` is applied to something named like money."""

    hits: list[str] = []
    for number, line in enumerate(source.splitlines(), start=1):
        if ".quantize(" in line and _MONEY_QUANTIZE_NAME.search(line):
            hits.append(f"{number}: {line.strip()}")
    return hits


def test_no_money_is_rounded_outside_the_owner() -> None:
    """``core/money.py`` holds the system's only money rounding call.

    Five paths quantised with decimal's default ``ROUND_HALF_EVEN`` while the owner
    (and the discount arithmetic) say ``ROUND_HALF_UP`` — a half cent rounded two
    ways in one product. Every money ``.quantize(`` must now be a call to
    ``core.money.quantise_half_up``; the scan proves no line in ``src`` carries a
    money word and a raw ``.quantize(`` at once, anywhere except the owner itself.
    """

    offenders: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(_SRC_ROOT).as_posix()
        if relative == "core/money.py":
            continue  # the owner: the one place a rounding call may sit
        for hit in _money_quantize_lines(path.read_text(encoding="utf-8")):
            offenders.append(f"{relative}:{hit}")
    assert not offenders, (
        "money rounded beside the owner:\n"
        + "\n".join(offenders)
        + "\nUse core.money.quantise_half_up(value, quantum) — the one ROUND_HALF_UP "
        "statement. (services/trials.py quantises a scan-coverage *fraction*, which "
        "is not money, and its line must not name money.)"
    )


def test_the_quantize_scan_sees_money_and_leaves_fractions_alone() -> None:
    """Self-check: the scan catches the reported shapes and does not flag the
    non-money fraction it shares a method name with."""

    # The half-cent commission, verbatim shape: caught.
    assert _money_quantize_lines(
        '        earned = (charged * percent / Decimal("100")).quantize(Decimal("0.01"))'
    )
    # ``x.quantize(Decimal("0.01"))`` on a money name: caught.
    assert _money_quantize_lines('    commission = x.quantize(Decimal("0.01"))') == [
        '1: commission = x.quantize(Decimal("0.01"))'
    ]
    # The trials coverage fraction — the same method, not money: left alone.
    assert _money_quantize_lines(
        "        cycle.successful_scan_coverage = coverage.quantize(Decimal(\"0.00001\"))"
    ) == []


# ── 7. The payload and the shipped page: Decimal in, bare number out ────────────

_PRICE_FIELDS: Final[tuple[str, ...]] = (
    "monthlyPrice",
    "annualPrice",
    "originalMonthlyPrice",
    "fullMonthlyPrice",
)


@pytest.mark.parametrize("code", sorted(PLAN_DEFINITIONS))
def test_plan_offer_payload_prices_are_decimals_not_floats(code: str) -> None:
    """Every price the payload carries is a ``Decimal`` (or absent, as ``None``).

    Existing callers are safe because ``Decimal == float`` compares by value, the
    templates use ``| int``, and the JSON boundary now writes exact numbers. A float
    here would re-open the landing-page defect the moment ``tojson`` touched it.
    """

    payload = plan_offer_payload(code, now=PROMOTION_ENDS_AT - timedelta(days=1))
    for field in _PRICE_FIELDS:
        value = payload[field]
        assert not isinstance(value, float), f"{code}.{field} is a float: {value!r}"
        assert value is None or isinstance(value, Decimal), (
            f"{code}.{field} is {type(value).__name__}, not the owner's Decimal/None"
        )
    for purchasable in PURCHASABLE_PLAN_CODES:
        assert isinstance(plan_offer_payload(purchasable)["monthlyPrice"], Decimal)
        assert isinstance(plan_offer_payload(purchasable)["fullMonthlyPrice"], Decimal)


def _landing_html(plans: list[dict[str, object]]) -> str:
    """Render the shipped React landing template with these plan payloads.

    The real ``hilal/public/react_site.html`` is read from the repository — the
    test cannot pass by editing a template, because no template is edited here.
    Only the two context blocks the page pipes through ``| tojson`` need real
    values (every remaining hole renders as Jinja's empty ``Undefined``).
    """

    templates = register(Jinja2Templates(directory=str(_SRC_ROOT / "templates")))

    def landing(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "hilal/public/react_site.html",
            {
                "title": "Halal Trading With Clarity",
                "description": "Halal crypto monitoring.",
                "robots_content": "index,follow",
                "canonical_url": "http://testserver/",
                "site_name": "Hilal Markets",
                "social_title": "Hilal Markets",
                "social_description": "Halal crypto monitoring.",
                "og_image_url": "http://testserver/static/preview.png",
                "og_image_alt": "Hilal Markets",
                "public_chat_enabled": False,
                "site_visit_measurement_enabled": False,
                "cookie_consent_version": "test-1",
                "public_analytics_enabled": False,
                "marketing_consent_enabled": False,
                "analytics_runtime_config": {
                    "gtmId": None,
                    "xPixelEnabled": False,
                    "xPixelId": None,
                },
                "legal_name": "Test",
                "company_address": "Test",
                "governing_law": "Test",
                "privacy_email": "privacy@example.test",
                "support_email": "support@example.test",
                "legal_review_required": False,
                "site_chrome_runtime_config": {},
                "methodology_runtime_config": {},
                "waitlist_mode": False,
                "waitlist_eyebrow": "",
                "waitlist_headline": "",
                "waitlist_body": "",
                "waitlist_cta_label": "",
                "waitlist_url": "#waitlist",
                "billing_enabled": True,
                "card_checkout_available": True,
                "crypto_checkout_available": False,
                "whatsapp_operational": False,
                "annual_billing_supported": False,
                "public_pricing_plans": plans,
                "public_plan_comparison": [],
                "promotion_ends_at": PROMOTION_ENDS_AT.isoformat(),
                "promotion_active": True,
                "json_ld": [],
            },
        )

    app = Starlette(
        routes=[
            Route("/", landing),
            Route("/cookies", lambda request: PlainTextResponse("cookies"), name="public_cookies"),
            Mount(
                "/static",
                app=StaticFiles(directory=str(_SRC_ROOT / "static")),
                name="static",
            ),
        ]
    )
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    return response.text


def test_the_landing_page_embeds_prices_as_bare_exact_numbers() -> None:
    """The browser's money contract, proven in the raw HTML of the shipped page.

    ``Pricing.tsx`` types ``monthlyPrice`` as ``number | null`` and does arithmetic
    on it, so the page must carry a bare JSON number — and the number must keep the
    exact cents text (``9.00``, not the ``9.0`` a float writes). This renders the
    real ``react_site.html`` through the real registered environment: no template
    and no bundle changed to get here, and the template still embeds
    ``public_pricing_plans | tojson`` exactly as it always did.
    """

    template_text = (_SRC_ROOT / "templates" / "hilal" / "public" / "react_site.html").read_text(
        encoding="utf-8"
    )
    assert '"plans": public_pricing_plans' in template_text, "the page stopped embedding the plans"
    assert "| tojson" in template_text, "the plans stopped travelling through tojson"

    when = PROMOTION_ENDS_AT - timedelta(days=1)
    plans = [{"code": code, **plan_offer_payload(code, now=when)} for code in PUBLIC_PLAN_CODES]
    html = _landing_html(plans)

    # ``trader`` is 9.00 during the offer: a bare number, exact cents text.
    assert re.search(r'"monthlyPrice":\s*9\.00(?![0-9])', html), (
        "the landing page does not carry 9.00 as an exact bare number — "
        "a float prints it as 9.0 (D3 on the browser path) or a writer quoted it (H-3)"
    )
    assert '"monthlyPrice": "' not in html, "a page price left quoted"
    match = re.search(r"window\.HilalMarketsRuntimeConfig = (\{.*?\});", html, re.DOTALL)
    assert match, "the landing page published no runtime config"
    commerce = json.loads(match.group(1), parse_float=Decimal, parse_int=Decimal)["commerce"]
    by_code = {plan["code"]: plan for plan in commerce["plans"]}
    assert str(by_code["trader"]["monthlyPrice"]) == "9.00"
    assert str(by_code["trader"]["fullMonthlyPrice"]) == "9.00"
    assert str(by_code["trader"]["originalMonthlyPrice"]) == "15.00"
    assert str(by_code["pro"]["monthlyPrice"]) == "17.00"
    assert by_code["trader"]["monthlyPrice"] == Decimal("9")  # still the same money


def test_the_page_json_writer_is_installed_in_every_template_environment() -> None:
    """``register`` is the one door every router's Jinja environment goes through;
    wiring the writer there is what keeps ``| tojson`` alive on a ``Decimal``.
    (Before it, ``tojson`` had ``json.dumps_function = None`` and raised
    ``TypeError: Object of type Decimal is not JSON serializable``.)"""

    templates = register(Jinja2Templates(directory=str(_SRC_ROOT / "templates")))
    assert templates.env.policies["json.dumps_function"] is money_owner.money_json_dumps
