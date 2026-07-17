from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

from ai_market_monitor.db.models.enums import ConditionType
from ai_market_monitor.engine.capabilities import all_capabilities
from ai_market_monitor.schemas.strategy import (
    Comparator,
    ConditionRule,
    InterpretationIssue,
    Operand,
    OperandKind,
)

SUPPORTED_TIMEFRAMES = {
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
}
SYNTHETIC_EVALUATOR_CAPABILITIES = {"candle_change_percent"}
TIMEFRAME_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
}

OperandParameterValue = int | float | str | bool | list[int | float | str | bool]


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    group_key: str
    semantic_type: str
    phrase: str
    source_fragment: str
    start: int
    end: int
    canonical_intent: str
    capability_key: str | None
    timeframe: str
    required: bool
    negated: bool
    confidence: float
    reason: str


@dataclass(slots=True)
class PromptSemanticResult:
    conditions: list[ConditionRule] = field(default_factory=list)
    issues: list[InterpretationIssue] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    matches: list[SemanticMatch] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "vocabulary_version": prompt_vocabulary().get("version"),
            "semantic_matches": [
                {
                    "group_key": match.group_key,
                    "semantic_type": match.semantic_type,
                    "source_fragment": match.source_fragment,
                    "canonical_intent": match.canonical_intent,
                    "capability_key": match.capability_key,
                    "timeframe": match.timeframe,
                    "required": match.required,
                    "negated": match.negated,
                    "confidence": match.confidence,
                    "reason": match.reason,
                }
                for match in self.matches
            ],
        }


@lru_cache(maxsize=1)
def prompt_vocabulary() -> dict[str, Any]:
    with (
        resources.files("ai_market_monitor.engine")
        .joinpath("prompt_vocabulary.json")
        .open(encoding="utf-8") as handle
    ):
        payload = json.load(handle)
    _validate_vocabulary(payload)
    return payload


@lru_cache(maxsize=1)
def executable_capability_keys() -> frozenset[str]:
    return frozenset(capability.key for capability in all_capabilities() if capability.executable)


@lru_cache(maxsize=1)
def provider_required_capability_keys() -> frozenset[str]:
    return frozenset(
        capability.key for capability in all_capabilities() if capability.provider_required
    )


def normalize_prompt_text(text: str) -> str:
    cleaned = (text or "").casefold()
    cleaned = cleaned.replace("％", "%")
    cleaned = re.sub(r"[\u2010-\u2015]", "-", cleaned)
    cleaned = re.sub(r"[_/]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def analyze_prompt_semantics(prompt: str, default_timeframe: str = "15m") -> PromptSemanticResult:
    vocabulary = prompt_vocabulary()
    normalized = normalize_prompt_text(prompt)
    result = PromptSemanticResult()
    seen_conditions: set[tuple[str, str, str]] = set()
    seen_issues: set[tuple[str, str]] = set()

    for group in vocabulary.get("phrase_groups", []):
        for phrase in group.get("phrases", []):
            for match in _phrase_matches(prompt, phrase):
                if not _context_gate_passes(normalized, match.start(), match.end(), group):
                    continue
                semantic_type = str(group.get("semantic_type") or "")
                source_fragment = prompt[match.start() : match.end()]
                timeframe = _timeframe_near(prompt, match.start(), match.end(), default_timeframe)
                required = _required_near(prompt, match.start(), match.end(), vocabulary)
                negated = _negated_near(prompt, match.start(), match.end(), vocabulary)
                semantic_match = SemanticMatch(
                    group_key=str(group["key"]),
                    semantic_type=semantic_type,
                    phrase=phrase,
                    source_fragment=source_fragment,
                    start=match.start(),
                    end=match.end(),
                    canonical_intent=str(group.get("canonical_intent") or group["key"]),
                    capability_key=group.get("capability_key"),
                    timeframe=timeframe,
                    required=required,
                    negated=negated,
                    confidence=_confidence_for_group(group, source_fragment),
                    reason="Matched data-driven prompt vocabulary group.",
                )

                if semantic_type == "condition":
                    condition = _condition_from_group(
                        group,
                        prompt=prompt,
                        source_fragment=source_fragment,
                        start=match.start(),
                        end=match.end(),
                        timeframe=timeframe,
                        required=required,
                        negated=negated,
                    )
                    if condition is None:
                        continue
                    capability_key = str(group.get("capability_key") or "")
                    if capability_key:
                        condition = condition.model_copy(update={"capability_key": capability_key})
                    condition_key = (
                        condition.key,
                        condition.timeframe,
                        condition.comparator.value,
                    )
                    if condition_key not in seen_conditions:
                        result.conditions.append(condition)
                        result.matches.append(semantic_match)
                        seen_conditions.add(condition_key)
                    assumption = group.get("assumption")
                    if assumption and str(assumption) not in result.assumptions:
                        result.assumptions.append(str(assumption))
                elif semantic_type == "provider_required":
                    issue = _provider_required_issue(group, source_fragment, required)
                    issue_key = (issue.code, issue.source_fragment or "")
                    if issue_key not in seen_issues:
                        result.issues.append(issue)
                        result.matches.append(semantic_match)
                        seen_issues.add(issue_key)
                elif semantic_type == "vague":
                    issue = _vague_issue(source_fragment)
                    issue_key = (issue.code, issue.source_fragment or "")
                    if issue_key not in seen_issues:
                        result.issues.append(issue)
                        result.matches.append(semantic_match)
                        seen_issues.add(issue_key)

    return result


def _validate_vocabulary(payload: dict[str, Any]) -> None:
    keys = [group.get("key") for group in payload.get("phrase_groups", [])]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"Duplicate prompt vocabulary groups: {duplicates}")


