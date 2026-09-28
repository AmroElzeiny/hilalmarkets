import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from ai_market_monitor.core.money import json_money_number, quantise_half_up
from ai_market_monitor.schemas.timeframes import TIMEFRAME_MINUTES

UNLIMITED_SYMBOL_CAP = 100_000
PUBLIC_PLAN_CODES = ("demo", "trader", "pro")
PURCHASABLE_PLAN_CODES = ("trader", "pro")

#: How long the strategy-approval allowance looks back, in days. **The same window on
#: every plan** — only the count differs.
#:
#: One owner because three things have to agree: the limit key below is named after it,
#: `EntitlementService.enforce_strategy_approval` counts inside it, and the pricing cards
#: and comparison table print it. Two of those used to write "30" out by hand, so a
#: changed window would have left the page promising one thing and the gate enforcing
#: another.
STRATEGY_APPROVAL_WINDOW_DAYS = 30

#: The limit key that carries that allowance. Written once so a typo in one plan cannot
#: silently mean "no limit" — a missing key is unlimited, which is exactly how the Plus
#: plan came to allow unlimited approvals while the table said otherwise.
STRATEGY_APPROVAL_LIMIT_KEY = "strategy_approvals_per_30_days"

FULL_ACCESS_LIMITS: dict[str, int] = {
    "saved_strategies": UNLIMITED_SYMBOL_CAP,
    STRATEGY_APPROVAL_LIMIT_KEY: UNLIMITED_SYMBOL_CAP,
    "active_strategies": UNLIMITED_SYMBOL_CAP,
    "symbols_per_strategy": UNLIMITED_SYMBOL_CAP,
    "minimum_timeframe_minutes": 1,
    "alerts_per_day": UNLIMITED_SYMBOL_CAP,
    "alerts_per_week": UNLIMITED_SYMBOL_CAP,
    "alerts_per_trial_cycle": UNLIMITED_SYMBOL_CAP,
    "forensic_investigations_per_month": UNLIMITED_SYMBOL_CAP,
    "historical_previews_per_month": UNLIMITED_SYMBOL_CAP,
    "historical_previews_per_trial_cycle": UNLIMITED_SYMBOL_CAP,
    "on_demand_scans_per_month": UNLIMITED_SYMBOL_CAP,
    "user_initiated_scans_per_week": UNLIMITED_SYMBOL_CAP,
    "on_demand_scans_total": UNLIMITED_SYMBOL_CAP,
    "light_prompt_scans_per_day": UNLIMITED_SYMBOL_CAP,
    "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
    "detailed_history_days": UNLIMITED_SYMBOL_CAP,
}

#: What each plan limit is called wherever a plan's allowance is listed in words: the
#: subscription page, the checkout review and the payment receipt.
#:
#: A monitor is the thing that runs; a Watchlist is a saved list of coins, a different
#: object. The checkout review and the receipt each kept their own labels, and both
#: called a monitor a Watchlist ("Active Watchlists", "Markets per Watchlist") while the
#: subscription page did not. The comparison table's row names are table headings and
#: stay in `_comparison_rows`.
PLAN_LIMIT_WORDS: dict[str, str] = {
    "active_strategies": "Monitors running at once",
    "symbols_per_strategy": "Coins in one monitor",
    "on_demand_scans_per_month": "Market checks a month",
    "user_initiated_scans_per_week": "Market checks a week",
    "detailed_history_days": "Days of history kept",
}

FULL_ACCESS_WITHOUT_WHATSAPP: dict[str, bool] = {
    "telegram": True,
    "whatsapp": False,
    "light_prompt_scan": True,
    "near_miss": True,
    "full_near_miss_history": True,
    "condition_proof": True,
    "why_no_alert_limited": False,
    "missed_alert_investigations": True,
    "advanced_forensics": True,
    "forward_testing": True,
    "basic_analytics": True,
    "advanced_liquidity_filters": True,
    "setup_lifecycle": True,
    "correlation_compression": True,
    "advanced_analytics": True,
    "shared_templates": True,
    "community_delivery": True,
    "custom_webhooks": True,
    "api_access": True,
    "exports": True,
    "consultation_quota": True,
    "team_members": True,
    "shared_strategies": True,
    "role_integration": True,
    "branded_bot": True,
    "admin_controls": True,
    "white_label": True,
    "advanced_custom_indicators": True,
    "advanced_backtesting": True,
}


#: Which plan a user is on while billing is switched off. Free, and the only one.
PRIVATE_BETA_PLAN_CODE = "demo"


def visible_public_plan_codes(*, billing_enabled: bool) -> tuple[str, ...]:
    """Which plans a visitor may be shown. Always all of them.

    Prices stay on the page whether or not checkout is switched on, because the page has
    a second job besides selling: telling a visitor what the product will cost. Hiding
    them left the pricing page with one free plan and nothing to compare it against.

    What checkout being off *does* change is the button, not the price. Availability is
    handled per plan and per interval by :func:`plan_offer`, so a plan that cannot be
    bought says so on its own card instead of disappearing.
    """
    del billing_enabled
    return PUBLIC_PLAN_CODES


# --------------------------------------------------------------------------------
# The current offer: which plans can be bought, and at what price, right now.
#
# One definition, read by the landing page, the public pricing page and the dashboard.
# Three surfaces showing prices is three chances to disagree, and a visitor who sees one
# price on the landing page and another in the dashboard has no way to know which is real.
# --------------------------------------------------------------------------------

