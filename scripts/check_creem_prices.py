"""Does the card company charge the number the website shows, and know the same codes?

Card checkout sends Creem a **product id** and nothing else. Creem then charges whatever
that product is priced at in Creem's own dashboard. The app's price lives in
`core/plans.py`. Those are two separate numbers and nothing keeps them together.

When they drift apart the customer really pays, and then
`BillingService._validate_paid_amount_and_currency` refuses the confirmation because the
amount does not match the checkout — so the money is gone and the plan never starts. That
is the worst outcome this code has.

The launch **discount code** has the same shape of problem and the same worst outcome.
The crypto route applies it here, in this application. The card route cannot: a card buyer
types it on Creem's own page, so the code has to exist in Creem, at the same percentage,
or the two routes charge different amounts for the same offer. Nothing offline can see
Creem, so this script is the only thing that can catch either disagreement.

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
    LAUNCH_DISCOUNT_CODE,
    PUBLIC_PLAN_PRESENTATIONS,
    coded_monthly_price,
    effective_monthly_price,
    launch_discount_percent,
)


def expected_price(product_key: str) -> Decimal | None:
    """What this product must cost, taken from the one owner of the prices."""

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


def codes_this_deployment_honours(settings: Settings) -> list[tuple[str, Decimal, bool]]:
    """Every code a buyer could type, what it is worth here, and whether Creem needs it.

    Two lists feed one answer, and the script has to check both or it checks nothing: the
    launch code that `core/plans.py` owns, and ``BILLING_DISCOUNT_CODES``. Checking only
    the first is how adding a second code silently escapes every check there is.

    The flag says whether Creem *must* hold the code. The launch code must: it is printed
    on the public pricing cards, which card buyers read too. A code that only lives in the
    env list is never advertised on a card and is only accepted on the crypto route, so
    Creem not having it is a fact worth printing, not a fault.
    """

    wanted: list[tuple[str, Decimal, bool]] = []
    plans_with_a_code = [
        code for code in PUBLIC_PLAN_PRESENTATIONS if launch_discount_percent(code) is not None
    ]
    if plans_with_a_code:
        launch = launch_discount_percent(plans_with_a_code[0])
        if launch is not None:
            wanted.append((LAUNCH_DISCOUNT_CODE, launch, True))
    for code, percent in sorted(settings.billing_discount_codes.items()):
        if code == LAUNCH_DISCOUNT_CODE:
            # Already listed above, and the settings loader refuses a different number for
            # it, so the two entries can only ever be the same one.
            continue
        wanted.append((code, percent, False))
    return wanted


async def check_discount_codes(
    client: httpx.AsyncClient, base: str, key: str, settings: Settings
) -> int:
    """Does Creem agree with this deployment about every code, and its percentage?

    Only the card route reads Creem: crypto buyers type the code here and this application
    applies it. But both routes can advertise the same offer, and a code that exists on
    both sides at *different* percentages charges two customers two different prices for
    the same thing — so a disagreement is a fault whichever list the code came from.
    """

    wanted = codes_this_deployment_honours(settings)
    if not wanted:
        print("\nNo discount codes are running. Nothing to check in Creem's discounts.")
        return 0

    problems = 0
    print("\ndiscount codes:")
    for code, percent, must_exist in wanted:
        response = await client.get(
            f"{base}/v1/discounts",
            params={"discount_code": code},
            headers={"x-api-key": key, "User-Agent": "HilalMarkets/1.0"},
        )
        if response.status_code == 404:
            if must_exist:
                example = coded_monthly_price(
                    next(
                        plan
                        for plan in PUBLIC_PLAN_PRESENTATIONS
                        if launch_discount_percent(plan) is not None
                    )
                )
                print(
                    f"  {code}: Creem has never heard of this code. Crypto buyers get "
                    f"{example} USD; card buyers cannot get the offer at all. Create a "
                    f"{percent}% percentage discount named {code} in Creem."
                )
                problems += 1
            else:
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
            if must_exist:
                problems += 1
    return problems


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
