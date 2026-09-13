import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import lxml.html
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.copy_rules import scan_text
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PLAN_LIMIT_WORDS,
    PURCHASABLE_PLAN_CODES,
    RETIRED_DISCOUNT_CODES,
    effective_monthly_price,
    original_monthly_price,
    plan_name,
    plan_offer,
    price_after_percent,
    promotion_is_active,
)
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    PaymentEmailDelivery,
    Plan,
    Subscription,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import SubscriptionStatus, UserRole
from ai_market_monitor.services import billing as billing_module
from ai_market_monitor.services.billing import (
    PAYMENT_METHODS,
    BillingError,
    BillingService,
    CreemBillingProvider,
    plan_checkout_availability,
)
from ai_market_monitor.services.discount_codes import DiscountCodeService
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService
from ai_market_monitor.services.payment_emails import RECEIPT_LIMIT_KEYS
from tests.support.billing_config import live_billing_overrides


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


@pytest.mark.parametrize("plan_code", PURCHASABLE_PLAN_CODES)
async def test_a_selected_paid_plan_opens_its_checkout_dialog(test_context, plan_code):
    """Every paid plan selected before sign-in must still be selected after sign-in.

    Pro used to reach the billing page and then disappear because the page's auto-open
    state named the old sole paid plan rather than reading the paid-plan catalogue.
    """

    await _signup(test_context, f"selected-{plan_code}@example.com")
    enabled = test_context["settings"].model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    selected = await test_context["client"].get(
        f"/subscribe?plan_code={plan_code}&billing_interval=monthly",
        follow_redirects=False,
    )
    assert selected.status_code == 303
    billing = await test_context["client"].get(selected.headers["location"])
    assert billing.status_code == 200
    assert 'data-auto-open="true"' in billing.text
    assert f'data-selected-plan="{plan_code}"' in billing.text


@pytest.mark.parametrize(
    ("plan_code", "payment_method"),
    tuple(
        (plan_code, payment_method)
        for plan_code in PURCHASABLE_PLAN_CODES
        for payment_method in PAYMENT_METHODS
    ),
)
async def test_every_reported_payment_choice_opens_a_provider_page(
    test_context,
    monkeypatch,
    plan_code,
    payment_method,
):
    """Post the exact values rendered by the review page for each reported failure."""

    await _signup(
        test_context,
        f"reported-{plan_code}-{payment_method}@example.com",
    )
    enabled = test_context["settings"].model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    async def fake_creem_post(self, path, payload):
        assert path == "/v1/checkouts"
        return {
            "id": f"ch_{payload['request_id']}",
            "checkout_url": f"https://checkout.creem.io/{payload['request_id']}",
        }

    # The keyword parameter below is named ``json``, which shadows the module's
    # ``json`` inside this function, so bind the real parser before the def.
    json_loads = json.loads

    async def fake_provider_request(*args, provider, json=None, content=None, **kwargs):
        assert provider == "nowpayments"
        # The crypto invoice now sends its finished body as exact bytes through
        # ``content=`` (see ``core/money.wire_json_body``); a caller still passing
        # ``json=`` behaves as before. Read whichever one carried the body, once.
        body = json
        if body is None:
            assert content is not None, "the provider request carried no body"
            # ``json.loads`` accepts both ``bytes`` and ``str``.
            body = json_loads(content)
        return httpx.Response(
            200,
            json={
                "id": f"inv_{body['order_id']}",
                "invoice_url": f"https://nowpayments.io/payment/{body['order_id']}",
            },
        )

    monkeypatch.setattr(CreemBillingProvider, "_post", fake_creem_post)
    monkeypatch.setattr(billing_module, "provider_request", fake_provider_request)

    review = await test_context["client"].get(
        f"/dashboard/billing/checkout?plan_code={plan_code}"
    )
    assert review.status_code == 200, review.text

    def hidden(name: str) -> str:
        found = re.search(rf'name="{name}" value="([^"]*)"', review.text)
        assert found is not None, name
        return found.group(1)

    checkout = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data={
            "plan_code": hidden("plan_code"),
            "billing_cycle": hidden("billing_cycle"),
            "payment_method": payment_method,
            "checkout_request_id": hidden("checkout_request_id"),
            "terms_accepted": "true",
            "first_name": "Amina",
            "last_name": "Tester",
            "address_line1": "1 Market Street",
            "city": "Cairo",
            "country": "Egypt",
            "csrf_token": hidden("csrf_token"),
        },
        headers={"accept": "application/json", "x-requested-with": "XMLHttpRequest"},
    )
    assert checkout.status_code == 200, checkout.text
    expected_host = "checkout.creem.io" if payment_method == "card" else "nowpayments.io"
    assert urlsplit(checkout.json()["checkout_url"]).hostname == expected_host


async def test_review_page_shows_terms_for_each_available_payment_method(test_context):
    """A crypto choice must not inherit the default card provider's renewal terms."""

    await _signup(test_context, "method-terms@example.com")
    enabled = test_context["settings"].model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    review = await test_context["client"].get(
        "/dashboard/billing/checkout?plan_code=trader"
    )
    assert review.status_code == 200, review.text
    assert "Card access" in review.text
    assert "Monthly subscription. Renews monthly until cancelled." in review.text
    assert "Crypto access" in review.text
    assert "One-time 30-day access. No automatic renewal." in review.text


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


