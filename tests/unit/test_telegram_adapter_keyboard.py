"""What the Telegram adapter actually puts on the wire, and how an answer is stored.

Two defect classes live here.

**The keyboard a message asks for.** A button with no link used to be decided by the
adapter, not by the message: every link-less button set became a *reply* keyboard, which
sits above the composer and keeps offering the same choices long after the question has
been answered. The connect prompt was such a question, and the answer the person pressed
stayed on screen beside it. A message now says which keyboard it wants; when it says
nothing, the old rule still applies, so no other screen changes.

**One writer for a stored answer.** A delivered answer is kept so a replayed update can be
answered identically without asking the database again. Three files used to write that
shape by hand, each knowing a slightly different set of fields, so a field added to the
dataclass was silently dropped on the way into storage and missing on the way out. The
dataclass owns the shape now, and the field-by-field round trip below is what makes
forgetting a field a failure rather than a surprise in production.
"""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.telegram.adapter import TelegramHttpAdapter
from ai_market_monitor.telegram.types import (
    KEYBOARD_INLINE,
    KEYBOARD_REMOVE_REPLY_KEYBOARD,
    TelegramButton,
    TelegramOutboundMessage,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="telegram-adapter-test-secret-at-least-thirty-two-characters",
        telegram_bot_token=SecretStr("server-only-bot-token"),
    )


