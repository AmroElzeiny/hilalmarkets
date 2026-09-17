"""What every customer paid, what they asked to change, and when each thing happened.

One screen, because these are three views of one story and an administrator asked about
a single customer needs all three at once: *did this person pay, how often, and did they
ask to stop?*

The three sources are already in the database and none of them is written here:

``billing_checkout_attempts``
    One row per attempt to pay. A completed row is a payment: it holds the plan, the
    amount actually charged, the currency, the discount code used, and which payment
    company took it.
``subscription_plan_changes``
    One row per cancel, upgrade or downgrade form a customer submitted, with the reason
    they picked, anything they typed, and the exact sentence they ticked.
``billing_events``
    Everything the payment company told us, in order.

**Nothing here recalculates money.** Every figure shown is the figure that was stored at
the time. A page that recomputed a price from today's plan table would quietly restate
history the day a price changes — which is exactly what happened on 8 September 2026 when
Plus went from $15 to $9. The stored amount is the truth about what somebody paid. The
one reading applied to a stored figure is ``money_kept`` (paid minus what refund events
stored back): both halves stay stored facts, and a money reader that ignored a stored
partial refund would show staff money the customer no longer owes us.

**Reading is capped and paged.** A single customer's history is small, but the list of
everybody's is not, and one busy account must not make this page slow for the rest. The
totals are counted in the database rather than by loading rows and adding them up in
Python.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.money import money_kept
from ai_market_monitor.core.plans import plan_name
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    BillingEvent,
    Plan,
    Subscription,
    SubscriptionPlanChange,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, SubscriptionStatus
from ai_market_monitor.services.billing import OPEN_ATTEMPT_STATUSES
from ai_market_monitor.services.plan_changes import reason_words

__all__ = [
    "CustomerPayments",
    "PaymentRow",
    "PlanChangeRow",
    "SystemBrainPaymentsService",
    "TimelineEntry",
]

#: How many customers one page of the list shows.
PAGE_SIZE: Final[int] = 25

#: How far back the timelog on one customer reaches. Long enough to cover a year of
#: monthly payments and every form they ever sent, short enough that one page load never
#: pulls an unbounded number of rows.
TIMELINE_LIMIT: Final[int] = 200

#: A checkout attempt that really took money, because "tried to pay four times" and "paid
#: four times" are very different facts about a customer.
#:
#: This is not the other half of a two-way split. An attempt that is neither paid nor
#: still open — ``refunded``, ``cancelled``, ``failed``, ``expired``, ``partially_paid``
#: — is a finished attempt that never became money: it is neither counted here nor as
#: unfinished. The unfinished count asks the positive question "is it still open?"
#: against :data:`ai_market_monitor.services.billing.OPEN_ATTEMPT_STATUSES`, imported
#: above, and never "is it not paid?". Counting a returned payment as an attempt the
#: customer still owes was defect R5.
PAID_STATUSES: Final[frozenset[str]] = frozenset({"completed", "succeeded", "paid"})


@dataclass(frozen=True, slots=True)
class PaymentRow:
    """One payment that really happened."""

    paid_at: datetime
    plan_code: str
    plan_words: str
    amount: Decimal
    currency: str
    provider: str
    billing_cycle: str
    discount_code: str | None
    discount_percent: Decimal | None
    reference: str | None


@dataclass(frozen=True, slots=True)
class PlanChangeRow:
    """One cancel, upgrade or downgrade form a customer submitted."""

    requested_at: datetime
    kind: str
    from_plan_words: str
    to_plan_words: str | None
    timing: str
    timing_words: str
    status: str
    reason_words: str
    reason_text: str | None
    consent_text: str
    consented_at: datetime
    effective_at: datetime | None
    applied_at: datetime | None
    provider_error: str | None


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One thing that happened, in plain words, with the time it happened."""

    happened_at: datetime
    kind: str
    headline: str
    detail: str


@dataclass(frozen=True, slots=True)
class CustomerPayments:
    """Everything one customer's money story contains."""

    user_id: UUID
    display_name: str
    email: str
    current_plan_code: str | None
    current_plan_words: str
    subscription_status: str | None
    period_ends_at: datetime | None
    payment_count: int
    total_paid: Decimal
    first_paid_at: datetime | None
    last_paid_at: datetime | None
    unfinished_attempts: int
    payments: tuple[PaymentRow, ...] = ()
    plan_changes: tuple[PlanChangeRow, ...] = ()
    timeline: tuple[TimelineEntry, ...] = ()
    currencies: tuple[str, ...] = field(default=())


_TIMING_WORDS: Final[dict[str, str]] = {
    "immediate": "Straight away",
    "period_end": "At the end of the paid period",
}

