"""The bounded Setup Agent: read the whole turn, then say what actually happened.

The path this replaces decided what a message *was* with regular expressions before
the model saw it, gave that one label, and answered anything it did not recognise with
a fixed sentence:

    I'm ready. Describe the market behavior you want to scan or monitor.

A user who had just written three lines of exact market logic got told to describe a
setup. That is the defect this module exists to remove.

Every arbitrary free-text turn reaches the planner first. Deterministic readers run
only after planning to verify the proposed operations. A mixed turn may use one bounded
composer call after canonical execution; simple mutations use an evidence-only summary.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any, cast

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.capability_shortlist import (
    CapabilityShortlist,
    build_capability_shortlist,
    configured_runtime_provider_requirements,
)
from ai_market_monitor.engine.setup_turn_execution import (
    ProviderGate,
    RuntimePreflight,
    ScreeningGate,
    SetupTurnRejected,
    SetupTurnRequest,
    apply_setup_turn,
    conversation_from_segments,
    validated_clarification,
)
from ai_market_monitor.engine.strategy_draft_v2 import validate_draft_semantics
from ai_market_monitor.engine.timeframes import SUPPORTED_TIMEFRAMES
from ai_market_monitor.schemas.setup_agent import (
    DIALOGUE_WINDOW_MAX,
    CapabilityRoutingCandidate,
    CapabilityRoutingContext,
    SegmentKind,
    SetupAgentPlanEnvelope,
    SetupAgentReply,
    SetupAgentTurnPlan,
    SetupConversationContext,
    SetupTurnExecutionResult,
)
from ai_market_monitor.schemas.setup_authorization import ClarificationContract
from ai_market_monitor.schemas.strategy_draft_v2 import (
    FORMULA_CONTRACTS,
    ConditionNodeType,
    DraftMode,
    StrategyDraftV2,
)
from ai_market_monitor.services.ai_model_routing import select_setup_model
from ai_market_monitor.services.openai_structured_call import (
    StructuredCallError,
    estimate_structured_call_cost,
    structured_call,
)


class SetupAgentError(ValueError):
    """A turn that could not be completed, with the stage that failed named.

    The stage matters: a planning failure, a refused plan, a compile refusal and a
    composing failure need different handling, and collapsing them lost the draft or
    told the user their message was small talk.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str,
        retryable: bool = False,
        details: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.details = details


@dataclass(frozen=True, slots=True)
class SetupAgentTurnInput:
    """One authenticated free-text turn and the context needed to understand it."""

    #: Exactly what the user typed: line breaks, list bullets and spacing intact.
    #:
    #: Collapsing whitespace before the model saw the turn destroyed the structure that
    #: tells a numbered list of three rules apart from one run-on sentence, and made the
    #: stored provenance disagree with what the user could see they had written.
    message: str
    source_turn_id: str
    draft: StrategyDraftV2
    #: Recent user/assistant messages, oldest first, **excluding this turn** — it is
    #: already supplied as `current_user_turn`, and sending it twice invited the model
    #: to treat its own echo as prior context.
    dialogue: tuple[dict[str, str], ...] = ()
    conversation: SetupConversationContext = field(default_factory=SetupConversationContext)
    history: tuple[dict[str, Any], ...] = ()
    setup_mode: DraftMode = DraftMode.MONITOR
    #: True when the previous turn failed, so this one routes to the better model.
    previous_turn_failed: bool = False
    #: Final gates, supplied by the service that owns a database session.
    screening: ScreeningGate | None = None
    providers: ProviderGate | None = None
    runtime_preflight: RuntimePreflight | None = None
    stage_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None

    @property
    def normalized_message(self) -> str:
        """A whitespace-collapsed copy, for deterministic lexical helpers only.

        Never used for spans, provenance or anything the user sees.
        """
        return " ".join((self.message or "").split())


@dataclass(frozen=True, slots=True)
class SetupAgentTrace:
    """Redacted evidence for one turn. No hidden reasoning, no credentials."""

    source_turn_id: str
    planner_model: str = ""
    planner_reasons: tuple[str, ...] = ()
    planner_latency_ms: float = 0.0
    segments: tuple[dict[str, Any], ...] = ()
    plan_confidence: float = 0.0
    tool_called: bool = False
    patch_validation: str = "not_attempted"
    semantic_diff: tuple[str, ...] = ()
    compile_status: str = "not_attempted"
    response_model: str = ""
    response_latency_ms: float = 0.0
    failure_stage: str | None = None
    shortlist_keys: tuple[str, ...] = ()
    lexical_hint: str = ""
    model_calls: int = 0
    #: A question the composer asked for that the server did not authorise.
    dropped_clarification: str | None = None
    #: The model's own summaries, kept as diagnostics only. They are never the evidence
    #: for a success claim: that comes from the canonical before/after diff.
    model_intent_summaries: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.source_turn_id,
            "planner_model": self.planner_model,
            "planner_route_reasons": list(self.planner_reasons),
            "planner_latency_ms": round(self.planner_latency_ms, 3),
            "segments": list(self.segments),
            "plan_confidence": self.plan_confidence,
            "tool_called": self.tool_called,
            "patch_validation": self.patch_validation,
            "semantic_diff": list(self.semantic_diff),
            "compile_status": self.compile_status,
            "response_model": self.response_model,
            "response_latency_ms": round(self.response_latency_ms, 3),
            "failure_stage": self.failure_stage,
            "capability_shortlist": list(self.shortlist_keys),
            "lexical_hint": self.lexical_hint,
            "model_call_count": self.model_calls,
            "dropped_clarification": self.dropped_clarification,
            "model_intent_summaries_diagnostic_only": list(self.model_intent_summaries),
        }


