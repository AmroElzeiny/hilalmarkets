"""INV-AGENT: what a Setup Chat turn must do, across the whole family of turns.

The path this replaces gave a whole free-text message one intent, chosen by regular
expressions before the model saw it, and answered anything it did not recognise with a
fixed sentence:

    I'm ready. Describe the market behavior you want to scan or monitor.

A user who had just written exact market logic got told to describe a setup. These
tests assert the general rules that make that impossible, not the phrasings that
happened to be reported:

* a message can carry several intents at once, and each is handled on its own
* technical content survives conversation wrapped around it
* conversation never becomes executable logic
* every applied change is grounded in the user's exact words
* every claim in the reply comes from the execution result
* unknown terminology becomes a clarification or an unsupported requirement, never a
  generic conversational answer

The model is scripted through a mock transport, so the *real* planner payload, the
*real* deterministic tool and the *real* compiler all run. The sole network call is
faked. Assertions look at what the server did, never at the assistant's wording.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from redis.exceptions import RedisError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.capability_shortlist import build_capability_shortlist
from ai_market_monitor.engine.planner_references import (
    MethodologyReference,
    PlannerReferenceContext,
    WatchlistReference,
)
from ai_market_monitor.engine.setup_turn_execution import (
    SetupTurnRejected,
    SetupTurnRequest,
    apply_setup_turn,
)
from ai_market_monitor.engine.strategy_draft_v2 import apply_strategy_patch
from ai_market_monitor.schemas.setup_agent import (
    ApprovalIntent,
    ClarificationAnswer,
    ResponseDirective,
    SegmentKind,
    SetupAgentPlanEnvelope,
    SetupAgentTurnPlan,
    SetupConversationContext,
    StrategyInstructionPlan,
    TurnSegment,
    UnsupportedSegment,
)
from ai_market_monitor.schemas.setup_authorization import (
    AuthorizedPatchOperation,
    ClarificationContract,
)
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    FormulaKind,
    MovementDirection,
    StrategyBias,
    StrategyDraftV2,
    StrategyPatch,
    StrategyUniverseV2,
    UnresolvedFieldV2,
)
from ai_market_monitor.services.setup_chat_agent import (
    SetupAgentError,
    SetupAgentTurnInput,
    SetupChatAgent,
    _repair_can_help,
    deterministic_summary,
)
from ai_market_monitor.services.strategy_patch_extractor import deterministic_strategy_patch
from tests.support.setup_agent_plans import operations_from_patch, planner_envelope_json

#: The sentence this rebuild exists to remove. No reply may contain it.
BANNED_READINESS_PHRASE = "describe the market behavior you want to scan or monitor"

TURN_ID = "turn-00000001"


def test_genuine_unsupported_or_unoffered_mechanics_never_enter_repair() -> None:
    """A boundary is not a mistake, so asking the model again cannot change it."""

    for detail in ("condition:unsupported_mechanic", "condition:capability_not_offered"):
        assert not _repair_can_help("SEMANTIC_VALIDATION_FAILED", (detail,))
    assert not _repair_can_help("INTENT_NOT_PERMITTED", ("i0:USER_QUESTION:cannot_authorize",))
    assert _repair_can_help("VALUE_NOT_GROUNDED", ("op1:add_exclusion:ETH/USDT:not_grounded",))


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="setup-agent-secret-with-at-least-32-characters",
        openai_api_key=SecretStr("test-key"),
        sharia_screening_enforced=False,
        setup_agent_max_estimated_cost_usd_per_turn=5,
    )


def _responses_body(text: str) -> dict[str, Any]:
    return {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 20, "output_tokens": 8},
    }


@dataclass
class Script:
    """What the fake model returns, and what it was asked."""

    plan: SetupAgentPlanEnvelope | None = None
    reply: str = "Done."
    clarification_question_id: str | None = None
    #: Raise this instead of answering the planner call.
    plan_failure: Exception | None = None
    plan_raw_text: str | None = None
    plan_second_raw_text: str | None = None
    #: A second planner answer, for the one retry allowed when the first could not be
    #: read at all. Repair by delta cannot help there: there is nothing parsed to name.
    retry_plan: SetupAgentPlanEnvelope | None = None
    #: Corrections the repair call returns, in the compact delta contract.
    repair_deltas: list[dict[str, Any]] | None = None
    repair_cannot_fix: bool = False
    repair_raw_text: str | None = None
    #: Raise this instead of answering the composing call.
    reply_failure: Exception | None = None
    planner_payloads: list[dict[str, Any]] = field(default_factory=list)
    composer_payloads: list[dict[str, Any]] = field(default_factory=list)
    schema_names: list[str] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            name = body["text"]["format"]["name"]
            self.schema_names.append(name)
            payload = json.loads(body["input"])
            if name == "hilalmarkets_setup_turn_intent":
                first = len(self.planner_payloads) == 0
                self.planner_payloads.append(payload)
                if self.plan_failure is not None:
                    raise self.plan_failure
                raw = self.plan_raw_text if first else self.plan_second_raw_text
                if raw is not None:
                    return httpx.Response(200, json=_responses_body(raw))
                answer = self.plan if first else (self.retry_plan or self.plan)
                assert answer is not None, "the test did not script a plan"
                return httpx.Response(200, json=_responses_body(planner_envelope_json(answer)))
            if name == "hilalmarkets_setup_intent_repair":
                self.planner_payloads.append(payload)
                if self.repair_raw_text is not None:
                    return httpx.Response(200, json=_responses_body(self.repair_raw_text))
                return httpx.Response(
                    200,
                    json=_responses_body(
                        json.dumps(
                            {
                                "deltas": list(self.repair_deltas or []),
                                "cannot_repair": self.repair_cannot_fix,
                            }
                        )
                    ),
                )
            self.composer_payloads.append(payload)
            if self.reply_failure is not None:
                raise self.reply_failure
            return httpx.Response(
                200,
                json=_responses_body(
                    json.dumps(
                        {
                            "message": self.reply,
                            "clarification_question_id": self.clarification_question_id,
                        }
                    )
                ),
            )

        return httpx.MockTransport(handler)


async def _run(
    script: Script,
    message: str,
    *,
    draft: StrategyDraftV2 | None = None,
    conversation: SetupConversationContext | None = None,
    dialogue: tuple[dict[str, str], ...] = (),
    history: tuple[dict[str, Any], ...] = (),
):
    agent = SetupChatAgent(_settings(), transport=script.transport())
    return await agent.run_turn(
        SetupAgentTurnInput(
            message=message,
            source_turn_id=TURN_ID,
            draft=draft or StrategyDraftV2(),
            dialogue=dialogue,
            conversation=conversation or SetupConversationContext(),
            history=history,
        )
    )


def _segment(
    message: str,
    text: str,
    kind: SegmentKind,
    *,
    segment_id: str,
    action: bool = False,
    reply: bool = False,
    target: str | None = None,
) -> TurnSegment:
    start = message.index(text)
    return TurnSegment(
        segment_id=segment_id,
        exact_source_text=text,
        start_offset=start,
        end_offset=start + len(text),
        kind=kind,
        action_required=action,
        reply_required=reply,
        confidence=0.95,
        target_condition_id=target,
    )


def _patch_for(text: str, draft: StrategyDraftV2 | None = None) -> StrategyPatch:
    patch = deterministic_strategy_patch(draft or StrategyDraftV2(), text, source_turn_id=TURN_ID)
    assert patch is not None, f"no deterministic patch for {text!r}"
    return patch


def _conditions(draft: StrategyDraftV2) -> list[Any]:
    if draft.condition_ast is None:
        return []
    return [
        node for node in draft.condition_ast.walk() if node.node_type is ConditionNodeType.CONDITION
    ]


def _draft_with(text: str) -> StrategyDraftV2:
    return apply_strategy_patch(StrategyDraftV2(), _patch_for(text)).draft


# --------------------------------------------------------------------------------
# 1-2. Pure conversation costs no tool call, no version, and no canned sentence.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "text", "kind"),
    [
        ("Hi, how are you?", "Hi, how are you?", SegmentKind.SOCIAL_REPLY),
        ("hey there", "hey there", SegmentKind.SOCIAL_REPLY),
        ("thanks, that's clear", "thanks, that's clear", SegmentKind.ACKNOWLEDGEMENT_NO_ACTION),
        ("ok got it", "ok got it", SegmentKind.ACKNOWLEDGEMENT_NO_ACTION),
        ("مرحبا", "مرحبا", SegmentKind.SOCIAL_REPLY),
    ],
)
async def test_pure_conversation_changes_nothing_and_calls_no_tool(
    message: str, text: str, kind: SegmentKind
) -> None:
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[_segment(message, text, kind, segment_id="s1", reply=True)],
                overall_confidence=0.99,
            ),
            direct_reply="Happy to help. What should I watch for you?",
        )
    )
    draft = StrategyDraftV2()
    result = await _run(script, message, draft=draft)

    assert result.execution is None, "conversation must not run the tool"
    assert result.draft.version == draft.version
    assert result.draft.semantic_hash == draft.semantic_hash
    assert result.trace.tool_called is False
    assert script.schema_names == ["hilalmarkets_setup_turn_intent"], "one call only"
    assert BANNED_READINESS_PHRASE not in result.reply.message.casefold()


async def test_a_conversation_turn_with_no_model_words_still_reports_real_state() -> None:
    """Even the last-resort reply describes the draft rather than resetting the user."""
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[_segment("hello", "hello", SegmentKind.SOCIAL_REPLY, segment_id="s1")],
                overall_confidence=0.9,
            ),
            direct_reply="   ",
        )
    )
    draft = _draft_with("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%")
    result = await _run(script, "hello", draft=draft)

    assert BANNED_READINESS_PHRASE not in result.reply.message.casefold()
    assert "1 rule" in result.reply.message
    assert str(draft.version) in result.reply.message


# --------------------------------------------------------------------------------
# 3-5. Technical content is applied, and survives conversation around it.
# --------------------------------------------------------------------------------


async def test_a_pure_technical_instruction_is_applied_and_compiles() -> None:
    message = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        message,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    )
                ],
                operations=operations_from_patch(_patch_for(message), segment_id="s1"),
                strategy_instructions=[
                    StrategyInstructionPlan(segment_id="s1", intent_summary="15m rise >= 5%")
                ],
                overall_confidence=0.96,
            )
        ),
        reply="Added: a 15m open-to-close rise of at least 5% on BTC/USDT.",
    )
    result = await _run(script, message)

    assert result.execution is not None
    assert result.execution.applied is True
    assert result.execution.strategy_mutated is True
    assert result.execution.compile_status == "compiled"
    assert result.execution.approval_eligible is True
    assert len(_conditions(result.draft)) == 1


@pytest.mark.parametrize(
    ("message", "social", "instruction"),
    [
        (
            "hey thanks! also monitor BTC/USDT when the 15m candle rises "
            "open-to-close by at least 5%",
            "hey thanks!",
            "monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%",
        ),
        (
            "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5% "
            "- thanks a lot",
            "thanks a lot",
            "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%",
        ),
        (
            "sorry to bother you, please monitor BTC/USDT when the 15m candle rises "
            "open-to-close by at least 5%, appreciate it",
            "sorry to bother you",
            "monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%",
        ),
    ],
)
async def test_conversation_around_an_instruction_never_discards_the_instruction(
    message: str, social: str, instruction: str
) -> None:
    """INV: technical content cannot be dropped because chatter surrounds it."""
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message, social, SegmentKind.SOCIAL_REPLY, segment_id="s1", reply=True
                    ),
                    _segment(
                        message,
                        instruction,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s2",
                        action=True,
                    ),
                ],
                operations=operations_from_patch(_patch_for(instruction), segment_id="s2"),
                strategy_instructions=[
                    StrategyInstructionPlan(segment_id="s2", intent_summary="15m rise >= 5%")
                ],
                response_points=[
                    ResponseDirective(point="acknowledge the thanks", kind="acknowledge"),
                    ResponseDirective(point="state the rule added", kind="explain_change"),
                ],
                overall_confidence=0.94,
            )
        ),
        reply="Thanks! I added the 15m rule.",
    )
    result = await _run(script, message)

    assert result.execution is not None
    assert result.execution.strategy_mutated is True, "the instruction must survive"
    assert len(_conditions(result.draft)) == 1
    ignored = {item.kind for item in result.execution.ignored_non_actionable_segments}
    assert SegmentKind.SOCIAL_REPLY in ignored, "the greeting must be recorded as not compiled"


async def test_an_instruction_and_a_question_are_both_handled() -> None:
    """INV: a question is answered while a valid patch is applied."""
    instruction = "monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    question = "why does the timeframe matter?"
    message = f"{instruction} - also {question}"
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        instruction,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    ),
                    _segment(
                        message,
                        question,
                        SegmentKind.USER_QUESTION,
                        segment_id="s2",
                        reply=True,
                    ),
                ],
                operations=operations_from_patch(_patch_for(instruction), segment_id="s1"),
                strategy_instructions=[
                    StrategyInstructionPlan(segment_id="s1", intent_summary="15m rise >= 5%")
                ],
                questions_to_answer=[question],
                overall_confidence=0.93,
            )
        ),
        reply="Added the rule. The timeframe decides which candle closes trigger it.",
    )
    result = await _run(script, message)

    assert result.execution is not None
    assert result.execution.strategy_mutated is True
    assert result.plan is not None
    assert question in result.plan.questions_to_answer
    assert len(script.composer_payloads) == 1
    assert result.trace.model_calls == 2
    assert result.reply.message_without_question.startswith(script.reply)
    assert "added the rule" in result.reply.message_without_question.casefold()
    kinds = {item.kind for item in result.execution.ignored_non_actionable_segments}
    assert SegmentKind.USER_QUESTION in kinds


# --------------------------------------------------------------------------------
# 6-8. Corrections, clarification answers and references to earlier turns.
# --------------------------------------------------------------------------------


async def test_a_correction_updates_the_named_condition_without_adding_one() -> None:
    base = _draft_with("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%")
    existing = _conditions(base)[0]
    message = "change that to at least 8% and explain why it matters"
    replacement = existing.model_copy(
        update={
            "source_turn_id": TURN_ID,
            "source_fragment": "change that to at least 8%",
            "threshold": 8.0,
        }
    )
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        "change that to at least 8%",
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                        target=existing.node_id,
                    ),
                    _segment(
                        message,
                        "explain why it matters",
                        SegmentKind.EXPLANATION_REQUEST,
                        segment_id="s2",
                        reply=True,
                    ),
                ],
                operations=[
                    AuthorizedPatchOperation(
                        authorizing_segment_id="s1",
                        kind="update_condition",
                        condition=replacement,
                        target_condition_id=existing.node_id,
                    )
                ],
                strategy_instructions=[
                    StrategyInstructionPlan(
                        segment_id="s1",
                        intent_summary="raise the threshold to 8%",
                        target_condition_id=existing.node_id,
                    )
                ],
                overall_confidence=0.95,
            )
        ),
        reply="Raised it to 8%. A bigger move means fewer, stronger alerts.",
    )
    result = await _run(script, message, draft=base)

    assert result.execution is not None
    conditions = _conditions(result.draft)
    assert len(conditions) == 1, "a correction must not add a second rule"
    assert conditions[0].threshold == 8.0


def _answer_plan(answer: str, *, question_id: str, operations=()) -> SetupAgentPlanEnvelope:
    return SetupAgentPlanEnvelope(
        plan=SetupAgentTurnPlan(
            source_turn_id=TURN_ID,
            segments=[
                _segment(
                    answer,
                    answer,
                    SegmentKind.CLARIFICATION_ANSWER,
                    segment_id="s1",
                    action=True,
                )
            ],
            operations=list(operations),
            clarification_answers=[
                ClarificationAnswer(
                    segment_id="s1",
                    question_id=question_id,
                    answer_text=answer,
                )
            ],
            overall_confidence=0.9,
        )
    )


@pytest.mark.parametrize("answer", ["yes", "the second one", "use the first option", "no"])
async def test_a_non_mutating_answer_closes_its_question_and_creates_no_condition(
    answer: str,
) -> None:
    """INV: an acknowledgement can resolve a pending question without adding a rule."""
    contract = ClarificationContract(
        question_id="confirm_reading",
        question="Did I read that the way you meant?",
        reason="A yes closes this; nothing executable changes either way.",
        target_type="conversational",
        expected_answer_schema="yes or no",
        mutating=False,
    )
    result = await _run(
        Script(plan=_answer_plan(answer, question_id="confirm_reading"), reply="Noted."),
        answer,
        conversation=SetupConversationContext().with_question(contract),
    )

    assert result.execution is not None
    assert "confirm_reading" in result.execution.answered_questions
    assert _conditions(result.draft) == [], "an answer is not a market rule"
    assert result.conversation.active_question_id is None, "the question is closed"
    assert "confirm_reading" in result.conversation.answered_question_ids


async def test_a_mutating_question_with_no_continuation_is_paused_not_answered() -> None:
    """INV: a clarification cannot clear without resolving its declared target.

    Trusting `resolves_question` let an open item disappear while the draft stayed
    blocked for exactly the reason the question existed.

    A mutating question can no longer be *created* without the completion that will
    apply its answer. Stored state written before that rule can still contain one, and
    this is what happens when it does: the question is put down visibly and the blocker
    stays. It is never cleared, never claimed as answered, and never handed to a model.
    """
    contract = ClarificationContract(
        question_id="timeframe",
        question="Which timeframe should evaluate this rule?",
        reason="The rule cannot run without one.",
        target_type="draft_field",
        target_field="timeframe",
        expected_answer_schema="one of: 15m; 1h",
        mutating=True,
    )
    context = SetupConversationContext().with_question(contract)
    result = await _run(
        Script(plan=_answer_plan("yes", question_id="timeframe"), reply="Noted."),
        "yes",
        conversation=context,
    )

    assert result.execution is None, "words alone cannot close it"
    assert result.conversation.active_question_id is None, "it is put down, not left open"
    assert result.conversation.paused_question is not None, "and it is retrievable"
    assert result.conversation.paused_question.question_id == "timeframe"
    assert "timeframe" not in result.conversation.answered_question_ids, (
        "pausing must never record the question as answered"
    )


async def test_a_mutating_answer_that_changes_the_target_does_close_it() -> None:
    answer = "use BTC/USDT only"
    contract = ClarificationContract(
        question_id="universe",
        question="Which market should this watch?",
        reason="The draft has no market yet.",
        target_type="universe",
        expected_answer_schema="one or more symbols",
        mutating=True,
    )
    result = await _run(
        Script(
            plan=_answer_plan(
                answer,
                question_id="universe",
                operations=[
                    AuthorizedPatchOperation(
                        authorizing_segment_id="s1",
                        kind="add_inclusion",
                        symbol="BTC/USDT",
                    )
                ],
            ),
            reply="Set to BTC/USDT.",
        ),
        answer,
        conversation=SetupConversationContext().with_question(contract),
    )

    assert result.execution is not None
    assert "universe" in result.execution.answered_questions
    assert result.draft.universe.included_symbols == ["BTC/USDT"]
    assert result.conversation.active_question_id is None


async def test_complete_condition_answer_removes_live_creation_blocker() -> None:
    answer = "the 15m candle rises open-to-close by at least 5%"
    unresolved = UnresolvedFieldV2(
        unresolved_id="create-condition",
        source_turn_id="prior-turn",
        source_fragment="a strong bullish move",
        target_type="condition_creation",
        expected_answer_schema={"type": "number"},
        question="What percentage should strong mean?",
        reason="A measurable threshold is required.",
    )
    before = apply_strategy_patch(
        StrategyDraftV2(),
        StrategyPatch(
            source_turn_id="prior-turn",
            unresolved_references=[unresolved],
        ),
    ).draft
    condition = _conditions(_draft_with(answer))[0]
    contract = ClarificationContract(
        question_id=unresolved.key,
        question=unresolved.question,
        reason=unresolved.reason,
        target_type="condition_creation",
        expected_answer_schema='{"type":"number"}',
        mutating=True,
    )
    result = await _run(
        Script(
            plan=_answer_plan(
                answer,
                question_id=unresolved.key,
                operations=[
                    AuthorizedPatchOperation(
                        operation_id="add-complete-condition",
                        authorizing_segment_id="s1",
                        kind="add_condition",
                        condition=condition,
                    )
                ],
            ),
            reply="Added the complete rule.",
        ),
        answer,
        draft=before,
        conversation=SetupConversationContext().with_question(contract),
    )

    assert result.execution is not None
    assert unresolved.key in result.execution.answered_questions
    assert result.draft.unresolved_fields == []
    assert result.conversation.active_question_id is None
    assert len(_conditions(result.draft)) == 1


def _single_exclusion_envelope(message: str, symbol: str) -> SetupAgentPlanEnvelope:
    return SetupAgentPlanEnvelope(
        plan=SetupAgentTurnPlan(
            source_turn_id=TURN_ID,
            segments=[
                TurnSegment(
                    segment_id="s1",
                    exact_source_text=message,
                    start_offset=0,
                    end_offset=len(message),
                    kind=SegmentKind.STRATEGY_INSTRUCTION,
                    action_required=True,
                    confidence=1.0,
                )
            ],
            operations=[
                AuthorizedPatchOperation(
                    operation_id="exclude-1",
                    authorizing_segment_id="s1",
                    kind="add_exclusion",
                    symbol=symbol,
                )
            ],
            overall_confidence=1.0,
        )
    )


async def test_schema_invalid_plan_gets_exactly_one_repair_before_one_execution() -> None:
    """An unreadable answer buys exactly one more attempt, and then one execution.

    A delta cannot correct an answer nobody could parse — there is no intent to name — so
    the recovery here is one more planner attempt. It is still one recovery for the turn.
    """

    message = "exclude LTC/USDT"
    before = StrategyDraftV2()
    script = Script(
        plan_raw_text="{}",
        retry_plan=_single_exclusion_envelope(message, "LTC/USDT"),
    )

    result = await _run(script, message, draft=before)

    assert script.schema_names == [
        "hilalmarkets_setup_turn_intent",
        "hilalmarkets_setup_turn_intent",
    ]
    assert result.trace.model_calls == 2
    assert result.execution is not None
    assert result.execution.previous_executable_version == before.executable_version
    assert result.execution.current_executable_version == before.executable_version + 1
    assert result.draft.universe.excluded_symbols == ["LTC/USDT"]


async def test_failed_single_repair_leaves_the_authoritative_draft_unchanged() -> None:
    """Two unreadable answers end the turn, and the saved setup is untouched."""

    message = "exclude LTC/USDT"
    before = StrategyDraftV2()
    script = Script(plan_raw_text="{}", plan_second_raw_text="{}")
    agent = SetupChatAgent(_settings(), transport=script.transport())

    with pytest.raises(SetupAgentError) as error:
        await agent.run_turn(
            SetupAgentTurnInput(
                message=message,
                source_turn_id=TURN_ID,
                draft=before,
            )
        )

    assert error.value.stage == "planner_repair"
    assert agent.model_call_count == 2
    assert before.universe.excluded_symbols == []
    assert before.executable_version == 1


async def test_ambiguous_governed_methodology_returns_one_typed_clarification() -> None:
    message = "Use the standard-family Sharia methodology"
    planner_answer = {
        "segments": [
            {
                "segment_ref": "s1",
                "exact_source_text": message,
                "segment_kind": "STRATEGY_INSTRUCTION",
            }
        ],
        "semantic_intents": [
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "set_sharia_preferences",
                    "methodology_family": "standard-family",
                },
            }
        ],
        "clarification_answers": [],
        "questions_to_answer": [],
        "unsupported_intents": [],
        "approval_intent": None,
        "overall_confidence": 0.95,
    }
    script = Script(plan_raw_text=json.dumps(planner_answer))
    references = PlannerReferenceContext(
        methodologies=(
            MethodologyReference(
                reference="methodology_1",
                public_identifier="Method A",
                public_name="Method A",
                family="standard-family",
                aliases=(),
                methodology_id="11111111-1111-4111-8111-111111111111",
                methodology_version="1.0",
            ),
            MethodologyReference(
                reference="methodology_2",
                public_identifier="Method B",
                public_name="Method B",
                family="standard-family",
                aliases=(),
                methodology_id="22222222-2222-4222-8222-222222222222",
                methodology_version="1.0",
            ),
        )
    )
    before = StrategyDraftV2()
    result = await SetupChatAgent(_settings(), transport=script.transport()).run_turn(
        SetupAgentTurnInput(
            message=message,
            source_turn_id=TURN_ID,
            draft=before,
            planner_references=references,
        )
    )

    assert result.execution is None
    assert result.draft == before
    assert result.clarification is not None
    assert result.clarification.target_type == "sharia_policy"
    assert result.clarification.target_field == "methodology_id"
    assert result.clarification.allowed_options == ["Method A", "Method B"]
    assert result.conversation.active_question == result.clarification
    assert result.message.count(result.clarification.question) == 1
    assert script.schema_names == ["hilalmarkets_setup_turn_intent"]

    answer_message = "Use Method A"
    answer_script = Script(
        plan_raw_text=json.dumps(
            {
                "segments": [
                    {
                        "segment_ref": "s1",
                        "exact_source_text": answer_message,
                        "segment_kind": "CLARIFICATION_ANSWER",
                    }
                ],
                "semantic_intents": [
                    {
                        "segment_ref": "s1",
                        "payload": {
                            "action": "set_sharia_preferences",
                            "methodology_identifier": "Method A",
                        },
                    }
                ],
                "clarification_answers": [
                    {
                        "segment_ref": "s1",
                        "clarification_ref": "clarification_1",
                        "answer_text": answer_message,
                    }
                ],
                "questions_to_answer": [],
                "unsupported_intents": [],
                "approval_intent": None,
                "overall_confidence": 0.99,
            }
        )
    )
    answered = await SetupChatAgent(_settings(), transport=answer_script.transport()).run_turn(
        SetupAgentTurnInput(
            message=answer_message,
            source_turn_id="turn-00000002",
            draft=result.draft,
            conversation=result.conversation,
            planner_references=references,
        )
    )
    assert answered.execution is not None
    assert answered.conversation.active_question is None
    assert str(answered.draft.sharia_policy.methodology_id) == (
        "11111111-1111-4111-8111-111111111111"
    )
    assert answered.execution.answered_questions == [result.clarification.question_id]


@pytest.mark.parametrize(
    ("message", "preference"),
    (
        ("Do not use screened assets only", {"screened_assets_only": False}),
        ("Do not use an approved watchlist only", {"approved_watchlist_only": False}),
        (
            "Use neither screened assets nor an approved watchlist only",
            {"screened_assets_only": False, "approved_watchlist_only": False},
        ),
        (
            "Use screened assets only and my approved watchlist only",
            {"screened_assets_only": True, "approved_watchlist_only": True},
        ),
    ),
)
async def test_sharia_universe_boolean_conflicts_clarify_without_mutation(
    message: str,
    preference: dict[str, bool],
) -> None:
    planner_answer = {
        "segments": [
            {
                "segment_ref": "s1",
                "exact_source_text": message,
                "segment_kind": "STRATEGY_INSTRUCTION",
            }
        ],
        "semantic_intents": [
            {
                "segment_ref": "s1",
                "payload": {"action": "set_sharia_preferences", **preference},
            }
        ],
        "clarification_answers": [],
        "questions_to_answer": [],
        "unsupported_intents": [],
        "approval_intent": None,
        "overall_confidence": 0.99,
    }
    before = StrategyDraftV2()
    result = await SetupChatAgent(
        _settings(), transport=Script(plan_raw_text=json.dumps(planner_answer)).transport()
    ).run_turn(
        SetupAgentTurnInput(
            message=message,
            source_turn_id=TURN_ID,
            draft=before,
        )
    )

    assert result.execution is None
    assert result.draft == before
    assert result.clarification is not None
    assert result.clarification.target_type == "sharia_policy"
    assert result.draft.executable_hash == before.executable_hash


@pytest.mark.parametrize(
    ("message", "preference", "expected_mode"),
    (
        (
            "Use screened assets only, not an approved watchlist",
            {"screened_assets_only": True, "approved_watchlist_only": False},
            "eligible_market",
        ),
        (
            "Use my Core assets approved watchlist only, not screened assets only",
            {"screened_assets_only": False, "approved_watchlist_only": True},
            "approved_watchlist",
        ),
    ),
)
async def test_negative_sharia_boolean_executes_only_with_grounded_alternative(
    message: str,
    preference: dict[str, bool],
    expected_mode: str,
) -> None:
    planner_answer = {
        "segments": [
            {
                "segment_ref": "s1",
                "exact_source_text": message,
                "segment_kind": "STRATEGY_INSTRUCTION",
            }
        ],
        "semantic_intents": [
            {
                "segment_ref": "s1",
                "payload": {"action": "set_sharia_preferences", **preference},
            }
        ],
        "clarification_answers": [],
        "questions_to_answer": [],
        "unsupported_intents": [],
        "approval_intent": None,
        "overall_confidence": 0.99,
    }
    references = PlannerReferenceContext(
        watchlists=(
            WatchlistReference(
                reference="watchlist_1",
                public_name="Core assets",
                aliases=(),
                watchlist_id="22222222-2222-4222-8222-222222222222",
                watchlist_version="wlv2:core",
            ),
        )
    )
    before = StrategyDraftV2(
        sharia_policy={
            "universe_mode": "explicit_assets",
            "explicit_symbols": ["BTC/USDT"],
        }
    )
    result = await SetupChatAgent(
        _settings(), transport=Script(plan_raw_text=json.dumps(planner_answer)).transport()
    ).run_turn(
        SetupAgentTurnInput(
            message=message,
            source_turn_id=TURN_ID,
            draft=before,
            planner_references=references,
        )
    )

    assert result.execution is not None
    assert result.draft.sharia_policy.universe_mode.value == expected_mode
    assert result.draft.executable_version == before.executable_version + 1
    assert result.draft.executable_hash != before.executable_hash
    if expected_mode == "approved_watchlist":
        assert str(result.draft.sharia_policy.approved_watchlist_id) == (
            "22222222-2222-4222-8222-222222222222"
        )


async def test_fail_open_sharia_preference_is_explicitly_unsupported_without_mutation() -> None:
    message = "Use fail open Sharia handling"
    planner_answer = {
        "segments": [
            {
                "segment_ref": "s1",
                "exact_source_text": message,
                "segment_kind": "STRATEGY_INSTRUCTION",
            }
        ],
        "semantic_intents": [
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "set_sharia_preferences",
                    "fail_closed_preference": False,
                },
            }
        ],
        "clarification_answers": [],
        "questions_to_answer": [],
        "unsupported_intents": [],
        "approval_intent": None,
        "overall_confidence": 0.99,
    }
    before = StrategyDraftV2()
    agent = SetupChatAgent(
        _settings(), transport=Script(plan_raw_text=json.dumps(planner_answer)).transport()
    )
    with pytest.raises(SetupAgentError) as failure:
        await agent.run_turn(
            SetupAgentTurnInput(message=message, source_turn_id=TURN_ID, draft=before)
        )

    assert failure.value.code == "SHARIA_FAIL_OPEN_UNSUPPORTED"
    assert agent.model_call_count == 1
    assert before.executable_version == 1


async def test_exact_current_sharia_preference_is_a_non_mutating_turn() -> None:
    message = "Use screened assets only"
    planner_answer = {
        "segments": [
            {
                "segment_ref": "s1",
                "exact_source_text": message,
                "segment_kind": "STRATEGY_INSTRUCTION",
            }
        ],
        "semantic_intents": [
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "set_sharia_preferences",
                    "screened_assets_only": True,
                },
            }
        ],
        "clarification_answers": [],
        "questions_to_answer": [],
        "unsupported_intents": [],
        "approval_intent": None,
        "overall_confidence": 0.99,
    }
    before = StrategyDraftV2()
    result = await SetupChatAgent(
        _settings(), transport=Script(plan_raw_text=json.dumps(planner_answer)).transport()
    ).run_turn(
        SetupAgentTurnInput(message=message, source_turn_id=TURN_ID, draft=before)
    )

    assert result.execution is not None
    assert result.execution.applied is False
    assert result.execution.strategy_mutated is False
    assert result.plan is not None
    assert result.plan.operations == []
    assert result.draft.executable_hash == before.executable_hash
    assert result.draft.executable_version == before.executable_version


async def test_shape_recovery_then_semantic_failure_stops_after_two_calls() -> None:
    """Shape recovery consumes the turn's only repair allowance."""

    message = "exclude LTC/USDT"
    before = StrategyDraftV2()
    script = Script(
        plan_raw_text="{}",
        retry_plan=_single_exclusion_envelope(message, "ETH/USDT"),
    )
    agent = SetupChatAgent(_settings(), transport=script.transport())

    with pytest.raises(SetupAgentError) as error:
        await agent.run_turn(
            SetupAgentTurnInput(
                message=message,
                source_turn_id=TURN_ID,
                draft=before,
            )
        )

    assert error.value.code == "VALUE_NOT_GROUNDED"
    assert agent.model_call_count == 2
    assert script.schema_names == [
        "hilalmarkets_setup_turn_intent",
        "hilalmarkets_setup_turn_intent",
    ]
    assert before.universe.excluded_symbols == []