@pytest.mark.parametrize("plan_code", PURCHASABLE_PLAN_CODES)
async def test_the_review_page_quotes_the_price_it_will_charge(test_context, plan_code):
    """The last screen before payment must say the number the payment will ask for.

    It used to print the plan catalogue's ``price_monthly``, which is the *normal*
    price. While a launch offer ran, the page said $20 and the payment asked for $7.
    Asserted for every purchasable plan, not only the one on offer, because the same
    page draws them all.
    """

    await _signup(test_context, f"review-price-{plan_code}@example.com")
    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    response = await test_context["client"].get(
        f"/dashboard/billing/checkout?plan_code={plan_code}"
    )
    if not plan_offer(plan_code).monthly_available:
        # A plan the site says is "Soon" never reaches this page, and never leaks its
        # price into the page source. Typing the address by hand goes back to billing.
        assert response.status_code in {302, 303}
        assert "plan_not_available" in response.headers["location"]
        pytest.skip(f"{plan_code} is not on sale, so there is no review page")
    assert response.status_code == 200, response.text

    body = response.text
    # What the payment will really ask for. Nothing has to be typed to reach the launch
    # price now, so this one number is the price on the card, the number on this page and
    # the amount the payment company is asked for.
    charged = effective_monthly_price(plan_code)
    plan_offer_currency = "USD"
    assert f"{charged} {plan_offer_currency}" in body
    # A page that quotes a price is a page for something on sale.
    assert plan_offer(plan_code).monthly_available is True

    was = original_monthly_price(plan_code)
    if promotion_is_active():
        # The normal price is shown crossed out, so the customer can see what the offer
        # is worth - but it is never the number they are asked to pay.
        assert was is not None and was > charged
        assert f"{was} {plan_offer_currency}" in body
        assert 'class="price-original"' in body
        assert "data-offer-countdown" in body
    # A code that no longer works may never be named on a page that takes money.
    for retired in RETIRED_DISCOUNT_CODES:
        assert retired not in body


async def test_a_discount_code_changes_the_amount_the_payment_is_created_for(test_context):
    """The whole point: the code has to reach the charge, not just the screen.

    A code that only changes what a page displays is worse than no code at all — the
    person agrees to one number and is charged another.
    """

    await _signup(test_context, "discount-code@example.com")
    enabled = test_context["settings"].model_copy(
        update={
            "billing_enabled": True,
            "billing_crypto_provider": "nowpayments",
            # The key has to be here, not only the company name. A checkout is refused
            # when the company could not confirm the payment afterwards, and a company
            # with no key never can. Naming the company alone described a server that
            # shows no crypto Pay button at all, so the test would have proved the
            # discount on a screen no customer can reach.
            "nowpayments_api_key": SecretStr("test-only-not-a-real-key-000000"),
            "nowpayments_ipn_secret": SecretStr("test-only-not-a-real-key-000000"),
            "billing_discount_codes": {"TESTHALF": Decimal("50")},
        }
    )
    form = await _review_form(test_context, "trader")
    # `_review_form` installs its own settings override, so ours goes on afterwards or
    # it is quietly replaced and crypto stays switched off.
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    full = effective_monthly_price("trader")
    expected = price_after_percent(full, Decimal("50"))

    # The Apply button quotes a price...
    checked = await test_context["client"].post(
        "/dashboard/billing/discount",
        data={
            "plan_code": "trader",
            "payment_method": "crypto",
            "discount_code": "testhalf",
            "csrf_token": form["csrf_token"],
        },
    )
    assert checked.status_code == 200, checked.text
    quoted = checked.json()
    assert quoted["code"] == "TESTHALF"
    assert quoted["was"] == str(full)
    assert quoted["now"] == str(expected)

    # ...and the payment attempt is opened for exactly that, with the reason beside it.
    #
    # Asserted against the service rather than by driving the crypto route to a real
    # payment company: opening the invoice is a live HTTPS call, and what matters here is
    # the amount written down, which is what both the invoice and the webhook read.
    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == "discount-code@example.com")
        )
        assert user is not None
        await PlanCatalogService(session).sync_defaults()
        service = BillingService(session, enabled, provider_name="nowpayments")
        priced = await DiscountCodeService(enabled).price_for(
            "TESTHALF", plan_code="trader", full_amount=full, currency="USD"
        )
        prepared = await service.prepare_checkout(
            user_id=user.id,
            plan_code="trader",
            billing_cycle="monthly",
            request_key="discount-test",
            terms_accepted=True,
            discount=priced,
        )
        await session.commit()
        assert prepared.attempt.amount == expected, "the code did not reach the charge"
        assert prepared.attempt.discount_code == "TESTHALF"
        assert prepared.attempt.discount_percent == Decimal("50")

        # The same request without the code is a different order, not the same one at a
        # different price. Handing back the discounted attempt would charge the full-price
        # buyer less; handing back the full-price attempt would ignore the code.
        plain = await service.prepare_checkout(
            user_id=user.id,
            plan_code="trader",
            billing_cycle="monthly",
            request_key="discount-test",
            terms_accepted=True,
        )
        await session.commit()
        assert plain.attempt.id != prepared.attempt.id
        assert plain.attempt.amount == full
        assert plain.attempt.discount_code is None


