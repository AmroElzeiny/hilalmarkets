import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services import email_delivery
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError


class FakeSMTP:
    sent_message = None

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def starttls(self):
        return None

    def login(self, username, password):
        return None

    def send_message(self, message):
        FakeSMTP.sent_message = message


def test_smtp_from_name_is_used_in_from_header(monkeypatch):
    FakeSMTP.sent_message = None
    monkeypatch.setattr(email_delivery.smtplib, "SMTP", FakeSMTP)
    settings = Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        email_adapter="smtp",
        smtp_host="smtp.example.com",
        smtp_from_email="no-reply@trace-edge.com",
        smtp_from_name="TraceEdge",
        smtp_use_tls=False,
        ai_interpreter_provider="rules",
    )

    AuthEmailService(settings)._send_smtp(
        recipient="user@example.com",
        subject="Test",
        body="Hello",
    )

    assert FakeSMTP.sent_message is not None
    assert FakeSMTP.sent_message["From"] == "TraceEdge <no-reply@trace-edge.com>"


@pytest.mark.asyncio
async def test_disabled_email_adapter_reports_actionable_code():
    settings = Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        email_adapter="none",
        ai_interpreter_provider="rules",
    )

    with pytest.raises(EmailDeliveryError) as error:
        await AuthEmailService(settings).send_code(
            recipient="user@example.com",
            code="123456",
            purpose="login",
        )

    assert error.value.code == "email_adapter_disabled"