def _phrase_matches(prompt: str, phrase: str) -> list[re.Match[str]]:
    if not phrase:
        return []
    separator = r"[\s\-]+"
    escaped = separator.join(re.escape(part) for part in phrase.split())
    pattern = rf"(?<!\w){escaped}(?!\w)"
    return list(re.finditer(pattern, prompt, flags=re.IGNORECASE))


def _context_gate_passes(normalized: str, start: int, end: int, group: dict[str, Any]) -> bool:
    phrase_window = normalized[max(0, start - 48) : min(len(normalized), end + 48)]
    phrase_text = normalized[start:end]
    if any(term in phrase_window for term in group.get("blocked_context", [])):
        return False
    required_context = set(group.get("context_required", []))
    close_open_phrase = any(
        term in phrase_text
        for term in (
            "close above open",
            "close below open",
            "closed above open",
            "closed below open",
            "closed green",
            "closed red",
            "finished green",
            "finished red",
            "ended green",
            "ended red",
        )
    )
    if "candle" in required_context and "candle" not in phrase_window and not close_open_phrase:
        return False
    if "close_open" in required_context and not any(
        term in phrase_window
        for term in (
            "candle",
            "close above open",
            "close below open",
            "closed",
            "finished",
            "ended",
        )
    ):
        return False
    if "price_move" in required_context:
        if "%" not in phrase_window:
            return False
        if "candle" in phrase_window:
            return False
    return True


def _timeframe_near(prompt: str, start: int, end: int, fallback: str) -> str:
    vocabulary = prompt_vocabulary()
    fallback = _normalize_timeframe(fallback) or "15m"
    window = prompt[max(0, start - 80) : min(len(prompt), end + 80)].casefold()
    for raw, timeframe in vocabulary.get("timeframes", {}).items():
        if re.search(rf"(?<!\w){re.escape(raw)}(?!\w)", window):
            return timeframe
    match = re.search(r"\b(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)\b", window)
    if match:
        return match.group(1)
    if "daily" in window or "day" in window:
        return "1d"
    if "hourly" in window or "hour" in window:
        return "1h"
    return fallback if fallback in SUPPORTED_TIMEFRAMES else "15m"


def _normalize_timeframe(value: str) -> str:
    vocabulary = prompt_vocabulary()
    lowered = (value or "").strip().casefold()
    return str(vocabulary.get("timeframes", {}).get(lowered, lowered))


def _required_near(prompt: str, start: int, end: int, vocabulary: dict[str, Any]) -> bool:
    window = prompt[max(0, start - 96) : min(len(prompt), end + 48)].casefold()
    return not any(phrase in window for phrase in vocabulary.get("optional_phrases", []))