async def test_one_omitted_explicit_timeframe_role_uses_the_single_repair() -> None:
    message = (
        "Use 15m as context and trigger on 5m when the candle rises "
        "open-to-close by at least 5%"
    )
    patch = _patch_for(message)
    condition = patch.add_conditions[0].model_copy(update={"context_timeframes": []})
    patch = patch.model_copy(update={"add_conditions": [condition]})
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        message,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    )
                ],
                operations=operations_from_patch(patch, segment_id="s1"),
                overall_confidence=0.97,
            )
        ),
        repair_deltas=[
            {
                "intent_ref": "intent_1",
                "target_path": "condition.context_timeframes",
                "repair_kind": "replace_with_grounded_value",
                "replacement_value": {
                    "kind": "string_list",
                    "string_items": ["15m"],
                },
                "source_segment_ref": "segment_1",
                "validation_code": "PLANNER_SEMANTIC_OMISSION",
            }
        ],
    )

    result = await _run(script, message)

    assert result.trace.model_calls == 2
    assert result.execution is not None
    assert result.execution.applied is True
    assert result.draft.condition_ast is not None
    assert result.draft.condition_ast.trigger_timeframe == "5m"
    assert result.draft.condition_ast.context_timeframes == ["15m"]
    assert script.schema_names == [
        "hilalmarkets_setup_turn_intent",
        "hilalmarkets_setup_intent_repair",
    ]


