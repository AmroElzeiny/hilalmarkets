from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    DashboardPreference,
    DiscordConnection,
    DiscordRoleMapping,
    DiscordRoleSyncJob,
    DiscordSetupThread,
    SetupInstance,
    Strategy,
    StrategyVersion,
    Subscription,
    SupportRequest,
    User,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConnectionStatus,
    DeliveryStatus,
    SetupLifecycleState,
    StrategyStatus,
    StrategyVersionStatus,
    SubscriptionStatus,
)
from ai_market_monitor.discord.service import (
    DiscordAlertService,
    DiscordConnectionService,
    DiscordError,
    DiscordModerationService,
    DiscordRoleSyncService,
    DiscordSlashCommandService,
    DiscordSupportService,
)
from ai_market_monitor.discord.types import (
    DiscordCommandContext,
    DiscordOAuthProfile,
    DiscordPermissionSet,
    DiscordSendResult,
)
from ai_market_monitor.services.entitlements import PlanCatalogService
from tests.factories import load_strategy


class RecordingDiscordGateway:
    def __init__(self):
        self.tests = []
        self.embeds = []
        self.threads = []
        self.roles = []
        self.fail_next_embed = False

    async def send_test(self, *, destination):
        self.tests.append(destination)
        return DiscordSendResult(provider_message_id=f"test-{len(self.tests)}")

    async def send_embed(self, *, destination, embed, thread_id=None):
        if self.fail_next_embed:
            self.fail_next_embed = False
            raise RuntimeError("discord unavailable")
        self.embeds.append((destination, embed, thread_id))
        return DiscordSendResult(provider_message_id=f"msg-{len(self.embeds)}", thread_id=thread_id)

    async def create_thread(self, *, destination, name, first_message_id=None):
        thread_id = f"thread-{len(self.threads) + 1}"
        self.threads.append((destination, name, first_message_id, thread_id))
        return thread_id

    async def sync_role(self, *, discord_user_id, guild_id, role_id, action):
        self.roles.append((discord_user_id, guild_id, role_id, action))


async def create_user(session, display_name: str = "Discord Trader") -> User:
    user = User(display_name=display_name)
    session.add(user)
    await session.flush()
    return user


async def grant_pro_access(session, user_id, settings):
    plan = await PlanCatalogService(session).get_or_sync("pro")
    session.add(
        Subscription(
            user_id=user_id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            provider="test",
            provider_customer_id=f"cus_{user_id}",
            provider_subscription_id=f"sub_{user_id}_pro",
            current_period_start=datetime.now(UTC),
            current_period_end=datetime.now(UTC) + timedelta(days=30),
        )
    )
    await session.flush()


def full_permissions() -> DiscordPermissionSet:
    return DiscordPermissionSet(
        send_messages=True,
        embed_links=True,
        read_message_history=True,
        create_public_threads=True,
        send_messages_in_threads=True,
        manage_roles=True,
    )


async def create_strategy_version(session, user_id):
    definition = load_strategy()
    strategy = Strategy(user_id=user_id, name="Sweep", status=StrategyStatus.ACTIVE)
    session.add(strategy)
    await session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version_number=1,
        status=StrategyVersionStatus.ACTIVE,
        source_type="structured",
        schema_json=definition.model_dump(mode="json"),
        schema_hash=definition.canonical_hash(),
        interpretation_provider="test",
        approved_at=datetime.now(UTC),
        approved_schema_hash=definition.canonical_hash(),
        activated_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()
    strategy.active_version_id = version.id
    return strategy, version


async def test_discord_oauth_links_existing_user_and_prevents_duplicate_account(test_context):
    async with test_context["session_factory"]() as session:
        gateway = RecordingDiscordGateway()
        user = await create_user(session)
        service = DiscordConnectionService(session, test_context["settings"], gateway)
        state = await service.generate_oauth_state(
            user_id=user.id,
            redirect_url="https://example.test/discord/callback",
        )
        connection = await service.complete_oauth(
            state=state,
            profile=DiscordOAuthProfile(
                discord_user_id="discord-123",
                username="market_trader",
                email="person@example.com",
                email_verified=True,
                scopes=["identify", "email"],
            ),
        )
        assert connection.user_id == user.id
        assert connection.status == ConnectionStatus.ACTIVE
        assert await session.scalar(select(func.count(User.id))) == 1
        assert await session.scalar(select(func.count(DiscordConnection.id))) == 1

        other = await create_user(session, "Other")
        second_state = await service.generate_oauth_state(
            user_id=other.id,
            redirect_url="https://example.test/discord/callback",
        )
        with pytest.raises(DiscordError) as conflict:
            await service.complete_oauth(
                state=second_state,
                profile=DiscordOAuthProfile(discord_user_id="discord-123", username="same"),
            )
        assert conflict.value.code == "discord_identity_in_use"


