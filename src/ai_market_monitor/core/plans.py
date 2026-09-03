import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from ai_market_monitor.schemas.timeframes import TIMEFRAME_MINUTES

UNLIMITED_SYMBOL_CAP = 100_000
PUBLIC_PLAN_CODES = ("demo", "trader", "pro")
PURCHASABLE_PLAN_CODES = ("trader", "pro")

FULL_ACCESS_LIMITS: dict[str, int] = {
    "saved_strategies": UNLIMITED_SYMBOL_CAP,
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

#: When the launch offer stops. After this instant the launch code stops working, the
#: plan costs its normal price, and the countdown disappears. All three facts come from
#: this one value.
PROMOTION_ENDS_AT = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)

#: The code somebody types to get the launch price, and how much it takes off.
#:
#: The launch price used to apply on its own, to everybody, with nothing to type. It is a
#: **discount code** now: the plan costs its normal price, and this code is the only way
#: to the lower one. That is why the number below is a percentage and not a second price
#: — one number, so "25% off" and "$15 a month" can never drift apart. Every surface that
#: quotes the lower price derives it here through :func:`coded_monthly_price`.
#:
#: The card route is a separate world: Creem holds its own copy of the product price and
#: its own discount codes, so the same code must exist in the Creem dashboard at the same
#: percentage. `scripts/check_creem_prices.py` compares the two and is the only thing
#: that can catch a disagreement, because nothing offline can see Creem.
LAUNCH_DISCOUNT_CODE = "HILAL25"

#: What a card says instead of a price when the plan cannot be bought yet.
COMING_SOON_LABEL = "Soon"

#: Money is rounded to the cent, and never in the customer's disfavour by accident.
#: ``ROUND_HALF_UP`` on a percentage of a whole-dollar price is exact for every price
#: this product sells; it is named rather than left to the default so a future price with
#: an odd cent cannot quietly round a charge up.
_CENTS = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class PlanOffer:
    """Whether a plan can be bought, per interval, and what a code takes off it."""

    monthly_available: bool
    annual_available: bool
    #: How much :data:`LAUNCH_DISCOUNT_CODE` takes off a month, as a percentage, while
    #: :data:`PROMOTION_ENDS_AT` is still in the future. ``None`` means this plan has no
    #: launch code and is only ever sold at its normal price.
    launch_discount_percent: Decimal | None = None


PLAN_OFFERS: dict[str, PlanOffer] = {
    # Free, so there is nothing to buy and nothing to discount.
    "demo": PlanOffer(monthly_available=True, annual_available=False),
    # The one plan on offer. Annual billing is not open yet.
    "trader": PlanOffer(
        monthly_available=True,
        annual_available=False,
        launch_discount_percent=Decimal("25"),
    ),
    # Not open yet, on either interval.
    "pro": PlanOffer(monthly_available=False, annual_available=False),
}

_DEFAULT_OFFER = PlanOffer(monthly_available=False, annual_available=False)


def plan_offer(code: str) -> PlanOffer:
    """The offer for one plan. An unknown plan is not for sale.

    Fails closed: a plan nobody described cannot be bought by accident.
    """
    return PLAN_OFFERS.get(code, _DEFAULT_OFFER)


def promotion_is_active(now: datetime | None = None) -> bool:
    """Is the launch offer still running?"""
    return (now or datetime.now(UTC)) < PROMOTION_ENDS_AT


def price_after_percent(amount: Decimal, percent: Decimal) -> Decimal:
    """``amount`` with ``percent`` taken off, to the cent.

    The one place this arithmetic happens. Every discount in the product — the launch
    code, a code read from Creem, a code read from the environment — comes through here,
    so no two of them can round differently and no page can quote a number a checkout
    would not charge.
    """
    if percent <= 0:
        return amount.quantize(_CENTS, rounding=ROUND_HALF_UP)
    if percent >= 100:
        return Decimal("0.00")
    kept = (Decimal("100") - percent) / Decimal("100")
    return (amount * kept).quantize(_CENTS, rounding=ROUND_HALF_UP)