async def test_repaired_role_in_adjacent_noop_clause_keeps_one_grounded_evidence_span() -> None:
    """A split model segment may be repaired; the server never invents the role."""

    first = (
        "By strong I mean a bearish close-to-close percentage move of at least 7.5% "
        "on the 1h trigger timeframe."
    )
    second = "Keep 1m as context, BTCUSDT included, and LTCUSDT excluded."
    message = f"{first} {second}"
    planner_answer = {
        "segments": [
            {
                "segment_ref": "condition_clause",
                "exact_source_text": first,
                "segment_kind": "STRATEGY_INSTRUCTION",
            },
            {
                "segment_ref": "scope_clause",
                "exact_source_text": second,
                "segment_kind": "STRATEGY_INSTRUCTION",
            },
        ],
        "semantic_intents": [
            {
                "segment_ref": "condition_clause",
                "payload": {
                    "action": "add_condition",
                    "condition": {
                        "formula_key": "close_to_close_percentage",
                        "movement_direction": "down",
                        "strategy_bias": "neutral",
                        "comparator": "gte",
                        "threshold": 7.5,
                        "unit": "percent",
                        "trigger_timeframe": "1h",
                    },
                },
            },
            {
                "segment_ref": "scope_clause",
                "payload": {"action": "include_symbol", "symbol": "BTCUSDT"},
            },
            {
                "segment_ref": "scope_clause",
                "payload": {"action": "exclude_symbol", "symbol": "LTCUSDT"},
            },
        ],
        "clarification_answers": [],
        "questions_to_answer": [],
        "unsupported_intents": [],
        "approval_intent": None,
        "overall_confidence": 0.96,
    }
    script = Script(
        plan_raw_text=json.dumps(planner_answer),
        repair_deltas=[
            {
                "intent_ref": "intent_1",
                "target_path": "condition.context_timeframes",
                "repair_kind": "correct_semantic_role",
                "replacement_value": {
                    "kind": "timeframe",
                    "string_value": "1m",
                },
                "source_segment_ref": "scope_clause",
                "validation_code": "PLANNER_SEMANTIC_OMISSION",
            }
        ],
    )

    result = await _run(script, message)

    assert result.trace.model_calls == 2
    assert result.execution is not None and result.execution.applied is True
    assert result.draft.condition_ast is not None
    assert result.draft.condition_ast.trigger_timeframe == "1h"
    assert result.draft.condition_ast.context_timeframes == ["1m"]
    assert result.draft.universe.included_symbols == ["BTC/USDT"]
    assert result.draft.universe.excluded_symbols == ["LTC/USDT"]
    assert len(result.plan.segments) == 1
    assert result.plan.segments[0].exact_source_text == message
    assert {item.authorizing_segment_id for item in result.plan.operations} == {
        result.plan.segments[0].segment_id
    }