def _negated_near(prompt: str, start: int, end: int, vocabulary: dict[str, Any]) -> bool:
    before = prompt[max(0, start - 72) : start].casefold()
    return any(
        re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", before)
        for phrase in vocabulary.get("negation_phrases", [])
    )


def _confidence_for_group(group: dict[str, Any], source_fragment: str) -> float:
    if group.get("semantic_type") == "vague":
        return 0.2
    if group.get("semantic_type") == "provider_required":
        return 0.8
    if len(source_fragment.split()) <= 1:
        return 0.72
    return 0.92


def _condition_from_group(
    group: dict[str, Any],
    *,
    prompt: str,
    source_fragment: str,
    start: int,
    end: int,
    timeframe: str,
    required: bool,
    negated: bool,
) -> ConditionRule | None:
    capability_key = str(group.get("capability_key") or "")
    if (
        capability_key
        and capability_key not in executable_capability_keys()
        and capability_key not in SYNTHETIC_EVALUATOR_CAPABILITIES
    ):
        return None
    semantic_intent = str(group.get("canonical_intent") or "")
    if semantic_intent.startswith("candle_direction_"):
        candle_parameters: dict[str, OperandParameterValue] = {}
        if _previous_candle_context(prompt, start, end):
            candle_parameters["offset"] = 1
        return _candle_condition(
            key=str(group["operand_name"]),
            label=str(group.get("default_label") or source_fragment),
            timeframe=timeframe,
            name=str(group["operand_name"]),
            source_fragment=source_fragment,
            required=required,
            negated=negated,
            parameters=candle_parameters,
        )
    if semantic_intent.startswith("candle_body_percent_"):
        threshold = _threshold_percent_near(prompt, start, end)
        if threshold is None:
            return None
        direction = str(group.get("direction") or "absolute")
        offset = 1 if _previous_candle_context(prompt, start, end) else 0
        body_parameters: dict[str, OperandParameterValue] = {
            "threshold_percent": threshold,
            "direction": direction,
            **_event_search_parameters(prompt, timeframe),
        }
        if offset:
            body_parameters["offset"] = offset
        return _candle_condition(
            key=f"candle_move_{str(threshold).replace('.', '_')}pct",
            label=(
                f"{'Previous ' if offset else ''}{timeframe} candle moved "
                f"{'up' if direction == 'up' else 'down'} at least {threshold:g}%"
            ),
            timeframe=timeframe,
            name="candle_change_percent",
            source_fragment=source_fragment,
            required=required,
            negated=negated,
            parameters=body_parameters,
            confidence=0.9,
        )
    if semantic_intent.startswith("price_percent_change_"):
        threshold = _threshold_percent_near(prompt, start, end)
        if threshold is None:
            return None
        direction = str(group.get("direction") or "up")
        lookback = _lookback_candles(prompt, timeframe)
        operand_name = "percent_change_down" if direction == "down" else "percent_change_up"
        label_direction = "decreased" if direction == "down" else "increased"
        return _price_action_condition(
            key=f"price_{direction}_{str(threshold).replace('.', '_')}pct",
            label=f"Price {label_direction} by at least {threshold:g}%",
            timeframe=timeframe,
            name=operand_name,
            source_fragment=source_fragment,
            required=required,
            parameters={"threshold_percent": threshold, "lookback": lookback},
            confidence=0.9,
        )
    if semantic_intent in {
        "relative_volume_above_average",
        "minimum_relative_volume_assumption",
        "relative_volume_below_average",
    }:
        comparator = Comparator(str(group.get("default_comparator") or "gte"))
        threshold = float(group.get("default_threshold") or 1.0)
        if negated:
            comparator = _invert_comparator(comparator)
        return _indicator_constant(
            key="relative_volume",
            label=str(group.get("default_label") or "Relative volume condition"),
            timeframe=timeframe,
            indicator="volume_ratio",
            comparator=comparator,
            threshold=threshold,
            source_fragment=source_fragment,
            required=required,
            parameters={"period": 20},
            confidence=0.88 if semantic_intent == "minimum_relative_volume_assumption" else 0.9,
            forming_tolerance_percent=15,
        )
    if semantic_intent in {"price_reclaims_vwap", "price_above_vwap"}:
        comparator = Comparator(str(group.get("default_comparator") or "gt"))
        if negated:
            comparator = _invert_comparator(comparator)
        return _price_vs_indicator(
            key=(
                "price_reclaims_vwap"
                if comparator == Comparator.CROSSES_ABOVE
                else "price_above_vwap"
            ),
            label=str(group.get("default_label") or "Price above VWAP"),
            timeframe=timeframe,
            indicator="vwap",
            period=20,
            comparator=comparator,
            source_fragment=source_fragment,
            required=required,
        )
    if semantic_intent in {"price_above_ema", "price_reclaims_ema", "price_below_ema"}:
        period = _period_near(prompt, start, end, int(group.get("default_period") or 200))
        comparator = Comparator(str(group.get("default_comparator") or "gt"))
        if negated:
            comparator = _invert_comparator(comparator)
        relation = {
            Comparator.GREATER_THAN: "above",
            Comparator.GREATER_THAN_OR_EQUAL: "above",
            Comparator.LESS_THAN: "below",
            Comparator.LESS_THAN_OR_EQUAL: "below",
            Comparator.CROSSES_ABOVE: "reclaims",
            Comparator.CROSSES_BELOW: "loses",
        }.get(comparator, "vs")
        return _price_vs_indicator(
            key=f"price_{relation}_ema_{period}",
            label=f"Price {relation} EMA {period}",
            timeframe=timeframe,
            indicator="ema",
            period=period,
            comparator=comparator,
            source_fragment=source_fragment,
            required=required,
        )
    if semantic_intent == "rsi_crosses_above_30":
        return _indicator_constant(
            key="rsi_exits_oversold",
            label=str(group.get("default_label") or "RSI exits oversold"),
            timeframe=timeframe,
            indicator="rsi",
            comparator=Comparator.CROSSES_ABOVE,
            threshold=float(group.get("default_threshold") or 30),
            source_fragment=source_fragment,
            required=required,
            parameters={"period": 14},
            confidence=0.9,
        )
    if semantic_intent.endswith("_session_filter"):
        parameters = _parameter_dict(group.get("default_parameters"))
        return ConditionRule(
            key="time_window_new_york" if "new_york" in semantic_intent else "time_window",
            label=str(group.get("default_label") or "Session time filter"),
            condition_type=ConditionType.MARKET_FILTER,
            timeframe=timeframe,
            left=Operand(kind=OperandKind.MARKET_METRIC, name="time_window", parameters=parameters),
            comparator=Comparator.IS_TRUE,
            required=required,
            required_data=["time"],
            source_fragment=source_fragment,
            confidence=0.9,
        )
    condition_type = str(group.get("condition_type") or "")
    operand_name = str(group.get("operand_name") or "")
    if condition_type == "price_action" and operand_name:
        return _price_action_condition(
            key=str(group.get("key") or operand_name),
            label=str(group.get("default_label") or source_fragment),
            timeframe=timeframe,
            name=operand_name,
            source_fragment=source_fragment,
            required=required,
            parameters=_parameter_dict(group.get("default_parameters")),
            confidence=float(group.get("confidence", 0.86)),
        )
    if condition_type == "indicator" and operand_name:
        comparator = Comparator(str(group.get("default_comparator") or "gte"))
        threshold = float(group.get("default_threshold", 0))
        if negated:
            comparator = _invert_comparator(comparator)
        return _indicator_constant(
            key=str(group.get("key") or operand_name),
            label=str(group.get("default_label") or source_fragment),
            timeframe=timeframe,
            indicator=operand_name,
            comparator=comparator,
            threshold=threshold,
            source_fragment=source_fragment,
            required=required,
            parameters=_parameter_dict(group.get("default_parameters")),
            confidence=float(group.get("confidence", 0.86)),
        )
    return None


