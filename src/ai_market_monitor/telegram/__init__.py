"""Telegram application service package."""

from typing import Any

__all__ = ["TelegramBotService"]


def __getattr__(name: str) -> Any:
    if name == "TelegramBotService":
        from ai_market_monitor.telegram.service import TelegramBotService

        return TelegramBotService
    raise AttributeError(name)
