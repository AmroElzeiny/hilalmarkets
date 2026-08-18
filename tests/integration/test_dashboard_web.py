from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from ai_market_monitor.api.dependencies import get_market_data_provider
from ai_market_monitor.core.csrf import csrf_token
from ai_market_monitor.core.plans import plan_offer_payload
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
from ai_market_monitor.services.fixture_market_data import FixtureMarketDataProvider
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

    # `/dashboard` was the old counting front page. It is deleted, and the address takes
    # a person to Home — which is the page a new account is meant to land on.
    moved = await test_context["client"].get("/dashboard", follow_redirects=False)
    assert moved.status_code == 303
    assert moved.headers["location"] == "/home"

    dashboard = await test_context["client"].get("/home")
    assert dashboard.status_code == 200
    assert "Let us set up your first monitor." in dashboard.text
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

    pending = await test_context["client"].get("/dashboard/connections")
    assert pending.status_code == 200
    assert '<html lang="ar" dir="rtl"' in pending.text
    # A Telegram link that was started and never finished is never reported as working.
    # Only Telegram's own card is read, because the other three channels have states of
    # their own that have nothing to do with this.
    #
    # It used to say "Not set up", which was the wrong half of the truth: enough *is* set
    # up that starting again from scratch fails, because the half-made row is already
    # attached to this person. So the card says it was not finished, and offers the one
    # action that clears it — which it did not offer at all before.
    telegram_card = pending.text.split('data-channel="telegram"', 1)[1].split("</li>", 1)[0]
    assert "Not finished" in telegram_card
    assert "Connected" not in telegram_card
    assert "data-c-unlink-telegram" in telegram_card
    assert "data-c-connect-telegram" not in telegram_card

    async with test_context["session_factory"]() as session:
        connection = await session.scalar(select(TelegramConnection))
        assert connection is not None
        connection.status = ConnectionStatus.ACTIVE
        connection.alerts_enabled = True
        # A Telegram link with no chat to send to cannot deliver anything, so the page
        # refuses to call it live without one. The fixture has to be a real connection
        # for the "active" half of this test to mean what it says.
        connection.chat_id = "rtl-active-chat"
        await session.commit()

    active = await test_context["client"].get("/dashboard/connections")
    assert active.status_code == 200
    live_card = active.text.split('data-channel="telegram"', 1)[1].split("</li>", 1)[0]
    # Linked *and* switched on. The two are separate facts and the card says which:
    # showing one word for both is how somebody ends up certain they will be told and
    # hearing nothing.
    assert "Messages are being sent here." in live_card
    assert "Not set up" not in live_card


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
    assert "Favorites" in watchlist_page.text
    assert "SOL" in watchlist_page.text
    assert "Your saved asset passports will appear here." not in watchlist_page.text

    # Screening changes are answered by Evidence and Activity, which is a different page
    # from Opportunities and keeps its own address.
    compliance_redirect = await test_context["client"].get("/dashboard/compliance")
    assert compliance_redirect.status_code == 303
    assert compliance_redirect.headers["location"] == (
        "/dashboard/lifecycles?tab=compliance_changes"
    )
    compliance_page = await test_context["client"].get(compliance_redirect.headers["location"])
    assert compliance_page.status_code == 200
    assert "Recent updates" in compliance_page.text
    assert "Screening changes" in compliance_page.text
    assert "SOL" in compliance_page.text


