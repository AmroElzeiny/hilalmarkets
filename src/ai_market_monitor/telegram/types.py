from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

#: A message asks for its buttons with a word, rather than by which buttons it happens to
#: carry. Absent means "as this screen has always done it", which is what keeps every other
#: bot screen rendering exactly as it does today.
#:
#: ``inline`` attaches the buttons to the message itself: one row per button, each one
#: either a link out or a callback back to the bot. It is what a question with a single
#: correct answer needs — a reply keyboard stays above the keyboard, so it keeps asking its
#: question from every later message. An empty button list under ``inline`` is how a
#: message says "the choices on this message are finished": Telegram accepts an empty
#: inline keyboard on ``editMessageText``, and nothing else.
#:
#: ``remove_reply_keyboard`` answers a keyboard a person no longer needs — the persistent
#: one the connect prompt used to leave behind. Telegram allows only one interface per
#: message, so a message that clears the keyboard cannot also carry buttons: its next step
#: has to be written into the text.
KEYBOARD_INLINE: Final[str] = "inline"
KEYBOARD_REMOVE_REPLY_KEYBOARD: Final[str] = "remove_reply_keyboard"


@dataclass(frozen=True, slots=True)
class TelegramButton:
    text: str
    callback_data: str
    url: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """One owner for storing a button. See :class:`TelegramOutboundMessage`."""

        return {
            "text": self.text,
            "callback_data": self.callback_data,
            "url": self.url,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TelegramButton":
        return cls(
            text=str(payload["text"]),
            callback_data=str(payload.get("callback_data") or ""),
            url=payload.get("url"),
        )


@dataclass(frozen=True, slots=True)
class TelegramOutboundMessage:
    chat_id: str
    text: str
    buttons: list[TelegramButton] = field(default_factory=list)
    menu: list[str] = field(default_factory=list)
    parse_mode: str | None = None
    correlation_id: str | None = None
    edit_message_id: str | None = None
    keyboard: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Store this message so it can be sent later, by another process, or both.

        A delivered update is answered once, and the answer is kept in two places — the
        webhook receipt and the callback receipt — so the same update can be replayed
        without asking the model or the database again. Until now the shape was written
        out by hand in three files, and each copy knew a different set of fields: a field
        added here was quietly dropped on the way to the replay, so the replayed message
        was not the message the person was first answered. This is the only writer, and
        :meth:`from_payload` the only reader; a new field belongs in both, and
        ``test_outbound_message_round_trips_every_field`` fails if it is in only one.
        """

        return {
            "chat_id": self.chat_id,
            "text": self.text,
            "buttons": [button.to_payload() for button in self.buttons],
            "menu": list(self.menu),
            "parse_mode": self.parse_mode,
            "correlation_id": self.correlation_id,
            "edit_message_id": self.edit_message_id,
            "keyboard": self.keyboard,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TelegramOutboundMessage":
        return cls(
            chat_id=str(payload["chat_id"]),
            text=str(payload["text"]),
            buttons=[TelegramButton.from_payload(button) for button in payload.get("buttons", [])],
            menu=[str(item) for item in payload.get("menu", [])],
            parse_mode=payload.get("parse_mode"),
            correlation_id=payload.get("correlation_id"),
            edit_message_id=payload.get("edit_message_id"),
            keyboard=payload.get("keyboard"),
        )


@dataclass(frozen=True, slots=True)
class TelegramInboundMessage:
    telegram_user_id: str
    chat_id: str
    text: str
    username: str | None = None
    message_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TelegramCallback:
    callback_query_id: str
    telegram_user_id: str
    chat_id: str
    data: str
    message_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NearMissListItem:
    symbol: str
    exchange: str
    timeframe: str
    score: float
    trend: str
    passed: list[str]
    missing: list[str]
    chart_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
