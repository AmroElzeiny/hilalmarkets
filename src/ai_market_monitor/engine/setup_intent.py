from __future__ import annotations

import re
from dataclasses import dataclass

from ai_market_monitor.engine.turn_fragments import classify_turn
from ai_market_monitor.schemas.strategy_draft_v2 import SetupIntent


@dataclass(frozen=True, slots=True)
class IntentDecision:
    intent: SetupIntent
    confidence: float
    reason: str
    requires_structured_extraction: bool = False


_PRODUCT_RE = re.compile(
    r"\b(?:hilalmarkets|watchlists?|market scanner|halal market|passport|"
    r"methodolog(?:y|ies)|pricing|plan|telegram|notification|favorite|"
    r"how (?:does|do|can)|what (?:is|are|does))\b",
    re.IGNORECASE,
)
_EXPLANATION_RE = re.compile(
    r"^\s*(?:explain|why|what do you mean|make that simpler|show me why|"
    r"how did you|tell me more)\b",
    re.IGNORECASE,
)
_APPROVAL_ACTION_RE = re.compile(
    r"\b(?:approve|confirm|accept|proceed|go\s+ahead|mowafe2|waafe2|"
    r"\u0648\u0627\u0641\u0642|\u0645\u0648\u0627\u0641\u0642|"
    r"\u0627\u0639\u062a\u0645\u062f|\u0623\u0643\u062f)\b",
    re.IGNORECASE,
)
_APPROVAL_NEGATION_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|not|never)\s+(?:approve|confirm|proceed)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_RE = re.compile(
    r"\b(?:place|execute|open|close)\s+(?:a\s+)?(?:trade|order)|"
    r"\b(?:buy|sell)\s+(?:it|now|for me)|\bguarantee(?:d)?\s+(?:profit|return)",
    re.IGNORECASE,
)
_CONVERSATIONAL_META_RE = re.compile(
    r"^\s*(?:yeah|yes|no|okay|ok|thanks?|understood|fine|right|"
    r"not\s+(?:heavy|too\s+many|more)|no\s+more|let(?:'s| us)|"
    r"it\s+(?:means|ensures|keeps)|that\s+(?:means|ensures|keeps))\b",
    re.IGNORECASE,
)


def decide_setup_intent(text: str) -> IntentDecision:
    """Return one and only one setup-chat intent before any state mutation.

    The existing fragment reader is reused only as a lexical parser. It does not
    compile, resolve capabilities, or mutate state here.
    """

    cleaned = " ".join((text or "").split())
    report = classify_turn(cleaned)
    if _UNSUPPORTED_RE.search(cleaned):
        return IntentDecision(
            SetupIntent.UNSUPPORTED_REQUEST,
            1.0,
            "automatic trading or guaranteed-outcome request",
        )
    if _is_explicit_approval_action(cleaned, report):
        return IntentDecision(SetupIntent.APPROVAL_ACTION, 1.0, "explicit approval action")
    if _EXPLANATION_RE.search(cleaned) and not _has_strategy_mutation(report):
        return IntentDecision(
            SetupIntent.EXPLANATION_REQUEST,
            0.98,
            "explanation request without strategy mutation",
        )
    if _PRODUCT_RE.search(cleaned) and not _has_strategy_mutation(report):
        return IntentDecision(
            SetupIntent.PRODUCT_QUESTION,
            0.96,
            "product question without strategy mutation",
        )
    if report.is_conversational_only:
        return IntentDecision(SetupIntent.CONVERSATION, 1.0, "conversation-only turn")
    if _CONVERSATIONAL_META_RE.search(cleaned) and not _has_executable_fields(report):
        return IntentDecision(
            SetupIntent.CONVERSATION,
            0.98,
            "conversation or presentation preference without executable fields",
        )
    if _has_strategy_mutation(report):
        return IntentDecision(
            SetupIntent.STRATEGY_PATCH,
            0.99,
            "deterministic strategy fields or mechanic detected",
            requires_structured_extraction=True,
        )
    if cleaned.endswith(("?", "\u061f")):
        return IntentDecision(
            SetupIntent.PRODUCT_QUESTION,
            0.72,
            "non-trading question",
        )
    return IntentDecision(SetupIntent.CONVERSATION, 0.86, "no strategy mutation detected")


def _is_explicit_approval_action(text: str, report: object) -> bool:
    """Recognize a user action, not prose that merely discusses approval."""

    if (
        not text
        or len(text.split()) > 14
        or text.endswith(("?", "\u061f"))
        or _APPROVAL_NEGATION_RE.search(text)
        or _has_strategy_mutation(report)
    ):
        return False
    match = _APPROVAL_ACTION_RE.search(text)
    if match is None:
        return False
    prefix = text[: match.start()].strip(" ,.!:;-").casefold().split()
    return len(prefix) <= 3


def _has_strategy_mutation(report: object) -> bool:
    fragments = tuple(getattr(report, "fragments", ()))
    strategy_categories = {
        "SYMBOL",
        "INCLUDE",
        "EXCLUDE",
        "TIMEFRAME",
        "DIRECTION",
        "OPERATOR",
        "THRESHOLD",
        "FORMULA",
        "REVERSION",
        "TRADING_MECHANIC",
    }
    return any(getattr(item, "category", "") in strategy_categories for item in fragments)


def _has_executable_fields(report: object) -> bool:
    fragments = tuple(getattr(report, "fragments", ()))
    executable_categories = {
        "INCLUDE",
        "EXCLUDE",
        "TIMEFRAME",
        "DIRECTION",
        "OPERATOR",
        "THRESHOLD",
        "FORMULA",
        "REVERSION",
        "TRADING_MECHANIC",
    }
    return any(
        getattr(item, "category", "") in executable_categories for item in fragments
    )
