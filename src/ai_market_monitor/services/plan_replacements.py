"""End one paid plan only after a different paid plan has been paid for."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Final
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES
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
_LIVE_STATUSES: Final[tuple[SubscriptionStatus, ...]] = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.TRIALING,
)


class PlanReplacementError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def manual_return_window_words() -> str:
    """The one customer-facing promise for the manual payment window."""

    return f"within {MANUAL_RETURN_HOURS} hours"


def money_owed_for_unused_time(
    *, paid_amount: Decimal, period_start: datetime, period_end: datetime, ended_at: datetime
) -> Decimal:
    """Return the unused share, in cents, without ever using binary floating point."""

    paid = max(paid_amount, Decimal("0")).quantize(MONEY_QUANTUM, ROUND_HALF_UP)
    total = _microseconds(period_end - period_start)
    unused = _microseconds(period_end - ended_at)
    if paid == 0 or total <= 0 or unused <= 0:
        return Decimal("0.00")
    share = Decimal(min(unused, total)) / Decimal(total)
    return min(paid, (paid * share).quantize(MONEY_QUANTUM, ROUND_HALF_UP))


def _microseconds(value: timedelta) -> int:
    return (
        value.days * 86_400_000_000
        + value.seconds * 1_000_000
        + value.microseconds
    )


class PaidPlanReplacementService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def source_for_checkout(
        self, *, user_id: UUID, target_plan_code: str
    ) -> tuple[Subscription, BillingCheckoutAttempt] | None:
        """Freeze the old paid period before a new payment page is opened."""

        rows = list(
            (
                await self.session.scalars(
                    select(Subscription)
                    .join(Plan, Plan.id == Subscription.plan_id)
                    .where(
                        Subscription.user_id == user_id,
                        Plan.code.in_(PURCHASABLE_PLAN_CODES),
                        Plan.code != target_plan_code,
                        Subscription.provider.notin_(("admin", "free", "trial")),
                        Subscription.status.in_(_LIVE_STATUSES),
                        (Subscription.current_period_end.is_(None))
                        | (Subscription.current_period_end > datetime.now(UTC)),
                    )
                    .order_by(Subscription.updated_at.desc())
                )
            ).all()
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise PlanReplacementError(
                "multiple_paid_plans_need_help",
                "More than one paid plan is still active. Nothing was charged. "
                "Please write to us so we can correct your billing first.",
            )
        old = rows[0]
        if old.current_period_start is None or old.current_period_end is None:
            raise PlanReplacementError(
                "paid_period_missing",
                "We cannot confirm the dates you already paid for. Nothing was charged. "
                "Please write to us and we will check them.",
            )
        source = await self.session.scalar(
            select(BillingCheckoutAttempt)
            .where(
                BillingCheckoutAttempt.user_id == user_id,
                BillingCheckoutAttempt.plan_id == old.plan_id,
                BillingCheckoutAttempt.provider == old.provider,
                BillingCheckoutAttempt.status == "completed",
                BillingCheckoutAttempt.completed_at.is_not(None),
            )
            .order_by(BillingCheckoutAttempt.completed_at.desc())
            .limit(1)
        )
        if source is None:
            raise PlanReplacementError(
                "paid_amount_missing",
                "We cannot confirm what you paid for your current plan. Nothing was "
                "charged. Please write to us and we will check it.",
            )
        return old, source

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
        source = await self.session.get(
            BillingCheckoutAttempt, attempt.replaces_checkout_attempt_id
        )
        if old is None or source is None or old.user_id != replacement.user_id:
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
        amount = money_owed_for_unused_time(
            paid_amount=source.amount,
            period_start=period_start,
            period_end=period_end,
            ended_at=moment,
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
            paid_amount=source.amount,
            amount_owed=amount,
            currency=source.currency.upper(),
            status="pending_manual",
        )
        self.session.add(owed)
        await self.session.flush()
        from ai_market_monitor.services.payment_emails import PaymentEmailOutboxService

        await PaymentEmailOutboxService(self.session, self.settings).enqueue_money_owed(
            billing_event=billing_event,
            money_owed=owed,
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
