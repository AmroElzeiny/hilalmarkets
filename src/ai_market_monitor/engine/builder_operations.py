"""Turning a Builder form into canonical operations, with no model involved.

The Builder and Setup Chat write the same draft through the same authority. The only
difference is where the operations come from: the chat asks a model to read a sentence,
the Builder reads fields the server itself drew. Both then hand
:func:`~ai_market_monitor.engine.setup_turn_execution.apply_setup_turn` a typed
``AuthorizedPatchOperation`` list and every gate runs unchanged.

Everything here fails closed. A value that is not one this mechanic offers is refused
with a plain reason — never coerced to the nearest one it does offer, never clamped into
range, never silently dropped. A form that could substitute is a form that can build a
rule nobody asked for, which is the same defect as a compiler that guesses.

This module also reads in the other direction: :func:`describe_condition` turns a stored
rule back into the fields that would rebuild it. That one function is why an edit made
in the Builder and an edit made in chat produce the same card — there is one description
of what a rule *is*, not one per surface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from ai_market_monitor.engine.builder_boolean import (
    BooleanStructureError,
)
from ai_market_monitor.engine.builder_boolean import (
    group_conditions as boolean_group_conditions,
)
from ai_market_monitor.engine.builder_boolean import (
    move_condition as boolean_move_condition,
)
from ai_market_monitor.engine.builder_boolean import (
    set_group_operator as boolean_set_group_operator,
)
from ai_market_monitor.engine.builder_boolean import (
    ungroup as boolean_ungroup,
)
from ai_market_monitor.engine.builder_contract import (
    BuilderMechanic,
    BuilderParameter,
    builder_mechanics,
    find_mechanic,
)
from ai_market_monitor.engine.reference_levels import (
    lookback_level_name,
    previous_candle_level_name,
)
from ai_market_monitor.schemas.setup_authorization import AuthorizedPatchOperation
from ai_market_monitor.schemas.strategy import UNARY_COMPARATORS, Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    PERCENTAGE_MEASUREMENTS,
    ConditionNodeType,
    ConditionNodeV2,
    FormulaKind,
    MovementDirection,
    OperandV2,
    percentage_runtime_parameters,
)


class BuilderActionError(ValueError):
    """A Builder request the server will not turn into a change.

    Carries a code for the client and a sentence for the person. The sentence never
    names a field path or an internal identifier — the reader is a beginner.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BuilderPlan:
    """Operations to apply, and the words that describe them."""

    operations: tuple[AuthorizedPatchOperation, ...]
    #: A plain sentence describing what was chosen. It becomes the rule's stored
    #: provenance, so a person reading the setup later sees why each rule exists.
    rendered: str


#: The percentage formulas, read from the one table that says what each one measures.
#:
#: This used to be a hand-written copy of the compiler's names, and it stored *only* the
#: name — no reference field, no current field. The runtime then fell back to comparing
#: a candle's close with its own close, so every percentage card built here measured
#: 0.00% forever. Nothing is written out again now: `percentage_runtime_parameters`
#: supplies the whole measurement.
_PERCENTAGE_RUNTIME: frozenset[FormulaKind] = frozenset(PERCENTAGE_MEASUREMENTS)

_PRICE_OPERAND_FORMULAS: frozenset[FormulaKind] = frozenset(
    {
        FormulaKind.PREVIOUS_CANDLE_REFERENCE,
        FormulaKind.FIXED_REFERENCE_LEVEL,
        FormulaKind.LOOKBACK_REFERENCE_LEVEL,
        FormulaKind.CROSS,
    }
)

def _readable(value: Any) -> Any:
    """One setting, as a reader would say it.

    ``True`` and ``False`` are Python's words, not a trader's. The sentence the Builder
    writes back for somebody to read and approve was saying "with confirmation required
    False" to a person who has never seen code.
    """

    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, str) and "_" in value:
        # A picked option keeps the platform's spelling as its stored value, and that
        # spelling was going straight into the sentence: "session asia_session",
        # "mode break_high", "component price_above_cloud". The stored value is
        # untouched; only the reading of it changes.
        return value.replace("_", " ")
    return value


#: Words for each candle field, used when a rule is written out for the person.
_FIELD_WORDS: dict[str, str] = {
    "close": "closing price",
    "open": "opening price",
    "high": "highest price",
    "low": "lowest price",
}


# ---------------------------------------------------------------------------
# Reading the form
# ---------------------------------------------------------------------------


