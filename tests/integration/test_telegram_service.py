from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AttributionTouch,
    DashboardPreference,
    Strategy,
    TelegramCallbackReceipt,
    TelegramConversationState,
    TelegramDashboardLink,
    Trial,
    UserFeedback,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, StrategyStatus
from ai_market_monitor.services.interfaces import Candle
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
        assert "Welcome to TraceEdge" in response.text
        assert "Choose what you want to do next." not in response.text
        assert any(button.text == "Sign up / sign in" for button in response.buttons)
        assert any("Create Monitor" in item for item in response.menu)
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


async def test_telegram_claim_trial_requires_dashboard_signup(test_context):
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
        assert "requires a Dashboard account" in blocked.text
        assert any(button.callback_data == "account:signup" for button in blocked.buttons)

        conversation = await db.scalar(select(TelegramConversationState))
        db.add(
            UserIdentity(
                user_id=conversation.user_id,
                provider=IdentityProvider.EMAIL,
                provider_subject="trial@example.com",
                normalized_identifier="trial@example.com",
                display_identifier="trial@example.com",
                is_verified=True,
            )
        )
        await db.commit()
        claimed = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-trial-claimed",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="claim_trial",
            )
        )
        assert "Trial claimed successfully" in claimed.text
        assert await db.scalar(select(func.count(Trial.id))) == 1


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
        subscription = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="Subscription",
            )
        )
        assert "No active trial or subscription" in subscription.text
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-feedback",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="feedback:incorrect_match",
            )
        )
        support_response = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-support",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="support:missing_alert",
            )
        )
        assert await db.scalar(select(func.count(UserFeedback.id))) == 1
        assert "Please contact support directly" in support_response.text
        assert "tg:tg-100" in support_response.text


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
        assert "/signup?telegram_link=" in response.text
        assert "/signin?telegram_link=" in response.text
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
            assert any(button.callback_data == "back:previous" for button in response.buttons)

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
        assert "Bybit" in selected.text

        templated = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-scan-template",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="scan_template:six_month_high_breakout",
            )
        )
        assert "Quick Scan results" in templated.text
        assert "- Exchange: bybit" in templated.text
        assert "SOL/USDT" in templated.text
        assert "Risk/R:R filter: not requested" in templated.text
        assert await db.scalar(select(func.count(Strategy.id))) == 0


async def test_telegram_back_returns_one_step_before_main_menu(test_context):
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
        assert "My Monitors" in previous.text

        home = await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-back-home",
                telegram_user_id="tg-100",
                chat_id="chat-100",
                data="back:previous",
            )
        )
        assert "Welcome to TraceEdge" in home.text
        assert "Choose what you want to do next." not in home.text


async def test_telegram_my_monitors_controls_pause_edit_and_delete(test_context):
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

        assert "Live monitor - active" in monitors.text
        assert not any(button.text == "Active Monitors" for button in monitors.buttons)
        paused = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="Pause #1",
            )
        )
        assert "Live monitor - paused" in paused.text
        strategy = await db.scalar(select(Strategy).where(Strategy.name == "Live monitor"))
        assert strategy.status == StrategyStatus.PAUSED

        edit = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="Edit #1",
            )
        )
        assert "Editing Live monitor" in edit.text

        await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="My Monitors",
            )
        )
        deleted = await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="Delete #1",
            )
        )
        assert "No monitors yet" in deleted.text
        assert strategy.status == StrategyStatus.ARCHIVED


async def test_telegram_settings_store_alert_schedule_and_near_miss_preference(test_context):
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
        assert "Alert Days" in days.text
        await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="Monday",
            )
        )
        await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="09:00",
            )
        )
        await service.handle_message(
            TelegramInboundMessage(
                telegram_user_id="tg-100",
                chat_id="chat-100",
                text="Enable Near-Miss Alerts",
            )
        )

        prefs = await db.scalar(select(DashboardPreference))
        assert prefs.notification_preferences["alert_days"] == ["Monday"]
        assert prefs.notification_preferences["alert_hours"] == ["09:00"]
        assert prefs.notification_preferences["near_miss_enabled"] is True


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


async def test_telegram_manual_setup_text_from_create_menu_interprets(test_context):
    async with test_context["session_factory"]() as db:
        service = make_service(test_context, db)
        await service.handle_start(start_message())
        await service.handle_callback(
            TelegramCallback(
                callback_query_id="cb-create-manual",
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
                text=(
                    "Find bullish liquidity sweeps. Price above the four-hour 200 EMA. "
                    "Volume at least 1.5 times average."
                ),
            )
        )

        assert "structured interpretation" in summary.text
        assert await db.scalar(select(func.count(Strategy.id))) == 1


async def test_telegram_unsupported_setup_highlights_action_required(test_context):
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

        assert summary.parse_mode == "HTML"
        assert "<b>[ACTION REQUIRED] Unsupported conditions</b>" in summary.text
        assert "No supported deterministic monitor condition" in summary.text
        assert "</b>" in summary.text


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


async def test_telegram_approve_before_interpretation_shows_relevant_create_menu(test_context):
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
        assert any(button.callback_data == "mode_describe" for button in response.buttons)
        assert not any(button.text == "Create Monitor" for button in response.buttons)
