from dataclasses import dataclass, field
from decimal import Decimal

UNLIMITED_SYMBOL_CAP = 100_000
PUBLIC_PLAN_CODES = ("demo", "trader", "pro")
PURCHASABLE_PLAN_CODES = ("trader", "pro")


def visible_public_plan_codes(*, billing_enabled: bool) -> tuple[str, ...]:
    """Return the customer catalog allowed by the current release mode."""
    return PUBLIC_PLAN_CODES if billing_enabled else ("demo",)


@dataclass(frozen=True, slots=True)
class PlanDefinition:
    code: str
    name: str
    monthly_price: Decimal
    currency: str = "USD"
    description: str | None = None
    limits: dict[str, int | float | str | None] = field(default_factory=dict)
    features: dict[str, bool | int | float | str] = field(default_factory=dict)


PLAN_DEFINITIONS: dict[str, PlanDefinition] = {
    "demo": PlanDefinition(
        code="demo",
        name="Free",
        monthly_price=Decimal("0.00"),
        description="Explore screened assets and create one guided Watch Plan.",
        limits={
            "saved_strategies": 1,
            "active_strategies": 1,
            "symbols_per_strategy": 50,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 10,
            "forensic_investigations_per_month": 0,
            "historical_previews_per_month": 3,
            "on_demand_scans_per_month": 1,
            "light_prompt_scans_per_day": 3,
            "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
        },
        features={
            "telegram": True,
            "light_prompt_scan": True,
            "near_miss": False,
            "condition_proof": True,
            "advanced_forensics": False,
            "custom_webhooks": False,
            "community_delivery": False,
        },
    ),
    "trader": PlanDefinition(
        code="trader",
        name="Core",
        monthly_price=Decimal("12.00"),
        description="Continuous guided monitoring for active individual investors.",
        limits={
            "saved_strategies": 10,
            "active_strategies": 3,
            "symbols_per_strategy": 200,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 100,
            "forensic_investigations_per_month": 20,
            "on_demand_scans_per_month": 10,
            "light_prompt_scans_per_day": 10,
            "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
        },
        features={
            "telegram": True,
            "light_prompt_scan": True,
            "near_miss": True,
            "condition_proof": True,
            "forward_testing": True,
            "basic_analytics": True,
            "advanced_forensics": False,
        },
    ),
    "pro": PlanDefinition(
        code="pro",
        name="Pro",
        monthly_price=Decimal("29.00"),
        description="More Watch Plans, deeper diagnostics, and advanced controls.",
        limits={
            "saved_strategies": 50,
            "active_strategies": 10,
            "symbols_per_strategy": UNLIMITED_SYMBOL_CAP,
            "minimum_timeframe_minutes": 1,
            "alerts_per_day": 500,
            "forensic_investigations_per_month": 500,
            "on_demand_scans_per_month": 60,
            "light_prompt_scans_per_day": 50,
            "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
        },
        features={
            "telegram": True,
            "light_prompt_scan": True,
            "near_miss": True,
            "full_near_miss_history": True,
            "condition_proof": True,
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
        name="Conditional 14-Day Trial",
        monthly_price=Decimal("0.00"),
        description=(
            "Renewable 14-day monitoring cycle that renews until one qualifying alert is delivered."
        ),
        limits={
            "saved_strategies": 1,
            "active_strategies": 1,
            "symbols_per_strategy": 300,
            "minimum_timeframe_minutes": 1,
            "alerts_per_trial_cycle": 500,
            "historical_previews_per_trial_cycle": 3,
            "on_demand_scans_total": 1,
            "light_prompt_scans_per_day": 10,
            "light_prompt_symbols": UNLIMITED_SYMBOL_CAP,
            "detailed_history_days": 7,
            "forensic_investigations_per_month": 5,
        },
        features={
            "telegram": True,
            "light_prompt_scan": True,
            "near_miss": True,
            "condition_proof": True,
            "why_no_alert_limited": True,
            "advanced_forensics": False,
            "forward_testing": True,
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
