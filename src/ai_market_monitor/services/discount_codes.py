"""What a discount code is worth. One question, one answer, one owner.

Three different things want to know whether ``HILAL25`` is real and what it takes off:
the Apply button beside the crypto payment choice, the checkout that writes the amount
onto a payment attempt, and the pricing cards that name the code. If each of them held
its own list of codes, a card could advertise a code checkout refuses, or checkout could
charge a discount a card never offered. So none of them holds a list. They all ask here.

**Where a code comes from.** Two places, in this order:

1. **Creem** — ``GET /v1/discounts?discount_code=…``. Creem is where discount codes are
   really administered, because the card route runs through Creem's own checkout page and
   Creem applies them itself. Reading Creem means one place to create a code.
2. **This deployment's own list** — ``BILLING_DISCOUNT_CODES``, plus the launch code that
   `core/plans.py` owns. Used when Creem is not configured, does not know the code, or
   cannot be reached.

**Fail closed, in the one direction that matters.** A code Creem has *refused* — expired,
switched off, used up, for a different product, or a fixed-amount code we cannot honour on
the crypto route — is refused here too. It never falls through to the local list, because
falling through would turn every Creem refusal into "ask the other list instead", which is
the opposite of a refusal. Only "Creem has never heard of it" and "Creem did not answer"
fall through, and those two are not refusals.

**A percentage, never a second price.** Everything here works in percent and hands the
arithmetic to :func:`ai_market_monitor.core.plans.price_after_percent`. A discount that
carried its own final price would be a second copy of the price, free to disagree with the
first one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import (
    DISCOUNT_CODE_PATTERN as _DISCOUNT_CODE_PATTERN,
)
from ai_market_monitor.core.plans import (
    LAUNCH_DISCOUNT_CODE,
    is_discount_code_shaped,
    launch_discount_percent,
    price_after_percent,
)
from ai_market_monitor.services.provider_reliability import ProviderCallError
from ai_market_monitor.services.provider_runtime import provider_request

__all__ = [
    "DISCOUNT_CODE_PATTERN",
    "DiscountCodeError",
    "DiscountCodeService",
    "DiscountOffer",
    "DiscountedPrice",
    "normalize_discount_code",
]

#: What a code may be made of. Letters, digits, dash and underscore, two to forty
#: characters. Shared with the browser so the Apply button refuses the same shapes the
#: server refuses, rather than sending an obvious non-code all the way to Creem.
#:
#: Re-exported rather than written again: `core/plans.py` owns it, because the settings
#: loader has to apply the same rule and cannot import this module without a cycle.
DISCOUNT_CODE_PATTERN: Final[str] = _DISCOUNT_CODE_PATTERN

#: Where a code was found. Kept on the offer so an audit record can say which list
#: granted a discount, months later, when the lists have both changed.
SOURCE_LAUNCH: Final[str] = "launch"
SOURCE_SETTINGS: Final[str] = "settings"
SOURCE_CREEM: Final[str] = "creem"


class DiscountCodeError(ValueError):
    """A code cannot be used, and why — in words a beginner can act on.

    ``code`` is the machine name of the reason. Nothing outside this module writes the
    sentence, so the Apply button, the checkout refusal and the audit trail all say the
    same thing about the same problem.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DiscountOffer:
    """One code that works, and how much it takes off."""

    #: The code as it is written down — always upper case, so ``Hilal25`` and ``HILAL25``
    #: are the same code and are shown back the same way.
    code: str
    #: How much comes off, as a percentage. Always above 0 and at most 100.
    percent: Decimal
    #: ``launch``, ``settings`` or ``creem``.
    source: str


@dataclass(frozen=True, slots=True)
class DiscountedPrice:
    """A price with a code applied: what it was, what it is, and what was saved."""

    code: str
    percent: Decimal
    source: str
    #: What the checkout would charge with no code at all.
    full: Decimal
    #: What the checkout will charge now.
    final: Decimal
    #: ``full`` minus ``final``. Kept rather than recomputed, so a page showing "you save
    #: $5" cannot arrive at a different number from the one that was charged.
    saving: Decimal
    currency: str