async def test_a_code_is_refused_on_the_card_route_rather_than_quietly_dropped(test_context):
    """Dropping it would open a payment page at the full price straight after telling
    somebody their code had worked."""

    await _signup(test_context, "card-code@example.com")
    enabled = test_context["settings"].model_copy(
        update={
            "billing_enabled": True,
            "billing_discount_codes": {"TESTHALF": Decimal("50")},
        }
    )
    form = await _review_form(test_context, "trader")
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    answer = await test_context["client"].post(
        "/dashboard/billing/discount",
        data={
            "plan_code": "trader",
            "payment_method": "card",
            "discount_code": "TESTHALF",
            "csrf_token": form["csrf_token"],
        },
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "discount_code_card_route"


async def test_a_wrong_code_is_refused_and_charges_nothing(test_context):
    await _signup(test_context, "bad-code@example.com")
    enabled = test_context["settings"].model_copy(
        update={"billing_enabled": True, "billing_crypto_provider": "nowpayments"}
    )
    form = await _review_form(test_context, "trader")
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    answer = await test_context["client"].post(
        "/dashboard/billing/discount",
        data={
            "plan_code": "trader",
            "payment_method": "crypto",
            "discount_code": "NOTAREALCODE",
            "csrf_token": form["csrf_token"],
        },
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "discount_code_unknown"
    # A sentence a beginner can act on, with no code name and no field name in it.
    message = answer.json()["error"]["message"]
    assert "discount_code" not in message
    assert message.endswith(".")

    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(BillingCheckoutAttempt.id))) == 0


async def test_a_full_price_renewal_after_a_discounted_first_period_lands(test_context):
    """A code typed on the payment page moves the FIRST charge, not every later one.

    The buyer here used a discount code on Creem's own page, so the checkout row ended
    at the discounted figure. A month later Creem charged the plan's real price. The
    renewal used to be compared only against the stored (discounted) figure and refused
    as overpaid: the customer paid, and the renewal never landed. A confirmed renewal
    must be accepted at either figure — what was last stored, or the plan's current
    effective price — while an amount above the plan's price is still refused.
    """

    email = "renewal-after-discount@example.com"
    await _signup(test_context, email)
    enabled = test_context["settings"].model_copy(update=live_billing_overrides())
    full_price = effective_monthly_price("trader")
    first_period = full_price - Decimal("2.00")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == email)
        )
        assert user is not None
        plan = await PlanCatalogService(session).get_or_sync("trader")
        now = datetime.now(UTC)
        attempt = BillingCheckoutAttempt(
            user_id=user.id,
            plan_id=plan.id,
            billing_cycle="monthly_auto_renewal",
            provider="creem",
            status="completed",
            idempotency_key=f"renewal-discount-{user.id}",
            terms_version="test",
            amount=first_period,
            currency="USD",
            discount_code="WELCOME2",
            terms_accepted_at=now - timedelta(days=30),
            expires_at=now,
            completed_at=now - timedelta(days=30),
            billing_profile={"first_name": "Amina"},
        )
        subscription = Subscription(
            user_id=user.id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            provider="creem",
            provider_subscription_id=f"creem-renew-{user.id}",
            current_period_start=now - timedelta(days=30),
            current_period_end=now - timedelta(days=1),
        )
        session.add_all([attempt, subscription])
        await session.commit()

    def renewal(event_id: str, amount: Decimal, **extra: str) -> dict[str, object]:
        moment = datetime.now(UTC)
        return {
            "id": event_id,
            "type": "subscription.paid",
            "data": {
                "checkout_attempt_id": str(attempt.id),
                "plan_code": "trader",
                "provider_subscription_id": subscription.provider_subscription_id,
                "amount": str(amount),
                "currency": "USD",
                "status": "active",
                "current_period_start": moment.isoformat(),
                "current_period_end": (moment + timedelta(days=30)).isoformat(),
                **extra,
            },
        }

    async with test_context["session_factory"]() as session:
        service = BillingService(session, enabled, provider_name="creem")

        # 1. Full-price renewal after the discounted first period: accepted, and the
        #    row records the figure really paid.
        first = await service.process_event(
            provider="creem", payload=renewal("renew-full-price", full_price)
        )
        await session.commit()
        assert first.processing_status == "processed"
        landed = await session.get(BillingCheckoutAttempt, attempt.id)
        assert landed is not None
        assert landed.amount == full_price

        # 2. A later renewal that is discounted again still lands, and the row follows.
        again = full_price - Decimal("3.00")
        second = await service.process_event(
            provider="creem",
            payload=renewal(
                "renew-discounted-again",
                again,
                provider_discount_amount="3.00",
                provider_discount_code="SAVE3",
            ),
        )
        await session.commit()
        assert second.processing_status == "processed"
        relanded = await session.get(BillingCheckoutAttempt, attempt.id)
        assert relanded is not None
        assert relanded.amount == again

        # 3. Still no free ride: an amount above the plan's price is refused.
        with pytest.raises(BillingError) as error:
            await service.process_event(
                provider="creem",
                payload=renewal("renew-too-much", full_price + Decimal("5.00")),
            )
        assert error.value.code == "payment_overpaid"


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
    assert payment_messages[0]["subject"] == (
        f"Your Hilal Markets {plan_name('trader')} plan is active"
    )
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
    # Beginner-language repair (R29 sweep): this line used to read "Only a verified
    # provider webhook can change paid access." The word "webhook" is machine talk, so
    # the sentence was rewritten to say what really happens. The assertion stays strict:
    # it pins the new sentence, and the replacement wording must still pass the product
    # copy lint (`core/copy_rules.py`) and carry no machine word on the page.
    assert (
        "Paid access changes only when we receive the payment confirmation from "
        "the payment company." in pending.text
    )
    assert "webhook" not in _visible_words(pending.text).casefold()
    assert scan_text(
        _visible_words(pending.text), Path("billing_result [pending]")
    ) == ()

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
    # Named from the catalog. The plan was called "Monitor" here for weeks after it had
    # been renamed to Plus, and this assertion happily agreed with the stale name.
    assert plan_name("trader") in portal_page.text
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
    # A refusal, in a sentence a beginner can act on. It used to answer "Creem API access
    # is not configured" — the name of a setting, shown to somebody buying a plan.
    assert checkout.json() == {
        "error": {
            "code": "payment_method_unavailable",
            "message": (
                "This plan cannot be paid for by card yet. "
                "There is no other way to pay for this plan yet."
            ),
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
        paid_at = datetime.now(UTC)
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
        prepared.attempt.status = "completed"
        prepared.attempt.completed_at = paid_at

        pro = await PlanCatalogService(session).get_or_sync("pro")
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=trader.id,
                status=SubscriptionStatus.ACTIVE,
                provider=prepared.attempt.provider,
                provider_subscription_id=f"paid-trader-{user.id}",
                current_period_start=paid_at,
                current_period_end=paid_at + timedelta(days=30),
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

        moved = await BillingService(session, enabled).prepare_checkout(
            user_id=user.id,
            plan_code=pro.code,
            billing_cycle="monthly",
            request_key="paid-plan-buys-another-paid-plan",
            terms_accepted=True,
            billing_profile={
                "first_name": "Billing",
                "last_name": "Access",
                "address_line1": "1 Market Street",
                "country": "Egypt",
            },
        )
        assert moved.attempt.amount == effective_monthly_price(pro.code)
        assert moved.attempt.replaces_subscription_id is not None
        assert moved.attempt.replaces_checkout_attempt_id == prepared.attempt.id


async def _seed_paid_subscription(
    test_context,
    email: str,
    plan_code: str,
    *,
    provider: str = "creem",
) -> None:
    """Hand the signed-up account one live provider-backed paid plan.

    The provider is a real payment company's name on purpose: administrative grants, the
    free plan and trials do not count as paid, so seeding with one of those would not
    exercise the rule this file exists to prove.

    Which company matters as much as the fact of paying. "creem" holds a card and can be
    re-priced; "nowpayments" is a crypto invoice that holds nothing. The two lead to
    opposite answers about buying another plan, and both are seeded here.
    """

    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == email)
        )
        assert user is not None
        plan = await PlanCatalogService(session).get_or_sync(plan_code)
        paid_at = datetime.now(UTC)
        session.add(
            BillingCheckoutAttempt(
                user_id=user.id,
                plan_id=plan.id,
                billing_cycle=(
                    "monthly_auto_renewal" if provider == "creem" else "one_time_30_day"
                ),
                provider=provider,
                status="completed",
                idempotency_key=f"seed-paid-{provider}-{user.id}-{plan_code}",
                terms_version="test",
                amount=effective_monthly_price(plan_code),
                currency="USD",
                terms_accepted_at=paid_at,
                expires_at=paid_at + timedelta(hours=1),
                completed_at=paid_at,
                billing_profile={},
            )
        )
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider=provider,
                provider_subscription_id=f"{provider}-monitor-{user.id}",
                current_period_start=paid_at,
                current_period_end=paid_at + timedelta(days=30),
            )
        )
        await session.commit()


