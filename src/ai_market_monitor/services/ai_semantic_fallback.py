from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.capabilities import CapabilitySpec, all_capabilities
from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.engine.prompt_audit import audit_prompt_coverage
from ai_market_monitor.engine.prompt_semantics import normalize_prompt_text
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import (
    ConditionRule,
    InterpretationIssue,
    InterpretationPreview,
    StrategyDefinition,
)
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from ai_market_monitor.services.provider_runtime import provider_request


class AISemanticFallbackClient(Protocol):
    async def classify_fragment(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class AISemanticClassification(BaseModel):
    fragment: str
    semantic_type: Literal[
        "condition",
        "timeframe",
        "comparator",
        "threshold",
        "direction",
        "negation",
        "requiredness",
        "provider_required",
        "vague",
        "unsupported",
    ]
    plain_english_meaning: str
    canonical_intent: str
    candidate_capability_keys: list[str] = Field(default_factory=list)
    direction: Literal["bullish", "bearish", "up", "down", "neutral"] | None = None
    comparator: (
        Literal[
            "gt",
            "gte",
            "lt",
            "lte",
            "eq",
            "crosses_above",
            "crosses_below",
        ]
        | None
    ) = None
    threshold: float | None = None
    timeframe: str | None = None
    required: bool = True
    negated: bool = False
    confidence: float = Field(ge=0, le=1)
    provider_required: bool = False
    needs_clarification: bool = False
    clarification_question: str | None = None
    reason: str
    safe_to_convert: bool = False


@dataclass(slots=True)
class AISemanticFallbackOutcome:
    status: Literal[
        "disabled",
        "not_called",
        "converted",
        "provider_required",
        "needs_review",
        "needs_clarification",
        "unsupported",
        "rejected",
        "error",
    ]
    classification: AISemanticClassification | None = None
    condition: ConditionRule | None = None
    issue: InterpretationIssue | None = None
    from_cache: bool = False


class OpenAISemanticFallbackClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def classify_fragment(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = self.settings.openai_api_key
        if api_key is None:
            raise ValueError("OPENAI_API_KEY is not configured")
        request_payload = {
            "model": self.settings.ai_semantic_fallback_model,
            "store": False,
            "max_output_tokens": 1200,
            "reasoning": {"effort": self.settings.openai_reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "semantic_fragment_classification",
                    "strict": True,
                    "schema": _classification_schema(),
                }
            },
            "instructions": _semantic_instructions(),
            "input": json.dumps(payload, sort_keys=True),
        }
        headers = {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        response = await provider_request(
            self.settings,
            "POST",
            f"{str(self.settings.openai_base_url).rstrip('/')}/responses",
            provider="openai",
            operation="semantic_fallback",
            # One paid answer per turn: this call is not repeated.
            retry=False,
            model=str(request_payload.get("model") or ""),
            timeout=self.settings.openai_timeout_seconds,
            deadline_seconds=float(self.settings.openai_timeout_seconds),
            mutation_committed=False,
            transport=self.transport,
            headers=headers,
            json=request_payload,
        )
        response.raise_for_status()
        return _loads_json_object(_extract_output_text(response.json()))


class AISemanticFallbackService:
    def __init__(
        self,
        settings: Settings,
        *,
        client: AISemanticFallbackClient | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or OpenAISemanticFallbackClient(settings)
        self._now = now or time.monotonic
        self._cache: dict[str, tuple[float, AISemanticFallbackOutcome]] = {}
        self._capabilities = {capability.key: capability for capability in all_capabilities()}
        self._resolver = CapabilityResolver()

    async def resolve_fragment(
        self,
        *,
        original_prompt: str,
        unresolved_fragment: str,
        parsed_conditions: list[ConditionRule],
        default_timeframe: str,
    ) -> AISemanticFallbackOutcome:
        if not self.settings.ai_semantic_fallback_enabled:
            return AISemanticFallbackOutcome(status="disabled")
        if self.settings.openai_api_key is None:
            return AISemanticFallbackOutcome(
                status="error",
                issue=_issue(
                    "ai_semantic_unavailable",
                    unresolved_fragment,
                    "AI semantic fallback is enabled but OPENAI_API_KEY is not configured.",
                    blocking=False,
                ),
            )
        fragment = unresolved_fragment.strip()[
            : self.settings.ai_semantic_fallback_max_fragment_chars
        ]
        if not fragment:
            return AISemanticFallbackOutcome(status="not_called")
        resolution = self._resolver.resolve_fragment(fragment)
        candidate_keys = tuple(candidate.capability_key for candidate in resolution.candidates)
        if not candidate_keys:
            return AISemanticFallbackOutcome(
                status="needs_clarification",
                issue=_issue(
                    "ai_semantic_clarification_required",
                    fragment,
                    resolution.clarification_question
                    or "This phrase needs a measurable definition before activation.",
                    blocking=True,
                    reason=(
                        f"Unknown terms: {', '.join(resolution.unknown_terms)}"
                        if resolution.unknown_terms
                        else None
                    ),
                ),
            )
        cache_key = self._cache_key(fragment)
        cached = self._cache.get(cache_key)
        now = self._now()
        if cached and cached[0] > now:
            cached_outcome = cached[1]
            return AISemanticFallbackOutcome(
                status=cached_outcome.status,
                classification=cached_outcome.classification,
                condition=cached_outcome.condition,
                issue=cached_outcome.issue,
                from_cache=True,
            )
        payload = {
            "original_prompt": original_prompt,
            "unresolved_fragment": fragment,
            "already_parsed_conditions": [
                {
                    "key": condition.key,
                    "label": condition.label,
                    "type": condition.condition_type.value,
                    "timeframe": condition.timeframe,
                    "source_fragment": condition.source_fragment,
                }
                for condition in parsed_conditions
            ],
            "capability_resolution": resolution.to_dict(),
            "available_capabilities": self._capability_summary(
                candidate_keys, provider_required=False
            ),
            "provider_required_capabilities": self._capability_summary(
                candidate_keys, provider_required=True
            ),
            "default_timeframe": default_timeframe,
        }
        try:
            raw = await self.client.classify_fragment(payload)
            classification = AISemanticClassification.model_validate(raw)
            outcome = self._validate_classification(
                classification,
                source_fragment=fragment,
                default_timeframe=default_timeframe,
                allowed_candidate_keys=set(candidate_keys),
            )
        except (httpx.HTTPError, ValueError, TypeError, ValidationError, KeyError) as exc:
            outcome = AISemanticFallbackOutcome(
                status="error",
                issue=_issue(
                    "ai_semantic_error",
                    fragment,
                    "AI semantic fallback could not safely classify this phrase.",
                    blocking=False,
                    reason=type(exc).__name__,
                ),
            )
        self._cache[cache_key] = (
            now + self.settings.ai_semantic_fallback_cache_ttl_seconds,
            outcome,
        )
        return outcome

    def _validate_classification(
        self,
        classification: AISemanticClassification,
        *,
        source_fragment: str,
        default_timeframe: str,
        allowed_candidate_keys: set[str],
    ) -> AISemanticFallbackOutcome:
        if classification.provider_required or classification.semantic_type == "provider_required":
            return AISemanticFallbackOutcome(
                status="provider_required",
                classification=classification,
                issue=_issue(
                    "provider_required",
                    source_fragment,
                    f"'{source_fragment}' requires provider data and cannot be made "
                    "executable by AI.",
                    blocking=classification.required,
                ),
            )
        if (
            classification.semantic_type in {"vague", "unsupported"}
            or classification.needs_clarification
        ):
            return AISemanticFallbackOutcome(
                status="needs_clarification"
                if classification.needs_clarification
                else "unsupported",
                classification=classification,
                issue=_issue(
                    "ai_semantic_clarification_required"
                    if classification.needs_clarification
                    else "ai_semantic_unsupported",
                    source_fragment,
                    classification.clarification_question
                    or classification.reason
                    or "The phrase needs a measurable definition before activation.",
                    blocking=classification.required,
                ),
            )
        if classification.confidence < self.settings.ai_semantic_fallback_review_confidence:
            return AISemanticFallbackOutcome(
                status="rejected",
                classification=classification,
                issue=_issue(
                    "ai_semantic_low_confidence",
                    source_fragment,
                    "AI confidence was too low to convert this phrase safely.",
                    blocking=classification.required,
                ),
            )
        if classification.confidence < self.settings.ai_semantic_fallback_min_confidence:
            return AISemanticFallbackOutcome(
                status="needs_review",
                classification=classification,
                issue=_issue(
                    "ai_semantic_review_required",
                    source_fragment,
                    "AI suggested an interpretation, but it needs user confirmation "
                    "before activation.",
                    blocking=classification.required,
                ),
            )
        if not classification.safe_to_convert or classification.semantic_type != "condition":
            return AISemanticFallbackOutcome(
                status="rejected",
                classification=classification,
                issue=_issue(
                    "ai_semantic_not_safe_to_convert",
                    source_fragment,
                    "AI did not mark this phrase as safe to convert into a deterministic "
                    "condition.",
                    blocking=classification.required,
                ),
            )
        if not classification.candidate_capability_keys:
            return AISemanticFallbackOutcome(
                status="rejected",
                classification=classification,
                issue=_issue(
                    "ai_semantic_missing_capability",
                    source_fragment,
                    "AI did not return a known capability key.",
                    blocking=classification.required,
                ),
            )
        selected_key = classification.candidate_capability_keys[0]
        if selected_key not in allowed_candidate_keys:
            return AISemanticFallbackOutcome(
                status="rejected",
                classification=classification,
                issue=_issue(
                    "ai_semantic_candidate_outside_shortlist",
                    source_fragment,
                    "AI selected a capability outside the registry resolver shortlist.",
                    blocking=classification.required,
                ),
            )
        capability = self._capabilities.get(selected_key)
        if capability is None:
            return AISemanticFallbackOutcome(
                status="rejected",
                classification=classification,
                issue=_issue(
                    "ai_semantic_unknown_capability",
                    source_fragment,
                    "AI returned a capability key that is not in the registry.",
                    blocking=classification.required,
                ),
            )
        if capability.provider_required or not capability.executable:
            return AISemanticFallbackOutcome(
                status="provider_required" if capability.provider_required else "unsupported",
                classification=classification,
                issue=_issue(
                    (
                        "provider_required"
                        if capability.provider_required
                        else "ai_semantic_unsupported"
                    ),
                    source_fragment,
                    f"{capability.label} is not executable in the current beta scanner.",
                    blocking=classification.required,
                ),
            )
        condition = self._condition_from_capability(
            capability,
            classification=classification,
            source_fragment=source_fragment,
            default_timeframe=default_timeframe,
        )
        return AISemanticFallbackOutcome(
            status="converted",
            classification=classification,
            condition=condition,
        )

    def _condition_from_capability(
        self,
        capability: CapabilitySpec,
        *,
        classification: AISemanticClassification,
        source_fragment: str,
        default_timeframe: str,
    ) -> ConditionRule:
        parameters: dict[str, Any] = {}
        if classification.threshold is not None:
            parameters["threshold"] = classification.threshold
        condition = self._resolver.validate_selection(
            capability_key=capability.key,
            parameters=parameters,
            timeframe=classification.timeframe or default_timeframe,
            required=classification.required,
            source_fragment=source_fragment,
            comparator=classification.comparator,
            confidence=classification.confidence,
        )
        if capability.condition_type == "candle_pattern" and classification.negated:
            condition = condition.model_copy(update={"comparator": "is_false"})
        return condition

    def _capability_summary(
        self,
        keys: tuple[str, ...],
        *,
        provider_required: bool,
    ) -> list[dict[str, Any]]:
        return [
            {
                "key": capability.key,
                "label": capability.label,
                "category": capability.category,
                "condition_type": capability.condition_type,
                "aliases": list(capability.aliases[:8]),
                "semantic_tags": list(capability.semantic_tags),
                "intent_examples": list(capability.intent_examples[:4]),
                "negative_examples": list(capability.negative_examples[:4]),
                "direction_support": list(capability.direction_support),
                "temporal_behavior": capability.temporal_behavior,
                "parameter_schema": capability.parameter_schema,
                "provider_required": capability.provider_required,
            }
            for key in keys
            if (capability := self._capabilities.get(key)) is not None
            if bool(capability.provider_required) is provider_required
            and (provider_required or capability.executable)
        ]

    def _cache_key(self, fragment: str) -> str:
        alias_count = sum(len(item.aliases) for item in self._capabilities.values())
        registry_version = f"{len(self._capabilities)}:{alias_count}"
        return "|".join(
            (
                normalize_prompt_text(fragment),
                registry_version,
                self.settings.ai_semantic_fallback_model,
                str(self.settings.ai_semantic_fallback_min_confidence),
            )
        )


class AISemanticFallbackStrategyInterpreter:
    name = "rules-v2:ai-semantic-fallback"
    deterministic_core_authority = True

    def __init__(
        self,
        settings: Settings,
        *,
        deterministic: RuleBasedStrategyInterpreter | None = None,
        service: AISemanticFallbackService | None = None,
    ) -> None:
        self.settings = settings
        self.deterministic = deterministic or RuleBasedStrategyInterpreter()
        self.service = service or AISemanticFallbackService(settings)

    async def interpret(self, guided_setup: GuidedSetupRequest) -> InterpretationPreview:
        preview = await self.deterministic.interpret(guided_setup)
        fragments = self._unresolved_fragments(preview)
        if not fragments:
            return preview

        strategy = preview.strategy.model_copy(deep=True)
        conditions = [
            condition
            for condition in strategy.conditions.children
            if not (
                isinstance(condition, ConditionRule) and condition.key == "clarification_required"
            )
        ]
        assumptions = list(preview.assumptions)
        unsupported = [
            issue
            for issue in preview.unsupported_conditions
            if issue.code != "no_supported_monitor_condition"
        ]
        outcomes: list[dict[str, Any]] = []
        added = False
        for fragment in fragments[: self.settings.ai_semantic_fallback_max_calls_per_prompt]:
            outcome = await self.service.resolve_fragment(
                original_prompt=guided_setup.setup_text or "",
                unresolved_fragment=fragment,
                parsed_conditions=[
                    condition for condition in conditions if isinstance(condition, ConditionRule)
                ],
                default_timeframe=strategy.base_timeframe,
            )
            outcomes.append(
                {
                    "fragment": fragment,
                    "status": outcome.status,
                    "from_cache": outcome.from_cache,
                    "capability": (
                        outcome.classification.candidate_capability_keys[0]
                        if outcome.classification
                        and outcome.classification.candidate_capability_keys
                        else None
                    ),
                }
            )
            if outcome.condition is not None and outcome.condition.key not in {
                condition.key for condition in conditions
            }:
                conditions.append(outcome.condition)
                added = True
                assumptions.append(
                    f"AI-assisted semantic fallback understood '{fragment}' as "
                    f"{outcome.condition.label}."
                )
            elif outcome.issue is not None and not any(
                issue.code == outcome.issue.code
                and issue.source_fragment == outcome.issue.source_fragment
                for issue in unsupported
            ):
                unsupported.append(outcome.issue)

        if not added:
            return preview.model_copy(
                update={
                    "unsupported_conditions": unsupported or preview.unsupported_conditions,
                    "raw_metadata": {
                        **(preview.raw_metadata or {}),
                        "ai_semantic_fallback": outcomes,
                    },
                }
            )
        strategy.conditions = strategy.conditions.model_copy(update={"children": conditions})
        coverage = audit_prompt_coverage(
            guided_setup.setup_text or "",
            strategy,
            assumptions=assumptions,
            ambiguities=preview.ambiguities,
            unsupported=unsupported,
            ai_interpreted=True,
        )
        return InterpretationPreview(
            strategy=StrategyDefinition.model_validate(strategy.model_dump(mode="json")),
            assumptions=assumptions,
            ambiguities=preview.ambiguities,
            unsupported_conditions=unsupported,
            interpreter=self.name,
            raw_metadata={
                **(preview.raw_metadata or {}),
                "ai_semantic_fallback": outcomes,
                "prompt_coverage_report": coverage.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _unresolved_fragments(preview: InterpretationPreview) -> list[str]:
        fragments: list[str] = []
        report = (preview.raw_metadata or {}).get("prompt_coverage_report") or {}
        for row in report.get("mapping_table", []):
            if row.get("bucket") == "unclassified" and row.get("fragment"):
                fragments.append(str(row["fragment"]))
        for issue in preview.unsupported_conditions:
            if (
                issue.code
                in {
                    "no_supported_monitor_condition",
                    "prompt_fragment_unclassified",
                    "instruction_not_converted",
                }
                and issue.source_fragment
            ):
                fragments.append(issue.source_fragment)
        deduped: list[str] = []
        for fragment in fragments:
            normalized = normalize_prompt_text(fragment)
            if normalized and normalized not in {normalize_prompt_text(item) for item in deduped}:
                deduped.append(fragment)
        return deduped


def _issue(
    code: str,
    fragment: str,
    message: str,
    *,
    blocking: bool,
    reason: str | None = None,
) -> InterpretationIssue:
    suffix = f" Reason: {reason}." if reason else ""
    return InterpretationIssue(
        code=code,
        field="setup_text",
        message=f"{message}{suffix}",
        blocking=blocking,
        source_fragment=fragment,
    )


def _classification_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "fragment": {"type": "string"},
            "semantic_type": {
                "type": "string",
                "enum": [
                    "condition",
                    "timeframe",
                    "comparator",
                    "threshold",
                    "direction",
                    "negation",
                    "requiredness",
                    "provider_required",
                    "vague",
                    "unsupported",
                ],
            },
            "plain_english_meaning": {"type": "string"},
            "canonical_intent": {"type": "string"},
            "candidate_capability_keys": {"type": "array", "items": {"type": "string"}},
            "direction": {
                "type": ["string", "null"],
                "enum": ["bullish", "bearish", "up", "down", "neutral", None],
            },
            "comparator": {
                "type": ["string", "null"],
                "enum": [
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "eq",
                    "crosses_above",
                    "crosses_below",
                    None,
                ],
            },
            "threshold": {"type": ["number", "null"]},
            "timeframe": {"type": ["string", "null"]},
            "required": {"type": "boolean"},
            "negated": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "provider_required": {"type": "boolean"},
            "needs_clarification": {"type": "boolean"},
            "clarification_question": {"type": ["string", "null"]},
            "reason": {"type": "string"},
            "safe_to_convert": {"type": "boolean"},
        },
        "required": [
            "fragment",
            "semantic_type",
            "plain_english_meaning",
            "canonical_intent",
            "candidate_capability_keys",
            "direction",
            "comparator",
            "threshold",
            "timeframe",
            "required",
            "negated",
            "confidence",
            "provider_required",
            "needs_clarification",
            "clarification_question",
            "reason",
            "safe_to_convert",
        ],
    }


def _semantic_instructions() -> str:
    return (
        "You classify unresolved crypto research-monitor language. You do not provide "
        "trading advice and you do not invent unsupported conditions. Map only to the "
        "listed capability keys. If the phrase is vague, mark needs_clarification=true. "
        "If the phrase requires unavailable provider data such as news, open interest, "
        "funding, order book, market cap, macro, or BTC/ETH context, mark "
        "provider_required=true. Preserve the exact source fragment. Return JSON only. "
        "Examples: green candle -> green_candle; positive candle -> green_candle; red "
        "candle -> red_candle; volume not dead -> review/minimum volume only if a default "
        "threshold exists; strong coin -> vague; positive news -> provider_required; "
        "BTC above EMA 200 -> provider_required cross-symbol context."
    )


def _extract_output_text(payload: dict[str, Any]) -> str:
    direct_output = payload.get("output_text")
    if isinstance(direct_output, str) and direct_output.strip():
        return direct_output
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                return str(content.get("text") or "")
    raise ValueError("AI semantic response did not contain output_text")


def _loads_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("AI semantic response JSON was not an object")
    return parsed
