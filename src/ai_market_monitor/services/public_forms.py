from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    ContactEmailDelivery,
    ContactSubmission,
    WaitlistSheetDelivery,
    WaitlistSignup,
)
from ai_market_monitor.schemas.public_forms import (
    ContactSubmissionRequest,
    FirstTouchAttribution,
    WaitlistDeliveryStatus,
    WaitlistSignupRequest,
)
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError

PUBLIC_FORMS_CSRF_COOKIE = "hm_public_forms_csrf"


def issue_public_forms_csrf(settings: Settings) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(32)
    return nonce, _csrf_token(settings, nonce)


def public_forms_csrf_matches(
    settings: Settings,
    nonce: str | None,
    supplied: str | None,
) -> bool:
    if not nonce or not supplied:
        return False
    return hmac.compare_digest(_csrf_token(settings, nonce), supplied)


def _csrf_token(settings: Settings, nonce: str) -> str:
    return hmac.new(
        settings.app_secret_key.get_secret_value().encode(),
        f"public-forms:{nonce}".encode(),
        hashlib.sha256,
    ).hexdigest()


class PublicFormConflict(ValueError):
    pass


class PublicFormsService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        sheet_transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.session = session
        self.settings = settings
        self.sheet_transport = sheet_transport

    async def register_waitlist(
        self,
        payload: WaitlistSignupRequest,
        *,
        country_code: str | None,
        attribution_allowed: bool,
    ) -> tuple[WaitlistSignup, bool]:
        normalized_email = str(payload.email).strip().casefold()
        attribution = self._attribution(payload.attribution) if attribution_allowed else {}
        request_hash = self._request_hash(
            {
                "email": normalized_email,
                "source_page": self._path(payload.source_page),
                "attribution": attribution,
            }
        )
        existing_key = await self.session.scalar(
            select(WaitlistSignup).where(
                WaitlistSignup.idempotency_key == payload.idempotency_key
            )
        )
        if existing_key is not None:
            if existing_key.request_hash != request_hash:
                raise PublicFormConflict("That request identifier was already used.")
            await self._ensure_waitlist_delivery(existing_key)
            return existing_key, False

        existing_email = await self.session.scalar(
            select(WaitlistSignup).where(
                WaitlistSignup.normalized_email == normalized_email
            )
        )
        if existing_email is not None:
            await self._ensure_waitlist_delivery(existing_email)
            return existing_email, False

        now = datetime.now(UTC)
        signup = WaitlistSignup(
            normalized_email=normalized_email,
            country_code=country_code,
            source_page=self._path(payload.source_page),
            attribution=attribution,
            idempotency_key=payload.idempotency_key,
            request_hash=request_hash,
            status="active",
            submitted_at=now,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(signup)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(
                select(WaitlistSignup).where(
                    or_(
                        WaitlistSignup.normalized_email == normalized_email,
                        WaitlistSignup.idempotency_key == payload.idempotency_key,
                    )
                )
            )
            if existing is None:
                raise
            if (
                existing.idempotency_key == payload.idempotency_key
                and existing.request_hash != request_hash
            ):
                raise PublicFormConflict(
                    "That request identifier was already used."
                ) from None
            await self._ensure_waitlist_delivery(existing)
            return existing, False
        await self._ensure_waitlist_delivery(signup)
        return signup, True

    async def submit_contact(
        self,
        payload: ContactSubmissionRequest,
    ) -> tuple[ContactSubmission, bool]:
        normalized_email = str(payload.email).strip().casefold()
        clean_payload = {
            "email": normalized_email,
            "title": payload.title,
            "description": payload.description,
            "source_page": self._path(payload.source_page),
        }
        request_hash = self._request_hash(clean_payload)
        existing = await self.session.scalar(
            select(ContactSubmission).where(
                ContactSubmission.idempotency_key == payload.idempotency_key
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise PublicFormConflict("That request identifier was already used.")
            await self._ensure_contact_delivery(existing)
            return existing, False

        now = datetime.now(UTC)
        submission = ContactSubmission(
            normalized_email=normalized_email,
            title=payload.title,
            description=payload.description,
            source_page=clean_payload["source_page"],
            idempotency_key=payload.idempotency_key,
            request_hash=request_hash,
            status="received",
            submitted_at=now,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(submission)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(
                select(ContactSubmission).where(
                    ContactSubmission.idempotency_key == payload.idempotency_key
                )
            )
            if existing is None:
                raise
            if existing.request_hash != request_hash:
                raise PublicFormConflict(
                    "That request identifier was already used."
                ) from None
            await self._ensure_contact_delivery(existing)
            return existing, False
        await self._ensure_contact_delivery(submission)
        return submission, True

    async def process_waitlist_due(
        self,
        *,
        signup_id: UUID | None = None,
        limit: int = 25,
    ) -> dict[str, int]:
        result = {"processed": 0, "sent": 0, "retryable": 0, "failed": 0}
        if not self.settings.waitlist_google_sheets_enabled:
            return result
        now = datetime.now(UTC)
        abandoned = now - timedelta(
            minutes=self.settings.public_form_delivery_claim_timeout_minutes
        )
        query = select(WaitlistSheetDelivery).where(
            or_(
                and_(
                    WaitlistSheetDelivery.status.in_({"pending", "retryable"}),
                    or_(
                        WaitlistSheetDelivery.next_retry_at.is_(None),
                        WaitlistSheetDelivery.next_retry_at <= now,
                    ),
                ),
                and_(
                    WaitlistSheetDelivery.status == "sending",
                    WaitlistSheetDelivery.last_attempt_at.is_not(None),
                    WaitlistSheetDelivery.last_attempt_at <= abandoned,
                ),
            )
        )
        if signup_id is not None:
            query = query.where(WaitlistSheetDelivery.signup_id == signup_id)
        rows = list(
            (
                await self.session.scalars(
                    query.order_by(WaitlistSheetDelivery.created_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for row in rows:
            row.status = "sending"
            row.attempt_count += 1
            row.last_attempt_at = datetime.now(UTC)
            row.next_retry_at = None
            await self.session.commit()
            try:
                endpoint = self._sheet_endpoint()
                signup = await self.session.get(WaitlistSignup, row.signup_id)
                if signup is None:
                    raise RuntimeError("waitlist_signup_missing")
                secret = self.settings.waitlist_google_sheets_webhook_secret
                if secret is None:
                    raise RuntimeError("waitlist_sheet_secret_missing")
                secret_value = (
                    secret.get_secret_value()
                    if hasattr(secret, "get_secret_value")
                    else str(secret)
                ).strip()
                if not secret_value:
                    raise RuntimeError("waitlist_sheet_secret_missing")
                async with httpx.AsyncClient(
                    transport=self.sheet_transport,
                    timeout=self.settings.waitlist_google_sheets_timeout_seconds,
                    follow_redirects=True,
                ) as client:
                    response = await client.post(
                        endpoint,
                        headers={"Content-Type": "application/json"},
                        json={
                            "webhook_secret": secret_value,
                            "event_id": row.event_key,
                            "email": signup.normalized_email,
                            "submitted_at": signup.submitted_at.isoformat(),
                            "country": signup.country_code or "unknown",
                            "source_page": signup.source_page,
                            **signup.attribution,
                        },
                    )
                    response.raise_for_status()
                    acknowledgement = response.json()
                    if (
                        not isinstance(acknowledgement, dict)
                        or acknowledgement.get("ok") is not True
                    ):
                        raise RuntimeError("waitlist_sheet_rejected")
            except Exception as exc:
                row = await self.session.get(WaitlistSheetDelivery, row.id) or row
                exhausted = (
                    row.attempt_count >= self.settings.waitlist_google_sheets_max_attempts
                )
                row.status = "failed" if exhausted else "retryable"
                row.last_error_type = exc.__class__.__name__[:120]
                row.next_retry_at = (
                    None
                    if exhausted
                    else datetime.now(UTC)
                    + timedelta(minutes=self.settings.waitlist_google_sheets_retry_minutes)
                )
                result["failed" if exhausted else "retryable"] += 1
            else:
                row = await self.session.get(WaitlistSheetDelivery, row.id) or row
                row.status = "sent"
                row.last_error_type = None
                row.delivered_at = datetime.now(UTC)
                result["sent"] += 1
            result["processed"] += 1
            await self.session.commit()
        return result

    async def process_contact_due(
        self,
        *,
        submission_id: UUID | None = None,
        limit: int = 25,
    ) -> dict[str, int]:
        now = datetime.now(UTC)
        abandoned = now - timedelta(
            minutes=self.settings.public_form_delivery_claim_timeout_minutes
        )
        retryable = ContactEmailDelivery.status.in_({"pending", "retryable"})
        if submission_id is not None:
            # A visitor-initiated retry is explicit and rate-limited at the route.
            # It may retry the same idempotent delivery before the background delay.
            query = select(ContactEmailDelivery).where(
                ContactEmailDelivery.submission_id == submission_id,
                retryable,
            )
        else:
            query = select(ContactEmailDelivery).where(
                or_(
                    and_(
                        retryable,
                        or_(
                            ContactEmailDelivery.next_retry_at.is_(None),
                            ContactEmailDelivery.next_retry_at <= now,
                        ),
                    ),
                    and_(
                        ContactEmailDelivery.status == "sending",
                        ContactEmailDelivery.last_attempt_at.is_not(None),
                        ContactEmailDelivery.last_attempt_at <= abandoned,
                    ),
                )
            )
        rows = list(
            (
                await self.session.scalars(
                    query.order_by(ContactEmailDelivery.created_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        result = {"processed": 0, "sent": 0, "retryable": 0, "failed": 0}
        for row in rows:
            row.status = "sending"
            row.attempt_count += 1
            row.last_attempt_at = datetime.now(UTC)
            row.next_retry_at = None
            await self.session.commit()
            try:
                submission = await self.session.get(ContactSubmission, row.submission_id)
                if submission is None:
                    raise RuntimeError("contact_submission_missing")
                subject, text_body, html_body = self._contact_email(submission)
                message_id = await AuthEmailService(self.settings).send_transactional(
                    recipient=row.recipient,
                    sender_email=row.sender,
                    sender_name="Hilal Markets",
                    reply_to=submission.normalized_email,
                    subject=subject,
                    text_body=text_body,
                    html_body=html_body,
                    idempotency_key=row.event_key,
                    purpose="public_contact_office",
                )
            except EmailDeliveryError as exc:
                row = await self.session.get(ContactEmailDelivery, row.id) or row
                exhausted = row.attempt_count >= self.settings.public_form_email_max_attempts
                row.status = "failed" if exhausted else "retryable"
                row.last_error_type = exc.code[:120]
                row.next_retry_at = (
                    None
                    if exhausted
                    else datetime.now(UTC)
                    + timedelta(minutes=self.settings.public_form_email_retry_minutes)
                )
                result["failed" if exhausted else "retryable"] += 1
            except Exception as exc:
                row = await self.session.get(ContactEmailDelivery, row.id) or row
                row.status = "failed"
                row.last_error_type = exc.__class__.__name__[:120]
                row.next_retry_at = None
                result["failed"] += 1
            else:
                row = await self.session.get(ContactEmailDelivery, row.id) or row
                row.status = "sent"
                row.provider_message_id = str(message_id)[:255]
                row.last_error_type = None
                row.sent_at = datetime.now(UTC)
                submission.status = "sent"
                result["sent"] += 1
            result["processed"] += 1
            await self.session.commit()
        return result

    async def waitlist_delivery_status(
        self, signup_id: UUID
    ) -> WaitlistDeliveryStatus:
        if not self.settings.waitlist_google_sheets_enabled:
            return "not_configured"
        status = await self.session.scalar(
            select(WaitlistSheetDelivery.status).where(
                WaitlistSheetDelivery.signup_id == signup_id
            )
        )
        if status == "sent":
            return "sent"
        if status in {"retryable", "failed"}:
            return "retrying"
        return "queued"

    async def contact_delivery_status(self, submission_id: UUID) -> str:
        return (
            await self.session.scalar(
                select(ContactEmailDelivery.status).where(
                    ContactEmailDelivery.submission_id == submission_id
                )
            )
            or "pending"
        )

    async def _ensure_waitlist_delivery(self, signup: WaitlistSignup) -> None:
        existing = await self.session.scalar(
            select(WaitlistSheetDelivery.id).where(
                WaitlistSheetDelivery.signup_id == signup.id
            )
        )
        if existing is None:
            now = datetime.now(UTC)
            self.session.add(
                WaitlistSheetDelivery(
                    signup_id=signup.id,
                    event_key=f"waitlist:{signup.id}:google-sheet",
                    status="pending",
                    attempt_count=0,
                    next_retry_at=now,
                    created_at=now,
                )
            )
            await self.session.flush()

    async def _ensure_contact_delivery(self, submission: ContactSubmission) -> None:
        existing = await self.session.scalar(
            select(ContactEmailDelivery.id).where(
                ContactEmailDelivery.submission_id == submission.id
            )
        )
        if existing is None:
            now = datetime.now(UTC)
            self.session.add(
                ContactEmailDelivery(
                    submission_id=submission.id,
                    event_key=f"contact:{submission.id}:office",
                    sender=self.settings.contact_form_sender_email.strip().casefold(),
                    recipient=self.settings.contact_form_recipient_email.strip().casefold(),
                    status="pending",
                    attempt_count=0,
                    next_retry_at=now,
                    created_at=now,
                )
            )
            await self.session.flush()

    def _sheet_endpoint(self) -> str:
        configured = self.settings.waitlist_google_sheets_webhook_url
        if configured is None:
            raise RuntimeError("waitlist_sheet_endpoint_missing")
        endpoint = (
            configured.get_secret_value()
            if hasattr(configured, "get_secret_value")
            else str(configured)
        ).strip()
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "script.google.com"
            or not parsed.path.startswith("/macros/s/")
            or not parsed.path.endswith("/exec")
        ):
            raise RuntimeError("waitlist_sheet_endpoint_invalid")
        return endpoint

    @staticmethod
    def _contact_email(submission: ContactSubmission) -> tuple[str, str, str]:
        subject = f"Hilal Markets contact: {submission.title}"[:250]
        text = (
            "A contact-page message was received.\n\n"
            f"Submitted: {submission.submitted_at.isoformat()}\n"
            f"Email: {submission.normalized_email}\n"
            f"Title: {submission.title}\n"
            f"Source page: {submission.source_page}\n\n"
            f"Description:\n{submission.description}\n"
        )
        return (
            subject,
            text,
            '<div style="font-family:Arial,sans-serif;line-height:1.55;color:#2b2e35">'
            f"{html.escape(text).replace(chr(10), '<br>')}</div>",
        )

    @staticmethod
    def _attribution(value: FirstTouchAttribution) -> dict[str, str]:
        payload = value.model_dump(exclude_none=True)
        if "referrer" in payload:
            payload["referrer"] = PublicFormsService._safe_url(payload["referrer"])
        if "landing_page" in payload:
            payload["landing_page"] = PublicFormsService._safe_url(
                payload["landing_page"]
            )
        return {key: item for key, item in payload.items() if item}

    @staticmethod
    def _safe_url(value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            return ""
        if parsed.scheme:
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))[
                :500
            ]
        return PublicFormsService._path(parsed.path or "/")

    @staticmethod
    def _path(value: str) -> str:
        parsed = urlsplit(value)
        path = parsed.path or "/"
        return path[:240] if path.startswith("/") else "/"

    @staticmethod
    def _request_hash(value: Mapping[str, object]) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()
