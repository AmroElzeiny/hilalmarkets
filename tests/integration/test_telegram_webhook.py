from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.api.routers.telegram import get_telegram_adapter_factory
from ai_market_monitor.db.models import TelegramUpdateReceipt
from ai_market_monitor.telegram.adapter import TelegramDeliveryResult


class FakeTelegramAdapter:
    def __init__(self):
        self.deliveries = 0
        self.callbacks = 0

    async def deliver(self, message):
        self.deliveries += 1
        return TelegramDeliveryResult(message_ids=[f"sent-{self.deliveries}"])

    async def answer_callback(self, callback_query_id: str, *, text: str | None = None):
        self.callbacks += 1


async def test_telegram_webhook_validates_secret_and_deduplicates_update(test_context):
    settings = test_context["settings"]
    settings.telegram_webhook_secret = SecretStr("telegram-webhook-secret")
    settings.telegram_bot_token = SecretStr("telegram-token")
    adapter = FakeTelegramAdapter()
    test_context["app"].dependency_overrides[get_telegram_adapter_factory] = lambda: (
        lambda _settings: adapter
    )
    update = {
        "update_id": 1001,
        "message": {
            "message_id": 10,
            "date": 1781438400,
            "text": "/start src_launch",
            "from": {"id": 12345, "username": "trader"},
            "chat": {"id": 12345},
        },
    }
    unauthorized = await test_context["client"].post(
        "/api/v1/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert unauthorized.status_code == 401

    headers = {"X-Telegram-Bot-Api-Secret-Token": "telegram-webhook-secret"}
    first = await test_context["client"].post(
        "/api/v1/telegram/webhook", json=update, headers=headers
    )
    second = await test_context["client"].post(
        "/api/v1/telegram/webhook", json=update, headers=headers
    )
    assert first.status_code == 200
    assert second.json()["replayed"] is True
    assert adapter.deliveries == 1
    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(TelegramUpdateReceipt.id))) == 1
