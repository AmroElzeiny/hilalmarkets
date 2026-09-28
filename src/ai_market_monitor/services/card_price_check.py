"""Does the card company charge the number the website shows?

Card checkout sends Creem a **product id** and nothing else, and Creem charges whatever that
product is priced at in Creem's own dashboard. The website's price lives in
`core/plans.py`. Those are two numbers in two systems, and nothing keeps them together.

When they drift apart the customer really pays, and then
`BillingService._validate_paid_amount_and_currency` refuses the confirmation because the
amount does not match the checkout. The money is gone and the plan never starts — the
worst outcome this product has. It happened on 20 September 2026: the launch price ended
in `core/plans.py`, the Creem products kept the launch prices, and every card checkout
after that asked Creem for one figure while the site recorded another.

The comparison lived only in `scripts/check_creem_prices.py`, which somebody had to
remember to run. It lives here now so two callers share one reading of it:

* the script, which prints every line for a person at a keyboard;
* the daily ``check_card_prices`` worker task, which tells the operator by Telegram the
  first day the two disagree, whether or not anybody remembered.

It also checks that the running offer's code (``HILAL30``) is **not** an active discount
in Creem. The offer is already inside every price; a Creem discount of the same name would
let a card buyer take it off a second time, and that payment would be refused as
underpaid.

Read-only: one ``GET`` per configured product plus one per code. It never prints or
returns a key or a product id.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import (
    PUBLIC_PLAN_PRESENTATIONS,
    current_offer,
    effective_monthly_price,
    promotion_is_active,
)

__all__ = [
    "CardPriceCheck",
    "card_price_check_configured",
    "check_card_prices",
    "expected_card_price",
    "offer_code_problem",
]


@dataclass(frozen=True, slots=True)
class CardPriceCheck:
    """One Creem product compared with the website's price for it today."""

    product_key: str
    expected: Decimal | None
    charged: Decimal | None
    currency: str
    state: str
    mode: str
    #: A sentence for a person, or ``None`` when Creem and the website agree.
    problem: str | None


def expected_card_price(product_key: str) -> Decimal | None:
    """What this Creem product must cost **today**, from the one owner of the prices.

    ``trader_monthly`` → the monthly price a checkout charges now, offer included.
    ``pro_annual`` → the published yearly price. A trial product charges nothing up front,
    so there is no number to agree on and the answer is ``None``.
    """

    plan_code, _, period = product_key.rpartition("_")
    if period == "monthly" and plan_code in PUBLIC_PLAN_PRESENTATIONS:
        return effective_monthly_price(plan_code)
    if period == "annual":
        presentation = PUBLIC_PLAN_PRESENTATIONS.get(plan_code)
        return presentation.annual_price if presentation else None
    return None


def card_price_check_configured(settings: Settings) -> bool:
    """Is there a Creem account and at least one product to compare?"""

    key = settings.creem_api_key
    return bool(key is not None and key.get_secret_value().strip() and settings.creem_product_ids)


def _headers(settings: Settings) -> dict[str, str]:
    key = settings.creem_api_key
    secret = key.get_secret_value() if key is not None else ""
    return {"x-api-key": secret, "User-Agent": "HilalMarkets/1.0"}


async def check_card_prices(
    settings: Settings, client: httpx.AsyncClient
) -> list[CardPriceCheck]:
    """Compare every configured Creem product with what the website charges today."""

    base = str(settings.creem_api_base).rstrip("/")
    results: list[CardPriceCheck] = []
    for product_key, product_id in sorted(settings.creem_product_ids.items()):
        wanted = expected_card_price(product_key)
        try:
            response = await client.get(
                f"{base}/v1/products",
                params={"product_id": product_id},
                headers=_headers(settings),
            )
        except httpx.HTTPError as exc:
            results.append(
                CardPriceCheck(
                    product_key, wanted, None, "", "", "",
                    f"Creem could not be reached ({type(exc).__name__}), so this price "
                    "was not checked.",
                )
            )
            continue
        if response.status_code != 200:
            results.append(
                CardPriceCheck(
                    product_key, wanted, None, "", "", "",
                    f"Creem answered {response.status_code}, so this price was not checked.",
                )
            )
            continue
        body = response.json()
        smallest_unit = body.get("price")
        currency = str(body.get("currency") or "")
        state = str(body.get("status") or "")
        mode = str(body.get("mode") or "")
        charged = (
            Decimal(smallest_unit) / 100
            if isinstance(smallest_unit, int) and not isinstance(smallest_unit, bool)
            else None
        )
        problem: str | None = None
        if wanted is not None and charged is None:
            problem = "Creem gave no price to compare."
        elif wanted is not None and (charged != wanted or currency.upper() != "USD"):
            problem = (
                f"Creem charges {charged} {currency or '?'}, the website charges "
                f"{wanted} USD. Card payments for this plan will be refused after the "
                "customer pays. Change the price of this product in Creem."
            )
        elif state and state != "active":
            problem = f"This product is '{state}' in Creem, not active."
        results.append(
            CardPriceCheck(product_key, wanted, charged, currency, state, mode, problem)
        )
    return results


async def offer_code_problem(settings: Settings, client: httpx.AsyncClient) -> str | None:
    """A sentence when the running offer's code is an active Creem discount, else ``None``.

    ``None`` too when no offer is running, or when Creem could not be asked — the price
    comparison above already reports an unreachable Creem, and one outage is one message.
    """

    offer = current_offer()
    if offer is None or not promotion_is_active():
        return None
    code = offer.code
    base = str(settings.creem_api_base).rstrip("/")
    try:
        response = await client.get(
            f"{base}/v1/discounts",
            params={"discount_code": code},
            headers=_headers(settings),
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    if str(response.json().get("status") or "") != "active":
        return None
    return (
        f"{code} is an active discount in Creem. It is already taken off every price, so "
        "a card buyer who types it pays less than the site recorded and the payment is "
        f"refused. Switch the {code} discount off in Creem."
    )
