from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.capability_index import get_capability_index
from ai_market_monitor.engine.capability_resolver import (
    CapabilityResolutionReport,
    FragmentResolution,
    ResolutionStatus,
)
from ai_market_monitor.services.capability_registry import OpenAIEmbeddingClient


class CapabilityRerankDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fragment_index: int = Field(ge=0)
    capability_key: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool
    clarification_question: str | None = None
    reason: str = Field(min_length=1, max_length=500)


class CapabilityRerankResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[CapabilityRerankDecision] = Field(default_factory=list, max_length=30)


class CapabilityReranker(Protocol):
    last_usage: dict[str, Any]

    async def rerank(self, payload: dict[str, Any]) -> CapabilityRerankResponse: ...


class OpenAICapabilityReranker:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.last_usage: dict[str, Any] = {}

    async def rerank(self, payload: dict[str, Any]) -> CapabilityRerankResponse:
        if self.settings.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is not configured")
        request_payload = {
            "model": self.settings.openai_model,
            "store": False,
            "max_output_tokens": 1800,
            "reasoning": {"effort": self.settings.openai_reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "traceedge_capability_rerank",
                    "strict": False,
                    "schema": _rerank_schema(),
                }
            },
            "instructions": _rerank_instructions(),
            "input": json.dumps(payload, sort_keys=True),
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=str(self.settings.openai_base_url).rstrip("/"),
            timeout=self.settings.openai_timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post("/responses", headers=headers, json=request_payload)
        response.raise_for_status()
        body = response.json()
        self.last_usage = dict(body.get("usage") or {})
        return CapabilityRerankResponse.model_validate_json(_output_text(body))


@dataclass(slots=True)
class HybridCapabilityResolution:
    report: CapabilityResolutionReport
    bindings: list[dict[str, Any]]
    usage: dict[str, Any]


