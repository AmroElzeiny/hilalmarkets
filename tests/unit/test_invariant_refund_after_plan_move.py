"""A refund that arrives AFTER a plan move must still owe the customer nothing back.

D1 has three windows for one payment: the refund lands **before** the move (the move
never finds a completed payment and refuses), **during** the frozen payment page (the
move reads the refunded row and writes zero), and **after** the move has already
written the manual-payout record. The first two are pinned in
``test_invariant_refund_is_recorded.py``. This file pins the third.

A ``PlanMoveMoneyOwed`` row says "a person must send this money by hand", and its
``billing:money-owed:{id}`` ticket is how staff learn about it. When the payment
behind that row is refunded afterwards, the payment company has already given the
same money back: a payout still reading ``pending_manual`` pays the customer twice
for one period. So the refund must void the row and resolve the ticket, in the same
transaction that records the refund — while the move itself stands (the old plan
stays ended, nothing is auto-refunded, the new payment stays intact).

The adversarial review reproduced this open finding at
``.hm-orchestrator/runs/20260913T162406Z-39060cc7/_adv_repro_move_then_refund.py``
("money-owed row still pending after the source payment was refunded"). Each void
test below fails on the code as the review found it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.plans import effective_monthly_price
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    BillingEvent,
    OperationalIssue,
    PlanMoveMoneyOwed,
    Subscription,
)
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.services import plan_replacements as replacement_module
from ai_market_monitor.services.billing import BillingService
from ai_market_monitor.services.entitlements import PlanCatalogService
from ai_market_monitor.services.plan_replacements import PaidPlanReplacementService
from tests.support.billing_config import live_billing_overrides
from tests.unit.test_invariant_refund_is_recorded import (
    REFUND_EVENT_TYPES,
    _refund_payload,
    _seed_paid_plan,
)

#: The move under test runs on the card company whose paid plan the replacement
#: service really cancels (the outbound call is answered in process, below).
PROVIDER = "creem"

#: The one status a money-owed row carries while a person still has to act. Written
#: by ``apply_after_payment``; the refund path must leave none of them standing.
PENDING_MANUAL = "pending_manual"


async def _move_a_paid_plan(
    test_context: Any, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, dict[str, Any]]:
    """Old Pro plan paid, new Trader plan paid, move record and ticket standing.

    The same shape as ``_adv_repro_move_then_refund.py``: seed the held paid plan,
    freeze a move onto it (``attach_source``), then complete the new payment through
    the real webhook service. Returns the settings that money ran under and every id
    the tests read back.
    """

    async def successful_cancel(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", str(args[2])))

    monkeypatch.setattr(replacement_module, "provider_request", successful_cancel)
    settings = test_context["settings"].model_copy(update=live_billing_overrides())
    async with test_context["session_factory"]() as session:
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
        assert new_attempt.replaces_checkout_attempt_id == source.id
        await session.commit()
        held: dict[str, Any] = {
            "user_id": user.id,
            "old_id": old.id,
            "source_id": source.id,
            "new_attempt_id": new_attempt.id,
            "old_subscription_reference": old.provider_subscription_id,
        }

    # The new plan is paid: the move runs and writes the money-owed record.
    async with test_context["session_factory"]() as session:
        paid_at = datetime.now(UTC)
        await BillingService(session, settings).process_event(
            provider=PROVIDER,
            payload={
                "id": f"evt-paid-{held['new_attempt_id']}",
                "type": "subscription.paid",
                "data": {
                    "checkout_attempt_id": str(held["new_attempt_id"]),
                    "plan_code": "trader",
                    "user_id": str(held["user_id"]),
                    "provider_customer_id": f"cus_{held['user_id']}",
                    "provider_subscription_id": f"sub_new_{held['user_id']}",
                    "amount": str(effective_monthly_price("trader")),
                    "currency": "USD",
                    "status": "active",
                    "current_period_start": paid_at.isoformat(),
                    "current_period_end": (paid_at + timedelta(days=30)).isoformat(),
                },
            },
        )
        await session.commit()

    # The precondition this whole file is about: the record and its ticket exist.
    async with test_context["session_factory"]() as session:
        rows = list((await session.scalars(select(PlanMoveMoneyOwed))).all())
        assert len(rows) == 1, "the move itself must have written the payout record"
        assert rows[0].status == PENDING_MANUAL
        assert rows[0].source_checkout_attempt_id == held["source_id"]
        held["owed_id"] = rows[0].id
        ticket = await session.scalar(
            select(OperationalIssue).where(
                OperationalIssue.dedupe_key == f"billing:money-owed:{rows[0].id}"
            )
        )
        assert ticket is not None, "the move must raise the staff ticket the void closes"
        assert ticket.state == "open"
    return settings, held


@pytest.mark.parametrize("event_type", REFUND_EVENT_TYPES)
async def test_a_refund_after_a_plan_move_voids_the_manual_payout(
    test_context, monkeypatch: pytest.MonkeyPatch, event_type: str
) -> None:
    """The third window: the payout row exists, and the payment behind it came back.

    Every name that says "money went back" is checked, not just the reported one.
    Afterwards: no payout for that source is still waiting on a person, the row is
    voided rather than deleted (the record of the decision stays readable), the
    staff ticket is resolved, and the move itself stands — the old plan is ended,
    the new payment and plan are untouched, and nothing was auto-refunded.
    """

    settings, held = await _move_a_paid_plan(test_context, monkeypatch)
    async with test_context["session_factory"]() as session:
        source = await session.get(BillingCheckoutAttempt, held["source_id"])
        result = await BillingService(session, settings).process_event(
            provider=PROVIDER,
            payload=_refund_payload(
                event_type=event_type,
                attempt=source,
                user_id=held["user_id"],
                subscription_reference=held["old_subscription_reference"],
            ),
        )
        assert result.processing_status == "processed"
        await session.commit()

    async with test_context["session_factory"]() as session:
        still_owed = await session.scalar(
            select(func.count(PlanMoveMoneyOwed.id)).where(
                PlanMoveMoneyOwed.source_checkout_attempt_id == held["source_id"],
                PlanMoveMoneyOwed.status == PENDING_MANUAL,
            )
        )
        assert still_owed == 0, (
            f"{event_type} left a manual payout standing on a refunded payment: "
            "a person would send the money on top of the provider's refund"
        )
        owed = await session.get(PlanMoveMoneyOwed, held["owed_id"])
        assert owed is not None
        assert owed.status == "voided"
        assert owed.amount_owed > 0  # voiding is not deleting; the figures stay readable
        ticket = await session.scalar(
            select(OperationalIssue).where(
                OperationalIssue.dedupe_key == f"billing:money-owed:{held['owed_id']}"
            )
        )
        assert ticket is not None
        assert ticket.state == "resolved", (
            f"the payout was voided but its ticket still reads {ticket.state!r}"
        )
        assert ticket.resolved_at is not None
        # The move stands, and the refund touched only the payment it named.
        source = await session.get(BillingCheckoutAttempt, held["source_id"])
        assert source is not None and source.status == "refunded"
        old = await session.get(Subscription, held["old_id"])
        assert old is not None and old.status == SubscriptionStatus.CANCELED
        new_attempt = await session.get(BillingCheckoutAttempt, held["new_attempt_id"])
        assert new_attempt is not None and new_attempt.status == "completed"
        new_sub = await session.scalar(
            select(Subscription).where(
                Subscription.user_id == held["user_id"],
                Subscription.provider_subscription_id == f"sub_new_{held['user_id']}",
            )
        )
        assert new_sub is not None and new_sub.status == SubscriptionStatus.ACTIVE


async def test_a_second_refund_of_the_same_payment_changes_nothing_further(
    test_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One refund is one void; the duplicate must not re-open or double-report.

    Two refund events for one payment are the normal shape (a company sends both
    names), and the second arrives on an attempt that already says ``refunded``.
    Both deliveries must go through the full path — this is not the same-event-id
    replay, which ``process_event`` short-circuits — and the second may change
    nothing: the row stays voided, one ticket stays resolved, its count stays one.
    """

    settings, held = await _move_a_paid_plan(test_context, monkeypatch)
    for index, event_type in enumerate(("payment.refunded", "charge.refunded"), start=1):
        async with test_context["session_factory"]() as session:
            source = await session.get(BillingCheckoutAttempt, held["source_id"])
            payload = _refund_payload(
                event_type=event_type,
                attempt=source,
                user_id=held["user_id"],
                subscription_reference=held["old_subscription_reference"],
            )
            payload["id"] = f"evt-after-move-{index}-{held['source_id']}"
            await BillingService(session, settings).process_event(
                provider=PROVIDER, payload=payload
            )
            await session.commit()

    async with test_context["session_factory"]() as session:
        refunds_seen = await session.scalar(
            select(func.count(BillingEvent.id)).where(
                BillingEvent.event_type.in_(("payment.refunded", "charge.refunded"))
            )
        )
        assert refunds_seen == 2, "both refund deliveries must have gone through the path"
        owed = await session.get(PlanMoveMoneyOwed, held["owed_id"])
        assert owed is not None and owed.status == "voided"
        assert await session.scalar(
            select(func.count(PlanMoveMoneyOwed.id)).where(
                PlanMoveMoneyOwed.status == PENDING_MANUAL
            )
        ) == 0
        tickets = list(
            (
                await session.scalars(
                    select(OperationalIssue).where(
                        OperationalIssue.dedupe_key.like("billing:money-owed:%")
                    )
                )
            ).all()
        )
        assert len(tickets) == 1
        assert tickets[0].state == "resolved"
        assert tickets[0].occurrence_count == 1, "the duplicate re-reported the payout"


