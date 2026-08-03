import hashlib
import hmac

import pytest

from ai_market_monitor.whatsapp.security import (
    WhatsAppSecurityError,
    mask_e164,
    normalize_e164,
    verify_webhook_signature,
    verify_webhook_token,
)
from ai_market_monitor.whatsapp.webhook import (
    WhatsAppWebhookPayloadError,
    extract_whatsapp_events,
)


def _payload(*, messages=None, statuses=None) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "phone-1"},
                            "contacts": [
                                {
                                    "wa_id": "12025550123",
                                    "profile": {"name": "Test Person"},
                                }
                            ],
                            "messages": messages or [],
                            "statuses": statuses or [],
                        },
                    }
                ],
            }
        ],
    }


def test_webhook_signature_and_verify_token_use_exact_secret_values():
    raw = b'{"object":"whatsapp_business_account"}'
    digest = hmac.new(b"app-secret", raw, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(
        raw_body=raw,
        signature_header=f"sha256={digest}",
        app_secret="app-secret",
    )
    assert not verify_webhook_signature(
        raw_body=raw + b" ",
        signature_header=f"sha256={digest}",
        app_secret="app-secret",
    )
    assert not verify_webhook_signature(
        raw_body=raw,
        signature_header=None,
        app_secret="app-secret",
    )
    assert verify_webhook_token(supplied="verify-me", expected="verify-me")
    assert not verify_webhook_token(supplied="verify-me", expected="different")


def test_webhook_parser_reads_every_message_and_every_status_in_batch():
    messages = [
        {
            "id": "wamid.text",
            "from": "12025550123",
            "timestamp": "1784300000",
            "type": "text",
            "text": {"body": "LINK token-value"},
        },
        {
            "id": "wamid.button",
            "from": "12025550123",
            "timestamp": "1784300001",
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "nav:menu", "title": "Main menu"},
            },
        },
        {
            "id": "wamid.list",
            "from": "12025550123",
            "timestamp": "1784300002",
            "type": "interactive",
            "interactive": {
                "type": "list_reply",
                "list_reply": {"id": "nav:monitors", "title": "My Watchlists"},
            },
        },
        {
            "id": "wamid.image",
            "from": "12025550123",
            "timestamp": "1784300003",
            "type": "image",
        },
    ]
    statuses = [
        {
            "id": "wamid.outbound",
            "status": "sent",
            "timestamp": "1784300010",
            "recipient_id": "12025550123",
        },
        {
            "id": "wamid.outbound",
            "status": "delivered",
            "timestamp": "1784300011",
            "recipient_id": "12025550123",
        },
        {
            "id": "wamid.outbound",
            "status": "read",
            "timestamp": "1784300012",
            "recipient_id": "12025550123",
        },
        {
            "id": "wamid.failed",
            "status": "failed",
            "timestamp": "1784300013",
            "recipient_id": "12025550123",
            "errors": [
                {
                    "code": 131026,
                    "title": "Undeliverable",
                    "message": "Recipient unavailable",
                    "error_data": {"details": "Delivery was not possible"},
                }
            ],
        },
    ]

    events = extract_whatsapp_events(
        _payload(messages=messages, statuses=statuses),
        expected_waba_id="waba-1",
        expected_phone_number_id="phone-1",
    )

    assert len(events) == 8
    assert [event.payload["kind"] for event in events[:4]] == [
        "text",
        "button_reply",
        "list_reply",
        "unsupported",
    ]
    assert events[0].payload["profile_name"] == "Test Person"
    assert [event.event_key for event in events[4:]] == [
        "status:wamid.outbound:sent",
        "status:wamid.outbound:delivered",
        "status:wamid.outbound:read",
        "status:wamid.failed:failed",
    ]
    assert events[-1].payload["error_code"] == "131026"


@pytest.mark.parametrize(
    "payload,expected_message",
    [
        ({"object": "page", "entry": []}, "Unexpected"),
        (_payload(), ""),
    ],
)
def test_webhook_parser_fails_closed_for_wrong_authority(payload, expected_message):
    if not expected_message:
        payload["entry"][0]["id"] = "another-waba"
        expected_message = "business account"
    with pytest.raises(WhatsAppWebhookPayloadError, match=expected_message):
        extract_whatsapp_events(
            payload,
            expected_waba_id="waba-1",
            expected_phone_number_id="phone-1",
        )


def test_phone_normalization_and_masking_do_not_treat_typed_numbers_as_identity():
    assert normalize_e164("+1 (202) 555-0123") == "+12025550123"
    assert mask_e164("+12025550123").startswith("+120")
    assert "50123" not in mask_e164("+12025550123")
    with pytest.raises(WhatsAppSecurityError):
        normalize_e164("202-555-0123")