#: Discount codes this product used to run and must **not** honour any more.
#:
#: A retired code is not the same as a deleted one. Creem holds its own copy of every
#: code, and a code left active there is a code a card buyer can still type on Creem's own
#: checkout page — taking a further percentage off a price that is already the launch
#: price. Nothing offline can see Creem, so `scripts/check_creem_prices.py` holds this
#: list against Creem's discounts and reports any that is still alive.
#:
#: ``HILAL25`` was the launch **code**. The launch price is not reached by typing anything
#: any more — the lower price now comes from :data:`CURRENT_OFFER` by itself — so the code must
#: stop working on both sides, not only on ours.
RETIRED_DISCOUNT_CODES: tuple[str, ...] = ("HILAL25",)

#: What any discount code may be made of: letters, digits, dash and underscore, two to
#: forty characters.
#:
#: It lives here, beside the launch code, because three layers have to agree on it and
#: two of them cannot import the third. The settings loader checks a code before it is
#: written down, `services/discount_codes.py` checks one somebody typed, and the browser
#: refuses the same shapes so an obvious non-code is answered without a trip to Creem.
#:
#: Before this was shared, the settings loader had no shape rule at all. A code of one
#: letter loaded happily and then failed every time it was typed, because the reader
#: demanded at least two — a discount that existed in the configuration and could not be
#: used by anybody, which is exactly the "offered but not runnable" fault this codebase
#: keeps finding.
DISCOUNT_CODE_PATTERN = r"^[A-Z0-9][A-Z0-9_-]{1,39}$"

_DISCOUNT_CODE_RE = re.compile(DISCOUNT_CODE_PATTERN)


def is_discount_code_shaped(code: str) -> bool:
    """Could this ever be a discount code? One answer, for every layer that asks."""

    return bool(_DISCOUNT_CODE_RE.match(code))


@dataclass(frozen=True, slots=True)
class PriceOffer:
    """A percentage the product takes off its normal prices **by itself**.

    Nobody types anything to get it. Every page crosses the normal price out and shows the
    lower one, and every checkout charges the lower one, because all of them read
    :func:`effective_monthly_price`. The code is a *name* for the offer that the cards
    show; typing it adds nothing, and `services/discount_codes.py` says so rather than
    taking the percentage off a second time.

    ``ends_at`` is optional. ``None`` means the offer runs until this line is changed, and
    then no page shows a countdown — there is nothing to count down to. A date turns the
    countdown back on and ends the offer by itself at that instant.

    **The card company does not follow this by itself.** Creem charges whatever its own
    product is priced at, so changing this offer means re-pricing both Creem products the
    same day. `scripts/check_creem_prices.py` compares the two, and the daily
    ``check_card_prices`` task alerts the operator when they differ.
    """

    code: str
    percent: Decimal
    ends_at: datetime | None = None

    def __post_init__(self) -> None:
        # Refused at import rather than at checkout: an offer that cannot be represented
        # must stop the application, not quietly charge a different number.
        if not is_discount_code_shaped(self.code):
            raise ValueError(f"offer code {self.code!r} is not shaped like a discount code")
        if not Decimal("0") < self.percent < Decimal("100"):
            raise ValueError(f"offer percent {self.percent} must be above 0 and below 100")
        if self.ends_at is not None and self.ends_at.tzinfo is None:
            raise ValueError("offer end must carry a time zone")


#: The offer running now, or ``None`` when every plan costs its normal price.
#:
#: HILAL30 — 30% off the normal monthly price of every paid plan, with no end date and so
#: no countdown (owner's decision of 28 September 2026).
CURRENT_OFFER: PriceOffer | None = PriceOffer(code="HILAL30", percent=Decimal("30"))


def current_offer() -> PriceOffer | None:
    """The running offer. Every reader asks this rather than importing the value.

    An imported value is a copy taken when the importing module loaded; a function reads
    this module's value at the moment it is asked, so there is exactly one answer at any
    time — including in a test that installs a dated offer to check how one ends.
    """
    return CURRENT_OFFER

#: What a card says instead of a price when the plan cannot be bought yet.
COMING_SOON_LABEL = "Soon"

#: Money is rounded to the cent, and never in the customer's disfavour by accident.
#: The rounding itself is the owner's — ``core.money.quantise_half_up``, which is
#: ``ROUND_HALF_UP`` on a percentage of a whole-dollar price and exact for every
#: price this product sells; it is named there rather than left to decimal's
#: default so a future price with an odd cent cannot quietly round a charge the
#: other way. This constant is only the quantum.
_CENTS = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class PlanOffer:
    """Whether a plan can be bought, per interval, and whether the current offer applies."""

    monthly_available: bool
    annual_available: bool
    #: Whether :data:`CURRENT_OFFER` comes off this plan's monthly price.
    #:
    #: A yes/no rather than a price. The offer used to hold a hand-written launch price per
    #: plan beside a separate end date; now the one percentage in :data:`CURRENT_OFFER`
    #: produces every discounted figure, so no plan can be discounted by a different amount
    #: from the one its card names.
    takes_offer: bool = False


PLAN_OFFERS: dict[str, PlanOffer] = {
    # Free, so there is nothing to buy and nothing to discount.
    "demo": PlanOffer(monthly_available=True, annual_available=False),
    # Annual billing is not open yet on either paid plan.
    "trader": PlanOffer(monthly_available=True, annual_available=False, takes_offer=True),
    "pro": PlanOffer(monthly_available=True, annual_available=False, takes_offer=True),
}

_DEFAULT_OFFER = PlanOffer(monthly_available=False, annual_available=False)


def plan_offer(code: str) -> PlanOffer:
    """The offer for one plan. An unknown plan is not for sale.

    Fails closed: a plan nobody described cannot be bought by accident.
    """
    return PLAN_OFFERS.get(code, _DEFAULT_OFFER)


