"""What the Guided Builder is allowed to offer, decided by the server.

The Builder must be able to create every launch-supported setup **without a single
model call**. That only stays true if the fields it shows come from the platform's own
contracts. A frontend that hard-codes "these are the timeframes" or "these are the
comparisons" is a second vocabulary, and the moment the compiler's list changes the
form starts offering something that will be refused — or, worse, quietly accepted as
something else.

So every field is derived here:

===========================  ============================================
What                         Where it comes from
===========================  ============================================
which comparisons a rule     ``FORMULA_CONTRACTS`` in the draft schema
can use
which side it can measure    the same contract's forbidden directions
what the threshold counts    the same contract's units
which timeframes exist       ``schemas/timeframes.ORDERED_TIMEFRAMES``
what a capability needs      the registry's own ``CapabilitySpec``
===========================  ============================================

Nothing in this module invents a value, and nothing in it decides whether a mechanic is
*safe* — it reports what the registry and the compiler already say. A mechanic the
platform cannot execute is returned with an explicit reason rather than left out, so
the Builder can say "not yet" instead of silently offering the nearest thing that works.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from ai_market_monitor.engine.capabilities import CapabilitySpec, all_capabilities
from ai_market_monitor.schemas.strategy import UNARY_COMPARATORS, Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    FORMULA_CONTRACTS,
    FormulaKind,
    MovementDirection,
)
from ai_market_monitor.schemas.timeframes import ORDERED_TIMEFRAMES

ParameterKind = Literal["number", "integer", "choice", "text", "boolean", "timeframe"]


@dataclass(frozen=True, slots=True)
class BuilderChoice:
    """One value a person may pick, with the words they will read."""

    value: str
    label: str
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class BuilderParameter:
    """One field on the form, and every rule the server puts on it."""

    name: str
    label: str
    kind: ParameterKind
    required: bool
    #: What the number counts, in the compiler's own vocabulary: percent, price, count,
    #: timeframe or none. The Builder shows the unit beside the box so nobody has to
    #: guess whether "5" means five percent or five dollars.
    unit: str = "none"
    help: str = ""
    choices: tuple[BuilderChoice, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    default: Any = None


@dataclass(frozen=True, slots=True)
class BuilderMechanic:
    """One thing the Builder can watch for, and everything needed to draw its form."""

    key: str
    version: str
    label: str
    explanation: str
    category: str
    #: The canonical formula this compiles to. Never shown to a person.
    formula: str
    capability_key: str | None = None
    parameters: tuple[BuilderParameter, ...] = ()
    operators: tuple[BuilderChoice, ...] = ()
    directions: tuple[BuilderChoice, ...] = ()
    timeframes: tuple[str, ...] = ()
    unit: str = "none"
    provider_requirements: tuple[str, ...] = ()
    #: False when the platform cannot run this right now. The Builder still lists it, so
    #: a person sees "not yet" instead of wondering where it went.
    available: bool = True
    unavailable_reason: str | None = None
    #: Whether a beginner can pick this without extra explanation. A hint for how the
    #: Builder groups its list — never a gate. It used to decide which capabilities the
    #: Builder offered at all, which left 455 of the 502 launch-supported mechanics
    #: reachable only by asking the assistant. Authoring must never require the AI.
    beginner_friendly: bool = False
    #: The data feeds this needs beyond candles, and whether the configured adapter
    #: actually provides them. Sent so the form can say which feed is missing instead of
    #: offering a rule that would compile and then never evaluate.
    provider_requirements_met: bool = True
    examples: tuple[str, ...] = field(default_factory=tuple)
    #: The words a trader would use for this, in their own language — "bb squeeze",
    #: "quote volume", "rsi above". They come from the capability registry, which is
    #: the same list the interpreter matches a written sentence against, so searching
    #: the Builder's own list finds a rule by exactly the words that would have found
    #: it in a sentence. Never shown; only searched.
    search_words: tuple[str, ...] = field(default_factory=tuple)
    #: Which of the fields above belong to the capability's own registry schema. The
    #: comparison, the side and the candle size live on the rule itself, not inside the
    #: capability, and putting them in its parameter bag is refused as an invented
    #: parameter. Recorded here so the compiler never has to guess which is which.
    registry_parameter_names: frozenset[str] = frozenset()

    def parameter(self, name: str) -> BuilderParameter | None:
        return next((item for item in self.parameters if item.name == name), None)


#: How each comparison reads to somebody who has never used a trading tool. The values
#: are the compiler's, the words are ours; keeping them here means one place to fix if a
#: phrase turns out to confuse people.
COMPARATOR_LABELS: dict[Comparator, tuple[str, str]] = {
    Comparator.GREATER_THAN: ("more than", "Bigger than the number you set."),
    Comparator.GREATER_THAN_OR_EQUAL: (
        "at least",
        "The number you set, or anything bigger.",
    ),
    Comparator.LESS_THAN: ("less than", "Smaller than the number you set."),
    Comparator.LESS_THAN_OR_EQUAL: (
        "at most",
        "The number you set, or anything smaller.",
    ),
    Comparator.EQUAL: ("exactly", "The same as the number you set."),
    Comparator.CROSSES_ABOVE: (
        "crosses up through",
        "Was below the level, and has just moved above it.",
    ),
    Comparator.CROSSES_BELOW: (
        "crosses down through",
        "Was above the level, and has just moved below it.",
    ),
    Comparator.IS_TRUE: ("happens", "The event you chose takes place."),
    Comparator.IS_FALSE: ("does not happen", "The event you chose does not take place."),
}

DIRECTION_LABELS: dict[MovementDirection, tuple[str, str]] = {
    MovementDirection.UP: ("goes up", "The price rises."),
    MovementDirection.DOWN: ("goes down", "The price falls."),
    MovementDirection.NEUTRAL: ("either way", "It does not matter which way it moves."),
    MovementDirection.NOT_APPLICABLE: ("not used", "Direction does not apply to this rule."),
}

#: The candle fields a rule can measure or point at. One list, used for every mechanic
#: that needs one.
_PRICE_FIELDS: tuple[BuilderChoice, ...] = (
    BuilderChoice("close", "closing price", "The price when the candle finished."),
    BuilderChoice("open", "opening price", "The price when the candle started."),
    BuilderChoice("high", "highest price", "The highest price inside the candle."),
    BuilderChoice("low", "lowest price", "The lowest price inside the candle."),
)


def _timeframe_parameter(*, label: str = "Candle size") -> BuilderParameter:
    return BuilderParameter(
        name="timeframe",
        label=label,
        kind="timeframe",
        required=True,
        unit="timeframe",
        help="How long each candle covers. 15m means each candle is fifteen minutes.",
        choices=tuple(
            BuilderChoice(value, value, "") for value in ORDERED_TIMEFRAMES
        ),
        default="15m",
    )


def _comparator_choices(formula: FormulaKind) -> tuple[BuilderChoice, ...]:
    """Only the comparisons this formula owns, in the compiler's own order."""

    allowed = FORMULA_CONTRACTS[formula].operators
    return tuple(
        BuilderChoice(item.value, COMPARATOR_LABELS[item][0], COMPARATOR_LABELS[item][1])
        for item in Comparator
        if item in allowed
    )


