"""Adversarial repro (WP1, attacks 1/3/4): refund amount edge cases at the recorder.

Cases:
  (a) a *known* zero amount (e.g. Stripe reports amount_refunded = 0) -> partial?
  (b) a *known* negative amount -> partial, and a negative total is stored?
  (c) one real refund delivered under two different event ids, incremental provider
      -> does the total double?
  (d) unknown amount -> full (baseline, must match the old behaviour)

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
from test_invariant_refund_is_recorded import (  # noqa: E402
    _refund_payload,
    _seed_paid_plan,
)

from ai_market_monitor.db.models import (  # noqa: E402
    BillingCheckoutAttempt,
    OperationalIssue,
    Subscription,
)
from ai_market_monitor.services.billing import BillingService  # noqa: E402

PROVIDER = "creem"


async def _run_case(
    session_factory, settings, label, values, *, cumulative=False, attempt_status="completed"
):
    """Seed a fresh payment and replay the given refund amounts in order."""

    async with session_factory() as session:
        user, subscription, attempt = await _seed_paid_plan(
            session, provider=PROVIDER, attempt_status=attempt_status
        )
        amount = Decimal(str(attempt.amount))
        service = BillingService(session, settings)
        for index, value in enumerate(values):
            payload = _refund_payload(
                event_type="refund.created",
                attempt=attempt,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
            )
            payload["id"] = f"evt-{label}-{index}-{attempt.id}"
            if value is not None:
                payload["data"]["refunded_amount"] = f"{value:.2f}"
                if cumulative:
                    payload["data"]["refunded_total_is_cumulative"] = True
            await service.process_event(provider=PROVIDER, payload=payload)
            await session.commit()

    async with session_factory() as session:
        row = await session.get(BillingCheckoutAttempt, attempt.id)
        sub = await session.get(Subscription, subscription.id)
        alerts = list(
            (
                await session.scalars(
                    select(OperationalIssue).where(
                        OperationalIssue.affected_scope == "billing.partial_refund"
                    )
                )
            ).all()
        )
        print(
            f"{label}: paid={row.amount} reported={values} stored_refunded="
            f"{row.refunded_amount} status={row.status} plan={sub.status.value} "
            f"partial_alerts={len(alerts)}"
        )
        return row, sub


async def main() -> None:
    async for context in _build_context():
        session_factory = context["session_factory"]
        settings = context["settings"]

        await _run_case(session_factory, settings, "d-unknown", [None])
        await _run_case(session_factory, settings, "a-zero", [Decimal("0.00")])
        await _run_case(session_factory, settings, "b-negative", [Decimal("-1.00")])
        await _run_case(
            session_factory,
            settings,
            "c-two-ids-same-refund",
            [Decimal("8.50"), Decimal("8.50")],
        )
        await _run_case(
            session_factory,
            settings,
            "e-partial-on-pending",
            [Decimal("8.50")],
            attempt_status="pending",
        )
        await _run_case(
            session_factory,
            settings,
            "f-partial-on-processing",
            [Decimal("8.50")],
            attempt_status="processing",
        )
        break


if __name__ == "__main__":
    asyncio.run(main())
