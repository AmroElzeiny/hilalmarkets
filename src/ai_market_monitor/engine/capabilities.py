from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ai_market_monitor.engine.candle_patterns import pattern_names
from ai_market_monitor.engine.context_conditions import TIME_CONDITION_NAMES
from ai_market_monitor.engine.price_action import PRICE_ACTION_NAMES
from ai_market_monitor.schemas.strategy import (
    MEASURED_COMPARATORS,
    UNARY_COMPARATORS,
    Comparator,
)

CapabilityCategory = str

#: "The author of this capability did not say."
#:
#: `default_comparator` used to *default* to "is_true", which made a deliberate yes/no
#: declaration impossible to tell from one nobody had thought about. Both readings
#: existed in the registry — 143 capabilities meant it and the rest had simply
#: inherited it — and every consumer had to guess which. This is the marker that ends
#: the guessing: only a capability that actually writes `default_comparator=` has said
#: anything, and only that one is treated as having said it.
_NOT_DECLARED: Any = object()

#: Operand kinds that answer yes or no rather than producing a number to compare.
_BOOLEAN_OPERAND_KINDS: frozenset[str] = frozenset({"price_action", "candle_pattern"})

PRIMARY_BUILDER_CATEGORIES: tuple[str, ...] = (
    "price",
    "indicator",
    "candle_pattern",
    "price_action",
    "market_structure",
    "liquidity_smart_money",
    "volume_flow",
    "volatility_squeeze",
    "trend",
    "momentum",
    "time_session",
    "market_context",
    "relative_strength",
    "risk_trade_quality",
    "news_events",
    "order_book_liquidity",
    "ranking_universe",
    "alert_behavior",
    "setup_lifecycle",
    "advanced_logic",
)


@dataclass(frozen=True, slots=True)
class CapabilityParameter:
    name: str
    type: str
    default: Any = None
    required: bool = False
    description: str = ""
    options: tuple[str, ...] = ()
    #: What a trader might call this parameter, in their own words. Used to prove that a
    #: number belongs to *this* role and not to another one measured in the same unit —
    #: an RSI period and a confirmation count are both candle counts, so the value alone
    #: cannot tell them apart. Leave empty and the name itself is the only phrase.
    source_aliases: tuple[str, ...] = ()
    #: Force role evidence even when nothing else shares this unit. For a parameter whose
    #: meaning changes the rule completely if mistaken.
    requires_role_phrase: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    key: str
    label: str
    category: CapabilityCategory
    condition_type: str
    description: str
    aliases: tuple[str, ...] = ()
    required_data: tuple[str, ...] = ("ohlcv",)
    light_mode: bool = True
    free_plan: bool = True
    deterministic: bool = True
    requires_higher_timeframe: bool = False
    optional_only: bool = False
    executable: bool = True
    operand_kind: str | None = None
    operand_name: str | None = None
    default_parameters: dict[str, Any] = field(default_factory=dict)
    #: How this is compared, and every comparison it allows. `_cap` is the only place
    #: that builds a CapabilitySpec and it always settles both together, so these
    #: defaults are never the ones that ship. They are written as a *consistent* pair
    #: anyway: the previous pair said "is_true" here and listed only numeric
    #: comparisons below, which is the exact contradiction that let a yes/no rule be
    #: rewritten as ">= 0". A type should not be able to hold that by default.
    default_comparator: str = "is_true"
    default_threshold: Any = True
    parameters: tuple[CapabilityParameter, ...] = ()
    guidance: str | None = None
    examples: tuple[str, ...] = ()
    supported_markets: tuple[str, ...] = ("spot",)
    supported_timeframes: tuple[str, ...] = (
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "6h",
        "8h",
        "12h",
        "1d",
    )
    outputs: tuple[str, ...] = ("value",)
    #: Consistent with `default_comparator` above, and always replaced by `_cap`.
    supported_comparators: tuple[str, ...] = ("is_false", "is_true")
    visual_card_sentence: str | None = None
    risk_notes: str = ""
    evaluator_function: str | None = None
    warmup_candles: int = 1
    test_cases: tuple[str, ...] = ()
    builder_category: str | None = None
    beginner_friendly: bool = False
    provider_required: str | None = None
    availability: str = "available"
    phase: int = 1
    approximation: bool = False
    approximation_note: str = ""
    semantic_tags: tuple[str, ...] = ()
    intent_examples: tuple[str, ...] = ()
    negative_examples: tuple[str, ...] = ()
    direction_support: tuple[str, ...] = ("bullish", "bearish", "neutral")
    temporal_behavior: str = "current_candle"
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    conflicts_with: tuple[str, ...] = ()
    composes_with: tuple[str, ...] = ()
    provider_requirements: tuple[str, ...] = ()
    capability_version: str = "1.0"
    proof_template: str = ""
    resource_cost: str = "low"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["parameters"] = [parameter.to_dict() for parameter in self.parameters]
        payload["display_name"] = self.label
        payload["prompt_aliases"] = list(self.aliases)
        payload["example_sentence"] = self.examples[0] if self.examples else self.description
        payload["visual_card_sentence"] = self.visual_card_sentence or self.description
        payload["implementation_status"] = (
            "implemented"
            if self.executable and self.availability == "available"
            else self.availability
            if self.availability != "available"
            else "recognized_not_executable"
        )
        payload["evaluator_function"] = self.evaluator_function or self.operand_name
        payload["builder_category"] = self.builder_category or _builder_category(self)
        payload["provider_badge"] = self.provider_required or "OHLCV"
        return payload


@dataclass(frozen=True, slots=True)
class SynonymSpec:
    phrase: str
    maps_to: str
    default_parameters: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.8
    clarification_required: bool = False
    light_mode: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _semantic_tags(
    key: str,
    label: str,
    category: str,
    description: str,
    aliases: tuple[str, ...],
    explicit: tuple[str, ...],
) -> tuple[str, ...]:
    text = " ".join((key.replace("_", " "), label, category, description, *aliases)).casefold()
    tags = [category, *explicit]
    tag_terms = {
        "sweep": ("sweep", "stop hunt", "liquidity"),
        "momentum": ("momentum", "rsi", "stochastic", "macd", "oscillator"),
        "volatility": ("volatility", "atr", "bollinger", "keltner", "range expansion"),
        "reversal": ("reversal", "reclaim", "rejection", "engulf", "hammer", "fakeout"),
        "trend": ("trend", "moving average", "ema", "sma", "supertrend", "ichimoku"),
        "session": ("session", "timezone", "midnight", "killzone", "time window"),
    }
    for tag, terms in tag_terms.items():
        if any(term in text for term in terms):
            tags.append(tag)
    return tuple(dict.fromkeys(tag.strip().casefold() for tag in tags if tag.strip()))


def _direction_support(text: str, explicit: tuple[str, ...]) -> tuple[str, ...]:
    if explicit:
        return explicit
    lowered = text.casefold()
    bullish = any(
        term in lowered
        for term in ("bullish", "upside", "sweep low", "sweep lows", "above", "reclaim")
    )
    bearish = any(
        term in lowered
        for term in ("bearish", "downside", "sweep high", "sweep highs", "below", "breakdown")
    )
    if bullish and not bearish:
        return ("bullish",)
    if bearish and not bullish:
        return ("bearish",)
    return ("bullish", "bearish", "neutral")


def _temporal_behavior(text: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    lowered = text.casefold()
    if "previous candle" in lowered or "prior candle" in lowered:
        return "previous_candle"
    if any(term in lowered for term in ("within", "lookback", "sequence", "persist", "count")):
        return "within_n_candles"
    return "current_candle"


def _json_type(parameter_type: str) -> str:
    return {
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "choice": "string",
        "timeframe": "string",
    }.get(parameter_type, "string")


def _parameter_schema(
    parameters: tuple[CapabilityParameter, ...],
    defaults: dict[str, Any],
    default_threshold: Any,
) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for parameter in parameters:
        item: dict[str, Any] = {
            "type": _json_type(parameter.type),
            "description": parameter.description,
            "x-semantic-unit": _parameter_semantic_unit(parameter.name),
            # The words that can name this role. The registry owns them so every reader
            # shares one vocabulary instead of hand-writing a subset that drifts.
            "x-source-aliases": list(
                dict.fromkeys(
                    (*parameter.source_aliases, *_role_aliases(parameter.name))
                )
            ),
            "x-requires-role-phrase": parameter.requires_role_phrase,
        }
        if parameter.default is not None:
            item["default"] = parameter.default
        if parameter.options:
            item["enum"] = list(parameter.options)
        properties[parameter.name] = item
        if parameter.required:
            required.append(parameter.name)
    for name, value in defaults.items():
        properties.setdefault(
            name,
            {
                "type": (
                    "boolean"
                    if isinstance(value, bool)
                    else "integer"
                    if isinstance(value, int)
                    else "number"
                    if isinstance(value, float)
                    else "string"
                ),
                "default": value,
            },
        )
    if default_threshold is not None and not isinstance(default_threshold, bool):
        properties.setdefault(
            "threshold",
            {"type": "number", "default": default_threshold},
        )
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


#: Everyday words for the roles the platform's own parameter names use. Keyed by the
#: registry's parameter name, so a capability author gets the vocabulary for free and a
#: capability with an unusual name can still add its own via ``source_aliases``.
#:
#: These are role *names*, not conditions or thresholds — nothing here decides what a
#: rule does. They only answer "which parameter is this number".
_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "period": ("period", "length", "setting", "over", "fatra", "mudda"),
    "lookback": ("lookback", "look back", "previous", "back", "ago", "candles back"),
    "window": ("window", "range", "span", "over the last", "within"),
    "candles": ("candles", "bars", "candlesticks", "sham3a", "shumu3"),
    "confirmation_candles": (
        "confirmation",
        "confirm",
        "consecutive",
        "in a row",
        "closes",
        "ta2keed",
    ),
    "threshold": ("threshold", "level", "value", "at", "mustawa"),
    "multiplier": ("multiplier", "times", "x", "multiple"),
    "deviation": ("deviation", "standard deviation", "std", "sigma"),
}


def _role_aliases(name: str) -> tuple[str, ...]:
    """Words that can name this parameter's role, from the shared vocabulary above."""

    lowered = name.casefold()
    aliases = list(_ROLE_ALIASES.get(lowered, ()))
    # A compound name carries its parts' vocabulary: `signal_period` is still a period.
    for part in lowered.split("_"):
        aliases.extend(_ROLE_ALIASES.get(part, ()))
    aliases.append(lowered.replace("_", " "))
    return tuple(dict.fromkeys(item for item in aliases if item))


def _parameter_semantic_unit(name: str) -> str:
    """Registry-owned unit used when grounding trader-controlled values."""

    lowered = name.casefold()
    if lowered in {"period", "lookback", "window", "candles", "length"}:
        return "count"
    if "percent" in lowered or lowered.endswith("_pct"):
        return "percent"
    if lowered in {"price", "price_level", "level"}:
        return "price"
    if "multiplier" in lowered or lowered.endswith("_multiple"):
        return "multiple"
    if "timeframe" in lowered:
        return "timeframe"
    if "symbol" in lowered:
        return "symbol"
    return "plain"


def _resource_cost(warmup_candles: int, provider_required: str | None) -> str:
    if provider_required or warmup_candles > 500:
        return "high"
    if warmup_candles > 100:
        return "medium"
    return "low"


def _cap(
    key: str,
    label: str,
    category: CapabilityCategory,
    condition_type: str,
    description: str,
    *,
    aliases: tuple[str, ...] = (),
    operand_kind: str | None = None,
    operand_name: str | None = None,
    default_parameters: dict[str, Any] | None = None,
    default_comparator: Any = _NOT_DECLARED,
    default_threshold: Any = _NOT_DECLARED,
    required_data: tuple[str, ...] = ("ohlcv",),
    light_mode: bool = True,
    free_plan: bool = True,
    deterministic: bool = True,
    requires_higher_timeframe: bool = False,
    optional_only: bool = False,
    executable: bool = True,
    guidance: str | None = None,
    parameters: tuple[CapabilityParameter, ...] = (),
    examples: tuple[str, ...] = (),
    supported_markets: tuple[str, ...] = ("spot",),
    supported_timeframes: tuple[str, ...] = (
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "6h",
        "8h",
        "12h",
        "1d",
    ),
    outputs: tuple[str, ...] = ("value",),
    supported_comparators: tuple[str, ...] | None = None,
    visual_card_sentence: str | None = None,
    risk_notes: str = "",
    evaluator_function: str | None = None,
    warmup_candles: int = 1,
    test_cases: tuple[str, ...] = (),
    builder_category: str | None = None,
    beginner_friendly: bool = False,
    provider_required: str | None = None,
    availability: str = "available",
    phase: int = 1,
    approximation: bool = False,
    approximation_note: str = "",
    semantic_tags: tuple[str, ...] = (),
    intent_examples: tuple[str, ...] = (),
    negative_examples: tuple[str, ...] = (),
    direction_support: tuple[str, ...] = (),
    temporal_behavior: str | None = None,
    parameter_schema: dict[str, Any] | None = None,
    conflicts_with: tuple[str, ...] = (),
    composes_with: tuple[str, ...] = (),
    provider_requirements: tuple[str, ...] = (),
    capability_version: str = "1.0",
    proof_template: str | None = None,
    resource_cost: str | None = None,
) -> CapabilitySpec:
    searchable_text = " ".join((key, label, category, description, *aliases, *examples))
    resolved_comparator, supported_comparators = _resolve_comparison(
        key=key,
        declared_comparator=default_comparator,
        declared_supported=supported_comparators,
        operand_kind=operand_kind,
    )
    default_threshold = _resolve_threshold(
        declared=default_threshold,
        comparator=resolved_comparator,
    )
    return CapabilitySpec(
        key=key,
        label=label,
        category=category,
        condition_type=condition_type,
        description=description,
        aliases=aliases,
        operand_kind=operand_kind,
        operand_name=operand_name,
        default_parameters=default_parameters or {},
        default_comparator=resolved_comparator,
        default_threshold=default_threshold,
        required_data=required_data,
        light_mode=light_mode,
        free_plan=free_plan,
        deterministic=deterministic,
        requires_higher_timeframe=requires_higher_timeframe,
        optional_only=optional_only,
        executable=executable,
        guidance=guidance,
        parameters=parameters,
        examples=examples,
        supported_markets=supported_markets,
        supported_timeframes=supported_timeframes,
        outputs=outputs,
        supported_comparators=supported_comparators,
        visual_card_sentence=visual_card_sentence,
        risk_notes=risk_notes,
        evaluator_function=evaluator_function,
        warmup_candles=warmup_candles,
        test_cases=test_cases,
        builder_category=builder_category,
        beginner_friendly=beginner_friendly,
        provider_required=provider_required,
        availability=availability,
        phase=phase,
        approximation=approximation,
        approximation_note=approximation_note,
        semantic_tags=_semantic_tags(key, label, category, description, aliases, semantic_tags),
        intent_examples=(intent_examples or examples or aliases[:3] or (description,)),
        negative_examples=negative_examples,
        direction_support=_direction_support(searchable_text, direction_support),
        temporal_behavior=_temporal_behavior(searchable_text, temporal_behavior),
        parameter_schema=(
            parameter_schema
            or _parameter_schema(parameters, default_parameters or {}, default_threshold)
        ),
        conflicts_with=conflicts_with,
        composes_with=composes_with,
        provider_requirements=(
            provider_requirements or ((provider_required,) if provider_required else ())
        ),
        capability_version=capability_version,
        proof_template=(proof_template or visual_card_sentence or description),
        resource_cost=(resource_cost or _resource_cost(warmup_candles, provider_required)),
    )


