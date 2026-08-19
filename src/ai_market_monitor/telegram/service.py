import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.dashboard_paths import (
    CONNECTIONS_PATH,
    LIFECYCLES_PATH,
    OPPORTUNITIES_PATH,
    SETTINGS_PATH,
    SUPPORT_PATH,
)
from ai_market_monitor.core.security import IdentityAssertionTokenService
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    AuditEvent,
    DashboardPreference,
    OnboardingSession,
    SetupInstance,
    Strategy,
    StrategyVersion,
    Subscription,
    TelegramCallbackReceipt,
    TelegramConnection,
    TelegramConversationState,
    Trial,
    User,
    UserFeedback,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
    IdentityProvider,
    OnboardingStatus,
    OnboardingStep,
    SetupLifecycleState,
    StrategyStatus,
)
from ai_market_monitor.engine.dedup import stable_event_hash
from ai_market_monitor.engine.models import EvaluationResult
from ai_market_monitor.schemas.on_demand import OnDemandScanRequest
from ai_market_monitor.schemas.onboarding import (
    AttributionInput,
    GuidedSetupRequest,
    IdentityInput,
    StartOnboardingRequest,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.admin_notifications import AdminNotificationService
from ai_market_monitor.services.alert_presentation import ACTION_LABELS
from ai_market_monitor.services.billing import BillingError, BillingService
from ai_market_monitor.services.interfaces import MarketDataProvider, RecentMarketPreviewer
from ai_market_monitor.services.monitor_operations import (
    MonitorOperationError,
    MonitorOperationService,
)
from ai_market_monitor.services.on_demand_scans import OnDemandScanError, OnDemandScanService
from ai_market_monitor.services.onboarding import OnboardingError, OnboardingService
from ai_market_monitor.services.openai_interpreter import configured_strategy_interpreter
from ai_market_monitor.services.risk_disclaimer import (
    DisclaimerIdentityMissing,
)
from ai_market_monitor.services.risk_disclaimer import (
    record_acceptance as record_disclaimer_acceptance,
)
from ai_market_monitor.services.strategy import StrategyGateError, StrategyService
from ai_market_monitor.services.telegram_account_links import (
    TelegramAccountLinkError,
    TelegramAccountLinkService,
)
from ai_market_monitor.services.template_catalog import BUILTIN_STRATEGY_TEMPLATES
from ai_market_monitor.services.trials import TrialError, TrialLifecycleService
from ai_market_monitor.services.verified_strategy import (
    VerifiedStrategyError,
    VerifiedStrategyService,
)
from ai_market_monitor.telegram.rendering import (
    escape,
    render_confirmed_alert,
    render_lifecycle_update,
    render_near_miss_list,
)
from ai_market_monitor.telegram.types import (
    NearMissListItem,
    TelegramButton,
    TelegramCallback,
    TelegramInboundMessage,
    TelegramOutboundMessage,
)

#: Where a "scan the market once" button really goes.
#:
#: It used to be ``/dashboard/scan-now``, which was one of the two addresses of the
#: Trading Assistant page. That page has been removed, and a button inside a Telegram
#: message lives for as long as the message does — so leaving them pointed at it would
#: send people from months-old chats to a "not found". The one-time scan itself is
#: unchanged: it is a mode of the builder, and this is the builder's own address.
_ONE_TIME_SCAN_PATH = "/dashboard/strategies/new?mode=scanner"

PRIMARY_MENU = [
    "📋 My Monitors",
    "🔄 Lifecycles",
    "🎁 Trial",
    "💸 Pricing",
    "⚙️ Settings",
    "🆘 Support",
    "ℹ️ About",
]

MAIN_MENU_TEXT = (
    "🏠 Main Menu\n\n"
    "Welcome to Hilal Markets.\n\n"
    "📈 I monitor approved crypto spot-market conditions.\n"
    "🔄 Lifecycles show what is forming, complete, invalidated, or expired.\n"
    "🧾 Alerts include deterministic proof.\n\n"
    "Decision support only. It does not guarantee outcomes or place trades."
)

MAIN_MENU_BUTTONS = [
    TelegramButton("📋 My Monitors", "menu:my_monitors"),
    TelegramButton("🔄 Lifecycles", "menu:latest_setups"),
    TelegramButton("🎁 Trial", "account:signup"),
    TelegramButton("💸 Pricing", "pricing"),
    TelegramButton("⚙️ Settings", "dashboard:settings"),
    TelegramButton("🆘 Support", "menu:support"),
    TelegramButton("ℹ️ About", "menu:about"),
]

MONITOR_FILTER_HELP = (
    "You can describe your monitor by symbols, direction, indicator, condition, timeframe, "
    "exchange, quote asset, volume/liquidity filter, sessions, candle patterns, and optional "
    "trade-quality context if you want it."
)

CREATE_TEMPLATES = {
    key: (template.label, template.setup_text)
    for key, template in BUILTIN_STRATEGY_TEMPLATES.items()
}
CREATE_TEMPLATE_LABELS = {
    template.label: key for key, template in BUILTIN_STRATEGY_TEMPLATES.items()
}
CREATE_TEMPLATE_LABELS.update(
    {
        "Liquidity Sweep": "liquidity_sweep",
        "Volume Breakout": "breakout_volume",
        "Six-Month High Breakout": "six_month_high_breakout",
    }
)

ALERT_DAYS = [
    "Every Day",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

ALERT_HOURS = [f"{hour:02d}:00" for hour in range(24)]

TELEGRAM_TIMEZONES = [
    "UTC",
    "America/New_York",
    "Europe/London",
    "Europe/Moscow",
    "Asia/Dubai",
    "Asia/Singapore",
]


class NearMissProvider(Protocol):
    async def top(
        self, user_id: UUID, *, strategy_id: UUID | None, limit: int, minimum_score: float
    ) -> list[NearMissListItem]: ...


class EmptyNearMissProvider:
    async def top(
        self, user_id: UUID, *, strategy_id: UUID | None, limit: int, minimum_score: float
    ) -> list[NearMissListItem]:
        return []


class TelegramBotService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        previewer: RecentMarketPreviewer,
        near_miss_provider: NearMissProvider | None = None,
        market_data_provider: MarketDataProvider | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.previewer = previewer
        self.near_miss_provider = near_miss_provider or EmptyNearMissProvider()
        self.market_data_provider = market_data_provider

    async def handle_start(self, message: TelegramInboundMessage) -> TelegramOutboundMessage:
        start_param = message.text.partition(" ")[2].strip()
        if start_param.startswith("link_"):
            return await self._handle_dashboard_start_link(
                message,
                start_param.removeprefix("link_"),
            )
        if not start_param:
            existing_conversation = await self._conversation(message.telegram_user_id)
            if (
                existing_conversation is not None
                and existing_conversation.flow == "telegram_link"
                and existing_conversation.step == "confirm"
            ):
                return self._telegram_link_confirmation_message(
                    message,
                    existing_conversation,
                )
        attribution = self._parse_deep_link(start_param)
        assertion = IdentityAssertionTokenService(self.settings).issue(
            "telegram", message.telegram_user_id
        )
        onboarding = await OnboardingService(self.session, self.settings).start(
            StartOnboardingRequest(
                identity=IdentityInput(
                    provider=IdentityProvider.TELEGRAM,
                    provider_subject=message.telegram_user_id,
                    display_identifier=message.username,
                    display_name=message.username,
                    verified=True,
                ),
                entry_channel="telegram",
                attribution=attribution,
                identity_assertion=assertion,
            )
        )
        await self._upsert_connection(message, onboarding.user_id)
        conversation = await self._upsert_conversation(
            message,
            user_id=onboarding.user_id,
            onboarding_session_id=onboarding.session_id,
            flow="onboarding",
            step="disclaimer",
            state_data={
                **onboarding.state_data,
                "session_token": onboarding.session_token,
                "template": attribution.metadata_json.get("template"),
            },
        )
        await self._audit(
            onboarding.user_id,
            "telegram.start",
            "onboarding_session",
            str(onboarding.session_id),
            {"deep_link": bool(start_param), "correlation_id": conversation.correlation_id},
        )
        linked = await self._has_email_identity(onboarding.user_id)
        trial_claimed = await self._has_claimed_trial(onboarding.user_id)
        await self.session.commit()
        await AdminNotificationService(self.settings).send(
            f"Bot start: @{message.username or '-'} tg:{message.telegram_user_id}"
        )
        linked_text = (
            "\n\nDashboard account connected. I can now show trial status, subscription dates "
            "and monitor stats for this Telegram user."
            if linked
            else ""
        )
        return TelegramOutboundMessage(
            chat_id=message.chat_id,
            text=f"{MAIN_MENU_TEXT}{linked_text}",
            buttons=self._main_menu_buttons(linked=linked, trial_claimed=trial_claimed),
            menu=PRIMARY_MENU,
            correlation_id=conversation.correlation_id,
        )

    async def handle_message(self, message: TelegramInboundMessage) -> TelegramOutboundMessage:
        if message.text.startswith("/free"):
            return await self._free_command(message)
        if message.text.startswith("/start"):
            return await self.handle_start(message)
        conversation = await self._conversation(message.telegram_user_id)
        if conversation is None:
            return self._plain(message, "Send /start to begin or resume onboarding.")
        text = message.text.strip()
        if conversation.flow == "telegram_link" and conversation.step == "confirm":
            normalized_link_text = self._normalize_menu_text(text).casefold()
            if "yes" in normalized_link_text and "connect" in normalized_link_text:
                return await self._confirm_dashboard_telegram_link(
                    self._callback_from_message(message, "telegram_link:confirm"),
                    conversation,
                )
            if normalized_link_text in {"cancel", "go back", "back", "main menu"}:
                return await self._cancel_dashboard_telegram_link(
                    self._callback_from_message(message, "telegram_link:cancel"),
                    conversation,
                )
            return self._telegram_link_confirmation_message(message, conversation)
        if conversation.flow == "create_monitor" and conversation.step == "collect_setup_text":
            try:
                return await self._receive_setup_text(message, conversation)
            except (OnboardingError, StrategyGateError) as exc:
                await self.session.rollback()
                return self._plain(
                    message,
                    "Action needed: I could not convert that setup yet.\n\n"
                    f"{escape(str(exc))}\n\n"
                    "You can send the setup again, choose a template, or cancel this draft.",
                    buttons=[
                        TelegramButton("Send Another Description", "mode_describe"),
                        TelegramButton("Use Template", "mode_template"),
                        TelegramButton("Cancel", "cancel"),
                    ],
                )
        normalized = self._normalize_menu_text(text)
        monitor_action = self._parse_monitor_action_label(normalized)
        if monitor_action is not None:
            action, index = monitor_action
            return await self._handle_monitor_action(message, conversation, action, index)
        if normalized in ALERT_DAYS:
            return await self._set_alert_day(message, conversation, normalized)
        if normalized in ALERT_HOURS:
            return await self._toggle_alert_hour(message, conversation, normalized)
        if normalized in {"Enable Near-Miss Alerts", "Disable Near-Miss Alerts"}:
            return await self._set_near_miss_preference(
                message,
                conversation,
                enabled=normalized.startswith("Enable"),
            )
        if normalized in {"Claim Trial", "Trial"}:
            return await self._account_link_callback(
                self._callback_from_message(message, "account:signup"), conversation
            )
        if normalized in {"View a Sample Alert", "Sample Alert"}:
            return self._about_message(message)
        if normalized == "View Proof":
            return self._sample_proof_callback(self._callback_from_message(message, "sample_proof"))
        if normalized == "View Full Proof":
            return await self._utility_callback(
                self._callback_from_message(message, "proof:view"), conversation
            )
        if normalized == "Open Chart":
            return self._plain(
                message,
                "Open a chart for the alert symbol. If this came from a specific alert, "
                "use the chart button attached to that alert message for the exact symbol.",
                buttons=[
                    TelegramButton(
                        "Open TradingView",
                        "external:chart",
                        url="https://www.tradingview.com/chart/",
                    ),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        if normalized == "Mute Near-Miss":
            return self._plain(
                message,
                "Near-Miss alerts muted for this sample context. Live mute settings are "
                "managed per strategy in Settings.",
                buttons=self._back_buttons("dashboard:settings"),
            )
        if normalized == "Mute Strategy":
            return await self._utility_callback(
                self._callback_from_message(message, "mute_strategy"), conversation
            )
        if normalized == "Mark Entered":
            return await self._feedback(
                self._callback_from_message(message, "feedback:entered"), conversation
            )
        if normalized == "Ignore":
            return await self._feedback(
                self._callback_from_message(message, "feedback:ignored"), conversation
            )
        if normalized == "See How It Works":
            return self._about_message(message)
        if normalized == "Open Main Website":
            return self._plain(
                message,
                f"Main website: {self._dashboard_url('/')}",
                buttons=[
                    TelegramButton(
                        "Open Main Website",
                        "external:website",
                        url=self._dashboard_url("/"),
                    )
                ],
            )
        if normalized in {"Open Dashboard", "Dashboard"}:
            return await self._dashboard_callback(
                self._callback_from_message(message, "dashboard:home"), conversation
            )
        if normalized == "Sign up / sign in":
            return self._account_auth_callback(self._callback_from_message(message, "account:auth"))
        if normalized == "Sign up":
            return await self._account_link_callback(
                self._callback_from_message(message, "account:signup"), conversation
            )
        if normalized == "Sign in":
            return await self._account_link_callback(
                self._callback_from_message(message, "account:signin"), conversation
            )
        if normalized == "Describe my setup":
            conversation.flow = "create_monitor"
            conversation.step = "collect_setup_text"
            await self.session.commit()
            return self._plain(
                message,
                "Describe the setup in one message.\n\n"
                f"{MONITOR_FILTER_HELP}\n\n"
                "Example: bullish liquidity sweep, price above the 4h 200 EMA, volume at "
                "least 1.5x average.",
                menu=["Go Back"],
            )
        if normalized == "Use a template":
            return self._template_menu_message(message)
        if normalized == "Import strategy":
            conversation.flow = "create_monitor"
            conversation.step = "collect_setup_text"
            await self.session.commit()
            return self._plain(
                message,
                "Paste your strategy description or JSON-like rules. I will convert it into "
                "a draft for approval before it can monitor live.",
                menu=["Go Back"],
            )
        if normalized in CREATE_TEMPLATE_LABELS:
            key = CREATE_TEMPLATE_LABELS[normalized]
            return await self._create_from_template(
                self._callback_from_message(message, f"template:{key}"), conversation
            )
        if normalized in {"Approve", "Confirm", "Confirm Alert", "Confirm Explanation"}:
            try:
                return await self._approve_strategy(
                    self._callback_from_message(message, "approve_strategy"), conversation
                )
            except (OnboardingError, StrategyGateError) as exc:
                await self.session.rollback()
                return self._action_needed_message(message, conversation, exc)
        if normalized in {"Explain a rule", "Explain rules"}:
            return await self._explain_current_strategy_callback(
                self._callback_from_message(message, "explain_rule"), conversation
            )
        if normalized == "Edit":
            conversation.flow = "create_monitor"
            conversation.step = "collect_setup_text"
            await self.session.commit()
            return self._plain(
                message,
                "Send the revised setup description. I will create a new structured draft for "
                "approval.",
                menu=["Go Back"],
            )
        if normalized == "Save Draft":
            conversation.flow = "main_menu"
            conversation.step = "idle"
            await self.session.commit()
            return self._plain(message, "Draft saved. It will not scan live until activation.")
        if normalized in {"Reject", "Reject Alert", "Reject Explanation"}:
            conversation.flow = "main_menu"
            conversation.step = "idle"
            await self.session.commit()
            return self._plain(
                message,
                "Draft rejected. Nothing was activated.",
                buttons=[
                    self._dashboard_button("Dashboard", "/dashboard/strategies/new"),
                    TelegramButton("🏠 Main Menu", "back:main"),
                ],
            )
        if normalized == "Cancel":
            return await self._main_menu_message(message, conversation)
        if normalized == "I Understand - Activate":
            try:
                return await self._activate_strategy(
                    self._callback_from_message(message, "activate_strategy"), conversation
                )
            except (BillingError, OnboardingError, StrategyGateError) as exc:
                await self.session.rollback()
                return self._action_needed_message(message, conversation, exc)
        if normalized == "Activate Free Plan":
            return await self._activate_free_plan_message(message, conversation)
        if normalized in {"Upgrade Trader", "Upgrade Pro", "Upgrade Creator"}:
            plan_code = {
                "Upgrade Trader": "trader",
                "Upgrade Pro": "pro",
                "Upgrade Creator": "creator",
            }[normalized]
            return await self._billing_checkout_message(message, conversation, plan_code)
        if normalized in {"Back", "Go Back", "Main Menu"}:
            return await self._main_menu_message(message, conversation)
        if normalized == "Create Monitor":
            return await self._dashboard_callback(
                self._callback_from_message(message, "dashboard:builder"), conversation
            )
        if normalized == "Quick Scan":
            return await self._dashboard_callback(
                self._callback_from_message(message, "dashboard:scan"), conversation
            )
        if normalized in {"My Monitors", "My Drafts"}:
            await self._push_navigation(conversation, "menu:my_monitors")
            return await self._my_monitors(message, conversation)
        if normalized in {"Use Existing Strategy", "Previous Scans"}:
            return await self._dashboard_callback(
                self._callback_from_message(message, "dashboard:scan"), conversation
            )
        if normalized == "Describe New Condition":
            return await self._dashboard_callback(
                self._callback_from_message(message, "dashboard:builder"), conversation
            )
        if normalized == "Top Near-Misses":
            await self._push_navigation(conversation, "menu:latest_setups")
            return await self._latest_setups(message, conversation)
        if normalized == "One Condition Remaining":
            await self._push_navigation(conversation, "near:one_left")
            return await self._show_near_miss(message, conversation, mode="one_left")
        if normalized in {"Confirmed", "Forming", "Invalidated", "Expired"}:
            await self._push_navigation(conversation, "menu:latest_setups")
            return await self._latest_setups(message, conversation, category=normalized.lower())
        if normalized in {"Usage and Limits", "Manage Billing", "Compare Plans"}:
            return self._pricing_message(message)
        if normalized in {
            "Alert Channels",
            "Alert Schedule",
            "Alert Days",
            "Alert Hours",
            "Near-Miss Threshold",
            "Near-Miss Alerts",
            "Time Zone",
        }:
            return await self._dashboard_callback(
                self._callback_from_message(message, "dashboard:settings"), conversation
            )
        if normalized in {
            "Report Missing Alert",
            "Technical Issue",
            "Billing Issue",
            "Strategy Help",
        }:
            return self._support_menu_message(message)
        if normalized in {"Scan Market Now", "Quick Scan"}:
            return await self._dashboard_callback(
                self._callback_from_message(message, "dashboard:scan"), conversation
            )
        if conversation.flow == "light_scan" and conversation.step == "collect_prompt":
            return await self._run_light_scan_message(message, conversation)
        if normalized in {"Near-Miss Radar", "Lifecycles"}:
            await self._push_navigation(conversation, "menu:latest_setups")
            return await self._latest_setups(message, conversation)
        if normalized == "Latest Alerts":
            return await self._latest_alerts(message, conversation)
        if normalized == "Trial":
            return await self._account_link_callback(
                self._callback_from_message(message, "account:signup"), conversation
            )
        if normalized in {"Subscription", "Pricing"}:
            return self._pricing_message(message)
        if normalized == "Performance":
            await self._push_navigation(conversation, "menu:performance")
            return self._plain(
                message,
                "Performance\n\nForward-test analytics and setup-performance summaries are "
                "available in the dashboard. Telegram will show a compact summary once live "
                "results exist.",
                buttons=self._back_buttons("dashboard:performance"),
            )
        if normalized == "About":
            return self._about_message(message)
        if normalized == "Settings":
            return await self._dashboard_callback(
                self._callback_from_message(message, "dashboard:settings"), conversation
            )
        if normalized == "Support":
            return self._support_menu_message(message)
        if normalized == "Setup Replay":
            await self._push_navigation(conversation, "menu:latest_setups")
            return await self._latest_setups(message, conversation)
        if conversation.flow in {"why_no_alert", "setup_replay"}:
            return self._plain(
                message,
                "Lifecycles\n\nReplay is hidden. Use lifecycle cards for setup state, "
                "missing conditions, proof context and chart evidence.",
                buttons=[
                    self._dashboard_button("Open Lifecycles", LIFECYCLES_PATH),
                    TelegramButton("Support", "support:missing_alert"),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        if conversation.flow == "create_monitor" and conversation.step in {
            "choose_mode",
            "approval",
        }:
            conversation.step = "collect_setup_text"
            await self.session.flush()
            try:
                return await self._receive_setup_text(message, conversation)
            except (OnboardingError, StrategyGateError) as exc:
                await self.session.rollback()
                return self._plain(
                    message,
                    f"Action needed: I could not convert that setup yet.\n\n{escape(str(exc))}",
                    buttons=[
                        TelegramButton("Describe Again", "mode_describe"),
                        TelegramButton("Use Template", "mode_template"),
                        TelegramButton("Cancel", "cancel"),
                    ],
                )
        return self._plain(message, "Choose an item from the menu.", menu=PRIMARY_MENU)

    async def handle_callback(self, callback: TelegramCallback) -> TelegramOutboundMessage:
        payload_hash = stable_event_hash({"data": callback.data, "user": callback.telegram_user_id})
        existing = await self.session.scalar(
            select(TelegramCallbackReceipt).where(
                TelegramCallbackReceipt.callback_query_id == callback.callback_query_id
            )
        )
        if existing:
            return self._outbound_from_payload(existing.result_payload)
        conversation = await self._conversation(callback.telegram_user_id)
        if conversation is None:
            return await self._store_callback(
                callback,
                payload_hash,
                None,
                TelegramOutboundMessage(
                    chat_id=callback.chat_id,
                    text="This action expired. Send /start to resume.",
                    menu=PRIMARY_MENU,
                ),
            )
        callback_user_id = conversation.user_id
        try:
            await self._push_navigation(conversation, callback.data)
            if callback.data == "accept_disclaimer":
                response = await self._accept_disclaimer(callback, conversation)
            elif callback.data == "back:previous":
                response = await self._previous_callback(callback, conversation)
            elif callback.data == "back:main":
                response = await self._main_menu_callback(callback, conversation)
            elif callback.data == "account:auth":
                response = self._account_auth_callback(callback)
            elif callback.data in {"account:signup", "account:signin"}:
                response = await self._account_link_callback(callback, conversation)
            elif callback.data == "telegram_link:confirm":
                response = await self._confirm_dashboard_telegram_link(callback, conversation)
            elif callback.data == "telegram_link:cancel":
                response = await self._cancel_dashboard_telegram_link(callback, conversation)
            elif callback.data == "open_dashboard":
                response = await self._dashboard_callback(
                    TelegramCallback(
                        callback_query_id=callback.callback_query_id,
                        telegram_user_id=callback.telegram_user_id,
                        chat_id=callback.chat_id,
                        data="dashboard:home",
                        message_id=callback.message_id,
                        created_at=callback.created_at,
                    ),
                    conversation,
                )
            elif callback.data.startswith("menu:"):
                response = await self._render_callback_screen(
                    callback.data, callback, conversation
                ) or self._plain_callback(callback, "That menu is no longer available.")
            elif callback.data == "claim_trial":
                response = await self._account_link_callback(
                    TelegramCallback(
                        callback_query_id=callback.callback_query_id,
                        telegram_user_id=callback.telegram_user_id,
                        chat_id=callback.chat_id,
                        data="account:signup",
                        message_id=callback.message_id,
                        created_at=callback.created_at,
                    ),
                    conversation,
                )
            elif callback.data == "sample_alert":
                response = self._about_callback(callback)
            elif callback.data == "sample_proof":
                response = self._sample_proof_callback(callback)
            elif callback.data == "mute_near_miss":
                response = self._plain_callback(
                    callback,
                    "Near-Miss alerts muted for this sample context. Live mute settings are "
                    "managed per strategy in Settings.",
                    buttons=self._back_buttons("dashboard:settings"),
                )
            elif callback.data in {"proof:view", "mute_strategy", "ignore_symbol"}:
                response = await self._utility_callback(callback, conversation)
            elif callback.data == "how_it_works":
                response = self._about_callback(callback)
            elif callback.data == "pricing":
                response = self._pricing_callback(callback)
            elif callback.data == "create_monitor":
                response = await self._dashboard_callback(
                    TelegramCallback(
                        callback_query_id=callback.callback_query_id,
                        telegram_user_id=callback.telegram_user_id,
                        chat_id=callback.chat_id,
                        data="dashboard:builder",
                        message_id=callback.message_id,
                        created_at=callback.created_at,
                    ),
                    conversation,
                )
            elif callback.data == "quick_scan":
                response = await self._dashboard_callback(
                    TelegramCallback(
                        callback_query_id=callback.callback_query_id,
                        telegram_user_id=callback.telegram_user_id,
                        chat_id=callback.chat_id,
                        data="dashboard:scan",
                        message_id=callback.message_id,
                        created_at=callback.created_at,
                    ),
                    conversation,
                )
            elif callback.data == "mode_describe":
                conversation.flow = "create_monitor"
                conversation.step = "collect_setup_text"
                await self.session.flush()
                response = self._plain_callback(
                    callback,
                    "Describe the setup in one message.\n\n"
                    f"{MONITOR_FILTER_HELP}\n\n"
                    "Example: bullish liquidity sweep, price above the 4h 200 EMA, volume "
                    "at least 1.5x average.",
                    buttons=self._back_buttons("back:create"),
                )
            elif callback.data == "mode_template":
                response = self._template_menu_callback(callback)
            elif callback.data == "mode_import":
                conversation.flow = "create_monitor"
                conversation.step = "collect_setup_text"
                await self.session.flush()
                response = self._plain_callback(
                    callback,
                    "Paste your strategy description or JSON-like rules. I will convert it "
                    "into a draft for approval before it can monitor live.",
                    buttons=self._back_buttons("back:create"),
                )
            elif callback.data.startswith("template:"):
                response = await self._create_from_template(callback, conversation)
            elif callback.data == "approve_strategy":
                response = await self._approve_strategy(callback, conversation)
            elif callback.data == "activate_strategy":
                response = await self._activate_strategy(callback, conversation)
            elif callback.data == "save_draft":
                conversation.flow = "main_menu"
                conversation.step = "idle"
                await self.session.flush()
                response = self._plain_callback(
                    callback,
                    "Draft saved. It will not scan live until you approve and activate it.",
                    buttons=self._back_buttons("dashboard:monitors"),
                )
            elif callback.data == "cancel":
                response = await self._main_menu_callback(callback, conversation)
            elif callback.data == "explain_rule":
                response = await self._explain_current_strategy_callback(callback, conversation)
            elif callback.data == "back:create":
                response = await self._dashboard_callback(
                    TelegramCallback(
                        callback_query_id=callback.callback_query_id,
                        telegram_user_id=callback.telegram_user_id,
                        chat_id=callback.chat_id,
                        data="dashboard:builder",
                        message_id=callback.message_id,
                        created_at=callback.created_at,
                    ),
                    conversation,
                )
            elif callback.data == "billing:free":
                response = await self._activate_free_plan_message(
                    self._message_from_callback(callback),
                    conversation,
                )
            elif callback.data.startswith("billing:checkout:"):
                response = await self._billing_checkout_message(
                    self._message_from_callback(callback),
                    conversation,
                    callback.data.rsplit(":", 1)[-1],
                )
            elif callback.data.startswith("monitor:"):
                _, action, strategy_id = callback.data.split(":", 2)
                if action == "manage":
                    response = await self._monitor_manage_options_by_id(
                        callback,
                        conversation,
                        UUID(strategy_id),
                    )
                else:
                    response = await self._handle_monitor_action_by_id(
                        self._message_from_callback(callback),
                        conversation,
                        action,
                        UUID(strategy_id),
                    )
            elif (
                callback.data == "scan:template"
                or callback.data.startswith("scan_provider:")
                or callback.data == "scan:new"
                or callback.data.startswith("scan_template:")
            ):
                response = await self._dashboard_callback(
                    TelegramCallback(
                        callback_query_id=callback.callback_query_id,
                        telegram_user_id=callback.telegram_user_id,
                        chat_id=callback.chat_id,
                        data="dashboard:scan",
                        message_id=callback.message_id,
                        created_at=callback.created_at,
                    ),
                    conversation,
                )
            elif callback.data == "near:top":
                response = await self._latest_setups(
                    self._message_from_callback(callback), conversation
                )
            elif callback.data == "near:one_left":
                response = await self._latest_setups(
                    self._message_from_callback(callback), conversation, category="forming"
                )
            elif callback.data.startswith("latest:"):
                response = await self._latest_setups(
                    self._message_from_callback(callback),
                    conversation,
                    category=callback.data.partition(":")[2],
                )
            elif (
                callback.data.startswith("settings:day:")
                or callback.data.startswith("settings:hour:")
                or callback.data.startswith("settings:near_miss:")
                or callback.data.startswith("settings:timezone:")
                or callback.data.startswith("settings:")
            ):
                response = await self._dashboard_callback(
                    TelegramCallback(
                        callback_query_id=callback.callback_query_id,
                        telegram_user_id=callback.telegram_user_id,
                        chat_id=callback.chat_id,
                        data="dashboard:settings",
                        message_id=callback.message_id,
                        created_at=callback.created_at,
                    ),
                    conversation,
                )
            elif callback.data.startswith("dashboard:"):
                response = await self._dashboard_callback(callback, conversation)
            elif callback.data.startswith("dashboard_lifecycle:"):
                response = await self._dashboard_callback(
                    TelegramCallback(
                        callback_query_id=callback.callback_query_id,
                        telegram_user_id=callback.telegram_user_id,
                        chat_id=callback.chat_id,
                        data="dashboard:lifecycles",
                        message_id=callback.message_id,
                        created_at=callback.created_at,
                    ),
                    conversation,
                )
            elif callback.data.startswith("mute_symbol:"):
                response = await self._mute_symbol_from_alert(callback, conversation)
            elif callback.data.startswith("mute_strategy:"):
                response = await self._mute_strategy_from_alert(callback, conversation)
            elif callback.data in {
                "proof:view",
                "mute_strategy",
                "ignore_symbol",
                "monitors:active",
                "monitors:drafts",
                "monitors:paused",
                "scan:existing",
                "scan:previous",
            }:
                response = await self._utility_callback(callback, conversation)
            elif callback.data.startswith("feedback:"):
                response = await self._feedback(callback, conversation)
            elif callback.data.startswith("support:"):
                response = await self._support(callback, conversation)
            else:
                response = self._plain_callback(callback, "That action is no longer available.")
        except (BillingError, OnboardingError, StrategyGateError) as exc:
            response = self._action_needed_callback(callback, conversation, exc)
        return await self._store_callback(callback, payload_hash, callback_user_id, response)

    async def render_confirmed_alert(
        self, chat_id: str, result: EvaluationResult
    ) -> TelegramOutboundMessage:
        return TelegramOutboundMessage(
            chat_id=chat_id,
            text=render_confirmed_alert(result),
            buttons=[
                self._dashboard_button(
                    ACTION_LABELS["opportunity"], OPPORTUNITIES_PATH
                ),
                self._dashboard_button(ACTION_LABELS["dashboard"]),
                TelegramButton(ACTION_LABELS["mute"], "mute_strategy"),
            ],
            menu=[],
        )

    async def render_lifecycle_alert(
        self, chat_id: str, result: EvaluationResult
    ) -> TelegramOutboundMessage:
        return TelegramOutboundMessage(
            chat_id=chat_id,
            text=render_lifecycle_update(result),
            buttons=[
                self._dashboard_button(
                    ACTION_LABELS["opportunity"], OPPORTUNITIES_PATH
                ),
                self._dashboard_button(ACTION_LABELS["dashboard"]),
                TelegramButton(ACTION_LABELS["mute"], "mute_strategy"),
            ],
            menu=[],
        )

    async def prevent_duplicate_delivery(self, alert_id: UUID, destination_key: str) -> bool:
        existing = await self.session.scalar(
            select(AlertDelivery.id).where(
                AlertDelivery.alert_id == alert_id,
                AlertDelivery.channel == DeliveryChannel.TELEGRAM,
                AlertDelivery.destination_key == destination_key,
            )
        )
        if existing:
            return False
        self.session.add(
            AlertDelivery(
                alert_id=alert_id,
                channel=DeliveryChannel.TELEGRAM,
                destination_key=destination_key,
                status=DeliveryStatus.PENDING,
            )
        )
        await self.session.commit()
        return True

    async def _accept_disclaimer(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        return self._plain_callback(
            callback,
            "Risk acknowledgement now appears after your setup is interpreted and previewed, "
            "right before live activation. Trial claiming requires sign-up first.",
            buttons=[
                TelegramButton("Sign up / sign in", "account:auth"),
                TelegramButton("🏠 Main Menu", "back:main"),
            ],
        )

    async def _mute_strategy_from_alert(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage:
        raw_version_id = callback.data.partition(":")[2]
        try:
            version_id = UUID(raw_version_id)
        except ValueError:
            return self._plain_callback(
                callback,
                "This monitor mute action has expired.",
                buttons=[TelegramButton("Go Back", "back:previous")],
            )
        version = await self.session.get(StrategyVersion, version_id)
        strategy = await self.session.get(Strategy, version.strategy_id) if version else None
        if strategy is None or strategy.user_id != conversation.user_id:
            return self._plain_callback(
                callback,
                "This monitor mute action is unavailable.",
                buttons=[TelegramButton("Go Back", "back:previous")],
            )
        preference = await self.session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == conversation.user_id)
        )
        if preference is None:
            preference = DashboardPreference(
                user_id=conversation.user_id,
                theme="light",
                default_timezone="UTC",
            )
            self.session.add(preference)
        settings = dict(preference.notification_preferences or {})
        muted_until = dict(settings.get("muted_strategy_until", {}) or {})
        muted_until[str(version_id)] = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
        settings["muted_strategy_until"] = muted_until
        preference.notification_preferences = settings
        self.session.add(
            AuditEvent(
                actor_user_id=conversation.user_id,
                actor_type="telegram_user",
                action="strategy.notifications_muted",
                target_type="strategy_version",
                target_id=str(version_id),
                metadata_redacted={"source": "alert_action"},
                created_at=datetime.now(UTC),
            )
        )
        await self.session.commit()
        return self._plain_callback(
            callback,
            f"{strategy.name} notifications are muted for 24 hours. "
            "The monitor and evidence remain saved.",
            buttons=[
                self._dashboard_button("Open Settings", "/dashboard/settings"),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _mute_symbol_from_alert(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage:
        raw_alert_id = callback.data.partition(":")[2]
        try:
            alert_id = UUID(raw_alert_id)
        except ValueError:
            return self._plain_callback(
                callback,
                "This symbol mute action has expired.",
                buttons=[TelegramButton("🏠 Main Menu", "back:main")],
            )
        alert = await self.session.get(Alert, alert_id)
        if alert is None or alert.user_id != conversation.user_id:
            return self._plain_callback(
                callback,
                "This alert action is unavailable or belongs to another account.",
                buttons=[TelegramButton("🏠 Main Menu", "back:main")],
            )
        proof = alert.proof_receipt or {}
        symbol = str(proof.get("symbol") or "").upper()
        if not symbol or alert.strategy_version_id is None:
            return self._plain_callback(
                callback,
                "This alert does not include enough strategy-symbol context to mute safely.",
                buttons=[
                    self._dashboard_button("Dashboard", "/dashboard/settings"),
                    TelegramButton("🏠 Main Menu", "back:main"),
                ],
            )
        preference = await self.session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == conversation.user_id)
        )
        if preference is None:
            preference = DashboardPreference(
                user_id=conversation.user_id,
                theme="light",
                default_timezone="UTC",
            )
            self.session.add(preference)
        settings = dict(preference.notification_preferences or {})
        muted_by_strategy = {
            str(key): list(value or [])
            for key, value in (settings.get("muted_strategy_symbols", {}) or {}).items()
        }
        version_key = str(alert.strategy_version_id)
        symbols = {str(item).upper() for item in muted_by_strategy.get(version_key, [])}
        symbols.add(symbol)
        muted_by_strategy[version_key] = sorted(symbols)
        settings["muted_strategy_symbols"] = muted_by_strategy
        preference.notification_preferences = settings
        self.session.add(
            AuditEvent(
                actor_user_id=conversation.user_id,
                actor_type="telegram_user",
                action="strategy_symbol.notifications_muted",
                target_type="alert",
                target_id=str(alert.id),
                metadata_redacted={
                    "strategy_version_id": version_key,
                    "symbol": symbol,
                    "source": "telegram_alert_action",
                },
                created_at=datetime.now(UTC),
            )
        )
        await self.session.commit()
        return self._plain_callback(
            callback,
            f"🔕 Muted {symbol} for this strategy. "
            "Future setups from this pair will not be delivered.",
            buttons=[
                self._dashboard_button("🔄 Lifecycles", LIFECYCLES_PATH),
                TelegramButton("🏠 Main Menu", "back:main"),
            ],
        )

    def _main_menu_buttons(
        self, *, linked: bool, trial_claimed: bool = False
    ) -> list[TelegramButton]:
        buttons = [
            TelegramButton("📋 My Monitors", "menu:my_monitors"),
            TelegramButton("🔄 Lifecycles", "menu:latest_setups"),
            TelegramButton("🎁 Trial", "account:signup"),
            TelegramButton("💸 Pricing", "pricing"),
            TelegramButton("⚙️ Settings", "dashboard:settings"),
            TelegramButton("🆘 Support", "menu:support"),
            TelegramButton("ℹ️ About", "menu:about"),
        ]
        if linked:
            buttons.insert(0, self._dashboard_button("📊 Dashboard"))
        else:
            buttons.append(TelegramButton("Sign up / sign in", "account:auth"))
        return buttons

    @staticmethod
    def _normalize_menu_text(text: str) -> str:
        normalized = text.strip().replace("\ufe0f", "")
        for prefix in (
            "🔍 ",
            "📡 ",
            "📋 ",
            "🔄 ",
            "🚦 ",
            "🎬 ",
            "✅ ",
            "🎁 ",
            "💳 ",
            "💸 ",
            "⚙ ",
            "🆘 ",
            "ℹ ",
        ):
            normalized = normalized.removeprefix(prefix)
        legacy = {
            "Why No Alert?": "Lifecycles",
            "Latest Setups": "Lifecycles",
            "Subscription": "Pricing",
            "Pricings": "Pricing",
            "See How It Works": "About",
        }
        return legacy.get(normalized, normalized)

    async def _free_command(self, message: TelegramInboundMessage) -> TelegramOutboundMessage:
        conversation = await self._conversation(message.telegram_user_id)
        linked = (
            await self._has_email_identity(conversation.user_id)
            if conversation and conversation.user_id
            else False
        )
        text = (
            "Welcome to the free Hilal Markets starter screen.\n\n"
            "Use the free/trial path to create a monitor, preview how proof receipts work, "
            "and start monitoring without automatic trading.\n\n"
            "You stay responsible for every trading decision."
        )
        return self._plain(
            message,
            text,
            buttons=[
                TelegramButton("Claim trial", "account:signup"),
                TelegramButton(
                    "Open Main Website",
                    "external:website",
                    url=self._dashboard_url("/"),
                ),
                *([] if linked else [TelegramButton("Sign up / sign in", "account:auth")]),
            ],
            menu=["Trial", "About"],
        )

    def _about_text(self) -> str:
        return (
            "ℹ️ About Hilal Markets\n\n"
            "📌 What it does:\n"
            "- Monitors approved crypto spot-market conditions.\n"
            "- Tracks setup lifecycles from forming to complete, invalidated, or expired.\n"
            "- Sends compact alerts with deterministic proof.\n\n"
            "🧠 How it works:\n"
            "1. You describe what to watch in the Dashboard.\n"
            "2. AI converts words into structured rules.\n"
            "3. You approve the interpretation.\n"
            "4. The deterministic scanner monitors markets.\n\n"
            "🛡️ What it does not do:\n"
            "- It does not place trades.\n"
            "- It does not guarantee outcomes.\n"
            "- It does not ask for wallet seed phrases or withdrawal keys.\n\n"
            "Use Telegram for quick status and alerts. Use Dashboard for monitor building, "
            "settings, tickets, and billing."
        )

    def _about_message(self, message: TelegramInboundMessage) -> TelegramOutboundMessage:
        return self._plain(
            message,
            self._about_text(),
            buttons=[
                self._dashboard_button("📊 Dashboard"),
                TelegramButton("💸 Pricing", "pricing"),
                TelegramButton("🏠 Main Menu", "back:main"),
            ],
        )

    def _about_callback(self, callback: TelegramCallback) -> TelegramOutboundMessage:
        return self._plain_callback(
            callback,
            self._about_text(),
            buttons=[
                self._dashboard_button("📊 Dashboard"),
                TelegramButton("💸 Pricing", "pricing"),
            ],
        )

    def _pricing_text(self) -> str:
        if self.settings.waitlist_mode:
            # The public pricing page is not published before launch, so a button here
            # would open the waitlist under a "Pricing" label. Say what is true instead.
            return (
                "💸 Pricing\n\nHilal Markets is invite-only during its private beta, so "
                "plans and prices are not published yet. Nothing is charged in the beta."
            )
        return "💸 Pricing\n\nCompare plans on the public pricing page."

    def _pricing_buttons(self) -> list[TelegramButton]:
        if self.settings.waitlist_mode:
            return []
        return [
            TelegramButton(
                "Open Pricing",
                "external:pricing",
                url=self._dashboard_url("/pricing#pricing"),
            )
        ]

    def _pricing_message(self, message: TelegramInboundMessage) -> TelegramOutboundMessage:
        return self._plain(
            message,
            self._pricing_text(),
            buttons=self._pricing_buttons(),
        )

    def _pricing_callback(self, callback: TelegramCallback) -> TelegramOutboundMessage:
        return self._plain_callback(
            callback,
            self._pricing_text(),
            buttons=self._pricing_buttons(),
        )

    def _support_menu_message(self, message: TelegramInboundMessage) -> TelegramOutboundMessage:
        buttons = []
        if self.settings.support_telegram_username:
            buttons.append(
                TelegramButton(
                    "Telegram support",
                    "external:support",
                    url=f"https://t.me/{self.settings.support_telegram_username}",
                )
            )
        buttons.extend(
            [
                self._dashboard_button("Create a ticket", "/dashboard/support"),
            ]
        )
        return self._plain(
            message,
            "🆘 Support\n\nChoose one option. Tickets are created in Dashboard so context "
            "and screenshots stay attached.",
            buttons=buttons,
        )

    @staticmethod
    def _template_menu_message(message: TelegramInboundMessage) -> TelegramOutboundMessage:
        return TelegramBotService._plain(
            message,
            "Choose a template. I will create a structured draft that still requires your "
            "approval before live monitoring.",
            buttons=TelegramBotService._template_buttons("back:create"),
            menu=TelegramBotService._template_menu_labels("Go Back"),
        )

    def _account_auth_callback(self, callback: TelegramCallback) -> TelegramOutboundMessage:
        # Pre-launch, "sign up on the Dashboard" is an instruction most readers cannot
        # follow: accounts are issued by invitation. The buttons stay, because an invited
        # person still needs them to link Telegram to the account they were given.
        text = (
            "Hilal Markets is invite-only during its private beta. Join the waitlist on "
            "the website to be considered. If you have already been invited, use the "
            "buttons below to link this Telegram chat to your account."
            if self.settings.waitlist_mode
            else "Sign up or sign in on the Dashboard, then Telegram will link to that "
            "account for trial status, monitor counts, subscription dates and alerts."
        )
        return self._plain_callback(
            callback,
            text,
            buttons=[
                TelegramButton("Sign up", "account:signup"),
                TelegramButton("Sign in", "account:signin"),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    def _template_menu_callback(self, callback: TelegramCallback) -> TelegramOutboundMessage:
        return self._plain_callback(
            callback,
            "Choose a template. I will create a structured draft that still requires "
            "your approval before live monitoring.",
            buttons=self._template_buttons("back:create"),
        )

    def _scan_template_menu_callback(self, callback: TelegramCallback) -> TelegramOutboundMessage:
        return self._plain_callback(
            callback,
            "Choose a template for a one-off Quick Scan. This will not save or activate a monitor.",
            buttons=self._scan_template_buttons(),
        )

    @staticmethod
    def _template_buttons(back_action: str) -> list[TelegramButton]:
        return [
            *[
                TelegramButton(template.label, f"template:{key}")
                for key, template in BUILTIN_STRATEGY_TEMPLATES.items()
            ],
            TelegramButton("Go Back", back_action),
        ]

    @staticmethod
    def _scan_template_buttons() -> list[TelegramButton]:
        return [
            *[
                TelegramButton(template.label, f"scan_template:{key}")
                for key, template in BUILTIN_STRATEGY_TEMPLATES.items()
            ],
            TelegramButton("Go Back", "back:previous"),
        ]

    @staticmethod
    def _template_menu_labels(back_label: str) -> list[str]:
        return [template.label for template in BUILTIN_STRATEGY_TEMPLATES.values()] + [back_label]

    def _sample_alert_callback(self, callback: TelegramCallback) -> TelegramOutboundMessage:
        return TelegramOutboundMessage(
            chat_id=callback.chat_id,
            text=(
                "SOL/USDT — 85% complete\n\nPassed:\n✅ Price above four-hour EMA 200\n"
                "✅ Liquidity sweep detected\n\nMissing:\n"
                "⏳ Volume is 1.42x; required: 1.50x\n"
                "⏳ Fifteen-minute candle has not closed\n\nStatus: Forming"
            ),
            buttons=[
                TelegramButton("View Proof", "sample_proof"),
                TelegramButton("Mute Near-Miss", "mute_near_miss"),
                TelegramButton("Go Back", "back:previous"),
            ],
            menu=[],
        )

    def _sample_proof_callback(self, callback: TelegramCallback) -> TelegramOutboundMessage:
        return self._plain_callback(
            callback,
            "Sample Proof Receipt\n\n"
            "Strategy: Liquidity Sweep Continuation v1\n"
            "Symbol: SOL/USDT\nExchange: Binance\nTimeframe: 15m\n"
            "Completion: 85%\n\n"
            "PASS - Price above 4h EMA 200\n"
            "PASS - Liquidity sweep detected\n"
            "MISSING - Volume 1.42x; required 1.50x\n"
            "PENDING - 15m candle close\n\n"
            "This is a sample receipt, not a real market result.",
            buttons=self._back_buttons(),
        )

    async def _claim_trial(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        if not await self._has_email_identity(user_id):
            return self._plain_callback(
                callback,
                "Claiming a trial requires a Dashboard account first so trial limits, "
                "subscription ending date and account recovery are tied to you safely.",
                buttons=[
                    TelegramButton("Sign up", "account:signup"),
                    TelegramButton("Sign in", "account:signin"),
                    TelegramButton("🏠 Main Menu", "back:main"),
                ],
            )
        try:
            trial = await TrialLifecycleService(self.session, self.settings).activate(user_id)
            await self.session.commit()
            if trial.status.value == "eligible":
                text = (
                    "Trial claimed successfully.\n\n"
                    "Your trial will start when your first approved live monitor is activated. "
                    "Next: create a monitor or run a market preview."
                )
            else:
                text = f"Trial status: {trial.status.value}."
            await AdminNotificationService(self.settings).send(f"Trial claimed: user:{user_id}")
            return self._plain_callback(
                callback,
                text,
                buttons=[
                    self._dashboard_button("Dashboard"),
                    TelegramButton("🏠 Main Menu", "back:main"),
                ],
            )
        except TrialError as exc:
            await self.session.rollback()
            return self._plain_callback(callback, f"Trial cannot be claimed: {escape(str(exc))}")

    async def _activate_free_plan_message(
        self, message: TelegramInboundMessage, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        if not await self._has_email_identity(user_id):
            return self._plain(
                message,
                "Activate Free Plan requires sign up/sign in first so usage and account recovery "
                "are tied to your profile.",
                buttons=[
                    TelegramButton("Sign up", "account:signup"),
                    TelegramButton("Sign in", "account:signin"),
                ],
                menu=["Sign up", "Sign in", "Go Back"],
            )
        try:
            await BillingService(self.session, self.settings).activate_free_plan(
                user_id=user_id,
                plan_code="demo",
            )
            await self.session.commit()
            await AdminNotificationService(self.settings).send(f"Free plan: user:{user_id}")
        except BillingError as exc:
            await self.session.rollback()
            return self._plain(message, f"Free plan could not be activated: {escape(str(exc))}")
        return self._plain(
            message,
            "Free plan activated. You can create one live monitor within Demo limits.",
            menu=["Create Monitor", "My Monitors", "Subscription", "Go Back"],
        )

    async def _billing_checkout_message(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        plan_code: str,
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        if not await self._has_email_identity(user_id):
            try:
                signup_url = await TelegramAccountLinkService(self.session, self.settings).create(
                    user_id=user_id,
                    telegram_user_id=message.telegram_user_id,
                    target="signup",
                )
                signin_url = await TelegramAccountLinkService(self.session, self.settings).create(
                    user_id=user_id,
                    telegram_user_id=message.telegram_user_id,
                    target="signin",
                )
                await self.session.commit()
            except TelegramAccountLinkError as exc:
                await self.session.rollback()
                return self._plain(message, f"Could not create account link: {escape(str(exc))}")
            return self._plain(
                message,
                "Connect a Dashboard account first. Payment links require sign up/sign in so "
                "the subscription attaches to the correct account.",
                buttons=[
                    TelegramButton("Open Sign Up", "external:signup", url=signup_url),
                    TelegramButton("Open Sign In", "external:signin", url=signin_url),
                ],
                menu=["Sign up", "Sign in", "Go Back"],
            )
        base = str(self.settings.public_base_url).rstrip("/")
        try:
            checkout = await BillingService(self.session, self.settings).checkout_session(
                user_id=user_id,
                plan_code=plan_code,
                success_url=f"{base}/billing/success",
                cancel_url=f"{base}/billing/cancel",
            )
            await self.session.commit()
            await AdminNotificationService(self.settings).send(
                f"Payment link: user:{user_id} {plan_code}"
            )
        except BillingError as exc:
            await self.session.rollback()
            return self._plain(message, f"Payment link failed: {escape(str(exc))}")
        return self._plain(
            message,
            f"Payment link created for {plan_code.title()}.\n\n"
            "Payment confirmation comes from the billing provider webhook.",
            buttons=[
                TelegramButton("Open Payment Link", "external:payment", url=checkout.checkout_url),
                self._dashboard_button("Subscription", "/dashboard/billing"),
            ],
            menu=["Subscription", "Support", "Go Back"],
        )

    async def _prepare_monitor_creation(self, conversation: TelegramConversationState) -> None:
        onboarding = await self.session.get(OnboardingSession, conversation.onboarding_session_id)
        if onboarding is None:
            return
        if onboarding.current_step in {OnboardingStep.DISCLAIMER, OnboardingStep.GUIDED_SETUP}:
            if onboarding.current_step == OnboardingStep.DISCLAIMER:
                onboarding.current_step = OnboardingStep.GUIDED_SETUP
                onboarding.version += 1
            onboarding.status = OnboardingStatus.IN_PROGRESS
            onboarding.blocked_reason = None
            return
        state = dict(onboarding.state_data or {})
        for key in (
            "guided_setup",
            "strategy_id",
            "strategy_version_id",
            "schema_hash",
        ):
            state.pop(key, None)
        onboarding.state_data = state
        onboarding.current_step = OnboardingStep.GUIDED_SETUP
        onboarding.status = OnboardingStatus.IN_PROGRESS
        onboarding.blocked_reason = None
        onboarding.last_error_code = None
        onboarding.version += 1

    async def _begin_create_monitor(
        self, message: TelegramInboundMessage, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        await self._prepare_monitor_creation(conversation)
        conversation.flow = "create_monitor"
        conversation.step = "choose_mode"
        await self.session.commit()
        return self._plain(
            message,
            "How would you like to create the monitor?\n\n" + MONITOR_FILTER_HELP,
            buttons=[
                TelegramButton("Use a template", "mode_template"),
                TelegramButton("Describe my setup", "mode_describe"),
                TelegramButton("Import strategy", "mode_import"),
                self._dashboard_button("My Drafts", "/dashboard/strategies/new#monitors"),
                TelegramButton("Go Back", "back:previous"),
            ],
            menu=[
                "Describe my setup",
                "Use a template",
                "Import strategy",
                "My Drafts",
                "Go Back",
            ],
        )

    async def _begin_create_monitor_callback(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        await self._prepare_monitor_creation(conversation)
        conversation.flow = "create_monitor"
        conversation.step = "choose_mode"
        await self.session.flush()
        return self._plain_callback(
            callback,
            "How would you like to create the monitor?\n\n" + MONITOR_FILTER_HELP,
            buttons=[
                TelegramButton("Use a template", "mode_template"),
                TelegramButton("Describe my setup", "mode_describe"),
                TelegramButton("Import strategy", "mode_import"),
                self._dashboard_button("My Drafts", "/dashboard/strategies/new#monitors"),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _run_light_scan_message(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage:
        return await self._run_light_scan_prompt(
            message,
            conversation,
            prompt=message.text,
            source_label="Free prompt",
        )

    async def _run_light_scan_template(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage:
        key = callback.data.partition(":")[2]
        template = BUILTIN_STRATEGY_TEMPLATES.get(key)
        if template is None:
            return self._plain_callback(
                callback,
                "That template is no longer available. Choose another template or describe a scan.",
                buttons=[
                    TelegramButton("Use Template", "scan:template"),
                    TelegramButton("Describe Condition", "scan:new"),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        provider = str((conversation.state_data or {}).get("scan_provider", "binance")).lower()
        if provider not in {"binance", "bybit"}:
            provider = "binance"
        definition = template.definition()
        definition = definition.model_copy(
            update={
                "universe": definition.universe.model_copy(update={"exchange": provider}),
                "risk": definition.risk.model_copy(
                    update={
                        "enabled": False,
                        "maximum_stop_percent": None,
                        "minimum_reward_to_risk": None,
                    }
                ),
            }
        )
        return await self._run_light_scan_definition(
            self._message_from_callback(callback),
            conversation,
            strategy=definition,
            source_label=f"Template: {template.label}",
        )

    async def _run_light_scan_prompt(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        *,
        prompt: str,
        source_label: str,
    ) -> TelegramOutboundMessage:
        provider = str((conversation.state_data or {}).get("scan_provider", "binance")).lower()
        if provider not in {"binance", "bybit"}:
            provider = "binance"
        guided = GuidedSetupRequest(
            exchange=provider,
            quote_currency="USDT",
            timeframe="15m",
            setup_mode="free_text",
            setup_text=prompt,
            trigger_mode="candle_close",
            maximum_stop_percent=None,
            minimum_reward_to_risk=None,
            forming_alerts=True,
            near_miss_threshold=70,
            delivery_channels=["telegram"],
        )
        preview = await configured_strategy_interpreter(self.settings).interpret(guided)
        if not self._definition_has_executable_conditions(preview.strategy):
            return self._plain(
                message,
                "Action needed: Quick Scan needs at least one supported deterministic "
                "condition.\n\n"
                "<b>Unsupported or unclear:</b>\n"
                + "\n".join(
                    f"<b>- {escape(issue.message)}</b>" for issue in preview.unsupported_conditions
                ),
                buttons=[
                    TelegramButton("Try Again", "quick_scan"),
                    TelegramButton("Use Template", "scan:template"),
                    TelegramButton("Go Back", "back:previous"),
                ],
                parse_mode="HTML",
            )
        return await self._run_light_scan_definition(
            message,
            conversation,
            strategy=preview.strategy,
            source_label=source_label,
        )

    async def _run_light_scan_definition(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        *,
        strategy: StrategyDefinition,
        source_label: str,
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        if self.market_data_provider is None:
            return self._plain(
                message,
                "Quick Scan is ready, but this Telegram runtime does not have a market-data "
                "provider attached. Open the dashboard Quick Scan page to run it.",
                buttons=[
                    self._dashboard_button("Open Quick Scan", _ONE_TIME_SCAN_PATH),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        request = OnDemandScanRequest(
            strategy=strategy,
            max_symbols=100000,
            light_scan=True,
        )
        try:
            response = await OnDemandScanService(
                self.session,
                self.market_data_provider,
                settings=self.settings,
            ).run(user_id, request)
        except OnDemandScanError as exc:
            await self.session.rollback()
            return self._plain(
                message,
                f"Action needed: {escape(str(exc))}",
                buttons=[
                    TelegramButton("Try Again", "quick_scan"),
                    self._dashboard_button("Open Dashboard", _ONE_TIME_SCAN_PATH),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        await self.session.commit()
        top_results = response.results[:5]
        if not top_results:
            return self._plain(
                message,
                "Quick Scan completed, but no markets returned useful matches.\n\n"
                "Try a broader prompt or leave symbols unrestricted in Dashboard.",
                buttons=[
                    TelegramButton("Run Again", "quick_scan"),
                    self._dashboard_button("Open Dashboard", _ONE_TIME_SCAN_PATH),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        timeframes = ", ".join([strategy.base_timeframe, *strategy.supporting_timeframes])
        required_rules = sum(
            1 for condition in strategy.conditions.children if getattr(condition, "required", True)
        )
        lines = [
            "Quick Scan results",
            "",
            "What I understood:",
            f"- Source: {source_label}",
            f"- Strategy: {strategy.name}",
            f"- Exchange: {strategy.universe.exchange}",
            f"- Market: {strategy.universe.market_type.value}",
            f"- Timeframes: {timeframes}",
            f"- Required rules: {required_rules}",
            "- Risk/R:R filter: "
            + (
                "not requested"
                if not strategy.risk.enabled
                else (
                    f"max stop {strategy.risk.maximum_stop_percent}%, "
                    f"minimum R:R {strategy.risk.minimum_reward_to_risk}"
                )
            ),
            "",
            f"Scanned {response.symbols_scanned} market(s). "
            f"Quota remaining today: {response.quota_remaining}.",
        ]
        if response.warnings:
            lines.extend(["", "Warnings:", *[f"- {warning}" for warning in response.warnings[:3]]])
        lines.append("")
        for index, result in enumerate(top_results, 1):
            passed = ", ".join(item.name for item in result.passed_conditions[:2]) or "none"
            missing = ", ".join(item.name for item in result.missing_conditions[:2]) or "none"
            lines.extend(
                [
                    f"{index}. {result.symbol} - {result.match_percentage:.0f}% match",
                    f"Passed: {passed}",
                    f"Missing: {missing}",
                    "",
                ]
            )
        return self._plain(
            message,
            "\n".join(lines).strip(),
            buttons=[
                TelegramButton("Save as Monitor", "create_monitor"),
                TelegramButton("Run Again", "quick_scan"),
                self._dashboard_button("Open Dashboard", _ONE_TIME_SCAN_PATH),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _receive_setup_text(
        self, message: TelegramInboundMessage, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        onboarding = await self.session.get(OnboardingSession, conversation.onboarding_session_id)
        if onboarding is None:
            raise OnboardingError("session_missing", "Onboarding session was not found")
        guided = GuidedSetupRequest(
            exchange="binance",
            quote_currency="USDT",
            timeframe="15m",
            setup_mode="free_text",
            setup_text=message.text,
            trigger_mode="candle_close",
            maximum_stop_percent=None,
            minimum_reward_to_risk=None,
            forming_alerts=True,
            near_miss_threshold=70,
            delivery_channels=["telegram"],
        )
        onboarding_service = OnboardingService(self.session, self.settings)
        if onboarding.current_step == OnboardingStep.DISCLAIMER:
            onboarding.current_step = OnboardingStep.GUIDED_SETUP
            onboarding.version += 1
        elif onboarding.current_step != OnboardingStep.GUIDED_SETUP:
            await self._prepare_monitor_creation(conversation)
            onboarding = await self.session.get(
                OnboardingSession, conversation.onboarding_session_id
            )
            if onboarding is None:
                raise OnboardingError("session_missing", "Onboarding session was not found")
        if onboarding.current_step == OnboardingStep.GUIDED_SETUP:
            await onboarding_service.save_guided_setup(onboarding, guided)
        preview = await configured_strategy_interpreter(self.settings).interpret(guided)
        strategy_service = StrategyService(self.session, self.settings.disclaimer_version)
        editing_strategy_id = (conversation.state_data or {}).get("editing_strategy_id")
        if editing_strategy_id:
            strategy = await self.session.get(Strategy, UUID(str(editing_strategy_id)))
            if strategy is None or strategy.user_id != user_id:
                raise StrategyGateError("strategy_missing", "Monitor to edit was not found.")
            version = await strategy_service.revise(
                strategy,
                preview.strategy,
                user_id=user_id,
                source_text=message.text,
                assumptions=preview.assumptions,
                ambiguities=[issue.model_dump(mode="json") for issue in preview.ambiguities],
                unsupported=[
                    issue.model_dump(mode="json") for issue in preview.unsupported_conditions
                ],
                interpreter=preview.interpreter,
            )
        else:
            strategy, version = await strategy_service.create_from_interpretation(
                user_id, preview, source_text=message.text
            )
        parent = (
            await self.session.get(StrategyVersion, version.parent_version_id)
            if version.parent_version_id
            else None
        )
        verification_service = VerifiedStrategyService(self.session, self.settings)
        await verification_service.prepare_version(
            user_id=user_id,
            strategy=strategy,
            version=version,
            parent=parent,
        )
        if self.market_data_provider is not None:
            await verification_service.run_saved_tests(
                user_id=user_id,
                version=version,
                provider=self.market_data_provider,
            )
        await onboarding_service.mark_interpreted(
            onboarding, strategy.id, version.id, preview.activation_blocked
        )
        conversation.flow = "create_monitor"
        conversation.step = "approval"
        conversation.state_data = {
            **conversation.state_data,
            "strategy_id": str(strategy.id),
            "strategy_version_id": str(version.id),
            "schema_hash": version.schema_hash,
        }
        conversation.state_data.pop("editing_strategy_id", None)
        await self.session.commit()
        await AdminNotificationService(self.settings).send(
            f"Monitor draft: user:{user_id} @{message.username or '-'}"
        )
        summary = self._strategy_summary(preview.strategy, preview)
        if preview.activation_blocked:
            return self._plain(
                message,
                "I need clarification before this can run.\n\n" + summary,
                parse_mode="HTML",
                buttons=[
                    TelegramButton("Edit", "mode_describe"),
                    TelegramButton("Cancel", "cancel"),
                ],
            )
        return self._plain(
            message,
            "Here is the structured interpretation. It will not monitor live until you approve.\n\n"
            + summary,
            parse_mode="HTML",
            buttons=[
                TelegramButton("Approve", "approve_strategy"),
                TelegramButton("Explain a rule", "explain_rule"),
                TelegramButton("Edit", "mode_describe"),
                TelegramButton("Save Draft", "save_draft"),
                TelegramButton("Cancel", "cancel"),
            ],
        )

    async def _create_from_template(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        template_key = callback.data.partition(":")[2]
        template = CREATE_TEMPLATES.get(template_key)
        if template is None:
            return self._plain_callback(
                callback,
                "That template is not available anymore.",
                buttons=self._back_buttons("back:create"),
            )
        name, setup_text = template
        message = TelegramInboundMessage(
            telegram_user_id=callback.telegram_user_id,
            chat_id=callback.chat_id,
            username=None,
            text=setup_text,
            message_id=callback.message_id,
            created_at=callback.created_at,
        )
        response = await self._receive_setup_text(message, conversation)
        return TelegramOutboundMessage(
            chat_id=response.chat_id,
            text=f"Template selected: {name}\n\n{response.text}",
            buttons=response.buttons,
            menu=response.menu,
            parse_mode=response.parse_mode,
            correlation_id=response.correlation_id,
        )

    async def _approve_strategy(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        state_data = conversation.state_data or {}
        if "strategy_version_id" not in state_data or "schema_hash" not in state_data:
            raise StrategyGateError(
                "step_out_of_order",
                "Complete strategy interpretation before approval.",
            )
        version = await self.session.get(StrategyVersion, UUID(state_data["strategy_version_id"]))
        if version is None:
            raise StrategyGateError("version_missing", "Strategy version not found")
        onboarding = await self.session.get(OnboardingSession, conversation.onboarding_session_id)
        service = StrategyService(
            self.session,
            self.settings.disclaimer_version,
            self.settings,
        )
        strategy = await self.session.get(Strategy, version.strategy_id)
        if strategy is None or strategy.user_id != user_id:
            raise StrategyGateError("strategy_missing", "Strategy not found")
        verification_service = VerifiedStrategyService(self.session, self.settings)
        await verification_service.prepare_version(
            user_id=user_id,
            strategy=strategy,
            version=version,
        )
        await verification_service.sync_interpretation(
            user_id=user_id,
            strategy=strategy,
            version=version,
        )
        try:
            await verification_service.approve_visible_draft(
                user_id=user_id,
                version=version,
                expected_schema_hash=state_data["schema_hash"],
            )
        except VerifiedStrategyError as exc:
            raise StrategyGateError(exc.code, str(exc)) from exc
        await service.approve(
            version,
            user_id=user_id,
            expected_schema_hash=state_data["schema_hash"],
        )
        if onboarding:
            await OnboardingService(self.session, self.settings).mark_approved(onboarding)
        preview = await service.run_preview(version, user_id=user_id, previewer=self.previewer)
        if onboarding:
            await OnboardingService(self.session, self.settings).mark_previewed(
                onboarding, preview.status == "succeeded"
            )
        conversation.step = "activation"
        await self.session.commit()
        return self._plain_callback(
            callback,
            f"Approved. Historical preview checked {preview.symbols_checked} symbols and "
            f"found {len(preview.sample_matches)} sample matches.\n\n"
            "Before live monitoring starts, confirm the risk acknowledgement: this is "
            "decision support, not financial advice, results can be delayed or wrong, "
            "and the system will not place trades for you.",
            buttons=[
                TelegramButton("I Understand - Activate", "activate_strategy"),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _activate_strategy(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        state_data = conversation.state_data or {}
        if "strategy_version_id" not in state_data:
            raise StrategyGateError(
                "step_out_of_order",
                "Complete strategy approval before activation.",
            )
        version = await self.session.get(StrategyVersion, UUID(state_data["strategy_version_id"]))
        if version is None:
            raise StrategyGateError("version_missing", "Strategy version not found")
        strategy_service = StrategyService(
            self.session,
            self.settings.disclaimer_version,
            self.settings,
        )
        if version.preview_status != "succeeded":
            retry_preview = await strategy_service.run_preview(
                version, user_id=user_id, previewer=self.previewer
            )
            if retry_preview.status != "succeeded":
                warning_text = "\n".join(f"- {item}" for item in retry_preview.warnings[:3])
                raise StrategyGateError(
                    "preview_required",
                    "A successful recent-market preview is required before activation.\n"
                    f"{warning_text}",
                )
        await self._record_risk_acknowledgement(
            user_id,
            source="telegram_strategy_activation",
        )
        strategy = await strategy_service.activate(
            version, user_id=user_id, strategy_name=version.schema_json["name"]
        )
        onboarding = await self.session.get(OnboardingSession, conversation.onboarding_session_id)
        if onboarding:
            await OnboardingService(self.session, self.settings).complete(onboarding)
        conversation.flow = "main_menu"
        conversation.step = "idle"
        await self.session.commit()
        await AdminNotificationService(self.settings).send(
            f"Monitor active: user:{user_id} strategy:{strategy.name[:40]}"
        )
        live_text = (
            "Live scanning is enabled. The worker scheduler will scan this monitor."
            if self.settings.scanning_enabled
            else "Live scanning is currently disabled in SCANNING_ENABLED."
        )
        return self._plain_callback(
            callback,
            f"Monitor activated: {escape(strategy.name)}. I’ll send Telegram alerts when your "
            f"approved conditions form or confirm.\n\n{live_text}",
        )

    async def _explain_current_strategy_callback(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        state_data = conversation.state_data or {}
        version_id = state_data.get("strategy_version_id")
        if not version_id:
            return self._plain_callback(
                callback,
                "Explain rules is available after I interpret your setup.\n\n"
                "Use Edit to send a different setup description. Explain only describes the "
                "current draft; it does not change anything.",
                buttons=[
                    TelegramButton("Describe Setup", "mode_describe"),
                    TelegramButton("Use Template", "mode_template"),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        version = await self.session.get(StrategyVersion, UUID(version_id))
        if version is None:
            raise StrategyGateError("version_missing", "Strategy version not found")
        strategy = StrategyDefinition.model_validate(version.schema_json)
        rule_lines = []
        for child in strategy.conditions.children:
            label = getattr(child, "label", getattr(child, "key", "condition"))
            timeframe = getattr(child, "timeframe", strategy.base_timeframe)
            comparator = getattr(getattr(child, "comparator", None), "value", None)
            if comparator:
                rule_lines.append(
                    f"- {label} ({timeframe}): scanner checks whether this rule is {comparator}."
                )
            else:
                rule_lines.append(f"- {label} ({timeframe})")
        return self._plain_callback(
            callback,
            "Explain rules\n\n"
            "This screen explains the draft you are reviewing. It does not edit the monitor. "
            "Use Edit if you want to change the setup text and generate a new interpretation.\n\n"
            "Current deterministic rules:\n"
            + "\n".join(rule_lines)
            + "\n\nEvery alert later includes actual value, required value, pass/fail/pending "
            "state, timeframe, candle timestamp and data freshness.",
            buttons=[
                TelegramButton("Approve", "approve_strategy"),
                TelegramButton("Edit", "mode_describe"),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _show_near_miss(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        *,
        mode: str = "top",
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        items = await self.near_miss_provider.top(
            user_id,
            strategy_id=None,
            limit=20 if mode == "one_left" else 5,
            minimum_score=1 if mode == "one_left" else 70,
        )
        if mode == "one_left":
            items = [item for item in items if len(item.missing) == 1 and item.score < 100][:5]
            text = (
                "One Condition Remaining\n\n"
                + render_near_miss_list(items).removeprefix("Near-Miss Radar\n\n")
                if items
                else (
                    "One Condition Remaining\n\n"
                    "No symbols currently have exactly one missing condition."
                )
            )
        else:
            text = render_near_miss_list(items)
        return self._plain(
            message,
            text,
            buttons=[
                TelegramButton("Top Near-Misses", "near:top"),
                TelegramButton("One Condition Remaining", "near:one_left"),
                self._dashboard_button("Dashboard", OPPORTUNITIES_PATH),
                TelegramButton("🏠 Main Menu", "back:main"),
            ],
        )

    async def _my_monitors(
        self, message: TelegramInboundMessage, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        strategies = (
            await self.session.scalars(
                select(Strategy)
                .where(
                    Strategy.user_id == user_id,
                    Strategy.status != StrategyStatus.ARCHIVED,
                )
                .order_by(Strategy.created_at)
            )
        ).all()
        if strategies:
            lines = []
            for strategy in strategies[:10]:
                status = strategy.status.value.replace("_", " ").title()
                icon = {
                    StrategyStatus.ACTIVE: "🟣",
                    StrategyStatus.PAUSED: "⏸️",
                    StrategyStatus.DRAFT: "📝",
                    StrategyStatus.ARCHIVED: "🗄️",
                }.get(strategy.status, "•")
                scan_state = (
                    "live alerts on" if strategy.status == StrategyStatus.ACTIVE else "alerts off"
                )
                lines.append(f"{icon} {escape(strategy.name)}\n   Status: {status} · {scan_state}")
            text = (
                "📋 My Monitors\n\n"
                + "\n\n".join(lines)
                + "\n\nTap Manage to pause, resume, or archive a monitor."
            )
        else:
            text = "📋 My Monitors\n\nNo monitors yet. Create your first monitor in Dashboard."
        state = dict(conversation.state_data or {})
        state["monitor_index_map"] = {
            str(index): str(strategy.id) for index, strategy in enumerate(strategies[:10], start=1)
        }
        conversation.state_data = state
        await self.session.flush()
        control_buttons: list[TelegramButton] = []
        for strategy in strategies[:5]:
            label = strategy.name if len(strategy.name) <= 24 else f"{strategy.name[:21]}..."
            control_buttons.append(
                TelegramButton(f"Manage {label}", f"monitor:manage:{strategy.id}")
            )
        return self._plain(
            message,
            text,
            buttons=[
                *control_buttons,
                self._dashboard_button("Dashboard", "/dashboard/strategies/new#monitors"),
                TelegramButton("🏠 Main Menu", "back:main"),
            ],
        )

    async def _scan_market_now(
        self, message: TelegramInboundMessage, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        self._require_user_id(conversation)
        provider = (conversation.state_data or {}).get("scan_provider", "binance")
        return self._plain(
            message,
            "Quick Scan\n\nChoose how to run an on-demand scan. Quotas and plan limits "
            "are enforced by the API and worker layer.\n\n"
            f"Data provider: {str(provider).title()}",
            buttons=[
                TelegramButton("Provider: Binance", "scan_provider:binance"),
                TelegramButton("Provider: Bybit", "scan_provider:bybit"),
                TelegramButton("Use Existing Strategy", "scan:existing"),
                TelegramButton("Describe New Condition", "scan:new"),
                TelegramButton("Use Template", "scan:template"),
                TelegramButton("Previous Scans", "scan:previous"),
                self._dashboard_button("Open Dashboard", _ONE_TIME_SCAN_PATH),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _set_scan_provider(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage:
        provider = callback.data.partition(":")[2].lower()
        if provider not in {"binance", "bybit"}:
            provider = "binance"
        conversation.state_data = {**(conversation.state_data or {}), "scan_provider": provider}
        await self.session.flush()
        return self._plain_callback(
            callback,
            f"Quick Scan data provider set to {provider.title()}.\n\n"
            "Run a template or describe a condition to scan with this provider.",
            buttons=[
                TelegramButton("Describe Condition", "scan:new"),
                TelegramButton("Use Template", "scan:template"),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _latest_setups(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        *,
        category: str | None = None,
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        query = (
            select(SetupInstance)
            .where(SetupInstance.user_id == user_id)
            .order_by(SetupInstance.updated_at.desc())
            .limit(10)
        )
        if category:
            states = self._setup_states_for_category(category)
            if states:
                query = query.where(SetupInstance.state.in_(states))
        setups = (await self.session.scalars(query)).all()
        category_labels = {
            "confirmed": "✅ Confirmed",
            "forming": "⏳ Forming",
            "invalidated": "❌ Invalidated",
            "expired": "⌛ Expired",
        }
        title = (
            f"🔄 Lifecycles · {category_labels.get(category, category.title())}"
            if category
            else "🔄 Lifecycles"
        )
        if setups:
            lines = []
            for setup in setups:
                state = setup.state.value.replace("_", " ").title()
                icon = {
                    SetupLifecycleState.CANDIDATE_DETECTED: "🔎",
                    SetupLifecycleState.DETECTED: "🔎",
                    SetupLifecycleState.FORMING: "⏳",
                    SetupLifecycleState.NEAR_CONFIRMATION: "🟣",
                    SetupLifecycleState.CONFIRMED: "✅",
                    SetupLifecycleState.INVALIDATED: "❌",
                    SetupLifecycleState.EXPIRED: "⌛",
                    SetupLifecycleState.ENTRY_MISSED: "⌛",
                    SetupLifecycleState.ENTRY_ZONE_MISSED: "⌛",
                }.get(setup.state, "•")
                score = f"{round(setup.completion_score or 0):.0f}%"
                checked = (
                    setup.last_evaluated_at.strftime("%Y-%m-%d %H:%M UTC")
                    if setup.last_evaluated_at
                    else "not checked yet"
                )
                lines.append(
                    f"{icon} {escape(setup.symbol)} · {score}\n"
                    f"   {state}\n"
                    f"   Last checked: {checked}"
                )
            text = f"{title}\n\n" + "\n".join(lines)
        else:
            text = f"{title}\n\nNo lifecycle cards matched this view yet."
        return self._plain(
            message,
            text,
            buttons=[
                TelegramButton("⏳ Forming", "latest:forming"),
                TelegramButton("✅ Confirmed", "latest:confirmed"),
                TelegramButton("❌ Invalidated", "latest:invalidated"),
                TelegramButton("⌛ Expired", "latest:expired"),
                self._dashboard_button("Dashboard", OPPORTUNITIES_PATH),
                TelegramButton("🏠 Main Menu", "back:main"),
            ],
        )

    async def _latest_alerts(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage:
        return await self._latest_setups(message, conversation)

    async def _subscription(
        self, message: TelegramInboundMessage, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        trial = await self.session.scalar(
            select(Trial).where(Trial.user_id == conversation.user_id)
        )
        subscription = await self.session.scalar(
            select(Subscription).where(Subscription.user_id == conversation.user_id)
        )
        if subscription:
            text = f"Subscription status: {subscription.status.value}"
        elif trial:
            text = f"Trial status: {trial.status.value}\nTrial ends: {trial.ends_at.isoformat()}"
        else:
            text = "No active trial or subscription found."
        return self._plain(
            message,
            "Subscription\n\n" + text,
            buttons=[
                TelegramButton("Activate Free Plan", "billing:free"),
                TelegramButton("Upgrade Trader", "billing:checkout:trader"),
                TelegramButton("Upgrade Pro", "billing:checkout:pro"),
                TelegramButton("Upgrade Creator", "billing:checkout:creator"),
                self._dashboard_button("Usage and Limits", "/dashboard/billing"),
                TelegramButton("Go Back", "back:previous"),
            ],
            menu=[
                "Activate Free Plan",
                "Upgrade Trader",
                "Upgrade Pro",
                "Upgrade Creator",
                "Go Back",
            ],
        )

    async def _trial(
        self, message: TelegramInboundMessage, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        return await self._account_link_callback(
            self._callback_from_message(message, "account:signup"), conversation
        )

    async def _main_menu_message(
        self, message: TelegramInboundMessage, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        conversation.flow = "main_menu"
        conversation.step = "idle"
        state = dict(conversation.state_data or {})
        state["nav_stack"] = []
        state.pop("current_screen", None)
        conversation.state_data = state
        await self.session.commit()
        return self._plain(
            message,
            MAIN_MENU_TEXT,
            buttons=self._main_menu_buttons(
                linked=await self._has_email_identity(self._require_user_id(conversation)),
                trial_claimed=await self._has_claimed_trial(self._require_user_id(conversation)),
            ),
            menu=PRIMARY_MENU,
        )

    async def _main_menu_callback(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        conversation.flow = "main_menu"
        conversation.step = "idle"
        state = dict(conversation.state_data or {})
        state["nav_stack"] = []
        state.pop("current_screen", None)
        conversation.state_data = state
        await self.session.flush()
        return self._plain_callback(
            callback,
            MAIN_MENU_TEXT,
            buttons=self._main_menu_buttons(
                linked=await self._has_email_identity(self._require_user_id(conversation)),
                trial_claimed=await self._has_claimed_trial(self._require_user_id(conversation)),
            ),
            menu=PRIMARY_MENU,
        )

    async def _render_message_screen(
        self,
        action: str,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage | None:
        if action == "create_monitor":
            return await self._begin_create_monitor(message, conversation)
        if action == "menu:my_monitors":
            return await self._my_monitors(message, conversation)
        if action == "menu:scan_market_now":
            return await self._scan_market_now(message, conversation)
        if action == "menu:near_miss":
            return await self._latest_setups(message, conversation)
        if action == "menu:latest_setups":
            return await self._latest_setups(message, conversation)
        if action == "menu:latest_alerts":
            return await self._latest_alerts(message, conversation)
        if action == "menu:trial":
            return await self._trial(message, conversation)
        if action == "menu:subscription":
            return self._pricing_message(message)
        if action == "menu:performance":
            return self._plain(
                message,
                "Performance\n\nForward-test analytics and setup-performance summaries are "
                "available in the dashboard. Telegram will show a compact summary once live "
                "results exist.",
                buttons=self._back_buttons("dashboard:performance"),
            )
        if action == "menu:about":
            return self._about_message(message)
        if action == "menu:settings":
            return await self._dashboard_callback(
                self._callback_from_message(message, "dashboard:settings"), conversation
            )
        if action == "menu:support":
            return self._support_menu_message(message)
        if action in {"menu:why_no_alert", "menu:setup_replay"}:
            await self._push_navigation(conversation, "menu:latest_setups")
            return await self._latest_setups(message, conversation)
        return None

    @staticmethod
    def _parse_monitor_action_label(text: str) -> tuple[str, int] | None:
        match = re.fullmatch(r"(Pause|Resume|Edit|Delete|Approve)\s+#(\d+)", text.strip())
        if not match:
            return None
        return match.group(1).lower(), int(match.group(2))

    async def _handle_monitor_action(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        action: str,
        index: int,
    ) -> TelegramOutboundMessage:
        monitor_map = {
            str(key): value
            for key, value in (conversation.state_data or {}).get("monitor_index_map", {}).items()
        }
        strategy_id = monitor_map.get(str(index))
        if not strategy_id:
            return self._plain(
                message,
                "That monitor number is no longer available. Open My Monitors again.",
                buttons=[TelegramButton("My Monitors", "dashboard:monitors")],
            )
        return await self._handle_monitor_action_by_id(
            message, conversation, action, UUID(strategy_id)
        )

    async def _monitor_manage_options_by_id(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
        strategy_id: UUID,
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        strategy = await self.session.get(Strategy, strategy_id)
        if (
            strategy is None
            or strategy.user_id != user_id
            or strategy.status == StrategyStatus.ARCHIVED
        ):
            return self._plain_callback(
                callback,
                "Monitor not found. Open My Monitors again.",
                buttons=[TelegramButton("📋 My Monitors", "menu:my_monitors")],
            )
        status = strategy.status.value.replace("_", " ").title()
        buttons: list[TelegramButton] = []
        if strategy.status == StrategyStatus.ACTIVE:
            buttons.append(TelegramButton("⏸️ Pause", f"monitor:pause:{strategy.id}"))
        elif strategy.status == StrategyStatus.PAUSED:
            buttons.append(TelegramButton("▶️ Resume", f"monitor:resume:{strategy.id}"))
        buttons.extend(
            [
                TelegramButton("🗄️ Delete", f"monitor:delete:{strategy.id}"),
                self._dashboard_button("Dashboard", "/dashboard/strategies/new#monitors"),
                TelegramButton("📋 My Monitors", "menu:my_monitors"),
            ]
        )
        return self._plain_callback(
            callback,
            (
                f"📋 Manage Monitor\n\n"
                f"{escape(strategy.name)}\n"
                f"Status: {status}\n\n"
                "Choose an action. Paused monitors stay saved but do not send notifications."
            ),
            buttons=buttons,
        )

    async def _handle_monitor_action_by_id(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        action: str,
        strategy_id: UUID,
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        strategy = await self.session.get(Strategy, strategy_id)
        if strategy is None or strategy.user_id != user_id:
            return self._plain(message, "Monitor not found.", buttons=self._back_buttons())
        if action == "pause":
            if strategy.status != StrategyStatus.ACTIVE:
                return self._plain(message, "Only active monitors can be paused.")
            await MonitorOperationService(
                self.session,
                settings=self.settings,
                previewer=self.previewer,
            ).pause(
                user_id=user_id,
                strategy_id=strategy.id,
                actor_type="telegram_user",
            )
            await self.session.commit()
            return await self._my_monitors(message, conversation)
        if action == "resume":
            if strategy.status != StrategyStatus.PAUSED:
                return self._plain(message, "Only paused monitors can be resumed.")
            try:
                await MonitorOperationService(
                    self.session,
                    settings=self.settings,
                    previewer=self.previewer,
                ).resume(
                    user_id=user_id,
                    strategy_id=strategy.id,
                    actor_type="telegram_user",
                )
            except MonitorOperationError as exc:
                await self.session.rollback()
                return self._plain(
                    message,
                    f"This monitor remains paused: {escape(str(exc))}",
                    buttons=self._back_buttons(),
                )
            await self.session.commit()
            return await self._my_monitors(message, conversation)
        if action == "delete":
            await MonitorOperationService(
                self.session,
                settings=self.settings,
                previewer=self.previewer,
            ).delete(
                user_id=user_id,
                strategy_id=strategy.id,
                actor_type="telegram_user",
            )
            await self.session.commit()
            return await self._my_monitors(message, conversation)
        if action == "edit":
            state = dict(conversation.state_data or {})
            state["editing_strategy_id"] = str(strategy.id)
            conversation.state_data = state
            conversation.flow = "create_monitor"
            conversation.step = "collect_setup_text"
            await self.session.commit()
            return self._plain(
                message,
                f"Editing {escape(strategy.name)}.\n\n"
                "Send the revised monitor description. I will create a new version and ask "
                "for approval before it can run live.",
                menu=["Go Back"],
            )
        if action == "approve":
            version = await self.session.scalar(
                select(StrategyVersion)
                .where(StrategyVersion.strategy_id == strategy.id)
                .order_by(StrategyVersion.version_number.desc())
            )
            if version is None:
                return self._plain(message, "This draft has no strategy version to approve.")
            conversation.flow = "create_monitor"
            conversation.step = "approval"
            conversation.state_data = {
                **(conversation.state_data or {}),
                "strategy_id": str(strategy.id),
                "strategy_version_id": str(version.id),
                "schema_hash": version.schema_hash,
            }
            await self.session.commit()
            strategy_definition = StrategyDefinition.model_validate(version.schema_json)
            return self._plain(
                message,
                "Review this draft before approval.\n\n"
                + self._strategy_summary(strategy_definition),
                buttons=[
                    TelegramButton("Approve", "approve_strategy"),
                    TelegramButton("Edit", "mode_describe"),
                    TelegramButton("Delete #1", f"monitor:delete:{strategy.id}"),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        return self._plain(message, "That monitor action is not available.")

    async def _settings_choice(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        label: str,
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        prefs = await self._notification_preferences(user_id)
        if label in {"Alert Days", "Alert Schedule"}:
            selected = prefs.get("alert_days", ["Every Day"])
            return self._plain(
                message,
                f"Alert Days\n\nChoose when alerts are allowed.\nCurrent: {', '.join(selected)}",
                buttons=[
                    *(TelegramButton(day, f"settings:day:{day}") for day in ALERT_DAYS),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        if label == "Alert Hours":
            selected = [str(item) for item in prefs.get("alert_hours", [])]
            current = ", ".join(selected) if selected else "Any hour"
            return self._plain(
                message,
                "Alert Hours\n\nChoose allowed alert hours. Tap an hour to toggle it.\n"
                f"Current: {current}",
                buttons=[
                    *(TelegramButton(hour, f"settings:hour:{hour}") for hour in ALERT_HOURS),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        if label == "Near-Miss Alerts":
            enabled = bool(prefs.get("near_miss_enabled", True))
            return self._plain(
                message,
                "Near-Miss Alerts\n\n"
                f"Current: {'enabled' if enabled else 'disabled'}.\n"
                "Lifecycle cards remain available either way.",
                buttons=[
                    TelegramButton("Enable Near-Miss Alerts", "settings:near_miss:on"),
                    TelegramButton("Disable Near-Miss Alerts", "settings:near_miss:off"),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        if label == "Alert Channels":
            # Gated on nothing, this used to say Telegram was the channel available
            # "during private beta" — so it kept saying it after the beta ended. What
            # is true either way is which channels exist, so that is what it says.
            whatsapp = (
                " WhatsApp is also available."
                if self.settings.whatsapp_enabled
                else ""
            )
            return self._plain(
                message,
                "Alert Channels\n\nTelegram is the external notification channel."
                f"{whatsapp} In-app records remain available in the dashboard.",
                buttons=self._back_buttons("dashboard:settings"),
            )
        if label == "Time Zone":
            user = await self.session.get(User, user_id)
            current = user.timezone if user is not None else "UTC"
            return self._plain(
                message,
                "Time Zone\n\nChoose the timezone used for alert schedules, replay displays "
                f"and billing dates.\nCurrent: {current}",
                buttons=[
                    *(
                        TelegramButton(item, f"settings:timezone:{item}")
                        for item in TELEGRAM_TIMEZONES
                    ),
                    TelegramButton("Go Back", "back:previous"),
                ],
            )
        return self._plain(
            message,
            "That setting is not available yet.",
            buttons=self._back_buttons(),
        )

    async def _set_alert_day(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        day: str,
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        prefs = await self._notification_preferences(user_id)
        if day == "Every Day":
            prefs["alert_days"] = ["Every Day"]
        else:
            current = set(str(item) for item in prefs.get("alert_days", []))
            current.discard("Every Day")
            if day in current:
                current.remove(day)
            else:
                current.add(day)
            prefs["alert_days"] = sorted(current, key=lambda item: ALERT_DAYS.index(item)) or [
                "Every Day"
            ]
        await self._save_notification_preferences(user_id, prefs)
        await self.session.commit()
        return self._plain(
            message,
            "Alert day saved. Now choose allowed hours, or go back.",
            buttons=[
                *(TelegramButton(hour, f"settings:hour:{hour}") for hour in ALERT_HOURS),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _toggle_alert_hour(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        hour: str,
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        prefs = await self._notification_preferences(user_id)
        current = set(str(item) for item in prefs.get("alert_hours", []))
        if hour in current:
            current.remove(hour)
        else:
            current.add(hour)
        prefs["alert_hours"] = sorted(current)
        await self._save_notification_preferences(user_id, prefs)
        await self.session.commit()
        current_text = ", ".join(prefs["alert_hours"]) if prefs["alert_hours"] else "Any hour"
        return self._plain(
            message,
            f"Alert hours updated.\n\nCurrent: {current_text}",
            buttons=[
                *(TelegramButton(item, f"settings:hour:{item}") for item in ALERT_HOURS),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _set_near_miss_preference(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        *,
        enabled: bool,
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        prefs = await self._notification_preferences(user_id)
        prefs["near_miss_enabled"] = enabled
        await self._save_notification_preferences(user_id, prefs)
        await self.session.commit()
        return self._plain(
            message,
            f"Near-Miss alerts are now {'enabled' if enabled else 'disabled'}.",
            buttons=[
                TelegramButton("Near-Miss Alerts", "settings:near_miss"),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _set_timezone_preference(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        timezone: str,
    ) -> TelegramOutboundMessage:
        if timezone not in TELEGRAM_TIMEZONES:
            return self._plain(message, "That timezone is not supported yet.")
        user_id = self._require_user_id(conversation)
        user = await self.session.get(User, user_id)
        if user is not None:
            user.timezone = timezone
        prefs = await self._notification_preferences(user_id)
        prefs["timezone"] = timezone
        await self._save_notification_preferences(user_id, prefs)
        row = await self.session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user_id)
        )
        if row is not None:
            row.default_timezone = timezone
        await self.session.commit()
        return self._plain(
            message,
            f"Timezone saved: {timezone}",
            buttons=[
                TelegramButton("Time Zone", "settings:timezone"),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _notification_preferences(self, user_id: UUID) -> dict:
        row = await self.session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user_id)
        )
        if row is None:
            row = DashboardPreference(
                user_id=user_id,
                notification_preferences={
                    "near_miss_enabled": True,
                    "near_miss_threshold": 70,
                    "maximum_alerts_per_hour": 50,
                    "maximum_alerts_per_day": 500,
                    "providers": ["binance", "bybit"],
                },
            )
            self.session.add(row)
            await self.session.flush()
        data = dict(row.notification_preferences or {})
        data.setdefault("near_miss_enabled", True)
        data.setdefault("near_miss_threshold", 70)
        data.setdefault("maximum_alerts_per_hour", 50)
        data.setdefault("maximum_alerts_per_day", 500)
        data.setdefault("providers", ["binance", "bybit"])
        return data

    async def _save_notification_preferences(self, user_id: UUID, prefs: dict) -> None:
        row = await self.session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user_id)
        )
        if row is None:
            row = DashboardPreference(user_id=user_id, notification_preferences=prefs)
            self.session.add(row)
        else:
            row.notification_preferences = prefs

    @staticmethod
    def _setup_states_for_category(category: str) -> list[SetupLifecycleState]:
        return {
            "confirmed": [
                SetupLifecycleState.CONFIRMED,
                SetupLifecycleState.ENTRY_ACTIVE,
                SetupLifecycleState.ENTRY_ZONE_ACTIVE,
                SetupLifecycleState.ENTRY_TOUCHED,
            ],
            "forming": [
                SetupLifecycleState.CANDIDATE_DETECTED,
                SetupLifecycleState.DETECTED,
                SetupLifecycleState.FORMING,
                SetupLifecycleState.NEAR_CONFIRMATION,
            ],
            "invalidated": [SetupLifecycleState.INVALIDATED],
            "expired": [
                SetupLifecycleState.EXPIRED,
                SetupLifecycleState.ENTRY_MISSED,
                SetupLifecycleState.ENTRY_ZONE_MISSED,
            ],
        }.get(category, [])

    def _message_from_callback(self, callback: TelegramCallback) -> TelegramInboundMessage:
        return TelegramInboundMessage(
            telegram_user_id=callback.telegram_user_id,
            chat_id=callback.chat_id,
            text="",
            message_id=callback.message_id,
            created_at=callback.created_at,
        )

    def _callback_from_message(
        self, message: TelegramInboundMessage, data: str
    ) -> TelegramCallback:
        return TelegramCallback(
            callback_query_id=f"message:{message.message_id or secrets.token_hex(8)}:{data}",
            telegram_user_id=message.telegram_user_id,
            chat_id=message.chat_id,
            data=data,
            message_id=message.message_id,
            created_at=message.created_at,
        )

    async def _render_callback_screen(
        self,
        action: str,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage | None:
        if action == "create_monitor":
            return await self._dashboard_callback(
                TelegramCallback(
                    callback_query_id=callback.callback_query_id,
                    telegram_user_id=callback.telegram_user_id,
                    chat_id=callback.chat_id,
                    data="dashboard:builder",
                    message_id=callback.message_id,
                    created_at=callback.created_at,
                ),
                conversation,
            )
        message_screen = await self._render_message_screen(
            action, self._message_from_callback(callback), conversation
        )
        if message_screen is not None:
            return message_screen
        if action in {"mode_describe", "mode_import"}:
            conversation.flow = "create_monitor"
            conversation.step = "collect_setup_text"
            await self.session.flush()
            prompt = (
                "Describe the setup in one message. Example: bullish liquidity sweep, "
                "price above the 4h 200 EMA, volume at least 1.5x average."
                if action == "mode_describe"
                else "Paste your strategy description or JSON-like rules. I will convert it "
                "into a draft for approval before it can monitor live."
            )
            return self._plain_callback(callback, prompt, buttons=self._back_buttons("back:create"))
        if action == "mode_template":
            return self._template_menu_callback(callback)
        if action == "account:auth":
            return self._account_auth_callback(callback)
        if action == "sample_alert":
            return self._about_callback(callback)
        if action == "sample_proof":
            return self._sample_proof_callback(callback)
        if action == "how_it_works":
            return self._about_callback(callback)
        if action == "pricing":
            return self._pricing_callback(callback)
        if self._is_utility_action(action):
            utility_callback = TelegramCallback(
                callback_query_id=callback.callback_query_id,
                telegram_user_id=callback.telegram_user_id,
                chat_id=callback.chat_id,
                data=action,
                message_id=callback.message_id,
                created_at=callback.created_at,
            )
            return await self._utility_callback(utility_callback, conversation)
        return None

    async def _previous_message(
        self, message: TelegramInboundMessage, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        return await self._main_menu_message(message, conversation)

    async def _utility_callback(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        self._require_user_id(conversation)
        action = callback.data
        descriptions = {
            "monitors:active": "Active monitors are listed in My Monitors and the dashboard.",
            "monitors:drafts": (
                "Draft monitors remain inactive until you approve and activate them."
            ),
            "monitors:paused": "Paused monitors stay saved and can be resumed from the dashboard.",
            "scan:existing": "Open the dashboard Quick Scan page to choose an approved strategy.",
            "scan:new": "Describe a one-off condition and run a deterministic Quick Scan.",
            "scan:template": "Choose a saved template and run a one-off Quick Scan.",
            "scan:previous": "Previous scans are shown on the Scan Results dashboard page.",
            "near:top": "Showing closest setups uses the Lifecycles view.",
            "near:one_left": "One-condition-remaining results are filtered by proof receipts.",
            "near:strategy": "Filter lifecycle cards by strategy in the dashboard.",
            "near:symbol": "Filter lifecycle cards by symbol in the dashboard.",
            "latest:confirmed": "Confirmed setups appear after deterministic rule confirmation.",
            "latest:forming": "Forming setups appear when conditions are close but incomplete.",
            "latest:invalidated": "Invalidated setup history is preserved in lifecycle tracking.",
            "latest:expired": (
                "Expired setups are shown when monitored conditions are no longer valid."
            ),
            "settings:channels": "Alert channels can be managed in Settings.",
            "settings:frequency": "Alert frequency and cooldowns can be managed in Settings.",
            "settings:threshold": "Near-Miss thresholds can be managed in Settings.",
            "settings:timezone": "Timezone preferences can be managed in Settings.",
            "proof:view": "Use Lifecycles for deterministic proof context.",
            "mute_strategy": "Strategy mute controls are managed in Settings.",
            "ignore_symbol": "Symbol ignore lists are managed in Settings.",
        }
        return self._plain_callback(
            callback,
            descriptions.get(action, "This Telegram action is connected to the dashboard."),
            buttons=self._back_buttons(self._dashboard_action_for(action)),
        )

    async def _feedback(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        parts = callback.data.split(":", 2)
        feedback_type = parts[1] if len(parts) > 1 else "unknown"
        alert_id = None
        setup_instance_id = None
        if len(parts) > 2:
            try:
                candidate_id = UUID(parts[2])
            except ValueError:
                candidate_id = None
            if candidate_id is not None:
                alert = await self.session.get(Alert, candidate_id)
                if alert is None or alert.user_id != conversation.user_id:
                    return self._plain_callback(
                        callback,
                        "This alert action is unavailable or belongs to another account.",
                        buttons=[TelegramButton("Go Back", "back:previous")],
                    )
                alert_id = alert.id
                setup_instance_id = alert.setup_instance_id
        self.session.add(
            UserFeedback(
                user_id=conversation.user_id,
                alert_id=alert_id,
                setup_instance_id=setup_instance_id,
                feedback_type=feedback_type,
                source="telegram",
                metadata_json={"callback_query_id": callback.callback_query_id},
            )
        )
        self.session.add(
            AuditEvent(
                actor_user_id=conversation.user_id,
                actor_type="telegram_user",
                action="alert.feedback_submitted",
                target_type="alert",
                target_id=str(alert_id) if alert_id else None,
                metadata_redacted={"feedback_type": feedback_type},
                created_at=datetime.now(UTC),
            )
        )
        await self.session.commit()
        return self._plain_callback(
            callback,
            "Feedback recorded. I will not change your strategy without explicit approval.",
            buttons=[
                self._dashboard_button("Lifecycles", LIFECYCLES_PATH),
                self._dashboard_button("Dashboard"),
                TelegramButton("🏠 Main Menu", "back:main"),
            ],
        )

    async def _support(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        return await self._dashboard_callback(
            TelegramCallback(
                callback_query_id=callback.callback_query_id,
                telegram_user_id=callback.telegram_user_id,
                chat_id=callback.chat_id,
                data="dashboard:support",
                message_id=callback.message_id,
                created_at=callback.created_at,
            ),
            conversation,
        )

    async def _account_link_callback(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        user_id = self._require_user_id(conversation)
        target = "signup" if callback.data.endswith("signup") else "signin"
        try:
            url = await TelegramAccountLinkService(self.session, self.settings).create(
                user_id=user_id,
                telegram_user_id=callback.telegram_user_id,
                target=target,
            )
            await self.session.commit()
        except TelegramAccountLinkError as exc:
            await self.session.rollback()
            return self._plain_callback(
                callback,
                f"Could not create account link: {escape(str(exc))}",
                buttons=[TelegramButton("Go Back", "back:previous")],
            )
        return self._plain_callback(
            callback,
            "Open this secure Dashboard link. It expires shortly.\n\n"
            "After you submit the form, come back to Telegram. I will recognize the linked "
            "Dashboard account for trial status, subscription dates and monitor stats.",
            buttons=[
                TelegramButton(
                    "Open Sign Up" if target == "signup" else "Open Sign In",
                    "external:account_link",
                    url=url,
                ),
                TelegramButton("Go Back", "back:previous"),
            ],
        )

    async def _has_email_identity(self, user_id: UUID) -> bool:
        identity_id = await self.session.scalar(
            select(UserIdentity.id).where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.is_verified.is_(True),
            )
        )
        return identity_id is not None

    async def _has_claimed_trial(self, user_id: UUID) -> bool:
        trial_id = await self.session.scalar(select(Trial.id).where(Trial.user_id == user_id))
        return trial_id is not None

    async def _record_risk_acknowledgement(self, user_id: UUID, *, source: str) -> None:
        """Written through the one owner in `services/risk_disclaimer.py`.

        This used to be its own copy of "check, find the identity, insert". So did the
        onboarding flow. A legal record with two writers is a legal record that can end
        up written two different ways.
        """

        try:
            await record_disclaimer_acceptance(
                self.session,
                user_id=user_id,
                version=self.settings.disclaimer_version,
                source=source,
            )
        except DisclaimerIdentityMissing as exc:
            raise OnboardingError("identity_missing", str(exc)) from exc

    async def _push_navigation(self, conversation: TelegramConversationState, action: str) -> None:
        if action.startswith("back:"):
            return
        state = dict(conversation.state_data or {})
        stack = [str(item) for item in state.get("nav_stack", [])][-8:]
        current = state.get("current_screen")
        if current and current != action and (not stack or stack[-1] != current):
            stack.append(str(current))
        if not stack or stack[-1] != action:
            stack.append(action)
        state["nav_stack"] = stack
        state["current_screen"] = action
        conversation.state_data = state
        await self.session.flush()

    async def _previous_callback(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        return await self._main_menu_callback(callback, conversation)

    def _action_needed_callback(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
        exc: Exception,
    ) -> TelegramOutboundMessage:
        message = escape(str(exc))
        if conversation.flow == "create_monitor":
            if (
                "before interpretation" in message
                or getattr(exc, "code", "") == "step_out_of_order"
            ):
                return self._plain_callback(
                    callback,
                    f"Action needed: {message}\n\n"
                    "I reset the create-monitor flow so you can send your setup description "
                    "again or choose a template.",
                    buttons=[
                        TelegramButton("Describe Setup", "mode_describe"),
                        TelegramButton("Use Template", "mode_template"),
                        self._dashboard_button("My Drafts", "/dashboard/strategies/new#monitors"),
                        TelegramButton("Cancel", "cancel"),
                    ],
                )
            return self._plain_callback(
                callback,
                f"Action needed: {message}",
                buttons=[
                    TelegramButton("Describe Setup", "mode_describe"),
                    TelegramButton("Use Template", "mode_template"),
                    TelegramButton("Cancel", "cancel"),
                ],
            )
        return self._plain_callback(
            callback,
            f"Action needed: {message}",
            buttons=[
                TelegramButton("Sign up / sign in", "account:auth"),
                self._dashboard_button("Dashboard", "/dashboard/strategies/new"),
                TelegramButton("🏠 Main Menu", "back:main"),
            ],
        )

    def _action_needed_message(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        exc: Exception,
    ) -> TelegramOutboundMessage:
        callback = self._callback_from_message(message, "action_needed")
        response = self._action_needed_callback(callback, conversation, exc)
        return TelegramOutboundMessage(
            chat_id=message.chat_id,
            text=response.text,
            buttons=response.buttons,
            menu=response.menu,
            parse_mode=response.parse_mode,
            correlation_id=response.correlation_id,
        )

    async def _upsert_connection(self, message: TelegramInboundMessage, user_id: UUID) -> None:
        connection = await self.session.scalar(
            select(TelegramConnection).where(
                TelegramConnection.telegram_user_id == message.telegram_user_id
            )
        )
        if connection is None:
            self.session.add(
                TelegramConnection(
                    user_id=user_id,
                    telegram_user_id=message.telegram_user_id,
                    chat_id=message.chat_id,
                    username=message.username,
                    status=ConnectionStatus.ACTIVE,
                    connected_at=datetime.now(UTC),
                )
            )
        else:
            connection.user_id = user_id
            connection.chat_id = message.chat_id
            connection.username = message.username
            connection.status = ConnectionStatus.ACTIVE

    async def _handle_dashboard_start_link(
        self,
        message: TelegramInboundMessage,
        raw_token: str,
    ) -> TelegramOutboundMessage:
        try:
            user, email = await TelegramAccountLinkService(
                self.session,
                self.settings,
            ).pending_dashboard_start_link(raw_token)
        except TelegramAccountLinkError as exc:
            await self.session.rollback()
            return self._plain(
                message,
                f"This Telegram connection link is not available: {escape(str(exc))}\n\n"
                "Open the dashboard Integrations page and request a fresh Telegram link.",
                buttons=[self._dashboard_button("Dashboard")],
            )
        conversation = await self._upsert_conversation(
            message,
            user_id=user.id,
            onboarding_session_id=None,
            flow="telegram_link",
            step="confirm",
            state_data={
                "telegram_dashboard_link_token": raw_token,
                "dashboard_email": email,
            },
        )
        await self.session.commit()
        visible_email = email or "this dashboard account"
        return TelegramOutboundMessage(
            chat_id=message.chat_id,
            text=(
                "🔗 Telegram connection\n\n"
                f"Connect this Telegram account to {visible_email}?\n\n"
                "After confirmation, this Telegram chat can receive Hilal Markets notifications."
            ),
            buttons=[
                TelegramButton("✅ Yes, connect", "telegram_link:confirm"),
                TelegramButton("Cancel", "telegram_link:cancel"),
            ],
            correlation_id=conversation.correlation_id,
        )

    def _telegram_link_confirmation_message(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage:
        visible_email = str(
            (conversation.state_data or {}).get("dashboard_email") or "this dashboard account"
        )
        return TelegramOutboundMessage(
            chat_id=message.chat_id,
            text=(
                "Telegram connection\n\n"
                f"Connect this Telegram account to {visible_email}?\n\n"
                "After confirmation, this Telegram chat can receive Hilal Markets notifications."
            ),
            buttons=[
                TelegramButton("Yes, connect", "telegram_link:confirm"),
                TelegramButton("Cancel", "telegram_link:cancel"),
            ],
            correlation_id=conversation.correlation_id,
        )

    async def _confirm_dashboard_telegram_link(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage:
        raw_token = str((conversation.state_data or {}).get("telegram_dashboard_link_token") or "")
        try:
            user, email = await TelegramAccountLinkService(
                self.session,
                self.settings,
            ).complete_dashboard_start_link(
                raw_token=raw_token,
                telegram_user_id=callback.telegram_user_id,
                chat_id=callback.chat_id,
                username=conversation.username,
            )
        except TelegramAccountLinkError as exc:
            return self._plain_callback(
                callback,
                f"Could not connect Telegram: {escape(str(exc))}\n\n"
                "Open the dashboard Integrations page and request a fresh link.",
                buttons=[self._dashboard_button("Dashboard")],
            )
        conversation.user_id = user.id
        conversation.flow = "main_menu"
        conversation.step = "idle"
        conversation.state_data = {
            **(conversation.state_data or {}),
            "dashboard_linked_at": datetime.now(UTC).isoformat(),
            "dashboard_email": email,
        }
        await self.session.flush()
        return self._plain_callback(
            callback,
            (
                "✅ Telegram connected\n\n"
                f"Linked to {email or 'your dashboard account'}.\n"
                "Your dashboard will refresh the Integrations status automatically."
            ),
            buttons=[
                self._dashboard_button("Dashboard"),
                TelegramButton("Main Menu", "back:main"),
            ],
            menu=PRIMARY_MENU,
        )

    async def _cancel_dashboard_telegram_link(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage:
        conversation.flow = "main_menu"
        conversation.step = "idle"
        state = dict(conversation.state_data or {})
        state.pop("telegram_dashboard_link_token", None)
        conversation.state_data = state
        await self.session.flush()
        return self._plain_callback(
            callback,
            "Telegram connection cancelled.",
            buttons=[TelegramButton("Main Menu", "back:main")],
        )

    async def _upsert_conversation(
        self,
        message: TelegramInboundMessage,
        *,
        user_id: UUID,
        onboarding_session_id: UUID | None,
        flow: str,
        step: str,
        state_data: dict,
    ) -> TelegramConversationState:
        conversation = await self._conversation(message.telegram_user_id)
        if conversation is None:
            conversation = TelegramConversationState(
                user_id=user_id,
                onboarding_session_id=onboarding_session_id,
                telegram_user_id=message.telegram_user_id,
                chat_id=message.chat_id,
                username=message.username,
                flow=flow,
                step=step,
                state_data=state_data,
                correlation_id=secrets.token_hex(8),
            )
            self.session.add(conversation)
        else:
            merged_state = dict(conversation.state_data or {})
            linked_at = merged_state.get("dashboard_linked_at")
            merged_state.update(state_data)
            if linked_at:
                merged_state["dashboard_linked_at"] = linked_at
            conversation.user_id = user_id
            conversation.onboarding_session_id = onboarding_session_id
            conversation.chat_id = message.chat_id
            conversation.username = message.username
            conversation.flow = flow
            conversation.step = step
            conversation.state_data = merged_state
        await self.session.flush()
        return conversation

    async def _conversation(self, telegram_user_id: str) -> TelegramConversationState | None:
        return await self.session.scalar(
            select(TelegramConversationState).where(
                TelegramConversationState.telegram_user_id == telegram_user_id
            )
        )

    async def _store_callback(
        self,
        callback: TelegramCallback,
        payload_hash: str,
        user_id: UUID | None,
        response: TelegramOutboundMessage,
    ) -> TelegramOutboundMessage:
        self.session.add(
            TelegramCallbackReceipt(
                callback_query_id=callback.callback_query_id,
                telegram_user_id=callback.telegram_user_id,
                user_id=user_id,
                action=callback.data.split(":", 1)[0],
                payload_hash=payload_hash,
                status="processed",
                result_payload={
                    "chat_id": response.chat_id,
                    "text": response.text,
                    "buttons": [
                        {
                            "text": button.text,
                            "callback_data": button.callback_data,
                            "url": button.url,
                        }
                        for button in response.buttons
                    ],
                    "menu": response.menu,
                    "parse_mode": response.parse_mode,
                    "correlation_id": response.correlation_id,
                    "edit_message_id": response.edit_message_id,
                },
                consumed_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=7),
                created_at=datetime.now(UTC),
            )
        )
        await self.session.commit()
        return response

    async def _audit(
        self,
        user_id: UUID,
        action: str,
        target_type: str,
        target_id: str,
        metadata: dict,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=user_id,
                actor_type="telegram_user",
                action=action,
                target_type=target_type,
                target_id=target_id,
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _parse_deep_link(start_param: str) -> AttributionInput:
        metadata: dict[str, str] = {}
        values: dict[str, str] = {}
        for part in start_param.replace("&", "__").split("__"):
            if not part:
                continue
            if "=" in part:
                key, value = part.split("=", 1)
            elif "_" in part:
                key, value = part.split("_", 1)
            else:
                continue
            key = {
                "src": "source",
                "cmp": "campaign",
                "ref": "referral_code",
                "tpl": "template",
            }.get(key, key)
            if key in {"source", "campaign", "referral_code"}:
                values[key] = value
            else:
                metadata[key] = value
        return AttributionInput(
            source=values.get("source"),
            campaign=values.get("campaign"),
            referral_code=values.get("referral_code"),
            landing_path="telegram:/start",
            consented=True,
            metadata_json=metadata,
        )

    @staticmethod
    def _strategy_summary(strategy, preview=None) -> str:
        condition_lines = []
        for child in strategy.conditions.children:
            label = getattr(child, "label", getattr(child, "key", "condition"))
            timeframe = getattr(child, "timeframe", strategy.base_timeframe)
            condition_lines.append(f"- {escape(label)} ({escape(timeframe)})")
        extra_timeframes = (
            f", {escape(', '.join(strategy.supporting_timeframes))}"
            if strategy.supporting_timeframes
            else ""
        )
        summary = (
            f"Direction: {escape(strategy.direction.value)}\n"
            f"Exchange: {escape(strategy.universe.exchange)}\n"
            f"Market type: {escape(strategy.universe.market_type.value)}\n"
            f"Pair universe: {escape(', '.join(strategy.universe.quote_currencies))} quotes\n"
            f"Timeframes: {escape(strategy.base_timeframe)}{extra_timeframes}\n"
            f"Entry conditions:\n" + "\n".join(condition_lines) + "\n"
            f"Trigger mode: {escape(strategy.trigger_mode.value)}\n"
            f"Near-Miss threshold: {strategy.alerts.near_miss_threshold:.0f}%\n"
            f"Alert limit: {strategy.alerts.maximum_alerts_per_hour}/hour"
        )
        if preview is not None:
            notes = []
            assumptions = [
                item
                for item in preview.assumptions
                if not TelegramBotService._is_internal_interpreter_note(item)
            ]
            if assumptions:
                notes.append(
                    "Assumptions:\n" + "\n".join(f"- {escape(item)}" for item in assumptions)
                )
            if preview.unsupported_conditions:
                notes.append(
                    "<b>[ACTION REQUIRED] Unsupported conditions</b>\n"
                    "These cannot run until you rewrite or clarify them:\n"
                    + "\n".join(
                        f"- <b>{escape(item.message)}</b>"
                        for item in preview.unsupported_conditions
                    )
                )
            if preview.ambiguities:
                notes.append(
                    "<b>[ACTION REQUIRED] Clarifications needed</b>\n"
                    "Please define these before approval:\n"
                    + "\n".join(f"- <b>{escape(item.message)}</b>" for item in preview.ambiguities)
                )
            if notes:
                summary += "\n\n" + "\n\n".join(notes)
        return summary

    @staticmethod
    def _is_internal_interpreter_note(note: str) -> bool:
        lowered = note.lower()
        return any(
            phrase in lowered
            for phrase in (
                "openai interpretation",
                "conservative rule parser",
                "interpreter",
                "openai_error",
            )
        )

    @staticmethod
    def _definition_has_executable_conditions(strategy: StrategyDefinition) -> bool:
        executable = False

        def walk(node) -> None:
            nonlocal executable
            if getattr(node, "node_type", None) == "condition":
                if getattr(node, "key", "") != "clarification_required":
                    executable = True
                return
            for child in getattr(node, "children", []):
                walk(child)

        walk(strategy.conditions)
        return executable

    @staticmethod
    def _require_user_id(conversation: TelegramConversationState) -> UUID:
        if conversation.user_id is None:
            raise OnboardingError(
                "conversation_unlinked",
                "This Telegram conversation is no longer linked to an account. Send /start.",
            )
        return conversation.user_id

    def _dashboard_url(self, path: str = "/dashboard") -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{str(self.settings.public_base_url).rstrip('/')}{normalized}"

    def _dashboard_button(
        self, label: str = "Dashboard", path: str = "/dashboard"
    ) -> TelegramButton:
        return TelegramButton(label, "external:dashboard", url=self._dashboard_url(path))

    @staticmethod
    def _dashboard_path_for_page(page: str) -> str:
        return {
            "home": "/dashboard",
            "how": "/how-it-works",
            "about": "/about",
            "billing": "/dashboard/billing",
            "trial": "/dashboard/trial",
            "builder": "/dashboard/strategies/new",
            "create_monitor": "/dashboard/strategies/new",
            "monitors": "/dashboard/strategies/new#monitors",
            "scan": _ONE_TIME_SCAN_PATH,
            "near_miss": LIFECYCLES_PATH,
            "lifecycles": LIFECYCLES_PATH,
            "setups": LIFECYCLES_PATH,
            "alerts": LIFECYCLES_PATH,
            "settings": SETTINGS_PATH,
            "support": SUPPORT_PATH,
            "connections": CONNECTIONS_PATH,
            "performance": "/dashboard",
            "setup_replay": LIFECYCLES_PATH,
            "why_no_alert": LIFECYCLES_PATH,
        }.get(page, "/dashboard")

    @staticmethod
    def _chart_url(result: EvaluationResult) -> str:
        if result.chart_reference and result.chart_reference.startswith(("http://", "https://")):
            return result.chart_reference
        exchange = result.exchange.upper()
        symbol = result.symbol.replace("/", "").replace("-", "").upper()
        return f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol}"

    async def _dashboard_callback(
        self, callback: TelegramCallback, conversation: TelegramConversationState
    ) -> TelegramOutboundMessage:
        page = callback.data.partition(":")[2] or "home"
        path = self._dashboard_path_for_page(page)
        user_id = self._require_user_id(conversation)
        if not await self._has_email_identity(user_id):
            signup_url = await TelegramAccountLinkService(self.session, self.settings).create(
                user_id=user_id,
                telegram_user_id=callback.telegram_user_id,
                target="signup",
            )
            signin_url = await TelegramAccountLinkService(self.session, self.settings).create(
                user_id=user_id,
                telegram_user_id=callback.telegram_user_id,
                target="signin",
            )
            await self.session.commit()
            return self._plain_callback(
                callback,
                "Connect a Dashboard account first. This secure link binds Dashboard to "
                "Telegram so monitor stats, trial status and subscription dates stay in sync.",
                buttons=[
                    TelegramButton("Open Sign Up", "external:signup", url=signup_url),
                    TelegramButton("Open Sign In", "external:signin", url=signin_url),
                ],
            )
        return self._plain_callback(
            callback,
            "Open Dashboard in your browser.",
            buttons=[self._dashboard_button("Dashboard", path)],
            menu=PRIMARY_MENU,
        )

    @staticmethod
    def _dashboard_action_for(action: str) -> str:
        if action == "proof:view":
            return "dashboard:lifecycles"
        if action in {"mute_strategy", "ignore_symbol"}:
            return "dashboard:settings"
        if action.startswith("scan:"):
            return "dashboard:scan"
        if action.startswith("near:"):
            return "dashboard:lifecycles"
        if action.startswith("latest:"):
            return "dashboard:setups"
        if action.startswith("settings:"):
            return "dashboard:settings"
        if action.startswith("monitors:"):
            return "dashboard:monitors"
        return "dashboard:home"

    @staticmethod
    def _is_utility_action(action: str) -> bool:
        return action in {
            "proof:view",
            "mute_strategy",
            "ignore_symbol",
            "monitors:active",
            "monitors:drafts",
            "monitors:paused",
            "scan:existing",
            "scan:new",
            "scan:template",
            "scan:previous",
            "near:top",
            "near:one_left",
            "near:strategy",
            "near:symbol",
            "latest:confirmed",
            "latest:forming",
            "latest:invalidated",
            "latest:expired",
            "settings:channels",
            "settings:frequency",
            "settings:threshold",
            "settings:timezone",
        }

    def _back_buttons(self, dashboard_action: str | None = None) -> list[TelegramButton]:
        buttons: list[TelegramButton] = []
        if dashboard_action:
            if dashboard_action.startswith("back:"):
                buttons.append(TelegramButton("🏠 Main Menu", "back:main"))
                return buttons
            page = (
                dashboard_action.partition(":")[2]
                if dashboard_action.startswith("dashboard:")
                else "home"
            )
            buttons.append(self._dashboard_button("Dashboard", self._dashboard_path_for_page(page)))
        buttons.append(TelegramButton("🏠 Main Menu", "back:main"))
        return buttons

    @staticmethod
    def _plain(
        message: TelegramInboundMessage,
        text: str,
        *,
        buttons: list[TelegramButton] | None = None,
        menu: list[str] | None = None,
        parse_mode: str | None = None,
    ) -> TelegramOutboundMessage:
        actual_buttons = buttons or []
        if menu is None and not actual_buttons:
            selected_menu = PRIMARY_MENU
        elif (
            menu is None and actual_buttons and all(button.url is None for button in actual_buttons)
        ):
            selected_menu = [button.text for button in actual_buttons]
        else:
            selected_menu = menu or []
        return TelegramOutboundMessage(
            chat_id=message.chat_id,
            text=text,
            buttons=actual_buttons,
            menu=selected_menu,
            parse_mode=parse_mode,
        )

    @staticmethod
    def _plain_callback(
        callback: TelegramCallback,
        text: str,
        *,
        buttons: list[TelegramButton] | None = None,
        menu: list[str] | None = None,
        parse_mode: str | None = None,
    ) -> TelegramOutboundMessage:
        actual_buttons = buttons or []
        if menu is None and not actual_buttons:
            selected_menu = PRIMARY_MENU
        elif (
            menu is None and actual_buttons and all(button.url is None for button in actual_buttons)
        ):
            selected_menu = [button.text for button in actual_buttons]
        else:
            selected_menu = menu or []
        return TelegramOutboundMessage(
            chat_id=callback.chat_id,
            text=text,
            buttons=actual_buttons,
            menu=selected_menu,
            parse_mode=parse_mode,
        )

    @staticmethod
    def _outbound_from_payload(payload: dict) -> TelegramOutboundMessage:
        return TelegramOutboundMessage(
            chat_id=str(payload["chat_id"]),
            text=str(payload["text"]),
            buttons=[
                TelegramButton(
                    text=str(button["text"]),
                    callback_data=str(button.get("callback_data") or ""),
                    url=button.get("url"),
                )
                for button in payload.get("buttons", [])
            ],
            menu=[str(item) for item in payload.get("menu", [])],
            parse_mode=payload.get("parse_mode"),
            correlation_id=payload.get("correlation_id"),
            edit_message_id=payload.get("edit_message_id"),
        )
