"""Does the card company charge the number the website shows?

Card checkout sends Creem a **product id** and nothing else. Creem then charges whatever
that product is priced at in Creem's own dashboard. The app's price lives in
`core/plans.py`. Those are two separate numbers and nothing keeps them together.

When they drift apart the customer really pays, and then
`BillingService._validate_paid_amount_and_currency` refuses the confirmation because the
amount does not match the checkout — so the money is gone and the plan never starts. That
is the worst outcome this code has.

Run this whenever the price in `core/plans.py` changes, and after creating or editing a
product in Creem:

    .venv/Scripts/python scripts/check_creem_prices.py
    .venv/Scripts/python scripts/check_creem_prices.py --env-file .env.production

It makes one read-only call per configured product. It prints no key and no product id.
Exit code 0 means every configured product charges what the website quotes.
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
    PUBLIC_PLAN_PRESENTATIONS,
    effective_monthly_price,
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

    if problems:
        print(
            f"\n{problems} problem(s). A customer would be charged a number the website "
            "did not show, and the payment could not be confirmed afterwards."
        )
        return 1
    print("\nEvery Creem product charges what the website quotes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
