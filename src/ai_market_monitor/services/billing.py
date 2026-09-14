import hmac
import json
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256, sha512
from typing import Any, Final, Literal, Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.money import money_kept, wire_json_body
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PUBLIC_PLAN_PRESENTATIONS,
    PURCHASABLE_PLAN_CODES,
    effective_monthly_price,
    plan_name,
    plan_offer,
    plan_offer_payload,
)
from ai_market_monitor.db.models import (
    AuditEvent,
    BillingCheckoutAttempt,
    BillingEvent,
    Plan,
    PlanMoveMoneyOwed,
    Subscription,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, SubscriptionStatus
from ai_market_monitor.services.discount_codes import DiscountedPrice
from ai_market_monitor.services.entitlements import (
    EntitlementContext,
    EntitlementService,
    PlanCatalogService,
)
from ai_market_monitor.services.provider_reliability import ProviderCallError
from ai_market_monitor.services.provider_runtime import provider_request
from ai_market_monitor.services.trials import TrialLifecycleService

logger = logging.getLogger(__name__)

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


class PlanMoveFailed(BillingError):
    """A new plan was paid for, and the plan it replaces could not be ended safely.

    Every such failure rolls the whole payment back, so the customer keeps the old plan,
    and is saved as a critical issue a person sees. It used to be only a failed cancel
    that was saved; a move refused for any other reason failed without an alert.
    """


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


#: Every way of paying this product offers. Written once. "card" and "crypto" used to be
#: typed separately into two routers, three templates and two scripts, and a page could
#: offer one of them while the server refused it.
PAYMENT_METHODS: Final[tuple[str, ...]] = ("card", "crypto")

#: What each payment company is called in front of a person. Its internal name is a word
#: from the code, and this product is written for beginners.
_PROVIDER_WORDS: Final[dict[str, str]] = {
    "creem": "Creem",
    "stripe": "Stripe",
    "nowpayments": "NOWPayments",
    "static": "a test payment page",
}

#: How each way of paying is named inside a sentence.
_METHOD_WORDS: Final[dict[str, str]] = {"card": "card", "crypto": "crypto"}

#: The payment company's own website. It is what the "Payments secured by …" mark under a
#: way of paying links to, so a buyer can check the company before typing a card number.
#: A company with no entry here gets no mark — `static` is this product's own local test
#: page and has no company behind it at all.
_PROVIDER_SITES: Final[dict[str, str]] = {
    "creem": "https://www.creem.io/",
    "stripe": "https://stripe.com/",
    "nowpayments": "https://nowpayments.io/",
}


def provider_word(provider: str | None) -> str | None:
    """What one payment company is called in front of a person.

    One owner. The sentence under a way of paying and the mark beside it both name the
    same company, so both ask here rather than each keeping a table of names.
    """

    if provider is None:
        return None
    return _PROVIDER_WORDS.get(provider)


def provider_site(provider: str | None) -> str | None:
    """The payment company's own website, or ``None`` when it has no public one."""

    if provider is None:
        return None
    return _PROVIDER_SITES.get(provider)


#: Which way of paying each company serves. A finished payment keeps only the company's
#: name, so this is how a past payment can still be shown as "Card" or "Crypto".
_PROVIDER_METHODS: Final[dict[str, str]] = {
    "creem": "card",
    "stripe": "card",
    "static": "card",
    "nowpayments": "crypto",
}


def provider_method(provider: str | None) -> str | None:
    """The way of paying one company takes, or ``None`` for a company we do not know."""

    if provider is None:
        return None
    return _PROVIDER_METHODS.get(provider)


#: Which ways of paying take a discount code typed on **our** pages.
#:
#: Only crypto. The card route ends on Creem's own checkout page, which already has a
#: discount box of its own, and Creem — not this application — decides what a card buyer
#: is charged. A code box on our side of the card route would take a code, quote a price,
#: and then watch Creem charge a different one.
#:
#: One owner, because three screens draw the box and one route accepts it. A page that
#: worked this out for itself is how a box came to be offered where checkout refused it.
DISCOUNT_CODE_METHODS: Final[tuple[str, ...]] = ("crypto",)


def method_takes_discount_code(method: str | None) -> bool:
    """Does this way of paying accept a code typed on our pages?"""

    return method in DISCOUNT_CODE_METHODS


def provider_takes_discount_code(provider: str | None) -> bool:
    """Does this payment company's route accept a code typed on our pages?"""

    return method_takes_discount_code(provider_method(provider))


def creem_product_id_for(
    settings: Settings,
    *,
    plan_code: str,
    billing_cycle: str,
) -> str | None:
    """Which Creem product a plan and period map to, or ``None`` when there is not one.

    Two callers need this and they must agree: the card checkout, which sends the id to
    Creem, and the discount lookup, which asks Creem whether a code covers that product.
    A code scoped to the card product is a code for *the plan*, so the crypto route asks
    about the same product rather than about nothing.
    """

    return settings.creem_product_ids.get(f"{plan_code}_{_cycle_key(billing_cycle)}") or None


def plan_code_for_creem_product(settings: Settings, product_id: str | None) -> str | None:
    """Which plan a Creem product belongs to, read backwards from the same table.

    This is what a **renewal** has to be judged by. A Creem subscription carries the
    metadata it was created with, so after a plan change its ``plan_code`` still names the
    plan somebody used to be on — while the product it is really charging for is the new
    one. Believing the metadata would move a customer who paid to upgrade back down to
    their old plan on their very first renewal, silently.

    ``None`` when the product is not one of ours, and then the metadata is all there is.
    """

    if not product_id:
        return None
    for key, value in settings.creem_product_ids.items():
        if value != product_id:
            continue
        plan_code, _, period = key.rpartition("_")
        if plan_code and period in {"monthly", "annual", "trial"}:
            return plan_code
    return None


def method_word(method: str | None) -> str | None:
    """How one way of paying is named in front of a person."""

    if method is None:
        return None
    return _METHOD_WORDS.get(method)


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
            f"Paying by {_METHOD_WORDS.get(payment_method, payment_method)} "
            "is switched off just now.",
        )
    return provider


# ── Which ways of paying really work ─────────────────────────────────────────────
#
# One question, one answer, one owner: *can this person pay for this plan, this way,
# right now?* Every page that draws a payment choice and every route that accepts one
# asks the functions below, so a page can never offer a button the server refuses.
#
# That had already happened. The plan page drew "Card" and "Crypto" side by side
# whatever the settings said, and pressing Pay with card switched off answered
# "That payment method is not currently available" — a dead end with no way out of it.


@dataclass(frozen=True, slots=True)
class PaymentMethodOffer:
    """One way of paying, and whether it can really be used for a plan right now.

    ``note`` is the sentence shown under the name. It is written here rather than in a
    template because which sentence is true depends on *why* a method cannot be used,
    and a template cannot be tested on that.

    ``company`` and ``company_site`` are the payment company in front of a person, and
    its own website. They travel with the offer for the same reason: the mark that says
    *Payments secured by Creem* must name the company the server is really configured to
    use. A page that writes the name itself keeps saying "Creem" the day the setting
    moves to another company.
    """

    method: str
    provider: str | None
    available: bool
    note: str
    company: str | None = None
    company_site: str | None = None
    #: The period this offer was asked about. It is part of what paying does to
    #: somebody's money — "every month" and "every year" are not the same promise — so
    #: the sentence below cannot be written without it.
    billing_cycle: str = "monthly"

    @property
    def access_terms(self) -> str:
        """How access works through this offer's actual payment company."""

        if self.provider is None:
            return "This payment method is unavailable."
        capabilities = billing_provider_capabilities(self.provider)
        if capabilities.supports_recurring_billing:
            return "Monthly subscription. Renews monthly until cancelled."
        return "One-time 30-day access. No automatic renewal."

    @property
    def renews_by_itself(self) -> bool:
        """Whether paying this way takes money again without being asked."""

        if self.provider is None:
            return False
        return billing_provider_capabilities(self.provider).supports_recurring_billing

    @property
    def charge_story(self) -> str:
        """The same fact as :attr:`access_terms`, told with the amount in it.

        Both exist, and neither may be written in a browser, because "does this take
        money again by itself?" is the payment company's capability and nothing else may
        answer it. Both checkout popups wrote one sentence for every method — "then $17
        every month until you stop it" — so a crypto buyer was promised a monthly
        subscription NOWPayments cannot create, on the same screen where the landing page
        promises the checkout will show "whether it renews by itself".

        ``{amount}`` is left as a placeholder rather than filled in, because a discount
        code changes the amount in the browser after this sentence was written. The page
        puts the number in; it never decides which sentence.
        """

        if self.provider is None:
            return "This payment method is unavailable."
        if not self.renews_by_itself:
            if self.billing_cycle == "monthly":
                return (
                    "{amount} today, for 30 days of access. It does not renew by "
                    "itself, so nothing is taken again unless you buy again."
                )
            return (
                "{amount} today. It does not renew by itself, so nothing is taken "
                "again unless you buy again."
            )
        if self.billing_cycle == "trial_7_day":
            return (
                "Nothing today. After the 7 free days it is {amount} every month "
                "until you stop it. You can stop it whenever you like."
            )
        every = "every year" if self.billing_cycle == "annual" else "every month"
        return (
            f"{{amount}} today, then {{amount}} {every} until you stop it. "
            "You can stop it whenever you like."
        )

    @property
    def cancellation_terms(self) -> str:
        """How this offer is stopped, derived from the provider capability contract."""

        if self.provider is None:
            return "This payment method is unavailable."
        capabilities = billing_provider_capabilities(self.provider)
        if capabilities.supports_customer_portal:
            return "Manage through the payment provider's portal."
        return "No subscription cancellation is needed."


def billing_method_provider(settings: Settings, payment_method: str) -> str | None:
    """The payment company behind one method, or ``None`` when it is switched off."""

    try:
        return configured_billing_provider(settings, payment_method)
    except BillingError:
        return None


def webhook_secret_for(settings: Settings, provider: str) -> SecretStr | None:
    """The secret that proves a message really came from one payment company.

    One owner. This choice — and in particular that NOWPayments falls back to the shared
    ``BILLING_WEBHOOK_SECRET`` when it has no IPN secret of its own — was written inside
    the webhook verifier, while the rule deciding whether crypto could be *offered* had
    its own narrower copy. So a server holding only the shared secret could confirm a
    crypto payment perfectly well, and the plan page hid crypto anyway.
    """

    if provider == "creem":
        return settings.creem_webhook_secret
    if provider == "nowpayments":
        return settings.nowpayments_ipn_secret or settings.billing_webhook_secret
    return settings.billing_webhook_secret


def payment_can_be_confirmed(settings: Settings, provider: str) -> bool:
    """Whether a payment taken through this company could afterwards be proven.

    Selling without this is the worst outcome the billing code has: the customer really
    pays, the confirmation arrives unsigned or never arrives, and the plan never starts.
    A refusal before the money moves is always better.

    ``static`` is the local adapter, which confirms itself through a signed return address
    rather than a callback, so it needs no secret.
    """

    if provider == "static":
        return True
    secret = webhook_secret_for(settings, provider)
    return secret is not None and bool(secret.get_secret_value().strip())