async def test_one_explicitly_authored_formula_omission_uses_one_compact_repair() -> None:
    message = (
        "Bearish close-to-close percentage move of at least 7.5% on the 1h trigger "
        "timeframe with 1m as context"
    )
    planner_answer = {
        "segments": [
            {
                "segment_ref": "s1",
                "exact_source_text": message,
                "segment_kind": "STRATEGY_INSTRUCTION",
            }
        ],
        "semantic_intents": [
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "add_condition",
                    "condition": {
                        "movement_direction": "down",
                        "comparator": "gte",
                        "threshold": 7.5,
                        "unit": "percent",
                        "trigger_timeframe": "1h",
                        "context_timeframes": ["1m"],
                    },
                },
            }
        ],
        "clarification_answers": [],
        "questions_to_answer": [],
        "unsupported_intents": [],
        "approval_intent": None,
        "overall_confidence": 0.96,
    }
    script = Script(
        plan_raw_text=json.dumps(planner_answer),
        repair_deltas=[
            {
                "intent_ref": "intent_1",
                "target_path": "condition.formula_key",
                "repair_kind": "replace_with_grounded_value",
                "replacement_value": {
                    "kind": "enum",
                    "string_value": "close_to_close_percentage",
                },
                "source_segment_ref": "s1",
                "validation_code": "PLANNER_SEMANTIC_OMISSION",
            }
        ],
    )

    result = await _run(script, message)

    assert result.trace.model_calls == 2
    assert result.execution is not None and result.execution.applied is True
    assert result.draft.condition_ast is not None
    assert result.draft.condition_ast.formula == FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE
    assert result.draft.condition_ast.context_timeframes == ["1m"]


async def test_narrow_source_quote_cannot_hide_formula_from_verified_condition_segment() -> None:
    """A model-owned subquote cannot make an explicit formula look absent."""

    message = (
        "Bearish close-to-close percentage move of at least 7.5% on the 1h trigger "
        "timeframe with 1m as context"
    )
    planner_answer = {
        "segments": [
            {
                "segment_ref": "s1",
                "exact_source_text": message,
                "segment_kind": "STRATEGY_INSTRUCTION",
            }
        ],
        "semantic_intents": [
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "add_condition",
                    "condition": {
                        # The verified segment owns the complete action. This optional
                        # narrower quote must not hide close-to-close from validation.
                        "source_quote": "at least 7.5% on the 1h trigger timeframe",
                        "movement_direction": "down",
                        "comparator": "gte",
                        "threshold": 7.5,
                        "unit": "percent",
                        "trigger_timeframe": "1h",
                        "context_timeframes": ["1m"],
                    },
                },
            }
        ],
        "clarification_answers": [],
        "questions_to_answer": [],
        "unsupported_intents": [],
        "approval_intent": None,
        "overall_confidence": 0.96,
    }
    script = Script(
        plan_raw_text=json.dumps(planner_answer),
        repair_deltas=[
            {
                "intent_ref": "intent_1",
                "target_path": "condition.formula_key",
                "repair_kind": "replace_with_grounded_value",
                "replacement_value": {
                    "kind": "enum",
                    "string_value": "close_to_close_percentage",
                },
                "source_segment_ref": "s1",
                "validation_code": "PLANNER_SEMANTIC_OMISSION",
            }
        ],
    )

    result = await _run(script, message)

    assert result.trace.model_calls == 2
    assert result.execution is not None and result.execution.applied is True
    assert result.draft.condition_ast is not None
    assert result.draft.condition_ast.formula == FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE


async def test_compiler_invariant_violation_never_calls_repair(monkeypatch) -> None:
    """An internal compiler defect is an engineering failure, not model-owned meaning."""

    import ai_market_monitor.services.setup_chat_agent as agent_module
    from ai_market_monitor.engine.planner_intent_compiler import IntentCompileError

    message = "exclude LTC/USDT"
    script = Script(plan=_single_exclusion_envelope(message, "LTC/USDT"))

    def broken_compiler(*args, **kwargs):
        raise IntentCompileError(
            "COMPILER_INVARIANT_VIOLATION",
            "internal compiler contract failed",
        )

    monkeypatch.setattr(agent_module, "compile_planner_intents", broken_compiler)
    agent = SetupChatAgent(_settings(), transport=script.transport())
    with pytest.raises(SetupAgentError) as error:
        await agent.run_turn(
            SetupAgentTurnInput(
                message=message,
                source_turn_id=TURN_ID,
                draft=StrategyDraftV2(),
            )
        )
    assert error.value.code == "COMPILER_INVARIANT_VIOLATION"
    assert script.schema_names == ["hilalmarkets_setup_turn_intent"]
    assert agent.model_call_count == 1


async def test_dry_grounding_repair_cannot_keep_an_ungrounded_symbol() -> None:
    """A correction has to quote the trader's own words, and then it lands."""

    message = "exclude LTC/USDT"
    script = Script(
        plan=_single_exclusion_envelope(message, "ETH/USDT"),
        repair_deltas=[
            {
                "intent_ref": "intent_1",
                "target_path": "symbol",
                "repair_kind": "replace_with_grounded_value",
                "replacement_value": {"kind": "symbol", "string_value": "LTC/USDT"},
                "source_segment_ref": "segment_1",
                "validation_code": "VALUE_NOT_GROUNDED",
            }
        ],
    )

    result = await _run(script, message)

    assert result.trace.model_calls == 2
    assert result.draft.universe.excluded_symbols == ["LTC/USDT"]
    assert "ETH/USDT" not in result.draft.universe.excluded_symbols
    repair_payload = script.planner_payloads[1]
    assert set(repair_payload) == {
        "invalid_intent",
        "verified_source_segment",
        "relevant_existing_value",
        "validation",
        "allowed_repair_kinds",
        "minimum_target_references",
    }
    serialized = json.dumps(repair_payload)
    for canonical_name in (
        "AuthorizedPatchOperation",
        "ConditionNodeV2",
        "OperandV2",
        "UnresolvedFieldV2",
        "ShariaPolicyV2",
        "DraftFieldPatch",
        "StrategyDraftV2",
    ):
        assert canonical_name not in serialized
    assert "complete_intent_list" not in repair_payload
    assert "canonical_operations" not in repair_payload
    assert "full_draft" not in repair_payload


async def test_a_correction_quoting_words_the_user_never_wrote_is_ignored() -> None:
    """The repair call cannot smuggle in a value; it must cite the real message."""

    message = "exclude LTC/USDT"
    script = Script(
        plan=_single_exclusion_envelope(message, "ETH/USDT"),
        repair_deltas=[
            {
                "intent_ref": "intent_1",
                "target_path": "symbol",
                "repair_kind": "replace_with_grounded_value",
                "replacement_value": {"kind": "symbol", "string_value": "DOGE/USDT"},
                "source_segment_ref": "segment_1",
                "validation_code": "VALUE_NOT_GROUNDED",
            }
        ],
    )
    agent = SetupChatAgent(_settings(), transport=script.transport())

    with pytest.raises(SetupAgentError):
        await agent.run_turn(
            SetupAgentTurnInput(message=message, source_turn_id=TURN_ID, draft=StrategyDraftV2())
        )


async def test_a_reference_to_an_earlier_condition_reaches_the_planner() -> None:
    """The agent must be given enough context to resolve ordinary references."""
    base = _draft_with("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%")
    existing = _conditions(base)[0]
    message = "remove the one we just added"
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        message,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                        target=existing.node_id,
                    )
                ],
                operations=[
                    AuthorizedPatchOperation(
                        authorizing_segment_id="s1",
                        kind="remove_condition",
                        target_condition_id=existing.node_id,
                    )
                ],
                strategy_instructions=[
                    StrategyInstructionPlan(
                        segment_id="s1",
                        intent_summary="remove the rule added last turn",
                        target_condition_id=existing.node_id,
                    )
                ],
                overall_confidence=0.92,
            )
        ),
        reply="Removed it.",
    )
    result = await _run(
        script,
        message,
        draft=base,
        dialogue=({"role": "user", "content": "add a 15m rise rule"},),
        conversation=SetupConversationContext(
            last_changed_condition_ids=[existing.node_id],
            recent_references=["the 15m rule"],
        ),
    )

    payload = script.planner_payloads[0]
    assert payload["draft"]["conditions"], "the planner must see the existing conditions"
    assert payload["draft"]["conditions"][0]["condition_ref"] == "condition_1"
    assert existing.node_id not in json.dumps(payload)
    for forbidden in (
        "source_turn_id",
        "source_segment_id",
        "intent_id",
        "operation_id",
        "node_id",
        "unresolved_id",
        "snapshot_id",
        "executable_version",
        "workflow_revision",
        "registry_version",
        "capability_version",
    ):
        assert forbidden not in json.dumps(payload)
    assert "registry_version" not in json.dumps(payload["capability_shortlist"])
    assert "capability_version" not in json.dumps(payload["capability_shortlist"])
    assert payload["recent_dialogue"], "the planner must see recent dialogue"
    assert result.execution is not None
    assert _conditions(result.draft) == []


