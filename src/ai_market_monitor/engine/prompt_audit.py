from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from ai_market_monitor.schemas.strategy import (
    ConditionGroup,
    ConditionRule,
    InterpretationIssue,
    StrategyDefinition,
)

FragmentBucket = Literal[
    "executable_condition",
    "optional_condition",
    "assumption",
    "ambiguity",
    "unsupported",
    "ignored_filler",
    "unclassified",
]


class PromptFragment(BaseModel):
    original: str
    normalized: str
    meaningful: bool


class PromptCoverageMapping(BaseModel):
    fragment: str
    bucket: FragmentBucket
    condition_id: str | None = None
    condition_label: str | None = None
    confidence: float | None = None
    reason: str


class PromptCoverageReport(BaseModel):
    original_prompt: str
    normalized_prompt: str
    prompt_fragments: list[PromptFragment]
    extracted_intents: list[str]
    executable_conditions: list[str]
    optional_conditions: list[str]
    unsupported_fragments: list[str]
    ignored_fragments: list[str]
    ambiguous_fragments: list[str]
    assumptions: list[str]
    confidence_score: float = Field(ge=0, le=100)
    activation_blocked: bool
    coverage_score: float = Field(ge=0, le=100)
    critical_missing_fields: list[str]
    warnings: list[str]
    mapping_table: list[PromptCoverageMapping]


_MEANINGFUL_HINTS = {
    "%",
    "$",
    "above",
    "below",
    "over",
    "under",
    "cross",
    "crosses",
    "near",
    "break",
    "breakout",
    "breakdown",
    "retest",
    "sweep",
    "liquidity",
    "rsi",
    "mfi",
    "obv",
    "cmf",
    "macd",
    "hma",
    "ema",
    "sma",
    "vwap",
    "bollinger",
    "volume",
    "doji",
    "hammer",
    "hummer",
    "engulfing",
    "green",
    "red",
    "bullish",
    "bearish",
    "candle",
    "candles",
    "stop",
    "target",
    "risk",
    "telegram",
    "discord",
    "session",
    "weekday",
    "weekdays",
    "weekend",
    "midnight",
    "btc",
    "eth",
    "ath",
    "high",
    "low",
    "pumped",
    "dropped",
    "gained",
    "lost",
    "head and shoulders",
    "head & shoulders",
    "head and sholders",
    "head & sholders",
    "neckline",
    "double top",
    "double bottom",
    "triangle",
    "chart pattern",
}

_FILLER_WORDS = {
    "find",
    "show",
    "bring",
    "me",
    "all",
    "symbols",
    "coins",
    "pairs",
    "usdt",
    "spot",
    "markets",
    "please",
    "halal",
    "sharia",
    "shariah",
    "screened",
    "eligible",
    "assets",
    "tokens",
}


