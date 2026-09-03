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

        problems += await check_launch_code(client, base, key.get_secret_value())

    if problems:
        print(
            f"\n{problems} problem(s). A customer would be charged a number the website "
            "did not show, and the payment could not be confirmed afterwards."
        )
        return 1
    print("\nCreem agrees with the website about every price and about the launch code.")
    return 0


async def check_launch_code(client: httpx.AsyncClient, base: str, key: str) -> int:
    """Does Creem hold the launch code, at the percentage the website advertises?

    Only the card route needs this: crypto buyers type the code here and this application
    applies it. But both routes advertise the same offer on the same pricing card, so a
    code that exists on one side and not the other means a card buyer is told about a
    discount that does nothing when they reach the payment page.
    """

    plans_with_a_code = [
        code for code in PUBLIC_PLAN_PRESENTATIONS if launch_discount_percent(code) is not None
    ]
    if not plans_with_a_code:
        print("\nNo launch code is running. Nothing to check in Creem's discounts.")
        return 0
    wanted = launch_discount_percent(plans_with_a_code[0])
    response = await client.get(
        f"{base}/v1/discounts",
        params={"discount_code": LAUNCH_DISCOUNT_CODE},
        headers={"x-api-key": key, "User-Agent": "HilalMarkets/1.0"},
    )
    if response.status_code == 404:
        example = coded_monthly_price(plans_with_a_code[0])
        print(
            f"\n{LAUNCH_DISCOUNT_CODE}: Creem has never heard of this code. Crypto buyers "
            f"get {example} USD; card buyers cannot get the offer at all. "
            f"Create a {wanted}% percentage discount named {LAUNCH_DISCOUNT_CODE} in Creem."
        )
        return 1
    if response.status_code != 200:
        print(f"\n{LAUNCH_DISCOUNT_CODE}: Creem answered {response.status_code}. Cannot check.")
        return 1
    body = response.json()
    kind = str(body.get("type") or "")
    state = str(body.get("status") or "")
    percent = body.get("percentage")
    if percent is None:
        percent = body.get("amount")
    same = kind == "percentage" and Decimal(str(percent or 0)) == wanted
    print(
        f"\n{LAUNCH_DISCOUNT_CODE}: Creem takes off {percent}{'%' if kind == 'percentage' else ''}"
        f", the website says {wanted}% ({state}) -> {'same' if same else 'DIFFERENT'}"
    )
    problems = 0
    if not same:
        problems += 1
    if state != "active":
        print(f"{LAUNCH_DISCOUNT_CODE}: this discount is not active in Creem.")
        problems += 1
    return problems


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
