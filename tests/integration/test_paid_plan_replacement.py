from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES, effective_monthly_price
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    BillingEvent,
    OperationalIssue,
    PaymentEmailDelivery,
    PlanMoveMoneyOwed,
    Subscription,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.services import plan_replacements as replacement_module
from ai_market_monitor.services.billing import BillingError, BillingService
from ai_market_monitor.services.entitlements import PlanCatalogService
from ai_market_monitor.services.plan_replacements import MANUAL_RETURN_HOURS
from tests.integration.test_checkout_and_payment_email import _signup
from tests.support.billing_config import live_billing_overrides

PLAN_PAIRS = tuple(
    (held, target)
    for held in PURCHASABLE_PLAN_CODES
    for target in PURCHASABLE_PLAN_CODES
    if held != target
)


async def _find_user(test_context, email: str) -> User:
    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == email)
        )
        assert user is not None
        return user


async def _seed_old_payment(
    test_context, *, email: str, plan_code: str, provider: str = "creem"
) -> tuple[User, Subscription, BillingCheckoutAttempt]:
    user = await _find_user(test_context, email)
    async with test_context["session_factory"]() as session:
        plan = await PlanCatalogService(session).get_or_sync(plan_code)
        now = datetime.now(UTC)
        source = BillingCheckoutAttempt(
            user_id=user.id,
            plan_id=plan.id,
            billing_cycle="monthly_auto_renewal",
            provider=provider,
            status="completed",
            idempotency_key=f"source-{user.id}-{plan_code}",
            terms_version="test",
            amount=effective_monthly_price(plan_code),
            currency="USD",
            terms_accepted_at=now - timedelta(days=15),
            expires_at=now,
            completed_at=now - timedelta(days=15),
            billing_profile={"first_name": "Amina"},
        )
        old = Subscription(
            user_id=user.id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            provider=provider,
            provider_subscription_id=f"old-{provider}-{user.id}",
            current_period_start=now - timedelta(days=15),
            current_period_end=now + timedelta(days=15),
        )
        session.add_all([source, old])
        await session.commit()
        return user, old, source


async def _prepare_new_checkout(
    test_context, *, user: User, target_code: str, settings
) -> BillingCheckoutAttempt:
    async with test_context["session_factory"]() as session:
        prepared = await BillingService(session, settings, provider_name="creem").prepare_checkout(
            user_id=user.id,
            plan_code=target_code,
            billing_cycle="monthly",
            request_key=f"move-{user.id}-{target_code}",
            terms_accepted=True,
            billing_profile={"first_name": "Amina"},
        )
        await session.commit()
        return prepared.attempt


def _paid_event(
    *, attempt: BillingCheckoutAttempt, target_code: str, event_id: str
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": event_id,
        "type": "subscription.paid",
        "data": {
            "checkout_attempt_id": str(attempt.id),
            "plan_code": target_code,
            "provider_subscription_id": f"new-creem-{attempt.user_id}",
            "amount": str(attempt.amount),
            "currency": attempt.currency,
            "status": "active",
            "current_period_start": now.isoformat(),
            "current_period_end": (now + timedelta(days=30)).isoformat(),
        },
    }


@pytest.mark.parametrize(("held_code", "target_code"), PLAN_PAIRS)
async def test_confirmed_card_move_leaves_one_charge_and_one_money_record(
    test_context, monkeypatch, held_code: str, target_code: str
) -> None:
    email = f"paid-move-{held_code}-{target_code}@example.com"
    await _signup(test_context, email)
    user, old, source = await _seed_old_payment(
        test_context, email=email, plan_code=held_code
    )
    enabled = test_context["settings"].model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    attempt = await _prepare_new_checkout(
        test_context, user=user, target_code=target_code, settings=enabled
    )
    assert attempt.amount == effective_monthly_price(target_code)
    assert attempt.replaces_subscription_id == old.id
    assert attempt.replaces_checkout_attempt_id == source.id

    cancel_calls: list[dict[str, object]] = []

    async def successful_cancel(*args, **kwargs):
        cancel_calls.append(kwargs)
        return httpx.Response(200, request=httpx.Request("POST", str(args[2])))

    monkeypatch.setattr(replacement_module, "provider_request", successful_cancel)
    payload = _paid_event(
        attempt=attempt,
        target_code=target_code,
        event_id=f"move-paid-{held_code}-{target_code}",
    )
    async with test_context["session_factory"]() as session:
        service = BillingService(session, enabled, provider_name="creem")
        first = await service.process_event(provider="creem", payload=payload)
        await session.commit()
        replay = await service.process_event(provider="creem", payload=payload)
        await session.commit()
        assert first.replayed is False
        assert replay.replayed is True

    async with test_context["session_factory"]() as session:
        subscriptions = list(
            (
                await session.scalars(
                    select(Subscription).where(Subscription.user_id == user.id)
                )
            ).all()
        )
        live = [row for row in subscriptions if row.status == SubscriptionStatus.ACTIVE]
        recurring = [
            row for row in live if (row.provider or "") in {"creem", "stripe"}
        ]
        owed = list(
            (
                await session.scalars(
                    select(PlanMoveMoneyOwed).where(
                        PlanMoveMoneyOwed.user_id == user.id
                    )
                )
            ).all()
        )
        emails = list(
            (
                await session.scalars(
                    select(PaymentEmailDelivery).where(
                        PaymentEmailDelivery.user_id == user.id,
                        PaymentEmailDelivery.purpose == "plan_move_money_owed",
                    )
                )
            ).all()
        )
        assert len(live) == 1
        assert len(recurring) == 1
        assert live[0].plan_id != old.plan_id
        assert len(cancel_calls) == 1
        assert cancel_calls[0]["retry"] is True
        assert cancel_calls[0]["mutation_committed"] is False
        assert len(owed) == 1
        assert owed[0].paid_amount == source.amount
        assert 0 < owed[0].amount_owed <= source.amount
        assert owed[0].due_at - owed[0].ended_at == timedelta(
            hours=MANUAL_RETURN_HOURS
        )
        assert len(emails) == 1
        assert emails[0].amount == owed[0].amount_owed