def _provider_can_sell(
    settings: Settings,
    *,
    provider: str | None,
    plan_code: str,
    billing_cycle: str,
) -> bool:
    """Whether one payment company can really take money for this plan and period."""

    if not settings.billing_enabled or provider is None:
        return False
    # A plan that is not for sale cannot be paid for by any means. The free plan reads as
    # "available monthly" in the offer table — it is, at no charge — and that made every
    # way of paying look open for a plan `prepare_checkout` refuses outright.
    if plan_code not in PURCHASABLE_PLAN_CODES:
        return False
    if not payment_can_be_confirmed(settings, provider):
        return False
    offer = plan_offer(plan_code)
    if billing_cycle == "trial_7_day":
        return False
    if billing_cycle == "monthly" and not offer.monthly_available:
        return False
    if billing_cycle == "annual" and not offer.annual_available:
        return False
    if provider == "creem":
        # The webhook secret is checked above, for every company at once.
        if settings.creem_api_key is None or not settings.creem_api_key.get_secret_value().strip():
            return False
        key = (
            f"{plan_code}_trial"
            if billing_cycle == "trial_7_day"
            else f"{plan_code}_{billing_cycle}"
        )
        return key in settings.creem_product_ids
    if provider == "stripe":
        if settings.stripe_secret_key is None:
            return False
        return (
            f"{plan_code}_{billing_cycle}" in settings.stripe_price_ids
            or plan_code in settings.stripe_price_ids
        )
    if provider == "nowpayments":
        return (
            billing_cycle == "monthly"
            and settings.nowpayments_api_key is not None
            and bool(settings.nowpayments_api_key.get_secret_value().strip())
        )
    return provider == "static" and not settings.is_deployed


def payment_method_available(
    settings: Settings,
    *,
    method: str,
    plan_code: str,
    billing_cycle: str = "monthly",
) -> bool:
    """Whether one way of paying really works for one plan and one period."""

    return _provider_can_sell(
        settings,
        provider=billing_method_provider(settings, method),
        plan_code=plan_code,
        billing_cycle=billing_cycle,
    )


def plan_is_on_sale(
    settings: Settings,
    plan_code: str,
    *,
    billing_cycle: str = "monthly",
) -> bool:
    """Whether a page may invite somebody to take this plan today.

    One question with one answer, asked by every surface that draws a plan card: the
    public pricing page, the landing page and the dashboard. They used to answer it in
    two different ways. The public card asked the price list alone — "is this plan for
    sale?" — while the dashboard card asked whether a payment company could really take
    the money for *this* plan. On a server holding a product id for one paid plan and not
    the other, the public page invited a visitor to choose the second one and the
    dashboard then told the same person it was coming soon.

    The free plan is always on sale: there is nothing to charge for it, so no payment
    company has to be ready.
    """

    offer = plan_offer(plan_code)
    listed = offer.annual_available if billing_cycle == "annual" else offer.monthly_available
    if not listed:
        return False
    if plan_code not in PURCHASABLE_PLAN_CODES:
        return True
    return any(
        payment_method_available(
            settings, method=method, plan_code=plan_code, billing_cycle=billing_cycle
        )
        for method in PAYMENT_METHODS
    )


def plan_sale_payload(settings: Settings, plan_code: str) -> dict[str, object]:
    """``plan_offer_payload`` with availability answered by *this* server.

    ``plan_offer_payload`` is the price list, and knows nothing about payment companies.
    Every page that draws a card gets this instead, so "available" on a card always means
    a visitor could really reach a payment page from it.
    """

    payload = dict(plan_offer_payload(plan_code))
    payload["monthlyAvailable"] = plan_is_on_sale(
        settings, plan_code, billing_cycle="monthly"
    )
    payload["annualAvailable"] = plan_is_on_sale(
        settings, plan_code, billing_cycle="annual"
    )
    return payload


#: Payment companies that keep a card on file and can be asked to charge it again, so a
#: plan change is a re-price of the subscription they already hold. Anything else - a
#: crypto invoice, an administrator's grant, the free plan - holds no card: it buys a
#: fixed period, charges once, and ends by itself.
#:
#: One owner, here, because two very different rules depend on it: whether a plan change
#: is a form or a purchase, and whether a purchase may be offered at all.
RECURRING_PROVIDERS: frozenset[str] = frozenset({"creem", "stripe"})
#: How long after a recurring subscription's period end it may still be the plan that is
#: replaced. A recurring card subscription stays ``ACTIVE`` at the payment company until
#: the renewal decision happens, so treating it as already ended in this window leaves
#: the old card charging next to a new plan.
RECURRING_LAPSED_GRACE_WINDOW: Final[timedelta] = timedelta(days=30)

#: The refund names that also say the paid plan is over: the company returned the money
#: for it, so the access it bought ends with it.
REFUND_ENDS_PLAN_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"payment.refunded", "refund.created"}
)

#: The names that say a charge is being contested or was reversed by the card network.
#: The payment is recorded like any other return, but the plan is **not** ended here: a
#: dispute can still be won back, and switching a customer off on an event the card
#: company may yet reverse is a product decision, not a webhook detail. That is why these
#: three names have always been audit-only in ``_apply_event``, and still are.
REFUND_CHARGEBACK_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"charge.refunded", "charge.dispute.created", "dispute.created"}
)

#: Every event that says money this product already took is coming back, or has come
#: back: a refund, or a charge the card network settled against us.
#:
#: One list, made of the two named above, because the whole product asks one question
#: about it — "is this payment still money we hold?" — and reads the answer out of
#: :attr:`BillingCheckoutAttempt.status`. Before this existed the reading was split three
#: ways and each copy knew a different subset: ``payment.refunded`` was the only name the
#: attempt writer knew, ``refund.created`` and the three dispute names were routed to
#: ``_apply_event`` and stopped there, and the settled attempt was left saying
#: ``completed`` after the money had gone back. Every reader of ``completed`` then kept
#: counting a returned payment as a sale: the money-owed valuation, the affiliate
#: commission, the operations count of completed checkouts. A refund that is not written
#: down is a payment that can be given back twice.
#:
#: Add a new company's word for "refunded" to whichever of the two sets above it means —
#: this list is built from them, so it cannot drift behind them.
REFUND_EVENT_TYPES: Final[frozenset[str]] = (
    REFUND_ENDS_PLAN_EVENT_TYPES | REFUND_CHARGEBACK_EVENT_TYPES
)

#: Marks a refund event whose payment this system could not find, and what a person is
#: told about each kind of gap. The key is what :class:`BillingService` records as
#: evidence; the sentence is the whole alert body.
#:
#: Fixed sentences written once at module level, the way ``REPLACEMENT_REFUSALS`` does it
#: for the checkout route. Two reasons. The issue queue refuses a summary longer than its
#: own cap, so a sentence built at the call site can destroy the alert that exists to
#: report lost money; and ``payment.refunded`` reaching nobody and a matched payment whose
#: plan cannot be found are different problems for the person reading the queue.
REFUND_RECONCILIATION_ALERTS: Final[dict[str, str]] = {
    "payment": (
        "A payment company returned money, but no checkout of ours matched the message. "
        "That payment may still read as money kept. A person must find it and correct "
        "the record."
    ),
    "plan": (
        "A payment company returned money and the checkout was found, but the "
        "subscription to end was not named. The plan may still be live. A person must "
        "check the access against the refund."
    ),
}

#: One sentence per partial refund — a refund the event says is smaller than the payment
#: it matched. The plan stays, the payment stays ``completed``, and the money kept is
#: re-derived from :attr:`BillingCheckoutAttempt.refunded_amount`, so a person must look
#: at it. The money figures are filled at the call site; the sentence shape is owned here
#: like :data:`REFUND_RECONCILIATION_ALERTS` for the same reason: an over-long summary
#: fails the queue's own cap, and an alert that cannot be written is a refund nobody hears
#: about. It stays under 200 characters even for the widest figures the column can hold.
PARTIAL_REFUND_ALERT_SUMMARY: Final[str] = (
    "Part of a payment came back: {refunded} {currency} refunded of {paid} {currency} "
    "taken; {kept} {currency} is still kept. The plan stays active and staff must "
    "check the refund."
)

#: The attempt status a refund writes. Not ``failed``: money did move and came back, and
#: "no money was taken" is the wrong sentence to leave beside it. This is the value the
#: payment page already has words for ("Refunded" / "The money went back to you") and the
#: value the billing history already treats as settled, so no surface needed a new case.
REFUNDED_ATTEMPT_STATUS: Final[str] = "refunded"

#: The attempt status that says money moved and stayed moved. The money-owed reader
#: (:func:`ai_market_monitor.services.plan_replacements.payment_that_bought`) asks for
#: exactly this, which is why a refund has to leave it.
SETTLED_ATTEMPT_STATUS: Final[str] = "completed"


async def paid_access_can_be_repriced(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> bool:
    """Does any paid plan this account holds sit on a card the company can charge again?

    This decides which route to another plan exists. A card subscription is re-priced by
    the switch buttons, so a second checkout would leave two live subscriptions and two
    charges every month - it must be refused. Crypto access holds no card and cannot be
    re-priced, so buying the other plan **is** the route, and refusing that purchase
    leaves the person with a button that can never work.

    ``False`` when nothing paid is held at all. Callers only ask once they know a paid
    plan is in the way, and "nothing to re-price" is the honest answer either way.
    """

    providers = (
        await session.scalars(
            select(Subscription.provider)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Subscription.user_id == user_id,
                Plan.code.in_(PURCHASABLE_PLAN_CODES),
                Subscription.provider.notin_(("admin", "free", "trial")),
                Subscription.status.in_(
                    (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)
                ),
                (Subscription.current_period_end.is_(None))
                | (Subscription.current_period_end > datetime.now(UTC)),
            )
        )
    ).all()
    return any((provider or "") in RECURRING_PROVIDERS for provider in providers)


#: What becomes of the access somebody holds today when its period ends.
RenewalKind = Literal["free", "trial", "grant", "renews", "stopped", "fixed_period"]


@dataclass(frozen=True, slots=True)
class HeldRenewal:
    """How the access somebody holds today goes on, or ends.

    ``kind`` is one of:

    ``free``          the free plan - no end, nothing to cancel;
    ``trial``         a trial - it ends on its day, and nothing renews;
    ``grant``         given by Hilal Markets - nothing is charged, and nothing renews;
    ``renews``        a card plan the payment company charges again, each ``every``;
    ``stopped``       a card plan whose renewal was stopped - it ends with its period;
    ``fixed_period``  a payment that holds no card, such as crypto - it ends with its period.
    """

    kind: RenewalKind
    ends_at: datetime | None = None
    #: ``"month"`` or ``"year"`` for a plan that renews by itself, when the payment that
    #: bought it says which; ``None`` otherwise, rather than a guess.
    every: str | None = None


