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
from ai_market_monitor.engine.provider_families import (
    ProviderAvailability,
    runtime_availability,
)
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


def compatibility_report(
    availability: ProviderAvailability | None = None,
) -> list[CapabilityCompatibility]:
    resolved = availability or runtime_availability()
    registry = IndicatorRegistry()
    return [_check_capability(capability, registry, resolved) for capability in all_capabilities()]


def compatibility_by_key(
    availability: ProviderAvailability | None = None,
) -> dict[str, CapabilityCompatibility]:
    """The compatibility row for every capability, keyed by capability key.

    Resolving the deployment's availability *before* the cache is what keeps the cache
    honest. Caching a no-argument call would freeze whichever answer happened to be
    correct at import time, and a deployment that later configured a feed would keep
    reading the old one for the life of the process.
    """

    return _compatibility_by_key(availability or runtime_availability())


@lru_cache(maxsize=8)
def _compatibility_by_key(
    availability: ProviderAvailability,
) -> dict[str, CapabilityCompatibility]:
    return {row.key: row for row in compatibility_report(availability)}


def prompt_executable_capabilities(
    availability: ProviderAvailability | None = None,
) -> tuple[CapabilitySpec, ...]:
    compatibility = compatibility_by_key(availability)
    return tuple(
        capability
        for capability in all_capabilities()
        if compatibility[capability.key].availability == "available"
    )


def prompt_blocked_capabilities(
    availability: ProviderAvailability | None = None,
) -> tuple[CapabilitySpec, ...]:
    compatibility = compatibility_by_key(availability)
    return tuple(
        capability
        for capability in all_capabilities()
        if compatibility[capability.key].availability != "available"
    )


def _check_capability(
    capability: CapabilitySpec,
    indicators: IndicatorRegistry,
    availability: ProviderAvailability,
) -> CapabilityCompatibility:
    notes: list[str] = []
    template_valid = True
    evaluator_supported = True
    try:
        condition = ConditionRule.model_validate(condition_template(capability, timeframe="15m"))
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

    resolved = _availability(capability, template_valid, evaluator_supported, availability)
    if resolved == "provider_required":
        notes.append(f"feed_not_configured:{capability.provider_required}")
    return CapabilityCompatibility(
        key=capability.key,
        label=capability.label,
        availability=resolved,
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
    availability: ProviderAvailability,
) -> Availability:
    # Asked of the deployment, not read off the label. A capability that names a feed is
    # hidden only while that feed cannot answer; when it can, the capability goes on to
    # face exactly the same template and evaluator checks as every other one, and is
    # published only if it passes them. The old code returned here unconditionally, so a
    # feed the platform serves itself — the order book, the risk numbers, the alert
    # budget — could never be reached however the product was configured.
    if capability.provider_required and not availability.serves(capability.provider_required):
        return "provider_required"
    if not capability.executable or capability.availability == "unsupported":
        return "unsupported"
    if capability.availability in {"planned", "experimental"}:
        return capability.availability  # type: ignore[return-value]
    if not template_valid or not evaluator_supported:
        return "unsupported"
    return "available"
