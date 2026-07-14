from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    CandidateReadinessSnapshot,
    DiscordDeliveryDestination,
    TelegramConnection,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.alert_presentation import AlertPresentation
from ai_market_monitor.services.notification_preferences import NotificationPreferenceService
from ai_market_monitor.services.trials import TrialLifecycleService
from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter
from ai_market_monitor.telegram.types import TelegramButton, TelegramOutboundMessage


class NotificationDispatcher:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def enqueue(
        self,
        alert: Alert,
        definition: StrategyDefinition,
    ) -> list[AlertDelivery]:
        deliveries: list[AlertDelivery] = []
        requested = {
            DeliveryChannel(channel)
            for channel in definition.alerts.channels
            if channel in {item.value for item in DeliveryChannel}
        }
        preference = await NotificationPreferenceService(self.session).current(alert.user_id)
        requested |= preference.channels
        requested = await NotificationPreferenceService(self.session).allowed_channels(
            alert.user_id,
            requested,
            alert=alert,
        )
        if DeliveryChannel.TELEGRAM in requested:
            connection = await self.session.scalar(
                select(TelegramConnection).where(
                    TelegramConnection.user_id == alert.user_id,
                    TelegramConnection.status == ConnectionStatus.ACTIVE,
                    TelegramConnection.alerts_enabled.is_(True),
                    TelegramConnection.chat_id.is_not(None),
                )
            )
            if connection and connection.chat_id:
                deliveries.append(
                    await self._enqueue_one(
                        alert,
                        DeliveryChannel.TELEGRAM,
                        f"chat:{connection.chat_id}",
                    )
                )
        if DeliveryChannel.DISCORD in requested:
            destinations = (
                await self.session.scalars(
                    select(DiscordDeliveryDestination).where(
                        DiscordDeliveryDestination.user_id == alert.user_id,
                        DiscordDeliveryDestination.status == "active",
                        DiscordDeliveryDestination.permissions_status.in_(["ok", "valid"]),
                        DiscordDeliveryDestination.test_status == "sent",
                    )
                )
            ).all()
            for destination in destinations:
                key = (
                    f"dm:{destination.discord_user_id}"
                    if destination.mode == "dm"
                    else f"guild:{destination.guild_id}:channel:{destination.channel_id}"
                )
                deliveries.append(await self._enqueue_one(alert, DeliveryChannel.DISCORD, key))
        if DeliveryChannel.WEB in requested:
            deliveries.append(
                await self._enqueue_one(
                    alert,
                    DeliveryChannel.WEB,
                    f"dashboard:{alert.user_id}",
                    status=DeliveryStatus.PENDING,
                )
            )
        await self.session.flush()
        return deliveries

    async def enqueue_user_alert(
        self,
        alert: Alert,
        *,
        channels: list[DeliveryChannel] | None = None,
    ) -> list[AlertDelivery]:
        requested = set(channels or [DeliveryChannel.TELEGRAM])
        requested = await NotificationPreferenceService(self.session).allowed_channels(
            alert.user_id,
            requested,
            alert=alert,
        )
        deliveries: list[AlertDelivery] = []
        if DeliveryChannel.TELEGRAM in requested:
            connection = await self.session.scalar(
                select(TelegramConnection).where(
                    TelegramConnection.user_id == alert.user_id,
                    TelegramConnection.status == ConnectionStatus.ACTIVE,
                    TelegramConnection.alerts_enabled.is_(True),
                    TelegramConnection.chat_id.is_not(None),
                )
            )
            if connection and connection.chat_id:
                deliveries.append(
                    await self._enqueue_one(
                        alert,
                        DeliveryChannel.TELEGRAM,
                        f"chat:{connection.chat_id}",
                    )
                )
        await self.session.flush()
        return deliveries

    async def _enqueue_one(
        self,
        alert: Alert,
        channel: DeliveryChannel,
        destination_key: str,
        *,
        status: DeliveryStatus = DeliveryStatus.PENDING,
    ) -> AlertDelivery:
        existing = await self.session.scalar(
            select(AlertDelivery).where(
                AlertDelivery.alert_id == alert.id,
                AlertDelivery.channel == channel,
                AlertDelivery.destination_key == destination_key,
            )
        )
        if existing:
            return existing
        delivery = AlertDelivery(
            alert_id=alert.id,
            channel=channel,
            destination_key=destination_key,
            status=status,
        )
        self.session.add(delivery)
        return delivery


