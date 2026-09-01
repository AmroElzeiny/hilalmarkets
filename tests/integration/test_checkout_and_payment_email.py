import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.plans import effective_monthly_price
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    PaymentEmailDelivery,
    Plan,
    Subscription,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import SubscriptionStatus, UserRole
from ai_market_monitor.services.billing import BillingError, BillingService, CreemBillingProvider
from ai_market_monitor.services.entitlements import PlanCatalogService


async def _signup(test_context, email: str = "checkout@example.com") -> None:
    requested = await test_context["client"].post(
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
        "payment_method": "card",
        "checkout_request_id": request_id.group(1),
        "terms_accepted": "true",
        "first_name": "Amina",
        "last_name": "Trader",
        "address_line1": "1 Market Street",
        "city": "Cairo",
        "country": "Egypt",
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
    assert second.headers["location"] == first.headers["location"]

    async with test_context["session_factory"]() as session:
        attempts = list((await session.scalars(select(BillingCheckoutAttempt))).all())
        plan = await session.scalar(select(Plan).where(Plan.code == "trader"))
        assert len(attempts) == 1
        assert plan is not None
        assert attempts[0].amount == effective_monthly_price("trader")
        assert attempts[0].currency == plan.currency


async def test_verified_static_payment_activates_once_and_emails_once(test_context):
    await _signup(test_context, "paid-once@example.com")
    form = await _review_form(test_context, "trader")
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
    # "Hilal Markets" with the space: the name in prose, per `brand guide.md` section 4
    # and `core/copy_rules.py`, which enforces it.
    assert payment_messages[0]["subject"] == "Your Hilal Markets Monitor plan is active"
    assert "Create a Watchlist" in payment_messages[0]["body"]
    assert "Hilal Markets provides screening" in payment_messages[0]["body"]

    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(Subscription.id))) == 1
        assert await session.scalar(select(func.count(PaymentEmailDelivery.id))) == 1
        attempt = await session.scalar(select(BillingCheckoutAttempt))
        delivery = await session.scalar(select(PaymentEmailDelivery))
        assert attempt is not None and attempt.status == "completed"
        assert delivery is not None and delivery.status == "sent"
        assert delivery.attempt_count == 1


