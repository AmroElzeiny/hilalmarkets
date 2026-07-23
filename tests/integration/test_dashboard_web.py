from datetime import UTC, datetime

from pydantic import SecretStr
from sqlalchemy import select

from ai_market_monitor.core.security import hash_password
from ai_market_monitor.db.models import (
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    ComplianceDriftNotification,
    DashboardPreference,
    DisclaimerAcceptance,
    PendingEmailSignup,
    TelegramConnection,
    TelegramConversationState,
    TelegramDashboardLink,
    Trial,
    User,
    UserIdentity,
    WebSession,
)
from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    ConnectionStatus,
    IdentityProvider,
    ShariaAssetStatus,
)
from ai_market_monitor.services.admin_notifications import AdminNotificationService
from ai_market_monitor.services.telegram_account_links import TelegramAccountLinkService
from ai_market_monitor.telegram.adapter import TelegramDeliveryResult


async def _signup_and_verify(
    test_context,
    *,
    email: str,
    password: str = "CorrectHorse123!",
    repeat_password: str | None = None,
    telegram_link: str | None = None,
):
    data = {
        "email": email,
        "password": password,
        "repeat_password": repeat_password or password,
    }
    if telegram_link:
        data["telegram_link"] = telegram_link
    requested = await test_context["client"].post(
        "/signup",
        data=data,
        follow_redirects=False,
    )
    assert requested.status_code == 303
    assert requested.headers["location"].startswith("/signup/verify")
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verify_data = {"email": email, "code": code}
    if telegram_link:
        verify_data["telegram_link"] = telegram_link
    verified = await test_context["client"].post(
        "/signup/verify",
        data=verify_data,
        follow_redirects=False,
    )
    return requested, verified


