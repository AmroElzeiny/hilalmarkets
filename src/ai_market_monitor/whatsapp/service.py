from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.dashboard_paths import (
    COMPLIANCE_CHANGES_PATH,
    LIFECYCLES_PATH,
    MARKET_PATH,
    MONITOR_PATH,
    SETTINGS_PATH,
    SUBSCRIPTION_PATH,
    SUPPORT_PATH,
)
from ai_market_monitor.core.platforms import Platform
from ai_market_monitor.core.security import opaque_token, token_digest
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    AuditEvent,
    CandidateReadinessSnapshot,
    IdentityLinkToken,
    IntegrationHealth,
    IntegrationTestResult,
    Strategy,
    User,
    UserIdentity,
    WhatsAppConnection,
    WhatsAppConversationState,
    WhatsAppWebhookReceipt,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
    HealthStatus,
    IdentityProvider,
    StrategyStatus,
)
from ai_market_monitor.services.alert_presentation import AlertPresentation
from ai_market_monitor.services.dashboard_links import DashboardLinkService
from ai_market_monitor.services.monitor_operations import (
    MonitorOperationError,
    MonitorOperationService,
)
from ai_market_monitor.services.trials import TrialLifecycleService
from ai_market_monitor.whatsapp.adapter import WhatsAppCloudAdapter, WhatsAppDeliveryError
from ai_market_monitor.whatsapp.rendering import (
    WHATSAPP_OPPORTUNITY_EVENTS,
    WhatsAppAlertRenderer,
    WhatsAppTemplateRegistry,
)
from ai_market_monitor.whatsapp.security import (
    WhatsAppSecurityError,
    mask_e164,
    normalize_e164,
    wa_id_to_e164,
)
from ai_market_monitor.whatsapp.types import (
    WhatsAppInboundButtonReply,
    WhatsAppInboundListReply,
    WhatsAppInboundMessage,
    WhatsAppInboundText,
    WhatsAppInboundUnsupported,
    WhatsAppInteractiveButtons,
    WhatsAppInteractiveList,
    WhatsAppLinkRequest,
    WhatsAppListRow,
    WhatsAppListSection,
    WhatsAppOutboundMessage,
    WhatsAppPreferencesUpdate,
    WhatsAppReplyButton,
    WhatsAppSessionText,
)

