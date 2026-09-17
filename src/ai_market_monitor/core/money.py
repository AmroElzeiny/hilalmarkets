"""One owner for what a money amount looks like when it leaves this system.

Money inside the application is a ``Decimal``. A payment provider's wire format is
JSON text. Between the two sits the one conversion where exact money can stop being
exact: a ``float`` is binary, and most two-decimal amounts (``10.05``, ``6.03``) have
no binary representation at all — the value changes at about the twentieth decimal,
and whole-cent amounts like ``9.00`` silently print as ``9.0``. A price that goes
through ``float`` on its way out is no longer the price that was decided here.

This module is the only place that decision is translated. Anything that sends a
fiat amount to a payment company must call :func:`wire_json_body` (or
:func:`wire_number`, when the caller builds the body itself) — never ``float()``,
never ``str()`` on an unquantised Decimal, and never a hand-written ``quantize`` at
the call site.

**The wire format (verified against the provider's own spec).**
NOWPayments' published OpenAPI types ``price_amount`` on ``POST /invoice`` as
``number`` (``format: double``), its official client library takes
``int|float``, and its own request examples send an unquoted number
(``"price_amount": 3999.5``, ``"price_amount": 1000``). The help-centre article
"API and endpoint description" (https://nowpayments.zendesk.com/hc/en-us/articles/
21345824322717-API-and-endpoint-description) carries two tables for the same
endpoint that contradict each other — ``price_amount | Number`` in one and
``price_amount | String`` in the other — so it settles nothing and the spec wins.

The amount therefore leaves this system as a **bare JSON number whose digits are the
exact quantised decimal text** — for example ``"price_amount":9.00``, unquoted, with
the trailing zero kept. That is not the same thing as a float: the standard library's
JSON encoder can only write a float or quote a string, so the number is placed by
:func:`wire_json_body`, which serialises everything else and drops the exact digits in
as a number token. No float is ever created, on either path.

The other two companies this product uses are not asked for an amount at all:
Creem's checkout carries a ``product_id`` and Stripe's carries a ``price_id``
(both ``del`` the ``amount`` argument in ``services/billing.py``), so NOWPayments
is currently the only provider that receives a money value from us. When another
one starts, it must use this owner too.

Rounding matches ``core/plans.price_after_percent`` — the owner of discount
arithmetic — which quantises to the cent with ``ROUND_HALF_UP``. This module never
re-decides what a charge is; it only decides how a decided amount is written down.
Since WP3 the rounding itself lives here too: :func:`quantise_half_up` is the
system's one ``.quantize`` on money, :func:`json_money_number` is the exact amount
handed to a page, and :func:`money_json_dumps` — wired as the Jinja ``tojson``
writer in ``api/template_env.py`` — is what makes that amount arrive in the
browser as a bare JSON number with its cents text kept. The same D3/H-3 rule, at
the second money boundary this product has.

The module owns one reading of stored money as well: :func:`money_kept`, the answer
to "what does this payment still hold after what came back?". Every money reader uses
it, because a refund that is subtracted twice — or in one place and not another — is
the same class of disagreement this module exists to prevent on the way out.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

#: Digits to the right of the decimal point for each fiat currency we hold money in.
#: A small explicit map rather than a full catalogue: every currency this product can
#: be priced in must be named here deliberately, and the default below is the one the
#: two listed currencies already share, so an unmapped-but-real minor-unit currency
#: (JPY, KWD) must be *added*, not quietly rounded to the wrong exponent.
FIAT_MINOR_UNITS: Final[dict[str, int]] = {
    "USD": 2,
    "EUR": 2,
}

#: Assumed for a currency not in the map above. Two is what this product prices in
#: today; see the map's comment for why an odd currency has to be named explicitly.
DEFAULT_FIAT_MINOR_UNITS: Final[int] = 2


def minor_units(currency: str) -> int:
    """How many minor-unit digits ``currency`` has (USD/EUR → 2)."""

    return FIAT_MINOR_UNITS.get((currency or "").strip().upper(), DEFAULT_FIAT_MINOR_UNITS)


def quantise_half_up(value: Decimal, quantum: Decimal) -> Decimal:
    """``value`` to ``quantum``, halves away from zero — the one money rounding.

    This is the system's only ``.quantize`` on money. Decimal's default rounding is
    ``ROUND_HALF_EVEN`` (banker's rounding), which records ``1.00 × 12.5% = 0.125``
    as ``0.12`` while this product's discount owner rounds the same half cent to
    ``0.13``: two rules, one product, money in the middle. Every money path —
    :func:`quantise` here, the invoice amounts, the commission ledger, the
    replacement payout, the receipt — reaches ``ROUND_HALF_UP`` through this call
    and holds no rounding statement of its own.
    """

    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def quantise(amount: Decimal, currency: str) -> Decimal:
    """``amount`` to the currency's minor unit, ``ROUND_HALF_UP``.

    Accepts (and returns) a ``Decimal`` only — the exactness of this whole module
    depends on no binary float ever joining, and ``Decimal`` is how money travels
    inside this application.
    """

    exponent = Decimal(1).scaleb(-minor_units(currency))
    return quantise_half_up(amount, exponent)


def json_money_number(amount: Decimal, currency: str = "USD") -> Decimal:
    """The amount to be written as a JSON **number**, quantised, still a ``Decimal``.

    Pages do arithmetic on money (the React pricing card adds a price up), so the
    browser must receive a number, not a string — but *this* is the last exact
    stop, and ``float`` here is the defect it was on the wire: ``float`` prints
    ``Decimal("9.00")`` as ``9.0`` and cannot represent ``10.05`` at all. Hand the
    value to :func:`money_json_dumps` (wired as the Jinja ``tojson`` writer) and
    it reaches the browser as ``9.00`` — a bare JSON number with its cents text.
    """

    return quantise(amount, currency)


#: A complete JSON number token (RFC 8259): an optional minus, an integer part with
#: no redundant leading zero, an optional fraction and an optional exponent. Used to
#: prove that what :func:`wire_number` writes can sit inside a JSON body unquoted —
#: ``Decimal("NaN")`` and its friends print as text too, but not as valid JSON.
_JSON_NUMBER_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?(?:0|[1-9][0-9]*))?\Z"
)


def wire_number(amount: Decimal, currency: str) -> str:
    """The exact amount written the way a JSON **number** is written —
    ``Decimal("9")`` → ``"9.00"``.

    ``str`` of a quantised ``Decimal`` keeps every minor-unit digit (never the
    ``9.0`` that a float would print) and needs no exponent, so it is already valid
    JSON number syntax. This returns the digits only; putting them in a body
    unquoted is :func:`wire_json_body`'s job.

    Raises:
        ValueError: if ``amount`` is not a finite number. A ``NaN`` or an infinity is
            not money, and "NaN" is not a JSON number either — it must be refused
            here rather than sent to a payment company or written into a body that
            the receiver cannot parse.
    """

    if not amount.is_finite():
        raise ValueError(
            f"{amount} is not a finite money amount; a payment amount must be a number."
        )
    text = str(quantise(amount, currency))
    if not _JSON_NUMBER_TOKEN.match(text):  # pragma: no cover - guarded by is_finite()
        raise ValueError(
            f"{amount} quantised to {text!r}, which is not a valid JSON number token."
        )
    return text


def wire_text(amount: Decimal, currency: str) -> str:
    """The exact amount as plain text — ``Decimal("9.00")`` → ``"9.00"``.

    The same digits :func:`wire_number` writes, for the rare case of a body that
    genuinely wants the amount **quoted**. Nothing in this product does today: the
    only provider that receives an amount (NOWPayments) wants a number, so
    ``services/billing.py`` goes through :func:`wire_json_body`. Kept because its
    exactness rule is the one this module owns and it is pinned by
    ``tests/unit/test_invariant_money_on_the_wire.py``.
    """

    return wire_number(amount, currency)


def wire_json_body(
    payload: Mapping[str, object],
    *,
    number_field: str,
    amount: Decimal,
    currency: str,
) -> bytes:
    """``payload`` as JSON bytes, with ``number_field`` carrying ``amount`` as a bare
    JSON **number** with the exact quantised digits.

    ``{"price_currency": "usd"}, number_field="price_amount", Decimal("9"), "USD"``
    →  ``b'{"price_currency":"usd","price_amount":9.00}'``.

    Why the body is built here instead of by ``json.dumps``: the standard encoder
    knows only Python types, and of those it turns a float into a number (inexact —
    the D3 defect) and a string into a quoted value (the wrong wire type — the H-3
    defect). A ``Decimal`` is not serialisable at all. So the amount travels through
    the encoder as a unique placeholder and the quoted placeholder is swapped, byte
    for byte, for the number text from :func:`wire_number`. Everything else in the
    body is written by ``json.dumps`` exactly as it would have been.

    The encoders' settings match what httpx uses for a ``json=`` body
    (``ensure_ascii=False``, ``separators=(",", ":")``, ``allow_nan=False``), so
    switching a call to this function changes the amount's type and nothing else.

    Fails closed — it raises ``ValueError`` rather than shipping a body it cannot
    vouch for — if ``number_field`` is already in the payload (the amount must come
    through this owner), or if the placeholder is not unique in the serialised text
    (a swap would then be a guess).

    The caller must set ``Content-Type: application/json`` itself, as it already does
    for httpx: this returns finished bytes, so httpx is asked for ``content=`` and
    ``content=`` alone does not imply a content type.
    """

    if number_field in payload:
        raise ValueError(
            f"{number_field} is already in the payload; the amount goes on the wire "
            "through this function, not beside it."
        )
    number_text = wire_number(amount, currency)
    placeholder = f"__hm_wire_number__{number_field}__"
    body = json.dumps(
        {**payload, number_field: placeholder},
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    quoted = json.dumps(placeholder, ensure_ascii=False)
    if body.count(quoted) != 1:
        raise ValueError(
            f"{number_field} cannot be placed safely: the placeholder appears "
            f"{body.count(quoted)} times in the serialised payload."
        )
    return body.replace(quoted, number_text).encode("utf-8")


#: Prefix of the placeholder :func:`money_json_dumps` swaps each ``Decimal`` for.
#: A deliberate fixed token, so a payload that already carries one fails the
#: uniqueness check below instead of being quietly rewritten.
_DECIMAL_PLACEHOLDER: Final[str] = "__hm_decimal__"


def money_json_dumps(value: object, **kwargs: object) -> str:
    """``json.dumps`` for pages: every ``Decimal`` becomes a **bare JSON number**
    carrying its exact text — ``Decimal("9.00")`` must come out as ``9.00``, not
    ``9.0`` and not ``"9.00"``.

    This is wired as Jinja's ``tojson`` writer (``templates.env.policies`` in
    ``api/template_env.py``), which is how the React landing page and the
    dashboard's plan cards keep their ``number`` contract while the payload holds
    ``Decimal``: the stdlib encoder can only write a float (inexact, and it drops
    ``9.00``'s cents text — the D3 defect on the browser path) or quote a string
    (the wrong type for arithmetic), and an un-wired ``Decimal`` simply raises
    ``TypeError: Object of type Decimal is not JSON serializable``.

    The technique is the one :func:`wire_json_body` already proves on the provider
    wire: walk the structure, swap every ``Decimal`` for a unique placeholder
    string, serialise everything else with ``json.dumps`` — passing ``**kwargs``
    straight through, so Jinja's ``sort_keys=True`` (and any ``indent``) still
    applies — then drop each placeholder's quoted form back in as the number text.

    Fails closed with ``ValueError``, never a silent rewrite: a non-finite
    ``Decimal`` is not money and not a JSON number, and a placeholder that is not
    unique in the serialised text would make its swap a guess.
    """

    replacements: dict[str, str] = {}

    def walk(node: object) -> object:
        if isinstance(node, Decimal):
            if not node.is_finite():
                raise ValueError(
                    f"{node} is not a finite money amount; it cannot be written "
                    "as a JSON number on a page."
                )
            token = f"{_DECIMAL_PLACEHOLDER}{len(replacements)}__"
            # ``str`` of a Decimal is its exact text (trailing zeros kept). A
            # quantised amount is always plain minor-unit digits; an unquantised
            # one may carry an exponent (``1E+2``), which is still valid JSON
            # number syntax — no float is created on either path.
            replacements[token] = str(node)
            return token
        if isinstance(node, Mapping):
            return {key: walk(item) for key, item in node.items()}
        if isinstance(node, (list, tuple)):
            # json.dumps writes a tuple as a JSON array anyway; walking it as a
            # list changes no bytes and reaches a Decimal held inside it.
            return [walk(item) for item in node]
        return node

    # ``json.dumps`` is typed option-by-option; the policy that reaches here is
    # Jinja's (``sort_keys``/``indent``), so the options travel untyped on purpose.
    dumps: Callable[..., str] = json.dumps
    body = dumps(walk(value), **kwargs)
    for token, number_text in replacements.items():
        quoted = json.dumps(token)
        if body.count(quoted) != 1:
            raise ValueError(
                "a Decimal cannot be placed safely: the placeholder "
                f"{token!r} appears {body.count(quoted)} times in the serialised "
                "text, so the swap would be a guess."
            )
        body = body.replace(quoted, number_text)
    return body


def money_kept(paid_amount: Decimal, refunded_amount: Decimal | None) -> Decimal:
    """The money this payment still holds: paid minus what came back, never below zero.

    This is the single owner of that reading. It sits beside the wire owners because
    it answers the same question the wire does — what is this payment worth in fiat —
    just read back rather than sent out. Every reader of a payment's value (the plan-
    move payout, the affiliate commission, the operations totals and per-payment view)
    calls this and nothing else subtracts ``refunded_amount`` by hand: two readers that
    disagreed about what a payment holds is the whole defect class this fix removes.

    An unknown refund (``None``) and a zero refund both mean nothing came back yet, so
    the whole payment is kept. A partial keeps the remainder. A full or over-full refund
    keeps ``Decimal("0")`` — a payment never holds less than nothing, which is why the
    clamp is here and not left to each caller.
    """

    refunded = refunded_amount or Decimal("0")
    if refunded <= 0:
        return paid_amount
    return max(paid_amount - refunded, Decimal("0"))
