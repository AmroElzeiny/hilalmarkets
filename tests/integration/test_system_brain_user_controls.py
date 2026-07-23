import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.security import hash_password
from ai_market_monitor.db.models import (
    AccountAdminAction,
    AccountBan,
    AccountEmailDelivery,
    AuditEvent,
    Plan,
    Subscription,
    Trial,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    IdentityProvider,
    SubscriptionStatus,
    UserRole,
    UserStatus,
)
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService


async def _account(
    test_context,
    *,
    email: str,
    role: UserRole = UserRole.USER,
) -> User:
    async with test_context["session_factory"]() as session:
        user = User(
            display_name=email.split("@", 1)[0].replace(".", " ").title(),
            role=role,
        )
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


async def _user_page(test_context, admin: User):
    response = await test_context["client"].get(
        "/dashboard/system-brain/users",
        headers={"X-User-ID": str(admin.id)},
    )
    csrf = re.search(r'name="csrf_token" value="([a-f0-9]+)"', response.text)
    assert response.status_code == 200
    assert csrf is not None
    return response, csrf.group(1)


async def test_system_brain_user_registry_has_bounded_controls_and_custom_dialog(
    test_context,
):
    admin = await _account(
        test_context,
        email="user-admin@hilalmarkets.test",
        role=UserRole.ADMIN,
    )
    customer = await _account(
        test_context,
        email="customer-controls@example.com",
    )

    response, _csrf = await _user_page(test_context, admin)

    assert 'href="/dashboard/system-brain/users"' in response.text
    assert 'data-testid="system-brain-user-list"' in response.text
    assert f'data-user-id="{customer.id}"' in response.text
    assert "Free plan" in response.text
    assert "Full access" in response.text
    assert "Lifetime partner" in response.text
    assert 'data-user-action-dialog' in response.text
    assert 'data-user-admin-form' in response.text
    assert "blocked from every future signup" in response.text
    assert "window.confirm" not in response.text

    denied = await test_context["client"].get(
        "/dashboard/system-brain/users",
        headers={"X-User-ID": str(customer.id)},
    )
    assert denied.status_code == 403


