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
*real* deterministic tool and the *real* compiler all run. Only the two network calls
are faked. Assertions look at what the server did, never at the assistant's wording.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.capability_shortlist import build_capability_shortlist
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
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    StrategyDraftV2,
    StrategyPatch,
)
from ai_market_monitor.services.setup_chat_agent import (
    SetupAgentError,
    SetupAgentTurnInput,
    SetupChatAgent,
    deterministic_summary,
)
from ai_market_monitor.services.strategy_patch_extractor import deterministic_strategy_patch
from tests.support.setup_agent_plans import operations_from_patch

#: The sentence this rebuild exists to remove. No reply may contain it.
BANNED_READINESS_PHRASE = "describe the market behavior you want to scan or monitor"

TURN_ID = "turn-00000001"


def _settings() -> Settings:
    return Settings(
        app_secret_key="setup-agent-secret-with-at-least-32-characters",
        openai_api_key=SecretStr("test-key"),
        sharia_screening_enforced=False,
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
            if name == "hilalmarkets_setup_turn_plan":
                self.planner_payloads.append(payload)
                if self.plan_failure is not None:
                    raise self.plan_failure
                assert self.plan is not None, "the test did not script a plan"
                return httpx.Response(200, json=_responses_body(self.plan.model_dump_json()))
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
    patch = deterministic_strategy_patch(
        draft or StrategyDraftV2(), text, source_turn_id=TURN_ID
    )
    assert patch is not None, f"no deterministic patch for {text!r}"
    return patch


def _conditions(draft: StrategyDraftV2) -> list[Any]:
    if draft.condition_ast is None:
        return []
    return [
        node
        for node in draft.condition_ast.walk()
        if node.node_type is ConditionNodeType.CONDITION
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
    assert script.schema_names == ["hilalmarkets_setup_turn_plan"], "one call only"
    assert BANNED_READINESS_PHRASE not in result.reply.message.casefold()


async def test_a_conversation_turn_with_no_model_words_still_reports_real_state() -> None:
    """Even the last-resort reply describes the draft rather than resetting the user."""
    script = Script(
        plan=SetupAgentPlanEnvelope(
            plan=SetupAgentTurnPlan(
                source_turn_id=TURN_ID,
                segments=[
                    _segment("hello", "hello", SegmentKind.SOCIAL_REPLY, segment_id="s1")
                ],
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
    assert question in script.composer_payloads[0]["questions_to_answer"]
    kinds = {item.kind for item in result.execution.ignored_non_actionable_segments}
    assert SegmentKind.USER_QUESTION in kinds


# --------------------------------------------------------------------------------
# 6-8. Corrections, clarification answers and references to earlier turns.
# --------------------------------------------------------------------------------


async def test_a_correction_updates_the_named_condition_without_adding_one() -> None:
    base = _draft_with(
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    )
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


async def test_a_mutating_question_does_not_close_until_its_target_changes() -> None:
    """INV: a clarification cannot clear without resolving its declared target.

    Trusting `resolves_question` let an open item disappear while the draft stayed
    blocked for exactly the reason the question existed.
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

    assert result.execution is not None
    assert result.execution.answered_questions == [], "words alone cannot close it"
    assert result.conversation.active_question_id == "timeframe", "it stays open"


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


async def test_a_reference_to_an_earlier_condition_reaches_the_planner() -> None:
    """The agent must be given enough context to resolve ordinary references."""
    base = _draft_with(
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    )
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
    assert payload["conversation_context"]["last_changed_condition_ids"] == [existing.node_id]
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
    base = _draft_with(
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    )
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
    assert payload["lexical_hint_non_authoritative"], "the hint travels as a hint"


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
async def test_mixed_language_and_noisy_input_still_apply(
    message: str, instruction: str
) -> None:
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
    base = _draft_with(
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    )
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
                approval_intent=ApprovalIntent(
                    segment_id="s1", accompanied_by_material_edit=True
                ),
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
    base = _draft_with(
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"
    )
    script = Script(plan_failure=failure)
    with pytest.raises(SetupAgentError) as error:
        await _run(script, "add a 1h confirmation", draft=base)

    assert error.value.stage == "planning"
    assert error.value.code == code
    assert error.value.retryable is True


async def test_a_composing_failure_after_success_reports_what_actually_changed() -> None:
    """The work is durable, so it is described from the result, not thrown away."""
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
        reply_failure=httpx.ReadTimeout("composer timed out"),
    )
    result = await _run(script, message)

    assert result.execution is not None
    assert result.execution.strategy_mutated is True, "the applied work survives"
    assert result.trace.failure_stage == "response_composition"
    assert "open to close percentage" in result.reply.message
    assert BANNED_READINESS_PHRASE not in result.reply.message.casefold()


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