async def test_discord_destination_requires_entitlement_and_permissions(test_context):
    async with test_context["session_factory"]() as session:
        gateway = RecordingDiscordGateway()
        user = await create_user(session)
        service = DiscordConnectionService(session, test_context["settings"], gateway)
        with pytest.raises(DiscordError) as feature_error:
            await service.select_destination(
                user_id=user.id,
                mode="dm",
                discord_user_id="discord-123",
                permissions=full_permissions(),
            )
        assert feature_error.value.code == "feature_not_available"

        await grant_pro_access(session, user.id, test_context["settings"])
        with pytest.raises(DiscordError) as permission_error:
            await service.select_destination(
                user_id=user.id,
                mode="dm",
                discord_user_id="discord-123",
                permissions=DiscordPermissionSet(send_messages=True),
            )
        assert permission_error.value.code == "missing_permissions"

        destination = await service.select_destination(
            user_id=user.id,
            mode="dm",
            discord_user_id="discord-123",
            permissions=full_permissions(),
        )
        assert destination.permissions_status == "ok"
        assert destination.test_status == "sent"
        assert gateway.tests


async def test_discord_alert_embed_delivery_reuses_setup_thread_and_suppresses_duplicates(
    test_context,
):
    async with test_context["session_factory"]() as session:
        gateway = RecordingDiscordGateway()
        user = await create_user(session)
        await grant_pro_access(session, user.id, test_context["settings"])
        connection_service = DiscordConnectionService(session, test_context["settings"], gateway)
        await connection_service.register_guild_installation(
            installed_by_user_id=user.id,
            guild_id="guild-1",
            guild_name="Creator Server",
            permissions=full_permissions(),
        )
        destination = await connection_service.select_destination(
            user_id=user.id,
            mode="server_channel",
            guild_id="guild-1",
            channel_id="alerts",
            permissions=full_permissions(),
        )
        _, version = await create_strategy_version(session, user.id)
        setup = SetupInstance(
            user_id=user.id,
            strategy_version_id=version.id,
            exchange="binance",
            symbol="SOL/USDT",
            timeframe="15m",
            setup_key="sol-sweep-1",
            state=SetupLifecycleState.CONFIRMED,
            completion_score=Decimal("100"),
            first_detected_at=datetime.now(UTC) - timedelta(minutes=15),
            last_evaluated_at=datetime.now(UTC),
            confirmed_at=datetime.now(UTC),
        )
        session.add(setup)
        await session.flush()
        alert = Alert(
            user_id=user.id,
            strategy_version_id=version.id,
            setup_instance_id=setup.id,
            alert_type=AlertType.CONFIRMED,
            deduplication_key="discord-alert-1",
            title="SOL/USDT confirmed",
            body="Deterministic proof attached.",
            proof_receipt={
                "strategy_name": "Liquidity Sweep Continuation",
                "symbol": "SOL/USDT",
                "direction": "Bullish Setup",
                "exchange": "binance",
                "timeframe": "15m",
                "completion_score": 100,
                "entry_zone": {"low": 100, "high": 101},
                "current_price": 100.5,
                "stop": 99,
                "stop_distance": "1.5%",
                "targets": [{"price": 103}, {"price": 104.5}],
                "reward_to_risk": "2.5R",
                "data_freshness": "1.2s",
                "conditions": [{"key": "volume", "state": "passed"}],
                "setup_state": "confirmed",
            },
            chart_snapshot_url="chart://sol",
            candle_timestamp=datetime.now(UTC),
        )
        session.add(alert)
        await session.flush()

        service = DiscordAlertService(session, gateway)
        delivery = await service.deliver_alert(alert=alert, destination=destination)
        duplicate = await service.deliver_alert(alert=alert, destination=destination)
        assert delivery.id == duplicate.id
        assert delivery.status == DeliveryStatus.SENT
        assert await session.scalar(select(func.count(AlertDelivery.id))) == 1
        assert await session.scalar(select(func.count(DiscordSetupThread.id))) == 1
        assert len(gateway.threads) == 1
        assert len(gateway.embeds) == 1
        embed = gateway.embeds[0][1]
        assert embed.title == "SOL/USDT - Research Match Confirmed"
        field_names = {field.name for field in embed.fields}
        assert "Required completion" in field_names
        assert "Strategy version" in field_names
        assert "User-defined trade context" in field_names
        assert embed.metadata["proof"]["conditions"][0]["state"] == "passed"
        action_labels = {action.label for action in embed.actions}
        assert action_labels == {"🔄 View lifecycle", "📊 Dashboard", "🔕 Mute symbol"}

        gateway.fail_next_embed = True
        second_alert = Alert(
            user_id=user.id,
            strategy_version_id=version.id,
            setup_instance_id=setup.id,
            alert_type=AlertType.LIFECYCLE,
            deduplication_key="discord-alert-2",
            title="SOL update",
            body="Setup update.",
            proof_receipt={"symbol": "SOL/USDT", "conditions": []},
        )
        session.add(second_alert)
        await session.flush()
        failed = await service.deliver_alert(alert=second_alert, destination=destination)
        assert failed.status == DeliveryStatus.FAILED_RETRYABLE
        assert failed.next_retry_at is not None


