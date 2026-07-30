"""One registry-owned check that a capability node is actually usable.

A node saying ``formula: capability, capability_key: rsi_threshold`` was accepted on the
strength of the key alone. Nothing confirmed that the capability is executable, that it
supports the operator and side the node carries, that the timeframe is one it works on,
that the parameters it requires are present, that no invented parameter came along, or
that a user-facing number was grounded in the words that authorised it.

Every one of those questions is answered by the registry's own ``CapabilitySpec``. This
module asks it, once, in one place — so the prompt does not have to restate the schema
and the compiler does not have to grow a branch per capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_market_monitor.engine.capabilities import CapabilitySpec, all_capabilities
from ai_market_monitor.engine.semantic_grounding import (
    grounds_number,
    grounds_operator,
    grounds_timeframe,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeV2,
    DraftDirection,
    FormulaKind,
    ProviderRequirementV2,
)

#: How a draft direction maps onto the registry's own vocabulary.
_DIRECTION_WORDS: dict[DraftDirection, str] = {
    DraftDirection.LONG: "bullish",
    DraftDirection.SHORT: "bearish",
    DraftDirection.NEUTRAL: "neutral",
}

#: Parameters the platform supplies, so the trader never has to state them and they are
#: exempt from source grounding. Anything else numeric is the trader's choice.
_PLATFORM_PARAMETERS = frozenset({"formula", "reference_field", "current_field", "scale"})


@dataclass(frozen=True, slots=True)
class CapabilityContractResult:
    """What the registry says about one capability node."""

    errors: tuple[str, ...] = ()
    provider_requirements: tuple[ProviderRequirementV2, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def _spec_by_key() -> dict[str, CapabilitySpec]:
    return {capability.key: capability for capability in all_capabilities()}


def validate_capability_node(
    node: ConditionNodeV2,
    *,
    authorizing_text: str,
    allowed_keys: frozenset[str],
    source_turn_id: str | None = None,
) -> CapabilityContractResult:
    """Check one capability node against its registry contract.

    ``authorizing_text`` is the single segment that permitted this node. Trader-chosen
    parameter values are grounded there and nowhere else, so a number written about a
    different rule cannot supply this one's period or level.
    """

    if node.formula != FormulaKind.CAPABILITY:
        return CapabilityContractResult()
    key = node.capability_key
    if not key:
        return CapabilityContractResult(errors=(f"{node.node_id}:capability_key_missing",))
    if key not in allowed_keys:
        return CapabilityContractResult(
            errors=(f"{node.node_id}:capability_not_offered:{key}",)
        )
    spec = _spec_by_key().get(key)
    if spec is None:
        return CapabilityContractResult(errors=(f"{node.node_id}:capability_unknown:{key}",))

    errors: list[str] = []
    if not spec.executable:
        errors.append(f"{node.node_id}:capability_not_executable:{key}")
    if spec.availability != "available":
        errors.append(f"{node.node_id}:capability_unavailable:{key}:{spec.availability}")
    if node.operator is not None and node.operator.value not in spec.supported_comparators:
        errors.append(f"{node.node_id}:operator_unsupported:{node.operator.value}")
    direction_word = _DIRECTION_WORDS[node.direction]
    if direction_word not in spec.direction_support:
        errors.append(f"{node.node_id}:direction_unsupported:{direction_word}")
    if node.trigger_timeframe and node.trigger_timeframe not in spec.supported_timeframes:
        errors.append(f"{node.node_id}:timeframe_unsupported:{node.trigger_timeframe}")
    if spec.requires_higher_timeframe and not (
        node.context_timeframes or node.reference_timeframe
    ):
        # A mechanic defined against a higher timeframe cannot be evaluated without one.
        errors.append(f"{node.node_id}:higher_timeframe_required")

    errors.extend(
        _parameter_errors(
            node,
            spec,
            authorizing_text=authorizing_text,
        )
    )

    providers = tuple(
        ProviderRequirementV2(
            provider=provider,
            capability=key,
            source_turn_id=source_turn_id,
            source_fragment=(authorizing_text or spec.label)[:500],
            # Availability is decided by the provider check, not asserted here.
            available=False,
        )
        for provider in (
            spec.provider_requirements
            or ((spec.provider_required,) if spec.provider_required else ())
        )
    )
    return CapabilityContractResult(errors=tuple(errors), provider_requirements=providers)


def _parameters(node: ConditionNodeV2) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for operand in node.operands:
        merged.update(operand.parameters)
    return merged


def _parameter_errors(
    node: ConditionNodeV2,
    spec: CapabilitySpec,
    *,
    authorizing_text: str,
) -> list[str]:
    """Required present, nothing invented, every value inside its declared bounds."""

    schema = spec.parameter_schema or {}
    declared = {
        parameter.name: parameter for parameter in spec.parameters
    }
    supplied = _parameters(node)
    errors: list[str] = []

    # Some registry parameters are carried by the condition itself rather than by an
    # operand: a `threshold` lives in `node.threshold`, a `timeframe` in
    # `node.trigger_timeframe`. Treating those as missing reported a correct node as
    # incomplete.
    node_level: dict[str, Any] = {
        "threshold": node.threshold,
        "level": node.threshold,
        "value": node.threshold,
        "timeframe": node.trigger_timeframe,
        "direction": node.direction.value,
        "comparator": node.operator.value if node.operator else None,
    }
    for name, parameter in declared.items():
        required = bool(getattr(parameter, "required", False))
        if required and name not in supplied and node_level.get(name) is None:
            errors.append(f"{node.node_id}:parameter_missing:{name}")
    known = set(declared) | set(schema) | _PLATFORM_PARAMETERS
    for name in supplied:
        if name not in known:
            errors.append(f"{node.node_id}:parameter_unknown:{name}")

    for name, value in supplied.items():
        rules = schema.get(name)
        if isinstance(rules, dict):
            errors.extend(_schema_errors(node.node_id, name, value, rules))
        # A number the trader controls has to come from the trader. Periods and levels
        # the platform fills in are exempt via `_PLATFORM_PARAMETERS`.
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and name not in _PLATFORM_PARAMETERS
            and not _grounded_quantity(authorizing_text, float(value))
        ):
            errors.append(f"{node.node_id}:parameter_not_grounded:{name}")
    return errors


def _grounded_quantity(text: str, value: float) -> bool:
    """Accept the value under any unit the trader might have written it as."""

    return any(
        grounds_number(text, value, unit=unit)
        for unit in ("plain", "percent", "multiple", "price", "count")
    )


def _schema_errors(
    node_id: str,
    name: str,
    value: Any,
    rules: dict[str, Any],
) -> list[str]:
    """Type, enum and bound checks straight from the registry's declared schema."""

    errors: list[str] = []
    expected = rules.get("type")
    if expected == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
        errors.append(f"{node_id}:parameter_type:{name}")
        return errors
    if expected == "number" and not (
        isinstance(value, int | float) and not isinstance(value, bool)
    ):
        errors.append(f"{node_id}:parameter_type:{name}")
        return errors
    if expected == "string" and not isinstance(value, str):
        errors.append(f"{node_id}:parameter_type:{name}")
        return errors
    if expected == "boolean" and not isinstance(value, bool):
        errors.append(f"{node_id}:parameter_type:{name}")
        return errors
    choices = rules.get("enum")
    if isinstance(choices, list | tuple) and value not in choices:
        errors.append(f"{node_id}:parameter_enum:{name}")
    minimum = rules.get("minimum")
    if isinstance(minimum, int | float) and isinstance(value, int | float) and value < minimum:
        errors.append(f"{node_id}:parameter_minimum:{name}")
    maximum = rules.get("maximum")
    if isinstance(maximum, int | float) and isinstance(value, int | float) and value > maximum:
        errors.append(f"{node_id}:parameter_maximum:{name}")
    return errors