@pytest.mark.parametrize(
    ("held_code", "target_code"),
    [
        (held, target)
        for held in PURCHASABLE_PLAN_CODES
        for target in PURCHASABLE_PLAN_CODES
        if held != target
    ],
)
async def test_the_review_page_allows_full_price_for_every_different_paid_plan(
    test_context,
    held_code,
    target_code,
):
    """Every ordered pair agrees that the different plan is bought at full price."""

    email = f"review-refusal-{held_code}-{target_code}@example.com"
    await _signup(test_context, email)
    await _seed_paid_subscription(test_context, email, held_code)
    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    response = await test_context["client"].get(
        f"/dashboard/billing/checkout?plan_code={target_code}"
    )
    assert response.status_code == 200, response.text

    expected = plan_checkout_availability(
        enabled,
        plan_code=target_code,
        active_paid_plan_codes={held_code},
    )
    assert expected["purchasable"]
    assert expected["refusal"] == ""
    assert "checkout-confirm-form" in response.text
    assert "First name" in response.text


@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
async def test_the_review_page_shows_already_subscribed_for_the_held_plan(
    test_context,
    code,
):
    """Same-plan reversal: holding plan X and opening plan X shows the active-plan
    notice, never a payment form. Asserted for every paid plan."""

    email = f"review-self-{code}@example.com"
    await _signup(test_context, email)
    await _seed_paid_subscription(test_context, email, code)
    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    response = await test_context["client"].get(
        f"/dashboard/billing/checkout?plan_code={code}"
    )
    assert response.status_code == 200, response.text
    assert "notice notice-success" in response.text
    assert "checkout-confirm-form" not in response.text