async def test_discord_role_sync_support_moderation_and_slash_commands(test_context):
    async with test_context["session_factory"]() as session:
        gateway = RecordingDiscordGateway()
        user = await create_user(session)
        await PlanCatalogService(session).sync_defaults()
        plan = await PlanCatalogService(session).get_or_sync("pro")
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider="stripe",
                provider_customer_id="cus_roles",
                provider_subscription_id="sub_roles",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
            )
        )
        session.add(
            DiscordConnection(
                user_id=user.id,
                discord_user_id="discord-roles",
                status=ConnectionStatus.ACTIVE,
                oauth_scopes=["identify"],
                connected_at=datetime.now(UTC),
            )
        )
        session.add_all(
            [
                DiscordRoleMapping(
                    guild_id="guild-roles",
                    role_id="role-pro",
                    role_name="Pro",
                    entitlement_key="plan",
                    plan_code="pro",
                ),
                DiscordRoleMapping(
                    guild_id="guild-roles",
                    role_id="role-trader",
                    role_name="Trader",
                    entitlement_key="plan",
                    plan_code="trader",
                ),
            ]
        )
        await session.flush()
        role_service = DiscordRoleSyncService(session, gateway)
        jobs = await role_service.enqueue_for_user(
            user_id=user.id,
            source_event_id="evt_roles",
            current_role_ids={"role-trader"},
        )
        assert {job.action for job in jobs} == {"add", "remove"}
        await role_service.process_due()
        assert ("discord-roles", "guild-roles", "role-pro", "add") in gateway.roles
        assert ("discord-roles", "guild-roles", "role-trader", "remove") in gateway.roles
        assert await session.scalar(select(func.count(DiscordRoleSyncJob.id))) == 2

        response = await DiscordSlashCommandService(session, test_context["settings"]).handle(
            DiscordCommandContext(
                command_name="/subscription",
                user_id=user.id,
                discord_user_id="discord-roles",
            )
        )
        assert "Current plan: Pro" in response.content
        assert "Dashboard" in response.content
        assert response.actions[0].url is not None
        assert "/dashboard/link/" in response.actions[0].url
        assert "stripe" not in response.content.lower()

        await create_strategy_version(session, user.id)
        create_response = await DiscordSlashCommandService(
            session, test_context["settings"]
        ).handle(
            DiscordCommandContext(
                command_name="/create_monitor",
                user_id=user.id,
                discord_user_id="discord-roles",
            )
        )
        assert "Strategy Builder" in create_response.content
        monitors_response = await DiscordSlashCommandService(
            session, test_context["settings"]
        ).handle(
            DiscordCommandContext(
                command_name="/monitors",
                user_id=user.id,
                discord_user_id="discord-roles",
            )
        )
        assert "Sweep: active" in monitors_response.content
        replay_response = await DiscordSlashCommandService(
            session, test_context["settings"]
        ).handle(
            DiscordCommandContext(
                command_name="/setup_replay",
                user_id=user.id,
                discord_user_id="discord-roles",
                options={"strategy_id": "strategy-id", "symbol": "SOL/USDT"},
            )
        )
        assert "Lifecycles" in replay_response.content
        assert replay_response.actions[0].label == "Open Lifecycles"
        assert replay_response.actions[0].url is not None

        settings_response = await DiscordSlashCommandService(
            session, test_context["settings"]
        ).handle(
            DiscordCommandContext(
                command_name="/settings",
                user_id=user.id,
                discord_user_id="discord-roles",
                options={
                    "theme": "light",
                    "timezone": "Europe/Moscow",
                    "near_miss_enabled": True,
                    "near_miss_threshold": 88,
                    "maximum_alerts_per_hour": 4,
                    "alert_days": "Tuesday Thursday",
                    "alert_hours": "08:00, 22:00",
                },
            )
        )
        assert "Theme: light" in settings_response.content
        preference = await session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user.id)
        )
        assert preference is not None
        assert preference.theme == "light"
        assert preference.notification_preferences["alert_days"] == ["Tuesday", "Thursday"]
        assert preference.notification_preferences["alert_hours"] == ["08:00", "22:00"]

        ticket = await DiscordSupportService(session).create_ticket(
            user_id=user.id,
            category="discord_delivery",
            description="My Discord alert did not arrive.",
        )
        assert ticket.context["plan"] == "pro"
        assert ticket.context["discord_connection"] is True
        assert await session.scalar(select(func.count(SupportRequest.id))) == 1

        moderation = DiscordModerationService().assess(
            content="Guaranteed profit if you send your seed phrase",
            attachment_names=["installer.exe"],
        )
        assert moderation.allowed is False
        assert moderation.requires_human_review is True
        assert {finding.code for finding in moderation.findings} >= {
            "guaranteed_profit_claim",
            "unsafe_support_request",
            "malicious_file_type",
        }
