from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    CandidateReadinessSnapshot,
    TelegramConnection,
    WhatsAppConnection,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.alert_emails import alert_email_address
from ai_market_monitor.services.alert_limits import CHOSEN_CHANNEL_NOT_CONNECTED
from ai_market_monitor.services.alert_presentation import AlertPresentation
from ai_market_monitor.services.email_delivery import email_delivery_available
from ai_market_monitor.services.notification_preferences import NotificationPreferenceService
from ai_market_monitor.services.trials import TrialLifecycleService
from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter
from ai_market_monitor.telegram.types import TelegramButton, TelegramOutboundMessage
from ai_market_monitor.whatsapp.rendering import (
    WHATSAPP_OPPORTUNITY_EVENTS,
    WhatsAppAlertRenderer,
    WhatsAppTemplateRegistry,
)


class NotificationDispatcher:
    def __init__(self, session: AsyncSession, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

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
        preference = await NotificationPreferenceService(
            self.session, self.settings
        ).current(alert.user_id)
        requested |= preference.channels
        decision = await NotificationPreferenceService(
            self.session, self.settings
        ).delivery_decision(
            alert.user_id,
            requested,
            alert=alert,
        )
        requested = decision.channels
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
        if DeliveryChannel.WHATSAPP in requested:
            delivery = await self._enqueue_whatsapp(alert)
            if delivery is not None:
                deliveries.append(delivery)
        if DeliveryChannel.EMAIL in requested:
            delivery = await self._enqueue_email(alert)
            if delivery is not None:
                deliveries.append(delivery)
        if DeliveryChannel.WEB in requested:
            deliveries.append(
                await self._enqueue_one(
                    alert,
                    DeliveryChannel.WEB,
                    f"dashboard:{alert.user_id}",
                    status=DeliveryStatus.PENDING,
                )
            )
        self._record_silence(alert, deliveries, decision.blocked_by)
        await self.session.flush()
        return deliveries

    @staticmethod
    def _record_silence(
        alert: Alert,
        deliveries: list[AlertDelivery],
        blocked_by: str | None,
    ) -> None:
        """Write down why an alert was sent nowhere, so a screen can say it later.

        An alert row with no delivery beside it and no reason on it is the product losing
        its own decision. It happened fifty times in a row on one monitor: every message
        was withheld because an hourly limit had been reached, none of it was recorded,
        and the owner was left with a monitor that had simply gone quiet.

        Silence has a second cause, and it needs its own words. The gate can allow a
        channel that then queues nothing: Telegram with no live connection, or email on
        an account with no address. Nothing was refused, so ``blocked_by`` is empty — and
        the alert would fall through here with no reason at all, which is the very hole
        this exists to close. The person's chosen way of being told is simply not
        connected, and that is a different thing to fix from a limit they set.

        Never written over a reason already there — the scanner records its own
        suppressions before this point, and its answer is the earlier one.
        """

        if deliveries or alert.suppressed_reason:
            return
        alert.suppressed_reason = (blocked_by or CHOSEN_CHANNEL_NOT_CONNECTED)[:160]

    async def enqueue_user_alert(
        self,
        alert: Alert,
        *,
        channels: list[DeliveryChannel] | None = None,
    ) -> list[AlertDelivery]:
        if channels is None:
            requested = (
                await NotificationPreferenceService(
                    self.session, self.settings
                ).current(alert.user_id)
            ).channels
        else:
            requested = set(channels)
        decision = await NotificationPreferenceService(
            self.session, self.settings
        ).delivery_decision(
            alert.user_id,
            requested,
            alert=alert,
        )
        requested = decision.channels
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
        if DeliveryChannel.WHATSAPP in requested:
            delivery = await self._enqueue_whatsapp(alert)
            if delivery is not None:
                deliveries.append(delivery)
        if DeliveryChannel.EMAIL in requested:
            delivery = await self._enqueue_email(alert)
            if delivery is not None:
                deliveries.append(delivery)
        if DeliveryChannel.WEB in requested:
            deliveries.append(
                await self._enqueue_one(
                    alert,
                    DeliveryChannel.WEB,
                    f"dashboard:{alert.user_id}",
                )
            )
        self._record_silence(alert, deliveries, decision.blocked_by)
        await self.session.flush()
        return deliveries

    async def _enqueue_email(self, alert: Alert) -> AlertDelivery | None:
        """Queue one alert for the address on the account.

        Two things have to be true, and both are asked of their owner rather than
        answered here: the platform must be able to send email at all, and the account
        must have an address. Queueing without either would put a row in the table that
        nothing can ever deliver and nothing will ever clear.
        """

        if not email_delivery_available(self.settings):
            return None
        recipient = await alert_email_address(self.session, alert.user_id)
        if not recipient:
            return None
        return await self._enqueue_one(alert, DeliveryChannel.EMAIL, f"email:{recipient}")

    async def _enqueue_whatsapp(self, alert: Alert) -> AlertDelivery | None:
        if not self.settings.whatsapp_enabled:
            return None
        connection = await self.session.scalar(
            select(WhatsAppConnection).where(
                WhatsAppConnection.user_id == alert.user_id,
                WhatsAppConnection.status == ConnectionStatus.ACTIVE,
                WhatsAppConnection.alerts_enabled.is_(True),
                WhatsAppConnection.verified_at.is_not(None),
                WhatsAppConnection.opt_in_at.is_not(None),
                WhatsAppConnection.opt_out_at.is_(None),
                WhatsAppConnection.revoked_at.is_(None),
            )
        )
        if connection is None:
            return None
        presentation = AlertPresentation.from_alert(
            alert, public_base_url=str(self.settings.public_base_url)
        )
        rendered = WhatsAppAlertRenderer.render(
            presentation,
            dashboard_url=str(self.settings.public_base_url).rstrip("/") + "/dashboard",
        )
        if rendered.category not in set(connection.opt_in_categories or []):
            return None
        registry = WhatsAppTemplateRegistry(self.settings)
        template_name = registry.template_name(
            rendered.event_type, connection.preferred_locale
        )
        now = datetime.now(UTC)
        window_open = bool(
            connection.service_window_expires_at is not None
            and (
                connection.service_window_expires_at.replace(tzinfo=UTC)
                if connection.service_window_expires_at.tzinfo is None
                else connection.service_window_expires_at
            )
            > now
        )
        if rendered.event_type in WHATSAPP_OPPORTUNITY_EVENTS and (
            not self.settings.whatsapp_opportunity_alerts_enabled or template_name is None
        ):
            return None
        if not window_open and template_name is None:
            return None
        return await self._enqueue_one(
            alert,
            DeliveryChannel.WHATSAPP,
            f"wa:{connection.wa_id}",
        )

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
