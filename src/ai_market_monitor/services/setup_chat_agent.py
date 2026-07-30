"""The bounded Setup Agent: read the whole turn, then say what actually happened.

The path this replaces decided what a message *was* with regular expressions before
the model saw it, gave that one label, and answered anything it did not recognise with
a fixed sentence:

    I'm ready. Describe the market behavior you want to scan or monitor.

A user who had just written three lines of exact market logic got told to describe a
setup. That is the defect this module exists to remove.

Here the model is the first semantic layer and the server is the only executable
authority. One turn costs at most:

* one planning call — divide the turn into segments, propose at most one patch
* one deterministic execution — :func:`apply_setup_turn`, which can refuse anything
* one composing call — write the reply from what the server actually did

No tool loops, no second opinion, no fallback orchestrator. When planning succeeds but
composing fails, the reply is built deterministically from the execution result, so the
user still learns what changed instead of being reset to a greeting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.capability_shortlist import (
    CapabilityShortlist,
    build_capability_shortlist,
)
from ai_market_monitor.engine.setup_intent import decide_setup_intent
from ai_market_monitor.engine.setup_turn_execution import (
    SetupTurnOutcome,
    SetupTurnRejected,
    SetupTurnRequest,
    apply_setup_turn,
    conversation_from_segments,
)
from ai_market_monitor.engine.strategy_draft_v2 import validate_draft_semantics
from ai_market_monitor.engine.timeframes import SUPPORTED_TIMEFRAMES
from ai_market_monitor.schemas.setup_agent import (
    DIALOGUE_WINDOW_MAX,
    SetupAgentPlanEnvelope,
    SetupAgentReply,
    SetupAgentTurnPlan,
    SetupConversationContext,
    SetupTurnExecutionResult,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    FORMULA_CONTRACTS,
    ConditionNodeType,
    DraftMode,
    StrategyDraftV2,
)
from ai_market_monitor.services.ai_model_routing import select_setup_model
from ai_market_monitor.services.openai_structured_call import (
    StructuredCallError,
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

    message: str
    source_turn_id: str
    draft: StrategyDraftV2
    #: Recent user/assistant messages, oldest first. Bounded, never the full log.
    dialogue: tuple[dict[str, str], ...] = ()
    conversation: SetupConversationContext = field(default_factory=SetupConversationContext)
    history: tuple[dict[str, Any], ...] = ()
    setup_mode: DraftMode = DraftMode.MONITOR
    #: True when the previous turn failed, so this one routes to the better model.
    previous_turn_failed: bool = False


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
    usage: dict[str, Any] = field(default_factory=dict)


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

    async def run_turn(self, turn: SetupAgentTurnInput) -> SetupAgentTurnResult:
        self.model_call_count = 0
        self.last_usage = {}
        shortlist = build_capability_shortlist(turn.message)
        # The old authoritative gate survives only as a hint the model may disagree
        # with. It can no longer stop a message from being understood.
        hint = decide_setup_intent(turn.message)
        route = select_setup_model(
            self.settings,
            current_message=turn.message,
            history=list(turn.dialogue),
            active_clarification=(
                {"question": turn.conversation.question_text}
                if turn.conversation.active_question_id
                else None
            ),
            capability_context={"candidate_keys": sorted(shortlist.allowed_keys)},
            draft_condition_count=_condition_count(turn.draft),
            unresolved_field_count=len(turn.draft.unresolved_fields),
            previous_turn_failed=turn.previous_turn_failed,
        )

        started = monotonic()
        try:
            envelope, plan_usage = await structured_call(
                self.settings,
                schema_model=SetupAgentPlanEnvelope,
                schema_name="hilalmarkets_setup_turn_plan",
                instructions=_PLANNER_INSTRUCTIONS,
                payload=self._planner_payload(turn, shortlist, hint.intent.value),
                model=route.model,
                reasoning_effort=route.reasoning_effort,
                max_output_tokens=6000,
                transport=self.transport,
            )
        except StructuredCallError as exc:
            raise SetupAgentError(
                exc.code,
                str(exc),
                stage="planning",
                retryable=exc.retryable,
            ) from exc
        planner_latency = (monotonic() - started) * 1000
        self.model_call_count += 1
        self.last_usage = {**plan_usage, **route.usage_metadata()}

        trace = SetupAgentTrace(
            source_turn_id=turn.source_turn_id,
            planner_model=route.model,
            planner_reasons=route.reasons,
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
            lexical_hint=hint.intent.value,
            model_calls=self.model_call_count,
        )

        plan = envelope.plan
        if plan is None or not plan.requires_tool:
            # Pure conversation. No tool, no new version, and still a real answer.
            asked = plan.clarifications_to_ask if plan else []
            reply = SetupAgentReply(
                message=_trimmed(envelope.direct_reply)
                or _deterministic_conversation_reply(turn.draft),
                clarification=asked[0] if asked else None,
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
            outcome = apply_setup_turn(
                SetupTurnRequest(
                    plan=plan,
                    message=turn.message,
                    draft=turn.draft,
                    source_turn_id=turn.source_turn_id,
                    allowed_capability_keys=shortlist.allowed_keys,
                    history=list(turn.history),
                    conversation=turn.conversation,
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

        composed_started = monotonic()
        try:
            reply, reply_usage = await structured_call(
                self.settings,
                schema_model=SetupAgentReply,
                schema_name="hilalmarkets_setup_reply",
                instructions=_COMPOSER_INSTRUCTIONS,
                payload=self._composer_payload(turn, plan, outcome),
                model=route.model,
                reasoning_effort=route.reasoning_effort,
                max_output_tokens=1600,
                transport=self.transport,
            )
            self.model_call_count += 1
            self.last_usage = {
                **_merged_usage(self.last_usage, reply_usage),
                **route.usage_metadata(),
            }
            response_model = route.model
        except StructuredCallError:
            # The work is already done and durable. Describing it from the execution
            # result is always better than discarding a successful turn, and far
            # better than a generic reset message.
            reply = SetupAgentReply(message=deterministic_summary(outcome.result))
            response_model = "deterministic_summary"
            trace = _with(trace, failure_stage="response_composition")
        trace = _with(
            trace,
            response_model=response_model,
            response_latency_ms=(monotonic() - composed_started) * 1000,
            model_calls=self.model_call_count,
        )
        conversation = outcome.conversation.model_copy(
            update={"last_assistant_summary": reply.message[:1000]}
        )
        if reply.clarification is not None:
            conversation = conversation.with_question(reply.clarification)
        return SetupAgentTurnResult(
            reply=reply,
            execution=outcome.result,
            draft=outcome.draft,
            conversation=conversation,
            plan=plan,
            trace=trace,
            definition=outcome.definition,
            history_snapshot=outcome.history_snapshot,
            usage=self.last_usage,
        )

    def _planner_payload(
        self,
        turn: SetupAgentTurnInput,
        shortlist: CapabilityShortlist,
        lexical_hint: str,
    ) -> dict[str, Any]:
        draft = turn.draft
        return {
            "current_user_turn": turn.message,
            "source_turn_id": turn.source_turn_id,
            "recent_dialogue": list(turn.dialogue)[-DIALOGUE_WINDOW_MAX:],
            "setup_mode": turn.setup_mode.value,
            "draft": {
                "draft_id": str(draft.draft_id),
                "version": draft.version,
                "name": draft.name,
                "included_symbols": draft.universe.included_symbols[:50],
                "excluded_symbols": draft.universe.excluded_symbols[:50],
                "market_scope": draft.market_scope.model_dump(mode="json"),
                "conditions": _condition_labels(draft),
                "boolean_shape": _boolean_shape(draft),
            },
            "unresolved_fields": [
                {"key": item.key, "question": item.question}
                for item in draft.unresolved_fields
            ],
            "unsupported_requirements": [
                {"key": item.key, "missing": item.missing_contract}
                for item in draft.unsupported_requirements
            ],
            "recent_semantic_diff": list(turn.conversation.last_changed_condition_ids)[:12],
            "conversation_context": turn.conversation.model_dump(mode="json"),
            "approval_eligible": draft.approval_eligible,
            "semantic_violations": validate_draft_semantics(draft),
            "core_primitives": _core_primitives(),
            "capability_shortlist": shortlist.to_prompt_dict(),
            "product_boundaries": _PRODUCT_BOUNDARIES,
            "lexical_hint_non_authoritative": lexical_hint,
        }

    def _composer_payload(
        self,
        turn: SetupAgentTurnInput,
        plan: SetupAgentTurnPlan,
        outcome: SetupTurnOutcome,
    ) -> dict[str, Any]:
        return {
            "current_user_turn": turn.message,
            "recent_dialogue": list(turn.dialogue)[-DIALOGUE_WINDOW_MAX:],
            "segments": [
                {
                    "segment_id": item.segment_id,
                    "kind": item.kind.value,
                    "text": item.exact_source_text,
                }
                for item in plan.segments
            ],
            "response_points": [item.model_dump(mode="json") for item in plan.response_points],
            "questions_to_answer": plan.questions_to_answer,
            "execution_result": outcome.result.model_dump(mode="json"),
            "draft_after": {
                "version": outcome.draft.version,
                "conditions": _condition_labels(outcome.draft),
                "included_symbols": outcome.draft.universe.included_symbols[:50],
                "excluded_symbols": outcome.draft.universe.excluded_symbols[:50],
            },
            "product_boundaries": _PRODUCT_BOUNDARIES,
        }


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
                "direction": node.direction.value,
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
Set strategy_patch only when the turn genuinely changes the setup. Every threshold,
timeframe, symbol, operator and direction in it must appear in this turn's own text,
or belong to an existing condition you name by condition_id. If a value is not in the
user's words, do not supply one: ask, or record it as unresolved.

For a registered mechanic, choose a capability_key from capability_shortlist and
nothing else. If no candidate expresses the request exactly, return an
unsupported_segments entry with the user's own wording, or ask one clarification.
Never invent a key. Never substitute a mechanic that is merely similar — a near miss
watches the wrong market and looks like success.

For the core primitives listed in core_primitives, use no capability_key at all.

CLARIFICATION ANSWERS
If conversation_context has an active_question_id and this turn answers it, record a
clarification_answers entry. An answer resolves that question; it does not become a
new condition. "yes" is not a market rule.

REFERENCES
Use recent_dialogue and conversation_context to resolve "that one", "the second
option", "the one we just added", "make it stricter". Point at the existing
condition_id rather than rebuilding the rule.

target_condition_id names a condition that ALREADY EXISTS in draft.conditions. Leave it
null when you are creating a new rule — there is no id for a rule that does not exist
yet. To change an existing rule use update_conditions with its exact node_id; to delete
one use remove_conditions. An id that is not in draft.conditions is refused.

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

Cover every response_point and answer every entry in questions_to_answer. Acknowledge
the conversational parts of the turn briefly and naturally when there were any.

Write for a beginner in the user's own language. Short sentences, everyday words, no
field names, no error-template phrasing, no bullet lists unless they genuinely help.
Be concise unless the user asked for detail.

Ask at most one question, and only when the draft cannot go further without it. Do not
repeat a question that execution_result shows was answered. Never ask the user to
describe their setup when they already have.

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


def _trimmed(value: str | None) -> str:
    return " ".join((value or "").split())


def _with(trace: SetupAgentTrace, **updates: Any) -> SetupAgentTrace:
    current = {
        "source_turn_id": trace.source_turn_id,
        "planner_model": trace.planner_model,
        "planner_reasons": trace.planner_reasons,
        "planner_latency_ms": trace.planner_latency_ms,
        "segments": trace.segments,
        "plan_confidence": trace.plan_confidence,
        "tool_called": trace.tool_called,
        "patch_validation": trace.patch_validation,
        "semantic_diff": trace.semantic_diff,
        "compile_status": trace.compile_status,
        "response_model": trace.response_model,
        "response_latency_ms": trace.response_latency_ms,
        "failure_stage": trace.failure_stage,
        "shortlist_keys": trace.shortlist_keys,
        "lexical_hint": trace.lexical_hint,
        "model_calls": trace.model_calls,
    }
    current.update(updates)
    return SetupAgentTrace(**current)  # type: ignore[arg-type]


def planner_schema_json() -> str:
    """The plan schema, for the rebuild report and operator tooling."""

    from ai_market_monitor.services.agent_tools import strict_json_schema

    return json.dumps(strict_json_schema(SetupAgentPlanEnvelope), indent=2, sort_keys=True)