def _direction_choices(formula: FormulaKind) -> tuple[BuilderChoice, ...]:
    """Only the sides this formula can measure.

    A high-to-low move is a fall. Offering "goes up" for it would let somebody build a
    rule that inverts what they asked for, which is exactly what the contract forbids.
    """

    forbidden = FORMULA_CONTRACTS[formula].forbidden_directions
    return tuple(
        BuilderChoice(item.value, DIRECTION_LABELS[item][0], DIRECTION_LABELS[item][1])
        for item in (MovementDirection.UP, MovementDirection.DOWN, MovementDirection.NEUTRAL)
        if item not in forbidden
    )


def _formula_unit(formula: FormulaKind) -> str:
    """The one unit this formula's threshold is measured in."""

    units = FORMULA_CONTRACTS[formula].units
    # Every core formula owns exactly one unit. `capability` owns them all and is never
    # asked this question, because a capability carries its own unit.
    return sorted(units)[0] if len(units) == 1 else "none"


def _threshold_parameter(formula: FormulaKind, *, label: str, help_text: str) -> BuilderParameter:
    unit = _formula_unit(formula)
    return BuilderParameter(
        name="threshold",
        label=label,
        kind="number",
        required=True,
        unit=unit,
        help=help_text,
        minimum=0.01 if unit == "percent" else None,
        maximum=1000.0 if unit == "percent" else None,
        step=0.1 if unit == "percent" else None,
    )