async def test_a_refund_that_matches_no_payment_leaves_the_payout_open(
    test_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The void fires on a refund this system can place, and on no other reading.

    A refund naming no checkout stays one critical alert (the D1 rule); the payout
    still reads ``pending_manual``, because whether the provider really returned
    THAT payment is exactly what the alert asks a person to find out. Voiding on an
    unplaced refund — or on any refund at all — would let one stray event silence a
    live money promise.
    """

    settings, held = await _move_a_paid_plan(test_context, monkeypatch)
    async with test_context["session_factory"]() as session:
        result = await BillingService(session, settings).process_event(
            provider=PROVIDER,
            payload={
                "id": f"evt-unplaced-after-move-{held['source_id']}",
                "type": "payment.refunded",
                "data": {"status": "refunded"},
            },
        )
        await session.commit()
        assert result.processing_status == "processed"

    async with test_context["session_factory"]() as session:
        owed = await session.get(PlanMoveMoneyOwed, held["owed_id"])
        assert owed is not None and owed.status == PENDING_MANUAL
        source = await session.get(BillingCheckoutAttempt, held["source_id"])
        assert source is not None and source.status == "completed"
        ticket = await session.scalar(
            select(OperationalIssue).where(
                OperationalIssue.dedupe_key == f"billing:money-owed:{held['owed_id']}"
            )
        )
        assert ticket is not None and ticket.state == "open"