def audit_prompt_coverage(
    original_prompt: str,
    strategy: StrategyDefinition,
    *,
    assumptions: list[str] | None = None,
    ambiguities: list[InterpretationIssue] | None = None,
    unsupported: list[InterpretationIssue] | None = None,
    ai_interpreted: bool = False,
) -> PromptCoverageReport:
    assumptions = assumptions or []
    ambiguities = ambiguities or []
    unsupported = unsupported or []
    fragments = split_prompt_fragments(original_prompt)
    conditions = _condition_leaves(strategy.conditions)
    attach_condition_sources(
        strategy,
        original_prompt,
        fragments=fragments,
        ai_interpreted=ai_interpreted,
    )
    mapping_table: list[PromptCoverageMapping] = []
    executable: list[str] = []
    optional: list[str] = []
    ambiguous: list[str] = []
    unsupported_fragments: list[str] = []
    ignored: list[str] = []
    meaningful_total = 0
    covered_total = 0

    for fragment in fragments:
        if not fragment.meaningful:
            ignored.append(fragment.original)
            mapping_table.append(
                PromptCoverageMapping(
                    fragment=fragment.original,
                    bucket="ignored_filler",
                    reason="Structural or routing text only.",
                )
            )
            continue
        meaningful_total += 1
        condition = _condition_for_fragment(fragment.normalized, conditions)
        if condition is not None:
            bucket: FragmentBucket = (
                "executable_condition" if condition.required else "optional_condition"
            )
            target = executable if condition.required else optional
            target.append(fragment.original)
            covered_total += 1
            mapping_table.append(
                PromptCoverageMapping(
                    fragment=fragment.original,
                    bucket=bucket,
                    condition_id=condition.key,
                    condition_label=condition.label,
                    confidence=condition.confidence,
                    reason="Mapped to deterministic condition.",
                )
            )
            continue
        issue = _issue_for_fragment(fragment.normalized, ambiguities)
        if issue is not None:
            ambiguous.append(fragment.original)
            covered_total += 1
            mapping_table.append(
                PromptCoverageMapping(
                    fragment=fragment.original,
                    bucket="ambiguity",
                    reason=issue.message,
                )
            )
            continue
        issue = _issue_for_fragment(fragment.normalized, unsupported)
        if issue is not None:
            unsupported_fragments.append(fragment.original)
            covered_total += 1
            mapping_table.append(
                PromptCoverageMapping(
                    fragment=fragment.original,
                    bucket="unsupported",
                    reason=issue.message,
                )
            )
            continue
        if _assumption_for_fragment(fragment.normalized, assumptions):
            covered_total += 1
            mapping_table.append(
                PromptCoverageMapping(
                    fragment=fragment.original,
                    bucket="assumption",
                    reason="Captured as an explicit assumption.",
                )
            )
            continue
        ignored.append(fragment.original)
        mapping_table.append(
            PromptCoverageMapping(
                fragment=fragment.original,
                bucket="unclassified",
                reason="Meaningful fragment was not represented in the strategy draft.",
            )
        )

    condition_confidences = [
        condition.confidence
        for condition in conditions
        if condition.key != "clarification_required" and condition.confidence is not None
    ]
    confidence_score = (
        sum(condition_confidences) / len(condition_confidences) * 100
        if condition_confidences
        else 0.0
    )
    coverage_score = 100.0 if meaningful_total == 0 else (covered_total / meaningful_total) * 100
    unclassified = [row.fragment for row in mapping_table if row.bucket == "unclassified"]
    critical_missing_fields: list[str] = []
    if not [condition for condition in conditions if condition.key != "clarification_required"]:
        critical_missing_fields.append("executable_condition")
    if unclassified:
        critical_missing_fields.append("prompt_fragment_classification")
    if any(issue.blocking for issue in unsupported):
        critical_missing_fields.append("unsupported_required_condition")
    if ambiguities:
        critical_missing_fields.append("ambiguity_resolution")
    warnings = [
        *(f"Unclassified fragment: {fragment}" for fragment in unclassified),
        *(
            f"Unsupported required fragment: {issue.source_fragment or issue.message}"
            for issue in unsupported
            if issue.blocking
        ),
    ]
    return PromptCoverageReport(
        original_prompt=original_prompt,
        normalized_prompt=_normalize(original_prompt),
        prompt_fragments=fragments,
        extracted_intents=_extract_intents(fragments),
        executable_conditions=executable,
        optional_conditions=optional,
        unsupported_fragments=unsupported_fragments,
        ignored_fragments=ignored,
        ambiguous_fragments=ambiguous,
        assumptions=assumptions,
        confidence_score=round(confidence_score, 2),
        activation_blocked=bool(critical_missing_fields),
        coverage_score=round(coverage_score, 2),
        critical_missing_fields=critical_missing_fields,
        warnings=warnings,
        mapping_table=mapping_table,
    )


def attach_condition_sources(
    strategy: StrategyDefinition,
    original_prompt: str,
    *,
    fragments: list[PromptFragment] | None = None,
    ai_interpreted: bool = False,
) -> None:
    fragments = fragments or split_prompt_fragments(original_prompt)
    meaningful = [fragment for fragment in fragments if fragment.meaningful]
    for condition in _condition_leaves(strategy.conditions):
        if condition.key == "clarification_required":
            condition.source_fragment = condition.source_fragment or original_prompt[:500]
            condition.confidence = condition.confidence or 0.1
            continue
        if not condition.source_fragment:
            best_fragment, score = _best_fragment_for_condition(condition, meaningful)
            condition.source_fragment = (
                best_fragment.original if best_fragment is not None else original_prompt
            )[:500]
            condition.confidence = condition.confidence or _confidence_from_score(score)
        else:
            condition.confidence = condition.confidence or 0.85
        condition.ai_interpreted = bool(condition.ai_interpreted or ai_interpreted)


