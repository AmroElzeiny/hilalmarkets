import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.api.routers.whatsapp import (
    get_whatsapp_receipt_enqueuer,
)
from ai_market_monitor.api.routers.whatsapp import (
    router as whatsapp_router,
)
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.csrf import csrf_token
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    AuditEvent,
    DashboardPreference,
    IdentityLinkToken,
    IntegrationTestResult,
    User,
    UserIdentity,
    WhatsAppConnection,
    WhatsAppWebhookReceipt,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
    IdentityProvider,
)
from ai_market_monitor.services.notifications import NotificationDispatcher
from ai_market_monitor.whatsapp.adapter import WhatsAppDeliveryError
from ai_market_monitor.whatsapp.service import (
    WhatsAppAccountService,
    WhatsAppConversationService,
    WhatsAppDeliveryService,
    WhatsAppServiceError,
    WhatsAppStatusService,
    WhatsAppWebhookProcessor,
    connection_payload,
)
from ai_market_monitor.whatsapp.types import (
    WhatsAppDeliveryResult,
    WhatsAppInboundText,
    WhatsAppLinkRequest,
    WhatsAppSessionText,
    WhatsAppTemplateMessage,
)


class FakeWhatsAppAdapter:
    def __init__(self):
        self.messages = []
        self.read_ids: list[str] = []

    async def deliver(self, message):
        self.messages.append(message)
        return WhatsAppDeliveryResult(
            provider_message_id=f"wamid.test-{len(self.messages)}",
            accepted_wa_id=message.to,
        )

    async def mark_read(self, provider_message_id: str) -> None:
        self.read_ids.append(provider_message_id)


class RetryableFailureAdapter(FakeWhatsAppAdapter):
    async def deliver(self, message):
        self.messages.append(message)
        raise WhatsAppDeliveryError(
            "whatsapp_network_error",
            "Meta WhatsApp Cloud API could not be reached.",
            retryable=True,
        )


def _wa_settings(base: Settings, **changes) -> Settings:
    values = {
        "whatsapp_enabled": True,
        "whatsapp_adapter": "http",
        "whatsapp_graph_api_version": "v23.0",
        "whatsapp_access_token": SecretStr("server-only-test-access-token"),
        "whatsapp_app_secret": SecretStr("server-only-test-app-secret"),
        "whatsapp_verify_token": SecretStr("server-only-test-verify-token"),
        "whatsapp_phone_number_id": "phone-1",
        "whatsapp_business_account_id": "waba-1",
        "whatsapp_business_phone_e164": "+12025550999",
        "whatsapp_default_language": "en_US",
        "whatsapp_template_names": {},
        "whatsapp_opportunity_alerts_enabled": False,
    }
    values.update(changes)
    return base.model_copy(update=values)


def _enable_whatsapp_test_routes(test_context, settings: Settings) -> None:
    """Mount the dormant integration only inside its explicitly enabled tests."""
    if not any(
        getattr(route, "path", None) == "/api/v1/whatsapp/status"
        for route in test_context["app"].routes
    ):
        test_context["app"].include_router(whatsapp_router, prefix="/api/v1")
    test_context["app"].dependency_overrides[get_settings] = lambda: settings