def _resolve_comparison(
    *,
    key: str,
    declared_comparator: Any,
    declared_supported: tuple[str, ...] | None,
    operand_kind: str | None,
) -> tuple[str, tuple[str, ...]]:
    """Settle, once, which comparisons a capability accepts and which it starts on.

    These two facts describe the same thing, and until now they were declared
    separately and were free to disagree. 149 capabilities did disagree: they said
    "my comparison is `is_true`" while their list of allowed comparisons held only
    numeric ones. Nothing raised — the template builder quietly resolved the
    contradiction by rewriting `is_true` into `>= 0`, which is true of every number
    there is, so a yes/no rule became a rule that matches everything.

    Deciding both here means the contradiction cannot be written down any more.

    The order of preference is deliberate:

    1. What the author explicitly wrote wins, and if the two explicit statements
       disagree that is an error in the registry, raised at import.
    2. A capability that declared only its comparison gets the list that fits it.
    3. A capability that declared neither is read from its operand kind, which is the
       rule this file already used and the only signal available for it.
    """

    declared = declared_comparator is not _NOT_DECLARED
    comparator = str(declared_comparator) if declared else None

    if declared_supported is not None:
        supported = tuple(declared_supported)
        if comparator is not None and comparator not in supported:
            raise ValueError(
                f"capability {key!r} declares default_comparator={comparator!r} but does "
                f"not list it in supported_comparators={supported!r}. One of the two is "
                "wrong; the registry will not choose between them."
            )
        if comparator is None:
            comparator = _natural_comparator(supported)
        return comparator, supported

    if comparator is not None:
        supported = (
            tuple(sorted(UNARY_COMPARATORS))
            if comparator in UNARY_COMPARATORS
            else MEASURED_COMPARATORS
        )
        return comparator, supported

    if operand_kind in _BOOLEAN_OPERAND_KINDS:
        # A pattern match is a yes/no answer, so "greater than" has no meaning for it.
        return Comparator.IS_TRUE.value, tuple(sorted(UNARY_COMPARATORS))
    if operand_kind == "indicator":
        # An indicator produces a number, and a number is compared against one.
        return Comparator.GREATER_THAN_OR_EQUAL.value, MEASURED_COMPARATORS

    # Everything else has to say. A metric read from a provider or from the risk
    # context can be either a flag or a measurement, and there is no property of the
    # capability that says which — the old code answered "yes/no" for all of them by
    # accident, through a parameter default, and a later reader turned that answer
    # into ">= 0". Refusing here costs one line in the registry and makes the mistake
    # impossible to repeat silently.
    raise ValueError(
        f"capability {key!r} (operand_kind={operand_kind!r}) does not say how it is "
        "compared. Add default_comparator='is_true' if it answers yes or no, or a "
        "measured comparison such as 'gte' if it produces a number to compare."
    )


def _natural_comparator(supported: tuple[str, ...]) -> str:
    """The comparison a capability starts on, given only the list it allows."""

    if not supported:
        raise ValueError("a capability must allow at least one comparison")
    for preferred in (Comparator.IS_TRUE.value, Comparator.GREATER_THAN_OR_EQUAL.value):
        if preferred in supported:
            return preferred
    return supported[0]


def _resolve_threshold(*, declared: Any, comparator: str) -> Any:
    """The level a capability starts on, or nothing when a level makes no sense.

    A yes/no comparison takes no right-hand side at all, so its level is irrelevant.
    A measured comparison needs a real number; the bare ``True`` that used to be the
    registry-wide default is not one, and writing it into a rule produced a comparison
    against a boolean. Undeclared stays undeclared, so a reader can offer an empty box
    rather than a number nobody chose.
    """

    if comparator in UNARY_COMPARATORS:
        return True
    if declared is _NOT_DECLARED:
        return None
    if isinstance(declared, bool):
        return None
    return declared


def _builder_category(capability: CapabilitySpec) -> str:
    if capability.category == "candle_pattern":
        return "candle_pattern"
    if capability.category == "price_action":
        if any(term in capability.key for term in ("structure", "swing", "character")):
            return "market_structure"
        if any(
            term in capability.key
            for term in ("liquidity", "sweep", "order_block", "fair_value", "fvg")
        ):
            return "liquidity_smart_money"
        return "price_action"
    return {
        "trend": "trend",
        "momentum": "momentum",
        "volatility": "volatility_squeeze",
        "volume_liquidity": "volume_flow",
        "market_filter": "market_context",
        "risk": "risk_trade_quality",
        "session_time": "time_session",
        "advanced": "advanced_logic",
        "indicator": "indicator",
    }.get(capability.category, "indicator")


PERIOD = CapabilityParameter("period", "integer", 20, False, "Indicator lookback period.")
LOOKBACK = CapabilityParameter("lookback", "integer", 20, False, "Closed-candle lookback.")
THRESHOLD = CapabilityParameter("threshold", "number", None, True, "Required threshold.")
TIMEFRAME = CapabilityParameter("timeframe", "timeframe", "15m", False, "Evaluation timeframe.")
PATTERN_LOOKBACK = CapabilityParameter(
    "lookback", "integer", 80, False, "Closed-candle pattern search window."
)
PIVOT_BARS = CapabilityParameter(
    "pivot_bars", "integer", 2, False, "Bars on each side required to confirm a pivot."
)
BREAKOUT_BUFFER = CapabilityParameter(
    "breakout_buffer_percent",
    "number",
    0.0,
    False,
    "Extra close distance beyond the neckline or pattern boundary.",
)


