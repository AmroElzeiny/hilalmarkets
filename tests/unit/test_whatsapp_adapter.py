import json

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.whatsapp.adapter import WhatsAppCloudAdapter, WhatsAppDeliveryError
from ai_market_monitor.whatsapp.types import (
    WhatsAppInteractiveButtons,
    WhatsAppInteractiveList,
    WhatsAppListRow,
    WhatsAppListSection,
    WhatsAppReplyButton,
    WhatsAppSessionText,
    WhatsAppTemplateComponent,
    WhatsAppTemplateMessage,
    WhatsAppTemplateParameter,
)


def _settings(**changes) -> Settings:
    values = {
        "app_env": "test",
        "app_secret_key": SecretStr(
            "whatsapp-adapter-test-secret-at-least-thirty-two-characters"
        ),
        "whatsapp_enabled": True,
        "whatsapp_adapter": "http",
        "whatsapp_graph_api_version": "v23.0",
        "whatsapp_access_token": SecretStr("server-only-whatsapp-token"),
        "whatsapp_phone_number_id": "phone-number-id-1",
    }
    values.update(changes)
    return Settings(**values)


async def test_cloud_adapter_builds_supported_payloads_and_returns_wamid():
    requests: list[tuple[str, dict, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            (
                request.method,
                json.loads(request.content),
                request.headers.get("Authorization", ""),
            )
        )
        return httpx.Response(
            200,
            json={
                "contacts": [{"wa_id": "12025550123"}],
                "messages": [{"id": f"wamid.{len(requests)}"}],
            },
        )

    adapter = WhatsAppCloudAdapter(_settings(), transport=httpx.MockTransport(handler))
    results = [
        await adapter.deliver(
            WhatsAppSessionText(to="12025550123", body="Account update")
        ),
        await adapter.deliver(
            WhatsAppInteractiveButtons(
                to="12025550123",
                body="Choose an action",
                buttons=[WhatsAppReplyButton(id="nav:menu", title="Main menu")],
            )
        ),
        await adapter.deliver(
            WhatsAppInteractiveList(
                to="12025550123",
                body="Choose a Watchlist",
                button_text="Open list",
                sections=[
                    WhatsAppListSection(
                        title="Watchlists",
                        rows=[WhatsAppListRow(id="monitor:1", title="BTC Watchlist")],
                    )
                ],
            )
        ),
        await adapter.deliver(
            WhatsAppTemplateMessage(
                to="12025550123",
                name="account_notice_v1",
                language="en_US",
                components=[
                    WhatsAppTemplateComponent(
                        type="body",
                        parameters=[WhatsAppTemplateParameter(text="Account update")],
                    )
                ],
            )
        ),
    ]

    assert [result.provider_message_id for result in results] == [
        "wamid.1",
        "wamid.2",
        "wamid.3",
        "wamid.4",
    ]
    assert {payload["type"] for _, payload, _ in requests} == {
        "text",
        "interactive",
        "template",
    }
    assert requests[1][1]["interactive"]["type"] == "button"
    assert requests[2][1]["interactive"]["type"] == "list"
    assert all(method == "POST" for method, _, _ in requests)
    assert all(auth == "Bearer server-only-whatsapp-token" for _, _, auth in requests)


async def test_cloud_adapter_marks_an_inbound_message_read():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    adapter = WhatsAppCloudAdapter(_settings(), transport=httpx.MockTransport(handler))
    await adapter.mark_read("wamid.inbound-1")

    assert captured == [
        {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": "wamid.inbound-1",
        }
    ]


@pytest.mark.parametrize(
    ("status_code", "provider_error", "expected_code", "retryable"),
    [
        (401, {"code": 190, "message": "Invalid token"}, "whatsapp_access_token_invalid", False),
        (403, {"code": 10, "message": "Permission denied"}, "whatsapp_permission_denied", False),
        (400, {"code": 132001, "message": "Template missing"}, "whatsapp_template_error", False),
        (
            400,
            {"code": 131026, "message": "Recipient unavailable"},
            "whatsapp_recipient_or_window_error",
            False,
        ),
        (503, {"code": 2, "message": "Temporary"}, "whatsapp_api_error", True),
    ],
)
async def test_cloud_adapter_classifies_provider_errors(
    status_code, provider_error, expected_code, retryable
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": provider_error})

    adapter = WhatsAppCloudAdapter(_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(WhatsAppDeliveryError) as raised:
        await adapter.deliver(WhatsAppSessionText(to="12025550123", body="Test"))

    assert raised.value.code == expected_code
    assert raised.value.retryable is retryable
    assert raised.value.http_status == status_code


async def test_cloud_adapter_respects_retry_after_and_redacts_sensitive_values():
    secret = "server-only-whatsapp-token"
    recipient = "12025550123"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "17"},
            json={
                "error": {
                    "code": 80007,
                    "message": f"Bearer {secret} recipient {recipient} is limited",
                }
            },
        )

    adapter = WhatsAppCloudAdapter(_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(WhatsAppDeliveryError) as raised:
        await adapter.deliver(WhatsAppSessionText(to=recipient, body="Test"))

    assert raised.value.code == "whatsapp_rate_limited"
    assert raised.value.retryable is True
    assert raised.value.retry_after_seconds == 17
    assert secret not in str(raised.value)
    assert recipient not in str(raised.value)


async def test_cloud_adapter_rejects_malformed_success_response():
    adapter = WhatsAppCloudAdapter(
        _settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )

    with pytest.raises(WhatsAppDeliveryError) as raised:
        await adapter.deliver(WhatsAppSessionText(to="12025550123", body="Test"))

    assert raised.value.code == "whatsapp_message_id_missing"
    assert raised.value.retryable is True


async def test_cloud_adapter_network_timeout_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    adapter = WhatsAppCloudAdapter(_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(WhatsAppDeliveryError) as raised:
        await adapter.deliver(WhatsAppSessionText(to="12025550123", body="Test"))

    assert raised.value.code == "whatsapp_network_error"
    assert raised.value.retryable is True
