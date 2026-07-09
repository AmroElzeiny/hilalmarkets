from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ai_market_monitor.core.platforms import Platform, PlatformCapability, capability_rule
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    AuditEvent,
    DashboardPreference,
    DiscordDeliveryDestination,
    Strategy,
    TelegramConnection,
    User,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConnectionStatus,
    DeliveryChannel,
    StrategyStatus,
)
from ai_market_monitor.services.dashboard_links import DashboardLinkService
from ai_market_monitor.services.monitor_operations import MonitorOperationService
from ai_market_monitor.services.notifications import NotificationDispatcher
from tests.factories import load_strategy


async def _user(session) -> User:
    user = User(display_name="Platform Trader")
    session.add(user)
    await session.flush()
    return user


async def _alert(
    session,
    user: User,
    *,
    alert_type: AlertType = AlertType.CONFIRMED,
    suffix: str = "1",
) -> Alert:
    alert = Alert(
        user_id=user.id,
        alert_type=alert_type,
        deduplication_key=f"handoff-{user.id}-{alert_type.value}-{suffix}",
        title="SOL/USDT confirmed",
        body="Deterministic proof attached.",
        proof_receipt={
            "strategy_name": "Liquidity Sweep Continuation",
            "symbol": "SOL/USDT",
            "completion_score": 85,
            "conditions": [{"name": "Volume", "state": "passed"}],
        },
        candle_timestamp=datetime.now(UTC),
    )
    session.add(alert)
    await session.flush()
    return alert


async def test_platform_matrix_keeps_full_billing_dashboard_owned():
    telegram_rule = capability_rule(Platform.TELEGRAM, PlatformCapability.FULL_BILLING)
    discord_rule = capability_rule(Platform.DISCORD, PlatformCapability.FULL_BILLING)
    dashboard_rule = capability_rule(Platform.DASHBOARD, PlatformCapability.FULL_BILLING)

    assert telegram_rule.enabled is False
    assert telegram_rule.handoff_platform == Platform.DASHBOARD
    assert discord_rule.enabled is False
    assert discord_rule.handoff_platform == Platform.DASHBOARD
    assert dashboard_rule.enabled is True


async def test_signed_dashboard_link_logs_in_correct_user_and_is_single_use(test_context):
    async with test_context["session_factory"]() as session:
        user = await _user(session)
        url = await DashboardLinkService(session, test_context["settings"]).create(
            user_id=user.id,
            source_platform=Platform.TELEGRAM,
            source_subject="tg-123",
            target_path="/dashboard/billing",
        )
        await session.commit()

    path = url.removeprefix(str(test_context["settings"].public_base_url))
    first = await test_context["client"].get(path, follow_redirects=False)
    assert first.status_code == 303
    assert first.headers["location"] == "/dashboard/billing"
    assert "amm_session=" in first.headers["set-cookie"]

    second = await test_context["client"].get(path, follow_redirects=False)
    assert second.status_code == 303
    assert second.headers["location"] == "/signin?error=dashboard_link_used"


async def test_signed_dashboard_link_expiry_fails_safely(test_context):
    async with test_context["session_factory"]() as session:
        user = await _user(session)
        url = await DashboardLinkService(session, test_context["settings"]).create(
            user_id=user.id,
            source_platform=Platform.DISCORD,
            source_subject="discord-123",
            target_path="/dashboard",
            ttl_minutes=-1,
        )
        await session.commit()

    path = url.removeprefix(str(test_context["settings"].public_base_url))
    response = await test_context["client"].get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/signin?error=dashboard_link_expired"


