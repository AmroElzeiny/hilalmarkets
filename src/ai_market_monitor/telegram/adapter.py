from dataclasses import dataclass
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.telegram.types import TelegramOutboundMessage


class TelegramDeliveryError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class TelegramDeliveryResult:
    message_ids: list[str]


class TelegramHttpAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        token = settings.telegram_bot_token
        if token is None:
            raise TelegramDeliveryError(
                "telegram_token_missing",
                "Telegram bot token is not configured.",
                retryable=False,
            )
        self.base_url = f"https://api.telegram.org/bot{token.get_secret_value()}"
        self.transport = transport

    async def deliver(self, message: TelegramOutboundMessage) -> TelegramDeliveryResult:
        message_ids: list[str] = []
        payload = {
            "chat_id": message.chat_id,
            "text": message.text,
            "disable_web_page_preview": True,
        }
        if message.parse_mode:
            payload["parse_mode"] = message.parse_mode
        if message.buttons:
            url_buttons = [button for button in message.buttons if button.url]
            action_buttons = [button for button in message.buttons if not button.url]
            if url_buttons and action_buttons:
                payload["reply_markup"] = {
                    "inline_keyboard": [
                        [
                            {
                                "text": button.text,
                                **(
                                    {"url": button.url}
                                    if button.url
                                    else {"callback_data": button.callback_data}
                                ),
                            }
                        ]
                        for button in message.buttons
                    ]
                }
            elif url_buttons:
                payload["reply_markup"] = {
                    "inline_keyboard": [
                        [{"text": button.text, "url": button.url}] for button in url_buttons
                    ]
                }
            else:
                menu = [button.text for button in action_buttons]
                for item in message.menu:
                    if item not in menu:
                        menu.append(item)
                payload["reply_markup"] = self._menu_markup(menu)
        elif message.menu:
            payload["reply_markup"] = {
                **self._menu_markup(message.menu),
            }

        if message.edit_message_id:
            edit_payload = {**payload, "message_id": message.edit_message_id}
            try:
                response = await self._call("editMessageText", edit_payload)
            except TelegramDeliveryError as exc:
                if exc.code not in {
                    "message_not_modified",
                    "message_cannot_be_edited",
                    "message_to_edit_not_found",
                }:
                    raise
                response = await self._call("sendMessage", payload)
        else:
            response = await self._call("sendMessage", payload)
        message_id = self._message_id(response)
        if message_id:
            message_ids.append(message_id)

        return TelegramDeliveryResult(message_ids=message_ids)

    async def answer_callback(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        await self._call("answerCallbackQuery", payload)

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        limit: int = 20,
        timeout: int = 0,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "limit": limit,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload)
        return result if isinstance(result, list) else []

    async def get_webhook_info(self) -> dict[str, Any]:
        result = await self._call("getWebhookInfo", {})
        return result if isinstance(result, dict) else {}

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        await self._call(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
        )

    async def send_photo(
        self,
        *,
        chat_id: str,
        photo_url: str,
        caption: str | None = None,
    ) -> TelegramDeliveryResult:
        payload: dict[str, Any] = {"chat_id": chat_id, "photo": photo_url}
        if caption:
            payload["caption"] = caption[:1024]
        response = await self._call("sendPhoto", payload)
        message_id = self._message_id(response)
        return TelegramDeliveryResult(message_ids=[message_id] if message_id else [])

    async def send_photo_bytes(
        self,
        *,
        chat_id: str,
        filename: str,
        content: bytes,
        content_type: str,
        caption: str | None = None,
    ) -> TelegramDeliveryResult:
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30,
            transport=self.transport,
        ) as client:
            response = await client.post(
                "/sendPhoto",
                data=data,
                files={"photo": (filename, content, content_type)},
            )
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_error or body.get("ok") is not True:
            description = str(body.get("description") or response.reason_phrase)
            raise TelegramDeliveryError(
                "telegram_photo_error",
                description,
                retryable=response.status_code == 429 or response.status_code >= 500,
                retry_after_seconds=(body.get("parameters") or {}).get("retry_after"),
            )
        message_id = self._message_id(body.get("result"))
        return TelegramDeliveryResult(message_ids=[message_id] if message_id else [])

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=15,
            transport=self.transport,
        ) as client:
            response = await client.post(f"/{method}", json=payload)
        body: dict[str, Any]
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_error or body.get("ok") is not True:
            parameters = body.get("parameters") or {}
            description = str(body.get("description") or response.reason_phrase)
            normalized = description.casefold()
            code = "telegram_api_error"
            if "message is not modified" in normalized:
                code = "message_not_modified"
            elif "message can't be edited" in normalized:
                code = "message_cannot_be_edited"
            elif "message to edit not found" in normalized:
                code = "message_to_edit_not_found"
            elif "can't use getupdates" in normalized or "webhook is active" in normalized:
                code = "telegram_webhook_active"
            elif response.status_code in {401, 403}:
                code = "telegram_forbidden"
            raise TelegramDeliveryError(
                code,
                description,
                retryable=response.status_code == 429 or response.status_code >= 500,
                retry_after_seconds=parameters.get("retry_after"),
            )
        return body.get("result")

    @staticmethod
    def _menu_markup(menu: list[str]) -> dict[str, Any]:
        rows = [menu[index : index + 2] for index in range(0, len(menu), 2)]
        return {
            "keyboard": [[{"text": item} for item in row] for row in rows],
            "resize_keyboard": True,
            "is_persistent": True,
        }

    @staticmethod
    def _message_id(result: Any) -> str | None:
        if isinstance(result, dict) and result.get("message_id") is not None:
            return str(result["message_id"])
        return None
