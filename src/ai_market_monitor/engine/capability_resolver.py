from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

from ai_market_monitor.engine.builder_templates import condition_template
from ai_market_monitor.engine.capabilities import CapabilitySpec, all_capabilities
from ai_market_monitor.engine.capability_compatibility import (
    CapabilityCompatibility,
    compatibility_by_key,
)
from ai_market_monitor.engine.prompt_aliases import normalized_phrases
from ai_market_monitor.engine.prompt_audit import split_prompt_fragments
from ai_market_monitor.engine.turn_fragments import classify_fragment
from ai_market_monitor.schemas.strategy import ConditionGroup, ConditionRule, StrategyDefinition

ResolutionStatus = Literal["matched", "ambiguous", "unknown"]

_COMMON_PROMPT_WORDS = {
    "a",
    "all",
    "and",
    "any",
    "at",
    "be",
    "bring",
    "binance",
    "bybit",
    "coin",
    "coins",
    "condition",
    "conditions",
    "crypto",
    "find",
    "five",
    "for",
    "four",
    "from",
    "had",
    "has",
    "have",
    "in",
    "is",
    "it",
    "its",
    "last",
    "least",
    "hour",
    "hours",
    "market",
    "markets",
    "me",
    "monitor",
    "of",
    "on",
    "only",
    "or",
    "pair",
    "pairs",
    "price",
    "row",
    "scan",
    "setup",
    "scanner",
    "seven",
    "six",
    "spot",
    "symbol",
    "symbols",
    "than",
    "ten",
    "that",
    "the",
    "this",
    "today",
    "through",
    "three",
    "to",
    "during",
    "usdc",
    "usdt",
    "watch",
    "which",
    "with",
    "check",
    "current",
    "definition",
    "do",
    "i",
    "mean",
    "meaning",
    "no",
    "previous",
    "prior",
    "whether",
    "where",
    "want",
    "week",
    "weekly",
    "one",
    "two",
    "eight",
    "nine",
}
_KNOWN_MARKET_ACRONYMS = {
    "ADX",
    "ATR",
    "BTC",
    "CCI",
    "EMA",
    "ETH",
    "FVG",
    "HTF",
    "MACD",
    "MFI",
    "OBV",
    "PDH",
    "PDL",
    "PO3",
    "ROC",
    "RSI",
    "RVOL",
    "SMA",
    "UTC",
    "VWAP",
}


@dataclass(frozen=True, slots=True)
class CapabilityCandidate:
    capability_key: str
    label: str
    score: float
    confidence: float
    availability: str
    matched_on: tuple[str, ...]
    source_fragment: str
    semantic_tags: tuple[str, ...]
    parameter_schema: dict[str, Any]
    direction_support: tuple[str, ...]
    temporal_behavior: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FragmentResolution:
    fragment: str
    status: ResolutionStatus
    candidates: tuple[CapabilityCandidate, ...]
    unknown_terms: tuple[str, ...] = ()
    clarification_question: str | None = None
    selected_capability_key: str | None = None
    selected_parameters: dict[str, Any] | None = None
    selection_confidence: float | None = None
    selection_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fragment": self.fragment,
            "status": self.status,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "unknown_terms": list(self.unknown_terms),
            "clarification_question": self.clarification_question,
            "selected_capability_key": self.selected_capability_key,
            "selected_parameters": self.selected_parameters or {},
            "selection_confidence": self.selection_confidence,
            "selection_source": self.selection_source,
        }


@dataclass(frozen=True, slots=True)
class CapabilityResolutionReport:
    prompt: str
    fragments: tuple[FragmentResolution, ...]

    @property
    def candidate_keys(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                candidate.capability_key
                for fragment in self.fragments
                for candidate in fragment.candidates
            )
        )

    @property
    def needs_clarification(self) -> bool:
        return any(fragment.status != "matched" for fragment in self.fragments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "candidate_keys": list(self.candidate_keys),
            "needs_clarification": self.needs_clarification,
            "fragments": [fragment.to_dict() for fragment in self.fragments],
        }

    def ai_context(self) -> dict[str, Any]:
        return {
            "candidate_keys": list(self.candidate_keys),
            "fragments": [
                {
                    "fragment": fragment.fragment,
                    "status": fragment.status,
                    "unknown_terms": list(fragment.unknown_terms),
                    "clarification_question": fragment.clarification_question,
                    "selected_capability_key": fragment.selected_capability_key,
                    "selected_parameters": fragment.selected_parameters or {},
                    "selection_confidence": fragment.selection_confidence,
                    "selection_source": fragment.selection_source,
                    "candidates": [
                        {
                            "capability_key": candidate.capability_key,
                            "label": candidate.label,
                            "confidence": candidate.confidence,
                            "availability": candidate.availability,
                            "semantic_tags": list(candidate.semantic_tags),
                            "parameter_schema": candidate.parameter_schema,
                            "direction_support": list(candidate.direction_support),
                            "temporal_behavior": candidate.temporal_behavior,
                        }
                        for candidate in fragment.candidates
                    ],
                }
                for fragment in self.fragments
            ],
        }