OUTBOUND_ADAPTER: TypeAdapter[WhatsAppOutboundMessage] = TypeAdapter(
    WhatsAppOutboundMessage
)
INBOUND_ADAPTER: TypeAdapter[WhatsAppInboundMessage] = TypeAdapter(
    WhatsAppInboundMessage
)
OPT_OUT_WORDS = frozenset({"STOP", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"})


class WhatsAppServiceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class WhatsAppLinkResult:
    url: str
    expires_at: datetime
    masked_phone: str
    categories: tuple[str, ...]


class WhatsAppAccountService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def create_link(
        self,
        *,
        user_id: UUID,
        request: WhatsAppLinkRequest,
        ttl_minutes: int = 15,
    ) -> WhatsAppLinkResult:
        if not self.settings.whatsapp_enabled:
            raise WhatsAppServiceError(
                "whatsapp_disabled", "WhatsApp is not enabled on this Hilal Markets server."
            )
        business_phone = self.settings.whatsapp_business_phone_e164
        if not business_phone:
            raise WhatsAppServiceError(
                "whatsapp_not_configured", "The WhatsApp business number is not configured."
            )
        user = await self.session.get(User, user_id)
        if user is None:
            raise WhatsAppServiceError("user_missing", "The dashboard user was not found.")
        phone = normalize_e164(request.phone_e164)
        assigned = await self.session.scalar(
            select(WhatsAppConnection).where(WhatsAppConnection.phone_e164 == phone)
        )
        if assigned is not None and assigned.user_id != user_id:
            raise WhatsAppServiceError(
                "whatsapp_phone_assigned",
                "This WhatsApp number is already assigned to another account.",
            )
        now = datetime.now(UTC)
        pending = (
            await self.session.scalars(
                select(IdentityLinkToken).where(
                    IdentityLinkToken.user_id == user_id,
                    IdentityLinkToken.target_channel == DeliveryChannel.WHATSAPP.value,
                    IdentityLinkToken.consumed_at.is_(None),
                    IdentityLinkToken.canceled_at.is_(None),
                )
            )
        ).all()
        for token in pending:
            token.canceled_at = now
        raw = opaque_token()
        expires_at = now + timedelta(minutes=max(5, min(30, ttl_minutes)))
        link = IdentityLinkToken(
            user_id=user_id,
            onboarding_session_id=None,
            token_digest=token_digest(raw),
            target_channel=DeliveryChannel.WHATSAPP.value,
            expires_at=expires_at,
            metadata_json={
                "expected_phone_e164": phone,
                "categories": request.categories,
                "locale": request.locale,
                "consent": True,
                "consent_version": self.settings.whatsapp_opt_in_version,
                "consent_source": "dashboard_wa_link",
                "frequency_notice": "Event-driven messages based on saved preferences",
            },
            created_at=now,
        )
        self.session.add(link)
        await self.session.flush()
        self._audit(
            user_id,
            "whatsapp.link_requested",
            "identity_link_token",
            str(link.id),
            {
                "phone": mask_e164(phone),
                "categories": request.categories,
                "opt_in_version": self.settings.whatsapp_opt_in_version,
            },
            actor_type="dashboard_user",
        )
        business_digits = normalize_e164(business_phone).removeprefix("+")
        message = quote(f"LINK {raw}", safe="")
        return WhatsAppLinkResult(
            url=f"https://wa.me/{business_digits}?text={message}",
            expires_at=expires_at,
            masked_phone=mask_e164(phone),
            categories=tuple(request.categories),
        )

    async def complete_link(
        self,
        *,
        raw_token: str,
        wa_id: str,
        profile_name: str | None,
        inbound_at: datetime,
    ) -> WhatsAppConnection:
        if not raw_token or len(raw_token) > 256:
            raise WhatsAppServiceError("whatsapp_link_invalid", "The link token is invalid.")
        link = await self.session.scalar(
            select(IdentityLinkToken)
            .where(IdentityLinkToken.token_digest == token_digest(raw_token))
            .with_for_update()
        )
        now = datetime.now(UTC)
        if link is None or link.target_channel != DeliveryChannel.WHATSAPP.value:
            raise WhatsAppServiceError("whatsapp_link_invalid", "The link token is invalid.")
        if link.consumed_at is not None:
            raise WhatsAppServiceError("whatsapp_link_used", "The link token was already used.")
        if link.canceled_at is not None:
            raise WhatsAppServiceError("whatsapp_link_canceled", "The link token was canceled.")
        if _aware(link.expires_at) <= now:
            raise WhatsAppServiceError("whatsapp_link_expired", "The link token has expired.")
        metadata = link.metadata_json or {}
        try:
            expected_phone = normalize_e164(str(metadata.get("expected_phone_e164") or ""))
            verified_phone = wa_id_to_e164(wa_id)
        except WhatsAppSecurityError as exc:
            raise WhatsAppServiceError(
                "whatsapp_link_invalid", "The WhatsApp link data is invalid."
            ) from exc
        if expected_phone != verified_phone:
            raise WhatsAppServiceError(
                "whatsapp_phone_mismatch",
                "Send the link message from the same WhatsApp number entered in the dashboard.",
            )
        connection = await self.session.scalar(
            select(WhatsAppConnection)
            .where(WhatsAppConnection.user_id == link.user_id)
            .with_for_update()
        )
        wa_owner = await self.session.scalar(
            select(WhatsAppConnection)
            .where(WhatsAppConnection.wa_id == wa_id)
            .with_for_update()
        )
        phone_owner = await self.session.scalar(
            select(WhatsAppConnection)
            .where(WhatsAppConnection.phone_e164 == verified_phone)
            .with_for_update()
        )
        if any(
            owner is not None and owner.user_id != link.user_id
            for owner in (wa_owner, phone_owner)
        ):
            raise WhatsAppServiceError(
                "whatsapp_account_assigned",
                "This WhatsApp account is already assigned to another Hilal Markets user.",
            )
        categories = [str(value) for value in metadata.get("categories", [])]
        locale = str(metadata.get("locale") or self.settings.whatsapp_default_language)
        if connection is None:
            connection = WhatsAppConnection(
                user_id=link.user_id,
                wa_id=wa_id,
                phone_e164=verified_phone,
            )
            self.session.add(connection)
        connection.wa_id = wa_id
        connection.phone_e164 = verified_phone
        connection.profile_name = profile_name
        connection.status = ConnectionStatus.ACTIVE
        connection.alerts_enabled = True
        connection.preferred_locale = locale
        connection.opt_in_categories = categories
        connection.connected_at = connection.connected_at or now
        connection.verified_at = now
        connection.last_inbound_at = inbound_at
        connection.service_window_expires_at = inbound_at + timedelta(hours=24)
        connection.opt_in_at = now
        connection.opt_in_source = str(metadata.get("consent_source") or "dashboard_wa_link")
        connection.opt_in_version = str(
            metadata.get("consent_version") or self.settings.whatsapp_opt_in_version
        )
        connection.opt_out_at = None
        connection.opt_out_reason = None
        connection.paused_at = None
        connection.revoked_at = None
        connection.last_error_code = None
        identity = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == IdentityProvider.WHATSAPP,
                UserIdentity.provider_subject == wa_id,
            )
        )
        if identity is not None and identity.user_id != link.user_id:
            raise WhatsAppServiceError(
                "whatsapp_account_assigned",
                "This WhatsApp account is already assigned to another Hilal Markets user.",
            )
        if identity is None:
            identity = UserIdentity(
                user_id=link.user_id,
                provider=IdentityProvider.WHATSAPP,
                provider_subject=wa_id,
                normalized_identifier=verified_phone,
                display_identifier=mask_e164(verified_phone),
                is_verified=True,
                is_primary=False,
                verified_at=now,
                profile_data={"profile_name": profile_name} if profile_name else {},
            )
            self.session.add(identity)
        else:
            identity.user_id = link.user_id
            identity.normalized_identifier = verified_phone
            identity.display_identifier = mask_e164(verified_phone)
            identity.is_verified = True
            identity.verified_at = now
            identity.profile_data = (
                {"profile_name": profile_name} if profile_name else identity.profile_data
            )
        link.consumed_at = now
        await self.session.flush()
        self._audit(
            link.user_id,
            "whatsapp.connected",
            "whatsapp_connection",
            str(connection.id),
            {
                "phone": mask_e164(verified_phone),
                "categories": categories,
                "opt_in_version": connection.opt_in_version,
                "source_link_id": str(link.id),
            },
            actor_type="whatsapp",
        )
        return connection

    async def pause(
        self, user_id: UUID, *, actor_type: str = "dashboard_user"
    ) -> WhatsAppConnection:
        connection = await self._owned_connection(user_id)
        connection.alerts_enabled = False
        connection.paused_at = datetime.now(UTC)
        await self._cancel_queued(user_id, "whatsapp_paused")
        self._audit(
            user_id,
            "whatsapp.paused",
            "whatsapp_connection",
            str(connection.id),
            {},
            actor_type=actor_type,
        )
        return connection

    async def resume(
        self, user_id: UUID, *, actor_type: str = "dashboard_user"
    ) -> WhatsAppConnection:
        connection = await self._owned_connection(user_id)
        if connection.opt_out_at is not None or connection.revoked_at is not None:
            raise WhatsAppServiceError(
                "whatsapp_reconsent_required",
                "Reconnect from the dashboard to provide fresh WhatsApp consent.",
            )
        connection.status = ConnectionStatus.ACTIVE
        connection.alerts_enabled = True
        connection.paused_at = None
        self._audit(
            user_id,
            "whatsapp.resumed",
            "whatsapp_connection",
            str(connection.id),
            {},
            actor_type=actor_type,
        )
        return connection

    async def opt_out(
        self, connection: WhatsAppConnection, *, reason: str
    ) -> WhatsAppConnection:
        now = datetime.now(UTC)
        connection.alerts_enabled = False
        connection.opt_out_at = now
        connection.opt_out_reason = reason[:160]
        connection.paused_at = now
        await self._cancel_queued(connection.user_id, "whatsapp_opt_out")
        self._audit(
            connection.user_id,
            "whatsapp.opted_out",
            "whatsapp_connection",
            str(connection.id),
            {"reason": reason[:80]},
            actor_type="whatsapp",
        )
        return connection

    async def disconnect(self, user_id: UUID) -> WhatsAppConnection:
        connection = await self._owned_connection(user_id)
        now = datetime.now(UTC)
        connection.status = ConnectionStatus.REVOKED
        connection.alerts_enabled = False
        connection.revoked_at = now
        connection.paused_at = now
        await self._cancel_queued(user_id, "whatsapp_disconnected")
        identities = (
            await self.session.scalars(
                select(UserIdentity).where(
                    UserIdentity.user_id == user_id,
                    UserIdentity.provider == IdentityProvider.WHATSAPP,
                )
            )
        ).all()
        for identity in identities:
            await self.session.delete(identity)
        self._audit(
            user_id,
            "whatsapp.disconnected",
            "whatsapp_connection",
            str(connection.id),
            {},
            actor_type="dashboard_user",
        )
        return connection

    async def update_preferences(
        self, user_id: UUID, update: WhatsAppPreferencesUpdate
    ) -> WhatsAppConnection:
        connection = await self._owned_connection(user_id)
        if update.categories is not None:
            connection.opt_in_categories = update.categories
        if update.locale is not None:
            connection.preferred_locale = update.locale
        if update.alerts_enabled is not None:
            if update.alerts_enabled:
                await self.resume(user_id)
            else:
                await self.pause(user_id)
        self._audit(
            user_id,
            "whatsapp.preferences_updated",
            "whatsapp_connection",
            str(connection.id),
            {
                "categories": connection.opt_in_categories,
                "locale": connection.preferred_locale,
                "alerts_enabled": connection.alerts_enabled,
            },
            actor_type="dashboard_user",
        )
        return connection

    async def clear_error(self, user_id: UUID) -> WhatsAppConnection:
        connection = await self._owned_connection(user_id)
        connection.last_error_code = None
        if connection.verified_at is not None and connection.revoked_at is None:
            connection.status = ConnectionStatus.ACTIVE
        self._audit(
            user_id,
            "whatsapp.error_cleared",
            "whatsapp_connection",
            str(connection.id),
            {},
            actor_type="dashboard_user",
        )
        return connection

    async def _owned_connection(self, user_id: UUID) -> WhatsAppConnection:
        connection = await self.session.scalar(
            select(WhatsAppConnection)
            .where(WhatsAppConnection.user_id == user_id)
            .with_for_update()
        )
        if connection is None:
            raise WhatsAppServiceError(
                "whatsapp_not_connected", "No WhatsApp account is connected."
            )
        return connection

    async def _cancel_queued(self, user_id: UUID, reason: str) -> None:
        deliveries = (
            await self.session.scalars(
                select(AlertDelivery)
                .join(Alert, Alert.id == AlertDelivery.alert_id)
                .where(
                    Alert.user_id == user_id,
                    AlertDelivery.channel == DeliveryChannel.WHATSAPP,
                    AlertDelivery.status.in_(
                        {DeliveryStatus.PENDING, DeliveryStatus.FAILED_RETRYABLE}
                    ),
                )
            )
        ).all()
        for delivery in deliveries:
            delivery.status = DeliveryStatus.CANCELED
            delivery.last_error_code = reason
            delivery.next_retry_at = None

    def _audit(
        self,
        user_id: UUID,
        action: str,
        target_type: str,
        target_id: str,
        metadata: dict[str, Any],
        *,
        actor_type: str,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=user_id,
                actor_type=actor_type,
                action=action,
                target_type=target_type,
                target_id=target_id,
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )


