from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ai_market_monitor.core.plans import timeframe_to_minutes
from ai_market_monitor.engine.condition_registry import CONDITION_REGISTRY
from ai_market_monitor.schemas.strategy import (
    Comparator,
    ConditionGroup,
    ConditionRule,
    LogicalOperator,
    OperandKind,
    StrategyDefinition,
)


@dataclass(frozen=True, slots=True)
class DiagnosticFinding:
    code: str
    severity: str
    message: str
    condition_keys: tuple[str, ...] = ()
    suggested_fix: str | None = None
    ignorable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "condition_keys": list(self.condition_keys),
            "suggested_fix": self.suggested_fix,
            "ignorable": self.ignorable,
        }


def condition_rules(definition: StrategyDefinition) -> list[ConditionRule]:
    rules: list[ConditionRule] = []

    def walk(node: ConditionRule | ConditionGroup) -> None:
        if isinstance(node, ConditionRule):
            rules.append(node)
            return
        for child in node.children:
            walk(child)

    walk(definition.conditions)
    return rules


def validate_strategy_conflicts(definition: StrategyDefinition) -> list[DiagnosticFinding]:
    rules = condition_rules(definition)
    findings: list[DiagnosticFinding] = []
    signatures: dict[tuple[Any, ...], ConditionRule] = {}
    threshold_groups: dict[tuple[Any, ...], list[ConditionRule]] = {}

    for rule in rules:
        signature = _condition_signature(rule)
        previous = signatures.get(signature)
        if previous is not None:
            findings.append(
                DiagnosticFinding(
                    code="duplicate_condition",
                    severity="warning",
                    message=f"{rule.label} duplicates {previous.label} on {rule.timeframe}.",
                    condition_keys=(previous.key, rule.key),
                    suggested_fix="Remove one duplicate condition.",
                )
            )
        signatures[signature] = rule
        threshold_groups.setdefault(_threshold_group(rule), []).append(rule)
        provider_finding = _provider_finding(rule)
        if provider_finding is not None:
            findings.append(provider_finding)

    for grouped_rules in threshold_groups.values():
        findings.extend(_contradiction_findings(grouped_rules))

    required_count = sum(1 for rule in rules if rule.required)
    if required_count >= 9 and definition.conditions.operator == LogicalOperator.AND:
        findings.append(
            DiagnosticFinding(
                code="overly_strict_combination",
                severity="warning",
                message=(
                    f"{required_count} mandatory conditions are joined by ALL OF logic. "
                    "The monitor may be silent."
                ),
                suggested_fix="Make a secondary confirmation optional or use a nested group.",
            )
        )
    if len(rules) == 1:
        findings.append(
            DiagnosticFinding(
                code="broad_single_condition",
                severity="warning",
                message="A single condition may create frequent or low-context alerts.",
                condition_keys=(rules[0].key,),
                suggested_fix="Add a trend, volume, liquidity, or session filter.",
            )
        )
    if not definition.alerts.channels:
        findings.append(
            DiagnosticFinding(
                code="missing_alert_destination",
                severity="critical",
                message="No alert destination is configured.",
                suggested_fix="Select in-app or Telegram delivery.",
                ignorable=False,
            )
        )
    if definition.alerts.cooldown_seconds >= 43_200:
        findings.append(
            DiagnosticFinding(
                code="cooldown_too_aggressive",
                severity="warning",
                message="The cooldown is at least 12 hours and may hide new occurrences.",
                suggested_fix="Use a shorter cooldown or state-change suppression.",
            )
        )
    if definition.risk.enabled and not definition.targets:
        findings.append(
            DiagnosticFinding(
                code="risk_target_missing",
                severity="critical",
                message="Risk validation is enabled but no target is configured.",
                suggested_fix="Add at least one target or disable risk validation.",
                ignorable=False,
            )
        )
    if definition.trigger_mode.value == "intrabar" and any(
        "candle close" in (rule.explanation_template or "").casefold() for rule in rules
    ):
        findings.append(
            DiagnosticFinding(
                code="trigger_confirmation_conflict",
                severity="warning",
                message="Intrabar triggering conflicts with a candle-close condition.",
                suggested_fix="Use candle-close triggering or rewrite the confirmation.",
            )
        )
    optional = [rule for rule in rules if not rule.required]
    if optional and not definition.near_miss.enabled:
        findings.append(
            DiagnosticFinding(
                code="optional_conditions_limited_effect",
                severity="info",
                message=(
                    "Optional conditions affect proof and completion scoring, but forming "
                    "evidence is disabled."
                ),
                condition_keys=tuple(rule.key for rule in optional),
                suggested_fix="Enable forming evidence if optional context should be visible.",
            )
        )
    return findings


