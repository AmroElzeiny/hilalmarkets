"""An unfinished attempt is one that is still open, not one that failed to pay.

The staff payments page answers a simple question about a customer: *did they pay, and is
anything still outstanding?* One number on that page — "Unfinished attempts" — is read by
a staff member as "this person still has a checkout they can finish". So the number must
count only attempts that are **open**: ``creating``, ``pending``, ``processing``.

Defect R5 was the opposite reading. The count asked "which attempts are not in
``PAID_STATUSES``?" — a negative question — and every word the answer did not know fell
into the count: ``refunded``, ``cancelled``, ``failed``, ``expired``, ``partially_paid``.
A customer who was refunded, or who closed a checkout without paying, therefore showed up
as somebody still holding an unfinished payment. A returned payment is money this product
does not hold and no longer expects; calling it "unfinished" invites staff to chase money
that has already gone back.

The rule these tests pin, across the whole family of attempt words rather than the
reported case:

* the count is the number of attempts whose status is one of the three open words;
* every other word — paid, refunded, cancelled, failed, expired, partially paid — is a
  finished attempt and counts zero;
* the open words have exactly one owner, the billing service's
  ``OPEN_ATTEMPT_STATUSES``, which this view imports rather than re-spelling, because a
  second list of the same three words is how the two readers drifted apart;
* ``PAID_STATUSES`` still means what it always meant: money that was really taken.

On the unfixed code the parametrised count test fails for ``refunded``, ``cancelled``,
``failed``, ``expired`` and ``partially_paid`` (each wrongly counted as 1), and the
one-owner tests fail because no such constant existed to import.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ai_market_monitor.db.models import BillingCheckoutAttempt, User
from ai_market_monitor.services.entitlements import PlanCatalogService
from ai_market_monitor.services.system_brain_payments import (
    PAID_STATUSES,
    SystemBrainPaymentsService,
)

#: Every status word this product's own code, or a payment company's event, can leave on a
#: checkout attempt. Written out one by one: a fix that only handles the reported word
#: must fail here for the rest.
ATTEMPT_STATUSES: tuple[str, ...] = (
    "creating",  # checkout row made, nobody sent to a payment company yet
    "pending",  # sent to the payment company, nothing back yet
    "processing",  # the company says it is working on it
    "completed",  # money taken and kept: a payment
    "succeeded",  # the same fact in another company's words
    "paid",  # and in a third company's words
    "refunded",  # money taken and given back: finished, and owed to nobody
    "cancelled",  # the customer walked away from this checkout: finished
    "failed",  # the company could not take the money: finished
    "expired",  # the payment window closed: finished
    "partially_paid",  # part of the money came back: finished
)

#: The three words that mean "still open". Spelled again here on purpose: this test file
#: is the outside check on the single owner in ``services/billing.py``, so it may not read
#: its expectation from the very constant it is checking.
OPEN_STATUSES: frozenset[str] = frozenset({"creating", "pending", "processing"})

#: The words that mean "this was money". Pinned unchanged by ``test_paid_statuses_still``;
#: R5 must not quietly widen or narrow this list while fixing the other one.
PAID_WORDS: frozenset[str] = frozenset({"completed", "succeeded", "paid"})

#: An attempt that never reached money has no completed time.
_NOT_PAID_AT: Decimal = Decimal("17.00")


async def _seed_one_attempt(session, *, user: User, status: str, plan_id) -> None:
    """One checkout attempt carrying exactly one status word."""

    settled = status in PAID_WORDS
    session.add(
        BillingCheckoutAttempt(
            user_id=user.id,
            plan_id=plan_id,
            billing_cycle="monthly_auto_renewal",
            provider="creem",
            status=status,
            idempotency_key=f"unfinished-{status}-{user.id}",
            terms_version="test",
            amount=_NOT_PAID_AT,
            currency="USD",
            terms_accepted_at=datetime.now(UTC) - timedelta(days=2),
            expires_at=datetime.now(UTC) + timedelta(days=1),
            completed_at=datetime.now(UTC) - timedelta(days=2) if settled else None,
            billing_profile={"first_name": "Amina"},
        )
    )


async def _customer_with(session, *, display_name: str, statuses: tuple[str, ...]):
    """A person and their checkout attempts, one per status word asked for."""

    user = User(display_name=display_name)
    session.add(user)
    await session.flush()
    plan = await PlanCatalogService(session).get_or_sync("pro")
    for status in statuses:
        await _seed_one_attempt(session, user=user, status=status, plan_id=plan.id)
    await session.commit()
    return user


@pytest.mark.parametrize("status", ATTEMPT_STATUSES)
async def test_unfinished_attempts_counts_only_an_attempt_that_is_still_open(
    test_context, status: str
) -> None:
    """One attempt, one word: the count is 1 only while the attempt is open.

    ``refunded`` and ``cancelled`` are the reported defect (defect R5): the old code asked
    "is it not paid?" and counted both. ``failed``, ``expired`` and ``partially_paid`` are
    the same mistake in the same list, so they are checked by name too.
    """

    async with test_context["session_factory"]() as session:
        user = await _customer_with(session, display_name=f"R5 {status}", statuses=(status,))

    async with test_context["session_factory"]() as session:
        view = await SystemBrainPaymentsService(session).customer(user.id)
        assert view is not None, "the customer vanished from the payments view"
        expected = 1 if status in OPEN_STATUSES else 0
        assert view.unfinished_attempts == expected, (
            f"an attempt reading {status!r} was counted as {view.unfinished_attempts} "
            f"unfinished; only {sorted(OPEN_STATUSES)} are still open, so this word "
            f"{'is finished and must count zero' if expected == 0 else 'is open'}"
        )


@pytest.mark.parametrize("status", ATTEMPT_STATUSES)
async def test_no_status_word_is_ever_counted_twice(test_context, status: str) -> None:
    """Paid and unfinished are two different questions, and one attempt answers at most one.

    A settled payment must not appear as an unfinished attempt (that is R5's twin: the
    same row shown as both a sale and money still owed), and an open attempt must not
    appear as a payment.
    """

    async with test_context["session_factory"]() as session:
        user = await _customer_with(session, display_name=f"R5-pair {status}", statuses=(status,))

    async with test_context["session_factory"]() as session:
        view = await SystemBrainPaymentsService(session).customer(user.id)
        assert view is not None
        paid = view.payment_count
        unfinished = view.unfinished_attempts
        assert paid + unfinished <= 1, (
            f"{status!r} was counted as both money taken ({paid}) and an unfinished "
            f"attempt ({unfinished}); one checkout attempt is at most one of those"
        )
        assert (paid == 1) == (status in PAID_WORDS), (
            f"{status!r} counted as payment={paid}; the paid words are {sorted(PAID_WORDS)}"
        )
        assert (unfinished == 1) == (status in OPEN_STATUSES)


async def test_a_customer_holding_every_status_word_is_counted_by_word_not_by_elimination(
    test_context,
) -> None:
    """The whole family on one person: 11 attempts, 3 unfinished, 3 paid, nothing else.

    The old reading was a subtraction ("everything that is not paid"), so it grew with
    every new word a payment company introduced. The new reading is a positive list, and
    this test says what that list contains today: three open attempts and three payments,
    and five finished attempts that are neither.
    """

    async with test_context["session_factory"]() as session:
        user = await _customer_with(
            session, display_name="R5 every word", statuses=ATTEMPT_STATUSES
        )

    async with test_context["session_factory"]() as session:
        view = await SystemBrainPaymentsService(session).customer(user.id)
        assert view is not None
        assert view.unfinished_attempts == len(OPEN_STATUSES), (
            "the unfinished count is the number of still-open attempts, not the number of "
            "attempts that are not paid"
        )
        assert view.payment_count == len(PAID_WORDS)


def test_the_open_words_have_exactly_one_owner() -> None:
    """The view may not keep its own copy of the three open words.

    R5 was not only a wrong operator; it was a second list. Both the writer that decides
    whether an event may still change an attempt and the page that counts unfinished
    attempts ask the same question, so they read the same constant: same object, not two
    equal-looking tables that can drift.
    """

    from ai_market_monitor.services import billing, system_brain_payments

    assert system_brain_payments.OPEN_ATTEMPT_STATUSES is billing.OPEN_ATTEMPT_STATUSES, (
        "the payments view no longer reads the billing service's open list: it has a copy "
        "of its own again, which is the shape of the defect"
    )


def test_the_open_set_is_exactly_the_three_open_words() -> None:
    """What the one owner contains: still open, and nothing finished.

    ``refunded`` and ``cancelled`` must never join this set — a returned payment is not an
    attempt the customer still owes.
    """

    from ai_market_monitor.services.billing import OPEN_ATTEMPT_STATUSES

    assert frozenset({"creating", "pending", "processing"}) == OPEN_ATTEMPT_STATUSES
    assert "refunded" not in OPEN_ATTEMPT_STATUSES
    assert "cancelled" not in OPEN_ATTEMPT_STATUSES
    assert "completed" not in OPEN_ATTEMPT_STATUSES


def test_paid_statuses_still_mean_money_taken() -> None:
    """The fix adds a second list; it does not move the first one.

    ``PAID_STATUSES`` is what the money figures on the same page are summed from. R5 is
    about the unfinished count only, so this set is pinned word for word.
    """

    assert PAID_STATUSES == PAID_WORDS
    assert PAID_STATUSES.isdisjoint(OPEN_STATUSES), (
        "a word cannot be both money taken and an open attempt; the two counts would "
        "double-report one checkout"
    )
