import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.dashboard_paths import (
    ABOUT_PATH,
    CONNECTIONS_PATH,
    HOME_PATH,
    HOW_IT_WORKS_PATH,
    LIFECYCLES_PATH,
    MONITOR_PATH,
    MONITORS_PATH,
    OPPORTUNITIES_PATH,
    PRICING_PATH,
    PUBLIC_HOME_PATH,
    SETTINGS_PATH,
    SUBSCRIPTION_PATH,
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
from ai_market_monitor.engine.active_question import (
    ConfirmationReply,
    normalize_answer_text,
    resolve_confirmation,
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
from ai_market_monitor.services.billing import BillingError
from ai_market_monitor.services.interfaces import MarketDataProvider, RecentMarketPreviewer
from ai_market_monitor.services.monitor_operations import (
    MonitorOperationError,
    MonitorOperationService,
)
from ai_market_monitor.services.on_demand_scans import OnDemandScanError, OnDemandScanService
from ai_market_monitor.services.onboarding import OnboardingError, OnboardingService
from ai_market_monitor.services.openai_interpreter import configured_strategy_interpreter
from ai_market_monitor.services.product_language import market_checking_notice
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
from ai_market_monitor.telegram.profile import (
    BOUNDARY_TEXT,
    PRODUCT_DESCRIPTOR,
    TELEGRAM_ROLE_TEXT,
)
from ai_market_monitor.telegram.rendering import (
    escape,
    render_confirmed_alert,
    render_lifecycle_update,
    render_near_miss_list,
)
from ai_market_monitor.telegram.types import (
    KEYBOARD_INLINE,
    KEYBOARD_REMOVE_REPLY_KEYBOARD,
    NearMissListItem,
    TelegramButton,
    TelegramCallback,
    TelegramInboundMessage,
    TelegramOutboundMessage,
)

#: What a typed reply has to say to answer the open Telegram-connection question, when it
#: is not a yes or a no: the words this screen prints on its own buttons, and the labels
#: this bot prints everywhere for "leave this step". Everything else is read by the one
#: yes/no vocabulary in ``engine/active_question.py``, which is also why "ok", "sure" and
#: a mistyped "confitm" answer this question too.
_TELEGRAM_LINK_LABELS: Final[dict[str, ConfirmationReply]] = {
    "confirm": ConfirmationReply.AFFIRMATIVE,
    "connect": ConfirmationReply.AFFIRMATIVE,
    # The reply-keyboard captions this prompt used before its buttons went inline. They
    # are still sitting in chats that opened before the fix, and a key pressed is an
    # answer, not a mystery.
    "yes connect": ConfirmationReply.AFFIRMATIVE,
    "cancel": ConfirmationReply.NEGATIVE,
    "back": ConfirmationReply.NEGATIVE,
    "go back": ConfirmationReply.NEGATIVE,
    "main menu": ConfirmationReply.NEGATIVE,
}

#: Where a "scan the market once" button really goes.
#:
#: It used to be ``/dashboard/scan-now`` and then ``/dashboard/strategies/new?mode=scanner``
#: — first the Trading Assistant page, then the assistant page that replaced it. Both are
#: gone, and the one-time scan went with the second one: it was a mode of that page and
#: had no front door of its own.
#:
#: A button inside a Telegram message lives for as long as the message does, so these
#: cannot be left pointing at a page that no longer answers. They go to the canvas, which
#: is where a monitor is made now. The message beside them says so.
_ONE_TIME_SCAN_PATH = MONITOR_PATH

PRIMARY_MENU = [
    "📋 My Monitors",
    "🔄 Lifecycles",
    "🎁 Trial",
    "💸 Pricing",
    "⚙️ Settings",
    "🆘 Support",
    "ℹ️ About",
]

#: The start screen. The descriptor and the boundaries are read from ``telegram/profile``,
#: the same sentences Telegram's own profile shows before the first message.
MAIN_MENU_TEXT = (
    "🏠 Main Menu\n\n"
    f"Welcome to Hilal Markets. {PRODUCT_DESCRIPTOR}\n\n"
    f"{TELEGRAM_ROLE_TEXT}\n"
    "📋 My Monitors: the rules you asked Hilal Markets to watch.\n"
    "🔄 Lifecycles: how close each setup is, from forming to confirmed, no longer valid "
    "or expired.\n\n"
    f"{BOUNDARY_TEXT}"
)

#: Where results are. There is no separate performance page: what the monitors found is
#: on Opportunities, and this used to promise "forward-test analytics" that do not exist.
RESULTS_TEXT = "Results\n\nWhat your monitors found is on the Opportunities page on the website."

#: The "Import strategy" prompt, written in three places before. It asked for
#: "JSON-like rules", which no beginner can act on.
IMPORT_STRATEGY_TEXT = (
    "Paste your strategy in your own words. I will turn it into a draft for you to check "
    "and approve before it can watch the market."
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

#: The labels this bot prints for "leave where you are". One owner, because the connect
#: question and the rest of the menus must not disagree about what "Go Back" means.
_BACK_LABELS: Final[frozenset[str]] = frozenset({"Back", "Go Back", "Main Menu"})

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
                # Start pressed while the connect question is still open and still
                # unanswered. The question is shown again, on the same inline buttons,
                # rather than starting a second account beside it. Once the question has
                # been answered this step is gone, so /start can never bring it back.
                state = existing_conversation.state_data or {}
                return self._telegram_link_prompt(
                    chat_id=message.chat_id,
                    email=str(state.get("dashboard_email") or "") or None,
                    replaces=str(state.get("dashboard_replaces") or "") or None,
                    correlation_id=existing_conversation.correlation_id,
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
            "\n\nDashboard account connected. Your monitors, trial and plan now show up in "
            "this chat."
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
        # The commands in ``telegram/profile.BOT_COMMANDS``. None of them needs an
        # account or an open conversation, so they are answered before either is read.
        command = self._command_name(message.text)
        if command in {"about", "help"}:
            return self._about_message(message)
        if command == "pricing":
            return self._pricing_message(message)
        if command == "support":
            return self._support_menu_message(message)
        conversation = await self._conversation(message.telegram_user_id)
        if conversation is None:
            return self._plain(message, "Send /start to open the main menu.")
        text = message.text.strip()
        if conversation.flow == "telegram_link" and conversation.step == "confirm":
            decision = self._telegram_link_decision(text)
            if decision is ConfirmationReply.AFFIRMATIVE:
                return await self._confirm_dashboard_telegram_link(
                    self._callback_from_message(message, "telegram_link:confirm"),
                    conversation,
                )
            if decision is ConfirmationReply.NEGATIVE:
                return await self._cancel_dashboard_telegram_link(
                    self._callback_from_message(message, "telegram_link:cancel"),
                    conversation,
                )
            return self._telegram_link_reminder_message(message, conversation)
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
                f"Main website: {self._dashboard_url(PUBLIC_HOME_PATH)}",
                buttons=[
                    TelegramButton(
                        "Open Main Website",
                        "external:website",
                        url=self._dashboard_url(PUBLIC_HOME_PATH),
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
                IMPORT_STRATEGY_TEXT,
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
                    # A button that says "Dashboard" opens the dashboard. Every other
                    # one in this file does; these two named the setup chat instead, so
                    # the same word led to two different places.
                    self._dashboard_button("Dashboard"),
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
        if normalized in _BACK_LABELS:
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
                RESULTS_TEXT,
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
                "Lifecycles\n\nOpen Lifecycles on the website to see each setup: which "
                "conditions passed, which are still missing, and the chart.",
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
                response = await self._confirm_dashboard_telegram_link(
                    callback,
                    conversation,
                    # A tap carries the message it was pressed on, which is the question
                    # being answered: the answer replaces it, buttons and all.
                    edited_message_id=callback.message_id,
                )
            elif callback.data == "telegram_link:cancel":
                response = await self._cancel_dashboard_telegram_link(
                    callback,
                    conversation,
                    edited_message_id=callback.message_id,
                )
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
                    IMPORT_STRATEGY_TEXT,
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
                self._dashboard_button("Open Settings", SETTINGS_PATH),
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
                    self._dashboard_button("Settings", SETTINGS_PATH),
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
    def _command_name(text: str) -> str | None:
        """``/about``, ``/About`` and ``/about@hilal_bot`` are all the command ``about``."""

        stripped = text.strip()
        if not stripped.startswith("/"):
            return None
        word = stripped[1:].split(maxsplit=1)[0] if len(stripped) > 1 else ""
        return word.partition("@")[0].casefold() or None

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
            "Start with Hilal Markets for free.\n\n"
            "Create an account on the website and choose the coins and conditions to watch. "
            "Your alerts then arrive in this chat. Nothing is ever traded for you.\n\n"
            "You make every trading decision."
        )
        return self._plain(
            message,
            text,
            buttons=[
                TelegramButton("Create account", "account:signup"),
                TelegramButton(
                    "Open Main Website",
                    "external:website",
                    url=self._dashboard_url(PUBLIC_HOME_PATH),
                ),
                *([] if linked else [TelegramButton("Sign up / sign in", "account:auth")]),
            ],
            menu=["Trial", "About"],
        )

    def _about_text(self) -> str:
        return (
            "ℹ️ About Hilal Markets\n\n"
            f"{PRODUCT_DESCRIPTOR}\n\n"
            "📌 What it does:\n"
            "- Shows which crypto coins are screened, with the evidence behind each status.\n"
            "- Watches the spot market for the conditions you choose.\n"
            "- Sends you an alert here when they are met, with the reasons.\n\n"
            "🧠 How it works:\n"
            "1. On the website, you choose the coins and the conditions to watch.\n"
            "2. You check the rules and switch the monitor on.\n"
            "3. Hilal Markets checks the market and tells you here.\n\n"
            "🛡️ What it does not do:\n"
            "- It does not place trades or move money.\n"
            "- It does not guarantee outcomes.\n"
            "- It never asks for your password, wallet seed phrase or keys.\n\n"
            "Use Telegram for alerts and a quick look. Use the website to build monitors, "
            "change settings, manage your plan and get help."
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
        return (
            "💸 Pricing\n\nSee every plan and its price on the public pricing page. "
            "To choose or change your plan, open the Subscription page."
        )

    def _pricing_buttons(self) -> list[TelegramButton]:
        if self.settings.waitlist_mode:
            return []
        return [
            TelegramButton(
                "Open Pricing",
                "external:pricing",
                url=self._dashboard_url(f"{PRICING_PATH}#pricing"),
            ),
            self._dashboard_button("Subscription page", SUBSCRIPTION_PATH),
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
                self._dashboard_button("Create a ticket", SUPPORT_PATH),
            ]
        )
        return self._plain(
            message,
            "🆘 Support\n\nNeed help? Create a support ticket on the website. Your account "
            "details are attached to it, so we can help you faster.",
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
            else "Create an account or sign in on the website. This chat is then connected "
            "to that account, so your alerts, monitors, trial and plan show up here."
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
                "To start a trial, first create a Hilal Markets account or sign in. The trial "
                "then belongs to that account, so you can never lose it.",
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
        """Kept for the "Activate Free Plan" key already sitting in people's chats.

        It used to switch the account onto the ``demo`` plan from Telegram, outside the one
        place that decides which plan a person may take. It goes to that place now.
        """

        return await self._subscription_page_message(message, conversation)

    async def _billing_checkout_message(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
        plan_code: str,
    ) -> TelegramOutboundMessage:
        """Kept for the "Upgrade ..." keys and ``billing:checkout:*`` buttons already sent.

        This built a payment link for ``trader``, ``pro`` or ``creator`` straight from the
        bot. It never asked ``services/billing.plan_is_on_sale`` — the one owner of
        "may somebody be offered this plan today" — so the bot could open a payment for a
        plan the Subscription page would refuse, and it named no way of paying at all.
        Plans are chosen on the Subscription page, which asks that owner.
        """

        del plan_code
        return await self._subscription_page_message(message, conversation)

    async def _subscription_page_message(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
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
                "Connect a Dashboard account first. A plan belongs to an account, so sign up "
                "or sign in, then choose your plan on the Subscription page.",
                buttons=[
                    TelegramButton("Open Sign Up", "external:signup", url=signup_url),
                    TelegramButton("Open Sign In", "external:signin", url=signin_url),
                ],
                menu=["Sign up", "Sign in", "Go Back"],
            )
        return self._plain(
            message,
            "💳 Plans and payment\n\n"
            "Choose or change your plan on the Subscription page. It shows only the plans "
            "and ways to pay that are open to you right now.",
            buttons=[
                self._dashboard_button("Subscription page", SUBSCRIPTION_PATH),
                TelegramButton("🏠 Main Menu", "back:main"),
            ],
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
                self._dashboard_button("My Drafts", MONITORS_PATH),
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
                self._dashboard_button("My Drafts", MONITORS_PATH),
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
                "Action needed: Quick Scan needs at least one condition it can check.\n\n"
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
        # Both halves used to be written from inside the machine: one named the worker
        # scheduler and the other named the setting, `SCANNING_ENABLED`, to whoever had
        # just switched their first monitor on. The words come from the one owner now, so
        # the bot, the front page and the Monitors page say the same thing.
        checking = market_checking_notice(scanning_enabled=self.settings.scanning_enabled)
        live_text = (
            "It is checking the market now."
            if checking is None
            else f"{checking.title} {checking.detail}"
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
            "The rules as they will be checked:\n"
            + "\n".join(rule_lines)
            + "\n\nEvery alert shows the real value, the value your rule needs, whether it "
            "passed, the timeframe, the candle time and how fresh the prices are.",
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
                self._dashboard_button("Dashboard", MONITORS_PATH),
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
            "Quick Scan\n\nChoose how to check the market once. Your plan decides how many "
            "checks you can run each day.\n\n"
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
                RESULTS_TEXT,
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
                self._dashboard_button("Dashboard", MONITORS_PATH),
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
                else IMPORT_STRATEGY_TEXT
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
            "scan:existing": (
                "Open the Create a monitor page on the website to check the market with one "
                "of your monitors."
            ),
            "scan:new": "Describe a condition and check the market for it once.",
            "scan:template": "Choose a saved template and run a one-off Quick Scan.",
            "scan:previous": (
                "Check the market again from the Create a monitor page on the website."
            ),
            "near:top": "The closest setups are on the Lifecycles page.",
            "near:one_left": "Setups with one condition left are on the Lifecycles page.",
            "near:strategy": "Filter lifecycle cards by strategy in the dashboard.",
            "near:symbol": "Filter lifecycle cards by symbol in the dashboard.",
            "latest:confirmed": (
                "A setup is confirmed when every one of your conditions has passed."
            ),
            "latest:forming": "Forming setups appear when conditions are close but incomplete.",
            "latest:invalidated": "Invalidated setup history is preserved in lifecycle tracking.",
            "latest:expired": (
                "Expired setups are shown when monitored conditions are no longer valid."
            ),
            "settings:channels": "Alert channels can be managed in Settings.",
            "settings:frequency": "Alert frequency and cooldowns can be managed in Settings.",
            "settings:threshold": "Near-Miss thresholds can be managed in Settings.",
            "settings:timezone": "Timezone preferences can be managed in Settings.",
            "proof:view": "Open Lifecycles to see which conditions passed and why.",
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
            "Open this secure Dashboard link. It works once and expires in 30 minutes.\n\n"
            "When you have signed up or signed in, come back to this chat. It will be "
            "connected to your account.",
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
                        self._dashboard_button("My Drafts", MONITORS_PATH),
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
                # Same rule as above: "Dashboard" opens the dashboard, not the setup chat.
                self._dashboard_button("Dashboard"),
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
            offer = await TelegramAccountLinkService(
                self.session,
                self.settings,
            ).pending_dashboard_start_link(raw_token, telegram_user_id=message.telegram_user_id)
        except TelegramAccountLinkError as exc:
            await self.session.rollback()
            return self._plain(
                message,
                f"This Telegram connection link is not available: {escape(str(exc))}\n\n"
                f"Ask for a new one on the Connections page: "
                f"{self._dashboard_url(CONNECTIONS_PATH)}",
                buttons=[self._dashboard_button("Connections page", CONNECTIONS_PATH)],
            )
        conversation = await self._upsert_conversation(
            message,
            user_id=offer.user.id,
            onboarding_session_id=None,
            flow="telegram_link",
            step="confirm",
            state_data={
                "telegram_dashboard_link_token": raw_token,
                "dashboard_email": offer.email,
                "dashboard_replaces": offer.replaces,
            },
        )
        await self.session.commit()
        return self._telegram_link_prompt(
            chat_id=message.chat_id,
            email=offer.email,
            replaces=offer.replaces,
            correlation_id=conversation.correlation_id,
        )

    def _telegram_link_prompt(
        self,
        *,
        chat_id: str,
        email: str | None,
        replaces: str | None,
        correlation_id: str | None,
    ) -> TelegramOutboundMessage:
        """The one question, with one Confirm and one Cancel attached to it.

        Written as the same message from both doors — the ``/start link_...`` itself, and
        a plain ``/start`` sent while the question is still open — so the words a person
        confirms cannot differ between the two.
        """

        visible_email = email or "this dashboard account"
        lines = [
            "🔗 Telegram connection",
            "",
            f"Connect this Telegram account to {visible_email}?",
            "",
            "Once connected, this Telegram chat receives your Hilal Markets alerts. "
            "Nothing is sent before you confirm, and connecting never places a trade "
            "or moves money.",
        ]
        if replaces:
            lines += ["", f"This replaces {replaces}, which is connected to that account now."]
        lines += ["", "Press Confirm to connect, or Cancel to leave it."]
        return TelegramOutboundMessage(
            chat_id=chat_id,
            text="\n".join(lines),
            buttons=self._telegram_link_buttons(),
            menu=[],
            correlation_id=correlation_id,
            keyboard=KEYBOARD_INLINE,
        )

    @staticmethod
    def _telegram_link_buttons() -> list[TelegramButton]:
        """The two buttons of the open question. One owner, so no copy drifts."""

        return [
            TelegramButton("Confirm", "telegram_link:confirm"),
            TelegramButton("Cancel", "telegram_link:cancel"),
        ]

    def _telegram_link_reminder_message(
        self,
        message: TelegramInboundMessage,
        conversation: TelegramConversationState,
    ) -> TelegramOutboundMessage:
        """One line, pointing at the buttons — never the whole question again.

        Re-sending the prompt for every message that was not an exact match is how a
        person got the same message over and over. Anything the shared yes/no reader
        cannot answer still leaves the question open, but it is answered with a line and
        the two buttons, not with the prompt.
        """

        return TelegramOutboundMessage(
            chat_id=message.chat_id,
            text=(
                "Press Confirm to connect, or Cancel to stop. "
                "Nothing has been connected yet."
            ),
            buttons=self._telegram_link_buttons(),
            menu=[],
            correlation_id=conversation.correlation_id,
            keyboard=KEYBOARD_INLINE,
        )

    @classmethod
    def _telegram_link_decision(cls, text: str) -> ConfirmationReply:
        """Read one typed reply against the open connect question.

        The words that mean yes and no are the product's one vocabulary, in
        ``engine/active_question.py`` — the same reader the setup questions use, so a
        person who answers "ok" here is not being read by a stricter list over there.
        Two things are checked first because they are not vocabulary at all: the words
        this screen prints on its own buttons ("Confirm", "Cancel", "Connect"), and the
        app-wide back labels, which mean "leave this step" everywhere in this bot.
        """

        label = normalize_answer_text(cls._normalize_menu_text(text))
        if label in _TELEGRAM_LINK_LABELS:
            return _TELEGRAM_LINK_LABELS[label]
        return resolve_confirmation(text)

    def _telegram_link_answer(
        self,
        *,
        chat_id: str,
        edit_message_id: str | None,
        text: str,
        buttons: list[TelegramButton],
    ) -> TelegramOutboundMessage:
        """Answer a decided connect question, on the message that asked it.

        A tap carries the message to edit, so the question and its two buttons are
        replaced by the answer. A typed reply has no message of ours to edit: it goes out
        as a new message and takes the leftover reply keyboard with it, because a message
        cannot clear a keyboard and carry buttons at the same time — Telegram allows one
        interface per message. Every answer therefore writes its next step into the text
        as well, which is the half that works on both paths.
        """

        if edit_message_id is not None:
            return TelegramOutboundMessage(
                chat_id=chat_id,
                text=text,
                buttons=buttons,
                menu=[],
                edit_message_id=edit_message_id,
                keyboard=KEYBOARD_INLINE,
            )
        return TelegramOutboundMessage(
            chat_id=chat_id,
            text=text,
            menu=[],
            keyboard=KEYBOARD_REMOVE_REPLY_KEYBOARD,
        )

    def _telegram_link_connections_button(self) -> TelegramButton:
        """Where to go next: the Connections page, as a real link rather than a guess."""

        return self._dashboard_button("Connections page", CONNECTIONS_PATH)

    def _telegram_link_refusal_text(self, code: str, *, email: str | None) -> str:
        connections = self._dashboard_url(CONNECTIONS_PATH)
        if code == "telegram_already_linked":
            return (
                "⚠️ This Telegram is already connected to another Hilal Markets account. "
                f"Remove it there first on the Connections page:\n{connections}"
            )
        if code == "telegram_link_expired":
            return (
                "⏳ This Telegram connection expired before it was confirmed.\n\n"
                f"Ask for a new one on the Connections page:\n{connections}"
            )
        if code == "telegram_link_used":
            return (
                "ℹ️ This Telegram connection link has already been used.\n\n"
                f"If this chat is not connected yet, start again on the Connections page:\n"
                f"{connections}"
            )
        if code == "telegram_link_invalid":
            return (
                "⚠️ This Telegram connection link is not one I can use, so nothing was "
                f"connected.\n\nStart again on the Connections page:\n{connections}"
            )
        return (
            "⚠️ Telegram could not be connected, so nothing was changed.\n\n"
            f"Start again on the Connections page:\n{connections}"
        )

    async def _confirm_dashboard_telegram_link(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
        *,
        edited_message_id: str | None = None,
    ) -> TelegramOutboundMessage:
        """Answer the connect question. Every outcome leaves the step behind.

        The step used to survive every failure, which is what made the prompt come back
        for the rest of the conversation: an expired link, a Telegram belonging to someone
        else and an unexpected error all answered the person and then left the question
        open underneath. Whatever happens here, the step ends, the pending token is
        forgotten, and the answer names one way forward.
        """

        state = conversation.state_data or {}
        raw_token = str(state.get("telegram_dashboard_link_token") or "")
        email = str(state.get("dashboard_email") or "") or None
        links = TelegramAccountLinkService(self.session, self.settings)
        if not raw_token:
            # The question was already answered, or was never opened in this conversation.
            # Say which, instead of pretending a fresh decision was made or asking again.
            holder = await links.account_holding(callback.telegram_user_id)
            if holder is not None and holder == conversation.user_id:
                return await self._finish_telegram_link_step(
                    callback,
                    conversation,
                    edited_message_id=edited_message_id,
                    text=self._telegram_link_connected_text(email),
                    buttons=[],
                )
            return await self._finish_telegram_link_step(
                callback,
                conversation,
                edited_message_id=edited_message_id,
                text=self._telegram_link_refusal_text("telegram_link_invalid", email=email),
                buttons=[self._telegram_link_connections_button()],
                owner_id=holder,
            )
        try:
            completion = await links.complete_dashboard_start_link(
                raw_token=raw_token,
                telegram_user_id=callback.telegram_user_id,
                chat_id=callback.chat_id,
                username=conversation.username,
            )
        except TelegramAccountLinkError as exc:
            await self.session.rollback()
            # The conversation was pointed at the account named in the link when the
            # question was asked. If the answer is no, it goes back to the account this
            # Telegram is actually on: a chat that was refused must not keep acting as the
            # account that refused it.
            holder = await links.account_holding(callback.telegram_user_id)
            return await self._finish_telegram_link_step(
                callback,
                await self._conversation(callback.telegram_user_id),
                edited_message_id=edited_message_id,
                text=self._telegram_link_refusal_text(exc.code, email=email),
                buttons=[self._telegram_link_connections_button()],
                owner_id=holder,
            )
        except Exception:  # noqa: BLE001 - a step that outlives its own crash is the trap
            await self.session.rollback()
            holder = await links.account_holding(callback.telegram_user_id)
            return await self._finish_telegram_link_step(
                callback,
                await self._conversation(callback.telegram_user_id),
                edited_message_id=edited_message_id,
                text=self._telegram_link_refusal_text("unexpected", email=email),
                buttons=[self._telegram_link_connections_button()],
                owner_id=holder,
            )
        return await self._finish_telegram_link_step(
            callback,
            conversation,
            edited_message_id=edited_message_id,
            text=self._telegram_link_connected_text(completion.email),
            buttons=[],
            owner_id=completion.user.id,
        )

    def _telegram_link_connected_text(self, email: str | None) -> str:
        return (
            "✅ Telegram connected\n\n"
            "This chat now receives your Hilal Markets alerts"
            f"{f' for {email}' if email else ''}.\n\n"
            "You can change this any time on the Connections page: "
            f"{self._dashboard_url(CONNECTIONS_PATH)}"
        )

    async def _cancel_dashboard_telegram_link(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState,
        *,
        edited_message_id: str | None = None,
    ) -> TelegramOutboundMessage:
        return await self._finish_telegram_link_step(
            callback,
            conversation,
            edited_message_id=edited_message_id,
            text=(
                "Cancelled — nothing was connected.\n\n"
                "To connect later, start again on the Connections page: "
                f"{self._dashboard_url(CONNECTIONS_PATH)}"
            ),
            buttons=[],
        )

    async def _finish_telegram_link_step(
        self,
        callback: TelegramCallback,
        conversation: TelegramConversationState | None,
        *,
        edited_message_id: str | None,
        text: str,
        buttons: list[TelegramButton],
        owner_id: UUID | None = None,
    ) -> TelegramOutboundMessage:
        """End the connect step, and answer it in the same breath.

        The commit is here rather than left to the caller on purpose: a step ended only in
        the session's pending state is still open tomorrow if the message after it fails,
        and an open step is the prompt coming back.
        """

        if conversation is not None:
            state = dict(conversation.state_data or {})
            state.pop("telegram_dashboard_link_token", None)
            state.pop("dashboard_replaces", None)
            conversation.flow = "main_menu"
            conversation.step = "idle"
            conversation.state_data = state
            if owner_id is not None:
                conversation.user_id = owner_id
            await self.session.commit()
        return self._telegram_link_answer(
            chat_id=callback.chat_id,
            edit_message_id=edited_message_id,
            text=text,
            buttons=buttons,
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
                result_payload=response.to_payload(),
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

    def _dashboard_url(self, path: str = HOME_PATH) -> str:
        """An address in a message. ``path`` is always a name from ``core/dashboard_paths``.

        ``tests/integration/test_telegram_links_resolve.py`` refuses a path typed into a
        call here, and opens every one the bot can send against the real app.
        """

        normalized = path if path.startswith("/") else f"/{path}"
        return f"{str(self.settings.public_base_url).rstrip('/')}{normalized}"

    def _dashboard_button(self, label: str = "Dashboard", path: str = HOME_PATH) -> TelegramButton:
        return TelegramButton(label, "external:dashboard", url=self._dashboard_url(path))

    @staticmethod
    def _dashboard_path_for_page(page: str) -> str:
        return {
            # Home. "/dashboard" only redirects here now.
            "home": HOME_PATH,
            "how": HOW_IT_WORKS_PATH,
            "about": ABOUT_PATH,
            # The plan page the redesigned dashboard uses. "/dashboard/billing" is the older
            # billing screen; both names stay because they are in buttons already sent.
            "billing": SUBSCRIPTION_PATH,
            "trial": SUBSCRIPTION_PATH,
            "subscription": SUBSCRIPTION_PATH,
            # One page authors a monitor: the canvas. Both names are kept because both
            # are already written into buttons in messages that have been sent.
            "builder": MONITOR_PATH,
            "create_monitor": MONITOR_PATH,
            # The monitors somebody already has, on the page that lists them. This
            # pointed at a section of the setup-chat page that is hidden, so the button
            # landed on a chat and showed no monitors at all.
            "monitors": MONITORS_PATH,
            "scan": _ONE_TIME_SCAN_PATH,
            "near_miss": LIFECYCLES_PATH,
            "lifecycles": LIFECYCLES_PATH,
            "setups": LIFECYCLES_PATH,
            "alerts": LIFECYCLES_PATH,
            "settings": SETTINGS_PATH,
            "support": SUPPORT_PATH,
            "connections": CONNECTIONS_PATH,
            # What the monitors found. There is no separate performance page.
            "performance": OPPORTUNITIES_PATH,
            "opportunities": OPPORTUNITIES_PATH,
            "setup_replay": LIFECYCLES_PATH,
            "why_no_alert": LIFECYCLES_PATH,
        }.get(page, HOME_PATH)

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
                "Connect a Dashboard account first. Sign up or sign in with a secure link "
                "below, and this chat is connected to your account.",
                buttons=[
                    TelegramButton("Open Sign Up", "external:signup", url=signup_url),
                    TelegramButton("Open Sign In", "external:signin", url=signin_url),
                ],
            )
        return self._plain_callback(
            callback,
            "Open this page on the website.",
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
        """Read back an answer kept for a replay. One reader: see ``TelegramOutboundMessage``."""

        return TelegramOutboundMessage.from_payload(payload)