CAPABILITIES: tuple[CapabilitySpec, ...] = (
    # Trend and moving average logic
    _cap(
        "ema_crossover",
        "EMA crossover",
        "trend",
        "indicator",
        "Fast EMA crosses slow EMA.",
        aliases=("ema cross", "golden cross", "death cross"),
        operand_kind="indicator",
        operand_name="ema",
        default_comparator="crosses_above",
        parameters=(PERIOD, TIMEFRAME),
    ),
    _cap(
        "sma_crossover",
        "SMA crossover",
        "trend",
        "indicator",
        "Fast SMA crosses slow SMA.",
        aliases=("sma cross", "moving average cross"),
        operand_kind="indicator",
        operand_name="sma",
        default_comparator="crosses_above",
        parameters=(PERIOD, TIMEFRAME),
    ),
    _cap(
        "price_above_ema",
        "Price above EMA",
        "trend",
        "indicator",
        "Close is above a configured EMA.",
        aliases=("above ema", "trend above ema"),
        operand_kind="price",
        operand_name="close",
        default_comparator="gt",
        parameters=(PERIOD, TIMEFRAME),
    ),
    _cap(
        "price_below_ema",
        "Price below EMA",
        "trend",
        "indicator",
        "Close is below a configured EMA.",
        aliases=("below ema", "under ema"),
        operand_kind="price",
        operand_name="close",
        default_comparator="lt",
        parameters=(PERIOD, TIMEFRAME),
    ),
    _cap(
        "price_above_sma",
        "Price above SMA",
        "trend",
        "indicator",
        "Close is above a configured SMA.",
        aliases=("above sma",),
        operand_kind="price",
        operand_name="close",
        default_comparator="gt",
        parameters=(PERIOD, TIMEFRAME),
    ),
    _cap(
        "price_below_sma",
        "Price below SMA",
        "trend",
        "indicator",
        "Close is below a configured SMA.",
        aliases=("below sma",),
        operand_kind="price",
        operand_name="close",
        default_comparator="lt",
        parameters=(PERIOD, TIMEFRAME),
    ),
    _cap(
        "ema_slope",
        "EMA slope",
        "trend",
        "indicator",
        "EMA slope is positive or negative.",
        aliases=("ema rising", "ema falling"),
        operand_kind="indicator",
        operand_name="ema_slope",
        default_comparator="gt",
        default_threshold=0,
        parameters=(PERIOD, TIMEFRAME),
    ),
    _cap(
        "sma_slope",
        "SMA slope",
        "trend",
        "indicator",
        "SMA slope is positive or negative.",
        aliases=("sma rising", "sma falling"),
        operand_kind="indicator",
        operand_name="sma_slope",
        default_comparator="gt",
        default_threshold=0,
        parameters=(PERIOD, TIMEFRAME),
    ),
    _cap(
        "ema_stack",
        "EMA stack",
        "trend",
        "indicator",
        "Multiple EMAs are ordered bullish or bearish.",
        aliases=("ema ribbon", "ema alignment"),
        operand_kind="indicator",
        operand_name="moving_average_ribbon",
        default_parameters={"periods": "10,20,50,100", "component": "bullish_stack"},
        default_comparator="eq",
        default_threshold=1,
        parameters=(
            CapabilityParameter("periods", "string", "10,20,50,100"),
            CapabilityParameter(
                "component",
                "choice",
                "bullish_stack",
                options=(
                    "bullish_stack",
                    "bearish_stack",
                    "compression",
                    "expansion",
                    "spread_percent",
                ),
            ),
            TIMEFRAME,
        ),
        outputs=(
            "bullish_stack",
            "bearish_stack",
            "compression",
            "expansion",
            "spread_percent",
        ),
        warmup_candles=101,
    ),
    _cap(
        "ma_reclaim",
        "Moving average reclaim",
        "trend",
        "indicator",
        "Close crosses back above a moving average.",
        aliases=("reclaim ema", "reclaim moving average"),
        operand_kind="price",
        operand_name="close",
        default_comparator="crosses_above",
        parameters=(PERIOD, TIMEFRAME),
    ),
    _cap(
        "ma_retest",
        "Moving average retest",
        "trend",
        "price_action",
        "Price retests an EMA/SMA and closes back in trend direction.",
        aliases=("ema retest", "moving average retest"),
        operand_kind="price_action",
        operand_name="pullback_to_ema",
        parameters=(PERIOD, TIMEFRAME),
    ),
    _cap(
        "ma_distance_percent",
        "Distance from MA percent",
        "trend",
        "indicator",
        "Percent distance between close and EMA/SMA.",
        aliases=("distance from ema", "extended from moving average"),
        operand_kind="indicator",
        operand_name="moving_average_distance_percent",
        default_comparator="lte",
        parameters=(PERIOD, THRESHOLD, TIMEFRAME),
    ),
    # Momentum
    _cap(
        "rsi_threshold",
        "RSI threshold",
        "momentum",
        "indicator",
        "RSI is above or below a configured level.",
        aliases=("rsi above", "rsi below"),
        operand_kind="indicator",
        operand_name="rsi",
        default_comparator="gte",
        default_threshold=50,
        parameters=(CapabilityParameter("period", "integer", 14), THRESHOLD, TIMEFRAME),
    ),
    _cap(
        "rsi_cross",
        "RSI cross",
        "momentum",
        "indicator",
        "RSI crosses above or below a configured level.",
        aliases=("rsi crosses", "rsi crosses back"),
        operand_kind="indicator",
        operand_name="rsi",
        default_comparator="crosses_above",
        default_threshold=30,
        parameters=(TIMEFRAME,),
    ),
    _cap(
        "rsi_exits_oversold",
        "RSI exits oversold",
        "momentum",
        "indicator",
        "RSI crosses above 30 after oversold.",
        aliases=("exits oversold", "crosses back above 30"),
        operand_kind="indicator",
        operand_name="rsi",
        default_comparator="crosses_above",
        default_threshold=30,
    ),
    _cap(
        "rsi_exits_overbought",
        "RSI exits overbought",
        "momentum",
        "indicator",
        "RSI crosses below 70 after overbought.",
        aliases=("exits overbought", "crosses back below 70"),
        operand_kind="indicator",
        operand_name="rsi",
        default_comparator="crosses_below",
        default_threshold=70,
    ),
    _cap(
        "rsi_divergence",
        "RSI divergence",
        "momentum",
        "price_action",
        "Bullish or bearish RSI divergence recognition.",
        aliases=("bullish divergence", "bearish divergence"),
        operand_kind="price_action",
        operand_name="rsi_divergence",
        default_parameters={"direction": "bullish", "lookback": 60, "rsi_period": 14},
        guidance="Uses deterministic pivot pairing between price and RSI.",
    ),
    _cap(
        "macd_line_cross_signal",
        "MACD line crosses signal",
        "momentum",
        "indicator",
        "MACD line crosses MACD signal.",
        aliases=("macd cross", "macd bullish cross"),
        operand_kind="indicator",
        operand_name="macd",
        default_comparator="crosses_above",
    ),
    _cap(
        "macd_histogram_flip",
        "MACD histogram flips",
        "momentum",
        "indicator",
        "MACD histogram crosses zero.",
        aliases=("macd histogram turns positive", "macd histogram turns negative"),
        operand_kind="indicator",
        operand_name="macd",
        default_comparator="crosses_above",
        default_threshold=0,
    ),
    _cap(
        "macd_histogram_slope",
        "MACD histogram slope",
        "momentum",
        "indicator",
        "MACD histogram is increasing or decreasing.",
        aliases=("macd histogram increasing", "macd histogram decreasing"),
        operand_kind="indicator",
        operand_name="macd_histogram_delta",
        default_comparator="gt",
        default_threshold=0,
    ),
    _cap(
        "stochastic_kd_cross",
        "Stochastic K/D cross",
        "momentum",
        "indicator",
        "Stochastic K crosses D.",
        aliases=("stoch cross", "stochastic cross"),
        operand_kind="indicator",
        operand_name="stochastic",
        default_comparator="crosses_above",
    ),
    _cap(
        "stochastic_exit",
        "Stochastic overbought/oversold exit",
        "momentum",
        "indicator",
        "Stochastic exits oversold or overbought.",
        aliases=("stochastic oversold exit", "stochastic overbought exit"),
        operand_kind="indicator",
        operand_name="stochastic",
        default_comparator="crosses_above",
        default_threshold=20,
    ),
    _cap(
        "adx_trend_strength",
        "ADX trend strength",
        "momentum",
        "indicator",
        "ADX exceeds a trend-strength threshold.",
        aliases=("strong trend", "adx above"),
        operand_kind="indicator",
        operand_name="adx",
        default_comparator="gte",
        default_threshold=20,
    ),
    # Volatility and bands
    _cap(
        "bollinger_touch",
        "Bollinger band touch",
        "volatility",
        "indicator",
        "High or low touches a Bollinger band.",
        aliases=("bollinger touch", "touch upper band", "touch lower band"),
        operand_kind="price",
        operand_name="high",
        default_comparator="gte",
    ),
    _cap(
        "bollinger_close_outside",
        "Bollinger close outside band",
        "volatility",
        "indicator",
        "Close finishes outside upper or lower Bollinger band.",
        aliases=("close outside bollinger", "outside band"),
        operand_kind="price",
        operand_name="close",
        default_comparator="gt",
    ),
    _cap(
        "bollinger_reentry",
        "Bollinger re-entry",
        "volatility",
        "price_action",
        "Price closes back inside Bollinger bands after closing outside.",
        aliases=("reentry into bands", "bollinger re-entry"),
        operand_kind="price_action",
        operand_name="bollinger_reentry",
    ),
    _cap(
        "bollinger_bandwidth_expansion",
        "Bollinger bandwidth expansion",
        "volatility",
        "indicator",
        "Bollinger bandwidth is expanding.",
        aliases=("bandwidth expansion",),
        operand_kind="indicator",
        operand_name="bollinger_bandwidth_delta",
        default_comparator="gt",
        default_threshold=0,
    ),
    _cap(
        "bollinger_squeeze",
        "Bollinger squeeze",
        "volatility",
        "indicator",
        "Bollinger Bands are inside Keltner Channels or have just fired.",
        aliases=("squeeze", "squeezing", "coin is squeezing", "bb squeeze"),
        operand_kind="indicator",
        operand_name="squeeze_detection",
        default_parameters={"component": "squeeze_on"},
        default_comparator="eq",
        default_threshold=1,
        outputs=(
            "squeeze_on",
            "squeeze_off",
            "squeeze_fired",
            "bullish_fire",
            "bearish_fire",
        ),
        parameters=(
            CapabilityParameter("bb_period", "integer", 20),
            CapabilityParameter("kc_ema_period", "integer", 20),
            CapabilityParameter("kc_atr_period", "integer", 10),
            CapabilityParameter("kc_multiplier", "number", 1.5),
            CapabilityParameter(
                "component",
                "choice",
                "squeeze_on",
                options=(
                    "squeeze_on",
                    "squeeze_off",
                    "squeeze_fired",
                    "bullish_fire",
                    "bearish_fire",
                ),
            ),
        ),
        warmup_candles=22,
    ),
    _cap(
        "atr_threshold",
        "ATR threshold",
        "volatility",
        "indicator",
        "ATR is above or below a configured value.",
        aliases=("atr above", "atr below"),
        operand_kind="indicator",
        operand_name="atr",
        default_comparator="gte",
    ),
    _cap(
        "atr_percent",
        "ATR percent of price",
        "volatility",
        "indicator",
        "ATR as a percent of close.",
        aliases=("atr percent", "atr percentage"),
        operand_kind="indicator",
        operand_name="atr_percent",
        default_comparator="gte",
    ),
    _cap(
        "volatility_contraction",
        "Volatility contraction",
        "volatility",
        "price_action",
        "Recent range is tightly compressed.",
        aliases=("volatility contraction", "range contraction"),
        operand_kind="price_action",
        operand_name="tight_consolidation",
    ),
    _cap(
        "range_expansion_candle",
        "Range expansion candle",
        "volatility",
        "price_action",
        "Current candle range is larger than average.",
        aliases=("range expansion", "wide range candle"),
        operand_kind="price_action",
        operand_name="range_expansion",
    ),
    _cap(
        "atr_stop",
        "ATR stop placement",
        "risk",
        "risk",
        "Stop can be placed by ATR multiple.",
        aliases=("atr stop", "average true range stop"),
        operand_kind="risk_metric",
        operand_name="atr_stop",
        # "can be placed" — this answers yes or no. It does not produce a number.
        default_comparator="is_true",
    ),
    # Volume and liquidity
    _cap(
        "volume_ratio",
        "Volume ratio",
        "volume_liquidity",
        "indicator",
        "Volume is above or below its average.",
        aliases=(
            "relative volume",
            "volume multiplier",
            "times average",
            "volume above average",
            "volume x average",
        ),
        operand_kind="indicator",
        operand_name="volume_ratio",
        default_comparator="gte",
        default_threshold=1.5,
        intent_examples=(
            "Volume is at least 1.5x its average.",
            "Find symbols with volume above the recent candle average.",
        ),
    ),
    _cap(
        "relative_volume_rising",
        "Relative volume rising",
        "volume_liquidity",
        "indicator",
        "Relative volume is increasing.",
        aliases=("relative volume rising", "rvol rising"),
        operand_kind="indicator",
        operand_name="relative_volume_slope",
        default_comparator="gt",
        default_threshold=0,
    ),
    _cap(
        "volume_spike",
        "Volume spike",
        "volume_liquidity",
        "indicator",
        "Volume is far above average.",
        aliases=("volume spike", "strong volume", "pump volume", "volume burst"),
        operand_kind="indicator",
        operand_name="volume_ratio",
        default_comparator="gte",
        default_threshold=1.8,
    ),
    _cap(
        "volume_dry_up",
        "Volume dry-up",
        "volume_liquidity",
        "indicator",
        "Volume is below average during pullback.",
        aliases=("low volume pullback", "volume dry up"),
        operand_kind="indicator",
        operand_name="volume_ratio",
        default_comparator="lte",
        default_threshold=0.8,
    ),
    _cap(
        "volume_breakout_confirmation",
        "Volume breakout confirmation",
        "volume_liquidity",
        "indicator",
        "Breakout is confirmed by relative volume.",
        aliases=("volume breakout", "breakout volume"),
        operand_kind="indicator",
        operand_name="volume_ratio",
        default_comparator="gte",
        default_threshold=1.5,
    ),
    _cap(
        "vwap_reclaim",
        "VWAP reclaim",
        "volume_liquidity",
        "indicator",
        "Price crosses back above VWAP.",
        aliases=("vwap reclaim", "reclaim vwap"),
        operand_kind="price",
        operand_name="close",
        default_comparator="crosses_above",
    ),
    _cap(
        "price_vs_vwap",
        "Price above or below VWAP",
        "volume_liquidity",
        "indicator",
        "Close is above or below VWAP.",
        aliases=("above vwap", "below vwap"),
        operand_kind="price",
        operand_name="close",
        default_comparator="gt",
    ),
    _cap(
        "vwap_deviation_percent",
        "VWAP deviation percent",
        "volume_liquidity",
        "indicator",
        "Percent distance between close and VWAP.",
        aliases=("vwap deviation",),
        operand_kind="indicator",
        operand_name="vwap_deviation_percent",
        default_comparator="lte",
    ),
    _cap(
        "min_quote_volume_24h",
        "Minimum 24h quote volume",
        "market_filter",
        "market_filter",
        "Market must meet 24h quote-volume minimum.",
        aliases=("24h volume", "minimum quote volume", "avoid low liquidity"),
        operand_kind="market_metric",
        operand_name="quote_volume_24h",
        # "must meet a minimum" — a traded volume compared against a number.
        default_comparator="gte",
        required_data=("ticker",),
    ),
    _cap(
        "min_average_candle_volume",
        "Minimum average candle volume",
        "market_filter",
        "market_filter",
        "Average candle volume must meet minimum.",
        aliases=("average candle volume",),
        operand_kind="market_metric",
        operand_name="average_volume",
        # "must meet minimum" — a measured volume against a number.
        default_comparator="gte",
    ),
    # Price action
    _cap(
        "bullish_liquidity_sweep",
        "Bullish liquidity sweep",
        "price_action",
        "price_action",
        "Low sweeps prior lows and closes back above.",
        aliases=("sweep lows", "previous low sweep", "stop hunt lows"),
        operand_kind="price_action",
        operand_name="sell_side_liquidity_sweep",
    ),
    _cap(
        "bearish_liquidity_sweep",
        "Bearish liquidity sweep",
        "price_action",
        "price_action",
        "High sweeps prior highs and closes back below.",
        aliases=("sweep highs", "previous high sweep", "stop hunt highs"),
        operand_kind="price_action",
        operand_name="buy_side_liquidity_sweep",
    ),
    _cap(
        "break_of_structure_bullish",
        "Bullish break of structure",
        "price_action",
        "price_action",
        "Close breaks above prior swing structure.",
        aliases=("bullish bos", "break of structure bullish"),
        operand_kind="price_action",
        operand_name="market_structure_shift_bullish",
    ),
    _cap(
        "break_of_structure_bearish",
        "Bearish break of structure",
        "price_action",
        "price_action",
        "Close breaks below prior swing structure.",
        aliases=("bearish bos", "break of structure bearish"),
        operand_kind="price_action",
        operand_name="market_structure_shift_bearish",
    ),
    _cap(
        "change_of_character_bullish",
        "Bullish change of character",
        "price_action",
        "price_action",
        "Close reclaims prior structure after bearish pressure.",
        aliases=("bullish choch", "change of character bullish"),
        operand_kind="price_action",
        operand_name="market_structure_shift_bullish",
    ),
    _cap(
        "change_of_character_bearish",
        "Bearish change of character",
        "price_action",
        "price_action",
        "Close loses prior structure after bullish pressure.",
        aliases=("bearish choch", "change of character bearish"),
        operand_kind="price_action",
        operand_name="market_structure_shift_bearish",
    ),
    _cap(
        "higher_high",
        "Higher high",
        "price_action",
        "price_action",
        "Latest high exceeds lookback high.",
        aliases=("new high", "higher high"),
        operand_kind="price_action",
        operand_name="higher_high",
    ),
    _cap(
        "higher_low",
        "Higher low",
        "price_action",
        "price_action",
        "Latest low remains above lookback low.",
        aliases=("higher low",),
        operand_kind="price_action",
        operand_name="higher_low",
    ),
    _cap(
        "lower_high",
        "Lower high",
        "price_action",
        "price_action",
        "Latest high remains below lookback high.",
        aliases=("lower high",),
        operand_kind="price_action",
        operand_name="lower_high",
    ),
    _cap(
        "lower_low",
        "Lower low",
        "price_action",
        "price_action",
        "Latest low breaks lookback low.",
        aliases=("lower low", "new low"),
        operand_kind="price_action",
        operand_name="lower_low",
    ),
    _cap(
        "range_breakout",
        "Range breakout",
        "price_action",
        "price_action",
        "Close breaks above recent range high.",
        aliases=("breakout", "range breakout"),
        operand_kind="price_action",
        operand_name="breakout_from_consolidation",
    ),
    _cap(
        "range_breakdown",
        "Range breakdown",
        "price_action",
        "price_action",
        "Close breaks below recent range low.",
        aliases=("breakdown", "range breakdown"),
        operand_kind="price_action",
        operand_name="breakdown_from_consolidation",
    ),
    _cap(
        "breakout_retest",
        "Retest of breakout level",
        "price_action",
        "price_action",
        "Price retests a prior breakout level and holds.",
        aliases=("breakout retest", "retest of breakout"),
        operand_kind="price_action",
        operand_name="retest_after_breakout",
    ),
    _cap(
        "support_retest",
        "Support retest",
        "price_action",
        "price_action",
        "Price retests support and closes above it.",
        aliases=("bounce", "support bounce", "support retest"),
        operand_kind="price_action",
        operand_name="price_bounces_from_support",
    ),
    _cap(
        "resistance_retest",
        "Resistance retest",
        "price_action",
        "price_action",
        "Price retests resistance and closes below it.",
        aliases=("reject", "resistance rejection", "resistance retest"),
        operand_kind="price_action",
        operand_name="price_rejects_resistance",
    ),
    _cap(
        "equal_highs",
        "Equal highs liquidity pool",
        "price_action",
        "price_action",
        "Recent highs cluster within tolerance.",
        aliases=("equal highs", "liquidity pool highs"),
        operand_kind="price_action",
        operand_name="equal_highs_liquidity_pool",
    ),
    _cap(
        "equal_lows",
        "Equal lows liquidity pool",
        "price_action",
        "price_action",
        "Recent lows cluster within tolerance.",
        aliases=("equal lows", "liquidity pool lows"),
        operand_kind="price_action",
        operand_name="equal_lows_liquidity_pool",
    ),
    _cap(
        "consolidation_range",
        "Consolidation range",
        "price_action",
        "price_action",
        "Recent range is narrow relative to price.",
        aliases=("consolidation", "tight range"),
        operand_kind="price_action",
        operand_name="tight_consolidation",
    ),
    _cap(
        "impulse_candle",
        "Impulse candle",
        "price_action",
        "price_action",
        "Current candle range and close location show impulse.",
        aliases=("impulse candle", "momentum candle", "strong candle"),
        operand_kind="price_action",
        operand_name="wide_range_candle",
    ),
    _cap(
        "pullback_depth_percent",
        "Pullback depth percent",
        "price_action",
        "indicator",
        "Pullback depth from recent swing high or low.",
        aliases=("pullback depth", "retracement percent"),
        operand_kind="indicator",
        operand_name="pullback_depth_percent",
    ),
    # Multi-candle technical chart patterns. These are geometric OHLCV definitions, not
    # discretionary labels or AI confidence scores.
    _cap(
        "head_and_shoulders_formed",
        "Head and shoulders structure formed",
        "price_action",
        "price_action",
        "Three confirmed swing highs form two similar shoulders around a higher head.",
        aliases=(
            "forming head and shoulders",
            "forming head and sholders",
            "head and shoulders pattern",
            "head and sholders pattern",
            "head shoulders forming",
            "h&s forming",
            "h&s pattern",
        ),
        operand_kind="price_action",
        operand_name="head_and_shoulders_formed",
        default_parameters={
            "lookback": 80,
            "pivot_bars": 2,
            "shoulder_tolerance_percent": 5.0,
            "head_prominence_percent": 1.0,
            "maximum_spacing_ratio": 3.0,
            "breakout_buffer_percent": 0.0,
        },
        parameters=(
            PATTERN_LOOKBACK,
            PIVOT_BARS,
            CapabilityParameter("shoulder_tolerance_percent", "number", 5.0),
            CapabilityParameter("head_prominence_percent", "number", 1.0),
            CapabilityParameter("maximum_spacing_ratio", "number", 3.0),
            BREAKOUT_BUFFER,
            TIMEFRAME,
        ),
        supported_comparators=("is_true", "is_false"),
        warmup_candles=90,
        semantic_tags=("technical_pattern", "reversal", "forming"),
        direction_support=("bearish",),
        temporal_behavior="within_n_candles",
        composes_with=("head_and_shoulders_neckline_break", "volume_ratio"),
        intent_examples=(
            "Monitor a forming head and shoulders pattern.",
            "Find head and sholders structures before the neckline breaks.",
        ),
        negative_examples=("Inverse head and shoulders is forming.",),
        proof_template=(
            "Head and shoulders structure: confirmed left shoulder, head, right shoulder, "
            "and projected neckline from closed candles."
        ),
        builder_category="price_action",
        beginner_friendly=True,
    ),
    _cap(
        "head_and_shoulders_neckline_break",
        "Head and shoulders neckline break",
        "price_action",
        "price_action",
        "A formed head and shoulders closes below its projected neckline.",
        aliases=(
            "head and shoulders neckline break",
            "head and sholders neckline break",
            "head shoulders neckline broken",
            "neckline is broken",
            "neckline broken",
            "break head and shoulders neckline",
            "h&s neckline break",
        ),
        operand_kind="price_action",
        operand_name="head_and_shoulders_neckline_break",
        default_parameters={
            "lookback": 80,
            "pivot_bars": 2,
            "shoulder_tolerance_percent": 5.0,
            "head_prominence_percent": 1.0,
            "maximum_spacing_ratio": 3.0,
            "breakout_buffer_percent": 0.0,
        },
        parameters=(
            PATTERN_LOOKBACK,
            PIVOT_BARS,
            CapabilityParameter("shoulder_tolerance_percent", "number", 5.0),
            CapabilityParameter("head_prominence_percent", "number", 1.0),
            CapabilityParameter("maximum_spacing_ratio", "number", 3.0),
            BREAKOUT_BUFFER,
            TIMEFRAME,
        ),
        supported_comparators=("is_true", "is_false"),
        warmup_candles=90,
        semantic_tags=("technical_pattern", "reversal", "confirmation"),
        direction_support=("bearish",),
        temporal_behavior="within_n_candles",
        composes_with=("head_and_shoulders_formed", "volume_ratio"),
        intent_examples=(
            "Alert once the head and shoulders neckline is broken.",
            "Confirm a head and sholders pattern on a neckline close below.",
        ),
        proof_template=(
            "Head and shoulders confirmation: actual close is below the projected neckline."
        ),
        builder_category="price_action",
        beginner_friendly=True,
    ),
    _cap(
        "inverse_head_and_shoulders_formed",
        "Inverse head and shoulders structure formed",
        "price_action",
        "price_action",
        "Three confirmed swing lows form two similar shoulders around a lower head.",
        aliases=(
            "forming inverse head and shoulders",
            "inverse head and sholders pattern",
            "inverse head shoulders forming",
            "inverse h&s forming",
        ),
        operand_kind="price_action",
        operand_name="inverse_head_and_shoulders_formed",
        default_parameters={
            "lookback": 80,
            "pivot_bars": 2,
            "shoulder_tolerance_percent": 5.0,
            "head_prominence_percent": 1.0,
            "maximum_spacing_ratio": 3.0,
            "breakout_buffer_percent": 0.0,
        },
        parameters=(
            PATTERN_LOOKBACK,
            PIVOT_BARS,
            CapabilityParameter("shoulder_tolerance_percent", "number", 5.0),
            CapabilityParameter("head_prominence_percent", "number", 1.0),
            CapabilityParameter("maximum_spacing_ratio", "number", 3.0),
            BREAKOUT_BUFFER,
            TIMEFRAME,
        ),
        supported_comparators=("is_true", "is_false"),
        warmup_candles=90,
        semantic_tags=("technical_pattern", "reversal", "forming"),
        direction_support=("bullish",),
        temporal_behavior="within_n_candles",
        composes_with=("inverse_head_and_shoulders_neckline_break", "volume_ratio"),
        proof_template=(
            "Inverse head and shoulders structure from three confirmed swing lows and a "
            "projected neckline."
        ),
        builder_category="price_action",
        beginner_friendly=True,
    ),
    _cap(
        "inverse_head_and_shoulders_neckline_break",
        "Inverse head and shoulders neckline break",
        "price_action",
        "price_action",
        "A formed inverse head and shoulders closes above its projected neckline.",
        aliases=(
            "inverse head and shoulders neckline break",
            "inverse head and sholders neckline break",
            "inverse neckline break",
            "inverse h&s neckline break",
        ),
        operand_kind="price_action",
        operand_name="inverse_head_and_shoulders_neckline_break",
        default_parameters={
            "lookback": 80,
            "pivot_bars": 2,
            "shoulder_tolerance_percent": 5.0,
            "head_prominence_percent": 1.0,
            "maximum_spacing_ratio": 3.0,
            "breakout_buffer_percent": 0.0,
        },
        parameters=(
            PATTERN_LOOKBACK,
            PIVOT_BARS,
            CapabilityParameter("shoulder_tolerance_percent", "number", 5.0),
            CapabilityParameter("head_prominence_percent", "number", 1.0),
            CapabilityParameter("maximum_spacing_ratio", "number", 3.0),
            BREAKOUT_BUFFER,
            TIMEFRAME,
        ),
        supported_comparators=("is_true", "is_false"),
        warmup_candles=90,
        semantic_tags=("technical_pattern", "reversal", "confirmation"),
        direction_support=("bullish",),
        temporal_behavior="within_n_candles",
        composes_with=("inverse_head_and_shoulders_formed", "volume_ratio"),
        proof_template=(
            "Inverse head and shoulders confirmation: actual close is above the projected "
            "neckline."
        ),
        builder_category="price_action",
        beginner_friendly=True,
    ),
    _cap(
        "double_top_neckline_break",
        "Double top neckline break",
        "price_action",
        "price_action",
        "Two similar confirmed highs form before price closes below the intervening low.",
        aliases=("double top", "double top break", "double top neckline break", "m pattern"),
        operand_kind="price_action",
        operand_name="double_top_neckline_break",
        default_parameters={
            "lookback": 80,
            "pivot_bars": 2,
            "level_tolerance_percent": 2.0,
            "minimum_depth_percent": 1.0,
            "breakout_buffer_percent": 0.0,
        },
        parameters=(
            PATTERN_LOOKBACK,
            PIVOT_BARS,
            CapabilityParameter("level_tolerance_percent", "number", 2.0),
            CapabilityParameter("minimum_depth_percent", "number", 1.0),
            BREAKOUT_BUFFER,
            TIMEFRAME,
        ),
        supported_comparators=("is_true", "is_false"),
        warmup_candles=90,
        semantic_tags=("technical_pattern", "reversal", "confirmation"),
        direction_support=("bearish",),
        temporal_behavior="within_n_candles",
        proof_template="Double top confirmation: close is below the intervening swing low.",
        builder_category="price_action",
        beginner_friendly=True,
    ),
    _cap(
        "double_bottom_neckline_break",
        "Double bottom neckline break",
        "price_action",
        "price_action",
        "Two similar confirmed lows form before price closes above the intervening high.",
        aliases=(
            "double bottom",
            "double bottom break",
            "double bottom neckline break",
            "w pattern",
        ),
        operand_kind="price_action",
        operand_name="double_bottom_neckline_break",
        default_parameters={
            "lookback": 80,
            "pivot_bars": 2,
            "level_tolerance_percent": 2.0,
            "minimum_depth_percent": 1.0,
            "breakout_buffer_percent": 0.0,
        },
        parameters=(
            PATTERN_LOOKBACK,
            PIVOT_BARS,
            CapabilityParameter("level_tolerance_percent", "number", 2.0),
            CapabilityParameter("minimum_depth_percent", "number", 1.0),
            BREAKOUT_BUFFER,
            TIMEFRAME,
        ),
        supported_comparators=("is_true", "is_false"),
        warmup_candles=90,
        semantic_tags=("technical_pattern", "reversal", "confirmation"),
        direction_support=("bullish",),
        temporal_behavior="within_n_candles",
        proof_template="Double bottom confirmation: close is above the intervening swing high.",
        builder_category="price_action",
        beginner_friendly=True,
    ),
    *(
        _cap(
            key,
            label,
            "price_action",
            "price_action",
            description,
            aliases=aliases,
            operand_kind="price_action",
            operand_name=key,
            default_parameters={
                "lookback": 80,
                "pivot_bars": 2,
                "flat_slope_percent_per_bar": 0.15,
                "minimum_slope_percent_per_bar": 0.02,
                "breakout_buffer_percent": 0.0,
            },
            parameters=(
                PATTERN_LOOKBACK,
                PIVOT_BARS,
                CapabilityParameter("flat_slope_percent_per_bar", "number", 0.15),
                CapabilityParameter("minimum_slope_percent_per_bar", "number", 0.02),
                BREAKOUT_BUFFER,
                TIMEFRAME,
            ),
            supported_comparators=("is_true", "is_false"),
            warmup_candles=90,
            semantic_tags=("technical_pattern", "continuation", "confirmation"),
            direction_support=(direction,),
            temporal_behavior="within_n_candles",
            proof_template=proof,
            builder_category="price_action",
            beginner_friendly=True,
        )
        for key, label, description, aliases, direction, proof in (
            (
                "ascending_triangle_breakout",
                "Ascending triangle breakout",
                "Flat confirmed highs and rising confirmed lows precede a close above resistance.",
                ("ascending triangle", "ascending triangle breakout"),
                "bullish",
                "Ascending triangle confirmation: close is above projected flat resistance.",
            ),
            (
                "descending_triangle_breakdown",
                "Descending triangle breakdown",
                "Flat confirmed lows and falling confirmed highs precede a close below support.",
                ("descending triangle", "descending triangle breakdown"),
                "bearish",
                "Descending triangle confirmation: close is below projected flat support.",
            ),
            (
                "symmetrical_triangle_breakout",
                "Symmetrical triangle bullish break",
                "Falling highs and rising lows converge before a close above the upper boundary.",
                ("symmetrical triangle breakout", "symmetric triangle breakout"),
                "bullish",
                "Symmetrical triangle confirmation: close is above the converging upper line.",
            ),
            (
                "symmetrical_triangle_breakdown",
                "Symmetrical triangle bearish break",
                "Falling highs and rising lows converge before a close below the lower boundary.",
                ("symmetrical triangle breakdown", "symmetric triangle breakdown"),
                "bearish",
                "Symmetrical triangle confirmation: close is below the converging lower line.",
            ),
        )
    ),
    # Candle patterns
    _cap(
        "bullish_engulfing",
        "Bullish engulfing",
        "candle_pattern",
        "candle_pattern",
        "Bullish candle engulfs previous bearish body.",
        aliases=("bullish engulfing",),
        operand_kind="candle_pattern",
        operand_name="bullish_engulfing",
    ),
    _cap(
        "bearish_engulfing",
        "Bearish engulfing",
        "candle_pattern",
        "candle_pattern",
        "Bearish candle engulfs previous bullish body.",
        aliases=("bearish engulfing",),
        operand_kind="candle_pattern",
        operand_name="bearish_engulfing",
    ),
    _cap(
        "hammer",
        "Hammer",
        "candle_pattern",
        "candle_pattern",
        "Long lower wick with small body.",
        aliases=("hammer",),
        operand_kind="candle_pattern",
        operand_name="hammer",
    ),
    _cap(
        "shooting_star",
        "Shooting star",
        "candle_pattern",
        "candle_pattern",
        "Long upper wick with small body.",
        aliases=("shooting star",),
        operand_kind="candle_pattern",
        operand_name="shooting_star",
    ),
    _cap(
        "doji",
        "Doji",
        "candle_pattern",
        "candle_pattern",
        "Very small candle body relative to range.",
        aliases=("doji",),
        operand_kind="candle_pattern",
        operand_name="doji",
    ),
    _cap(
        "inside_bar",
        "Inside bar",
        "candle_pattern",
        "candle_pattern",
        "Current high/low is inside previous high/low.",
        aliases=("inside bar",),
        operand_kind="candle_pattern",
        operand_name="inside_bar",
    ),
    _cap(
        "outside_bar",
        "Outside bar",
        "candle_pattern",
        "candle_pattern",
        "Current high/low exceeds previous high/low.",
        aliases=("outside bar",),
        operand_kind="candle_pattern",
        operand_name="outside_bar",
    ),
    _cap(
        "pin_bar",
        "Pin bar",
        "candle_pattern",
        "candle_pattern",
        "Long wick rejection candle.",
        aliases=("pin bar",),
        operand_kind="candle_pattern",
        operand_name="pin_bar",
    ),
    _cap(
        "strong_close_near_high",
        "Strong close near high",
        "candle_pattern",
        "candle_pattern",
        "Close is near the candle high.",
        aliases=("close near high", "strong close near high"),
        operand_kind="candle_pattern",
        operand_name="strong_close_near_high",
    ),
    _cap(
        "strong_close_near_low",
        "Strong close near low",
        "candle_pattern",
        "candle_pattern",
        "Close is near the candle low.",
        aliases=("close near low", "strong close near low"),
        operand_kind="candle_pattern",
        operand_name="strong_close_near_low",
    ),
    _cap(
        "green_candle",
        "Green candle close",
        "candle_pattern",
        "candle_pattern",
        "Close is above open.",
        aliases=("closes green", "green candle"),
        operand_kind="candle_pattern",
        operand_name="green_candle",
    ),
    _cap(
        "red_candle",
        "Red candle close",
        "candle_pattern",
        "candle_pattern",
        "Close is below open.",
        aliases=("closes red", "red candle"),
        operand_kind="candle_pattern",
        operand_name="red_candle",
    ),
    # Advanced, market filters and sessions
    _cap(
        "fibonacci_retracement_zone",
        "Fibonacci retracement zone",
        "advanced",
        "price_action",
        "Price enters a configured Fibonacci retracement zone.",
        aliases=("fib retracement", "fibonacci retracement"),
        operand_kind="price_action",
        operand_name="pullback_to_fibonacci_zone",
        default_parameters={
            "lookback": 50,
            "minimum_retracement": 0.382,
            "maximum_retracement": 0.618,
        },
        guidance="Uses the configured closed-candle lookback as deterministic swing anchors.",
    ),
    _cap(
        "fibonacci_extension_targets",
        "Fibonacci extension targets",
        "advanced",
        "risk",
        "Targets can reference Fibonacci extensions.",
        aliases=("fib extension", "fibonacci extension"),
        operand_kind="risk_metric",
        operand_name="fibonacci_extension_targets",
        # "Checks whether ... aligns" — a yes or no answer, per its own guidance below.
        default_comparator="is_true",
        guidance="Checks whether the configured first target aligns with a common extension.",
    ),
    _cap(
        "golden_pocket_zone",
        "Golden pocket zone",
        "advanced",
        "price_action",
        "Price enters the 0.618-0.65 retracement zone.",
        aliases=("golden pocket",),
        operand_kind="price_action",
        operand_name="pullback_to_fibonacci_zone",
        default_parameters={
            "lookback": 50,
            "minimum_retracement": 0.618,
            "maximum_retracement": 0.65,
        },
        guidance="Uses the configured closed-candle lookback as deterministic swing anchors.",
    ),
    _cap(
        "pivot_high_low",
        "Pivot high/low",
        "price_action",
        "price_action",
        "Pivot swing detection.",
        aliases=("pivot high", "pivot low"),
        executable=True,
        operand_kind="price_action",
        operand_name="pivot_break",
    ),
    _cap(
        "daily_high_low",
        "Daily high/low",
        "price_action",
        "price_action",
        "Daily high/low break or sweep.",
        aliases=("daily high", "daily low"),
        operand_kind="price_action",
        operand_name="higher_high",
    ),
    _cap(
        "previous_daily_low_sweep",
        "Previous daily low sweep",
        "price_action",
        "price_action",
        "Price trades below the previous UTC daily low and closes back above it.",
        aliases=(
            "pdl sweep",
            "sweep pdl",
            "sweep previous daily low",
            "sweep the previous daily low",
            "swept pdl",
            "sweeped pdl",
            "swept through pdl",
            "previous daily low sweep",
            "previous day low sweep",
        ),
        operand_kind="price_action",
        operand_name="daily_low_swept",
        default_parameters={"timezone": "UTC"},
        parameters=(CapabilityParameter("timezone", "timezone", "UTC"), TIMEFRAME),
        semantic_tags=("sweep", "liquidity", "reversal"),
        direction_support=("bullish",),
        temporal_behavior="current_candle",
        composes_with=("bullish_engulfing", "volume_spike", "price_above_ema"),
        intent_examples=(
            "Find coins that swept PDL.",
            "Alert when price sweeps the previous daily low and reclaims it.",
        ),
        negative_examples=("Price closes below PDL without reclaiming it.",),
    ),
    _cap(
        "previous_daily_high_sweep",
        "Previous daily high sweep",
        "price_action",
        "price_action",
        "Price trades above the previous UTC daily high and closes back below it.",
        aliases=(
            "pdh sweep",
            "sweep pdh",
            "sweep previous daily high",
            "sweep the previous daily high",
            "swept pdh",
            "sweeped pdh",
            "swept through pdh",
            "previous daily high sweep",
            "previous day high sweep",
        ),
        operand_kind="price_action",
        operand_name="daily_high_swept",
        default_parameters={"timezone": "UTC"},
        parameters=(CapabilityParameter("timezone", "timezone", "UTC"), TIMEFRAME),
        semantic_tags=("sweep", "liquidity", "reversal"),
        direction_support=("bearish",),
        temporal_behavior="current_candle",
        composes_with=("bearish_engulfing", "volume_spike", "price_below_ema"),
        intent_examples=(
            "Find coins that swept PDH.",
            "Alert when price sweeps the previous daily high and rejects it.",
        ),
        negative_examples=("Price closes above PDH without rejecting it.",),
    ),
    _cap(
        "weekly_high_low",
        "Weekly high/low",
        "price_action",
        "price_action",
        "Weekly high/low break or sweep.",
        aliases=("weekly high", "weekly low"),
        operand_kind="price_action",
        operand_name="higher_high",
    ),
    _cap(
        "reference_period_sweep",
        "Previous period high/low sweep",
        "price_action",
        "price_action",
        (
            "The current candle trades beyond a completed previous day, week, or month "
            "high/low and closes back through that level."
        ),
        aliases=(
            "previous weekly candle sweep",
            "sweep previous weekly candle",
            "sweep the previous week",
            "previous week sweep",
            "prior week sweep",
            "previous week low sweep",
            "previous week high sweep",
            "swept the previous week's low",
            "swept the previous week's high",
            "previous monthly candle sweep",
            "previous month low sweep",
            "previous month high sweep",
            "previous period sweep",
        ),
        operand_kind="price_action",
        operand_name="reference_period_sweep",
        default_parameters={"reference_period": "week", "side": "low", "timezone": "UTC"},
        parameters=(
            CapabilityParameter(
                "reference_period",
                "choice",
                "week",
                False,
                "Completed reference period.",
                options=("day", "week", "month"),
            ),
            CapabilityParameter(
                "side",
                "choice",
                None,
                True,
                "High for a bearish rejection or low for a bullish reclaim.",
                options=("high", "low"),
            ),
            CapabilityParameter("timezone", "timezone", "UTC"),
            TIMEFRAME,
        ),
        semantic_tags=("sweep", "liquidity", "reversal"),
        direction_support=("bullish", "bearish"),
        temporal_behavior="current_candle",
        intent_examples=(
            "Find coins whose current candle swept the previous week's low.",
            "Alert when this week's candle sweeps last week's high and closes below it.",
            "Show a sweep of the previous monthly candle's low.",
        ),
        negative_examples=(
            "The current candle remains inside the previous period range.",
            "Price breaks the previous period level without closing back through it.",
        ),
        proof_template=(
            "Current candle swept the previous {reference_period} {side} and reclaimed it."
        ),
        capability_version="1.1",
    ),
    _cap(
        "monthly_high_low",
        "Monthly high/low",
        "price_action",
        "price_action",
        "Monthly high/low break or sweep.",
        aliases=("monthly high", "monthly low"),
        operand_kind="price_action",
        operand_name="higher_high",
    ),
    _cap(
        "previous_session_high_low",
        "Previous session high/low",
        "session_time",
        "price_action",
        "Previous session high/low break or sweep.",
        aliases=("previous session high", "previous session low"),
        operand_kind="price_action",
        operand_name="previous_session_high_low",
        default_parameters={
            "session": "new_york",
            "timezone": "America/New_York",
            "start_hour": 9.5,
            "end_hour": 16,
            "mode": "break_high",
        },
        guidance="Segments closed candles using the configured timezone and session hours.",
    ),
    _cap(
        "time_window",
        "Time window/session filter",
        "session_time",
        "market_filter",
        "Signal candle timestamp must fall inside a time window.",
        aliases=("ny session", "london session", "midnight"),
        operand_kind="market_metric",
        operand_name="time_window",
        # `evaluate_time_condition` returns a bool for "time_window": the candle is
        # either inside the window or it is not.
        default_comparator="is_true",
        required_data=("candle_timestamp",),
    ),
    _cap(
        "killzone_filter",
        "Killzone/session filter",
        "session_time",
        "market_filter",
        "Signal candle timestamp must fall inside a named killzone.",
        aliases=("killzone", "ny killzone", "london killzone"),
        operand_kind="market_metric",
        operand_name="time_window",
        # Same operand as the window above, so the same yes/no answer.
        default_comparator="is_true",
        required_data=("candle_timestamp",),
    ),
    _cap(
        "percent_change_lookback",
        "Percent change in lookback",
        "price_action",
        "price_action",
        "Price increases or decreases by X percent over lookback.",
        aliases=(
            "pump",
            "dump",
            "increasing by",
            "decreasing by",
            "price up percent",
            "price down percent",
            "gained percent",
            "dropped percent",
        ),
        operand_kind="price_action",
        operand_name="percent_change_up",
        default_parameters={"direction": "up", "threshold_percent": 5, "lookback": 1},
        # Unlike the rest of this file's price_action capabilities, this one measures
        # a percentage, not a pattern match, so it keeps the numeric comparator set
        # that _cap() otherwise stops defaulting to for operand_kind="price_action".
        supported_comparators=("gt", "gte", "lt", "lte", "eq"),
        parameters=(
            CapabilityParameter("direction", "choice", "up", options=("up", "down")),
            CapabilityParameter("threshold_percent", "number", 5),
            CapabilityParameter("lookback", "integer", 1),
            TIMEFRAME,
        ),
        intent_examples=(
            "Find coins up 5% today.",
            "Find coins down 4% this week.",
        ),
        direction_support=("bullish", "bearish"),
    ),
    _cap(
        "new_n_day_high",
        "New N-day high",
        "price_action",
        "price_action",
        "Latest high exceeds the previous N-day high.",
        aliases=("20-day high", "n-day high", "six month high"),
        operand_kind="price_action",
        operand_name="n_day_high_breakout",
    ),
    _cap(
        "new_n_day_low",
        "New N-day low",
        "price_action",
        "price_action",
        "Latest low breaks the previous N-day low.",
        aliases=("20-day low", "n-day low"),
        operand_kind="price_action",
        operand_name="n_day_low_breakdown",
    ),
    _cap(
        "market_cap_minimum",
        "Market cap minimum",
        "market_filter",
        "market_filter",
        "External market-cap filter.",
        aliases=("market cap",),
        required_data=("market_cap_provider",),
        operand_kind="market_metric",
        operand_name="market_cap_minimum",
        default_parameters={
            "provider": "market_cap_provider",
            "context_category": "market_cap_provider",
        },
        default_comparator="gte",
        default_threshold=100_000_000,
        provider_required="market_cap_provider",
        guidance="Uses configured market metadata; missing market cap is unavailable.",
    ),
    _cap(
        "stablecoin_exclusion",
        "Stablecoin exclusion",
        "market_filter",
        "market_filter",
        "Exclude stablecoin base assets.",
        aliases=("exclude stables", "stablecoin exclusion"),
        # An exclusion is a flag: this coin either is a stablecoin or it is not.
        default_comparator="is_true",
        required_data=("market_metadata",),
    ),
    _cap(
        "leveraged_token_exclusion",
        "Leveraged token exclusion",
        "market_filter",
        "market_filter",
        "Exclude leveraged tokens.",
        aliases=("no leveraged tokens", "3l", "3s"),
        # An exclusion flag, like the stablecoin one above.
        default_comparator="is_true",
        required_data=("market_metadata",),
    ),
    _cap(
        "spread_filter",
        "Spread filter",
        "market_filter",
        "market_filter",
        "Maximum spread in basis points.",
        aliases=("max spread", "spread bps"),
        # "Maximum" — a measured spread that must stay at or under a number.
        default_comparator="lte",
        required_data=("ticker", "order_book"),
    ),
    _cap(
        "listing_age_filter",
        "Listing age filter",
        "market_filter",
        "market_filter",
        "Minimum market listing age.",
        aliases=("listing age", "new listing"),
        # "Minimum" — a measured age that must reach a number.
        default_comparator="gte",
        required_data=("market_metadata",),
    ),
    _cap(
        "correlation_filter",
        "Correlation filter",
        "advanced",
        "market_filter",
        "Filter by correlation to another asset.",
        aliases=("correlation", "bitcoin dominance"),
        operand_kind="market_metric",
        operand_name="correlation_filter",
        default_parameters={
            "provider": "cross_market",
            "context_category": "cross_market",
            "threshold": 0.7,
        },
        # It carries its own threshold of 0.7, so it measures a correlation and
        # compares it. It is the one filter in this group that is not a flag.
        default_comparator="gte",
        default_threshold=0.7,
        guidance="Uses aligned closed-candle returns against BTC.",
    ),
    _cap(
        "btc_trend_filter",
        "BTC trend filter for alts",
        "advanced",
        "price_action",
        "Altcoin scans gated by BTC trend.",
        aliases=("btc above", "bitcoin trend filter", "btc trend"),
        operand_kind="market_metric",
        operand_name="btc_trend_filter",
        default_parameters={
            "provider": "cross_market",
            "context_category": "cross_market",
        },
        # A gate, not a measurement: BTC is in the trend the scan wants, or it is not.
        # Its provider siblings (`btc_usdt_trend_filter`) already say the same.
        default_comparator="is_true",
        guidance="Uses BTC benchmark candles from the selected exchange.",
    ),
    _cap(
        "eth_trend_filter",
        "ETH trend filter for alts",
        "advanced",
        "price_action",
        "Altcoin scans gated by ETH trend.",
        aliases=("eth above", "ethereum trend filter", "eth trend"),
        operand_kind="market_metric",
        operand_name="eth_trend_filter",
        default_parameters={
            "provider": "cross_market",
            "context_category": "cross_market",
        },
        # A gate, like the BTC one above.
        default_comparator="is_true",
        guidance="Uses ETH benchmark candles from the selected exchange.",
    ),
    _cap(
        "meme_coin_exclusion",
        "Meme-coin exclusion",
        "market_filter",
        "market_filter",
        "Exclude meme coins by external tags.",
        aliases=("no meme coins", "avoid memes"),
        operand_kind="market_metric",
        operand_name="meme_coin_exclusion",
        default_parameters={
            "provider": "token_categories",
            "context_category": "token_categories",
        },
        # The evaluator sets this from the coin's category as a bool: the coin is
        # either outside the meme categories or it is not.
        default_comparator="is_true",
        required_data=("token_categories",),
        provider_required="token_categories",
        guidance="Uses configured token-category metadata; missing categories stay explicit.",
    ),
    # Extended oscillator families
    _cap(
        "stochastic_rsi",
        "Stochastic RSI",
        "momentum",
        "indicator",
        "RSI normalized inside its recent range, with K and D outputs.",
        aliases=("stoch rsi", "stochastic relative strength index"),
        operand_kind="indicator",
        operand_name="stochastic_rsi",
        default_parameters={
            "rsi_period": 14,
            "stoch_period": 14,
            "k_period": 3,
            "d_period": 3,
            "component": "k",
        },
        default_comparator="lte",
        default_threshold=20,
        parameters=(
            CapabilityParameter("rsi_period", "integer", 14),
            CapabilityParameter("stoch_period", "integer", 14),
            CapabilityParameter("k_period", "integer", 3),
            CapabilityParameter("d_period", "integer", 3),
            CapabilityParameter(
                "field",
                "choice",
                "close",
                options=("open", "high", "low", "close"),
            ),
            CapabilityParameter("component", "choice", "k", options=("k", "d")),
        ),
        outputs=("k", "d"),
        examples=("Stochastic RSI K crosses above D below 20.",),
        warmup_candles=33,
    ),
    _cap(
        "money_flow_index",
        "Money Flow Index",
        "momentum",
        "indicator",
        "Volume-weighted momentum oscillator bounded from 0 to 100.",
        aliases=("mfi", "money flow"),
        operand_kind="indicator",
        operand_name="money_flow_index",
        default_parameters={"period": 14},
        default_comparator="lte",
        default_threshold=20,
        parameters=(CapabilityParameter("period", "integer", 14), THRESHOLD, TIMEFRAME),
        outputs=("value",),
        examples=("MFI is below 20.",),
        warmup_candles=15,
    ),
    _cap(
        "commodity_channel_index",
        "Commodity Channel Index",
        "momentum",
        "indicator",
        "Deviation of typical price from its mean, scaled by mean deviation.",
        aliases=("cci", "commodity channel"),
        operand_kind="indicator",
        operand_name="commodity_channel_index",
        default_parameters={"period": 20},
        default_comparator="gte",
        default_threshold=100,
        parameters=(CapabilityParameter("period", "integer", 20), THRESHOLD, TIMEFRAME),
        examples=("CCI crosses above 100.",),
        warmup_candles=20,
    ),
    _cap(
        "williams_percent_r",
        "Williams %R",
        "momentum",
        "indicator",
        "Close position inside the recent high-low range, scaled from -100 to 0.",
        aliases=("williams r", "williams percent r", "%r"),
        operand_kind="indicator",
        operand_name="williams_percent_r",
        default_parameters={"period": 14},
        default_comparator="lte",
        default_threshold=-80,
        parameters=(CapabilityParameter("period", "integer", 14), THRESHOLD, TIMEFRAME),
        examples=("Williams %R crosses above -80.",),
        warmup_candles=14,
    ),
    _cap(
        "rate_of_change",
        "Rate of Change",
        "momentum",
        "indicator",
        "Percentage change from the value N candles ago.",
        aliases=("roc", "rate of change"),
        operand_kind="indicator",
        operand_name="rate_of_change",
        default_parameters={"period": 12, "field": "close"},
        default_comparator="gt",
        default_threshold=0,
        parameters=(CapabilityParameter("period", "integer", 12), THRESHOLD, TIMEFRAME),
        examples=("Twelve-period ROC is positive.",),
        warmup_candles=13,
    ),
    _cap(
        "momentum_indicator",
        "Momentum",
        "momentum",
        "indicator",
        "Absolute price difference from N candles ago.",
        aliases=("momentum value", "price momentum"),
        operand_kind="indicator",
        operand_name="momentum",
        default_parameters={"period": 10, "field": "close"},
        default_comparator="gt",
        default_threshold=0,
        parameters=(CapabilityParameter("period", "integer", 10), THRESHOLD, TIMEFRAME),
        examples=("Momentum is above zero and rising.",),
        warmup_candles=11,
    ),
    _cap(
        "true_strength_index",
        "True Strength Index",
        "momentum",
        "indicator",
        "Double-smoothed price momentum with a signal output.",
        aliases=("tsi", "true strength"),
        operand_kind="indicator",
        operand_name="true_strength_index",
        default_parameters={"component": "tsi"},
        default_comparator="crosses_above",
        default_threshold=0,
        parameters=(
            CapabilityParameter("long_period", "integer", 25),
            CapabilityParameter("short_period", "integer", 13),
            CapabilityParameter("signal_period", "integer", 7),
            CapabilityParameter("component", "choice", "tsi", options=("tsi", "signal")),
        ),
        outputs=("tsi", "signal"),
        examples=("TSI crosses above its signal.",),
        warmup_candles=46,
    ),
    _cap(
        "ultimate_oscillator",
        "Ultimate Oscillator",
        "momentum",
        "indicator",
        "Buying pressure across short, medium, and long lookbacks.",
        aliases=("ultimate oscillator", "uo"),
        operand_kind="indicator",
        operand_name="ultimate_oscillator",
        default_parameters={"short": 7, "medium": 14, "long": 28},
        default_comparator="lte",
        default_threshold=30,
        parameters=(
            CapabilityParameter("short", "integer", 7),
            CapabilityParameter("medium", "integer", 14),
            CapabilityParameter("long", "integer", 28),
            THRESHOLD,
        ),
        examples=("Ultimate Oscillator crosses above 30.",),
        warmup_candles=29,
    ),
    _cap(
        "relative_vigor_index",
        "Relative Vigor Index",
        "momentum",
        "indicator",
        "Close-open vigor normalized by candle range, with signal output.",
        aliases=("rvi", "relative vigor"),
        operand_kind="indicator",
        operand_name="relative_vigor_index",
        default_parameters={"period": 10, "signal_period": 4, "component": "rvi"},
        default_comparator="crosses_above",
        default_threshold=0,
        parameters=(
            CapabilityParameter("period", "integer", 10),
            CapabilityParameter("signal_period", "integer", 4),
            CapabilityParameter("component", "choice", "rvi", options=("rvi", "signal")),
        ),
        outputs=("rvi", "signal"),
        examples=("RVI crosses above its signal.",),
        warmup_candles=13,
    ),
    _cap(
        "connors_rsi",
        "Connors RSI",
        "momentum",
        "indicator",
        "Composite mean-reversion oscillator using price RSI, streak RSI, and percent rank.",
        aliases=("crsi", "connors relative strength"),
        operand_kind="indicator",
        operand_name="connors_rsi",
        default_parameters={"rsi_period": 3, "streak_rsi_period": 2, "percent_rank_period": 100},
        default_comparator="lte",
        default_threshold=20,
        parameters=(
            CapabilityParameter("rsi_period", "integer", 3),
            CapabilityParameter("streak_rsi_period", "integer", 2),
            CapabilityParameter("percent_rank_period", "integer", 100),
            THRESHOLD,
        ),
        examples=("Connors RSI is below 10.",),
        warmup_candles=105,
    ),
    # Extended trend families
    _cap(
        "weighted_moving_average",
        "Weighted Moving Average",
        "trend",
        "indicator",
        "Moving average with linearly increasing weight on recent candles.",
        aliases=("wma", "weighted average"),
        operand_kind="indicator",
        operand_name="weighted_moving_average",
        default_parameters={"period": 20},
        parameters=(PERIOD, TIMEFRAME),
        warmup_candles=20,
    ),
    _cap(
        "hull_moving_average",
        "Hull Moving Average",
        "trend",
        "indicator",
        "Low-lag moving average composed from weighted moving averages.",
        aliases=("hma", "hull average"),
        operand_kind="indicator",
        operand_name="hull_moving_average",
        default_parameters={"period": 20},
        parameters=(PERIOD, TIMEFRAME),
        warmup_candles=24,
    ),
    _cap(
        "double_exponential_moving_average",
        "Double Exponential Moving Average",
        "trend",
        "indicator",
        "EMA-derived average designed to reduce lag.",
        aliases=("dema", "double ema"),
        operand_kind="indicator",
        operand_name="double_exponential_moving_average",
        default_parameters={"period": 20},
        parameters=(PERIOD, TIMEFRAME),
        warmup_candles=39,
    ),
    _cap(
        "triple_exponential_moving_average",
        "Triple Exponential Moving Average",
        "trend",
        "indicator",
        "Triple-smoothed EMA combination designed to reduce lag.",
        aliases=("tema", "triple ema"),
        operand_kind="indicator",
        operand_name="triple_exponential_moving_average",
        default_parameters={"period": 20},
        parameters=(PERIOD, TIMEFRAME),
        warmup_candles=58,
    ),
    _cap(
        "kaufman_adaptive_moving_average",
        "Kaufman Adaptive Moving Average",
        "trend",
        "indicator",
        "Adaptive average that changes smoothing with price efficiency.",
        aliases=("kama", "kaufman adaptive"),
        operand_kind="indicator",
        operand_name="kaufman_adaptive_moving_average",
        default_parameters={"period": 10, "fast_period": 2, "slow_period": 30},
        parameters=(
            CapabilityParameter("period", "integer", 10),
            CapabilityParameter("fast_period", "integer", 2),
            CapabilityParameter("slow_period", "integer", 30),
        ),
        warmup_candles=11,
    ),
    _cap(
        "volume_weighted_moving_average",
        "Volume Weighted Moving Average",
        "trend",
        "indicator",
        "Moving average weighted by each candle's volume.",
        aliases=("vwma", "volume weighted average"),
        operand_kind="indicator",
        operand_name="volume_weighted_moving_average",
        default_parameters={"period": 20},
        required_data=("ohlcv", "volume"),
        parameters=(PERIOD, TIMEFRAME),
        warmup_candles=20,
    ),
    _cap(
        "linear_regression_moving_average",
        "Linear Regression Moving Average",
        "trend",
        "indicator",
        "Endpoint value of a least-squares regression line.",
        aliases=("lrma", "linear regression average"),
        operand_kind="indicator",
        operand_name="linear_regression_moving_average",
        default_parameters={"period": 20},
        parameters=(PERIOD, TIMEFRAME),
        warmup_candles=20,
    ),
    _cap(
        "zero_lag_ema",
        "Zero Lag EMA",
        "trend",
        "indicator",
        "EMA calculated from lag-adjusted source values.",
        aliases=("zlema", "zero lag exponential"),
        operand_kind="indicator",
        operand_name="zero_lag_ema",
        default_parameters={"period": 20},
        parameters=(PERIOD, TIMEFRAME),
        warmup_candles=29,
    ),
    _cap(
        "ichimoku_cloud",
        "Ichimoku Cloud",
        "trend",
        "indicator",
        "Tenkan, Kijun, cloud boundaries, Chikou, and deterministic cloud states.",
        aliases=("ichimoku", "kumo cloud", "tenkan kijun"),
        operand_kind="indicator",
        operand_name="ichimoku_cloud",
        default_parameters={"component": "price_above_cloud"},
        default_comparator="eq",
        default_threshold=1,
        parameters=(
            CapabilityParameter("tenkan_period", "integer", 9),
            CapabilityParameter("kijun_period", "integer", 26),
            CapabilityParameter("senkou_b_period", "integer", 52),
            CapabilityParameter("displacement", "integer", 26),
            CapabilityParameter(
                "component",
                "choice",
                "price_above_cloud",
                options=(
                    "tenkan",
                    "kijun",
                    "senkou_a",
                    "senkou_b",
                    "chikou",
                    "cloud_top",
                    "cloud_bottom",
                    "price_above_cloud",
                    "price_below_cloud",
                    "price_inside_cloud",
                    "future_cloud_bullish",
                    "future_cloud_bearish",
                ),
            ),
        ),
        outputs=(
            "tenkan",
            "kijun",
            "senkou_a",
            "senkou_b",
            "chikou",
            "cloud_top",
            "cloud_bottom",
            "price_above_cloud",
            "price_below_cloud",
            "price_inside_cloud",
            "future_cloud_bullish",
            "future_cloud_bearish",
        ),
        examples=("Price is above the Ichimoku cloud.",),
        warmup_candles=52,
    ),
    _cap(
        "supertrend",
        "SuperTrend",
        "trend",
        "indicator",
        "ATR-based trailing trend line and bullish or bearish direction.",
        aliases=("super trend", "supertrend flip"),
        operand_kind="indicator",
        operand_name="supertrend",
        default_parameters={"atr_period": 10, "multiplier": 3, "component": "direction"},
        default_comparator="eq",
        default_threshold=1,
        parameters=(
            CapabilityParameter("atr_period", "integer", 10),
            CapabilityParameter("multiplier", "number", 3),
            CapabilityParameter(
                "component",
                "choice",
                "direction",
                options=("line", "direction", "price_above", "price_below"),
            ),
        ),
        outputs=("line", "direction", "price_above", "price_below"),
        examples=("SuperTrend flips bullish.",),
        warmup_candles=12,
    ),
    _cap(
        "parabolic_sar",
        "Parabolic SAR",
        "trend",
        "indicator",
        "Trailing stop-and-reverse line with trend direction.",
        aliases=("psar", "sar flip", "parabolic stop and reverse"),
        operand_kind="indicator",
        operand_name="parabolic_sar",
        default_parameters={"step": 0.02, "max_step": 0.2, "component": "direction"},
        default_comparator="eq",
        default_threshold=1,
        parameters=(
            CapabilityParameter("step", "number", 0.02),
            CapabilityParameter("max_step", "number", 0.2),
            CapabilityParameter(
                "component",
                "choice",
                "direction",
                options=("line", "direction", "price_above", "price_below"),
            ),
        ),
        outputs=("line", "direction", "price_above", "price_below"),
        examples=("Parabolic SAR flips bullish.",),
        warmup_candles=3,
    ),
    _cap(
        "aroon",
        "Aroon",
        "trend",
        "indicator",
        "Measures recency of period highs and lows.",
        aliases=("aroon up", "aroon down", "aroon oscillator"),
        operand_kind="indicator",
        operand_name="aroon",
        default_parameters={"period": 25, "component": "oscillator"},
        default_comparator="gt",
        default_threshold=0,
        parameters=(
            CapabilityParameter("period", "integer", 25),
            CapabilityParameter(
                "component",
                "choice",
                "oscillator",
                options=("aroon_up", "aroon_down", "oscillator"),
            ),
            THRESHOLD,
        ),
        outputs=("aroon_up", "aroon_down", "oscillator"),
        examples=("Aroon oscillator crosses above zero.",),
        warmup_candles=26,
    ),
    _cap(
        "directional_movement_components",
        "Directional Movement Components",
        "trend",
        "indicator",
        "Plus DI, minus DI, DX, ADX, and ADXR from one canonical calculation.",
        aliases=("plus di", "minus di", "di cross", "adxr", "directional movement"),
        operand_kind="indicator",
        operand_name="directional_movement",
        default_parameters={"period": 14, "component": "plus_di"},
        default_comparator="crosses_above",
        default_threshold=0,
        parameters=(
            CapabilityParameter("period", "integer", 14),
            CapabilityParameter(
                "component",
                "choice",
                "plus_di",
                options=("plus_di", "minus_di", "dx", "adx", "adxr"),
            ),
            THRESHOLD,
        ),
        outputs=("plus_di", "minus_di", "dx", "adx", "adxr"),
        examples=("Plus DI crosses above minus DI while ADX is above 25.",),
        warmup_candles=29,
    ),
    _cap(
        "elder_impulse",
        "Elder Impulse System",
        "trend",
        "indicator",
        "EMA slope and MACD histogram direction combined into bullish, bearish, or neutral.",
        aliases=("elder impulse", "impulse color"),
        operand_kind="indicator",
        operand_name="elder_impulse",
        default_parameters={"component": "state"},
        default_comparator="eq",
        default_threshold=1,
        parameters=(
            CapabilityParameter("ema_period", "integer", 13),
            CapabilityParameter(
                "component",
                "choice",
                "state",
                options=("state", "bullish", "bearish", "neutral"),
            ),
        ),
        outputs=("state", "bullish", "bearish", "neutral"),
        examples=("Elder Impulse is bullish.",),
        warmup_candles=35,
    ),
    # Extended volatility and range families
    _cap(
        "keltner_channels",
        "Keltner Channels",
        "volatility",
        "indicator",
        "EMA center line with ATR-based upper and lower channels.",
        aliases=("keltner", "kc channel"),
        operand_kind="indicator",
        operand_name="keltner_channel",
        default_parameters={"component": "upper"},
        parameters=(
            CapabilityParameter("ema_period", "integer", 20),
            CapabilityParameter("atr_period", "integer", 10),
            CapabilityParameter("multiplier", "number", 2),
            CapabilityParameter(
                "component",
                "choice",
                "upper",
                options=("upper", "middle", "lower", "width_percent"),
            ),
        ),
        outputs=("upper", "middle", "lower", "width_percent"),
        examples=("Close is above the upper Keltner Channel.",),
        warmup_candles=21,
    ),
    _cap(
        "donchian_channels",
        "Donchian Channels",
        "volatility",
        "indicator",
        "Prior N-candle high, low, and midpoint channel.",
        aliases=("donchian", "n period channel"),
        operand_kind="indicator",
        operand_name="donchian_channel",
        default_parameters={"period": 20, "component": "upper"},
        parameters=(
            CapabilityParameter("period", "integer", 20),
            CapabilityParameter(
                "component",
                "choice",
                "upper",
                options=("upper", "middle", "lower"),
            ),
        ),
        outputs=("upper", "middle", "lower"),
        examples=("Close breaks above the prior 20-candle Donchian high.",),
        warmup_candles=21,
    ),
    _cap(
        "bollinger_percent_b",
        "Bollinger %B",
        "volatility",
        "indicator",
        "Close location relative to the lower and upper Bollinger Bands.",
        aliases=("percent b", "bollinger %b", "bb percent b"),
        operand_kind="indicator",
        operand_name="bollinger_percent_b",
        default_parameters={"period": 20, "standard_deviations": 2},
        default_comparator="gt",
        default_threshold=1,
        parameters=(
            CapabilityParameter("period", "integer", 20),
            CapabilityParameter("standard_deviations", "number", 2),
            THRESHOLD,
        ),
        outputs=("value",),
        examples=("Bollinger %B crosses above 0.5.",),
        warmup_candles=20,
    ),
)


