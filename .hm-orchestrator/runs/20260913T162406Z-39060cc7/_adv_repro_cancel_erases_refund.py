"""Adversarial repro (H3): the GET /billing/cancel route erases a recorded refund.

dashboard.py:4507 rewrites any attempt status that is not completed/failed/expired to
"cancelled". A "refunded" attempt is not in that set, so visiting the stale cancel URL
destroys the refund record. That also disables the NOWPayments double-grant guard at
billing.py:3321 (it checks status in {completed, refunded}), so a re-delivered
payment.finished can put the payment back to "completed".

This script performs exactly that status write (the route's own one-line effect) and
then replays payment.finished, to show the guard is bypassed. Read-only.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.abspath("."))

from sqlalchemy import select

from tests.conftest import _build_context
from tests.support.billing_config import live_billing_overrides

from ai_market_monitor.core.plans import effective_monthly_price  # noqa: E402
from ai_market_monitor.db.models import (  # noqa: E402
    BillingCheckoutAttempt,
    Plan,
    Subscription,
)
from ai_market_monitor.services.billing import BillingService  # noqa: E402


async def main() -> None:
    async for context in _build_context():
        session_factory = context["session_factory"]
        settings = context["settings"].model_copy(update=live_billing_overrides())
        provider = "nowpayments"
        amount = effective_monthly_price("trader")
        async with session_factory() as session:
            from tests.unit.test_invariant_refund_is_recorded import _seed_paid_plan

            user, subscription, attempt = await _seed_paid_plan(
                session, provider=provider, plan_code="trader"
            )
            user_id = user.id
            source_id = attempt.id
            sub_id = subscription.id
            # Give the subscription the id a real NOWPayments payment would have.
            ref = "nowpayments_orig_123"
            subscription.provider_subscription_id = ref
            await session.commit()

        # 1) A refund lands but names no subscription we can end: attempt refunded,
        #    plan stays live (the same shape the D1 tests pin for charge.refunded).
        async with session_factory() as session:
            await BillingService(session, settings).process_event(
                provider=provider,
                payload={
                    "id": "evt-refund-noplan",
                    "type": "payment.refunded",
                    "data": {
                        "checkout_attempt_id": str(source_id),
                        "user_id": str(user_id),
                        "status": "refunded",
                    },
                },
            )
            await session.commit()

        async with session_factory() as session:
            attempt = await session.get(BillingCheckoutAttempt, source_id)
            sub = await session.get(Subscription, sub_id)
            print(f"AFTER REFUND: attempt={attempt.status} subscription={sub.status}")

        # 2) The user revisits the stale cancel URL. dashboard.py:4507-4508 does this:
        async with session_factory() as session:
            attempt = await session.get(BillingCheckoutAttempt, source_id)
            if attempt.status not in {"completed", "failed", "expired"}:
                attempt.status = "cancelled"
            await session.commit()
        async with session_factory() as session:
            attempt = await session.get(BillingCheckoutAttempt, source_id)
            print(f"AFTER CANCEL ROUTE EQUIVALENT: attempt={attempt.status}")

        # 3) NOWPayments re-delivers payment.finished for the same invoice.
        async with session_factory() as session:
            now = datetime.now(UTC)
            await BillingService(session, settings).process_event(
                provider=provider,
                payload={
                    "id": "evt-replayed-finished",
                    "type": "payment.finished",
                    "data": {
                        "checkout_attempt_id": str(source_id),
                        "user_id": str(user_id),
                        "plan_code": "trader",
                        "provider_subscription_id": ref,
                        "status": "active",
                        "amount": str(amount),
                        "currency": "USD",
                        "settlement_expected_amount": str(amount),
                        "settlement_actual_amount": str(amount),
                        "settlement_currency": "USDT",
                        "current_period_start": now.isoformat(),
                        "current_period_end": (now + timedelta(days=30)).isoformat(),
                    },
                },
            )
            await session.commit()

        async with session_factory() as session:
            attempt = await session.get(BillingCheckoutAttempt, source_id)
            sub = await session.get(Subscription, sub_id)
            print(
                f"AFTER REPLAY: attempt={attempt.status} completed_at={attempt.completed_at} "
                f"subscription={sub.status}"
            )
            if attempt.status == "completed":
                print(
                    "FINDING REPRODUCED: the cancel route erased 'refunded', the "
                    "double-grant guard was bypassed, and the returned payment is "
                    "counted as money kept again."
                )
            else:
                print("Guard held: the replayed payment.finished did not re-settle.")
        break


if __name__ == "__main__":
    asyncio.run(main())
