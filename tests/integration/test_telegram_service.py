from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AttributionTouch,
    DashboardPreference,
    Strategy,
    TelegramCallbackReceipt,
    TelegramConnection,
    TelegramConversationState,
    TelegramDashboardLink,
    Trial,
    User,
    UserFeedback,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, StrategyStatus
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.telegram_account_links import TelegramAccountLinkService
from ai_market_monitor.telegram.service import TelegramBotService
from ai_market_monitor.telegram.types import (
    NearMissListItem,
    TelegramCallback,
    TelegramInboundMessage,
)
from tests.factories import candles


class FakeNearMissProvider:
    async def top(self, user_id, *, strategy_id, limit, minimum_score):
        return [
            NearMissListItem(
                symbol="SOL/USDT",
                exchange="binance",
                timeframe="15m",
                score=88,
                trend="improving",
                passed=["Price above EMA 200", "Liquidity sweep detected"],
                missing=["Volume 1.42x; required 1.50x"],
                chart_reference="chart://sol",
            ),
            NearMissListItem(
                symbol="LINK/USDT",
                exchange="binance",
                timeframe="15m",
                score=81,
                trend="stable",
                passed=["Price above EMA 200"],
                missing=["Liquidity sweep not closed"],
            ),
        ][:limit]


class FakePreviewer:
    async def run(self, strategy):
        from ai_market_monitor.schemas.onboarding import MarketPreviewResponse

        return MarketPreviewResponse(
            status="succeeded",
            symbols_checked=2,
            candles_checked=600,
            sample_matches=[
                {
                    "exchange": strategy.universe.exchange,
                    "symbol": "SOL/USDT",
                    "completion_score": 100,
                }
            ],
            warnings=[],
            data_as_of="2026-06-14T12:00:00+00:00",
        )


class FakeTelegramMarketProvider:
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return ["SOL/USDT"]

    async def fetch_ohlcv(self, exchange: str, symbol: str, timeframe: str, limit: int):
        rows = candles(
            limit,
            start=datetime.now(UTC) - timedelta(minutes=15 * limit),
            minutes=15,
            close=100,
            volume=2000,
        )
        rows[-1] = Candle(
            timestamp=rows[-1].timestamp,
            open=100,
            high=120,
            low=99,
            close=119,
            volume=2000,
            is_closed=True,
        )
        return rows

    async def close(self) -> None:
        return None


def make_service(test_context, db, *, market_data_provider=None):
    service = TelegramBotService(
        db,
        test_context["settings"],
        previewer=FakePreviewer(),
        near_miss_provider=FakeNearMissProvider(),
        market_data_provider=market_data_provider,
    )
    return service


def start_message(text: str = "/start src_youtube__cmp_launch__ref_CREATOR10__tpl_sweep"):
    return TelegramInboundMessage(
        telegram_user_id="tg-100",
        chat_id="chat-100",
        username="market_trader",
        text=text,
        message_id="m1",
    )


async def test_telegram_start_records_attribution_and_persistent_menu(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        response = await service.handle_start(start_message())
        assert "does not guarantee outcomes" in response.text
        assert "Welcome to HilalMarkets" in response.text
        assert "Choose what you want to do next." not in response.text
        assert any(button.text == "Sign up / sign in" for button in response.buttons)
        assert not any("Create Monitor" in item for item in response.menu)
        assert any("Lifecycles" in item for item in response.menu)
        conversation = await db.scalar(select(TelegramConversationState))
        assert conversation is not None
        assert conversation.flow == "onboarding"
        touch = await db.scalar(select(AttributionTouch))
        assert touch.source == "youtube"
        assert touch.campaign == "launch"
        assert touch.referral_code == "CREATOR10"
        assert touch.metadata_json["template"] == "sweep"


async def test_telegram_callback_idempotency_for_old_disclaimer_button(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        callback = TelegramCallback(
            callback_query_id="cb-accept",
            telegram_user_id="tg-100",
            chat_id="chat-100",
            data="accept_disclaimer",
        )
        first = await service.handle_callback(callback)
        second = await service.handle_callback(callback)
        assert first.text == second.text
        assert await db.scalar(select(func.count(TelegramCallbackReceipt.id))) == 1
        assert await db.scalar(select(func.count(Trial.id))) == 0


async def test_telegram_trial_button_hands_off_to_dashboard_signup(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        blocked = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-trial-blocked",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="claim_trial",
            )
        )
        assert "Open this secure Dashboard link" in blocked.text
        assert any(
            button.url and "/signup?telegram_link=" in button.url for button in blocked.buttons
        )
        assert await db.scalar(select(func.count(Trial.id))) == 0


async def test_telegram_create_approve_and_activate_monitor(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-create",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="create_monitor",
            )
        )
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-describe",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="mode_describe",
            )
        )
        summary = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                username="market_trader",
                text=(
                    "Find bullish liquidity sweeps. Price above the four-hour 200 EMA. "
                    "Volume at least 1.5 times average."
                ),
            )
        )
        assert "structured interpretation" in summary.text
        assert "Interpreter:" not in summary.text
        assert "OpenAI interpretation was unavailable" not in summary.text
        explanation = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-explain",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="explain_rule",
            )
        )
        assert "Explain rules" in explanation.text
        assert "does not edit the monitor" in explanation.text
        assert "Current deterministic rules" in explanation.text
        approved = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-approve",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="approve_strategy",
            )
        )
        assert "Historical preview" in approved.text
        activated = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-activate",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="activate_strategy",
            )
        )
        assert "Monitor activated" in activated.text
        strategy = await db.scalar(select(Strategy))
        assert strategy.status == StrategyStatus.ACTIVE
        assert isinstance(strategy.active_version_id, UUID)