def _comparator_parameter(formula: FormulaKind) -> BuilderParameter:
    return BuilderParameter(
        name="comparator",
        label="Compare how",
        kind="choice",
        required=True,
        help="How the measured number is compared with yours.",
        choices=_comparator_choices(formula),
    )


def _direction_parameter(formula: FormulaKind) -> BuilderParameter:
    choices = _direction_choices(formula)
    return BuilderParameter(
        name="direction",
        label="Which way",
        kind="choice",
        required=True,
        help="Whether you want the price going up, going down, or either.",
        choices=choices,
        default=choices[0].value if len(choices) == 1 else None,
    )


def _reference_field_parameter(
    *,
    label: str,
    help_text: str,
    choices: tuple[BuilderChoice, ...] = _PRICE_FIELDS,
    default: str | None = None,
) -> BuilderParameter:
    return BuilderParameter(
        name="reference_field",
        label=label,
        kind="choice",
        required=True,
        help=help_text,
        choices=choices,
        default=default,
    )


def _percentage_mechanic(
    formula: FormulaKind,
    *,
    label: str,
    explanation: str,
    threshold_label: str,
    examples: tuple[str, ...],
) -> BuilderMechanic:
    return BuilderMechanic(
        key=formula.value,
        version="1.0",
        label=label,
        explanation=explanation,
        category="Price move",
        formula=formula.value,
        parameters=(
            _direction_parameter(formula),
            _comparator_parameter(formula),
            _threshold_parameter(
                formula,
                label=threshold_label,
                help_text="Written as a percentage. 5 means five percent.",
            ),
            _timeframe_parameter(),
        ),
        operators=_comparator_choices(formula),
        directions=_direction_choices(formula),
        timeframes=tuple(ORDERED_TIMEFRAMES),
        unit=_formula_unit(formula),
        examples=examples,
    )