def forecast_from_structure(
    definition: StrategyDefinition,
    *,
    historical_matches: int,
    observation_days: float,
    symbols_observed: int,
) -> dict[str, Any]:
    rules = condition_rules(definition)
    required_count = sum(1 for rule in rules if rule.required)
    optional_count = len(rules) - required_count
    if historical_matches > 0 and observation_days > 0:
        weekly = historical_matches / observation_days * 7
        confidence = "high" if historical_matches >= 20 else "medium"
        source = "historical_scans"
    else:
        timeframe_minutes = _timeframe_minutes(definition.base_timeframe)
        universe_size = (
            len(definition.universe.include_symbols)
            or definition.universe.max_symbols
            or max(symbols_observed, 50)
        )
        opportunities = max(1, universe_size) * (10_080 / timeframe_minutes)
        strictness = 0.24 ** max(1, required_count)
        weekly = opportunities * strictness * max(0.5, 1 - optional_count * 0.04)
        confidence = "low"
        source = "structural_estimate"
    cooldown_minutes = definition.alerts.cooldown_seconds / 60
    if cooldown_minutes > 0:
        symbol_count = (
            len(definition.universe.include_symbols)
            or definition.universe.max_symbols
            or symbols_observed
            or 1
        )
        weekly = min(weekly, 10_080 / cooldown_minutes * symbol_count)
    if definition.alerts.daily_alert_budget is not None:
        weekly = min(weekly, definition.alerts.daily_alert_budget * 7)
    weekly = max(0, weekly)
    low = max(0, weekly * 0.7)
    high = max(low, weekly * 1.3)
    if high < 0.5:
        classification = "likely_silent"
    elif high < 2:
        classification = "very_strict"
    elif high <= 20:
        classification = "reasonable"
    else:
        classification = "likely_noisy"
    warnings: list[str] = []
    suggestions: list[str] = []
    if classification in {"likely_silent", "very_strict"}:
        warnings.append("Historical behavior suggests this monitor may trigger rarely.")
        suggestions.append("Review the top bottleneck or make one secondary rule optional.")
    if classification == "likely_noisy":
        warnings.append("This monitor may create frequent alerts across the selected universe.")
        suggestions.extend(
            [
                "Add a trend or volume confirmation.",
                "Increase the cooldown or reduce the monitored universe.",
            ]
        )
    if required_count >= 8:
        warnings.append("The strategy contains many mandatory conditions.")
    if symbols_observed == 0 and not definition.universe.include_symbols:
        warnings.append("No completed scan history exists for the dynamic universe yet.")
    return {
        "estimated_min_per_week": round(low, 3),
        "estimated_max_per_week": round(high, 3),
        "classification": classification,
        "confidence": confidence,
        "inputs": {
            "source": source,
            "historical_matches": historical_matches,
            "observation_days": round(observation_days, 3),
            "symbols_observed": symbols_observed,
            "required_conditions": required_count,
            "optional_conditions": optional_count,
            "timeframe": definition.base_timeframe,
            "cooldown_seconds": definition.alerts.cooldown_seconds,
        },
        "warnings": warnings,
        "suggestions": suggestions,
    }


