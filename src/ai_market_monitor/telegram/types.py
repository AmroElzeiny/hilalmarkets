from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class TelegramButton:
    text: str
    callback_data: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramOutboundMessage:
    chat_id: str
    text: str
    buttons: list[TelegramButton] = field(default_factory=list)
    menu: list[str] = field(default_factory=list)
    parse_mode: str | None = None
    correlation_id: str | None = None
    edit_message_id: str | None = None


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
