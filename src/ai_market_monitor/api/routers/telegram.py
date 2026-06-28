import hashlib
import hmac
import json
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import get_market_data_provider, get_market_previewer
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import TelegramUpdateReceipt
from ai_market_monitor.services.interfaces import MarketDataProvider, RecentMarketPreviewer
from ai_market_monitor.telegram.adapter import (
    TelegramDeliveryError,
    TelegramHttpAdapter,
)
from ai_market_monitor.telegram.providers import DatabaseNearMissProvider
from ai_market_monitor.telegram.service import TelegramBotService
from ai_market_monitor.telegram.types import (
    TelegramButton,
    TelegramCallback,
    TelegramInboundMessage,
    TelegramOutboundMessage,
)

router = APIRouter(prefix="/telegram", tags=["telegram"])


class TelegramAdapterFactory(Protocol):
    def __call__(self, settings: Settings) -> TelegramHttpAdapter: ...


def get_telegram_adapter_factory() -> TelegramAdapterFactory:
    return TelegramHttpAdapter


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    previewer: RecentMarketPreviewer = Depends(get_market_previewer),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    adapter_factory: TelegramAdapterFactory = Depends(get_telegram_adapter_factory),
    telegram_secret: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
) -> dict[str, Any]:
    expected_secret = settings.telegram_webhook_secret
    if (
        expected_secret is None
        or telegram_secret is None
        or not hmac.compare_digest(
            expected_secret.get_secret_value(),
            telegram_secret,
        )
    ):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")
    adapter = adapter_factory(settings)
    body = await request.body()
    try:
        update = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram update") from exc
    return await process_telegram_update(
        update,
        session=session,
        settings=settings,
        previewer=previewer,
        provider=provider,
        adapter=adapter,
        raw_body=body,
    )


async def process_telegram_update(
    update: dict[str, Any],
    *,
    session: AsyncSession,
    settings: Settings,
    previewer: RecentMarketPreviewer,
    adapter: TelegramHttpAdapter,
    provider: MarketDataProvider | None = None,
    raw_body: bytes | None = None,
) -> dict[str, Any]:
    body = raw_body or json.dumps(update, sort_keys=True, separators=(",", ":")).encode()
    update_id = str(update.get("update_id") or "")
    if not update_id:
        raise HTTPException(status_code=400, detail="Telegram update_id is required")

    receipt = await session.scalar(
        select(TelegramUpdateReceipt).where(TelegramUpdateReceipt.update_id == update_id)
    )
    if receipt is not None and receipt.status == "processed":
        return {"ok": True, "replayed": True}

    if receipt is None:
        receipt = TelegramUpdateReceipt(
            update_id=update_id,
            payload_hash=hashlib.sha256(body).hexdigest(),
            status="processing",
            response_payload={},
            provider_message_ids=[],
            created_at=datetime.now(UTC),
        )
        session.add(receipt)
        await session.flush()

    callback_query_id: str | None = None
    if receipt.response_payload:
        outbound = _outbound_from_dict(receipt.response_payload)
        callback_query_id = _callback_query_id(update)
    else:
        try:
            service = TelegramBotService(
                session,
                settings,
                previewer=previewer,
                near_miss_provider=DatabaseNearMissProvider(session),
                market_data_provider=provider,
            )
            processing = _processing_outbound(update)
            if processing is not None:
                with suppress(TelegramDeliveryError):
                    await adapter.deliver(processing)
            callback = _parse_callback(update)
            if callback is not None:
                callback_query_id = callback.callback_query_id
                outbound = await service.handle_callback(callback)
            else:
                message = _parse_message(update)
                if message is None:
                    receipt.status = "ignored"
                    receipt.processed_at = datetime.now(UTC)
                    await session.commit()
                    return {"ok": True, "ignored": True}
                outbound = await service.handle_message(message)
            receipt.response_payload = _outbound_to_dict(outbound)
            receipt.status = "ready"
            await session.commit()
        except Exception as exc:
            receipt.error_code = type(exc).__name__
            receipt.error_detail = "Telegram update processing required user recovery."
            fallback = _fallback_outbound(update)
            if fallback is None:
                receipt.status = "failed_permanent"
                receipt.processed_at = datetime.now(UTC)
                await session.commit()
                return {"ok": True, "accepted": False}
            outbound = fallback
            receipt.response_payload = _outbound_to_dict(outbound)
            receipt.status = "ready"
            await session.commit()

    try:
        if callback_query_id:
            await adapter.answer_callback(callback_query_id)
        delivery = await adapter.deliver(outbound)
    except TelegramDeliveryError as exc:
        receipt.status = "failed_retryable" if exc.retryable else "failed_permanent"
        receipt.error_code = exc.code
        receipt.error_detail = str(exc)[:500]
        await session.commit()
        if exc.retryable:
            raise HTTPException(status_code=503, detail="Telegram delivery retry required") from exc
        return {"ok": True, "delivered": False}

    receipt.status = "processed"
    receipt.provider_message_ids = delivery.message_ids
    receipt.error_code = None
    receipt.error_detail = None
    receipt.processed_at = datetime.now(UTC)
    await session.commit()
    return {"ok": True, "replayed": False, "message_ids": delivery.message_ids}


