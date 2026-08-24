from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import AccountEmailDelivery
from ai_market_monitor.services.email_branding import BrandedEmail, HilalMarketsEmailRenderer
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError

#: What kind of message each template is, for the header chip and the delivery record.
#:
#: The sender used to be handed a flat ``"account_access_changed"`` for every row in this
#: outbox, so a sign-up welcome and an affiliate approval both arrived labelled as an
#: access change — and the chip in the header, which is the one thing telling a reader
#: what kind of message they have opened, said the wrong thing on both.
PURPOSE_BY_TEMPLATE_KIND: dict[str, str] = {
    "signup_welcome": "account_notice",
    "access_changed": "account_access_changed",
    "affiliate_application_received": "affiliate_programme",
    "affiliate_application_approved": "affiliate_programme",
    "affiliate_application_rejected": "affiliate_programme",
}


class AccountEmailOutboxService:
    """Deliver idempotent account notices without delaying the action that raised them."""

    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def process_due(
        self,
        *,
        delivery_id: UUID | None = None,
        limit: int = 25,
    ) -> dict[str, int]:
        now = datetime.now(UTC)
        query = (
            select(AccountEmailDelivery)
            .where(
                AccountEmailDelivery.status.in_({"pending", "retryable"}),
                or_(
                    AccountEmailDelivery.next_retry_at.is_(None),
                    AccountEmailDelivery.next_retry_at <= now,
                ),
            )
            .order_by(AccountEmailDelivery.created_at.asc())
            .limit(min(max(limit, 1), 100))
        )
        if delivery_id is not None:
            query = query.where(AccountEmailDelivery.id == delivery_id)
        rows = list((await self.session.scalars(query)).all())
        result = {"processed": 0, "sent": 0, "retryable": 0, "failed": 0}
        for row in rows:
            row.status = "sending"
            row.attempt_count += 1
            row.last_attempt_at = datetime.now(UTC)
            row.next_retry_at = None
            await self.session.commit()
            try:
                rendered = self._render(row)
                message_id = await AuthEmailService(self.settings).send_transactional(
                    recipient=row.recipient,
                    subject=rendered.subject,
                    text_body=rendered.text_body,
                    html_body=rendered.html_body,
                    idempotency_key=row.event_key,
                    purpose=PURPOSE_BY_TEMPLATE_KIND.get(
                        row.template_kind, "account_notice"
                    ),
                )
            except EmailDeliveryError as exc:
                refreshed = await self.session.get(AccountEmailDelivery, row.id)
                if refreshed is None:
                    continue
                row = refreshed
                exhausted = row.attempt_count >= self.settings.payment_email_max_attempts
                row.status = "failed" if exhausted or not exc.retryable else "retryable"
                row.last_error = f"{exc.code}: {str(exc)}"[:500]
                row.next_retry_at = (
                    None
                    if row.status == "failed"
                    else datetime.now(UTC)
                    + timedelta(minutes=self.settings.payment_email_retry_minutes)
                )
                result[row.status] += 1
            except Exception as exc:
                refreshed = await self.session.get(AccountEmailDelivery, row.id)
                if refreshed is None:
                    continue
                row = refreshed
                row.status = "failed"
                row.last_error = f"render_failed: {exc.__class__.__name__}"[:500]
                row.next_retry_at = None
                result["failed"] += 1
            else:
                refreshed = await self.session.get(AccountEmailDelivery, row.id)
                if refreshed is None:
                    continue
                row = refreshed
                row.status = "sent"
                row.provider_message_id = message_id[:255]
                row.sent_at = datetime.now(UTC)
                row.last_error = None
                row.next_retry_at = None
                result["sent"] += 1
            result["processed"] += 1
            await self.session.commit()
        return result

    def _render(self, delivery: AccountEmailDelivery) -> BrandedEmail:
        payload = dict(delivery.payload_redacted or {})
        renderer = HilalMarketsEmailRenderer(self.settings)
        if delivery.template_kind == "signup_welcome":
            return renderer.signup_welcome(
                first_name=str(payload.get("first_name") or ""),
                email=delivery.recipient,
            )
        if delivery.template_kind == "access_changed":
            ends_at = _optional_datetime(payload.get("ends_at"))
            return renderer.access_changed(
                first_name=str(payload.get("first_name") or "there"),
                plan_name=str(payload.get("plan_name") or "Hilal Markets access"),
                duration_label=str(payload.get("duration_label") or "Not specified"),
                ends_at_label=_utc_label(ends_at) if ends_at else None,
            )
        # The three affiliate messages queue here rather than in an outbox of their own.
        # One outbox means one retry rule, one idempotency key, and one place a stuck
        # message is found — three of those, one per feature, is how a message quietly
        # stops being retried in the corner nobody looks at.
        if delivery.template_kind == "affiliate_application_received":
            return renderer.affiliate_application_received(
                first_name=str(payload.get("first_name") or ""),
                requested_code=str(payload.get("requested_code") or ""),
                decision_hours=int(payload.get("decision_hours") or 24),
            )
        if delivery.template_kind == "affiliate_application_approved":
            return renderer.affiliate_application_approved(
                first_name=str(payload.get("first_name") or ""),
                discount_code=str(payload.get("discount_code") or ""),
                discount_percent=str(payload.get("discount_percent") or "0"),
                commission_percent=str(payload.get("commission_percent") or "0"),
                referral_url=str(payload.get("referral_url") or ""),
                minimum_payout=str(payload.get("minimum_payout") or "$5.00"),
            )
        if delivery.template_kind == "affiliate_application_rejected":
            return renderer.affiliate_application_rejected(
                first_name=str(payload.get("first_name") or ""),
                reason=str(payload.get("reason") or "") or None,
            )
        raise ValueError("Unsupported account email template.")


def _optional_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _utc_label(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d %b %Y, %H:%M UTC")