_KIND_WORDS: Final[dict[str, str]] = {
    "cancel": "Cancelled the plan",
    "upgrade": "Moved up a plan",
    "downgrade": "Moved down a plan",
}


def _plan_words(code: str | None) -> str:
    """A plan code as a person would say it, with a safe answer for no plan at all."""

    if not code:
        return "No plan"
    return plan_name(code)


def _timing_words(timing: str) -> str:
    return _TIMING_WORDS.get(timing, timing.replace("_", " ").capitalize())


def _money(amount: Decimal | None, currency: str) -> str:
    if amount is None:
        return "-"
    return f"{amount:.2f} {currency.upper()}"


class SystemBrainPaymentsService:
    """Reads the payment record. Writes nothing, ever.

    Every method is a question about what already happened. Keeping it read-only is what
    makes it safe to open on a live system while payments are running.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ── the list of customers ────────────────────────────────────────────────────

    async def list_customers(
        self,
        *,
        query: str | None = None,
        page: int = 1,
    ) -> dict[str, Any]:
        """One page of everybody who has ever paid, newest payment first.

        The totals are counted in the database. Loading every payment row to add them up
        in Python is what made another admin list take 1.6 GB and freeze the site, so it
        is not done here.
        """

        page = max(1, page)
        totals = (
            select(
                BillingCheckoutAttempt.user_id.label("user_id"),
                func.count(BillingCheckoutAttempt.id).label("payment_count"),
                # What the payments still hold: a partial refund lowers what staff is
                # shown as taken, and the subtraction is written once here as the SQL
                # form of ``money_kept``. Only rows still in ``PAID_STATUSES`` count, so
                # a fully refunded payment has already left this sum altogether, and a
                # ``completed`` row never carries a refund total above its amount.
                func.coalesce(
                    func.sum(
                        BillingCheckoutAttempt.amount
                        - BillingCheckoutAttempt.refunded_amount
                    ),
                    0,
                ).label("total_paid"),
                func.min(BillingCheckoutAttempt.completed_at).label("first_paid_at"),
                func.max(BillingCheckoutAttempt.completed_at).label("last_paid_at"),
            )
            .where(
                BillingCheckoutAttempt.status.in_(PAID_STATUSES),
                BillingCheckoutAttempt.completed_at.is_not(None),
            )
            .group_by(BillingCheckoutAttempt.user_id)
            .subquery()
        )

        rows: Select[Any] = (
            select(
                totals.c.user_id,
                totals.c.payment_count,
                totals.c.total_paid,
                totals.c.first_paid_at,
                totals.c.last_paid_at,
                User.display_name,
                UserIdentity.display_identifier,
                UserIdentity.normalized_identifier,
                Plan.code,
                Subscription.status,
                Subscription.current_period_end,
            )
            .join(User, User.id == totals.c.user_id)
            .outerjoin(
                UserIdentity,
                (UserIdentity.user_id == totals.c.user_id)
                & (UserIdentity.provider == IdentityProvider.EMAIL)
                & (UserIdentity.is_primary.is_(True)),
            )
            .outerjoin(
                Subscription,
                (Subscription.user_id == totals.c.user_id)
                & Subscription.status.in_(
                    [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
                ),
            )
            .outerjoin(Plan, Plan.id == Subscription.plan_id)
        )
        if query:
            needle = f"%{query.strip().lower()}%"
            rows = rows.where(
                or_(
                    func.lower(User.display_name).like(needle),
                    func.lower(UserIdentity.normalized_identifier).like(needle),
                    func.lower(UserIdentity.display_identifier).like(needle),
                )
            )
        rows = rows.order_by(totals.c.last_paid_at.desc()).limit(PAGE_SIZE + 1).offset(
            (page - 1) * PAGE_SIZE
        )
        found = list((await self.session.execute(rows)).all())
        has_next = len(found) > PAGE_SIZE
        found = found[:PAGE_SIZE]

        customers = [
            CustomerPayments(
                user_id=row.user_id,
                display_name=row.display_name or "Customer",
                email=row.display_identifier or row.normalized_identifier or "-",
                current_plan_code=row.code,
                current_plan_words=_plan_words(row.code),
                subscription_status=row.status.value if row.status is not None else None,
                period_ends_at=row.current_period_end,
                payment_count=int(row.payment_count or 0),
                total_paid=Decimal(str(row.total_paid or 0)),
                first_paid_at=row.first_paid_at,
                last_paid_at=row.last_paid_at,
                unfinished_attempts=0,
            )
            for row in found
        ]

        return {
            "payment_customers": customers,
            "payments_page": page,
            "payments_has_next": has_next,
            "payments_has_previous": page > 1,
            "payments_query": query or "",
            "payments_totals": await self.overall_totals(),
        }

    async def overall_totals(self) -> dict[str, Any]:
        """The three numbers at the top of the page, counted in the database."""

        paid = (
            await self.session.execute(
                select(
                    func.count(BillingCheckoutAttempt.id),
                    # The SQL form of ``money_kept`` (see ``list_customers``): a partial
                    # refund lowers what staff is told was taken; fully refunded rows
                    # left this set with their status, they are not subtracted here.
                    func.coalesce(
                        func.sum(
                            BillingCheckoutAttempt.amount
                            - BillingCheckoutAttempt.refunded_amount
                        ),
                        0,
                    ),
                    func.count(func.distinct(BillingCheckoutAttempt.user_id)),
                ).where(
                    BillingCheckoutAttempt.status.in_(PAID_STATUSES),
                    BillingCheckoutAttempt.completed_at.is_not(None),
                )
            )
        ).one()
        pending_changes = await self.session.scalar(
            select(func.count(SubscriptionPlanChange.id)).where(
                SubscriptionPlanChange.status.in_(("requested", "scheduled"))
            )
        )
        failed_changes = await self.session.scalar(
            select(func.count(SubscriptionPlanChange.id)).where(
                SubscriptionPlanChange.status == "failed"
            )
        )
        return {
            "payments_made": int(paid[0] or 0),
            "money_taken": Decimal(str(paid[1] or 0)),
            "paying_customers": int(paid[2] or 0),
            "changes_waiting": int(pending_changes or 0),
            "changes_failed": int(failed_changes or 0),
        }

    # ── one customer ─────────────────────────────────────────────────────────────

    async def customer(self, user_id: UUID) -> CustomerPayments | None:
        """Everything about one customer's money, including the forms they sent."""

        user = await self.session.get(User, user_id)
        if user is None:
            return None

        identity = await self.session.scalar(
            select(UserIdentity)
            .where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider == IdentityProvider.EMAIL,
            )
            .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
            .limit(1)
        )
        subscription = await self.session.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status.in_(
                    [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
                ),
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        plan_code: str | None = None
        if subscription is not None:
            plan = await self.session.get(Plan, subscription.plan_id)
            plan_code = plan.code if plan else None

        payments = await self._payments(user_id)
        changes = await self._plan_changes(user_id)
        unfinished = await self.session.scalar(
            select(func.count(BillingCheckoutAttempt.id)).where(
                BillingCheckoutAttempt.user_id == user_id,
                # Unfinished means still open, not "not paid". The three words are the
                # billing service's own list (:data:`OPEN_ATTEMPT_STATUSES`), so a
                # refunded, cancelled, failed or expired attempt — an attempt that is
                # over — is never shown to staff as money still owed by the customer.
                BillingCheckoutAttempt.status.in_(OPEN_ATTEMPT_STATUSES),
            )
        )
        total = sum((row.amount for row in payments), start=Decimal("0"))

        return CustomerPayments(
            user_id=user_id,
            display_name=user.display_name or "Customer",
            email=(
                identity.display_identifier or identity.normalized_identifier
                if identity
                else "-"
            )
            or "-",
            current_plan_code=plan_code,
            current_plan_words=_plan_words(plan_code),
            subscription_status=(
                subscription.status.value if subscription is not None else None
            ),
            period_ends_at=subscription.current_period_end if subscription else None,
            payment_count=len(payments),
            total_paid=total,
            first_paid_at=payments[-1].paid_at if payments else None,
            last_paid_at=payments[0].paid_at if payments else None,
            unfinished_attempts=int(unfinished or 0),
            payments=payments,
            plan_changes=changes,
            timeline=await self._timeline(user_id, payments, changes),
            currencies=tuple(sorted({row.currency.upper() for row in payments})),
        )

    async def _payments(self, user_id: UUID) -> tuple[PaymentRow, ...]:
        rows = (
            await self.session.execute(
                select(BillingCheckoutAttempt, Plan.code)
                .outerjoin(Plan, Plan.id == BillingCheckoutAttempt.plan_id)
                .where(
                    BillingCheckoutAttempt.user_id == user_id,
                    BillingCheckoutAttempt.status.in_(PAID_STATUSES),
                    BillingCheckoutAttempt.completed_at.is_not(None),
                )
                .order_by(BillingCheckoutAttempt.completed_at.desc())
                .limit(TIMELINE_LIMIT)
            )
        ).all()
        return tuple(
            PaymentRow(
                # Guarded by the query above, but written out so a reader can see the
                # column is never None on a row that reaches here.
                paid_at=attempt.completed_at or attempt.created_at,
                plan_code=code or "",
                plan_words=_plan_words(code),
                # The money this payment still holds, not the figure first taken: the
                # staff reading a customer's history after a partial refund must see the
                # remainder, and ``money_kept`` is the owner of that reading.
                amount=money_kept(attempt.amount, attempt.refunded_amount),
                currency=attempt.currency,
                provider=attempt.provider,
                billing_cycle=attempt.billing_cycle,
                discount_code=attempt.discount_code,
                discount_percent=attempt.discount_percent,
                reference=attempt.provider_session_id,
            )
            for attempt, code in rows
        )

    async def _plan_changes(self, user_id: UUID) -> tuple[PlanChangeRow, ...]:
        rows = (
            await self.session.scalars(
                select(SubscriptionPlanChange)
                .where(SubscriptionPlanChange.user_id == user_id)
                .order_by(SubscriptionPlanChange.created_at.desc())
                .limit(TIMELINE_LIMIT)
            )
        ).all()
        return tuple(
            PlanChangeRow(
                requested_at=change.created_at,
                kind=change.kind,
                from_plan_words=_plan_words(change.from_plan_code),
                to_plan_words=_plan_words(change.to_plan_code) if change.to_plan_code else None,
                timing=change.timing,
                timing_words=_timing_words(change.timing),
                status=change.status,
                # The stored code turned back into the sentence the person chose from.
                # `plan_changes` owns that list, so the words here can never drift from
                # the words on the form.
                reason_words=reason_words(change.kind, change.reason_code),
                reason_text=change.reason_text,
                consent_text=change.consent_text,
                consented_at=change.consented_at,
                effective_at=change.effective_at,
                applied_at=change.applied_at,
                provider_error=change.provider_error,
            )
            for change in rows
        )

    async def _timeline(
        self,
        user_id: UUID,
        payments: Sequence[PaymentRow],
        changes: Sequence[PlanChangeRow],
    ) -> tuple[TimelineEntry, ...]:
        """Everything that happened to this customer's money, newest first.

        Payments, forms and what the payment company reported, merged into one list. Kept
        as one list on purpose: three separate tables side by side make it very hard to
        see that a customer cancelled *before* a renewal charge rather than after it.
        """

        entries: list[TimelineEntry] = []

        for payment in payments:
            discount = (
                f" with code {payment.discount_code}" if payment.discount_code else ""
            )
            paid = _money(payment.amount, payment.currency)
            entries.append(
                TimelineEntry(
                    happened_at=payment.paid_at,
                    kind="payment",
                    headline=f"Paid {paid} for {payment.plan_words}",
                    detail=f"Taken by {payment.provider}{discount}.",
                )
            )

        for change in changes:
            moving = (
                f" to {change.to_plan_words}"
                if change.to_plan_words and change.kind != "cancel"
                else ""
            )
            entries.append(
                TimelineEntry(
                    happened_at=change.requested_at,
                    kind=change.kind,
                    headline=(
                        f"{_KIND_WORDS.get(change.kind, change.kind.capitalize())}"
                        f"{moving}"
                    ),
                    detail=(
                        f"Reason: {change.reason_words}. "
                        f"{change.timing_words}. Now {change.status}."
                    ),
                )
            )
            if change.applied_at is not None:
                entries.append(
                    TimelineEntry(
                        happened_at=change.applied_at,
                        kind="applied",
                        headline=f"The change took effect ({change.from_plan_words}"
                        f"{' to ' + change.to_plan_words if change.to_plan_words else ''})",
                        detail="Their limits changed on this date.",
                    )
                )

        events = (
            await self.session.scalars(
                select(BillingEvent)
                .where(BillingEvent.user_id == user_id)
                .order_by(BillingEvent.created_at.desc())
                .limit(TIMELINE_LIMIT)
            )
        ).all()
        for event in events:
            entries.append(
                TimelineEntry(
                    happened_at=event.created_at,
                    kind="provider",
                    headline=f"{event.provider} reported {event.event_type}",
                    detail=(
                        f"Handled: {event.processing_status}."
                        + (f" Problem: {event.error_code}." if event.error_code else "")
                    ),
                )
            )

        entries.sort(key=lambda item: _as_aware(item.happened_at), reverse=True)
        return tuple(entries[:TIMELINE_LIMIT])


def _as_aware(value: datetime) -> datetime:
    """Sorting must not crash on a row stored without a timezone.

    SQLite gives back naive datetimes for columns PostgreSQL returns with a timezone, so
    a merged list of rows from different tables can hold both kinds. Comparing the two
    raises `TypeError`, which would turn one old row into a broken page.
    """

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
