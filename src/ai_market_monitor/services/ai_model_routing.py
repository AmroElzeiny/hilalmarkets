from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from ai_market_monitor.core.config import Settings

_TIMEFRAME_RE = re.compile(r"(?<!\w)(?:[1-9]\d*)(?:m|h|d|w)(?!\w)", re.IGNORECASE)
_CORRECTION_RE = re.compile(
    r"^(?:no\b|i mean\b|what i mean\b|correction\b|change\b|instead\b|not .+ like before)",
    re.IGNORECASE,
)
_CLARIFICATION_FRICTION_RE = re.compile(
    r"\b(?:that is not what i mean|you asked this already|again|still wrong|i do not understand|"
    r"i don't understand)\b",
    re.IGNORECASE,
)
_ROUTING_TECHNICAL_TERMS = frozenset(
    {
        "above",
        "average",
        "below",
        "breakout",
        "candle",
        "close",
        "confirmation",
        "daily",
        "high",
        "invalidated",
        "low",
        "optional",
        "previous",
        "required",
        "retest",
        "sweep",
        "volume",
        "weekly",
    }
)


@dataclass(frozen=True, slots=True)
class AISetupModelRoute:
    model: str
    reasoning_effort: str
    tier: str
    reasons: tuple[str, ...]
    condition_count: int
    correction_count: int

    def usage_metadata(self) -> dict[str, Any]:
        return {
            "_traceedge_model": self.model,
            "_traceedge_reasoning_effort": self.reasoning_effort,
            "_traceedge_route_tier": self.tier,
            "_traceedge_route_reasons": list(self.reasons),
            "_traceedge_condition_count": self.condition_count,
            "_traceedge_correction_count": self.correction_count,
        }


def select_setup_model(
    settings: Settings,
    *,
    current_message: str,
    accumulated_setup: str = "",
    history: Iterable[dict[str, str]] = (),
    active_clarification: dict[str, Any] | None = None,
    capability_context: dict[str, Any] | None = None,
) -> AISetupModelRoute:
    """Select a configured model tier without granting it any additional authority."""

    conversation = list(history)
    combined = "\n".join(value for value in (accumulated_setup, current_message) if value)
    lowered = combined.casefold()
    condition_count = _condition_count(combined)
    reasons: list[str] = []

    has_and = bool(re.search(r"\band\b|&&", lowered))
    has_or = bool(re.search(r"\bor\b|\|\|", lowered))
    has_not = bool(re.search(r"\b(?:not|avoid|without|unless|except)\b", lowered))
    if condition_count >= settings.ai_setup_complex_condition_threshold:
        reasons.append("four_or_more_conditions")
    if (has_and and has_or) or (has_not and (has_and or has_or)):
        reasons.append("mixed_boolean_logic")
    if len(set(_TIMEFRAME_RE.findall(lowered))) > 1:
        reasons.append("multiple_timeframes")
    if _looks_contradictory(lowered):
        reasons.append("possible_contradiction")

    user_turns = [
        str(item.get("content") or "").strip()
        for item in conversation
        if item.get("role") == "user"
    ]
    correction_count = sum(bool(_CORRECTION_RE.search(item)) for item in user_turns)
    correction_count += bool(_CORRECTION_RE.search(current_message.strip()))
    if correction_count >= settings.ai_setup_repeated_correction_threshold:
        reasons.append("repeated_corrections")

    clarification_friction = sum(
        bool(_CLARIFICATION_FRICTION_RE.search(item)) for item in user_turns[-8:]
    )
    clarification_friction += bool(_CLARIFICATION_FRICTION_RE.search(current_message))
    if active_clarification and clarification_friction >= 1:
        reasons.append("repeated_clarification_failure")

    confidences = _capability_confidences(capability_context or {})
    if confidences and min(confidences) < settings.ai_setup_low_capability_confidence:
        reasons.append("low_capability_confidence")
    if _unknown_terms(capability_context or {}):
        reasons.append("custom_terminology")
    if any(ord(character) > 127 for character in combined):
        reasons.append("multilingual_or_mixed_language")
    elif _looks_like_arabizi(combined):
        reasons.append("arabizi_or_transliterated_language")
    if _looks_like_technical_typo(combined):
        reasons.append("possible_typographical_error")

    unique_reasons = tuple(dict.fromkeys(reasons))
    complex_route = bool(unique_reasons)
    model = (
        settings.ai_setup_complex_model if complex_route else settings.ai_setup_simple_model
    ) or settings.openai_model
    effort = (
        settings.ai_setup_complex_reasoning_effort
        if complex_route
        else settings.ai_setup_simple_reasoning_effort
    ) or settings.openai_reasoning_effort
    return AISetupModelRoute(
        model=model,
        reasoning_effort=effort,
        tier="complex" if complex_route else "simple",
        reasons=unique_reasons or ("simple_clear_request",),
        condition_count=condition_count,
        correction_count=correction_count,
    )