def _extended_indicator_capabilities() -> list[CapabilitySpec]:
    rows: tuple[
        tuple[
            str,
            str,
            str,
            dict[str, Any],
            str,
            Any,
            tuple[str, ...],
            int,
            str,
        ],
        ...,
    ] = (
        (
            "historical_volatility",
            "Historical Volatility",
            "historical_volatility",
            {"period": 20, "component": "value"},
            "gte",
            40,
            ("historical vol", "realized volatility", "volatility rising"),
            21,
            "volatility_squeeze",
        ),
        (
            "normalized_atr",
            "Normalized ATR",
            "normalized_atr",
            {"period": 14},
            "gte",
            2,
            ("natr", "atr percent of price", "volatility too high"),
            15,
            "volatility_squeeze",
        ),
        (
            "choppiness_index",
            "Choppiness Index",
            "choppiness_index",
            {"period": 14},
            "lte",
            38.2,
            ("choppy market", "trending market", "range market"),
            15,
            "volatility_squeeze",
        ),
        (
            "trend_strength",
            "Trend Strength",
            "trend_strength",
            {"period": 50},
            "gte",
            0.55,
            ("trend strength", "clean trend", "efficient trend", "directional trend"),
            51,
            "trend",
        ),
        (
            "atr_expansion_ratio",
            "ATR Expansion Ratio",
            "expansion_ratio",
            {"short_period": 14, "long_period": 50},
            "gte",
            1.2,
            ("atr expansion ratio", "volatility expansion ratio", "atr expanding"),
            51,
            "volatility_squeeze",
        ),
        (
            "ulcer_index",
            "Ulcer Index",
            "ulcer_index",
            {"period": 14},
            "lte",
            10,
            ("ulcer risk", "drawdown intensity", "downside risk"),
            14,
            "risk_trade_quality",
        ),
        (
            "on_balance_volume",
            "On Balance Volume",
            "on_balance_volume",
            {"component": "delta", "bars": 1},
            "gt",
            0,
            ("obv", "on balance volume rising", "obv breaks average"),
            2,
            "volume_flow",
        ),
        (
            "chaikin_money_flow",
            "Chaikin Money Flow",
            "chaikin_money_flow",
            {"period": 20},
            "gt",
            0,
            ("cmf", "chaikin flow", "money flow above zero"),
            20,
            "volume_flow",
        ),
        (
            "accumulation_distribution",
            "Accumulation / Distribution Line",
            "accumulation_distribution",
            {"component": "delta", "bars": 1},
            "gt",
            0,
            ("a/d line", "accumulation distribution rising", "distribution line"),
            2,
            "volume_flow",
        ),
        (
            "ease_of_movement",
            "Ease of Movement",
            "ease_of_movement",
            {"period": 14},
            "gt",
            0,
            ("eom", "ease of movement positive", "ease of movement crosses zero"),
            15,
            "volume_flow",
        ),
        (
            "force_index",
            "Force Index",
            "force_index",
            {"period": 13, "component": "value"},
            "gt",
            0,
            ("force index", "force spike", "volume force"),
            14,
            "volume_flow",
        ),
        (
            "volume_oscillator",
            "Volume Oscillator",
            "volume_oscillator",
            {"short_period": 5, "long_period": 20, "component": "percent"},
            "gt",
            0,
            ("volume momentum", "volume oscillator crosses zero"),
            20,
            "volume_flow",
        ),
        (
            "volume_profile_proxy",
            "Volume Profile Proxy",
            "volume_profile_proxy",
            {"period": 100, "bins": 24, "component": "volume_node_near_price"},
            "eq",
            1,
            ("high volume price zone", "volume node near price", "poc proxy"),
            100,
            "volume_flow",
        ),
        (
            "relative_volume_by_session",
            "Relative Volume by Session",
            "relative_volume_by_session",
            {"component": "same_time_ratio", "timezone": "UTC", "lookback_days": 30},
            "gte",
            1.5,
            ("same time relative volume", "session rvol", "top volume same time"),
            30,
            "volume_flow",
        ),
        (
            "anchored_vwap",
            "Anchored VWAP",
            "anchored_vwap",
            {"anchor_bars": 100},
            "gt",
            0,
            ("anchored vwap", "avwap", "vwap from anchor", "vwap from sweep"),
            100,
            "volume_flow",
        ),
        (
            "dollar_volume",
            "Dollar Volume",
            "dollar_volume",
            {"period": 1, "component": "value"},
            "gte",
            1_000_000,
            ("quote volume", "price times volume", "dollar volume spike"),
            1,
            "volume_flow",
        ),
        (
            "buy_sell_pressure_proxy",
            "Buy / Sell Pressure Proxy",
            "buy_sell_pressure_proxy",
            {"period": 20, "component": "pressure_score"},
            "gt",
            0,
            ("candle pressure", "buy pressure proxy", "sell pressure proxy"),
            20,
            "volume_flow",
        ),
        (
            "pivot_points",
            "Pivot Points",
            "pivot_points",
            {"lookback": 1, "component": "r1"},
            "gt",
            0,
            ("daily pivot", "pivot r1", "pivot support resistance"),
            2,
            "price",
        ),
        (
            "candle_anatomy",
            "Candle Anatomy",
            "candle_anatomy",
            {"component": "body_percent"},
            "gte",
            50,
            ("body size percent", "upper wick percent", "close in top quarter"),
            1,
            "candle_pattern",
        ),
        (
            "distance_to_reference",
            "Distance to Market Reference",
            "distance_to_reference",
            {"reference": "ema", "period": 200},
            "lte",
            2,
            ("distance to ema", "distance to vwap", "distance to support"),
            201,
            "risk_trade_quality",
        ),
    )
    return [
        _cap(
            key,
            label,
            (
                "volatility"
                if builder_category == "volatility_squeeze"
                else "volume_liquidity"
                if builder_category == "volume_flow"
                else "risk"
                if builder_category == "risk_trade_quality"
                else "indicator"
            ),
            "indicator",
            f"Deterministic {label.lower()} calculation from closed OHLCV candles.",
            aliases=aliases,
            operand_kind="indicator",
            operand_name=operand,
            default_parameters=parameters,
            default_comparator=comparator,
            default_threshold=threshold,
            parameters=tuple(
                CapabilityParameter(
                    name, "number" if isinstance(value, float) else "integer", value
                )
                for name, value in parameters.items()
                if isinstance(value, (int, float))
            ),
            warmup_candles=warmup,
            builder_category=builder_category,
            beginner_friendly=key
            in {
                "historical_volatility",
                "normalized_atr",
                "choppiness_index",
                "on_balance_volume",
                "chaikin_money_flow",
                "dollar_volume",
                "pivot_points",
            },
            approximation=key in {"volume_profile_proxy", "buy_sell_pressure_proxy"},
            approximation_note=(
                "OHLCV approximation; true trade-at-price or order-flow data is not available."
                if key in {"volume_profile_proxy", "buy_sell_pressure_proxy"}
                else ""
            ),
            examples=(f"{label} meets the configured threshold.",),
            test_cases=("positive threshold case", "negative threshold case", "warm-up failure"),
        )
        for (
            key,
            label,
            operand,
            parameters,
            comparator,
            threshold,
            aliases,
            warmup,
            builder_category,
        ) in rows
    ]