@pytest.mark.parametrize(
    ("held_code", "target_code"),
    [
        (held, target)
        for held in PURCHASABLE_PLAN_CODES
        for target in PURCHASABLE_PLAN_CODES
        if held != target
    ],
)
async def test_crypto_access_can_be_replaced_by_buying_another_plan(
    test_context,
    held_code,
    target_code,
):
    """A crypto customer really can buy a different plan, on the page and at the server.

    Crypto access holds no card. Nothing can be re-priced and nothing can be charged
    twice, so buying is the only route to another plan - and the billing page says so in
    its own words. The purchase guard used to refuse it anyway, which turned that advice
    into a Pay button nobody could press. Both halves are checked here, because a page
    that offers a purchase the server refuses is the same dead end wearing a nicer face.
    """

    email = f"crypto-switch-{held_code}-{target_code}@example.com"
    await _signup(test_context, email)
    await _seed_paid_subscription(
        test_context, email, held_code, provider="nowpayments"
    )
    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    # The page: the payment form, not a refusal.
    response = await test_context["client"].get(
        f"/dashboard/billing/checkout?plan_code={target_code}"
    )
    assert response.status_code == 200, response.text
    assert "checkout-confirm-form" in response.text
    assert "You cannot buy this plan on this page" not in response.text

    # The server: the checkout is really created, not refused at the last step.
    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == email)
        )
        assert user is not None
        target = await PlanCatalogService(session).get_or_sync(target_code)
        prepared = await BillingService(session, enabled).prepare_checkout(
            user_id=user.id,
            plan_code=target_code,
            billing_cycle="monthly",
            request_key=f"crypto-holder-buys-{target_code}",
            terms_accepted=True,
            billing_profile={
                "first_name": "Amina",
                "last_name": "Yusuf",
                "address_line1": "1 Market Street",
                "country": "Malaysia",
            },
        )
        assert prepared.attempt.plan_id == target.id


@pytest.mark.parametrize(
    ("held_code", "target_code"),
    [
        (held, target)
        for held in PURCHASABLE_PLAN_CODES
        for target in PURCHASABLE_PLAN_CODES
        if held != target
    ],
)
async def test_a_card_subscription_buys_the_different_plan_at_full_price(
    test_context,
    held_code,
    target_code,
):
    """Card holders use the same full checkout for every different paid plan."""

    email = f"card-switch-{held_code}-{target_code}@example.com"
    await _signup(test_context, email)
    await _seed_paid_subscription(test_context, email, held_code, provider="creem")
    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    response = await test_context["client"].get(
        f"/dashboard/billing/checkout?plan_code={target_code}"
    )
    assert response.status_code == 200, response.text
    assert "checkout-confirm-form" in response.text

    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == email)
        )
        assert user is not None
        prepared = await BillingService(session, enabled).prepare_checkout(
            user_id=user.id,
            plan_code=target_code,
            billing_cycle="monthly",
            request_key=f"card-holder-buys-{target_code}",
            terms_accepted=True,
            billing_profile={
                "first_name": "Amina",
                "last_name": "Yusuf",
                "address_line1": "1 Market Street",
                "country": "Malaysia",
            },
        )
        assert prepared.attempt.amount == effective_monthly_price(target_code)
        assert prepared.attempt.replaces_subscription_id is not None
        assert prepared.attempt.replaces_checkout_attempt_id is not None


async def test_an_old_payment_link_attaches_the_newly_held_plan_without_ending_it(
    test_context,
):
    """The rule is asked again when a saved payment link is opened.

    A checkout can be started while nothing is held, and a card subscription taken by
    some other route before the link is opened again. The link used to send that person
    straight to the payment company, which would charge them for a plan the rule says
    they may not buy. Time passing between the two answers is exactly why the older one
    may not be trusted.
    """

    email = "stale-payment-link@example.com"
    await _signup(test_context, email)
    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == email)
        )
        assert user is not None
        prepared = await BillingService(session, enabled).prepare_checkout(
            user_id=user.id,
            plan_code="pro",
            billing_cycle="monthly",
            request_key="link-made-while-free",
            terms_accepted=True,
            billing_profile={
                "first_name": "Amina",
                "last_name": "Yusuf",
                "address_line1": "1 Market Street",
                "country": "Malaysia",
            },
        )
        attempt = prepared.attempt
        # A link that really would open the payment company's page, so the guard is what
        # stops it rather than the link being unusable for some other reason.
        attempt.status = "pending"
        attempt.provider_session_id = "session-made-while-free"
        attempt.checkout_url = "https://pay.example.com/session-made-while-free"
        attempt_id = attempt.id
        await session.commit()

    # Still free: the link works.
    works = await test_context["client"].get(
        f"/dashboard/billing/checkout/{attempt_id}/resume", follow_redirects=False
    )
    assert works.status_code == 303
    assert works.headers["location"].startswith("https://pay.example.com/")

    # Now a card plan is held. The link may still be used, but merely opening it must not
    # end that plan. The saved links make the later confirmed payment replace it safely.
    await _seed_paid_subscription(test_context, email, "trader", provider="creem")
    resumed = await test_context["client"].get(
        f"/dashboard/billing/checkout/{attempt_id}/resume", follow_redirects=False
    )
    assert resumed.status_code == 303
    assert resumed.headers["location"].startswith("https://pay.example.com/")
    async with test_context["session_factory"]() as session:
        attempt = await session.get(BillingCheckoutAttempt, attempt_id)
        assert attempt is not None
        old = await session.get(Subscription, attempt.replaces_subscription_id)
        assert old is not None
        assert old.status == SubscriptionStatus.ACTIVE
        assert old.canceled_at is None
        assert old.current_period_end is not None
        assert old.current_period_end.replace(tzinfo=UTC) > datetime.now(UTC)


