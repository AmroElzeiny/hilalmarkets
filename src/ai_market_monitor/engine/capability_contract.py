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

from jsonschema import Draft202012Validator

from ai_market_monitor.engine.capabilities import CapabilitySpec, all_capabilities
from ai_market_monitor.engine.capability_index import get_capability_index
from ai_market_monitor.engine.parameter_roles import role_grounding_errors
from ai_market_monitor.engine.semantic_grounding import (
    grounds_boolean,
    grounds_number,
    grounds_operator,
    grounds_symbol,
    grounds_text_value,
    grounds_timeframe,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeV2,
    FormulaKind,
    MovementDirection,
    ProviderRequirementV2,
)

#: How a draft direction maps onto the registry's own vocabulary.
_DIRECTION_WORDS: dict[MovementDirection, str] = {
    MovementDirection.UP: "bullish",
    MovementDirection.DOWN: "bearish",
    MovementDirection.NEUTRAL: "neutral",
    MovementDirection.NOT_APPLICABLE: "neutral",
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
    require_current_shortlist: bool = True,
    previous_node: ConditionNodeV2 | None = None,
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
    if require_current_shortlist and key not in allowed_keys:
        return CapabilityContractResult(
            errors=(f"{node.node_id}:capability_not_offered:{key}",)
        )
    spec = _spec_by_key().get(key)
    if spec is None:
        return CapabilityContractResult(errors=(f"{node.node_id}:capability_unknown:{key}",))

    errors: list[str] = []
    if require_current_shortlist and key not in _exact_capability_keys(authorizing_text):
        errors.append(f"{node.node_id}:capability_semantics_not_exact:{key}")
    if node.capability_version != spec.capability_version:
        errors.append(
            f"{node.node_id}:capability_version_mismatch:"
            f"{node.capability_version or 'missing'}:{spec.capability_version}"
        )
    if not spec.executable:
        errors.append(f"{node.node_id}:capability_not_executable:{key}")
    if spec.availability != "available":
        errors.append(f"{node.node_id}:capability_unavailable:{key}:{spec.availability}")
    if node.operator is not None and node.operator.value not in spec.supported_comparators:
        errors.append(f"{node.node_id}:operator_unsupported:{node.operator.value}")
    direction_word = _DIRECTION_WORDS[node.movement_direction]
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
            previous_node=previous_node,
        )
    )

    providers = tuple(
        ProviderRequirementV2(
            provider=provider,
            capability=key,
            source_turn_id=source_turn_id,
            source_fragment=(authorizing_text or spec.label)[:500],
            capability_version=spec.capability_version,
        )
        for provider in (
            spec.provider_requirements
            or ((spec.provider_required,) if spec.provider_required else ())
        )
    )
    return CapabilityContractResult(errors=tuple(errors), provider_requirements=providers)


def _exact_capability_keys(authorizing_text: str) -> frozenset[str]:
    """Capabilities the deterministic resolver matched exactly, never nearest."""

    report = get_capability_index().resolver.resolve_prompt(authorizing_text)
    return frozenset(
        resolution.candidates[0].capability_key
        for resolution in report.fragments
        if resolution.status == "matched" and resolution.candidates
    )


def _parameters(node: ConditionNodeV2) -> dict[str, Any]:
    merged: dict[str, Any] = dict(node.capability_parameters)
    for operand in node.operands:
        merged.update(operand.parameters)
    return merged


def _parameter_errors(
    node: ConditionNodeV2,
    spec: CapabilitySpec,
    *,
    authorizing_text: str,
    previous_node: ConditionNodeV2 | None = None,
) -> list[str]:
    """Required present, nothing invented, every value inside its declared bounds."""

    schema = spec.parameter_schema or {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    declared = {parameter.name: parameter for parameter in spec.parameters}
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
        "direction": _DIRECTION_WORDS[node.movement_direction],
        "comparator": node.operator.value if node.operator else None,
    }
    raw_properties = schema.get("properties")
    properties: dict[str, Any] = (
        dict(raw_properties) if isinstance(raw_properties, dict) else {}
    )
    registry_defaults = {
        name: rules["default"]
        for name, rules in properties.items()
        if isinstance(rules, dict) and "default" in rules
    }
    registry_defaults.update(spec.default_parameters)
    validation_payload = {**registry_defaults, **supplied}
    for name in set(schema.get("properties") or {}) | set(declared):
        if name not in validation_payload and node_level.get(name) is not None:
            validation_payload[name] = node_level[name]
    previous_payload: dict[str, Any] = {}
    if (
        previous_node is not None
        and previous_node.capability_key == node.capability_key
        and previous_node.capability_version == node.capability_version
    ):
        previous_payload = _parameters(previous_node)
        previous_node_level: dict[str, Any] = {
            "threshold": previous_node.threshold,
            "level": previous_node.threshold,
            "value": previous_node.threshold,
            "timeframe": previous_node.trigger_timeframe,
            "direction": _DIRECTION_WORDS[previous_node.movement_direction],
            "comparator": (
                previous_node.operator.value if previous_node.operator else None
            ),
        }
        for name in set(schema.get("properties") or {}) | set(declared):
            if name not in previous_payload and previous_node_level.get(name) is not None:
                previous_payload[name] = previous_node_level[name]
    for failure in Draft202012Validator(schema).iter_errors(validation_payload):
        location = ".".join(str(item) for item in failure.absolute_path) or "parameters"
        validator = str(failure.validator or "schema")
        errors.append(f"{node.node_id}:parameter_{validator}:{location}")

    for name, value in validation_payload.items():
        rules = properties.get(name) if isinstance(properties, dict) else None
        semantic_unit = (
            str(rules.get("x-semantic-unit"))
            if isinstance(rules, dict) and rules.get("x-semantic-unit")
            else _semantic_unit(name)
        )
        # A number the trader controls has to come from the trader. Periods and levels
        # the platform fills in are exempt via `_PLATFORM_PARAMETERS`.
        trader_controlled = (
            name not in _PLATFORM_PARAMETERS
            and previous_payload.get(name, object()) != value
            and (
                name in supplied
                or name not in registry_defaults
                or registry_defaults[name] != value
            )
        )
        if trader_controlled and not _parameter_value_grounded(
            authorizing_text,
            value,
            rules if isinstance(rules, dict) else {},
            semantic_unit=semantic_unit,
        ):
            errors.append(
                f"{node.node_id}:parameter_not_grounded:{name}:{semantic_unit}"
            )

    # Value grounding asks "did the trader write this number?". When one capability takes
    # two trader-controlled numbers of the same unit — an RSI period and a confirmation
    # count are both candle counts — that question has the same answer whichever way round
    # they are assigned, so `period=3, confirmation=14` passed while monitoring the
    # opposite of what was asked for. Role grounding asks the second half: *why is this
    # number this parameter?* See `engine/parameter_roles.py`.
    errors.extend(
        role_grounding_errors(
            node_id=node.node_id,
            supplied={
                name: value
                for name, value in supplied.items()
                if name not in _PLATFORM_PARAMETERS
            },
            defaults=registry_defaults,
            parameter_schema=properties,
            authorizing_text=authorizing_text,
        )
    )
    return errors


