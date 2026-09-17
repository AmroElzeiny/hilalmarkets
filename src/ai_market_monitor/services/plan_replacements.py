"""End one paid plan only after a different paid plan has been paid for."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Final
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.money import money_kept, quantise_half_up
from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES, plan_name
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    BillingEvent,
    Plan,
    PlanMoveMoneyOwed,
    Subscription,
)
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.services.entitlements import EntitlementService
from ai_market_monitor.services.provider_reliability import ProviderCallError
from ai_market_monitor.services.provider_runtime import provider_request

MANUAL_RETURN_HOURS: Final[int] = 48
MONEY_QUANTUM: Final[Decimal] = Decimal("0.01")
#: How long after a recurring subscription's period end it may still be the plan that is
#: replaced. A recurring card subscription stays ``ACTIVE`` at the payment company until
#: the renewal decision happens, so treating it as already ended in this window leaves
#: the old card charging next to a new plan.
_RECURRING_LAPSED_GRACE_WINDOW: Final[timedelta] = timedelta(days=30)
_LIVE_STATUSES: Final[tuple[SubscriptionStatus, ...]] = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.TRIALING,
)
_ENDED_STATUSES: Final[tuple[SubscriptionStatus, ...]] = (
    SubscriptionStatus.CANCELED,
    SubscriptionStatus.EXPIRED,
)
_RECURRING_PROVIDERS: Final[tuple[str, ...]] = ("creem", "stripe")

#: The checkout status that says money moved and is still moved: the only row a plan's
#: unused days may be valued from.
#:
#: One word, written once, because two readers ask this question and a different answer
#: from each is a double refund. :func:`payment_that_bought` uses it to search the live
#: payments, and ``apply_after_payment`` uses it to check the payment the move was frozen
#: onto — the row can change between those two moments, because a refund can arrive while
#: the customer is on the payment company's page. A refunded payment fails this test by
#: definition, and money the company already returned must not also be promised by us.
SETTLED_PAYMENT_STATUS: Final[str] = "completed"

#: The two words a :class:`~ai_market_monitor.db.models.commercial.PlanMoveMoneyOwed`
#: row answers with. ``pending_manual`` says a person still has to send the money by
#: hand; ``voided`` says the payment the record was valued from has since been
#: refunded — the company has already returned that money, so nothing more is owed and
#: nobody must send it. ``status`` is a free ``String(24)`` on the row, so a new word
#: is not a migration. The refund path in ``services/billing.py`` is the only writer
#: of ``voided`` and the only place that decision is made; a second place that could
#: void a payout is how one refund half-voids it and a person gets paid anyway.
MONEY_OWED_PENDING_STATUS: Final[str] = "pending_manual"
MONEY_OWED_VOID_STATUS: Final[str] = "voided"


#: The checkout route's refusals when the paid plan held now cannot be replaced safely,
#: keyed by the code it redirects with. Pages read the sentence from here — the dashboard
#: error notice included — so a code can never reach a person as its own name
#: ("Paid Amount Missing"), which is what the notice's fallback used to print.
REPLACEMENT_REFUSALS: Final[dict[str, str]] = {
    "multiple_paid_plans_need_help": (
        "More than one paid plan is still active. Nothing was charged. "
        "Please write to us so we can correct your billing first."
    ),
    "paid_period_missing": (
        "We cannot confirm the dates you already paid for. Nothing was charged. "
        "Please write to us and we will check them."
    ),
    "paid_amount_missing": (
        "We cannot confirm what you paid for your current plan. Nothing was "
        "charged. Please write to us and we will check it."
    ),
}


class PlanReplacementError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def manual_return_window_words() -> str:
    """The one customer-facing promise for the manual payment window."""

    return f"within {MANUAL_RETURN_HOURS} hours"


#: What somebody agrees to when the paid plan they hold now is replaced by a different
#: one. Shown inside the tick box of every checkout that would replace a paid plan — the
#: billing popup, the review page and the subscription popup — so the sentence a person
#: agrees to is the owner's rule of 2026-09-10 and not only "the price shown above".
#: One constant, because three hand-written copies of a money promise is three chances
#: for one of them to promise a different window.
#:
#: It says "the new plan price", not "the new plan price in full": a code entered on
#: the payment company's own page lowers what is really charged, and a sentence that
#: promised "in full" would promise a number the payment company can change. The owner's
#: rule is untouched: the new plan is paid today, the old one ends only after that
#: payment is confirmed, and the unused value comes back within 48 hours. (No apostrophe:
#: this string must appear in a page byte for byte, and the page escapes one.)
CONSENT_PLAN_REPLACEMENT: Final[str] = (
    "I understand I pay the new plan price today. My current paid plan ends "
    "only when this payment is confirmed. A person will send me the value of its unused "
    f"time {manual_return_window_words()}."
)


def money_owed_for_unused_time(
    *, paid_amount: Decimal, period_start: datetime, period_end: datetime, ended_at: datetime
) -> Decimal:
    """Return the unused share, in cents, without ever using binary floating point."""

    paid = quantise_half_up(max(paid_amount, Decimal("0")), MONEY_QUANTUM)
    total = _microseconds(period_end - period_start)
    unused = _microseconds(period_end - ended_at)
    if paid == 0 or total <= 0 or unused <= 0:
        return Decimal("0.00")
    share = Decimal(min(unused, total)) / Decimal(total)
    return min(paid, quantise_half_up(paid * share, MONEY_QUANTUM))


def _microseconds(value: timedelta) -> int:
    return (
        value.days * 86_400_000_000
        + value.seconds * 1_000_000
        + value.microseconds
    )


async def payment_that_bought(
    session: AsyncSession, subscription: Subscription
) -> BillingCheckoutAttempt | None:
    """The completed payment that bought this paid plan, or ``None``.

    Matched on the person, the plan and the payment company, newest first. One owner for
    two readers: the money owed for a plan's unused days is valued from this payment, and
    whether a card plan renews each month or each year is read from it.
    """

    return await session.scalar(
        select(BillingCheckoutAttempt)
        .where(
            BillingCheckoutAttempt.user_id == subscription.user_id,
            BillingCheckoutAttempt.plan_id == subscription.plan_id,
            BillingCheckoutAttempt.provider == subscription.provider,
            BillingCheckoutAttempt.status == SETTLED_PAYMENT_STATUS,
            BillingCheckoutAttempt.completed_at.is_not(None),
        )
        .order_by(BillingCheckoutAttempt.completed_at.desc())
        .limit(1)
    )


class PaidPlanReplacementService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def source_for_checkout(
        self, *, user_id: UUID, target_plan_code: str
    ) -> tuple[Subscription, BillingCheckoutAttempt] | None:
        """Freeze the old paid period before a new payment page is opened."""

        return await self._the_one_live_paid_plan(
            user_id=user_id, except_plan_code=target_plan_code
        )

    async def _the_one_live_paid_plan(
        self,
        *,
        user_id: UUID,
        except_plan_code: str = "",
        except_subscription_id: UUID | None = None,
    ) -> tuple[Subscription, BillingCheckoutAttempt] | None:
        """The paid plan this account holds now besides the one named, and what bought it.

        ``None`` when there is none. Two or more, a period with no dates, or a plan with
        no recorded payment are refused: each would make the money owed a guess.
        """

        now = datetime.now(UTC)
        grace_start = now - _RECURRING_LAPSED_GRACE_WINDOW
        query = (
            select(Subscription)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Subscription.user_id == user_id,
                Plan.code.in_(PURCHASABLE_PLAN_CODES),
                Plan.code != except_plan_code,
                Subscription.provider.notin_(("admin", "free", "trial")),
                Subscription.status.in_(_LIVE_STATUSES),
                (Subscription.current_period_end.is_(None))
                | (Subscription.current_period_end > now)
                | (
                    Subscription.provider.in_(_RECURRING_PROVIDERS)
                    & (Subscription.cancel_at_period_end.is_(False))
                    & (Subscription.current_period_end >= grace_start)
                    & (Subscription.current_period_end <= now)
                ),
            )
            .order_by(Subscription.updated_at.desc())
        )
        if except_subscription_id is not None:
            query = query.where(Subscription.id != except_subscription_id)
        rows = list((await self.session.scalars(query)).all())
        if not rows:
            return None
        if len(rows) != 1:
            raise PlanReplacementError(
                "multiple_paid_plans_need_help",
                REPLACEMENT_REFUSALS["multiple_paid_plans_need_help"],
            )
        old = rows[0]
        if old.current_period_start is None or old.current_period_end is None:
            raise PlanReplacementError(
                "paid_period_missing", REPLACEMENT_REFUSALS["paid_period_missing"]
            )
        source = await payment_that_bought(self.session, old)
        if source is None:
            raise PlanReplacementError(
                "paid_amount_missing", REPLACEMENT_REFUSALS["paid_amount_missing"]
            )
        return old, source

    async def replacement_refusal(self, *, user_id: UUID) -> str:
        """Why a checkout replacing the paid plan held now would be refused, or ``""``.

        The same checks `source_for_checkout` runs when Pay is pressed, asked before a page
        is drawn. A page that offers Pay to somebody the server then refuses with
        `paid_amount_missing` is a page offering what the server will not do, so every
        surface that draws a Pay button reads this first and shows the same sentence.

        The plan being bought does not change the answer. It only leaves that plan out of
        the search, and a plan the person already holds is refused before this matters.
        """

        try:
            await self.source_for_checkout(user_id=user_id, target_plan_code="")
        except PlanReplacementError as exc:
            return str(exc)
        return ""

    async def attach_source(
        self, *, attempt: BillingCheckoutAttempt, target_plan_code: str
    ) -> None:
        """Attach a newly appeared old plan before an existing payment link is used."""

        if attempt.replaces_subscription_id is not None:
            return
        source = await self.source_for_checkout(
            user_id=attempt.user_id, target_plan_code=target_plan_code
        )
        if source is None:
            return
        attempt.replaces_subscription_id = source[0].id
        attempt.replaces_checkout_attempt_id = source[1].id
        await self.session.flush()

    async def apply_after_payment(
        self,
        *,
        checkout_attempt_id: UUID,
        replacement: Subscription,
        billing_event: BillingEvent,
        now: datetime | None = None,
    ) -> PlanMoveMoneyOwed | None:
        """End the old plan and write the manual-payment record after confirmed payment."""

        attempt = await self.session.scalar(
            select(BillingCheckoutAttempt)
            .where(BillingCheckoutAttempt.id == checkout_attempt_id)
            .with_for_update()
        )
        if attempt is None or attempt.replaces_subscription_id is None:
            return None
        old = await self.session.scalar(
            select(Subscription)
            .where(Subscription.id == attempt.replaces_subscription_id)
            .with_for_update()
        )
        # The frozen payment is read under a row lock, for the same reason the old plan
        # above is: its ``status`` is the single fact that decides whether a payout row
        # is written, and the refund writer in ``services/billing.py`` can commit at any
        # moment between an unlocked read and this transaction's own commit. The lock
        # serialises the two: a refund that lands first is seen here and the guard below
        # writes nothing; a move that lands first is seen by that refund's void
        # (``_void_manual_payout_for_refund``), which reads the row only after its own
        # locked status write. ``populate_existing`` makes the attributes carry the
        # post-lock truth rather than anything an earlier read of the same row left in
        # the session.
        source = await self.session.scalar(
            select(BillingCheckoutAttempt)
            .where(BillingCheckoutAttempt.id == attempt.replaces_checkout_attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if old is None or source is None or old.user_id != replacement.user_id:
            raise PlanReplacementError(
                "old_paid_plan_missing",
                "The old paid plan could not be checked after the new payment.",
            )
        if old.status in _ENDED_STATUSES:
            # The plan frozen onto this checkout is already over. Two different things
            # arrive here, and neither may end that plan a second time.
            #
            # * A renewal of the new plan. Creem keeps the checkout's metadata on the
            #   subscription, so every monthly `subscription.paid` names this same
            #   checkout. The key below cannot recognise it: the first run moved
            #   `current_period_end` to the moment of the move, so a later run builds a
            #   different key. Without this branch each renewal asked Creem to cancel an
            #   already-cancelled subscription, and a refusal rolled the renewal back —
            #   the customer paid for the new plan and it was not extended.
            # * A second payment page opened before the first was paid — the same plan
            #   by card and by crypto, or monthly and annual — both frozen onto the same
            #   old plan. The first payment ended it; this one must end whatever that
            #   first payment bought, or two paid plans stay live and both keep charging.
            #
            # So: the plan to end now is any paid plan still live besides the one just
            # paid for. A renewal normally finds none and changes nothing.
            earlier = await self.session.scalar(
                select(PlanMoveMoneyOwed)
                .where(
                    PlanMoveMoneyOwed.ended_subscription_id == old.id,
                    PlanMoveMoneyOwed.replacement_subscription_id == replacement.id,
                )
                .limit(1)
            )
            if earlier is not None:
                return earlier
            still_live = await self._the_one_live_paid_plan(
                user_id=replacement.user_id, except_subscription_id=replacement.id
            )
            if still_live is None:
                return None
            old = await self.session.scalar(
                select(Subscription)
                .where(Subscription.id == still_live[0].id)
                .with_for_update()
            )
            # The same lock the frozen link above takes, on the payment this branch
            # would value the payout from. ``payment_that_bought`` selects a
            # ``completed`` row without one, and a refund can commit in that gap;
            # the status guard below asks the same question of this row whichever
            # branch filled it, so both reads must come from behind the lock.
            source = await self.session.scalar(
                select(BillingCheckoutAttempt)
                .where(BillingCheckoutAttempt.id == still_live[1].id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if old is None or source is None:
                raise PlanReplacementError(
                    "old_paid_plan_missing",
                    "The old paid plan could not be checked after the new payment.",
                )
        period_start = _aware(old.current_period_start)
        period_end = _aware(old.current_period_end)
        if period_start is None or period_end is None:
            raise PlanReplacementError(
                "paid_period_missing",
                "The paid period could not be checked after the new payment.",
            )
        moment = _aware(now) or datetime.now(UTC)
        key = sha256(
            f"plan-move:{old.id}:{period_start.isoformat()}:{period_end.isoformat()}".encode()
        ).hexdigest()
        existing = await self.session.scalar(
            select(PlanMoveMoneyOwed).where(PlanMoveMoneyOwed.idempotency_key == key)
        )
        if existing is not None:
            return existing

        await self._cancel_old_recurring_plan(old)
        old.status = SubscriptionStatus.CANCELED
        old.cancel_at_period_end = False
        old.canceled_at = moment
        old.current_period_end = moment

        old_plan = await self.session.get(Plan, old.plan_id)
        new_plan = await self.session.get(Plan, replacement.plan_id)
        if old_plan is None or new_plan is None:
            raise PlanReplacementError(
                "replacement_plan_missing", "A paid plan record disappeared during the move."
            )
        # What the payment still holds: the whole amount when nothing came back, the
        # remainder after a partial refund, and this is the one place that figure is
        # derived — ``money_kept`` is the owner, not a subtraction beside it.
        source_kept = money_kept(source.amount, source.refunded_amount)
        amount = (
            # The frozen link above names the payment that bought the old plan, taken at
            # the moment the new payment page was opened. A refund can land in between:
            # the payment company has already given that money back, so the unused days
            # are worth nothing more — writing a record here would promise the same money
            # a second time, and the customer would be paid twice for one period. The old
            # plan still ends, because it has really been replaced by the new one.
            #
            # ``payment_that_bought`` excludes a refunded payment the same way; this is
            # the same rule read off the frozen row instead of the live search, because
            # the frozen row is what this move was priced from. A *partial* refund keeps
            # the row ``completed`` — the guard cannot see it — and the valuation reads
            # the money kept instead of the money taken, so the customer is paid back
            # only for unused time the company did not already return.
            Decimal("0.00")
            if source.status != SETTLED_PAYMENT_STATUS
            else money_owed_for_unused_time(
                paid_amount=source_kept,
                period_start=period_start,
                period_end=period_end,
                ended_at=moment,
            )
        )
        if amount <= 0:
            await EntitlementService(self.session).snapshot(replacement.user_id)
            await self.session.flush()
            return None
        owed = PlanMoveMoneyOwed(
            user_id=replacement.user_id,
            ended_subscription_id=old.id,
            replacement_subscription_id=replacement.id,
            source_checkout_attempt_id=source.id,
            billing_event_id=billing_event.id,
            idempotency_key=key,
            from_plan_code=old_plan.code,
            to_plan_code=new_plan.code,
            period_start=period_start,
            original_period_end=period_end,
            ended_at=moment,
            due_at=moment + timedelta(hours=MANUAL_RETURN_HOURS),
            # The figure the payout was valued from — the money kept, not the money
            # taken — so the row's own check ``amount_owed <= paid_amount`` stays true
            # after a partial refund shrinks what the payment holds.
            paid_amount=source_kept,
            amount_owed=amount,
            currency=source.currency.upper(),
            status=MONEY_OWED_PENDING_STATUS,
        )
        self.session.add(owed)
        await self.session.flush()
        from ai_market_monitor.observability.issues import OperationalIssueService
        from ai_market_monitor.services.payment_emails import PaymentEmailOutboxService

        await PaymentEmailOutboxService(self.session, self.settings).enqueue_money_owed(
            billing_event=billing_event,
            money_owed=owed,
        )
        # The customer's email promises that a person sends this money within
        # MANUAL_RETURN_HOURS. That email goes to the customer alone, so without this row
        # nobody on our side is told that anything is owed, and the promise rests on
        # somebody happening to look. Written in the same transaction as the record: a
        # move that rolls back leaves neither behind.
        await OperationalIssueService(self.session).record_occurrence(
            dedupe_key=f"billing:money-owed:{owed.id}",
            category="billing",
            # A task with a deadline, not an outage: the queue's "ticket", not "page".
            severity="ticket",
            summary=(
                f"A customer is owed {owed.amount_owed} {owed.currency} for unused time on "
                f"{plan_name(owed.from_plan_code)} after moving to "
                f"{plan_name(owed.to_plan_code)}. Send it by hand before "
                f"{owed.due_at:%Y-%m-%d %H:%M} UTC."
            ),
            affected_scope="billing.plan_replacement",
            evidence_refs=(f"plan_move_money_owed:{owed.id}",),
            source="billing_webhook",
        )
        await EntitlementService(self.session).snapshot(replacement.user_id)
        await EntitlementService(self.session).pause_excess_after_downgrade(
            replacement.user_id
        )
        await self.session.flush()
        return owed

    async def _cancel_old_recurring_plan(self, subscription: Subscription) -> None:
        provider = subscription.provider or ""
        if provider not in {"creem", "stripe"}:
            return
        reference = subscription.provider_subscription_id
        if not reference:
            raise PlanReplacementError(
                "old_subscription_cancel_failed",
                "The old recurring plan has no payment reference.",
            )
        try:
            if provider == "creem":
                secret = self.settings.creem_api_key
                if secret is None:
                    raise PlanReplacementError(
                        "old_subscription_cancel_failed",
                        "Card cancellation is not configured.",
                    )
                response = await provider_request(
                    self.settings,
                    "POST",
                    f"{str(self.settings.creem_api_base).rstrip('/')}/v1/subscriptions/{reference}/cancel",
                    provider="creem",
                    operation="cancel_replaced_subscription",
                    timeout=self.settings.creem_timeout_seconds,
                    retry=True,
                    mutation_committed=False,
                    raise_for_failure=True,
                    unwrap_transport_errors=False,
                    headers={
                        "x-api-key": secret.get_secret_value(),
                        "Content-Type": "application/json",
                        "User-Agent": "HilalMarkets/1.0",
                    },
                    json={"mode": "immediate", "onExecute": "cancel"},
                )
            else:
                secret = self.settings.stripe_secret_key
                if secret is None:
                    raise PlanReplacementError(
                        "old_subscription_cancel_failed",
                        "Card cancellation is not configured.",
                    )
                response = await provider_request(
                    self.settings,
                    "DELETE",
                    f"{str(self.settings.stripe_api_base).rstrip('/')}/v1/subscriptions/{reference}",
                    provider="stripe",
                    operation="cancel_replaced_subscription",
                    retry=True,
                    mutation_committed=False,
                    raise_for_failure=True,
                    unwrap_transport_errors=False,
                    headers={"Authorization": f"Bearer {secret.get_secret_value()}"},
                )
        except (httpx.RequestError, ProviderCallError) as exc:
            raise PlanReplacementError(
                "old_subscription_cancel_failed",
                "The old plan could not be stopped after repeated attempts.",
            ) from exc
        if response.is_error:
            raise PlanReplacementError(
                "old_subscription_cancel_failed",
                "The old plan could not be stopped after repeated attempts.",
            )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)
