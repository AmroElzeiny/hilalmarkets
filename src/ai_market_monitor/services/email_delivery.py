import asyncio
import smtplib
from email.message import EmailMessage

from ai_market_monitor.core.config import Settings


class EmailDeliveryError(RuntimeError):
    pass


class AuthEmailService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def send_code(self, *, recipient: str, code: str, purpose: str) -> None:
        subject = (
            "Your TraceEdge login code"
            if purpose == "login"
            else "Your TraceEdge password reset code"
        )
        body = (
            f"Your TraceEdge verification code is {code}.\n\n"
            f"It expires in {self.settings.auth_code_ttl_minutes} minutes. "
            "If you did not request this code, you can ignore this email."
        )
        if self.settings.email_adapter == "memory":
            self.settings.email_test_outbox.append(
                {
                    "recipient": recipient,
                    "subject": subject,
                    "body": body,
                    "code": code,
                    "purpose": purpose,
                }
            )
            return
        if self.settings.email_adapter != "smtp":
            raise EmailDeliveryError("Email delivery is not configured.")
        if not self.settings.smtp_host or not self.settings.smtp_from_email:
            raise EmailDeliveryError("SMTP_HOST and SMTP_FROM_EMAIL are required.")
        await asyncio.to_thread(
            self._send_smtp,
            recipient=recipient,
            subject=subject,
            body=body,
        )

    def _send_smtp(self, *, recipient: str, subject: str, body: str) -> None:
        host = self.settings.smtp_host
        if not host:
            raise EmailDeliveryError("SMTP_HOST is required.")
        message = EmailMessage()
        message["From"] = self.settings.smtp_from_email
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        try:
            with smtplib.SMTP(host, self.settings.smtp_port, timeout=20) as smtp:
                if self.settings.smtp_use_tls:
                    smtp.starttls()
                if self.settings.smtp_username:
                    smtp.login(
                        self.settings.smtp_username,
                        self.settings.smtp_password.get_secret_value()
                        if self.settings.smtp_password
                        else "",
                    )
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("The verification email could not be sent.") from exc