def _candle_pattern_capabilities() -> list[CapabilitySpec]:
    existing = {capability.key for capability in CAPABILITIES}
    return [
        _cap(
            name,
            name.replace("_", " ").title(),
            "candle_pattern",
            "candle_pattern",
            f"Deterministic {name.replace('_', ' ')} detector with configurable candle anatomy.",
            aliases=(name.replace("_", " "),),
            operand_kind="candle_pattern",
            operand_name=name,
            default_parameters={
                "min_body_percent": 25,
                "max_body_percent": 40,
                "wick_ratio": 2,
                "trend_context_required": False,
                "confirmation_required": False,
                "pattern_strength": "medium",
                "direction": "neutral",
            },
            supported_comparators=("is_true", "is_false"),
            default_comparator="is_true",
            default_threshold=True,
            parameters=(
                CapabilityParameter("min_body_percent", "number", 25),
                CapabilityParameter("max_body_percent", "number", 40),
                CapabilityParameter("wick_ratio", "number", 2),
                CapabilityParameter("trend_context_required", "boolean", False),
                CapabilityParameter("confirmation_required", "boolean", False),
                CapabilityParameter(
                    "pattern_strength",
                    "choice",
                    "medium",
                    options=("weak", "medium", "strong"),
                ),
                CapabilityParameter(
                    "direction",
                    "choice",
                    "neutral",
                    options=("bullish", "bearish", "neutral"),
                ),
            ),
            warmup_candles=5,
            builder_category="candle_pattern",
            beginner_friendly=name
            in {
                "bullish_engulfing",
                "bearish_engulfing",
                "hammer",
                "shooting_star",
                "doji",
                "morning_star",
                "evening_star",
                "three_white_soldiers",
                "three_black_crows",
            },
            examples=(f"{name.replace('_', ' ').title()} appears on the selected timeframe.",),
            test_cases=("pattern present", "pattern absent", "insufficient candles"),
        )
        for name in pattern_names()
        if name not in existing
    ]


