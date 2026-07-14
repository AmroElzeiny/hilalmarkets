from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.platforms import Platform
from ai_market_monitor.core.security import opaque_token, token_digest
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    CandidateReadinessSnapshot,
    DashboardPreference,
    DiscordConnection,
    DiscordDeliveryDestination,
    DiscordGuildInstallation,
    DiscordOAuthState,
    DiscordRoleMapping,
    DiscordRoleSyncJob,
    DiscordSetupThread,
    IntegrationHealth,
    SetupInstance,
    Strategy,
    StrategyVersion,
    SupportRequest,
    TelegramConnection,
    User,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
    HealthStatus,
    IdentityProvider,
    SupportRequestStatus,
)
from ai_market_monitor.discord.types import (
    DiscordAction,
    DiscordCommandContext,
    DiscordCommandResponse,
    DiscordEmbed,
    DiscordField,
    DiscordOAuthProfile,
    DiscordPermissionSet,
    DiscordSendResult,
    ModerationFinding,
    ModerationResult,
)
from ai_market_monitor.schemas.onboarding import IdentityInput
from ai_market_monitor.services.alert_presentation import AlertPresentation
from ai_market_monitor.services.dashboard_links import DashboardLinkService
from ai_market_monitor.services.entitlements import EntitlementError, EntitlementService
from ai_market_monitor.services.identity import IdentityConflictError, IdentityService
from ai_market_monitor.services.monitor_operations import (
    MonitorOperationError,
    MonitorOperationService,
)
from ai_market_monitor.services.template_catalog import BUILTIN_STRATEGY_TEMPLATES
from ai_market_monitor.services.trials import TrialLifecycleService

DISCORD_ALERT_DAYS = {
    "Every Day",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
}
DISCORD_ALERT_HOURS = {f"{hour:02d}:00" for hour in range(24)}
DISCORD_THEMES = {"dark", "light", "system"}


class DiscordError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DiscordGateway(Protocol):
    async def send_test(self, *, destination: DiscordDeliveryDestination) -> DiscordSendResult: ...

    async def send_embed(
        self,
        *,
        destination: DiscordDeliveryDestination,
        embed: DiscordEmbed,
        thread_id: str | None = None,
    ) -> DiscordSendResult: ...

    async def create_thread(
        self,
        *,
        destination: DiscordDeliveryDestination,
        name: str,
        first_message_id: str | None = None,
    ) -> str: ...

    async def sync_role(
        self,
        *,
        discord_user_id: str,
        guild_id: str,
        role_id: str,
        action: str,
    ) -> None: ...


class NoopDiscordGateway:
    async def send_test(self, *, destination: DiscordDeliveryDestination) -> DiscordSendResult:
        key = destination.channel_id or destination.discord_user_id or "unknown"
        return DiscordSendResult(provider_message_id=f"test_{key}")

    async def send_embed(
        self,
        *,
        destination: DiscordDeliveryDestination,
        embed: DiscordEmbed,
        thread_id: str | None = None,
    ) -> DiscordSendResult:
        key = destination.channel_id or destination.discord_user_id or "unknown"
        suffix = abs(hash((embed.title, key, thread_id))) % 1_000_000
        return DiscordSendResult(provider_message_id=f"msg_{suffix}", thread_id=thread_id)

    async def create_thread(
        self,
        *,
        destination: DiscordDeliveryDestination,
        name: str,
        first_message_id: str | None = None,
    ) -> str:
        key = destination.channel_id or destination.discord_user_id or "unknown"
        return f"thread_{abs(hash((key, name, first_message_id))) % 1_000_000}"

    async def sync_role(
        self,
        *,
        discord_user_id: str,
        guild_id: str,
        role_id: str,
        action: str,
    ) -> None:
        return None


def configured_discord_gateway(
    gateway: DiscordGateway | None,
    settings: Settings | None,
) -> DiscordGateway:
    if gateway is not None:
        return gateway
    if settings is not None and settings.discord_adapter == "http":
        from ai_market_monitor.discord.http_gateway import DiscordHttpGateway

        return DiscordHttpGateway(settings)
    if settings is not None and settings.is_deployed:
        raise DiscordError(
            "discord_gateway_missing",
            "A real Discord gateway must be configured in staging and production.",
        )
    return NoopDiscordGateway()