@lru_cache(maxsize=1)
def core_mechanics() -> tuple[BuilderMechanic, ...]:
    """Every mechanic the compiler runs without needing a registered capability.

    These are the launch grammar. Each one is a ``FormulaKind`` the compiler already
    executes, so offering it in the Builder adds no new execution path — only a way to
    reach it without describing it in a sentence.
    """

    return (
        _percentage_mechanic(
            FormulaKind.OPEN_TO_CLOSE_PERCENTAGE,
            label="Candle moves by a percentage",
            explanation=(
                "Compares where a candle closed with where it opened. Use this for "
                "“this candle moved 5%”."
            ),
            threshold_label="How big a move",
            examples=("Tell me when a coin moves up 5% in one hour.",),
        ),
        _percentage_mechanic(
            FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
            label="Price moves between two candles",
            explanation=(
                "Compares this candle's close with the one before it. Use this for a "
                "move measured candle to candle."
            ),
            threshold_label="How big a move",
            examples=("Tell me when the price rises 3% from the last candle.",),
        ),
        _percentage_mechanic(
            FormulaKind.HIGH_TO_LOW_PERCENTAGE,
            label="Drop from the candle's high",
            explanation=(
                "Measures the fall from the highest price inside the candle down to "
                "the lowest. This is always a fall."
            ),
            threshold_label="How big a drop",
            examples=("Tell me when a coin drops 4% inside one candle.",),
        ),
        _percentage_mechanic(
            FormulaKind.LOW_TO_HIGH_PERCENTAGE,
            label="Rise from the candle's low",
            explanation=(
                "Measures the rise from the lowest price inside the candle up to the "
                "highest. This is always a rise."
            ),
            threshold_label="How big a rise",
            examples=("Tell me when a coin bounces 4% inside one candle.",),
        ),
        _percentage_mechanic(
            FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE,
            label="Move away from an earlier price",
            explanation=(
                "Measures how far the price has travelled from an earlier candle's "
                "price to now."
            ),
            threshold_label="How far it moved",
            examples=("Tell me when the price is 10% above yesterday's close.",),
        ),
        BuilderMechanic(
            key=FormulaKind.PREVIOUS_CANDLE_REFERENCE.value,
            version="1.0",
            label="Price passes the last candle's high or low",
            explanation=(
                "Watches the price against the candle before this one. Use this for a "
                "break above the last high, or a break below the last low."
            ),
            category="Price level",
            formula=FormulaKind.PREVIOUS_CANDLE_REFERENCE.value,
            parameters=(
                _reference_field_parameter(
                    label="Which price of the last candle",
                    help_text="The level from the candle before this one.",
                    default="high",
                ),
                _comparator_parameter(FormulaKind.PREVIOUS_CANDLE_REFERENCE),
                _timeframe_parameter(),
            ),
            operators=_comparator_choices(FormulaKind.PREVIOUS_CANDLE_REFERENCE),
            directions=_direction_choices(FormulaKind.PREVIOUS_CANDLE_REFERENCE),
            timeframes=tuple(ORDERED_TIMEFRAMES),
            unit=_formula_unit(FormulaKind.PREVIOUS_CANDLE_REFERENCE),
            examples=("Tell me when the price breaks above the last candle's high.",),
        ),
        BuilderMechanic(
            key=FormulaKind.FIXED_REFERENCE_LEVEL.value,
            version="1.0",
            label="Price reaches a price you choose",
            explanation="Watches for the price reaching an exact number you type in.",
            category="Price level",
            formula=FormulaKind.FIXED_REFERENCE_LEVEL.value,
            parameters=(
                _comparator_parameter(FormulaKind.FIXED_REFERENCE_LEVEL),
                _threshold_parameter(
                    FormulaKind.FIXED_REFERENCE_LEVEL,
                    label="Price level",
                    help_text="The exact price to watch for, in the quote currency.",
                ),
                _timeframe_parameter(),
            ),
            operators=_comparator_choices(FormulaKind.FIXED_REFERENCE_LEVEL),
            directions=_direction_choices(FormulaKind.FIXED_REFERENCE_LEVEL),
            timeframes=tuple(ORDERED_TIMEFRAMES),
            unit=_formula_unit(FormulaKind.FIXED_REFERENCE_LEVEL),
            examples=("Tell me when Bitcoin reaches 70000.",),
        ),
        BuilderMechanic(
            key=FormulaKind.LOOKBACK_REFERENCE_LEVEL.value,
            version="1.0",
            label="Price reaches the highest or lowest of recent candles",
            explanation=(
                "Looks back over a number of candles you choose and watches the price "
                "against the highest or lowest point in that stretch."
            ),
            category="Price level",
            formula=FormulaKind.LOOKBACK_REFERENCE_LEVEL.value,
            parameters=(
                _reference_field_parameter(
                    label="Highest or lowest",
                    help_text="Which end of the recent range to watch.",
                    choices=(
                        BuilderChoice("high", "highest price", "The top of the stretch."),
                        BuilderChoice("low", "lowest price", "The bottom of the stretch."),
                    ),
                    default="high",
                ),
                BuilderParameter(
                    name="lookback",
                    label="How many candles back",
                    kind="integer",
                    required=True,
                    unit="count",
                    help="How far back to look. 20 means the last twenty candles.",
                    minimum=1,
                    maximum=1000,
                    step=1,
                    default=20,
                ),
                _comparator_parameter(FormulaKind.LOOKBACK_REFERENCE_LEVEL),
                _timeframe_parameter(),
            ),
            operators=_comparator_choices(FormulaKind.LOOKBACK_REFERENCE_LEVEL),
            directions=_direction_choices(FormulaKind.LOOKBACK_REFERENCE_LEVEL),
            timeframes=tuple(ORDERED_TIMEFRAMES),
            unit=_formula_unit(FormulaKind.LOOKBACK_REFERENCE_LEVEL),
            examples=("Tell me when the price passes the highest point of the last 20 candles.",),
        ),
        BuilderMechanic(
            key=FormulaKind.CROSS.value,
            version="1.0",
            label="Price crosses a level",
            explanation=(
                "Fires only at the moment the price passes through a level, not while "
                "it stays on one side of it."
            ),
            category="Price level",
            formula=FormulaKind.CROSS.value,
            parameters=(
                _reference_field_parameter(
                    label="Which level to cross",
                    help_text="The level from the candle before this one.",
                    default="high",
                ),
                _comparator_parameter(FormulaKind.CROSS),
                _timeframe_parameter(),
            ),
            operators=_comparator_choices(FormulaKind.CROSS),
            directions=_direction_choices(FormulaKind.CROSS),
            timeframes=tuple(ORDERED_TIMEFRAMES),
            unit=_formula_unit(FormulaKind.CROSS),
            examples=("Tell me when the price crosses up through the last candle's high.",),
        ),
        BuilderMechanic(
            key=FormulaKind.SWEEP_AND_RECLAIM.value,
            version="1.0",
            label="Price dips under a level then comes back",
            explanation=(
                "Two things in order: the price goes past a level, then returns to the "
                "side it came from. Traders call this a sweep and reclaim."
            ),
            category="Price pattern",
            formula=FormulaKind.SWEEP_AND_RECLAIM.value,
            parameters=(
                _reference_field_parameter(
                    label="Which level is swept",
                    help_text="The level from the candle before this one.",
                    choices=(
                        BuilderChoice(
                            "low",
                            "the last candle's low",
                            "The price dips below it, then closes back above.",
                        ),
                        BuilderChoice(
                            "high",
                            "the last candle's high",
                            "The price pokes above it, then closes back below.",
                        ),
                    ),
                    default="low",
                ),
                _comparator_parameter(FormulaKind.SWEEP_AND_RECLAIM),
                _timeframe_parameter(),
            ),
            operators=_comparator_choices(FormulaKind.SWEEP_AND_RECLAIM),
            directions=_direction_choices(FormulaKind.SWEEP_AND_RECLAIM),
            timeframes=tuple(ORDERED_TIMEFRAMES),
            unit=_formula_unit(FormulaKind.SWEEP_AND_RECLAIM),
            examples=("Tell me when the price sweeps the last low and reclaims it.",),
        ),
    )