def promotion_is_active(now: datetime | None = None) -> bool:
    """Is :data:`CURRENT_OFFER` running at this instant?

    An offer without an end date runs until it is removed from this file.
    """
    offer = current_offer()
    if offer is None:
        return False
    return offer.ends_at is None or (now or datetime.now(UTC)) < offer.ends_at


def promotion_ends_at(now: datetime | None = None) -> str | None:
    """When the running offer stops, as ISO text — or ``None`` when there is no countdown.

    ``None`` both when nothing is running and when the running offer has no end date. A
    page draws a countdown **only** for a string here. An empty or missing deadline used
    to be read by the countdown script as "already over", which hid the crossed-out price
    on an offer that was still being charged.
    """
    offer = current_offer()
    if offer is None or offer.ends_at is None or not promotion_is_active(now):
        return None
    return offer.ends_at.isoformat()


def running_offer_code(code: str, *, now: datetime | None = None) -> str | None:
    """The offer's name to show on this plan's card, or ``None`` when it does not apply."""
    offer = current_offer()
    if promotional_monthly_price(code, now=now) is None or offer is None:
        return None
    return offer.code


def running_offer_percent(code: str, *, now: datetime | None = None) -> Decimal | None:
    """The percentage the running offer takes off this plan, or ``None``."""
    offer = current_offer()
    if promotional_monthly_price(code, now=now) is None or offer is None:
        return None
    return offer.percent


def price_after_percent(amount: Decimal, percent: Decimal) -> Decimal:
    """``amount`` with ``percent`` taken off, to the cent.

    The one place this arithmetic happens. Every discount in the product — the launch
    code, a code read from Creem, a code read from the environment — comes through here,
    so no two of them can round differently and no page can quote a number a checkout
    would not charge.
    """
    if percent <= 0:
        return quantise_half_up(amount, _CENTS)
    if percent >= 100:
        return Decimal("0.00")
    kept = (Decimal("100") - percent) / Decimal("100")
    return quantise_half_up(amount * kept, _CENTS)


def promotional_monthly_price(code: str, *, now: datetime | None = None) -> Decimal | None:
    """This plan's price with the running offer taken off, or ``None`` when none applies.

    Reads the offer and the clock together, so the price on a card and any countdown
    beside it can never say different things. The figure is always
    :func:`price_after_percent` of the normal price — the same arithmetic a typed code
    goes through — so the offer cannot round differently from any other discount. A
    result that is not below the normal price is not an offer, and is refused rather than
    shown as one.
    """
    offer = current_offer()
    if offer is None or not plan_offer(code).takes_offer or not promotion_is_active(now):
        return None
    normal = PLAN_DEFINITIONS[code].monthly_price
    price = price_after_percent(normal, offer.percent)
    if price >= normal or price <= 0:
        return None
    return price


def effective_monthly_price(code: str, *, now: datetime | None = None) -> Decimal:
    """What a checkout charges for one month, with nothing typed anywhere.

    This is the price the payment company is asked for, so it is also the number
    `scripts/check_creem_prices.py` holds the Creem product against, the amount written
    onto a card checkout attempt, and the amount a crypto invoice is raised for.

    While :data:`CURRENT_OFFER` runs it **is** the offer price. Nobody types a code to
    reach it, so a function that returned the higher number would charge everybody more
    than every page shows.
    """
    promotional = promotional_monthly_price(code, now=now)
    if promotional is not None:
        return promotional
    return PLAN_DEFINITIONS[code].monthly_price


def original_monthly_price(code: str, *, now: datetime | None = None) -> Decimal | None:
    """The crossed-out price, or ``None`` when there is nothing to cross out."""
    if promotional_monthly_price(code, now=now) is None:
        return None
    return PLAN_DEFINITIONS[code].monthly_price


def annual_saving(code: str, *, now: datetime | None = None) -> Decimal:
    """What a year on this plan saves against paying month by month.

    Measured against the price a person actually pays month by month, which is the
    launch-code price while the launch offer is running. Comparing a year against the
    normal monthly price would advertise a saving nobody can get.
    """
    presentation = PUBLIC_PLAN_PRESENTATIONS.get(code)
    if presentation is None or presentation.annual_price <= 0:
        return Decimal("0.00")
    monthly = effective_monthly_price(code, now=now)
    saving = (monthly * 12) - presentation.annual_price
    return max(saving, Decimal("0.00"))


def maximum_annual_saving(*, now: datetime | None = None) -> Decimal:
    """The best annual saving a visitor can actually buy today.

    Derived, never typed out. The toggle used to promise "Save up to $44" in fixed text,
    which was the Pro figure; a changed monthly price left it advertising a saving that
    no plan gave.
    """
    savings = [
        annual_saving(code, now=now)
        for code in PUBLIC_PLAN_CODES
        if plan_offer(code).annual_available
    ]
    return max(savings, default=Decimal("0.00"))


def _percent_number(percent: Decimal | None) -> int | Decimal | None:
    """A percentage as a JSON number that reads the way a person writes it: ``30``.

    Whole numbers go out as ``int`` so a card can print "30% off" as it is; anything with
    a fraction stays an exact ``Decimal``. Never ``float``, for the reason
    :func:`plan_offer_payload` gives.
    """
    if percent is None:
        return None
    if percent == percent.to_integral_value():
        return int(percent)
    return percent