class DiscordConnectionService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        gateway: DiscordGateway | None = None,
    ):
        self.session = session
        self.settings = settings
        self.gateway = configured_discord_gateway(gateway, settings)

    async def generate_oauth_state(
        self,
        *,
        user_id: UUID,
        redirect_url: str,
        scopes: list[str] | None = None,
        metadata: dict | None = None,
    ) -> str:
        state = opaque_token()
        self.session.add(
            DiscordOAuthState(
                user_id=user_id,
                state_digest=token_digest(state),
                redirect_url=redirect_url,
                scopes=scopes or ["identify", "email", "guilds"],
                metadata_json=metadata or {},
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return state

    async def complete_oauth(
        self,
        *,
        state: str,
        profile: DiscordOAuthProfile,
    ) -> DiscordConnection:
        oauth_state = await self.session.scalar(
            select(DiscordOAuthState).where(
                DiscordOAuthState.state_digest == token_digest(state),
                DiscordOAuthState.consumed_at.is_(None),
                DiscordOAuthState.expires_at > datetime.now(UTC),
            )
        )
        if oauth_state is None:
            raise DiscordError("invalid_oauth_state", "Discord connection link expired.")
        user = await self.session.get(User, oauth_state.user_id)
        if user is None:
            raise DiscordError(
                "user_missing",
                "The account for this Discord link no longer exists.",
            )
        existing = await self.session.scalar(
            select(DiscordConnection).where(
                DiscordConnection.discord_user_id == profile.discord_user_id
            )
        )
        if existing and existing.user_id != user.id:
            raise DiscordError(
                "discord_identity_in_use",
                "That Discord account is already connected to another HilalMarkets user.",
            )
        identity = IdentityInput(
            provider=IdentityProvider.DISCORD,
            provider_subject=profile.discord_user_id,
            email=profile.email,
            display_identifier=profile.username,
            display_name=profile.username,
            verified=True,
            profile_data={
                "avatar_url": profile.avatar_url,
                "discriminator": profile.discriminator,
            },
        )
        try:
            await IdentityService(self.session).link_to_user(
                user, identity, trusted_provider_assertion=True
            )
        except IdentityConflictError as exc:
            raise DiscordError("identity_conflict", str(exc)) from exc
        connection = existing or DiscordConnection(
            user_id=user.id,
            discord_user_id=profile.discord_user_id,
        )
        connection.status = ConnectionStatus.ACTIVE
        connection.oauth_scopes = profile.scopes or oauth_state.scopes
        connection.connected_at = datetime.now(UTC)
        connection.last_error_code = None
        self.session.add(connection)
        oauth_state.consumed_at = datetime.now(UTC)
        await self.session.flush()
        return connection

    async def register_guild_installation(
        self,
        *,
        installed_by_user_id: UUID,
        guild_id: str,
        guild_name: str | None,
        permissions: DiscordPermissionSet,
    ) -> DiscordGuildInstallation:
        missing = permissions.missing_for_alerts(threaded=True)
        installation = await self.session.scalar(
            select(DiscordGuildInstallation).where(DiscordGuildInstallation.guild_id == guild_id)
        )
        if installation is None:
            installation = DiscordGuildInstallation(
                guild_id=guild_id,
                installed_by_user_id=installed_by_user_id,
                installed_at=datetime.now(UTC),
            )
            self.session.add(installation)
        installation.guild_name = guild_name
        installation.status = "active" if not missing else "needs_permissions"
        installation.bot_permissions = asdict(permissions)
        installation.alerts_enabled = not missing
        installation.last_permission_check_at = datetime.now(UTC)
        installation.last_error_code = "missing_permissions" if missing else None
        await self.session.flush()
        return installation

    async def select_destination(
        self,
        *,
        user_id: UUID,
        mode: str,
        permissions: DiscordPermissionSet,
        discord_user_id: str | None = None,
        guild_id: str | None = None,
        channel_id: str | None = None,
        thread_policy: str = "per_setup",
        send_test: bool = True,
    ) -> DiscordDeliveryDestination:
        try:
            await EntitlementService(self.session).require_feature(user_id, "discord")
        except EntitlementError as exc:
            raise DiscordError(exc.code, str(exc)) from exc
        threaded = mode == "server_channel" and thread_policy == "per_setup"
        missing = permissions.missing_for_alerts(threaded=threaded)
        if missing:
            raise DiscordError(
                "missing_permissions",
                "Discord destination is missing: " + ", ".join(missing),
            )
        installation_id = None
        if mode == "server_channel":
            if not guild_id or not channel_id:
                raise DiscordError("destination_missing", "Guild and channel are required.")
            installation = await self.session.scalar(
                select(DiscordGuildInstallation).where(
                    DiscordGuildInstallation.guild_id == guild_id
                )
            )
            if installation is None or installation.status not in {"active", "needs_permissions"}:
                raise DiscordError("bot_not_installed", "Install the bot in this server first.")
            installation_id = installation.id
        elif mode == "dm":
            if not discord_user_id:
                connection = await self.session.scalar(
                    select(DiscordConnection).where(
                        DiscordConnection.user_id == user_id,
                        DiscordConnection.status == ConnectionStatus.ACTIVE,
                    )
                )
                if connection is None:
                    raise DiscordError("discord_not_connected", "Connect Discord first.")
                discord_user_id = connection.discord_user_id
        else:
            raise DiscordError("unsupported_destination", "Unsupported Discord destination mode.")
        destination = await self.session.scalar(
            select(DiscordDeliveryDestination).where(
                DiscordDeliveryDestination.user_id == user_id,
                DiscordDeliveryDestination.mode == mode,
                DiscordDeliveryDestination.guild_id == guild_id,
                DiscordDeliveryDestination.channel_id == channel_id,
            )
        )
        if destination is None:
            destination = DiscordDeliveryDestination(
                user_id=user_id,
                mode=mode,
                guild_id=guild_id,
                channel_id=channel_id,
            )
            self.session.add(destination)
        destination.guild_installation_id = installation_id
        destination.discord_user_id = discord_user_id
        destination.thread_policy = thread_policy
        destination.permissions_status = "ok"
        destination.status = "active"
        if send_test:
            try:
                result = await self.gateway.send_test(destination=destination)
                destination.test_status = "sent"
                destination.metadata_json = {
                    **(destination.metadata_json or {}),
                    "test_message_id": result.provider_message_id,
                }
                await self._set_integration_health("healthy", destination, None)
            except Exception as exc:
                destination.test_status = "failed"
                await self._set_integration_health("degraded", destination, exc.__class__.__name__)
                raise DiscordError(
                    "test_delivery_failed",
                    "Discord test notification failed; check bot permissions.",
                ) from exc
        await self.session.flush()
        return destination

    async def _set_integration_health(
        self,
        status: str,
        destination: DiscordDeliveryDestination,
        error_code: str | None,
    ) -> None:
        health_status = HealthStatus(status)
        scope = destination_key(destination)
        health = await self.session.scalar(
            select(IntegrationHealth).where(
                IntegrationHealth.integration == "discord",
                IntegrationHealth.scope_key == scope,
            )
        )
        if health is None:
            health = IntegrationHealth(
                integration="discord",
                scope_key=scope,
            )
            self.session.add(health)
        health.status = health_status
        health.consecutive_failures = 0 if health_status == HealthStatus.HEALTHY else 1
        health.last_success_at = (
            datetime.now(UTC) if health_status == HealthStatus.HEALTHY else None
        )
        health.last_failure_at = (
            datetime.now(UTC) if health_status != HealthStatus.HEALTHY else None
        )
        health.last_error_code = error_code
        health.checked_at = datetime.now(UTC)


class DiscordAlertService:
    def __init__(
        self,
        session: AsyncSession,
        gateway: DiscordGateway | None = None,
        *,
        settings: Settings | None = None,
    ):
        self.session = session
        self.settings = settings
        self.gateway = configured_discord_gateway(gateway, settings)

    def render_confirmed_setup_embed(self, alert: Alert) -> DiscordEmbed:
        presentation = AlertPresentation.from_alert(
            alert,
            public_base_url=str(self.settings.public_base_url) if self.settings else None,
        )
        score = (
            f"{presentation.setup_score:.0f}%" if presentation.setup_score is not None else "n/a"
        )
        fields = [
            DiscordField("Strategy", presentation.strategy),
            DiscordField("Strategy version", presentation.strategy_version or "n/a"),
            DiscordField("Exchange", presentation.exchange),
            DiscordField("Timeframe", presentation.timeframe),
            DiscordField("Required completion", score),
            DiscordField("Setup age", presentation.setup_age),
            DiscordField("Data freshness", presentation.data_freshness),
            DiscordField(
                "Alert Trust",
                (
                    f"{presentation.trust_grade} ({presentation.trust_score:.0f}%)"
                    if presentation.trust_score is not None
                    else presentation.trust_grade
                ),
            ),
        ]
        if presentation.sharia_status:
            fields.extend(
                [
                    DiscordField(
                        "Screening status at evaluation",
                        presentation.sharia_status.replace("_", " ").title(),
                    ),
                    DiscordField(
                        "Screening methodology",
                        presentation.sharia_methodology or "Recorded in proof",
                    ),
                    DiscordField(
                        "Screening review",
                        presentation.sharia_reviewed_at or "Not recorded",
                    ),
                    DiscordField(
                        "Evidence Passport",
                        presentation.sharia_passport_url or "Available in dashboard proof",
                    ),
                ]
            )
        if presentation.has_trade_context:
            fields.extend(
                [
                    DiscordField("User-defined trade context", presentation.entry_zone),
                    DiscordField("User-defined stop", presentation.stop),
                    DiscordField("Stop distance", presentation.stop_distance),
                    DiscordField(
                        "Target 1",
                        presentation.targets[0] if presentation.targets else "n/a",
                    ),
                    DiscordField(
                        "Target 2",
                        presentation.targets[1] if len(presentation.targets) > 1 else "n/a",
                    ),
                    DiscordField("User-defined R:R", presentation.reward_to_risk),
                ]
            )
        passed_count = len(presentation.passed_conditions)
        total_count = passed_count + len(presentation.missing_conditions)
        description = (
            f"Research match confirmed: {passed_count}/{total_count} condition checks passed. "
            "HilalMarkets does not execute trades."
        )
        actions = [
            DiscordAction(
                action.label,
                action.action_id,
                "link" if action.url else "secondary",
                action.url,
            )
            for action in presentation.actions
        ]
        title = f"{presentation.symbol} - Research Match Confirmed"
        return DiscordEmbed(
            title=title,
            description=description,
            fields=fields,
            image_url=presentation.chart_reference,
            footer=f"Lifecycle: {presentation.lifecycle_state}",
            timestamp=alert.candle_timestamp,
            actions=actions,
            metadata={"alert_id": str(alert.id), "proof": alert.proof_receipt},
        )

    def render_near_miss_embed(self, snapshot: dict) -> DiscordEmbed:
        missing = snapshot.get("missing_conditions", [])
        passed = snapshot.get("passed_conditions", snapshot.get("passed_condition_keys", []))
        description = (
            f"Completion score: {snapshot.get('completion_score')}%\n"
            f"Score movement: {snapshot.get('trend', 'unknown')}"
        )
        fields = [
            DiscordField("Passed rules", "\n".join(map(str, passed)) or "None", inline=False),
            DiscordField(
                "Missing rules",
                "\n".join(str(item.get("label", item)) for item in missing) or "None",
                inline=False,
            ),
            DiscordField(
                "Closest missing threshold",
                str(snapshot.get("closest_missing_threshold", "n/a")),
                inline=False,
            ),
            DiscordField("Next evaluation", str(snapshot.get("next_evaluation", "n/a"))),
        ]
        return DiscordEmbed(
            title=f"{snapshot.get('symbol', 'Market')} - Near-Miss",
            description=description,
            fields=fields,
            image_url=snapshot.get("chart_reference"),
            footer="Near-Miss Radar",
            actions=[
                DiscordAction("View Proof", f"proof:{snapshot.get('scan_result_id', '')}"),
                DiscordAction(
                    "Open Chart",
                    f"chart:{snapshot.get('symbol', '')}",
                    "link",
                    "/charts",
                ),
                DiscordAction("Mute Near-Miss", f"mute_near_miss:{snapshot.get('symbol', '')}"),
            ],
            metadata=snapshot,
        )

    async def deliver_alert(
        self,
        *,
        alert: Alert,
        destination: DiscordDeliveryDestination,
    ) -> AlertDelivery:
        existing = await self.session.scalar(
            select(AlertDelivery).where(
                AlertDelivery.alert_id == alert.id,
                AlertDelivery.channel == DeliveryChannel.DISCORD,
                AlertDelivery.destination_key == destination_key(destination),
            )
        )
        if existing and existing.status in {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}:
            return existing
        delivery = existing or AlertDelivery(
            alert_id=alert.id,
            channel=DeliveryChannel.DISCORD,
            destination_key=destination_key(destination),
            status=DeliveryStatus.PENDING,
        )
        self.session.add(delivery)
        embed = self.render_confirmed_setup_embed(alert)
        try:
            thread_id = await self._thread_id_for_alert(alert=alert, destination=destination)
            result = await self.gateway.send_embed(
                destination=destination,
                embed=embed,
                thread_id=thread_id,
            )
            delivery.status = DeliveryStatus.SENT
            delivery.provider_message_id = result.provider_message_id
            delivery.attempt_count += 1
            delivery.last_attempt_at = datetime.now(UTC)
            delivery.delivered_at = datetime.now(UTC)
            delivery.next_retry_at = None
            delivery.last_error_code = None
            delivery.last_error_detail = None
            if thread_id:
                await self._record_thread_message(
                    alert=alert,
                    destination=destination,
                    thread_id=thread_id,
                    message_id=result.provider_message_id,
                )
            if self.settings is not None:
                await TrialLifecycleService(self.session, self.settings).record_successful_delivery(
                    delivery
                )
            if alert.setup_instance_id is not None:
                readiness = await self.session.scalar(
                    select(CandidateReadinessSnapshot).where(
                        CandidateReadinessSnapshot.setup_instance_id == alert.setup_instance_id
                    )
                )
                if readiness is not None:
                    readiness.notification_status = "delivered"
        except Exception as exc:
            delivery.attempt_count += 1
            permanent = delivery.attempt_count >= 5
            delivery.status = (
                DeliveryStatus.FAILED_PERMANENT if permanent else DeliveryStatus.FAILED_RETRYABLE
            )
            delivery.last_attempt_at = datetime.now(UTC)
            delivery.next_retry_at = (
                None
                if permanent
                else datetime.now(UTC) + timedelta(minutes=min(60, 5 * delivery.attempt_count))
            )
            delivery.last_error_code = exc.__class__.__name__
            delivery.last_error_detail = "Discord delivery failed; retry scheduled."
            if alert.setup_instance_id is not None:
                readiness = await self.session.scalar(
                    select(CandidateReadinessSnapshot).where(
                        CandidateReadinessSnapshot.setup_instance_id == alert.setup_instance_id
                    )
                )
                if readiness is not None:
                    readiness.notification_status = "failed"
        await self.session.flush()
        return delivery

    async def _thread_id_for_alert(
        self,
        *,
        alert: Alert,
        destination: DiscordDeliveryDestination,
    ) -> str | None:
        if destination.mode != "server_channel" or destination.thread_policy != "per_setup":
            return None
        if alert.setup_instance_id is None:
            return None
        setup = await self.session.get(SetupInstance, alert.setup_instance_id)
        if setup is None:
            return None
        existing = await self.session.scalar(
            select(DiscordSetupThread).where(
                DiscordSetupThread.destination_id == destination.id,
                DiscordSetupThread.setup_key == setup.setup_key,
            )
        )
        if existing:
            return existing.thread_id
        thread_id = await self.gateway.create_thread(
            destination=destination,
            name=f"{setup.symbol} {setup.state.value}",
        )
        thread = DiscordSetupThread(
            destination_id=destination.id,
            setup_instance_id=setup.id,
            strategy_version_id=setup.strategy_version_id,
            setup_key=setup.setup_key,
            thread_id=thread_id,
            status="active",
            last_message_at=datetime.now(UTC),
        )
        self.session.add(thread)
        await self.session.flush()
        return thread_id

    async def _record_thread_message(
        self,
        *,
        alert: Alert,
        destination: DiscordDeliveryDestination,
        thread_id: str,
        message_id: str,
    ) -> None:
        if alert.setup_instance_id is None:
            return
        setup = await self.session.get(SetupInstance, alert.setup_instance_id)
        if setup is None:
            return
        thread = await self.session.scalar(
            select(DiscordSetupThread).where(
                DiscordSetupThread.destination_id == destination.id,
                DiscordSetupThread.setup_key == setup.setup_key,
            )
        )
        if thread:
            thread.last_message_id = message_id
            thread.last_message_at = datetime.now(UTC)

    async def retry_due_deliveries(self, *, limit: int = 50) -> list[AlertDelivery]:
        deliveries = (
            await self.session.scalars(
                select(AlertDelivery)
                .where(
                    AlertDelivery.channel == DeliveryChannel.DISCORD,
                    (
                        (AlertDelivery.status == DeliveryStatus.PENDING)
                        | (
                            (AlertDelivery.status == DeliveryStatus.FAILED_RETRYABLE)
                            & (AlertDelivery.next_retry_at <= datetime.now(UTC))
                        )
                    ),
                )
                .limit(limit)
            )
        ).all()
        retried: list[AlertDelivery] = []
        for delivery in deliveries:
            alert = await self.session.get(Alert, delivery.alert_id)
            destination = await find_destination_by_key(self.session, delivery.destination_key)
            if alert is None or destination is None:
                continue
            retried.append(await self.deliver_alert(alert=alert, destination=destination))
        return retried

    @staticmethod
    def _format_range(value) -> str:
        if isinstance(value, dict):
            low = value.get("low")
            high = value.get("high")
            return f"{low} - {high}"
        return str(value or "n/a")

    @staticmethod
    def _target_at(proof: dict, index: int) -> str:
        targets = proof.get("target_levels") or []
        if index >= len(targets):
            return "n/a"
        target = targets[index]
        if isinstance(target, dict):
            return str(target.get("price") or target.get("value") or target)
        return str(target)

    @staticmethod
    def _proof_value(proof: dict, key: str):
        market = proof.get("market")
        if isinstance(market, dict):
            return market.get(key)
        return None


class DiscordSlashCommandService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def handle(self, context: DiscordCommandContext) -> DiscordCommandResponse:
        command = context.command_name.lstrip("/").replace("_", "-")
        if command in {"dashboard", "monitor-create", "create-monitor"}:
            target = "/dashboard" if command == "dashboard" else "/dashboard/strategies/new"
            url = await self._dashboard_link(context, target)
            template_names = ", ".join(
                template.label for template in list(BUILTIN_STRATEGY_TEMPLATES.values())[:5]
            )
            message = "Open the secure Dashboard link. It expires shortly."
            if command in {"monitor-create", "create-monitor"}:
                message = (
                    "Open the secure Strategy Builder link. Templates include "
                    f"{template_names}, and more."
                )
            return DiscordCommandResponse(
                message,
                actions=[DiscordAction("Open Dashboard", "open_dashboard", "link", url)],
            )
        if command in {"templates", "strategy-templates"}:
            lines = [
                f"- {template.label}: {template.description}"
                for template in BUILTIN_STRATEGY_TEMPLATES.values()
            ]
            url = await self._dashboard_link(context, "/dashboard/strategies/new")
            return DiscordCommandResponse(
                "Built-in strategy templates:\n" + "\n".join(lines),
                actions=[DiscordAction("Open Strategy Builder", "open_builder", "link", url)],
            )
        if command in {"monitor-list", "monitors"}:
            strategies = (
                await self.session.scalars(
                    select(Strategy).where(Strategy.user_id == context.user_id).limit(10)
                )
            ).all()
            lines = [f"- {strategy.name}: {strategy.status.value}" for strategy in strategies]
            return DiscordCommandResponse("\n".join(lines) or "No monitors yet.")
        if command == "subscription":
            entitlement = await EntitlementService(self.session).current(context.user_id)
            url = await self._dashboard_link(context, "/dashboard/billing")
            return DiscordCommandResponse(
                f"Current plan: {entitlement.plan.name}. Source: {entitlement.source}. "
                "Billing and upgrades continue in Dashboard.",
                actions=[DiscordAction("Open Billing", "open_billing", "link", url)],
            )
        if command in {"quick-scan", "scan-now"}:
            prompt = str(context.options.get("prompt") or "").strip()
            target = "/dashboard/scan-now"
            url = await self._dashboard_link(context, target)
            message = (
                "Open Quick Scan in Dashboard to review the interpreted mechanics and run the "
                "lightweight market scan."
            )
            if prompt:
                message = f"Quick Scan prompt received: {prompt[:120]}\n\n{message}"
            return DiscordCommandResponse(
                message,
                actions=[DiscordAction("Open Quick Scan", "open_quick_scan", "link", url)],
            )
        if command == "monitor-status":
            strategy_id = context.options.get("strategy_id")
            if strategy_id:
                try:
                    strategy = await self.session.get(Strategy, UUID(str(strategy_id)))
                except ValueError:
                    strategy = None
                if strategy is None or strategy.user_id != context.user_id:
                    return DiscordCommandResponse("Monitor not found.")
                return DiscordCommandResponse(f"{strategy.name}: {strategy.status.value}.")
            url = await self._dashboard_link(context, "/dashboard/monitors")
            return DiscordCommandResponse(
                "Open Dashboard to choose a monitor.",
                actions=[DiscordAction("Open Monitors", "open_monitors", "link", url)],
            )
        if command in {"monitor-pause", "monitor-resume"}:
            strategy_id = context.options.get("strategy_id")
            if not strategy_id:
                return DiscordCommandResponse("Provide strategy_id.")
            try:
                strategy_uuid = UUID(str(strategy_id))
            except ValueError:
                return DiscordCommandResponse("Monitor not found.")
            try:
                if command == "monitor-pause":
                    strategy = await MonitorOperationService(self.session).pause(
                        user_id=context.user_id,
                        strategy_id=strategy_uuid,
                        actor_type="discord_user",
                    )
                    action = "paused"
                else:
                    strategy = await MonitorOperationService(self.session).resume(
                        user_id=context.user_id,
                        strategy_id=strategy_uuid,
                        actor_type="discord_user",
                    )
                    action = "resumed"
            except MonitorOperationError:
                return DiscordCommandResponse("Monitor not found.")
            return DiscordCommandResponse(f"{strategy.name} {action}.")
        if command == "near-miss":
            url = await self._dashboard_link(context, "/dashboard/lifecycles")
            return DiscordCommandResponse(
                "Near-Miss browsing has moved into Lifecycles. Open Lifecycles to see "
                "active setup cards, completion scores, missing conditions, and chart evidence.",
                actions=[DiscordAction("Open Lifecycles", "open_lifecycles", "link", url)],
            )
        if command == "latest-setups":
            setups = (
                await self.session.scalars(
                    select(SetupInstance)
                    .join(
                        StrategyVersion,
                        StrategyVersion.id == SetupInstance.strategy_version_id,
                    )
                    .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
                    .where(Strategy.user_id == context.user_id)
                    .order_by(SetupInstance.last_evaluated_at.desc())
                    .limit(10)
                )
            ).all()
            lines = [
                f"{setup.symbol} - {setup.state.value} ({float(setup.completion_score):.0f}%)"
                for setup in setups
            ]
            return DiscordCommandResponse("\n".join(lines) or "No setup instances yet.")
        if command in {"why-no-alert", "setup-replay"}:
            url = await self._dashboard_link(
                context,
                "/dashboard/lifecycles",
            )
            return DiscordCommandResponse(
                "Setup Replay is hidden. Open Lifecycles for deterministic setup evidence, "
                "missing conditions, lifecycle state, and chart context.",
                actions=[DiscordAction("Open Lifecycles", "open_lifecycles", "link", url)],
            )
        if command == "support":
            category = str(context.options.get("category", "technical_issue"))
            description = str(
                context.options.get("description", "Created from Discord slash command.")
            )
            ticket = await DiscordSupportService(self.session).create_ticket(
                user_id=context.user_id,
                category=category,
                description=description,
            )
            return DiscordCommandResponse(f"Support request created: {ticket.id}.")
        if command == "connect-telegram":
            username = self.settings.telegram_bot_username
            if not username:
                return DiscordCommandResponse("Telegram connection is not configured.")
            return DiscordCommandResponse(
                f"Open https://t.me/{username}?start=src_discord to connect Telegram."
            )
        if command == "settings":
            return await self._settings_response(context)
        if command in {"alerts", "proof"}:
            paths = {
                "alerts": "/dashboard/lifecycles",
                "proof": "/dashboard/lifecycles",
            }
            url = await self._dashboard_link(context, paths[command])
            return DiscordCommandResponse(
                "The Dashboard Alerts & Proof section was removed. Use Lifecycles "
                "for setup history and deterministic proof context.",
                actions=[DiscordAction("Open Dashboard", "open_dashboard", "link", url)],
            )
        return DiscordCommandResponse(
            "Commands: /create_monitor, /monitors, /quick_scan, "
            "/latest_setups, /alerts, /proof, /subscription, "
            "/templates, /settings, /support, /dashboard"
        )

    async def _settings_response(self, context: DiscordCommandContext) -> DiscordCommandResponse:
        preference = await self.session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == context.user_id)
        )
        if preference is None:
            preference = DashboardPreference(user_id=context.user_id, theme="dark")
            self.session.add(preference)
            await self.session.flush()
        prefs = dict(preference.notification_preferences or {})
        changed: list[str] = []
        options = context.options or {}
        theme = str(options.get("theme") or "").strip().lower()
        if theme:
            if theme not in DISCORD_THEMES:
                return DiscordCommandResponse("Theme must be dark, light, or system.")
            preference.theme = theme
            prefs["theme"] = theme
            changed.append(f"theme={theme}")
        timezone = str(options.get("timezone") or "").strip()
        if timezone:
            preference.default_timezone = timezone
            prefs["timezone"] = timezone
            changed.append(f"timezone={timezone}")
        near_miss = options.get("near_miss_enabled")
        if near_miss is not None:
            enabled = str(near_miss).lower() in {"true", "1", "yes", "on"}
            prefs["near_miss_enabled"] = enabled
            changed.append(f"near_miss={'on' if enabled else 'off'}")
        if "near_miss_threshold" in options:
            threshold = max(1, min(100, int(options["near_miss_threshold"])))
            prefs["near_miss_threshold"] = threshold
            changed.append(f"near_miss_threshold={threshold}")
        if "maximum_alerts_per_hour" in options:
            maximum = max(1, min(1000, int(options["maximum_alerts_per_hour"])))
            prefs["maximum_alerts_per_hour"] = maximum
            changed.append(f"max_alerts_per_hour={maximum}")
        if "alert_days" in options:
            raw_days = str(options["alert_days"]).replace(",", " ").split()
            days = [day for day in raw_days if day in DISCORD_ALERT_DAYS]
            prefs["alert_days"] = ["Every Day"] if "Every" in raw_days or not days else days
            changed.append(f"days={','.join(prefs['alert_days'])}")
        if "alert_hours" in options:
            raw_hours = str(options["alert_hours"]).replace(",", " ").split()
            hours = [hour for hour in raw_hours if hour in DISCORD_ALERT_HOURS]
            prefs["alert_hours"] = hours
            changed.append(f"hours={','.join(hours) if hours else 'any'}")
        preference.notification_preferences = prefs
        await self.session.commit()
        dashboard_url = await self._dashboard_link(context, "/dashboard/settings")
        summary = "Settings updated: " + "; ".join(changed) if changed else "Current settings"
        lines = [
            summary,
            f"Theme: {preference.theme}",
            f"Timezone: {prefs.get('timezone', preference.default_timezone)}",
            f"Near-Miss alerts: {'on' if prefs.get('near_miss_enabled') else 'off'}",
            f"Near-Miss threshold: {prefs.get('near_miss_threshold', 70)}%",
            f"Max alerts/hour: {prefs.get('maximum_alerts_per_hour', 50)}",
            f"Days: {', '.join(prefs.get('alert_days', ['Every Day']))}",
            f"Hours: {', '.join(prefs.get('alert_hours', [])) or 'Any hour'}",
        ]
        return DiscordCommandResponse(
            "\n".join(lines),
            actions=[DiscordAction("Open Settings", "open_settings", "link", dashboard_url)],
        )

    async def _dashboard_link(self, context: DiscordCommandContext, target_path: str) -> str:
        url = await DashboardLinkService(self.session, self.settings).create(
            user_id=context.user_id,
            source_platform=Platform.DISCORD,
            source_subject=context.discord_user_id,
            target_path=target_path,
        )
        await self.session.commit()
        return url