# --------------------------------------------------------------------------------
# 9-10. Several conditions, and nested Boolean structure.
# --------------------------------------------------------------------------------


async def test_several_independent_conditions_each_keep_their_own_semantics() -> None:
    first = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    base = _draft_with(first)
    second = "also require the 1h close-to-close move to fall by at least 2%"
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        second,
                        second,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    )
                ],
                operations=operations_from_patch(_patch_for(second, base), segment_id="s1"),
                strategy_instructions=[
                    StrategyInstructionPlan(segment_id="s1", intent_summary="1h fall >= 2%")
                ],
                overall_confidence=0.94,
            )
        ),
        reply="Added the second rule.",
    )
    result = await _run(script, second, draft=base)

    conditions = _conditions(result.draft)
    assert len(conditions) == 2
    thresholds = {item.threshold for item in conditions}
    timeframes = {item.trigger_timeframe for item in conditions}
    assert thresholds == {5.0, 2.0}, "each rule keeps its own size"
    assert timeframes == {"15m", "1h"}, "each rule keeps its own timeframe"
    assert result.execution is not None
    assert result.execution.semantic_violations == []
    assert result.draft.executable_version == base.executable_version + 1


async def test_nested_boolean_structure_is_preserved_through_the_tool() -> None:
    message = (
        "the 15m candle rises open-to-close by at least 2% AND (the 1h close-to-close "
        "move rises by at least 3% OR NOT the 4h high-to-low move drops by at least 4%)"
    )
    patch = _patch_for(message)
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        message,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    )
                ],
                operations=operations_from_patch(patch, segment_id="s1"),
                strategy_instructions=[
                    StrategyInstructionPlan(segment_id="s1", intent_summary="nested boolean rule")
                ],
                overall_confidence=0.9,
            )
        ),
        reply="Added the grouped rule.",
    )
    result = await _run(script, message)

    assert result.draft.condition_ast is not None

    def shape(node: Any) -> str:
        if not node.children:
            return node.node_type.value
        return f"{node.node_type.value}(" + ",".join(shape(c) for c in node.children) + ")"

    assert shape(result.draft.condition_ast) == "and(condition,or(condition,not(condition)))"


# --------------------------------------------------------------------------------
# 11-12. Unknown terminology, and exact registered capabilities.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "alert me when the flarbnix indicator inverts",
        "watch for a gamma squeeze on the order book",
        "notify me when the whale ratio breaks its band",
    ],
)
async def test_an_unknown_mechanic_becomes_a_blocking_unsupported_requirement(
    message: str,
) -> None:
    """INV: unknown *market* wording produces a typed refusal that blocks the draft."""
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        message,
                        # A market rule the platform cannot express is an *instruction*
                        # it failed to convert, so it belongs in the draft as a blocker.
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    )
                ],
                operations=[
                    AuthorizedPatchOperation(
                        authorizing_segment_id="s1",
                        kind="add_unsupported",
                        missing_contract="No registered mechanic measures this exactly.",
                    )
                ],
                overall_confidence=0.8,
            )
        ),
        reply="I cannot express that one exactly yet.",
    )
    result = await _run(script, message)

    assert result.execution is not None
    assert result.execution.unsupported_requirements, "the refusal must land in the draft"
    assert result.draft.blocking is True, "an unsupported requirement blocks eligibility"
    assert result.execution.approval_eligible is False
    assert BANNED_READINESS_PHRASE not in result.reply.message.casefold()


@pytest.mark.parametrize(
    "message",
    [
        "just buy BTC for me right now",
        "place the trade and set a stop loss",
        "guarantee me 10% a month",
        "should I go long here?",
    ],
)
async def test_an_out_of_scope_request_answers_a_boundary_and_touches_nothing(
    message: str,
) -> None:
    """INV: a boundary refusal is reply-only. It cannot block or version the draft.

    `UNSUPPORTED_REQUEST` means "outside what this product does". Letting it write an
    `UnsupportedRequirementV2` made a draft permanently unapprovable because the user
    once asked for advice.
    """
    base = _draft_with("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%")
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        message,
                        SegmentKind.UNSUPPORTED_REQUEST,
                        segment_id="s1",
                        reply=True,
                    )
                ],
                unsupported_segments=[
                    UnsupportedSegment(
                        segment_id="s1",
                        missing_contract="This product does not place trades or advise.",
                    )
                ],
                overall_confidence=0.95,
            )
        ),
        reply="I cannot do that — I only build and watch rules you approve.",
    )
    result = await _run(script, message, draft=base)

    assert result.draft.version == base.version, "no new version"
    assert result.draft.semantic_hash == base.semantic_hash, "no semantic change"
    assert result.draft.unsupported_requirements == [], "a boundary is not a draft blocker"
    if result.execution is not None:
        assert result.execution.strategy_mutated is False
        assert result.execution.approval_status != "invalidated_by_edit"
    assert BANNED_READINESS_PHRASE not in result.reply.message.casefold()


async def test_a_capability_key_must_come_from_the_server_shortlist() -> None:
    message = "alert me when RSI drops below 30 on the 15m"
    shortlist = build_capability_shortlist(message)
    assert shortlist.allowed_keys, "this message should retrieve at least one candidate"
    offered = sorted(shortlist.allowed_keys)[0]

    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[
            _segment(
                message,
                message,
                SegmentKind.STRATEGY_INSTRUCTION,
                segment_id="s1",
                action=True,
            )
        ],
        strategy_instructions=[
            StrategyInstructionPlan(
                segment_id="s1",
                intent_summary="RSI below 30",
                capability_key=offered,
            )
        ],
        overall_confidence=0.9,
    )
    # The offered key passes.
    await apply_setup_turn(
        SetupTurnRequest(
            plan=plan,
            message=message,
            draft=StrategyDraftV2(),
            source_turn_id=TURN_ID,
            allowed_capability_keys=shortlist.allowed_keys,
        )
    )
    # A key the server never offered is refused, however plausible it looks.
    invented = plan.model_copy(
        update={
            "strategy_instructions": [
                StrategyInstructionPlan(
                    segment_id="s1",
                    intent_summary="RSI below 30",
                    capability_key="rsi_oversold_deluxe",
                )
            ]
        }
    )
    with pytest.raises(SetupTurnRejected) as error:
        await apply_setup_turn(
            SetupTurnRequest(
                plan=invented,
                message=message,
                draft=StrategyDraftV2(),
                source_turn_id=TURN_ID,
                allowed_capability_keys=shortlist.allowed_keys,
            )
        )
    assert error.value.code == "CAPABILITY_NOT_OFFERED"


async def test_inherited_capability_and_parameters_need_no_rediscovery() -> None:
    existing = _conditions(
        _draft_with("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%")
    )[0].model_copy(
        update={
            "formula": FormulaKind.CAPABILITY,
            "operands": [],
            "operator": Comparator.LESS_THAN,
            "threshold": 30,
            "unit": "index",
            "movement_direction": MovementDirection.DOWN,
            "strategy_bias": StrategyBias.SHORT,
            "capability_key": "rsi_threshold",
            "capability_version": "1.0",
            "capability_parameters": {},
            "source_fragment": "RSI below 30 on the 15m",
        }
    )
    draft = StrategyDraftV2(
        universe=StrategyUniverseV2(included_symbols=["BTC/USDT"]),
        condition_ast=existing,
    )
    message = "make that stricter at 25"
    replacement = existing.model_copy(
        update={
            "threshold": 25,
            "source_turn_id": TURN_ID,
            "source_fragment": message,
        }
    )
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[
            _segment(
                message,
                message,
                SegmentKind.STRATEGY_INSTRUCTION,
                segment_id="s1",
                action=True,
                target=existing.node_id,
            )
        ],
        operations=[
            AuthorizedPatchOperation(
                operation_id="inherit-rsi-threshold",
                authorizing_segment_id="s1",
                kind="update_condition",
                target_condition_id=existing.node_id,
                condition=replacement,
            )
        ],
        strategy_instructions=[
            StrategyInstructionPlan(
                segment_id="s1",
                intent_summary="make the existing condition stricter",
                target_condition_id=existing.node_id,
                capability_key="rsi_threshold",
            )
        ],
        overall_confidence=0.95,
    )

    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=plan,
            message=message,
            draft=draft,
            source_turn_id=TURN_ID,
            allowed_capability_keys=frozenset(),
        )
    )

    changed = _conditions(outcome.draft)[0]
    assert changed.threshold == 25
    assert changed.capability_key == "rsi_threshold"
    assert outcome.draft.executable_version == draft.executable_version + 1


async def test_snapshot_restore_requires_exact_owned_snapshot_identity() -> None:
    original = _draft_with(
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    )
    changed_patch = deterministic_strategy_patch(
        original,
        "Also require the 1h candle to fall close-to-close by at most -2%",
        source_turn_id="turn-00000002",
    )
    assert changed_patch is not None
    changed = apply_strategy_patch(original, changed_patch).draft
    message = "Undo that and restore the prior setup."
    segment = _segment(
        message,
        message,
        SegmentKind.STRATEGY_INSTRUCTION,
        segment_id="restore",
        action=True,
    )
    operation = AuthorizedPatchOperation(
        operation_id="restore-prior",
        authorizing_segment_id="restore",
        kind="restore_snapshot",
        target_snapshot_id="snapshot-owned",
        target_executable_version=original.executable_version,
    )
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[segment],
        operations=[operation],
        overall_confidence=0.99,
    )
    history = [
        {
            "snapshot_id": "snapshot-owned",
            "executable_version": original.executable_version,
            "draft": original.model_dump(mode="json"),
        }
    ]

    restored = await apply_setup_turn(
        SetupTurnRequest(
            plan=plan,
            message=message,
            draft=changed,
            source_turn_id=TURN_ID,
            history=history,
        )
    )

    assert restored.draft.executable_version == changed.executable_version + 1
    assert restored.draft.executable_hash == original.executable_hash
    assert restored.draft.approval.approved is False
    assert restored.result.operation_results[0].operation_id == "restore-prior"

    unowned = plan.model_copy(
        update={
            "operations": [
                operation.model_copy(update={"target_snapshot_id": "snapshot-other-user"})
            ]
        }
    )
    with pytest.raises(SetupTurnRejected) as error:
        await apply_setup_turn(
            SetupTurnRequest(
                plan=unowned,
                message=message,
                draft=changed,
                source_turn_id=TURN_ID,
                history=history,
            )
        )
    assert error.value.code == "VALUE_NOT_GROUNDED"


async def test_the_planner_is_always_given_the_shortlist_and_the_boundaries() -> None:
    message = "alert me when RSI drops below 30 on the 15m"
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message, message, SegmentKind.USER_QUESTION, segment_id="s1", reply=True
                    )
                ],
                overall_confidence=0.9,
            ),
            direct_reply="RSI is available. Which timeframe should it use?",
        )
    )
    await _run(script, message)

    payload = script.planner_payloads[0]
    assert payload["capability_shortlist"]["candidates"], "candidates must be supplied"
    assert "rule" in payload["capability_shortlist"]
    assert payload["core_primitives"]["formulas"], "core primitives must be supplied"
    assert payload["product_boundaries"]["cannot"], "boundaries must be supplied"
    assert "lexical_hint_non_authoritative" not in payload