@pytest.mark.parametrize(
    ("tier", "months", "plan_code", "expected_status", "expected_days"),
    [
        ("free", "", "pro_trial", SubscriptionStatus.TRIALING, 14),
        ("full_access", "3", "full_access", SubscriptionStatus.ACTIVE, None),
        (
            "lifetime_partner",
            "",
            "lifetime_partner",
            SubscriptionStatus.ACTIVE,
            None,
        ),
    ],
)
async def test_system_brain_applies_real_access_and_branded_email_once(
    test_context,
    tier,
    months,
    plan_code,
    expected_status,
    expected_days,
):
    admin = await _account(
        test_context,
        email=f"admin-{tier}@hilalmarkets.test",
        role=UserRole.ADMIN,
    )
    customer = await _account(
        test_context,
        email=f"customer-{tier}@example.com",
    )
    _page, csrf = await _user_page(test_context, admin)
    action_key = str(uuid4())
    payload = {
        "csrf_token": csrf,
        "idempotency_key": action_key,
        "tier": tier,
        "months": months,
        "reason": f"Private beta access assignment for {tier}.",
    }
    settings = test_context["settings"]
    settings.email_test_outbox.clear()

    applied = await test_context["client"].post(
        f"/dashboard/system-brain/users/{customer.id}/access",
        data=payload,
        headers={"X-User-ID": str(admin.id)},
        follow_redirects=False,
    )
    replayed = await test_context["client"].post(
        f"/dashboard/system-brain/users/{customer.id}/access",
        data=payload,
        headers={"X-User-ID": str(admin.id)},
        follow_redirects=False,
    )

    assert applied.status_code == 303
    assert "success=" in applied.headers["location"]
    assert replayed.status_code == 303
    assert "no+duplicate+was+sent" in replayed.headers["location"]
    async with test_context["session_factory"]() as session:
        subscription = await session.scalar(
            select(Subscription)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Subscription.user_id == customer.id,
                Plan.code == plan_code,
            )
        )
        entitlement = await EntitlementService(session).current(customer.id)
        actions = await session.scalar(
            select(func.count(AccountAdminAction.id)).where(
                AccountAdminAction.idempotency_key == action_key
            )
        )
        deliveries = await session.scalar(
            select(func.count(AccountEmailDelivery.id)).where(
                AccountEmailDelivery.user_id == customer.id
            )
        )
        audit_events = await session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.target_id == str(customer.id),
                AuditEvent.action == f"account.apply_{tier}",
            )
        )
        trial = await session.scalar(select(Trial).where(Trial.user_id == customer.id))

    assert subscription is not None
    assert subscription.status == expected_status
    assert entitlement.plan.code == plan_code
    assert entitlement.feature_enabled("whatsapp") is False
    assert actions == 1
    assert deliveries == 1
    assert audit_events == 1
    assert trial is None
    assert len(settings.email_test_outbox) == 1
    email = settings.email_test_outbox[0]
    assert email["purpose"] == "account_access_changed"
    assert 'data-hm-email-shell="true"' in email["html_body"]
    assert "#cbfa4d" in email["html_body"]
    assert "Your HilalMarkets access is now" in email["subject"]
    if expected_days is not None:
        assert subscription.current_period_end is not None
        period_end = subscription.current_period_end
        if period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=UTC)
        remaining = period_end - datetime.now(UTC)
        assert timedelta(days=expected_days - 1) < remaining <= timedelta(
            days=expected_days
        )
        assert subscription.cancel_at_period_end is True
    elif tier == "full_access":
        assert subscription.current_period_end is not None
        assert subscription.cancel_at_period_end is True
    else:
        assert subscription.current_period_end is None
        assert subscription.cancel_at_period_end is False
        assert "Lifetime" in email["html_body"]


async def test_ban_blocks_signup_login_code_and_password_with_exact_message(
    test_context,
):
    admin = await _account(
        test_context,
        email="ban-admin@hilalmarkets.test",
        role=UserRole.ADMIN,
    )
    customer = await _account(
        test_context,
        email="banned-customer@example.com",
    )
    _page, csrf = await _user_page(test_context, admin)

    banned = await test_context["client"].post(
        f"/dashboard/system-brain/users/{customer.id}/ban",
        data={
            "csrf_token": csrf,
            "idempotency_key": str(uuid4()),
            "reason": "Repeated abuse of the private beta access policy.",
        },
        headers={"X-User-ID": str(admin.id)},
        follow_redirects=False,
    )
    signup = await test_context["client"].post(
        "/signup",
        data={
            "first_name": "Banned",
            "last_name": "Customer",
            "email": "banned-customer@example.com",
            "password": "Valid1!",
            "repeat_password": "Valid1!",
        },
        follow_redirects=False,
    )
    signin = await test_context["client"].post(
        "/signin",
        data={
            "email": "banned-customer@example.com",
            "password": "Valid1!",
        },
        follow_redirects=False,
    )
    code = await test_context["client"].post(
        "/signin/code/request",
        data={"email": "banned-customer@example.com"},
        follow_redirects=False,
    )

    assert banned.status_code == 303
    assert "error=account_banned" in signup.headers["location"]
    assert "error=account_banned" in signin.headers["location"]
    assert "error=account_banned" in code.headers["location"]
    message = await test_context["client"].get("/signin?error=account_banned")
    assert "Your profile is banned." in message.text
    async with test_context["session_factory"]() as session:
        stored_user = await session.get(User, customer.id)
        ban_count = await session.scalar(select(func.count(AccountBan.id)))
    assert stored_user is not None
    assert stored_user.status == UserStatus.SUSPENDED
    assert ban_count == 1


