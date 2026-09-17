"""Adversarial repro (WP1): a negative reported refund amount is stored, and the
staff SQL form ``sum(amount - refunded_amount)`` then *adds* money that was never taken.

Read-only script: writes nothing to the repo. Run from the repo root.
"""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.abspath("."))

from tests.conftest import _build_context

sys.path.insert(0, "tests/unit")
from test_invariant_refund_is_recorded import (  # noqa: E402
    _refund_payload,
    _seed_paid_plan,
)

from ai_market_monitor.db.models import BillingCheckoutAttempt  # noqa: E402
from ai_market_monitor.services.billing import BillingService  # noqa: E402
from ai_market_monitor.services.system_brain_payments import (  # noqa: E402
    SystemBrainPaymentsService,
)


async def main() -> None:
    async for context in _build_context():
        session_factory = context["session_factory"]
        settings = context["settings"]

        async with session_factory() as session:
            user, subscription, attempt = await _seed_paid_plan(
                session, provider="creem"
            )
            payload = _refund_payload(
                event_type="refund.created",
                attempt=attempt,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
            )
            payload["id"] = f"evt-negative-{attempt.id}"
            payload["data"]["refunded_amount"] = "-1.00"
            await BillingService(session, settings).process_event(
                provider="creem", payload=payload
            )
            await session.commit()

        async with session_factory() as session:
            row = await session.get(BillingCheckoutAttempt, attempt.id)
            assert row is not None
            totals = await SystemBrainPaymentsService(session).overall_totals()
            page = await SystemBrainPaymentsService(session).list_customers()
            listed = [c for c in page["payment_customers"] if c.user_id == user.id]
            print(
                f"attempt: amount={row.amount} refunded_amount={row.refunded_amount} "
                f"status={row.status}"
            )
            print(f"overall_totals: money_taken={totals['money_taken']}")
            print(f"list_customers total_paid={[c.total_paid for c in listed]}")
            if Decimal(str(totals["money_taken"])) > Decimal(str(row.amount)):
                print(
                    "FINDING REPRODUCED: a negative refund amount makes the staff "
                    "'money taken' total larger than the payment amount."
                )
        break


if __name__ == "__main__":
    asyncio.run(main())