class DiscordRoleSyncService:
    def __init__(
        self,
        session: AsyncSession,
        gateway: DiscordGateway | None = None,
        *,
        settings: Settings | None = None,
    ):
        self.session = session
        self.gateway = configured_discord_gateway(gateway, settings)

    async def enqueue_for_user(
        self,
        *,
        user_id: UUID,
        source_event_id: str | None = None,
        current_role_ids: set[str] | None = None,
    ) -> list[DiscordRoleSyncJob]:
        entitlement = await EntitlementService(self.session).current(user_id)
        connection = await self.session.scalar(
            select(DiscordConnection).where(
                DiscordConnection.user_id == user_id,
                DiscordConnection.status == ConnectionStatus.ACTIVE,
            )
        )
        if connection is None:
            return []
        mappings = (
            await self.session.scalars(
                select(DiscordRoleMapping).where(DiscordRoleMapping.is_active.is_(True))
            )
        ).all()
        jobs: list[DiscordRoleSyncJob] = []
        current_roles = current_role_ids or set()
        for mapping in mappings:
            should_have = mapping.plan_code == entitlement.plan.code or (
                mapping.plan_code is None and entitlement.feature_enabled(mapping.entitlement_key)
            )
            action = "add" if should_have else "remove"
            if action == "remove" and mapping.role_id not in current_roles:
                continue
            key = f"{user_id}:{mapping.guild_id}:{mapping.role_id}:{action}:{source_event_id}"
            existing = await self.session.scalar(
                select(DiscordRoleSyncJob).where(DiscordRoleSyncJob.idempotency_key == key)
            )
            if existing:
                jobs.append(existing)
                continue
            job = DiscordRoleSyncJob(
                user_id=user_id,
                guild_id=mapping.guild_id,
                role_id=mapping.role_id,
                action=action,
                source_event_id=source_event_id,
                idempotency_key=key,
                status="pending",
                created_at=datetime.now(UTC),
            )
            self.session.add(job)
            jobs.append(job)
        await self.session.flush()
        return jobs

    async def process_due(self, *, limit: int = 100) -> list[DiscordRoleSyncJob]:
        jobs = (
            await self.session.scalars(
                select(DiscordRoleSyncJob)
                .where(
                    DiscordRoleSyncJob.status.in_(["pending", "failed"]),
                    (DiscordRoleSyncJob.next_retry_at.is_(None))
                    | (DiscordRoleSyncJob.next_retry_at <= datetime.now(UTC)),
                )
                .limit(limit)
            )
        ).all()
        processed: list[DiscordRoleSyncJob] = []
        for job in jobs:
            connection = await self.session.scalar(
                select(DiscordConnection).where(DiscordConnection.user_id == job.user_id)
            )
            if connection is None:
                job.status = "failed"
                job.last_error_code = "discord_not_connected"
                continue
            try:
                await self.gateway.sync_role(
                    discord_user_id=connection.discord_user_id,
                    guild_id=job.guild_id,
                    role_id=job.role_id,
                    action=job.action,
                )
                job.status = "processed"
                job.processed_at = datetime.now(UTC)
                job.last_error_code = None
            except Exception as exc:
                job.status = "failed"
                job.attempt_count += 1
                job.next_retry_at = datetime.now(UTC) + timedelta(
                    minutes=min(120, 10 * job.attempt_count)
                )
                job.last_error_code = exc.__class__.__name__
            processed.append(job)
        await self.session.flush()
        return processed