@dataclass(frozen=True, slots=True)
class SetupAgentTurnResult:
    """Everything the caller needs to persist one completed turn."""

    reply: SetupAgentReply
    execution: SetupTurnExecutionResult | None
    draft: StrategyDraftV2
    conversation: SetupConversationContext
    plan: SetupAgentTurnPlan | None
    trace: SetupAgentTrace
    definition: Any | None = None
    history_snapshot: dict[str, Any] | None = None
    #: The server-authorised question this turn asked, if any.
    clarification: ClarificationContract | None = None
    #: True only when the canonical draft materially changed, so the caller knows
    #: whether to archive a previous approval.
    material_change: bool = False
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        if self.clarification is None:
            return self.reply.message_without_question
        return (
            f"{self.reply.message_without_question.rstrip()}\n\n"
            f"{self.clarification.question}"
        )


class SetupChatAgent:
    """One specialized agent with exactly one state-changing tool."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.model_call_count = 0
        self.last_usage: dict[str, Any] = {}
        self._circuit_redis: Redis | None = (
            None
            if settings.app_env == "test"
            else Redis.from_url(settings.redis_url, decode_responses=True)
        )
        self._local_circuit: dict[str, tuple[int, float]] = {}

    def _circuit_key(self, model: str) -> str:
        provider = (
            f"{str(self.settings.openai_base_url).rstrip('/')}:"
            f"{model}"
        )
        digest = hashlib.sha256(provider.encode("utf-8")).hexdigest()[:24]
        return f"hm:setup-agent:circuit:{digest}"

    async def _before_provider_call(self, model: str) -> None:
        if self._circuit_redis is None:
            self._before_local_provider_call(model)
            return
        cooldown = self.settings.setup_agent_circuit_breaker_cooldown_seconds
        script = """
        local state = redis.call('HGET', KEYS[1], 'state') or 'CLOSED'
        if state == 'CLOSED' then
          return 1
        end
        if state == 'HALF_OPEN' then
          return 0
        end
        local opened_at = tonumber(redis.call('HGET', KEYS[1], 'opened_at') or '0')
        if (tonumber(ARGV[1]) - opened_at) < tonumber(ARGV[2]) then
          return 0
        end
        redis.call('HSET', KEYS[1], 'state', 'HALF_OPEN')
        redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
        return 1
        """
        try:
            allowed = await cast(
                Awaitable[Any],
                self._circuit_redis.eval(
                    script,
                    1,
                    self._circuit_key(model),
                    str(time.time()),
                    str(cooldown),
                    str(max(cooldown * 3, 300)),
                ),
            )
        except RedisError:
            # Redis coordinates workers but is not semantic authority. A cache outage
            # falls back to a conservative process-local breaker and still permits a
            # healthy provider call.
            self._before_local_provider_call(model)
            return
        if not bool(allowed):
            raise StructuredCallError(
                "SETUP_AGENT_CIRCUIT_OPEN",
                "Setup interpretation is temporarily unavailable. Your draft is unchanged.",
                retryable=True,
                stage="provider",
            )

    def _before_local_provider_call(self, model: str) -> None:
        local_key = self._circuit_key(model)
        failures, opened_at = self._local_circuit.get(local_key, (0, 0.0))
        cooldown = self.settings.setup_agent_circuit_breaker_cooldown_seconds
        if (
            failures >= self.settings.setup_agent_circuit_breaker_failures
            and time.time() - opened_at < cooldown
        ):
            raise StructuredCallError(
                "SETUP_AGENT_CIRCUIT_OPEN",
                "Setup interpretation is temporarily unavailable. Your draft is unchanged.",
                retryable=True,
                stage="provider",
            )

    async def _provider_succeeded(self, model: str) -> None:
        self._local_circuit.pop(self._circuit_key(model), None)
        if self._circuit_redis is None:
            return
        try:
            await self._circuit_redis.delete(self._circuit_key(model))
        except RedisError:
            # The provider result is already complete and authoritative. Losing the
            # success marker may leave the circuit conservative for a later turn, but
            # must never discard this result or cause a second paid model call.
            return

    async def _provider_failed(self, exc: StructuredCallError, model: str) -> None:
        if not exc.retryable:
            return
        local_key = self._circuit_key(model)
        failures, _opened_at = self._local_circuit.get(local_key, (0, 0.0))
        self._local_circuit[local_key] = (failures + 1, time.time())
        if self._circuit_redis is None:
            return
        threshold = self.settings.setup_agent_circuit_breaker_failures
        cooldown = self.settings.setup_agent_circuit_breaker_cooldown_seconds
        script = """
        local failures = redis.call('HINCRBY', KEYS[1], 'failures', 1)
        local state = redis.call('HGET', KEYS[1], 'state') or 'CLOSED'
        if state == 'HALF_OPEN' or failures >= tonumber(ARGV[1]) then
          redis.call(
            'HSET',
            KEYS[1],
            'state',
            'OPEN',
            'opened_at',
            ARGV[2]
          )
        end
        redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
        return failures
        """
        try:
            await cast(
                Awaitable[Any],
                self._circuit_redis.eval(
                    script,
                    1,
                    self._circuit_key(model),
                    str(threshold),
                    str(time.time()),
                    str(max(cooldown * 3, 300)),
                ),
            )
        except RedisError:
            # Preserve the original provider classification. Redis health is diagnosed
            # separately and must not turn a provider timeout into another error.
            return

    async def run_turn(self, turn: SetupAgentTurnInput) -> SetupAgentTurnResult:
        self.model_call_count = 0
        self.last_usage = {}
        shortlist = build_capability_shortlist(
            turn.normalized_message,
            available_provider_requirements=configured_runtime_provider_requirements(
                self.settings.market_data_provider
            ),
        )
        started = monotonic()
        route = select_setup_model(
            self.settings,
            current_message=turn.normalized_message,
            history=list(turn.dialogue),
            active_clarification=(
                {"question": turn.conversation.question_text}
                if turn.conversation.active_question_id
                else None
            ),
            capability_context=_routing_context(shortlist),
            draft_condition_count=_condition_count(turn.draft),
            unresolved_field_count=len(turn.draft.unresolved_fields),
            previous_turn_failed=turn.previous_turn_failed,
        )
        try:
            envelope, plan_usage = await self._plan_once(turn, shortlist, route)
        except StructuredCallError as exc:
            raise SetupAgentError(
                exc.code,
                str(exc),
                stage="planning",
                retryable=exc.retryable,
                details=exc.details,
            ) from exc
        planner_model = route.model
        planner_reasons = route.reasons
        plan_usage = {**plan_usage, **route.usage_metadata()}
        planner_latency = (monotonic() - started) * 1000
        # `_plan_once` already counted its model call. Counting again here would make
        # the per-turn cost and latency telemetry inaccurate.
        self.last_usage = plan_usage

        trace = SetupAgentTrace(
            source_turn_id=turn.source_turn_id,
            planner_model=planner_model,
            planner_reasons=planner_reasons,
            planner_latency_ms=planner_latency,
            plan_confidence=envelope.plan.overall_confidence if envelope.plan else 1.0,
            segments=tuple(
                {
                    "segment_id": item.segment_id,
                    "kind": item.kind.value,
                    "text": item.exact_source_text,
                    "confidence": item.confidence,
                    "action_required": item.action_required,
                }
                for item in (envelope.plan.segments if envelope.plan else ())
            ),
            shortlist_keys=tuple(sorted(shortlist.allowed_keys)),
            lexical_hint="",
            model_calls=self.model_call_count,
        )

        plan = envelope.plan
        if turn.stage_callback is not None:
            await turn.stage_callback(
                "EXECUTING" if plan is not None and plan.requires_tool else "COMPOSING",
                {
                    "planner_model": planner_model,
                    "plan": plan.model_dump(mode="json") if plan is not None else None,
                },
            )
        if plan is None or not plan.requires_tool:
            # Pure conversation. No tool, no new version, no status change, and still a
            # real answer. A greeting cannot touch approval because it never gets here.
            reply = SetupAgentReply(
                message_without_question=_trimmed(envelope.direct_reply)
                or _deterministic_conversation_reply(turn.draft),
            )
            return SetupAgentTurnResult(
                reply=reply,
                execution=None,
                draft=turn.draft,
                conversation=conversation_from_segments(
                    turn.conversation,
                    list(plan.segments) if plan else [],
                    assistant_summary=reply.message,
                ),
                plan=plan,
                trace=trace,
                usage=self.last_usage,
            )

        try:
            outcome = await apply_setup_turn(
                SetupTurnRequest(
                    plan=plan,
                    # The raw message: spans are located in what the user actually typed.
                    message=turn.message,
                    draft=turn.draft,
                    source_turn_id=turn.source_turn_id,
                    allowed_capability_keys=shortlist.allowed_keys,
                    history=list(turn.history),
                    conversation=turn.conversation,
                    screening=turn.screening,
                    providers=turn.providers,
                    runtime_preflight=turn.runtime_preflight,
                )
            )
        except SetupTurnRejected as exc:
            raise SetupAgentError(
                exc.code,
                str(exc),
                stage="tool_validation",
                details=exc.details,
            ) from exc

        trace = _with(
            trace,
            tool_called=True,
            patch_validation="accepted",
            semantic_diff=tuple(outcome.result.semantic_diff),
            compile_status=outcome.result.compile_status,
        )
        if turn.stage_callback is not None:
            await turn.stage_callback(
                "COMPOSING",
                {
                    "planner_model": planner_model,
                    "plan": plan.model_dump(mode="json"),
                    "execution_result": outcome.result.model_dump(mode="json"),
                    "draft_after": outcome.draft.model_dump(mode="json"),
                    "conversation_after": outcome.conversation.model_dump(mode="json"),
                    "definition": (
                        outcome.definition.model_dump(mode="json")
                        if outcome.definition is not None
                        else None
                    ),
                    "history_snapshot": outcome.history_snapshot,
                    "material_change": outcome.material_change,
                },
            )

        composed_started = monotonic()
        reply = SetupAgentReply(
            message_without_question=deterministic_summary(outcome.result),
            selected_clarification_id=(
                outcome.result.allowed_clarifications[0].question_id
                if outcome.result.allowed_clarifications
                else None
            ),
        )
        response_model = "deterministic_summary"
        if _requires_contextual_composer(plan, outcome.result):
            try:
                reply, composer_usage = await self._compose_once(
                    turn,
                    plan,
                    outcome.result,
                    model=route.model,
                    planner_usage=plan_usage,
                )
            except StructuredCallError as exc:
                # Execution is already canonical and checkpointed. Composition failure
                # can only affect wording, so return the exact deterministic fallback
                # once and never replay mutation or make another model call.
                self.last_usage = {
                    **self.last_usage,
                    "_setup_composer_failure": exc.code,
                }
                response_model = "deterministic_fallback"
            else:
                self.last_usage = _merged_usage(self.last_usage, composer_usage)
                response_model = route.model
        trace = _with(
            trace,
            response_model=response_model,
            response_latency_ms=(monotonic() - composed_started) * 1000,
            model_calls=self.model_call_count,
        )
        conversation = outcome.conversation
        # The composer may only ask a question the server put on the list. An id it
        # invented, or one already answered, is dropped rather than persisted.
        chosen = validated_clarification(outcome.result, reply.selected_clarification_id)
        if chosen is not None:
            conversation = conversation.with_question(chosen)
        elif reply.selected_clarification_id:
            trace = _with(trace, dropped_clarification=reply.selected_clarification_id)
        final_message = (
            f"{reply.message_without_question.rstrip()}\n\n{chosen.question}"
            if chosen is not None
            else reply.message_without_question
        )
        conversation = conversation.model_copy(
            update={"last_assistant_summary": final_message[:1000]}
        )
        return SetupAgentTurnResult(
            reply=reply,
            execution=outcome.result,
            draft=outcome.draft,
            conversation=conversation,
            plan=plan,
            trace=trace,
            definition=outcome.definition,
            history_snapshot=outcome.history_snapshot,
            clarification=chosen,
            material_change=outcome.material_change,
            usage=self.last_usage,
        )

    async def _plan_once(
        self,
        turn: SetupAgentTurnInput,
        shortlist: CapabilityShortlist,
        route: Any,
    ) -> tuple[SetupAgentPlanEnvelope, dict[str, Any]]:
        """Make at most one bounded structured model call for a free-text turn."""
        planner_payload = self._planner_payload(turn, shortlist)
        reserved_cost = estimate_structured_call_cost(
            self.settings,
            instructions=_PLANNER_INSTRUCTIONS,
            payload=planner_payload,
            model=route.model,
            max_output_tokens=self.settings.setup_agent_planner_max_output_tokens,
        )
        if reserved_cost > self.settings.setup_agent_max_estimated_cost_usd_per_turn:
            raise StructuredCallError(
                "SETUP_AGENT_COST_LIMIT",
                "The planner call would exceed the configured per-turn AI budget.",
                stage="planning",
            )
        try:
            await self._before_provider_call(route.model)
            envelope, usage = await structured_call(
                self.settings,
                schema_model=SetupAgentPlanEnvelope,
                schema_name="hilalmarkets_setup_turn_plan",
                instructions=_PLANNER_INSTRUCTIONS,
                payload=planner_payload,
                model=route.model,
                reasoning_effort=route.reasoning_effort,
                max_output_tokens=self.settings.setup_agent_planner_max_output_tokens,
                timeout_seconds=self.settings.setup_agent_planner_timeout_seconds,
                estimated_cost_limit=self.settings.setup_agent_max_estimated_cost_usd_per_turn,
                stage="planning",
                transport=self.transport,
            )
        except StructuredCallError as exc:
            self.model_call_count = 1
            if _counts_toward_circuit(exc):
                await self._provider_failed(exc, route.model)
            else:
                await self._provider_succeeded(route.model)
            raise
        self.model_call_count = 1
        await self._provider_succeeded(route.model)
        return envelope, {
            **usage,
            "_setup_reserved_cost_usd": reserved_cost,
            "_setup_planner_attempts": 1,
        }

    async def _compose_once(
        self,
        turn: SetupAgentTurnInput,
        plan: SetupAgentTurnPlan,
        result: SetupTurnExecutionResult,
        *,
        model: str,
        planner_usage: dict[str, Any],
    ) -> tuple[SetupAgentReply, dict[str, Any]]:
        payload = {
            "final_execution_result": result.model_dump(mode="json"),
            "operation_specific_diffs": [
                item.model_dump(mode="json") for item in result.operation_results
            ],
            "final_compiled_status": result.compile_status,
            "screening_status": result.screening_status,
            "provider_status": result.provider_status,
            "final_chat_status": result.final_chat_status,
            "draft_read_model": result.draft_read_model,
            "response_points": [
                item.model_dump(mode="json") for item in plan.response_points
            ],
            "questions_to_answer": list(plan.questions_to_answer),
            "grounded_product_knowledge": product_knowledge(),
            "authorized_clarification_list": [
                item.model_dump(mode="json")
                for item in result.allowed_clarifications
            ],
            "user_message": turn.message,
        }
        reserved = estimate_structured_call_cost(
            self.settings,
            instructions=_COMPOSER_INSTRUCTIONS,
            payload=payload,
            model=model,
            max_output_tokens=self.settings.setup_agent_composer_max_output_tokens,
        )
        planner_reserved = float(
            planner_usage.get("_setup_reserved_cost_usd") or 0.0
        )
        if (
            planner_reserved + reserved
            > self.settings.setup_agent_max_estimated_cost_usd_per_turn
        ):
            raise StructuredCallError(
                "SETUP_AGENT_COST_LIMIT",
                "Contextual wording would exceed the configured per-turn AI budget.",
                stage="response_composition",
            )
        try:
            await self._before_provider_call(model)
            reply, usage = await structured_call(
                self.settings,
                schema_model=SetupAgentReply,
                schema_name="hilalmarkets_setup_turn_reply",
                instructions=_COMPOSER_INSTRUCTIONS,
                payload=payload,
                model=model,
                reasoning_effort="low",
                max_output_tokens=self.settings.setup_agent_composer_max_output_tokens,
                timeout_seconds=self.settings.setup_agent_composer_timeout_seconds,
                estimated_cost_limit=max(
                    0.000001,
                    self.settings.setup_agent_max_estimated_cost_usd_per_turn
                    - planner_reserved,
                ),
                stage="response_composition",
                transport=self.transport,
            )
        except StructuredCallError as exc:
            self.model_call_count += 1
            if _counts_toward_circuit(exc):
                await self._provider_failed(exc, model)
            else:
                await self._provider_succeeded(model)
            raise
        self.model_call_count += 1
        await self._provider_succeeded(model)
        if not _composer_reply_is_grounded(reply, result, payload):
            raise StructuredCallError(
                "COMPOSER_FACT_NOT_GROUNDED",
                "The contextual reply introduced a fact outside the execution evidence.",
                stage="response_composition",
            )
        merged = _merged_usage(
            usage,
            {
                "_setup_composer_attempts": 1,
                "_setup_composer_reserved_cost_usd": reserved,
            },
        )
        combined_actual = _estimated_usage_cost(
            _merged_usage(planner_usage, usage),
            self.settings,
            model,
        )
        merged["_setup_combined_actual_cost_usd"] = combined_actual
        return reply, merged

    def _planner_payload(
        self,
        turn: SetupAgentTurnInput,
        shortlist: CapabilityShortlist,
    ) -> dict[str, Any]:
        draft = turn.draft
        return {
            "current_user_turn": turn.message,
            "source_turn_id": turn.source_turn_id,
            "recent_dialogue": list(turn.dialogue)[-DIALOGUE_WINDOW_MAX:],
            "setup_mode": turn.setup_mode.value,
            "draft": {
                "draft_id": str(draft.draft_id),
                "executable_version": draft.executable_version,
                "workflow_revision": draft.workflow_revision,
                "name": draft.name,
                "included_symbols": draft.universe.included_symbols[:50],
                "excluded_symbols": draft.universe.excluded_symbols[:50],
                "market_scope": draft.market_scope.model_dump(mode="json"),
                "sharia_policy": draft.sharia_policy.model_dump(mode="json"),
                "conditions": _condition_labels(draft),
                "boolean_shape": _boolean_shape(draft),
            },
            "unresolved_fields": [
                item.model_dump(mode="json")
                for item in draft.unresolved_fields
            ],
            "unsupported_requirements": [
                {"key": item.key, "missing": item.missing_contract}
                for item in draft.unsupported_requirements
            ],
            "recent_semantic_diff": list(turn.conversation.last_changed_condition_ids)[:12],
            "available_snapshots": [
                {
                    "snapshot_id": str(item.get("snapshot_id") or ""),
                    "executable_version": int(item.get("executable_version") or 0),
                }
                for item in turn.history[-20:]
                if item.get("snapshot_id") and item.get("executable_version")
            ],
            "conversation_context": turn.conversation.model_dump(mode="json"),
            "approval_eligible": draft.approval_eligible,
            "semantic_violations": validate_draft_semantics(draft),
            "core_primitives": _core_primitives(),
            "capability_shortlist": shortlist.to_prompt_dict(),
            "product_boundaries": _PRODUCT_BOUNDARIES,
            "product_knowledge": product_knowledge(),
            "operation_kinds": _OPERATION_GUIDE,
        }


def _requires_contextual_composer(
    plan: SetupAgentTurnPlan,
    result: SetupTurnExecutionResult,
) -> bool:
    """Use a second bounded call only when factual summary cannot answer the turn."""

    reply_bearing_kinds = {
        SegmentKind.SOCIAL_REPLY,
        SegmentKind.CONVERSATIONAL_CONTEXT,
        SegmentKind.USER_QUESTION,
        SegmentKind.PRODUCT_QUESTION,
        SegmentKind.EXPLANATION_REQUEST,
        SegmentKind.UNSUPPORTED_REQUEST,
    }
    return bool(
        plan.questions_to_answer
        or len(plan.response_points) > 1
        or any(item.kind in reply_bearing_kinds for item in plan.segments)
        or any(item.kind == "explain_refusal" for item in plan.response_points)
        or plan.unsupported_segments
        or result.safe_errors
    )


def deterministic_summary(result: SetupTurnExecutionResult) -> str:
    """A factual reply built only from what the server did.

    Used when composing fails after a successful execution. Plain, not templated
    small talk, and never a claim the result does not support.
    """

    lines: list[str] = []
    if result.applied_instructions:
        lines.append("I applied this:")
        lines.extend(f"- {item.summary}" for item in result.applied_instructions[:6])
    elif result.status == "no_change":
        lines.append("Nothing in the draft needed to change for that.")
    if result.answered_questions:
        lines.append("That answered the open question.")
    if result.strategy_mutated:
        lines.append(f"The draft is now version {result.current_version}.")
    for item in result.unsupported_requirements[:3]:
        lines.append(f"I could not express this exactly: {item.get('missing_contract', '')}")
    for item in result.unresolved_fields[:1]:
        lines.append(f"Still needed: {item.get('question', '')}")
    lines.extend(result.safe_errors[:2])
    if result.approval_eligible:
        lines.append("The inactive preview is ready. Use Review and approve when it matches.")
    elif result.approval_status == "invalidated_by_edit":
        lines.append(
            "That edit created a new version, so it needs approving again before it can run."
        )
    return "\n".join(lines) or "Nothing changed on this turn."


def _deterministic_conversation_reply(draft: StrategyDraftV2) -> str:
    """Last-resort words for a conversation turn the model left empty.

    Deliberately reports the real state instead of asking the user to start over.
    """

    count = _condition_count(draft)
    if count == 0:
        return (
            "Nothing is set up yet. Tell me the market behaviour you want followed and "
            "I will turn it into exact rules."
        )
    return (
        f"The draft currently holds {count} rule{'s' if count != 1 else ''} "
        f"at version {draft.version}. Tell me what to change, or ask about any of them."
    )


def _condition_count(draft: StrategyDraftV2) -> int:
    if draft.condition_ast is None:
        return 0
    return sum(
        node.node_type == ConditionNodeType.CONDITION for node in draft.condition_ast.walk()
    )


def _condition_labels(draft: StrategyDraftV2) -> list[dict[str, Any]]:
    """Short, stable labels so the model can refer to a rule the user means."""

    if draft.condition_ast is None:
        return []
    labels: list[dict[str, Any]] = []
    for node in draft.condition_ast.walk():
        if node.node_type != ConditionNodeType.CONDITION:
            continue
        labels.append(
            {
                "condition_id": node.node_id,
                "formula": node.formula.value if node.formula else None,
                "operator": node.operator.value if node.operator else None,
                "threshold": node.threshold,
                "unit": node.unit,
                "movement_direction": node.movement_direction.value,
                "strategy_bias": node.strategy_bias.value,
                "trigger_timeframe": node.trigger_timeframe,
                "context_timeframes": list(node.context_timeframes),
                "confirmation_timeframes": list(node.confirmation_timeframes),
                "capability_key": node.capability_key,
                "said_by_user": node.source_fragment,
            }
        )
    return labels[:40]


def _boolean_shape(draft: StrategyDraftV2) -> str:
    def shape(node: Any) -> str:
        if not node.children:
            return node.node_id
        return (
            f"{node.node_type.value}("
            + ", ".join(shape(child) for child in node.children)
            + ")"
        )

    return shape(draft.condition_ast) if draft.condition_ast is not None else ""


def _core_primitives() -> dict[str, Any]:
    """What the deterministic compiler can express without any capability key."""

    return {
        "formulas": {
            name.value: {
                "operators": sorted(item.value for item in contract.operators),
                "units": sorted(contract.units),
                "cannot_measure": sorted(item.value for item in contract.forbidden_directions),
            }
            for name, contract in FORMULA_CONTRACTS.items()
            if name.value != "capability"
        },
        "timeframes": sorted(SUPPORTED_TIMEFRAMES),
        "timeframe_roles": ["trigger", "context", "confirmation", "reference"],
        "boolean": ["and", "or", "not"],
        "universe": ["include symbol", "exclude symbol", "exchange", "quote asset", "spot only"],
        "rule": (
            "A formula may only carry the operators and units listed for it. If the "
            "request needs something else, it is unsupported, not approximated."
        ),
    }


def _routing_context(shortlist: CapabilityShortlist) -> CapabilityRoutingContext:
    """The shortlist's shape, so the router's ambiguity signals can actually fire.

    Passing only the keys left `low_capability_confidence` and `custom_terminology`
    permanently unreachable, so a turn with unknown wording or two near-equal candidates
    was priced as a simple one.
    """
    scores = [item.score for item in shortlist.candidates]
    top = max(scores, default=0.0)
    margin = (top - sorted(scores, reverse=True)[1]) if len(scores) > 1 else top
    ordered = sorted(shortlist.candidates, key=lambda item: item.score, reverse=True)
    return CapabilityRoutingContext(
        candidate_keys=sorted(shortlist.allowed_keys),
        candidate_count=len(ordered),
        unknown_terms=list(shortlist.unknown_terms),
        top_candidate_key=ordered[0].capability_key if ordered else None,
        top_candidate_score=top,
        selection_confidence=min(1.0, top / 100.0),
        top_score_margin=margin,
        candidates=[
            CapabilityRoutingCandidate(
                key=item.capability_key,
                score=item.score,
                normalized_confidence=min(1.0, item.score / 100.0),
                availability=item.availability,
                executable=item.executable,
                source_fragment=item.source_fragment,
            )
            for item in ordered
        ],
    )


#: Server-owned product facts, versioned so an answer can be traced to what was told.
#: The agent answers product questions from this, not from model memory.
_PRODUCT_KNOWLEDGE_VERSION = "1.0"


def product_knowledge() -> dict[str, Any]:
    """Grounded answers to the product questions this chat actually gets asked."""

    return {
        "version": _PRODUCT_KNOWLEDGE_VERSION,
        "what_this_chat_does": (
            "It turns your own description of market behaviour into exact rules, shows "
            "them to you as an inactive preview, and never runs anything until you "
            "approve it yourself."
        ),
        "scanner_vs_monitor": (
            "A Scanner checks the market once, right now. A Monitor keeps watching and "
            "alerts you when the rules match."
        ),
        "how_approval_works": (
            "Approval is a separate control you press. It binds to the exact version of "
            "the draft you were shown, so any later edit needs approving again."
        ),
        "what_alerts_look_like": (
            "An alert names the coin, the rule that matched and the candle it matched "
            "on, in the app and on Telegram if you connect it."
        ),
        "sharia_status": (
            "Sharia status comes only from the platform's own governed review with its "
            "evidence. This chat never decides or guesses whether something is halal."
        ),
        "costs_nothing_to_preview": (
            "Building and previewing a draft changes nothing in the market and places "
            "no orders."
        ),
        "if_unsure": (
            "If a fact is not listed here, say you are not certain and offer to point "
            "the user at support rather than guessing."
        ),
    }


#: How each operation kind is used, so the planner emits authorised operations rather
#: than a free-floating patch.
_OPERATION_GUIDE = {
    "rule": (
        "Every change is one entry in `operations` with a stable unique operation_id, "
        "and every entry names the "
        "`authorizing_segment_id` of the STRATEGY_INSTRUCTION or CLARIFICATION_ANSWER "
        "segment that asked for it. A SOCIAL_REPLY, USER_QUESTION, PRODUCT_QUESTION, "
        "EXPLANATION_REQUEST, APPROVAL_INTENT or UNSUPPORTED_REQUEST segment can never "
        "authorize an operation, and every value in an operation must appear in that "
        "one segment's own text."
    ),
    "kinds": {
        "set_fields": "change the mode, name, exchange or quote asset",
        "set_sharia_policy": (
            "change the canonical static Sharia policy, including methodology, universe "
            "mode, statuses, watchlist identity, explicit symbols or drift behavior"
        ),
        "add_condition": "create one new rule",
        "update_condition": "change one existing rule, named by target_condition_id",
        "remove_condition": "delete one existing rule",
        "replace_groups": "replace the whole AND/OR/NOT structure",
        "add_inclusion": "add one symbol to the watchlist",
        "add_exclusion": "keep one symbol out",
        "remove_inclusion": "stop watching one symbol",
        "remove_exclusion": "stop excluding one symbol",
        "add_unresolved": "persist one typed missing value before asking about it",
        "update_unresolved": "replace one typed unresolved contract",
        "add_unsupported": "record a market rule the platform cannot express exactly",
        "resolve_unresolved_key": "close an open question by its exact key",
        "remove_unsupported_key": "drop an unsupported item by its exact key",
        "restore_snapshot": "request a server-verified immutable prior executable version",
    },
}


_PRODUCT_BOUNDARIES = {
    "can": [
        "build an inactive Scanner or Monitor preview from exact market rules",
        "explain what a rule measures and which words produced it",
        "answer questions about the product and about the current draft",
    ],
    "cannot": [
        "place, close or size a trade",
        "give buy or sell advice, price predictions or guaranteed outcomes",
        "assign or imply a Sharia, halal or haram status — that comes only from the "
        "platform's own governed review",
        "approve or activate anything; approval is a separate authenticated action",
        "use leverage or margin",
    ],
}


_PLANNER_INSTRUCTIONS = """\
You are the HilalMarkets Setup Chat planner for a beginner-friendly, Halal
crypto-monitoring product. You read one authenticated user turn and divide it into
segments. You never execute anything: a deterministic server tool applies whatever
survives its own checks.