async def _signup(test_context, email: str) -> None:
    response = await test_context["client"].post(
        "/signup",
        data={
            "email": email,
            "display_name": "WhatsApp User",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def _user_id(test_context):
    async with test_context["session_factory"]() as session:
        user_id = await session.scalar(
            select(UserIdentity.user_id).where(UserIdentity.provider == IdentityProvider.EMAIL)
        )
        assert user_id is not None
        return user_id


def _raw_link_token(link_url: str) -> str:
    message = parse_qs(urlsplit(link_url).query)["text"][0]
    prefix, token = message.split(" ", 1)
    assert prefix == "LINK"
    return token


def _webhook_payload(*, messages=None, statuses=None) -> dict:
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
                                    "profile": {"name": "WhatsApp User"},
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


def _signature(raw: bytes, secret: str = "server-only-test-app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


async def _service_user(session, name: str = "WhatsApp Service User") -> User:
    user = User(display_name=name)
    session.add(user)
    await session.flush()
    return user


def _connection(user: User, *, categories=None, window_open=True) -> WhatsAppConnection:
    now = datetime.now(UTC)
    return WhatsAppConnection(
        user_id=user.id,
        wa_id="12025550123",
        phone_e164="+12025550123",
        profile_name=user.display_name,
        status=ConnectionStatus.ACTIVE,
        alerts_enabled=True,
        preferred_locale="en_US",
        opt_in_categories=categories or ["subscription"],
        connected_at=now,
        verified_at=now,
        opt_in_at=now,
        opt_in_source="dashboard_wa_link",
        opt_in_version="2026-07",
        service_window_expires_at=(now + timedelta(hours=1) if window_open else None),
    )


async def _alert(session, user: User, alert_type=AlertType.TRIAL, suffix="1") -> Alert:
    alert = Alert(
        user_id=user.id,
        alert_type=alert_type,
        deduplication_key=f"whatsapp-{user.id}-{alert_type.value}-{suffix}",
        title="HilalMarkets update",
        body="Evidence is available in the dashboard.",
        proof_receipt={
            "strategy_name": "BTC Watch Plan",
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "setup_state": "confirmed",
            "conditions": [{"name": "Trigger", "state": "passed"}],
        },
        candle_timestamp=datetime.now(UTC),
    )
    session.add(alert)
    await session.flush()
    return alert


async def test_dashboard_link_requires_csrf_and_inbound_phone_ownership(test_context):
    await _signup(test_context, "whatsapp-link@example.com")
    user_id = await _user_id(test_context)
    settings = _wa_settings(test_context["settings"])
    _enable_whatsapp_test_routes(test_context, settings)

    missing_csrf = await test_context["client"].post(
        "/api/v1/whatsapp/link",
        json={
            "phone_e164": "+12025550123",
            "consent": True,
            "categories": ["account", "compliance"],
            "locale": "en_US",
        },
    )
    assert missing_csrf.status_code == 403

    response = await test_context["client"].post(
        "/api/v1/whatsapp/link",
        headers={"X-CSRF-Token": csrf_token(settings, user_id)},
        json={
            "phone_e164": "+12025550123",
            "consent": True,
            "categories": ["account", "compliance"],
            "locale": "en_US",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["phone"] != "+12025550123"
    raw_token = _raw_link_token(payload["link_url"])

    async with test_context["session_factory"]() as session:
        stored = await session.scalar(select(IdentityLinkToken))
        assert stored is not None
        assert stored.token_digest != raw_token
        assert stored.metadata_json["consent"] is True
        with pytest.raises(WhatsAppServiceError, match="same WhatsApp number"):
            await WhatsAppAccountService(session, settings).complete_link(
                raw_token=raw_token,
                wa_id="12025550124",
                profile_name="Wrong Number",
                inbound_at=datetime.now(UTC),
            )
        connection = await WhatsAppAccountService(session, settings).complete_link(
            raw_token=raw_token,
            wa_id="12025550123",
            profile_name="WhatsApp User",
            inbound_at=datetime.now(UTC),
        )
        public = connection_payload(connection)
        assert public is not None
        assert "wa_id" not in public
        assert public["phone"] != "+12025550123"
        assert public["verified"] is True
        with pytest.raises(WhatsAppServiceError, match="already used"):
            await WhatsAppAccountService(session, settings).complete_link(
                raw_token=raw_token,
                wa_id="12025550123",
                profile_name="WhatsApp User",
                inbound_at=datetime.now(UTC),
            )


async def test_signed_webhook_verification_batching_and_idempotency(test_context):
    settings = _wa_settings(test_context["settings"])
    _enable_whatsapp_test_routes(test_context, settings)
    queued: list[str] = []
    test_context["app"].dependency_overrides[get_whatsapp_receipt_enqueuer] = (
        lambda: queued.append
    )

    verified = await test_context["client"].get(
        "/api/v1/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "server-only-test-verify-token",
            "hub.challenge": "challenge-value",
        },
    )
    assert verified.status_code == 200
    assert verified.text == "challenge-value"
    rejected = await test_context["client"].get(
        "/api/v1/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "challenge-value",
        },
    )
    assert rejected.status_code == 403

    body = _webhook_payload(
        messages=[
            {
                "id": "wamid.inbound-1",
                "from": "12025550123",
                "timestamp": "1784300000",
                "type": "text",
                "text": {"body": "MENU"},
            }
        ],
        statuses=[
            {
                "id": "wamid.outbound-1",
                "status": "sent",
                "timestamp": "1784300001",
                "recipient_id": "12025550123",
            },
            {
                "id": "wamid.outbound-1",
                "status": "delivered",
                "timestamp": "1784300002",
                "recipient_id": "12025550123",
            },
        ],
    )
    raw = json.dumps(body, separators=(",", ":")).encode()
    missing_signature = await test_context["client"].post(
        "/api/v1/whatsapp/webhook",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert missing_signature.status_code == 401
    invalid = await test_context["client"].post(
        "/api/v1/whatsapp/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert invalid.status_code == 401
    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(WhatsAppWebhookReceipt.id))) == 0

    first = await test_context["client"].post(
        "/api/v1/whatsapp/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _signature(raw)},
    )
    assert first.status_code == 200
    assert first.json()["accepted"] == 3
    assert len(queued) == 3
    second = await test_context["client"].post(
        "/api/v1/whatsapp/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _signature(raw)},
    )
    assert second.status_code == 200
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 3
    assert len(queued) == 3

    malformed = b"{not-json"
    malformed_response = await test_context["client"].post(
        "/api/v1/whatsapp/webhook",
        content=malformed,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _signature(malformed),
        },
    )
    assert malformed_response.status_code == 400


async def test_link_expiry_cross_user_ownership_and_reconsent(test_context):
    settings = _wa_settings(test_context["settings"])
    async with test_context["session_factory"]() as session:
        owner = await _service_user(session, "WhatsApp Owner")
        other = await _service_user(session, "Other User")
        first = await WhatsAppAccountService(session, settings).create_link(
            user_id=owner.id,
            request=WhatsAppLinkRequest(
                phone_e164="+12025550123",
                consent=True,
                categories=["account"],
                locale="en_US",
            ),
        )
        first_token = _raw_link_token(first.url)
        link_row = await session.scalar(
            select(IdentityLinkToken).where(IdentityLinkToken.user_id == owner.id)
        )
        assert link_row is not None
        link_row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(WhatsAppServiceError, match="expired"):
            await WhatsAppAccountService(session, settings).complete_link(
                raw_token=first_token,
                wa_id="12025550123",
                profile_name="WhatsApp Owner",
                inbound_at=datetime.now(UTC),
            )

        current = await WhatsAppAccountService(session, settings).create_link(
            user_id=owner.id,
            request=WhatsAppLinkRequest(
                phone_e164="+12025550123",
                consent=True,
                categories=["account", "compliance"],
                locale="en_US",
            ),
        )
        connection = await WhatsAppAccountService(session, settings).complete_link(
            raw_token=_raw_link_token(current.url),
            wa_id="12025550123",
            profile_name="WhatsApp Owner",
            inbound_at=datetime.now(UTC),
        )
        with pytest.raises(WhatsAppServiceError, match="assigned to another account"):
            await WhatsAppAccountService(session, settings).create_link(
                user_id=other.id,
                request=WhatsAppLinkRequest(
                    phone_e164="+12025550123",
                    consent=True,
                    categories=["account"],
                    locale="en_US",
                ),
            )

        await WhatsAppAccountService(session, settings).disconnect(owner.id)
        assert connection.status == ConnectionStatus.REVOKED
        reconnect = await WhatsAppAccountService(session, settings).create_link(
            user_id=owner.id,
            request=WhatsAppLinkRequest(
                phone_e164="+12025550123",
                consent=True,
                categories=["evidence"],
                locale="en_US",
            ),
        )
        restored = await WhatsAppAccountService(session, settings).complete_link(
            raw_token=_raw_link_token(reconnect.url),
            wa_id="12025550123",
            profile_name="WhatsApp Owner",
            inbound_at=datetime.now(UTC),
        )
        assert restored.id == connection.id
        assert restored.status == ConnectionStatus.ACTIVE
        assert restored.alerts_enabled is True
        assert restored.opt_in_categories == ["evidence"]
        assert restored.opt_out_at is None


async def test_webhook_processor_completes_link_sends_confirmation_and_redacts_body(
    test_context,
):
    settings = _wa_settings(test_context["settings"])
    adapter = FakeWhatsAppAdapter()
    async with test_context["session_factory"]() as session:
        user = await _service_user(session)
        link = await WhatsAppAccountService(session, settings).create_link(
            user_id=user.id,
            request=WhatsAppLinkRequest(
                phone_e164="+12025550123",
                consent=True,
                categories=["account", "compliance"],
                locale="en_US",
            ),
        )
        raw_token = _raw_link_token(link.url)
        receipt = WhatsAppWebhookReceipt(
            event_key="message:wamid.link-1",
            event_type="inbound",
            provider_message_id="wamid.link-1",
            payload_hash="a" * 64,
            payload_redacted={
                "kind": "text",
                "message_id": "wamid.link-1",
                "wa_id": "12025550123",
                "profile_name": "WhatsApp User",
                "timestamp": datetime.now(UTC).isoformat(),
                "text": f"LINK {raw_token}",
            },
            response_payload={},
            processing_status="pending",
            attempt_count=0,
            received_at=datetime.now(UTC),
            retain_until=datetime.now(UTC) + timedelta(days=30),
        )
        session.add(receipt)
        await session.flush()

        result = await WhatsAppWebhookProcessor(session, settings, adapter).process(receipt.id)

        assert result == "processed"
        assert isinstance(adapter.messages[0], WhatsAppSessionText)
        assert "connected to HilalMarkets" in adapter.messages[0].body
        assert adapter.read_ids == ["wamid.link-1"]
        assert receipt.payload_redacted["processed"] is True
        assert raw_token not in json.dumps(receipt.payload_redacted)
        connection = await session.scalar(select(WhatsAppConnection))
        assert connection is not None and connection.alerts_enabled is True
        assert await session.scalar(select(func.count(AuditEvent.id))) == 2


async def test_delivery_uses_session_text_then_monotonic_status_updates(test_context):
    settings = _wa_settings(test_context["settings"])
    adapter = FakeWhatsAppAdapter()
    async with test_context["session_factory"]() as session:
        user = await _service_user(session)
        session.add(_connection(user, categories=["subscription"], window_open=True))
        session.add(
            DashboardPreference(
                user_id=user.id,
                notification_preferences={"channels": ["whatsapp"]},
            )
        )
        alert = await _alert(session, user, AlertType.TRIAL)
        deliveries = await NotificationDispatcher(session, settings).enqueue_user_alert(alert)
        assert len(deliveries) == 1
        assert deliveries[0].destination_key == "wa:12025550123"

        processed = await WhatsAppDeliveryService(session, settings, adapter).process_due()
        assert len(processed) == 1
        delivery = processed[0]
        assert isinstance(adapter.messages[0], WhatsAppSessionText)
        assert delivery.status == DeliveryStatus.SENT
        assert delivery.provider_message_id == "wamid.test-1"

        event_time = datetime.now(UTC)
        await WhatsAppStatusService(session, settings).apply(
            {
                "provider_message_id": delivery.provider_message_id,
                "status": "read",
                "timestamp": event_time.isoformat(),
            }
        )
        await WhatsAppStatusService(session, settings).apply(
            {
                "provider_message_id": delivery.provider_message_id,
                "status": "sent",
                "timestamp": (event_time + timedelta(seconds=1)).isoformat(),
            }
        )
        await WhatsAppStatusService(session, settings).apply(
            {
                "provider_message_id": delivery.provider_message_id,
                "status": "failed",
                "timestamp": (event_time + timedelta(seconds=2)).isoformat(),
                "error_code": "131026",
                "error_message": "late failure",
            }
        )
        assert delivery.status == DeliveryStatus.DELIVERED
        assert delivery.provider_status == "read"
        assert delivery.read_at is not None
        assert delivery.last_error_code is None
        assert delivery.provider_status_metadata["observed_statuses"] == [
            "read",
            "sent",
            "failed",
        ]


async def test_outside_service_window_requires_configured_template(test_context):
    settings = _wa_settings(
        test_context["settings"],
        whatsapp_template_names={"trial_update": "trial_update_v1"},
    )
    adapter = FakeWhatsAppAdapter()
    async with test_context["session_factory"]() as session:
        user = await _service_user(session)
        session.add(_connection(user, categories=["subscription"], window_open=False))
        session.add(
            DashboardPreference(
                user_id=user.id,
                notification_preferences={"channels": ["whatsapp"]},
            )
        )
        alert = await _alert(session, user, AlertType.TRIAL)
        deliveries = await NotificationDispatcher(session, settings).enqueue_user_alert(alert)
        await WhatsAppDeliveryService(session, settings, adapter).process_due()

        assert len(deliveries) == 1
        assert isinstance(adapter.messages[0], WhatsAppTemplateMessage)
        assert adapter.messages[0].name == "trial_update_v1"


async def test_outside_window_never_falls_back_to_free_form(test_context):
    settings = _wa_settings(test_context["settings"])
    adapter = FakeWhatsAppAdapter()
    async with test_context["session_factory"]() as session:
        user = await _service_user(session)
        session.add(_connection(user, categories=["subscription"], window_open=False))
        session.add(
            DashboardPreference(
                user_id=user.id,
                notification_preferences={"channels": ["whatsapp"]},
            )
        )
        alert = await _alert(session, user, AlertType.TRIAL)
        first = await NotificationDispatcher(session, settings).enqueue_user_alert(alert)
        second = await NotificationDispatcher(session, settings).enqueue_user_alert(alert)
        processed = await WhatsAppDeliveryService(session, settings, adapter).process_due()

        assert first == []
        assert second == []
        assert processed == []
        assert adapter.messages == []


async def test_retryable_delivery_stops_at_configured_maximum(test_context):
    settings = _wa_settings(
        test_context["settings"],
        whatsapp_max_delivery_attempts=2,
    )
    adapter = RetryableFailureAdapter()
    async with test_context["session_factory"]() as session:
        user = await _service_user(session)
        session.add(_connection(user, categories=["subscription"], window_open=True))
        session.add(
            DashboardPreference(
                user_id=user.id,
                notification_preferences={"channels": ["whatsapp"]},
            )
        )
        alert = await _alert(session, user, AlertType.TRIAL)
        deliveries = await NotificationDispatcher(session, settings).enqueue_user_alert(alert)
        duplicate = await NotificationDispatcher(session, settings).enqueue_user_alert(alert)
        assert len(deliveries) == 1
        assert duplicate[0] is deliveries[0]

        await WhatsAppDeliveryService(session, settings, adapter).process_due()
        delivery = deliveries[0]
        assert delivery.status == DeliveryStatus.FAILED_RETRYABLE
        assert delivery.attempt_count == 1
        delivery.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)

        await WhatsAppDeliveryService(session, settings, adapter).process_due()
        assert delivery.status == DeliveryStatus.FAILED_PERMANENT
        assert delivery.attempt_count == 2
        assert delivery.next_retry_at is None
        assert len(adapter.messages) == 2


async def test_opportunity_delivery_stays_off_without_flag_and_approved_template(
    test_context,
):
    settings = _wa_settings(test_context["settings"])
    async with test_context["session_factory"]() as session:
        user = await _service_user(session)
        session.add(_connection(user, categories=["opportunity"], window_open=True))
        session.add(
            DashboardPreference(
                user_id=user.id,
                notification_preferences={"channels": ["whatsapp"]},
            )
        )
        alert = await _alert(session, user, AlertType.CONFIRMED)
        deliveries = await NotificationDispatcher(session, settings).enqueue_user_alert(alert)

        assert deliveries == []


async def test_stop_cancels_queued_delivery_and_start_requires_dashboard_reconsent(
    test_context,
):
    settings = _wa_settings(test_context["settings"])
    async with test_context["session_factory"]() as session:
        user = await _service_user(session)
        connection = _connection(user, categories=["subscription"], window_open=True)
        session.add(connection)
        alert = await _alert(session, user, AlertType.TRIAL)
        queued = AlertDelivery(
            alert_id=alert.id,
            channel=DeliveryChannel.WHATSAPP,
            destination_key="wa:12025550123",
            status=DeliveryStatus.PENDING,
        )
        session.add(queued)
        await session.flush()

        response = await WhatsAppConversationService(session, settings).handle(
            WhatsAppInboundText(
                message_id="wamid.stop",
                wa_id="12025550123",
                timestamp=datetime.now(UTC),
                text="sToP",
            )
        )
        assert isinstance(response, WhatsAppSessionText)
        assert "alerts are now off" in response.body
        assert connection.alerts_enabled is False
        assert connection.opt_out_at is not None
        assert queued.status == DeliveryStatus.CANCELED

        start = await WhatsAppConversationService(session, settings).handle(
            WhatsAppInboundText(
                message_id="wamid.start",
                wa_id="12025550123",
                timestamp=datetime.now(UTC),
                text="START",
            )
        )
        assert isinstance(start, WhatsAppSessionText)
        assert "Fresh consent is required" in start.body
        assert connection.alerts_enabled is False


async def test_test_result_status_is_monotonic(test_context):
    settings = _wa_settings(test_context["settings"])
    async with test_context["session_factory"]() as session:
        user = await _service_user(session)
        result = IntegrationTestResult(
            user_id=user.id,
            integration="whatsapp",
            destination="+120******23",
            status="sent",
            provider_message_id="wamid.test-status",
            metadata_json={},
        )
        session.add(result)
        await session.flush()
        now = datetime.now(UTC)

        for state in ("read", "sent", "failed"):
            await WhatsAppStatusService(session, settings).apply(
                {
                    "provider_message_id": "wamid.test-status",
                    "status": state,
                    "timestamp": now.isoformat(),
                    "error_code": "131026" if state == "failed" else None,
                }
            )

        assert result.status == "read"
        assert result.error_code is None
        assert result.metadata_json["observed_statuses"] == ["read", "sent", "failed"]
