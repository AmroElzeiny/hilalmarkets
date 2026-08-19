"""One real message, on every way of being told the person chose.

Switching a monitor on and being told "it worked" proves nothing. The only honest way
to answer "will I actually hear about it?" is to send something and report what each
channel did — which is why this sends a **real** message through the real sender for
every channel, one at a time, and returns one row per channel saying whether it left.

Three things it deliberately does *not* do:

* it does not queue. A queued message tells a person nothing while they are looking at
  the screen. Every channel here is sent inside the request, so the result on the page
  is the result the provider gave;
* it does not stop at the first failure. Telegram being disconnected must not hide the
  fact that email worked, so every channel is tried and every outcome is reported;
* it does not invent an outcome. A channel the platform cannot deliver on comes back as
  a failure with the reason, never as a quiet success.

The message itself is not a fake alert. It says plainly that it is a test and that
nothing has happened in the market yet, because a message that looks like a real find
is a message that could be acted on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import Alert, AlertDelivery, TelegramConnection
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
)
from ai_market_monitor.services.alert_emails import (
    ALERT_SENDER_EMAIL,
    ALERT_SENDER_NAME,
    alert_email_address,
)
from ai_market_monitor.services.email_branding import HilalMarketsEmailRenderer
from ai_market_monitor.services.email_delivery import (
    AuthEmailService,
    EmailDeliveryError,
    email_delivery_available,
)
from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter
from ai_market_monitor.telegram.types import TelegramButton, TelegramOutboundMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChannelResult:
    """What one way of being told actually did."""

    channel: str
    sent: bool
    #: One sentence a beginner can act on. Present whether it worked or not.
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"channel": self.channel, "sent": self.sent, "detail": self.detail}


class MonitorTestAlertService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def send(
        self,
        *,
        user_id: UUID,
        monitor_name: str,
        in_plain_words: str,
        channels: list[str],
        strategy_version_id: UUID | None = None,
    ) -> list[ChannelResult]:
        """One message per channel, in the order the person sees them on the board."""

        results: list[ChannelResult] = []
        for channel in dict.fromkeys(channels):
            if channel == DeliveryChannel.WEB.value:
                results.append(
                    await self._web(
                        user_id=user_id,
                        monitor_name=monitor_name,
                        in_plain_words=in_plain_words,
                        strategy_version_id=strategy_version_id,
                    )
                )
            elif channel == DeliveryChannel.EMAIL.value:
                results.append(
                    await self._email(
                        user_id=user_id,
                        monitor_name=monitor_name,
                        in_plain_words=in_plain_words,
                    )
                )
            elif channel == DeliveryChannel.TELEGRAM.value:
                results.append(
                    await self._telegram(
                        user_id=user_id,
                        monitor_name=monitor_name,
                        in_plain_words=in_plain_words,
                    )
                )
            elif channel == DeliveryChannel.WHATSAPP.value:
                results.append(
                    await self._whatsapp(user_id=user_id, monitor_name=monitor_name)
                )
            else:
                results.append(
                    ChannelResult(
                        channel=channel,
                        sent=False,
                        detail="This platform cannot send to that yet.",
                    )
                )
        return results

    # ── In the dashboard ──────────────────────────────────────────────────────

    async def _web(
        self,
        *,
        user_id: UUID,
        monitor_name: str,
        in_plain_words: str,
        strategy_version_id: UUID | None,
    ) -> ChannelResult:
        """A real notice, waiting in the dashboard.

        Written as a ``lifecycle`` alert with a web delivery, which is exactly the shape
        the notice list already reads. A second shape here would be a notice the bell
        never shows.
        """

        now = datetime.now(UTC)
        alert = Alert(
            user_id=user_id,
            strategy_version_id=strategy_version_id,
            alert_type=AlertType.LIFECYCLE,
            deduplication_key=f"monitor-test:{uuid4().hex}",
            title=f"Test: {monitor_name} is now watching",
            body=(
                "This is a test notice, sent because you just switched this monitor on. "
                f"Nothing has happened in the market yet. It watches for: {in_plain_words}"
            ),
            proof_receipt={
                "kind": "monitor_activation_test",
                "monitor": monitor_name,
                "in_plain_words": in_plain_words,
                "sent_at": now.isoformat(),
            },
        )
        self.session.add(alert)
        await self.session.flush()
        self.session.add(
            AlertDelivery(
                alert_id=alert.id,
                channel=DeliveryChannel.WEB,
                destination_key=f"dashboard:{user_id}",
                status=DeliveryStatus.PENDING,
                attempt_count=1,
                last_attempt_at=now,
                accepted_at=now,
            )
        )
        await self.session.flush()
        return ChannelResult(
            channel=DeliveryChannel.WEB.value,
            sent=True,
            detail="A notice is waiting for you in the dashboard.",
        )

    # ── Email ─────────────────────────────────────────────────────────────────

    async def _email(
        self, *, user_id: UUID, monitor_name: str, in_plain_words: str
    ) -> ChannelResult:
        if not email_delivery_available(self.settings):
            return ChannelResult(
                channel=DeliveryChannel.EMAIL.value,
                sent=False,
                detail="Email is not switched on for this platform yet.",
            )
        # The one owner of "which address would a real alert go to". Reading the account
        # row here instead would let a test succeed to an address alerts never reach.
        recipient = await alert_email_address(self.session, user_id)
        if not recipient:
            return ChannelResult(
                channel=DeliveryChannel.EMAIL.value,
                sent=False,
                detail="There is no confirmed email address on this account.",
            )
        rendered = HilalMarketsEmailRenderer(self.settings).monitor_test_alert(
            monitor_name=monitor_name,
            in_plain_words=in_plain_words,
        )
        try:
            await AuthEmailService(self.settings).send_transactional(
                recipient=recipient,
                subject=rendered.subject,
                text_body=rendered.text_body,
                html_body=rendered.html_body,
                # New every time: switching a monitor on twice should arrive twice, or
                # the second one looks like a failure.
                idempotency_key=f"monitor-test-{uuid4()}",
                purpose="connection_test",
                sender_email=ALERT_SENDER_EMAIL,
                sender_name=ALERT_SENDER_NAME,
            )
        except EmailDeliveryError as exc:
            logger.warning("monitor test email failed: %s", exc.code)
            return ChannelResult(
                channel=DeliveryChannel.EMAIL.value,
                sent=False,
                detail="We could not send the email. Nothing about your account changed.",
            )
        return ChannelResult(
            channel=DeliveryChannel.EMAIL.value,
            sent=True,
            detail=f"Sent to {recipient}.",
        )

    # ── Telegram ──────────────────────────────────────────────────────────────

    async def _telegram(
        self, *, user_id: UUID, monitor_name: str, in_plain_words: str
    ) -> ChannelResult:
        if self.settings.telegram_adapter != "http" or self.settings.telegram_bot_token is None:
            return ChannelResult(
                channel=DeliveryChannel.TELEGRAM.value,
                sent=False,
                detail="Telegram is not switched on for this platform yet.",
            )
        connection = await self.session.scalar(
            select(TelegramConnection).where(
                TelegramConnection.user_id == user_id,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
                TelegramConnection.alerts_enabled.is_(True),
                TelegramConnection.chat_id.is_not(None),
            )
        )
        if connection is None or not connection.chat_id:
            return ChannelResult(
                channel=DeliveryChannel.TELEGRAM.value,
                sent=False,
                detail="Telegram is not connected yet. Connect it on the Connections page.",
            )
        base_url = str(self.settings.public_base_url).rstrip("/")
        try:
            await TelegramHttpAdapter(self.settings).deliver(
                TelegramOutboundMessage(
                    chat_id=connection.chat_id,
                    text=(
                        f"Test: {monitor_name} is now watching.\n\n"
                        "This is a test message, sent because you just switched this "
                        "monitor on. Nothing has happened in the market yet.\n\n"
                        f"What it watches for: {in_plain_words}"
                    ),
                    buttons=[
                        TelegramButton(
                            "My monitors",
                            "external:monitors",
                            url=f"{base_url}/dashboard/monitors",
                        )
                    ],
                )
            )
        except TelegramDeliveryError as exc:
            logger.warning("monitor test telegram failed: %s", exc.code)
            return ChannelResult(
                channel=DeliveryChannel.TELEGRAM.value,
                sent=False,
                detail="Telegram did not accept the message. Nothing else changed.",
            )
        return ChannelResult(
            channel=DeliveryChannel.TELEGRAM.value,
            sent=True,
            detail="Sent to your Telegram.",
        )

    # ── WhatsApp ──────────────────────────────────────────────────────────────

    async def _whatsapp(self, *, user_id: UUID, monitor_name: str) -> ChannelResult:
        """Sent through the same service the Connections page's own test uses.

        WhatsApp only accepts a message outside the service window if it matches an
        approved template, so the wording is not ours to choose here. That is why this
        one channel sends the platform's connection-test template rather than the
        monitor's own sentence — and why the row below says so plainly.
        """

        del monitor_name
        if not self.settings.whatsapp_enabled:
            return ChannelResult(
                channel=DeliveryChannel.WHATSAPP.value,
                sent=False,
                detail="WhatsApp is not switched on for this platform yet.",
            )
        from ai_market_monitor.whatsapp.adapter import WhatsAppCloudAdapter
        from ai_market_monitor.whatsapp.service import (
            WhatsAppIntegrationTestService,
            WhatsAppServiceError,
        )

        try:
            await WhatsAppIntegrationTestService(
                self.session,
                self.settings,
                WhatsAppCloudAdapter(self.settings),
            ).send(user_id)
        except WhatsAppServiceError as exc:
            logger.warning("monitor test whatsapp failed: %s", exc.code)
            return ChannelResult(
                channel=DeliveryChannel.WHATSAPP.value,
                sent=False,
                detail="WhatsApp is not connected yet. Connect it on the Connections page.",
            )
        except Exception:  # noqa: BLE001 - one channel failing must not hide the others
            logger.exception("monitor test whatsapp raised")
            return ChannelResult(
                channel=DeliveryChannel.WHATSAPP.value,
                sent=False,
                detail="WhatsApp did not accept the message. Nothing else changed.",
            )
        return ChannelResult(
            channel=DeliveryChannel.WHATSAPP.value,
            sent=True,
            detail="Sent to your WhatsApp.",
        )