async def test_creem_checkout_route_waits_for_signed_payment_before_activation(
    test_context,
    monkeypatch,
):
    await _signup(test_context, "creem-route@example.com")
    enabled = test_context["settings"].model_copy(
        update={
            "billing_enabled": True,
            "billing_card_provider": "creem",
            "creem_api_key": SecretStr("creem-test-key"),
            "creem_webhook_secret": SecretStr("creem-webhook-secret"),
            "creem_product_ids": {"trader_monthly": "prod_monitor_monthly"},
        }
    )
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    async def fake_creem_post(self, path, payload):
        assert path == "/v1/checkouts"
        return {
            "id": f"ch_{payload['request_id']}",
            "checkout_url": f"https://checkout.creem.io/{payload['request_id']}",
        }

    monkeypatch.setattr(CreemBillingProvider, "_post", fake_creem_post)
    page = await test_context["client"].get("/dashboard/billing")
    csrf = re.search(r'name="csrf_token" value="([a-f0-9]+)"', page.text)
    request_id = re.search(
        r'name="checkout_request_id" value="([a-f0-9]+)"',
        page.text,
    )
    assert csrf is not None
    assert request_id is not None

    checkout = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data={
            "plan_code": "trader",
            "billing_cycle": "monthly",
            "payment_method": "card",
            "checkout_request_id": request_id.group(1),
            "terms_accepted": "true",
            "first_name": "Creem",
            "last_name": "Route",
            "address_line1": "1 Market Street",
            "city": "Cairo",
            "country": "Egypt",
            "csrf_token": csrf.group(1),
        },
        headers={"accept": "application/json", "x-requested-with": "XMLHttpRequest"},
    )
    assert checkout.status_code == 200
    assert checkout.json()["checkout_url"].startswith("https://checkout.creem.io/")

    async with test_context["session_factory"]() as session:
        attempt = await session.scalar(
            select(BillingCheckoutAttempt).where(
                BillingCheckoutAttempt.provider == "creem"
            )
        )
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(
                UserIdentity.normalized_identifier == "creem-route@example.com"
            )
        )
        assert attempt is not None
        assert user is not None
        assert attempt.status == "pending"
        attempt_id = attempt.id
        user_id = user.id

    billing_page = await test_context["client"].get("/dashboard/billing")
    assert billing_page.status_code == 200
    assert f"/dashboard/billing/checkout/{attempt_id}/resume" in billing_page.text
    resumed = await test_context["client"].get(
        f"/dashboard/billing/checkout/{attempt_id}/resume",
        follow_redirects=False,
    )
    assert resumed.status_code == 303
    assert resumed.headers["location"].startswith("https://checkout.creem.io/")
    assert resumed.headers["referrer-policy"] == "no-referrer"

    pending = await test_context["client"].get(
        f"/billing/success?attempt={attempt_id}"
    )
    assert pending.status_code == 200
    assert "Payment confirmation pending" in pending.text
    assert "Only a verified provider webhook can change paid access." in pending.text

    payload = {
        "id": "evt_creem_route_paid",
        "eventType": "subscription.paid",
        "object": {
            "object": "subscription",
            "id": "sub_creem_route",
            "status": "active",
            "customer": {"id": "cus_creem_route"},
            "product": {
                "id": "prod_monitor_monthly",
                "price": int(effective_monthly_price("trader") * 100),
                "currency": "USD",
            },
            "metadata": {
                "checkout_attempt_id": str(attempt_id),
                "user_id": str(user_id),
                "plan_code": "trader",
                "billing_cycle": "monthly_auto_renewal",
            },
            "current_period_start_date": "2035-01-01T00:00:00+00:00",
            "current_period_end_date": "2035-02-01T00:00:00+00:00",
            "last_transaction_id": "txn_creem_route",
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(
        b"creem-webhook-secret",
        body,
        digestmod="sha256",
    ).hexdigest()
    webhook = await test_context["client"].post(
        "/api/v1/billing/webhooks/creem",
        content=body,
        headers={"creem-signature": signature},
    )
    assert webhook.status_code == 200, webhook.text

    completed = await test_context["client"].get(
        f"/billing/success?attempt={attempt_id}"
    )
    assert completed.status_code == 200
    assert "Plan activated" in completed.text
    assert "Go to dashboard" in completed.text
    payment_messages = [
        row
        for row in enabled.email_test_outbox
        if row.get("purpose") == "payment_success"
    ]
    assert len(payment_messages) == 1
    assert "Assalamu Alaikum Creem" in payment_messages[0]["body"]

    portal_page = await test_context["client"].get("/dashboard/billing/portal")
    assert portal_page.status_code == 200
    assert "Manage your subscription" in portal_page.text
    assert "Monitor" in portal_page.text
    assert "Creem" in portal_page.text
    assert "Your payments and receipts" in portal_page.text
    portal_csrf = re.search(
        r'action="/dashboard/billing/portal".*?name="csrf_token" value="([a-f0-9]+)"',
        portal_page.text,
        flags=re.DOTALL,
    )
    assert portal_csrf is not None

    async def fake_creem_portal(self, path, payload):
        assert path == "/v1/customers/billing"
        assert payload["customer_id"] == "cus_creem_route"
        return {"customer_portal_link": "https://creem.io/customer/secure-session"}

    monkeypatch.setattr(CreemBillingProvider, "_post", fake_creem_portal)
    portal = await test_context["client"].post(
        "/dashboard/billing/portal",
        data={"csrf_token": portal_csrf.group(1)},
        follow_redirects=False,
    )
    assert portal.status_code == 303
    assert portal.headers["location"] == "https://creem.io/customer/secure-session"


async def test_checkout_json_error_stays_in_the_billing_dialog(test_context):
    await _signup(test_context, "creem-dialog-error@example.com")
    enabled = test_context["settings"].model_copy(
        update={
            "billing_enabled": True,
            "billing_card_provider": "creem",
            "creem_api_key": None,
            "creem_webhook_secret": SecretStr("creem-webhook-secret"),
            "creem_product_ids": {"trader_monthly": "prod_monitor_monthly"},
        }
    )
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    page = await test_context["client"].get("/dashboard/billing")
    csrf = re.search(r'name="csrf_token" value="([a-f0-9]+)"', page.text)
    request_id = re.search(
        r'name="checkout_request_id" value="([a-f0-9]+)"',
        page.text,
    )
    assert csrf is not None
    assert request_id is not None

    checkout = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data={
            "plan_code": "trader",
            "billing_cycle": "monthly",
            "payment_method": "card",
            "checkout_request_id": request_id.group(1),
            "terms_accepted": "true",
            "first_name": "Creem",
            "last_name": "Error",
            "address_line1": "1 Market Street",
            "city": "Cairo",
            "country": "Egypt",
            "csrf_token": csrf.group(1),
        },
        headers={"accept": "application/json", "x-requested-with": "XMLHttpRequest"},
    )
    assert checkout.status_code == 400
    assert checkout.json() == {
        "error": {
            "code": "creem_api_key_missing",
            "message": "Creem API access is not configured.",
        }
    }
    assert "location" not in checkout.headers


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
    assert "error=billing_terms_required" in rejected.headers["location"]

    invalid_csrf = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data={**form, "csrf_token": "invalid"},
        follow_redirects=False,
    )
    assert invalid_csrf.status_code == 403


