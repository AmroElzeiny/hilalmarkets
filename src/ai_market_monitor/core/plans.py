from dataclasses import dataclass, field
from decimal import Decimal

UNLIMITED_SYMBOL_CAP = 100_000
PUBLIC_PLAN_CODES = ("demo", "trader", "pro")
PURCHASABLE_PLAN_CODES = ("trader", "pro")

FULL_ACCESS_LIMITS: dict[str, int] = {
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
}

FULL_ACCESS_WITHOUT_WHATSAPP: dict[str, bool] = {
    "telegram": True,
    "whatsapp": False,
    "light_prompt_scan": True,
    "near_miss": True,
    "full_near_miss_history": True,
    "condition_proof": True,
    "why_no_alert_limited": False,
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
    """Which plans a visitor may be shown.

    With billing off, only the free Private Beta plan. Showing the paid plans returned a
    price and a "Try Monitor for 7 days" button for something nobody can buy: the
    checkout route is disabled, so every one of those buttons was a dead end. A price the
    user cannot act on is worse than no price — it reads as a charge they are about to
    incur.
    """
    if not billing_enabled:
        return (PRIVATE_BETA_PLAN_CODE,)
    return PUBLIC_PLAN_CODES


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
        name="Explore",
        monthly_price=Decimal("0.00"),
        description=(
            "Explore methodology-screened assets, inspect their evidence, and follow "
            "status changes for your favorites."
        ),
        limits={
            "saved_strategies": 0,
            "active_strategies": 0,
            "symbols_per_strategy": 0,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 0,
            "forensic_investigations_per_month": 0,
            "historical_previews_per_month": 0,
            "on_demand_scans_per_month": 0,
            "light_prompt_scans_per_day": 0,
            "light_prompt_symbols": 0,
            "detailed_history_days": 0,
        },
        features={
            "telegram": True,
            "whatsapp": False,
            "light_prompt_scan": False,
            "near_miss": False,
            "condition_proof": False,
            "setup_lifecycle": False,
            "advanced_forensics": False,
            "custom_webhooks": False,
            "community_delivery": False,
        },
    ),
    "trader": PlanDefinition(
        code="trader",
        name="Monitor",
        monthly_price=Decimal("12.00"),
        description="Continuous guided monitoring for active individual investors.",
        limits={
            "saved_strategies": 2,
            "active_strategies": 2,
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
            "admin_controls": True,
            "white_label": True,
        },
    ),
    "full_access": PlanDefinition(
        code="full_access",
        name="Full Access",
        monthly_price=Decimal("0.00"),
        description="Time-limited full HilalMarkets access granted by an administrator.",
        limits={**FULL_ACCESS_LIMITS},
        features={**FULL_ACCESS_WITHOUT_WHATSAPP},
    ),
    "lifetime_partner": PlanDefinition(
        code="lifetime_partner",
        name="Lifetime Partner",
        monthly_price=Decimal("0.00"),
        description="Permanent partner access to HilalMarkets, excluding WhatsApp.",
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
        description="Seven days of Monitor access before the first provider charge.",
        limits={
            "saved_strategies": 2,
            "active_strategies": 2,
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
            "For traders who want to explore assets listed as Halal under a selected "
            "methodology, inspect the evidence, and follow changes to favorite coins."
        ),
        cta_label="Start free",
        highlighted_feature="Halal assets, methodologies, and evidence reports",
        visible_features=(
            "Halal assets, methodologies, and evidence reports",
            "Full Evidence Passports",
            "Methodology reasons, sources, versions, and review dates",
            "Methodology comparison when available",
            "Favorite coins",
            "In-app Halal status-change alerts for favorites",
            "Telegram Halal status-change alerts for favorites",
        ),
        additional_features=(
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
        cta_label="Try Monitor for 7 days",
        badge="Most Popular",
        trial_note="No charge for seven days. Cancel before the first payment.",
        highlighted_feature="AI assistant for creating market monitors",
        visible_features=(
            "Everything in Explore",
            "AI assistant for creating market monitors",
            "2 active market monitors",
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
    ("AI assistant", "Not included", "Included", "Included"),
    ("Active market monitors", "Not included", "2", "10"),
    ("Quick scans per month", "Not included", "10", "100"),
    ("Monitor alerts per day", "Not included", "Up to 50", "Unlimited"),
    ("Condition proof", "Not included", "Full", "Full"),
    ("Opportunity Journeys", "Not included", "Complete", "Complete"),
    ("Missed-alert investigations", "Not included", "Included", "Included"),
    ("Telegram monitor delivery", "Not included", "Included", "Included"),
    ("WhatsApp", "Not included", "Not included", "Coming soon"),
    ("Monitor trial", "Not included", "7 days, no charge", "Not included"),
)


#: Which plan each column of `PUBLIC_PLAN_COMPARISON` describes, after the feature name.
PLAN_COMPARISON_COLUMNS: tuple[str, ...] = ("demo", "trader", "pro")


def visible_plan_comparison_headers(*, billing_enabled: bool) -> tuple[str, ...]:
    """Column names for the comparison table, for the plans on offer."""
    return tuple(
        PLAN_DEFINITIONS[code].name
        for code in visible_public_plan_codes(billing_enabled=billing_enabled)
    )


def visible_plan_comparison(*, billing_enabled: bool) -> tuple[tuple[str, ...], ...]:
    """Comparison rows trimmed to the plans on offer.

    With billing off the table used to compare three plans while only one of them could
    be had — a page that answers "what do I get for $22" for a product with no way to pay
    $22.
    """
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


def timeframe_to_minutes(timeframe: str) -> int:
    unit = timeframe[-1]
    value = int(timeframe[:-1])
    if unit == "m":
        return value
    if unit == "h":
        return value * 60
    if unit == "d":
        return value * 1440
    raise ValueError(f"Unsupported timeframe: {timeframe}")