class CapabilityResolver:
    """Retrieves capability candidates without making market decisions."""

    def __init__(
        self,
        capabilities: tuple[CapabilitySpec, ...] | None = None,
        *,
        approved_aliases: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._capabilities = capabilities or all_capabilities()
        self._by_key = {capability.key: capability for capability in self._capabilities}
        self._compatibility = compatibility_by_key()
        approved_aliases = approved_aliases or {}
        self._phrases_by_key = {
            capability.key: tuple(
                (phrase, _normalize(phrase))
                for phrase in (
                    *normalized_phrases(capability),
                    *approved_aliases.get(capability.key, ()),
                )
                if _normalize(phrase)
            )
            for capability in self._capabilities
        }
        self._known_tokens = {
            token
            for capability in self._capabilities
            for phrase in (
                *normalized_phrases(capability),
                *approved_aliases.get(capability.key, ()),
                *capability.semantic_tags,
                *capability.intent_examples,
            )
            for token in _tokens(phrase)
        }

    def resolve_prompt(
        self, prompt: str, *, limit_per_fragment: int = 8
    ) -> CapabilityResolutionReport:
        fragments = split_prompt_fragments(prompt)
        resolutions: list[FragmentResolution] = []
        for fragment in fragments:
            parts = self._compound_parts(fragment.original, limit=limit_per_fragment)
            for part in parts:
                if _is_structured_clarification_answer(part):
                    continue
                # Symbols, exclusions, timeframes, directions, operators, thresholds,
                # approval instructions and conversation carry no market mechanic. Asking
                # the registry to identify one produces "unknown capability" questions for
                # terms the platform already parses deterministically.
                if not classify_fragment(part).enters_capability_resolution:
                    continue
                resolution = self.resolve_fragment(part, limit=limit_per_fragment)
                if not resolution.candidates and _is_context_only(part):
                    continue
                resolutions.append(resolution)
        return CapabilityResolutionReport(prompt=prompt, fragments=tuple(resolutions))

    def _compound_parts(self, fragment: str, *, limit: int) -> tuple[str, ...]:
        parts = tuple(
            part.strip()
            for part in re.split(r"\s+with\s+", fragment, flags=re.IGNORECASE)
            if part.strip()
        )
        if len(parts) < 2:
            return (fragment,)
        resolutions = [self.resolve_fragment(part, limit=limit) for part in parts]
        if sum(bool(item.candidates) for item in resolutions) < 2:
            return (fragment,)
        return parts

    def resolve_fragment(self, fragment: str, *, limit: int = 5) -> FragmentResolution:
        semantic_fragment = _semantic_fragment(fragment)
        normalized = _normalize(semantic_fragment)
        scored: list[CapabilityCandidate] = []
        for capability in self._capabilities:
            candidate = self._score(capability, semantic_fragment, normalized)
            if candidate is not None:
                scored.append(candidate)
        candidates = tuple(sorted(scored, key=lambda item: (-item.score, item.label))[:limit])
        unknown_terms = self._unknown_terms(
            semantic_fragment,
            top_score=candidates[0].score if candidates else None,
        )
        if not candidates:
            question = (
                f"What do you mean by '{unknown_terms[0]}' in this setup?"
                if unknown_terms
                else f"How should HilalMarkets measure '{fragment.strip()}'?"
            )
            return FragmentResolution(
                fragment=fragment,
                status="unknown",
                candidates=(),
                unknown_terms=unknown_terms,
                clarification_question=question,
            )
        top = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        ambiguous = (
            bool(unknown_terms)
            or top.score < 100
            or not _candidate_contract_is_exact(top, semantic_fragment)
            or (second is not None and second.score >= 100 and top.score - second.score < 9)
        )
        if ambiguous:
            labels = " or ".join(candidate.label for candidate in candidates[:3])
            question = (
                f"What does '{unknown_terms[0]}' mean in '{fragment.strip()}'?"
                if unknown_terms
                else f"Does '{fragment.strip()}' mean {labels}?"
            )
            return FragmentResolution(
                fragment=fragment,
                status="ambiguous",
                candidates=candidates,
                unknown_terms=unknown_terms,
                clarification_question=question,
            )
        return FragmentResolution(
            fragment=fragment,
            status="matched",
            candidates=candidates,
            unknown_terms=unknown_terms,
        )

    def broad_candidates(
        self, fragment: str, *, limit: int = 12
    ) -> tuple[CapabilityCandidate, ...]:
        """Recall-oriented retrieval for the AI reranker; it never makes a final selection."""
        semantic_fragment = _semantic_fragment(fragment)
        normalized = _normalize(semantic_fragment)
        normal = self.resolve_fragment(fragment, limit=limit).candidates
        by_key = {candidate.capability_key: candidate for candidate in normal}
        for capability in self._capabilities:
            if capability.key in by_key:
                continue
            ratios = [
                max(
                    SequenceMatcher(None, normalized, normalized_phrase).ratio(),
                    _token_overlap(normalized, normalized_phrase),
                )
                for _phrase, normalized_phrase in self._phrases_by_key[capability.key]
            ]
            best = max(ratios, default=0.0)
            tag_overlap = len(set(_tokens(normalized)).intersection(capability.semantic_tags))
            if best < 0.18 and not tag_overlap:
                continue
            score = min(70.0, best * 62 + tag_overlap * 8)
            compatibility = self._compatibility[capability.key]
            by_key[capability.key] = CapabilityCandidate(
                capability_key=capability.key,
                label=capability.label,
                score=round(score, 2),
                confidence=round(min(0.65, score / 100), 3),
                availability=compatibility.availability,
                matched_on=("broad_retrieval",),
                source_fragment=fragment,
                semantic_tags=capability.semantic_tags,
                parameter_schema=capability.parameter_schema,
                direction_support=capability.direction_support,
                temporal_behavior=capability.temporal_behavior,
            )
        return tuple(sorted(by_key.values(), key=lambda item: (-item.score, item.label))[:limit])

    def get(self, capability_key: str) -> CapabilitySpec:
        try:
            return self._by_key[capability_key]
        except KeyError as exc:
            raise ValueError(f"Unknown capability_key: {capability_key}") from exc

    def compatibility(self, capability_key: str) -> CapabilityCompatibility:
        self.get(capability_key)
        return self._compatibility[capability_key]

    def validate_selection(
        self,
        *,
        capability_key: str,
        parameters: dict[str, Any] | None,
        timeframe: str,
        required: bool,
        source_fragment: str,
        condition_key: str | None = None,
        comparator: str | None = None,
        confidence: float | None = None,
    ) -> ConditionRule:
        capability = self.get(capability_key)
        compatibility = self._compatibility[capability.key]
        if compatibility.availability != "available":
            raise ValueError(
                f"Capability {capability_key} is {compatibility.availability} and cannot execute"
            )
        if timeframe not in capability.supported_timeframes:
            raise ValueError(f"Capability {capability_key} does not support timeframe {timeframe}")
        selected = dict(parameters or {})
        schema = capability.parameter_schema or {}
        properties = dict(schema.get("properties") or {})
        unknown = sorted(set(selected) - set(properties) - {"comparator", "threshold"})
        if unknown:
            raise ValueError(
                f"Capability {capability_key} received unknown parameters: {', '.join(unknown)}"
            )
        missing = sorted(name for name in schema.get("required") or [] if name not in selected)
        if missing:
            raise ValueError(
                f"Capability {capability_key} requires parameters: {', '.join(missing)}"
            )
        for name, value in selected.items():
            if name in {"comparator", "threshold"}:
                continue
            _validate_parameter(name, value, properties.get(name) or {})
        payload = condition_template(
            capability,
            timeframe=timeframe,
            key=condition_key or capability.key,
        )
        payload["capability_key"] = capability.key
        payload["capability_version"] = capability.capability_version
        payload["required"] = required
        payload["source_fragment"] = source_fragment[:500]
        payload["confidence"] = confidence if confidence is not None else 0.9
        payload["ai_interpreted"] = True

        selected_comparator = comparator or selected.pop("comparator", None)
        if selected_comparator:
            allowed_comparators = {
                *capability.supported_comparators,
                capability.default_comparator,
            }
            if capability.default_comparator == "is_true":
                allowed_comparators.add("is_false")
            if selected_comparator not in allowed_comparators:
                raise ValueError(
                    f"Capability {capability_key} does not support comparator {selected_comparator}"
                )
            payload["comparator"] = selected_comparator
        threshold = selected.pop("threshold", None)
        if threshold is not None:
            if (payload.get("right") or {}).get("kind") != "constant":
                raise ValueError(f"Capability {capability_key} does not accept a numeric threshold")
            payload["right"]["value"] = threshold
        for name, value in selected.items():
            if name == "timeframe":
                continue
            target = _parameter_target(payload, name)
            target.setdefault("parameters", {})[name] = value
        payload["resolved_parameters"] = dict(parameters or {})
        if capability.key == "percent_change_lookback":
            direction = str(payload["left"]["parameters"].get("direction", "up"))
            payload["left"]["name"] = (
                "percent_change_down" if direction == "down" else "percent_change_up"
            )
        return ConditionRule.model_validate(payload)

    def canonicalize_ai_strategy(self, definition: StrategyDefinition) -> StrategyDefinition:
        def walk(node: ConditionRule | ConditionGroup) -> ConditionRule | ConditionGroup:
            if isinstance(node, ConditionGroup):
                return node.model_copy(
                    update={"children": [walk(child) for child in node.children]}
                )
            if not node.capability_key:
                raise ValueError(f"AI condition {node.key} is missing immutable capability_key")
            parameters = _selection_parameters(node)
            return self.validate_selection(
                capability_key=node.capability_key,
                parameters=parameters,
                timeframe=node.timeframe,
                required=node.required,
                source_fragment=node.source_fragment or node.label,
                condition_key=node.key,
                comparator=node.comparator.value,
                confidence=node.confidence,
            )

        return definition.model_copy(update={"conditions": walk(definition.conditions)})

    def bind_known_condition(self, condition: ConditionRule) -> ConditionRule:
        if condition.capability_key:
            return condition
        capability = self._by_key.get(condition.key)
        if capability is None:
            return condition
        return condition.model_copy(update={"capability_key": capability.key})

    def _score(
        self,
        capability: CapabilitySpec,
        fragment: str,
        normalized: str,
    ) -> CapabilityCandidate | None:
        matched_on: list[str] = []
        score = 0.0
        for negative in capability.negative_examples:
            if _normalize(negative) == normalized:
                return None
        for phrase, normalized_phrase in self._phrases_by_key[capability.key]:
            if normalized == normalized_phrase:
                score = max(score, 140)
                matched_on.append(f"exact:{phrase}")
            elif _contains_phrase(normalized, normalized_phrase):
                phrase_score = 118 + min(18, len(_tokens(normalized_phrase)) * 4)
                score = max(score, phrase_score)
                matched_on.append(f"alias:{phrase}")
            elif _contains_tokens_in_order(normalized, normalized_phrase):
                phrase_score = 100 + min(18, len(_tokens(normalized_phrase)) * 3)
                score = max(score, phrase_score)
                matched_on.append(f"ordered_alias:{phrase}")
            else:
                overlap = _token_overlap(normalized, normalized_phrase)
                if overlap >= 0.45:
                    score = max(score, 46 + overlap * 28)
                    matched_on.append(f"token:{phrase}")
        for example in capability.intent_examples:
            overlap = _token_overlap(normalized, _normalize(example))
            if overlap >= 0.45:
                score = max(score, 38 + overlap * 38)
                matched_on.append("intent_example")
        fragment_tokens = set(_tokens(normalized))
        tag_matches = sorted(fragment_tokens.intersection(capability.semantic_tags))
        if tag_matches:
            score = max(score, 34 + len(tag_matches) * 9)
            matched_on.extend(f"tag:{tag}" for tag in tag_matches)
        if (
            capability.key == "volume_ratio"
            and "volume" in fragment_tokens
            and "average" in fragment_tokens
            and re.search(r"\b\d+(?:\.\d+)?\s*x\b|\btimes?\b", fragment, re.IGNORECASE)
        ):
            score = max(score, 132)
            matched_on.append("semantic:volume_ratio")
        if (
            capability.key == "percent_change_lookback"
            and "%" in fragment
            and any(
                term in fragment_tokens
                for term in ("up", "down", "gain", "gained", "increase", "decrease", "drop")
            )
        ):
            score = max(score, 132)
            matched_on.append("semantic:percent_change")
        if score < 45:
            return None
        compatibility = self._compatibility[capability.key]
        return CapabilityCandidate(
            capability_key=capability.key,
            label=capability.label,
            score=round(score, 2),
            confidence=round(min(0.99, score / 140), 3),
            availability=compatibility.availability,
            matched_on=tuple(dict.fromkeys(matched_on)),
            source_fragment=fragment,
            semantic_tags=capability.semantic_tags,
            parameter_schema=capability.parameter_schema,
            direction_support=capability.direction_support,
            temporal_behavior=capability.temporal_behavior,
        )

    def _unknown_terms(
        self,
        fragment: str,
        *,
        top_score: float | None,
    ) -> tuple[str, ...]:
        symbol_tokens = {
            symbol
            for symbol, _quote in re.findall(
                r"\b([A-Z0-9]{2,12})[/\-](USDT|USDC)\b",
                fragment,
            )
        }
        acronyms = [
            token
            for token in re.findall(r"\b[A-Z][A-Z0-9]{1,7}\b", fragment)
            if token not in _KNOWN_MARKET_ACRONYMS
            and token not in symbol_tokens
            and token.casefold() not in self._known_tokens
        ]
        if acronyms:
            return tuple(dict.fromkeys(acronyms))
        # A missing dictionary token is not evidence that a trader used an unknown
        # mechanic. Ordinary prose is open-ended. Only inspect an explicitly additive
        # clause when a known mechanic already matched; otherwise the clarification
        # refers to the complete technical phrase instead of interrogating one word.
        if top_score is None:
            return ()
        additive_parts = re.split(
            r"\b(?:with|plus|along\s+with|as\s+well\s+as)\b",
            fragment,
            flags=re.IGNORECASE,
        )[1:]
        if not additive_parts:
            return ()
        words = [
            token
            for token in _tokens(" ".join(additive_parts))
            if token not in self._known_tokens
            and token not in _COMMON_PROMPT_WORDS
            and not token.isdigit()
            and re.fullmatch(r"\d+(?:m|h|d|w)", token) is None
            and len(token) > 2
        ]
        if not words:
            return ()
        return (" ".join(words[:4]),)


def _selection_parameters(condition: ConditionRule) -> dict[str, Any]:
    parameters: dict[str, Any] = dict(condition.left.parameters)
    if condition.right is not None:
        parameters.update(condition.right.parameters)
        if condition.right.kind.value == "constant":
            parameters["threshold"] = condition.right.value
    return parameters


def _candidate_contract_is_exact(
    candidate: CapabilityCandidate,
    fragment: str,
) -> bool:
    """Fuzzy relevance is useful for choices, never for executable selection."""

    exact_markers = ("exact:", "alias:", "ordered_alias:", "semantic:volume_ratio")
    if any(marker.startswith(exact_markers) for marker in candidate.matched_on):
        return True
    if "semantic:percent_change" in candidate.matched_on:
        lowered = fragment.casefold()
        return bool(
            re.search(
                r"\b(?:open[\s-]*to[\s-]*close|close[\s-]*to[\s-]*close|"
                r"previous\s+(?:candle\s+)?close|lookback|since\s+(?:the\s+)?"
                r"(?:open|close|midnight)|from\s+(?:the\s+)?"
                r"(?:open|close|high|low)|today|daily\s+move)\b",
                lowered,
            )
        )
    return False


def _parameter_target(payload: dict[str, Any], name: str) -> dict[str, Any]:
    left = payload["left"]
    right = payload.get("right")
    if name in left.get("parameters", {}):
        return left
    if isinstance(right, dict) and name in right.get("parameters", {}):
        return right
    if isinstance(right, dict) and right.get("kind") != "constant":
        return right
    return left


def _validate_parameter(name: str, value: Any, schema: dict[str, Any]) -> None:
    expected = schema.get("type")
    valid = (
        expected == "boolean"
        and isinstance(value, bool)
        or expected == "integer"
        and isinstance(value, int)
        and not isinstance(value, bool)
        or expected == "number"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        or expected == "string"
        and isinstance(value, str)
        or expected is None
    )
    if not valid:
        raise ValueError(f"Parameter {name} must be {expected}")
    if schema.get("enum") and value not in schema["enum"]:
        raise ValueError(f"Parameter {name} must be one of {schema['enum']}")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%]+", " ", value.casefold())).strip()