#: Registry parameter names the platform fills in itself. A person is never asked for
#: them, so they never become form fields.
_PLATFORM_OWNED_PARAMETERS = frozenset(
    {"formula", "reference_field", "current_field", "scale", "direction", "comparator", "timeframe"}
)


def _capability_parameters(spec: CapabilitySpec) -> tuple[BuilderParameter, ...]:
    """Turn one capability's registry contract into form fields.

    Ranges, types and allowed values come from the capability's own JSON schema. Writing
    them out again in the Builder would be a second contract that drifts from the one
    the compiler enforces.
    """

    raw_properties = spec.parameter_schema.get("properties")
    properties: dict[str, Any] = raw_properties if isinstance(raw_properties, dict) else {}
    required = set(spec.parameter_schema.get("required") or ())
    declared = {item.name: item for item in spec.parameters}
    # The JSON schema is what the capability contract actually enforces, and most schemas
    # here set ``additionalProperties: false``. Offering a field the schema does not list
    # produced a form whose every submission was refused as an invented parameter.
    offerable = set(properties) if properties else set(declared)
    fields: list[BuilderParameter] = []
    for name in sorted(offerable):
        if name in _PLATFORM_OWNED_PARAMETERS:
            continue
        raw_rules = properties.get(name)
        rules: dict[str, Any] = raw_rules if isinstance(raw_rules, dict) else {}
        declaration = declared.get(name)
        json_type = str(rules.get("type") or (declaration.type if declaration else "string"))
        enum = rules.get("enum") or (list(declaration.options) if declaration else [])
        kind: ParameterKind = "text"
        if enum:
            kind = "choice"
        elif json_type in {"integer"}:
            kind = "integer"
        elif json_type in {"number"}:
            kind = "number"
        elif json_type == "boolean":
            kind = "boolean"
        default = (
            spec.default_parameters.get(name)
            if name in spec.default_parameters
            else rules.get("default", declaration.default if declaration else None)
        )
        fields.append(
            BuilderParameter(
                name=name,
                label=name.replace("_", " ").capitalize(),
                kind=kind,
                required=name in required or bool(declaration and declaration.required),
                unit=str(rules.get("x-semantic-unit") or _unit_for(name)),
                help=(declaration.description if declaration else "")
                or str(rules.get("description") or ""),
                choices=tuple(BuilderChoice(str(item), str(item)) for item in enum),
                minimum=_number_or_none(rules.get("minimum")),
                maximum=_number_or_none(rules.get("maximum")),
                default=default,
            )
        )
    return tuple(fields)


def _unit_for(name: str) -> str:
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
    return "none"


