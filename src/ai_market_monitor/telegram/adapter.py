from dataclasses import dataclass
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.provider_runtime import provider_request
from ai_market_monitor.telegram.types import (
    KEYBOARD_INLINE,
    KEYBOARD_REMOVE_REPLY_KEYBOARD,
    TelegramOutboundMessage,
)

#: What the connection pool is keyed by. The bot token lives in the path, never in the
#: pool key, the provider label or any log line.
_TELEGRAM_ORIGIN = "https://api.telegram.org"

#: Telegram methods that only read. Everything else changes something on Telegram's side —
#: a message posted, a keyboard replaced, a webhook moved — and must not be repeated.
_TELEGRAM_READ_ONLY_METHODS: frozenset[str] = frozenset(
    {"getMe", "getUpdates", "getChat", "getWebhookInfo", "getFile", "getChatMember"}
)


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
        self.settings = settings
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
        markup = self._reply_markup(message, editing=message.edit_message_id is not None)
        if markup is not None:
            payload["reply_markup"] = markup

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

    def _reply_markup(
        self, message: TelegramOutboundMessage, *, editing: bool
    ) -> dict[str, Any] | None:
        """The keyboard this message asked for, or ``None`` for no keyboard at all.

        The message says which keyboard it wants. When it says nothing, the answer is what
        this adapter has always done, so no other screen changes shape: a link is an
        inline button, buttons without a link become the app menu, and a bare ``menu`` is
        the persistent keyboard underneath the composer.

        ``editing`` carries the one rule Telegram's own API imposes and this adapter used
        to ignore: ``editMessageText`` accepts an inline keyboard and nothing else. A
        button that is not a link therefore becomes an inline button when the message is
        an edit — the choice stays usable — and the app menu, which has no inline form, is
        left off. Nothing in the product edits a message without saying so today, so this
        only governs the messages that ask to be edited.
        """

        if message.keyboard == KEYBOARD_INLINE:
            return self._inline_markup(message.buttons)
        if message.keyboard == KEYBOARD_REMOVE_REPLY_KEYBOARD:
            if message.buttons:
                raise ValueError(
                    "A Telegram message that removes the reply keyboard cannot also carry "
                    "buttons: Telegram accepts one interface per message. Put the next step "
                    "in the text, or send the buttons on an inline keyboard instead."
                )
            # An edit cannot remove a keyboard it cannot address: clearing the inline
            # buttons is the only thing an edit interface can do here.
            return {"inline_keyboard": []} if editing else {"remove_keyboard": True}
        if message.buttons:
            has_link = any(button.url for button in message.buttons)
            if has_link or editing:
                return self._inline_markup(message.buttons)
            menu = [button.text for button in message.buttons]
            for item in message.menu:
                if item not in menu:
                    menu.append(item)
            return self._menu_markup(menu)
        if message.menu and not editing:
            return self._menu_markup(message.menu)
        return None

    @staticmethod
    def _inline_markup(buttons: list[Any]) -> dict[str, Any]:
        """One row per button. An empty list is Telegram's way of saying "no buttons"."""

        return {
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
                for button in buttons
            ]
        }

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

    async def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        """The command list Telegram shows when somebody types ``/`` in the chat."""

        await self._call("setMyCommands", {"commands": commands})

    async def set_my_description(self, description: str) -> None:
        """The text in an empty chat, above the Start button."""

        await self._call("setMyDescription", {"description": description})

    async def set_my_short_description(self, short_description: str) -> None:
        """The line on the bot's profile page and in shared links."""

        await self._call("setMyShortDescription", {"short_description": short_description})

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
        response = await provider_request(
            self.settings,
            "POST",
            f"{self.base_url}/sendPhoto",
            provider="telegram",
            operation="sendPhoto",
            base_url=_TELEGRAM_ORIGIN,
            timeout=30,
            # A photo that was delivered and then timed out looks exactly like one that
            # was never delivered. Sending it again would post it twice.
            mutation_committed=True,
            transport=self.transport,
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
        response = await provider_request(
            self.settings,
            "POST",
            f"{self.base_url}/{method}",
            provider="telegram",
            operation=method,
            base_url=_TELEGRAM_ORIGIN,
            timeout=15,
            # Anything not on the read-only list is assumed to have changed something on
            # Telegram's side, so it is never retried. The safe default is the strict one:
            # a new method added later must be named here before it can be repeated.
            mutation_committed=method not in _TELEGRAM_READ_ONLY_METHODS,
            transport=self.transport,
            json=payload,
        )
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
