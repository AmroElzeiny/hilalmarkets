from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import AccountEmailDelivery
from ai_market_monitor.services.email_branding import BrandedEmail, HilalMarketsEmailRenderer
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError


class AccountEmailOutboxService:
    """Deliver idempotent account-access notices without delaying the admin action."""

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
                    purpose="account_access_changed",
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
        if delivery.template_kind != "access_changed":
            raise ValueError("Unsupported account email template.")
        payload = dict(delivery.payload_redacted or {})
        ends_at = _optional_datetime(payload.get("ends_at"))
        return HilalMarketsEmailRenderer(self.settings).access_changed(
            first_name=str(payload.get("first_name") or "there"),
            plan_name=str(payload.get("plan_name") or "HilalMarkets access"),
            duration_label=str(payload.get("duration_label") or "Not specified"),
            ends_at_label=_utc_label(ends_at) if ends_at else None,
        )


def _optional_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _utc_label(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d %b %Y, %H:%M UTC")