class TelegramDeliveryService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        adapter: TelegramHttpAdapter,
    ):
        self.session = session
        self.settings = settings
        self.adapter = adapter

    async def process_due(self, *, limit: int = 50) -> list[AlertDelivery]:
        now = datetime.now(UTC)
        deliveries = (
            await self.session.scalars(
                select(AlertDelivery)
                .where(
                    AlertDelivery.channel == DeliveryChannel.TELEGRAM,
                    or_(
                        AlertDelivery.status == DeliveryStatus.PENDING,
                        (
                            (AlertDelivery.status == DeliveryStatus.FAILED_RETRYABLE)
                            & (AlertDelivery.next_retry_at <= now)
                        ),
                        (
                            (AlertDelivery.status == DeliveryStatus.SENT)
                            & AlertDelivery.provider_message_id.is_(None)
                        ),
                    ),
                )
                .order_by(AlertDelivery.created_at.asc())
                .limit(limit)
            )
        ).all()
        processed: list[AlertDelivery] = []
        for delivery in deliveries:
            alert = await self.session.get(Alert, delivery.alert_id)
            if alert is None or not delivery.destination_key.startswith("chat:"):
                delivery.status = DeliveryStatus.FAILED_PERMANENT
                delivery.last_error_code = "delivery_context_missing"
                processed.append(delivery)
                continue
            chat_id = delivery.destination_key.removeprefix("chat:")
            delivery.attempt_count += 1
            delivery.last_attempt_at = now
            try:
                presentation = AlertPresentation.from_alert(
                    alert,
                    public_base_url=str(self.settings.public_base_url),
                )
                result = await self.adapter.deliver(
                    TelegramOutboundMessage(
                        chat_id=chat_id,
                        text=presentation.telegram_text(),
                        buttons=[
                            TelegramButton(
                                action.label,
                                action.action_id,
                                url=action.url,
                            )
                            for action in presentation.actions
                        ],
                    )
                )
                if not result.message_ids:
                    raise TelegramDeliveryError(
                        "telegram_message_id_missing",
                        "Telegram accepted the request but did not return a message id.",
                        retryable=True,
                    )
                delivery.status = DeliveryStatus.SENT
                delivery.provider_message_id = result.message_ids[0]
                delivery.delivered_at = now
                delivery.next_retry_at = None
                delivery.last_error_code = None
                delivery.last_error_detail = None
                connection = await self.session.scalar(
                    select(TelegramConnection).where(TelegramConnection.chat_id == chat_id)
                )
                if connection:
                    connection.last_delivery_at = now
                    connection.last_error_code = None
                await TrialLifecycleService(self.session, self.settings).record_successful_delivery(
                    delivery
                )
                if alert.setup_instance_id is not None:
                    readiness = await self.session.scalar(
                        select(CandidateReadinessSnapshot).where(
                            CandidateReadinessSnapshot.setup_instance_id
                            == alert.setup_instance_id
                        )
                    )
                    if readiness is not None:
                        readiness.notification_status = "delivered"
            except TelegramDeliveryError as exc:
                permanent = not exc.retryable or delivery.attempt_count >= 5
                delivery.status = (
                    DeliveryStatus.FAILED_PERMANENT
                    if permanent
                    else DeliveryStatus.FAILED_RETRYABLE
                )
                delivery.delivered_at = None
                delivery.last_error_code = exc.code
                delivery.last_error_detail = str(exc)[:500]
                connection = await self.session.scalar(
                    select(TelegramConnection).where(TelegramConnection.chat_id == chat_id)
                )
                if connection:
                    connection.last_error_code = exc.code
                if alert.setup_instance_id is not None:
                    readiness = await self.session.scalar(
                        select(CandidateReadinessSnapshot).where(
                            CandidateReadinessSnapshot.setup_instance_id
                            == alert.setup_instance_id
                        )
                    )
                    if readiness is not None:
                        readiness.notification_status = "failed"
                if not permanent:
                    delay = exc.retry_after_seconds or min(
                        3600, 30 * (2 ** (delivery.attempt_count - 1))
                    )
                    delivery.next_retry_at = now + timedelta(seconds=delay)
            processed.append(delivery)
        await self.session.flush()
        return processed