class WhatsAppConversationService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings
        self.accounts = WhatsAppAccountService(session, settings)

    async def handle(self, message: WhatsAppInboundMessage) -> WhatsAppOutboundMessage | None:
        inbound_at = _aware(message.timestamp)
        if isinstance(message, WhatsAppInboundText):
            link_token = _link_token(message.text)
            if link_token is not None:
                try:
                    linked_connection = await self.accounts.complete_link(
                        raw_token=link_token,
                        wa_id=message.wa_id,
                        profile_name=message.profile_name,
                        inbound_at=inbound_at,
                    )
                except WhatsAppServiceError as exc:
                    return WhatsAppSessionText(to=message.wa_id, body=str(exc))
                settings_url = await self._dashboard_url(
                    linked_connection, "/dashboard/integrations"
                )
                return WhatsAppSessionText(
                    to=message.wa_id,
                    body=(
                        "WhatsApp connected to Hilal Markets. Alerts follow your saved categories "
                        f"and schedule. Manage them here: {settings_url}\n\nSend MENU at any time."
                    ),
                )

        connection = await self.session.scalar(
            select(WhatsAppConnection).where(WhatsAppConnection.wa_id == message.wa_id)
        )
        if connection is None:
            signin_url = str(self.settings.public_base_url).rstrip("/") + "/signin"
            return WhatsAppSessionText(
                to=message.wa_id,
                body=(
                    "This WhatsApp account is not connected to Hilal Markets. "
                    f"Sign in and connect it at {signin_url}"
                ),
            )
        connection.last_inbound_at = inbound_at
        connection.service_window_expires_at = inbound_at + timedelta(hours=24)
        if message.profile_name:
            connection.profile_name = message.profile_name
        command = _message_command(message)
        if command.upper() in OPT_OUT_WORDS:
            await self.accounts.opt_out(connection, reason=f"inbound_{command.upper().lower()}")
            return WhatsAppSessionText(
                to=message.wa_id,
                body=(
                    "WhatsApp alerts are now off. No queued WhatsApp notifications will be sent. "
                    "Reconnect from Dashboard > Integrations to opt in again."
                ),
            )
        if command.upper() == "START" and connection.opt_out_at is not None:
            integrations_url = await self._dashboard_url(
                connection, "/dashboard/integrations"
            )
            return WhatsAppSessionText(
                to=message.wa_id,
                body=(
                    "Fresh consent is required before alerts resume. "
                    f"Reconnect here: {integrations_url}"
                ),
            )
        if connection.status != ConnectionStatus.ACTIVE or connection.revoked_at is not None:
            return WhatsAppSessionText(
                to=message.wa_id,
                body="This connection is inactive. Reconnect it from Dashboard > Integrations.",
            )
        conversation = await self._conversation(connection, message.message_id)
        if isinstance(message, WhatsAppInboundUnsupported):
            return WhatsAppSessionText(
                to=message.wa_id,
                body="I can read text and the buttons or lists shown here. Send MENU to continue.",
            )
        normalized = command.strip()
        upper = normalized.upper()
        if upper in {"MENU", "HELP", "HI", "HELLO", "START", "NAV:MENU"}:
            return self._main_menu(message.wa_id)
        if upper in {"PAUSE", "NAV:PAUSE"}:
            await self.accounts.pause(connection.user_id, actor_type="whatsapp")
            return WhatsAppSessionText(
                to=message.wa_id,
                body="WhatsApp alerts are paused. Send RESUME to turn them back on.",
            )
        if upper in {"RESUME", "NAV:RESUME"}:
            try:
                await self.accounts.resume(connection.user_id, actor_type="whatsapp")
            except WhatsAppServiceError as exc:
                return WhatsAppSessionText(to=message.wa_id, body=str(exc))
            return WhatsAppSessionText(
                to=message.wa_id, body="WhatsApp alerts are active again."
            )
        if upper in {"MONITORS", "MY MONITORS", "NAV:MONITORS"}:
            return await self._monitor_list(connection, conversation)
        if normalized.startswith("monitor:"):
            return await self._monitor_action(connection, normalized)
        if normalized.startswith("nav:"):
            return await self._navigation(connection, normalized.partition(":")[2])
        if upper in {"ABOUT", "NAV:ABOUT"}:
            return WhatsAppSessionText(
                to=message.wa_id,
                body=(
                    "Hilal Markets turns your crypto spot research rules into explainable Watch "
                    "Plans. It does not execute trades or promise outcomes. Send MENU to continue."
                ),
            )
        setup_url = await self._dashboard_url(connection, MONITOR_PATH)
        return WhatsAppSessionText(
            to=message.wa_id,
            body=(
                "I can help navigate your account here. Strategy interpretation and approval stay "
                f"in the secure setup workspace: {setup_url}\n\nSend MENU for other actions."
            ),
        )

    async def _conversation(
        self, connection: WhatsAppConnection, message_id: str
    ) -> WhatsAppConversationState:
        row = await self.session.scalar(
            select(WhatsAppConversationState).where(
                WhatsAppConversationState.wa_id == connection.wa_id
            )
        )
        if row is None:
            row = WhatsAppConversationState(
                user_id=connection.user_id,
                wa_id=connection.wa_id,
                flow="main_menu",
                step="idle",
                state_data={},
                correlation_id=uuid4().hex,
            )
            self.session.add(row)
        row.user_id = connection.user_id
        row.last_inbound_message_id = message_id
        row.expires_at = datetime.now(UTC) + timedelta(days=7)
        return row

    @staticmethod
    def _main_menu(wa_id: str) -> WhatsAppInteractiveList:
        return WhatsAppInteractiveList(
            to=wa_id,
            body="Hilal Markets menu. Choose what you want to review.",
            button_text="Open menu",
            sections=[
                WhatsAppListSection(
                    title="Research monitoring",
                    rows=[
                        WhatsAppListRow(
                            id="nav:monitors",
                            title="My Watchlists",
                            description="Status and controls",
                        ),
                        WhatsAppListRow(
                            id="nav:lifecycles",
                            title="Opportunity journeys",
                            description="Forming and completed",
                        ),
                        WhatsAppListRow(
                            id="nav:check",
                            title="Check market now",
                            description="One-time research scan",
                        ),
                        WhatsAppListRow(
                            id="nav:create",
                            title="Create Watchlist",
                            description="Secure setup workspace",
                        ),
                    ],
                ),
                WhatsAppListSection(
                    title="Account",
                    rows=[
                        WhatsAppListRow(id="nav:settings", title="Notifications"),
                        WhatsAppListRow(id="nav:billing", title="Trial and pricing"),
                        WhatsAppListRow(id="nav:support", title="Support"),
                        WhatsAppListRow(id="nav:dashboard", title="Dashboard"),
                        WhatsAppListRow(id="nav:about", title="About Hilal Markets"),
                    ],
                ),
            ],
            footer="Decision support only. No trade execution.",
        )

    async def _navigation(
        self, connection: WhatsAppConnection, destination: str
    ) -> WhatsAppOutboundMessage:
        paths = {
            "lifecycles": LIFECYCLES_PATH,
            "check": MARKET_PATH,
            "create": MONITOR_PATH,
            "settings": SETTINGS_PATH,
            "billing": SUBSCRIPTION_PATH,
            "support": SUPPORT_PATH,
            "dashboard": "/dashboard",
        }
        if destination == "monitors":
            conversation = await self._conversation(connection, f"navigation-{uuid4().hex}")
            return await self._monitor_list(connection, conversation)
        if destination == "about":
            return WhatsAppSessionText(
                to=connection.wa_id,
                body=(
                    "Hilal Markets provides explainable crypto spot research monitoring with "
                    "Shariah screening evidence. It does not execute trades. Send MENU to "
                    "continue."
                ),
            )
        path = paths.get(destination)
        if path is None:
            return self._main_menu(connection.wa_id)
        url = await self._dashboard_url(connection, path)
        label = destination.replace("_", " ").title()
        return WhatsAppSessionText(
            to=connection.wa_id,
            body=f"Open {label} securely: {url}\n\nThis one-time link expires shortly.",
        )

    async def _monitor_list(
        self,
        connection: WhatsAppConnection,
        conversation: WhatsAppConversationState,
    ) -> WhatsAppOutboundMessage:
        strategies = list(
            (
                await self.session.scalars(
                    select(Strategy)
                    .where(
                        Strategy.user_id == connection.user_id,
                        Strategy.status != StrategyStatus.ARCHIVED,
                        Strategy.archived_at.is_(None),
                    )
                    .order_by(Strategy.updated_at.desc())
                    .limit(10)
                )
            ).all()
        )
        if not strategies:
            url = await self._dashboard_url(connection, MONITOR_PATH)
            return WhatsAppSessionText(
                to=connection.wa_id,
                body=f"You have no Watchlists yet. Create one securely: {url}",
            )
        conversation.state_data = {
            **(conversation.state_data or {}),
            "monitor_ids": [str(strategy.id) for strategy in strategies],
        }
        return WhatsAppInteractiveList(
            to=connection.wa_id,
            body="Your Watchlists. Select one to view safe controls.",
            button_text="Choose Watchlist",
            sections=[
                WhatsAppListSection(
                    title="Watchlists",
                    rows=[
                        WhatsAppListRow(
                            id=f"monitor:view:{strategy.id}",
                            title=strategy.name[:24],
                            description=strategy.status.value.replace("_", " ").title(),
                        )
                        for strategy in strategies
                    ],
                )
            ],
        )

    async def _monitor_action(
        self, connection: WhatsAppConnection, action_id: str
    ) -> WhatsAppOutboundMessage:
        parts = action_id.split(":", 2)
        if len(parts) != 3:
            return WhatsAppSessionText(
                to=connection.wa_id, body="That Watchlist action is invalid. Send MENU."
            )
        _, action, raw_id = parts
        try:
            strategy_id = UUID(raw_id)
        except ValueError:
            return WhatsAppSessionText(
                to=connection.wa_id, body="That Watchlist action is invalid. Send MENU."
            )
        strategy = await self.session.get(Strategy, strategy_id)
        if strategy is None or strategy.user_id != connection.user_id:
            return WhatsAppSessionText(
                to=connection.wa_id, body="Watchlist not found. Send MONITORS to refresh."
            )
        if action == "view":
            controls: list[WhatsAppReplyButton] = []
            if strategy.status == StrategyStatus.ACTIVE:
                controls.append(
                    WhatsAppReplyButton(id=f"monitor:pause:{strategy.id}", title="Pause alerts")
                )
            elif strategy.status == StrategyStatus.PAUSED:
                controls.append(
                    WhatsAppReplyButton(id=f"monitor:resume:{strategy.id}", title="Resume alerts")
                )
            controls.append(
                WhatsAppReplyButton(id=f"monitor:open:{strategy.id}", title="Open dashboard")
            )
            controls.append(WhatsAppReplyButton(id="nav:menu", title="Main menu"))
            return WhatsAppInteractiveButtons(
                to=connection.wa_id,
                body=(
                    f"{strategy.name}\nStatus: {strategy.status.value.replace('_', ' ').title()}\n"
                    "Pausing stops future notifications but keeps the Watchlist saved."
                ),
                buttons=controls,
            )
        if action == "open":
            # This monitor's own page. It used to be the setup-chat page carrying a
            # strategy id nothing on that page reads and an anchor naming a section of it
            # that is marked hidden — so "Open dashboard" for one monitor opened somebody's
            # last conversation instead, and named no monitor at all.
            url = await self._dashboard_url(connection, f"/dashboard/strategies/{strategy.id}")
            return WhatsAppSessionText(
                to=connection.wa_id, body=f"Open this Watchlist securely: {url}"
            )
        operations = MonitorOperationService(self.session, settings=self.settings)
        try:
            if action == "pause":
                strategy = await operations.pause(
                    user_id=connection.user_id,
                    strategy_id=strategy.id,
                    actor_type="whatsapp",
                )
            elif action == "resume":
                strategy = await operations.resume(
                    user_id=connection.user_id,
                    strategy_id=strategy.id,
                    actor_type="whatsapp",
                )
            else:
                raise MonitorOperationError("unsupported_action", "Action is unsupported.")
        except MonitorOperationError as exc:
            return WhatsAppSessionText(to=connection.wa_id, body=str(exc))
        return WhatsAppSessionText(
            to=connection.wa_id,
            body=(
                f"{strategy.name} is now {strategy.status.value.replace('_', ' ')}. "
                "Send MONITORS to review all Watchlists."
            ),
        )

    async def _dashboard_url(self, connection: WhatsAppConnection, path: str) -> str:
        return await DashboardLinkService(self.session, self.settings).create(
            user_id=connection.user_id,
            source_platform=Platform.WHATSAPP,
            source_subject=connection.wa_id,
            target_path=path,
        )