def plan_offer_payload(code: str, *, now: datetime | None = None) -> dict[str, object]:
    """The offer as plain data, for the landing page and the dashboard.

    A price for an interval that is not open yet is ``None``, not the number. Leaving the
    number in the payload would ship it in the page source for anyone to read, and the
    point of "Soon" is that there is no price to quote yet.

    ``monthlyPrice`` is both the headline on a card **and** what a checkout charges: the
    offer price while the offer runs, the normal price otherwise. ``originalMonthlyPrice``
    is the number to cross out beside it, or ``None`` when there is nothing to cross out.

    ``offerCode`` and ``offerPercent`` name the running offer for the card ("HILAL30,
    30% off"). They are a label, not an instruction: the lower price is already the
    charge, so a card never tells anybody to go and type the code.

    ``promotionEndsAt`` is ``None`` for an offer without an end date, and a page shows a
    countdown only for a real date — see :func:`promotion_ends_at`.

    The prices are ``Decimal`` (or ``None``), never ``float``: this payload is
    embedded in the landing page and the dashboard as JSON, and ``float`` is where
    ``9.00`` loses its cents text and ``10.05`` its value. The page writer is
    ``core.money.money_json_dumps`` (installed as Jinja's ``tojson`` in
    ``api/template_env.py``), which sends each amount as a bare JSON number with
    the exact digits — the contract the React card does arithmetic on.
    """
    offer = plan_offer(code)
    charged = effective_monthly_price(code, now=now)
    original = original_monthly_price(code, now=now)
    return {
        "monthlyAvailable": offer.monthly_available,
        "annualAvailable": offer.annual_available,
        "monthlyPrice": json_money_number(charged) if offer.monthly_available else None,
        "annualPrice": (
            json_money_number(PUBLIC_PLAN_PRESENTATIONS[code].annual_price)
            if offer.annual_available and code in PUBLIC_PLAN_PRESENTATIONS
            else None
        ),
        "originalMonthlyPrice": (
            json_money_number(original) if original is not None else None
        ),
        # The same number as `monthlyPrice`, kept because three templates and the landing
        # bundle read it. It stopped being a *second* price the day the code went away:
        # what a checkout charges and what a card shows are now one figure by construction.
        "fullMonthlyPrice": json_money_number(charged) if offer.monthly_available else None,
        "offerCode": running_offer_code(code, now=now),
        "offerPercent": _percent_number(running_offer_percent(code, now=now)),
        "promotionEndsAt": promotion_ends_at(now) if original is not None else None,
        "promotionRunning": original is not None,
        "comingSoonLabel": COMING_SOON_LABEL,
    }


