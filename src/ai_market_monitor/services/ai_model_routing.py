from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.setup_agent import (
    CapabilityRoutingCandidate,
    CapabilityRoutingContext,
)

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
#: Words that make a fragment a market statement rather than conversation. Shared by
#: the condition counter and the mixed-turn test so the two cannot disagree.
_TECHNICAL_FRAGMENT_RE = re.compile(
    r"\b(?:above|below|cross|sweep|break|retest|volume|rsi|macd|ema|sma|vwap|"
    r"candle|high|low|support|resistance|session|close|change|pattern|gap)\b",
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

# Provider model aliases do not necessarily support every value accepted by the
# Responses API schema. Luna rejects ``minimal``; ``low`` is its first supported
# reasoning level after ``none``. Keep this guard at the routing boundary so a stale
# deployment override cannot turn an ordinary chat message into an HTTP 400.
_MODEL_REASONING_EFFORT_OVERRIDES = {
    ("gpt-5.6-luna", "minimal"): "low",
}


@dataclass(frozen=True, slots=True)
class AISetupModelRoute:
    model: str
    reasoning_effort: str
    service_tier: str
    tier: str
    reasons: tuple[str, ...]
    condition_count: int
    correction_count: int

    def usage_metadata(self) -> dict[str, Any]:
        return {
            "_traceedge_model": self.model,
            "_traceedge_reasoning_effort": self.reasoning_effort,
            "_setup_requested_service_tier": self.service_tier,
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
    capability_context: CapabilityRoutingContext | dict[str, Any] | None = None,
    draft_condition_count: int = 0,
    unresolved_field_count: int = 0,
    previous_turn_failed: bool = False,
) -> AISetupModelRoute:
    """Select a configured model tier without granting it any additional authority.

    ``draft_condition_count``, ``unresolved_field_count`` and ``previous_turn_failed``
    describe the *conversation*, not the sentence. Reading only the current message
    routed a hard turn to the cheap tier: "make that stricter" is four words, and
    answering it needs the draft, the open question and the turn that just failed.
    """

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
    elif condition_count > 1:
        reasons.append("multiple_conditions")
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
    elif correction_count:
        reasons.append("existing_condition_correction")

    clarification_friction = sum(
        bool(_CLARIFICATION_FRICTION_RE.search(item)) for item in user_turns[-8:]
    )
    clarification_friction += bool(_CLARIFICATION_FRICTION_RE.search(current_message))
    if active_clarification and clarification_friction >= 1:
        reasons.append("repeated_clarification_failure")

    routing_context = _normalize_routing_context(capability_context)
    confidences = _capability_confidences(routing_context)
    if confidences and min(confidences) < settings.ai_setup_low_capability_confidence:
        reasons.append("low_capability_confidence")
    if routing_context.unknown_terms:
        reasons.append("custom_terminology")
    if any(ord(character) > 127 for character in combined):
        reasons.append("multilingual_or_mixed_language")
    elif _looks_like_arabizi(combined):
        reasons.append("arabizi_or_transliterated_language")
    if _looks_like_technical_typo(combined):
        reasons.append("possible_typographical_error")

    # Signals that live in the conversation rather than in this sentence. A short
    # message can be the hardest turn in the session.
    if draft_condition_count >= settings.ai_setup_complex_condition_threshold:
        reasons.append("complex_existing_draft")
    if _refers_to_earlier_turn(current_message):
        reasons.append("reference_to_previous_turn")
    if re.search(r"\b(?:undo|revert|restore|go back|return to)\b", current_message, re.I):
        reasons.append("snapshot_restoration")
    if active_clarification and current_message.strip():
        reasons.append("active_clarification_answer")
    elif unresolved_field_count > 0 and _short_answer(current_message):
        # `yes` against an open question needs the question to make any sense.
        reasons.append("answering_open_question")
    if previous_turn_failed:
        reasons.append("previous_interpretation_failed")
    if _sounds_like_objection(current_message):
        reasons.append("user_objection")
    if _mixed_segment_count(current_message) >= 2:
        reasons.append("mixed_conversation_and_instruction")

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
    from ai_market_monitor.services.ai_setup_evaluator_control import (
        current_evaluator_target,
    )

    evaluator_target = current_evaluator_target()
    if evaluator_target is not None:
        model = evaluator_target.model
        effort = evaluator_target.reasoning_effort
        unique_reasons = (*unique_reasons, "evaluator_target_version")
    requested_effort = effort
    effort = _MODEL_REASONING_EFFORT_OVERRIDES.get(
        (model.casefold(), effort.casefold()),
        effort,
    )
    if effort != requested_effort:
        unique_reasons = (*unique_reasons, "model_reasoning_effort_normalized")
    configured_service_tier = (
        settings.setup_agent_complex_service_tier
        if complex_route
        else settings.setup_agent_simple_service_tier
    )
    service_tier = configured_service_tier
    if (
        configured_service_tier == "fast"
        and model not in settings.openai_fast_model_pricing_usd_per_million
    ):
        # Fast availability is model-specific. A test/canary model that has no
        # configured Fast price remains executable on Standard without changing
        # the semantic plan or substituting another model.
        service_tier = "default"
        unique_reasons = (*unique_reasons, "fast_service_tier_unavailable_for_model")
    return AISetupModelRoute(
        model=model,
        reasoning_effort=effort,
        service_tier=service_tier,
        tier="complex" if complex_route else "simple",
        reasons=unique_reasons or ("simple_clear_request",),
        condition_count=condition_count,
        correction_count=correction_count,
    )


#: Wording that points at something said earlier rather than naming it again. These
#: turns cannot be understood from the sentence alone.
_BACK_REFERENCE_RE = re.compile(
    r"\b(?:that one|the other one|the (?:first|second|third|last|previous) one|"
    r"the one (?:we|you|i) (?:just|already)|make (?:that|it|this)\b|"
    r"keep (?:that|it|the first|the second)\b|remove (?:that|it|the one)\b|"
    r"same as (?:before|above)|like (?:before|earlier)|as (?:we|you) said|"
    r"revert|undo|go back)\b",
    re.IGNORECASE,
)

#: A short reply that only means something against an open question.
_SHORT_ANSWER_RE = re.compile(
    r"^\s*(?:yes|no|yeah|yep|nope|sure|ok|okay|correct|right|wrong|both|neither|"
    r"the (?:first|second|third)|option\s*[a-c1-3]|[a-c1-3])\b[\s.!,]*$",
    re.IGNORECASE,
)

#: The user telling us we got it wrong. These turns need the better model most.
_OBJECTION_RE = re.compile(
    r"\b(?:that(?:'s| is) (?:not|wrong|incorrect)|not what i (?:said|meant|asked)|"
    r"you (?:changed|removed|added|ignored|misunderstood)|why did you|"
    r"i never (?:said|asked)|stop (?:asking|changing)|this is wrong|"
    r"you keep\b|i already (?:said|told))\b",
    re.IGNORECASE,
)

#: Conversational openers and closers that sit around an instruction. Their presence
#: alongside technical wording is what makes a turn mixed.
_SOCIAL_RE = re.compile(
    r"\b(?:hi|hey|hello|thanks|thank you|please|good (?:morning|evening)|"
    r"sorry|cheers|got it|understood|makes sense|nice|great|cool)\b",
    re.IGNORECASE,
)


def _refers_to_earlier_turn(value: str) -> bool:
    return bool(_BACK_REFERENCE_RE.search(value or ""))


def _short_answer(value: str) -> bool:
    return bool(_SHORT_ANSWER_RE.match(value or ""))


def _sounds_like_objection(value: str) -> bool:
    return bool(_OBJECTION_RE.search(value or ""))


def _mixed_segment_count(value: str) -> int:
    """How many different things one message is doing at once.

    Only a routing hint. The model decides the real segmentation, and the server
    checks it; this just stops a mixed turn from being priced as a simple one.
    """
    text = value or ""
    kinds = 0
    if _SOCIAL_RE.search(text):
        kinds += 1
    if "?" in text or "؟" in text:
        kinds += 1
    # `_condition_count` reports at least one for any non-empty string, so it cannot
    # answer "is there technical content here". The vocabulary test can.
    if _TECHNICAL_FRAGMENT_RE.search(text):
        kinds += 1
    if _BACK_REFERENCE_RE.search(text) or _CORRECTION_RE.search(text.strip()):
        kinds += 1
    return kinds


def _condition_count(value: str) -> int:
    fragments = [
        fragment.strip()
        for fragment in re.split(r"\n+|[;,]+|\b(?:and|or|then|plus)\b", value, flags=re.I)
        if fragment.strip()
    ]
    technical = [
        fragment for fragment in fragments if _TECHNICAL_FRAGMENT_RE.search(fragment)
    ]
    return max(1, len(technical)) if value.strip() else 0


def _looks_contradictory(value: str) -> bool:
    return bool(
        re.search(r"\b(?:must|required)\b.{0,80}\b(?:must not|exclude|without)\b", value)
        or re.search(r"\b(?:above|over)\b.{0,40}\b(?:below|under)\b", value)
        and re.search(r"\b(?:same|simultaneously|at once)\b", value)
    )


def _normalize_routing_context(
    value: CapabilityRoutingContext | dict[str, Any] | None,
) -> CapabilityRoutingContext:
    if isinstance(value, CapabilityRoutingContext):
        return value
    if not value:
        return CapabilityRoutingContext(candidate_count=0)
    # Read compatibility for older non-Setup callers. The typed model is authoritative
    # after this boundary.
    candidates = value.get("candidates") or []
    normalized_candidates = [
        CapabilityRoutingCandidate(
            key=str(item.get("key") or f"candidate_{index}"),
            score=float(item.get("score") or 0),
            normalized_confidence=float(
                item.get("normalized_confidence")
                or item.get("confidence")
                or 0
            ),
            availability=str(item.get("availability") or "unknown"),
            executable=bool(item.get("executable", False)),
            source_fragment=str(
                item.get("source_fragment") or item.get("fragment") or ""
            ),
        )
        for index, item in enumerate(candidates)
        if isinstance(item, dict)
    ]
    return CapabilityRoutingContext(
        candidate_count=len(normalized_candidates),
        candidate_keys=[item.key for item in normalized_candidates],
        top_candidate_key=(
            normalized_candidates[0].key if normalized_candidates else None
        ),
        top_candidate_score=float(value.get("top_candidate_score") or value.get("top_score") or 0),
        selection_confidence=float(value.get("selection_confidence") or 0),
        top_score_margin=float(value.get("top_score_margin") or 0),
        unknown_terms=[str(item) for item in value.get("unknown_terms") or []],
        candidates=normalized_candidates,
    )


def _capability_confidences(context: CapabilityRoutingContext) -> list[float]:
    values = [item.normalized_confidence for item in context.candidates]
    if context.selection_confidence:
        values.append(context.selection_confidence)
    return values


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