async def test_the_plan_somebody_has_is_the_best_one_they_paid_for(test_context):
    """Two live subscriptions at once: the better plan is the one that counts.

    Buying a second plan while crypto access is still running is now the intended route,
    so an account really can hold two at the same time for a while. Which one they get
    used to be decided by whichever row was written to last - so any later touch on the
    older subscription would have silently taken away the plan they had just paid for.
    """

    email = "two-live-subscriptions@example.com"
    await _signup(test_context, email)
    await _seed_paid_subscription(test_context, email, "trader", provider="nowpayments")

    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == email)
        )
        assert user is not None
        pro = await PlanCatalogService(session).get_or_sync("pro")
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=pro.id,
                status=SubscriptionStatus.ACTIVE,
                provider="nowpayments",
                provider_subscription_id=f"nowpayments-pro-{user.id}",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.commit()

        # The older, smaller subscription is touched last on purpose: under the old rule
        # that alone was enough to hand the person back the plan they had moved off.
        older = await session.scalar(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.provider_subscription_id == f"nowpayments-monitor-{user.id}",
            )
        )
        assert older is not None
        older.cancel_at_period_end = False
        older.updated_at = datetime.now(UTC) + timedelta(minutes=5)
        await session.commit()

        entitlement = await EntitlementService(session).current(user.id)
        assert entitlement.plan.code == "pro"


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


# ---------------------------------------------------------------------------
# Beginner language on the checkout and billing surfaces.
#
# A defect class, not one sentence: the checkout page used to tell a person
# "The payment provider could not open a secure checkout", "No entitlement was
# changed" and "creating a duplicate payment", and printed the stored state
# itself as the heading — "Provider Unavailable". None of those words answer a
# beginner's two questions: what happened to me, and what do I do now. Every
# check below is parametrised over the whole family (every notice state, every
# jargon term, every fixed surface), so a new page in the same shape fails too.
# ---------------------------------------------------------------------------

BEGINNER_JARGON: tuple[str, ...] = ("provider", "entitlement", "duplicate payment")