async def test_monitor_monthly_selection_skips_an_unconfigured_trial(test_context):
    await _signup(test_context, "monitor-monthly@example.com")
    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    response = await test_context["client"].get(
        "/subscribe?plan_code=trader&billing_interval=monthly",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/dashboard/billing?selected_plan=trader&billing_interval=monthly&checkout=1"
    )


async def test_only_matching_paid_plan_blocks_checkout_and_admin_access_does_not(
    test_context,
):
    await _signup(test_context, "billing-access@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == "billing-access@example.com")
        )
        assert user is not None
        catalog = PlanCatalogService(session)
        trader = await catalog.get_or_sync("trader")
        lifetime = await catalog.get_or_sync("lifetime_partner")
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=lifetime.id,
                status=SubscriptionStatus.ACTIVE,
                provider="admin",
                provider_subscription_id=f"admin-lifetime-{user.id}",
                current_period_start=datetime.now(UTC),
                current_period_end=None,
            )
        )
        await session.commit()

    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    async with test_context["session_factory"]() as session:
        prepared = await BillingService(session, enabled).prepare_checkout(
            user_id=user.id,
            plan_code=trader.code,
            billing_cycle="monthly",
            request_key="lifetime-access-can-still-pay",
            terms_accepted=True,
            billing_profile={
                "first_name": "Billing",
                "last_name": "Access",
                "address_line1": "1 Market Street",
                "country": "Egypt",
            },
        )
        assert prepared.attempt.plan_id == trader.id

        pro = await PlanCatalogService(session).get_or_sync("pro")
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=trader.id,
                status=SubscriptionStatus.ACTIVE,
                provider="creem",
                provider_subscription_id=f"creem-monitor-{user.id}",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.flush()

        with pytest.raises(BillingError, match="already active"):
            await BillingService(session, enabled).prepare_checkout(
                user_id=user.id,
                plan_code=trader.code,
                billing_cycle="monthly",
                request_key="active-monitor-cannot-select-monitor-again",
                terms_accepted=True,
                billing_profile={
                    "first_name": "Billing",
                    "last_name": "Access",
                    "address_line1": "1 Market Street",
                    "country": "Egypt",
                },
            )

        with pytest.raises(BillingError) as error:
            await BillingService(session, enabled).prepare_checkout(
                user_id=user.id,
                plan_code=pro.code,
                billing_cycle="monthly",
                request_key="pro-is-coming-soon",
                terms_accepted=True,
                billing_profile={
                    "first_name": "Billing",
                    "last_name": "Access",
                    "address_line1": "1 Market Street",
                    "country": "Egypt",
                },
            )
        assert error.value.code == "plan_not_available"


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
