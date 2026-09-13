"""Adversarial repro (H3): a refund that arrives AFTER the plan move has already
written the money-owed record. D1 says no money owed may come from a refunded payment.
This checks whether the already-written PlanMoveMoneyOwed row is reconciled when the
source payment is later refunded.

Read-only script: it writes nothing to the repo. Run from repo root.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.abspath("."))

import httpx
from sqlalchemy import func, select

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
    PlanMoveMoneyOwed,
)
from ai_market_monitor.services import plan_replacements as replacement_module  # noqa: E402
from ai_market_monitor.services.billing import BillingService  # noqa: E402
from ai_market_monitor.services.entitlements import PlanCatalogService  # noqa: E402
from ai_market_monitor.services.plan_replacements import (  # noqa: E402
    PaidPlanReplacementService,
)


async def main() -> None:
    async def successful_cancel(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", str(args[2])))

    replacement_module.provider_request = successful_cancel  # type: ignore[assignment]

    async for context in _build_context():
        session_factory = context["session_factory"]
        settings = context["settings"].model_copy(update=live_billing_overrides())
        provider = "creem"
        # The price written onto the float-free new attempt.
        async with session_factory() as session:
            user, old, source = await _seed_paid_plan(
                session, provider=provider, plan_code="pro"
            )
            plan = await PlanCatalogService(session).get_or_sync("trader")
            now = datetime.now(UTC)
            new_attempt = BillingCheckoutAttempt(
                user_id=user.id,
                plan_id=plan.id,
                billing_cycle="monthly_auto_renewal",
                provider=provider,
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
            old_id = old.id
            source_id = source.id
            new_attempt_id = new_attempt.id
            old_sub_ref = old.provider_subscription_id

        # 1) The new plan is paid for -> the move runs and writes the money-owed row.
        async with session_factory() as session:
            paid_at = datetime.now(UTC)
            await BillingService(session, settings).process_event(
                provider=provider,
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
            owed_before = (
                await session.execute(
                    select(
                        func.count(PlanMoveMoneyOwed.id),
                        func.coalesce(func.sum(PlanMoveMoneyOwed.amount_owed), 0),
                    )
                )
            ).one()
            src = await session.get(BillingCheckoutAttempt, source_id)
            print(
                f"AFTER MOVE: money_owed_rows={owed_before[0]} total_owed={owed_before[1]} "
                f"source_status={src.status if src else None}"
            )

        # 2) Now the OLD payment is refunded, after the move.
        async with session_factory() as session:
            src = await session.get(BillingCheckoutAttempt, source_id)
            await BillingService(session, settings).process_event(
                provider=provider,
                payload=_refund_payload(
                    event_type="payment.refunded",
                    attempt=src,
                    user_id=user_id,
                    subscription_reference=old_sub_ref,
                ),
            )
            await session.commit()

        async with session_factory() as session:
            rows = list(
                (await session.scalars(select(PlanMoveMoneyOwed))).all()
            )
            src = await session.get(BillingCheckoutAttempt, source_id)
            print(
                f"AFTER LATE REFUND: source_status={src.status if src else None} "
                f"money_owed_rows={len(rows)} "
                + "; ".join(f"{r.status}:{r.amount_owed}" for r in rows)
            )
            if rows:
                print(
                    "FINDING REPRODUCED: money-owed row still pending after the source "
                    "payment was refunded."
                )
            else:
                print("No open finding: money-owed was reconciled/removed on refund.")
        break


if __name__ == "__main__":
    asyncio.run(main())