async def test_signup_creates_user_session_and_dashboard_access(test_context):
    requested = await test_context["client"].post(
        "/signup",
        data={
            "email": "Trader@example.com",
            "first_name": "Amal",
            "last_name": "Trader",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert requested.status_code == 303
    assert requested.headers["location"].startswith("/signup/verify")
    assert "amm_session=" not in requested.headers.get("set-cookie", "")
    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(User)) is None
        assert await session.scalar(select(PendingEmailSignup)) is not None

    code = test_context["settings"].email_test_outbox[-1]["code"]
    response = await test_context["client"].post(
        "/signup/verify",
        data={"email": "Trader@example.com", "code": code},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/dashboard")
    assert "amm_session=" in response.headers["set-cookie"]

    dashboard = await test_context["client"].get("/dashboard")
    assert dashboard.status_code == 200
    assert "Create your first Watchlist" in dashboard.text
    assert "Your next useful action" in dashboard.text
    assert "Coverage score" not in dashboard.text
    assert 'class="dashboard-body hilal-dashboard theme-' in dashboard.text

    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        assert user is not None
        assert user.display_name == "Amal Trader"
        identity = await session.scalar(select(UserIdentity))
        assert identity.password_hash
        assert identity.profile_data["first_name"] == "Amal"
        assert identity.profile_data["last_name"] == "Trader"
        assert await session.scalar(select(WebSession)) is not None
        assert await session.scalar(select(DisclaimerAcceptance)) is None


async def test_dashboard_uses_account_locale_and_only_reports_active_telegram(test_context):
    await _signup_and_verify(test_context, email="rtl-dashboard@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        assert user is not None
        user.locale = "ar"
        session.add(
            TelegramConnection(
                user_id=user.id,
                telegram_user_id="rtl-pending-telegram",
                status=ConnectionStatus.PENDING,
                alerts_enabled=True,
            )
        )
        await session.commit()

    pending = await test_context["client"].get("/dashboard")
    assert pending.status_code == 200
    assert '<html lang="ar" dir="rtl">' in pending.text
    assert ">Set up<" in pending.text
    assert "<small>Not connected</small>" not in pending.text

    async with test_context["session_factory"]() as session:
        connection = await session.scalar(select(TelegramConnection))
        assert connection is not None
        connection.status = ConnectionStatus.ACTIVE
        connection.alerts_enabled = True
        await session.commit()

    active = await test_context["client"].get("/dashboard")
    assert active.status_code == 200
    assert ">Ready<" in active.text
    assert "<small>Connected</small>" not in active.text


async def test_signup_verification_sends_admin_notification(test_context, monkeypatch):
    sent = []

    async def fake_signup_notice(self, *, user_id, email, source):
        sent.append({"user_id": user_id, "email": email, "source": source})

    monkeypatch.setattr(
        AdminNotificationService,
        "send_signup_created",
        fake_signup_notice,
    )

    _, response = await _signup_and_verify(
        test_context,
        email="admin-notified@example.com",
    )

    assert response.status_code == 303
    assert len(sent) == 1
    assert sent[0]["email"] == "admin-notified@example.com"
    assert sent[0]["source"] == "dashboard"


async def test_repeated_signup_submit_does_not_send_second_code(test_context):
    test_context["settings"].email_test_outbox.clear()
    payload = {
        "email": "double-submit@example.com",
        "password": "CorrectHorse123!",
        "repeat_password": "CorrectHorse123!",
    }

    first = await test_context["client"].post(
        "/signup",
        data=payload,
        follow_redirects=False,
    )
    assert first.status_code == 303
    assert first.headers["location"].startswith("/signup/verify")
    assert len(test_context["settings"].email_test_outbox) == 1
    first_code = test_context["settings"].email_test_outbox[0]["code"]

    second = await test_context["client"].post(
        "/signup",
        data=payload,
        follow_redirects=False,
    )

    assert second.status_code == 303
    assert second.headers["location"].startswith("/signup/verify")
    assert len(test_context["settings"].email_test_outbox) == 1
    assert test_context["settings"].email_test_outbox[0]["code"] == first_code


async def test_signup_does_not_require_disclaimer_acceptance(test_context):
    _, response = await _signup_and_verify(
        test_context,
        email="trader@example.com",
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/dashboard")


async def test_consolidated_market_and_notification_pages_use_only_persisted_user_records(
    test_context,
):
    email = "hilal-pages@example.com"
    _, verified = await _signup_and_verify(test_context, email=email)
    assert verified.status_code == 303
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User).where(User.display_name == "hilal-pages"))
        assert user is not None
        watchlist = ApprovedWatchlist(
            user_id=user.id,
            name="My screened assets",
            is_default=True,
        )
        session.add(watchlist)
        await session.flush()
        session.add(
            ApprovedWatchlistAsset(
                watchlist_id=watchlist.id,
                canonical_asset="SOL",
                added_at=datetime.now(UTC),
            )
        )
        session.add(
            ComplianceDriftNotification(
                user_id=user.id,
                canonical_asset="SOL",
                previous_status=ShariaAssetStatus.ELIGIBLE,
                new_status=ShariaAssetStatus.UNDER_REVIEW,
                behavior=ComplianceChangeBehavior.NOTIFY_ONLY,
                impact={"reason": "source_updated"},
                idempotency_key="hilal-pages-sol-change",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    watchlist_redirect = await test_context["client"].get("/dashboard/watchlist")
    assert watchlist_redirect.status_code == 303
    assert watchlist_redirect.headers["location"] == "/dashboard/market?saved_assets=1"
    watchlist_page = await test_context["client"].get(watchlist_redirect.headers["location"])
    assert watchlist_page.status_code == 200
    assert "Saved Assets" in watchlist_page.text
    assert "SOL" in watchlist_page.text
    assert "Your saved asset passports will appear here." not in watchlist_page.text

    compliance_redirect = await test_context["client"].get("/dashboard/compliance")
    assert compliance_redirect.status_code == 303
    assert compliance_redirect.headers["location"] == (
        "/dashboard/activity?tab=compliance_changes"
    )
    compliance_page = await test_context["client"].get(compliance_redirect.headers["location"])
    assert compliance_page.status_code == 200
    assert "Recent updates" in compliance_page.text
    assert "Screening changes" in compliance_page.text
    assert "SOL" in compliance_page.text


async def test_signin_success_and_failure(test_context):
    failed = await test_context["client"].post(
        "/signin",
        data={"email": "missing@example.com", "password": "CorrectHorse123!"},
        follow_redirects=False,
    )
    assert failed.status_code == 303
    assert failed.headers["location"] == "/signin?error=invalid_login"

    await _signup_and_verify(test_context, email="signin@example.com")
    wrong = await test_context["client"].post(
        "/signin",
        data={"email": "signin@example.com", "password": "WrongHorse123"},
        follow_redirects=False,
    )
    assert wrong.status_code == 303
    assert wrong.headers["location"] == "/signin?error=invalid_login"
    success = await test_context["client"].post(
        "/signin",
        data={"email": "signin@example.com", "password": "CorrectHorse123!"},
        follow_redirects=False,
    )
    assert success.status_code == 303
    assert success.headers["location"] == "/dashboard?message=login_successful"


async def test_signup_with_telegram_link_connects_dashboard_account(test_context):
    async with test_context["session_factory"]() as session:
        telegram_user = User(display_name="Telegram shell")
        session.add(telegram_user)
        await session.flush()
        session.add(
            TelegramConnection(
                user_id=telegram_user.id,
                telegram_user_id="tg-link",
                chat_id="chat-link",
                status=ConnectionStatus.ACTIVE,
            )
        )
        session.add(
            TelegramConversationState(
                user_id=telegram_user.id,
                telegram_user_id="tg-link",
                chat_id="chat-link",
                flow="onboarding",
                step="idle",
                state_data={},
                correlation_id="corr-link",
            )
        )
        url = await TelegramAccountLinkService(session, test_context["settings"]).create(
            user_id=telegram_user.id,
            telegram_user_id="tg-link",
            target="signup",
        )
        await session.commit()

    token = url.split("telegram_link=", 1)[1]
    _, response = await _signup_and_verify(
        test_context,
        email="linked@example.com",
        telegram_link=token,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard?message=telegram_connected"

    async with test_context["session_factory"]() as session:
        users = (await session.scalars(select(User))).all()
        dashboard_user = next(user for user in users if user.display_name == "linked")
        connection = await session.scalar(select(TelegramConnection))
        conversation = await session.scalar(select(TelegramConversationState))
        assert connection.user_id == dashboard_user.id
        assert conversation.user_id == dashboard_user.id
        assert conversation.state_data["dashboard_linked_at"]


async def test_signup_with_telegram_link_sends_connected_notification(test_context, monkeypatch):
    delivered = []
    settings = test_context["settings"]
    settings.telegram_adapter = "http"
    settings.telegram_bot_token = SecretStr("telegram-token")

    async def fake_deliver(self, message):
        delivered.append(message)
        return TelegramDeliveryResult(message_ids=["sent-linked"])

    monkeypatch.setattr(
        "ai_market_monitor.api.routers.dashboard.TelegramHttpAdapter.deliver",
        fake_deliver,
    )

    async with test_context["session_factory"]() as session:
        telegram_user = User(display_name="Telegram shell")
        session.add(telegram_user)
        await session.flush()
        session.add(
            TelegramConnection(
                user_id=telegram_user.id,
                telegram_user_id="tg-notify",
                chat_id="chat-notify",
                status=ConnectionStatus.ACTIVE,
            )
        )
        session.add(
            TelegramConversationState(
                user_id=telegram_user.id,
                telegram_user_id="tg-notify",
                chat_id="chat-notify",
                flow="onboarding",
                step="idle",
                state_data={},
                correlation_id="corr-notify",
            )
        )
        url = await TelegramAccountLinkService(session, settings).create(
            user_id=telegram_user.id,
            telegram_user_id="tg-notify",
            target="signin",
        )
        dashboard_user = User(display_name="Existing")
        session.add(dashboard_user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=dashboard_user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject="existing@example.com",
                normalized_identifier="existing@example.com",
                display_identifier="existing@example.com",
                password_hash=hash_password("CorrectHorse123!"),
                is_verified=True,
            )
        )
        await session.commit()

    token = url.split("telegram_link=", 1)[1]
    response = await test_context["client"].post(
        "/signin",
        data={
            "email": "existing@example.com",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
            "telegram_link": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard?message=telegram_connected"
    assert len(delivered) == 1
    assert delivered[0].chat_id == "chat-notify"
    assert "Dashboard account connected" in delivered[0].text


async def test_trial_claim_from_dashboard_blocks_duplicate_claim(test_context):
    await _signup_and_verify(test_context, email="trial@example.com")
    first = await test_context["client"].post("/dashboard/trial/claim", follow_redirects=False)
    second = await test_context["client"].post("/dashboard/trial/claim", follow_redirects=False)
    assert first.headers["location"] == "/dashboard/billing?message=trial_claimed"
    assert second.headers["location"] == "/dashboard/billing?message=trial_claimed"
    async with test_context["session_factory"]() as session:
        trials = (await session.scalars(select(Trial))).all()
        assert len(trials) == 1


async def test_private_beta_billing_page_blocks_paid_checkout(test_context):
    await _signup_and_verify(test_context, email="billing@example.com")
    page = await test_context["client"].get("/dashboard/billing")
    assert page.status_code == 200
    assert "Subscription and Billing" in page.text
    assert "Paid billing is disabled" in page.text
    review = await test_context["client"].get(
        "/dashboard/billing/checkout?plan_code=trader",
        follow_redirects=False,
    )
    assert review.status_code == 303
    assert review.headers["location"] == "/dashboard/billing?error=billing_disabled"


async def test_dashboard_settings_timezone_dropdown_persists(test_context):
    await _signup_and_verify(test_context, email="timezone@example.com")

    page = await test_context["client"].get("/dashboard/settings")
    assert page.status_code == 200
    assert "Europe/Moscow" in page.text
    assert 'name="theme"' not in page.text
    assert "data-settings-save" in page.text

    response = await test_context["client"].post(
        "/dashboard/settings",
        data={"timezone": "Europe/Moscow"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/settings?message=settings_saved"

    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        preference = await session.scalar(select(DashboardPreference))
        assert user.timezone == "Europe/Moscow"
        assert preference.default_timezone == "Europe/Moscow"
        assert preference.notification_preferences["timezone"] == "Europe/Moscow"


async def test_signup_password_confirmation_and_complexity_are_enforced(test_context):
    mismatch = await test_context["client"].post(
        "/signup",
        data={
            "email": "mismatch@example.com",
            "password": "CorrectHorse123!",
            "repeat_password": "DifferentHorse123!",
        },
        follow_redirects=False,
    )
    assert mismatch.headers["location"] == "/signup?error=password_mismatch"

    weak = await test_context["client"].post(
        "/signup",
        data={
            "email": "weak@example.com",
            "password": "lowercase",
            "repeat_password": "lowercase",
        },
        follow_redirects=False,
    )
    assert weak.headers["location"] == "/signup?error=invalid_password"


async def test_email_code_login_and_password_reset(test_context):
    await _signup_and_verify(test_context, email="code-login@example.com")
    await test_context["client"].post("/logout")

    requested = await test_context["client"].post(
        "/signin/code/request",
        data={"email": "code-login@example.com"},
        follow_redirects=False,
    )
    assert requested.status_code == 303
    login_code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signin/code/verify",
        data={"email": "code-login@example.com", "code": login_code},
        follow_redirects=False,
    )
    assert verified.headers["location"] == "/dashboard?message=login_successful"
    assert "amm_session=" in verified.headers["set-cookie"]

    await test_context["client"].post("/logout")
    reset_requested = await test_context["client"].post(
        "/reset-password/request",
        data={"email": "code-login@example.com"},
        follow_redirects=False,
    )
    assert reset_requested.status_code == 303
    reset_code = test_context["settings"].email_test_outbox[-1]["code"]
    reset = await test_context["client"].post(
        "/reset-password/verify",
        data={
            "email": "code-login@example.com",
            "code": reset_code,
            "password": "NewPassword7!",
            "repeat_password": "NewPassword7!",
        },
        follow_redirects=False,
    )
    assert reset.headers["location"] == "/signin?message=password_reset_successful"

    old_password = await test_context["client"].post(
        "/signin",
        data={"email": "code-login@example.com", "password": "CorrectHorse123!"},
        follow_redirects=False,
    )
    assert old_password.headers["location"] == "/signin?error=invalid_login"
    new_password = await test_context["client"].post(
        "/signin",
        data={"email": "code-login@example.com", "password": "NewPassword7!"},
        follow_redirects=False,
    )
    assert new_password.headers["location"] == "/dashboard?message=login_successful"


async def test_reset_password_unknown_email_shows_not_registered(test_context):
    test_context["settings"].email_test_outbox.clear()
    requested = await test_context["client"].post(
        "/reset-password/request",
        data={"email": "missing-reset@example.com"},
        follow_redirects=False,
    )

    assert requested.status_code == 303
    assert requested.headers["location"] == "/reset-password?error=account_not_registered"
    assert test_context["settings"].email_test_outbox == []
    page = await test_context["client"].get(requested.headers["location"])
    assert page.status_code == 200
    assert "This email is not registered. Please sign up first." in page.text


async def test_integrations_telegram_link_opens_new_tab_and_creates_pending_link(test_context):
    test_context["settings"].telegram_bot_username = "trace_edge_bot"
    await _signup_and_verify(test_context, email="dashboard-telegram-link@example.com")

    page = await test_context["client"].get("/dashboard/integrations")

    assert page.status_code == 200
    assert 'target="_blank"' in page.text
    assert "https://t.me/trace_edge_bot?start=link_" in page.text
    assert "Using Telegram Web?" in page.text
    assert "/start link_" in page.text
    assert "Under Maintenance" not in page.text
    assert "Discord" not in page.text
    async with test_context["session_factory"]() as session:
        pending = await session.scalar(select(TelegramDashboardLink))
        assert pending is not None
        assert pending.telegram_user_id == "pending"