def capability_condition_errors(
    nodes: list[ConditionNodeV2],
    *,
    authorizing_text_by_node: dict[str, str],
    allowed_keys: frozenset[str],
    source_turn_id: str | None = None,
) -> tuple[list[str], list[ProviderRequirementV2]]:
    """Validate every capability node in one place, before compilation."""

    errors: list[str] = []
    providers: list[ProviderRequirementV2] = []
    for node in nodes:
        result = validate_capability_node(
            node,
            authorizing_text=authorizing_text_by_node.get(node.node_id, ""),
            allowed_keys=allowed_keys,
            source_turn_id=source_turn_id,
        )
        errors.extend(result.errors)
        providers.extend(result.provider_requirements)
    return errors, providers


def grounded_operator_and_timeframe(
    node: ConditionNodeV2,
    *,
    authorizing_text: str,
) -> list[str]:
    """The operator and timeframe on a capability node must be the trader's own."""

    errors: list[str] = []
    if node.operator is not None and not grounds_operator(authorizing_text, node.operator):
        errors.append(f"{node.node_id}:operator_not_grounded")
    if node.trigger_timeframe and not grounds_timeframe(
        authorizing_text, node.trigger_timeframe
    ):
        errors.append(f"{node.node_id}:trigger_timeframe_not_grounded")
    return errors