CHECKOUT_NOTICE_STATES: tuple[str, ...] = (
    "duplicate",
    "billing_terms_required",
    "provider_unavailable",
    "already_subscribed",
    "checkout_expired",
    "plan_not_available",
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: (surface, phrases that must never appear in it, replacements that must).
#:
#: Phrases are checked against the whole file case-insensitively, so each one
#: is deliberately a full customer-facing phrase, not a bare word a code
#: comment or an import name could also contain. Comments and internal keys
#: are allowed to keep their machine words; sentences a person reads are not.
BILLING_COPY_SURFACES: tuple[tuple[Path, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        REPO_ROOT / "src/ai_market_monitor/templates/hilal/dashboard/checkout.html",
        ("payment provider", "no entitlement", "duplicate payment"),
        (
            "payment company did not answer",
            "paying twice for the same thing",
            "nothing was charged and your plan did not change",
        ),
    ),
    (
        REPO_ROOT
        / "src/ai_market_monitor/templates/hilal/dashboard/billing_portal.html",
        (
            "payment provider",
            "provider-managed",
            "provider invoices",
            "provider portal",
        ),
        ("payment company",),
    ),
    (
        REPO_ROOT / "src/ai_market_monitor/templates/hilal/dashboard/billing.html",
        ("selected provider",),
        ("payment method you chose",),
    ),
    (
        REPO_ROOT / "src/ai_market_monitor/api/routers/dashboard.py",
        ('"payment provider unavailable"',),
        ('"we could not reach the payment company"',),
    ),
    (
        # The payment result page the buyer lands on. The pending/failed sentences and
        # the security line used to say "payment provider", "signed confirmation",
        # "No plan or entitlement was changed" and "verified provider webhook".
        REPO_ROOT / "src/ai_market_monitor/templates/billing_result.html",
        (
            "payment provider",
            "provider webhook",
            "signed confirmation",
            "no plan or entitlement",
        ),
        (
            "payment company",
            "payment confirmation",
            "your plan did not change",
        ),
    ),
    (
        # The checkout popup's status line. "selected provider's secure page" named
        # the machine; the payment company's name is what a beginner needs.
        REPO_ROOT / "src/ai_market_monitor/static/hilalmarkets-billing.js",
        ("selected provider's secure page",),
        ("payment company's own secure page",),
    ),
)


def _visible_words(markup: str) -> str:
    """Only what a person actually reads, never the markup around it."""

    document = lxml.html.fromstring(markup)
    for node in document.xpath("//script | //style | //template"):
        node.getparent().remove(node)
    return " ".join(document.text_content().split())


async def _checkout_page_with_state(test_context, email: str, state: str) -> str:
    await _signup(test_context, email)
    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    response = await test_context["client"].get(
        f"/dashboard/billing/checkout?plan_code=trader&state={state}"
    )
    assert response.status_code == 200, response.text
    return response.text


@pytest.mark.parametrize("jargon", BEGINNER_JARGON)
@pytest.mark.parametrize("state", CHECKOUT_NOTICE_STATES)
async def test_no_machine_word_reaches_the_checkout_page(test_context, state, jargon):
    """Every notice the checkout page can show, against every term in the family.

    "Provider", "entitlement" and "duplicate payment" are words from inside the
    machine. A beginner reading them on the page that takes their money learns
    nothing about what happened or what to do.
    """

    page = await _checkout_page_with_state(
        test_context,
        f"checkout-plain-{state}-{jargon.split()[0]}@example.com",
        state,
    )
    words = _visible_words(page)
    assert jargon not in words.casefold(), f"{jargon!r} on the {state} notice"


@pytest.mark.parametrize(
    ("state", "must_say"),
    [
        pytest.param(
            "duplicate",
            "Use it now instead of paying twice for the same thing",
            id="duplicate-says-pay-twice",
        ),
        pytest.param(
            "provider_unavailable",
            "Nothing was charged and your plan did not change. "
            "Please try again in a few minutes",
            id="provider-unavailable-says-retry",
        ),
        pytest.param(
            "checkout_expired",
            "Nothing was charged and your plan did not change. "
            "Check the details above, or go back to Plan and Billing to start again",
            id="expired-says-start-again",
        ),
        pytest.param(
            "plan_not_available",
            "Nothing was charged and your plan did not change. "
            "Check the details above, or go back to Plan and Billing to start again",
            id="plan-not-available-says-start-again",
        ),
    ],
)
async def test_the_checkout_notice_says_what_happened_and_what_to_do(
    test_context,
    state,
    must_say,
):
    """The replacement for each fixed sentence carries both halves: what happened,
    and the action the person can take. And the words pass the same copy rules
    the rest of the product is linted with — `core/copy_rules.py`."""

    page = await _checkout_page_with_state(
        test_context, f"checkout-action-{state}@example.com", state
    )
    words = _visible_words(page)
    assert must_say in words, words
    assert scan_text(words, Path(f"hilal/dashboard/checkout [{state}]")) == ()


@pytest.mark.parametrize(
    ("surface", "banned", "carried"),
    BILLING_COPY_SURFACES,
    ids=(surface.name for surface, _, _ in BILLING_COPY_SURFACES),
)
def test_customer_billing_surfaces_carry_no_engineer_speak(surface, banned, carried):
    """The same sweep over every surface fixed for this defect class, so a later
    edit can quietly bring one phrase back without any test noticing."""

    text = surface.read_text(encoding="utf-8").casefold()
    for phrase in banned:
        assert phrase not in text, f"{phrase!r} back in {surface.name}"
    for phrase in carried:
        assert phrase in text, f"{phrase!r} missing from {surface.name}"


# ── What a plan's limits are called ──────────────────────────────────────────


def _limit_tile_names(page: str) -> list[str]:
    document = lxml.html.fromstring(page)
    return [
        name.text_content().strip()
        for name in document.xpath(
            '//div[contains(@class,"checkout-limit-grid")]/article/span[last()]'
        )
    ]


@pytest.mark.parametrize("plan_code", PURCHASABLE_PLAN_CODES)
async def test_the_checkout_names_each_limit_as_every_other_page_does(test_context, plan_code):
    """The review page's limit tiles had their own names and called a plan's monitors
    "Active Watchlists". A Watchlist is a saved list of coins, a different thing. The
    names come from `PLAN_LIMIT_WORDS`, which the subscription page and the receipt use.
    """

    await _signup(test_context, f"limit-words-{plan_code}@example.com")
    enabled = test_context["settings"].model_copy(update={"billing_enabled": True})
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    response = await test_context["client"].get(
        f"/dashboard/billing/checkout?plan_code={plan_code}"
    )
    assert response.status_code == 200, response.text

    names = _limit_tile_names(response.text)
    assert names[:3] == [
        PLAN_LIMIT_WORDS["active_strategies"],
        PLAN_LIMIT_WORDS["on_demand_scans_per_month"],
        PLAN_LIMIT_WORDS["detailed_history_days"],
    ]
    assert not [name for name in names if "watchlist" in name.casefold()], names


@pytest.mark.parametrize("plan_code", PURCHASABLE_PLAN_CODES)
async def test_the_receipt_names_each_limit_as_every_other_page_does(test_context, plan_code):
    """The receipt's list of limits had its own names too: "Active Watchlists" and
    "Markets per Watchlist" for a plan's monitors. Read from the email really sent after
    a verified payment, in both of its parts."""

    await _signup(test_context, f"receipt-words-{plan_code}@example.com")
    form = await _review_form(test_context, plan_code)
    checkout = await test_context["client"].post(
        "/dashboard/billing/checkout",
        data=form,
        follow_redirects=False,
    )
    assert checkout.status_code == 303
    parsed = urlsplit(checkout.headers["location"])
    paid = await test_context["client"].get(f"{parsed.path}?{parsed.query}")
    assert paid.status_code == 200

    (receipt,) = [
        row
        for row in test_context["settings"].email_test_outbox
        if row.get("purpose") == "payment_success"
    ]
    limits = PLAN_DEFINITIONS[plan_code].limits
    expected = [PLAN_LIMIT_WORDS[key] for key in RECEIPT_LIMIT_KEYS if key in limits]
    assert expected, "a rule that matches nothing passes for the wrong reason"

    block = receipt["body"].split("Your main limits:\n", 1)[1].split("\n\n", 1)[0]
    named = [
        line[2:].split(": ", 1)[0] for line in block.splitlines() if line.startswith("- ")
    ]
    assert named == expected, block
    assert "watchlist" not in block.casefold(), block
    for name in expected:
        assert name in receipt["html_body"], name
    for retired in ("Active Watchlists", "Markets per Watchlist"):
        assert retired not in receipt["html_body"]


async def _refund_a_nowpayments_checkout(test_context, email: str) -> tuple:
    """One crypto checkout whose money has come back, owned by the person using the browser.

    The ``refunded`` word is written by the product's own refund reader
    (``BillingService.process_event``), not set by hand, so the route is aimed at exactly
    the record a real refund leaves: money returned, no plan named to end, so the plan is
    untouched and the payment was never ``completed`` in this shape. Returns the attempt
    id (for the browser routes) and the user id (for the guard replay).
    """

    await _signup(test_context, email)
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        user = await session.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.normalized_identifier == email)
        )
        assert user is not None
        plan = await PlanCatalogService(session).get_or_sync("trader")
        attempt = BillingCheckoutAttempt(
            user_id=user.id,
            plan_id=plan.id,
            billing_cycle="one_time_30_day",
            provider="nowpayments",
            status="pending",
            idempotency_key=f"refund-cancel-{user.id}",
            terms_version="test",
            amount=effective_monthly_price("trader"),
            currency="USD",
            terms_accepted_at=now,
            expires_at=now + timedelta(days=1),
            billing_profile={"first_name": "Amina"},
        )
        session.add(attempt)
        await session.commit()
        attempt_id = attempt.id
        user_id = user.id

    async with test_context["session_factory"]() as session:
        await BillingService(session, test_context["settings"]).process_event(
            provider="nowpayments",
            payload={
                "id": f"evt-refund-{attempt_id}",
                "type": "payment.refunded",
                "data": {
                    "checkout_attempt_id": str(attempt_id),
                    "user_id": str(user_id),
                    "status": "refunded",
                },
            },
        )
        await session.commit()

    # Sanity on the starting shape: the refund reader really did record it, and this
    # payment was never settled, so ``completed_at`` has nothing to hold.
    async with test_context["session_factory"]() as session:
        refunded = await session.get(BillingCheckoutAttempt, attempt_id)
        assert refunded is not None
        assert refunded.status == "refunded"
        assert refunded.completed_at is None
    return attempt_id, user_id