def normalize_discount_code(raw: str | None) -> str:
    """The one reading of what somebody typed into the code box.

    Trims, drops inner spaces (people paste ``HILAL 25``), and upper-cases. Refuses an
    empty box and anything that is not code-shaped, so an obviously wrong entry is
    answered instantly instead of after a trip to Creem.
    """

    cleaned = "".join(str(raw or "").split()).upper()
    if not cleaned:
        raise DiscountCodeError("discount_code_empty", "Write your code in the box first.")
    if not is_discount_code_shaped(cleaned):
        raise DiscountCodeError(
            "discount_code_shape",
            "That does not look like a code. A code is letters and numbers, like HILAL25.",
        )
    return cleaned


class DiscountCodeService:
    """Answers "is this code real, and what is it worth?" for one deployment."""

    def __init__(self, settings: Settings):
        self.settings = settings

    # ── The local list ───────────────────────────────────────────────────────

    def local_offer(
        self,
        code: str,
        *,
        plan_code: str,
        now: datetime | None = None,
    ) -> DiscountOffer | None:
        """This deployment's own answer for a code, or ``None`` if it does not know it.

        The launch code is checked first and is **not** overridable from the environment.
        `core/plans.py` owns what the launch offer is worth and when it stops; a second
        number for it in an env file is exactly the drift this module exists to prevent.
        """

        launch_percent = launch_discount_percent(plan_code, now=now)
        if launch_percent is not None and code == LAUNCH_DISCOUNT_CODE:
            return DiscountOffer(code=code, percent=launch_percent, source=SOURCE_LAUNCH)
        percent = self.settings.billing_discount_codes.get(code)
        if percent is None:
            return None
        return DiscountOffer(code=code, percent=percent, source=SOURCE_SETTINGS)

    # ── Creem ────────────────────────────────────────────────────────────────

    @property
    def creem_configured(self) -> bool:
        return self.settings.creem_api_key is not None

    async def creem_offer(
        self,
        code: str,
        *,
        creem_product_id: str | None,
        now: datetime | None = None,
    ) -> DiscountOffer | None:
        """Creem's answer for a code.

        ``None`` means "Creem has never heard of it" or "Creem did not answer" — both of
        which are questions the local list may still answer. Anything Creem *refuses*
        raises instead, so a refusal stays a refusal.
        """

        secret = self.settings.creem_api_key
        if secret is None:
            return None
        url = f"{str(self.settings.creem_api_base).rstrip('/')}/v1/discounts"
        try:
            response = await provider_request(
                self.settings,
                "GET",
                url,
                provider="creem",
                operation="v1_discounts_lookup",
                timeout=self.settings.creem_timeout_seconds,
                # A lookup changes nothing on Creem's side, so repeating it is free and a
                # single dropped packet must not cost somebody their discount.
                mutation_committed=False,
                headers={
                    "x-api-key": secret.get_secret_value(),
                    "User-Agent": "HilalMarkets/1.0",
                },
                params={"discount_code": code},
            )
        except (httpx.HTTPError, ProviderCallError):
            # Creem is having a bad minute. Not knowing is not the same as refusing.
            return None
        if response.status_code == 404:
            return None
        if response.is_error:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        return self._offer_from_creem(
            body,
            code=code,
            creem_product_id=creem_product_id,
            now=now or datetime.now(UTC),
        )

    def _offer_from_creem(
        self,
        body: dict[str, Any],
        *,
        code: str,
        creem_product_id: str | None,
        now: datetime,
    ) -> DiscountOffer:
        """Read Creem's discount record, refusing everything we cannot honour exactly."""

        status = str(body.get("status") or "").strip().lower()
        if status in {"expired", "deleted"}:
            raise DiscountCodeError(
                "discount_code_expired",
                "That code has finished. It cannot be used any more.",
            )
        if status and status != "active":
            raise DiscountCodeError(
                "discount_code_unknown",
                "That code is not working. Check the spelling, or ask us for a new one.",
            )
        kind = str(body.get("type") or "").strip().lower()
        if kind != "percentage":
            # A fixed-amount code is a real code we simply cannot honour here: it is
            # written in one currency for one product, and this route may be paying in
            # another. Refusing keeps the misunderstanding visible; guessing a percentage
            # from it would invent a discount nobody wrote.
            raise DiscountCodeError(
                "discount_code_not_percentage",
                "That code cannot be used on this page. Please ask us for a percentage code.",
            )
        percent = _decimal_or_none(body.get("percentage"))
        if percent is None:
            percent = _decimal_or_none(body.get("amount"))
        if percent is None or percent <= 0 or percent > 100:
            raise DiscountCodeError(
                "discount_code_unknown",
                "That code is not working. Check the spelling, or ask us for a new one.",
            )
        expiry = _datetime_or_none(body.get("expiry_date"))
        if expiry is not None and expiry <= now:
            raise DiscountCodeError(
                "discount_code_expired",
                "That code has finished. It cannot be used any more.",
            )
        maximum = _decimal_or_none(body.get("max_redemptions"))
        used = _decimal_or_none(body.get("redeem_count")) or Decimal("0")
        if maximum is not None and maximum > 0 and used >= maximum:
            raise DiscountCodeError(
                "discount_code_used_up",
                "That code has already been used as many times as it allows.",
            )
        products = body.get("applies_to_products")
        if isinstance(products, list) and products:
            allowed = {str(_product_id(item)) for item in products}
            allowed.discard("")
            if allowed and (creem_product_id or "") not in allowed:
                raise DiscountCodeError(
                    "discount_code_wrong_plan",
                    "That code is for a different plan.",
                )
        return DiscountOffer(code=code, percent=percent, source=SOURCE_CREEM)

    # ── The whole answer ─────────────────────────────────────────────────────

    async def offer_for(
        self,
        typed: str | None,
        *,
        plan_code: str,
        creem_product_id: str | None = None,
        now: datetime | None = None,
    ) -> DiscountOffer:
        """The one offer a typed code produces, or a refusal saying why not."""

        code = normalize_discount_code(typed)
        # Creem refuses by raising, so a refusal leaves this block rather than falling
        # through to the local list. `None` covers the two cases that are not refusals —
        # Creem has never heard of the code, or Creem did not answer — and both of those
        # are questions the local list is still allowed to answer.
        if self.creem_configured:
            found = await self.creem_offer(code, creem_product_id=creem_product_id, now=now)
            if found is not None:
                return found
        local = self.local_offer(code, plan_code=plan_code, now=now)
        if local is not None:
            return local
        raise DiscountCodeError(
            "discount_code_unknown",
            "That code is not working. Check the spelling, or ask us for a new one.",
        )

    async def price_for(
        self,
        typed: str | None,
        *,
        plan_code: str,
        full_amount: Decimal,
        currency: str,
        creem_product_id: str | None = None,
        now: datetime | None = None,
    ) -> DiscountedPrice:
        """What this checkout costs once the code is applied.

        The single place a discounted amount is produced. The Apply button quotes what
        this returns and the checkout charges what this returns, so the number on the
        screen and the number on the invoice are the same object.
        """

        offer = await self.offer_for(
            typed, plan_code=plan_code, creem_product_id=creem_product_id, now=now
        )
        final = price_after_percent(full_amount, offer.percent)
        if final <= 0:
            # A payment page cannot ask for nothing, and a free month is a plan change
            # rather than a discount. Refused rather than sent to a payment company that
            # would reject it with a message nobody could understand.
            raise DiscountCodeError(
                "discount_code_covers_everything",
                "That code takes off the whole price. Please write to us and we will "
                "open the plan for you.",
            )
        return DiscountedPrice(
            code=offer.code,
            percent=offer.percent,
            source=offer.source,
            full=full_amount,
            final=final,
            saving=full_amount - final,
            currency=currency,
        )


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _product_id(item: Any) -> str:
    """A product in Creem's scope list is either the id itself or an object holding it."""

    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("id") or "").strip()
    return ""