def _price_action_capabilities() -> list[CapabilitySpec]:
    existing = {capability.key for capability in CAPABILITIES}
    numeric = {
        "level_strength_score": ("gte", 60),
        "level_distance_percent": ("lte", 1),
        "dynamic_trendline": ("gt", 0),
    }
    beginner = {
        "breaks_n_candle_high",
        "breaks_n_candle_low",
        "failed_breakout",
        "retest_after_breakout",
        "price_near_horizontal_level",
        "price_bounces_from_support",
        "price_rejects_resistance",
        "range_high_rejection",
        "range_low_rejection",
        "bullish_fair_value_gap",
        "bearish_fair_value_gap",
        "displacement_candle_bullish",
        "displacement_candle_bearish",
        "inside_range",
        "breakout_from_consolidation",
        "nr4_candle",
        "nr7_candle",
        "pullback_to_ema",
        "pullback_to_vwap",
    }
    alias_overrides = {
        "market_structure_shift_bullish": ("market structure shift", "mss bullish"),
        "market_structure_shift_bearish": ("market structure shift", "mss bearish"),
        "previous_high_swept": ("takes previous high", "previous high taken"),
        "previous_low_swept": ("takes previous low", "previous low taken"),
        "sweep_and_reclaim": ("reclaims level", "sweep reclaim"),
        "po3_dealing_range_sweep_bullish": (
            "po3 bullish dealing range sweep",
            "bullish dealing range sweep",
            "sell side dealing range sweep",
        ),
        "po3_dealing_range_sweep_bearish": (
            "po3 bearish dealing range sweep",
            "bearish dealing range sweep",
            "buy side dealing range sweep",
        ),
        "po3_sweep_displacement_bullish": (
            "bullish sweep and displacement",
            "sell side sweep with displacement",
        ),
        "po3_sweep_displacement_bearish": (
            "bearish sweep and displacement",
            "buy side sweep with displacement",
        ),
        "po3_sweep_displacement_structure_bullish": (
            "bullish po3",
            "bullish sweep displacement bos",
            "sell side sweep displacement bos",
            "bullish sweep displacement structure",
        ),
        "po3_sweep_displacement_structure_bearish": (
            "bearish po3",
            "bearish sweep displacement bos",
            "buy side sweep displacement bos",
            "bearish sweep displacement structure",
        ),
        "fvg_virgin": ("virgin fvg", "unmitigated fvg"),
        "fvg_touched": ("touched fvg", "fvg touched"),
        "fvg_mid_mitigated": ("mid mitigated fvg", "fvg midpoint mitigated"),
        "fvg_fully_mitigated": ("fully mitigated fvg", "fvg filled"),
        "fvg_structure_invalidated": ("invalidated fvg", "fvg structure invalidated"),
        "fvg_still_open_bullish": ("bullish fvg still open", "bullish open fvg"),
        "fvg_still_open_bearish": ("bearish fvg still open", "bearish open fvg"),
        "displacement_candle_bullish": ("strong bullish candle", "bullish displacement"),
        "displacement_candle_bearish": ("strong bearish candle", "bearish displacement"),
        "session_high_swept": ("session high swept", "takes session high"),
        "session_low_swept": ("session low swept", "takes session low"),
        "break_and_retest_confirmed": ("breaks and retests", "break retest"),
        "tight_consolidation": ("quiet range", "tight market"),
    }
    specs: list[CapabilitySpec] = []
    for name in sorted(PRICE_ACTION_NAMES):
        if name in existing or name == "certified_dynamic":
            continue
        if any(term in name for term in ("structure", "swing", "protected", "weak_")):
            builder_category = "market_structure"
        elif any(
            term in name
            for term in ("liquidity", "sweep", "stop_hunt", "fair_value", "fvg", "order_block")
        ):
            builder_category = "liquidity_smart_money"
        else:
            builder_category = "price_action"
        comparator, threshold = numeric.get(name, ("is_true", True))
        specs.append(
            _cap(
                name,
                name.replace("_", " ").title(),
                "price_action",
                "price_action",
                f"Deterministic {name.replace('_', ' ')} from closed OHLCV history.",
                aliases=(
                    name.replace("_", " "),
                    name.replace("_", " ").replace("n candle", "range"),
                    *alias_overrides.get(name, ()),
                ),
                operand_kind="price_action",
                operand_name=name,
                default_parameters={"lookback": 20, "tolerance_percent": 0.25},
                default_comparator=comparator,
                default_threshold=threshold,
                supported_comparators=(
                    ("gt", "gte", "lt", "lte", "eq") if name in numeric else ("is_true", "is_false")
                ),
                parameters=(LOOKBACK, TIMEFRAME),
                warmup_candles=21,
                builder_category=builder_category,
                beginner_friendly=name in beginner,
                phase=2 if builder_category in {"market_structure", "liquidity_smart_money"} else 1,
                examples=(f"{name.replace('_', ' ').title()} is confirmed.",),
                test_cases=("positive OHLCV case", "negative OHLCV case", "warm-up failure"),
            )
        )
    return specs


