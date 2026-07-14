import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256, sha512
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PLAN_DEFINITIONS
from ai_market_monitor.db.models import AuditEvent, BillingEvent, Subscription
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.discord.service import DiscordRoleSyncService
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService
from ai_market_monitor.services.trials import TrialLifecycleService

SENSITIVE_KEYS = {
    "card",
    "card_number",
    "client_secret",
    "cvc",
    "cvv",
    "payment_method",
    "secret",
    "token",
}


class BillingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    provider: str
    checkout_url: str
    provider_session_id: str


@dataclass(frozen=True, slots=True)
class BillingPortalSession:
    provider: str
    portal_url: str


@dataclass(frozen=True, slots=True)
class BillingWebhookResult:
    event_id: str
    event_type: str
    processing_status: str
    replayed: bool
    user_id: UUID | None


class BillingProvider(Protocol):
    provider_name: str

    async def create_checkout_session(
        self, *, user_id: UUID, plan_code: str, success_url: str, cancel_url: str
    ) -> CheckoutSession: ...

    async def create_billing_portal_session(
        self,
        *,
        user_id: UUID,
        return_url: str,
        provider_customer_id: str | None = None,
    ) -> BillingPortalSession: ...


class StaticBillingProvider:
    provider_name = "static"

    async def create_checkout_session(
        self, *, user_id: UUID, plan_code: str, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        session_id = f"static_{user_id}_{plan_code}_{uuid4().hex[:12]}"
        return CheckoutSession(
            provider=self.provider_name,
            checkout_url=(
                f"{success_url}?checkout=pending&plan={plan_code}&user={user_id}"
                f"&session={session_id}"
            ),
            provider_session_id=session_id,
        )

    async def create_billing_portal_session(
        self,
        *,
        user_id: UUID,
        return_url: str,
        provider_customer_id: str | None = None,
    ) -> BillingPortalSession:
        return BillingPortalSession(provider=self.provider_name, portal_url=return_url)


class StripeBillingProvider:
    provider_name = "stripe"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_checkout_session(
        self, *, user_id: UUID, plan_code: str, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        price_id = self.settings.stripe_price_ids.get(plan_code)
        if not price_id:
            raise BillingError(
                "stripe_price_missing",
                f"No Stripe price is configured for plan {plan_code}.",
            )
        payload = await self._post(
            "/v1/checkout/sessions",
            {
                "mode": "subscription",
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "client_reference_id": str(user_id),
                "metadata[user_id]": str(user_id),
                "metadata[plan_code]": plan_code,
                "subscription_data[metadata][user_id]": str(user_id),
                "subscription_data[metadata][plan_code]": plan_code,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "allow_promotion_codes": "true",
            },
        )
        return CheckoutSession(
            provider=self.provider_name,
            checkout_url=str(payload["url"]),
            provider_session_id=str(payload["id"]),
        )

    async def create_billing_portal_session(
        self,
        *,
        user_id: UUID,
        return_url: str,
        provider_customer_id: str | None = None,
    ) -> BillingPortalSession:
        if not provider_customer_id:
            raise BillingError(
                "billing_customer_missing",
                "A Stripe customer must exist before opening the billing portal.",
            )
        payload = await self._post(
            "/v1/billing_portal/sessions",
            {"customer": provider_customer_id, "return_url": return_url},
        )
        return BillingPortalSession(
            provider=self.provider_name,
            portal_url=str(payload["url"]),
        )

    async def _post(self, path: str, data: dict[str, str]) -> dict[str, Any]:
        secret = self.settings.stripe_secret_key
        if secret is None:
            raise BillingError("stripe_secret_missing", "Stripe secret key is missing.")
        async with httpx.AsyncClient(
            base_url=str(self.settings.stripe_api_base).rstrip("/"),
            timeout=15,
            headers={
                "Authorization": f"Bearer {secret.get_secret_value()}",
                "User-Agent": "AI-Market-Monitor/0.1",
            },
        ) as client:
            response = await client.post(path, data=data)
        if response.is_error:
            request_id = response.headers.get("request-id")
            raise BillingError(
                "stripe_request_failed",
                f"Stripe request failed with status {response.status_code}"
                + (f" ({request_id})" if request_id else "."),
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise BillingError("stripe_response_invalid", "Stripe returned an invalid response.")
        return payload


class NowPaymentsBillingProvider:
    provider_name = "nowpayments"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_checkout_session(
        self, *, user_id: UUID, plan_code: str, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        api_key = self.settings.nowpayments_api_key
        if api_key is None:
            raise BillingError(
                "nowpayments_api_key_missing",
                "NOWPayments API key is missing.",
            )
        plan = PLAN_DEFINITIONS.get(plan_code)
        if plan is None:
            raise BillingError("plan_not_found", f"Plan {plan_code} was not found.")
        order_id = f"amm|{user_id}|{plan_code}|{uuid4().hex[:12]}"
        payload = {
            "price_amount": float(plan.monthly_price),
            "price_currency": plan.currency.lower(),
            "order_id": order_id,
            "order_description": f"HilalMarkets {plan.name} monthly access",
            "ipn_callback_url": (
                f"{str(self.settings.public_base_url).rstrip('/')}"
                "/api/v1/billing/webhooks/nowpayments"
            ),
            "success_url": success_url,
            "cancel_url": cancel_url,
            "is_fee_paid_by_user": False,
        }
        async with httpx.AsyncClient(
            base_url=str(self.settings.nowpayments_base_url).rstrip("/"),
            timeout=20,
            headers={
                "x-api-key": api_key.get_secret_value(),
                "Content-Type": "application/json",
                "User-Agent": "AI-Market-Monitor/0.1",
            },
        ) as client:
            response = await client.post("/v1/invoice", json=payload)
        body = self._json_response(response)
        invoice_url = body.get("invoice_url") or body.get("url")
        invoice_id = body.get("id") or body.get("invoice_id") or order_id
        if not invoice_url:
            raise BillingError(
                "nowpayments_invoice_invalid",
                "NOWPayments did not return an invoice URL.",
            )
        return CheckoutSession(
            provider=self.provider_name,
            checkout_url=str(invoice_url),
            provider_session_id=str(invoice_id),
        )

    async def create_billing_portal_session(
        self,
        *,
        user_id: UUID,
        return_url: str,
        provider_customer_id: str | None = None,
    ) -> BillingPortalSession:
        return BillingPortalSession(provider=self.provider_name, portal_url=return_url)

    @staticmethod
    def _json_response(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise BillingError(
                "nowpayments_response_invalid",
                "NOWPayments returned an invalid response.",
            ) from exc
        if response.is_error:
            message = body.get("message") if isinstance(body, dict) else response.reason_phrase
            raise BillingError(
                "nowpayments_request_failed",
                f"NOWPayments request failed: {message}",
            )
        if not isinstance(body, dict):
            raise BillingError(
                "nowpayments_response_invalid",
                "NOWPayments returned an invalid response.",
            )
        return body


class BillingWebhookVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    def verify(
        self,
        body: bytes,
        signature: str | None,
        *,
        provider: str = "generic",
        now: datetime | None = None,
    ) -> None:
        secret = self.settings.billing_webhook_secret
        if secret is None:
            if self.settings.is_production:
                raise BillingError("webhook_secret_missing", "Billing webhook secret is missing.")
            return
        if not signature:
            raise BillingError("signature_missing", "Missing billing webhook signature.")
        if provider == "stripe":
            self._verify_stripe(
                body,
                signature,
                secret.get_secret_value(),
                now=now or datetime.now(UTC),
            )
            return
        if provider == "nowpayments":
            self._verify_nowpayments(body, signature, secret.get_secret_value())
            return
        expected = hmac.new(secret.get_secret_value().encode("utf-8"), body, sha256).hexdigest()
        provided = self._extract_signature(signature)
        if not hmac.compare_digest(expected, provided):
            raise BillingError("invalid_signature", "Invalid billing webhook signature.")

    @staticmethod
    def _verify_stripe(
        body: bytes,
        signature: str,
        secret: str,
        *,
        now: datetime,
        tolerance_seconds: int = 300,
    ) -> None:
        parts: dict[str, list[str]] = {}
        for item in signature.split(","):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            parts.setdefault(key.strip(), []).append(value.strip())
        try:
            timestamp = int(parts["t"][0])
        except (KeyError, ValueError) as exc:
            raise BillingError(
                "invalid_signature", "Stripe signature timestamp is invalid."
            ) from exc
        if abs(int(now.timestamp()) - timestamp) > tolerance_seconds:
            raise BillingError("stale_signature", "Stripe webhook signature is too old.")
        signed_payload = str(timestamp).encode("ascii") + b"." + body
        expected = hmac.new(secret.encode("utf-8"), signed_payload, sha256).hexdigest()
        if not any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", [])):
            raise BillingError("invalid_signature", "Invalid Stripe webhook signature.")

    @staticmethod
    def _extract_signature(signature: str) -> str:
        if "," not in signature and "=" not in signature:
            return signature.strip()
        parts = {}
        for item in signature.split(","):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            parts[key.strip()] = value.strip()
        return parts.get("v1") or parts.get("sha256") or ""

    @staticmethod
    def _verify_nowpayments(body: bytes, signature: str | None, secret: str) -> None:
        if not signature:
            raise BillingError("signature_missing", "Missing NOWPayments IPN signature.")
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BillingError("invalid_payload", "NOWPayments IPN payload is invalid.") from exc
        signed_payload = json.dumps(
            _sort_json(payload),
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), signed_payload, sha512).hexdigest()
        if not hmac.compare_digest(expected, signature.strip()):
            raise BillingError("invalid_signature", "Invalid NOWPayments IPN signature.")


class BillingService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        provider: BillingProvider | None = None,
    ):
        self.session = session
        self.settings = settings
        if provider is not None:
            self.provider = provider
        elif settings.billing_provider == "stripe":
            self.provider = StripeBillingProvider(settings)
        elif settings.billing_provider == "nowpayments":
            self.provider = NowPaymentsBillingProvider(settings)
        elif settings.is_deployed:
            raise BillingError(
                "billing_provider_missing",
                "A real billing provider must be configured in staging and production.",
            )
        else:
            self.provider = StaticBillingProvider()

    async def checkout_session(
        self, *, user_id: UUID, plan_code: str, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        await PlanCatalogService(self.session).get_or_sync(plan_code)
        return await self.provider.create_checkout_session(
            user_id=user_id,
            plan_code=plan_code,
            success_url=success_url,
            cancel_url=cancel_url,
        )

    async def activate_free_plan(self, *, user_id: UUID, plan_code: str = "demo") -> Subscription:
        plan_definition = PLAN_DEFINITIONS.get(plan_code)
        if plan_definition is None:
            raise BillingError("plan_not_found", f"Plan {plan_code} was not found.")
        if plan_definition.monthly_price > 0:
            raise BillingError("plan_requires_payment", f"Plan {plan_code} requires payment.")
        plan = await PlanCatalogService(self.session).get_or_sync(plan_code)
        provider_subscription_id = f"free_{user_id}_{plan_code}"
        subscription = await self.session.scalar(
            select(Subscription).where(
                Subscription.provider == "free",
                Subscription.provider_subscription_id == provider_subscription_id,
            )
        )
        if subscription is None:
            subscription = Subscription(
                user_id=user_id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider="free",
                provider_customer_id=None,
                provider_subscription_id=provider_subscription_id,
            )
            self.session.add(subscription)
        subscription.user_id = user_id
        subscription.plan_id = plan.id
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.current_period_start = datetime.now(UTC)
        subscription.current_period_end = None
        await EntitlementService(self.session).snapshot(user_id)
        self._audit(
            user_id,
            "billing.free_plan_activated",
            "subscription",
            subscription.id,
            {"plan_code": plan_code},
        )
        await self.session.flush()
        return subscription

    async def billing_portal(self, *, user_id: UUID, return_url: str) -> BillingPortalSession:
        subscription = await self.session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.updated_at.desc())
        )
        return await self.provider.create_billing_portal_session(
            user_id=user_id,
            return_url=return_url,
            provider_customer_id=subscription.provider_customer_id if subscription else None,
        )

    async def process_verified_webhook(
        self, *, provider: str, body: bytes, signature: str | None
    ) -> BillingWebhookResult:
        BillingWebhookVerifier(self.settings).verify(body, signature, provider=provider)
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BillingError(
                "invalid_payload",
                "Billing webhook payload is not valid JSON.",
            ) from exc
        normalized = self._normalize_provider_payload(provider, payload)
        return await self.process_event(provider=provider, payload=normalized)

    @staticmethod
    def _normalize_provider_payload(provider: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if provider != "stripe":
            if provider == "nowpayments":
                return BillingService._normalize_nowpayments_payload(payload)
            return payload
        event_data = payload.get("data")
        if not isinstance(event_data, Mapping) or not isinstance(event_data.get("object"), Mapping):
            return payload
        stripe_object = dict(event_data["object"])
        metadata = dict(stripe_object.get("metadata") or {})
        customer = stripe_object.get("customer")
        subscription_value = stripe_object.get("subscription")
        object_type = str(stripe_object.get("object") or "")
        provider_subscription_id = (
            stripe_object.get("id") if object_type == "subscription" else subscription_value
        )
        plan_code = metadata.get("plan_code")
        if not plan_code and object_type == "subscription":
            items = dict(stripe_object.get("items") or {}).get("data") or []
            if items:
                price = dict(items[0].get("price") or {})
                plan_code = price.get("lookup_key")
        normalized_data = {
            "user_id": metadata.get("user_id") or stripe_object.get("client_reference_id"),
            "plan_code": plan_code,
            "provider_customer_id": customer,
            "provider_subscription_id": provider_subscription_id,
            "status": stripe_object.get("status"),
            "current_period_start": stripe_object.get("current_period_start"),
            "current_period_end": stripe_object.get("current_period_end"),
            "cancel_at_period_end": stripe_object.get("cancel_at_period_end", False),
        }
        return {
            "id": payload.get("id"),
            "type": payload.get("type"),
            "data": normalized_data,
        }

    @staticmethod
    def _normalize_nowpayments_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        order_id = str(payload.get("order_id") or "")
        user_id: str | None = None
        plan_code: str | None = None
        parts = order_id.split("|")
        if len(parts) >= 4 and parts[0] == "amm":
            user_id = parts[1]
            plan_code = parts[2]
        status = str(payload.get("payment_status") or payload.get("invoice_status") or "unknown")
        payment_id = str(
            payload.get("payment_id")
            or payload.get("invoice_id")
            or payload.get("purchase_id")
            or order_id
        )
        normalized_status = "active" if status == "finished" else status
        now = datetime.now(UTC)
        return {
            "id": f"nowpayments:{payment_id}:{status}",
            "type": f"payment.{status}",
            "data": {
                "user_id": user_id,
                "plan_code": plan_code,
                "provider_customer_id": str(payload.get("purchase_id") or "") or None,
                "provider_subscription_id": f"nowpayments_{payment_id}",
                "status": normalized_status,
                "current_period_start": now.isoformat(),
                "current_period_end": (now + timedelta(days=30)).isoformat(),
                "cancel_at_period_end": False,
            },
        }

    async def process_event(
        self, *, provider: str, payload: Mapping[str, Any]
    ) -> BillingWebhookResult:
        event_id = str(payload.get("id") or "")
        event_type = str(payload.get("type") or "")
        if not event_id or not event_type:
            raise BillingError("invalid_event", "Billing event requires id and type.")
        existing = await self.session.scalar(
            select(BillingEvent).where(BillingEvent.provider_event_id == event_id)
        )
        if existing is not None:
            return BillingWebhookResult(
                event_id=event_id,
                event_type=existing.event_type,
                processing_status=existing.processing_status,
                replayed=True,
                user_id=existing.user_id,
            )
        data = dict(payload.get("data") or {})
        user_id = self._parse_uuid(data.get("user_id"))
        event = BillingEvent(
            user_id=user_id,
            provider=provider,
            provider_event_id=event_id,
            event_type=event_type,
            processing_status="processing",
            payload_redacted=redact_payload(dict(payload)),
            created_at=datetime.now(UTC),
        )
        self.session.add(event)
        await self.session.flush()
        try:
            await self._apply_event(
                provider=provider, event_id=event_id, event_type=event_type, data=data
            )
        except Exception as exc:
            event.processing_status = "failed"
            event.error_code = getattr(exc, "code", exc.__class__.__name__)
            await self.session.flush()
            raise
        event.processing_status = "processed"
        event.processed_at = datetime.now(UTC)
        await self.session.flush()
        return BillingWebhookResult(
            event_id=event_id,
            event_type=event_type,
            processing_status=event.processing_status,
            replayed=False,
            user_id=event.user_id,
        )

    async def reprocess_failed_event(self, provider_event_id: str) -> BillingWebhookResult:
        event = await self.session.scalar(
            select(BillingEvent).where(BillingEvent.provider_event_id == provider_event_id)
        )
        if event is None:
            raise BillingError("event_missing", "Billing event was not found.")
        if event.processing_status != "failed":
            return BillingWebhookResult(
                event_id=event.provider_event_id,
                event_type=event.event_type,
                processing_status=event.processing_status,
                replayed=True,
                user_id=event.user_id,
            )
        event.processing_status = "processing"
        event.error_code = None
        payload = dict(event.payload_redacted or {})
        data = dict(payload.get("data") or {})
        try:
            await self._apply_event(
                provider=event.provider,
                event_id=event.provider_event_id,
                event_type=event.event_type,
                data=data,
            )
        except Exception as exc:
            event.processing_status = "failed"
            event.error_code = getattr(exc, "code", exc.__class__.__name__)
            await self.session.flush()
            raise
        event.processing_status = "processed"
        event.processed_at = datetime.now(UTC)
        await self.session.flush()
        return BillingWebhookResult(
            event_id=event.provider_event_id,
            event_type=event.event_type,
            processing_status=event.processing_status,
            replayed=False,
            user_id=event.user_id,
        )

    async def _apply_event(
        self, *, provider: str, event_id: str, event_type: str, data: dict[str, Any]
    ) -> None:
        if event_type in {
            "checkout.session.completed",
            "customer.subscription.created",
            "customer.subscription.updated",
            "subscription.created",
            "subscription.updated",
            "invoice.payment_succeeded",
            "payment.finished",
        }:
            subscription = await self._upsert_subscription(provider=provider, data=data)
            if subscription.status in {
                SubscriptionStatus.ACTIVE,
                SubscriptionStatus.TRIALING,
            }:
                await TrialLifecycleService(self.session, self.settings).convert(
                    subscription.user_id, subscription.id
                )
            await EntitlementService(self.session).snapshot(subscription.user_id)
            await EntitlementService(self.session).pause_excess_after_downgrade(
                subscription.user_id
            )
            await DiscordRoleSyncService(self.session).enqueue_for_user(
                user_id=subscription.user_id,
                source_event_id=event_id,
            )
            self._audit(
                subscription.user_id,
                "billing.subscription_synced",
                "subscription",
                subscription.id,
                {
                    "event_type": event_type,
                    "plan_id": str(subscription.plan_id),
                    "status": subscription.status.value,
                },
            )
            return
        if event_type in {
            "customer.subscription.deleted",
            "subscription.deleted",
            "subscription.canceled",
        }:
            subscription = await self._upsert_subscription(
                provider=provider, data=data, forced_status=SubscriptionStatus.CANCELED
            )
            subscription.canceled_at = datetime.now(UTC)
            await EntitlementService(self.session).snapshot(subscription.user_id)
            await EntitlementService(self.session).pause_excess_after_downgrade(
                subscription.user_id
            )
            await DiscordRoleSyncService(self.session).enqueue_for_user(
                user_id=subscription.user_id,
                source_event_id=event_id,
            )
            self._audit(
                subscription.user_id,
                "billing.subscription_canceled",
                "subscription",
                subscription.id,
                {},
            )
            return
        if event_type in {"invoice.payment_failed", "payment.failed"}:
            subscription = await self._upsert_subscription(
                provider=provider, data=data, forced_status=SubscriptionStatus.PAST_DUE
            )
            await EntitlementService(self.session).snapshot(subscription.user_id)
            await DiscordRoleSyncService(self.session).enqueue_for_user(
                user_id=subscription.user_id,
                source_event_id=event_id,
            )
            self._audit(
                subscription.user_id,
                "billing.payment_failed",
                "subscription",
                subscription.id,
                {},
            )
            return
        if event_type in {"payment.expired", "payment.failed", "payment.refunded"}:
            user_id = self._parse_uuid(data.get("user_id"))
            if user_id:
                self._audit(user_id, f"billing.{event_type}", "user", user_id, {})
            return
        if event_type in {"charge.refunded", "refund.created", "charge.dispute.created"}:
            user_id = self._parse_uuid(data.get("user_id"))
            if user_id:
                self._audit(user_id, f"billing.{event_type}", "user", user_id, {})

    async def _upsert_subscription(
        self,
        *,
        provider: str,
        data: dict[str, Any],
        forced_status: SubscriptionStatus | None = None,
    ) -> Subscription:
        user_id = self._parse_uuid(data.get("user_id"))
        if user_id is None:
            raise BillingError("user_missing", "Billing event did not include a user id.")
        plan_code = str(data.get("plan_code") or data.get("price_lookup_key") or "demo")
        plan = await PlanCatalogService(self.session).get_or_sync(plan_code)
        provider_subscription_id = str(
            data.get("provider_subscription_id") or data.get("subscription_id") or ""
        )
        if not provider_subscription_id:
            raise BillingError(
                "subscription_missing", "Billing event did not include a subscription id."
            )
        status = forced_status or self._status_from_provider(str(data.get("status") or "active"))
        subscription = await self.session.scalar(
            select(Subscription).where(
                Subscription.provider == provider,
                Subscription.provider_subscription_id == provider_subscription_id,
            )
        )
        if subscription is None:
            subscription = Subscription(
                user_id=user_id,
                plan_id=plan.id,
                status=status,
                provider=provider,
                provider_customer_id=self._optional_str(data.get("provider_customer_id")),
                provider_subscription_id=provider_subscription_id,
            )
            self.session.add(subscription)
        subscription.user_id = user_id
        subscription.plan_id = plan.id
        subscription.status = status
        subscription.provider = provider
        subscription.provider_customer_id = self._optional_str(data.get("provider_customer_id"))
        subscription.provider_subscription_id = provider_subscription_id
        subscription.current_period_start = self._parse_datetime(data.get("current_period_start"))
        subscription.current_period_end = self._parse_datetime(data.get("current_period_end"))
        subscription.cancel_at_period_end = bool(data.get("cancel_at_period_end", False))
        if subscription.status == SubscriptionStatus.CANCELED and subscription.canceled_at is None:
            subscription.canceled_at = datetime.now(UTC)
        await self.session.flush()
        return subscription

    @staticmethod
    def _status_from_provider(status: str) -> SubscriptionStatus:
        normalized = status.lower()
        if normalized in {"active", "paid"}:
            return SubscriptionStatus.ACTIVE
        if normalized in {"trialing", "trial"}:
            return SubscriptionStatus.TRIALING
        if normalized in {"past_due", "unpaid", "incomplete", "payment_failed"}:
            return SubscriptionStatus.PAST_DUE
        if normalized in {"canceled", "cancelled"}:
            return SubscriptionStatus.CANCELED
        if normalized in {"expired"}:
            return SubscriptionStatus.EXPIRED
        return SubscriptionStatus.PENDING

    @staticmethod
    def _parse_uuid(value: Any) -> UUID | None:
        if value in (None, ""):
            return None
        return value if isinstance(value, UUID) else UUID(str(value))

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=UTC)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        return None if value in (None, "") else str(value)

    def _audit(
        self,
        user_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID | None,
        metadata: dict[str, Any],
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=None,
                actor_type="system",
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id else None,
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )


def redact_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if key.lower() in SENSITIVE_KEYS:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact_payload(value)
        return redacted
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    return payload


def _sort_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_json(item) for item in value]
    return value