class DiscordSupportService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_ticket(
        self,
        *,
        user_id: UUID,
        category: str,
        description: str,
        strategy_id: UUID | None = None,
        setup_instance_id: UUID | None = None,
        alert_id: UUID | None = None,
    ) -> SupportRequest:
        context = await self._diagnostic_context(
            user_id=user_id,
            strategy_id=strategy_id,
            setup_instance_id=setup_instance_id,
            alert_id=alert_id,
        )
        ticket = SupportRequest(
            user_id=user_id,
            category=category,
            priority="high" if category in {"billing", "missing_alert", "bug_report"} else "normal",
            status=SupportRequestStatus.OPEN,
            subject=f"Discord support: {category.replace('_', ' ')}",
            description=description,
            context=context,
        )
        self.session.add(ticket)
        await self.session.flush()
        return ticket

    async def _diagnostic_context(
        self,
        *,
        user_id: UUID,
        strategy_id: UUID | None,
        setup_instance_id: UUID | None,
        alert_id: UUID | None,
    ) -> dict:
        entitlement = await EntitlementService(self.session).current(user_id)
        telegram = await self.session.scalar(
            select(TelegramConnection).where(TelegramConnection.user_id == user_id)
        )
        discord = await self.session.scalar(
            select(DiscordConnection).where(DiscordConnection.user_id == user_id)
        )
        deliveries = []
        if alert_id:
            deliveries = [
                {
                    "channel": delivery.channel.value,
                    "status": delivery.status.value,
                    "attempt_count": delivery.attempt_count,
                    "last_error_code": delivery.last_error_code,
                }
                for delivery in (
                    await self.session.scalars(
                        select(AlertDelivery).where(AlertDelivery.alert_id == alert_id)
                    )
                ).all()
            ]
        return {
            "plan": entitlement.plan.code,
            "telegram_connection": bool(telegram),
            "discord_connection": bool(discord),
            "strategy_id": str(strategy_id) if strategy_id else None,
            "setup_instance_id": str(setup_instance_id) if setup_instance_id else None,
            "alert_id": str(alert_id) if alert_id else None,
            "delivery_logs": deliveries,
        }