async def test_notification_preferences_filter_cross_channel_deliveries(test_context):
    async with test_context["session_factory"]() as session:
        user = await _user(session)
        session.add(
            TelegramConnection(
                user_id=user.id,
                telegram_user_id="tg-pref",
                chat_id="chat-pref",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        session.add(
            DiscordDeliveryDestination(
                user_id=user.id,
                mode="dm",
                discord_user_id="discord-pref",
                permissions_status="ok",
                test_status="sent",
                status="active",
            )
        )
        session.add(
            DashboardPreference(
                user_id=user.id,
                notification_preferences={"channels": ["telegram", "discord"]},
            )
        )
        alert = await _alert(session, user)
        definition = load_strategy()
        definition.alerts.channels = ["telegram", "discord"]
        deliveries = await NotificationDispatcher(session).enqueue(alert, definition)

        assert {delivery.channel for delivery in deliveries} == {
            DeliveryChannel.TELEGRAM,
            DeliveryChannel.DISCORD,
        }
        assert await session.scalar(select(func.count(Alert.id))) == 1
        assert await session.scalar(select(func.count(AlertDelivery.id))) == 2


async def test_notification_preferences_mute_near_miss_without_ui_bypass(test_context):
    async with test_context["session_factory"]() as session:
        user = await _user(session)
        session.add(
            TelegramConnection(
                user_id=user.id,
                telegram_user_id="tg-muted",
                chat_id="chat-muted",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        session.add(
            DashboardPreference(
                user_id=user.id,
                notification_preferences={
                    "channels": ["telegram"],
                    "near_miss_enabled": False,
                },
            )
        )
        alert = await _alert(session, user, alert_type=AlertType.NEAR_MISS)
        deliveries = await NotificationDispatcher(session).enqueue(alert, load_strategy())

        assert deliveries == []
        assert await session.scalar(select(func.count(AlertDelivery.id))) == 0


async def test_notification_preferences_enforce_user_wide_hourly_cap(test_context):
    async with test_context["session_factory"]() as session:
        user = await _user(session)
        session.add(
            TelegramConnection(
                user_id=user.id,
                telegram_user_id="tg-cap",
                chat_id="chat-cap",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        session.add(
            DashboardPreference(
                user_id=user.id,
                notification_preferences={
                    "channels": ["telegram"],
                    "maximum_alerts_per_hour": 1,
                },
            )
        )
        first = await _alert(session, user, suffix="first")
        first_deliveries = await NotificationDispatcher(session).enqueue(
            first,
            load_strategy(),
        )
        second = await _alert(session, user, suffix="second")
        second_deliveries = await NotificationDispatcher(session).enqueue(
            second,
            load_strategy(),
        )

        assert len(first_deliveries) == 1
        assert second_deliveries == []


async def test_notification_preferences_enforce_user_wide_daily_cap(test_context):
    async with test_context["session_factory"]() as session:
        user = await _user(session)
        session.add(
            TelegramConnection(
                user_id=user.id,
                telegram_user_id="tg-daily-cap",
                chat_id="chat-daily-cap",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        session.add(
            DashboardPreference(
                user_id=user.id,
                notification_preferences={
                    "channels": ["telegram"],
                    "maximum_alerts_per_hour": 50,
                    "maximum_alerts_per_day": 1,
                    "timezone": "UTC",
                },
            )
        )
        first = await _alert(session, user, suffix="daily-first")
        first_deliveries = await NotificationDispatcher(session).enqueue(
            first,
            load_strategy(),
        )
        second = await _alert(session, user, suffix="daily-second")
        second_deliveries = await NotificationDispatcher(session).enqueue(
            second,
            load_strategy(),
        )

        assert len(first_deliveries) == 1
        assert second_deliveries == []


async def test_notification_schedule_uses_selected_timezone(test_context):
    async with test_context["session_factory"]() as session:
        user = await _user(session)
        session.add(
            TelegramConnection(
                user_id=user.id,
                telegram_user_id="tg-schedule",
                chat_id="chat-schedule",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        local_now = datetime.now(UTC).astimezone(ZoneInfo("Asia/Tokyo"))
        blocked_hour = f"{(local_now.hour + 1) % 24:02d}:00"
        session.add(
            DashboardPreference(
                user_id=user.id,
                default_timezone="Asia/Tokyo",
                notification_preferences={
                    "channels": ["telegram"],
                    "timezone": "Asia/Tokyo",
                    "alert_days": ["Every Day"],
                    "alert_hours": [blocked_hour],
                },
            )
        )
        alert = await _alert(session, user, suffix="timezone")
        deliveries = await NotificationDispatcher(session).enqueue(alert, load_strategy())

        assert deliveries == []


async def test_shared_monitor_operations_pause_resume_and_audit(test_context):
    async with test_context["session_factory"]() as session:
        user = await _user(session)
        strategy = Strategy(user_id=user.id, name="Shared Ops", status=StrategyStatus.ACTIVE)
        session.add(strategy)
        await session.flush()

        service = MonitorOperationService(session)
        await service.pause(user_id=user.id, strategy_id=strategy.id, actor_type="dashboard_user")
        assert strategy.status == StrategyStatus.PAUSED
        assert strategy.paused_at is not None

        await service.resume(user_id=user.id, strategy_id=strategy.id, actor_type="discord_user")
        assert strategy.status == StrategyStatus.ACTIVE
        assert strategy.paused_at is None
        assert await session.scalar(select(func.count(AuditEvent.id))) == 2