def split_prompt_fragments(prompt: str) -> list[PromptFragment]:
    cleaned = re.sub(r"\r\n?", "\n", prompt or "")
    cleaned = re.sub(
        r"\b(?:goal|find|must include|filters|extra instructions|detail):",
        "\n",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"(?<=\d)\.(?=\d)", "__DECIMAL__", cleaned)
    parts = re.split(
        r"\n+|[.;]+|\s+\b(?:but|then|also|plus|except)\b\s+|,\s*|\s+\band\b\s+",
        cleaned,
        flags=re.I,
    )
    fragments: list[PromptFragment] = []
    for part in parts:
        original = " ".join(part.replace("__DECIMAL__", ".").strip(" -:\t").split())
        if not original:
            continue
        normalized = _normalize(original)
        fragments.append(
            PromptFragment(
                original=original,
                normalized=normalized,
                # Clarification answers are server-authored provenance records. They remain in
                # the compiler input so a selected timeframe/threshold can affect the draft,
                # but they are not fresh user instructions and must not be coverage-audited as
                # standalone market mechanics.
                meaningful=(
                    _is_meaningful(normalized)
                    and not _is_structured_clarification_answer(normalized)
                ),
            )
        )
    return fragments or [
        PromptFragment(
            original=prompt,
            normalized=_normalize(prompt),
            meaningful=bool(prompt.strip()),
        )
    ]


def _condition_leaves(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionRule):
        return [node]
    leaves: list[ConditionRule] = []
    for child in node.children:
        leaves.extend(_condition_leaves(child))
    return leaves


