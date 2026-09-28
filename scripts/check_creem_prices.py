"""Does the card company charge the number the website shows, and know the same codes?

Card checkout sends Creem a **product id** and nothing else. Creem then charges whatever
that product is priced at in Creem's own dashboard. The app's price lives in
`core/plans.py`. Those are two separate numbers and nothing keeps them together.

When they drift apart the customer really pays, and then
`BillingService._validate_paid_amount_and_currency` refuses the confirmation because the
amount does not match the checkout — so the money is gone and the plan never starts. That
is the worst outcome this code has.

An offer makes that worse, because it changes the website's price while Creem's stays
where it was. ``core/plans.CURRENT_OFFER`` (HILAL30, 30% off) moves every monthly price,
so **the Creem products must be re-priced the same day the offer starts, changes or
ends**. On 20 September 2026 the old launch price ended here and not in Creem, and every
card payment after it would have been refused. The comparison now lives in
`services/card_price_check.py`, and the daily ``check_card_prices`` worker task runs it
and tells the operator; this script prints the same comparison in full.

Discount codes have the same shape of problem. The crypto route applies them here, in this
application. The card route cannot: a card buyer types one on Creem's own page. A code this
product has **retired**, or the offer's own code, still active in Creem is the dangerous
case — it comes off a price that already carries the offer.

Run this whenever `core/plans.py` changes, and after creating or editing a product or a
discount in Creem:

    .venv/Scripts/python scripts/check_creem_prices.py
    .venv/Scripts/python scripts/check_creem_prices.py --env-file .env.production

It makes one read-only call per configured product plus one per code. It prints
no key and no product id. Exit code 0 means Creem agrees with the website about every
price and about every code.
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
    RETIRED_DISCOUNT_CODES,
    current_offer,
    promotion_is_active,
)
from ai_market_monitor.services.card_price_check import (  # noqa: E402
    card_price_check_configured,
    check_card_prices,
    offer_code_problem,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.production")
    arguments = parser.parse_args()

    settings = Settings(_env_file=arguments.env_file)  # type: ignore[call-arg]
    key = settings.creem_api_key
    if not card_price_check_configured(settings) or key is None:
        print("No Creem API key or no Creem products in this environment. Nothing to check.")
        return 0

    base = str(settings.creem_api_base).rstrip("/")
    problems = 0
    offer = current_offer()
    if offer is not None and promotion_is_active():
        ends = (
            f"ends {offer.ends_at:%d %B %Y}" if offer.ends_at is not None else "has no end date"
        )
        print(
            f"Offer {offer.code} ({offer.percent}% off) is running and "
            f"{ends}. The Creem products must carry the prices below.\n"
        )
    async with httpx.AsyncClient(timeout=settings.creem_timeout_seconds) as client:
        # The comparison itself is `services/card_price_check.py`, shared with the daily
        # worker task, so the script and the alert can never disagree about a price.
        for result in await check_card_prices(settings, client):
            if result.expected is None and result.problem is None:
                print(
                    f"{result.product_key}: no fixed price to compare "
                    f"({result.state}, {result.mode})."
                )
                continue
            verdict = "DIFFERENT" if result.problem else "same"
            print(
                f"{result.product_key}: Creem charges {result.charged} "
                f"{result.currency or '?'}, the website says {result.expected} USD "
                f"({result.state}, {result.mode}) -> {verdict}"
            )
            if result.problem:
                print(f"  {result.problem}")
                problems += 1

        offer_problem = await offer_code_problem(settings, client)
        if offer_problem:
            print(f"\n{offer_problem}")
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

    One list: ``BILLING_DISCOUNT_CODES``. The running offer's code (``CURRENT_OFFER``) is
    not on it and must never be: its percentage is already inside every price, and the
    settings loader refuses to start with it listed.

    A code on this list is never advertised on a pricing card and is only accepted on the
    crypto route, so Creem not having it is a fact worth printing rather than a fault.
    """

    return sorted(settings.billing_discount_codes.items())


async def check_retired_codes(client: httpx.AsyncClient, base: str, key: str) -> int:
    """Is a code this product has retired still alive in Creem?

    This is the one discount check that can cost money. A card buyer reaches Creem's own
    checkout page, which has a discount box this application does not control. A retired
    code still active there comes off a price that **already** carries the offer, so the
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