DIVIDE THE TURN
A single message can do several things at once — greet, instruct, correct, ask. Split
it into segments. Each segment's exact_source_text must be a substring of the current
message, copied character for character — not a paraphrase, not a normalised copy, not
a quote from an earlier turn. The server searches the real message for that text; if it
is not there, the whole turn is refused. Do not spend effort on start_offset and
end_offset: give your best estimate, the server finds the real position itself. Never
let two actionable segments cover the same words.

Never force the whole message into one kind. Never discard technical content because
conversation surrounds it. Never turn conversation into a rule.

WHAT YOU MAY PROPOSE
Every change is one entry in `operations` with a unique stable operation_id, and each
entry names the
`authorizing_segment_id` of the segment that asked for it. Only a STRATEGY_INSTRUCTION
or a CLARIFICATION_ANSWER segment may authorize one. See operation_kinds for the list.

Every threshold, timeframe, symbol, operator, movement_direction, explicit strategy_bias
and formula in an operation must
appear in **that authorizing segment's own text** — not merely somewhere in the message.
A number written inside a question does not authorize a rule. The exception is an
`update_condition`: fields you leave unchanged are inherited from the rule you name, so
`change that to at least 8%` does not have to restate the timeframe.

If a value is not in the authorizing segment's words, do not supply one. Create an
add_unresolved operation with a typed target, answer schema and smallest useful
canonical question. Use condition_creation when the missing item is a whole rule.