def _parameter_dict(raw: object) -> dict[str, OperandParameterValue]:
    if not isinstance(raw, dict):
        return {}
    parameters: dict[str, OperandParameterValue] = {}
    for raw_key, value in raw.items():
        if not isinstance(raw_key, str):
            continue
        if isinstance(value, (bool, int, float, str)):
            parameters[raw_key] = value
            continue
        if isinstance(value, list) and all(
            isinstance(item, (bool, int, float, str)) for item in value
        ):
            parameters[raw_key] = value
    return parameters


def _provider_required_issue(
    group: dict[str, Any],
    source_fragment: str,
    required: bool,
) -> InterpretationIssue:
    provider = str(group.get("provider_required") or "external provider")
    return InterpretationIssue(
        code="provider_required",
        field="setup_text",
        message=(
            f"'{source_fragment}' requires {provider} data and is not available for "
            "live beta activation without a configured provider."
        ),
        blocking=required,
        source_fragment=source_fragment,
    )


def _vague_issue(source_fragment: str) -> InterpretationIssue:
    return InterpretationIssue(
        code="ambiguous_discretionary_language",
        field="setup_text",
        message=(
            "This phrase needs a measurable definition before it can be monitored: "
            f"'{source_fragment}'."
        ),
        blocking=True,
        source_fragment=source_fragment,
    )


