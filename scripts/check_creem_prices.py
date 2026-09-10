"""Does the card company charge the number the website shows, and know the same codes?

Card checkout sends Creem a **product id** and nothing else. Creem then charges whatever
that product is priced at in Creem's own dashboard. The app's price lives in
`core/plans.py`. Those are two separate numbers and nothing keeps them together.

When they drift apart the customer really pays, and then
`BillingService._validate_paid_amount_and_currency` refuses the confirmation because the
amount does not match the checkout — so the money is gone and the plan never starts. That
is the worst outcome this code has.

The **launch price** makes that worse rather than better, because it moves on its own. It
is not a code any more: until ``PROMOTION_ENDS_AT`` the plan simply costs less, and the
moment that instant passes this application starts expecting the normal price. Creem's
product price does not change by itself, so **the two Creem products must be re-priced on
the day the offer ends** or every card payment after it will be refused as underpaid.

Discount codes have the same shape of problem. The crypto route applies them here, in this
application. The card route cannot: a card buyer types one on Creem's own page. A code this
product has **retired** but Creem still holds is the dangerous case — it comes off a price
that is already the launch price. Nothing offline can see Creem, so this script is the only
thing that can catch any of it.

Run this whenever `core/plans.py` changes, and after creating or editing a product or a
discount in Creem:

    .venv/Scripts/python scripts/check_creem_prices.py
    .venv/Scripts/python scripts/check_creem_prices.py --env-file .env.production

It makes one read-only call per configured product plus one for the launch code. It prints
no key and no product id. Exit code 0 means Creem agrees with the website about every
price and about the launch code.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_market_monitor.core.config import Settings  # noqa: E402
from ai_market_monitor.core.plans import (  # noqa: E402
    PROMOTION_ENDS_AT,
    PUBLIC_PLAN_PRESENTATIONS,
    RETIRED_DISCOUNT_CODES,
    effective_monthly_price,
    promotional_monthly_price,
)


def expected_price(product_key: str) -> Decimal | None:
    """What this product must cost **today**, taken from the one owner of the prices.

    "Today" is not a figure of speech: while the launch offer runs this is the launch
    price, and the day it ends the same call returns the normal price instead.
    """

    plan_code, _, period = product_key.rpartition("_")
    if period == "monthly":
        return effective_monthly_price(plan_code)
    if period == "annual":
        presentation = PUBLIC_PLAN_PRESENTATIONS.get(plan_code)
        return presentation.annual_price if presentation else None
    # A trial product charges nothing up front, so there is no number to agree on.
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.production")
    arguments = parser.parse_args()

    settings = Settings(_env_file=arguments.env_file)  # type: ignore[call-arg]
    key = settings.creem_api_key
    if key is None or not key.get_secret_value().strip():
        print("No Creem API key in this environment. Nothing to check.")
        return 0
    if not settings.creem_product_ids:
        print("No Creem products in this environment. Nothing to check.")
        return 0

    base = str(settings.creem_api_base).rstrip("/")
    problems = 0
    running = [
        (code, promotional_monthly_price(code))
        for code in PUBLIC_PLAN_PRESENTATIONS
        if promotional_monthly_price(code) is not None
    ]
    if running:
        offer = ", ".join(f"{code} at {price} USD" for code, price in running)
        print(
            f"Launch offer is running ({offer}) and ends {PROMOTION_ENDS_AT:%d %B %Y}. "
            "Re-price these Creem products that day, then run this again.\n"
        )
    async with httpx.AsyncClient(timeout=settings.creem_timeout_seconds) as client:
        for product_key, product_id in sorted(settings.creem_product_ids.items()):
            wanted = expected_price(product_key)
            response = await client.get(
                f"{base}/v1/products",
                params={"product_id": product_id},
                headers={
                    "x-api-key": key.get_secret_value(),
                    "User-Agent": "HilalMarkets/1.0",
                },
            )
            if response.status_code != 200:
                print(f"{product_key}: Creem answered {response.status_code}. Cannot check.")
                problems += 1
                continue
            body = response.json()
            smallest_unit = body.get("price")
            currency = str(body.get("currency") or "")
            state = str(body.get("status") or "")
            mode = str(body.get("mode") or "")
            if wanted is None:
                print(f"{product_key}: no fixed price to compare ({state}, {mode}).")
                continue
            if not isinstance(smallest_unit, int):
                print(f"{product_key}: Creem gave no price to compare.")
                problems += 1
                continue
            charged = Decimal(smallest_unit) / 100
            same = charged == wanted and currency.upper() == "USD"
            print(
                f"{product_key}: Creem charges {charged} {currency or '?'}, "
                f"the website says {wanted} USD "
                f"({state}, {mode}) -> {'same' if same else 'DIFFERENT'}"
            )
            if not same:
                problems += 1
            if state != "active":
                print(f"{product_key}: this product is not active in Creem.")
                problems += 1

        problems += await check_discount_codes(
            client, base, key.get_secret_value(), settings
        )

    if problems:
        print(
            f"\n{problems} problem(s). A customer would be charged a number the website "
            "did not show, and the payment could not be confirmed afterwards."
        )
        return 1
    print("\nCreem agrees with the website about every price and about every code.")
    return 0


def codes_this_deployment_honours(settings: Settings) -> list[tuple[str, Decimal]]:
    """Every code a buyer could type here, and what it is worth.

    One list now: ``BILLING_DISCOUNT_CODES``. The launch offer used to add a code of its
    own; it does not any more, because the launch price is simply the price until the
    offer's timer runs out and nothing is typed to reach it.

    A code on this list is never advertised on a pricing card and is only accepted on the
    crypto route, so Creem not having it is a fact worth printing rather than a fault.
    """

    return sorted(settings.billing_discount_codes.items())


async def check_retired_codes(client: httpx.AsyncClient, base: str, key: str) -> int:
    """Is a code this product has retired still alive in Creem?

    This is the one discount check that can cost money. A card buyer reaches Creem's own
    checkout page, which has a discount box this application does not control. A retired
    code still active there comes off a price that is **already** the launch price, so the
    payment arrives smaller than the amount recorded when the checkout was created — and
    the confirmation is then refused as underpaid. The buyer pays and gets nothing.

    Nothing offline can see Creem's discount list, so this is the only place the fault can
    be found before a customer finds it.
    """

    if not RETIRED_DISCOUNT_CODES:
        return 0
    problems = 0
    print("\nretired codes (these must be switched off in Creem):")
    for code in RETIRED_DISCOUNT_CODES:
        response = await client.get(
            f"{base}/v1/discounts",
            params={"discount_code": code},
            headers={"x-api-key": key, "User-Agent": "HilalMarkets/1.0"},
        )
        if response.status_code == 404:
            print(f"  {code}: Creem does not have it. Good.")
            continue
        if response.status_code != 200:
            print(f"  {code}: Creem answered {response.status_code}. Cannot check.")
            problems += 1
            continue
        state = str(response.json().get("status") or "")
        if state == "active":
            print(
                f"  {code}: STILL ACTIVE in Creem. A card buyer can type it and pay less "
                "than the page shows, and that payment cannot be confirmed afterwards. "
                "Switch this discount off in the Creem dashboard."
            )
            problems += 1
        else:
            print(f"  {code}: Creem has it as '{state}'. Not usable. Good.")
    return problems


async def check_discount_codes(
    client: httpx.AsyncClient, base: str, key: str, settings: Settings
) -> int:
    """Does Creem agree with this deployment about every code, and its percentage?

    Only the card route reads Creem: crypto buyers type the code here and this application
    applies it. But both routes can advertise the same offer, and a code that exists on
    both sides at *different* percentages charges two customers two different prices for
    the same thing — so a disagreement is a fault whichever list the code came from.
    """

    problems = await check_retired_codes(client, base, key)
    wanted = codes_this_deployment_honours(settings)
    if not wanted:
        print("\nNo discount codes are running. Nothing to check in Creem's discounts.")
        return problems

    print("\ndiscount codes:")
    for code, percent in wanted:
        response = await client.get(
            f"{base}/v1/discounts",
            params={"discount_code": code},
            headers={"x-api-key": key, "User-Agent": "HilalMarkets/1.0"},
        )
        if response.status_code == 404:
            print(
                f"  {code}: {percent}% off, crypto only. Creem does not have it, so a "
                f"card buyer cannot use it. Add it to Creem if they should."
            )
            continue
        if response.status_code != 200:
            print(f"  {code}: Creem answered {response.status_code}. Cannot check.")
            problems += 1
            continue
        body = response.json()
        kind = str(body.get("type") or "")
        state = str(body.get("status") or "")
        creem_percent = body.get("percentage")
        if creem_percent is None:
            creem_percent = body.get("amount")
        same = kind == "percentage" and Decimal(str(creem_percent or 0)) == percent
        print(
            f"  {code}: Creem takes off {creem_percent}"
            f"{'%' if kind == 'percentage' else ''}, this deployment says {percent}% "
            f"({state}) -> {'same' if same else 'DIFFERENT'}"
        )
        if not same:
            problems += 1
        if state != "active":
            print(f"  {code}: this discount is not active in Creem.")
    return problems


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