For a registered mechanic, choose a capability_key from capability_shortlist and
nothing else. If no candidate expresses the request exactly, return an
add_unsupported operation with the user's own wording, or add one typed unresolved item.
Never invent a key. Never substitute a mechanic that is merely similar — a near miss
watches the wrong market and looks like success.

For the core primitives listed in core_primitives, use no capability_key at all.

CLARIFICATION ANSWERS
If conversation_context has an active_question_id and this turn answers it, record a
clarification_answers entry. An answer resolves that question; it does not become a
new condition. "yes" is not a market rule. A mutating answer must also include the
operation that fills the unresolved target, then resolve_unresolved_key.

REFERENCES
Use recent_dialogue and conversation_context to resolve "that one", "the second
option", "the one we just added", "make it stricter". Point at the existing
condition_id rather than rebuilding the rule. For undo or restore language, propose
restore_snapshot; the server, not you, resolves and verifies the history target.

target_condition_id names a condition that ALREADY EXISTS in draft.conditions. Leave it
null when you are creating a new rule — there is no id for a rule that does not exist
yet. To change an existing rule use update_condition with its exact node_id; to delete
one use remove_condition. An id that is not in draft.conditions is refused.

DIRECTION
movement_direction is up, down, neutral or not_applicable. strategy_bias is long,
short or neutral. Default strategy_bias to neutral unless the trader explicitly says
long or short. A falling market does not imply short, and a rising market does not
imply long.

