"""End-to-end plan change journeys.

A customer on a paid plan must be able to move up or down through the billing
page. The route, the service, and the page must agree on what is allowed, when
it takes effect, and what the payment company is asked to do. These tests drive
the real route with the real service and fake only the outbound Creem call.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.db.models import (
    Subscription,
    SubscriptionPlanChange,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, SubscriptionStatus
from ai_market_monitor.services import plan_changes as plan_changes_module
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService
from ai_market_monitor.services.plan_changes import (
    TIMING_IMMEDIATE,
    TIMING_PERIOD_END,
    PlanChangeService,
)
from tests.support.billing_config import live_billing_overrides


async def _signup_and_verify(client, settings, email: str) -> None:
    """Create a verified email account and keep the session cookie in client."""

    requested = await client.post(
        "/signup/password",
        data={
            "email": email,
            "display_name": "Amina Trader",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert requested.status_code == 303
    code = settings.email_test_outbox[-1]["code"]
    verified = await client.post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def _user_id_for_email(session_factory, email: str) -> UUID:
    """Find the user id behind a verified email identity."""

    async with session_factory() as session:
        user_id = await session.scalar(
            select(UserIdentity.user_id).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == email,
            )
        )
    assert user_id is not None, f"No user found for {email}"
    return user_id


async def _grant_paid_plan(
    session_factory,
    user_id: UUID,
    plan_code: str,
    *,
    provider: str = "creem",
    period_days: int = 30,
) -> Subscription:
    """Give the account an active paid subscription, Creem by default."""

    async with session_factory() as session:
        plan = await PlanCatalogService(session).get_or_sync(plan_code)
        now = datetime.now(UTC)
        subscription = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            provider=provider,
            provider_customer_id=f"cus_{user_id}",
            provider_subscription_id=f"sub_{user_id}_{plan_code}",
            current_period_start=now,
            current_period_end=now + timedelta(days=period_days),
        )
        session.add(subscription)
        await session.commit()
    return subscription


def _csrf(html: str) -> str:
    """CSRF token from the dashboard shell."""

    match = re.search(r'data-csrf-token="([a-f0-9]+)"', html)
    assert match is not None, "CSRF token not found on page"
    return match.group(1)


def _recorded_creem_payload(calls: list[dict]) -> dict:
    """The Creem upgrade payload captured by the fake provider_request."""

    upgrade_calls = [
        call for call in calls if "/v1/subscriptions/" in call["url"] and "upgrade" in call["url"]
    ]
    assert len(upgrade_calls) == 1, f"Expected exactly one Creem upgrade call, got {upgrade_calls}"
    return upgrade_calls[0]["payload"]


@pytest.fixture
def fake_creem(monkeypatch):
    """Capture the Creem upgrade call instead of sending it over the network."""

    calls: list[dict] = []

    async def fake_provider_request(
        settings,
        method: str,
        url: str,
        *,
        provider: str,
        operation: str,
        **kwargs,
    ) -> httpx.Response:
        calls.append(
            {
                "settings": settings,
                "method": method,
                "url": url,
                "provider": provider,
                "operation": operation,
                "payload": kwargs.get("json", {}),
            }
        )
        # Return a Creem-shaped success body.
        return httpx.Response(
            200,
            json={"id": f"creem_upgrade_{uuid4().hex[:12]}"},
        )

    monkeypatch.setattr(plan_changes_module, "provider_request", fake_provider_request)
    return calls


@pytest.mark.parametrize("timing", [TIMING_IMMEDIATE, TIMING_PERIOD_END])
@pytest.mark.parametrize("period_days", [30, 365], ids=["monthly", "annual"])
async def test_upgrade_trader_to_pro_records_intent_and_moves_access(
    test_context,
    fake_creem,
    timing: str,
    period_days: int,
):
    """Trader -> Pro: the right record, the right Creem instruction, and access
    moves immediately only when the customer chose now.
    """

    email = f"upgrade-{timing}-{period_days}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    subscription = await _grant_paid_plan(
        session_factory,
        user_id,
        "trader",
        period_days=period_days,
    )

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    before_page = await client.get("/dashboard/billing")
    assert before_page.status_code == 200, before_page.text
    assert 'data-plan-switch="upgrade"' in before_page.text
    assert "Upgrade" in before_page.text

    csrf = _csrf(before_page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": timing,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert post.headers["location"] == "/dashboard/billing?message=plan_upgraded"

    after_page = await client.get(post.headers["location"])
    assert after_page.status_code == 200, after_page.text
    assert "Your plan is changing" in after_page.text
    if timing == TIMING_PERIOD_END:
        assert "starts" in after_page.text.lower() or "end of the period" in after_page.text.lower()

    async with session_factory() as session:
        change = await session.scalar(
            select(SubscriptionPlanChange)
            .where(SubscriptionPlanChange.user_id == user_id)
            .order_by(SubscriptionPlanChange.created_at.desc())
        )
        assert change is not None
        assert change.kind == "upgrade"
        assert change.from_plan_code == "trader"
        assert change.to_plan_code == "pro"
        assert change.timing == timing
        assert change.provider == "creem"

        refreshed_sub = await session.get(Subscription, subscription.id)
        entitlement = await EntitlementService(session).current(user_id)

        if timing == TIMING_IMMEDIATE:
            assert change.status == "applied"
            effective_at = (
                change.effective_at.replace(tzinfo=UTC)
                if change.effective_at.tzinfo is None
                else change.effective_at
            )
            assert effective_at <= datetime.now(UTC)
            pro = await PlanCatalogService(session).get_or_sync("pro")
            assert refreshed_sub.plan_id == pro.id
            assert entitlement.plan.code == "pro"
        else:
            assert change.status == "scheduled"
            effective_at = (
                change.effective_at.replace(tzinfo=UTC)
                if change.effective_at.tzinfo is None
                else change.effective_at
            )
            assert effective_at == subscription.current_period_end
            trader = await PlanCatalogService(session).get_or_sync("trader")
            assert refreshed_sub.plan_id == trader.id
            assert entitlement.plan.code == "trader"

    payload = _recorded_creem_payload(fake_creem)
    expected_behaviour = (
        "proration-charge-immediately"
        if timing == TIMING_IMMEDIATE
        else "proration-none"
    )
    assert payload["update_behavior"] == expected_behaviour
    assert payload["product_id"] == "prod_test_pro_monthly"


async def test_downgrade_pro_to_trader_is_scheduled_and_applies_at_period_end(
    test_context,
    fake_creem,
):
    """Pro -> Trader: never immediate, stays Pro until the paid period ends, then
    apply_due_changes moves access and pauses any excess monitors.
    """

    email = "downgrade-pro-to-trader@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    subscription = await _grant_paid_plan(
        session_factory,
        user_id,
        "pro",
        period_days=30,
    )
    period_end = subscription.current_period_end

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    before_page = await client.get("/dashboard/billing")
    assert 'data-plan-switch="downgrade"' in before_page.text

    csrf = _csrf(before_page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "trader",
            "timing": TIMING_PERIOD_END,
            "reason_code": "too_expensive",
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert post.headers["location"] == "/dashboard/billing?message=plan_downgraded"

    after_page = await client.get(post.headers["location"])
    assert after_page.status_code == 200, after_page.text
    assert "Your smaller plan is booked" in after_page.text
    assert "You are moving to Plus" in after_page.text

    async with session_factory() as session:
        change = await session.scalar(
            select(SubscriptionPlanChange)
            .where(SubscriptionPlanChange.user_id == user_id)
            .order_by(SubscriptionPlanChange.created_at.desc())
        )
        assert change is not None
        assert change.kind == "downgrade"
        assert change.from_plan_code == "pro"
        assert change.to_plan_code == "trader"
        assert change.timing == TIMING_PERIOD_END
        assert change.status == "scheduled"
        effective_at = (
            change.effective_at.replace(tzinfo=UTC)
            if change.effective_at.tzinfo is None
            else change.effective_at
        )
        assert effective_at == period_end
        assert change.reason_code == "too_expensive"

        entitlement = await EntitlementService(session).current(user_id)
        assert entitlement.plan.code == "pro"

        # Simulate the scheduler running one second after the period ends.
        await PlanChangeService(session, settings).apply_due_changes(
            now=period_end + timedelta(seconds=1)
        )

        refreshed_sub = await session.get(Subscription, subscription.id)
        entitlement_after = await EntitlementService(session).current(user_id)
        assert refreshed_sub.plan_id == (await PlanCatalogService(session).get_or_sync("trader")).id
        assert entitlement_after.plan.code == "trader"

        refreshed_change = await session.get(SubscriptionPlanChange, change.id)
        assert refreshed_change.status == "applied"
        assert refreshed_change.applied_at is not None

    payload = _recorded_creem_payload(fake_creem)
    assert payload["update_behavior"] == "proration-none"
    assert payload["product_id"] == "prod_test_trader_monthly"


async def test_downgrade_immediate_is_refused(test_context, fake_creem):
    """A downgrade sent with timing=immediate must be refused, not silently
    turned into a period-end change.
    """

    email = "downgrade-immediate-refused@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "pro")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    csrf = _csrf(page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "trader",
            "timing": TIMING_IMMEDIATE,
            "reason_code": "too_expensive",
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert "error=downgrade_is_period_end" in post.headers["location"]

    error_page = await client.get(post.headers["location"])
    assert "end of the period you have already paid for" in error_page.text

    async with session_factory() as session:
        assert await session.scalar(select(SubscriptionPlanChange)) is None

    assert fake_creem == []


async def test_every_plan_card_button_matches_the_server(test_context, fake_creem):
    """What a plan card offers is exactly what the server accepts..."""

    email = f"drift-check-{uuid4().hex[:8]}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "trader")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text
    # The page draws exactly one live switch button per plan it offers a change to.
    # Reading the codes from the page, not from the plan catalog, is the point: the
    # server decides the buttons, so an offered button must also be accepted, and a
    # refused plan must have no button to press.
    offered = set(
        re.findall(
            r'data-plan-switch-trigger data-plan-switch="(?:upgrade|downgrade)" '
            r'data-plan-code="([a-z]+)"',
            page.text,
        )
    )
    assert offered, "the page drew no plan switch buttons at all"

    for plan_code in ("trader", "pro", "demo"):
        csrf = _csrf(page.text)
        post = await client.post(
            "/dashboard/billing/switch",
            data={
                "plan_code": plan_code,
                "timing": TIMING_PERIOD_END,
                "switch_consent": "true",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        location = post.headers["location"]
        if plan_code in offered:
            assert post.status_code == 303
            assert "error=" not in location, (
                f"the page offers switching to {plan_code}, but the server refuses: "
                f"{location}"
            )
        else:
            assert "error=" in location, (
                f"the page offers no way to switch to {plan_code}, but the server "
                f"accepted it: {location}"
            )


async def test_timeout_on_immediate_upgrade_never_invites_a_retry(
    test_context, monkeypatch
):
    """The payment company may have charged before its answer was lost.

    A timeout on an immediate upgrade has an unknown outcome, so the refusal must
    not say "please try again": trying again would ask Creem for the difference a
    second time. It must send the customer to the billing page instead.
    """

    email = "upgrade-timeout@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "trader")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    async def timeout_provider_request(*args, **kwargs):
        # The upgrade may already have taken the money before the answer was lost.
        raise httpx.TimeoutException("connection read timed out")

    monkeypatch.setattr(
        plan_changes_module, "provider_request", timeout_provider_request
    )

    page = await client.get("/dashboard/billing")
    csrf = _csrf(page.text)
    response = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_IMMEDIATE,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=billing_timeout_needs_check" in response.headers["location"]

    error_page = await client.get(response.headers["location"])
    assert "did not answer in time" in error_page.text
    assert "try again" not in error_page.text.casefold(), (
        "the refusal asked the customer to retry a charge of unknown outcome"
    )
    assert "do not repeat this request" in error_page.text.casefold()

    async with session_factory() as session:
        change = await session.scalar(select(SubscriptionPlanChange))
        assert change is not None
        assert change.status == "failed"


async def test_crypto_paid_account_is_told_to_buy_not_switch(test_context):
    """A NOWPayments customer has no card on file, so the switch route refuses and
    the page explains the checkout path instead of showing a dead upgrade form.
    """

    email = "crypto-switch-refused@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(
        session_factory,
        user_id,
        "trader",
        provider="nowpayments",
        period_days=30,
    )

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text
    assert "You paid with crypto" in page.text
    assert 'data-dashboard-purchase-button' in page.text
    assert 'data-plan-switch-trigger' not in page.text

    csrf = _csrf(page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_IMMEDIATE,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert "error=plan_change_needs_payment" in post.headers["location"]

    error_page = await client.get(post.headers["location"])
    assert "no card to change" in error_page.text.lower()

    async with session_factory() as session:
        assert await session.scalar(select(SubscriptionPlanChange)) is None


async def test_switch_to_free_plan_is_not_offered_and_is_refused(test_context):
    """The product does not sell a switch to the free plan. The page marks it
    unavailable and the route refuses it.
    """

    email = "switch-to-free-refused@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "trader")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text
    # The free plan card should say Not available, not show a switch form trigger.
    assert "Not available" in page.text

    csrf = _csrf(page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "demo",
            "timing": TIMING_PERIOD_END,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert "error=plan_not_available" in post.headers["location"]

    async with session_factory() as session:
        assert await session.scalar(select(SubscriptionPlanChange)) is None


@pytest.mark.parametrize(
    ("plan_code", "expected_error"),
    [
        ("trader", "plan_change_not_needed"),
        ("creator", "plan_not_available"),
        ("lifetime", "plan_not_available"),
    ],
)
async def test_invalid_or_denied_switches_are_refused(
    test_context,
    fake_creem,
    plan_code: str,
    expected_error: str,
):
    """Switching to the plan you already hold, or to a non-public/non-purchasable
    plan, must fail cleanly and write nothing.
    """

    email = f"denied-{expected_error}-{plan_code}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "trader")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    csrf = _csrf(page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": plan_code,
            "timing": TIMING_PERIOD_END,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert f"error={expected_error}" in post.headers["location"]

    async with session_factory() as session:
        assert await session.scalar(select(SubscriptionPlanChange)) is None

    assert fake_creem == []


async def test_switch_with_already_pending_change_is_refused(test_context, fake_creem):
    """Only one future change can be booked at a time."""

    email = "pending-change-refused@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "trader")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    csrf = _csrf(page.text)

    first = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_PERIOD_END,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert first.status_code == 303

    second = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_PERIOD_END,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert second.status_code == 303, second.text
    assert "error=change_already_requested" in second.headers["location"]

    async with session_factory() as session:
        changes = list(
            (await session.scalars(select(SubscriptionPlanChange))).all()
        )
        assert len(changes) == 1
        assert changes[0].to_plan_code == "pro"

    # Only the first Creem call was made.
    assert len(fake_creem) == 1


async def test_switch_without_paid_plan_is_refused(test_context, fake_creem):
    """An account with no paid subscription cannot use the switch route."""

    email = "no-paid-plan-switch@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    csrf = _csrf(page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_PERIOD_END,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert "error=no_paid_plan" in post.headers["location"]

    async with session_factory() as session:
        assert await session.scalar(select(SubscriptionPlanChange)) is None

    assert fake_creem == []


async def test_upgrade_without_consent_is_refused(test_context, fake_creem):
    """The consent checkbox is required; without it nothing is recorded or sent."""

    email = "upgrade-no-consent@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "trader")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    csrf = _csrf(page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_IMMEDIATE,
            # switch_consent intentionally omitted
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert "error=consent_required" in post.headers["location"]

    async with session_factory() as session:
        assert await session.scalar(select(SubscriptionPlanChange)) is None

    assert fake_creem == []