async def test_the_billing_cancel_route_never_erases_a_recorded_refund(test_context) -> None:
    """Opening the old cancel link after a refund must not overwrite the refund.

    ``GET /billing/cancel`` used to move any attempt that was not
    completed/failed/expired to ``cancelled``. ``refunded`` was not in that set, so a
    customer revisiting the cancel URL their crypto checkout was created with rewrote
    ``refunded`` to ``cancelled`` — erasing the refund record and, worse, switching off
    the NOWPayments double-grant guard (which keys on ``refunded``) so a re-delivered
    ``payment.finished`` counted the returned money as kept again.
    """

    attempt_id, user_id = await _refund_a_nowpayments_checkout(
        test_context, "cancel-keeps-refund@example.com"
    )

    cancelled = await test_context["client"].get(f"/billing/cancel?attempt={attempt_id}")
    assert cancelled.status_code == 200, cancelled.text

    async with test_context["session_factory"]() as session:
        attempt = await session.get(BillingCheckoutAttempt, attempt_id)
        assert attempt is not None
        assert attempt.status == "refunded", (
            "the cancel route overwrote a recorded refund with 'cancelled'"
        )
        assert attempt.completed_at is None

    # With the refund record intact, the double-grant guard still refuses to settle the
    # same money a second time when NOWPayments re-delivers its success event.
    async with test_context["session_factory"]() as session:
        with pytest.raises(BillingError) as refused:
            await BillingService(session, test_context["settings"]).process_event(
                provider="nowpayments",
                payload={
                    "id": f"evt-finished-after-cancel-{attempt_id}",
                    "type": "payment.finished",
                    "data": {
                        "checkout_attempt_id": str(attempt_id),
                        "user_id": str(user_id),
                        "plan_code": "trader",
                        "status": "active",
                        "amount": str(effective_monthly_price("trader")),
                        "currency": "USD",
                    },
                },
            )
        assert refused.value.code == "checkout_already_completed"

    async with test_context["session_factory"]() as session:
        after_replay = await session.get(BillingCheckoutAttempt, attempt_id)
        assert after_replay is not None
        assert after_replay.status == "refunded"
        assert after_replay.completed_at is None


async def test_the_billing_result_page_says_refunded_for_a_refunded_checkout(
    test_context,
) -> None:
    """A returned payment must not read as a pending or failed checkout on the result page.

    The post-payment ``state_content`` map had no ``refunded`` entry, so a refunded
    checkout fell back to "Payment confirmation pending" — and, being outside the
    completed/pending/processing set, was rendered in the error tone. The honest word is
    "Refunded", and it must not read as an error.
    """

    attempt_id, _ = await _refund_a_nowpayments_checkout(
        test_context, "success-says-refunded@example.com"
    )

    page = await test_context["client"].get(f"/billing/success?attempt={attempt_id}")
    assert page.status_code == 200, page.text
    words = _visible_words(page.text)
    assert "Refunded" in words
    assert "Payment confirmation pending" not in words
    assert "The checkout did not complete" not in words
    # The body paragraph must be the refund's own honest words, not the generic
    # "came back from the payment company" paragraph. That paragraph says the money
    # is still to come; for a refund the money already went back, so on this page
    # it is simply untrue.
    assert "The money went back to you. Your plan did not continue." in words
    assert "we have not received its payment confirmation" not in words
    # The mark at the top must not carry the pending (clock) or error (alert) tone:
    # nothing is still arriving, and nothing failed. A refund keeps the plain base
    # mark with the "went back" icon.
    mark = re.search(r'class="(billing-result-mark[^"]*)"', page.text)
    assert mark is not None
    assert mark.group(1) == "billing-result-mark"
    mark_icon = re.search(
        r'data-icon="([a-z0-9_]+)" data-icon-class="icon-lg"', page.text
    )
    assert mark_icon is not None
    assert mark_icon.group(1) not in {"clock", "alert"}
    # Beginner-language rule: the copy lint stays clean on the page the buyer lands on.
    assert scan_text(words, Path("billing_result [refunded]")) == ()
