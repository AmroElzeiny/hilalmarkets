import re
from urllib.parse import urlsplit

from sqlalchemy import func, select

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    PaymentEmailDelivery,
    Plan,
    Subscription,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import UserRole


async def _signup(test_context, email: str = "checkout@example.com") -> None:
    requested = await test_context["client"].post(
        "/signup",
        data={
            "email": email,
            "display_name": "Amina Trader",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert requested.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def _review_form(test_context, plan_code: str = "trader") -> dict[str, str]:
    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    response = await test_context["client"].get(
        f"/dashboard/billing/checkout?plan_code={plan_code}"
    )
    assert response.status_code == 200, response.text
    csrf = re.search(r'name="csrf_token" value="([a-f0-9]+)"', response.text)
    request_id = re.search(
        r'name="checkout_request_id" value="([a-f0-9]+)"', response.text
    )
    assert csrf is not None
    assert request_id is not None
    return {
        "plan_code": plan_code,
        "billing_cycle": "monthly",
        "checkout_request_id": request_id.group(1),
        "terms_accepted": "true",
        "csrf_token": csrf.group(1),
    }


async def _make_admin(test_context, email: str) -> None:
    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == email)
        )
        assert user is not None
        user.role = UserRole.ADMIN
        await session.commit()


async def test_checkout_uses_server_price_and_deduplicates_attempt(test_context):
    await _signup(test_context)
    form = await _review_form(test_context)
    tampered = {**form, "amount": "0.01", "currency": "XXX"}
    first = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data=tampered,
        follow_redirects=False,
    )
    second = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data=tampered,
        follow_redirects=False,
    )
    assert first.status_code == 303
    assert urlsplit(first.headers["location"]).path == "/billing/success"
    assert second.status_code == 303
    assert "state=duplicate" in second.headers["location"]

    async with test_context["session_factory"]() as session:
        attempts = list((await session.scalars(select(BillingCheckoutAttempt))).all())
        plan = await session.scalar(select(Plan).where(Plan.code == "trader"))
        assert len(attempts) == 1
        assert plan is not None
        assert attempts[0].amount == plan.price_monthly
        assert attempts[0].currency == plan.currency


async def test_verified_static_payment_activates_once_and_emails_once(test_context):
    await _signup(test_context, "paid-once@example.com")
    form = await _review_form(test_context, "pro")
    checkout = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data=form,
        follow_redirects=False,
    )
    assert checkout.status_code == 303
    parsed = urlsplit(checkout.headers["location"])
    success_url = f"{parsed.path}?{parsed.query}"

    first = await test_context["client"].get(success_url)
    replay = await test_context["client"].get(success_url)
    assert first.status_code == 200
    assert "Plan activated" in first.text
    assert replay.status_code == 200

    payment_messages = [
        row
        for row in test_context["settings"].email_test_outbox
        if row.get("purpose") == "payment_success"
    ]
    assert len(payment_messages) == 1
    assert payment_messages[0]["subject"] == "Your HilalMarkets Pro plan is active"
    assert "Create a Watch Plan" in payment_messages[0]["body"]
    assert "HilalMarkets provides screening" in payment_messages[0]["body"]

    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(Subscription.id))) == 1
        assert await session.scalar(select(func.count(PaymentEmailDelivery.id))) == 1
        attempt = await session.scalar(select(BillingCheckoutAttempt))
        delivery = await session.scalar(select(PaymentEmailDelivery))
        assert attempt is not None and attempt.status == "completed"
        assert delivery is not None and delivery.status == "sent"
        assert delivery.attempt_count == 1


async def test_checkout_requires_terms_and_valid_csrf(test_context):
    await _signup(test_context, "checkout-guard@example.com")
    form = await _review_form(test_context)
    without_terms = {key: value for key, value in form.items() if key != "terms_accepted"}
    rejected = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data=without_terms,
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert "state=billing_terms_required" in rejected.headers["location"]

    invalid_csrf = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data={**form, "csrf_token": "invalid"},
        follow_redirects=False,
    )
    assert invalid_csrf.status_code == 403


async def test_payment_email_preview_is_rendered_in_development(test_context):
    await _signup(test_context, "preview@example.com")
    forbidden = await test_context["client"].get(
        "/dashboard/admin/payment-email-preview?plan_code=pro"
    )
    assert forbidden.status_code == 403

    await _make_admin(test_context, "preview@example.com")
    response = await test_context["client"].get(
        "/dashboard/admin/payment-email-preview?plan_code=pro"
    )
    assert response.status_code == 200
    assert "Your Pro plan is active" in response.text
    assert "30-day access" in response.text
    assert "does not renew automatically" in response.text
    assert response.headers["x-robots-tag"] == "noindex, nofollow"


async def test_payment_email_preview_is_not_exposed_in_production(test_context):
    await _signup(test_context, "production-preview@example.com")
    await _make_admin(test_context, "production-preview@example.com")
    original_environment = test_context["settings"].app_env
    test_context["settings"].app_env = "production"
    try:
        response = await test_context["client"].get(
            "/dashboard/admin/payment-email-preview?plan_code=pro"
        )
    finally:
        test_context["settings"].app_env = original_environment
    assert response.status_code == 404