class WhatsAppIntegrationTestService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        adapter: WhatsAppCloudAdapter,
    ):
        self.session = session
        self.settings = settings
        self.adapter = adapter

    async def send(self, user_id: UUID) -> IntegrationTestResult:
        connection = await WhatsAppAccountService(
            self.session, self.settings
        )._owned_connection(user_id)
        if (
            connection.status != ConnectionStatus.ACTIVE
            or connection.verified_at is None
            or connection.opt_out_at is not None
        ):
            raise WhatsAppServiceError(
                "whatsapp_not_active", "Reconnect WhatsApp before sending a test."
            )
        settings_url = str(self.settings.public_base_url).rstrip("/") + "/dashboard/integrations"
        now = datetime.now(UTC)
        if _inside_service_window(connection, now):
            outbound: WhatsAppOutboundMessage = WhatsAppSessionText(
                to=connection.wa_id,
                body=(
                    "Hilal Markets WhatsApp test succeeded. Your saved notification preferences "
                    f"remain in control: {settings_url}"
                ),
            )
        else:
            template_message = WhatsAppTemplateRegistry(self.settings).build(
                event_type="connection_test",
                locale=connection.preferred_locale,
                to=connection.wa_id,
                variables={
                    "display_name": connection.profile_name or "Hilal Markets user",
                    "settings_url": settings_url,
                },
            )
            if template_message is None:
                raise WhatsAppServiceError(
                    "whatsapp_test_template_missing",
                    "The approved WhatsApp connection-test template is not configured.",
                )
            outbound = template_message
        result_row = IntegrationTestResult(
            user_id=user_id,
            integration=DeliveryChannel.WHATSAPP.value,
            connection_id=str(connection.id),
            destination=mask_e164(connection.phone_e164),
            status="sending",
            metadata_json={"service_window": _inside_service_window(connection, now)},
        )
        self.session.add(result_row)
        await self.session.flush()
        try:
            provider = await self.adapter.deliver(outbound)
        except WhatsAppDeliveryError as exc:
            result_row.status = "failed"
            result_row.error_code = exc.code
            result_row.error_detail = str(exc)[:500]
            connection.last_error_code = exc.code
            raise
        result_row.status = "sent"
        result_row.provider_message_id = provider.provider_message_id
        result_row.metadata_json = {
            **(result_row.metadata_json or {}),
            "provider_status": "accepted",
        }
        connection.last_error_code = None
        self.session.add(
            AuditEvent(
                actor_user_id=user_id,
                actor_type="dashboard_user",
                action="whatsapp.test_sent",
                target_type="whatsapp_connection",
                target_id=str(connection.id),
                metadata_redacted={"test_result_id": str(result_row.id)},
                created_at=now,
            )
        )
        return result_row


