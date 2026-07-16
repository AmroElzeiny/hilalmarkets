from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    BillingEvent,
    PaymentEmailDelivery,
    Plan,
    Subscription,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError

TEMPLATE_DIRECTORY = Path(__file__).resolve().parents[1] / "templates" / "email"
TEMPLATES = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIRECTORY)),
    autoescape=select_autoescape(enabled_extensions=("html", "xml")),
)


@dataclass(frozen=True, slots=True)
class RenderedPaymentEmail:
    subject: str
    text_body: str
    html_body: str


class PaymentEmailRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def render(
        self,
        *,
        first_name: str,
        plan_name: str,
        billing_frequency: str,
        amount: Decimal | None,
        currency: str,
        payment_date: datetime,
        renewal_date: datetime | None,
        receipt_url: str | None,
        plan_limits: dict[str, Any],
    ) -> RenderedPaymentEmail:
        base_url = str(self.settings.public_base_url).rstrip("/")
        context = {
            "first_name": first_name or "there",
            "plan_name": plan_name,
            "billing_frequency": billing_frequency,
            "amount": f"{amount:.2f}" if amount is not None else None,
            "currency": currency.upper(),
            "payment_date": _utc_label(payment_date),
            "renewal_date": _utc_label(renewal_date) if renewal_date else None,
            "receipt_url": receipt_url,
            "limits": _main_limits(plan_limits),
            "dashboard_url": f"{base_url}/dashboard",
            "create_watch_plan_url": f"{base_url}/dashboard/strategies/new",
            "billing_url": f"{base_url}/dashboard/billing",
            "support_url": f"{base_url}/dashboard/support",
            "terms_url": f"{base_url}/terms",
            "risk_url": f"{base_url}/risk-disclosure",
            "legal_name": self.settings.site_legal_name or "HilalMarkets",
            "company_address": self.settings.site_company_address,
        }
        return RenderedPaymentEmail(
            subject=f"Your HilalMarkets {plan_name} plan is active",
            text_body=TEMPLATES.get_template("payment_success.txt").render(**context).strip(),
            html_body=TEMPLATES.get_template("payment_success.html").render(**context),
        )