def suggest_schema_adjustment(
    definition: StrategyDefinition,
    action: str,
    *,
    bottleneck_key: str | None = None,
) -> tuple[StrategyDefinition, str]:
    payload = deepcopy(definition.model_dump(mode="json"))
    rules = _mutable_rule_nodes(payload["conditions"])
    reason = "A schema-valid monitoring adjustment was prepared for review."

    if action in {"make_stricter", "make_less_noisy", "reduce_false_alerts"}:
        payload["trigger_mode"] = "candle_close"
        payload["alerts"]["cooldown_seconds"] = max(
            int(payload["alerts"].get("cooldown_seconds") or 0),
            1800,
        )
        optional = next((rule for rule in rules if not rule.get("required", True)), None)
        if optional:
            optional["required"] = True
            reason = f"{optional['label']} becomes mandatory and alerts wait for candle close."
        else:
            reason = "Alerts wait for candle close and use at least a 30-minute cooldown."
    elif action in {"make_trigger_earlier", "increase_alert_frequency"}:
        target = next(
            (
                rule
                for rule in reversed(rules)
                if rule.get("required", True) and rule.get("key") != bottleneck_key
            ),
            None,
        )
        required_rules = [rule for rule in rules if rule.get("required", True)]
        if target and len(required_rules) > 1:
            target["required"] = False
            reason = f"{target['label']} becomes optional to increase frequency carefully."
        payload["alerts"]["cooldown_seconds"] = min(
            int(payload["alerts"].get("cooldown_seconds") or 0),
            900,
        )
    elif action == "make_safer":
        payload["trigger_mode"] = "candle_close"
        payload["alerts"]["cooldown_seconds"] = max(
            int(payload["alerts"].get("cooldown_seconds") or 0),
            900,
        )
        reason = "Candle-close confirmation is required and rapid duplicates are limited."
    elif action in {"make_simpler", "beginner_friendly"}:
        kept = [rule for rule in rules if rule.get("required", True)][:3]
        if not kept and rules:
            kept = [rules[0]]
        payload["conditions"] = {
            "node_type": "group",
            "key": "entry_conditions",
            "operator": "and",
            "parameters": {},
            "children": kept,
        }
        payload["trigger_mode"] = "candle_close"
        reason = "The strategy map is reduced to at most three mandatory conditions."
    elif action in {"add_volume_confirmation", "advanced_version"}:
        _append_rule(
            payload,
            {
                "node_type": "condition",
                "key": _unique_key(rules, "volume_confirmation"),
                "label": "Volume at least 1.5x average",
                "condition_type": "market_filter",
                "timeframe": payload["base_timeframe"],
                "left": {
                    "kind": "market_metric",
                    "name": "volume_multiplier",
                    "parameters": {"period": 20},
                },
                "comparator": "gte",
                "right": {"kind": "constant", "value": 1.5},
                "required": True,
                "weight": 1,
                "required_data": ["ohlcv"],
                "explanation_template": ("Volume must be at least 1.5 times its recent average."),
                "forming_tolerance_percent": 10,
            },
        )
        reason = "A deterministic volume confirmation is added as a mandatory rule."
    elif action == "add_market_context_filter":
        timeframe = "1h"
        supporting = set(payload.get("supporting_timeframes") or [])
        if payload["base_timeframe"] != timeframe:
            supporting.add(timeframe)
        payload["supporting_timeframes"] = sorted(supporting)
        _append_rule(
            payload,
            {
                "node_type": "condition",
                "key": _unique_key(rules, "trend_context"),
                "label": "Price above EMA 200 trend context",
                "condition_type": "indicator",
                "timeframe": timeframe,
                "left": {"kind": "price", "field": "close", "parameters": {}},
                "comparator": "gt",
                "right": {
                    "kind": "indicator",
                    "name": "ema",
                    "parameters": {"period": 200},
                },
                "required": True,
                "weight": 1,
                "required_data": ["ohlcv"],
                "explanation_template": "Price must remain above the one-hour EMA 200.",
                "forming_tolerance_percent": 5,
            },
        )
        reason = "A one-hour EMA 200 market-context filter is added."
    elif action == "explain_bottleneck":
        reason = (
            f"The current main bottleneck is {bottleneck_key}."
            if bottleneck_key
            else "More scan history is needed before a main bottleneck can be identified."
        )
    else:
        raise ValueError(f"Unsupported suggestion action: {action}")
    return StrategyDefinition.model_validate(payload), reason


def schema_diff(before: StrategyDefinition, after: StrategyDefinition) -> list[dict[str, Any]]:
    left = before.model_dump(mode="json")
    right = after.model_dump(mode="json")
    changes: list[dict[str, Any]] = []
    for section in (
        "trigger_mode",
        "conditions",
        "universe",
        "alerts",
        "risk",
        "entry",
        "stop",
        "targets",
        "near_miss",
        "expiry",
    ):
        if left.get(section) != right.get(section):
            changes.append(
                {
                    "section": section,
                    "before": left.get(section),
                    "after": right.get(section),
                }
            )
    return changes


def health_status(score: float) -> tuple[str, str]:
    if score >= 85:
        return "healthy", "A"
    if score >= 70:
        return "usable", "B"
    if score >= 55:
        return "needs_review", "C"
    if score >= 35:
        return "problematic", "D"
    return "unhealthy", "F"


def _condition_signature(rule: ConditionRule) -> tuple[Any, ...]:
    right = rule.right.model_dump(mode="json") if rule.right else None
    return (
        rule.timeframe,
        str(rule.left.model_dump(mode="json")),
        rule.comparator.value,
        str(right),
        rule.required,
    )


def _threshold_group(rule: ConditionRule) -> tuple[Any, ...]:
    return (
        rule.timeframe,
        rule.left.kind.value,
        rule.left.name,
        rule.left.field,
        str(sorted(rule.left.parameters.items())),
    )