async def test_delete_anonymizes_profile_and_releases_email_for_new_signup(
    test_context,
):
    admin = await _account(
        test_context,
        email="delete-admin@hilalmarkets.test",
        role=UserRole.ADMIN,
    )
    customer = await _account(
        test_context,
        email="reusable-after-delete@example.com",
    )
    _page, csrf = await _user_page(test_context, admin)
    access_applied = await test_context["client"].post(
        f"/dashboard/system-brain/users/{customer.id}/access",
        data={
            "csrf_token": csrf,
            "idempotency_key": str(uuid4()),
            "tier": "free",
            "months": "",
            "reason": "Temporary private beta access before profile deletion.",
        },
        headers={"X-User-ID": str(admin.id)},
        follow_redirects=False,
    )

    deleted = await test_context["client"].post(
        f"/dashboard/system-brain/users/{customer.id}/delete",
        data={
            "csrf_token": csrf,
            "idempotency_key": str(uuid4()),
            "reason": "Customer requested complete profile deletion.",
        },
        headers={"X-User-ID": str(admin.id)},
        follow_redirects=False,
    )
    signup = await test_context["client"].post(
        "/signup",
        data={
            "first_name": "New",
            "last_name": "Profile",
            "email": "reusable-after-delete@example.com",
            "password": "Valid1!",
            "repeat_password": "Valid1!",
        },
        follow_redirects=False,
    )
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signup/verify",
        data={
            "email": "reusable-after-delete@example.com",
            "code": code,
        },
        follow_redirects=False,
    )

    assert access_applied.status_code == 303
    assert deleted.status_code == 303
    assert signup.status_code == 303
    assert signup.headers["location"].startswith("/signup/verify?")
    assert verified.status_code == 303
    async with test_context["session_factory"]() as session:
        old_user = await session.get(User, customer.id)
        old_identity = await session.scalar(
            select(UserIdentity).where(UserIdentity.user_id == customer.id)
        )
        new_identity = await session.scalar(
            select(UserIdentity).where(
                UserIdentity.normalized_identifier
                == "reusable-after-delete@example.com"
            )
        )
        deletion_audit = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.target_id == str(customer.id),
                AuditEvent.action == "account.delete_profile",
            )
        )
        access_delivery = await session.scalar(
            select(AccountEmailDelivery).where(
                AccountEmailDelivery.admin_action_id.is_not(None),
            )
        )
    assert old_user is not None and old_user.status == UserStatus.DELETED
    assert old_identity is not None
    assert old_identity.normalized_identifier is None
    assert old_identity.password_hash is None
    assert new_identity is not None and new_identity.user_id != customer.id
    assert deletion_audit is not None
    assert access_delivery is not None
    assert access_delivery.user_id is None
    assert access_delivery.recipient.endswith("@invalid.local")
    assert access_delivery.payload_redacted == {}


async def test_provider_managed_subscription_blocks_destructive_local_replacement(
    test_context,
):
    admin = await _account(
        test_context,
        email="billing-admin@hilalmarkets.test",
        role=UserRole.ADMIN,
    )
    customer = await _account(
        test_context,
        email="provider-paid@example.com",
    )
    async with test_context["session_factory"]() as session:
        plan = await PlanCatalogService(session).get_or_sync("trader")
        session.add(
            Subscription(
                user_id=customer.id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider="stripe",
                provider_subscription_id=f"sub_{uuid4().hex}",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
                cancel_at_period_end=False,
            )
        )
        await session.commit()
    _page, csrf = await _user_page(test_context, admin)

    response = await test_context["client"].post(
        f"/dashboard/system-brain/users/{customer.id}/delete",
        data={
            "csrf_token": csrf,
            "idempotency_key": str(uuid4()),
            "reason": "Attempted profile deletion during active billing.",
        },
        headers={"X-User-ID": str(admin.id)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    async with test_context["session_factory"]() as session:
        stored = await session.get(User, customer.id)
        identity = await session.scalar(
            select(UserIdentity).where(UserIdentity.user_id == customer.id)
        )
    assert stored is not None and stored.status == UserStatus.ACTIVE
    assert identity is not None
    assert identity.normalized_identifier == "provider-paid@example.com"