def held_renewal(
    entitlement: EntitlementContext,
    subscription: Subscription | None,
    *,
    paid_cycle: str | None = None,
) -> HeldRenewal:
    """The one answer to "will the plan I hold renew by itself?".

    Read from the plan held - the payment company that sold it, and whether its renewal
    was stopped - never from the company this server would use for a new sale. Those are
    different questions. Two pages asked the server's company, so on a server that sells
    by card a customer who paid by crypto read that their plan "renews by itself each
    month", and on a server with no card company a card customer read that it would not.

    ``subscription`` is the row the access comes from. ``paid_cycle`` is the billing cycle
    of the payment that bought it, the only record of monthly or yearly.
    """

    if entitlement.plan.code == "demo":
        return HeldRenewal("free")
    if entitlement.source == "trial":
        return HeldRenewal("trial", entitlement.ends_at)
    provider = (subscription.provider if subscription is not None else None) or ""
    if provider == "trial":
        return HeldRenewal("trial", entitlement.ends_at)
    if provider in {"admin", "free"}:
        return HeldRenewal("grant", entitlement.ends_at)
    if subscription is not None and provider in RECURRING_PROVIDERS:
        if subscription.cancel_at_period_end:
            return HeldRenewal("stopped", entitlement.ends_at)
        every = {"monthly": "month", "annual": "year"}.get(_cycle_key(paid_cycle or ""))
        return HeldRenewal("renews", entitlement.ends_at, every)
    # A crypto invoice or a local test payment holds no card, so nothing can charge it
    # again. With no row at all, nothing on our side could either.
    return HeldRenewal("fixed_period", entitlement.ends_at)


async def held_access_renewal(
    session: AsyncSession, entitlement: EntitlementContext
) -> HeldRenewal:
    """`held_renewal` for the access `EntitlementService.current` returned."""

    from ai_market_monitor.services.plan_replacements import payment_that_bought

    subscription = (
        await session.get(Subscription, entitlement.source_id)
        if entitlement.source == "subscription" and entitlement.source_id is not None
        else None
    )
    payment = (
        await payment_that_bought(session, subscription)
        if subscription is not None and (subscription.provider or "") in RECURRING_PROVIDERS
        else None
    )
    return held_renewal(
        entitlement,
        subscription,
        paid_cycle=payment.billing_cycle if payment is not None else None,
    )


async def active_paid_plan_codes(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> frozenset[str]:
    """The codes of all provider-backed active paid plans for one account.

    Administrative grants, the free plan and trials are not "paid" in the sense that a
    checkout can conflict with them. This is the single source for the **access**
    question — is the person entitled to a paid plan right now — and a recurring card
    whose paid period has ended answers *no* there.

    It is **not** the answer to "which plans does this account hold for a purchase or
    replacement decision": a lapsed recurring card is still held until the renewal
    decision arrives, and that question has one owner,
    :func:`paid_plan_codes_for_replacement_decisions`. Reading the purchase answer
    from this function is how a payment page came to be opened for a plan the account
    was still scheduled to be charged for.
    """

    codes = list(
        (
            await session.scalars(
                select(Plan.code)
                .join(Subscription, Subscription.plan_id == Plan.id)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.provider.notin_(("admin", "free", "trial")),
                    Subscription.status.in_(
                        (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)
                    ),
                    (Subscription.current_period_end.is_(None))
                    | (Subscription.current_period_end > datetime.now(UTC)),
                )
                .order_by(Subscription.updated_at.desc())
            )
        ).all()
    )
    return frozenset(codes)