APPROVAL
You may record approval_intent. You can never approve. Approval happens only through
the authenticated Review and approve control.

OUTPUT
Return a plan when the turn needs the server to change or re-check state. Return
direct_reply instead, with no plan, only when the turn is purely conversational and
nothing needs applying — and then write the reply yourself, in plain words, in the
user's language. Never tell a user to describe a setup when they have already given
you technical content. Never claim anything changed; that is decided after you.

Use response_points to record what the final reply must cover, including answers to
their questions and honest explanations of anything refused.
"""


_COMPOSER_INSTRUCTIONS = """\
You write the final assistant message for one HilalMarkets Setup Chat turn.

execution_result is what the server actually did, and it is your only source of fact.
State a change only if it appears there. If applied is false, do not imply anything
landed. If something was refused or unsupported, say so plainly and say why.

execution_result already includes every gate: compile_status, screening_status,
provider_status and final_chat_status. If screening blocked it or a provider is
unavailable, the draft is not ready — do not call it ready. If approval_status is
`approved`, the setup is still approved and nothing needs approving again.

Answer questions about the current draft from draft_read_model, and product questions
from product_knowledge. If a product fact is not in product_knowledge, say you are not
certain rather than guessing.

Cover every response_point and answer every entry in questions_to_answer. Acknowledge
the conversational parts of the turn briefly and naturally when there were any.

