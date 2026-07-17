import json
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.engine.prompt_audit import audit_prompt_coverage
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import (
    InterpretationIssue,
    InterpretationPreview,
    StrategyDefinition,
)
from ai_market_monitor.services.interfaces import StrategyInterpreter
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


class AIInterpretationClient(Protocol):
    async def create_draft(self, guided_setup: GuidedSetupRequest) -> dict[str, Any]: ...


class OpenAIInterpretationError(ValueError):
    def __init__(self, message: str, *, output_excerpt: str | None = None) -> None:
        super().__init__(message)
        self.output_excerpt = output_excerpt


class OpenAIResponsesInterpretationClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.last_usage: dict[str, Any] = {}

    async def create_draft(self, guided_setup: GuidedSetupRequest) -> dict[str, Any]:
        api_key = self.settings.openai_api_key
        if api_key is None:
            raise ValueError("OPENAI_API_KEY is not configured")
        resolution = CapabilityResolver().resolve_prompt(guided_setup.setup_text or "")
        binding_keys = [
            str(item.get("capability_key") or "")
            for item in guided_setup.capability_bindings
            if item.get("capability_key")
        ]
        candidate_keys = list(dict.fromkeys([*resolution.candidate_keys, *binding_keys]))
        payload = {
            "model": self.settings.openai_model,
            "store": False,
            "max_output_tokens": 4000,
            "reasoning": {"effort": self.settings.openai_reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "market_monitor_strategy_draft",
                    "strict": False,
                    "schema": _strategy_draft_schema(candidate_keys),
                }
            },
            "instructions": _instructions(),
            "input": json.dumps(
                {
                    "guided_setup": guided_setup.model_dump(mode="json"),
                    "capability_resolution": resolution.ai_context(),
                    "verified_capability_bindings": guided_setup.capability_bindings,
                },
                sort_keys=True,
            ),
        }
        headers = {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=str(self.settings.openai_base_url).rstrip("/"),
            timeout=self.settings.openai_timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post("/responses", headers=headers, json=payload)
        response.raise_for_status()
        response_payload = response.json()
        self.last_usage = dict(response_payload.get("usage") or {})
        output_text = _extract_output_text(response_payload)
        try:
            return _loads_json_object(output_text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise OpenAIInterpretationError(
                "OpenAI response did not match the required JSON object format.",
                output_excerpt=_safe_excerpt(output_text),
            ) from exc


class OpenAIStrategyInterpreter:
    name = "openai-structured-v1"

    def __init__(
        self,
        settings: Settings,
        *,
        fallback: RuleBasedStrategyInterpreter | None = None,
        client: AIInterpretationClient | None = None,
    ) -> None:
        self.settings = settings
        self.fallback = fallback or RuleBasedStrategyInterpreter()
        self.client = client or OpenAIResponsesInterpretationClient(settings)

    async def interpret(self, guided_setup: GuidedSetupRequest) -> InterpretationPreview:
        if (
            self.settings.ai_interpreter_provider != "openai"
            or self.settings.openai_api_key is None
        ):
            return await self.fallback.interpret(guided_setup)
        try:
            draft = await self.client.create_draft(guided_setup)
            openai_usage = dict(getattr(self.client, "last_usage", {}) or {})
            strategy_payload = dict(draft)
            assumptions = [str(item) for item in strategy_payload.pop("assumptions", [])]
            ambiguities = [
                InterpretationIssue.model_validate(item)
                for item in strategy_payload.pop("ambiguities", [])
            ]
            unsupported = [
                InterpretationIssue.model_validate(item)
                for item in strategy_payload.pop("unsupported_conditions", [])
            ]
            definition = StrategyDefinition.model_validate(strategy_payload)
            if self._should_use_percent_move_fallback(guided_setup, definition):
                fallback = await self.fallback.interpret(guided_setup)
                return fallback.model_copy(
                    update={
                        "interpreter": f"{fallback.interpreter}:openai_percent_guard",
                        "raw_metadata": {
                            **(fallback.raw_metadata or {}),
                            "openai_guard": "percent_move_preserved",
                            "openai_usage": openai_usage,
                        },
                    }
                )
            resolution = CapabilityResolver().resolve_prompt(guided_setup.setup_text or "")
            definition = CapabilityResolver().canonicalize_ai_strategy(definition)
            coverage = audit_prompt_coverage(
                guided_setup.setup_text or "",
                definition,
                assumptions=assumptions,
                ambiguities=ambiguities,
                unsupported=unsupported,
                ai_interpreted=True,
            )
            if coverage.activation_blocked:
                fallback = await self.fallback.interpret(guided_setup)
                coverage_issue = InterpretationIssue(
                    code="openai_coverage_verification_failed",
                    field="setup_text",
                    message=(
                        "The AI draft did not account for every meaningful instruction. "
                        "Review the deterministic fallback before approval."
                    ),
                    blocking=True,
                    source_fragment=guided_setup.setup_text,
                )
                return fallback.model_copy(
                    update={
                        "interpreter": f"{fallback.interpreter}:openai_coverage_guard",
                        "ambiguities": [*fallback.ambiguities, coverage_issue],
                        "raw_metadata": {
                            **(fallback.raw_metadata or {}),
                            "openai_guard": "prompt_coverage_failed",
                            "openai_prompt_coverage_report": coverage.model_dump(mode="json"),
                            "openai_output_excerpt": _safe_excerpt(
                                json.dumps(draft, sort_keys=True, default=str)
                            ),
                            "openai_usage": openai_usage,
                        },
                    }
                )
            return InterpretationPreview(
                strategy=definition,
                assumptions=assumptions,
                ambiguities=ambiguities,
                unsupported_conditions=unsupported,
                interpreter=f"{self.name}:{self.settings.openai_model}",
                raw_metadata={
                    "provider": "openai",
                    "model": self.settings.openai_model,
                    "deterministic_evaluation_required": True,
                    "prompt_coverage_report": coverage.model_dump(mode="json"),
                    "capability_resolution": resolution.to_dict(),
                    "openai_output_excerpt": _safe_excerpt(
                        json.dumps(draft, sort_keys=True, default=str)
                    ),
                    "openai_usage": openai_usage,
                },
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError) as exc:
            fallback = await self.fallback.interpret(guided_setup)
            output_excerpt = getattr(exc, "output_excerpt", None)
            return InterpretationPreview(
                strategy=fallback.strategy,
                assumptions=fallback.assumptions,
                ambiguities=fallback.ambiguities,
                unsupported_conditions=fallback.unsupported_conditions,
                interpreter=f"{fallback.interpreter}:openai_fallback",
                raw_metadata={
                    **(fallback.raw_metadata or {}),
                    "openai_error": type(exc).__name__,
                    "openai_validation_errors": str(exc),
                    "openai_output_excerpt": output_excerpt,
                    "fallback_used": True,
                },
            )

    @staticmethod
    def _condition_tree_contains_operand(node: Any, operand_names: set[str]) -> bool:
        if node is None:
            return False
        left = getattr(node, "left", None)
        right = getattr(node, "right", None)
        if (
            getattr(left, "name", None) in operand_names
            or getattr(right, "name", None) in operand_names
        ):
            return True
        return any(
            OpenAIStrategyInterpreter._condition_tree_contains_operand(child, operand_names)
            for child in getattr(node, "children", []) or []
        )

    @classmethod
    def _should_use_percent_move_fallback(
        cls,
        guided_setup: GuidedSetupRequest,
        definition: StrategyDefinition,
    ) -> bool:
        text = (guided_setup.setup_text or "").casefold()
        timeframe = guided_setup.timeframe or definition.base_timeframe
        requested_percent_move = RuleBasedStrategyInterpreter._percent_move(text, timeframe)
        if requested_percent_move is None:
            return False
        return not cls._condition_tree_contains_operand(
            definition.conditions,
            {"percent_change_up", "percent_change_down"},
        )


def configured_strategy_interpreter(
    settings: Settings,
) -> StrategyInterpreter:
    if settings.ai_semantic_fallback_enabled:
        from ai_market_monitor.services.ai_semantic_fallback import (
            AISemanticFallbackStrategyInterpreter,
        )

        return AISemanticFallbackStrategyInterpreter(settings)
    if settings.ai_interpreter_provider == "openai":
        return OpenAIStrategyInterpreter(settings)
    return RuleBasedStrategyInterpreter()


def _extract_output_text(payload: dict[str, Any]) -> str:
    direct_output = payload.get("output_text")
    if isinstance(direct_output, str) and direct_output.strip():
        return direct_output
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                return str(content.get("text") or "")
            if isinstance(content, dict) and content.get("type") == "text":
                return str(content.get("text") or "")
    raise ValueError("OpenAI response did not contain output_text")


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
        raise ValueError("OpenAI response JSON was not an object")
    return parsed


def _safe_excerpt(text: str | None, *, limit: int = 1200) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.replace("\x00", "").split())
    return cleaned[:limit]


def _instructions() -> str:
    return (
        "You convert a crypto spot-market setup request into a deterministic strategy draft. "
        "Return only JSON matching the strict strategy schema. Do not invent market data, "
        "indicator values, results, profits, predictions, win rates, exchange API keys, wallet "
        "actions, or trade-execution instructions. The user makes all trading decisions. "
        "For every condition, select an immutable capability_key only from the candidate keys "
        "provided in capability_resolution. Never invent an operand, indicator, pattern, metric, "
        "or capability key. Return parameters separately and let the backend build mechanics. "
        "Treat verified_capability_bindings as backend-approved interpretations of their source "
        "fragments and preserve them in the strategy. "
        "If no candidate represents a phrase, put it in ambiguities with a direct clarification "
        "question. If a candidate is provider-required or unavailable, put it in "
        "unsupported_conditions with blocking=true "
        "when the user made them mandatory and blocking=false when they were optional or bonus "
        "confirmation. Separate required and optional rules using the condition mandatory flag. "
        "Condition-tree operators available are AND, OR, NOT, SEQUENCE, WITHIN_LAST, "
        "PERSISTED_FOR, COUNT_OF, COOLDOWN_CONDITION, FIRST_TIME_TRUE, CHANGED_STATE, "
        "CROSS_WITH_CONFIRMATION, and CONDITIONAL_BRANCH. Include an operator parameters object "
        "when a temporal or count operator needs configuration. "
        "Convert trader slang into known capabilities: liquidity sweep, RSI pullback, VWAP "
        "reclaim, Bollinger squeeze, MACD cross, moving-average crossover, breakout retest, "
        "support/resistance rejection, session time filter, and optional confirmation. "
        "For finder prompts such as 'grew 5% today', 'gained 3% this week', or "
        "'dropped 2% in the last 24h', create percent_change_up or percent_change_down "
        "price_action mechanics with threshold_percent and lookback; never convert a percent "
        "move into a dollar price threshold. "
        "Preserve user intent, include assumptions and ambiguity questions, prefer light-scan "
        "compatible defaults for Quick Scan, and never contradict deterministic evaluation. "
        "Do not drop any user instruction. Every instruction must appear as an executable "
        "condition, a declared assumption, an ambiguity, or an unsupported_conditions item. "
        "If a user asks for a candle state filter such as previous bullish candle, consecutive "
        "red candles, doji, hammer, or absence of a pattern inside a lookback window, express it "
        "with candle_pattern or candle_anatomy mechanics when possible. "
        "Return JSON only."
    )


def _strategy_draft_schema(candidate_keys: list[str] | None = None) -> dict[str, Any]:
    capability_key_schema: dict[str, Any] = {"type": "string"}
    if candidate_keys:
        capability_key_schema["enum"] = sorted(set(candidate_keys))
    condition = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "capability_key": capability_key_schema,
            "key": {"type": "string"},
            "label": {"type": "string"},
            "condition_type": {
                "type": "string",
                "enum": ["indicator", "price_action", "candle_pattern", "market_filter", "risk"],
            },
            "condition_id": {"type": "string"},
            "name": {"type": "string"},
            "type": {
                "type": "string",
                "enum": ["indicator", "price_action", "candle_pattern", "market_filter", "risk"],
            },
            "operator": {
                "type": "string",
                "enum": [
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "eq",
                    "crosses_above",
                    "crosses_below",
                    "is_true",
                    "is_false",
                ],
            },
            "threshold": {},
            "timeframe": {"type": "string"},
            "left": {"type": "object", "additionalProperties": True},
            "right": {"type": ["object", "null"], "additionalProperties": True},
            "indicator": {"type": "string"},
            "metric": {"type": "string"},
            "pattern": {"type": "string"},
            "operand_kind": {"type": "string"},
            "field": {"type": "string"},
            "parameters": {"type": "object", "additionalProperties": True},
            "weight": {"type": "number"},
            "mandatory": {"type": "boolean"},
            "required_data": {"type": "array", "items": {"type": "string"}},
            "explanation_template": {"type": "string"},
            "forming_tolerance_percent": {"type": "number"},
            "source_fragment": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "ai_interpreted": {"type": "boolean"},
            "provider_required": {"type": "boolean"},
            "availability": {"type": "string"},
        },
        "required": [
            "capability_key",
            "condition_id",
            "name",
            "type",
            "operator",
            "timeframe",
            "source_fragment",
            "confidence",
        ],
    }
    issue = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "code": {"type": "string"},
            "message": {"type": "string"},
            "field": {"type": ["string", "null"]},
            "options": {"type": "array", "items": {"type": "string"}},
            "blocking": {"type": "boolean"},
            "source_fragment": {"type": "string"},
        },
        "required": ["code", "message", "blocking", "source_fragment"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strategy_name": {"type": "string"},
            "description": {"type": "string"},
            "direction": {"type": "string", "enum": ["long", "short", "both"]},
            "market_type": {"type": "string", "enum": ["spot"]},
            "exchanges": {"type": "array", "items": {"type": "string"}},
            "quote_assets": {"type": "array", "items": {"type": "string"}},
            "symbols": {"type": "array", "items": {"type": "string"}},
            "excluded_symbols": {"type": "array", "items": {"type": "string"}},
            "primary_timeframe": {"type": "string"},
            "higher_timeframes": {"type": "array", "items": {"type": "string"}},
            "trigger_mode": {"type": "string", "enum": ["candle_close", "intrabar"]},
            "logic": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "operator": {
                        "type": "string",
                        "enum": [
                            "AND",
                            "OR",
                            "NOT",
                            "SEQUENCE",
                            "WITHIN_LAST",
                            "PERSISTED_FOR",
                            "COUNT_OF",
                            "COOLDOWN_CONDITION",
                            "FIRST_TIME_TRUE",
                            "CHANGED_STATE",
                            "CROSS_WITH_CONFIRMATION",
                            "CONDITIONAL_BRANCH",
                            "and",
                            "or",
                            "not",
                            "sequence",
                            "within_last",
                            "persisted_for",
                            "count_of",
                            "cooldown_condition",
                            "first_time_true",
                            "changed_state",
                            "cross_with_confirmation",
                            "conditional_branch",
                        ],
                    },
                    "parameters": {"type": "object", "additionalProperties": True},
                    "conditions": {"type": "array", "items": condition},
                },
                "required": ["operator", "conditions"],
            },
            "entry": {"type": "object", "additionalProperties": True},
            "stop": {"type": "object", "additionalProperties": True},
            "targets": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "risk_rules": {"type": "object", "additionalProperties": True},
            "liquidity_rules": {"type": "object", "additionalProperties": True},
            "near_miss_rules": {"type": "object", "additionalProperties": True},
            "alert_rules": {"type": "object", "additionalProperties": True},
            "expiry_rules": {"type": "object", "additionalProperties": True},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "ambiguities": {"type": "array", "items": issue},
            "unsupported_conditions": {"type": "array", "items": issue},
        },
        "required": [
            "strategy_name",
            "direction",
            "market_type",
            "exchanges",
            "quote_assets",
            "primary_timeframe",
            "trigger_mode",
            "logic",
            "risk_rules",
            "alert_rules",
            "assumptions",
            "ambiguities",
            "unsupported_conditions",
        ],
    }