async def paid_plan_codes_for_replacement_decisions(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> frozenset[str]:
    """The paid plans that a new checkout may need to replace, including a grace window.

    A recurring card subscription stays ``ACTIVE`` at the payment company for a short
    time after its paid period ends, until the renewal decision arrives. During that
    grace window a purchase of a different paid plan must replace it, or the old card
    keeps charging next to the new plan. This helper is for replacement decisions only:
    access still comes from ``active_paid_plan_codes`` and entitlements.
    """

    now = datetime.now(UTC)
    grace_start = now - RECURRING_LAPSED_GRACE_WINDOW
    codes = list(
        (
            await session.scalars(
                select(Plan.code)
                .join(Subscription, Subscription.plan_id == Plan.id)
                .where(
                    Subscription.user_id == user_id,
                    Plan.code.in_(PURCHASABLE_PLAN_CODES),
                    Subscription.provider.notin_(("admin", "free", "trial")),
                    Subscription.status.in_(
                        (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)
                    ),
                    (Subscription.current_period_end.is_(None))
                    | (Subscription.current_period_end > now)
                    | (
                        Subscription.provider.in_(RECURRING_PROVIDERS)
                        & (Subscription.cancel_at_period_end.is_(False))
                        & (Subscription.current_period_end >= grace_start)
                        & (Subscription.current_period_end <= now)
                    ),
                )
                .order_by(Subscription.updated_at.desc())
            )
        ).all()
    )
    return frozenset(codes)


def plan_checkout_availability(
    settings: Settings,
    plan_code: str,
    *,
    active_paid_plan_codes: Collection[str],
    held_access_can_be_repriced: bool = True,
    billing_cycle: str | None = None,
    payment_method: str | None = None,
    replacement_refusal: str = "",
) -> dict[str, Any]:
    """One owner for "can this account buy this plan today, by which method".

    The answer composes two layers: whether the payment company can really sell the plan
    for a period, and whether this account is allowed to buy it. A checkout must never be
    offered when either layer says no.

    A different paid plan is bought through a normal checkout at its full price. The old
    paid period is ended only after that new payment is confirmed. Buying the same plan
    remains refused.

    ``billing_cycle`` and ``payment_method`` let a saved checkout ask about its exact
    offer. Unknown values fail closed. The returned shape still carries every page-facing
    flag so a page can draw the whole choice. ``held_access_can_be_repriced`` remains in
    the signature while older callers are moved over; it no longer changes the answer.

    ``replacement_refusal`` is `PaidPlanReplacementService.replacement_refusal`'s sentence,
    read by the caller because this function reads no database. When the account holds a
    different paid plan and that sentence is not empty, nothing can be bought and the
    sentence is the refusal — the one the checkout route would give when Pay is pressed.
    """

    name = plan_name(plan_code)
    paid_held = set(active_paid_plan_codes) & set(PURCHASABLE_PLAN_CODES)
    holds_this = plan_code in active_paid_plan_codes
    holds_other = bool(paid_held) and not holds_this
    del held_access_can_be_repriced
    # Replacing a paid plan needs the payment that bought it, to value the unused time.
    # When that cannot be found the checkout route refuses (`paid_amount_missing` and its
    # two siblings), so the answer here is no as well, with the route's own sentence.
    replacement_blocked = holds_other and bool(replacement_refusal)
    account_may_buy = not holds_this and not replacement_blocked

    card_monthly = account_may_buy and payment_method_available(
        settings, method="card", plan_code=plan_code, billing_cycle="monthly"
    )
    card_annual = account_may_buy and payment_method_available(
        settings, method="card", plan_code=plan_code, billing_cycle="annual"
    )
    crypto_monthly = account_may_buy and payment_method_available(
        settings, method="crypto", plan_code=plan_code, billing_cycle="monthly"
    )
    purchasable = card_monthly or card_annual or crypto_monthly

    requested_purchasable = purchasable
    if billing_cycle is not None or payment_method is not None:
        cycle = _cycle_key(billing_cycle or "")
        requested_purchasable = {
            ("card", "monthly"): card_monthly,
            ("card", "annual"): card_annual,
            ("crypto", "monthly"): crypto_monthly,
        }.get((payment_method or "", cycle), False)

    refusal = ""
    if holds_this:
        refusal = (
            f"You already have the {name} plan. "
            "You can change it or cancel it on this billing page."
        )
    elif replacement_blocked:
        refusal = replacement_refusal
    elif not purchasable:
        # One sentence, one branch. There used to be two branches here with the
        # identical string — nothing but a place for them to drift apart.
        refusal = "There is no way to pay for this plan yet."

    result: dict[str, Any] = {
        "purchasable": purchasable,
        "card_monthly": card_monthly,
        "card_annual": card_annual,
        "crypto_monthly": crypto_monthly,
        "trial": False,
        "refusal": refusal,
        # The two facts the refusal is built from, published rather than kept private.
        # Pages need them separately: "you already have this one" is a success notice and
        # "you are on a different paid plan" is a refusal, and they are not the same
        # screen. Every page that asked one of these questions used to answer it itself
        # from the raw plan-code set, and each hand-written copy understood a slightly
        # different subset - one of them forgot to ignore plan codes nobody can buy.
        "holds_this": holds_this,
        "holds_other": holds_other,
        # "They hold another plan" and "they must switch instead of buying" are not the
        # same fact, and a page that treats them as one sends a crypto customer to a
        # switch button that cannot move them.
        "must_switch_instead": False,
        "requested_purchasable": requested_purchasable,
    }
    if billing_cycle is not None:
        result["billing_cycle"] = billing_cycle
    return result


def _method_note(
    *,
    method: str,
    provider: str | None,
    available: bool,
) -> str:
    word = _METHOD_WORDS.get(method, method)
    if available:
        company = provider_word(provider) or "our payment company"
        return f"Handled by {company}."
    if provider is None:
        return f"Paying by {word} is switched off just now."
    return f"This plan cannot be paid for by {word} yet."


def payment_method_offers(
    settings: Settings,
    *,
    plan_codes: Sequence[str],
    billing_cycle: str = "monthly",
) -> tuple[PaymentMethodOffer, ...]:
    """Every way of paying, in order, with the truth about each one.

    ``plan_codes`` is one plan when a page is drawing a choice for that plan, and every
    plan on sale when it is drawing the choice before a plan has been picked. A method
    is offered when it can sell at least one of them.
    """

    offers = []
    for method in PAYMENT_METHODS:
        provider = billing_method_provider(settings, method)
        available = any(
            _provider_can_sell(
                settings,
                provider=provider,
                plan_code=code,
                billing_cycle=billing_cycle,
            )
            for code in plan_codes
        )
        offers.append(
            PaymentMethodOffer(
                method=method,
                provider=provider,
                available=available,
                note=_method_note(method=method, provider=provider, available=available),
                company=provider_word(provider),
                company_site=provider_site(provider),
                billing_cycle=billing_cycle,
            )
        )
    return tuple(offers)


def payment_method_offers_by_method(
    settings: Settings,
    *,
    plan_codes: Sequence[str],
    billing_cycle: str = "monthly",
) -> dict[str, PaymentMethodOffer]:
    """The same answer as :func:`payment_method_offers`, keyed by method for templates."""

    return {
        offer.method: offer
        for offer in payment_method_offers(
            settings, plan_codes=plan_codes, billing_cycle=billing_cycle
        )
    }


#: Every billing period a checkout popup can ask about.
BILLING_CYCLES: Final[tuple[str, ...]] = ("monthly", "annual", "trial_7_day")

#: What a checkout charges before a way of paying has been chosen. Written here beside the
#: per-method sentences because it is the same fact, half answered: the amount is already
#: known and what happens after today is not, until a method is picked. ``{amount}`` is
#: filled in by the page for the reason :attr:`PaymentMethodOffer.charge_story` explains.
CHARGE_STORY_BEFORE_CHOOSING: Final[str] = (
    "{amount} today. Choose how you want to pay to see what happens after that."
)


def payment_method_payload(
    settings: Settings,
    *,
    plan_code: str,
    billing_cycles: Sequence[str] = BILLING_CYCLES,
) -> dict[str, dict[str, dict[str, object]]]:
    """One plan's payment choices, decided on the server, ready for a script to draw.

    The browser is handed the **answer**, never the ingredients. Both checkout popups used
    to receive raw flags and work out for themselves which methods to offer, which is a
    second copy of this rule written in JavaScript — and a copy that can disagree.

    The company's name travels with the answer for the same reason. The popup's own button
    read "Continue to Creem" and "Continue to NOWPayments" from two strings written into
    the script, so the day a setting names another company the button would have sent
    people to a company that was never going to charge them.

    ``story``, ``terms`` and ``renews`` travel with it for the third time the same thing
    happened. Both popups told everybody the charge repeated every month, because the one
    sentence they had was written for a card. It is not true of a crypto invoice, and it
    is a promise about somebody's money.
    """

    return {
        cycle: {
            offer.method: {
                "available": offer.available,
                "note": offer.note,
                "company": offer.company,
                "story": offer.charge_story,
                "terms": offer.access_terms,
                "renews": offer.renews_by_itself,
            }
            for offer in payment_method_offers(
                settings, plan_codes=(plan_code,), billing_cycle=cycle
            )
        }
        for cycle in billing_cycles
    }


def payment_method_refusal(
    settings: Settings,
    *,
    method: str,
    plan_code: str,
    billing_cycle: str = "monthly",
) -> str:
    """Why this way of paying cannot be used, and what to do instead.

    A refusal that only says "not available" leaves a beginner with nothing to try. This
    names the other way of paying when there is one, and says plainly that there is none
    when there is not.
    """

    offers = payment_method_offers(
        settings, plan_codes=(plan_code,), billing_cycle=billing_cycle
    )
    refused = next((offer for offer in offers if offer.method == method), None)
    lead = (
        refused.note
        if refused is not None
        else f"Paying by {_METHOD_WORDS.get(method, method)} is switched off just now."
    )
    others = [offer for offer in offers if offer.available and offer.method != method]
    if others:
        instead = " or ".join(_METHOD_WORDS.get(offer.method, offer.method) for offer in others)
        return f"{lead} You can pay with {instead} instead."
    return f"{lead} There is no other way to pay for this plan yet."


def annual_billing_available(
    settings: Settings,
    *,
    plan_codes: Sequence[str] = PURCHASABLE_PLAN_CODES,
) -> bool:
    """Whether a year at a time can really be bought for every plan named.

    The landing page, the older billing page and the checkout popup each used to decide
    this for themselves, and one of them only looked at whether a plan *offers* a yearly
    price — not at whether the payment company holds a yearly product to charge for it.
    That is how "Annual" stayed pressable and then failed at the payment step.
    """

    return all(
        any(
            payment_method_available(
                settings, method=method, plan_code=code, billing_cycle="annual"
            )
            for method in PAYMENT_METHODS
        )
        for code in plan_codes
    )


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
        # The exact money, written as a JSON number: ``core/money.py`` is the one
        # owner of how a decided amount leaves this system. NOWPayments' published
        # OpenAPI types ``price_amount`` for ``POST /invoice`` as a number
        # (``format: double``), its official client takes int|float and its own
        # request examples send ``"price_amount": 3999.5``; the help-centre article
        # carries one Number row and one String row for the same field, so it
        # settles nothing. It used to go out through ``float``, which is not the
        # same value for most cents and prints 9.00 as 9.0 — the D3 defect. The fix
        # for that sent the digits quoted, an unverified wire-type change on every
        # crypto checkout — the H-3 defect. ``wire_json_body`` is the one owner that
        # keeps both halves: the provider's number, this product's exact digits.
        payload: dict[str, object] = {
            "price_currency": currency.lower(),
            "order_id": order_id,
            "order_description": f"Hilal Markets {plan_name} 30-day access",
            "ipn_callback_url": (
                f"{str(self.settings.public_base_url).rstrip('/')}"
                "/api/v1/billing/webhooks/nowpayments"
            ),
            "success_url": success_url,
            "cancel_url": cancel_url,
            "is_fee_paid_by_user": False,
        }
        invoice_body = wire_json_body(
            payload,
            number_field="price_amount",
            amount=amount,
            currency=currency,
        )
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
            # ``content=`` and not ``json=``: the finished body is already exact
            # bytes, and letting httpx encode the payload instead would put a float
            # or a quoted string back into the amount. The Content-Type header
            # above is what says so, because ``content=`` carries no type of its own.
            content=invoice_body,
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
        product_id = creem_product_id_for(
            self.settings, plan_code=plan_code, billing_cycle=billing_cycle
        )
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
        secret = webhook_secret_for(self.settings, provider)
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

    def ensure_payment_can_be_confirmed(self) -> None:
        """Refuse the sale when a payment through this company could never be proven.

        The pages ask :func:`payment_can_be_confirmed` before drawing a way of paying;
        this asks the same question on the way in, so a request that skipped the page —
        an old browser tab, a saved form, a direct post — is refused too rather than
        creating a payment nobody can confirm afterwards.
        """

        if not payment_can_be_confirmed(self.settings, self.provider.provider_name):
            raise BillingError(
                "billing_webhook_secret_missing",
                "This way of paying is not finished being set up, "
                "so nothing was charged.",
            )

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
        discount: DiscountedPrice | None = None,
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
            await paid_plan_codes_for_replacement_decisions(
                self.session, user_id=user_id
            )
        )
        if plan_code in current_plans:
            raise BillingError(
                "already_subscribed",
                f"Your {plan.name} plan is already active.",
            )
        # Somebody already paying for a different plan buys the new one here, at its full
        # price (the owner's rule of 2026-09-10). The plan held now is frozen onto this
        # checkout below and ends only after the new payment is confirmed; the value of
        # its unused days is recorded and returned by hand (`plan_replacements.py`).
        #
        # Whether this account may buy this plan at all is one question with one owner,
        # `plan_checkout_availability`, and the pages ask it too — so a Pay button and
        # this route can never give different answers.
        other_paid = sorted(current_plans & set(PURCHASABLE_PLAN_CODES))
        availability = plan_checkout_availability(
            self.settings,
            plan_code=plan_code,
            active_paid_plan_codes=current_plans,
            billing_cycle=billing_cycle,
            payment_method=provider_method(self.provider.provider_name),
        )
        if not availability["requested_purchasable"]:
            raise BillingError(
                "plan_not_available",
                "This plan and payment choice are not available. Nothing was charged. "
                "Return to billing and choose an available option.",
            )

        replacement_source = None
        if other_paid:
            from ai_market_monitor.services.plan_replacements import (
                PaidPlanReplacementService,
                PlanReplacementError,
            )

            try:
                replacement_source = await PaidPlanReplacementService(
                    self.session, self.settings
                ).source_for_checkout(user_id=user_id, target_plan_code=plan_code)
            except PlanReplacementError as exc:
                raise BillingError(exc.code, str(exc)) from exc

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
        # A discount code is part of what is being bought, so it belongs in the key that
        # decides whether this is the same checkout as a previous one.
        #
        # Leaving it out was a way to charge the wrong amount: press Pay, come back, type
        # HILAL25, press Pay again, and the second request matched the first attempt —
        # which still held the full price and a payment page already opened at it. The
        # code was accepted on screen and never reached the invoice.
        discount_key = f"{discount.code}:{discount.percent}" if discount else ""
        idempotency_key = sha256(
            (
                f"checkout:{user_id}:{plan.id}:{self.provider.provider_name}:{billing_cycle}:"
                f"{self.settings.billing_terms_version}:{normalized_key}:{discount_key}"
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
                    # Same reason as the key above: an attempt opened at a different price
                    # is a different order, and handing it back would send somebody to a
                    # payment page for an amount they did not agree to.
                    BillingCheckoutAttempt.discount_code.is_(None)
                    if discount is None
                    else BillingCheckoutAttempt.discount_code == discount.code,
                )
                .order_by(BillingCheckoutAttempt.created_at.desc())
                .limit(1)
            )
        if existing is not None:
            return CheckoutAttemptResult(attempt=existing, duplicate=True)

        full_amount = self.checkout_amount(plan.code, plan.price_monthly, billing_cycle)
        amount = full_amount
        if discount is not None:
            # The discount was priced against the amount this checkout really charges, so
            # a mismatch here means the price moved between the Apply button and the Pay
            # button. Refusing is the only safe reading: the alternative is charging one
            # number while the screen shows another.
            if discount.full != full_amount:
                raise BillingError(
                    "discount_price_changed",
                    "The price changed while you were paying. Please check the code again.",
                )
            amount = discount.final
        attempt = BillingCheckoutAttempt(
            user_id=user_id,
            plan_id=plan.id,
            billing_cycle=billing_cycle,
            provider=self.provider.provider_name,
            status="creating",
            idempotency_key=idempotency_key,
            terms_version=self.settings.billing_terms_version,
            amount=amount,
            currency=plan.currency.upper(),
            discount_code=discount.code if discount else None,
            discount_percent=discount.percent if discount else None,
            terms_accepted_at=now,
            expires_at=now + timedelta(minutes=self.settings.billing_checkout_ttl_minutes),
            billing_profile=_sanitize_billing_profile(billing_profile),
            replaces_subscription_id=(
                replacement_source[0].id if replacement_source is not None else None
            ),
            replaces_checkout_attempt_id=(
                replacement_source[1].id if replacement_source is not None else None
            ),
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
                "discount_code": attempt.discount_code or "",
                "discount_percent": str(attempt.discount_percent or ""),
                "discount_source": discount.source if discount else "",
            },
        )
        return CheckoutAttemptResult(attempt=attempt, duplicate=False)

    def _normalize_billing_cycle(self, *, plan_code: str, requested: str) -> str:
        normalized = requested.strip().lower()
        offer = plan_offer(plan_code)
        if normalized == "trial_7_day":
            # Named from the catalog, never typed here: the plan was called "Monitor"
            # in this sentence long after the product had renamed it, and the sentence
            # also promised a refund window that only one plan actually has.
            raise BillingError(
                "billing_cycle_not_available",
                f"{plan_name(plan_code)} is sold as a paid monthly plan. "
                "There is no free trial period.",
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
    def checkout_amount(
        plan_code: str,
        monthly_amount: Decimal,
        billing_cycle: str,
    ) -> Decimal:
        """What this checkout will actually charge.

        Public because the review page must quote it. The page used to print the plan
        catalogue's ``price_monthly`` instead, which is the *normal* price: while a
        launch offer was running, the last screen before payment said $20 and the
        payment asked for $7. One function now answers "what does this cost", and the
        page and the charge cannot say different numbers.
        """

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
        # The last moment before a real payment page exists, and so the last moment a sale
        # that could never be confirmed can still be refused for free.
        self.ensure_payment_can_be_confirmed()
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
        availability = plan_checkout_availability(
            self.settings,
            plan_code=plan.code,
            # The same held set `prepare_checkout` and the resume route read — the
            # grace-aware owner. A recurring card whose paid period ended but whose
            # renewal decision has not arrived is still held, and the card will
            # charge again beside any new payment. The no-grace reader answers the
            # access question, not this one: on it this line computed "holds nothing"
            # for a plan the account holds, said it was purchasable, and opened a
            # second payment page for it.
            active_paid_plan_codes=await paid_plan_codes_for_replacement_decisions(
                self.session, user_id=user_id
            ),
            billing_cycle=attempt.billing_cycle,
            payment_method=provider_method(attempt.provider),
        )
        if not availability["requested_purchasable"]:
            raise BillingError(
                "plan_not_available",
                "This plan and payment choice are no longer available. Nothing was "
                "charged. Return to billing and choose an available option.",
            )
        from ai_market_monitor.services.plan_replacements import (
            PaidPlanReplacementService,
            PlanReplacementError,
        )

        try:
            await PaidPlanReplacementService(self.session, self.settings).attach_source(
                attempt=attempt, target_plan_code=plan.code
            )
        except PlanReplacementError as exc:
            raise BillingError(exc.code, str(exc)) from exc
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
            # What the company says has come back for this payment, already in **major
            # units** once normalized (the minor-unit parser divides by 100); the
            # recorder reads it with ``Decimal(str(...))`` and stores nothing it cannot
            # read. Stripe's ``amount_refunded`` exists on a charge — a refund event's
            # object — and is the **cumulative** total refunded on that charge, which is
            # why the flag below is set with it: the recorder must not add a cumulative
            # figure to a running total twice. A dispute carries no such field, and the
            # Creem and NOWPayments normalisers invent none either: no field name is
            # proven for them and no fixture proves one, so their refund amounts stay
            # unknown — which downstream means a full refund, exactly today's behaviour.
            "refunded_amount": BillingService._minor_unit_amount(
                stripe_object.get("amount_refunded")
            ),
            "refunded_total_is_cumulative": True,
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
        # What Creem's own checkout page took off, and the code that did it.
        #
        # A buyer on the card route reaches Creem's page, which has a discount box we do
        # not control. Creem reports the discount on the order; without reading it, a card
        # payment made with a Creem code arrives smaller than the amount recorded here and
        # is refused as underpaid — the buyer pays and never gets the plan.
        discount = dict(checkout.get("discount") or {})
        if not discount and isinstance(item.get("discount"), Mapping):
            discount = dict(item["discount"])
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
            # The product Creem is really charging for. It outranks `plan_code` above on a
            # renewal: the metadata is frozen at first checkout and a plan change never
            # rewrites it, so after an upgrade the two disagree and only this one is right.
            "provider_product_id": product.get("id"),
            "provider_discount_amount": BillingService._minor_unit_amount(
                order.get("discount_amount")
            ),
            "provider_discount_code": (
                discount.get("discountCode") or discount.get("discount_code") or None
            ),
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
            if existing.processing_status == "failed":
                return await self.reprocess_failed_event(event_id)
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
            event_id=event_id,
            event_type=event_type,
        )
        # Read without raising: an unreadable person id must not cost the product the
        # stored event. A payment whose checkout cannot be matched has already been
        # refused by ``_hydrate_checkout_data`` above, which runs first.
        user_id = self._uuid_if_readable(data.get("user_id"))
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
            if subscription is not None and self._is_completed_payment_event(
                provider=provider,
                event_type=event_type,
                subscription=subscription,
            ):
                await self._after_completed_payment(
                    billing_event=event,
                    subscription=subscription,
                    data=data,
                )
        except Exception as exc:
            if isinstance(exc, PlanMoveFailed):
                await self._save_replacement_failure(
                    provider=provider,
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    user_id=user_id,
                    error_code=exc.code,
                )
                raise
            if (
                isinstance(exc, BillingError)
                and exc.code == "subscription_resurrected_after_cancel"
            ):
                await self._save_resurrection_alert(
                    event=event,
                    error_code=exc.code,
                )
                raise
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
            # A retry must pass the same checkout, amount and currency checks as the
            # first delivery. Replaying stored data is not permission to skip them.
            await self._hydrate_checkout_data(
                data,
                provider=event.provider,
                event_id=event.provider_event_id,
                event_type=event.event_type,
            )
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
            if subscription is not None and self._is_completed_payment_event(
                provider=event.provider,
                event_type=event.event_type,
                subscription=subscription,
            ):
                await self._after_completed_payment(
                    billing_event=event,
                    subscription=subscription,
                    data=data,
                )
        except Exception as exc:
            if isinstance(exc, PlanMoveFailed):
                await self._save_replacement_failure(
                    provider=event.provider,
                    event_id=event.provider_event_id,
                    event_type=event.event_type,
                    payload=payload,
                    user_id=event.user_id,
                    error_code=exc.code,
                )
                raise
            if (
                isinstance(exc, BillingError)
                and exc.code == "subscription_resurrected_after_cancel"
            ):
                await self._save_resurrection_alert(
                    event=event,
                    error_code=exc.code,
                )
                raise
            event.processing_status = "failed"
            event.error_code = getattr(exc, "code", exc.__class__.__name__)
            await self.session.flush()
            raise
        event.processing_status = "processed"
        event.processed_at = datetime.now(UTC)
        await self._close_replacement_failure(event.provider_event_id)
        await self.session.flush()
        return BillingWebhookResult(
            event_id=event.provider_event_id,
            event_type=event.event_type,
            processing_status=event.processing_status,
            replayed=False,
            user_id=event.user_id,
        )

    async def _after_completed_payment(
        self,
        *,
        billing_event: BillingEvent,
        subscription: Subscription,
        data: dict[str, Any],
    ) -> None:
        """Run every durable result of one confirmed payment in one transaction."""

        from ai_market_monitor.services.payment_emails import PaymentEmailOutboxService
        from ai_market_monitor.services.plan_replacements import (
            PaidPlanReplacementService,
            PlanReplacementError,
        )

        attempt_id = self._parse_uuid(data.get("checkout_attempt_id"))
        if attempt_id is not None:
            try:
                await PaidPlanReplacementService(
                    self.session, self.settings
                ).apply_after_payment(
                    checkout_attempt_id=attempt_id,
                    replacement=subscription,
                    billing_event=billing_event,
                )
            except PlanReplacementError as exc:
                raise PlanMoveFailed(exc.code, str(exc)) from exc
        await PaymentEmailOutboxService(self.session, self.settings).enqueue(
            billing_event=billing_event,
            subscription=subscription,
            data=data,
        )
        await self._credit_the_affiliate(
            provider=billing_event.provider,
            provider_event_id=billing_event.provider_event_id,
            subscription=subscription,
            data=data,
        )

    @staticmethod
    def _plan_move_failure_key(event_id: str) -> str:
        """The one staff alert for one payment whose plan move could not be finished.

        Built through the issue queue's own sanitizer, so a provider event id in any
        shape — Stripe's uppercase ``evt_01JABCdefGHI``, NOWPayments' colons, an id
        longer than the queue can store — still produces a key that can be written.
        ``_close_replacement_failure`` reads the key back through this same function,
        so the write and the lookup can never disagree.
        """

        from ai_market_monitor.observability.issues import sanitize_dedupe_key

        return sanitize_dedupe_key(f"billing:plan-move-failed:{event_id}")

    async def _close_replacement_failure(self, event_id: str) -> None:
        """Close the alert a failed paid move raised, once the same event goes through.

        A later try — Creem sending the event again, or a person reprocessing it — that
        finishes the move leaves nothing to do. Left open, the critical alert would send a
        person to reprocess an event that is already done. The money-owed notice the move
        wrote stays open: that sum still has to be sent by hand.
        """

        from ai_market_monitor.db.models.operations import OperationalIssue
        from ai_market_monitor.observability.issues import OperationalIssueService

        issue = await self.session.scalar(
            select(OperationalIssue).where(
                OperationalIssue.dedupe_key == self._plan_move_failure_key(event_id)
            )
        )
        if issue is None or issue.state == "resolved":
            return
        await OperationalIssueService(self.session).transition(
            issue_id=issue.id,
            to_state="resolved",
            actor="system",
            reason="The same payment event went through on a later try.",
        )

    async def _save_replacement_failure(
        self,
        *,
        provider: str,
        event_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        user_id: UUID | None,
        error_code: str,
    ) -> None:
        """Keep a failed paid move visible even though its plan changes roll back."""

        from ai_market_monitor.observability.issues import OperationalIssueService

        await self.session.rollback()
        event = await self.session.scalar(
            select(BillingEvent).where(BillingEvent.provider_event_id == event_id)
        )
        if event is None:
            event = BillingEvent(
                user_id=user_id,
                provider=provider,
                provider_event_id=event_id,
                event_type=event_type,
                processing_status="failed",
                payload_redacted=redact_payload(dict(payload)),
                error_code=error_code,
                created_at=datetime.now(UTC),
            )
            self.session.add(event)
            await self.session.flush()
        else:
            event.processing_status = "failed"
            event.error_code = error_code
            event.processed_at = None
        await OperationalIssueService(self.session).record_occurrence(
            dedupe_key=self._plan_move_failure_key(event_id),
            category="billing",
            severity="critical",
            # Fixed sentences. With the error code written into the sentence, one code made
            # it longer than a record may be, and the alert itself failed. The code is kept
            # as evidence instead, where its length cannot matter.
            summary=(
                "A customer paid for a new plan, but the old recurring charge could not be "
                "stopped after retries. Money was taken and a new subscription may exist. "
                "Reprocess the failed billing event."
                if error_code == "old_subscription_cancel_failed"
                else "A customer paid for a new plan, but the plan it replaces could not be "
                "ended safely. Money was taken and a new subscription may exist. Check the "
                "account, then reprocess the failed billing event."
            ),
            affected_scope="billing.plan_replacement",
            evidence_refs=(
                f"billing_event:{event_id}"[:134],
                f"billing_error:{error_code}"[:134],
            ),
            source="billing_webhook",
        )
        # The calling route rolls back ordinary failures. Commit this alert first so a
        # person can see it and the saved event can be deliberately reprocessed.
        await self.session.commit()

    async def _save_resurrection_alert(
        self,
        *,
        event: BillingEvent,
        error_code: str,
    ) -> None:
        """Keep any event that tried to make an ended subscription live again visible.

        The provider sent an update naming a subscription our database already shows
        as ended. The subscription row is left ended, but a person must know: if the
        event came with a charge, that money moved against a plan we ended on purpose,
        and it has to be refunded or reconciled by hand. Whether money moved is the
        provider's, not ours to assume from the event type, so the alert asks rather
        than claims.
        """

        from ai_market_monitor.observability.issues import (
            OperationalIssueService,
            sanitize_dedupe_key,
        )

        event.processing_status = "failed"
        event.error_code = error_code
        await self.session.flush()
        await OperationalIssueService(self.session).record_occurrence(
            dedupe_key=sanitize_dedupe_key(
                f"billing:subscription-resurrected:{event.provider_event_id}"
            ),
            category="billing",
            severity="critical",
            # Fixed sentences, measured at 197 characters — inside the queue's
            # 200-character record limit. Naming the event id here would push an
            # over-long provider id past the limit and lose the alert.
            summary=(
                "An event from the payment company tried to make a subscription our "
                "system shows as ended live again. The subscription was left ended. A "
                "person must check whether the payment company charged for it."
            ),
            affected_scope="billing.subscription_resurrection",
            evidence_refs=(
                f"billing_event:{event.provider_event_id}"[:134],
                f"billing_error:{error_code}"[:134],
            ),
            source="billing_webhook",
        )
        await self.session.commit()

    async def _credit_the_affiliate(
        self,
        *,
        provider: str,
        provider_event_id: str,
        subscription: Subscription,
        data: Mapping[str, Any],
    ) -> None:
        """Turn one confirmed payment into one earning for whoever brought this customer.

        Called from the two places a payment is confirmed — the live webhook and the
        replay of a failed one — and keyed on the provider's own event id, so the same
        payment arriving twice earns the affiliate once. Everything else is decided by
        :mod:`ai_market_monitor.services.affiliate_attribution`: who the customer belongs
        to, whether this is their first payment or a later one, and which of the two
        rates applies. Nothing about commission is worked out here.

        A failure never fails the payment. The subscription is already active and the
        customer already has what they paid for; an earning that could not be written is
        a missing row somebody can see, while an exception here would turn a successful
        payment into a webhook the provider keeps retrying.
        """

        from ai_market_monitor.services.affiliate_attribution import (
            ReferralAttributionService,
        )

        try:
            await ReferralAttributionService(self.session).record_payment(
                customer_user_id=subscription.user_id,
                event_key=f"payment:{provider}:{provider_event_id}",
                # Only a last resort. The attribution service prefers the completed
                # checkout row, which is the record of money that really moved; this is
                # what the provider said, used when there is no checkout behind it.
                paid_amount_usd=_amount_or_none(data.get("amount")),
                plan_id=subscription.plan_id,
            )
        except Exception:  # pragma: no cover - a diagnostic must never become the failure
            logger.exception(
                "affiliate commission could not be recorded",
                extra={"provider_event_id": provider_event_id},
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
            subscription = await self._upsert_subscription(
                provider=provider, data=data, event_type=event_type
            )
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
        if event_type in REFUND_EVENT_TYPES:
            if data.get("refund_is_partial") is True:
                # Part of the money came back and part is still held: this is not the
                # event that ends a plan. The recorder in ``_attach_refund_to_attempt``
                # decided full vs partial from the amount the provider reported and
                # nobody else re-decides it here. The payment stays ``completed`` (the
                # status rule in ``_record_checkout_event`` leaves it alone), the
                # money readers value it through ``money_kept``, and a person is told —
                # a smaller refund is a human judgement about the customer's account,
                # not an automatic full return.
                attempt_id = self._uuid_if_readable(data.get("checkout_attempt_id"))
                attempt = (
                    await self.session.get(BillingCheckoutAttempt, attempt_id)
                    if attempt_id is not None
                    else None
                )
                if attempt is not None:
                    await self._save_partial_refund_alert(
                        event_id=event_id, event_type=event_type, attempt=attempt
                    )
                return None
            # The money is going back, so the plan it bought is over — for the refund
            # names. A dispute is money frozen while the card network decides, and
            # ending a plan on a decision that can still be reversed is not this
            # function's call to make, so those names stay audit-only.
            #
            # Neither kind may be *refused*. A payment event that names a checkout we
            # cannot see has to be refused, because accepting it would hand out a plan for
            # money nobody paid. A refund carries no plan to hand out, and raising here
            # rolls the whole webhook back: the record of money leaving is lost with it,
            # and the provider stops retrying eventually and stops at that loss. So when
            # the event names nobody, the attempt is still marked below and a person is
            # told, instead of an error being returned.
            refunded_plan = await self._end_refunded_plan(
                provider=provider, event_id=event_id, event_type=event_type, data=data
            )
            if refunded_plan is None:
                # Either a chargeback name, which has never touched the plan, or a refund
                # that named no subscription to end. The audit line is kept exactly as it
                # was for those names; the payment itself is settled by
                # ``_record_checkout_event`` a moment later.
                if event_type in REFUND_CHARGEBACK_EVENT_TYPES:
                    user_id = self._uuid_if_readable(data.get("user_id"))
                    if user_id:
                        self._audit(user_id, f"billing.{event_type}", "user", user_id, {})
                return None
            await EntitlementService(self.session).snapshot(refunded_plan.user_id)
            await EntitlementService(self.session).pause_excess_after_downgrade(
                refunded_plan.user_id
            )
            self._audit(
                refunded_plan.user_id,
                "billing.payment_refunded",
                "subscription",
                refunded_plan.id,
                {},
            )
            return refunded_plan
        if event_type in {"payment.expired", "payment.failed"}:
            user_id = self._parse_uuid(data.get("user_id"))
            if user_id:
                self._audit(user_id, f"billing.{event_type}", "user", user_id, {})
        return None

    async def _end_refunded_plan(
        self,
        *,
        provider: str,
        event_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> Subscription | None:
        """End the paid plan a refund returned the money for, when the event names it.

        Returns ``None`` when the event cannot be tied to a subscription — and the
        caller must treat that as "recorded, not refused". Which attempt the money came
        back for is what the money readers ask, and that is settled by
        :meth:`_attach_refund_to_attempt` on the way in; this is only the access half.

        A named-but-unknown subscription is one of those ``None``s: the event says a
        plan this system has never heard of was refunded, and inventing a real CANCELED
        row from that claim pollutes the customer's record with a plan that never
        existed — the next event naming the same reference would then be met by the
        resurrection alert over a row nobody can explain. The honest answer is the
        same one the code already gives when the event names nothing at all: leave the
        row unwritten, place the money, and tell a person the plan could not be found.
        """

        if event_type not in REFUND_ENDS_PLAN_EVENT_TYPES:
            return None
        if data.get("refund_is_full") is False:
            # The recorder read this event's amount and found the payment still holds
            # money: a partial refund ends nothing. The caller already guards; this is
            # the belt so a new caller cannot end a plan on a partial by accident.
            return None
        user_id = self._uuid_if_readable(data.get("user_id"))
        reference = data.get("provider_subscription_id") or data.get("subscription_id")
        if user_id is None or not reference:
            # Nothing names the plan, so nothing is ended. If the checkout was matched
            # the money truth is already safe, and the unattached-refund alert covers the
            # case where neither half could be placed.
            if data.get("checkout_attempt_id") not in (None, ""):
                await self._save_refund_alert(
                    event_id=event_id,
                    event_type=event_type,
                    reason="plan",
                )
            return None
        held = await self.session.scalar(
            select(Subscription.id)
            .where(
                Subscription.provider == provider,
                Subscription.provider_subscription_id == str(reference),
            )
            .limit(1)
        )
        if held is None:
            # The same lookup :meth:`_upsert_subscription` would make — asked here
            # first, because an answer of "no such row" must not create one.
            if data.get("checkout_attempt_id") not in (None, ""):
                await self._save_refund_alert(
                    event_id=event_id,
                    event_type=event_type,
                    reason="plan",
                )
            return None
        subscription = await self._upsert_subscription(
            provider=provider,
            data=data,
            forced_status=SubscriptionStatus.CANCELED,
        )
        subscription.canceled_at = datetime.now(UTC)
        return subscription

    @staticmethod
    def _refund_alert_key(event_id: str, reason: str) -> str:
        """The one key for one refund this system could not place.

        The provider's own event id is hashed instead of written in. The issue queue
        refuses a key containing whitespace, and an event id is exactly whatever the
        company produced — a refund alert that cannot be written is a refund that
        vanished without a trace, which is the failure this alert exists to prevent. The
        raw id is still kept as an evidence pointer, where the queue's own sanitizer
        truncates and cleans it.
        """

        from ai_market_monitor.observability.issues import sanitize_dedupe_key

        digest = sha256(event_id.encode("utf-8")).hexdigest()[:32]
        return sanitize_dedupe_key(f"billing:refund-unattached:{digest}:{reason}")

    async def _save_refund_alert(
        self, *, event_id: str, event_type: str, reason: str
    ) -> None:
        """Tell a person that money went back and this system could not place it.

        Two things can be missing. ``payment``: no checkout attempt could be matched, so
        a payment that is no longer money held still reads as one — that is the row the
        money-owed valuation and the completed-sale count read. ``plan``: the payment was
        matched but no subscription could be, so the access it bought may still be live.

        Fixed sentences: an over-long summary fails the queue's own check, and an alert
        that cannot be written is a refund that left no trace at all. The provider's event
        id never enters the sentence — see :meth:`_refund_alert_key`.
        """

        from ai_market_monitor.observability.issues import OperationalIssueService

        await OperationalIssueService(self.session).record_occurrence(
            dedupe_key=self._refund_alert_key(event_id, reason),
            category="billing",
            severity="critical",
            summary=REFUND_RECONCILIATION_ALERTS[reason],
            affected_scope="billing.refund_reconciliation",
            evidence_refs=(
                f"billing_event:{event_id}"[:134],
                f"billing_error:refund_{reason}_unmatched"[:134],
                f"billing_event_type:{event_type}"[:134],
            ),
            source="billing_webhook",
        )
        await self.session.flush()

    async def _save_partial_refund_alert(
        self, *, event_id: str, event_type: str, attempt: BillingCheckoutAttempt
    ) -> None:
        """Tell a person that part of a payment came back and this system left it standing.

        A partial refund is not an error — the money truth is stored on the payment —
        but it is a human judgement nobody should have to discover from a support
        ticket: the plan stayed live while part of its price went back, and a standing
        manual payout was re-valued rather than voided. So one critical row per
        payment, its summary holding the three money figures in plain words.

        The dedupe key is the payment, not the event: repeated partials on one payment
        are one problem described more, so they raise the count on the one row. The
        event id is kept as an evidence pointer, where the queue's own sanitizer cleans
        or hashes it — the same rule :meth:`_refund_alert_key` exists for.
        """

        from ai_market_monitor.observability.issues import (
            OperationalIssueService,
            sanitize_dedupe_key,
        )

        kept = money_kept(attempt.amount, attempt.refunded_amount)
        await OperationalIssueService(self.session).record_occurrence(
            dedupe_key=sanitize_dedupe_key(f"billing:partial-refund:{attempt.id}"),
            category="billing",
            severity="critical",
            summary=PARTIAL_REFUND_ALERT_SUMMARY.format(
                refunded=f"{attempt.refunded_amount:.2f}",
                paid=f"{attempt.amount:.2f}",
                kept=f"{kept:.2f}",
                currency=attempt.currency.upper(),
            ),
            affected_scope="billing.partial_refund",
            evidence_refs=(
                f"billing_checkout_attempt:{attempt.id}"[:134],
                f"billing_event:{event_id}"[:134],
                f"billing_event_type:{event_type}"[:134],
            ),
            source="billing_webhook",
        )
        await self.session.flush()

    async def _attach_refund_to_attempt(
        self,
        *,
        provider: str,
        event_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Point a refund or chargeback at the checkout attempt whose money came back.

        Providers are inconsistent about echoing the reference they were given: a Stripe
        charge carries the charge and the subscription, a Creem refund names the
        subscription, and a crypto order can arrive with nothing but the person. Skipping
        those was how a refund came to be applied to the plan and never to the payment.

        Nothing is guessed. When the event carries no reference the person, the plan or
        the subscription behind it has to leave exactly one settled payment standing;
        where the payload supports two readings, no payment is picked and a person is
        asked instead. Marking the wrong payment refunded takes a real sale away from the
        money-owed path, which is the same mistake wearing the other sign.

        This is also the one place a refund's amount is read and put on the record —
        before the access and status writers run, so every later reader sees the same
        total. A refund that matched no attempt is reported by the unattached alert
        below and decides nothing here: with no payment there is no total to add to.
        """

        attempt: BillingCheckoutAttempt | None = None
        raw_attempt_id = data.get("checkout_attempt_id")
        if raw_attempt_id in (None, ""):
            attempt = await self._resolve_refund_attempt(provider=provider, data=data)
        else:
            # An unreadable reference says this event is not safe to apply to any payment.
            # It does not say the refund did not happen, so it is a question for a person,
            # not a reason to fail the webhook.
            attempt_id = self._uuid_if_readable(raw_attempt_id)
            if attempt_id is not None:
                candidate = await self.session.get(BillingCheckoutAttempt, attempt_id)
                if candidate is not None and self._refund_matches_attempt(
                    candidate, provider=provider, data=data
                ):
                    attempt = candidate
        if attempt is None:
            # Drop the unusable reference rather than pass it on: it is the same string
            # the money-in path would refuse, and the next reader would only choke on it.
            data.pop("checkout_attempt_id", None)
            await self._save_refund_alert(
                event_id=event_id, event_type=event_type, reason="payment"
            )
            return
        data["checkout_attempt_id"] = str(attempt.id)
        data["user_id"] = str(attempt.user_id)
        plan = await self.session.get(Plan, attempt.plan_id)
        if plan is not None:
            data["plan_code"] = plan.code
        await self._record_refund_amount(attempt=attempt, data=data)

    async def _record_refund_amount(
        self, *, attempt: BillingCheckoutAttempt, data: dict[str, Any]
    ) -> None:
        """Store the refunded total on the payment and decide full vs partial.

        A running money total may not lose a concurrent update, so the matched row is
        re-read under ``FOR UPDATE`` (SQLite no-ops the lock; PostgreSQL serialises the
        two refunds). A replayed same event id never reaches here — ``process_event``
        short-circuits it — so two deliveries that do reach here are two refunds, and
        the totals say so:

        * amount unknown → the stored total stays as it was and the refund is full.
          Guessing a figure for a company that reports none would invent money evidence.
        * amount known, provider reports a cumulative total (Stripe) → the new total is
          the larger of stored and reported, never the sum.
        * amount known otherwise → the new total adds it in.

        Any total at or above the payment amount is a full refund — including more than
        the payment, which says the whole payment came back with something beside it, not
        that the payment still owes a credit. The flags below are the one decision
        every later reader (plan, status, payout) reads; nobody re-decides it.
        """

        locked = await self.session.scalar(
            select(BillingCheckoutAttempt)
            .where(BillingCheckoutAttempt.id == attempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        payment = locked if locked is not None else attempt
        refunded = self._refund_event_amount(data)
        stored = payment.refunded_amount or Decimal("0")
        if refunded is None:
            new_total = stored
        elif data.get("refunded_total_is_cumulative"):
            new_total = max(stored, refunded)
        else:
            new_total = stored + refunded
        refund_is_full = refunded is None or new_total >= payment.amount
        data["refund_is_full"] = refund_is_full
        data["refund_is_partial"] = not refund_is_full
        payment.refunded_amount = new_total
        await self.session.flush()

    @staticmethod
    def _refund_event_amount(data: Mapping[str, Any]) -> Decimal | None:
        """The refunded amount one normalized event reports, in major units, or ``None``.

        The normaliser already turned the provider's field into a major-units string (or
        left it absent); unreadable text is the same unknown, not a reason to fail the
        webhook — a refund that says nothing about its amount is a full refund by the
        rule above, which is what this system believed until the amount could be read.
        """

        raw = data.get("refunded_amount")
        if raw in (None, ""):
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            return None

    @staticmethod
    def _refund_matches_attempt(
        attempt: BillingCheckoutAttempt, *, provider: str, data: Mapping[str, Any]
    ) -> bool:
        """May this event alter that payment record, on the evidence it carries?

        Only two things are checked, and both are identity: the company must be the one
        that took the money, and a person named by the event must be the person the
        checkout belongs to. The plan is deliberately not compared — a subscription keeps
        the metadata it was created with, so after an upgrade the refund of a new plan's
        charge still names the old checkout, which is exactly right rather than a
        mismatch. A money *event* keeps its stricter reading in ``_hydrate_checkout_data``.
        """

        if attempt.provider not in {provider, "static"}:
            return False
        supplied_user_id = data.get("user_id")
        if supplied_user_id in (None, ""):
            return True
        return BillingService._uuid_if_readable(supplied_user_id) == attempt.user_id

    async def _resolve_refund_attempt(
        self, *, provider: str, data: Mapping[str, Any]
    ) -> BillingCheckoutAttempt | None:
        """Which settled payment a refund that names no checkout belongs to, or ``None``.

        Narrowed in the order the evidence goes: the subscription the event names, then
        the person and the plan it names, then the person alone when they hold one
        settled payment with this company. ``None`` when nothing fits or when two do.
        """

        user_id = self._uuid_if_readable(data.get("user_id"))
        if user_id is None:
            return None
        plan_id: UUID | None = None
        reference = data.get("provider_subscription_id") or data.get("subscription_id")
        if reference not in (None, ""):
            subscription = await self.session.scalar(
                select(Subscription)
                .where(
                    Subscription.provider == provider,
                    Subscription.provider_subscription_id == str(reference),
                )
                .order_by(Subscription.updated_at.desc())
                .limit(1)
            )
            if subscription is not None:
                user_id = subscription.user_id
                plan_id = subscription.plan_id
        if plan_id is None:
            plan_code = self._optional_str(data.get("plan_code"))
            if plan_code is not None:
                plan = await self.session.scalar(
                    select(Plan).where(Plan.code == plan_code)
                )
                plan_id = plan.id if plan is not None else None
        conditions = [
            BillingCheckoutAttempt.user_id == user_id,
            BillingCheckoutAttempt.provider == provider,
            BillingCheckoutAttempt.status.in_(
                {SETTLED_ATTEMPT_STATUS, REFUNDED_ATTEMPT_STATUS}
            ),
        ]
        if plan_id is not None:
            conditions.append(BillingCheckoutAttempt.plan_id == plan_id)
        candidates = list(
            (
                await self.session.scalars(
                    select(BillingCheckoutAttempt)
                    .where(*conditions)
                    .order_by(BillingCheckoutAttempt.completed_at.desc())
                    .limit(2)
                )
            ).all()
        )
        return candidates[0] if len(candidates) == 1 else None

    async def _hydrate_checkout_data(
        self,
        data: dict[str, Any],
        *,
        provider: str,
        event_id: str,
        event_type: str,
    ) -> None:
        if event_type in REFUND_EVENT_TYPES:
            # Money coming back is read by its own rules, and they are looser on purpose.
            # Everything below this point checks a *payment* against what was owed before
            # access is granted: an unreadable checkout reference, the wrong company, the
            # wrong person, the wrong plan or the wrong amount all refuse the event, and
            # each of those refusals protects a plan somebody could otherwise get for
            # nothing. A refund grants nothing. Refusing one for a bad reference threw the
            # record of money leaving away with the error, which is how a refunded payment
            # stayed a sale on our books: so a refund is attached where the payload allows
            # and alerted where it does not, and never refused here. The money-in checks
            # below are untouched.
            await self._attach_refund_to_attempt(
                provider=provider, event_id=event_id, event_type=event_type, data=data
            )
            return
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
            and attempt.status in {SETTLED_ATTEMPT_STATUS, REFUNDED_ATTEMPT_STATUS}
        ):
            # ``refunded`` is in this check because the money behind a one-time crypto
            # invoice came back after it was paid. Without it, a re-delivered
            # ``payment.finished`` — NOWPayments sends the invoice status again — would
            # pass as never-settled, because the refund has just moved the row off
            # ``completed``, and would hand out a second 30-day period for money this
            # product no longer holds.
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
        # Which plan this payment is really for.
        #
        # A subscription keeps the metadata it was created with for ever, so after an
        # upgrade or a downgrade the checkout attempt still names the old plan while the
        # product being charged is the new one. That is not a mismatch — it is exactly the
        # change the customer asked for — and the amount to expect is the **new** plan's
        # price, not the amount frozen on the first checkout.
        #
        # Without this a customer who upgraded would have their first renewal refused as
        # overpaid: they really pay the higher price, and nothing confirms it.
        charged_plan_code = plan_code_for_creem_product(
            self.settings, self._optional_str(data.get("provider_product_id"))
        )
        plan_changed = charged_plan_code is not None and charged_plan_code != plan.code
        supplied_plan = str(data.get("plan_code") or "")
        if supplied_plan and supplied_plan != plan.code and not plan_changed:
            raise BillingError(
                "checkout_plan_mismatch", "The paid plan does not match the checkout attempt."
            )
        renewal_price: Decimal | None = None
        if plan_changed and charged_plan_code is not None:
            expected_amount = effective_monthly_price(charged_plan_code)
        else:
            expected_amount = (
                plan.price_monthly
                if attempt.billing_cycle == "trial_7_day" and event_type == "subscription.paid"
                else attempt.amount
            )
            if attempt.status == "completed":
                # A renewal: the first payment for this checkout already landed, so
                # `attempt.amount` is the LAST confirmed figure, not a promise about
                # this one. If the first period was discounted — a code typed on the
                # payment company's own page — the stored figure is the discounted
                # one, and a later full-price charge for the same plan is exactly
                # right, not an overpayment. Accept either the stored figure or the
                # plan's current effective price; anything above both is still
                # refused below.
                renewal_price = effective_monthly_price(plan.code)
        # A discount Creem itself applied on its own checkout page. It is only ever read
        # from the provider's own report of this order, never from anything a buyer can
        # type, and it moves the accepted amount by exactly the figure Creem states.
        provider_discount = self._provider_discount(data) if provider == "creem" else Decimal("0")
        if event_type in {
            "checkout.session.completed",
            "invoice.payment_succeeded",
            "payment.finished",
            "subscription.paid",
        }:
            expected_amount = self._validate_paid_amount_and_currency(
                paid_amount=data.get("amount"),
                paid_currency=data.get("currency"),
                expected_amount=expected_amount,
                expected_currency=attempt.currency,
                provider_discount=provider_discount,
                also_expected=renewal_price,
            )
            # This row is the record of what really moved. A discount entered on the
            # payment company's page is known only now, so replace the earlier quote
            # with the confirmed discounted figure before any unused-time calculation.
            attempt.amount = expected_amount
        if provider == "nowpayments" and event_type == "payment.finished":
            self._validate_nowpayments_settlement(data)
        if provider_discount > 0 and not attempt.discount_code:
            # Keep the reason beside the smaller amount. Without this the attempt says
            # "$20" while the payment says "$15" and nothing on the record explains it.
            attempt.discount_code = (
                str(data.get("provider_discount_code") or "").strip().upper()[:40] or None
            )
        data["checkout_attempt_id"] = str(attempt.id)
        data["user_id"] = str(attempt.user_id)
        data["plan_code"] = charged_plan_code if plan_changed else plan.code
        data["amount"] = str(expected_amount)
        data["currency"] = attempt.currency

    @staticmethod
    def _provider_discount(data: Mapping[str, Any]) -> Decimal:
        """What the payment company says its own page took off this order.

        Never negative, and a missing or unreadable figure is zero. A diagnostic must not
        become the failure: a payment company that reports its discount badly should not
        stop a payment that is otherwise exactly right.
        """

        try:
            amount = Decimal(str(data.get("provider_discount_amount")))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")
        return amount if amount > 0 else Decimal("0")

    def _validate_paid_amount_and_currency(
        self,
        *,
        paid_amount: object,
        paid_currency: object,
        expected_amount: Decimal,
        expected_currency: str,
        provider_discount: Decimal = Decimal("0"),
        also_expected: Decimal | None = None,
    ) -> Decimal:
        """Check what was paid against what was owed, and answer which figure was met.

        ``provider_discount`` widens the answer by **one** extra figure, and only the one
        the payment company itself reported taking off this order. That is not the same as
        accepting any smaller payment: without a discount line from the provider there is
        still exactly one acceptable amount, which is how this has always worked.

        It exists because the card route ends on Creem's own checkout page, where a buyer
        can type a Creem discount code. The payment then arrives smaller than the amount
        recorded when the checkout was created, and every one of those buyers used to pay
        and receive nothing.

        ``also_expected`` widens the answer by one further figure, and only on a renewal
        the caller has already confirmed: the plan's current effective price beside the
        amount stored on the attempt. A first period bought with a code leaves the stored
        figure discounted, and the next month's full-price charge is right, not an
        overpayment. Nothing larger than both candidates is ever let through.
        """

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
        bases = [expected_amount]
        if also_expected is not None and also_expected != expected_amount:
            bases.append(also_expected)
        candidates = list(bases)
        if provider_discount > 0:
            candidates.extend(
                max(base - provider_discount, Decimal("0.00")) for base in bases
            )
        percent = Decimal(str(self.settings.billing_payment_amount_tolerance_percent))
        too_small = True
        for candidate in candidates:
            tolerance = candidate * percent / Decimal("100")
            if actual < candidate - tolerance:
                continue
            too_small = False
            if actual <= candidate + tolerance:
                return actual.quantize(Decimal("0.01"), ROUND_HALF_UP)
        if too_small:
            raise BillingError(
                "payment_underpaid", "The verified payment is below the accepted amount."
            )
        if not self.settings.billing_allow_overpayment:
            raise BillingError(
                "payment_overpaid",
                "The verified payment exceeds the accepted amount and requires manual review.",
            )
        return max(candidates)

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
        """Write what one payment event says about the checkout attempt behind it.

        Three readings, in this order: the money came back, the money arrived, the money
        did not arrive. The first and the third are easy to confuse and they are opposites
        — which is exactly how this function came to treat ``payment.refunded`` like a
        failed payment and a settled one stayed a sale.
        """

        attempt_id = self._parse_uuid(data.get("checkout_attempt_id"))
        if attempt_id is None:
            # A refund with nothing here was already reported by
            # ``_attach_refund_to_attempt``, which is where the searching happens. Every
            # event reaches this function through that one, so alerting again would count
            # one refund twice.
            return
        attempt = await self.session.get(BillingCheckoutAttempt, attempt_id)
        if attempt is None:
            return
        attempt.provider_event_id = provider_event_id
        normalized_status = self._status_from_provider(str(data.get("status") or "pending"))
        current_status = attempt.status
        if event_type in REFUND_EVENT_TYPES:
            # The money came back. This is the one reading of a settled payment that is
            # not a downgrade-by-accident, and the R2-6 guard below must not swallow it:
            # leaving the row ``completed`` is how a refunded payment keeps counting as a
            # sale to the money-owed valuation, the affiliate commission and the
            # operations count. Idempotent — a second refund event changes nothing.
            #
            # Whether the payment is still money held is decided by the amount, once, in
            # ``_record_refund_amount``: a full or unknown refund ends it (the unchanged
            # status move), a partial leaves it ``completed`` — the money kept still
            # counts, and the readers value it through ``money_kept``. Either way the
            # payout settlement runs: full voids what this payment owed back, partial
            # re-values it. Refund and settlement are one fact, never one-without-the-
            # other, and both live inside this transaction.
            refund_is_partial = data.get("refund_is_partial") is True
            if not refund_is_partial and current_status != REFUNDED_ATTEMPT_STATUS:
                attempt.status = REFUNDED_ATTEMPT_STATUS
                # Writing this word can also retire a promise made beside it. A plan
                # move paid for earlier may have left a manual-payout record valued
                # from this very payment; with the money now back from the company, a
                # person must not also send it. Voiding lives in the one place that
                # writes ``refunded``, and runs inside this transaction: the refund and
                # the void of what it refunded are one fact, never one-without-the-other.
            await self._void_manual_payout_for_refund(
                attempt, refund_is_partial=refund_is_partial
            )
        elif event_type in {
            "checkout.session.completed",
            "invoice.payment_succeeded",
            "payment.finished",
            "subscription.paid",
            "subscription.trialing",
        } and normalized_status in {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING}:
            attempt.status = SETTLED_ATTEMPT_STATUS
            attempt.completed_at = datetime.now(UTC)
            attempt.last_error = None
        elif event_type in {
            "invoice.payment_failed",
            "payment.failed",
            "payment.expired",
            "payment.partially_paid",
        }:
            # A settled attempt must never be downgraded to failed by a later event,
            # such as a cancellation webhook that still carries its metadata.
            if current_status in {"creating", "pending", "processing"}:
                attempt.status = event_type.split(".", 1)[1]
                attempt.last_error = event_type
        elif current_status in {"creating", "pending"}:
            attempt.status = "processing"
        await self.session.flush()

    async def _void_manual_payout_for_refund(
        self, attempt: BillingCheckoutAttempt, *, refund_is_partial: bool
    ) -> None:
        """Settle the promise to send money back against the money that came back.

        A paid plan move writes a :class:`PlanMoveMoneyOwed` row — "a person sends
        this sum by hand" — and opens the ``billing:money-owed:{id}`` ticket that is
        how staff hear about it. When the payment the row was valued from is
        refunded after the move, the company has already returned that money: a row
        still pending would have a person send it a second time, and the customer
        would hold two refunds for one period. So a full refund voids the row and
        resolves its ticket, in the same transaction that records the refund.

        A **partial** refund is the same fact at a smaller figure, and the row is
        re-valued in place instead: the payout is sized to the money still kept
        (:func:`ai_market_monitor.core.money.money_kept` through the one unused-time
        owner, :func:`money_owed_for_unused_time`), stays ``pending_manual``, and
        keeps its open ticket — the customer is still owed the smaller sum and a
        person still has to send it. If the smaller sum is nothing (the period was
        fully used), voiding is the honest answer.

        It decides nothing else. Nothing here moves money: no payment is created or
        reversed, the old plan stays ended (it really was replaced), and a row keeps
        its history — voiding is a word on the record, not a deletion of it. A
        duplicate refund reaches this with nothing pending and changes nothing
        further: the search takes only ``pending_manual`` rows, and
        :meth:`OperationalIssueService.transition` is never asked to resolve a
        ticket that already reads resolved. The two status words are the plan-move
        service's own constants, so the question "is this payout still open?" can
        never be answered from a second, drifting copy.
        """

        from ai_market_monitor.db.models.operations import OperationalIssue
        from ai_market_monitor.observability.issues import (
            OperationalIssueService,
            sanitize_dedupe_key,
        )
        from ai_market_monitor.services.plan_replacements import (
            MONEY_OWED_PENDING_STATUS,
            MONEY_OWED_VOID_STATUS,
            money_owed_for_unused_time,
        )

        owed_rows = list(
            (
                await self.session.scalars(
                    select(PlanMoveMoneyOwed).where(
                        PlanMoveMoneyOwed.source_checkout_attempt_id == attempt.id,
                        PlanMoveMoneyOwed.status == MONEY_OWED_PENDING_STATUS,
                    )
                )
            ).all()
        )
        if not owed_rows:
            return
        kept = money_kept(attempt.amount, attempt.refunded_amount)
        voided_rows: list[PlanMoveMoneyOwed] = []
        for owed in owed_rows:
            if not refund_is_partial or kept <= 0:
                owed.status = MONEY_OWED_VOID_STATUS
                voided_rows.append(owed)
                continue
            new_amount = money_owed_for_unused_time(
                paid_amount=kept,
                period_start=owed.period_start,
                period_end=owed.original_period_end,
                ended_at=owed.ended_at,
            )
            if new_amount <= 0:
                # Nothing of the shortened period is unused any more: there is no
                # smaller promise to keep, only one to retire. (``amount_owed`` is
                # constrained positive on the row — zero is not a value it may hold.)
                owed.status = MONEY_OWED_VOID_STATUS
                voided_rows.append(owed)
                continue
            # Re-value in place. ``paid_amount`` moves with it so the row's own
            # check — the payout cannot exceed what the payment held — still holds.
            owed.paid_amount = kept
            owed.amount_owed = new_amount
        await self.session.flush()
        if not voided_rows:
            # A pure re-valuation: the payout is still to be sent, so its ticket
            # stays open and is not re-reported.
            return
        service = OperationalIssueService(self.session)
        for owed in voided_rows:
            issue = await self.session.scalar(
                select(OperationalIssue).where(
                    OperationalIssue.dedupe_key
                    == sanitize_dedupe_key(f"billing:money-owed:{owed.id}")
                )
            )
            if issue is None or issue.state == "resolved":
                continue
            await service.transition(
                issue_id=issue.id,
                to_state="resolved",
                actor="system",
                reason=(
                    "The payment this payout was valued from has been refunded; the "
                    "money came back from the payment company instead."
                ),
            )

    @staticmethod
    def _is_completed_payment_event(
        *,
        provider: str,
        event_type: str,
        subscription: Subscription,
    ) -> bool:
        """Did money actually change hands on this event?

        One answer, because two things now depend on it: the receipt email, and the
        affiliate commission. It used to be named after the email alone, and a second
        reading of "is this a payment" written for the commission is exactly how one of
        them would come to fire on an event the other ignored.
        """

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
        event_type: str = "",
    ) -> Subscription:
        user_id = self._parse_uuid(data.get("user_id"))
        if user_id is None:
            raise BillingError("user_missing", "Billing event did not include a user id.")
        # Which plan this subscription is really for. The payment company's own product is
        # asked first, because it is the thing being charged; the metadata is a copy taken
        # when the checkout was created and a later plan change does not rewrite it.
        plan_code = plan_code_for_creem_product(
            self.settings, self._optional_str(data.get("provider_product_id"))
        ) or str(data.get("plan_code") or data.get("price_lookup_key") or "demo")
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
        elif (
            subscription.status
            in {
                SubscriptionStatus.CANCELED,
                SubscriptionStatus.EXPIRED,
            }
            and status
            in {
                SubscriptionStatus.ACTIVE,
                SubscriptionStatus.TRIALING,
            }
        ):
            # A legitimate re-subscribe creates a new provider subscription id, so an
            # event naming an already-ended id and a live status is never a genuine
            # start. This must be refused for EVERY event that can arrive here, not
            # only the ones that move money: an in-flight `subscription.updated`
            # (or `.update`, `customer.subscription.updated`, `scheduled_cancel`,
            # `trialing`) landing after our cancel used to set the ended row back to
            # ACTIVE, because the old guard checked the paid event types only — two
            # live subscriptions charging, the exact outcome alerts exist for. Keep
            # the row ended and raise a critical alert; the event itself is recorded
            # as failed so a person can reconcile any money behind it.
            raise BillingError(
                "subscription_resurrected_after_cancel",
                "An event tried to make a subscription that our system already shows "
                "as ended live again. The subscription stays ended on our side.",
            )
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

    @classmethod
    def _uuid_if_readable(cls, value: Any) -> UUID | None:
        """A provider id, read without raising: ``None`` means "nothing readable here".

        ``_parse_uuid`` raising is right for a payment — a checkout that cannot be found
        must be refused, because accepting it would hand out a plan for money nobody paid.
        It is wrong wherever the event only has to be *written down*: on a refund the same
        raise throws away the record of money leaving and answers the company with a
        failure it will keep retrying, and on the stored event it loses the row altogether.
        Those callers read through here and report the gap instead.
        """

        try:
            return cls._parse_uuid(value)
        except (TypeError, ValueError):
            return None

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


def _amount_or_none(value: Any) -> Decimal | None:
    """A money figure a provider sent, or nothing rather than a guess."""

    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


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