def _candle_condition(
    *,
    key: str,
    label: str,
    timeframe: str,
    name: str,
    source_fragment: str,
    required: bool,
    negated: bool = False,
    parameters: dict[str, OperandParameterValue] | None = None,
    confidence: float = 0.92,
) -> ConditionRule:
    return ConditionRule(
        key=_key(key),
        label=("Not " + label[0].lower() + label[1:] if negated else label),
        condition_type=ConditionType.CANDLE_PATTERN,
        timeframe=timeframe,
        left=Operand(kind=OperandKind.CANDLE_PATTERN, name=name, parameters=parameters or {}),
        comparator=Comparator.IS_FALSE if negated else Comparator.IS_TRUE,
        required=required,
        weight=1,
        required_data=["ohlcv"],
        explanation_template=f"{label}: actual {{actual}}; required {{required}}; {{state}}.",
        source_fragment=source_fragment,
        confidence=confidence,
    )


def _price_action_condition(
    *,
    key: str,
    label: str,
    timeframe: str,
    name: str,
    source_fragment: str,
    required: bool,
    parameters: dict[str, OperandParameterValue] | None = None,
    confidence: float = 0.9,
) -> ConditionRule:
    return ConditionRule(
        key=_key(key),
        label=label,
        condition_type=ConditionType.PRICE_ACTION,
        timeframe=timeframe,
        left=Operand(kind=OperandKind.PRICE_ACTION, name=name, parameters=parameters or {}),
        comparator=Comparator.IS_TRUE,
        required=required,
        weight=1.5,
        forming_tolerance_percent=10,
        required_data=["ohlcv"],
        explanation_template=f"{label}: actual {{actual}}; required {{required}}; {{state}}.",
        source_fragment=source_fragment,
        confidence=confidence,
    )


def _indicator_constant(
    *,
    key: str,
    label: str,
    timeframe: str,
    indicator: str,
    comparator: Comparator,
    threshold: float,
    source_fragment: str,
    required: bool,
    parameters: dict[str, OperandParameterValue] | None = None,
    confidence: float = 0.9,
    forming_tolerance_percent: float | None = None,
) -> ConditionRule:
    return ConditionRule(
        key=_key(key),
        label=label,
        condition_type=ConditionType.INDICATOR,
        timeframe=timeframe,
        left=Operand(kind=OperandKind.INDICATOR, name=indicator, parameters=parameters or {}),
        comparator=comparator,
        right=Operand(kind=OperandKind.CONSTANT, value=threshold),
        required=required,
        weight=1,
        forming_tolerance_percent=forming_tolerance_percent,
        required_data=["ohlcv"],
        explanation_template=f"{label}: actual {{actual}}; required {{required}}; {{state}}.",
        source_fragment=source_fragment,
        confidence=confidence,
    )


def _price_vs_indicator(
    *,
    key: str,
    label: str,
    timeframe: str,
    indicator: str,
    period: int,
    comparator: Comparator,
    source_fragment: str,
    required: bool,
) -> ConditionRule:
    return ConditionRule(
        key=_key(key),
        label=label,
        condition_type=ConditionType.INDICATOR,
        timeframe=timeframe,
        left=Operand(kind=OperandKind.PRICE, field="close"),
        comparator=comparator,
        right=Operand(
            kind=OperandKind.INDICATOR,
            name=indicator,
            parameters={"period": period, "field": "close"}
            if indicator in {"ema", "sma"}
            else {"period": period},
        ),
        required=required,
        weight=1,
        forming_tolerance_percent=5,
        required_data=["ohlcv"],
        explanation_template=f"{label}: actual {{actual}}; required {{required}}; {{state}}.",
        source_fragment=source_fragment,
        confidence=0.9,
    )


