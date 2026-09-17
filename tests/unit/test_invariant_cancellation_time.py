"""The first cancellation time is the one that is true, and it must survive.

``Subscription.canceled_at`` answers one question, and a staff member, a refund report and
a support reply all ask it the same way: *when did this person's access really end?* It is
a stored fact about a moment that already happened. It is not a "last touched" stamp.

A second event describing the same ending is normal. Payment companies re-deliver
webhooks, a cancellation is followed later by the refund of the period, a dispute is
raised after the plan was already closed. The old code took each of those events as a new
cancellation and wrote the current time over the stored moment, in two places: the
cancellation branch of ``BillingService._apply_event`` and
``BillingService._end_refunded_plan``. The customer's access then appears to have ended
days or weeks later than it did — the gap is invisible, and everything read from that date
(the period actually used, the money fairly owed, whether a charge landed after they
stopped paying) is read from the wrong day.

The rule is already written once in ``_upsert_subscription``: a cancellation stamps the
time **only when nothing was recorded yet**. These tests apply that same rule to the two
places that ignored it, and pin the whole family:

* every cancellation-shaped event name, on every company, leaves a recorded time alone;
* every refund-shaped event name does the same, including the refunds that end the plan;
* the first event to arrive still writes a time when the row has none — the fix must not
  simply delete the stamping;
* two cancellations in a row keep the time the first one wrote.

On the unfixed code the "second event must not move the time" tests fail: the stored
moment is replaced by the test's own clock reading, microsecond for microsecond.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from ai_market_monitor.db.models import BillingCheckoutAttempt, Subscription, User
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.services.billing import BillingService
from tests.unit.test_invariant_refund_is_recorded import (
    REFUND_EVENT_TYPES,
    REFUND_PROVIDERS,
    _refund_payload,
    _seed_paid_plan,
)

#: The moment access really ended. Deliberately in the past, and with microseconds, so a
#: replacement is visible as a different instant rather than a rounding difference.
FIRST_CANCELLATION = datetime(2026, 3, 1, 12, 30, 45, 123456, tzinfo=UTC)

#: SQLite keeps the wall-clock part of an aware datetime and drops the offset, so the form
#: read back from the store is this naive value. Both sides of every comparison below are
#: read back from the store, so this constant is only used to say the seed really landed.
FIRST_CANCELLATION_STORED = datetime(2026, 3, 1, 12, 30, 45, 123456)

#: The four event names whose branch in ``_apply_event`` ends access.
CANCEL_EVENT_TYPES: tuple[str, ...] = (
    "customer.subscription.deleted",
    "subscription.deleted",
    "subscription.canceled",
    "subscription.expired",
)

#: The refund names that end the plan, and so run ``_end_refunded_plan`` — the second of
#: the two writers this file is about.
REFUND_ENDS_PLAN_NAMES: tuple[str, ...] = ("payment.refunded", "refund.created")

#: The refund names that are audit-only: a dispute can still be won back, so the plan is
#: not ended. They must not move a recorded cancellation time either.
REFUND_AUDIT_ONLY_NAMES: tuple[str, ...] = (
    "charge.refunded",
    "charge.dispute.created",
    "dispute.created",
)


async def _cancelled_subscription(
    session, *, provider: str
) -> tuple[User, Subscription, BillingCheckoutAttempt]:
    """A person whose paid plan was already cancelled, at a time we know.

    Seeded through the same helper the refund tests use — a real completed payment and a
    live plan — and then moved into the state a weeks-old cancellation leaves:
    ``CANCELED``, ``canceled_at`` written, the payment still settled.
    """

    user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
    subscription.status = SubscriptionStatus.CANCELED
    subscription.canceled_at = FIRST_CANCELLATION
    await session.commit()
    return user, subscription, attempt


async def _stored_canceled_at(test_context, subscription_id: Any) -> datetime | None:
    """What the row really says, read back from the database.

    Read in a fresh session because SQLite hands back a naive datetime: comparing a stored
    value with the seeded Python object would compare two different objects rather than
    the same instant. Byte-for-byte means the stored value, so every comparison in this
    file is made between two readings of the store.
    """

    async with test_context["session_factory"]() as session:
        row = await session.get(Subscription, subscription_id)
    assert row is not None
    return row.canceled_at


def _cancel_payload(
    *,
    provider: str,
    event_type: str,
    user: User,
    subscription: Subscription,
    attempt_id: Any,
    event_id: str | None = None,
) -> dict[str, Any]:
    """The webhook body a payment company sends when the subscription is over."""

    return {
        "id": event_id or f"evt-{event_type}-{subscription.provider_subscription_id}",
        "type": event_type,
        "data": {
            "checkout_attempt_id": str(attempt_id),
            "user_id": str(user.id),
            "plan_code": "pro",
            "provider_customer_id": subscription.provider_customer_id,
            "provider_subscription_id": subscription.provider_subscription_id,
            "status": "canceled",
            "current_period_start": subscription.current_period_start.isoformat(),
            "current_period_end": subscription.current_period_end.isoformat(),
        },
    }


def test_the_two_event_lists_cover_every_name_that_can_end_access() -> None:
    """The family this file tests is the family the code knows, not a shorter list.

    A name added to the billing service's own refund table without being added here is a
    name whose cancellation-time behaviour nobody checks — which is how only one of the
    two writers was ever reported.
    """

    from ai_market_monitor.services.billing import REFUND_EVENT_TYPES as BILLING_REFUND_NAMES

    assert set(REFUND_ENDS_PLAN_NAMES) | set(REFUND_AUDIT_ONLY_NAMES) == set(
        BILLING_REFUND_NAMES
    ), "billing grew its refund vocabulary and this file's coverage did not follow"
    assert set(REFUND_EVENT_TYPES) == set(BILLING_REFUND_NAMES)


@pytest.mark.parametrize("provider", REFUND_PROVIDERS)
@pytest.mark.parametrize("event_type", CANCEL_EVENT_TYPES)
async def test_a_second_cancellation_event_keeps_the_first_cancellation_time(
    test_context, event_type: str, provider: str
) -> None:
    """A cancel webhook arriving after the plan is already closed restates nothing.

    The event is still accepted — the cancellation really did happen — but the date the
    customer stopped having access stays the date it happened.
    """

    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _cancelled_subscription(session, provider=provider)

    before = await _stored_canceled_at(test_context, subscription.id)
    assert before == FIRST_CANCELLATION_STORED, (
        f"the seeded ending date did not survive the seed itself: {before!r}"
    )

    async with test_context["session_factory"]() as session:
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload=_cancel_payload(
                provider=provider,
                event_type=event_type,
                user=user,
                subscription=subscription,
                attempt_id=attempt.id,
            ),
        )
        await session.commit()

    after = await _stored_canceled_at(test_context, subscription.id)
    assert after == before, (
        f"{provider}/{event_type} moved canceled_at from {before!r} to {after!r}: a "
        "second cancellation rewrote when access really ended"
    )
    assert after is not None and after.isoformat() == before.isoformat()
    async with test_context["session_factory"]() as session:
        row = await session.get(Subscription, subscription.id)
        assert row is not None and row.status == SubscriptionStatus.CANCELED


@pytest.mark.parametrize("provider", REFUND_PROVIDERS)
@pytest.mark.parametrize("event_type", REFUND_ENDS_PLAN_NAMES)
async def test_a_refund_after_a_cancellation_keeps_the_first_cancellation_time(
    test_context, event_type: str, provider: str
) -> None:
    """The refund of a period is not a second cancellation.

    This is the ordinary sequence — the plan is cancelled, the money comes back afterwards
    — and it is the one that reaches ``_end_refunded_plan``, which used to stamp the
    refund's arrival as the end of access.
    """

    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _cancelled_subscription(session, provider=provider)

    before = await _stored_canceled_at(test_context, subscription.id)
    assert before == FIRST_CANCELLATION_STORED

    async with test_context["session_factory"]() as session:
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload=_refund_payload(
                event_type=event_type,
                attempt=attempt,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
            ),
        )
        await session.commit()

    after = await _stored_canceled_at(test_context, subscription.id)
    assert after == before, (
        f"{provider}/{event_type} moved canceled_at from {before!r} to {after!r}: the "
        "refund of a period restated the day access ended"
    )
    assert after is not None and after.isoformat() == before.isoformat()


@pytest.mark.parametrize("event_type", REFUND_AUDIT_ONLY_NAMES)
async def test_a_dispute_after_a_cancellation_changes_nothing_at_all(
    test_context, event_type: str
) -> None:
    """A contested charge is audit-only, and must not re-date the ending either.

    These names never end the plan — a dispute can still be won back — so nothing about
    the recorded cancellation time may move, and no new subscription may appear.
    """

    provider = "creem"
    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _cancelled_subscription(session, provider=provider)

    async with test_context["session_factory"]() as session:
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload=_refund_payload(
                event_type=event_type,
                attempt=attempt,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
            ),
        )
        await session.commit()

    after = await _stored_canceled_at(test_context, subscription.id)
    assert after == FIRST_CANCELLATION_STORED, (
        f"{provider}/{event_type} moved the recorded end of access to {after!r}"
    )
    async with test_context["session_factory"]() as session:
        rows = await session.scalar(select(func.count(Subscription.id)))
        assert rows == 1, "an audit-only refund wrote a second subscription row"


@pytest.mark.parametrize("event_type", CANCEL_EVENT_TYPES + REFUND_ENDS_PLAN_NAMES)
async def test_the_first_cancellation_still_records_a_time(
    test_context, event_type: str
) -> None:
    """The fix keeps the stamp; it only stops the rewrite.

    A plan whose access has never been ended before, ended now, must get a time. Refusing
    to overwrite a stored date is not the same as never writing one — without this case,
    deleting the line would satisfy every test above and leave each real cancellation with
    no ending date at all.
    """

    provider = "creem"
    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        assert subscription.canceled_at is None

    async with test_context["session_factory"]() as session:
        payload: dict[str, Any]
        if event_type in REFUND_ENDS_PLAN_NAMES:
            payload = _refund_payload(
                event_type=event_type,
                attempt=attempt,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
            )
        else:
            payload = _cancel_payload(
                provider=provider,
                event_type=event_type,
                user=user,
                subscription=subscription,
                attempt_id=attempt.id,
            )
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider, payload=payload
        )
        await session.commit()

    stored = await _stored_canceled_at(test_context, subscription.id)
    assert stored is not None, f"{event_type} ended access and recorded no time"
    assert stored != FIRST_CANCELLATION_STORED, "the seeded past date was not replaced"
    # The stamp is roughly this moment: a first cancellation is really recorded.
    assert stored > (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None), (
        f"canceled_at read {stored!r}, which is not the time this event arrived"
    )


async def test_two_cancellations_in_a_row_keep_the_time_the_first_one_wrote(
    test_context,
) -> None:
    """The whole story in order, with nothing pre-seeded: cancel, then a second cancel.

    Both events arrive through the real webhook service, so the recorded date is the one
    the first event wrote and the second cannot move it. Payment companies produce exactly
    this by retrying a delivery; a different event id means ``process_event`` cannot
    short-circuit it for us.
    """

    provider = "stripe"
    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        await session.commit()

    async with test_context["session_factory"]() as session:
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload=_cancel_payload(
                provider=provider,
                event_type="customer.subscription.deleted",
                user=user,
                subscription=subscription,
                attempt_id=attempt.id,
            ),
        )
        await session.commit()

    first = await _stored_canceled_at(test_context, subscription.id)
    assert first is not None, "the first cancellation recorded no ending time"

    async with test_context["session_factory"]() as session:
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload=_cancel_payload(
                provider=provider,
                event_type="subscription.canceled",
                user=user,
                subscription=subscription,
                attempt_id=attempt.id,
                event_id="evt-second-delivery-of-the-same-ending",
            ),
        )
        await session.commit()

    second = await _stored_canceled_at(test_context, subscription.id)
    assert second == first, (
        f"the second cancellation moved the ending from {first!r} to {second!r}"
    )
    assert second is not None and second.isoformat() == first.isoformat()

    # A third event of a different kind — the refund of the period — restates nothing either.
    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload=_refund_payload(
                event_type="payment.refunded",
                attempt=settled,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
            ),
        )
        await session.commit()

    third = await _stored_canceled_at(test_context, subscription.id)
    assert third == first, "a later refund re-dated the end of access"
    async with test_context["session_factory"]() as session:
        row = await session.get(Subscription, subscription.id)
        assert row is not None and row.status == SubscriptionStatus.CANCELED
