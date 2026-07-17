import re
from dataclasses import dataclass
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.whatsapp.types import (
    WhatsAppDeliveryResult,
    WhatsAppInteractiveButtons,
    WhatsAppInteractiveList,
    WhatsAppOutboundMessage,
    WhatsAppSessionText,
    WhatsAppTemplateMessage,
)


class WhatsAppDeliveryError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
        http_status: int | None = None,
        provider_error_code: str | None = None,
        provider_error_subcode: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.http_status = http_status
        self.provider_error_code = provider_error_code
        self.provider_error_subcode = provider_error_subcode


@dataclass(frozen=True, slots=True)
class WhatsAppAdapterHealth:
    configured: bool
    graph_api_version: str
    phone_number_id: str


class WhatsAppCloudAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        token = settings.whatsapp_access_token
        if settings.whatsapp_adapter != "http" or token is None:
            raise WhatsAppDeliveryError(
                "whatsapp_not_configured",
                "WhatsApp Cloud API delivery is not configured.",
                retryable=False,
            )
        if not settings.whatsapp_graph_api_version or not settings.whatsapp_phone_number_id:
            raise WhatsAppDeliveryError(
                "whatsapp_not_configured",
                "WhatsApp Graph API version or phone-number identifier is missing.",
                retryable=False,
            )
        self.base_url = f"https://graph.facebook.com/{settings.whatsapp_graph_api_version}"
        self.phone_number_id = settings.whatsapp_phone_number_id
        self.timeout_seconds = settings.whatsapp_http_timeout_seconds
        self._access_token = token.get_secret_value()
        self.transport = transport

    @property
    def health(self) -> WhatsAppAdapterHealth:
        return WhatsAppAdapterHealth(
            configured=True,
            graph_api_version=self.base_url.rsplit("/", 1)[-1],
            phone_number_id=self.phone_number_id,
        )

    async def deliver(self, message: WhatsAppOutboundMessage) -> WhatsAppDeliveryResult:
        payload = self._payload(message)
        body = await self._call("POST", f"/{self.phone_number_id}/messages", payload)
        messages = body.get("messages") if isinstance(body, dict) else None
        provider_message_id = (
            str(messages[0].get("id"))
            if isinstance(messages, list)
            and messages
            and isinstance(messages[0], dict)
            and messages[0].get("id")
            else ""
        )
        if not provider_message_id:
            raise WhatsAppDeliveryError(
                "whatsapp_message_id_missing",
                "Meta accepted the request without returning a WhatsApp message identifier.",
                retryable=True,
            )
        contacts = body.get("contacts") if isinstance(body, dict) else None
        accepted_wa_id = (
            str(contacts[0].get("wa_id"))
            if isinstance(contacts, list)
            and contacts
            and isinstance(contacts[0], dict)
            and contacts[0].get("wa_id")
            else None
        )
        return WhatsAppDeliveryResult(
            provider_message_id=provider_message_id,
            accepted_wa_id=accepted_wa_id,
        )

    async def mark_read(self, provider_message_id: str) -> None:
        if not provider_message_id or len(provider_message_id) > 255:
            raise WhatsAppDeliveryError(
                "whatsapp_message_id_invalid",
                "The inbound WhatsApp message identifier is invalid.",
                retryable=False,
            )
        await self._call(
            "PUT",
            f"/{self.phone_number_id}/messages",
            {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": provider_message_id,
            },
        )

    @staticmethod
    def _payload(message: WhatsAppOutboundMessage) -> dict[str, Any]:
        common: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": message.to,
        }
        if isinstance(message, WhatsAppSessionText):
            return {
                **common,
                "type": "text",
                "text": {"preview_url": message.preview_url, "body": message.body},
            }
        if isinstance(message, WhatsAppInteractiveButtons):
            interactive: dict[str, Any] = {
                "type": "button",
                "body": {"text": message.body},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": button.id, "title": button.title},
                        }
                        for button in message.buttons
                    ]
                },
            }
            if message.footer:
                interactive["footer"] = {"text": message.footer}
            return {**common, "type": "interactive", "interactive": interactive}
        if isinstance(message, WhatsAppInteractiveList):
            interactive = {
                "type": "list",
                "body": {"text": message.body},
                "action": {
                    "button": message.button_text,
                    "sections": [
                        {
                            "title": section.title,
                            "rows": [row.model_dump(exclude_none=True) for row in section.rows],
                        }
                        for section in message.sections
                    ],
                },
            }
            if message.footer:
                interactive["footer"] = {"text": message.footer}
            return {**common, "type": "interactive", "interactive": interactive}
        if isinstance(message, WhatsAppTemplateMessage):
            template: dict[str, Any] = {
                "name": message.name,
                "language": {"code": message.language},
            }
            if message.components:
                template["components"] = [
                    component.model_dump(exclude_none=True) for component in message.components
                ]
            return {**common, "type": "template", "template": template}
        raise WhatsAppDeliveryError(
            "whatsapp_message_type_unsupported",
            "The WhatsApp message type is unsupported.",
            retryable=False,
        )

    async def _call(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
            ) as client:
                response = await client.request(method, path, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise WhatsAppDeliveryError(
                "whatsapp_network_error",
                "Meta WhatsApp Cloud API could not be reached.",
                retryable=True,
            ) from exc

        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_error:
            raise self._provider_error(
                response,
                body,
                sensitive_values=(self._access_token, str(payload.get("to") or "")),
            )
        if not isinstance(body, dict):
            raise WhatsAppDeliveryError(
                "whatsapp_response_invalid",
                "Meta returned an invalid WhatsApp response.",
                retryable=response.status_code >= 500,
                http_status=response.status_code,
            )
        return body

    @staticmethod
    def _provider_error(
        response: httpx.Response,
        body: Any,
        *,
        sensitive_values: tuple[str, ...] = (),
    ) -> WhatsAppDeliveryError:
        error = body.get("error") if isinstance(body, dict) else {}
        error = error if isinstance(error, dict) else {}
        provider_code = str(error.get("code")) if error.get("code") is not None else None
        provider_subcode = (
            str(error.get("error_subcode")) if error.get("error_subcode") is not None else None
        )
        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
        transient_codes = {"1", "2", "4", "17", "32", "341", "80007"}
        retryable = bool(
            response.status_code == 429
            or response.status_code >= 500
            or error.get("is_transient") is True
            or provider_code in transient_codes
        )
        internal_code = "whatsapp_api_error"
        if response.status_code == 429:
            internal_code = "whatsapp_rate_limited"
        elif provider_code == "190" or response.status_code == 401:
            internal_code = "whatsapp_access_token_invalid"
        elif provider_code in {"10", "200"} or response.status_code == 403:
            internal_code = "whatsapp_permission_denied"
        elif provider_code and provider_code.startswith("132"):
            internal_code = "whatsapp_template_error"
        elif provider_code in {"131026", "131030", "131047"}:
            internal_code = "whatsapp_recipient_or_window_error"
        message = _safe_error_message(error, sensitive_values=sensitive_values)
        return WhatsAppDeliveryError(
            internal_code,
            message,
            retryable=retryable,
            retry_after_seconds=retry_after,
            http_status=response.status_code,
            provider_error_code=provider_code,
            provider_error_subcode=provider_subcode,
        )


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(1, min(86400, int(float(value))))
    except ValueError:
        return None


def _safe_error_message(
    error: dict[str, Any], *, sensitive_values: tuple[str, ...] = ()
) -> str:
    error_data = error.get("error_data")
    error_data = error_data if isinstance(error_data, dict) else {}
    candidate = error_data.get("details") or error.get("message") or "WhatsApp delivery failed."
    normalized = " ".join(str(candidate).split())[:500]
    normalized = normalized.replace("Bearer ", "Bearer [redacted]")
    for value in sensitive_values:
        if value:
            normalized = normalized.replace(value, "[redacted]")
    normalized = re.sub(r"(?<!\w)\+?[1-9]\d{7,14}(?!\w)", "[masked]", normalized)
    return normalized or "WhatsApp delivery failed."
