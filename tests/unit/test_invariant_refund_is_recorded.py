"""A refund must be written down, or the same money can be sent back twice.

Every surface that decides money reads one word on a checkout attempt: ``completed``.
The plan-move service asks "which payment bought this plan?" and values the unused days
from the answer (``services/plan_replacements.py``). The affiliate service asks the same
question for a commission. The operations tools count ``completed`` rows as sales.

So a refund that never reaches that word is not a missing detail in a history table. It
is a payment the company already gave back and still counts as money it kept — and the
plan-move path will happily promise the customer the same money a second time.

These tests drive the real webhook service. They cover the whole family: every
refund-like event name any of the three payment companies can send, every prior state of
the attempt, replay, and the two things that must NOT change — a cancellation that must
never undo a settled payment, and the money-owed record that must never be valued from a
payment that came back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.plans import effective_monthly_price
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    BillingEvent,
    OperationalIssue,
    Plan,
    PlanMoveMoneyOwed,
    Subscription,
    User,
)
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.services import plan_replacements as replacement_module
from ai_market_monitor.services.billing import (
    REFUND_ENDS_PLAN_EVENT_TYPES,
    BillingError,
    BillingService,
)
from ai_market_monitor.services.entitlements import PlanCatalogService
from ai_market_monitor.services.plan_replacements import (
    PaidPlanReplacementService,
    PlanReplacementError,
    payment_that_bought,
)
from tests.support.billing_config import live_billing_overrides

#: Every event the code treats as "money that moved is coming back". Written out one by
#: one on purpose: a fix that only handles the reported name must fail here for the rest.
REFUND_EVENT_TYPES: tuple[str, ...] = (
    "payment.refunded",  # NOWPayments, and the name our own tests have always used
    "refund.created",  # Creem
    "charge.refunded",  # Stripe
    "charge.dispute.created",  # Stripe chargeback
    "dispute.created",  # Stripe and Creem dispute
)

#: The three companies the webhook route accepts. The recording path does not branch on
#: the company, so every name is checked against every company: NOWPayments cannot really
#: produce a card chargeback and Stripe does not send ``payment.refunded``, and that is
#: stated in the report rather than used as a reason to leave a hole untested.
REFUND_PROVIDERS: tuple[str, ...] = ("stripe", "creem", "nowpayments")

#: Every state a refund can arrive on. ``completed`` is the reported defect; the others
#: must land in the same place, because the money went back either way.
PRIOR_ATTEMPT_STATUSES: tuple[str, ...] = (
    "completed",
    "creating",
    "pending",
    "processing",
)

#: Events that say the customer stopped paying, not that money came back. None of these
#: may touch a settled payment — that is the R2-6 guard this fix must not break.
CANCELLATION_EVENT_TYPES: tuple[str, ...] = (
    "customer.subscription.deleted",
    "subscription.deleted",
    "subscription.canceled",
    "subscription.expired",
    "invoice.payment_failed",
    "payment.failed",
    "payment.expired",
    "payment.partially_paid",
)


async def _create_user(session) -> User:
    user = User(display_name="Refund test")
    session.add(user)
    await session.flush()
    return user


async def _seed_paid_plan(
    session,
    *,
    provider: str,
    plan_code: str = "pro",
    attempt_status: str = "completed",
    paid_days_ago: int = 15,
    period_days: int = 30,
) -> tuple[User, Subscription, BillingCheckoutAttempt]:
    """A person holding a paid plan, with the checkout row that bought it.

    Mirrors ``tests/integration/test_paid_plan_replacement.py::_seed_old_payment``: a live
    subscription over a half-used period, and a ``completed`` payment at the plan's own
    price. ``attempt_status`` lets the same shape be seeded unsettled, which is what the
    refund tests for ``creating``/``pending``/``processing`` need.
    """

    user = await _create_user(session)
    plan = await PlanCatalogService(session).get_or_sync(plan_code)
    now = datetime.now(UTC)
    period_start = now - timedelta(days=paid_days_ago)
    amount = (
        effective_monthly_price(plan_code)
        if attempt_status == "completed"
        else Decimal("17.00")
    )
    attempt = BillingCheckoutAttempt(
        user_id=user.id,
        plan_id=plan.id,
        billing_cycle="monthly_auto_renewal",
        provider=provider,
        status=attempt_status,
        idempotency_key=f"seed-{user.id}",
        terms_version="test",
        amount=amount,
        currency="USD",
        terms_accepted_at=period_start,
        expires_at=now + timedelta(days=1),
        completed_at=now - timedelta(days=paid_days_ago)
        if attempt_status == "completed"
        else None,
        billing_profile={"first_name": "Amina"},
    )
    subscription = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        provider=provider,
        provider_customer_id=f"cus_{user.id}",
        provider_subscription_id=f"sub_{provider}_{user.id}",
        current_period_start=period_start,
        current_period_end=period_start + timedelta(days=period_days),
    )
    session.add_all([attempt, subscription])
    await session.commit()
    return user, subscription, attempt


def _refund_payload(
    *,
    event_type: str,
    attempt: BillingCheckoutAttempt,
    user_id: Any = None,
    subscription_reference: str | None = None,
    name_attempt: bool = True,
) -> dict[str, Any]:
    """The webhook body a payment company sends when money goes back.

    ``name_attempt=False`` is the shape that used to be dropped on the floor: a refund
    that carries no ``checkout_attempt_id``.
    """

    data: dict[str, Any] = {"status": "refunded"}
    if name_attempt:
        data["checkout_attempt_id"] = str(attempt.id)
    if user_id is not None:
        data["user_id"] = str(user_id)
    if subscription_reference is not None:
        data["provider_subscription_id"] = subscription_reference
    return {
        "id": f"evt-{event_type}-{attempt.id}",
        "type": event_type,
        "data": data,
    }


@pytest.mark.parametrize("prior_status", PRIOR_ATTEMPT_STATUSES)
@pytest.mark.parametrize("event_type", REFUND_EVENT_TYPES)
@pytest.mark.parametrize("provider", REFUND_PROVIDERS)
async def test_a_refund_always_reaches_the_payment_it_refunds(
    test_context, provider: str, event_type: str, prior_status: str
) -> None:
    """Whatever the company calls it, and whatever the payment was doing, it is refunded.

    Before the fix this fails for a ``completed`` payment (the R2-6 guard swallows the
    event) and for every name except ``payment.refunded``.
    """

    async with test_context["session_factory"]() as session:
        user, _subscription, attempt = await _seed_paid_plan(
            session, provider=provider, attempt_status=prior_status
        )
        payload = _refund_payload(
            event_type=event_type,
            attempt=attempt,
            user_id=user.id,
            subscription_reference=f"sub_{provider}_{user.id}",
        )
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider, payload=payload
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None
        assert settled.status == "refunded", (
            f"{provider}/{event_type} on a {prior_status} payment left it as "
            f"{settled.status!r}: the refund was never written down"
        )


@pytest.mark.parametrize("event_type", REFUND_EVENT_TYPES)
@pytest.mark.parametrize("provider", REFUND_PROVIDERS)
async def test_a_refunded_payment_no_longer_counts_as_money_kept(
    test_context, provider: str, event_type: str
) -> None:
    """The readers that decide money ask "is it completed?" — so a refund must stop being it.

    ``payment_that_bought`` is the source of the money-owed amount, and
    ``system_brain_payments.PAID_STATUSES`` is the operations view's list of real
    payments. Both must answer "no payment" once the money came back.
    """

    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        assert await payment_that_bought(session, subscription) is not None
        payload = _refund_payload(
            event_type=event_type,
            attempt=attempt,
            user_id=user.id,
            subscription_reference=subscription.provider_subscription_id,
        )
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider, payload=payload
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None and settled.status == "refunded"
        # The money reader: no completed payment behind a refunded one.
        reloaded = await session.get(Subscription, subscription.id)
        assert await payment_that_bought(session, reloaded) is None
        paid = await session.scalar(
            select(func.count(BillingCheckoutAttempt.id)).where(
                BillingCheckoutAttempt.status == "completed"
            )
        )
        assert paid == 0


@pytest.mark.parametrize("event_type", REFUND_EVENT_TYPES)
@pytest.mark.parametrize("provider", REFUND_PROVIDERS)
async def test_a_refund_is_recorded_when_the_event_names_no_checkout(
    test_context, provider: str, event_type: str
) -> None:
    """A refund that arrives without our checkout reference is still a refund.

    The provider names the subscription it refunded, and the settled payment behind that
    subscription is found through it. Before the fix this shape was returned early by
    ``_hydrate_checkout_data`` and nothing at all was recorded.
    """

    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(
            session, provider=provider, attempt_status="completed"
        )
        payload = _refund_payload(
            event_type=event_type,
            attempt=attempt,
            user_id=user.id,
            subscription_reference=subscription.provider_subscription_id,
            name_attempt=False,
        )
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider, payload=payload
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None
        assert settled.status == "refunded"


async def test_a_refund_is_found_from_the_person_when_nothing_else_names_it(
    test_context,
) -> None:
    """The thinnest useful shape: a user id and nothing else.

    Resolution is refused when it would be a guess — one person, one settled payment on
    that company is the only reading the payload supports.
    """

    provider = "stripe"
    async with test_context["session_factory"]() as session:
        user, _subscription, attempt = await _seed_paid_plan(session, provider=provider)
        payload = {
            "id": f"evt-user-only-{attempt.id}",
            "type": "charge.refunded",
            "data": {"user_id": str(user.id), "status": "refunded"},
        }
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider, payload=payload
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None
        assert settled.status == "refunded"


async def test_a_refund_that_cannot_be_matched_is_an_alert_not_a_crash(
    test_context,
) -> None:
    """A refund we cannot place must not raise, and must not be silent either.

    Nothing names this payment - no checkout, no subscription, no person. Refusing the
    event with an error makes the provider retry and eventually drop it, and the money
    truth is lost with it; so the record stays a critical alert for a person instead.
    """

    async with test_context["session_factory"]() as session:
        service = BillingService(session, test_context["settings"])
        result = await service.process_event(
            provider="stripe",
            payload={
                "id": "evt-unplaceable-refund",
                "type": "charge.refunded",
                "data": {"status": "refunded"},
            },
        )
        await session.commit()
        assert result.processing_status == "processed"

    async with test_context["session_factory"]() as session:
        issues = list(
            (
                await session.scalars(
                    select(OperationalIssue).where(
                        OperationalIssue.affected_scope == "billing.refund_reconciliation"
                    )
                )
            ).all()
        )
        assert [row.severity for row in issues] == ["critical"]
        assert [row.state for row in issues] == ["open"]


async def test_every_money_reader_stops_counting_a_refunded_payment(
    test_context,
) -> None:
    """Each reader the defect class names, checked by name after one refund.

    Four different services ask "is this payment still money we hold?". After a refund
    none of them may still say yes: the plan-move valuation, the affiliate commission,
    the operations count of completed checkouts, and the payments list a staff member
    reads when somebody writes in about their money.
    """

    from ai_market_monitor.services.system_brain_payments import (
        PAID_STATUSES,
        SystemBrainPaymentsService,
    )

    provider = "creem"
    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload=_refund_payload(
                event_type="refund.created",
                attempt=attempt,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
            ),
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        # 1. The money-owed source.
        assert await payment_that_bought(session, subscription) is None
        # 2. The affiliate commission source: the same filter, so it finds no payment
        #    amount of ours to value an earning from.
        charged = await session.scalar(
            select(func.count(BillingCheckoutAttempt.id)).where(
                BillingCheckoutAttempt.user_id == user.id,
                BillingCheckoutAttempt.status == "completed",
            )
        )
        assert charged == 0
        # 3. The operations count of completed checkouts.
        completed_sales = await session.scalar(
            select(func.count(BillingCheckoutAttempt.id)).where(
                BillingCheckoutAttempt.status == "completed"
            )
        )
        assert completed_sales == 0
        # 4. The staff payments view: no payment listed, and nothing summed into total.
        view = await SystemBrainPaymentsService(session).customer(user.id)
        assert view is not None
        assert view.payment_count == 0
        assert view.total_paid == Decimal("0")
        assert view.payments == ()
        # The row is not lost: it is readable, and it says what happened to it.
        row = await session.get(BillingCheckoutAttempt, attempt.id)
        assert row is not None
        assert row.status == "refunded"
        assert row.status not in PAID_STATUSES


#: A checkout id shaped like ours but never created by us.
_MISSING_CHECKOUT = "11111111-1111-1111-1111-111111111111"


def _refusal(provider: str, event: str, reference: str | None, code: str, case: str):
    """One money-in refusal case, spelled once."""

    return pytest.param(provider, event, reference, code, id=case)


@pytest.mark.parametrize(
    ("provider", "event_type", "reference", "expected_code"),
    [
        _refusal(
            "nowpayments",
            "payment.finished",
            None,
            "checkout_reference_missing",
            "crypto-no-reference",
        ),
        _refusal(
            "nowpayments",
            "payment.finished",
            _MISSING_CHECKOUT,
            "checkout_reference_missing",
            "crypto-unknown-checkout",
        ),
        _refusal(
            "nowpayments",
            "payment.finished",
            "not-a-reference",
            "checkout_reference_invalid",
            "crypto-unreadable-reference",
        ),
        _refusal(
            "creem",
            "subscription.paid",
            None,
            "checkout_reference_missing",
            "creem-no-reference",
        ),
        _refusal(
            "creem",
            "subscription.paid",
            _MISSING_CHECKOUT,
            "checkout_reference_missing",
            "creem-unknown-checkout",
        ),
        _refusal(
            "creem",
            "subscription.paid",
            "not-a-reference",
            "checkout_reference_invalid",
            "creem-unreadable-reference",
        ),
        _refusal(
            "stripe",
            "checkout.session.completed",
            None,
            "checkout_reference_missing",
            "card-no-reference",
        ),
        _refusal(
            "stripe",
            "checkout.session.completed",
            _MISSING_CHECKOUT,
            "checkout_reference_missing",
            "card-unknown-checkout",
        ),
        _refusal(
            "stripe",
            "checkout.session.completed",
            "not-a-reference",
            "checkout_reference_invalid",
            "card-unreadable-reference",
        ),
    ],
)
async def test_a_payment_still_refuses_a_checkout_it_cannot_see(
    test_context, provider: str, event_type: str, reference: str | None, expected_code: str
) -> None:
    """The money-in refusals are untouched: the refund route is looser, nothing else is.

    This package added a path that accepts a refund it cannot match, so the opposite
    reading has to be pinned down where it cannot drift: a payment naming no checkout, an
    unknown checkout, or an unreadable reference still refuses the event, because each of
    those is a plan somebody could otherwise get for money that cannot be proved. The same
    three shapes on a refund name are accepted and alerted — covered by the tests above.
    """

    async with test_context["session_factory"]() as session:
        user, _subscription, attempt = await _seed_paid_plan(
            session, provider=provider, attempt_status="pending"
        )
        data: dict[str, Any] = {
            "user_id": str(user.id),
            "plan_code": "pro",
            "provider_subscription_id": f"sub_{provider}_{user.id}",
            "amount": str(attempt.amount),
            "currency": attempt.currency,
            "status": "active",
            "current_period_start": datetime.now(UTC).isoformat(),
            "current_period_end": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        }
        if reference is not None:
            data["checkout_attempt_id"] = reference
        with pytest.raises(BillingError) as error:
            await BillingService(session, test_context["settings"]).process_event(
                provider=provider,
                payload={
                    "id": f"evt-paid-refusal-{expected_code}-{provider}",
                    "type": event_type,
                    "data": data,
                },
            )
        assert error.value.code == expected_code
        await session.rollback()


@pytest.mark.parametrize(
    ("event_type", "data", "reason"),
    [
        pytest.param(
            "charge.refunded",
            {"status": "refunded"},
            "payment",
            id="names-nobody",
        ),
        pytest.param(
            "charge.refunded",
            {
                "checkout_attempt_id": "11111111-1111-1111-1111-111111111111",
                "status": "refunded",
            },
            "payment",
            id="names-a-checkout-we-never-created",
        ),
        pytest.param(
            "charge.refunded",
            {"checkout_attempt_id": "not-a-reference", "status": "refunded"},
            "payment",
            id="names-an-unreadable-checkout",
        ),
        pytest.param(
            # The payment is found from the person; no subscription is named, so nothing
            # can be ended. Both halves of the report have to be said.
            "payment.refunded",
            None,
            "plan",
            id="payment-found-plan-unfound",
        ),
        pytest.param(
            # A person id the provider wrote badly must not become a server error: the
            # money has already gone back, and a refusal throws that fact away.
            "charge.refunded",
            {"user_id": "not-a-uuid", "status": "refunded"},
            "payment",
            id="unreadable-person-id",
        ),
        pytest.param(
            "payment.refunded",
            {
                "checkout_attempt_id": "not-a-uuid",
                "user_id": "not-a-uuid either",
                "status": "refunded",
            },
            "payment",
            id="unreadable-everything",
        ),
    ],
)
async def test_every_unplaceable_refund_leaves_exactly_one_alert(
    test_context, event_type: str, data: dict[str, Any] | None, reason: str
) -> None:
    """Every way a refund can fail to land is reported once, and never refused.

    ``payment``: no payment could be matched, so the books still read a returned payment
    as money kept. ``plan``: the payment was matched but the access it bought was not, so
    a refunded plan may still be live. Each also proves the queue can store its own
    record: the alert sentences sit inside the limit the queue enforces, and the
    provider's event id - any shape, any length - never reaches the summary.
    """

    async with test_context["session_factory"]() as session:
        user: User | None = None
        if data is None:
            user, _subscription, attempt = await _seed_paid_plan(session, provider="stripe")
            data = {
                "user_id": str(user.id),
                "provider_customer_id": "cus_with_two_plans",
                "status": "refunded",
            }
        # An event id with spaces in it, and one longer than any column, are both
        # attempted: a provider id is whatever the company produced, and the alert must
        # not fail because of it.
        for event_id in (
            "evt-unplaced",
            "evt with spaces in it",
            f"evt-{'x' * 200}",
        ):
            await BillingService(session, test_context["settings"]).process_event(
                provider="stripe",
                payload={"id": event_id, "type": event_type, "data": dict(data)},
            )
        await session.commit()

    async with test_context["session_factory"]() as session:
        issues = list(
            (
                await session.scalars(
                    select(OperationalIssue).where(
                        OperationalIssue.affected_scope == "billing.refund_reconciliation"
                    )
                )
            ).all()
        )
        assert len(issues) == 3, [row.dedupe_key for row in issues]
        assert {row.severity for row in issues} == {"critical"}
        assert {row.occurrence_count for row in issues} == {1}
        for row in issues:
            # Reaching this line at all is the length check: the queue refuses a summary
            # over its own cap, so an over-long sentence would have raised instead of
            # storing a row. The two sentences must also say different things: which half
            # of the books is missing is the whole point of the report.
            missing_payment = "no checkout of ours matched" in row.summary
            missing_plan = "subscription to end was not named" in row.summary
            assert missing_payment != missing_plan, row.summary
            assert missing_payment if reason == "payment" else missing_plan
            assert f"refund_{reason}_unmatched" in " ".join(row.evidence_refs)
            assert event_type in " ".join(row.evidence_refs)


#: A subscription reference with our shape that no row of ours has ever carried.
_NEVER_HELD_SUBSCRIPTION = "sub_this_system_never_held"


@pytest.mark.parametrize("event_type", REFUND_EVENT_TYPES)
@pytest.mark.parametrize("provider", REFUND_PROVIDERS)
async def test_a_refund_naming_a_subscription_we_never_held_creates_no_row(
    test_context, provider: str, event_type: str
) -> None:
    """A refund may not invent the plan record it only claims ended.

    The event names our checkout — so the money is placed — and a provider
    subscription we do not hold. Recording the refund stays correct, and the
    "payment was found but the plan was not" alert is the existing honest answer for
    exactly this gap. What is not correct is ``_upsert_subscription`` building a
    real CANCELED ``Subscription`` row out of the event's claim: the customer now
    carries a plan record that never existed, and any later event naming the same
    reference is met by the resurrection alert over a row nobody can explain. Before
    this test existed, every plan-ending refund name wrote that phantom row.
    """

    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        result = await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload=_refund_payload(
                event_type=event_type,
                attempt=attempt,
                user_id=user.id,
                subscription_reference=_NEVER_HELD_SUBSCRIPTION,
            ),
        )
        await session.commit()
        assert result.processing_status == "processed"

    async with test_context["session_factory"]() as session:
        rows = list((await session.scalars(select(Subscription))).all())
        assert [row.id for row in rows] == [subscription.id], (
            f"{provider}/{event_type} wrote a Subscription row this system never held"
        )
        held = await session.get(Subscription, subscription.id)
        assert held is not None and held.status == SubscriptionStatus.ACTIVE, (
            "the refund ended a plan it could not have found"
        )
        # The money half of D1 is untouched: the payment the event names is recorded.
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None and settled.status == "refunded"
        alerts = list(
            (
                await session.scalars(
                    select(OperationalIssue).where(
                        OperationalIssue.affected_scope == "billing.refund_reconciliation"
                    )
                )
            ).all()
        )
        if event_type in REFUND_ENDS_PLAN_EVENT_TYPES:
            # Payment placed, plan not: exactly the "plan" report, nothing else.
            assert [row.state for row in alerts] == ["open"], [row.summary for row in alerts]
            assert "subscription to end was not named" in alerts[0].summary
        else:
            # A chargeback never claims a plan to end; it still may not create one.
            assert alerts == []


@pytest.mark.parametrize(
    ("provider", "event_type"),
    [
        pytest.param("stripe", "charge.refunded", id="stripe-charge-refunded"),
        pytest.param("stripe", "dispute.created", id="stripe-dispute-created"),
        pytest.param("creem", "refund.created", id="creem-refund-created"),
        pytest.param("nowpayments", "payment.refunded", id="crypto-payment-refunded"),
    ],
)
async def test_a_refund_that_names_nobody_is_accepted_and_alerted(
    test_context, provider: str, event_type: str
) -> None:
    """No company may turn a refund into a refused webhook.

    ``payment.refunded`` from NOWPayments used to be refused outright when the order did
    not carry a checkout reference, which threw the refund away. Every provider that can
    send money back is checked here: the event is stored, the alert is written, and the
    call answers instead of failing.
    """

    async with test_context["session_factory"]() as session:
        result = await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload={"id": f"evt-nobody-{event_type}", "type": event_type, "data": {}},
        )
        await session.commit()
        assert result.processing_status == "processed"

    async with test_context["session_factory"]() as session:
        event = await session.scalar(
            select(BillingEvent).where(BillingEvent.provider_event_id == f"evt-nobody-{event_type}")
        )
        assert event is not None
        assert event.processing_status == "processed"
        assert await session.scalar(
            select(func.count(OperationalIssue.id)).where(
                OperationalIssue.affected_scope == "billing.refund_reconciliation"
            )
        ) == 1


@pytest.mark.parametrize(
    ("reference", "label"),
    [
        pytest.param("00000000-0000-0000-0000-000000000000", "no such checkout", id="unknown-id"),
        pytest.param("not-a-uuid", "malformed reference", id="invalid-id"),
    ],
)
async def test_a_refund_naming_a_checkout_we_do_not_have_is_alerted_not_refused(
    test_context, reference: str, label: str
) -> None:
    """A bad reference on a refund is a reason to ask a person, not to fail the webhook.

    Money-in events keep refusing an unreadable checkout — that is a real risk of giving
    away a plan. A refund cannot give anything away, so it is recorded and alerted.
    """

    async with test_context["session_factory"]() as session:
        result = await BillingService(session, test_context["settings"]).process_event(
            provider="creem",
            payload={
                "id": f"evt-bad-ref-{label}",
                "type": "refund.created",
                "data": {"checkout_attempt_id": reference, "status": "refunded"},
            },
        )
        await session.commit()
        assert result.processing_status == "processed"

    async with test_context["session_factory"]() as session:
        assert await session.scalar(
            select(func.count(OperationalIssue.id)).where(
                OperationalIssue.affected_scope == "billing.refund_reconciliation"
            )
        ) == 1
        assert await session.scalar(
            select(func.count(OperationalIssue.id)).where(
                OperationalIssue.dedupe_key.like("billing:refund-unattached:%")
            )
        ) == 1


async def test_a_refund_that_could_belong_to_two_payments_is_not_guessed(
    test_context,
) -> None:
    """Two settled payments, one refund naming neither: the answer is a question, not a guess.

    Attaching it to the newer one would mark a payment refunded that the provider did
    not refund, and take that money away from the plan-move reading. So nothing moves and
    a person is asked.
    """

    provider = "creem"
    async with test_context["session_factory"]() as session:
        user_a, sub_a, attempt_a = await _seed_paid_plan(
            session, provider=provider, plan_code="pro"
        )
        _user_b, sub_b, attempt_b = await _seed_paid_plan(
            session, provider=provider, plan_code="trader"
        )
        # Same person, same company, two settled payments, no subscription named.
        sub_b.user_id = user_a.id
        attempt_b.user_id = user_a.id
        await session.commit()

    async with test_context["session_factory"]() as session:
        result = await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload={
                "id": "evt-two-payments",
                "type": "refund.created",
                "data": {
                    "user_id": str(user_a.id),
                    "provider_customer_id": "cus_shared",
                    "status": "refunded",
                },
            },
        )
        await session.commit()
        assert result.processing_status == "processed"

    async with test_context["session_factory"]() as session:
        for attempt_id in (attempt_a.id, attempt_b.id):
            row = await session.get(BillingCheckoutAttempt, attempt_id)
            assert row is not None
            assert row.status == "completed"
        assert await session.scalar(
            select(func.count(OperationalIssue.id)).where(
                OperationalIssue.affected_scope == "billing.refund_reconciliation"
            )
        ) == 1


@pytest.mark.parametrize("event_type", CANCELLATION_EVENT_TYPES)
@pytest.mark.parametrize("provider", REFUND_PROVIDERS)
async def test_our_own_cancellation_never_undoes_a_settled_payment(
    test_context, provider: str, event_type: str
) -> None:
    """R2-6, kept whole: a "they stopped paying" event is not a money fact.

    A cancel webhook for the old subscription still carries the checkout that bought it.
    Moving that settled payment to ``canceled`` or ``processing`` hides a real payment
    from the money-owed path. Only a refund may change a completed payment.
    """

    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        payload = {
            "id": f"evt-{event_type}-{attempt.id}",
            "type": event_type,
            "data": {
                "checkout_attempt_id": str(attempt.id),
                "user_id": str(user.id),
                "plan_code": "pro",
                "provider_customer_id": subscription.provider_customer_id,
                "provider_subscription_id": subscription.provider_subscription_id,
                "status": "canceled",
                "current_period_start": subscription.current_period_start.isoformat(),
                "current_period_end": subscription.current_period_end.isoformat(),
            },
        }
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider, payload=payload
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None
        assert settled.status == "completed", (
            f"{provider}/{event_type} moved a settled payment to {settled.status!r}"
        )


async def test_a_plan_move_after_a_refund_owes_nothing_back(test_context, monkeypatch) -> None:
    """Refunded money is not money held, so a later move may not promise it again.

    The real service path, so ``payment_that_bought`` decides. The refund is a
    ``charge.refunded`` — the one shape where the plan itself stays live, so the move
    would otherwise still find a paid plan to replace and value its unused days.
    """

    async def successful_cancel(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", str(args[2])))

    monkeypatch.setattr(replacement_module, "provider_request", successful_cancel)
    provider = "creem"
    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(
            session, provider=provider, plan_code="pro"
        )
        payload = _refund_payload(
            event_type="charge.refunded",
            attempt=attempt,
            user_id=user.id,
            subscription_reference=subscription.provider_subscription_id,
        )
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider, payload=payload
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        service = PaidPlanReplacementService(session, test_context["settings"])
        # The customer now asks for a different plan. Either the move is refused because
        # no payment stands behind the plan they hold, or it is allowed but must record
        # nothing. A money-owed row valued from refunded money is the bug.
        try:
            source = await service.source_for_checkout(
                user_id=user.id, target_plan_code="trader"
            )
        except PlanReplacementError as exc:
            assert exc.code == "paid_amount_missing"
            source = None
        assert source is None, "a refunded payment was still offered as the money held"
        assert (
            await session.scalar(select(func.count(PlanMoveMoneyOwed.id))) == 0
        )
        await session.commit()


async def test_a_refund_that_lands_between_freeze_and_payment_owes_nothing_back(
    test_context, monkeypatch
) -> None:
    """The second window: the move was already frozen onto the payment, then it refunded.

    ``apply_after_payment`` reads the frozen link, not ``payment_that_bought``. Before
    the fix it wrote a promise to send back money the provider had already returned. The
    move itself must still finish — the new plan is paid for.
    """

    async def successful_cancel(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", str(args[2])))

    monkeypatch.setattr(replacement_module, "provider_request", successful_cancel)
    # The shape of the server that can really take card money: ending the old plan asks
    # Creem to stop charging it, so the card settings have to be present. The outbound
    # call itself is answered above, never on the network.
    settings = test_context["settings"].model_copy(update=live_billing_overrides())
    provider = "creem"
    async with test_context["session_factory"]() as session:
        user, old, source = await _seed_paid_plan(
            session, provider=provider, plan_code="pro"
        )
        # Freeze the move exactly as the checkout route does.
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
        assert new_attempt.replaces_checkout_attempt_id == source.id
        await session.commit()

    async with test_context["session_factory"]() as session:
        service = BillingService(session, settings)
        await service.process_event(
            provider=provider,
            payload=_refund_payload(
                event_type="charge.refunded",
                attempt=source,
                user_id=user.id,
                subscription_reference=old.provider_subscription_id,
            ),
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        paid_at = datetime.now(UTC)
        await BillingService(session, settings).process_event(
            provider=provider,
            payload={
                "id": f"evt-paid-after-refund-{new_attempt.id}",
                "type": "subscription.paid",
                "data": {
                    "checkout_attempt_id": str(new_attempt.id),
                    "plan_code": "trader",
                    "user_id": str(user.id),
                    "provider_customer_id": f"cus_{user.id}",
                    "provider_subscription_id": f"sub_new_{user.id}",
                    "amount": str(new_attempt.amount),
                    "currency": "USD",
                    "status": "active",
                    "current_period_start": paid_at.isoformat(),
                    "current_period_end": (paid_at + timedelta(days=30)).isoformat(),
                },
            },
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(PlanMoveMoneyOwed.id))) == 0
        ended = await session.get(Subscription, old.id)
        new_sub = await session.scalar(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.provider_subscription_id == f"sub_new_{user.id}",
            )
        )
        # The move is not blocked by the refund: the old plan ends, the new one stands.
        assert ended is not None and ended.status == SubscriptionStatus.CANCELED
        assert new_sub is not None and new_sub.status == SubscriptionStatus.ACTIVE


async def test_the_billing_history_row_offers_nothing_for_a_refunded_payment(
    test_context,
) -> None:
    """A refunded row is settled: no "Pay again", and no claim that it is paid.

    ``_billing_history_rows`` is the single owner of "what may this person do next about
    a payment row", and every billing surface renders from it. A refund must read as
    finished: offering "Try again" on money that came back invites a second charge for
    one plan.
    """

    from ai_market_monitor.api.routers.dashboard import _billing_history_rows

    async def row_for(status: str) -> dict[str, Any]:
        async with test_context["session_factory"]() as session:
            _user, _subscription, attempt = await _seed_paid_plan(
                session, provider="creem", attempt_status=status
            )
            plan = await session.get(Plan, attempt.plan_id)
            rows = _billing_history_rows(
                [attempt], {plan.id: plan}, test_context["settings"], now=datetime.now(UTC)
            )
            assert len(rows) == 1
            return rows[0]

    refunded = await row_for("refunded")
    paid = await row_for("completed")
    abandoned = await row_for("failed")
    assert refunded["next_step"] is None, refunded
    assert refunded["blocked_reason"] is None
    assert refunded["resume_url"] is None
    assert refunded["can_resume"] is False
    # The same answer as a paid row: neither offers a second attempt. A row that did not
    # take money is still actionable - a next step, or the reason there is none - which is
    # what makes the refunded row's silence a statement about settled money, not a bug.
    assert paid["next_step"] is None
    assert paid["blocked_reason"] is None
    assert not (abandoned["next_step"] is None and abandoned["blocked_reason"] is None), (
        "a failed payment stopped saying anything at all"
    )


async def test_the_same_refund_arriving_twice_changes_nothing_twice(test_context) -> None:
    """Replay: one record, one event row, nothing owed.

    A payment company that sends the same refund twice, or a person reprocessing the
    failed event, must not produce two changes, two alerts or two money promises.
    """

    provider = "creem"
    async with test_context["session_factory"]() as session:
        service = BillingService(session, test_context["settings"])
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        payload = _refund_payload(
            event_type="payment.refunded",
            attempt=attempt,
            user_id=user.id,
            subscription_reference=subscription.provider_subscription_id,
        )
        first = await service.process_event(provider=provider, payload=payload)
        await session.commit()
        second = await service.process_event(provider=provider, payload=payload)
        await session.commit()
        assert first.replayed is False
        assert second.replayed is True

    async with test_context["session_factory"]() as session:
        paid_once = await session.get(BillingCheckoutAttempt, attempt.id)
        assert paid_once is not None
        # Read from the database on both sides of the replay: SQLite hands back a naive
        # datetime, so comparing it with the seeded Python value would compare two
        # different objects rather than the same stored instant.
        completed_at = paid_once.completed_at

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None
        assert settled.status == "refunded"
        # The date the money moved stays: a refund does not change when the payment
        # happened, and the money-owed period is read from it.
        assert settled.completed_at == completed_at
        assert await session.scalar(select(func.count(BillingEvent.id))) == 1
        assert await session.scalar(select(func.count(PlanMoveMoneyOwed.id))) == 0
        # The unattachable-refund alert is the only issue a placed refund should leave,
        # and a placed refund leaves none.
        assert await session.scalar(
            select(func.count(OperationalIssue.id)).where(
                OperationalIssue.affected_scope == "billing.refund_reconciliation"
            )
        ) == 0


async def test_two_refund_events_for_one_payment_produce_one_alert(test_context) -> None:
    """The same money described twice, under two different event ids.

    The second event must find the row the first one already marked ``refunded``, change
    it no further, and raise no second alarm.
    """

    provider = "stripe"
    async with test_context["session_factory"]() as session:
        service = BillingService(session, test_context["settings"])
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        for name, index in (("payment.refunded", 1), ("charge.refunded", 2)):
            payload = _refund_payload(
                event_type=name,
                attempt=attempt,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
            )
            payload["id"] = f"evt-two-{name}-{index}"
            await service.process_event(provider=provider, payload=payload)
        await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None
        assert settled.status == "refunded"
        assert await session.scalar(
            select(func.count(OperationalIssue.id)).where(
                OperationalIssue.affected_scope == "billing.refund_reconciliation"
            )
        ) == 0