def _semantic_fragment(value: str) -> str:
    """Remove conversational framing while preserving every market-mechanic token."""
    cleaned = " ".join(value.strip().split())
    request_frames = (
        r"^(?:please\s+)?(?:i\s+(?:just\s+)?want(?:\s+you)?\s+to|i\s+need(?:\s+you)?\s+to)\s+",
        (
            r"^(?:please\s+)?(?:bring|show|find|get|give)\s+(?:me\s+)?"
            r"(?:(?:coins?|symbols?|pairs?)\s+(?:where|that|which)\s+)?"
        ),
        r"^(?:please\s+)?(?:can|could|would)\s+you\s+",
        r"^(?:please\s+)?(?:to\s+)?check\s+(?:if|whether)\s+",
        r"^(?:please\s+)?(?:watch|monitor|alert\s+me)\s+(?:for|when|if)\s+",
        r"^(?:no[, ]+)?(?:what\s+)?i\s+(?:actually\s+)?(?:mean|want)(?:\s+is)?\s+",
    )
    changed = True
    while changed:
        changed = False
        for pattern in request_frames:
            updated = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip(" ,:-")
            if updated != cleaned:
                cleaned = updated
                changed = True
    return cleaned or value


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_stem_token(token) for token in re.findall(r"[a-z0-9%]+", value.casefold()))


