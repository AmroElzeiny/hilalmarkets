"""R8 — a database error inside a Telegram handler must still answer the person.

``process_telegram_update`` is what both the webhook and the polling worker call. When a
handler raised after a failed flush, the recovery branch wrote to the update receipt and
committed without rolling the session back first. SQLAlchemy refuses that with
``PendingRollbackError``, so the recovery itself raised: the webhook answered HTTP 500,
Telegram delivered the same update again, and it failed the same way again. The person
got nothing at all, and the poller re-fetched the update on every tick.

Every row here breaks a handler on purpose with a real database error, through both kinds
of update (a typed message and a button tap), both before and after the handler's own
first commit — the two states the session can be in when the error arrives.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from ai_market_monitor.api.routers.telegram import process_telegram_update
from ai_market_monitor.db.models import TelegramConversationState, TelegramUpdateReceipt
from ai_market_monitor.telegram.adapter import TelegramDeliveryResult
from ai_market_monitor.telegram.service import TelegramBotService
from ai_market_monitor.telegram.types import TelegramOutboundMessage

FALLBACK_TEXT = "Action needed: I could not"


class _Previewer:
    async def run(self, strategy):  # pragma: no cover - never reached
        raise AssertionError("no preview in this test")


class _RecordingAdapter:
    def __init__(self) -> None:
        self.sent: list[TelegramOutboundMessage] = []
        self.answered: list[str] = []

    async def deliver(self, message: TelegramOutboundMessage) -> TelegramDeliveryResult:
        self.sent.append(message)
        return TelegramDeliveryResult(message_ids=[f"msg-{len(self.sent)}"])

    async def answer_callback(self, callback_query_id: str, *, text: str | None = None) -> None:
        self.answered.append(callback_query_id)


def _message_update(update_id: int) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 7,
            "date": 1781438400,
            "text": "My Monitors",
            "from": {"id": "tg-recovery", "username": "recovery"},
            "chat": {"id": "chat-recovery"},
        },
    }


def _callback_update(update_id: int) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-recovery-{update_id}",
            "from": {"id": "tg-recovery", "username": "recovery"},
            "message": {"message_id": 7, "date": 1781438400, "chat": {"id": "chat-recovery"}},
            "data": "menu:my_monitors",
        },
    }


def _breaking_handler(*, commit_first: bool):
    """A handler that ends in a real IntegrityError at flush.

    ``chat_id`` is NOT NULL on the conversation table, so the flush fails in the database
    itself, exactly like a uniqueness or constraint error would in production.
    """

    async def handler(self: TelegramBotService, _incoming: object) -> TelegramOutboundMessage:
        if commit_first:
            await self.session.commit()
        self.session.add(
            TelegramConversationState(
                telegram_user_id="tg-broken-row",
                chat_id=None,  # type: ignore[arg-type]
                flow="main_menu",
                step="idle",
                state_data={},
                correlation_id="broken",
            )
        )
        await self.session.flush()
        raise AssertionError("the flush above must have raised")  # pragma: no cover

    return handler


@pytest.mark.parametrize("kind", ["message", "callback"])
@pytest.mark.parametrize("commit_first", [False, True], ids=["before_commit", "after_commit"])
async def test_a_database_error_in_a_handler_is_answered_once_and_recorded(
    test_context: dict, monkeypatch: pytest.MonkeyPatch, kind: str, commit_first: bool
) -> None:
    handler_name = "handle_message" if kind == "message" else "handle_callback"
    monkeypatch.setattr(
        TelegramBotService, handler_name, _breaking_handler(commit_first=commit_first)
    )
    update = _message_update(9001) if kind == "message" else _callback_update(9002)
    adapter = _RecordingAdapter()

    async with test_context["session_factory"]() as session:
        result = await process_telegram_update(
            update,
            session=session,
            settings=test_context["settings"],
            previewer=_Previewer(),
            adapter=adapter,
        )

        assert result.get("ok") is True, result
        assert result.get("replayed") is False, result
        fallbacks = [message for message in adapter.sent if FALLBACK_TEXT in message.text]
        assert len(fallbacks) == 1, [message.text for message in adapter.sent]

        receipt = await session.scalar(
            select(TelegramUpdateReceipt).where(
                TelegramUpdateReceipt.update_id == str(update["update_id"])
            )
        )
        assert receipt is not None, "the failed update left no receipt, so it would run again"
        assert receipt.status == "processed", receipt.status
        assert receipt.processed_at is not None

        # Telegram redelivering the same update must not run the broken handler again.
        replay = await process_telegram_update(
            update,
            session=session,
            settings=test_context["settings"],
            previewer=_Previewer(),
            adapter=adapter,
        )
        assert replay == {"ok": True, "replayed": True}
        assert len([m for m in adapter.sent if FALLBACK_TEXT in m.text]) == 1
