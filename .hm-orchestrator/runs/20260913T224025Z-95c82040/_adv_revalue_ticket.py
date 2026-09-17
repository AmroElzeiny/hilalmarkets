"""Adversarial repro (WP1, attack 3): after a partial refund re-values a standing
``pending_manual`` payout, does the staff ticket tell the truth?

The design says a partial refund re-values the ``PlanMoveMoneyOwed`` row in place and
leaves its ``billing:money-owed:{id}`` ticket open, "do not re-report it". The payout
amount is the number a person sends by hand. If the ticket summary still carries the
pre-refund figure, staff reads the wrong number.

Read-only script: writes nothing to the repo. Run from the repo root.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.abspath("."))

import httpx
from sqlalchemy import select

from tests.conftest import _build_context
from tests.support.billing_config import live_billing_overrides

sys.path.insert(0, "tests/unit")
from test_invariant_refund_is_recorded import (  # noqa: E402
    _refund_payload,
    _seed_paid_plan,
)

from ai_market_monitor.core.plans import effective_monthly_price  # noqa: E402
from ai_market_monitor.db.models import (  # noqa: E402
    BillingCheckoutAttempt,
    OperationalIssue,
    PlanMoveMoneyOwed,
)
from ai_market_monitor.services import plan_replacements as replacement_module  # noqa: E402
from ai_market_monitor.services.billing import BillingService  # noqa: E402
from ai_market_monitor.services.entitlements import PlanCatalogService  # noqa: E402
from ai_market_monitor.services.plan_replacements import (  # noqa: E402
    PaidPlanReplacementService,
    money_owed_for_unused_time,
)

PROVIDER = "creem"


async def main() -> None:
    async def successful_cancel(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", str(args[2])))

    replacement_module.provider_request = successful_cancel  # type: ignore[assignment]

    async for context in _build_context():
        session_factory = context["session_factory"]
        settings = context["settings"].model_copy(update=live_billing_overrides())

        async with session_factory() as session:
            user, old, source = await _seed_paid_plan(
                session, provider=PROVIDER, plan_code="pro"
            )
            plan = await PlanCatalogService(session).get_or_sync("trader")
            now = datetime.now(UTC)
            new_attempt = BillingCheckoutAttempt(
                user_id=user.id,
                plan_id=plan.id,
                billing_cycle="monthly_auto_renewal",
                provider=PROVIDER,
                status="pending",
                idempotency_key=f"move-{user.id}",
                terms_version="test",
                amount=effective_monthly_price("trader"),
                currency="USD",
                terms_accepted_at=now,
                expires_at=now + timedelta(days=1),
                billing_profile={"first_name": "Amina"},
            )
            session.add(new_attempt)
            await session.flush()
            await PaidPlanReplacementService(session, settings).attach_source(
                attempt=new_attempt, target_plan_code="trader"
            )
            await session.commit()
            user_id = user.id
            source_id = source.id
            new_attempt_id = new_attempt.id
            old_sub_ref = old.provider_subscription_id

        # The new plan is paid: the move writes the payout row and the ticket.
        async with session_factory() as session:
            paid_at = datetime.now(UTC)
            await BillingService(session, settings).process_event(
                provider=PROVIDER,
                payload={
                    "id": f"evt-paid-{new_attempt_id}",
                    "type": "subscription.paid",
                    "data": {
                        "checkout_attempt_id": str(new_attempt_id),
                        "plan_code": "trader",
                        "user_id": str(user_id),
                        "provider_customer_id": f"cus_{user_id}",
                        "provider_subscription_id": f"sub_new_{user_id}",
                        "amount": str(effective_monthly_price("trader")),
                        "currency": "USD",
                        "status": "active",
                        "current_period_start": paid_at.isoformat(),
                        "current_period_end": (paid_at + timedelta(days=30)).isoformat(),
                    },
                },
            )
            await session.commit()

        async with session_factory() as session:
            owed = (await session.scalars(select(PlanMoveMoneyOwed))).one()
            ticket = await session.scalar(
                select(OperationalIssue).where(
                    OperationalIssue.dedupe_key == f"billing:money-owed:{owed.id}"
                )
            )
            assert ticket is not None
            old_owed = owed.amount_owed
            old_summary = ticket.summary
            print(f"BEFORE PARTIAL: owed.amount_owed={old_owed} ticket.summary={old_summary!r}")
            owed_id = owed.id

        # A partial refund of the payment behind that payout.
        async with session_factory() as session:
            src = await session.get(BillingCheckoutAttempt, source_id)
            assert src is not None
            payload = _refund_payload(
                event_type="refund.created",
                attempt=src,
                user_id=user_id,
                subscription_reference=old_sub_ref,
            )
            payload["id"] = f"evt-partial-{source_id}"
            payload["data"]["refunded_amount"] = "4.00"
            await BillingService(session, settings).process_event(
                provider=PROVIDER, payload=payload
            )
            await session.commit()

        async with session_factory() as session:
            src = await session.get(BillingCheckoutAttempt, source_id)
            owed = await session.get(PlanMoveMoneyOwed, owed_id)
            ticket = await session.scalar(
                select(OperationalIssue).where(
                    OperationalIssue.dedupe_key == f"billing:money-owed:{owed_id}"
                )
            )
            assert owed is not None and ticket is not None
            expected = money_owed_for_unused_time(
                paid_amount=Decimal(str(src.amount)) - Decimal("4.00"),
                period_start=owed.period_start,
                period_end=owed.original_period_end,
                ended_at=owed.ended_at,
            )
            print(f"AFTER PARTIAL:  owed.status={owed.status} owed.amount_owed={owed.amount_owed}")
            print(f"AFTER PARTIAL:  ticket.state={ticket.state} ticket.summary={ticket.summary!r}")
            print(
                "AFTER PARTIAL:  expected re-valued amount is "
                f"{expected}; ticket text contains the OLD figure "
                f"{str(old_owed) in ticket.summary}"
            )
            if ticket.state == "open" and str(old_owed) in ticket.summary and expected != old_owed:
                print(
                    "FINDING REPRODUCED: the open staff ticket still tells the person to "
                    "send the pre-refund amount, while the payout row now holds less."
                )
            else:
                print("No finding: ticket text follows the re-valued payout.")
        break


if __name__ == "__main__":
    asyncio.run(main())
