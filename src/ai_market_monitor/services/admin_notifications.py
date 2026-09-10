from contextlib import suppress
from uuid import UUID

from ai_market_monitor.core.config import Settings


class AdminNotificationService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def send_signup_created(
        self,
        *,
        user_id: UUID,
        email: str,
        source: str,
    ) -> None:
        await self.send(f"New signup: {email} user:{user_id} source:{source}")

    async def send_payment_received(
        self,
        *,
        user_id: UUID,
        email: str | None,
        plan_code: str | None,
        provider: str,
        event_type: str,
    ) -> None:
        identity = email or f"user:{user_id}"
        plan = plan_code or "unknown-plan"
        await self.send(f"Payment received: {identity} {plan} via {provider} ({event_type})")

    async def send_plan_change(
        self,
        *,
        user_id: UUID,
        email: str | None,
        kind: str,
        from_plan: str,
        to_plan: str | None,
        timing: str,
        reason: str,
    ) -> None:
        """A customer cancelled, moved up a plan, or moved down a plan.

        Sent for all three because all three are money leaving or changing, and the reason
        is the part nobody can get back afterwards. ``reason`` arrives already turned into
        words by :func:`ai_market_monitor.services.plan_changes.reason_words`, so this does
        not hold a second copy of the list.
        """

        identity = email or f"user:{user_id}"
        moving = f"{from_plan} -> {to_plan}" if to_plan else from_plan
        when = "now" if timing == "immediate" else "at the end of the paid period"
        await self.send(
            f"Plan {kind}: {identity} {moving}, {when}. Reason: {reason}"
        )

    async def send_affiliate_application(
        self,
        *,
        user_id: UUID,
        email: str | None,
        display_name: str,
        requested_code: str,
    ) -> None:
        """Somebody applied to the affiliate programme and is waiting for an answer."""

        identity = email or f"user:{user_id}"
        await self.send(
            f"Affiliate application: {display_name} ({identity}) asked for code "
            f"{requested_code}. Decide it in the System Brain."
        )

    async def send(self, text: str) -> None:
        destination = self.settings.admin_notify_telegram_user_id
        if (
            self.settings.app_env == "test"
            or not destination
            or self.settings.telegram_adapter != "http"
            or self.settings.telegram_bot_token is None
        ):
            return
        from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter
        from ai_market_monitor.telegram.types import TelegramOutboundMessage

        with suppress(TelegramDeliveryError):
            await TelegramHttpAdapter(self.settings).deliver(
                TelegramOutboundMessage(
                    chat_id=destination,
                    text=text[:900],
                )
            )

    async def send_support_ticket(
        self,
        text: str,
        screenshots: list[tuple[str, str, bytes]],
    ) -> None:
        await self.send(text)
        destination = self.settings.admin_notify_telegram_user_id
        if (
            not screenshots
            or self.settings.app_env == "test"
            or not destination
            or self.settings.telegram_adapter != "http"
            or self.settings.telegram_bot_token is None
        ):
            return
        from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter

        adapter = TelegramHttpAdapter(self.settings)
        for filename, content_type, content in screenshots[:3]:
            with suppress(TelegramDeliveryError):
                await adapter.send_photo_bytes(
                    chat_id=destination,
                    filename=filename,
                    content=content,
                    content_type=content_type,
                    caption="Support ticket screenshot",
                )