def launch_discount_percent(code: str, *, now: datetime | None = None) -> Decimal | None:
    """How much the launch code takes off this plan today, or ``None``.

    Reads the offer and the clock together, so the price on a card, the sentence naming
    the code under it and the countdown beside it can never say different things.
    """
    offer = plan_offer(code)
    if offer.launch_discount_percent is None or not promotion_is_active(now):
        return None
    return offer.launch_discount_percent


def effective_monthly_price(code: str, *, now: datetime | None = None) -> Decimal:
    """What a checkout charges for one month when **no** discount code is used.

    This is the price the payment company is asked for by default, so it is also the
    number `scripts/check_creem_prices.py` holds the Creem product against.

    It used to carry the launch price. It does not any more: the launch price is reached
    by typing :data:`LAUNCH_DISCOUNT_CODE`, and a function that quietly returned the
    lower number would charge everybody the offer price whether they used the code or
    not — which is exactly what "the code is the only route to it" rules out.
    """
    del now
    return PLAN_DEFINITIONS[code].monthly_price


def coded_monthly_price(code: str, *, now: datetime | None = None) -> Decimal | None:
    """What one month costs **with** the launch code, or ``None`` when there is no code.

    This is the headline figure on a pricing card, beside the crossed-out normal price.
    """
    percent = launch_discount_percent(code, now=now)
    if percent is None:
        return None
    return price_after_percent(PLAN_DEFINITIONS[code].monthly_price, percent)


def original_monthly_price(code: str, *, now: datetime | None = None) -> Decimal | None:
    """The crossed-out price, or ``None`` when there is nothing to cross out."""
    if coded_monthly_price(code, now=now) is None:
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
    monthly = coded_monthly_price(code, now=now) or effective_monthly_price(code, now=now)
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


