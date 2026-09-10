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
from ai_market_monitor.core.dashboard_paths import MONITOR_PATH
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    BillingEvent,
    PaymentEmailDelivery,
    Plan,
    PlanMoveMoneyOwed,
    Subscription,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider
from ai_market_monitor.services.billing import billing_provider_capabilities
from ai_market_monitor.services.email_branding import (
    EmailLink,
    HilalMarketsEmailRenderer,
    button,
    fact_table,
    greeting_line,
    lead,
    link_row,
    message_kind_label,
    note,
)
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
    """The receipt, built from the same blocks as every other Hilal Markets email.

    It used to carry a frame of its own: its own header, its own footer, its own colours
    and its own font stack, written out inside ``payment_success.html``. That is the
    failure this codebase keeps repeating — the same decision made twice, in two places,
    drifting apart. The receipt's own header had already stopped matching the rest, and
    its type stack asked for Arial where the shared one asks for Onest first.

    So the HTML template is gone and the words are laid out with the shared blocks. The
    plain-text template stays: plain text has nothing to drift about.
    """

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
        period_end_date: datetime | None,
        renews_automatically: bool,
        receipt_url: str | None,
        plan_limits: dict[str, Any],
    ) -> RenderedPaymentEmail:
        base_url = str(self.settings.public_base_url).rstrip("/")
        # Every value the message shows, worked out once. The HTML and the plain-text
        # part are then two views of the same facts rather than two calculations of them.
        display_name = first_name or "there"
        amount_label = f"{amount:.2f}" if amount is not None else None
        renewal_label = (
            _utc_label(period_end_date)
            if period_end_date and renews_automatically
            else None
        )
        access_until_label = (
            _utc_label(period_end_date)
            if period_end_date and not renews_automatically
            else None
        )
        limits = _main_limits(plan_limits)
        dashboard_url = f"{base_url}/dashboard"
        create_watch_plan_url = f"{base_url}{MONITOR_PATH}"
        billing_url = f"{base_url}/dashboard/billing"
        support_url = f"{base_url}/dashboard/support"
        terms_url = f"{base_url}/terms"
        risk_url = f"{base_url}/risk-disclosure"
        context = {
            "first_name": display_name,
            "plan_name": plan_name,
            "billing_frequency": billing_frequency,
            "amount": amount_label,
            "currency": currency.upper(),
            "payment_date": _utc_label(payment_date),
            "renewal_date": renewal_label,
            "access_until": access_until_label,
            "renews_automatically": renews_automatically,
            "receipt_url": receipt_url,
            "limits": limits,
            "dashboard_url": dashboard_url,
            "create_watch_plan_url": create_watch_plan_url,
            "billing_url": billing_url,
            "support_url": support_url,
            "terms_url": terms_url,
            "risk_url": risk_url,
        }

        rows: list[tuple[str, str | EmailLink]] = [
            ("Plan", plan_name),
            ("Billing", billing_frequency.title()),
        ]
        if amount_label:
            rows.append(("Amount", f"{amount_label} {currency.upper()}"))
        rows.append(("Paid", _utc_label(payment_date)))
        if renewal_label:
            rows.append(("Next automatic renewal", renewal_label))
        elif access_until_label:
            rows.append(("Access through", access_until_label))
        if receipt_url:
            rows.append(("Receipt", EmailLink("Open receipt or invoice", receipt_url)))

        limit_rows: list[tuple[str, str | EmailLink]] = [
            (limit["label"], limit["value"]) for limit in limits
        ]
        # Word for word what the receipt already said. The frame changed; the promise
        # made to somebody who has just paid did not.
        renewal_sentence = (
            "Your plan renews automatically under the agreement shown at checkout."
            if renews_automatically
            else "This payment provides 30-day access and does not renew automatically."
        )
        content = (
            greeting_line(f"Assalamu Alaikum {display_name},")
            + lead(
                f"Your payment has been verified and your {plan_name} access is ready. "
                "Thank you for trusting Hilal Markets to help you monitor with clarity."
            )
            + fact_table(rows)
            + fact_table(limit_rows, title="Main plan limits")
            + button("Open Hilal Markets", dashboard_url)
            + link_row(
                [
                    EmailLink("Create a Watchlist", create_watch_plan_url),
                    EmailLink("Manage billing", billing_url),
                    EmailLink("Support", support_url),
                    EmailLink("Terms", terms_url),
                    EmailLink("Risk Disclosure", risk_url),
                ]
            )
            + note(renewal_sentence)
        )
        return RenderedPaymentEmail(
            subject=f"Your Hilal Markets {plan_name} plan is active",
            text_body=TEMPLATES.get_template("payment_success.txt").render(**context).strip(),
            html_body=HilalMarketsEmailRenderer(self.settings).shell(
                title=f"Your {plan_name} plan is active",
                eyebrow=message_kind_label("payment_success"),
                preheader=f"Payment confirmed. Your {plan_name} plan is active.",
                content_html=content,
                footer_reason=(
                    "You are receiving this because a payment on your Hilal Markets "
                    f"account was confirmed. {renewal_sentence}"
                ),
            ),
        )

    def render_money_owed(
        self,
        *,
        first_name: str,
        from_plan_name: str,
        to_plan_name: str,
        amount: Decimal,
        currency: str,
        due_at: datetime,
    ) -> RenderedPaymentEmail:
        """Tell the customer what a person will return after their plan move."""

        from ai_market_monitor.services.plan_replacements import (
            manual_return_window_words,
        )

        display_name = first_name or "there"
        window = manual_return_window_words()
        amount_label = f"{amount:.2f} {currency.upper()}"
        billing_url = f"{str(self.settings.public_base_url).rstrip('/')}/dashboard/billing"
        lead_text = (
            f"Your {to_plan_name} plan is active. Your old {from_plan_name} plan ended "
            f"today. We worked out {amount_label} for the unused time. A person will "
            f"send this money to you by hand {window}. Nothing was returned automatically."
        )
        rows: list[tuple[str, str | EmailLink]] = [
            ("Old plan", from_plan_name),
            ("New plan", to_plan_name),
            ("Money to be sent", amount_label),
            ("Send by", _utc_label(due_at)),
        ]
        content = (
            greeting_line(f"Assalamu Alaikum {display_name},")
            + lead(lead_text)
            + fact_table(rows)
            + button("Open billing", billing_url)
            + note(
                "You do not need to ask the payment company for this money. "
                "If it has not arrived by the time shown above, write to Hilal Markets support."
            )
        )
        text_body = (
            f"Assalamu Alaikum {display_name},\n\n{lead_text}\n\n"
            f"Old plan: {from_plan_name}\nNew plan: {to_plan_name}\n"
            f"Money to be sent: {amount_label}\nSend by: {_utc_label(due_at)}\n\n"
            "If it has not arrived by that time, write to Hilal Markets support."
        )
        return RenderedPaymentEmail(
            subject=f"Money due after your move to {to_plan_name}",
            text_body=text_body,
            html_body=HilalMarketsEmailRenderer(self.settings).shell(
                title="Money due after your plan move",
                eyebrow="Plan payment update",
                preheader=f"We will send {amount_label} {window}.",
                content_html=content,
                footer_reason=(
                    "You are receiving this because a confirmed payment moved your "
                    "Hilal Markets account to a different paid plan."
                ),
            ),
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
        capabilities = billing_provider_capabilities(billing_event.provider)
        billing_cycle = str(data.get("billing_cycle") or "").lower()
        if billing_cycle in {"annual", "annual_auto_renewal"}:
            billing_frequency = "annual auto-renewal"
        elif capabilities.supports_recurring_billing:
            billing_frequency = "monthly auto-renewal"
        else:
            billing_frequency = "30-day access"
        delivery = PaymentEmailDelivery(
            user_id=subscription.user_id,
            billing_event_id=billing_event.id,
            event_key=event_key,
            purpose="payment_success",
            recipient=identity.normalized_identifier,
            plan_code=plan.code,
            billing_frequency=billing_frequency,
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

    async def enqueue_money_owed(
        self,
        *,
        billing_event: BillingEvent,
        money_owed: PlanMoveMoneyOwed,
    ) -> PaymentEmailDelivery | None:
        identity = await self.session.scalar(
            select(UserIdentity)
            .where(
                UserIdentity.user_id == money_owed.user_id,
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.is_primary.is_(True),
                UserIdentity.is_verified.is_(True),
            )
            .limit(1)
        )
        if identity is None or not identity.normalized_identifier:
            return None
        event_key = f"plan-move-money-owed:{money_owed.idempotency_key}"
        existing = await self.session.scalar(
            select(PaymentEmailDelivery).where(PaymentEmailDelivery.event_key == event_key)
        )
        if existing is not None:
            return existing
        delivery = PaymentEmailDelivery(
            user_id=money_owed.user_id,
            billing_event_id=billing_event.id,
            event_key=event_key,
            purpose="plan_move_money_owed",
            recipient=identity.normalized_identifier,
            plan_code=money_owed.to_plan_code,
            billing_frequency="plan move",
            amount=money_owed.amount_owed,
            currency=money_owed.currency,
            payment_date=money_owed.ended_at,
            renewal_date=money_owed.due_at,
            receipt_url=None,
            plan_limits={"from_plan_code": money_owed.from_plan_code},
            status="pending",
            attempt_count=0,
            next_retry_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
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
                    purpose=row.purpose,
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
        billing_event = await self.session.get(BillingEvent, delivery.billing_event_id)
        if user is None or plan is None or billing_event is None:
            raise RuntimeError("Payment email user, plan, or billing event is unavailable.")
        capabilities = billing_provider_capabilities(billing_event.provider)
        first_name = await self._billing_first_name(
            billing_event=billing_event,
            user=user,
        )
        if delivery.purpose == "plan_move_money_owed":
            from_code = str(delivery.plan_limits.get("from_plan_code") or "")
            from_plan = await self.session.scalar(select(Plan).where(Plan.code == from_code))
            if from_plan is None or delivery.amount is None or delivery.renewal_date is None:
                raise RuntimeError("Plan move email is missing its saved money details.")
            return PaymentEmailRenderer(self.settings).render_money_owed(
                first_name=first_name,
                from_plan_name=from_plan.name,
                to_plan_name=plan.name,
                amount=delivery.amount,
                currency=delivery.currency,
                due_at=delivery.renewal_date,
            )
        return PaymentEmailRenderer(self.settings).render(
            first_name=first_name,
            plan_name=plan.name,
            billing_frequency=delivery.billing_frequency,
            amount=delivery.amount,
            currency=delivery.currency,
            payment_date=delivery.payment_date,
            period_end_date=delivery.renewal_date,
            renews_automatically=capabilities.supports_recurring_billing,
            receipt_url=delivery.receipt_url,
            plan_limits=delivery.plan_limits,
        )

    async def _billing_first_name(
        self,
        *,
        billing_event: BillingEvent,
        user: User,
    ) -> str:
        payload = dict(billing_event.payload_redacted or {})
        data = dict(payload.get("data") or {})
        try:
            attempt_id = UUID(str(data.get("checkout_attempt_id") or ""))
        except (TypeError, ValueError):
            attempt_id = None
        if attempt_id is not None:
            attempt = await self.session.scalar(
                select(BillingCheckoutAttempt).where(
                    BillingCheckoutAttempt.id == attempt_id,
                    BillingCheckoutAttempt.user_id == user.id,
                )
            )
            if attempt is not None:
                profile = dict(attempt.billing_profile or {})
                submitted_name = str(profile.get("first_name") or "").strip()
                if submitted_name:
                    return submitted_name
        return ((user.display_name or "").strip().split() or ["there"])[0]


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
        ("active_strategies", "Active Watchlists"),
        ("symbols_per_strategy", "Markets per Watchlist"),
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