@pytest.mark.parametrize(("held_code", "target_code"), PLAN_PAIRS)
async def test_abandoned_checkout_leaves_the_old_plan_untouched(
    test_context, held_code: str, target_code: str
) -> None:
    email = f"abandoned-move-{held_code}-{target_code}@example.com"
    await _signup(test_context, email)
    user, old, _source = await _seed_old_payment(
        test_context, email=email, plan_code=held_code
    )
    enabled = test_context["settings"].model_copy(update=live_billing_overrides())
    await _prepare_new_checkout(
        test_context, user=user, target_code=target_code, settings=enabled
    )
    async with test_context["session_factory"]() as session:
        unchanged = await session.get(Subscription, old.id)
        assert unchanged is not None
        assert unchanged.status == SubscriptionStatus.ACTIVE
        assert unchanged.canceled_at is None
        assert unchanged.current_period_end is not None
        assert await session.scalar(select(func.count(PlanMoveMoneyOwed.id))) == 0


async def test_failed_card_cancellation_is_visible_and_reprocessed(
    test_context, monkeypatch
) -> None:
    email = "cancel-failure@example.com"
    await _signup(test_context, email)
    user, old, _source = await _seed_old_payment(
        test_context, email=email, plan_code="trader"
    )
    enabled = test_context["settings"].model_copy(update=live_billing_overrides())
    attempt = await _prepare_new_checkout(
        test_context, user=user, target_code="pro", settings=enabled
    )
    payload = _paid_event(
        attempt=attempt, target_code="pro", event_id="move-cancel-failed"
    )

    async def failed_cancel(*args, **kwargs):
        raise httpx.ConnectError("offline", request=httpx.Request("POST", str(args[2])))

    monkeypatch.setattr(replacement_module, "provider_request", failed_cancel)
    async with test_context["session_factory"]() as session:
        with pytest.raises(BillingError) as error:
            await BillingService(session, enabled, provider_name="creem").process_event(
                provider="creem", payload=payload
            )
        assert error.value.code == "old_subscription_cancel_failed"

    async with test_context["session_factory"]() as session:
        unchanged = await session.get(Subscription, old.id)
        event = await session.scalar(
            select(BillingEvent).where(
                BillingEvent.provider_event_id == "move-cancel-failed"
            )
        )
        assert unchanged is not None
        assert unchanged.status == SubscriptionStatus.ACTIVE
        assert unchanged.canceled_at is None
        assert event is not None
        assert event.processing_status == "failed"
        assert event.error_code == "old_subscription_cancel_failed"
        assert await session.scalar(select(func.count(OperationalIssue.id))) == 1
        assert await session.scalar(select(func.count(PlanMoveMoneyOwed.id))) == 0

    async def successful_cancel(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", str(args[2])))

    monkeypatch.setattr(replacement_module, "provider_request", successful_cancel)
    async with test_context["session_factory"]() as session:
        result = await BillingService(
            session, enabled, provider_name="creem"
        ).process_event(provider="creem", payload=payload)
        await session.commit()
        assert result.processing_status == "processed"
    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(PlanMoveMoneyOwed.id))) == 1
        live_count = await session.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.user_id == user.id,
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
        )
        assert live_count == 1


@pytest.mark.parametrize(("held_code", "target_code"), PLAN_PAIRS)
async def test_failed_payment_leaves_the_old_plan_untouched(
    test_context, held_code: str, target_code: str
) -> None:
    email = f"failed-move-{held_code}-{target_code}@example.com"
    await _signup(test_context, email)
    user, old, _source = await _seed_old_payment(
        test_context, email=email, plan_code=held_code
    )
    enabled = test_context["settings"].model_copy(update=live_billing_overrides())
    attempt = await _prepare_new_checkout(
        test_context, user=user, target_code=target_code, settings=enabled
    )
    now = datetime.now(UTC)
    payload = {
        "id": f"move-failed-{held_code}-{target_code}",
        "type": "invoice.payment_failed",
        "data": {
            "checkout_attempt_id": str(attempt.id),
            "plan_code": target_code,
            "provider_subscription_id": f"failed-creem-{user.id}",
            "status": "past_due",
            "current_period_start": now.isoformat(),
            "current_period_end": (now + timedelta(days=30)).isoformat(),
        },
    }
    async with test_context["session_factory"]() as session:
        await BillingService(session, enabled, provider_name="creem").process_event(
            provider="creem", payload=payload
        )
        await session.commit()
    async with test_context["session_factory"]() as session:
        unchanged = await session.get(Subscription, old.id)
        failed_attempt = await session.get(BillingCheckoutAttempt, attempt.id)
        assert unchanged is not None
        assert unchanged.status == SubscriptionStatus.ACTIVE
        assert unchanged.canceled_at is None
        assert failed_attempt is not None
        assert failed_attempt.status == "payment_failed"
        assert await session.scalar(select(func.count(PlanMoveMoneyOwed.id))) == 0
