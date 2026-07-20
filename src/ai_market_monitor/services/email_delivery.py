import asyncio
import json
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any
from uuid import UUID

from ai_market_monitor.core.config import Settings


class EmailDeliveryError(RuntimeError):
    def __init__(self, message: str, code: str = "email_unavailable"):
        super().__init__(message)
        self.code = code


class AuthEmailService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def send_code(self, *, recipient: str, code: str, purpose: str) -> None:
        subjects = {
            "login": "Your HilalMarkets login code",
            "password_reset": "Your HilalMarkets password reset code",
            "signup": "Verify your HilalMarkets email",
            "system_brain": "Your HilalMarkets System Brain access code",
        }
        subject = subjects.get(purpose, "Your HilalMarkets verification code")
        ttl_minutes = (
            self.settings.system_brain_otp_ttl_minutes
            if purpose == "system_brain"
            else self.settings.auth_code_ttl_minutes
        )
        body = (
            f"Your HilalMarkets verification code is {code}.\n\n"
            f"It expires in {ttl_minutes} minutes. "
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
            raise EmailDeliveryError(
                "EMAIL_ADAPTER must be set to smtp to send one-time codes.",
                code="email_adapter_disabled",
            )
        if not self.settings.smtp_host or not self.settings.smtp_from_email:
            raise EmailDeliveryError(
                "SMTP_HOST and SMTP_FROM_EMAIL are required.",
                code="smtp_required_fields_missing",
            )
        await asyncio.to_thread(
            self._send_smtp,
            recipient=recipient,
            subject=subject,
            body=body,
        )

    async def send_support_ticket(
        self,
        *,
        recipient: str,
        ticket_id: UUID,
        user_id: UUID,
        requester_email: str | None,
        subject: str,
        description: str,
        context: dict[str, Any],
        screenshots: list[tuple[str, str, bytes]],
    ) -> None:
        subject = " ".join(subject.split())[:180] or "Support request"
        email_subject = f"HilalMarkets support ticket: {subject}"
        context_json = json.dumps(context, ensure_ascii=False, indent=2, default=str)
        body = (
            "A new HilalMarkets support ticket was created.\n\n"
            f"Ticket ID: {ticket_id}\n"
            f"User ID: {user_id}\n"
            f"Requester email: {requester_email or 'not provided'}\n"
            f"Subject: {subject}\n"
            f"Screenshots: {len(screenshots)}\n\n"
            "Description:\n"
            f"{description}\n\n"
            "Context:\n"
            f"{context_json}\n"
        )
        if self.settings.email_adapter == "memory":
            self.settings.email_test_outbox.append(
                {
                    "recipient": recipient,
                    "subject": email_subject,
                    "body": body,
                    "purpose": "support_ticket",
                    "ticket_id": str(ticket_id),
                    "attachments": [
                        {
                            "filename": filename,
                            "content_type": content_type,
                            "size_bytes": len(content),
                        }
                        for filename, content_type, content in screenshots
                    ],
                }
            )
            return
        if self.settings.email_adapter != "smtp":
            raise EmailDeliveryError(
                "EMAIL_ADAPTER must be set to smtp to send support tickets.",
                code="email_adapter_disabled",
            )
        if not self.settings.smtp_host or not self.settings.smtp_from_email:
            raise EmailDeliveryError(
                "SMTP_HOST and SMTP_FROM_EMAIL are required.",
                code="smtp_required_fields_missing",
            )
        await asyncio.to_thread(
            self._send_smtp,
            recipient=recipient,
            subject=email_subject,
            body=body,
            attachments=screenshots,
        )

    async def send_transactional(
        self,
        *,
        recipient: str,
        subject: str,
        text_body: str,
        html_body: str,
        idempotency_key: str,
        purpose: str,
        sender_email: str | None = None,
        sender_name: str | None = None,
        reply_to: str | None = None,
    ) -> str:
        message_id = make_msgid(
            idstring=idempotency_key[:48],
            domain=(sender_email or self.settings.smtp_from_email or "hilalmarkets.local").split(
                "@"
            )[-1],
        )
        if self.settings.email_adapter == "memory":
            self.settings.email_test_outbox.append(
                {
                    "recipient": recipient,
                    "sender": sender_email or self.settings.smtp_from_email,
                    "reply_to": reply_to,
                    "subject": subject,
                    "body": text_body,
                    "html_body": html_body,
                    "purpose": purpose,
                    "message_id": message_id,
                }
            )
            return message_id
        if self.settings.email_adapter != "smtp":
            raise EmailDeliveryError(
                "EMAIL_ADAPTER must be set to smtp to send transactional email.",
                code="email_adapter_disabled",
            )
        if not self.settings.smtp_host or not self.settings.smtp_from_email:
            raise EmailDeliveryError(
                "SMTP_HOST and SMTP_FROM_EMAIL are required.",
                code="smtp_required_fields_missing",
            )
        return await asyncio.to_thread(
            self._send_smtp,
            recipient=recipient,
            subject=subject,
            body=text_body,
            html_body=html_body,
            message_id=message_id,
            sender_email=sender_email,
            sender_name=sender_name,
            reply_to=reply_to,
        )

    def _send_smtp(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, str, bytes]] | None = None,
        html_body: str | None = None,
        message_id: str | None = None,
        sender_email: str | None = None,
        sender_name: str | None = None,
        reply_to: str | None = None,
    ) -> str:
        host = self.settings.smtp_host
        if not host:
            raise EmailDeliveryError(
                "SMTP_HOST is required.",
                code="smtp_required_fields_missing",
            )
        message = EmailMessage()
        message["From"] = self._from_header(
            sender_email=sender_email,
            sender_name=sender_name,
        )
        message["To"] = recipient
        message["Subject"] = subject
        if reply_to:
            message["Reply-To"] = reply_to
        if message_id:
            message["Message-ID"] = message_id
        message.set_content(body)
        if html_body:
            message.add_alternative(html_body, subtype="html")
        for filename, content_type, content in attachments or []:
            maintype, subtype = content_type.split("/", 1)
            message.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )

        try:
            with self._smtp_connection(host) as smtp:
                if self.settings.smtp_use_tls and not self._uses_implicit_ssl():
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
            raise EmailDeliveryError(
                "The verification email could not be sent.",
                code="smtp_delivery_failed",
            ) from exc
        return message_id or str(message.get("Message-ID") or "")

    def _smtp_connection(self, host: str):
        if self._uses_implicit_ssl():
            return smtplib.SMTP_SSL(host, self.settings.smtp_port, timeout=20)
        return smtplib.SMTP(host, self.settings.smtp_port, timeout=20)

    def _uses_implicit_ssl(self) -> bool:
        return self.settings.smtp_use_ssl or self.settings.smtp_port in {465, 2465}

    def _from_header(
        self,
        *,
        sender_email: str | None = None,
        sender_name: str | None = None,
    ) -> str:
        from_email = sender_email or self.settings.smtp_from_email
        if not from_email:
            raise EmailDeliveryError(
                "SMTP_FROM_EMAIL is required.",
                code="smtp_required_fields_missing",
            )
        from_name = (
            sender_name if sender_name is not None else self.settings.smtp_from_name or ""
        ).strip()
        return formataddr((from_name, from_email)) if from_name else from_email
