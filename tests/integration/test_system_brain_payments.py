"""The payments record in the System Brain.

The question this page answers is "what did this customer pay, and what did they ask to
change?", and the rule that makes the answer trustworthy is that **every figure shown is
the figure that was stored**. Plus moved from $15 to $9 on 8 September 2026; a page that
worked a price out again from today's plan table would have quietly restated every older
payment as if it had always been $9.

So the tests below store a payment at an old price, then change nothing and check the page
still says the old price.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ai_market_monitor.core.security import hash_password
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    BillingEvent,
    Subscription,
    SubscriptionPlanChange,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    IdentityProvider,
    SubscriptionStatus,
    UserRole,
)
from ai_market_monitor.services.entitlements import PlanCatalogService
from ai_market_monitor.services.plan_changes import CONSENT_CANCEL

#: The sentence a customer ticked when they booked a downgrade before 2026-09-10. Written
#: out, not imported: the switch form and its sentences are gone, and a stored consent
#: keeps the words that were on the screen that day whatever the product says now. That
#: is exactly what the page is checked for below.
BOOKED_DOWNGRADE_CONSENT = (
    "I understand my plan moves to the smaller plan at the end of the period I have "
    "already paid for, and the smaller price starts on my renewal day."
)

#: What Plus cost before the launch offer. Deliberately not read from `core.plans`: the
#: whole point is that a stored payment keeps its own number when the price list moves on.
OLD_PLUS_PRICE = Decimal("15.00")


async def _account(test_context, *, email: str, role: UserRole = UserRole.USER) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name=email.split("@", 1)[0].title(), role=role)
        session.add(user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject=email,
                normalized_identifier=email,
                display_identifier=email,
                password_hash=hash_password("Valid1!"),
                is_verified=True,
                is_primary=True,
                verified_at=datetime.now(UTC),
                profile_data={},
            )
        )
        await session.commit()
        return user


async def _paying_customer(test_context, *, email: str) -> User:
    """Somebody who paid twice, then asked to move down a plan, then cancelled."""

    customer = await _account(test_context, email=email)
    async with test_context["session_factory"]() as session:
        plan = await PlanCatalogService(session).get_or_sync("trader")
        pro = await PlanCatalogService(session).get_or_sync("pro")
        now = datetime.now(UTC)
        subscription = Subscription(
            user_id=customer.id,
            plan_id=pro.id,
            status=SubscriptionStatus.ACTIVE,
            provider="creem",
            provider_customer_id=f"cus_{customer.id}",
            provider_subscription_id=f"sub_{customer.id}",
            current_period_start=now - timedelta(days=3),
            current_period_end=now + timedelta(days=27),
        )
        session.add(subscription)
        await session.flush()

        for index, (amount, when) in enumerate(
            (
                (OLD_PLUS_PRICE, now - timedelta(days=63)),
                (OLD_PLUS_PRICE, now - timedelta(days=33)),
            )
        ):
            session.add(
                BillingCheckoutAttempt(
                    user_id=customer.id,
                    plan_id=plan.id,
                    billing_cycle="one_time_30_day",
                    provider="creem",
                    provider_session_id=f"ch_{customer.id}_{index}",
                    status="completed",
                    idempotency_key=f"payments-{customer.id}-{index}",
                    terms_version="2026-01",
                    amount=amount,
                    currency="USD",
                    discount_code="TINYTALES" if index else None,
                    discount_percent=Decimal("30.00") if index else None,
                    terms_accepted_at=when,
                    expires_at=when + timedelta(hours=1),
                    completed_at=when,
                    billing_profile={},
                )
            )
        # An attempt that never became money. It must be counted apart from the payments.
        session.add(
            BillingCheckoutAttempt(
                user_id=customer.id,
                plan_id=plan.id,
                billing_cycle="one_time_30_day",
                provider="creem",
                status="expired",
                idempotency_key=f"payments-abandoned-{customer.id}",
                terms_version="2026-01",
                amount=OLD_PLUS_PRICE,
                currency="USD",
                terms_accepted_at=now - timedelta(days=20),
                expires_at=now - timedelta(days=20),
                billing_profile={},
            )
        )
        session.add(
            BillingEvent(
                user_id=customer.id,
                provider="creem",
                provider_event_id=f"evt_renewal_{customer.id}",
                event_type="subscription.paid",
                processing_status="processed",
                payload_redacted={},
                created_at=now - timedelta(days=33),
            )
        )
        session.add(
            SubscriptionPlanChange(
                user_id=customer.id,
                subscription_id=subscription.id,
                kind="downgrade",
                from_plan_code="pro",
                to_plan_code="trader",
                timing="period_end",
                status="scheduled",
                reason_code="too_expensive",
                reason_text=None,
                consent_text=BOOKED_DOWNGRADE_CONSENT,
                consented_at=now - timedelta(days=2),
                effective_at=now + timedelta(days=27),
                provider="creem",
                metadata_json={},
            )
        )
        session.add(
            SubscriptionPlanChange(
                user_id=customer.id,
                subscription_id=subscription.id,
                kind="cancel",
                from_plan_code="trader",
                to_plan_code=None,
                timing="period_end",
                status="failed",
                reason_code="other",
                reason_text="I am taking a break from the markets",
                consent_text=CONSENT_CANCEL,
                consented_at=now - timedelta(days=1),
                effective_at=now + timedelta(days=27),
                provider="creem",
                provider_error="Creem said the subscription was already cancelled",
                metadata_json={},
            )
        )
        await session.commit()
    return customer


async def _page(test_context, admin: User, query: str = ""):
    response = await test_context["client"].get(
        f"/dashboard/system-brain/payments{query}",
        headers={"X-User-ID": str(admin.id)},
    )
    assert response.status_code == 200, response.text[:400]
    return response.text


async def test_the_payments_page_lists_who_paid_and_how_much(test_context):
    admin = await _account(
        test_context,
        email="payments-admin@hilalmarkets.test",
        role=UserRole.ADMIN,
    )
    customer = await _paying_customer(test_context, email="payer@example.com")

    page = await _page(test_context, admin)

    assert 'href="/dashboard/system-brain/payments"' in page
    assert 'data-testid="payments-table"' in page
    assert "payer@example.com" in page
    # Two payments of $15, so $30 taken - not two times today's $9 price.
    assert ">2<" in page
    assert "$30.00" in page
    assert f"customer={customer.id}" in page


async def test_one_customer_shows_payments_forms_and_a_timelog(test_context):
    admin = await _account(
        test_context,
        email="payments-detail@hilalmarkets.test",
        role=UserRole.ADMIN,
    )
    customer = await _paying_customer(test_context, email="detail@example.com")

    page = await _page(test_context, admin, f"?customer={customer.id}")

    assert 'data-testid="payment-customer"' in page
    assert 'data-testid="payment-history"' in page
    assert 'data-testid="plan-change-form"' in page
    assert 'data-testid="payment-timelog"' in page

    # The stored price, not the current one.
    assert "15.00 USD" in page
    assert "TINYTALES" in page
    assert "30% off" in page

    # The forms, with the reason in words and the sentence they ticked word for word.
    assert "Pro is too expensive for me" in page
    assert "I am taking a break from the markets" in page
    assert BOOKED_DOWNGRADE_CONSENT in page
    assert CONSENT_CANCEL in page
    assert "Creem said the subscription was already cancelled" in page

    # The plans are named, never shown as internal codes.
    assert "Plus" in page
    assert "Pro" in page
    assert ">trader<" not in page
    assert ">demo<" not in page

    # The attempt that never became money is counted, and counted separately.
    unfinished = page.split("Unfinished attempts", 1)[1].split("</div>", 1)[0]
    assert ">1<" in unfinished


async def test_a_customer_who_is_not_an_admin_cannot_open_the_payments_page(test_context):
    customer = await _account(test_context, email="nosy@example.com")

    response = await test_context["client"].get(
        "/dashboard/system-brain/payments",
        headers={"X-User-ID": str(customer.id)},
    )

    assert response.status_code == 403


async def test_searching_finds_a_customer_by_email(test_context):
    admin = await _account(
        test_context,
        email="payments-search@hilalmarkets.test",
        role=UserRole.ADMIN,
    )
    await _paying_customer(test_context, email="findme@example.com")
    await _paying_customer(test_context, email="hideme@example.com")

    found = await _page(test_context, admin, "?q=findme")

    assert "findme@example.com" in found
    assert "hideme@example.com" not in found


async def test_the_page_never_recalculates_a_price_from_todays_plan_table(test_context):
    """The rule, stated as a rule.

    Nothing on this page may quote the current price of a plan. Every number comes from
    a stored row, so the only prices that may appear are the ones that were charged.
    """

    admin = await _account(
        test_context,
        email="payments-frozen@hilalmarkets.test",
        role=UserRole.ADMIN,
    )
    customer = await _paying_customer(test_context, email="frozen@example.com")

    page = await _page(test_context, admin, f"?customer={customer.id}")
    history = page.split('data-testid="payment-history"', 1)[1].split("</table>", 1)[0]

    amounts = set(re.findall(r"(\d+\.\d{2}) USD", history))
    assert amounts == {f"{OLD_PLUS_PRICE:.2f}"}, amounts