async def test_telegram_lifecycles_subscription_feedback_and_support(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        radar = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="Lifecycles",
            )
        )
        assert "Lifecycles" in radar.text
        pricing = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="Subscription",
            )
        )
        assert "Pricing" in pricing.text
        assert any(button.url and "/pricing#pricing" in button.url for button in pricing.buttons)
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-feedback",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="feedback:incorrect_match",
            )
        )
        support_response = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="Support",
            )
        )
        assert await db.scalar(select(func.count(UserFeedback.id))) == 1
        assert "Support" in support_response.text
        assert any(
            button.url and "/dashboard/support" in button.url for button in support_response.buttons
        )


async def test_telegram_dashboard_actions_handoff_to_signup_when_unlinked(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        response = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-billing-link",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="billing:checkout:pro",
            )
        )

        assert "Connect a Dashboard account first" in response.text
        assert "/signup?telegram_link=" not in response.text
        assert "/signin?telegram_link=" not in response.text
        assert "stripe" not in response.text.lower()
        assert "nowpayments" not in response.text.lower()
        assert any(
            button.url and "/signup?telegram_link=" in button.url for button in response.buttons
        )
        assert any(
            button.url and "/signin?telegram_link=" in button.url for button in response.buttons
        )
        links = (await db.scalars(select(TelegramDashboardLink))).all()
        assert {link.target_path for link in links} == {"/signup", "/signin"}


async def test_telegram_submenus_have_back_buttons_and_templates_create_drafts(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        for label in (
            "My Monitors",
            "Quick Scan",
            "Lifecycles",
            "Settings",
            "About",
            "Support",
            "Why No Alert?",
        ):
            response = await service.handle_message(
                TelegramInboundMessage(
                    telegram_user_id="tg-100",
                    chat_id="chat-100",
                    text=label,
                )
            )
            assert response.buttons or response.menu

        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-template-menu",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="mode_template",
            )
        )
        templated = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-template-sweep",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="template:liquidity_sweep",
            )
        )
        assert "Template selected" in templated.text
        assert "structured interpretation" in templated.text
        assert await db.scalar(select(func.count(Strategy.id))) == 1


async def test_telegram_quick_scan_template_uses_selected_provider(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(
            test_context,
            db,
            market_data_provider=FakeTelegramMarketProvider(),
        )
        await service.handle_start(start_message())

        selected = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-scan-provider",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="scan_provider:bybit",
            )
        )
        assert "Dashboard account first" in selected.text

        templated = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-scan-template",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="scan_template:six_month_high_breakout",
            )
        )
        assert "Dashboard account first" in templated.text
        assert await db.scalar(select(func.count(Strategy.id))) == 0


async def test_telegram_back_returns_main_menu(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        monitors = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="My Monitors",
            )
        )
        assert "My Monitors" in monitors.text

        drafts = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-drafts",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="monitors:drafts",
            )
        )
        assert "Draft monitors" in drafts.text

        previous = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-back-one-step",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="back:previous",
            )
        )
        assert "Welcome to HilalMarkets" in previous.text
        assert "Choose what you want to do next." not in previous.text


async def test_telegram_my_monitors_links_to_dashboard_management(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        conversation = await db.scalar(select(TelegramConversationState))
        db.add(
            Strategy(
                user_id=conversation.user_id,
                name="Live monitor",
                status=StrategyStatus.ACTIVE,
            )
        )
        await db.commit()

        monitors = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="My Monitors",
            )
        )

        assert "Live monitor" in monitors.text
        assert "Status: Active" in monitors.text
        assert not any(button.text == "Active Monitors" for button in monitors.buttons)
        manage_button = next(
            button for button in monitors.buttons if button.text.startswith("Manage ")
        )
        assert "#1" not in manage_button.text
        assert not any(button.text.startswith("Edit #") for button in monitors.buttons)
        assert not any(button.text.startswith("Delete #") for button in monitors.buttons)
        manage = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-manage-monitor",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data=manage_button.callback_data,
            )
        )
        assert "Manage Monitor" in manage.text
        pause_button = next(button for button in manage.buttons if "Pause" in button.text)
        paused = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-pause-monitor",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data=pause_button.callback_data,
            )
        )
        assert "Status: Paused" in paused.text
        strategy = await db.scalar(select(Strategy).where(Strategy.name == "Live monitor"))
        assert strategy.status == StrategyStatus.PAUSED