# --------------------------------------------------------------------------------
# 13-15. Universe, mixed language, noisy input.
# --------------------------------------------------------------------------------


async def test_an_excluded_symbol_is_excluded_and_never_enters_a_condition() -> None:
    message = (
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%, "
        "excluding ETH/USDT"
    )
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        message,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    )
                ],
                operations=operations_from_patch(_patch_for(message), segment_id="s1"),
                strategy_instructions=[
                    StrategyInstructionPlan(segment_id="s1", intent_summary="15m rise, no ETH")
                ],
                overall_confidence=0.95,
            )
        ),
        reply="Added, and ETH/USDT stays out.",
    )
    result = await _run(script, message)

    assert "ETH/USDT" in result.draft.universe.excluded_symbols
    assert "ETH/USDT" not in result.draft.universe.included_symbols
    assert result.execution is not None
    assert result.execution.semantic_violations == []


@pytest.mark.parametrize(
    ("message", "instruction"),
    [
        (
            "تمام، راقب BTC/USDT when the 15m candle rises open-to-close by at least 5%",
            "BTC/USDT when the 15m candle rises open-to-close by at least 5%",
        ),
        (
            "plz montior BTC/USDT when the 15m candle rises open-to-close by at least 5%",
            "BTC/USDT when the 15m candle rises open-to-close by at least 5%",
        ),
    ],
)
async def test_mixed_language_and_noisy_input_still_apply(message: str, instruction: str) -> None:
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        instruction,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    )
                ],
                operations=operations_from_patch(_patch_for(instruction), segment_id="s1"),
                strategy_instructions=[
                    StrategyInstructionPlan(segment_id="s1", intent_summary="15m rise >= 5%")
                ],
                overall_confidence=0.9,
            )
        ),
        reply="Added.",
    )
    result = await _run(script, message)

    assert result.execution is not None
    assert result.execution.strategy_mutated is True
    assert len(_conditions(result.draft)) == 1


# --------------------------------------------------------------------------------
# 16. Approval wording never approves, and an edit invalidates it.
# --------------------------------------------------------------------------------


async def test_approval_wording_inside_a_material_edit_never_approves() -> None:
    base = _draft_with("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%")
    existing = _conditions(base)[0]
    message = "I approve, but first change that to at least 8%"
    replacement = existing.model_copy(
        update={
            "source_turn_id": TURN_ID,
            "source_fragment": "change that to at least 8%",
            "threshold": 8.0,
        }
    )
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        "I approve",
                        SegmentKind.APPROVAL_INTENT,
                        segment_id="s1",
                        reply=True,
                    ),
                    _segment(
                        message,
                        "change that to at least 8%",
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s2",
                        action=True,
                        target=existing.node_id,
                    ),
                ],
                operations=[
                    AuthorizedPatchOperation(
                        authorizing_segment_id="s2",
                        kind="update_condition",
                        condition=replacement,
                        target_condition_id=existing.node_id,
                    )
                ],
                strategy_instructions=[
                    StrategyInstructionPlan(
                        segment_id="s2",
                        intent_summary="raise to 8%",
                        target_condition_id=existing.node_id,
                    )
                ],
                approval_intent=ApprovalIntent(segment_id="s1", accompanied_by_material_edit=True),
                overall_confidence=0.92,
            )
        ),
        reply="Raised it to 8%. That is a new version, so it needs approving again.",
    )
    result = await _run(script, message, draft=base)

    assert result.execution is not None
    assert result.execution.approval_status == "invalidated_by_edit"
    assert result.draft.approval.approved is False


# --------------------------------------------------------------------------------
# 17-18. Failure behaviour.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (httpx.ReadTimeout("slow"), "TARGET_READ_TIMEOUT"),
        (httpx.ConnectError("All connection attempts failed"), "TARGET_CONNECTION_REFUSED"),
        (
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
            "TARGET_PARTIAL_STREAM",
        ),
    ],
)
async def test_a_provider_failure_while_planning_preserves_the_draft(
    failure: Exception, code: str
) -> None:
    """INV: a failed turn is reported as a failure, never as conversation."""
    base = _draft_with("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%")
    script = Script(plan_failure=failure)
    with pytest.raises(SetupAgentError) as error:
        await _run(script, "add a 1h confirmation", draft=base)

    assert error.value.stage == "planning"
    assert error.value.code == code
    assert error.value.retryable is True
    assert error.value.usage["_setup_reserved_cost_usd"] > 0
    assert error.value.usage["_traceedge_model"]


async def test_missing_ai_provider_credentials_are_retryable_and_do_not_mutate() -> None:
    settings = _settings().model_copy(update={"openai_api_key": None})
    agent = SetupChatAgent(settings)
    draft = StrategyDraftV2()

    with pytest.raises(SetupAgentError) as error:
        await agent.run_turn(
            SetupAgentTurnInput(
                # A turn that really needs the planner. "hello" no longer does: a
                # greeting is answered from server-owned wording without any provider
                # call, so it could not prove anything about missing credentials.
                message="add a 1h confirmation to the first rule",
                source_turn_id=TURN_ID,
                draft=draft,
            )
        )

    assert error.value.code == "TARGET_PROVIDER_NOT_CONFIGURED"
    assert error.value.stage == "planning"
    assert error.value.retryable is True
    assert draft.executable_version == 1


async def test_success_is_composed_deterministically_without_a_second_ai_call() -> None:
    """An exact primitive is AI-planned once and reported without a composer call."""
    message = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        message,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    )
                ],
                operations=operations_from_patch(_patch_for(message), segment_id="s1"),
                strategy_instructions=[
                    StrategyInstructionPlan(
                        segment_id="s1", intent_summary="15m open-to-close rise of at least 5%"
                    )
                ],
                overall_confidence=0.95,
            )
        ),
        reply_failure=httpx.ReadTimeout("a forbidden composer call would time out"),
    )
    result = await _run(script, message)

    assert result.execution is not None
    assert result.execution.strategy_mutated is True, "the applied work survives"
    assert result.trace.response_model == "deterministic_summary"
    assert result.trace.model_calls == 1
    assert len(script.planner_payloads) == 1
    assert script.composer_payloads == []
    assert "open to close percentage" in result.reply.message_without_question
    assert BANNED_READINESS_PHRASE not in result.reply.message_without_question.casefold()


async def test_redis_success_bookkeeping_failure_never_repeats_the_model_call() -> None:
    instruction = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    question = "what does the timeframe change?"
    message = f"{instruction} - also {question}"
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        instruction,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    ),
                    _segment(
                        message,
                        question,
                        SegmentKind.USER_QUESTION,
                        segment_id="s2",
                        reply=True,
                    ),
                ],
                operations=operations_from_patch(_patch_for(instruction), segment_id="s1"),
                strategy_instructions=[
                    StrategyInstructionPlan(
                        segment_id="s1",
                        intent_summary="15m open-to-close rise of at least 5%",
                    )
                ],
                questions_to_answer=[question],
                overall_confidence=0.95,
            )
        )
    )

    class RedisWithFailedSuccessWrite:
        async def eval(self, *args, **kwargs):
            return 1

        async def delete(self, *args, **kwargs):
            raise RedisError("shared state unavailable")

    # Installed into the one shared breaker. The agent used to carry a second breaker of
    # its own, so this test could only reach the copy; now it reaches the real one.
    _use_circuit_store(RedisWithFailedSuccessWrite())
    agent = SetupChatAgent(_settings(), transport=script.transport())
    result = await agent.run_turn(
        SetupAgentTurnInput(
            message=message,
            source_turn_id=TURN_ID,
            draft=StrategyDraftV2(),
        )
    )

    assert result.trace.model_calls == 2
    assert len(script.planner_payloads) == 1
    assert len(script.composer_payloads) == 1
    assert result.execution is not None
    assert result.execution.strategy_mutated is True



def _use_circuit_store(client: object) -> None:
    """Give the one process breaker a shared store that is about to misbehave.

    Reaching into the module is deliberate: the breaker is process state on purpose, and
    the test conftest resets it after every test. Building a private breaker here instead
    would test a copy, which is exactly the mistake this refactor removed.
    """

    from ai_market_monitor.services import provider_runtime
    from ai_market_monitor.services.provider_reliability import (
        CircuitBreaker,
        RedisCircuitStateStore,
    )

    provider_runtime._breaker = CircuitBreaker(  # noqa: SLF001
        failure_threshold=5,
        recovery_seconds=60.0,
        store=RedisCircuitStateStore(client),
    )


async def test_redis_outage_does_not_make_healthy_ai_semantics_unavailable() -> None:
    message = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment(
                        message,
                        message,
                        SegmentKind.STRATEGY_INSTRUCTION,
                        segment_id="s1",
                        action=True,
                    )
                ],
                operations=operations_from_patch(_patch_for(message), segment_id="s1"),
                strategy_instructions=[
                    StrategyInstructionPlan(segment_id="s1", intent_summary="15m rise")
                ],
                overall_confidence=0.95,
            )
        )
    )

    class RedisUnavailable:
        async def eval(self, *args, **kwargs):
            raise RedisError("shared state unavailable")

        async def delete(self, *args, **kwargs):
            raise RedisError("shared state unavailable")

    _use_circuit_store(RedisUnavailable())
    agent = SetupChatAgent(_settings(), transport=script.transport())
    result = await agent.run_turn(
        SetupAgentTurnInput(
            message=message,
            source_turn_id=TURN_ID,
            draft=StrategyDraftV2(),
        )
    )

    assert result.execution is not None
    assert result.execution.strategy_mutated
    assert result.trace.model_calls == 1


# --------------------------------------------------------------------------------
# Deterministic authority: what the tool refuses regardless of the plan.
# --------------------------------------------------------------------------------


def _instruction_plan(message: str, quoted: str, **overrides: Any) -> SetupAgentTurnPlan:
    base = {
        "source_turn_id": TURN_ID,
        "segments": [
            _segment(
                message, quoted, SegmentKind.STRATEGY_INSTRUCTION, segment_id="s1", action=True
            )
        ],
        "strategy_instructions": [
            StrategyInstructionPlan(segment_id="s1", intent_summary="a rule")
        ],
        "overall_confidence": 0.9,
    }
    base.update(overrides)
    return SetupAgentTurnPlan(**base)  # type: ignore[arg-type]


async def test_a_span_that_is_not_in_the_message_is_refused() -> None:
    """INV: every applied change is grounded in an exact source segment."""
    message = "Monitor BTC/USDT on the 15m"
    plan = _instruction_plan(message, "Monitor BTC/USDT on the 15m")
    fabricated = plan.model_copy(
        update={
            "segments": [
                plan.segments[0].model_copy(
                    update={
                        "exact_source_text": "Monitor BTC/USDT on the 1h",
                        "end_offset": plan.segments[0].start_offset
                        + len("Monitor BTC/USDT on the 1h"),
                    }
                )
            ]
        }
    )
    with pytest.raises(SetupTurnRejected) as error:
        await apply_setup_turn(
            SetupTurnRequest(
                plan=fabricated,
                message=message,
                draft=StrategyDraftV2(),
                source_turn_id=TURN_ID,
            )
        )
    assert error.value.code == "SPAN_NOT_GROUNDED"


