from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import ValidationError

from ai_market_monitor.engine.builder_templates import condition_template
from ai_market_monitor.engine.candle_patterns import pattern_names
from ai_market_monitor.engine.capabilities import CapabilitySpec, all_capabilities
from ai_market_monitor.engine.context_conditions import TIME_CONDITION_NAMES
from ai_market_monitor.engine.indicators import IndicatorRegistry
from ai_market_monitor.engine.price_action import PRICE_ACTION_NAMES
from ai_market_monitor.schemas.strategy import ConditionRule

Availability = Literal[
    "available",
    "provider_required",
    "planned",
    "unsupported",
    "experimental",
]


@dataclass(frozen=True, slots=True)
class CapabilityCompatibility:
    key: str
    label: str
    availability: Availability
    template_valid: bool
    evaluator_supported: bool
    prompt_alias_count: int
    required_data: tuple[str, ...]
    notes: tuple[str, ...]

    def to_row(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "availability": self.availability,
            "template_valid": self.template_valid,
            "evaluator_supported": self.evaluator_supported,
            "prompt_alias_count": self.prompt_alias_count,
            "required_data": list(self.required_data),
            "notes": list(self.notes),
        }


def compatibility_report() -> list[CapabilityCompatibility]:
    registry = IndicatorRegistry()
    return [_check_capability(capability, registry) for capability in all_capabilities()]


@lru_cache(maxsize=1)
def compatibility_by_key() -> dict[str, CapabilityCompatibility]:
    return {row.key: row for row in compatibility_report()}


def prompt_executable_capabilities() -> tuple[CapabilitySpec, ...]:
    compatibility = compatibility_by_key()
    return tuple(
        capability
        for capability in all_capabilities()
        if compatibility[capability.key].availability == "available"
    )


def prompt_blocked_capabilities() -> tuple[CapabilitySpec, ...]:
    compatibility = compatibility_by_key()
    return tuple(
        capability
        for capability in all_capabilities()
        if compatibility[capability.key].availability != "available"
    )


def _check_capability(
    capability: CapabilitySpec,
    indicators: IndicatorRegistry,
) -> CapabilityCompatibility:
    notes: list[str] = []
    template_valid = True
    evaluator_supported = True
    try:
        condition = ConditionRule.model_validate(
            condition_template(capability, timeframe="15m")
        )
    except (ValidationError, ValueError, KeyError) as exc:
        template_valid = False
        evaluator_supported = False
        notes.append(f"template_invalid:{type(exc).__name__}")
        condition = None

    if condition is not None:
        operand = condition.left
        if operand.kind.value == "indicator" and not indicators.supports(operand.name or ""):
            evaluator_supported = False
            notes.append(f"unsupported_indicator:{operand.name}")
        evaluator_special_price_actions = {
            "bollinger_reentry",
            "percent_change_up",
            "percent_change_down",
            "time_window",
        }
        if (
            operand.kind.value == "price_action"
            and operand.name not in PRICE_ACTION_NAMES
            and operand.name not in evaluator_special_price_actions
        ):
            evaluator_supported = False
            notes.append(f"unsupported_price_action:{operand.name}")
        if operand.kind.value == "candle_pattern" and operand.name not in pattern_names():
            evaluator_supported = False
            notes.append(f"unsupported_candle_pattern:{operand.name}")
        if operand.kind.value == "market_metric" and operand.name in {"market_cap"}:
            evaluator_supported = False
            notes.append("external_provider_required:market_cap")
        if operand.kind.value == "market_metric" and operand.name in TIME_CONDITION_NAMES:
            notes.append("time_context_supported")

    availability = _availability(capability, template_valid, evaluator_supported)
    return CapabilityCompatibility(
        key=capability.key,
        label=capability.label,
        availability=availability,
        template_valid=template_valid,
        evaluator_supported=evaluator_supported,
        prompt_alias_count=len(capability.aliases),
        required_data=capability.required_data,
        notes=tuple(notes),
    )


def _availability(
    capability: CapabilitySpec,
    template_valid: bool,
    evaluator_supported: bool,
) -> Availability:
    if capability.provider_required:
        return "provider_required"
    if not capability.executable or capability.availability == "unsupported":
        return "unsupported"
    if capability.availability in {"planned", "experimental"}:
        return capability.availability  # type: ignore[return-value]
    if not template_valid or not evaluator_supported:
        return "unsupported"
    return "available"