class PaymentEmailOutboxService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def enqueue(
        self,
        *,
        billing_event: BillingEvent,
        subscription: Subscription,
        data: dict[str, Any],
    ) -> PaymentEmailDelivery | None:
        identity = await self.session.scalar(
            select(UserIdentity)
            .where(
                UserIdentity.user_id == subscription.user_id,
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.is_primary.is_(True),
                UserIdentity.is_verified.is_(True),
            )
            .limit(1)
        )
        if identity is None or not identity.normalized_identifier:
            return None
        plan = await self.session.get(Plan, subscription.plan_id)
        if plan is None:
            return None
        period_reference = (
            subscription.current_period_end.isoformat()
            if subscription.current_period_end
            else str(data.get("provider_payment_reference") or billing_event.provider_event_id)
        )
        event_key = (
            f"payment-success:{billing_event.provider}:"
            f"{subscription.provider_subscription_id}:{period_reference}"
        )
        existing = await self.session.scalar(
            select(PaymentEmailDelivery).where(PaymentEmailDelivery.event_key == event_key)
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        amount = _decimal_or_none(data.get("amount"))
        if amount is None:
            amount = plan.price_monthly
        features = dict(plan.features or {})
        limits = dict(features.get("limits") or {})
        delivery = PaymentEmailDelivery(
            user_id=subscription.user_id,
            billing_event_id=billing_event.id,
            event_key=event_key,
            recipient=identity.normalized_identifier,
            plan_code=plan.code,
            billing_frequency="monthly",
            amount=amount,
            currency=str(data.get("currency") or plan.currency).upper()[:3],
            payment_date=billing_event.created_at or now,
            renewal_date=subscription.current_period_end,
            receipt_url=_safe_url(data.get("receipt_url")),
            plan_limits=limits,
            status="pending",
            attempt_count=0,
            next_retry_at=now,
            created_at=now,
        )
        self.session.add(delivery)
        await self.session.flush()
        return delivery

    async def process_due(
        self,
        *,
        delivery_id: UUID | None = None,
        limit: int = 25,
    ) -> dict[str, int]:
        now = datetime.now(UTC)
        query = (
            select(PaymentEmailDelivery)
            .where(
                PaymentEmailDelivery.status.in_({"pending", "retryable"}),
                or_(
                    PaymentEmailDelivery.next_retry_at.is_(None),
                    PaymentEmailDelivery.next_retry_at <= now,
                ),
            )
            .order_by(PaymentEmailDelivery.created_at.asc())
            .limit(limit)
        )
        if delivery_id is not None:
            query = query.where(PaymentEmailDelivery.id == delivery_id)
        rows = list((await self.session.scalars(query)).all())
        result = {"processed": 0, "sent": 0, "retryable": 0, "failed": 0}
        for row in rows:
            row.status = "sending"
            row.attempt_count += 1
            row.last_attempt_at = datetime.now(UTC)
            row.next_retry_at = None
            await self.session.commit()
            try:
                rendered = await self._render(row.id)
                message_id = await AuthEmailService(self.settings).send_transactional(
                    recipient=row.recipient,
                    subject=rendered.subject,
                    text_body=rendered.text_body,
                    html_body=rendered.html_body,
                    idempotency_key=row.event_key,
                    purpose="payment_success",
                )
            except EmailDeliveryError as exc:
                refreshed = await self.session.get(PaymentEmailDelivery, row.id)
                if refreshed is None:
                    continue
                row = refreshed
                exhausted = row.attempt_count >= self.settings.payment_email_max_attempts
                row.status = "failed" if exhausted else "retryable"
                row.last_error = f"{exc.code}: {str(exc)}"[:500]
                row.next_retry_at = (
                    None
                    if exhausted
                    else datetime.now(UTC)
                    + timedelta(minutes=self.settings.payment_email_retry_minutes)
                )
                result["failed" if exhausted else "retryable"] += 1
            except Exception as exc:
                refreshed = await self.session.get(PaymentEmailDelivery, row.id)
                if refreshed is None:
                    continue
                row = refreshed
                row.status = "failed"
                row.last_error = f"render_failed: {exc.__class__.__name__}"[:500]
                row.next_retry_at = None
                result["failed"] += 1
            else:
                refreshed = await self.session.get(PaymentEmailDelivery, row.id)
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

    async def _render(self, delivery_id: UUID) -> RenderedPaymentEmail:
        delivery = await self.session.get(PaymentEmailDelivery, delivery_id)
        if delivery is None:
            raise RuntimeError("Payment email delivery disappeared before rendering.")
        user = await self.session.get(User, delivery.user_id)
        plan = await self.session.scalar(select(Plan).where(Plan.code == delivery.plan_code))
        if user is None or plan is None:
            raise RuntimeError("Payment email user or plan is unavailable.")
        first_name = ((user.display_name or "").strip().split() or ["there"])[0]
        return PaymentEmailRenderer(self.settings).render(
            first_name=first_name,
            plan_name=plan.name,
            billing_frequency=delivery.billing_frequency,
            amount=delivery.amount,
            currency=delivery.currency,
            payment_date=delivery.payment_date,
            renewal_date=delivery.renewal_date,
            receipt_url=delivery.receipt_url,
            plan_limits=delivery.plan_limits,
        )


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _safe_url(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:2000] if text.startswith(("https://", "http://")) else None


def _main_limits(limits: dict[str, Any]) -> list[dict[str, str]]:
    labels = (
        ("active_strategies", "Active Watch Plans"),
        ("symbols_per_strategy", "Markets per Watch Plan"),
        ("on_demand_scans_per_month", "Market checks per month"),
        ("detailed_history_days", "Detailed evidence history"),
    )
    result: list[dict[str, str]] = []
    for key, label in labels:
        if key not in limits:
            continue
        value = limits[key]
        rendered = "Unlimited" if isinstance(value, int) and value >= 100_000 else str(value)
        if key == "detailed_history_days" and rendered != "Unlimited":
            rendered = f"{rendered} days"
        result.append({"label": label, "value": rendered})
    return result


def _utc_label(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%d %b %Y, %H:%M UTC")
