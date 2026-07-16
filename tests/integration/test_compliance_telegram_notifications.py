from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from ai_market_monitor.db.models import TelegramNotificationAttempt
from ai_market_monitor.services.sharia_governance import ShariaAdminTelegramService
from ai_market_monitor.telegram.adapter import (
    TelegramDeliveryError,
    TelegramDeliveryResult,
)
from tests.services.test_sc_malaysia_governance import _ready_case


class SequencedTelegramAdapter:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.messages = []

    async def deliver(self, message):
        self.messages.append(message)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def test_admin_notification_without_configured_recipient_is_not_queued(test_context):
    settings = test_context["settings"]
    settings.sharia_admin_telegram_chat_id = None
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        attempt = await ShariaAdminTelegramService(session, settings).enqueue(
            case,
            notification_type="material_change",
            idempotency_key=f"material-change:{case.id}",
        )
        row_count = await session.scalar(
            select(func.count(TelegramNotificationAttempt.id))
        )

    assert attempt is None
    assert row_count == 0


async def test_admin_notification_retries_timeout_and_rate_limit_without_duplicates(
    test_context,
):
    settings = test_context["settings"]
    settings.sharia_admin_telegram_chat_id = "test-admin-chat"
    settings.telegram_enabled = True
    settings.telegram_adapter = "http"
    settings.telegram_bot_token = "test-only-token"
    adapter = SequencedTelegramAdapter(
        [
            TelegramDeliveryError(
                "telegram_timeout",
                "The test Telegram request timed out.",
                retryable=True,
            ),
            TelegramDeliveryError(
                "telegram_rate_limited",
                "The test Telegram endpoint requested a retry.",
                retryable=True,
                retry_after_seconds=2,
            ),
            TelegramDeliveryResult(message_ids=["admin-message-1"]),
        ]
    )
    async with test_context["session_factory"]() as session:
        case, methodology = await _ready_case(session)
        service = ShariaAdminTelegramService(session, settings, adapter=adapter)
        attempt = await service.enqueue(
            case,
            notification_type="material_change",
            idempotency_key=f"material-change:{case.id}",
        )
        replay = await service.enqueue(
            case,
            notification_type="material_change",
            idempotency_key=f"material-change:{case.id}",
        )
        assert attempt is not None and replay is attempt

        await service.process_due()
        assert attempt.status == "retryable"
        assert attempt.last_error_code == "telegram_timeout"

        attempt.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await service.process_due()
        assert attempt.status == "retryable"
        assert attempt.last_error_code == "telegram_rate_limited"
        assert attempt.next_retry_at is not None

        attempt.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await service.process_due()
        row_count = await session.scalar(
            select(func.count(TelegramNotificationAttempt.id))
        )

    assert row_count == 1
    assert attempt.status == "sent"
    assert attempt.attempt_count == 3
    assert attempt.provider_message_id == "admin-message-1"
    assert len(adapter.messages) == 3
    final_message = adapter.messages[-1]
    assert final_message.chat_id == "test-admin-chat"
    assert f"Methodology: {methodology.name} v{methodology.version}" in final_message.text
    assert "Affected Watch Plans/users: 0/0" in final_message.text
    assert final_message.buttons[0].url is not None
    assert final_message.buttons[0].url.endswith(f"/system-brain/reviews/{case.id}")