def _time_capabilities() -> list[CapabilitySpec]:
    existing = {capability.key for capability in CAPABILITIES}
    aliases = {
        "weekend_filter": ("weekend filter", "include weekends"),
        "weekday_only": ("weekdays only", "avoid weekends"),
        "london_session": ("london session", "london hours"),
        "new_york_session": ("new york session", "ny session"),
        "asia_session": ("asia session", "asian session"),
        "session_open_window": ("session open", "london open", "new york open"),
        "session_close_window": ("session close", "london close", "new york close"),
        "avoid_daily_reset": ("avoid daily reset", "avoid midnight close"),
    }
    return [
        _cap(
            name,
            name.replace("_", " ").title(),
            "session_time",
            "market_filter",
            f"Timezone-safe {name.replace('_', ' ')} condition for 24/7 crypto markets.",
            aliases=aliases.get(name, (name.replace("_", " "),)),
            operand_kind="market_metric",
            operand_name=name,
            default_parameters={"timezone": "UTC"},
            default_comparator=(
                "gte"
                if name
                in {
                    "daily_open",
                    "weekly_open",
                    "monthly_open",
                    "time_since_last_alert",
                    "time_since_setup_detected",
                    "time_since_condition_true",
                }
                else "is_true"
            ),
            default_threshold=(
                0
                if name
                in {
                    "daily_open",
                    "weekly_open",
                    "monthly_open",
                    "time_since_last_alert",
                    "time_since_setup_detected",
                    "time_since_condition_true",
                }
                else True
            ),
            supported_comparators=(
                ("gt", "gte", "lt", "lte", "eq")
                if name
                in {
                    "daily_open",
                    "weekly_open",
                    "monthly_open",
                    "time_since_last_alert",
                    "time_since_setup_detected",
                    "time_since_condition_true",
                }
                else ("is_true", "is_false")
            ),
            parameters=(
                CapabilityParameter("timezone", "timezone", "UTC"),
                CapabilityParameter("start_hour", "number", 0),
                CapabilityParameter("end_hour", "number", 24),
            ),
            required_data=("candle_timestamp",),
            warmup_candles=1,
            builder_category="time_session",
            beginner_friendly=name
            in {
                "day_of_week",
                "weekend_filter",
                "weekday_only",
                "asia_session",
                "london_session",
                "new_york_session",
                "session_open_window",
            },
            examples=(f"{name.replace('_', ' ').title()} in the user's configured timezone.",),
            test_cases=("inside requested time", "outside requested time", "timezone conversion"),
        )
        for name in sorted(TIME_CONDITION_NAMES)
        if name not in existing
    ]


def _provider_capabilities() -> list[CapabilitySpec]:
    groups: tuple[
        tuple[str, str, str, tuple[tuple[str, str, tuple[str, ...]], ...]],
        ...,
    ] = (
        (
            "cross_market",
            "Cross-market candles",
            "market_context",
            (
                ("btc_usdt_trend_filter", "BTC/USDT Trend Filter", ("against btc trend",)),
                ("eth_usdt_trend_filter", "ETH/USDT Trend Filter", ("against eth trend",)),
                (
                    "eth_btc_relative_strength",
                    "ETH/BTC Relative Strength",
                    ("eth stronger than btc",),
                ),
                ("symbol_outperforming_btc", "Symbol Outperforming BTC", ("beats btc",)),
                ("symbol_underperforming_btc", "Symbol Underperforming BTC", ("lags btc",)),
                ("symbol_outperforming_eth", "Symbol Outperforming ETH", ("beats eth",)),
                ("pair_correlation_btc", "Pair Correlation with BTC", ("correlated with btc",)),
                ("pair_beta_btc", "Pair Beta vs BTC", ("beta to btc",)),
                ("pair_volatility_vs_btc", "Pair Volatility vs BTC", ("more volatile than btc",)),
                ("pair_move_relative_btc", "Pair Move Relative to BTC", ("relative move to btc",)),
            ),
        ),
        (
            "crypto_index",
            "Crypto index provider",
            "market_context",
            (
                ("total_market_cap_trend", "TOTAL Market Cap Trend", ("total market cap",)),
                ("total2_trend", "TOTAL2 Trend", ("total2",)),
                ("total3_trend", "TOTAL3 Trend", ("total3", "alts stronger than btc")),
                ("btc_dominance_trend", "BTC Dominance Trend", ("btc dominance",)),
                ("usdt_dominance_trend", "USDT Dominance Trend", ("usdt dominance",)),
                (
                    "stablecoin_dominance_trend",
                    "Stablecoin Dominance Trend",
                    ("stablecoin dominance",),
                ),
                ("altcoin_market_cap_vs_ma", "Altcoin Market Cap vs MA", ("alt market cap",)),
                ("altseason_context", "Altseason-style Context", ("altseason",)),
                ("risk_on_crypto_context", "Risk-on Crypto Context", ("crypto risk on",)),
                ("risk_off_crypto_context", "Risk-off Crypto Context", ("crypto risk off",)),
            ),
        ),
        (
            "macro_market",
            "External macro provider",
            "market_context",
            tuple(
                (f"{key}_trend_filter", f"{label} Trend Filter", (label.casefold(),))
                for key, label in (
                    ("dxy", "DXY"),
                    ("spx", "SPX"),
                    ("nasdaq", "NASDAQ"),
                    ("gold", "Gold"),
                    ("us10y", "US 10Y Yield"),
                    ("vix", "VIX"),
                )
            ),
        ),
        (
            "market_breadth",
            "Universe breadth aggregator",
            "market_context",
            (
                (
                    "universe_above_ema50_percent",
                    "Universe Above EMA 50 %",
                    ("breadth above ema 50",),
                ),
                (
                    "universe_above_ema200_percent",
                    "Universe Above EMA 200 %",
                    ("breadth above ema 200",),
                ),
                ("universe_positive_24h_percent", "Universe Positive 24h %", ("positive breadth",)),
                (
                    "universe_n_day_high_percent",
                    "Universe Making N-day Highs %",
                    ("new high breadth",),
                ),
                ("universe_volume_spike_percent", "Universe Volume Spike %", ("volume breadth",)),
                ("breadth_thrust", "Breadth Thrust", ("breadth thrust",)),
                (
                    "market_breadth_deteriorating",
                    "Market Breadth Deteriorating",
                    ("breadth weakening",),
                ),
                ("market_breadth_improving", "Market Breadth Improving", ("breadth improving",)),
            ),
        ),
        (
            "token_categories",
            "Token category provider",
            "market_context",
            tuple(
                (key, label, (label.casefold(),))
                for key, label in (
                    ("category_outperforming_market", "Category Outperforming Market"),
                    ("category_underperforming_market", "Category Underperforming Market"),
                    ("ai_coins_trending", "AI Coins Trending"),
                    ("defi_coins_trending", "DeFi Coins Trending"),
                    ("meme_coins_trending", "Meme Coins Trending"),
                    ("layer1_coins_trending", "Layer 1 Coins Trending"),
                    ("gaming_coins_trending", "Gaming Coins Trending"),
                    ("exchange_tokens_trending", "Exchange Tokens Trending"),
                )
            ),
        ),
        (
            "event_feed",
            "Event and economic calendar provider",
            "news_events",
            tuple(
                (key, label, (label.casefold(),))
                for key, label in (
                    ("major_exchange_listing_event", "Major Exchange Listing"),
                    ("major_exchange_delisting_event", "Major Exchange Delisting"),
                    ("token_unlock_upcoming", "Token Unlock Upcoming"),
                    ("token_unlock_occurred", "Token Unlock Just Occurred"),
                    ("airdrop_snapshot_event", "Airdrop / Snapshot Event"),
                    ("mainnet_launch_event", "Mainnet Launch"),
                    ("protocol_upgrade_event", "Protocol Upgrade"),
                    ("governance_vote_event", "Governance Vote"),
                    ("security_exploit_event", "Security Exploit / Hack News"),
                    ("stablecoin_depeg_event", "Stablecoin Depeg Event"),
                    ("institutional_news_event", "ETF / Institutional News"),
                    ("regulatory_headline_event", "Regulatory Headline"),
                    ("high_impact_market_news", "High-impact Market News"),
                    ("cpi_event_window", "CPI Event Window"),
                    ("fomc_event_window", "FOMC Event Window"),
                    ("fed_rate_decision_window", "Fed Rate Decision Window"),
                    ("nfp_event_window", "NFP Event Window"),
                    ("gdp_event_window", "GDP Event Window"),
                    ("economic_calendar_event", "High-impact Economic Calendar Event"),
                    ("event_actual_above_forecast", "Event Actual Above Forecast"),
                    ("event_actual_below_forecast", "Event Actual Below Forecast"),
                    ("event_surprise_magnitude", "Event Surprise Magnitude"),
                )
            ),
        ),
        (
            "order_book",
            "Order-book snapshot provider",
            "order_book_liquidity",
            tuple(
                (key, label, (label.casefold(),))
                for key, label in (
                    ("spread_below_threshold", "Bid / Ask Spread Below Threshold"),
                    ("spread_above_threshold", "Bid / Ask Spread Above Threshold"),
                    ("order_book_depth_above", "Order Book Depth Above Threshold"),
                    ("bid_ask_depth_imbalance", "Bid / Ask Depth Imbalance"),
                    ("large_wall_above_price", "Large Wall Above Price"),
                    ("large_wall_below_price", "Large Wall Below Price"),
                    ("liquidity_wall_pulled", "Liquidity Wall Pulled"),
                    ("liquidity_wall_added", "Liquidity Wall Added"),
                    ("approaching_liquidity_wall", "Price Approaching Liquidity Wall"),
                    ("slippage_below_threshold", "Slippage Estimate Below Threshold"),
                    ("trade_count_spike", "Trade Count Spike"),
                    ("average_trade_size_spike", "Average Trade Size Spike"),
                    ("aggressive_buy_volume_proxy", "Aggressive Buy Volume Proxy"),
                    ("aggressive_sell_volume_proxy", "Aggressive Sell Volume Proxy"),
                    ("trade_buy_sell_imbalance", "Trade Buy / Sell Imbalance"),
                    ("volume_burst_seconds", "Volume Burst in Last N Seconds"),
                )
            ),
        ),
        (
            "derivatives",
            "Derivatives market provider",
            "market_context",
            tuple(
                (key, label, (label.casefold(),))
                for key, label in (
                    ("long_liquidation_spike", "Long Liquidation Spike"),
                    ("short_liquidation_spike", "Short Liquidation Spike"),
                    ("open_interest_rising", "Open Interest Rising"),
                    ("open_interest_falling", "Open Interest Falling"),
                    ("funding_rate_positive", "Funding Rate Positive"),
                    ("funding_rate_negative", "Funding Rate Negative"),
                    ("funding_rate_extreme", "Funding Rate Extreme"),
                    ("price_up_oi_up", "Price Up + OI Up"),
                    ("price_up_oi_down", "Price Up + OI Down"),
                    ("price_down_oi_up", "Price Down + OI Up"),
                    ("price_down_oi_down", "Price Down + OI Down"),
                )
            ),
        ),
        (
            "universe_ranking",
            "Two-pass universe ranking service",
            "ranking_universe",
            tuple(
                (key, label, (label.casefold(),))
                for key, label in (
                    ("top_percent_24h_volume", "Top X% by 24h Volume"),
                    ("top_percent_1h_volume_change", "Top X% by 1h Volume Change"),
                    ("top_percent_relative_volume", "Top X% by Relative Volume"),
                    ("top_percent_momentum", "Top X% by Momentum"),
                    ("top_percent_volatility", "Top X% by Volatility"),
                    ("bottom_percent_volatility", "Bottom X% by Volatility"),
                    ("top_percent_trend_strength", "Top X% by Trend Strength"),
                    ("top_percent_distance_ema", "Top X% by Distance from EMA"),
                    ("near_24h_high", "Near 24h High"),
                    ("near_24h_low", "Near 24h Low"),
                    ("highest_volume_expansion", "Highest Volume Expansion"),
                    ("highest_compression_score", "Highest Compression Score"),
                    ("strongest_breakout_score", "Strongest Breakout Score"),
                    ("strongest_pullback_score", "Strongest Pullback Score"),
                    ("strongest_btc_relative_strength", "Strongest BTC-relative Strength"),
                )
            ),
        ),
    )
    specs: list[CapabilitySpec] = []
    existing = {capability.key for capability in CAPABILITIES}
    for provider, provider_label, builder_category, rows in groups:
        for key, label, aliases in rows:
            if key in existing:
                continue
            item_builder_category = (
                "relative_strength"
                if provider == "cross_market"
                and any(
                    term in key
                    for term in (
                        "relative",
                        "outperform",
                        "underperform",
                        "correlation",
                        "beta",
                        "volatility",
                        "pair_move",
                    )
                )
                else builder_category
            )
            specs.append(
                _cap(
                    key,
                    label,
                    "advanced",
                    "market_filter",
                    f"{label} supplied by {provider_label}; no value is inferred without data.",
                    aliases=aliases,
                    operand_kind="market_metric",
                    operand_name=key,
                    default_parameters={
                        "provider": provider,
                        "context_category": provider,
                    },
                    default_comparator="is_true",
                    default_threshold=True,
                    required_data=(provider,),
                    executable=True,
                    availability="available",
                    provider_required=provider,
                    phase=3
                    if provider not in {"cross_market", "market_breadth", "universe_ranking"}
                    else 2,
                    builder_category=item_builder_category,
                    guidance=(
                        f"{provider_label} is evaluated deterministically when configured; "
                        "missing data produces an unavailable proof state."
                    ),
                    examples=(f"{label} is present as context; it does not recommend a trade.",),
                    test_cases=(
                        "provider value present",
                        "provider value false",
                        "provider unavailable",
                    ),
                )
            )
    return specs