def _constant_value(rule: ConditionRule) -> float | None:
    if rule.right is None or rule.right.kind != OperandKind.CONSTANT:
        return None
    value = rule.right.value
    return float(value) if isinstance(value, int | float) else None


def _contradiction_findings(rules: list[ConditionRule]) -> list[DiagnosticFinding]:
    lower_bounds: list[tuple[float, ConditionRule]] = []
    upper_bounds: list[tuple[float, ConditionRule]] = []
    equalities: list[tuple[float, ConditionRule]] = []
    for rule in rules:
        value = _constant_value(rule)
        if value is None:
            continue
        if rule.comparator in {Comparator.GREATER_THAN, Comparator.GREATER_THAN_OR_EQUAL}:
            lower_bounds.append((value, rule))
        elif rule.comparator in {Comparator.LESS_THAN, Comparator.LESS_THAN_OR_EQUAL}:
            upper_bounds.append((value, rule))
        elif rule.comparator == Comparator.EQUAL:
            equalities.append((value, rule))
    findings: list[DiagnosticFinding] = []
    if lower_bounds and upper_bounds:
        lower = max(lower_bounds, key=lambda item: item[0])
        upper = min(upper_bounds, key=lambda item: item[0])
        if lower[0] > upper[0]:
            findings.append(
                DiagnosticFinding(
                    code="contradictory_thresholds",
                    severity="critical",
                    message=(
                        f"{lower[1].label} requires at least {lower[0]:g}, while "
                        f"{upper[1].label} requires at most {upper[0]:g}."
                    ),
                    condition_keys=(lower[1].key, upper[1].key),
                    suggested_fix=(
                        "Correct the thresholds or place alternatives in an ANY OF group."
                    ),
                    ignorable=False,
                )
            )
    if len({value for value, _ in equalities}) > 1:
        findings.append(
            DiagnosticFinding(
                code="conflicting_equalities",
                severity="critical",
                message="The same metric is required to equal different values.",
                condition_keys=tuple(rule.key for _, rule in equalities),
                suggested_fix="Keep one equality or use alternative branches.",
                ignorable=False,
            )
        )
    return findings


def _provider_finding(rule: ConditionRule) -> DiagnosticFinding | None:
    if rule.provider_required or rule.availability == "provider_required":
        provider = ", ".join(rule.required_data) or "external/runtime context"
        severity = "critical" if rule.required else "warning"
        return DiagnosticFinding(
            code="required_data_unavailable",
            severity=severity,
            message=f"{rule.label} requires {provider}, which is not configured.",
            condition_keys=(rule.key,),
            suggested_fix="Configure the provider or make this condition optional.",
            ignorable=not rule.required,
        )
    names = [rule.left.name, rule.right.name if rule.right else None]
    for name in names:
        if not name:
            continue
        try:
            capability = CONDITION_REGISTRY.get(name)
        except KeyError:
            continue
        if capability.executable:
            continue
        provider = capability.provider_required or "runtime context"
        severity = "critical" if rule.required else "warning"
        return DiagnosticFinding(
            code="required_data_unavailable",
            severity=severity,
            message=f"{rule.label} requires {provider}, which is not configured.",
            condition_keys=(rule.key,),
            suggested_fix="Configure the provider or make this condition optional.",
            ignorable=not rule.required,
        )
    return None


def _timeframe_minutes(timeframe: str) -> int:
    """How many minutes one candle covers, from the one table that knows.

    The hand-written version this replaces ended in a bare ``return 1440``: any period it
    did not recognise — including a typo — became a daily candle in silence, and the
    weekly opportunity forecast built on it was then wrong by a factor of hundreds.
    """

    return timeframe_to_minutes(timeframe)


def _mutable_rule_nodes(group: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for child in group.get("children", []):
        if child.get("node_type") == "condition":
            rows.append(child)
        else:
            rows.extend(_mutable_rule_nodes(child))
    return rows


def _append_rule(payload: dict[str, Any], rule: dict[str, Any]) -> None:
    conditions = payload["conditions"]
    if conditions["operator"] != "and":
        payload["conditions"] = {
            "node_type": "group",
            "key": "entry_conditions",
            "operator": "and",
            "parameters": {},
            "children": [conditions, rule],
        }
        return
    conditions["children"].append(rule)


def _unique_key(rules: list[dict[str, Any]], base: str) -> str:
    existing = {str(rule.get("key")) for rule in rules}
    if base not in existing:
        return base
    index = 2
    while f"{base}_{index}" in existing:
        index += 1
    return f"{base}_{index}"