async def test_two_actionable_segments_may_not_claim_the_same_words() -> None:
    message = "Monitor BTC/USDT on the 15m above 50000"
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[
            _segment(
                message,
                "Monitor BTC/USDT on the 15m",
                SegmentKind.STRATEGY_INSTRUCTION,
                segment_id="s1",
                action=True,
            ),
            _segment(
                message,
                "BTC/USDT on the 15m above 50000",
                SegmentKind.STRATEGY_INSTRUCTION,
                segment_id="s2",
                action=True,
            ),
        ],
        overall_confidence=0.9,
    )
    with pytest.raises(SetupTurnRejected) as error:
        await apply_setup_turn(
            SetupTurnRequest(
                plan=plan,
                message=message,
                draft=StrategyDraftV2(),
                source_turn_id=TURN_ID,
            )
        )
    assert error.value.code == "SPAN_NOT_GROUNDED"


async def test_a_threshold_the_message_never_states_is_refused() -> None:
    """A value the trader did not give was chosen by the model, not by them."""
    honest = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    patch = _patch_for(honest)
    # The same patch offered against a message that never mentions 5%.
    message = "Monitor BTC/USDT on the 15m when it rises a bit"
    plan = _instruction_plan(
        message, message, operations=operations_from_patch(patch, segment_id="s1")
    )
    with pytest.raises(SetupTurnRejected) as error:
        await apply_setup_turn(
            SetupTurnRequest(
                plan=plan,
                message=message,
                draft=StrategyDraftV2(),
                source_turn_id=TURN_ID,
            )
        )
    assert error.value.code == "VALUE_NOT_GROUNDED"


async def test_a_reference_to_a_condition_that_does_not_exist_is_refused() -> None:
    message = "remove that rule"
    plan = _instruction_plan(
        message,
        message,
        operations=[
            AuthorizedPatchOperation(
                authorizing_segment_id="s1",
                kind="remove_condition",
                target_condition_id="condition_does_not_exist",
            )
        ],
    )
    with pytest.raises(SetupTurnRejected) as error:
        await apply_setup_turn(
            SetupTurnRequest(
                plan=plan,
                message=message,
                draft=StrategyDraftV2(),
                source_turn_id=TURN_ID,
            )
        )
    assert error.value.code == "CONDITION_NOT_FOUND"


@pytest.mark.parametrize(
    "kind",
    [
        SegmentKind.SOCIAL_REPLY,
        SegmentKind.ACKNOWLEDGEMENT_NO_ACTION,
        SegmentKind.CONVERSATIONAL_CONTEXT,
        SegmentKind.USER_QUESTION,
        SegmentKind.EXPLANATION_REQUEST,
        SegmentKind.PRODUCT_QUESTION,
        SegmentKind.APPROVAL_INTENT,
        SegmentKind.UNSUPPORTED_REQUEST,
    ],
)
async def test_conversation_can_never_be_marked_actionable(kind: SegmentKind) -> None:
    """INV: conversational content cannot become executable logic."""
    message = "some words here"
    with pytest.raises(ValueError, match="cannot require an action"):
        _segment(message, message, kind, segment_id="s1", action=True)


async def test_a_result_cannot_claim_it_applied_something_it_did_not() -> None:
    """INV: every success claim is grounded in the execution result."""
    from uuid import uuid4

    from ai_market_monitor.schemas.setup_agent import SetupTurnExecutionResult

    with pytest.raises(ValueError, match="must record what it applied"):
        SetupTurnExecutionResult(
            status="applied",
            applied=True,
            strategy_mutated=False,
            draft_id=uuid4(),
            previous_version=1,
            current_version=1,
            previous_semantic_hash="",
            current_semantic_hash="",
            compile_status="not_attempted",
        )


async def test_approval_cannot_be_eligible_before_the_draft_compiles() -> None:
    from uuid import uuid4

    from ai_market_monitor.schemas.setup_agent import SetupTurnExecutionResult

    with pytest.raises(ValueError, match="approval cannot be eligible"):
        SetupTurnExecutionResult(
            status="no_change",
            applied=False,
            strategy_mutated=False,
            draft_id=uuid4(),
            previous_version=1,
            current_version=1,
            previous_semantic_hash="",
            current_semantic_hash="",
            compile_status="blocked",
            approval_eligible=True,
        )


# --------------------------------------------------------------------------------
# What a real model actually does. Each of these failed against gpt-5.4-mini first.
# --------------------------------------------------------------------------------


async def test_a_correct_quote_with_wrong_offsets_is_still_accepted() -> None:
    """Language models cannot count characters; the server locates the span itself.

    A real model quoted the message perfectly and then reported offsets that were off
    by several characters. Failing that rejected correct work for no safety gain, so
    the quote is the grounding check and the position is server-derived.
    """
    message = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    quoted = message
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[
            TurnSegment(
                segment_id="s1",
                exact_source_text=quoted,
                start_offset=0,  # deliberately wrong
                end_offset=7,  # deliberately wrong
                kind=SegmentKind.STRATEGY_INSTRUCTION,
                action_required=True,
                confidence=0.9,
            )
        ],
        operations=operations_from_patch(_patch_for(message), segment_id="s1"),
        strategy_instructions=[
            StrategyInstructionPlan(segment_id="s1", intent_summary="15m rise >= 5%")
        ],
        overall_confidence=0.9,
    )
    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=plan,
            message=message,
            draft=StrategyDraftV2(),
            source_turn_id=TURN_ID,
        )
    )
    assert outcome.result.strategy_mutated is True
    # A quote that is genuinely absent is still refused.
    absent = plan.model_copy(
        update={
            "segments": [
                plan.segments[0].model_copy(
                    update={"exact_source_text": "the 1h candle rises by at least 5%"}
                )
            ]
        }
    )
    with pytest.raises(SetupTurnRejected) as error:
        await apply_setup_turn(
            SetupTurnRequest(
                plan=absent,
                message=message,
                draft=StrategyDraftV2(),
                source_turn_id=TURN_ID,
            )
        )
    assert error.value.code == "SPAN_NOT_GROUNDED"


async def test_a_value_outside_the_authorizing_segment_is_refused() -> None:
    """INV: values from one segment cannot authorize another segment's mutation.

    The authorising span has to carry the values it authorises. Message-wide grounding
    is not authorization: in `drop LTC, and is 5% a lot on a 15m candle?` the 5% and the
    15m belong to a *question*, and accepting them anywhere in the message let a question
    author a rule.
    """
    message = "Monitor BTC/USDT on the 15m when the candle rises open-to-close by at least 5%"
    # The segment quotes only the clause, leaving the timeframe outside it.
    quoted = "the candle rises open-to-close by at least 5%"
    patch = _patch_for(message)
    node = patch.add_conditions[0].model_copy(update={"source_fragment": quoted})
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[
            _segment(
                message,
                quoted,
                SegmentKind.STRATEGY_INSTRUCTION,
                segment_id="s1",
                action=True,
            )
        ],
        operations=operations_from_patch(
            patch.model_copy(update={"add_conditions": [node]}), segment_id="s1"
        ),
        overall_confidence=0.9,
    )
    with pytest.raises(SetupTurnRejected) as error:
        await apply_setup_turn(
            SetupTurnRequest(
                plan=plan,
                message=message,
                draft=StrategyDraftV2(),
                source_turn_id=TURN_ID,
            )
        )
    assert error.value.code == "VALUE_NOT_GROUNDED"
    assert any("trigger_timeframe" in item for item in error.value.details)


async def test_a_segment_that_carries_its_own_values_is_accepted() -> None:
    """The same turn works when the authorising span covers what it authorises."""
    message = "Monitor BTC/USDT on the 15m when the candle rises open-to-close by at least 5%"
    # The span covers the symbol, the timeframe and the size — everything it authorises.
    quoted = message
    patch = _patch_for(message)
    node = patch.add_conditions[0].model_copy(update={"source_fragment": quoted})
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[
            _segment(
                message,
                quoted,
                SegmentKind.STRATEGY_INSTRUCTION,
                segment_id="s1",
                action=True,
            )
        ],
        operations=operations_from_patch(
            patch.model_copy(update={"add_conditions": [node]}), segment_id="s1"
        ),
        overall_confidence=0.9,
    )
    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=plan,
            message=message,
            draft=StrategyDraftV2(),
            source_turn_id=TURN_ID,
        )
    )
    condition = _conditions(outcome.draft)[0]
    assert condition.trigger_timeframe == "15m"
    assert condition.threshold == 5.0


async def test_condition_symbol_may_be_grounded_before_its_rule_clause() -> None:
    message = (
        "BTCUSDT only on Binance spot. "
        "Require a bullish close-to-close move of at least 1% on the 15m."
    )
    clause = "Require a bullish close-to-close move of at least 1% on the 15m."
    patch = _patch_for(message)
    node = patch.add_conditions[0].model_copy(
        update={"source_fragment": clause, "condition_symbols": ["BTCUSDT"]}
    )
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[
            _segment(
                message,
                message,
                SegmentKind.STRATEGY_INSTRUCTION,
                segment_id="s1",
                action=True,
            )
        ],
        operations=operations_from_patch(
            patch.model_copy(update={"add_conditions": [node]}), segment_id="s1"
        ),
        overall_confidence=0.9,
    )

    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=plan,
            message=message,
            draft=StrategyDraftV2(),
            source_turn_id=TURN_ID,
        )
    )

    assert _conditions(outcome.draft)[0].condition_symbols == ["BTCUSDT"]


async def test_a_hint_pointing_at_a_new_rule_is_dropped_not_fatal() -> None:
    """A real model labels the rule it is creating. That is a label, not a mutation."""
    message = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[
            _segment(
                message,
                message,
                SegmentKind.STRATEGY_INSTRUCTION,
                segment_id="s1",
                action=True,
                target="cond_1",
            )
        ],
        operations=operations_from_patch(_patch_for(message), segment_id="s1"),
        strategy_instructions=[
            StrategyInstructionPlan(
                segment_id="s1", intent_summary="15m rise", target_condition_id="cond_1"
            )
        ],
        overall_confidence=0.9,
    )
    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=plan,
            message=message,
            draft=StrategyDraftV2(),
            source_turn_id=TURN_ID,
        )
    )
    assert outcome.result.strategy_mutated is True
    # But an edit that names a rule which does not exist is still refused.
    with pytest.raises(SetupTurnRejected):
        await apply_setup_turn(
            SetupTurnRequest(
                plan=plan.model_copy(
                    update={
                        "operations": [
                            AuthorizedPatchOperation(
                                authorizing_segment_id="s1",
                                kind="remove_condition",
                                target_condition_id="cond_1",
                            )
                        ]
                    }
                ),
                message=message,
                draft=StrategyDraftV2(),
                source_turn_id=TURN_ID,
            )
        )


async def test_a_strict_schema_null_for_a_container_uses_the_default() -> None:
    """A strict schema requires every key, so `null` means "nothing to set" here."""
    patch = StrategyPatch.model_validate(
        {
            "source_turn_id": TURN_ID,
            "set_fields": None,
            "add_conditions": None,
            "correction": None,
        }
    )
    assert patch.set_fields.mode is None
    assert patch.add_conditions == []
    assert patch.correction is None


def test_unsupported_operation_forces_server_owned_blocking() -> None:
    operation = AuthorizedPatchOperation.model_validate(
        {
            "operation_id": "unsupported-1",
            "authorizing_segment_id": "s1",
            "kind": "add_unsupported",
            "missing_contract": "order-flow delta is not configured",
            "blocking": False,
        }
    )

    assert operation.kind == "add_unsupported"
    assert not hasattr(operation, "blocking")