class HybridCapabilityResolutionService:
    """Code retrieves candidates; AI may only choose and parameterize those candidates."""

    def __init__(
        self,
        settings: Settings,
        *,
        reranker: CapabilityReranker | None = None,
        embedding_client: OpenAIEmbeddingClient | None = None,
    ) -> None:
        self.settings = settings
        self.index = get_capability_index()
        self.resolver = self.index.resolver
        self.reranker = reranker or OpenAICapabilityReranker(settings)
        self.embedding_client = embedding_client or OpenAIEmbeddingClient(settings)

    async def resolve(
        self,
        report: CapabilityResolutionReport,
        *,
        history: list[dict[str, str]],
        default_timeframe: str,
        selections: dict[str, str] | None = None,
    ) -> HybridCapabilityResolution:
        fragments = list(report.fragments)
        selected_bindings = self._apply_user_selections(
            fragments,
            selections or {},
            default_timeframe,
        )
        bindings = self._deterministic_bindings(fragments, default_timeframe)
        bindings.extend(selected_bindings)
        unresolved_indexes = [
            index for index, fragment in enumerate(fragments) if fragment.status != "matched"
        ]
        if (
            not unresolved_indexes
            or not self.settings.ai_capability_reranker_enabled
            or self.settings.openai_api_key is None
            or self.settings.app_env == "test"
        ):
            return HybridCapabilityResolution(report, bindings, {})
        # Candidate retrieval must be local to the exact current fragment. Conversation history
        # is supplied separately to the AI reranker for corrections and pronouns; concatenating
        # an older message here leaks stale words into lexical/embedding retrieval and can make
        # unrelated fragments inherit an old capability candidate.
        candidate_texts = {
            index: fragments[index].fragment.strip()
            for index in unresolved_indexes
        }
        correction_context = _explicit_correction_context(history)
        query_embeddings: dict[int, list[float]] = {}
        if (
            self.settings.capability_embeddings_enabled
            and self.index.snapshot.embeddings
            and self.settings.openai_api_key is not None
        ):
            try:
                vectors = await self.embedding_client.embed(
                    [candidate_texts[index] for index in unresolved_indexes]
                )
            except httpx.HTTPError:
                vectors = []
            if len(vectors) == len(unresolved_indexes):
                query_embeddings = dict(zip(unresolved_indexes, vectors, strict=True))
        candidate_map = {}
        for index in unresolved_indexes:
            lexical = self.resolver.broad_candidates(
                candidate_texts[index],
                limit=self.settings.ai_capability_reranker_candidate_limit,
            )
            semantic = self.index.semantic_candidates(
                candidate_texts[index],
                query_embeddings.get(index),
                limit=self.settings.ai_capability_reranker_candidate_limit,
            )
            merged = {candidate.capability_key: candidate for candidate in semantic}
            merged.update({candidate.capability_key: candidate for candidate in lexical})
            if correction_context:
                # A direct correction may supply a missing side/reference (for example, "I mean
                # last week's low"). Add its candidates to the shortlist, but never concatenate
                # it into the current fragment or alter the source fragment being compiled.
                contextual = self.resolver.broad_candidates(
                    correction_context,
                    limit=self.settings.ai_capability_reranker_candidate_limit,
                )
                for candidate in contextual:
                    merged.setdefault(candidate.capability_key, candidate)
            candidate_map[index] = tuple(
                sorted(merged.values(), key=lambda item: (-item.score, item.label))[
                    : self.settings.ai_capability_reranker_candidate_limit
                ]
            )
        payload = {
            "conversation": history[-16:],
            "default_timeframe": default_timeframe,
            "fragments": [
                {
                    "fragment_index": index,
                    "source_fragment": fragments[index].fragment,
                    "unknown_terms": list(fragments[index].unknown_terms),
                    "candidates": [
                        {
                            "capability_key": candidate.capability_key,
                            "label": candidate.label,
                            "description": self.resolver.get(candidate.capability_key).description,
                            "semantic_tags": list(candidate.semantic_tags),
                            "parameter_schema": candidate.parameter_schema,
                            "direction_support": list(candidate.direction_support),
                            "temporal_behavior": candidate.temporal_behavior,
                            "availability": candidate.availability,
                        }
                        for candidate in candidate_map[index]
                    ],
                }
                for index in unresolved_indexes
            ],
        }
        try:
            response = await self.reranker.rerank(payload)
        except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError):
            return HybridCapabilityResolution(report, bindings, {})
        for decision in response.decisions:
            if decision.fragment_index not in candidate_map:
                continue
            candidates = candidate_map[decision.fragment_index]
            allowed = {candidate.capability_key for candidate in candidates}
            if not decision.capability_key or decision.capability_key not in allowed:
                continue
            selected = next(
                candidate
                for candidate in candidates
                if candidate.capability_key == decision.capability_key
            )
            source = fragments[decision.fragment_index]
            clarification = decision.clarification_question
            status: ResolutionStatus = "ambiguous"
            if (
                not decision.needs_clarification
                and decision.confidence >= self.settings.ai_capability_reranker_min_confidence
                and selected.availability == "available"
            ):
                try:
                    self.resolver.validate_selection(
                        capability_key=selected.capability_key,
                        parameters=decision.parameters,
                        timeframe=_timeframe(source.fragment, default_timeframe),
                        required=True,
                        source_fragment=source.fragment,
                        confidence=decision.confidence,
                    )
                except ValueError as exc:
                    clarification = _parameter_question(selected.label, str(exc))
                else:
                    status = "matched"
                    bindings.append(
                        _binding(
                            source.fragment,
                            selected.capability_key,
                            decision.parameters,
                            _timeframe(source.fragment, default_timeframe),
                            decision.confidence,
                            "ai_reranker",
                        )
                    )
            reordered = (selected, *[item for item in candidates if item != selected])
            fragments[decision.fragment_index] = FragmentResolution(
                fragment=source.fragment,
                status=status,
                candidates=reordered,
                unknown_terms=() if status == "matched" else source.unknown_terms,
                clarification_question=(
                    clarification
                    or f"How should HilalMarkets measure '{source.fragment}' precisely?"
                ),
                selected_capability_key=(selected.capability_key if status == "matched" else None),
                selected_parameters=(decision.parameters if status == "matched" else None),
                selection_confidence=decision.confidence,
                selection_source="ai_reranker",
            )
        deduplicated = list({_binding_key(item): item for item in bindings}.values())
        return HybridCapabilityResolution(
            CapabilityResolutionReport(prompt=report.prompt, fragments=tuple(fragments)),
            deduplicated,
            dict(getattr(self.reranker, "last_usage", {}) or {}),
        )

    def _apply_user_selections(
        self,
        fragments: list[FragmentResolution],
        selections: dict[str, str],
        default_timeframe: str,
    ) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []
        for index, fragment in enumerate(fragments):
            slug = re.sub(r"[^a-z0-9]+", "_", fragment.fragment.casefold()).strip("_")[:48]
            selected_key = selections.get(f"capability_meaning_{slug or 'unknown'}")
            if not selected_key:
                continue
            candidates = fragment.candidates or self.resolver.broad_candidates(fragment.fragment)
            selected = next(
                (item for item in candidates if item.capability_key == selected_key),
                None,
            )
            if selected is None:
                continue
            parameters = _obvious_parameters(selected_key, fragment.fragment)
            try:
                self.resolver.validate_selection(
                    capability_key=selected_key,
                    parameters=parameters,
                    timeframe=_timeframe(fragment.fragment, default_timeframe),
                    required=True,
                    source_fragment=fragment.fragment,
                    confidence=1.0,
                )
            except ValueError:
                continue
            fragments[index] = FragmentResolution(
                fragment=fragment.fragment,
                status="matched",
                candidates=(selected, *[item for item in candidates if item != selected]),
                selected_capability_key=selected_key,
                selected_parameters=parameters,
                selection_confidence=1.0,
                selection_source="user_choice",
            )
            bindings.append(
                _binding(
                    fragment.fragment,
                    selected_key,
                    parameters,
                    _timeframe(fragment.fragment, default_timeframe),
                    1.0,
                    "user_choice",
                )
            )
        return bindings

    def _deterministic_bindings(
        self,
        fragments: list[FragmentResolution],
        default_timeframe: str,
    ) -> list[dict[str, Any]]:
        results = []
        for fragment in fragments:
            if fragment.status != "matched" or not fragment.candidates:
                continue
            candidate = fragment.candidates[0]
            parameters = _obvious_parameters(candidate.capability_key, fragment.fragment)
            try:
                self.resolver.validate_selection(
                    capability_key=candidate.capability_key,
                    parameters=parameters,
                    timeframe=_timeframe(fragment.fragment, default_timeframe),
                    required=True,
                    source_fragment=fragment.fragment,
                    confidence=candidate.confidence,
                )
            except ValueError:
                continue
            results.append(
                _binding(
                    fragment.fragment,
                    candidate.capability_key,
                    parameters,
                    _timeframe(fragment.fragment, default_timeframe),
                    candidate.confidence,
                    "deterministic_resolver",
                )
            )
        return results


