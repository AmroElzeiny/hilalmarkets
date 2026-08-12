import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256, sha512
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PUBLIC_PLAN_PRESENTATIONS,
    PURCHASABLE_PLAN_CODES,
    effective_monthly_price,
    plan_offer,
)
from ai_market_monitor.db.models import (
    AuditEvent,
    BillingCheckoutAttempt,
    BillingEvent,
    Plan,
    Subscription,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, SubscriptionStatus
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService
from ai_market_monitor.services.provider_reliability import ProviderCallError
from ai_market_monitor.services.provider_runtime import provider_request
from ai_market_monitor.services.trials import TrialLifecycleService

SENSITIVE_KEYS = {
    "address",
    "address_line1",
    "address_line2",
    "card",
    "card_number",
    "city",
    "client_secret",
    "country",
    "cvc",
    "cvv",
    "email",
    "first_name",
    "last_name",
    "payment_method",
    "postal_code",
    "region",
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
class CheckoutAttemptResult:
    attempt: BillingCheckoutAttempt
    duplicate: bool


@dataclass(frozen=True, slots=True)
class BillingPortalSession:
    provider: str
    portal_url: str


@dataclass(frozen=True, slots=True)
class BillingProviderCapabilities:
    supports_recurring_billing: bool
    supports_customer_portal: bool
    supports_automatic_cancellation: bool
    supports_refunds: bool
    supports_invoice_receipts: bool


STATIC_BILLING_CAPABILITIES = BillingProviderCapabilities(
    supports_recurring_billing=False,
    supports_customer_portal=False,
    supports_automatic_cancellation=False,
    supports_refunds=False,
    supports_invoice_receipts=False,
)
STRIPE_BILLING_CAPABILITIES = BillingProviderCapabilities(
    supports_recurring_billing=True,
    supports_customer_portal=True,
    supports_automatic_cancellation=True,
    supports_refunds=True,
    supports_invoice_receipts=True,
)
NOWPAYMENTS_BILLING_CAPABILITIES = BillingProviderCapabilities(
    supports_recurring_billing=False,
    supports_customer_portal=False,
    supports_automatic_cancellation=False,
    supports_refunds=False,
    supports_invoice_receipts=True,
)
CREEM_BILLING_CAPABILITIES = BillingProviderCapabilities(
    supports_recurring_billing=True,
    supports_customer_portal=True,
    supports_automatic_cancellation=True,
    supports_refunds=True,
    supports_invoice_receipts=True,
)


def billing_provider_capabilities(provider: str) -> BillingProviderCapabilities:
    capabilities = {
        "static": STATIC_BILLING_CAPABILITIES,
        "stripe": STRIPE_BILLING_CAPABILITIES,
        "nowpayments": NOWPAYMENTS_BILLING_CAPABILITIES,
        "creem": CREEM_BILLING_CAPABILITIES,
    }
    try:
        return capabilities[provider]
    except KeyError as exc:
        raise BillingError(
            "billing_provider_unknown",
            "The billing provider is not supported.",
        ) from exc


def configured_billing_provider(settings: Settings, payment_method: str) -> str:
    if payment_method == "card":
        provider: str = settings.billing_card_provider
        if provider == "disabled" and settings.billing_provider in {"stripe", "creem", "static"}:
            provider = settings.billing_provider
    elif payment_method == "crypto":
        provider = settings.billing_crypto_provider
        if provider == "disabled" and settings.billing_provider == "nowpayments":
            provider = settings.billing_provider
    else:
        raise BillingError("payment_method_invalid", "Choose card or crypto payment.")
    if provider == "disabled":
        raise BillingError(
            "payment_method_unavailable",
            "That payment method is not currently available.",
        )
    return provider


@dataclass(frozen=True, slots=True)
class BillingWebhookResult:
    event_id: str
    event_type: str
    processing_status: str
    replayed: bool
    user_id: UUID | None


class BillingProvider(Protocol):
    provider_name: str
    capabilities: BillingProviderCapabilities

    async def create_checkout_session(
        self,
        *,
        user_id: UUID,
        checkout_attempt_id: UUID,
        plan_code: str,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str | None,
        success_url: str,
        cancel_url: str,
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
    capabilities = STATIC_BILLING_CAPABILITIES

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_checkout_session(
        self,
        *,
        user_id: UUID,
        checkout_attempt_id: UUID,
        plan_code: str,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        del billing_cycle, customer_email
        session_id = f"static_{checkout_attempt_id.hex}"
        token = hmac.new(
            self.settings.app_secret_key.get_secret_value().encode("utf-8"),
            f"static-checkout:{checkout_attempt_id}:{user_id}".encode(),
            sha256,
        ).hexdigest()
        separator = "&" if "?" in success_url else "?"
        attempt_parameter = "" if "attempt=" in success_url else f"attempt={checkout_attempt_id}&"
        return CheckoutSession(
            provider=self.provider_name,
            checkout_url=(
                f"{success_url}{separator}{attempt_parameter}"
                f"static_session={session_id}&static_token={token}"
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
        raise BillingError(
            "billing_portal_unavailable",
            "This local billing adapter does not provide a customer portal.",
        )


class StripeBillingProvider:
    provider_name = "stripe"
    capabilities = STRIPE_BILLING_CAPABILITIES

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_checkout_session(
        self,
        *,
        user_id: UUID,
        checkout_attempt_id: UUID,
        plan_code: str,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        del plan_name, amount, currency
        price_id = self.settings.stripe_price_ids.get(
            f"{plan_code}_{_cycle_key(billing_cycle)}"
        ) or self.settings.stripe_price_ids.get(plan_code)
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
                "metadata[checkout_attempt_id]": str(checkout_attempt_id),
                "subscription_data[metadata][user_id]": str(user_id),
                "subscription_data[metadata][plan_code]": plan_code,
                "subscription_data[metadata][checkout_attempt_id]": str(checkout_attempt_id),
                "success_url": success_url,
                "cancel_url": cancel_url,
                "allow_promotion_codes": "true",
                **({"customer_email": customer_email} if customer_email else {}),
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
        response = await provider_request(
            self.settings,
            "POST",
            f"{str(self.settings.stripe_api_base).rstrip('/')}{path}",
            provider="stripe",
            operation=path.strip("/").replace("/", "_"),
            timeout=15,
            # Money. A checkout that was created and then timed out looks exactly like one
            # that was not, and a retry would charge a customer twice. Never repeated.
            mutation_committed=True,
            headers={
                "Authorization": f"Bearer {secret.get_secret_value()}",
                "User-Agent": "AI-Market-Monitor/0.1",
            },
            data=data,
        )
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
    capabilities = NOWPAYMENTS_BILLING_CAPABILITIES

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_checkout_session(
        self,
        *,
        user_id: UUID,
        checkout_attempt_id: UUID,
        plan_code: str,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        del billing_cycle, customer_email
        api_key = self.settings.nowpayments_api_key
        if api_key is None:
            raise BillingError(
                "nowpayments_api_key_missing",
                "NOWPayments API key is missing.",
            )
        order_id = f"hm|{checkout_attempt_id.hex}|{plan_code}"
        payload = {
            "price_amount": float(amount),
            "price_currency": currency.lower(),
            "order_id": order_id,
            "order_description": f"HilalMarkets {plan_name} 30-day access",
            "ipn_callback_url": (
                f"{str(self.settings.public_base_url).rstrip('/')}"
                "/api/v1/billing/webhooks/nowpayments"
            ),
            "success_url": success_url,
            "cancel_url": cancel_url,
            "is_fee_paid_by_user": False,
        }
        response = await provider_request(
            self.settings,
            "POST",
            f"{str(self.settings.nowpayments_base_url).rstrip('/')}/v1/invoice",
            provider="nowpayments",
            operation="create_invoice",
            timeout=20,
            # An invoice that exists on the provider's side must not be created twice.
            mutation_committed=True,
            headers={
                "x-api-key": api_key.get_secret_value(),
                "Content-Type": "application/json",
                "User-Agent": "AI-Market-Monitor/0.1",
            },
            json=payload,
        )
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
        raise BillingError(
            "billing_portal_unavailable",
            "NOWPayments invoices provide 30-day access and do not include a subscription portal.",
        )

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


class CreemBillingProvider:
    provider_name = "creem"
    capabilities = CREEM_BILLING_CAPABILITIES

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_checkout_session(
        self,
        *,
        user_id: UUID,
        checkout_attempt_id: UUID,
        plan_code: str,
        plan_name: str,
        amount: Decimal,
        currency: str,
        billing_cycle: str,
        customer_email: str | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        del plan_name, amount, currency, cancel_url
        if not customer_email:
            raise BillingError(
                "billing_email_missing",
                "A verified account email is required for card checkout.",
            )
        product_key = f"{plan_code}_{_cycle_key(billing_cycle)}"
        product_id = self.settings.creem_product_ids.get(product_key)
        if not product_id:
            raise BillingError(
                "creem_product_missing",
                "This plan and billing period are not configured for card checkout.",
            )
        if not product_id.startswith("prod_"):
            raise BillingError(
                "creem_product_invalid",
                "The configured Creem product identifier is invalid.",
            )
        payload = await self._post(
            "/v1/checkouts",
            {
                "product_id": product_id,
                "request_id": str(checkout_attempt_id),
                "units": 1,
                "customer": {"email": customer_email},
                "success_url": success_url,
                "metadata": {
                    "checkout_attempt_id": str(checkout_attempt_id),
                    "user_id": str(user_id),
                    "plan_code": plan_code,
                    "billing_cycle": billing_cycle,
                },
            },
        )
        checkout_url = str(payload.get("checkout_url") or "")
        provider_session_id = str(payload.get("id") or "")
        if not checkout_url.startswith("https://") or not provider_session_id:
            raise BillingError(
                "creem_checkout_invalid",
                "Creem did not return a valid secure checkout.",
            )
        return CheckoutSession(
            provider=self.provider_name,
            checkout_url=checkout_url,
            provider_session_id=provider_session_id,
        )

    async def create_billing_portal_session(
        self,
        *,
        user_id: UUID,
        return_url: str,
        provider_customer_id: str | None = None,
    ) -> BillingPortalSession:
        del user_id, return_url
        if not provider_customer_id:
            raise BillingError(
                "billing_customer_missing",
                "A Creem customer must exist before opening the billing portal.",
            )
        payload = await self._post(
            "/v1/customers/billing",
            {"customer_id": provider_customer_id},
        )
        portal_url = str(payload.get("customer_portal_link") or "")
        if not portal_url.startswith("https://"):
            raise BillingError(
                "creem_portal_invalid",
                "Creem did not return a valid customer portal link.",
            )
        return BillingPortalSession(provider=self.provider_name, portal_url=portal_url)

    async def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        secret = self.settings.creem_api_key
        if secret is None:
            raise BillingError("creem_api_key_missing", "Creem API access is not configured.")
        try:
            response = await provider_request(
                self.settings,
                "POST",
                f"{str(self.settings.creem_api_base).rstrip('/')}{path}",
                provider="creem",
                operation=path.strip("/").replace("/", "_"),
                timeout=self.settings.creem_timeout_seconds,
                # Money again. The message below already promises "no payment was
                # created", and only a call that is never repeated can keep that promise.
                mutation_committed=True,
                headers={
                    "x-api-key": secret.get_secret_value(),
                    "Content-Type": "application/json",
                    "User-Agent": "HilalMarkets/1.0",
                },
                json=dict(payload),
            )
        except httpx.TimeoutException as exc:
            raise BillingError(
                "creem_timeout",
                "Creem checkout did not respond in time. No payment was created.",
            ) from exc
        except (httpx.RequestError, ProviderCallError) as exc:
            raise BillingError(
                "creem_unavailable",
                "Creem checkout is temporarily unavailable.",
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise BillingError(
                "creem_response_invalid",
                "Creem returned an invalid response.",
            ) from exc
        if response.is_error:
            raise BillingError(
                "creem_request_failed",
                f"Creem checkout failed with status {response.status_code}.",
            )
        if not isinstance(body, dict):
            raise BillingError(
                "creem_response_invalid",
                "Creem returned an invalid response.",
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
        secret = (
            self.settings.creem_webhook_secret
            if provider == "creem"
            else (
                self.settings.nowpayments_ipn_secret
                if provider == "nowpayments" and self.settings.nowpayments_ipn_secret
                else self.settings.billing_webhook_secret
            )
        )
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
        provider_name: str | None = None,
    ):
        self.session = session
        self.settings = settings
        if provider is not None:
            self.provider = provider
        else:
            selected_provider = provider_name or (
                settings.billing_card_provider
                if settings.billing_card_provider != "disabled"
                else (
                    settings.billing_crypto_provider
                    if settings.billing_crypto_provider != "disabled"
                    else settings.billing_provider
                )
            )
            self.provider = self._provider(selected_provider)

    def _provider(self, provider_name: str) -> BillingProvider:
        if provider_name == "stripe":
            self.provider = StripeBillingProvider(self.settings)
            return self.provider
        if provider_name == "nowpayments":
            self.provider = NowPaymentsBillingProvider(self.settings)
            return self.provider
        if provider_name == "creem":
            self.provider = CreemBillingProvider(self.settings)
            return self.provider
        if provider_name == "static" and (
            not self.settings.is_deployed or not self.settings.billing_enabled
        ):
            self.provider = StaticBillingProvider(self.settings)
            return self.provider
        if self.settings.is_deployed:
            raise BillingError(
                "billing_provider_missing",
                "A real billing provider must be configured in staging and production.",
            )
        self.provider = StaticBillingProvider(self.settings)
        return self.provider

    @property
    def provider_capabilities(self) -> BillingProviderCapabilities:
        return self.provider.capabilities

    @property
    def billing_cycle_code(self) -> str:
        return (
            "monthly_auto_renewal"
            if self.provider.capabilities.supports_recurring_billing
            else "one_time_30_day"
        )

    async def checkout_session(
        self, *, user_id: UUID, plan_code: str, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        prepared = await self.prepare_checkout(
            user_id=user_id,
            plan_code=plan_code,
            billing_cycle=self.billing_cycle_code,
            request_key=uuid4().hex,
            terms_accepted=True,
        )
        return await self.open_checkout_attempt(
            attempt_id=prepared.attempt.id,
            user_id=user_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )

    async def prepare_checkout(
        self,
        *,
        user_id: UUID,
        plan_code: str,
        billing_cycle: str,
        request_key: str,
        terms_accepted: bool,
        billing_profile: Mapping[str, Any] | None = None,
    ) -> CheckoutAttemptResult:
        if plan_code not in PURCHASABLE_PLAN_CODES:
            raise BillingError("plan_not_available", "This plan is not available for checkout.")
        billing_cycle = self._normalize_billing_cycle(
            plan_code=plan_code,
            requested=billing_cycle,
        )
        if not terms_accepted:
            raise BillingError(
                "billing_terms_required",
                "Accept the billing agreement before continuing.",
            )
        plan = await PlanCatalogService(self.session).get_or_sync(plan_code)
        if not plan.is_active or plan.price_monthly <= 0:
            raise BillingError("plan_not_available", "This paid plan is not available.")
        current_plans = set(
            (
                await self.session.scalars(
                    select(Plan.code)
                    .join(Subscription, Subscription.plan_id == Plan.id)
                    .where(
                        Subscription.user_id == user_id,
                        Subscription.provider.notin_(("admin", "free", "trial")),
                        Subscription.status.in_(
                            [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
                        ),
                        (Subscription.current_period_end.is_(None))
                        | (Subscription.current_period_end > datetime.now(UTC)),
                    )
                    .order_by(Subscription.updated_at.desc())
                )
            ).all()
        )
        if plan_code in current_plans:
            raise BillingError(
                "already_subscribed",
                f"Your {plan.name} plan is already active.",
            )

        now = datetime.now(UTC)
        expired = list(
            (
                await self.session.scalars(
                    select(BillingCheckoutAttempt).where(
                        BillingCheckoutAttempt.user_id == user_id,
                        BillingCheckoutAttempt.status.in_({"creating", "pending"}),
                        BillingCheckoutAttempt.expires_at <= now,
                    )
                )
            ).all()
        )
        for row in expired:
            row.status = "expired"

        normalized_key = request_key.strip()
        if not normalized_key or len(normalized_key) > 100:
            raise BillingError(
                "checkout_request_invalid",
                "The checkout request expired. Return to billing and try again.",
            )
        idempotency_key = sha256(
            (
                f"checkout:{user_id}:{plan.id}:{self.provider.provider_name}:{billing_cycle}:"
                f"{self.settings.billing_terms_version}:{normalized_key}"
            ).encode()
        ).hexdigest()
        existing = await self.session.scalar(
            select(BillingCheckoutAttempt).where(
                BillingCheckoutAttempt.idempotency_key == idempotency_key
            )
        )
        if existing is None:
            existing = await self.session.scalar(
                select(BillingCheckoutAttempt)
                .where(
                    BillingCheckoutAttempt.user_id == user_id,
                    BillingCheckoutAttempt.plan_id == plan.id,
                    BillingCheckoutAttempt.provider == self.provider.provider_name,
                    BillingCheckoutAttempt.billing_cycle == billing_cycle,
                    BillingCheckoutAttempt.status.in_({"creating", "pending"}),
                    BillingCheckoutAttempt.expires_at > now,
                )
                .order_by(BillingCheckoutAttempt.created_at.desc())
                .limit(1)
            )
        if existing is not None:
            return CheckoutAttemptResult(attempt=existing, duplicate=True)

        attempt = BillingCheckoutAttempt(
            user_id=user_id,
            plan_id=plan.id,
            billing_cycle=billing_cycle,
            provider=self.provider.provider_name,
            status="creating",
            idempotency_key=idempotency_key,
            terms_version=self.settings.billing_terms_version,
            amount=self._checkout_amount(plan.code, plan.price_monthly, billing_cycle),
            currency=plan.currency.upper(),
            terms_accepted_at=now,
            expires_at=now + timedelta(minutes=self.settings.billing_checkout_ttl_minutes),
            billing_profile=_sanitize_billing_profile(billing_profile),
        )
        self.session.add(attempt)
        await self.session.flush()
        self._audit(
            user_id,
            "billing.checkout_prepared",
            "billing_checkout_attempt",
            attempt.id,
            {
                "plan_code": plan.code,
                "billing_cycle": billing_cycle,
                "amount": str(attempt.amount),
                "currency": attempt.currency,
                "terms_version": attempt.terms_version,
            },
        )
        return CheckoutAttemptResult(attempt=attempt, duplicate=False)

    def _normalize_billing_cycle(self, *, plan_code: str, requested: str) -> str:
        normalized = requested.strip().lower()
        offer = plan_offer(plan_code)
        if normalized == "trial_7_day":
            raise BillingError(
                "billing_cycle_not_available",
                "Monitor is available as a paid monthly plan with a seven-day refund window.",
            )
        if normalized in {"annual", "annual_auto_renewal"} and not offer.annual_available:
            raise BillingError(
                "billing_cycle_not_available",
                "Annual billing is not available yet.",
            )
        if normalized in {"monthly", "monthly_auto_renewal", "one_time_30_day"} and not (
            offer.monthly_available
        ):
            raise BillingError(
                "plan_not_available",
                "This plan is not available for checkout.",
            )
        if self.provider.capabilities.supports_recurring_billing:
            aliases = {
                "monthly": "monthly_auto_renewal",
                "monthly_auto_renewal": "monthly_auto_renewal",
                "annual": "annual_auto_renewal",
                "annual_auto_renewal": "annual_auto_renewal",
            }
        else:
            aliases = {
                "monthly": "one_time_30_day",
                "one_time_30_day": "one_time_30_day",
            }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise BillingError(
                "billing_cycle_not_available",
                "The requested billing period is not available from this provider.",
            ) from exc

    @staticmethod
    def _checkout_amount(
        plan_code: str,
        monthly_amount: Decimal,
        billing_cycle: str,
    ) -> Decimal:
        if billing_cycle == "trial_7_day":
            return Decimal("0.00")
        if billing_cycle == "annual_auto_renewal":
            return PUBLIC_PLAN_PRESENTATIONS[plan_code].annual_price
        del monthly_amount
        return effective_monthly_price(plan_code)

    async def open_checkout_attempt(
        self,
        *,
        attempt_id: UUID,
        user_id: UUID,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        attempt = await self.session.get(BillingCheckoutAttempt, attempt_id)
        if attempt is None or attempt.user_id != user_id:
            raise BillingError("checkout_not_found", "The checkout request was not found.")
        now = datetime.now(UTC)
        if attempt.expires_at <= now:
            attempt.status = "expired"
            await self.session.flush()
            raise BillingError("checkout_expired", "This checkout request has expired.")
        if attempt.status == "completed":
            raise BillingError("already_subscribed", "This checkout is already complete.")
        if attempt.status == "pending" and attempt.checkout_url and attempt.provider_session_id:
            return CheckoutSession(
                provider=attempt.provider,
                checkout_url=attempt.checkout_url,
                provider_session_id=attempt.provider_session_id,
            )
        plan = await self.session.get(Plan, attempt.plan_id)
        if plan is None or not plan.is_active:
            raise BillingError("plan_not_available", "The selected plan is no longer available.")
        identity = await self.session.scalar(
            select(UserIdentity)
            .where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.is_primary.is_(True),
                UserIdentity.is_verified.is_(True),
            )
            .limit(1)
        )
        try:
            checkout = await self.provider.create_checkout_session(
                user_id=user_id,
                checkout_attempt_id=attempt.id,
                plan_code=plan.code,
                plan_name=plan.name,
                amount=attempt.amount,
                currency=attempt.currency,
                billing_cycle=attempt.billing_cycle,
                customer_email=(
                    identity.normalized_identifier
                    if identity and identity.normalized_identifier
                    else None
                ),
                success_url=success_url,
                cancel_url=cancel_url,
            )
        except BillingError as exc:
            attempt.status = "provider_unavailable"
            attempt.last_error = exc.code
            await self.session.flush()
            raise
        attempt.provider = checkout.provider
        attempt.provider_session_id = checkout.provider_session_id
        attempt.checkout_url = checkout.checkout_url
        attempt.status = "pending"
        attempt.last_error = None
        self._audit(
            user_id,
            "billing.checkout_opened",
            "billing_checkout_attempt",
            attempt.id,
            {
                "provider": checkout.provider,
                "plan_code": plan.code,
            },
        )
        await self.session.flush()
        return checkout

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
        if (
            subscription is None
            or not subscription.provider
            or subscription.provider in {"free", "admin", "trial"}
        ):
            raise BillingError(
                "billing_customer_missing",
                "No provider-managed subscription is available for this account.",
            )
        provider = (
            self.provider
            if self.provider.provider_name == subscription.provider
            else self._provider(subscription.provider)
        )
        if not provider.capabilities.supports_customer_portal:
            raise BillingError(
                "billing_portal_unavailable",
                "This subscription provider does not offer a customer portal.",
            )
        return await provider.create_billing_portal_session(
            user_id=user_id,
            return_url=return_url,
            provider_customer_id=subscription.provider_customer_id,
        )

    async def expire_ended_access(self, *, now: datetime | None = None) -> int:
        cutoff = now or datetime.now(UTC)
        subscriptions = list(
            (
                await self.session.scalars(
                    select(Subscription).where(
                        Subscription.status.in_(
                            {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING}
                        ),
                        Subscription.current_period_end.is_not(None),
                        Subscription.current_period_end <= cutoff,
                        Subscription.cancel_at_period_end.is_(True),
                    )
                )
            ).all()
        )
        for subscription in subscriptions:
            subscription.status = SubscriptionStatus.EXPIRED
            await EntitlementService(self.session).snapshot(subscription.user_id)
            await EntitlementService(self.session).pause_excess_after_downgrade(
                subscription.user_id
            )
            self._audit(
                subscription.user_id,
                "billing.access_expired",
                "subscription",
                subscription.id,
                {
                    "provider": subscription.provider,
                    "period_end": subscription.current_period_end.isoformat()
                    if subscription.current_period_end
                    else None,
                },
            )
        await self.session.flush()
        return len(subscriptions)

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
            if provider == "creem":
                return BillingService._normalize_creem_payload(payload)
            return payload
        event_data = payload.get("data")
        if not isinstance(event_data, Mapping) or not isinstance(event_data.get("object"), Mapping):
            return payload
        stripe_object = dict(event_data["object"])
        metadata = dict(stripe_object.get("metadata") or {})
        if not metadata:
            metadata = dict(
                dict(stripe_object.get("subscription_details") or {}).get("metadata") or {}
            )
        if not metadata:
            line_items = dict(stripe_object.get("lines") or {}).get("data") or []
            if line_items and isinstance(line_items[0], Mapping):
                metadata = dict(line_items[0].get("metadata") or {})
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
            "checkout_attempt_id": metadata.get("checkout_attempt_id"),
            "provider_customer_id": customer,
            "provider_subscription_id": provider_subscription_id,
            "provider_payment_reference": stripe_object.get("payment_intent")
            or stripe_object.get("id"),
            "status": stripe_object.get("payment_status") or stripe_object.get("status"),
            "current_period_start": stripe_object.get("current_period_start"),
            "current_period_end": stripe_object.get("current_period_end"),
            "cancel_at_period_end": stripe_object.get("cancel_at_period_end", False),
            "amount": BillingService._stripe_amount(stripe_object),
            "currency": stripe_object.get("currency"),
            "receipt_url": stripe_object.get("hosted_invoice_url")
            or stripe_object.get("invoice_pdf")
            or stripe_object.get("receipt_url"),
        }
        return {
            "id": payload.get("id"),
            "type": payload.get("type"),
            "data": normalized_data,
        }

    @staticmethod
    def _normalize_creem_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        event_type = str(payload.get("eventType") or payload.get("type") or "")
        raw_object = payload.get("object")
        item = dict(raw_object) if isinstance(raw_object, Mapping) else {}
        checkout = (
            item
            if str(item.get("object") or "") == "checkout"
            else dict(item.get("checkout") or {})
        )
        subscription = (
            item
            if str(item.get("object") or "") == "subscription"
            else dict(item.get("subscription") or {})
        )
        order = dict(item.get("order") or {})
        product_value = subscription.get("product") or item.get("product") or order.get("product")
        product = dict(product_value) if isinstance(product_value, Mapping) else {}
        customer_value = subscription.get("customer") or item.get("customer")
        customer = dict(customer_value) if isinstance(customer_value, Mapping) else {}
        metadata: dict[str, Any] = {}
        for source in (
            checkout.get("metadata"),
            subscription.get("metadata"),
            item.get("metadata"),
        ):
            if isinstance(source, Mapping):
                metadata.update(source)
        checkout_attempt_id = metadata.get("checkout_attempt_id") or checkout.get(
            "request_id"
        )
        provider_subscription_id = subscription.get("id")
        if not provider_subscription_id and isinstance(item.get("subscription"), str):
            provider_subscription_id = item.get("subscription")
        status = str(
            subscription.get("status")
            or order.get("status")
            or item.get("status")
            or "pending"
        )
        normalized_amount = BillingService._minor_unit_amount(
            order.get("amount") if order.get("amount") is not None else product.get("price")
        )
        normalized = {
            "user_id": metadata.get("user_id"),
            "plan_code": metadata.get("plan_code"),
            "checkout_attempt_id": checkout_attempt_id,
            "billing_cycle": metadata.get("billing_cycle"),
            "provider_customer_id": customer.get("id")
            or (
                subscription.get("customer")
                if isinstance(subscription.get("customer"), str)
                else None
            )
            or order.get("customer"),
            "provider_subscription_id": provider_subscription_id,
            "provider_payment_reference": subscription.get("last_transaction_id")
            or order.get("id")
            or item.get("id"),
            "status": status,
            "current_period_start": subscription.get("current_period_start_date"),
            "current_period_end": subscription.get("current_period_end_date"),
            "cancel_at_period_end": event_type == "subscription.scheduled_cancel",
            "amount": normalized_amount,
            "currency": order.get("currency") or product.get("currency"),
            "receipt_url": item.get("receipt_url") or order.get("receipt_url"),
        }
        return {
            "id": payload.get("id"),
            "type": event_type,
            "data": normalized,
        }

    @staticmethod
    def _normalize_nowpayments_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        order_id = str(payload.get("order_id") or "")
        user_id: str | None = None
        plan_code: str | None = None
        checkout_attempt_id: str | None = None
        parts = order_id.split("|")
        if len(parts) >= 3 and parts[0] == "hm":
            checkout_attempt_id = parts[1]
            plan_code = parts[2]
        elif len(parts) >= 4 and parts[0] == "amm":
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
                "checkout_attempt_id": checkout_attempt_id,
                "provider_customer_id": str(payload.get("purchase_id") or "") or None,
                "provider_subscription_id": f"nowpayments_{payment_id}",
                "provider_payment_reference": payment_id,
                "status": normalized_status,
                "current_period_start": now.isoformat(),
                "current_period_end": (now + timedelta(days=30)).isoformat(),
                "cancel_at_period_end": True,
                "access_type": "one_time_30_day",
                "renews_automatically": False,
                "amount": payload.get("price_amount"),
                "currency": payload.get("price_currency"),
                "settlement_expected_amount": payload.get("pay_amount"),
                "settlement_actual_amount": payload.get("actually_paid"),
                "settlement_currency": payload.get("pay_currency"),
                "receipt_url": payload.get("invoice_url"),
            },
        }

    @staticmethod
    def _stripe_amount(payload: Mapping[str, Any]) -> str | None:
        raw = payload.get("amount_paid")
        if raw is None:
            raw = payload.get("amount_total")
        if raw is None:
            return None
        try:
            return str((Decimal(str(raw)) / Decimal("100")).quantize(Decimal("0.01")))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _minor_unit_amount(raw: Any) -> str | None:
        if raw is None:
            return None
        try:
            return str((Decimal(str(raw)) / Decimal("100")).quantize(Decimal("0.01")))
        except (InvalidOperation, ValueError):
            return None

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
        await self._hydrate_checkout_data(
            data,
            provider=provider,
            event_type=event_type,
        )
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
            subscription = await self._apply_event(
                provider=provider, event_id=event_id, event_type=event_type, data=data
            )
            await self._record_checkout_event(
                provider_event_id=event_id,
                event_type=event_type,
                data=data,
            )
            if subscription is not None and self._is_payment_email_event(
                provider=provider,
                event_type=event_type,
                subscription=subscription,
            ):
                from ai_market_monitor.services.payment_emails import (
                    PaymentEmailOutboxService,
                )

                await PaymentEmailOutboxService(self.session, self.settings).enqueue(
                    billing_event=event,
                    subscription=subscription,
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
            subscription = await self._apply_event(
                provider=event.provider,
                event_id=event.provider_event_id,
                event_type=event.event_type,
                data=data,
            )
            await self._record_checkout_event(
                provider_event_id=event.provider_event_id,
                event_type=event.event_type,
                data=data,
            )
            if subscription is not None and self._is_payment_email_event(
                provider=event.provider,
                event_type=event.event_type,
                subscription=subscription,
            ):
                from ai_market_monitor.services.payment_emails import (
                    PaymentEmailOutboxService,
                )

                await PaymentEmailOutboxService(self.session, self.settings).enqueue(
                    billing_event=event,
                    subscription=subscription,
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
    ) -> Subscription | None:
        if event_type in {
            "checkout.session.completed",
            "customer.subscription.created",
            "customer.subscription.updated",
            "subscription.created",
            "subscription.updated",
            "invoice.payment_succeeded",
            "payment.finished",
            "subscription.paid",
            "subscription.trialing",
            "subscription.update",
            "subscription.scheduled_cancel",
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
            return subscription
        if event_type in {
            "customer.subscription.deleted",
            "subscription.deleted",
            "subscription.canceled",
            "subscription.expired",
        }:
            subscription = await self._upsert_subscription(
                provider=provider, data=data, forced_status=SubscriptionStatus.CANCELED
            )
            subscription.canceled_at = datetime.now(UTC)
            await EntitlementService(self.session).snapshot(subscription.user_id)
            await EntitlementService(self.session).pause_excess_after_downgrade(
                subscription.user_id
            )
            self._audit(
                subscription.user_id,
                "billing.subscription_canceled",
                "subscription",
                subscription.id,
                {},
            )
            return subscription
        if event_type in {
            "invoice.payment_failed",
            "payment.failed",
            "subscription.past_due",
            "subscription.paused",
        }:
            subscription = await self._upsert_subscription(
                provider=provider, data=data, forced_status=SubscriptionStatus.PAST_DUE
            )
            await EntitlementService(self.session).snapshot(subscription.user_id)
            self._audit(
                subscription.user_id,
                "billing.payment_failed",
                "subscription",
                subscription.id,
                {},
            )
            return subscription
        if event_type in {"payment.refunded", "refund.created"}:
            subscription = await self._upsert_subscription(
                provider=provider,
                data=data,
                forced_status=SubscriptionStatus.CANCELED,
            )
            subscription.canceled_at = datetime.now(UTC)
            await EntitlementService(self.session).snapshot(subscription.user_id)
            await EntitlementService(self.session).pause_excess_after_downgrade(
                subscription.user_id
            )
            self._audit(
                subscription.user_id,
                "billing.payment_refunded",
                "subscription",
                subscription.id,
                {},
            )
            return subscription
        if event_type in {"payment.expired", "payment.failed"}:
            user_id = self._parse_uuid(data.get("user_id"))
            if user_id:
                self._audit(user_id, f"billing.{event_type}", "user", user_id, {})
            return None
        if event_type in {"charge.refunded", "charge.dispute.created", "dispute.created"}:
            user_id = self._parse_uuid(data.get("user_id"))
            if user_id:
                self._audit(user_id, f"billing.{event_type}", "user", user_id, {})
        return None

    async def _hydrate_checkout_data(
        self,
        data: dict[str, Any],
        *,
        provider: str,
        event_type: str,
    ) -> None:
        raw_attempt_id = data.get("checkout_attempt_id")
        if raw_attempt_id in (None, ""):
            requires_checkout = provider == "nowpayments" or event_type in {
                "checkout.session.completed",
                "payment.finished",
                "subscription.paid",
                "subscription.trialing",
            }
            if requires_checkout:
                raise BillingError(
                    "checkout_reference_missing",
                    "A successful payment must match a server-created checkout attempt.",
                )
            return
        try:
            attempt_id = self._parse_uuid(raw_attempt_id)
        except (TypeError, ValueError) as exc:
            raise BillingError(
                "checkout_reference_invalid",
                "Billing event included an invalid checkout reference.",
            ) from exc
        attempt = None
        if attempt_id is not None:
            attempt = await self.session.scalar(
                select(BillingCheckoutAttempt)
                .where(BillingCheckoutAttempt.id == attempt_id)
                .with_for_update()
            )
        if attempt is None:
            raise BillingError(
                "checkout_reference_missing",
                "Billing event did not match a server-created checkout.",
            )
        if (
            provider == "nowpayments"
            and event_type == "payment.finished"
            and attempt.status == "completed"
        ):
            raise BillingError(
                "checkout_already_completed",
                "This one-time checkout has already granted its access period.",
            )
        plan = await self.session.get(Plan, attempt.plan_id)
        if plan is None:
            raise BillingError("plan_not_found", "Checkout plan no longer exists.")
        if attempt.provider not in {provider, "static"}:
            raise BillingError(
                "checkout_provider_mismatch",
                "The payment provider does not match the checkout attempt.",
            )
        supplied_user_id = self._parse_uuid(data.get("user_id"))
        if supplied_user_id is not None and supplied_user_id != attempt.user_id:
            raise BillingError(
                "checkout_user_mismatch", "The payment user does not match the checkout attempt."
            )
        supplied_plan = str(data.get("plan_code") or "")
        if supplied_plan and supplied_plan != plan.code:
            raise BillingError(
                "checkout_plan_mismatch", "The paid plan does not match the checkout attempt."
            )
        expected_amount = (
            plan.price_monthly
            if attempt.billing_cycle == "trial_7_day" and event_type == "subscription.paid"
            else attempt.amount
        )
        if event_type in {
            "checkout.session.completed",
            "invoice.payment_succeeded",
            "payment.finished",
            "subscription.paid",
        }:
            self._validate_paid_amount_and_currency(
                paid_amount=data.get("amount"),
                paid_currency=data.get("currency"),
                expected_amount=expected_amount,
                expected_currency=attempt.currency,
            )
        if provider == "nowpayments" and event_type == "payment.finished":
            self._validate_nowpayments_settlement(data)
        data["checkout_attempt_id"] = str(attempt.id)
        data["user_id"] = str(attempt.user_id)
        data["plan_code"] = plan.code
        data["amount"] = str(expected_amount)
        data["currency"] = attempt.currency

    def _validate_paid_amount_and_currency(
        self,
        *,
        paid_amount: object,
        paid_currency: object,
        expected_amount: Decimal,
        expected_currency: str,
    ) -> None:
        try:
            actual = Decimal(str(paid_amount))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BillingError(
                "payment_amount_missing", "The provider did not report a valid paid amount."
            ) from exc
        currency = str(paid_currency or "").upper()
        if currency != expected_currency.upper():
            raise BillingError(
                "payment_currency_mismatch",
                "The paid currency does not match the checkout attempt.",
            )
        tolerance = expected_amount * Decimal(
            str(self.settings.billing_payment_amount_tolerance_percent)
        ) / Decimal("100")
        minimum = expected_amount - tolerance
        maximum = expected_amount + tolerance
        if actual < minimum:
            raise BillingError(
                "payment_underpaid", "The verified payment is below the accepted amount."
            )
        if actual > maximum and not self.settings.billing_allow_overpayment:
            raise BillingError(
                "payment_overpaid",
                "The verified payment exceeds the accepted amount and requires manual review.",
            )

    def _validate_nowpayments_settlement(self, data: Mapping[str, Any]) -> None:
        try:
            expected = Decimal(str(data.get("settlement_expected_amount")))
            actual = Decimal(str(data.get("settlement_actual_amount")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BillingError(
                "payment_settlement_missing",
                "NOWPayments did not report a valid expected and actually paid amount.",
            ) from exc
        currency = str(data.get("settlement_currency") or "").strip().upper()
        if expected <= 0 or actual < 0 or not currency:
            raise BillingError(
                "payment_settlement_invalid",
                "NOWPayments reported invalid settlement amount or currency evidence.",
            )
        tolerance = expected * Decimal(
            str(self.settings.billing_payment_amount_tolerance_percent)
        ) / Decimal("100")
        if actual < expected - tolerance:
            raise BillingError(
                "payment_underpaid",
                "The amount actually received is below the accepted payment amount.",
            )
        if actual > expected + tolerance and not self.settings.billing_allow_overpayment:
            raise BillingError(
                "payment_overpaid",
                "The amount actually received exceeds policy and requires manual review.",
            )

    async def _record_checkout_event(
        self,
        *,
        provider_event_id: str,
        event_type: str,
        data: Mapping[str, Any],
    ) -> None:
        attempt_id = self._parse_uuid(data.get("checkout_attempt_id"))
        if attempt_id is None:
            return
        attempt = await self.session.get(BillingCheckoutAttempt, attempt_id)
        if attempt is None:
            return
        attempt.provider_event_id = provider_event_id
        normalized_status = self._status_from_provider(str(data.get("status") or "pending"))
        if event_type in {
            "checkout.session.completed",
            "invoice.payment_succeeded",
            "payment.finished",
            "subscription.paid",
            "subscription.trialing",
        } and normalized_status in {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING}:
            attempt.status = "completed"
            attempt.completed_at = datetime.now(UTC)
            attempt.last_error = None
        elif event_type in {
            "invoice.payment_failed",
            "payment.failed",
            "payment.expired",
            "payment.partially_paid",
            "payment.refunded",
        }:
            attempt.status = event_type.split(".", 1)[1]
            attempt.last_error = event_type
        else:
            attempt.status = "processing"
        await self.session.flush()

    @staticmethod
    def _is_payment_email_event(
        *,
        provider: str,
        event_type: str,
        subscription: Subscription,
    ) -> bool:
        if subscription.status not in {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.TRIALING,
        }:
            return False
        expected = {
            "stripe": {"invoice.payment_succeeded"},
            "nowpayments": {"payment.finished"},
            "creem": {"subscription.paid"},
            "static": {"checkout.session.completed"},
        }
        return event_type in expected.get(provider, {"invoice.payment_succeeded"})

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
        if normalized == "scheduled_cancel":
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


def _cycle_key(billing_cycle: str) -> str:
    return {
        "monthly": "monthly",
        "monthly_auto_renewal": "monthly",
        "annual": "annual",
        "annual_auto_renewal": "annual",
        "trial_7_day": "trial",
        "one_time_30_day": "monthly",
    }.get(billing_cycle, billing_cycle)


def _sanitize_billing_profile(profile: Mapping[str, Any] | None) -> dict[str, str]:
    if not profile:
        return {}
    limits = {
        "first_name": 60,
        "last_name": 60,
        "address_line1": 200,
        "address_line2": 200,
        "city": 100,
        "region": 100,
        "postal_code": 24,
        "country": 80,
    }
    result: dict[str, str] = {}
    for key, limit in limits.items():
        value = str(profile.get(key) or "").strip()
        if value:
            result[key] = value[:limit]
    return result