class WhatsAppDeliveryService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        adapter: WhatsAppCloudAdapter,
    ):
        self.session = session
        self.settings = settings
        self.adapter = adapter
        self.registry = WhatsAppTemplateRegistry(settings)

    async def process_due(self, *, limit: int = 50) -> list[AlertDelivery]:
        now = datetime.now(UTC)
        deliveries = list(
            (
                await self.session.scalars(
                    select(AlertDelivery)
                    .where(
                        AlertDelivery.channel == DeliveryChannel.WHATSAPP,
                        or_(
                            AlertDelivery.status == DeliveryStatus.PENDING,
                            (
                                (AlertDelivery.status == DeliveryStatus.FAILED_RETRYABLE)
                                & (AlertDelivery.next_retry_at <= now)
                            ),
                        ),
                    )
                    .order_by(AlertDelivery.created_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            ).all()
        )
        for delivery in deliveries:
            await self._process(delivery, now)
        await self.session.flush()
        return deliveries

    async def _process(self, delivery: AlertDelivery, now: datetime) -> None:
        alert = await self.session.get(Alert, delivery.alert_id)
        if alert is None or not delivery.destination_key.startswith("wa:"):
            self._permanent_failure(delivery, "delivery_context_missing", now)
            return
        wa_id = delivery.destination_key.removeprefix("wa:")
        connection = await self.session.scalar(
            select(WhatsAppConnection).where(
                WhatsAppConnection.user_id == alert.user_id,
                WhatsAppConnection.wa_id == wa_id,
            )
        )
        if not _connection_delivery_eligible(connection):
            delivery.status = DeliveryStatus.CANCELED
            delivery.last_error_code = "whatsapp_connection_inactive"
            delivery.next_retry_at = None
            return
        assert connection is not None
        presentation = AlertPresentation.from_alert(
            alert, public_base_url=str(self.settings.public_base_url)
        )
        dashboard_path = (
            COMPLIANCE_CHANGES_PATH
            if presentation.alert_type == "compliance"
            else LIFECYCLES_PATH
        )
        dashboard_url = await DashboardLinkService(self.session, self.settings).create(
            user_id=connection.user_id,
            source_platform=Platform.WHATSAPP,
            source_subject=connection.wa_id,
            target_path=dashboard_path,
        )
        rendered = WhatsAppAlertRenderer.render(presentation, dashboard_url=dashboard_url)
        if rendered.category not in set(connection.opt_in_categories or []):
            delivery.status = DeliveryStatus.SUPPRESSED
            delivery.last_error_code = "whatsapp_category_not_selected"
            delivery.next_retry_at = None
            return
        if rendered.event_type in WHATSAPP_OPPORTUNITY_EVENTS and (
            not self.settings.whatsapp_opportunity_alerts_enabled
            or self.registry.template_name(
                rendered.event_type, connection.preferred_locale
            )
            is None
        ):
            delivery.status = DeliveryStatus.SUPPRESSED
            delivery.last_error_code = "whatsapp_opportunity_delivery_disabled"
            delivery.next_retry_at = None
            return
        if _inside_service_window(connection, now):
            outbound: WhatsAppOutboundMessage = WhatsAppAlertRenderer.session_message(
                connection.wa_id, rendered.session_body
            )
        else:
            template_message = self.registry.build(
                event_type=rendered.event_type,
                locale=connection.preferred_locale,
                to=connection.wa_id,
                variables=rendered.template_variables,
            )
            if template_message is None:
                self._permanent_failure(delivery, "whatsapp_template_missing", now)
                return
            outbound = template_message
        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        try:
            result = await self.adapter.deliver(outbound)
        except WhatsAppDeliveryError as exc:
            permanent = (
                not exc.retryable
                or delivery.attempt_count >= self.settings.whatsapp_max_delivery_attempts
            )
            delivery.status = (
                DeliveryStatus.FAILED_PERMANENT
                if permanent
                else DeliveryStatus.FAILED_RETRYABLE
            )
            delivery.last_error_code = exc.code
            delivery.last_error_detail = str(exc)[:500]
            delivery.provider_status = "failed"
            delivery.provider_status_metadata = {
                "http_status": exc.http_status,
                "provider_error_code": exc.provider_error_code,
                "provider_error_subcode": exc.provider_error_subcode,
            }
            connection.last_error_code = exc.code
            if permanent:
                delivery.next_retry_at = None
            else:
                delay = exc.retry_after_seconds or min(
                    3600, 30 * (2 ** (delivery.attempt_count - 1))
                )
                delivery.next_retry_at = now + timedelta(seconds=delay)
            await _candidate_notification_state(self.session, alert, "failed")
            await _record_integration_health(
                self.session,
                connection,
                healthy=False,
                error_code=exc.code,
            )
            return
        delivery.status = DeliveryStatus.SENT
        delivery.provider_message_id = result.provider_message_id
        delivery.provider_status = "accepted"
        delivery.accepted_at = now
        delivery.next_retry_at = None
        delivery.last_error_code = None
        delivery.last_error_detail = None
        delivery.provider_status_metadata = {}
        connection.last_error_code = None
        await _candidate_notification_state(self.session, alert, "sent")

    @staticmethod
    def _permanent_failure(
        delivery: AlertDelivery, code: str, now: datetime
    ) -> None:
        delivery.status = DeliveryStatus.FAILED_PERMANENT
        delivery.last_attempt_at = now
        delivery.last_error_code = code
        delivery.next_retry_at = None


class WhatsAppStatusService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def apply(self, payload: dict[str, Any]) -> None:
        provider_id = str(payload.get("provider_message_id") or "")
        status = str(payload.get("status") or "unknown")
        event_at = _aware(datetime.fromisoformat(str(payload["timestamp"])))
        delivery = await self.session.scalar(
            select(AlertDelivery)
            .where(AlertDelivery.provider_message_id == provider_id)
            .with_for_update()
        )
        test_result = None
        if delivery is None:
            test_result = await self.session.scalar(
                select(IntegrationTestResult).where(
                    IntegrationTestResult.provider_message_id == provider_id
                )
            )
            if test_result is None:
                return
        if test_result is not None:
            if status in {"sent", "delivered", "read"}:
                if _provider_status_rank(status) >= _provider_status_rank(test_result.status):
                    test_result.status = status
                    test_result.error_code = None
                    test_result.error_detail = None
            elif status == "failed" and test_result.status not in {"delivered", "read"}:
                test_result.status = "failed"
                test_result.error_code = str(payload.get("error_code") or "whatsapp_failed")
                test_result.error_detail = str(payload.get("error_message") or "")[:500]
            observed_test_statuses = list(
                (test_result.metadata_json or {}).get("observed_statuses", [])
            )
            if status not in observed_test_statuses:
                observed_test_statuses.append(status)
            test_result.metadata_json = {
                **(test_result.metadata_json or {}),
                "provider_status": test_result.status,
                "provider_event_at": event_at.isoformat(),
                "observed_statuses": observed_test_statuses[-8:],
            }
            return
        assert delivery is not None
        alert = await self.session.get(Alert, delivery.alert_id)
        connection = await self.session.scalar(
            select(WhatsAppConnection).where(
                WhatsAppConnection.wa_id == delivery.destination_key.removeprefix("wa:")
            )
        )
        observed = list((delivery.provider_status_metadata or {}).get("observed_statuses", []))
        if status not in observed:
            observed.append(status)
        delivery.provider_status_metadata = {
            "observed_statuses": observed[-8:],
            "last_event_at": event_at.isoformat(),
            "error_code": payload.get("error_code"),
            "error_title": payload.get("error_title"),
            "error_message": payload.get("error_message"),
        }
        current_rank = _provider_status_rank(delivery.provider_status)
        incoming_rank = _provider_status_rank(status)
        if incoming_rank >= current_rank and status != "failed":
            delivery.provider_status = status
        if status == "sent":
            if delivery.status in {DeliveryStatus.PENDING, DeliveryStatus.SENT}:
                delivery.status = DeliveryStatus.SENT
        elif status == "delivered":
            newly_delivered = delivery.delivered_at is None
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered_at = delivery.delivered_at or event_at
            delivery.last_error_code = None
            delivery.next_retry_at = None
            if connection is not None:
                connection.last_delivery_at = event_at
                connection.last_error_code = None
                await _record_integration_health(
                    self.session, connection, healthy=True, error_code=None
                )
            if alert is not None:
                await _candidate_notification_state(self.session, alert, "delivered")
                if newly_delivered:
                    await TrialLifecycleService(
                        self.session, self.settings
                    ).record_successful_delivery(delivery)
        elif status == "read":
            newly_delivered = delivery.delivered_at is None
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered_at = delivery.delivered_at or event_at
            delivery.read_at = delivery.read_at or event_at
            delivery.provider_status = "read"
            delivery.last_error_code = None
            delivery.next_retry_at = None
            if connection is not None:
                connection.last_delivery_at = delivery.delivered_at
                connection.last_error_code = None
                await _record_integration_health(
                    self.session, connection, healthy=True, error_code=None
                )
            if alert is not None:
                await _candidate_notification_state(self.session, alert, "delivered")
                if newly_delivered:
                    await TrialLifecycleService(
                        self.session, self.settings
                    ).record_successful_delivery(delivery)
        elif status == "failed" and delivery.read_at is None and delivery.delivered_at is None:
            code = str(payload.get("error_code") or "whatsapp_provider_failed")
            retryable = _status_failure_retryable(code)
            permanent = (
                not retryable
                or delivery.attempt_count >= self.settings.whatsapp_max_delivery_attempts
            )
            delivery.status = (
                DeliveryStatus.FAILED_PERMANENT
                if permanent
                else DeliveryStatus.FAILED_RETRYABLE
            )
            delivery.provider_status = "failed"
            delivery.last_error_code = code[:80]
            delivery.last_error_detail = str(payload.get("error_message") or "")[:500]
            delivery.next_retry_at = (
                None
                if permanent
                else datetime.now(UTC)
                + timedelta(seconds=min(3600, 30 * (2 ** max(0, delivery.attempt_count - 1))))
            )
            if connection is not None:
                connection.last_error_code = code[:80]
                await _record_integration_health(
                    self.session, connection, healthy=False, error_code=code[:80]
                )
            if alert is not None:
                await _candidate_notification_state(self.session, alert, "failed")


class WhatsAppWebhookProcessor:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        adapter: WhatsAppCloudAdapter,
    ):
        self.session = session
        self.settings = settings
        self.adapter = adapter

    async def process(self, receipt_id: UUID) -> str:
        receipt = await self.session.scalar(
            select(WhatsAppWebhookReceipt)
            .where(WhatsAppWebhookReceipt.id == receipt_id)
            .with_for_update()
        )
        if receipt is None:
            return "missing"
        if receipt.processing_status == "processed":
            return "replayed"
        now = datetime.now(UTC)
        if receipt.next_retry_at is not None and _aware(receipt.next_retry_at) > now:
            return "not_due"
        if receipt.event_type == "status":
            await WhatsAppStatusService(self.session, self.settings).apply(
                receipt.payload_redacted
            )
            receipt.processing_status = "processed"
            receipt.processed_at = now
            receipt.error_code = None
            receipt.error_detail = None
            receipt.next_retry_at = None
            return "processed"

        outbound: WhatsAppOutboundMessage | None
        if receipt.processing_status == "ready" and receipt.response_payload:
            outbound = OUTBOUND_ADAPTER.validate_python(receipt.response_payload)
        else:
            inbound: WhatsAppInboundMessage = INBOUND_ADAPTER.validate_python(
                receipt.payload_redacted
            )
            outbound = await WhatsAppConversationService(
                self.session, self.settings
            ).handle(inbound)
            receipt.response_payload = (
                outbound.model_dump(mode="json", exclude_none=True) if outbound else {}
            )
            receipt.processing_status = "ready" if outbound else "processed"
            if outbound is None:
                receipt.processed_at = now
                return "processed"
        receipt.attempt_count += 1
        try:
            result = await self.adapter.deliver(outbound)
        except WhatsAppDeliveryError as exc:
            permanent = (
                not exc.retryable
                or receipt.attempt_count >= self.settings.whatsapp_max_delivery_attempts
            )
            receipt.processing_status = "failed_permanent" if permanent else "failed_retryable"
            receipt.error_code = exc.code
            receipt.error_detail = str(exc)[:500]
            receipt.next_retry_at = (
                None
                if permanent
                else now
                + timedelta(
                    seconds=exc.retry_after_seconds
                    or min(3600, 30 * (2 ** (receipt.attempt_count - 1)))
                )
            )
            return receipt.processing_status
        receipt.processing_status = "processed"
        receipt.processed_at = now
        receipt.result_provider_message_id = result.provider_message_id
        receipt.response_payload = {}
        receipt.error_code = None
        receipt.error_detail = None
        receipt.next_retry_at = None
        receipt.payload_redacted = {
            "kind": receipt.payload_redacted.get("kind"),
            "message_id": receipt.provider_message_id,
            "processed": True,
        }
        if self.settings.whatsapp_mark_inbound_read and receipt.provider_message_id:
            with suppress(WhatsAppDeliveryError):
                await self.adapter.mark_read(receipt.provider_message_id)
        return "processed"


def connection_payload(connection: WhatsAppConnection | None) -> dict[str, Any] | None:
    if connection is None:
        return None
    return {
        "id": connection.id,
        "phone": mask_e164(connection.phone_e164),
        "profile_name": connection.profile_name,
        "status": connection.status.value,
        "alerts_enabled": connection.alerts_enabled,
        "verified": connection.verified_at is not None,
        "opted_in": connection.opt_in_at is not None and connection.opt_out_at is None,
        "opt_in_categories": connection.opt_in_categories,
        "preferred_locale": connection.preferred_locale,
        "connected_at": connection.connected_at,
        "last_inbound_at": connection.last_inbound_at,
        "service_window_expires_at": connection.service_window_expires_at,
        "last_delivery_at": connection.last_delivery_at,
        "last_error_code": connection.last_error_code,
        "paused_at": connection.paused_at,
        "revoked_at": connection.revoked_at,
    }


def _link_token(text: str) -> str | None:
    parts = text.strip().split()
    if len(parts) == 2 and parts[0].upper() == "LINK":
        return parts[1]
    return None


def _message_command(message: WhatsAppInboundMessage) -> str:
    if isinstance(message, WhatsAppInboundText):
        return message.text
    if isinstance(message, WhatsAppInboundButtonReply | WhatsAppInboundListReply):
        return message.reply_id
    if isinstance(message, WhatsAppInboundUnsupported):
        return message.message_type
    return ""


def _connection_delivery_eligible(connection: WhatsAppConnection | None) -> bool:
    return bool(
        connection is not None
        and connection.status == ConnectionStatus.ACTIVE
        and connection.verified_at is not None
        and connection.alerts_enabled
        and connection.opt_in_at is not None
        and connection.opt_out_at is None
        and connection.revoked_at is None
    )


def _inside_service_window(connection: WhatsAppConnection, now: datetime) -> bool:
    return bool(
        connection.service_window_expires_at is not None
        and _aware(connection.service_window_expires_at) > now
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _provider_status_rank(value: str | None) -> int:
    return {None: 0, "accepted": 0, "sent": 1, "delivered": 2, "read": 3}.get(
        value, 0
    )


def _status_failure_retryable(code: str) -> bool:
    return code in {"130429", "131000", "131016", "131048", "131056"}


async def _candidate_notification_state(
    session: AsyncSession, alert: Alert, state: str
) -> None:
    if alert.setup_instance_id is None:
        return
    readiness = await session.scalar(
        select(CandidateReadinessSnapshot).where(
            CandidateReadinessSnapshot.setup_instance_id == alert.setup_instance_id
        )
    )
    if readiness is not None:
        readiness.notification_status = state


async def _record_integration_health(
    session: AsyncSession,
    connection: WhatsAppConnection,
    *,
    healthy: bool,
    error_code: str | None,
) -> None:
    now = datetime.now(UTC)
    scope = f"user:{connection.user_id}"
    health = await session.scalar(
        select(IntegrationHealth).where(
            IntegrationHealth.integration == DeliveryChannel.WHATSAPP.value,
            IntegrationHealth.scope_key == scope,
        )
    )
    if health is None:
        health = IntegrationHealth(
            integration=DeliveryChannel.WHATSAPP.value,
            scope_key=scope,
            status=HealthStatus.UNKNOWN,
            consecutive_failures=0,
            checked_at=now,
        )
        session.add(health)
    health.checked_at = now
    if healthy:
        health.status = HealthStatus.HEALTHY
        health.consecutive_failures = 0
        health.last_success_at = now
        health.last_error_code = None
    else:
        health.status = HealthStatus.DEGRADED
        health.consecutive_failures += 1
        health.last_failure_at = now
        health.last_error_code = error_code
