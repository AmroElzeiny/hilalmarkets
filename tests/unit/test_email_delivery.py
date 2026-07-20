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


class RejectingAuthSMTP(FakeSMTP):
    def login(self, username, password):
        raise email_delivery.smtplib.SMTPAuthenticationError(535, b"rejected")


class DeferringSMTP(FakeSMTP):
    def send_message(self, message):
        raise email_delivery.smtplib.SMTPDataError(451, b"try later")


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


def test_smtp_transactional_email_supports_bcc(monkeypatch):
    FakeSMTP.sent_message = None
    monkeypatch.setattr(email_delivery.smtplib, "SMTP", FakeSMTP)
    settings = Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        email_adapter="smtp",
        smtp_host="smtp.example.com",
        smtp_from_email="office@hilalmarkets.com",
        smtp_from_name="Hilal Markets",
        smtp_use_tls=False,
        ai_interpreter_provider="rules",
    )

    AuthEmailService(settings)._send_smtp(
        recipient="customer@example.com",
        subject="Support confirmation",
        body="We received your request.",
        reply_to="office@hilalmarkets.com",
        bcc=["office@hilalmarkets.com"],
    )

    assert FakeSMTP.sent_message is not None
    assert FakeSMTP.sent_message["To"] == "customer@example.com"
    assert FakeSMTP.sent_message["Bcc"] == "office@hilalmarkets.com"
    assert FakeSMTP.sent_message["Reply-To"] == "office@hilalmarkets.com"


def test_smtp_authentication_failure_is_classified_and_not_retryable(monkeypatch):
    monkeypatch.setattr(email_delivery.smtplib, "SMTP", RejectingAuthSMTP)
    settings = Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        email_adapter="smtp",
        smtp_host="smtp.example.com",
        smtp_username="configured-user",
        smtp_password="configured-password",
        smtp_from_email="office@hilalmarkets.com",
        smtp_use_tls=False,
        ai_interpreter_provider="rules",
    )

    with pytest.raises(EmailDeliveryError) as error:
        AuthEmailService(settings)._send_smtp(
            recipient="customer@example.com",
            subject="Support confirmation",
            body="We received your request.",
        )

    assert error.value.code == "smtp_authentication_failed"
    assert error.value.provider_status == 535
    assert error.value.retryable is False


def test_smtp_temporary_provider_failure_remains_retryable(monkeypatch):
    monkeypatch.setattr(email_delivery.smtplib, "SMTP", DeferringSMTP)
    settings = Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        email_adapter="smtp",
        smtp_host="smtp.example.com",
        smtp_from_email="office@hilalmarkets.com",
        smtp_use_tls=False,
        ai_interpreter_provider="rules",
    )

    with pytest.raises(EmailDeliveryError) as error:
        AuthEmailService(settings)._send_smtp(
            recipient="customer@example.com",
            subject="Support confirmation",
            body="We received your request.",
        )

    assert error.value.code == "smtp_provider_temporary"
    assert error.value.provider_status == 451
    assert error.value.retryable is True


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