class _Captured:
    """The method Telegram was called with, and the JSON body it was sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((method, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 501}})

    @property
    def method(self) -> str:
        return self.calls[-1][0]

    @property
    def payload(self) -> dict:
        return self.calls[-1][1]


def _adapter(captured: _Captured) -> TelegramHttpAdapter:
    return TelegramHttpAdapter(_settings(), transport=httpx.MockTransport(captured.handler))


#: The exact shape the connect prompt must reach Telegram as.
PROMPT_MARKUP = {
    "inline_keyboard": [
        [{"text": "Confirm", "callback_data": "telegram_link:confirm"}],
        [{"text": "Cancel", "callback_data": "telegram_link:cancel"}],
    ]
}


def _prompt(**changes) -> TelegramOutboundMessage:
    message = TelegramOutboundMessage(
        chat_id="chat-1",
        text="Connect this Telegram account to owner@example.com?",
        buttons=[
            TelegramButton("Confirm", "telegram_link:confirm"),
            TelegramButton("Cancel", "telegram_link:cancel"),
        ],
        menu=[],
        keyboard=KEYBOARD_INLINE,
    )
    return dataclasses.replace(message, **changes) if changes else message


async def test_a_prompt_asked_inline_reaches_telegram_as_an_inline_keyboard() -> None:
    """R1: the two buttons are attached to the message, not pinned above the composer."""

    captured = _Captured()
    result = await _adapter(captured).deliver(_prompt())

    assert captured.method == "sendMessage"
    assert captured.payload["reply_markup"] == PROMPT_MARKUP
    assert "keyboard" not in captured.payload["reply_markup"], (
        "the prompt was sent as a persistent reply keyboard, so its buttons never left "
        "the screen and every later message looked like the same question"
    )
    assert captured.payload["chat_id"] == "chat-1"
    assert result.message_ids == ["501"]


async def test_an_edit_replaces_the_prompt_and_removes_its_buttons() -> None:
    """R2: the answer is an ``editMessageText`` whose keyboard holds no callbacks."""

    captured = _Captured()
    await _adapter(captured).deliver(_prompt(edit_message_id="501", buttons=[]))

    assert captured.method == "editMessageText"
    assert captured.payload["message_id"] == "501"
    assert captured.payload["reply_markup"] == {"inline_keyboard": []}, (
        "an answer that names no buttons must clear the old ones, or the person can "
        "confirm a question that has already been answered"
    )


async def test_an_edit_can_carry_a_link_as_its_next_step() -> None:
    captured = _Captured()
    await _adapter(captured).deliver(
        _prompt(
            edit_message_id="501",
            text="✅ Telegram connected",
            buttons=[TelegramButton("Connections page", "external:x", url="https://d/connections")],
        )
    )

    assert captured.method == "editMessageText"
    assert captured.payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "Connections page", "url": "https://d/connections"}]]
    }


async def test_an_edit_never_sends_a_reply_keyboard() -> None:
    """D-H: ``editMessageText`` accepts an inline keyboard and nothing else.

    A message that asks to be edited without saying how its buttons are attached used to
    be sent as a reply keyboard, which Telegram refused with a 400 — so the answer never
    arrived at all. The link-less buttons now go inline, which is the only place an edit
    can carry a choice, and the app menu — which has no inline form — is left off.
    """

    captured = _Captured()
    await _adapter(captured).deliver(
        TelegramOutboundMessage(
            chat_id="chat-1",
            text="Telegram connected",
            buttons=[
                TelegramButton("Dashboard", "back:main"),
                TelegramButton("My Monitors", "menu:my_monitors"),
            ],
            menu=["📋 My Monitors", "🔄 Lifecycles"],
            edit_message_id="501",
        )
    )

    assert captured.method == "editMessageText"
    markup = captured.payload["reply_markup"]
    assert "keyboard" not in markup and "is_persistent" not in markup
    assert markup == {
        "inline_keyboard": [
            [{"text": "Dashboard", "callback_data": "back:main"}],
            [{"text": "My Monitors", "callback_data": "menu:my_monitors"}],
        ]
    }


async def test_a_message_that_clears_the_keyboard_says_so_to_telegram() -> None:
    """WP2: an answer to a typed reply has nothing to edit, so the keys are taken away."""

    captured = _Captured()
    await _adapter(captured).deliver(
        TelegramOutboundMessage(
            chat_id="chat-1",
            text="✅ Telegram connected",
            keyboard=KEYBOARD_REMOVE_REPLY_KEYBOARD,
        )
    )

    assert captured.method == "sendMessage"
    assert captured.payload["reply_markup"] == {"remove_keyboard": True}


async def test_a_keyboard_cannot_be_cleared_and_carry_buttons_at_once() -> None:
    """Telegram allows one interface per message; asking for two is a programming error."""

    captured = _Captured()
    with pytest.raises(ValueError, match="one interface per message"):
        await _adapter(captured).deliver(
            _prompt(keyboard=KEYBOARD_REMOVE_REPLY_KEYBOARD)
        )
    assert captured.calls == [], "the message was sent even though it asked for two things"


async def test_every_other_message_keeps_the_keyboard_it_kept_before() -> None:
    """No other screen changed: what these did before the field existed, they still do."""

    menu_only = _Captured()
    await _adapter(menu_only).deliver(
        TelegramOutboundMessage(
            chat_id="c", text="Main Menu", menu=["📋 My Monitors", "🔄 Lifecycles"]
        )
    )
    assert menu_only.method == "sendMessage"
    assert menu_only.payload["reply_markup"] == {
        "keyboard": [[{"text": "📋 My Monitors"}, {"text": "🔄 Lifecycles"}]],
        "resize_keyboard": True,
        "is_persistent": True,
    }

    link_only = _Captured()
    await _adapter(link_only).deliver(
        TelegramOutboundMessage(
            chat_id="c",
            text="Open the dashboard",
            buttons=[TelegramButton("Dashboard", "external:d", url="https://d/dashboard")],
        )
    )
    assert link_only.payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "Dashboard", "url": "https://d/dashboard"}]]
    }

    typed_keys = _Captured()
    await _adapter(typed_keys).deliver(
        TelegramOutboundMessage(
            chat_id="c",
            text="Choose",
            buttons=[TelegramButton("Claim Trial", "account:signup")],
            menu=["Trial"],
        )
    )
    assert typed_keys.payload["reply_markup"] == {
        "keyboard": [[{"text": "Claim Trial"}, {"text": "Trial"}]],
        "resize_keyboard": True,
        "is_persistent": True,
    }


# ---------------------------------------------------------------------------
# R9 — the one owner for storing and reading back an answer
# ---------------------------------------------------------------------------


def _every_field_set() -> TelegramOutboundMessage:
    """A message with a distinct value in every field the dataclass has."""

    return TelegramOutboundMessage(
        chat_id="chat-round-trip",
        text="Connect this Telegram account to owner@example.com?",
        buttons=[
            TelegramButton("Confirm", "telegram_link:confirm"),
            TelegramButton("Cancel", "telegram_link:cancel", url="https://example/cancel"),
        ],
        menu=["📋 My Monitors"],
        parse_mode="HTML",
        correlation_id="corr-round-trip",
        edit_message_id="900",
        keyboard=KEYBOARD_INLINE,
    )


def test_outbound_message_round_trips_every_field() -> None:
    """Every field is stored and read back, or this test names the one that is not."""

    message = _every_field_set()
    payload = message.to_payload()
    field_names = {field.name for field in dataclasses.fields(TelegramOutboundMessage)}

    assert set(payload) == field_names, (
        "to_payload no longer matches the dataclass: "
        f"missing {sorted(field_names - set(payload))}, "
        f"extra {sorted(set(payload) - field_names)}"
    )
    assert TelegramOutboundMessage.from_payload(payload) == message


def test_a_button_round_trips_every_field() -> None:
    button = TelegramButton("Open dashboard", "external:d", url="https://d/dashboard")
    payload = button.to_payload()

    assert set(payload) == {field.name for field in dataclasses.fields(TelegramButton)}
    assert TelegramButton.from_payload(payload) == button


def test_the_stored_shape_survives_json() -> None:
    """The receipts store a JSON column, so the payload has to be plain JSON values."""

    message = _every_field_set()
    payload = json.loads(json.dumps(message.to_payload()))

    assert TelegramOutboundMessage.from_payload(payload) == message


def test_a_payload_written_before_a_field_existed_still_reads_back() -> None:
    """Answers stored by the previous version of this code are still in the receipts.

    A missing key is not an error, and the field keeps the default the dataclass says.
    """

    stored = {
        "chat_id": "chat-old",
        "text": "Old answer",
        "buttons": [{"text": "Dashboard", "callback_data": "open_dashboard"}],
        "menu": [],
        "parse_mode": None,
        "correlation_id": None,
        "edit_message_id": None,
    }
    message = TelegramOutboundMessage.from_payload(stored)

    assert message.buttons == [TelegramButton("Dashboard", "open_dashboard")]
    assert message.keyboard is None


def test_the_webhook_router_and_the_bot_service_share_one_serialiser() -> None:
    """D-G: three hand-written copies of one shape is how fields went missing.

    The router and the service are not allowed their own idea of what a stored answer
    looks like, so both are checked against the dataclass rather than against each other.
    """

    from ai_market_monitor.api.routers import telegram as telegram_router
    from ai_market_monitor.telegram.service import TelegramBotService

    message = _every_field_set()

    assert telegram_router._outbound_to_dict(message) == message.to_payload()
    assert telegram_router._outbound_from_dict(message.to_payload()) == message
    assert TelegramBotService._outbound_from_payload(message.to_payload()) == message