#: What the Builder says about a mechanic it cannot actually build a valid rule from.
#: Honest rather than hidden: the rule is still reachable through Setup Chat, where the
#: assistant can supply the parts a simple form cannot.
ASSISTANT_ONLY_REASON = (
    "This rule needs the assistant for now. Describe it in the chat and it will be added."
)


@lru_cache(maxsize=8)
def mechanic_catalog(
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> tuple[BuilderMechanic, ...]:
    """Every mechanic, with ``available`` corrected by actually trying to build one.

    The contract describes what a mechanic *is*. This asks the harder question: given
    only the fields the Builder shows, can a valid rule be produced? A mechanic that
    cannot is marked unavailable with a plain reason, so the Builder never offers a
    form whose every submission would be refused.

    The check runs the real validators — the registry contract and the draft's own
    semantic rules — so it cannot drift from what the mutation path enforces.
    """

    checked: list[BuilderMechanic] = []
    for mechanic in builder_mechanics(configured_providers, disabled_capabilities):
        if not mechanic.available:
            checked.append(mechanic)
            continue
        if _self_check(mechanic):
            checked.append(mechanic)
            continue
        checked.append(
            replace(mechanic, available=False, unavailable_reason=ASSISTANT_ONLY_REASON)
        )
    return tuple(checked)


@lru_cache(maxsize=8)
def _catalog_by_key(
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> dict[str, BuilderMechanic]:
    return {
        item.key: item
        for item in mechanic_catalog(configured_providers, disabled_capabilities)
    }


def offered_mechanics(
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> tuple[BuilderMechanic, ...]:
    """The mechanics a person can actually pick right now."""

    return tuple(
        item
        for item in mechanic_catalog(configured_providers, disabled_capabilities)
        if item.available
    )


#: Shapes a required free-text field really expects, for the availability probe only.
#: Never stored, never shown; it exists so the probe can answer its own question.
_PROBE_TEXT: dict[str, str] = {
    "timestamp": "2026-01-01T00:00:00+00:00",
    "timezone": "UTC",
}


def _probe_values(mechanic: BuilderMechanic) -> dict[str, Any]:
    """Plausible values for every field, used only to test that the form can build."""

    values: dict[str, Any] = {}
    for parameter in mechanic.parameters:
        if parameter.default is not None:
            values[parameter.name] = parameter.default
        elif parameter.choices:
            values[parameter.name] = parameter.choices[0].value
        elif parameter.kind == "integer":
            values[parameter.name] = int(parameter.minimum if parameter.minimum else 1)
        elif parameter.kind == "number":
            values[parameter.name] = float(parameter.minimum if parameter.minimum else 1)
        elif parameter.kind == "boolean":
            values[parameter.name] = False
        elif parameter.required:
            # A required field this probe cannot fill is not evidence that the form is
            # broken — it is evidence that the probe never learned to fill it. Text and
            # timezone fields were missing here, so every mechanic with a required one
            # was quietly marked "needs the assistant" and vanished from the Builder.
            values[parameter.name] = _PROBE_TEXT.get(parameter.name, "probe")
    if mechanic.operators:
        values["comparator"] = mechanic.operators[0].value
    if mechanic.directions and mechanic.parameter("direction") is not None:
        values["direction"] = mechanic.directions[0].value
    if mechanic.parameter("timeframe") is not None and "timeframe" not in values:
        values["timeframe"] = mechanic.timeframes[0] if mechanic.timeframes else "15m"
    return {name: value for name, value in values.items() if mechanic.parameter(name)}


def _self_check(mechanic: BuilderMechanic) -> bool:
    from ai_market_monitor.engine.capability_contract import validate_capability_node
    from ai_market_monitor.engine.strategy_compiler_v2 import (
        StrategyV2CompileError,
        compile_strategy_draft_v2,
    )
    from ai_market_monitor.engine.strategy_draft_v2 import validate_draft_semantics
    from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2

    try:
        node, sentence = _build(
            mechanic,
            _probe_values(mechanic),
            source_turn_id="builder_self_check",
            node_id=None,
            required=True,
        )
    except BuilderActionError:
        return False
    if mechanic.capability_key:
        result = validate_capability_node(
            node,
            authorizing_text=sentence,
            allowed_keys=frozenset({mechanic.capability_key}),
            language_grounded=False,
        )
        if not result.ok:
            return False
    draft = StrategyDraftV2(condition_ast=node)
    if validate_draft_semantics(draft):
        return False
    # The last and strictest question: does it actually compile into something the
    # runtime can evaluate? A mechanic that validates but will not compile produces a
    # rule the person can save and can never run.
    try:
        compile_strategy_draft_v2(draft)
    except StrategyV2CompileError:
        return False
    return True


def _require_mechanic(
    mechanic_key: str,
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> BuilderMechanic:
    mechanic = _catalog_by_key(
        configured_providers, disabled_capabilities
    ).get(mechanic_key) or find_mechanic(
        mechanic_key,
        configured_providers=configured_providers,
        disabled_capabilities=disabled_capabilities,
    )
    if mechanic is None:
        raise BuilderActionError(
            "MECHANIC_UNKNOWN",
            "That kind of rule is not one Hilal Markets offers.",
        )
    if not mechanic.available:
        raise BuilderActionError(
            "MECHANIC_UNAVAILABLE",
            mechanic.unavailable_reason
            or "That kind of rule cannot be used on this account yet.",
        )
    return mechanic


#: A wrong comparison, a wrong side and a wrong candle size are three different mistakes
#: with three different fixes, so each says which it was. Anything else is a plain
#: "not one of the choices".
_CHOICE_CODES: dict[str, str] = {
    "comparator": "COMPARISON_NOT_OFFERED",
    "direction": "DIRECTION_NOT_OFFERED",
    "timeframe": "TIMEFRAME_NOT_OFFERED",
}

_CHOICE_MESSAGES: dict[str, str] = {
    "comparator": "That comparison cannot be used with this kind of rule.",
    "direction": "That direction cannot be used with this kind of rule.",
    "timeframe": "That candle size cannot be used with this kind of rule.",
}


def _read_choice(parameter: BuilderParameter, raw: Any) -> str:
    value = str(raw).strip()
    allowed = {item.value for item in parameter.choices}
    if value not in allowed:
        raise BuilderActionError(
            _CHOICE_CODES.get(parameter.name, "VALUE_NOT_OFFERED"),
            _CHOICE_MESSAGES.get(
                parameter.name,
                f"“{parameter.label}” must be one of the choices shown.",
            ),
        )
    return value


def _read_number(parameter: BuilderParameter, raw: Any) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise BuilderActionError(
            "VALUE_NOT_A_NUMBER",
            f"“{parameter.label}” needs a number.",
        )
    try:
        value = float(str(raw).strip())
    except ValueError as exc:
        raise BuilderActionError(
            "VALUE_NOT_A_NUMBER",
            f"“{parameter.label}” needs a number.",
        ) from exc
    if parameter.kind == "integer" and value != int(value):
        raise BuilderActionError(
            "VALUE_NOT_A_WHOLE_NUMBER",
            f"“{parameter.label}” needs a whole number.",
        )
    # Out of range is refused, never clamped. Clamping turns "RSI at least 999" into a
    # rule about 100 that the person never wrote.
    if parameter.minimum is not None and value < parameter.minimum:
        raise BuilderActionError(
            "VALUE_OUT_OF_RANGE",
            f"“{parameter.label}” cannot be smaller than {parameter.minimum:g}.",
        )
    if parameter.maximum is not None and value > parameter.maximum:
        raise BuilderActionError(
            "VALUE_OUT_OF_RANGE",
            f"“{parameter.label}” cannot be larger than {parameter.maximum:g}.",
        )
    return value


def _read_values(mechanic: BuilderMechanic, values: dict[str, Any]) -> dict[str, Any]:
    """Every field checked against the contract that drew it.

    A field the mechanic never declared is refused rather than ignored: it means the
    client and the server disagree about what this form is, and acting on the half they
    agree about would build a rule from a partly-understood request.
    """

    declared = {item.name for item in mechanic.parameters}
    unknown = sorted(set(values) - declared)
    if unknown:
        raise BuilderActionError(
            "FIELD_NOT_OFFERED",
            "That rule was sent with a setting this form does not have.",
        )
    read: dict[str, Any] = {}
    for parameter in mechanic.parameters:
        if parameter.name not in values or values[parameter.name] in (None, ""):
            if parameter.required and parameter.default is None:
                raise BuilderActionError(
                    "VALUE_REQUIRED",
                    f"“{parameter.label}” is needed before this rule can be saved.",
                )
            if parameter.default is not None:
                read[parameter.name] = parameter.default
            continue
        raw = values[parameter.name]
        if parameter.kind == "choice" or parameter.kind == "timeframe":
            read[parameter.name] = _read_choice(parameter, raw)
        elif parameter.kind in {"number", "integer"}:
            number = _read_number(parameter, raw)
            read[parameter.name] = int(number) if parameter.kind == "integer" else number
        elif parameter.kind == "boolean":
            read[parameter.name] = bool(raw)
        else:
            read[parameter.name] = str(raw).strip()
    return read


def _comparator(mechanic: BuilderMechanic, values: dict[str, Any]) -> Comparator:
    raw = str(values.get("comparator") or "")
    allowed = {item.value for item in mechanic.operators}
    if raw not in allowed:
        raise BuilderActionError(
            "COMPARISON_NOT_OFFERED",
            "That comparison cannot be used with this kind of rule.",
        )
    return Comparator(raw)


def _direction(mechanic: BuilderMechanic, values: dict[str, Any]) -> MovementDirection:
    allowed = {item.value for item in mechanic.directions}
    raw = str(values.get("direction") or "")
    if not raw:
        # A mechanic with only one possible side does not ask. One with several does,
        # and a missing answer is never guessed at.
        if len(allowed) == 1:
            return MovementDirection(next(iter(allowed)))
        return MovementDirection.NEUTRAL
    if raw not in allowed:
        raise BuilderActionError(
            "DIRECTION_NOT_OFFERED",
            "That direction cannot be used with this kind of rule.",
        )
    return MovementDirection(raw)


def _timeframe(mechanic: BuilderMechanic, values: dict[str, Any]) -> str:
    raw = str(values.get("timeframe") or "")
    if raw not in set(mechanic.timeframes):
        raise BuilderActionError(
            "TIMEFRAME_NOT_OFFERED",
            "That candle size cannot be used with this kind of rule.",
        )
    return raw


# ---------------------------------------------------------------------------
# Building the rule
# ---------------------------------------------------------------------------


def build_condition(
    *,
    mechanic_key: str,
    values: dict[str, Any],
    source_turn_id: str,
    node_id: str | None = None,
    required: bool = True,
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> tuple[ConditionNodeV2, str]:
    """One validated rule, plus the sentence that describes it.

    The sentence is not decoration. It becomes the rule's stored provenance, so the
    evidence trail for a rule built in the Builder reads the same way as one built from
    a typed message.
    """

    return _build(
        _require_mechanic(mechanic_key, configured_providers, disabled_capabilities),
        values,
        source_turn_id=source_turn_id,
        node_id=node_id,
        required=required,
    )


def _build(
    mechanic: BuilderMechanic,
    values: dict[str, Any],
    *,
    source_turn_id: str,
    node_id: str | None,
    required: bool,
) -> tuple[ConditionNodeV2, str]:
    read = _read_values(mechanic, values)
    formula = FormulaKind(mechanic.formula)
    comparator = _comparator(mechanic, read)
    direction = _direction(mechanic, read)
    timeframe = _timeframe(mechanic, read)
    sentence = render_condition_sentence(mechanic, read, comparator, direction, timeframe)

    payload: dict[str, Any] = {
        "node_type": ConditionNodeType.CONDITION,
        "source_turn_id": source_turn_id,
        "source_fragment": sentence,
        "required": required,
        "movement_direction": direction,
        "formula": formula,
        "operator": comparator,
        "unit": mechanic.unit,
        "trigger_timeframe": timeframe,
    }
    if node_id:
        payload["node_id"] = node_id

    if formula in _PERCENTAGE_RUNTIME:
        measurement = PERCENTAGE_MEASUREMENTS[formula]
        stated_lookback = read.get("lookback")
        parameters = percentage_runtime_parameters(
            formula,
            reference_field=(
                str(read["reference_field"]) if read.get("reference_field") else None
            ),
            lookback=int(stated_lookback) if stated_lookback is not None else None,
        )
        payload["threshold"] = read["threshold"]
        if stated_lookback is not None:
            payload["lookback"] = int(stated_lookback)
        if measurement.reference_is_chosen:
            payload["reference_definition"] = (
                f"{parameters['reference_field']} of the candle "
                f"{parameters['lookback']} back"
            )
        payload["operands"] = [
            OperandV2(
                role="measured_value",
                kind="market_metric",
                name="percentage_change",
                parameters=parameters,
            )
        ]
    elif formula is FormulaKind.SWEEP_AND_RECLAIM:
        level = str(read["reference_field"])
        payload["threshold"] = None
        payload["reference_definition"] = f"previous candle {level}"
        payload["operands"] = [
            OperandV2(
                role="sweep_state",
                kind="market_metric",
                name="sweep_and_reclaim",
                parameters={
                    "pierce_required": True,
                    "reclaim_required": True,
                    "reference_field": level,
                },
            )
        ]
    elif formula is FormulaKind.FIXED_REFERENCE_LEVEL:
        payload["threshold"] = read["threshold"]
        payload["reference_definition"] = f"fixed price {read['threshold']:g}"
        payload["operands"] = [OperandV2(role="left", kind="price", field="close")]
    elif formula is FormulaKind.LOOKBACK_REFERENCE_LEVEL:
        level = str(read["reference_field"])
        payload["lookback"] = int(read["lookback"])
        payload["reference_definition"] = f"{level} of the last {int(read['lookback'])} candles"
        payload["operands"] = [
            OperandV2(role="left", kind="price", field="close"),
            OperandV2(
                role="right",
                kind="reference",
                # Named by the module that also reads it, so a card can never store a
                # level the runtime has no answer for.
                name=lookback_level_name(level),
                parameters={"lookback": int(read["lookback"]), "reference_field": level},
            ),
        ]
    elif formula in _PRICE_OPERAND_FORMULAS:
        level = str(read["reference_field"])
        payload["reference_definition"] = f"previous candle {level}"
        payload["operands"] = [
            OperandV2(role="left", kind="price", field="close"),
            OperandV2(
                role="right",
                kind="reference",
                name=previous_candle_level_name(level),
                parameters={"reference_field": level},
            ),
        ]
    else:
        # A registered capability. Its own parameters come straight from the registry
        # fields; the capability contract checks them against the registry schema before
        # anything is applied.
        assert mechanic.capability_key is not None
        payload["capability_key"] = mechanic.capability_key
        payload["capability_version"] = mechanic.version
        # "Happens" and "does not happen" are yes/no questions. A number beside them
        # compiles into a comparison the rule does not make, and the compiler's own
        # equivalence check refuses the whole draft. A number is required for every
        # other comparison, and never invented when it is missing.
        if comparator in {Comparator.IS_TRUE, Comparator.IS_FALSE}:
            payload["threshold"] = None
        elif read.get("threshold") is None:
            raise BuilderActionError(
                "VALUE_REQUIRED",
                "This rule needs a number to compare against.",
            )
        else:
            payload["threshold"] = read["threshold"]
        # Only the capability's own registry fields go in its parameter bag. The
        # comparison, the side and the candle size belong to the rule, and most registry
        # schemas refuse an unexpected key outright — so a stray one is not a cosmetic
        # problem, it makes every submission of that form fail.
        payload["capability_parameters"] = {
            name: value
            for name, value in read.items()
            if name in mechanic.registry_parameter_names
        }
        payload["operands"] = [
            OperandV2(role="value", kind="market_metric", name=mechanic.capability_key)
        ]

    try:
        node = ConditionNodeV2(**payload)
    except ValueError as exc:
        raise BuilderActionError(
            "RULE_INCOMPLETE",
            "That rule is missing something Hilal Markets needs before it can watch it.",
        ) from exc
    return node, sentence


def render_condition_sentence(
    mechanic: BuilderMechanic,
    values: dict[str, Any],
    comparator: Comparator,
    direction: MovementDirection,
    timeframe: str,
) -> str:
    """Write one chosen rule out as a sentence a beginner can check.

    Built from the same contract that drew the form, so the words and the stored rule
    can never describe different things.
    """

    from ai_market_monitor.engine.builder_contract import (
        COMPARATOR_LABELS,
        DIRECTION_LABELS,
    )

    comparison = COMPARATOR_LABELS[comparator][0]
    parts: list[str] = []
    formula = FormulaKind(mechanic.formula)
    raw_field = str(values.get("reference_field"))
    level = _FIELD_WORDS.get(raw_field, raw_field)
    if formula in _PERCENTAGE_RUNTIME:
        side = DIRECTION_LABELS[direction][0]
        parts.append(f"price {side} by {comparison} {values['threshold']:g} percent")
        if PERCENTAGE_MEASUREMENTS[formula].reference_is_chosen:
            # Which earlier price, and how far back, is the measurement itself. Leaving
            # it out of the sentence let a person approve "price up by at least 2
            # percent" without ever being told what it was two percent away from.
            parts.append(
                f"from the {level} of the candle {int(values['lookback'])} back"
            )
    elif formula is FormulaKind.SWEEP_AND_RECLAIM:
        parts.append(f"price sweeps the previous candle {level} and reclaims it")
    elif formula is FormulaKind.FIXED_REFERENCE_LEVEL:
        parts.append(f"price is {comparison} {values['threshold']:g}")
    elif formula is FormulaKind.LOOKBACK_REFERENCE_LEVEL:
        parts.append(
            f"price is {comparison} the {level} of the last "
            f"{int(values['lookback'])} candles"
        )
    elif formula in _PRICE_OPERAND_FORMULAS:
        parts.append(f"price is {comparison} the previous candle {level}")
    else:
        threshold = values.get("threshold")
        # "happens" and "does not happen" are whole verbs already, so the linking "is"
        # in front of one produced "Bollinger re-entry **is happens** on the 15m candle".
        # That sentence is not a debug string: it is what the Builder writes back for the
        # trader to read and approve, and 262 of the 369 cards read that way, because
        # every yes/no card uses one of those two comparisons.
        measured = (
            f"{mechanic.label} {comparison}"
            if comparator.value in UNARY_COMPARATORS
            else f"{mechanic.label} is {comparison}"
        )
        parts.append(
            f"{measured} {threshold:g}" if isinstance(threshold, int | float) else measured
        )
        extras = [
            f"{name.replace('_', ' ')} {_readable(value)}"
            for name, value in sorted(values.items())
            if name not in {"comparator", "direction", "timeframe", "threshold"}
        ]
        if extras:
            parts.append("with " + ", ".join(extras))
    parts.append(f"on the {timeframe} candle")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Reading a stored rule back into form fields
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuilderConditionView:
    """One stored rule, as the Builder shows and edits it."""

    node_id: str
    mechanic_key: str | None
    label: str
    sentence: str
    values: dict[str, Any]
    required: bool
    editable: bool
    #: Why this rule cannot be edited in the Builder, when it cannot be.
    not_editable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "mechanic_key": self.mechanic_key,
            "label": self.label,
            "sentence": self.sentence,
            "values": dict(self.values),
            "required": self.required,
            "editable": self.editable,
            "not_editable_reason": self.not_editable_reason,
        }


def describe_condition(node: ConditionNodeV2) -> BuilderConditionView:
    """Turn a stored rule back into the fields that would rebuild it.

    A rule written by the assistant and a rule written in the Builder are the same
    object, so this reads both. When a rule uses something the Builder has no form for,
    it is reported as not editable *with the reason* — never hidden, and never shown as
    an empty card the person could overwrite by accident.
    """

    mechanic_key = (
        f"capability:{node.capability_key}"
        if node.formula is FormulaKind.CAPABILITY and node.capability_key
        else (node.formula.value if node.formula else None)
    )
    mechanic = find_mechanic(mechanic_key or "")
    declared = {item.name for item in mechanic.parameters} if mechanic else set()

    # What the *person* chose. Deliberately not everything the node holds: a compiled
    # rule also carries parameters the platform fills in itself — an open-to-close move
    # records that it measures open against close — and treating those as choices made
    # every assistant-written rule look like it used settings the form cannot show.
    carried: dict[str, Any] = {}
    if node.operator is not None:
        carried["comparator"] = node.operator.value
    if node.trigger_timeframe:
        carried["timeframe"] = node.trigger_timeframe
    if "direction" in declared:
        carried["direction"] = node.movement_direction.value
    elif (
        node.movement_direction in {MovementDirection.UP, MovementDirection.DOWN}
        and mechanic is not None
        and {item.value for item in mechanic.directions} != {node.movement_direction.value}
    ):
        # A side this form never asks about, on a rule that has one. Recorded so the
        # card is marked read-only below: saving would drop the side and quietly turn a
        # rule about a fall into a rule about any move.
        #
        # A mechanic with only one possible side is exempt: the form does not ask because
        # there is nothing to choose, and rebuilding the rule puts the same side back.
        carried["direction"] = node.movement_direction.value
    if node.threshold is not None:
        carried["threshold"] = node.threshold
    if node.lookback is not None:
        carried["lookback"] = node.lookback
    if "reference_field" in declared:
        # Only a choice when this mechanic asks for one. Elsewhere it is the platform's
        # own note about which prices the formula compares.
        reference_field = _reference_field_of(node)
        if reference_field:
            carried["reference_field"] = reference_field
    for name, value in node.capability_parameters.items():
        carried.setdefault(name, value)

    values = {name: value for name, value in carried.items() if name in declared}
    dropped = sorted(set(carried) - declared)

    editable = mechanic is not None and mechanic.available
    reason: str | None = None
    if mechanic is None:
        reason = "This rule was written in the assistant and has no simple form yet."
    elif not mechanic.available:
        reason = mechanic.unavailable_reason
    if editable and mechanic is not None:
        # A rule the form cannot round-trip is not editable, whatever its mechanic says.
        # Showing an editable card that drops a value on save would lose the person's
        # work without telling them.
        missing = [
            item.label
            for item in mechanic.parameters
            if item.required and item.default is None and values.get(item.name) in (None, "")
        ]
        if missing or dropped:
            editable = False
            reason = "This rule uses settings the simple form cannot show yet."
    return BuilderConditionView(
        node_id=node.node_id,
        mechanic_key=mechanic_key,
        label=mechanic.label if mechanic else "Custom rule",
        sentence=node.source_fragment or (mechanic.label if mechanic else node.node_id),
        values=values,
        required=node.required,
        editable=editable,
        not_editable_reason=reason,
    )


def _reference_field_of(node: ConditionNodeV2) -> str | None:
    for operand in node.operands:
        field = operand.parameters.get("reference_field")
        if isinstance(field, str):
            return field
    return None


# ---------------------------------------------------------------------------
# Tree shape
# ---------------------------------------------------------------------------


def condition_nodes(root: ConditionNodeV2 | None) -> list[ConditionNodeV2]:
    if root is None:
        return []
    return [item for item in root.walk() if item.node_type == ConditionNodeType.CONDITION]


def rebuild_tree(
    root: ConditionNodeV2 | None,
    *,
    order: list[str],
    join: str,
) -> ConditionNodeV2:
    """Put the same rules back in a new order, joined a new way.

    Only rules that already exist may appear, and every one of them must. A reorder that
    quietly dropped a rule would look like a tidy-up and behave like a deletion.
    """

    existing = {node.node_id: node for node in condition_nodes(root)}
    if not existing:
        raise BuilderActionError(
            "NO_RULES_YET",
            "There are no rules to arrange yet.",
        )
    if sorted(order) != sorted(existing):
        raise BuilderActionError(
            "ORDER_INCOMPLETE",
            "That arrangement does not match the rules you have. Reload and try again.",
        )
    if join not in {"and", "or"}:
        raise BuilderActionError(
            "LOGIC_NOT_OFFERED",
            "Rules can be joined with “all of these” or “any of these”.",
        )
    children = [existing[node_id] for node_id in order]
    if len(children) == 1:
        return children[0]
    return ConditionNodeV2(
        node_id=f"root_{join}",
        node_type=ConditionNodeType.AND if join == "and" else ConditionNodeType.OR,
        children=children,
    )


def current_join(root: ConditionNodeV2 | None) -> str:
    """How the rules are joined right now: ``and``, ``or`` or ``""`` for a single rule."""

    if root is None or not root.children:
        return ""
    return str(root.node_type.value)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def _operation(
    kind: str,
    operation_id: str,
    segment_id: str,
    **payload: Any,
) -> AuthorizedPatchOperation:
    return AuthorizedPatchOperation.model_validate(
        {
            "operation_id": operation_id[:80],
            "authorizing_segment_id": segment_id,
            "kind": kind,
            **payload,
        }
    )


def add_condition_plan(
    *,
    mechanic_key: str,
    values: dict[str, Any],
    source_turn_id: str,
    segment_id: str,
    required: bool = True,
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> BuilderPlan:
    node, sentence = build_condition(
        mechanic_key=mechanic_key,
        values=values,
        source_turn_id=source_turn_id,
        required=required,
        configured_providers=configured_providers,
        disabled_capabilities=disabled_capabilities,
    )
    return BuilderPlan(
        operations=(
            _operation(
                "add_condition",
                f"builder_add_{node.node_id}",
                segment_id,
                condition=node,
            ),
        ),
        rendered=sentence,
    )


def update_condition_plan(
    *,
    node_id: str,
    mechanic_key: str,
    values: dict[str, Any],
    source_turn_id: str,
    segment_id: str,
    required: bool = True,
    configured_providers: frozenset[str] = frozenset(),
    disabled_capabilities: frozenset[str] = frozenset(),
) -> BuilderPlan:
    node, sentence = build_condition(
        mechanic_key=mechanic_key,
        values=values,
        source_turn_id=source_turn_id,
        node_id=node_id,
        required=required,
        configured_providers=configured_providers,
        disabled_capabilities=disabled_capabilities,
    )
    return BuilderPlan(
        operations=(
            _operation(
                "update_condition",
                f"builder_edit_{node_id}",
                segment_id,
                condition=node,
                target_condition_id=node_id,
            ),
        ),
        rendered=sentence,
    )


def remove_condition_plan(
    *,
    node_id: str,
    segment_id: str,
) -> BuilderPlan:
    return BuilderPlan(
        operations=(
            _operation(
                "remove_condition",
                f"builder_remove_{node_id}",
                segment_id,
                target_condition_id=node_id,
            ),
        ),
        rendered="remove one rule",
    )


def _structural(build: Callable[[], ConditionNodeV2]) -> ConditionNodeV2:
    """Run a structural edit, reporting its refusal the way every Builder action does.

    ``builder_boolean`` raises its own error type because it knows nothing about the
    Builder's request envelope. Translating here — once — keeps every refusal reaching
    the client as the same shape, so a grouping that is too deep reads to the person
    exactly like any other refused change, with its own code and its own sentence.
    """

    try:
        return build()
    except BooleanStructureError as error:
        raise BuilderActionError(error.code, error.message) from error


def _structure_plan(
    tree: ConditionNodeV2,
    *,
    operation_id: str,
    segment_id: str,
    rendered: str,
) -> BuilderPlan:
    """One structural change, expressed the same way every other Builder edit is.

    Structural edits go through ``replace_groups`` like the flat arrange does, so the
    canonical diff, the approval invalidation and the evidence trail treat a regrouping
    exactly as they treat any other change to the rules.
    """

    return BuilderPlan(
        operations=(
            _operation("replace_groups", operation_id, segment_id, condition=tree),
        ),
        rendered=rendered,
    )


def group_conditions_plan(
    *,
    root: ConditionNodeV2 | None,
    node_ids: list[str],
    operator: str,
    segment_id: str,
) -> BuilderPlan:
    """Wrap selected rules in a group, leaving every other rule where it is."""

    tree = _structural(lambda: boolean_group_conditions(root, node_ids=node_ids, operator=operator))
    wording = {
        "and": "grouped those rules so all of them must match",
        "or": "grouped those rules so any one of them may match",
        "not": "set that rule to alert only when it does not match",
    }[operator]
    return _structure_plan(
        tree,
        operation_id=f"builder_group_{operator}_{'_'.join(node_ids)[:40]}",
        segment_id=segment_id,
        rendered=wording,
    )


def ungroup_conditions_plan(
    *,
    root: ConditionNodeV2 | None,
    group_id: str,
    segment_id: str,
) -> BuilderPlan:
    """Dissolve one group, lifting its rules into the group above it."""

    tree = _structural(lambda: boolean_ungroup(root, group_id=group_id))
    return _structure_plan(
        tree,
        operation_id=f"builder_ungroup_{group_id}",
        segment_id=segment_id,
        rendered="removed that grouping and kept every rule",
    )


def set_group_operator_plan(
    *,
    root: ConditionNodeV2 | None,
    group_id: str,
    operator: str,
    segment_id: str,
) -> BuilderPlan:
    """Change how one group joins, without touching the rules inside it."""

    tree = _structural(
        lambda: boolean_set_group_operator(root, group_id=group_id, operator=operator)
    )
    wording = {
        "and": "that group now needs all of its rules to match",
        "or": "that group now needs any one of its rules to match",
        "not": "that group now alerts only when its rule does not match",
    }[operator]
    return _structure_plan(
        tree,
        operation_id=f"builder_regroup_{group_id}_{operator}",
        segment_id=segment_id,
        rendered=wording,
    )


def move_condition_plan(
    *,
    root: ConditionNodeV2 | None,
    node_id: str,
    group_id: str,
    position: int | None,
    segment_id: str,
) -> BuilderPlan:
    """Move one rule or group into another group, at an exact place."""

    tree = _structural(
        lambda: boolean_move_condition(
            root, node_id=node_id, target_group_id=group_id, position=position
        )
    )
    return _structure_plan(
        tree,
        operation_id=f"builder_move_{node_id}_to_{group_id}",
        segment_id=segment_id,
        rendered="moved that rule into the other group",
    )


def arrange_plan(
    *,
    root: ConditionNodeV2 | None,
    order: list[str],
    join: str,
    segment_id: str,
) -> BuilderPlan:
    tree = rebuild_tree(root, order=order, join=join)
    return BuilderPlan(
        operations=(
            _operation(
                "replace_groups",
                f"builder_arrange_{join}",
                segment_id,
                condition=tree,
            ),
        ),
        rendered=(
            "all of these rules must match"
            if join == "and"
            else "any one of these rules may match"
        ),
    )