def _parameter_value_grounded(
    text: str,
    value: Any,
    schema: dict[str, Any],
    *,
    semantic_unit: str,
) -> bool:
    """Ground every JSON value type; schema validity is not user authorization."""

    if isinstance(value, bool):
        return grounds_boolean(text, value)
    if isinstance(value, int | float):
        return grounds_number(
            text,
            float(value),
            unit=_grounding_unit(semantic_unit),
        )
    if isinstance(value, str):
        if semantic_unit == "timeframe":
            return grounds_timeframe(text, value)
        if semantic_unit == "symbol":
            return grounds_symbol(text, value)
        if grounds_text_value(text, value):
            return True
        semantic_aliases = schema.get("x-semantic-aliases")
        aliases: list[str] = []
        if isinstance(semantic_aliases, dict):
            raw_aliases = semantic_aliases.get(value)
            if isinstance(raw_aliases, list):
                aliases = [str(item) for item in raw_aliases]
        elif isinstance(semantic_aliases, list):
            aliases = [str(item) for item in semantic_aliases]
        return any(grounds_text_value(text, alias) for alias in aliases)
    if isinstance(value, list):
        item_schema = schema.get("items")
        rules = item_schema if isinstance(item_schema, dict) else {}
        return all(
            _parameter_value_grounded(
                text,
                item,
                rules,
                semantic_unit=(
                    str(rules.get("x-semantic-unit"))
                    if rules.get("x-semantic-unit")
                    else semantic_unit
                ),
            )
            for item in value
        )
    if isinstance(value, dict):
        properties = schema.get("properties")
        property_rules = properties if isinstance(properties, dict) else {}
        return all(
            _parameter_value_grounded(
                text,
                item,
                _property_schema(property_rules, name),
                semantic_unit=(
                    str(_property_schema(property_rules, name).get("x-semantic-unit"))
                    if _property_schema(property_rules, name).get("x-semantic-unit")
                    else _semantic_unit(name)
                ),
            )
            for name, item in value.items()
        )
    return False


def _property_schema(properties: dict[str, Any], name: str) -> dict[str, Any]:
    value = properties.get(name)
    return value if isinstance(value, dict) else {}


def _semantic_unit(name: str) -> str:
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


def _grounding_unit(unit: str) -> str:
    return {
        "count": "count",
        "percent": "percent",
        "price": "price",
        "multiple": "multiple",
    }.get(unit, "plain")


def capability_condition_errors(
    nodes: list[ConditionNodeV2],
    *,
    authorizing_text_by_node: dict[str, str],
    allowed_keys: frozenset[str],
    source_turn_id: str | None = None,
    changed_node_ids: frozenset[str] = frozenset(),
    previous_nodes_by_id: dict[str, ConditionNodeV2] | None = None,
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
            require_current_shortlist=node.node_id in changed_node_ids,
            previous_node=(previous_nodes_by_id or {}).get(node.node_id),
        )
        errors.extend(result.errors)
        providers.extend(result.provider_requirements)
    return errors, providers


def derive_provider_requirements(
    condition_ast: ConditionNodeV2 | None,
) -> list[ProviderRequirementV2]:
    """Rebuild static provider contracts from the complete final AST.

    This deliberately replaces, rather than appends to, stored requirements. Removing
    the last provider-backed condition therefore produces an empty list.
    """

    if condition_ast is None:
        return []
    specs = _spec_by_key()
    requirements: list[ProviderRequirementV2] = []
    seen: set[tuple[str, str, str | None]] = set()
    for node in condition_ast.walk():
        if node.node_type.value != "condition" or node.formula != FormulaKind.CAPABILITY:
            continue
        spec = specs.get(node.capability_key or "")
        if spec is None:
            continue
        providers = spec.provider_requirements or (
            (spec.provider_required,) if spec.provider_required else ()
        )
        for provider in providers:
            identity = (provider, spec.key, spec.capability_version)
            if identity in seen:
                continue
            seen.add(identity)
            requirements.append(
                ProviderRequirementV2(
                    provider=provider,
                    capability=spec.key,
                    capability_version=spec.capability_version,
                    source_turn_id=node.source_turn_id,
                    source_fragment=node.source_fragment or spec.label,
                )
            )
    return requirements


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