def _number_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _capability_mechanic(
    spec: CapabilitySpec,
    *,
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> BuilderMechanic:
    providers = spec.provider_requirements or (
        (spec.provider_required,) if spec.provider_required else ()
    )
    # A feed the configured adapter does not implement is the third way a rule can be
    # unrunnable, alongside "cannot execute" and "not switched on". It used to be handled
    # by hiding the capability from the Builder entirely, which is why 142 launch
    # capabilities could only be authored by asking the assistant. Saying which feed is
    # missing is both honest and something a person can act on.
    missing_providers = tuple(
        item for item in providers if item.strip().casefold() not in configured_providers
    )
    providers_met = not missing_providers
    # One capability can be switched off on its own — a formula found to be wrong, a feed
    # that started returning nonsense — without taking the Builder, the assistant or the
    # other 501 capabilities down with it. Switched off still means *shown* with a reason,
    # never hidden: a person who used this rule yesterday needs to know why it stopped,
    # and a capability that silently disappears looks like lost work.
    switched_off = spec.key in disabled_capabilities
    available = (
        spec.executable
        and spec.availability == "available"
        and providers_met
        and not switched_off
    )
    reason: str | None = None
    if switched_off:
        reason = "This rule is paused right now. Everything else still works."
    elif not spec.executable:
        reason = "Hilal Markets can read this rule but cannot run it yet."
    elif spec.availability != "available":
        reason = "This rule is not switched on for your account yet."
    elif missing_providers:
        feeds = ", ".join(sorted(missing_providers))
        reason = (
            f"This rule needs market data Hilal Markets does not receive yet ({feeds}). "
            "You can still set it up; it cannot be approved for monitoring until the "
            "data feed is connected."
        )
    operators = tuple(
        BuilderChoice(item.value, COMPARATOR_LABELS[item][0], COMPARATOR_LABELS[item][1])
        for item in Comparator
        if item.value in spec.supported_comparators
    )
    directions = tuple(
        BuilderChoice(
            direction.value,
            DIRECTION_LABELS[direction][0],
            DIRECTION_LABELS[direction][1],
        )
        for direction, word in (
            (MovementDirection.UP, "bullish"),
            (MovementDirection.DOWN, "bearish"),
            (MovementDirection.NEUTRAL, "neutral"),
        )
        if word in spec.direction_support
    )
    registry_fields = _capability_parameters(spec)
    # A capability that can measure more than one side needs the person to say which.
    # Without the field the side is fixed at "either way", and a rule the assistant wrote
    # about a fall could not be edited here without losing that.
    side_fields: list[BuilderParameter] = []
    if len(directions) > 1:
        side_fields.append(
            BuilderParameter(
                name="direction",
                label="Which way",
                kind="choice",
                required=False,
                help="Whether you want the price going up, going down, or either.",
                choices=directions,
                default=MovementDirection.NEUTRAL.value
                if any(item.value == MovementDirection.NEUTRAL.value for item in directions)
                else directions[0].value,
            )
        )
    # Some capabilities declare their own `threshold` in the registry schema. Adding a
    # second field with the same name put two boxes on the form for one value, and
    # whichever the person filled in last silently won.
    measures_a_number = bool(set(spec.supported_comparators) - UNARY_COMPARATORS)
    comparison_fields: list[BuilderParameter] = [
        BuilderParameter(
            name="comparator",
            label="Compare how",
            kind="choice",
            required=True,
            help=(
                "How the measured number is compared with yours."
                if measures_a_number
                else "Whether this has to happen, or has to not happen."
            ),
            choices=operators,
        )
    ]
    # A yes/no rule has nothing to compare against, so it gets no box to type one in.
    # The registry could not tell the two apart until it settled the comparison and the
    # allowed list together, so every rule was offered a "Value" field — including the
    # 377 that answer yes or no, where anything typed into it could never be used.
    if measures_a_number and not any(item.name == "threshold" for item in registry_fields):
        comparison_fields.append(
            BuilderParameter(
                name="threshold",
                label="Value",
                kind="number",
                # Not required as a form rule, because "happens" and "does not happen"
                # take no number at all. The compiler insists on one for every other
                # comparison, so nothing is loosened by this — the check simply lives
                # where it can see which comparison was chosen.
                required=False,
                unit="none",
                help="The number this rule compares against.",
                default=(
                    spec.default_threshold
                    if isinstance(spec.default_threshold, int | float)
                    and not isinstance(spec.default_threshold, bool)
                    else None
                ),
            )
        )
    return BuilderMechanic(
        key=f"capability:{spec.key}",
        version=spec.capability_version,
        label=spec.label,
        explanation=spec.description,
        category=str(spec.builder_category or spec.category),
        formula=FormulaKind.CAPABILITY.value,
        capability_key=spec.key,
        parameters=(
            *registry_fields,
            *side_fields,
            *comparison_fields,
            _timeframe_parameter(),
        ),
        operators=operators,
        directions=directions,
        timeframes=tuple(spec.supported_timeframes),
        unit="none",
        provider_requirements=tuple(providers),
        available=available,
        unavailable_reason=reason,
        beginner_friendly=bool(spec.beginner_friendly),
        provider_requirements_met=providers_met,
        examples=tuple(spec.examples[:2]),
        search_words=tuple(spec.aliases),
        registry_parameter_names=frozenset(item.name for item in registry_fields),
    )


def disabled_capabilities_from(raw: str) -> frozenset[str]:
    """Read the paused-capability list from configuration.

    One reader, so the catalogue the Builder shows and the check that refuses an action
    can never disagree about which capabilities are paused. Two readers would eventually
    let a person build a rule the server then refuses, with no explanation on either side.
    """

    return frozenset(
        item.strip()
        for item in str(raw or "").replace(";", ",").split(",")
        if item.strip()
    )


@lru_cache(maxsize=32)
def capability_mechanics(
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> tuple[BuilderMechanic, ...]:
    """Every launch-supported capability, as a Builder form.

    All of them, not a beginner-friendly subset. The Builder previously offered only
    capabilities marked ``beginner_friendly`` that needed no extra data feed — 47 of the
    502 that the platform actually supports — and pointed at Setup Chat for the rest.
    That made the assistant the only way to author 90% of the product, so an AI outage,
    an exhausted budget or a disabled flag took most of the feature set with it.

    Difficulty and missing data feeds are now *described* rather than used to hide a
    capability: ``beginner_friendly`` groups the list, ``provider_requirements`` and
    ``unavailable_reason`` say what is needed and what is missing. Nothing here loosens
    approval — an unavailable mechanic still cannot pass the provider gate.
    """

    return tuple(
        _capability_mechanic(
            spec,
            configured_providers=configured_providers,
            disabled_capabilities=disabled_capabilities,
        )
        for spec in sorted(all_capabilities(), key=lambda item: item.label)
        if spec.executable and spec.availability == "available"
    )


@lru_cache(maxsize=32)
def builder_mechanics(
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> tuple[BuilderMechanic, ...]:
    """Everything the Builder may offer, core grammar first."""

    return (
        *core_mechanics(),
        *capability_mechanics(configured_providers, disabled_capabilities),
    )


@lru_cache(maxsize=32)
def mechanics_by_key(
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> dict[str, BuilderMechanic]:
    return {
        item.key: item
        for item in builder_mechanics(configured_providers, disabled_capabilities)
    }


def find_mechanic(
    key: str,
    *,
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> BuilderMechanic | None:
    return mechanics_by_key(configured_providers, disabled_capabilities).get(key)


#: The screened-universe choices, in the platform's own vocabulary. The Builder never
#: invents a fourth one, and never assigns a Shariah status: choosing a scope selects a
#: governed list, it does not create one.
UNIVERSE_CHOICES: tuple[BuilderChoice, ...] = (
    BuilderChoice(
        "eligible_market",
        "Every eligible coin",
        "All spot coins that pass the Shariah screening you choose below.",
    ),
    BuilderChoice(
        "approved_watchlist",
        "One of my Favorites lists",
        "Only the coins on a list you have already saved.",
    ),
    BuilderChoice(
        "explicit_assets",
        "Coins I name myself",
        "Only the coins you type in. Each one is still screened.",
    ),
)

MODE_CHOICES: tuple[BuilderChoice, ...] = (
    BuilderChoice(
        "monitor",
        "Monitor",
        "Watches continuously and tells you the moment your rules match.",
    ),
    BuilderChoice(
        "scanner",
        "Scanner",
        "Looks across the market once, when you ask, and lists what matches now.",
    ),
)

LOGIC_CHOICES: tuple[BuilderChoice, ...] = (
    BuilderChoice("and", "All of these", "Every rule must match at the same time."),
    BuilderChoice("or", "Any of these", "One rule matching is enough."),
    BuilderChoice("not", "None of these", "Alert only when this does not match."),
)


def _guard_contract_is_complete() -> None:
    """Every comparison and every side must have words a beginner can read.

    A missing label renders an empty option in a dropdown. Somebody picks it, and the
    rule they build is not the rule they meant.
    """

    for comparator in Comparator:
        if comparator not in COMPARATOR_LABELS:
            raise RuntimeError(f"comparator {comparator.value} has no plain-language label")
    for direction in MovementDirection:
        if direction not in DIRECTION_LABELS:
            raise RuntimeError(f"direction {direction.value} has no plain-language label")


_guard_contract_is_complete()
