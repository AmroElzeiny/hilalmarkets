"""Adversarial repro (WP1, attack 1): drive the REAL Stripe normaliser for
``charge.refunded`` and check the cumulative-total rule end to end.

The acceptance test injects ``refunded_amount`` straight into ``data``; it never runs
the Stripe normaliser into the recorder. This script does, with ``amount_refunded`` in
minor units:
  (1) 850 of 1700 -> partial
  (2) a second partial report of 500 (smaller, cumulative) -> must not go backwards
  (3) the full 1700 -> full refund, plan ended, payout voided
Also: a full refund delivered after a partial in one step (1200 -> then 1700).

Read-only script: writes nothing to the repo. Run from the repo root.
"""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.abspath("."))

from sqlalchemy import select

from tests.conftest import _build_context

sys.path.insert(0, "tests/unit")
from test_invariant_refund_is_recorded import _seed_paid_plan  # noqa: E402

from ai_market_monitor.db.models import (  # noqa: E402
    BillingCheckoutAttempt,
    Subscription,
)
from ai_market_monitor.services.billing import BillingService  # noqa: E402


def _stripe_charge_refunded(attempt, user_id, reference, amount_refunded_minor):
    return {
        "id": f"evt-{attempt.id}-{amount_refunded_minor}",
        "type": "charge.refunded",
        "data": {
            "object": {
                "id": f"ch_{attempt.id}",
                "object": "charge",
                "amount": 1700,
                "amount_paid": 1700,
                "amount_refunded": amount_refunded_minor,
                "currency": "usd",
                "subscription": reference,
                "metadata": {
                    "user_id": str(user_id),
                    "checkout_attempt_id": str(attempt.id),
                },
            }
        },
    }


async def _report(session, attempt_id, subscription_id, label):
    row = await session.get(BillingCheckoutAttempt, attempt_id)
    sub = await session.get(Subscription, subscription_id)
    print(
        f"{label}: stored_refunded={row.refunded_amount} status={row.status} "
        f"plan={sub.status.value}"
    )
    return Decimal(str(row.refunded_amount)), row.status


async def main() -> None:
    async for context in _build_context():
        session_factory = context["session_factory"]
        settings = context["settings"]
        provider = "stripe"

        # --- Scenario A: 850 -> 500 (smaller cumulative) -> 1700 -----------------
        async with session_factory() as session:
            user, subscription, attempt = await _seed_paid_plan(
                session, provider=provider
            )
            service = BillingService(session, settings)
            for minor in (850, 500):
                normalized = service._normalize_provider_payload(
                    provider,
                    _stripe_charge_refunded(
                        attempt, user.id, subscription.provider_subscription_id, minor
                    ),
                )
                await service.process_event(provider=provider, payload=normalized)
                await session.commit()
            async with session_factory() as check:
                await _report(check, attempt.id, subscription.id, "A after 850 then 500")
            # Full cumulative.
            normalized = service._normalize_provider_payload(
                provider,
                _stripe_charge_refunded(
                    attempt, user.id, subscription.provider_subscription_id, 1700
                ),
            )
            await service.process_event(provider=provider, payload=normalized)
            await session.commit()

        async with session_factory() as session:
            await _report(session, attempt.id, subscription.id, "A after full 1700")

        # --- Scenario B: full refund after a partial (1200 -> 1700) -------------
        async with session_factory() as session:
            user, subscription, attempt = await _seed_paid_plan(
                session, provider=provider
            )
            service = BillingService(session, settings)
            for minor in (1200, 1700):
                normalized = service._normalize_provider_payload(
                    provider,
                    _stripe_charge_refunded(
                        attempt, user.id, subscription.provider_subscription_id, minor
                    ),
                )
                await service.process_event(provider=provider, payload=normalized)
                await session.commit()
                async with session_factory() as check:
                    await _report(
                        check,
                        attempt.id,
                        subscription.id,
                        f"B after {minor}",
                    )
        break


if __name__ == "__main__":
    asyncio.run(main())