Write for a beginner in the user's own language. Short sentences, everyday words, no
field names, no error-template phrasing, no bullet lists unless they genuinely help.
Be concise unless the user asked for detail.

Write no clarification question inside message_without_question. To ask one, set
selected_clarification_id to the question_id of exactly one entry in
allowed_clarifications. The server appends that contract's canonical question after
validating the id. Ask nothing when allowed_clarifications is empty, and never ask the
user to describe their setup when they already have.

Never assign or imply a Sharia, halal or haram status. Never give trading advice,
predictions or guarantees. Never say the strategy is running or approved: approval is a
separate action the user takes with the Review and approve control, and everything here
is an inactive preview.
"""


def _merged_usage(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Add two Responses usage payloads so a turn reports its whole cost."""

    merged = dict(first)
    for key, value in second.items():
        if isinstance(value, int | float) and isinstance(merged.get(key), int | float):
            merged[key] = merged[key] + value
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merged_usage(merged[key], value)
        else:
            merged.setdefault(key, value)
    return merged


def _estimated_usage_cost(
    usage: dict[str, Any],
    settings: Settings,
    model: str,
) -> float:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    pricing = settings.openai_model_pricing_usd_per_million.get(model) or {}
    return (
        input_tokens * float(pricing.get("input", 0))
        + output_tokens * float(pricing.get("output", 0))
    ) / 1_000_000