def _threshold_percent_near(prompt: str, start: int, end: int) -> float | None:
    window = prompt[max(0, start - 40) : min(len(prompt), end + 56)]
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", window)
    return float(match.group(1)) if match else None


def _previous_candle_context(prompt: str, start: int, end: int) -> bool:
    window = prompt[max(0, start - 56) : min(len(prompt), end + 24)].casefold()
    return any(term in window for term in ("previous", "prior", "last closed", "last candle"))


def _period_near(prompt: str, start: int, end: int, default: int) -> int:
    window = prompt[max(0, start - 40) : min(len(prompt), end + 40)].casefold()
    match = re.search(r"(?:ema|sma|ma)\s*(\d{1,4})|(\d{1,4})\s*(?:ema|sma|ma)", window)
    if not match:
        return default
    return max(1, min(5000, int(match.group(1) or match.group(2))))


def _event_search_parameters(prompt: str, timeframe: str) -> dict[str, int | str]:
    lowered = prompt.casefold()
    if not any(
        phrase in lowered
        for phrase in (
            "had a",
            "had an",
            "any candle",
            "at any time",
            "over the last",
            "over the past",
            "within the last",
            "last ",
            "past ",
            "did not have any",
            "does not have any",
            "not have any",
            "in 20",
        )
    ):
        return {}
    lookback = _lookback_candles(prompt, timeframe)
    return {"search_lookback": lookback} if lookback != 100 else {}


def _lookback_candles(prompt: str, timeframe: str) -> int:
    lowered = prompt.casefold()
    minutes = TIMEFRAME_MINUTES.get(timeframe, 15)
    candle_match = re.search(r"(?:last|past)\s+(\d+)\s*(?:candle|candles|bars)", lowered)
    if candle_match:
        return max(1, min(50_000, int(candle_match.group(1))))
    hour_match = re.search(
        r"(?:last|past|previous|within the last)\s+(\d+)[ -]?(?:hour|hours|h)\b",
        lowered,
    )
    if hour_match:
        return max(1, min(50_000, int((int(hour_match.group(1)) * 60) / minutes)))
    day_match = re.search(
        r"(?:last|past|previous|over the last|over the past)\s+(\d+)[ -]?days?",
        lowered,
    )
    if day_match:
        return max(1, min(50_000, int((int(day_match.group(1)) * 24 * 60) / minutes)))
    if any(term in lowered for term in ("today", "since midnight", "daily move", "this day")):
        return max(1, int((24 * 60) / minutes))
    if any(
        term in lowered for term in ("past day", "last day", "last 24 hours", "24h", "24 hours")
    ):
        return max(1, int((24 * 60) / minutes))
    if any(
        term in lowered for term in ("past week", "last week", "7 days", "seven days", "this week")
    ):
        return max(1, int((7 * 24 * 60) / minutes))
    if any(
        term in lowered
        for term in ("past month", "last month", "30 days", "thirty days", "this month")
    ):
        return max(1, int((30 * 24 * 60) / minutes))
    return 1


def _invert_comparator(comparator: Comparator) -> Comparator:
    return {
        Comparator.GREATER_THAN: Comparator.LESS_THAN_OR_EQUAL,
        Comparator.GREATER_THAN_OR_EQUAL: Comparator.LESS_THAN,
        Comparator.LESS_THAN: Comparator.GREATER_THAN_OR_EQUAL,
        Comparator.LESS_THAN_OR_EQUAL: Comparator.GREATER_THAN,
        Comparator.CROSSES_ABOVE: Comparator.CROSSES_BELOW,
        Comparator.CROSSES_BELOW: Comparator.CROSSES_ABOVE,
    }.get(comparator, Comparator.IS_FALSE if comparator == Comparator.IS_TRUE else comparator)


def _key(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    key = re.sub(r"_+", "_", key)
    if not key or not key[0].isalpha():
        key = f"condition_{key}"
    return key[:100]