def _binding(
    fragment: str,
    capability_key: str,
    parameters: dict[str, Any],
    timeframe: str,
    confidence: float,
    source: str,
) -> dict[str, Any]:
    return {
        "capability_key": capability_key,
        "parameters": parameters,
        "timeframe": timeframe,
        "required": True,
        "source_fragment": fragment,
        "confidence": confidence,
        "selection_source": source,
    }


def _binding_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item["capability_key"]), str(item["source_fragment"]).casefold())


def _obvious_parameters(capability_key: str, fragment: str) -> dict[str, Any]:
    lowered = fragment.casefold()
    if capability_key != "reference_period_sweep":
        return {}
    period = (
        "month"
        if re.search(r"\bmonth(?:ly)?\b", lowered)
        else "day"
        if re.search(r"\bday|daily\b", lowered)
        else "week"
    )
    side = (
        "high"
        if re.search(r"\bhigh\b|\bbearish\b", lowered)
        else "low"
        if re.search(r"\blow\b|\bbullish\b", lowered)
        else None
    )
    return {
        "reference_period": period,
        **({"side": side} if side else {}),
        "timezone": "UTC",
    }


def _timeframe(fragment: str, default: str) -> str:
    match = re.search(
        r"\b(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)\b",
        fragment.casefold(),
    )
    return match.group(1) if match else default


def _explicit_correction_context(history: list[dict[str, str]]) -> str:
    latest = next(
        (
            str(item.get("content") or "").strip()
            for item in reversed(history)
            if item.get("role") == "user" and item.get("content")
        ),
        "",
    )
    if not latest:
        return ""
    return (
        latest
        if re.match(
            r"^(?:no[, ]+|i mean\b|what i mean\b|to clarify\b|correction\b|"
            r"by that i mean\b|not\b.+\binstead\b)",
            latest,
            flags=re.IGNORECASE,
        )
        else ""
    )


def _parameter_question(label: str, error: str) -> str:
    if "requires parameters" in error:
        names = error.rsplit(":", 1)[-1].strip().replace(",", " and")
        return f"For {label}, what should {names} be?"
    return f"Which exact parameters should HilalMarkets use for {label}?"


def _output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []):
        for content in item.get("content", []) if isinstance(item, dict) else []:
            if content.get("type") in {"output_text", "text"}:
                return str(content.get("text") or "")
    raise ValueError("OpenAI response did not contain output text")


def _rerank_instructions() -> str:
    return (
        "You resolve plain trader language to HilalMarkets' existing crypto spot monitoring "
        "capabilities. Treat phrases such as I want, bring me, show me, check whether, and "
        "alert me as conversational framing, never as trading mechanics. Use conversation "
        "context to understand corrections and pronouns. For each fragment, choose only a "
        "capability_key listed in that fragment's candidates. Never invent a key, formula, "
        "indicator, provider, or market value. Extract only parameters allowed by the chosen "
        "parameter_schema. Ask a short clarification only when two mechanics remain genuinely "
        "possible or a required parameter is absent. Do not ask for information already stated. "
        "A high/low sweep requires knowing the side. This is monitoring logic, not advice."
    )


def _rerank_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "fragment_index": {"type": "integer"},
                        "capability_key": {"type": ["string", "null"]},
                        "parameters": {"type": "object"},
                        "confidence": {"type": "number"},
                        "needs_clarification": {"type": "boolean"},
                        "clarification_question": {"type": ["string", "null"]},
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "fragment_index",
                        "capability_key",
                        "parameters",
                        "confidence",
                        "needs_clarification",
                        "clarification_question",
                        "reason",
                    ],
                },
            }
        },
        "required": ["decisions"],
    }