_CIRCUIT_FAILURE_CODES = frozenset(
    {
        "TARGET_CONNECT_TIMEOUT",
        "TARGET_READ_TIMEOUT",
        "TARGET_TOTAL_TIMEOUT",
        "TARGET_PARTIAL_STREAM",
        "TARGET_DNS_RESOLUTION_FAILURE",
        "TARGET_CONNECTION_REFUSED",
        "TARGET_HTTP_429",
        "TARGET_HTTP_5XX",
    }
)


def _counts_toward_circuit(exc: StructuredCallError) -> bool:
    """Only routed provider availability failures affect the breaker."""

    return exc.code in _CIRCUIT_FAILURE_CODES


def _composer_reply_is_grounded(
    reply: SetupAgentReply,
    result: SetupTurnExecutionResult,
    payload: dict[str, Any],
) -> bool:
    """Reject obvious new facts; deterministic execution stays the authority."""

    message = reply.message_without_question
    lowered = message.casefold()
    if re.search(r"\b(?:halal|haram)\b|(?:حلال|حرام)", lowered):
        return False
    if re.search(r"\b(?:running|activated|active now|live now)\b", lowered):
        return False
    positive_ready = bool(
        re.search(r"\b(?:is|it's|it is|now|fully|setup is)\s+ready\b", lowered)
    )
    if positive_ready and not result.approval_eligible:
        return False
    positive_approved = bool(
        re.search(r"\b(?:is|it's|it is|now|has been)\s+approved\b", lowered)
    )
    if positive_approved and result.approval_status != "approved":
        return False
    if (
        re.search(r"\b(?:verified|provider available|runtime available)\b", lowered)
        and result.provider_status not in {"available", "not_required"}
    ):
        return False
    if re.search(r"\b(?:compiled|compile-ready)\b", lowered) and (
        result.compile_status != "compiled"
    ):
        return False
    if re.search(r"\b(?:screening passed|screened and ready)\b", lowered) and (
        result.screening_status not in {"passed", "not_required"}
    ):
        return False
    if not any(item.applied for item in result.operation_results) and re.search(
        r"\b(?:i|we)\s+(?:added|changed|updated|removed|set|applied)\b",
        lowered,
    ):
        return False

    # Numbers, explicit market pairs and timeframe tokens are the highest-risk facts
    # for a trading rule. Every such token must exist in the execution/read-model or
    # grounded product knowledge supplied to this exact composer call.
    evidence = json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
    risky_tokens = {
        *re.findall(r"(?<!\w)[+-]?\d+(?:\.\d+)?%?", lowered),
        *re.findall(r"\b[a-z0-9]{2,12}/[a-z0-9]{2,12}\b", lowered),
        *re.findall(
            r"\b(?:\d+\s*(?:m|h|d|w)|hourly|daily|weekly)\b",
            lowered,
        ),
    }
    return all(token in evidence for token in risky_tokens)


def _trimmed(value: str | None) -> str:
    return " ".join((value or "").split())


def _with(trace: SetupAgentTrace, **updates: Any) -> SetupAgentTrace:
    return replace(trace, **updates)


def planner_schema_json() -> str:
    """The plan schema, for the rebuild report and operator tooling."""

    from ai_market_monitor.services.agent_tools import strict_json_schema

    return json.dumps(strict_json_schema(SetupAgentPlanEnvelope), indent=2, sort_keys=True)
