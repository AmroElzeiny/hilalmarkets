"""Money must leave this system as the exact money that was decided here.

Two defects live in that one sentence, and this file pins both of them.

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

The rule is asserted for the whole family, not one price:

* every catalogue price in both cycles — ``PLAN_DEFINITIONS`` monthly and
  ``PUBLIC_PLAN_PRESENTATIONS`` annual;
* the launch offers (``PLAN_OFFERS.promotional_monthly_price``) and the price a
  checkout really charges today (``effective_monthly_price``);
* every discount outcome — ``price_after_percent`` over every percent in
  ``settings.billing_discount_codes`` for each of those bases.

and by two source scans: ``float(`` on a money name is banned in the money-carrying
service modules, and the scan proves it can see the defect shape it exists for.

Creem and Stripe are never handed an amount (their checkouts carry a ``product_id`` /
``price_id``; both ``del`` the ``amount`` argument), so NOWPayments is the provider
this wire test drives.

All requests are captured in process — the outbound door is patched, so no test here
can contact a payment company.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Final
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

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
    PUBLIC_PLAN_PRESENTATIONS,
    PURCHASABLE_PLAN_CODES,
    effective_monthly_price,
    price_after_percent,
)
from ai_market_monitor.services import billing as billing_module
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


# ── 3. Source invariant: no money through float in the money-carrying modules ──

_SRC_ROOT: Final[Path] = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: The modules that carry customer money or commission amounts.
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


def _money_float_lines(source: str) -> list[str]:
    """Lines where ``float(`` is applied to money — by argument or by destination."""

    hits: list[str] = []
    for number, line in enumerate(source.splitlines(), start=1):
        for match in _FLOAT_SITE.finditer(line):
            names = f"{match.group('target') or ''} {match.group('args')}"
            if _MONEY_NAME.search(names):
                hits.append(f"{number}: {line.strip()}")
    return hits


@pytest.mark.parametrize("relative", _MONEY_MODULES)
def test_no_money_amount_is_pushed_through_a_binary_float(relative: str) -> None:
    path = _SRC_ROOT / relative
    assert path.exists(), f"{relative} moved or vanished — update this invariant with it"
    offenders = _money_float_lines(path.read_text(encoding="utf-8"))
    assert not offenders, (
        f"{relative} sends money through float():\n"
        + "\n".join(offenders)
        + "\nMoney goes to a provider through core.money.wire_text (the one owner); "
        "float(Decimal) is not the same number."
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
    # Negative samples: correct, non-money float uses must not be flagged.
    assert _money_float_lines(
        "        deadline_seconds=float(self.settings.openai_timeout_seconds),"
    ) == []
    assert _money_float_lines('series.total = float("inf")') == []
    assert _money_float_lines("            threshold=float(threshold),") == []
    assert _money_float_lines("    return _float(value) if value is not None else None") == []
