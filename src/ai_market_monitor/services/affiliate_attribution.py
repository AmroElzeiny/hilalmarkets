"""Which affiliate a customer belongs to, and what each of their payments earns.

Everything about *whose customer this is* lives here, in one module, because the rules
read as five separate sentences and are really one decision. Written in five places they
would have disagreed within a month — which is the failure this codebase keeps finding.

**The rules, stated once.**

1. Arriving on an affiliate's link remembers that link. Leaving, coming back days later
   and signing up then still credits it: the memory is a cookie on the browser, not
   something that dies with the visit.
2. Arriving on a *second* affiliate's link replaces the first. The most recent link is
   the one that counts.
3. Signing up assigns the person to that affiliate, permanently. Cancelling a
   subscription and starting another one later changes nothing.
4. A link beats a typed code. The code only decides when there was no link at all — that
   is :func:`winning_referral`, and it is the only place the comparison is made.
5. Only the account being deleted or banned ends an assignment. Nothing else does.

**And what an assignment is worth.** The first payment a customer ever makes earns the
first-payment rate; every payment after it earns the subsequent rate. The customer never
has to use the link or the code again — the assignment is what pays, not the code.

Two things here are deliberately not what an older version of this code did.

*The money is a ledger, not a number.* Every earning is a row with the moment, the
customer's name, what they paid and the share applied. Totals are sums of those rows. The
balance used to be recomputed from one field on the relationship, which meant an
affiliate could be shown a total with nothing behind it and no way to check it.

*The share is frozen onto the row.* An affiliate's rate can be changed by an
administrator; if the rate were read at display time, changing it would silently rewrite
what past payments were worth, including money already paid out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.money import money_kept, quantise_half_up
from ai_market_monitor.core.plans import is_discount_code_shaped
from ai_market_monitor.db.models import (
    AffiliateApplication,
    AffiliateCodeUse,
    AffiliateCommission,
    BillingCheckoutAttempt,
    Plan,
    ReferralCode,
    ReferralRelationship,
    Subscription,
    User,
)
from ai_market_monitor.db.models.enums import SubscriptionStatus, UserStatus

# ── The vocabulary ──────────────────────────────────────────────────────────────

#: The one query key an affiliate link carries: ``/signup?ref=THEIRCODE``.
#:
#: Written here and nowhere else. The System Brain's approval email, the affiliate's own
#: page and the middleware that catches the visit all use this constant, so a second
#: spelling — ``?r=``, ``?referral=`` — cannot appear on a link nothing reads.
REFERRAL_LINK_QUERY_KEY: Final[str] = "ref"

#: Where the remembered link lives between visits.
#:
#: A cookie and not the session, because the person is not signed in when they arrive and
#: usually does not sign up on the same visit. The name is prefixed like the product's
#: other cookies so it is recognisable in a browser's cookie list.
ATTRIBUTION_COOKIE_NAME: Final[str] = "hm_affiliate_ref"

#: How long a remembered link lasts. Ninety days is long enough to cover "saw the post,
#: thought about it, came back next month" and short enough that a link followed a year
#: ago does not quietly claim a customer who arrived for another reason.
ATTRIBUTION_COOKIE_DAYS: Final[int] = 90

#: How somebody came to belong to an affiliate.
SOURCE_LINK: Final[str] = "link"
SOURCE_CODE: Final[str] = "code"

#: Which payment this is for that customer.
KIND_FIRST: Final[str] = "first"
KIND_SUBSEQUENT: Final[str] = "subsequent"

#: Where a code was used, for the affiliate's own log.
CONTEXT_SIGNUP: Final[str] = "signup"
CONTEXT_CHECKOUT: Final[str] = "checkout"

#: The share everybody applies on, and what approval uses when nobody changes it.
#:
#: One constant and not two: the page shows this number as a fact, ``apply()`` stores it
#: because the applicant is never asked, and ``approve()`` falls back to it when the
#: administrator leaves the box alone.
DEFAULT_COMMISSION_PERCENT: Final[Decimal] = Decimal("25")

#: A status a customer must still be in for their payments to earn anybody anything.
#:
#: Banned and deleted accounts are the two things — and the only two — that end an
#: assignment. A suspended account keeps what it already earned; a receipt is a receipt.
EARNING_CUSTOMER_STATUSES: Final[frozenset[UserStatus]] = frozenset({UserStatus.ACTIVE})


class CommissionRates:
    """The pair of rates an approved affiliate earns on, resolved once.

    Nothing outside this class reads ``commission_percent`` or
    ``subsequent_commission_percent`` off an application. Two rates and several readers
    is precisely the shape that ends with a renewal paid at the first-payment rate, so
    there is one function that answers "what does this payment earn" and it takes the
    kind of payment as its argument.
    """

    __slots__ = ("first_percent", "subsequent_percent")

    def __init__(self, first_percent: Decimal, subsequent_percent: Decimal) -> None:
        self.first_percent = first_percent
        self.subsequent_percent = subsequent_percent

    def percent_for(self, sequence_kind: str) -> Decimal:
        """The share for this payment. Anything that is not the first is a renewal."""

        return (
            self.first_percent
            if sequence_kind == KIND_FIRST
            else self.subsequent_percent
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CommissionRates):
            return NotImplemented
        return (
            self.first_percent == other.first_percent
            and self.subsequent_percent == other.subsequent_percent
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"CommissionRates(first_percent={self.first_percent!r}, "
            f"subsequent_percent={self.subsequent_percent!r})"
        )


def rates_for(application: AffiliateApplication | None) -> CommissionRates:
    """Both rates for one affiliate, with every gap filled by something stated.

    A missing first-payment rate falls back to :data:`DEFAULT_COMMISSION_PERCENT`, which
    is the share the application was made under. A missing subsequent rate falls back to
    the first-payment rate — **never to zero**. Every application approved before the
    second rate existed has nothing in that column, and reading it as "earns nothing on
    renewals" would stop paying people who were promised otherwise.
    """

    first = getattr(application, "commission_percent", None) or DEFAULT_COMMISSION_PERCENT
    subsequent = getattr(application, "subsequent_commission_percent", None) or first
    return CommissionRates(first_percent=Decimal(first), subsequent_percent=Decimal(subsequent))


@dataclass(frozen=True, slots=True)
class WinningReferral:
    """Which code decided this, and which door it came through."""

    code: str
    source: str


def winning_referral(*, link_code: str | None, typed_code: str | None) -> WinningReferral | None:
    """The one code that decides who a new customer belongs to.

    **The link wins.** A person who arrived on an affiliate's link belongs to that
    affiliate even if they then typed somebody else's code into the discount box — the
    link is how they got here, and the code box is a price field that anybody can paste
    anything into. The typed code decides only when there was no link at all.

    ``None`` means neither was given, which is most sign-ups and is not a problem.
    """

    link = read_referral_code(link_code)
    if link is not None:
        return WinningReferral(code=link, source=SOURCE_LINK)
    typed = read_referral_code(typed_code)
    if typed is not None:
        return WinningReferral(code=typed, source=SOURCE_CODE)
    return None


def read_referral_code(raw: object) -> str | None:
    """One reading of a code that arrived from outside, or nothing.

    Whitespace out, upper case in — the same reading the discount box and the affiliate
    form both apply, so ``hilal25``, ``HILAL 25`` and ``HILAL25`` are one code everywhere.

    Anything that could never be a code is dropped rather than refused. This runs on a
    query string and on a cookie, where junk is ordinary and an error page would be a
    strange answer to somebody who simply followed a mangled link. Whether the code is
    *real* is a question only the database can answer, and it is asked separately.
    """

    cleaned = re.sub(r"\s+", "", str(raw or "")).upper()
    if not cleaned or len(cleaned) > 40:
        return None
    return cleaned if is_discount_code_shaped(cleaned) else None


def customer_display_name(user: User | None) -> str:
    """A customer's name for the affiliate to read, and never their email address.

    An affiliate is told who joined so they can recognise their own audience. Handing
    them somebody's address would be giving away another person's contact details, which
    is not ours to give — so an account with no name gets a plain placeholder rather than
    the part of the address before the ``@``, which is the address in all but punctuation.
    """

    name = str(getattr(user, "display_name", "") or "").strip()
    return name[:120] if name else "A Hilal Markets member"


# ── Reading and remembering the link ────────────────────────────────────────────


def link_code_in_query(params: object) -> str | None:
    """The affiliate code on an address somebody just opened, if there is one."""

    getter = getattr(params, "get", None)
    if getter is None:
        return None
    return read_referral_code(getter(REFERRAL_LINK_QUERY_KEY))


def link_code_in_cookies(cookies: object) -> str | None:
    """The link this browser was last sent here by, if it still remembers one."""

    getter = getattr(cookies, "get", None)
    if getter is None:
        return None
    return read_referral_code(getter(ATTRIBUTION_COOKIE_NAME))


def remember_link_code(response: object, code: str, *, secure: bool) -> None:
    """Write the link down for the next ninety days, replacing any earlier one.

    Replacing is the rule, not an accident of implementation: somebody who follows two
    affiliates' links belongs to the second one. ``httponly`` because no page needs to
    read this from script, and a value scripts cannot touch is one an extension or an
    injected snippet cannot rewrite either.
    """

    setter = getattr(response, "set_cookie", None)
    if setter is None:
        return
    setter(
        ATTRIBUTION_COOKIE_NAME,
        code,
        max_age=ATTRIBUTION_COOKIE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        # `lax` and not `strict`: the whole point is a visit that begins on somebody
        # else's website, and `strict` would drop the cookie on exactly that journey.
        samesite="lax",
        path="/",
    )


def forget_link_code(response: object) -> None:
    """Drop the remembered link once it has been used to assign somebody.

    Leaving it would let one person's link keep claiming later sign-ups from a shared
    computer, and the assignment it produced is permanent anyway — the cookie has done
    its whole job.
    """

    remover = getattr(response, "delete_cookie", None)
    if remover is None:
        return
    remover(ATTRIBUTION_COOKIE_NAME, path="/")


def capture_referral_link(request: object, response: object, *, secure: bool) -> str | None:
    """Remember the affiliate link this visit arrived on, wherever it landed.

    Called once, for every request the product answers, so an affiliate's link works on
    the landing page, on an article, on the pricing section and on the sign-up form
    alike. Anything narrower would be a list of pages somebody has to remember to add to,
    and the page left off it would silently pay nobody.

    Only the query key is read, so an ordinary visit costs one dictionary lookup and
    writes nothing.
    """

    code = link_code_in_query(getattr(request, "query_params", None))
    if code is None:
        return None
    remember_link_code(response, code, secure=secure)
    return code


class ReferralAttributionService:
    """Assigns customers to affiliates, and writes down what each payment earned."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ── Who somebody belongs to ─────────────────────────────────────────────────

    async def assignment_for(self, user_id: UUID) -> ReferralRelationship | None:
        return await self.session.scalar(
            select(ReferralRelationship).where(
                ReferralRelationship.referred_user_id == user_id
            )
        )

    async def code_owned_by(self, code: str) -> ReferralCode | None:
        """The live code row, or nothing. A switched-off code assigns nobody."""

        found = await self.session.scalar(
            select(ReferralCode).where(ReferralCode.code == code)
        )
        if found is None or found.owner_user_id is None or not found.is_active:
            return None
        if found.expires_at is not None and found.expires_at <= datetime.now(UTC):
            return None
        if found.max_uses is not None and found.use_count >= found.max_uses:
            return None
        return found

    async def assign(
        self,
        *,
        user_id: UUID,
        link_code: str | None = None,
        typed_code: str | None = None,
    ) -> ReferralRelationship | None:
        """Attach a new customer to an affiliate, once and for good.

        Returns the assignment that now stands. **An assignment that already exists is
        returned untouched** — that is what "permanent" means here, and it is why a
        customer who cancels and comes back through a different link keeps the affiliate
        they started with.

        ``None`` means nobody was credited: no code, a code nobody owns, a code that has
        been switched off, or the code's own owner trying to use it on themselves.
        """

        existing = await self.assignment_for(user_id)
        if existing is not None:
            return existing

        winner = winning_referral(link_code=link_code, typed_code=typed_code)
        if winner is None:
            return None
        code_row = await self.code_owned_by(winner.code)
        if code_row is None or code_row.owner_user_id == user_id:
            # Nobody owns it, it is switched off, or somebody is trying to refer
            # themselves. All three credit nobody rather than crediting the wrong person.
            return None

        relationship = ReferralRelationship(
            referrer_user_id=code_row.owner_user_id,
            referred_user_id=user_id,
            referral_code_id=code_row.id,
            assignment_source=winner.source,
            status="trial_activated",
            reward_status="pending_paid_conversion",
            metadata_json={"code": winner.code, "assigned_via": winner.source},
        )
        code_row.use_count += 1
        self.session.add(relationship)
        await self.session.flush()
        return relationship

    async def release(self, *, user_id: UUID) -> bool:
        """End an assignment because the account was banned or removed.

        The two things the programme's rules say end an assignment. Everything the
        affiliate already earned stays: the commission rows keep their own copy of the
        name and the amount, and their link to this row is allowed to become nothing.
        """

        relationship = await self.assignment_for(user_id)
        if relationship is None:
            return False
        await self.session.delete(relationship)
        await self.session.flush()
        return True

    # ── The code-use log ────────────────────────────────────────────────────────

    async def record_code_use(
        self,
        *,
        code: str | None,
        user_id: UUID,
        context: str,
        event_key: str,
    ) -> AffiliateCodeUse | None:
        """Write down one use of an affiliate's code, for the affiliate's own log.

        Separate from the assignment on purpose. A code can be used by somebody who
        already belongs to another affiliate — the use still happened, and the affiliate
        whose code it was is entitled to see it — while the assignment does not move.
        Counting only the uses that assigned somebody would show an affiliate a smaller
        number than the one their audience actually typed.

        Repeats are refused by ``event_key`` rather than by a check-then-insert, so a
        retried request cannot log the same use twice.
        """

        reading = read_referral_code(code)
        if reading is None:
            return None
        code_row = await self.code_owned_by(reading)
        if code_row is None or code_row.owner_user_id == user_id:
            return None
        already = await self.session.scalar(
            select(AffiliateCodeUse).where(AffiliateCodeUse.event_key == event_key)
        )
        if already is not None:
            return already
        customer = await self.session.get(User, user_id)
        use = AffiliateCodeUse(
            affiliate_user_id=code_row.owner_user_id,
            referral_code_id=code_row.id,
            code=reading,
            user_id=user_id,
            customer_name=customer_display_name(customer),
            context=context if context in {CONTEXT_SIGNUP, CONTEXT_CHECKOUT} else CONTEXT_SIGNUP,
            used_at=datetime.now(UTC),
            event_key=event_key[:200],
        )
        self.session.add(use)
        await self.session.flush()
        return use

    # ── The money ───────────────────────────────────────────────────────────────

    async def record_payment(
        self,
        *,
        customer_user_id: UUID,
        event_key: str,
        paid_amount_usd: Decimal | None = None,
        plan_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> AffiliateCommission | None:
        """One payment, turned into one earning for whoever this customer belongs to.

        The single writer of commission. Everything it needs it works out itself, so no
        caller can reach a different answer:

        * **who** — the permanent assignment, never the code on this particular payment;
        * **how much was paid** — the completed checkout row, which is the only record of
          money that really moved. A plan's list price is used only when there is no
          payment at all behind the subscription, and the row says which of the two it
          was;
        * **first or later** — whether this affiliate has ever earned anything from this
          customer before;
        * **the share** — from the affiliate's approved application, and copied onto the
          row so changing it later cannot rewrite this.

        ``None`` means nobody earned anything, and every route to it is ordinary: the
        customer belongs to no affiliate, the affiliate's application is not approved,
        the customer's account is banned or gone, nothing was actually paid, or this
        exact payment was already recorded.

        **A conversion and an earning are two separate things here.** The relationship is
        marked converted whether or not there is money behind it — that is a fact about
        the customer — while the ledger row needs an approved application, because the
        rates come from it. An affiliate whose application was refused after somebody had
        already joined through their code must not be paid, and the person who joined
        must still read as having joined.
        """

        already = await self.session.scalar(
            select(AffiliateCommission).where(AffiliateCommission.event_key == event_key)
        )
        if already is not None:
            return already

        relationship = await self.assignment_for(customer_user_id)
        if relationship is None:
            return None
        customer = await self.session.get(User, customer_user_id)
        if customer is None or customer.status not in EARNING_CUSTOMER_STATUSES:
            # Banned or removed. The assignment is over; nothing new is earned on it.
            return None

        charged, source = await self._amount_charged(
            customer_user_id=customer_user_id,
            plan_id=plan_id,
            fallback=paid_amount_usd,
        )
        if charged is None or charged <= 0:
            return None

        kind = await self._sequence_kind(
            affiliate_user_id=relationship.referrer_user_id,
            customer_user_id=customer_user_id,
        )

        # Where the referral stands, which is not a money question. Written once, on the
        # payment that first turned this person into a paying customer, so a renewal
        # cannot rewrite the record of what they first paid.
        if relationship.reward_status not in {"eligible_after_first_paid_month", "granted"}:
            relationship.status = "paid_converted"
            relationship.reward_status = "eligible_after_first_paid_month"
            relationship.metadata_json = {
                **(relationship.metadata_json or {}),
                "paid_amount_usd": str(charged),
                "paid_amount_source": source,
            }

        application = await self.session.scalar(
            select(AffiliateApplication).where(
                AffiliateApplication.user_id == relationship.referrer_user_id,
                AffiliateApplication.status == "approved",
            )
        )
        if application is None:
            await self.session.flush()
            return None

        percent = rates_for(application).percent_for(kind)
        earned = quantise_half_up(charged * percent / Decimal("100"), Decimal("0.01"))
        commission = AffiliateCommission(
            affiliate_user_id=relationship.referrer_user_id,
            relationship_id=relationship.id,
            customer_user_id=customer_user_id,
            customer_name=customer_display_name(customer),
            sequence_kind=kind,
            paid_amount_usd=quantise_half_up(charged, Decimal("0.01")),
            commission_percent=percent,
            commission_usd=earned,
            earned_at=occurred_at or datetime.now(UTC),
            event_key=event_key[:200],
            metadata_json={
                "paid_amount_source": source,
                "assignment_source": relationship.assignment_source or "",
            },
        )
        self.session.add(commission)
        await self.session.flush()
        return commission

    async def _sequence_kind(
        self, *, affiliate_user_id: UUID, customer_user_id: UUID
    ) -> str:
        """First payment from this customer, or one of the later ones.

        Asked of the ledger rather than of the subscription, because "has this customer
        ever earned this affiliate anything" is the question the two rates are actually
        about. A customer who cancels and subscribes again a year later is on their
        second payment, not their first.
        """

        earlier = await self.session.scalar(
            select(AffiliateCommission.id)
            .where(
                AffiliateCommission.affiliate_user_id == affiliate_user_id,
                AffiliateCommission.customer_user_id == customer_user_id,
            )
            .limit(1)
        )
        return KIND_SUBSEQUENT if earlier is not None else KIND_FIRST

    async def _amount_charged(
        self,
        *,
        customer_user_id: UUID,
        plan_id: UUID | None,
        fallback: Decimal | None,
    ) -> tuple[Decimal | None, str]:
        """What this customer paid **for this payment**, and which record said so.

        Three sources, in this order, and the order is the whole point:

        1. **What the payment company said it charged this time.** Only the caller that
           holds the verified payment event passes this, and it is the only figure that
           describes *this* payment rather than an earlier one.
        2. **The completed checkout for this plan.** The row the first payment itself
           wrote. It is right for a first payment and it is the best available answer
           when there is no event figure.
        3. **The plan's catalogue price**, marked as such, so a payout can never be
           argued about later.

        The event figure has to come first, and that is a correction rather than a
        preference. A subscription renews without opening a new checkout, so a renewal
        would otherwise be valued at the *first* month's price — and while an affiliate's
        code takes a percentage off that first month, every renewal after it would have
        been commissioned at the discounted figure for as long as the customer stayed.

        The catalogue price stays last, and never above the other two. It is what once
        credited an affiliate a share of $20 for somebody who paid $7.
        """

        if fallback is not None and fallback > 0:
            return _as_decimal(fallback), "event"
        query = (
            select(BillingCheckoutAttempt)
            .where(
                BillingCheckoutAttempt.user_id == customer_user_id,
                BillingCheckoutAttempt.status == "completed",
            )
            .order_by(BillingCheckoutAttempt.completed_at.desc())
            .limit(1)
        )
        if plan_id is not None:
            query = query.where(BillingCheckoutAttempt.plan_id == plan_id)
        row = await self.session.scalar(query)
        if row is not None:
            # The money kept, not the money taken: a partial refund (row still
            # ``completed``) must not commission the affiliate on money the customer
            # already got back. ``money_kept`` is the one owner of that reading.
            return _as_decimal(money_kept(row.amount, row.refunded_amount)), "checkout"
        if plan_id is not None:
            plan = await self.session.get(Plan, plan_id)
            if plan is not None:
                return _as_decimal(plan.price_monthly), "plan_price"
        return None, "none"

    async def record_payment_for_active_subscription(
        self,
        *,
        customer_user_id: UUID,
        event_key: str,
    ) -> AffiliateCommission | None:
        """Record the earning for whatever this customer is actively subscribed to.

        The entry point for anything that knows a customer is now paying but does not
        hold the payment event itself.
        """

        active = (
            await self.session.execute(
                select(Subscription.plan_id).where(
                    Subscription.user_id == customer_user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
        ).first()
        if active is None:
            return None
        return await self.record_payment(
            customer_user_id=customer_user_id,
            event_key=event_key,
            plan_id=active[0],
        )


def _as_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
