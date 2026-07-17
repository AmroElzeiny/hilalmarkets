"""Official Meta WhatsApp Cloud API integration."""

from typing import Any

__all__ = [
    "WhatsAppAccountService",
    "WhatsAppCloudAdapter",
    "WhatsAppDeliveryError",
    "WhatsAppDeliveryService",
]


def __getattr__(name: str) -> Any:
    if name in {"WhatsAppCloudAdapter", "WhatsAppDeliveryError"}:
        from ai_market_monitor.whatsapp.adapter import (
            WhatsAppCloudAdapter,
            WhatsAppDeliveryError,
        )

        return {
            "WhatsAppCloudAdapter": WhatsAppCloudAdapter,
            "WhatsAppDeliveryError": WhatsAppDeliveryError,
        }[name]
    if name in {"WhatsAppAccountService", "WhatsAppDeliveryService"}:
        from ai_market_monitor.whatsapp.service import (
            WhatsAppAccountService,
            WhatsAppDeliveryService,
        )

        return {
            "WhatsAppAccountService": WhatsAppAccountService,
            "WhatsAppDeliveryService": WhatsAppDeliveryService,
        }[name]
    raise AttributeError(name)