def _condition_for_fragment(
    normalized_fragment: str,
    conditions: list[ConditionRule],
) -> ConditionRule | None:
    for condition in conditions:
        if condition.source_fragment and _overlaps(
            normalized_fragment,
            _normalize(condition.source_fragment),
        ):
            return condition
    scored = sorted(
        (
            (_condition_fragment_score(condition, normalized_fragment), condition)
            for condition in conditions
            if condition.key != "clarification_required"
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored and scored[0][0] >= 3:
        return scored[0][1]
    return None


def _issue_for_fragment(
    normalized_fragment: str,
    issues: list[InterpretationIssue],
) -> InterpretationIssue | None:
    for issue in issues:
        source = _normalize(issue.source_fragment or "")
        message = _normalize(issue.message)
        if source and _overlaps(normalized_fragment, source):
            return issue
        if normalized_fragment and normalized_fragment in message:
            return issue
    return None


def _assumption_for_fragment(normalized_fragment: str, assumptions: list[str]) -> bool:
    return any(_overlaps(normalized_fragment, _normalize(assumption)) for assumption in assumptions)


def _best_fragment_for_condition(
    condition: ConditionRule,
    fragments: list[PromptFragment],
) -> tuple[PromptFragment | None, int]:
    best: tuple[PromptFragment | None, int] = (None, 0)
    for fragment in fragments:
        score = _condition_fragment_score(condition, fragment.normalized)
        if score > best[1]:
            best = (fragment, score)
    return best


def _condition_fragment_score(condition: ConditionRule, fragment: str) -> int:
    score = 0
    tokens = _condition_tokens(condition)
    for token in tokens:
        if token and token in fragment:
            score += 2 if len(token) > 2 else 1
    if condition.timeframe in fragment:
        score += 2
    if condition.right is not None and condition.right.value is not None:
        value = str(condition.right.value).rstrip("0").rstrip(".")
        if value and value in fragment:
            score += 2
    if condition.comparator.value in {"gt", "gte"} and any(
        word in fragment for word in ("above", "over", "greater", "more")
    ):
        score += 2
    if condition.comparator.value in {"lt", "lte"} and any(
        word in fragment for word in ("below", "under", "less")
    ):
        score += 2
    if condition.comparator.value == "is_false" and any(
        word in fragment for word in ("not", "no", "without", "avoid")
    ):
        score += 2
    return score


def _condition_tokens(condition: ConditionRule) -> set[str]:
    values = {
        condition.key.replace("_", " "),
        condition.label.casefold(),
        str(condition.left.name or ""),
        str(condition.left.field or ""),
        str(condition.condition_type.value),
    }
    values.update(
        str(value).casefold().replace("_", " ") for value in condition.left.parameters.values()
    )
    if condition.right is not None:
        values.add(str(condition.right.name or ""))
        values.add(str(condition.right.field or ""))
    alias_tokens = {
        "money_flow_index": {"mfi", "money", "flow"},
        "hull_moving_average": {"hma", "hull"},
        "weighted_moving_average": {"wma", "weighted"},
        "volume_weighted_moving_average": {"vwma"},
        "kaufman_adaptive_moving_average": {"kama", "kaufman"},
        "double_exponential_moving_average": {"dema"},
        "triple_exponential_moving_average": {"tema"},
    }
    tokens: set[str] = set()
    for value in values:
        tokens.update(token for token in re.split(r"[^a-z0-9%$]+", value.casefold()) if token)
        tokens.update(alias_tokens.get(value.casefold(), set()))
    return tokens


def _confidence_from_score(score: int) -> float:
    if score >= 8:
        return 0.92
    if score >= 5:
        return 0.82
    if score >= 3:
        return 0.68
    if score >= 1:
        return 0.52
    return 0.35


def _extract_intents(fragments: list[PromptFragment]) -> list[str]:
    intents: set[str] = set()
    for fragment in fragments:
        text = fragment.normalized
        if "%" in text or any(word in text for word in ("pumped", "dropped", "gained", "lost")):
            intents.add("percent_move")
        if any(word in text for word in ("rsi", "mfi", "macd", "stochastic", "adx")):
            intents.add("momentum")
        if any(word in text for word in ("ema", "sma", "hma", "vwap", "moving average")):
            intents.add("trend")
        if any(word in text for word in ("volume", "liquidity", "spread")):
            intents.add("volume_liquidity")
        if any(word in text for word in ("candle", "doji", "hammer", "engulfing")):
            intents.add("candle_pattern")
        if any(
            phrase in text
            for phrase in (
                "head and shoulders",
                "head & shoulders",
                "head and sholders",
                "head & sholders",
                "neckline",
                "double top",
                "double bottom",
                "triangle",
                "chart pattern",
            )
        ):
            intents.add("technical_pattern")
        if any(word in text for word in ("session", "midnight", "new york", "london")):
            intents.add("session_timing")
        if any(word in text for word in ("btc", "eth/btc", "alts outperforming")):
            intents.add("cross_symbol_context")
    return sorted(intents)


def _is_meaningful(normalized: str) -> bool:
    if not normalized:
        return False
    routing_context = re.sub(
        r"\b(?:alert|alerts|alerted|notify|notification|notifications|me|on|the|a|an|"
        r"chart|timeframe|time frame|at|using|use|trigger)\b",
        " ",
        normalized,
    )
    routing_tokens = set(routing_context.split())
    if routing_tokens and routing_tokens.issubset(
        {
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
            "close",
            "intrabar",
        }
    ):
        return False
    tokens = set(normalized.split())
    if tokens and tokens.issubset(_FILLER_WORDS):
        return False
    return any(hint in normalized for hint in _MEANINGFUL_HINTS) or bool(
        re.search(r"\d", normalized)
    )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def _is_structured_clarification_answer(value: str) -> bool:
    return bool(
        re.match(
            r"^clarification answer for [a-z0-9_]+\s*:",
            value,
            flags=re.IGNORECASE,
        )
    )


def _overlaps(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    left_tokens = {token for token in re.split(r"[^a-z0-9%$]+", left) if len(token) > 2}
    right_tokens = {token for token in re.split(r"[^a-z0-9%$]+", right) if len(token) > 2}
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens))) >= 0.5