def _risk_quality_capabilities() -> list[CapabilitySpec]:
    rows = (
        "stop_distance_atr_units",
        "stop_distance_too_tight",
        "stop_distance_too_wide",
        "target_distance_next_resistance",
        "target_distance_next_support",
        "r_multiple_before_obstacle",
        "liquidity_obstacle_before_target",
        "minimum_clean_path_to_target",
        "price_moved_too_far_from_trigger",
        "candle_overextended",
        "spread_too_wide_at_alert",
        "volatility_too_high",
        "volatility_too_low",
        "setup_age_too_old",
        "invalidation_not_calculable",
        "risk_context_incomplete",
        "target_overlaps_obstacle",
        "reward_to_risk_after_fees",
        "reward_to_risk_after_slippage",
        "maximum_alert_lateness",
        "maximum_data_latency",
        "minimum_candle_liquidity",
    )
    numeric_defaults: dict[str, tuple[str, float]] = {
        "stop_distance_atr_units": ("lte", 2),
        "target_distance_next_resistance": ("gte", 0),
        "target_distance_next_support": ("gte", 0),
        "r_multiple_before_obstacle": ("gte", 1),
        "minimum_clean_path_to_target": ("gte", 1),
        "price_moved_too_far_from_trigger": ("lte", 2),
        "candle_overextended": ("lte", 2),
        "spread_too_wide_at_alert": ("lte", 20),
        "volatility_too_high": ("lte", 5),
        "volatility_too_low": ("gte", 0.1),
        "setup_age_too_old": ("lte", 240),
        "reward_to_risk_after_fees": ("gte", 2),
        "reward_to_risk_after_slippage": ("gte", 2),
        "maximum_alert_lateness": ("lte", 60_000),
        "maximum_data_latency": ("lte", 60_000),
        "minimum_candle_liquidity": ("gte", 0),
    }
    negative_boolean = {
        "stop_distance_too_tight",
        "stop_distance_too_wide",
        "liquidity_obstacle_before_target",
        "invalidation_not_calculable",
        "risk_context_incomplete",
        "target_overlaps_obstacle",
    }
    return [
        _cap(
            key,
            key.replace("_", " ").title(),
            "risk",
            "risk",
            (
                "Post-condition risk and trade-quality context evaluated from the proof "
                "and risk result."
            ),
            aliases=(key.replace("_", " "),),
            operand_kind="risk_metric",
            operand_name=key,
            default_comparator=(
                "is_false"
                if key in negative_boolean
                else numeric_defaults.get(key, ("is_true", True))[0]
            ),
            default_threshold=(
                False
                if key in negative_boolean
                else numeric_defaults.get(key, ("is_true", True))[1]
            ),
            executable=True,
            availability="available",
            provider_required="risk_context",
            phase=2,
            builder_category="risk_trade_quality",
            guidance=(
                "Risk geometry is calculated before the condition tree and may block the "
                "same deterministic evaluation."
            ),
            examples=(f"{key.replace('_', ' ').title()} satisfies the configured quality limit.",),
            test_cases=("risk context pass", "risk context fail", "risk context unavailable"),
        )
        for key in rows
    ]


def _runtime_context_capabilities() -> list[CapabilitySpec]:
    groups = {
        "alert_behavior": (
            "same_symbol_alert_cooldown",
            "same_strategy_alert_cooldown",
            "maximum_alerts_per_hour_condition",
            "daily_alert_budget_condition",
            "alert_only_on_state_change",
            "maximum_alert_lateness_condition",
        ),
        "setup_lifecycle": (
            "setup_state_is",
            "setup_age_minutes",
            "setup_first_detected_within",
            "setup_entry_zone_active",
            "setup_not_invalidated",
            "setup_not_expired",
        ),
    }
    parameters_by_key: dict[str, dict[str, Any]] = {
        "same_symbol_alert_cooldown": {"cooldown_minutes": 60},
        "same_strategy_alert_cooldown": {"cooldown_minutes": 60},
        "maximum_alerts_per_hour_condition": {"maximum_alerts": 50},
        "daily_alert_budget_condition": {"daily_budget": 200},
        "maximum_alert_lateness_condition": {"maximum_lateness_ms": 60_000},
        "setup_state_is": {"state": "forming"},
        "setup_first_detected_within": {"minutes": 60},
    }
    return [
        _cap(
            key,
            key.replace("_", " ").title(),
            "advanced",
            "market_filter",
            "Runtime policy condition evaluated from persisted alert or setup lifecycle context.",
            aliases=(key.replace("_", " "),),
            operand_kind="market_metric",
            operand_name=key,
            default_parameters={
                "context_category": builder_category,
                **parameters_by_key.get(key, {}),
            },
            default_comparator="lte" if key == "setup_age_minutes" else "is_true",
            default_threshold=60 if key == "setup_age_minutes" else True,
            executable=True,
            availability="available",
            provider_required=builder_category,
            phase=2,
            builder_category=builder_category,
            guidance=(
                "The live scanner supplies persisted alert and setup state to this condition."
            ),
            examples=(f"{key.replace('_', ' ').title()} is satisfied.",),
            test_cases=("runtime context pass", "runtime context fail", "context unavailable"),
        )
        for builder_category, keys in groups.items()
        for key in keys
    ]


def _extend_capabilities() -> tuple[CapabilitySpec, ...]:
    existing = {capability.key for capability in CAPABILITIES}
    extensions: list[CapabilitySpec] = []
    for factory in (
        _extended_indicator_capabilities,
        _candle_pattern_capabilities,
        _price_action_capabilities,
        _time_capabilities,
        _provider_capabilities,
        _risk_quality_capabilities,
        _runtime_context_capabilities,
    ):
        for capability in factory():
            if capability.key not in existing:
                extensions.append(capability)
                existing.add(capability.key)
    return tuple(extensions)


CAPABILITIES += _extend_capabilities()


SYNONYMS: tuple[SynonymSpec, ...] = (
    SynonymSpec(
        "pump", "percent_change_lookback", {"direction": "up", "threshold_percent": 3}, 0.75
    ),
    SynonymSpec(
        "dump", "percent_change_lookback", {"direction": "down", "threshold_percent": 3}, 0.75
    ),
    SynonymSpec("breakout", "range_breakout", {"lookback": 40}, 0.9),
    SynonymSpec("breakdown", "range_breakdown", {"lookback": 40}, 0.9),
    SynonymSpec("sweep lows", "bullish_liquidity_sweep", {"lookback": 20}, 0.95),
    SynonymSpec("sweep highs", "bearish_liquidity_sweep", {"lookback": 20}, 0.95),
    SynonymSpec("reclaim", "ma_reclaim", {}, 0.75),
    SynonymSpec("reject", "resistance_retest", {"lookback": 20}, 0.8),
    SynonymSpec("bounce", "support_retest", {"lookback": 20}, 0.8),
    SynonymSpec("trend filter", "price_above_ema", {"period": 200, "timeframe": "4h"}, 0.85),
    SynonymSpec("oversold", "rsi_threshold", {"threshold": 30, "comparator": "lte"}, 0.9),
    SynonymSpec("overbought", "rsi_threshold", {"threshold": 70, "comparator": "gte"}, 0.9),
    SynonymSpec("strong volume", "volume_spike", {"threshold": 1.5}, 0.9),
    SynonymSpec("low volume pullback", "volume_dry_up", {"threshold": 0.8}, 0.9),
    SynonymSpec("close near high", "strong_close_near_high", {}, 0.95),
    SynonymSpec("close near low", "strong_close_near_low", {}, 0.95),
    SynonymSpec("good r:r", "risk_reward", {"minimum_reward_to_risk": 2}, 0.8),
    SynonymSpec("tight stop", "risk", {"maximum_stop_percent": 2}, 0.8),
    SynonymSpec("safe stop", "atr_stop", {"method": "structure_or_atr"}, 0.7, True),
    SynonymSpec("scalping", "timeframe", {"preferred_timeframes": ["1m", "3m", "5m"]}, 0.7),
    SynonymSpec("swing", "timeframe", {"preferred_timeframes": ["4h", "1d"]}, 0.7),
    SynonymSpec("spot only", "market_type", {"market_type": "spot"}, 0.99),
    SynonymSpec(
        "majors only",
        "symbol_universe",
        {"symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]},
        0.8,
    ),
    SynonymSpec("alts", "symbol_universe", {"exclude": ["BTC/USDT", "ETH/USDT"]}, 0.8),
    SynonymSpec(
        "avoid low liquidity", "min_quote_volume_24h", {"min_quote_volume_24h": 1000000}, 0.85
    ),
    SynonymSpec("no meme coins", "meme_coin_exclusion", {}, 0.85, True),
)


STRATEGY_TEMPLATE_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {"key": "liquidity_sweep", "name": "Liquidity Sweep Continuation", "category": "price_action"},
    {"key": "rsi_pullback", "name": "RSI Pullback", "category": "indicator"},
    {"key": "vwap_reclaim", "name": "VWAP Reclaim", "category": "indicator"},
    {"key": "ema_trend_continuation", "name": "EMA Trend Continuation", "category": "trend"},
    {"key": "ema_crossover", "name": "EMA Crossover", "category": "trend"},
    {"key": "macd_momentum_shift", "name": "MACD Momentum Shift", "category": "momentum"},
    {
        "key": "bollinger_squeeze_breakout",
        "name": "Bollinger Squeeze Breakout",
        "category": "volatility",
    },
    {"key": "range_breakout_retest", "name": "Range Breakout Retest", "category": "price_action"},
    {"key": "breakout_volume", "name": "Volume Breakout", "category": "breakout"},
    {"key": "support_retest_bounce", "name": "Support Retest Bounce", "category": "price_action"},
    {
        "key": "resistance_rejection_short",
        "name": "Resistance Rejection Short",
        "category": "price_action",
    },
    {
        "key": "higher_low_continuation",
        "name": "Higher-Low Continuation",
        "category": "price_action",
    },
    {"key": "previous_high_breakout", "name": "Previous High Breakout", "category": "price_action"},
    {
        "key": "previous_low_sweep_reversal",
        "name": "Previous Low Sweep Reversal",
        "category": "price_action",
    },
    {"key": "low_volume_pullback", "name": "Low-Volume Pullback", "category": "volume"},
    {"key": "strong_close_momentum", "name": "Strong Close Momentum", "category": "candle"},
    {
        "key": "atr_volatility_expansion",
        "name": "ATR Volatility Expansion",
        "category": "volatility",
    },
    {"key": "stochastic_pullback", "name": "Stochastic Pullback", "category": "momentum"},
    {"key": "six_month_high_breakout", "name": "Six-Month High Breakout", "category": "breakout"},
    {
        "key": "btc_trend_filter_altcoin",
        "name": "BTC Trend Filter Altcoin Scanner",
        "category": "advanced",
    },
)


def all_capabilities() -> tuple[CapabilitySpec, ...]:
    return CAPABILITIES


def executable_capabilities() -> tuple[CapabilitySpec, ...]:
    return tuple(
        capability
        for capability in CAPABILITIES
        if capability.executable and capability.availability == "available"
    )


def unsupported_capabilities() -> tuple[CapabilitySpec, ...]:
    return tuple(
        capability
        for capability in CAPABILITIES
        if not capability.executable or capability.availability != "available"
    )


def capability_by_key() -> dict[str, CapabilitySpec]:
    return {capability.key: capability for capability in CAPABILITIES}


def capability_prompt_categories() -> dict[str, tuple[str, ...]]:
    categories: dict[str, list[str]] = {}
    for capability in CAPABILITIES:
        keywords = [capability.key.replace("_", " "), capability.label.lower(), *capability.aliases]
        bucket = categories.setdefault(capability.category, [])
        for keyword in keywords:
            normalized = keyword.casefold()
            if normalized not in bucket:
                bucket.append(normalized)
    return {key: tuple(values) for key, values in categories.items()}


def _unique_operand_names(condition_type: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    values: list[dict[str, Any]] = []
    for capability in CAPABILITIES:
        if capability.condition_type != condition_type or not capability.executable:
            continue
        name = capability.operand_name or capability.key
        if name in seen:
            continue
        seen.add(name)
        values.append(
            {
                "name": name,
                "label": capability.label,
                "category": capability.category,
                "parameters": capability.default_parameters,
            }
        )
    return sorted(values, key=lambda item: item["label"])


def capability_registry_payload() -> dict[str, Any]:
    items = [capability.to_dict() for capability in CAPABILITIES]
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_category.setdefault(item["category"], []).append(item)
    unsupported = [capability.to_dict() for capability in unsupported_capabilities()]
    return {
        "schema_version": "1.0",
        "counts": {
            "total": len(CAPABILITIES),
            "executable": len(executable_capabilities()),
            "recognized_not_executable": len(unsupported),
            "templates": len(STRATEGY_TEMPLATE_CAPABILITIES),
            "synonyms": len(SYNONYMS),
        },
        "items": items,
        "by_category": by_category,
        "condition_types": [
            {"value": "indicator", "label": "Indicator"},
            {"value": "price_action", "label": "Price action"},
            {"value": "candle_pattern", "label": "Candle pattern"},
            {"value": "market_filter", "label": "Market filter"},
            {"value": "risk", "label": "Risk"},
        ],
        "indicators": _unique_operand_names("indicator"),
        "price_actions": _unique_operand_names("price_action"),
        "candle_patterns": _unique_operand_names("candle_pattern"),
        "market_filters": _unique_operand_names("market_filter"),
        "risk_rules": _unique_operand_names("risk"),
        "session_time_rules": [
            capability.to_dict()
            for capability in CAPABILITIES
            if capability.category == "session_time"
        ],
        "aliases": [synonym.to_dict() for synonym in SYNONYMS],
        "unsupported": unsupported,
        "strategy_templates": list(STRATEGY_TEMPLATE_CAPABILITIES),
        "builder_defaults": {
            "condition_type": "indicator",
            "indicator": "volume_ratio",
            "price_action": "range_breakout",
            "candle_pattern": "strong_close_near_high",
            "timeframe": "15m",
            "weight": 1,
            "required": True,
        },
    }