@dataclass(frozen=True, slots=True)
class PlanDefinition:
    code: str
    name: str
    monthly_price: Decimal
    currency: str = "USD"
    description: str | None = None
    limits: dict[str, int | float | str | None] = field(default_factory=dict)
    features: dict[str, bool | int | float | str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PublicPlanPresentation:
    # No `description`. The audience sentence said nothing the bullets below do not say,
    # so it was removed rather than kept as a fourth place a plan could be described
    # differently.
    annual_price: Decimal
    cta_label: str
    visible_features: tuple[str, ...]
    additional_features: tuple[str, ...] = ()
    badge: str | None = None
    trial_note: str | None = None
    highlighted_feature: str | None = None


PLAN_DEFINITIONS: dict[str, PlanDefinition] = {
    "demo": PlanDefinition(
        code="demo",
        name="Explore",
        monthly_price=Decimal("0.00"),
        description=(
            "Explore screened assets and use the AI assistant with measured monitoring "
            "limits."
        ),
        limits={
            "saved_strategies": 2,
            STRATEGY_APPROVAL_LIMIT_KEY: 2,
            "active_strategies": 1,
            "symbols_per_strategy": 200,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 2,
            "alerts_per_week": 2,
            "forensic_investigations_per_month": 0,
            "historical_previews_per_month": 0,
            "on_demand_scans_per_month": 0,
            "user_initiated_scans_per_week": 1,
            "light_prompt_scans_per_day": 0,
            "light_prompt_symbols": 200,
            "detailed_history_days": 0,
        },
        features={
            "telegram": True,
            "whatsapp": False,
            "light_prompt_scan": True,
            "near_miss": True,
            "condition_proof": False,
            "setup_lifecycle": False,
            "ai_assistant": True,
            "missed_alert_investigations": False,
            "advanced_forensics": False,
            "custom_webhooks": False,
            "community_delivery": False,
        },
    ),
    "trader": PlanDefinition(
        code="trader",
        name="Plus",
        monthly_price=Decimal("15.00"),
        description="Continuous guided monitoring for active individual investors.",
        limits={
            "saved_strategies": 5,
            STRATEGY_APPROVAL_LIMIT_KEY: 20,
            "active_strategies": 3,
            "symbols_per_strategy": 200,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 20,
            "forensic_investigations_per_month": UNLIMITED_SYMBOL_CAP,
            "on_demand_scans_per_month": 10,
            "light_prompt_scans_per_day": 10,
            "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
            "detailed_history_days": 90,
        },
        features={
            "telegram": True,
            "whatsapp": False,
            "light_prompt_scan": True,
            "near_miss": True,
            "condition_proof": True,
            "forward_testing": True,
            "basic_analytics": True,
            "ai_assistant": True,
            "missed_alert_investigations": True,
            "advanced_forensics": False,
        },
    ),
    "pro": PlanDefinition(
        code="pro",
        name="Pro",
        monthly_price=Decimal("25.00"),
        description="More simultaneous market monitors and unlimited alert capacity.",
        limits={
            "saved_strategies": 10,
            STRATEGY_APPROVAL_LIMIT_KEY: UNLIMITED_SYMBOL_CAP,
            "active_strategies": 10,
            "symbols_per_strategy": 500,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": UNLIMITED_SYMBOL_CAP,
            "forensic_investigations_per_month": UNLIMITED_SYMBOL_CAP,
            "on_demand_scans_per_month": 100,
            "light_prompt_scans_per_day": 50,
            "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
            "detailed_history_days": 365,
        },
        features={
            "telegram": True,
            "whatsapp": True,
            "light_prompt_scan": True,
            "near_miss": True,
            "full_near_miss_history": True,
            "condition_proof": True,
            "ai_assistant": True,
            "missed_alert_investigations": True,
            "advanced_forensics": True,
            "advanced_liquidity_filters": True,
            "setup_lifecycle": True,
            "correlation_compression": True,
            "advanced_analytics": True,
        },
    ),
    "creator": PlanDefinition(
        code="creator",
        name="Creator / Advanced",
        monthly_price=Decimal("79.00"),
        limits={
            "saved_strategies": 100,
            STRATEGY_APPROVAL_LIMIT_KEY: UNLIMITED_SYMBOL_CAP,
            "active_strategies": 25,
            "symbols_per_strategy": UNLIMITED_SYMBOL_CAP,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 2000,
            "on_demand_scans_per_month": 300,
            "light_prompt_scans_per_day": 200,
            "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
        },
        features={
            "telegram": True,
            "whatsapp": True,
            "light_prompt_scan": True,
            "shared_templates": True,
            "community_delivery": True,
            "custom_webhooks": True,
            "missed_alert_investigations": True,
            "api_access": True,
            "exports": True,
            "consultation_quota": True,
        },
    ),
    "community": PlanDefinition(
        code="community",
        name="Community / White-Label",
        monthly_price=Decimal("299.00"),
        limits={
            "saved_strategies": 250,
            STRATEGY_APPROVAL_LIMIT_KEY: UNLIMITED_SYMBOL_CAP,
            "active_strategies": 100,
            "symbols_per_strategy": UNLIMITED_SYMBOL_CAP,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 10000,
            "on_demand_scans_per_month": 1500,
            "light_prompt_scans_per_day": 1000,
            "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
        },
        features={
            "telegram": True,
            "whatsapp": True,
            "light_prompt_scan": True,
            "team_members": True,
            "shared_strategies": True,
            "role_integration": True,
            "branded_bot": True,
            "community_delivery": True,
            "missed_alert_investigations": True,
            "admin_controls": True,
            "white_label": True,
        },
    ),
    "full_access": PlanDefinition(
        code="full_access",
        name="Full Access",
        monthly_price=Decimal("0.00"),
        description="Time-limited full Hilal Markets access granted by an administrator.",
        limits={**FULL_ACCESS_LIMITS},
        features={**FULL_ACCESS_WITHOUT_WHATSAPP},
    ),
    "lifetime_partner": PlanDefinition(
        code="lifetime_partner",
        name="Lifetime Partner",
        monthly_price=Decimal("0.00"),
        description="Permanent partner access to Hilal Markets, excluding WhatsApp.",
        limits={**FULL_ACCESS_LIMITS},
        features={**FULL_ACCESS_WITHOUT_WHATSAPP},
    ),
    "lifetime": PlanDefinition(
        code="lifetime",
        name="Lifetime Founder",
        monthly_price=Decimal("0.00"),
        description="Founder/admin lifetime access with practical no-limit caps.",
        limits={
            "saved_strategies": UNLIMITED_SYMBOL_CAP,
            STRATEGY_APPROVAL_LIMIT_KEY: UNLIMITED_SYMBOL_CAP,
            "active_strategies": UNLIMITED_SYMBOL_CAP,
            "symbols_per_strategy": UNLIMITED_SYMBOL_CAP,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": UNLIMITED_SYMBOL_CAP,
            "alerts_per_trial_cycle": UNLIMITED_SYMBOL_CAP,
            "forensic_investigations_per_month": UNLIMITED_SYMBOL_CAP,
            "historical_previews_per_month": UNLIMITED_SYMBOL_CAP,
            "historical_previews_per_trial_cycle": UNLIMITED_SYMBOL_CAP,
            "on_demand_scans_per_month": UNLIMITED_SYMBOL_CAP,
            "on_demand_scans_total": UNLIMITED_SYMBOL_CAP,
            "light_prompt_scans_per_day": UNLIMITED_SYMBOL_CAP,
            "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
            "detailed_history_days": UNLIMITED_SYMBOL_CAP,
        },
        features={
            "telegram": True,
            "whatsapp": True,
            "light_prompt_scan": True,
            "near_miss": True,
            "full_near_miss_history": True,
            "condition_proof": True,
            "why_no_alert_limited": False,
            "missed_alert_investigations": True,
            "advanced_forensics": True,
            "forward_testing": True,
            "basic_analytics": True,
            "advanced_liquidity_filters": True,
            "setup_lifecycle": True,
            "correlation_compression": True,
            "advanced_analytics": True,
            "shared_templates": True,
            "community_delivery": True,
            "custom_webhooks": True,
            "api_access": True,
            "exports": True,
            "consultation_quota": True,
            "team_members": True,
            "shared_strategies": True,
            "role_integration": True,
            "branded_bot": True,
            "admin_controls": True,
            "white_label": True,
            "advanced_custom_indicators": True,
            "advanced_backtesting": True,
        },
    ),
    "pro_trial": PlanDefinition(
        code="pro_trial",
        name="7-Day Plus Trial",
        monthly_price=Decimal("0.00"),
        description="Seven days of Plus access for an existing trial account.",
        limits={
            "saved_strategies": 5,
            STRATEGY_APPROVAL_LIMIT_KEY: 20,
            "active_strategies": 3,
            "symbols_per_strategy": 200,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 20,
            "alerts_per_trial_cycle": 350,
            "forensic_investigations_per_month": UNLIMITED_SYMBOL_CAP,
            "historical_previews_per_trial_cycle": 10,
            "on_demand_scans_per_month": 10,
            "on_demand_scans_total": 10,
            "light_prompt_scans_per_day": 10,
            "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
            "detailed_history_days": 90,
        },
        features={
            "telegram": True,
            "whatsapp": False,
            "light_prompt_scan": True,
            "near_miss": True,
            "condition_proof": True,
            "why_no_alert_limited": True,
            "advanced_forensics": False,
            "forward_testing": True,
            "basic_analytics": True,
            "ai_assistant": True,
            "missed_alert_investigations": True,
            "api_access": False,
            "custom_webhooks": False,
            "shared_strategies": False,
            "community_delivery": False,
            "advanced_custom_indicators": False,
            "advanced_backtesting": False,
            "team_members": False,
            "white_label": False,
        },
    ),
}


# --------------------------------------------------------------------------------
# What a plan allows, written for a person.
#
# Every one of these reads :data:`PLAN_DEFINITIONS`, so the comparison table, the bullet
# points on a pricing card and the gate that actually stops somebody are three views of
# one number. They used to be three separate lists of words. That is how the Plus plan
# came to advertise a strategy-approval limit it did not have: the table said one thing,
# and the limits table simply had no such key, which means "no limit".
# --------------------------------------------------------------------------------

#: The words used when a plan does not cap something at all.
UNLIMITED_WORD = "Unlimited"

#: The words used when a plan does not include something at all.
NOT_INCLUDED_WORD = "Not included"


def _limit_value(code: str, key: str) -> int | None:
    """One plan's limit as a whole number, or ``None`` when it is not capped.

    A missing key means "not capped", which is how the limits table has always been read.
    Saying so here, once, is what stops each caller inventing its own reading of a gap.
    """

    raw = PLAN_DEFINITIONS[code].limits.get(key)
    if raw is None:
        return None
    number = int(raw)
    return None if number >= UNLIMITED_SYMBOL_CAP else number


def approval_allowance_words(code: str) -> str:
    """How many strategies this plan may approve, and over what window."""

    allowed = _limit_value(code, STRATEGY_APPROVAL_LIMIT_KEY)
    if allowed is None:
        return UNLIMITED_WORD
    return f"{allowed} per {STRATEGY_APPROVAL_WINDOW_DAYS} days"


def active_monitor_words(code: str) -> str:
    """How many market monitors may run at once on this plan."""

    allowed = _limit_value(code, "active_strategies")
    return UNLIMITED_WORD if allowed is None else str(allowed)


def notification_allowance_words(code: str) -> str:
    """How many monitor messages this plan allows, in the period that really binds.

    A plan can carry a daily cap, a weekly cap or both, and the one that binds is the
    smaller of the two once they are put on the same scale. The free plan's "2 a week" is
    tighter than its "2 a day", so a table that only read the daily figure would promise
    fourteen messages a week where two arrive.
    """

    daily = _limit_value(code, "alerts_per_day")
    weekly = _limit_value(code, "alerts_per_week")
    if weekly is not None and (daily is None or weekly <= daily * 7):
        return f"{weekly} per week"
    if daily is None:
        return UNLIMITED_WORD
    return f"{daily} per day"


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _approval_bullet(code: str) -> str:
    allowed = _limit_value(code, STRATEGY_APPROVAL_LIMIT_KEY)
    if allowed is None:
        return "Unlimited strategy approvals"
    return (
        f"Approve {allowed} {_plural(allowed, 'strategy', 'strategies')} "
        f"per {STRATEGY_APPROVAL_WINDOW_DAYS} days"
    )


def _active_monitor_bullet(code: str) -> str:
    allowed = _limit_value(code, "active_strategies")
    if allowed is None:
        return "Unlimited active market monitors"
    return f"{allowed} active market {_plural(allowed, 'monitor', 'monitors')}"


def _notification_bullet(code: str) -> str:
    daily = _limit_value(code, "alerts_per_day")
    weekly = _limit_value(code, "alerts_per_week")
    if weekly is not None and (daily is None or weekly <= daily * 7):
        return f"{weekly} monitor notifications per week across all monitors"
    if daily is None:
        return "Unlimited monitor alerts per day"
    return f"Up to {daily} monitor alerts per day"


#: How a monitor message can reach somebody, named the way a beginner would say it.
#: One string, because the pricing card, the comparison table and the settings page all
#: describe the same three destinations and used to name two of them.
DELIVERY_CHANNEL_WORDS = "In-app, Telegram, and email"

#: The same three, written the short way a narrow table column needs.
DELIVERY_CHANNEL_WORDS_SHORT = "In-app + Telegram + Email"

#: How long after paying a refund can still be asked for, per plan. ``None`` means the
#: plan has no refund window. One owner: the card, the table and the checkout consent
#: line all read it, and a number written out three times is a promise free to drift.
MONEY_BACK_DAYS: dict[str, int | None] = {"demo": None, "trader": None, "pro": 7}


def plan_name(code: str) -> str:
    """What one plan is called, wherever a sentence or a heading names it.

    One owner. Plan names are typed into templates, error messages and limit
    notices; a rename that missed one of them left a page and a refusal calling the
    same plan two different things. Everything that needs the word asks here.
    """

    definition = PLAN_DEFINITIONS.get(code)
    return definition.name if definition else code


def money_back_words(code: str) -> str:
    """The refund window for one plan, for a table cell."""

    days = MONEY_BACK_DAYS.get(code)
    return NOT_INCLUDED_WORD if days is None else f"{days} days"


def money_back_headline(code: str) -> str | None:
    """The bold line above that sentence, or ``None`` when there is no refund.

    A separate function from :func:`money_back_words` because the two read differently in
    the two places they appear: a table cell says "7 days", a headline says "7-day
    money-back guarantee". The Jinja card used the table cell's words inside the headline
    and read "7 days money-back guarantee", while the React card said "7-day" — the same
    promise, worded two ways, on two pages showing the same plan.
    """

    days = MONEY_BACK_DAYS.get(code)
    if days is None:
        return None
    return f"{days}-day money-back guarantee"


def money_back_note(code: str) -> str | None:
    """The refund sentence under a pricing card, or ``None`` when there is no refund."""

    days = MONEY_BACK_DAYS.get(code)
    if days is None:
        return None
    return f"Cancel within {days} days of payment for a full refund."


#: Every bullet on a pricing card is one row of `PUBLIC_PLAN_COMPARISON`, said in
#: sentence form. The table under the cards is the source of truth: a card may show fewer
#: rows than the table, never a different answer to one. Two rules follow from that, and
#: `tests/unit/test_invariant_plan_card_matches_comparison.py` checks both for every plan
#: and every row:
#:
#: * a card never names something the table marks "Not included" for that plan;
#: * where the table states a level ("Full") or an allowance ("20 per 30 days"), the
#:   bullet states the same one — the allowance bullets read the same limits the table
#:   reads, so a changed limit moves both together.
PUBLIC_PLAN_PRESENTATIONS: dict[str, PublicPlanPresentation] = {
    "demo": PublicPlanPresentation(
        annual_price=Decimal("0.00"),
        cta_label="Start free",
        highlighted_feature="Halal assets, passports, and monitors",
        visible_features=(
            "Halal assets, passports, and monitors",
            _approval_bullet("demo"),
            _active_monitor_bullet("demo"),
            _notification_bullet("demo"),
            "Full Evidence Passports",
        ),
        # "Published compliance-status changes" used to sit here beside "Favorite coins
        # and compliance-status changes". Both are the one table row "Halal status-change
        # alerts", so the card listed the same thing twice and a beginner counted two.
        additional_features=(
            "Full methodology reports: reasons, sources, versions, and review dates",
            "Favorite coins",
            f"{DELIVERY_CHANNEL_WORDS} notifications",
        ),
    ),
    "trader": PublicPlanPresentation(
        annual_price=Decimal("120.00"),
        cta_label="Choose Plus monthly",
        trial_note=money_back_note("trader"),
        highlighted_feature="AI assistant with Plus limits",
        visible_features=(
            "Everything in Explore",
            "AI assistant with Plus limits",
            _active_monitor_bullet("trader"),
            _approval_bullet("trader"),
            _notification_bullet("trader"),
            # The table says "Included" for this row, not "Full". The card said "Full",
            # which promised a level the table does not offer.
            "Why wasn't I alerted? explanations",
            "Full Opportunity Journeys",
        ),
        # "Missed-alert investigations" used to sit here as well. It is the same feature
        # as "Why wasn't I alerted? explanations" above — the settings field behind both
        # is `missed_alert_investigations` — so the card was selling one thing twice
        # under two names, and a beginner reading it would count two features.
        additional_features=(
            "Full condition-level proof",
            "Telegram monitor delivery",
        ),
    ),
    "pro": PublicPlanPresentation(
        annual_price=Decimal("220.00"),
        cta_label="Choose Pro",
        trial_note=money_back_note("pro"),
        highlighted_feature="Max limits across features",
        visible_features=(
            "Everything in Plus",
            "Max limits across features",
            _active_monitor_bullet("pro"),
            _approval_bullet("pro"),
            _notification_bullet("pro"),
        ),
        additional_features=(
            "AI assistant with Pro limits",
            "Full condition-level proof",
            "Full Opportunity Journeys",
            "Why wasn't I alerted? explanations",
            "Telegram monitor delivery",
        ),
    ),
}


#: How much of the AI assistant each plan carries. Words, not a number, because what
#: changes between the plans is how far the assistant will go — not a countable thing.
AI_ASSISTANT_WORDS: dict[str, str] = {
    "demo": "Limited",
    "trader": "Extended",
    "pro": "Max",
}


def _comparison_rows() -> tuple[tuple[str, str, str, str], ...]:
    """The comparison table, built from the limits it is describing.

    Nothing here is a second copy of a number. A row that states an allowance asks the
    same function the pricing card and the gate ask, so the three cannot drift.
    """

    def per_plan(reader: Callable[[str], str]) -> tuple[str, str, str]:
        return (reader("demo"), reader("trader"), reader("pro"))

    return (
        ("Halal Assets market", "Included", "Included", "Included"),
        ("Evidence Passports", "Full", "Full", "Full"),
        ("Methodology reports", "Full", "Full", "Full"),
        ("Favorite coins", "Included", "Included", "Included"),
        (
            "Halal status-change alerts",
            DELIVERY_CHANNEL_WORDS_SHORT,
            DELIVERY_CHANNEL_WORDS_SHORT,
            DELIVERY_CHANNEL_WORDS_SHORT,
        ),
        ("AI assistant", *per_plan(AI_ASSISTANT_WORDS.__getitem__)),
        ("Strategy approvals", *per_plan(approval_allowance_words)),
        ("Active market monitors", *per_plan(active_monitor_words)),
        ("Monitor notifications", *per_plan(notification_allowance_words)),
        ("Condition proof", NOT_INCLUDED_WORD, "Full", "Full"),
        ("Opportunity Journeys", NOT_INCLUDED_WORD, "Full", "Full"),
        ("Why wasn't I alerted?", NOT_INCLUDED_WORD, "Included", "Included"),
        ("Telegram monitor delivery", "Included", "Included", "Included"),
        ("Money-back window", *per_plan(money_back_words)),
    )


PUBLIC_PLAN_COMPARISON: tuple[tuple[str, str, str, str], ...] = _comparison_rows()


#: Which plan each column of `PUBLIC_PLAN_COMPARISON` describes, after the feature name.
PLAN_COMPARISON_COLUMNS: tuple[str, ...] = ("demo", "trader", "pro")


def visible_plan_comparison_headers(*, billing_enabled: bool) -> tuple[str, ...]:
    """Column names for the comparison table, one per visible plan.

    Derived from the same list the cards come from, so a plan can never appear as a card
    without a column or as a column without a card.
    """
    return tuple(
        PLAN_DEFINITIONS[code].name
        for code in visible_public_plan_codes(billing_enabled=billing_enabled)
    )


def visible_plan_comparison(*, billing_enabled: bool) -> tuple[tuple[str, ...], ...]:
    """Comparison rows, one column per visible plan."""
    visible = visible_public_plan_codes(billing_enabled=billing_enabled)
    columns = [0] + [
        1 + PLAN_COMPARISON_COLUMNS.index(code)
        for code in visible
        if code in PLAN_COMPARISON_COLUMNS
    ]
    return tuple(
        tuple(row[index] for index in columns) for row in PUBLIC_PLAN_COMPARISON
    )


# --------------------------------------------------------------------------------
# Which plan is above which, and therefore what pressing a button means.
# --------------------------------------------------------------------------------

#: The order the public plans sit in. One list, because four things ask the same
#: question — the button on a plan card ("Pay", "Upgrade" or "Downgrade"), the form that
#: opens when it is pressed, the rule deciding whether the change takes effect now or at
#: the end of the paid period, and the System Brain row describing what happened.
PLAN_RANK: dict[str, int] = {"demo": 0, "trader": 1, "pro": 2}

#: What one plan card's button does, given the plan somebody is already on.
PLAN_CHANGE_SAME = "same"
PLAN_CHANGE_UPGRADE = "upgrade"
PLAN_CHANGE_DOWNGRADE = "downgrade"
PLAN_CHANGE_NEW = "new"


def plan_rank(code: str) -> int:
    """Where a plan sits in the order. An unknown plan sits at the bottom."""

    return PLAN_RANK.get(code, 0)


def smallest_plan_with(feature: str) -> str | None:
    """The cheapest plan on sale that really carries this feature, or ``None``.

    One owner, because refusals name a plan. Two routes refusing "Why wasn't I alerted?"
    both had the sentence "available on the Monitor plan" typed into them, and both went
    on saying it after the plan was renamed to Plus. Asking the catalog which plan first
    grants the feature means a rename, a reprice, or moving a feature between plans can
    never leave a refusal pointing at the wrong plan.
    """

    for code in sorted(PURCHASABLE_PLAN_CODES, key=plan_rank):
        definition = PLAN_DEFINITIONS.get(code)
        if definition is not None and definition.features.get(feature):
            return code
    return None


def plan_change_kind(*, current: str | None, target: str) -> str:
    """Moving from one plan to another: is it the same plan, up, down, or a first buy?

    ``current`` is ``None`` — or the free plan — when nothing is being paid for yet, and
    then buying anything is a first purchase rather than an upgrade. That distinction is
    not cosmetic: an upgrade changes a subscription the payment company already holds,
    and a first purchase creates one.
    """

    if current is None or current not in PURCHASABLE_PLAN_CODES:
        return PLAN_CHANGE_SAME if current == target else PLAN_CHANGE_NEW
    if current == target:
        return PLAN_CHANGE_SAME
    return (
        PLAN_CHANGE_UPGRADE
        if plan_rank(target) > plan_rank(current)
        else PLAN_CHANGE_DOWNGRADE
    )


def get_plan_definition(code: str) -> PlanDefinition:
    try:
        return PLAN_DEFINITIONS[code]
    except KeyError as exc:
        raise ValueError(f"Unknown plan code: {code}") from exc


#: A period written the way this product writes them: a count and a unit, nothing else.
_PERIOD_PATTERN = re.compile(r"^(\d+)([mhd])$")

_UNIT_MINUTES: dict[str, int] = {"m": 1, "h": 60, "d": 1440}


def timeframe_to_minutes(timeframe: str) -> int:
    """How many minutes a period covers, for any period this product writes.

    Deliberately wider than :func:`ai_market_monitor.engine.data_freshness.timeframe_minutes`,
    and the two answer different questions. That one asks "how long is a candle we can
    actually evaluate", so it must refuse anything outside the executable list. This one
    also has to size a scan interval and a plan limit, and those are written in periods
    like ``3d`` that are real lengths of time but not candles anybody trades on.

    What both refuse is a period that is not a period. The copy this replaces ended in a
    bare ``return 1440``, so ``banana`` and a typo were sized as a day without a word.
    Where the two overlap they agree, and a test asserts that for every supported period.
    """

    normalized = str(timeframe or "").strip().casefold()
    known = TIMEFRAME_MINUTES.get(normalized)
    if known is not None:
        return known
    match = _PERIOD_PATTERN.match(normalized)
    if match is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return int(match.group(1)) * _UNIT_MINUTES[match.group(2)]