class DiscordModerationService:
    GUARANTEE_TERMS = ("guaranteed profit", "guaranteed profits", "risk-free", "100% win")
    SECRET_TERMS = ("seed phrase", "private key", "withdrawal permission", "remote desktop")
    SCAM_TERMS = ("airdrop claim", "connect wallet now", "double your money")
    MALICIOUS_EXTENSIONS = (".exe", ".scr", ".bat", ".cmd", ".js")

    def assess(
        self, *, content: str, attachment_names: list[str] | None = None
    ) -> ModerationResult:
        lowered = content.casefold()
        findings: list[ModerationFinding] = []
        if any(term in lowered for term in self.GUARANTEE_TERMS):
            findings.append(
                ModerationFinding(
                    "guaranteed_profit_claim",
                    "high",
                    "Guaranteed-profit claims are not allowed.",
                )
            )
        if any(term in lowered for term in self.SECRET_TERMS):
            findings.append(
                ModerationFinding(
                    "unsafe_support_request",
                    "critical",
                    "Official support never asks for seed phrases, private keys, "
                    "withdrawal permissions, or remote desktop access.",
                )
            )
        if any(term in lowered for term in self.SCAM_TERMS):
            findings.append(ModerationFinding("scam_pattern", "high", "Possible scam language."))
        if "official support" in lowered and "ai market monitor" not in lowered:
            findings.append(
                ModerationFinding(
                    "support_impersonation",
                    "high",
                    "Possible fake support or impersonation attempt.",
                )
            )
        for name in attachment_names or []:
            if name.casefold().endswith(self.MALICIOUS_EXTENSIONS):
                findings.append(
                    ModerationFinding(
                        "malicious_file_type",
                        "critical",
                        "Executable or script attachments are blocked.",
                    )
                )
        return ModerationResult(allowed=not findings, findings=findings)

    @staticmethod
    def official_support_notice() -> str:
        return (
            "Official HilalMarkets support will never ask for seed phrases, "
            "wallet private keys, "
            "exchange withdrawal permissions, or remote desktop access."
        )


def destination_key(destination: DiscordDeliveryDestination) -> str:
    if destination.mode == "dm":
        return f"dm:{destination.discord_user_id}"
    return f"guild:{destination.guild_id}:channel:{destination.channel_id}"


async def find_destination_by_key(
    session: AsyncSession, key: str
) -> DiscordDeliveryDestination | None:
    if key.startswith("dm:"):
        discord_user_id = key.removeprefix("dm:")
        return await session.scalar(
            select(DiscordDeliveryDestination).where(
                DiscordDeliveryDestination.mode == "dm",
                DiscordDeliveryDestination.discord_user_id == discord_user_id,
            )
        )
    if key.startswith("guild:"):
        _, guild_id, _, channel_id = key.split(":", 3)
        return await session.scalar(
            select(DiscordDeliveryDestination).where(
                DiscordDeliveryDestination.mode == "server_channel",
                DiscordDeliveryDestination.guild_id == guild_id,
                DiscordDeliveryDestination.channel_id == channel_id,
            )
        )
    return None