async def test_telegram_settings_hands_off_to_dashboard(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())

        days = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="Alert Days",
            )
        )
        assert "Dashboard account first" in days.text
        assert await db.scalar(select(DashboardPreference)) is None


async def test_telegram_signup_buttons_use_real_dashboard_links(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-account-menu",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="account:auth",
            )
        )
        signup = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-account-signup",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="account:signup",
            )
        )

        assert "Open this secure Dashboard link" in signup.text
        assert any(
            button.url and "/signup?telegram_link=" in button.url for button in signup.buttons
        )


async def test_telegram_dashboard_start_link_requires_confirmation_and_connects(test_context):
    test_context["settings"].telegram_bot_username = "trace_edge_bot"
    async with test_context["session_factory"]() as db:
        user = User(display_name="Dashboard Link")
        db.add(user)
        await db.flush()
        db.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject="dashboard-link@example.com",
                normalized_identifier="dashboard-link@example.com",
                display_identifier="dashboard-link@example.com",
                is_verified=True,
                is_primary=True,
                verified_at=datetime.now(UTC),
                profile_data={},
            )
        )
        url = await TelegramAccountLinkService(
            db,
            test_context["settings"],
        ).create_dashboard_start_link(user_id=user.id)
        raw_token = url.rsplit("link_", 1)[1]
        service = make_service(test_context, db)

        prompt = await service.handle_start(
            TelegramInboundMessage(
                telegram_user_id="tg-dashboard-link",
                chat_id="chat-dashboard-link",
                username="linked_trader",
                text=f"/start link_{raw_token}",
            )
        )

        assert "Connect this Telegram account to dashboard-link@example.com" in prompt.text
        assert await db.scalar(select(TelegramConnection)) is None

        resumed = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-dashboard-link",
                chat_id="chat-dashboard-link",
                username="linked_trader",
                text="/start",
            )
        )
        assert "Connect this Telegram account to dashboard-link@example.com" in resumed.text
        assert await db.scalar(select(TelegramConnection)) is None

        connected = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-dashboard-link",
                chat_id="chat-dashboard-link",
                username="linked_trader",
                text="Yes, connect",
            )
        )

        assert "Telegram connected" in connected.text
        connection = await db.scalar(select(TelegramConnection))
        assert connection is not None
        assert connection.user_id == user.id
        assert connection.username == "linked_trader"
        link = await db.scalar(select(TelegramDashboardLink))
        assert link.consumed_at is not None


async def test_telegram_create_monitor_hands_off_to_dashboard(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        response = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-create-manual",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="create_monitor",
            )
        )
        assert "Dashboard account first" in response.text
        assert await db.scalar(select(func.count(Strategy.id))) == 0


async def test_telegram_removed_create_flow_does_not_interpret_free_text(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-create-unsupported",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="create_monitor",
            )
        )

        summary = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                username="market_trader",
                text="Find vibes-based moonshot coins before influencers mention them.",
            )
        )

        assert summary.parse_mode is None
        assert "Choose an item from the menu" in summary.text
        assert await db.scalar(select(func.count(Strategy.id))) == 0


async def test_telegram_new_monitor_resets_stale_approval_state(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-create-first",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="create_monitor",
            )
        )
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-describe-first",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="mode_describe",
            )
        )
        first_summary = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                username="market_trader",
                text="Find bullish liquidity sweeps with 1.5x volume.",
            )
        )
        assert "structured interpretation" in first_summary.text

        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-create-second",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="create_monitor",
            )
        )
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-describe-second",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="mode_describe",
            )
        )
        second_summary = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                username="market_trader",
                text=(
                    "Find bullish liquidity sweeps. Price above the four-hour 200 EMA. "
                    "Volume at least 2 times average."
                ),
            )
        )

        assert "structured interpretation" in second_summary.text
        assert "Action needed" not in second_summary.text
        assert await db.scalar(select(func.count(Strategy.id))) == 2


async def test_telegram_approve_before_interpretation_points_to_dashboard(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-create-before-approve",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="create_monitor",
            )
        )
        response = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-approve-too-soon",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="approve_strategy",
            )
        )

        assert "Action needed" in response.text
        assert "Complete strategy interpretation" in response.text
        assert any(
            button.url and "/dashboard/strategies/new" in button.url for button in response.buttons
        )
        assert not any(button.text == "Create Monitor" for button in response.buttons)