def _parse_message(update: dict[str, Any]) -> TelegramInboundMessage | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    text = message.get("text")
    if sender.get("id") is None or chat.get("id") is None or not isinstance(text, str):
        return None
    return TelegramInboundMessage(
        telegram_user_id=str(sender["id"]),
        chat_id=str(chat["id"]),
        text=text,
        username=sender.get("username"),
        message_id=str(message["message_id"]) if message.get("message_id") is not None else None,
        created_at=datetime.fromtimestamp(message["date"], tz=UTC) if message.get("date") else None,
    )


def _parse_callback(update: dict[str, Any]) -> TelegramCallback | None:
    callback = update.get("callback_query")
    if not isinstance(callback, dict):
        return None
    sender = callback.get("from") or {}
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    if (
        callback.get("id") is None
        or sender.get("id") is None
        or chat.get("id") is None
        or not isinstance(callback.get("data"), str)
    ):
        return None
    return TelegramCallback(
        callback_query_id=str(callback["id"]),
        telegram_user_id=str(sender["id"]),
        chat_id=str(chat["id"]),
        data=callback["data"],
        message_id=str(message["message_id"]) if message.get("message_id") is not None else None,
        created_at=datetime.fromtimestamp(message["date"], tz=UTC) if message.get("date") else None,
    )


def _callback_query_id(update: dict[str, Any]) -> str | None:
    callback = update.get("callback_query")
    if isinstance(callback, dict) and callback.get("id") is not None:
        return str(callback["id"])
    return None


def _processing_outbound(update: dict[str, Any]) -> TelegramOutboundMessage | None:
    callback = _parse_callback(update)
    if callback is not None:
        text = None
        if callback.data.startswith(("template:", "scan_template:")):
            text = "Processing template..."
        elif callback.data in {
            "approve_strategy",
            "activate_strategy",
            "cancel",
            "save_draft",
            "explain_rule",
        }:
            text = "Processing..."
        elif callback.data.startswith("billing:checkout:"):
            text = "Creating secure payment link..."
        if text is None:
            return None
        return TelegramOutboundMessage(chat_id=callback.chat_id, text=text, menu=[])

    message = _parse_message(update)
    if message is None:
        return None
    normalized = message.text.strip()
    if normalized.startswith("/"):
        return None
    if normalized in {
        "Approve",
        "Confirm",
        "Confirm Alert",
        "Confirm Explanation",
        "I Understand - Activate",
    }:
        return TelegramOutboundMessage(chat_id=message.chat_id, text="Processing...", menu=[])
    if len(normalized) >= 40:
        return TelegramOutboundMessage(
            chat_id=message.chat_id,
            text="Processing your setup...",
            menu=[],
        )
    return None


def _fallback_outbound(update: dict[str, Any]) -> TelegramOutboundMessage | None:
    callback = _parse_callback(update)
    if callback is not None:
        return TelegramOutboundMessage(
            chat_id=callback.chat_id,
            text=(
                "Action needed: I could not complete that Telegram action. "
                "Please choose a relevant option below."
            ),
            buttons=[
                TelegramButton("Create Monitor", "create_monitor"),
                TelegramButton("Sign up / sign in", "account:auth"),
            ],
            menu=[
                "Create Monitor",
                "My Monitors",
                "Lifecycles",
                "Subscription",
                "Support",
            ],
        )
    message = _parse_message(update)
    if message is None:
        return None
    return TelegramOutboundMessage(
        chat_id=message.chat_id,
        text=(
            "Action needed: I could not finish that Telegram step. "
            "Use the current app-menu option again, or go back and choose the relevant screen."
        ),
        buttons=[
            TelegramButton("Create Monitor", "create_monitor"),
            TelegramButton("Use Template", "mode_template"),
            TelegramButton("Go Back", "back:previous"),
        ],
        menu=[
            "Create Monitor",
            "Use Template",
            "Go Back",
        ],
    )


def _outbound_to_dict(message: TelegramOutboundMessage) -> dict[str, Any]:
    return {
        "chat_id": message.chat_id,
        "text": message.text,
        "buttons": [
            {"text": button.text, "callback_data": button.callback_data, "url": button.url}
            for button in message.buttons
        ],
        "menu": message.menu,
        "parse_mode": message.parse_mode,
        "correlation_id": message.correlation_id,
        "edit_message_id": message.edit_message_id,
    }


def _outbound_from_dict(payload: dict[str, Any]) -> TelegramOutboundMessage:
    return TelegramOutboundMessage(
        chat_id=str(payload["chat_id"]),
        text=str(payload["text"]),
        buttons=[
            TelegramButton(
                text=str(button["text"]),
                callback_data=str(button["callback_data"]),
                url=button.get("url"),
            )
            for button in payload.get("buttons", [])
        ],
        menu=[str(item) for item in payload.get("menu", [])],
        parse_mode=payload.get("parse_mode"),
        correlation_id=payload.get("correlation_id"),
        edit_message_id=payload.get("edit_message_id"),
    )