def _contains_phrase(text: str, phrase: str) -> bool:
    text_tokens = _tokens(text)
    phrase_tokens = _tokens(phrase)
    width = len(phrase_tokens)
    return bool(width) and any(
        text_tokens[index : index + width] == phrase_tokens
        for index in range(len(text_tokens) - width + 1)
    )


def _contains_tokens_in_order(text: str, phrase: str) -> bool:
    phrase_tokens = [token for token in _tokens(phrase) if token not in _COMMON_PROMPT_WORDS]
    if len(phrase_tokens) < 2:
        return False
    iterator = iter(_tokens(text))
    return all(any(candidate == token for candidate in iterator) for token in phrase_tokens)


def _stem_token(token: str) -> str:
    irregular = {
        "swept": "sweep",
        "sweeped": "sweep",
        "sweeps": "sweep",
        "crossed": "cross",
        "crosses": "cross",
    }
    if token in irregular:
        return irregular[token]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(_tokens(left)) - _COMMON_PROMPT_WORDS
    right_tokens = set(_tokens(right)) - _COMMON_PROMPT_WORDS
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _is_context_only(fragment: str) -> bool:
    normalized = _normalize(fragment)
    # A scalar or ordinary confirmation can be an answer to a prior question, but it
    # can never identify an executable market capability on its own. This also makes
    # old sessions fail quiet instead of asking users to define `0`, `none`, or `yes`.
    if re.fullmatch(
        r"(?:[-+]?\d+(?:\.\d+)?(?:%|x)?|none|no|yes|exact(?: only)?)",
        normalized,
    ):
        return True
    if normalized.startswith(("apply ", "use ")):
        return True
    # Alert timing and logical sequencing are strategy-tree context, not market-data
    # capabilities. Keep them in the accumulated prompt for the compiler, but do not
    # ask the user to define ordinary sequencing language as a new indicator.
    if re.search(r"\b(?:alert|notify|notification)\b", normalized) and re.search(
        r"\b(?:after|before|when|once|then|within)\b|\b(?:candle|candles)\b",
        normalized,
    ):
        return True
    alert_context = re.sub(
        r"\b(?:alert|alerts|alerted|notify|notification|notifications|me|on|the|a|an|"
        r"chart|timeframe|time frame|at|using|use)\b",
        " ",
        normalized,
    )
    alert_context_tokens = set(_tokens(alert_context))
    if re.search(r"\b(?:alert|notify|notification)\b", normalized) and (
        not alert_context_tokens
        or alert_context_tokens.issubset(
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
        )
    ):
        return True
    normalized = re.sub(
        r"^(?:setup mode|timeframe|time frame|universe|watchlist|symbols?|pairs?|"
        r"exchange|quote assets?|direction)(?: choice)?\s+",
        "",
        normalized,
    )
    tokens = set(_tokens(normalized))
    context_tokens = {
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
        "all",
        "bearish",
        "binance",
        "both",
        "bullish",
        "bybit",
        "coins",
        "long",
        "markets",
        "neutral",
        "pairs",
        "short",
        "scanner",
        "spot",
        "symbols",
        "usdc",
        "usdt",
        "monitor",
    }
    return bool(tokens) and tokens.issubset(context_tokens)


def _is_structured_clarification_answer(fragment: str) -> bool:
    return _normalize(fragment).startswith("clarification answer for ")