def _condition_count(value: str) -> int:
    fragments = [
        fragment.strip()
        for fragment in re.split(r"\n+|[;,]+|\b(?:and|or|then|plus)\b", value, flags=re.I)
        if fragment.strip()
    ]
    technical = [
        fragment
        for fragment in fragments
        if re.search(
            r"\b(?:above|below|cross|sweep|break|retest|volume|rsi|macd|ema|sma|vwap|"
            r"candle|high|low|support|resistance|session|close|change|pattern|gap)\b",
            fragment,
            re.IGNORECASE,
        )
    ]
    return max(1, len(technical)) if value.strip() else 0


def _looks_contradictory(value: str) -> bool:
    return bool(
        re.search(r"\b(?:must|required)\b.{0,80}\b(?:must not|exclude|without)\b", value)
        or re.search(r"\b(?:above|over)\b.{0,40}\b(?:below|under)\b", value)
        and re.search(r"\b(?:same|simultaneously|at once)\b", value)
    )


def _capability_confidences(context: dict[str, Any]) -> list[float]:
    values: list[float] = []
    fragments = context.get("fragments")
    if isinstance(fragments, list):
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            selected = fragment.get("selection_confidence")
            candidates = fragment.get("candidates")
            top = (
                candidates[0].get("confidence")
                if candidates and isinstance(candidates[0], dict)
                else None
            )
            confidence = selected if selected is not None else top
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                values.append(float(confidence))
    else:
        candidates = context.get("candidates")
        if candidates and isinstance(candidates, list) and isinstance(candidates[0], dict):
            confidence = candidates[0].get("confidence")
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                values.append(float(confidence))
    return [value for value in values if 0 <= value <= 1]


def _looks_like_arabizi(value: str) -> bool:
    for token in re.findall(r"[a-z0-9]+", value.casefold()):
        if re.fullmatch(r"\d+(?:\.\d+)?(?:m|h|d|w|x)?", token):
            continue
        if re.search(r"[a-z]\d|\d[a-z]", token):
            return True
    return False


def _looks_like_technical_typo(value: str) -> bool:
    tokens = {
        token
        for token in re.findall(r"[a-z]{4,}", value.casefold())
        if token not in _ROUTING_TECHNICAL_TERMS
    }
    return any(
        abs(len(token) - len(term)) <= 2
        and SequenceMatcher(None, token, term).ratio() >= 0.78
        for token in tokens
        for term in _ROUTING_TECHNICAL_TERMS
    )


def _unknown_terms(context: dict[str, Any]) -> list[str]:
    terms: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            raw = item.get("unknown_terms")
            if isinstance(raw, list):
                terms.extend(str(value) for value in raw if str(value).strip())
            for value in item.values():
                visit(value)
        elif isinstance(item, list | tuple):
            for value in item:
                visit(value)

    visit(context)
    return terms