def plan_offer_payload(code: str, *, now: datetime | None = None) -> dict[str, object]:
    """The offer as plain data, for the landing page and the dashboard.

    A price for an interval that is not open yet is ``None``, not the number. Leaving the
    number in the payload would ship it in the page source for anyone to read, and the
    point of "Soon" is that there is no price to quote yet.

    ``monthlyPrice`` is the headline on a card: the launch-code price while the launch
    offer runs, the normal price otherwise. ``fullMonthlyPrice`` is always what a
    checkout charges with no code, so a card can say "without the code it is $20" from
    the same object rather than working it out.
    """
    offer = plan_offer(code)
    full = effective_monthly_price(code, now=now)
    coded = coded_monthly_price(code, now=now)
    original = original_monthly_price(code, now=now)
    percent = launch_discount_percent(code, now=now)
    return {
        "monthlyAvailable": offer.monthly_available,
        "annualAvailable": offer.annual_available,
        "monthlyPrice": float(coded if coded is not None else full)
        if offer.monthly_available
        else None,
        "annualPrice": (
            float(PUBLIC_PLAN_PRESENTATIONS[code].annual_price)
            if offer.annual_available and code in PUBLIC_PLAN_PRESENTATIONS
            else None
        ),
        "originalMonthlyPrice": float(original) if original is not None else None,
        # What a checkout charges when nobody types a code, and the code that changes it.
        # Both are `None` for a plan nobody can buy, for the same reason as the price.
        "fullMonthlyPrice": float(full) if offer.monthly_available else None,
        "discountCode": (
            LAUNCH_DISCOUNT_CODE if percent is not None and offer.monthly_available else None
        ),
        "discountPercent": (
            float(percent) if percent is not None and offer.monthly_available else None
        ),
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
    annual_price: Decimal
    description: str
    cta_label: str
    visible_features: tuple[str, ...]
    additional_features: tuple[str, ...] = ()
    badge: str | None = None
    trial_note: str | None = None
    highlighted_feature: str | None = None


PLAN_DEFINITIONS: dict[str, PlanDefinition] = {
    "demo": PlanDefinition(
        code="demo",
        name="Basic",
        monthly_price=Decimal("0.00"),
        description=(
            "Explore screened assets and use the AI assistant with measured monitoring "
            "limits."
        ),
        limits={
            "saved_strategies": 2,
            "strategy_approvals_per_30_days": 2,
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
        name="Monitor",
        monthly_price=Decimal("20.00"),
        description="Continuous guided monitoring for active individual investors.",
        limits={
            "saved_strategies": 5,
            "active_strategies": 5,
            "symbols_per_strategy": 200,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 50,
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
        monthly_price=Decimal("22.00"),
        description="More simultaneous market monitors, quick scans, and alert capacity.",
        limits={
            "saved_strategies": 10,
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
        name="7-Day Monitor Trial",
        monthly_price=Decimal("0.00"),
        description="Seven days of Monitor access for an existing trial account.",
        limits={
            "saved_strategies": 5,
            "active_strategies": 5,
            "symbols_per_strategy": 200,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 50,
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


PUBLIC_PLAN_PRESENTATIONS: dict[str, PublicPlanPresentation] = {
    "demo": PublicPlanPresentation(
        annual_price=Decimal("0.00"),
        description=(
            "For traders who want the AI assistant, screened-asset evidence, and a "
            "measured introduction to market monitoring."
        ),
        cta_label="Start free",
        highlighted_feature="AI assistant with Basic limits",
        visible_features=(
            "AI assistant with Basic limits",
            "Approve 2 strategies per 30 days",
            "1 active market monitor",
            "2 monitor notifications per week across all monitors",
            "1 quick scan per week",
            "Halal assets, methodologies, and evidence reports",
            "Full Evidence Passports",
        ),
        additional_features=(
            "Methodology reasons, sources, versions, and review dates",
            "Favorite coins and compliance-status changes",
            "In-app and Telegram notifications",
            "Why wasn't I alerted? available on Monitor",
            "Published compliance-status changes",
            "Standard email support",
        ),
    ),
    "trader": PublicPlanPresentation(
        annual_price=Decimal("120.00"),
        description=(
            "For regular traders who want AI-assisted market monitoring and clear "
            "evidence behind every alert."
        ),
        cta_label="Choose Monitor monthly",
        trial_note="Cancel within 7 days of payment for a full refund.",
        highlighted_feature="AI assistant for creating market monitors",
        visible_features=(
            "Everything in Basic",
            "AI assistant for creating market monitors",
            "5 active market monitors",
            "10 quick scans per month",
            "Up to 50 monitor alerts per day",
            "Full Why wasn't I alerted? explanations",
            "Complete Opportunity Journeys",
        ),
        additional_features=(
            "Condition-level proof",
            "Missed-alert investigations",
            "In-app and Telegram monitor alerts",
        ),
    ),
    "pro": PublicPlanPresentation(
        annual_price=Decimal("220.00"),
        description=(
            "For active traders who need more simultaneous monitors, more quick scans, "
            "and unlimited monitor alerts."
        ),
        cta_label="Choose Pro",
        highlighted_feature="WhatsApp delivery",
        visible_features=(
            "Everything in Monitor",
            "10 active market monitors",
            "100 quick scans per month",
            "Unlimited monitor alerts per day",
            "WhatsApp delivery",
        ),
        additional_features=(
            "AI assistant for creating market monitors",
            "Condition-level proof",
            "Complete Opportunity Journeys",
            "Missed-alert investigations",
        ),
    ),
}


PUBLIC_PLAN_COMPARISON: tuple[tuple[str, str, str, str], ...] = (
    ("Halal Assets market", "Included", "Included", "Included"),
    ("Evidence Passports", "Full", "Full", "Full"),
    ("Methodology reports", "Full", "Full", "Full"),
    ("Favorite coins", "Included", "Included", "Included"),
    (
        "Halal status-change alerts",
        "In-app + Telegram",
        "In-app + Telegram",
        "In-app + Telegram",
    ),
    ("AI assistant", "Limited", "Included", "Included"),
    ("Strategy approvals", "2 per 30 days", "Included", "Included"),
    ("Active market monitors", "1", "5", "10"),
    ("Quick scans", "1 per week", "10 per month", "100 per month"),
    ("Monitor notifications", "2 per week", "Up to 50 per day", "Unlimited"),
    ("Condition proof", "Not included", "Full", "Full"),
    ("Opportunity Journeys", "Not included", "Complete", "Complete"),
    ("Why wasn't I alerted?", "Not included", "Included", "Included"),
    ("Telegram monitor delivery", "Included", "Included", "Included"),
    ("WhatsApp", "Not included", "Not included", "Coming soon"),
    ("Money-back window", "Not included", "7 days", "Not included"),
)


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