async def test_market_favorite_json_flow_rejects_unresolved_methodology_without_creating_an_asset(
    test_context,
):
    await _signup_and_verify(test_context, email="favorite-json-error@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        assert user is not None
        token = csrf_token(test_context["settings"], user.id)

    response = await test_context["client"].post(
        "/dashboard/market/SOL/watchlist?format=json",
        data={"methodology_id": str(uuid4())},
        headers={"X-CSRF-Token": token, "Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "approved_methodology_required"
    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(ApprovedWatchlistAsset.id)) is None


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


async def test_public_trial_claim_is_closed(test_context):
    await _signup_and_verify(test_context, email="trial@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        assert user is not None
        token = csrf_token(test_context["settings"], user.id)
    first = await test_context["client"].post(
        "/dashboard/trial/claim",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    second = await test_context["client"].post(
        "/dashboard/trial/claim",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert first.headers["location"] == "/dashboard/billing?error=billing_disabled"
    assert second.headers["location"] == "/dashboard/billing?error=billing_disabled"
    async with test_context["session_factory"]() as session:
        trials = (await session.scalars(select(Trial))).all()
        assert trials == []


async def test_disabled_provider_blocks_checkout_without_obsolete_beta_copy(test_context):
    await _signup_and_verify(test_context, email="billing@example.com")
    page = await test_context["client"].get("/dashboard/billing")
    assert page.status_code == 200
    assert "Subscription and Billing" in page.text
    assert "Paid billing is disabled" not in page.text
    assert "What billing changes" not in page.text
    assert "Private beta access" not in page.text
    assert "Free forever" in page.text
    assert 'data-billing-page-interval' in page.text
    assert 'value="annual"' in page.text
    assert "Choose Monitor monthly" in page.text
    assert "7-day money-back guarantee" in page.text
    assert "Cancel within 7 days of payment for a full refund." in page.text
    assert 'value="annual"' in page.text and 'disabled aria-disabled=true' in page.text
    assert 'id="billing-checkout-dialog"' in page.text
    # The Pro plan is not on sale yet, so the card says "Soon" and carries no price.
    # A number beside "Soon" reads as a charge the user is about to face.
    assert "Pro is coming soon" in page.text
    assert "$22" not in page.text
    # The Monitor launch price, with the old one crossed out beside it. Both numbers
    # come from `core.plans`, not from this file, and the assertion still holds on the
    # day the offer ends, when there is no crossed-out price left to show.
    trader_offer = plan_offer_payload("trader")
    assert f"${int(trader_offer['monthlyPrice'])}" in page.text  # type: ignore[arg-type]
    original = trader_offer["originalMonthlyPrice"]
    if original:
        assert f"${int(original)}" in page.text  # type: ignore[arg-type]
        assert 'class="price-original"' in page.text
        assert "data-offer-countdown" in page.text
    review = await test_context["client"].get(
        "/dashboard/billing/checkout?plan_code=trader",
        follow_redirects=False,
    )
    assert review.status_code == 303
    assert review.headers["location"] == "/dashboard/billing?error=billing_disabled"


async def test_dashboard_settings_timezone_dropdown_persists(test_context):
    """The one Settings page offers the zones, and one service saves the choice.

    It used to be a whole-form `POST` to a handler of its own. That page and that handler
    were removed together when the redesigned page took over `/dashboard/settings`; the
    redesigned page saves one control at a time through the JSON endpoint, and both ways
    always called `AccountSettingsService`, which is why nothing about what a zone may be
    changed with the form.
    """

    await _signup_and_verify(test_context, email="timezone@example.com")

    page = await test_context["client"].get("/dashboard/settings")
    assert page.status_code == 200
    assert "Europe/Moscow" in page.text
    assert 'name="theme"' not in page.text
    # It saves as you go, so there is no Save button and no form to post.
    assert "data-g-saved" in page.text
    assert "data-settings-save" not in page.text

    async with test_context["session_factory"]() as session:
        saver = await session.scalar(select(User))
        token = csrf_token(test_context["settings"], saver.id)

    response = await test_context["client"].put(
        "/api/v1/dashboard/preferences/settings",
        json={"timezone": "Europe/Moscow"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text

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

    # `/dashboard/integrations` is the older name for the same page and is written into
    # outgoing WhatsApp replies and the account-link flow, so it still answers — with a
    # redirect to the one page, never a second copy of it.
    moved = await test_context["client"].get("/dashboard/integrations", follow_redirects=False)
    assert moved.status_code == 308
    assert moved.headers["location"] == "/dashboard/connections"

    page = await test_context["client"].get("/dashboard/connections")

    assert page.status_code == 200
    assert 'target="_blank"' in page.text
    assert "https://t.me/trace_edge_bot?start=link_" in page.text
    assert "/start link_" in page.text
    assert "Under Maintenance" not in page.text
    assert "Discord" not in page.text
    async with test_context["session_factory"]() as session:
        pending = await session.scalar(select(TelegramDashboardLink))
        assert pending is not None
        assert pending.telegram_user_id == "pending"


class _RaisingMarketProvider:
    """Stands in for a live exchange that cannot currently be reached."""

    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        raise RuntimeError("api.binance.com unreachable")

    async def close(self) -> None:
        return None


_EXCHANGE_UNREACHABLE = "Exchange market data is currently unavailable"

#: What the page always says about an empty list, whatever the exchange is doing.
#:
#: It is a screening result, not an outage: nothing is listed until a Shariah standard
#: and its evidence are published. This sentence is in the page every time, so it can
#: never be evidence of *why* the list is empty on any one request.
_NOTHING_PUBLISHED_YET = "Coins appear here only after a Shariah standard"


@pytest.mark.parametrize(
    ("provider_factory", "exchange_is_reachable"),
    [
        pytest.param(_RaisingMarketProvider, False, id="the_exchange_cannot_be_reached"),
        pytest.param(FixtureMarketDataProvider, True, id="the_exchange_answers"),
    ],
)
async def test_market_page_banner_matches_the_real_cause(
    test_context, provider_factory, exchange_is_reachable
):
    # A live-exchange fetch failure and "this methodology has zero eligible assets" are
    # different facts and must never share a message: the first is an infrastructure
    # problem, the second is a screening result. The note about the exchange appears when
    # — and only when — the exchange really could not be read.
    await _signup_and_verify(
        test_context, email=f"market-banner-{uuid4().hex[:8]}@example.com"
    )
    test_context["app"].dependency_overrides[get_market_data_provider] = provider_factory

    page = await test_context["client"].get("/dashboard/market")

    assert page.status_code == 200
    assert (_EXCHANGE_UNREACHABLE in page.text) is not exchange_is_reachable
    assert _NOTHING_PUBLISHED_YET in page.text