def test_incomplete_condition_is_rejected_for_the_bounded_repair_stage() -> None:
    message = "Use a clean liquidity signal near support."
    with pytest.raises(ValidationError):
        SetupAgentTurnPlan.model_validate(
            {
                "source_turn_id": TURN_ID,
                "segments": [
                    {
                        "segment_id": "s1",
                        "exact_source_text": message,
                        "start_offset": 0,
                        "end_offset": len(message),
                        "kind": "STRATEGY_INSTRUCTION",
                        "action_required": True,
                        "confidence": 0.9,
                    }
                ],
                "operations": [
                    {
                        "operation_id": "incomplete-condition-1",
                        "authorizing_segment_id": "s1",
                        "kind": "add_condition",
                        "condition": {
                            "node_type": "condition",
                            "formula": "capability",
                            "capability_key": "liquidity_sweep",
                            "operator": None,
                        },
                    }
                ],
                "overall_confidence": 0.9,
            }
        )


def test_uniquely_wrapped_core_formula_is_normalized_without_guessing() -> None:
    message = "Use a bullish close-to-close move of at least 5% on 5m."
    plan = SetupAgentTurnPlan.model_validate(
        {
            "source_turn_id": TURN_ID,
            "segments": [
                {
                    "segment_id": "s1",
                    "exact_source_text": message,
                    "start_offset": 0,
                    "end_offset": len(message),
                    "kind": "STRATEGY_INSTRUCTION",
                    "action_required": True,
                    "confidence": 0.9,
                }
            ],
            "operations": [
                {
                    "operation_id": "wrapped-formula-1",
                    "authorizing_segment_id": "s1",
                    "kind": "add_condition",
                    "condition": {
                        "node_type": "condition",
                        "formula": {"value": "close_to_close_percentage"},
                        "operator": "gte",
                        "threshold": 5,
                        "movement_direction": "up",
                        "trigger_timeframe": "5m",
                    },
                }
            ],
            "overall_confidence": 0.9,
        }
    )

    condition = plan.operations[0].condition
    assert condition is not None
    assert condition.formula is FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE
    assert condition.operands[0].parameters["formula"] == "close_to_close"


def test_unknown_wrapped_formula_fails_closed_without_a_type_error() -> None:
    message = "Use my private signal object."
    with pytest.raises(ValidationError):
        SetupAgentTurnPlan.model_validate(
            {
                "source_turn_id": TURN_ID,
                "segments": [
                    {
                        "segment_id": "s1",
                        "exact_source_text": message,
                        "start_offset": 0,
                        "end_offset": len(message),
                        "kind": "STRATEGY_INSTRUCTION",
                        "action_required": True,
                        "confidence": 0.9,
                    }
                ],
                "operations": [
                    {
                        "operation_id": "unknown-formula-1",
                        "authorizing_segment_id": "s1",
                        "kind": "add_condition",
                        "condition": {
                            "node_type": "condition",
                            "formula": {"value": "private_signal"},
                            "operator": "is_true",
                        },
                    }
                ],
                "overall_confidence": 0.4,
            }
        )


def test_incomplete_restore_proposal_requires_bounded_plan_repair() -> None:
    message = "Use Scanner and keep approval explicit."
    with pytest.raises(ValidationError):
        SetupAgentTurnPlan.model_validate(
            {
                "source_turn_id": TURN_ID,
                "segments": [
                    {
                        "segment_id": "s1",
                        "exact_source_text": "Use Scanner",
                        "start_offset": 0,
                        "end_offset": 11,
                        "kind": "STRATEGY_INSTRUCTION",
                        "action_required": True,
                        "confidence": 0.9,
                    },
                    {
                        "segment_id": "s2",
                        "exact_source_text": "keep approval explicit",
                        "start_offset": 16,
                        "end_offset": len(message) - 1,
                        "kind": "APPROVAL_INTENT",
                        "action_required": False,
                        "confidence": 0.9,
                    },
                ],
                "operations": [
                    {
                        "operation_id": "set-mode",
                        "authorizing_segment_id": "s1",
                        "kind": "set_fields",
                        "fields": {"mode": "scanner"},
                    },
                    {
                        "operation_id": "invalid-restore",
                        "authorizing_segment_id": "s2",
                        "kind": "restore_snapshot",
                    },
                ],
                "approval_intent": {
                    "segment_id": "s2",
                    "accompanied_by_material_edit": True,
                },
                "overall_confidence": 0.9,
            }
        )


def test_operation_payload_canonicalization_preserves_exact_unresolved_identity() -> None:
    unresolved = {
        "unresolved_id": "threshold-question",
        "source_turn_id": TURN_ID,
        "source_fragment": "at most 0.5%",
        "target_type": "condition_creation",
        "expected_answer_schema": {"type": "number"},
        "question": "What threshold?",
        "reason": "A number is required.",
        "blocking": True,
    }
    update = AuthorizedPatchOperation.model_validate(
        {
            "operation_id": "update-question",
            "authorizing_segment_id": "s1",
            "kind": "update_unresolved",
            "unresolved": unresolved,
        }
    )
    resolved = AuthorizedPatchOperation.model_validate(
        {
            "operation_id": "resolve-question",
            "authorizing_segment_id": "s1",
            "kind": "resolve_unresolved_key",
            "target_key": "threshold-question",
            "target_executable_version": 99,
        }
    )

    assert update.target_key == "threshold-question"
    assert resolved.target_key == "threshold-question"
    assert resolved.target_executable_version is None


def test_one_long_strategy_segment_preserves_the_exact_authorizing_text() -> None:
    message = "Keep the exact fields and explanation concise. " * 30
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[
            TurnSegment(
                segment_id="long-strategy",
                exact_source_text=message,
                start_offset=0,
                end_offset=len(message),
                kind=SegmentKind.STRATEGY_INSTRUCTION,
                action_required=True,
                confidence=0.9,
            )
        ],
        overall_confidence=0.9,
    )

    assert len(plan.segments[0].exact_source_text) > 1_000
    assert plan.segments[0].exact_source_text == message


def test_one_long_clarification_answer_preserves_the_exact_user_text() -> None:
    message = "Use these exact measurable clarification values. " * 30
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN_ID,
        segments=[
            TurnSegment(
                segment_id="clarification-answer",
                exact_source_text=message,
                start_offset=0,
                end_offset=len(message),
                kind=SegmentKind.CLARIFICATION_ANSWER,
                action_required=True,
                confidence=0.9,
            )
        ],
        clarification_answers=[
            ClarificationAnswer(
                segment_id="clarification-answer",
                question_id="condition-creation-1",
                answer_text=message,
            )
        ],
        overall_confidence=0.9,
    )

    assert len(plan.clarification_answers[0].answer_text) > 1_000
    assert plan.clarification_answers[0].answer_text == message


def test_unresolved_condition_field_without_an_id_stays_blocking_creation() -> None:
    unresolved = UnresolvedFieldV2.model_validate(
        {
            "unresolved_id": "missing-reference-1",
            "source_turn_id": TURN_ID,
            "source_fragment": "from local swing low",
            "target_type": "reference_definition",
            "target_field": "reference_definition",
            "target_condition_id": None,
            "expected_answer_schema": {"type": "string"},
            "question": "How should the local swing low be measured?",
            "reason": "The reference is not measurable yet.",
            "blocking": True,
        }
    )

    assert unresolved.target_type == "condition_creation"
    assert unresolved.target_condition_id is None
    assert unresolved.target_field is None
    assert unresolved.blocking is True


def test_condition_creation_discards_a_planner_authored_future_id() -> None:
    unresolved = UnresolvedFieldV2.model_validate(
        {
            "unresolved_id": "missing-threshold-1",
            "source_turn_id": TURN_ID,
            "source_fragment": "a strong bearish move",
            "target_type": "condition_creation",
            "target_field": "threshold",
            "target_condition_id": "planner-future-condition-id",
            "expected_answer_schema": {"type": "number"},
            "question": "What percentage should strong mean?",
            "reason": "Strong is not measurable yet.",
            "blocking": True,
        }
    )

    assert unresolved.target_type == "condition_creation"
    assert unresolved.target_condition_id is None
    assert unresolved.target_field == "threshold"


def test_numeric_unresolved_contract_discards_incompatible_string_options() -> None:
    unresolved = UnresolvedFieldV2.model_validate(
        {
            "unresolved_id": "numeric-threshold",
            "source_turn_id": TURN_ID,
            "source_fragment": "at most 0.5%",
            "target_type": "condition_creation",
            "expected_answer_schema": {"type": "number"},
            "allowed_options": ["gte", "lte"],
            "question": "What threshold?",
            "reason": "A number is required.",
        }
    )

    assert unresolved.expected_answer_schema == {"type": "number"}
    assert unresolved.allowed_options == []


async def test_future_condition_id_is_normalized_to_one_typed_creation_question() -> None:
    message = "Alert on a strong bullish close-to-close move."
    plan = SetupAgentTurnPlan.model_validate(
        {
            "source_turn_id": TURN_ID,
            "segments": [
                {
                    "segment_id": "s1",
                    "exact_source_text": message,
                    "start_offset": 0,
                    "end_offset": len(message),
                    "kind": "STRATEGY_INSTRUCTION",
                    "action_required": True,
                    "confidence": 0.9,
                }
            ],
            "operations": [
                {
                    "operation_id": "ask-strong-threshold",
                    "authorizing_segment_id": "s1",
                    "kind": "add_unresolved",
                    "unresolved": {
                        "unresolved_id": "missing-strong-threshold",
                        "source_turn_id": TURN_ID,
                        "source_fragment": message,
                        "target_type": "condition_field",
                        "target_field": "threshold",
                        "target_condition_id": "planner-invented-future-id",
                        "expected_answer_schema": {"type": "number"},
                        "question": "What percentage should strong mean?",
                        "reason": "Strong needs a measurable threshold.",
                        "blocking": True,
                    },
                },
            ],
            "overall_confidence": 0.8,
        }
    )

    # The invalid core-rule proposal is not duplicated as unsupported when its
    # exact segment already has one typed clarification authority.
    assert [operation.kind for operation in plan.operations] == ["add_unresolved"]

    before = StrategyDraftV2()
    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=plan,
            message=message,
            draft=before,
            source_turn_id=TURN_ID,
        )
    )

    assert len(outcome.draft.unresolved_fields) == 1
    unresolved = outcome.draft.unresolved_fields[0]
    assert unresolved.target_type == "condition_creation"
    assert unresolved.target_condition_id is None
    assert unresolved.target_field is None
    assert unresolved.expected_answer_schema == {"type": "number"}
    assert outcome.draft.executable_version == before.executable_version
    assert outcome.draft.workflow_revision == before.workflow_revision + 1


async def test_the_deterministic_summary_only_states_what_the_result_holds() -> None:
    from uuid import uuid4

    from ai_market_monitor.schemas.setup_agent import (
        AppliedInstruction,
        SetupTurnExecutionResult,
    )

    result = SetupTurnExecutionResult(
        status="applied",
        applied=True,
        strategy_mutated=True,
        draft_id=uuid4(),
        previous_version=1,
        current_version=2,
        previous_semantic_hash="a" * 64,
        current_semantic_hash="b" * 64,
        applied_instructions=[
            AppliedInstruction(
                segment_id="s1",
                source_text="drop LTC",
                summary="excluded LTC/USDT",
            )
        ],
        compile_status="compiled",
        approval_eligible=True,
    )
    summary = deterministic_summary(result)
    assert "excluded LTC/USDT" in summary
    assert "version 2" in summary
    assert BANNED_READINESS_PHRASE not in summary.casefold()
