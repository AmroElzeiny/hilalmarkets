"""Typo confirmation, question ownership and cancellation, through the real agent.

Every transcript here is run against the real ``SetupChatAgent``, the real draft, the
real canonical execution path and the real conversation record. Only the model provider
is a stub, and counting its calls is itself an assertion: answering our own question
must not need a model.

The defects each group proves are gone:

* a *did you mean 1h?* that nothing could answer — ``yes`` was thrown away, and the
  timeframe question came back a third time;
* a question that only owned the turn when a supported-rule blocker happened to exist,
  so every other kind of question leaked its answer into general routing;
* a cancellation that cleared the question and left its blocker in the draft, leaving
  the setup blocked for a reason nothing on screen mentioned any more.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.active_clarification import (
    TransitionOutcome,
    resolve_active_clarification_turn,
    workflow_invariants,
)
from ai_market_monitor.engine.clarification_continuation import continuation_for_unresolved
from ai_market_monitor.engine.conversation_language import ConversationLanguage
from ai_market_monitor.schemas.setup_agent import SetupConversationContext
from ai_market_monitor.schemas.setup_authorization import (
    CancellationPolicy,
    ClarificationContract,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    DraftMode,
    ShariaPolicyV2,
    StrategyDraftV2,
    UnresolvedFieldV2,
)
from ai_market_monitor.services.setup_chat_agent import (
    SetupAgentTurnInput,
    SetupChatAgent,
)

pytestmark = pytest.mark.anyio

ALERT_REQUEST = "Inform me once any coin increases 5%"

#: The reply every one of these transcripts used to end in.
GENERIC_MISS = "nothing is set up"


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
class _Planner:
    """A model that reports one supported-but-incomplete percentage request."""

    source_text: str = ALERT_REQUEST
    missing: tuple[str, ...] = ("trigger_timeframe", "reference_point")
    calls: int = 0
    payloads: list[dict[str, Any]] = field(default_factory=list)

    def envelope(self, message: str) -> str:
        said = message or self.source_text
        incomplete = said.strip() == self.source_text
        return json.dumps(
            {
                "segments": [
                    {
                        "segment_ref": "segment_1",
                        "exact_source_text": said,
                        "segment_kind": (
                            "STRATEGY_INSTRUCTION" if incomplete else "CONVERSATIONAL_CONTEXT"
                        ),
                    }
                ],
                "semantic_intents": [],
                "clarification_answers": [],
                "questions_to_answer": [],
                "supported_incomplete_intents": (
                    [{"segment_ref": "segment_1", "missing_fields": list(self.missing)}]
                    if incomplete
                    else []
                ),
                "unsupported_intents": [],
                "approval_intent": None,
                "overall_confidence": 0.95,
            }
        )

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            payload = json.loads(body["input"])
            self.payloads.append(payload)
            if body["text"]["format"]["name"] == "hilalmarkets_setup_turn_intent":
                self.calls += 1
                said = str(payload.get("current_user_turn") or "")
                return httpx.Response(200, json=_responses_body(self.envelope(said)))
            return httpx.Response(
                200,
                json=_responses_body(
                    json.dumps({"message": "Done.", "clarification_question_id": None})
                ),
            )

        return httpx.MockTransport(handler)


class _Session:
    """Carry draft and conversation forward exactly as the launch service does."""

    def __init__(
        self,
        planner: _Planner | None = None,
        *,
        draft: StrategyDraftV2 | None = None,
        conversation: SetupConversationContext | None = None,
        mode: DraftMode | None = None,
    ) -> None:
        self.planner = planner or _Planner()
        self.agent = SetupChatAgent(_settings(), transport=self.planner.transport())
        self.draft = draft if draft is not None else StrategyDraftV2()
        self.conversation = conversation or SetupConversationContext()
        self.mode = mode or self.draft.mode
        self.turns = 0
        self.replies: list[str] = []
        self.scan_requests: list[dict[str, object] | None] = []

    async def say(self, message: str) -> Any:
        self.turns += 1
        result = await self.agent.run_turn(
            SetupAgentTurnInput(
                message=message,
                source_turn_id=f"turn-{self.turns}",
                draft=self.draft,
                conversation=self.conversation,
                setup_mode=self.mode,
                active_language=self.conversation.active_language,
            )
        )
        self.draft = result.draft
        self.conversation = result.conversation
        self.replies.append(result.message)
        self.scan_requests.append(result.read_only_scan_request)
        self.assert_invariants()
        return result

    def assert_invariants(self) -> None:
        """Every state rule, after every persisted turn. Empty is the only pass."""

        assert workflow_invariants(self.conversation) == ()

    @property
    def question(self) -> str:
        contract = self.conversation.active_question
        return contract.question if contract is not None else ""

    @property
    def accepted(self) -> dict[str, Any]:
        workflow = self.conversation.pending_workflow
        return dict(workflow.accepted_values) if workflow is not None else {}

    @property
    def proposed(self) -> str | None:
        workflow = self.conversation.pending_workflow
        return workflow.proposed_value if workflow is not None else None

    @property
    def step(self) -> int:
        workflow = self.conversation.pending_workflow
        return workflow.step_revision if workflow is not None else -1

    @property
    def unresolved_ids(self) -> set[str]:
        return {item.unresolved_id for item in self.draft.unresolved_fields}

    @property
    def condition_count(self) -> int:
        if self.draft.condition_ast is None:
            return 0
        return len(list(self.draft.condition_ast.walk()))


def _scanner_ready_draft() -> StrategyDraftV2:
    """A Scanner draft whose screened scope is already settled.

    The scope question is a separate governed workflow with its own tests. These cases
    are about the *window* question owning its own answer, so the scope is set up front
    rather than becoming a second question in the middle of the transcript.
    """

    return StrategyDraftV2(
        mode=DraftMode.SCANNER,
        sharia_policy=ShariaPolicyV2(
            methodology_id=uuid4(),
            methodology_version="2026.1",
        ),
    )


def _period_question(session: _Session) -> str:
    assert "period" in session.question.casefold(), session.question
    return session.question


# ---------------------------------------------------------------------------------
# The reported transcript: a typo, confirmed
# ---------------------------------------------------------------------------------


async def test_a_typo_confirmed_with_yes_completes_the_whole_rule() -> None:
    """``5%`` → ``qh`` → ``yes`` → ``Candle open``. Nothing is asked twice."""

    session = _Session()
    await session.say(ALERT_REQUEST)
    _period_question(session)
    first_step = session.step

    await session.say("qh")
    assert session.proposed == "1h", "a near miss must be held, not stored"
    assert "1h" in session.replies[-1]
    assert session.accepted == {}, "nothing is stored before a yes"
    assert session.step == first_step, "asking for confirmation is not a step"

    await session.say("yes")
    assert session.proposed is None, "a confirmed proposal is cleared"
    assert session.accepted.get("trigger_timeframe") == "1h"
    assert session.step == first_step + 1, "a confirmed answer advances exactly once"
    assert "period" not in session.question.casefold(), session.question

    await session.say("Candle open")
    assert session.accepted.get("reference_point") == "candle_open"
    assert session.step == first_step + 2
    assert "period" not in session.question.casefold(), "the period is never re-asked"

    await session.say("at least")
    assert session.conversation.active_question is None
    assert session.condition_count == 1, "the rule is built, once"
    joined = " ".join(session.replies).casefold()
    assert GENERIC_MISS not in joined
    assert session.planner.calls == 1, "only the first turn needed a model"


async def test_a_rejected_typo_keeps_the_question_and_the_right_answer_still_works() -> None:
    """``5%`` → ``qh`` → ``no`` → ``1 minute`` → ``Candle open``. No resets."""

    session = _Session()
    await session.say(ALERT_REQUEST)
    first_step = session.step

    await session.say("qh")
    assert session.proposed == "1h"

    await session.say("no")
    assert session.proposed is None, "a rejected proposal is dropped"
    assert session.accepted == {}, "and nothing is stored in its place"
    assert session.step == first_step, "rejecting is not a step"
    _period_question(session)

    await session.say("1 minute")
    assert session.accepted.get("trigger_timeframe") == "1m"
    assert session.step == first_step + 1

    await session.say("Candle open")
    assert session.accepted.get("reference_point") == "candle_open"
    assert session.accepted.get("trigger_timeframe") == "1m", "the rejection is respected"

    await session.say("at least")
    assert session.condition_count == 1
    assert session.conversation.active_question is None
    assert session.planner.calls == 1


async def test_an_unclear_confirmation_asks_the_same_yes_or_no_again() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    await session.say("qh")
    assert session.proposed == "1h"

    await session.say("maybe")
    assert session.proposed == "1h", "the proposal survives an unclear reply"
    assert session.accepted == {}
    assert "1h" in session.replies[-1], "the reply names what it is asking about"
    assert session.planner.calls == 1, "an unclear confirmation must not reach the model"

    await session.say("yes")
    assert session.accepted.get("trigger_timeframe") == "1h"


@pytest.mark.parametrize(
    ("yes", "language"),
    [("yes", "en"), ("تمام", "ar"), ("d'accord", "fr"), ("vale", "es"), ("да", "ru")],
)
async def test_a_typo_can_be_confirmed_in_any_language(yes: str, language: str) -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    await session.say("qh")
    assert session.proposed == "1h"
    await session.say(yes)
    assert session.accepted.get("trigger_timeframe") == "1h", f"{language}: {yes}"
    assert session.proposed is None


@pytest.mark.parametrize("no", ["no", "not that", "لا", "non", "нет"])
async def test_a_typo_can_be_rejected_in_any_language(no: str) -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    await session.say("qh")
    await session.say(no)
    assert session.proposed is None
    assert session.accepted == {}
    _period_question(session)


async def test_correcting_yourself_beats_the_pending_guess() -> None:
    """``qh`` then ``4h`` stores 4h. Nobody should have to say "no" first."""

    session = _Session()
    await session.say(ALERT_REQUEST)
    await session.say("qh")
    await session.say("4h")
    assert session.accepted.get("trigger_timeframe") == "4h"
    assert session.proposed is None


async def test_a_confirmation_is_one_question_not_two() -> None:
    """"Did you mean 1h?" must not arrive with the list of periods stapled under it."""

    session = _Session()
    await session.say(ALERT_REQUEST)
    period = _period_question(session)
    result = await session.say("qh")
    assert period not in result.message
    assert result.message.count("?") == 1, result.message


# ---------------------------------------------------------------------------------
# Ownership: the visible question keeps the turn
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    ["qh", "purple bananas", "hmm", "1 gazillion", "؟؟x", "asdf"],
)
async def test_an_answer_the_reader_cannot_use_never_becomes_a_new_request(
    message: str,
) -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    before = dict(session.accepted)
    result = await session.say(message)
    assert session.conversation.active_question is not None, "the question stays open"
    assert session.accepted == before, "nothing already chosen is lost"
    assert GENERIC_MISS not in result.message.casefold()
    assert session.planner.calls == 1, "the model is never asked about our own question"


async def test_a_planner_generated_question_owns_its_answer_too() -> None:
    """Not only supported-rule steps. A compiler-built question behaves the same."""

    unresolved = UnresolvedFieldV2(
        unresolved_id="draft_name",
        source_turn_id="turn-0",
        source_fragment="watch BTC",
        target_type="draft_field",
        target_field="trigger_timeframe",
        expected_answer_schema={"type": "string"},
        question="Which candle period should I use?",
        reason="the setup cannot compile without a period",
        allowed_options=["1h", "4h", "1d"],
    )
    contract = ClarificationContract(
        question_id="draft_name",
        question=unresolved.question,
        reason=unresolved.reason,
        target_type="draft_field",
        target_field="trigger_timeframe",
        expected_answer_schema='{"type":"string"}',
        allowed_options=list(unresolved.allowed_options),
    )
    session = _Session(
        draft=StrategyDraftV2(unresolved_fields=[unresolved]),
        conversation=SetupConversationContext().with_question(contract),
    )
    result = await session.say("qh")
    assert session.conversation.active_question is not None
    assert "1h" in result.message, "a typo is asked about, not routed away"
    assert session.planner.calls == 0, "a deterministic reading needs no model call"


async def test_a_planner_authored_question_applies_its_own_answer_deterministically() -> None:
    """The reversal of the old rule. The planner is not told the answer — it is not asked.

    This test used to assert the opposite: that a question the planner raised handed its
    decided answer *back* to the planner to build the operation. That was the defect. The
    same message went to a model twice, and the second model had never seen the question,
    so a bare ``1h`` read as a timeframe with nothing attached and the answer was lost.

    The question now carries its own completion, built and checked before it was shown,
    so answering it costs nothing and cannot be re-read as anything else.
    """

    unresolved = UnresolvedFieldV2(
        unresolved_id="draft_name",
        source_turn_id="turn-0",
        source_fragment="call it my breakout watch",
        target_type="draft_field",
        target_field="name",
        expected_answer_schema={"type": "string"},
        question="What should I call this setup?",
        reason="the setup needs a name",
    )
    draft = StrategyDraftV2(unresolved_fields=[unresolved])
    continuation = continuation_for_unresolved(
        unresolved,
        draft,
        question_id="draft_name",
        step_revision=0,
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    )
    assert continuation is not None, "a mutating question must carry its completion"
    contract = ClarificationContract(
        question_id="draft_name",
        question=unresolved.question,
        reason=unresolved.reason,
        target_type="draft_field",
        target_field="name",
        expected_answer_schema='{"type":"string"}',
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        continuation=continuation,
    )
    session = _Session(
        draft=draft,
        conversation=SetupConversationContext().with_question(contract),
    )

    result = await session.say("My breakout watch")

    assert session.planner.calls == 0, "answering our own question needs no model"
    assert result.execution is not None, "the answer really became canonical state"
    assert result.draft.name == "My breakout watch"
    assert session.unresolved_ids == set(), "the blocker it answered is closed"


async def test_an_answer_never_reaches_the_planner_as_a_decided_value() -> None:
    """There is no "decided answer" channel any more, because nothing needs one."""

    unresolved = UnresolvedFieldV2(
        unresolved_id="draft_name_2",
        source_turn_id="turn-0",
        source_fragment="call it my breakout watch",
        target_type="draft_field",
        target_field="name",
        expected_answer_schema={"type": "string"},
        question="What should I call this setup?",
        reason="the setup needs a name",
    )
    draft = StrategyDraftV2(unresolved_fields=[unresolved])
    continuation = continuation_for_unresolved(
        unresolved,
        draft,
        question_id="draft_name_2",
        step_revision=0,
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    )
    contract = ClarificationContract(
        question_id="draft_name_2",
        question=unresolved.question,
        reason=unresolved.reason,
        target_type="draft_field",
        target_field="name",
        expected_answer_schema='{"type":"string"}',
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        continuation=continuation,
    )
    session = _Session(
        draft=draft,
        conversation=SetupConversationContext().with_question(contract),
    )

    await session.say("My breakout watch")

    assert session.planner.payloads == [], "no planner call was made at all"


# ---------------------------------------------------------------------------------
# Cancellation leaves a state a trader can see and act on
# ---------------------------------------------------------------------------------


async def test_cancelling_an_unfinished_rule_removes_its_blocker() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    assert session.unresolved_ids, "the half-built rule is canonical while it is open"

    result = await session.say("cancel")
    assert session.unresolved_ids == set(), "the blocker goes with the question"
    assert session.conversation.active_question is None
    assert session.conversation.pending_workflow is None
    assert session.conversation.pending_supported_request == {}
    assert session.condition_count == 0, "cancelling never builds a half-made rule"
    assert result.execution is not None, "removal is a canonical operation, not a note"


async def test_cancelling_one_workflow_leaves_unrelated_blockers_alone() -> None:
    other = UnresolvedFieldV2(
        unresolved_id="sharia.universe_mode",
        source_turn_id="turn-0",
        source_fragment="watch the screened market",
        target_type="universe",
        expected_answer_schema={"type": "string"},
        question="Which screened assets should HilalMarkets watch?",
        reason="a screened universe must be chosen",
    )
    session = _Session(draft=StrategyDraftV2(unresolved_fields=[other]))
    await session.say(ALERT_REQUEST)
    assert "sharia.universe_mode" in session.unresolved_ids

    await session.say("cancel")
    assert "sharia.universe_mode" in session.unresolved_ids, "unrelated blockers survive"


async def test_cancelling_twice_changes_nothing_the_second_time() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    await session.say("cancel")
    settled = session.draft.executable_hash
    unresolved = set(session.unresolved_ids)

    await session.say("cancel")
    assert session.draft.executable_hash == settled
    assert session.unresolved_ids == unresolved
    assert session.condition_count == 0


async def test_a_required_platform_question_is_paused_not_thrown_away() -> None:
    """A cancellation must never claim to remove a requirement that still blocks."""

    unresolved = UnresolvedFieldV2(
        unresolved_id="sharia.universe_mode",
        source_turn_id="turn-0",
        source_fragment="watch the screened market",
        target_type="universe",
        expected_answer_schema={"type": "string"},
        question="Which screened assets should HilalMarkets watch?",
        reason="a screened universe must be chosen",
    )
    contract = ClarificationContract(
        question_id="sharia.universe_mode",
        question=unresolved.question,
        reason=unresolved.reason,
        target_type="universe",
        expected_answer_schema='{"type":"string"}',
    )
    assert contract.cancellation_policy is CancellationPolicy.PAUSE_PENDING_REQUIREMENT
    session = _Session(
        draft=StrategyDraftV2(unresolved_fields=[unresolved]),
        conversation=SetupConversationContext().with_question(contract),
    )
    result = await session.say("cancel")
    assert "sharia.universe_mode" in session.unresolved_ids, "the blocker stays canonical"
    assert session.conversation.active_question is None
    assert "incomplete" in result.message.casefold(), result.message
    assert session.planner.calls == 0


async def test_cancelling_a_live_market_check_leaves_the_setup_untouched() -> None:
    session = _Session(mode=DraftMode.SCANNER)
    await session.say("which coins are up 5% right now?")
    assert session.conversation.pending_read_only_scan, "the scan is being collected"
    assert session.conversation.active_question is not None
    settled = session.draft.executable_hash

    result = await session.say("cancel")
    assert session.conversation.pending_read_only_scan == {}
    assert session.conversation.active_question is None
    assert session.conversation.active_goal is None
    assert session.draft.executable_hash == settled, "Scanner never mutates the draft"
    assert result.execution is None
    assert session.scan_requests[-1] is None, "a cancelled scan is not handed over"


# ---------------------------------------------------------------------------------
# Scanner: the window question owns its own answer
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["24 hours", "yes", "تمام", "correct", "sure"])
async def test_answering_the_window_question_finishes_the_scan(answer: str) -> None:
    session = _Session(draft=_scanner_ready_draft(), mode=DraftMode.SCANNER)
    await session.say("which coins are up 5% right now?")
    assert session.conversation.active_question is not None

    await session.say(answer)
    request = session.scan_requests[-1]
    assert request is not None, f"{answer!r} must finish the scan it was answering"
    assert request["measurement_window"] == "24h"
    assert request["threshold_percent"] == 5.0
    assert request["movement_direction"] == "up"
    assert session.draft.condition_ast is None, "a scan never writes a rule"


async def test_an_unreadable_window_answer_keeps_the_scan_and_the_question() -> None:
    session = _Session(mode=DraftMode.SCANNER)
    await session.say("which coins are up 5% right now?")
    pending = dict(session.conversation.pending_read_only_scan)

    result = await session.say("purple bananas")
    assert session.conversation.active_question is not None
    assert session.conversation.pending_read_only_scan == pending, "nothing is lost"
    assert session.scan_requests[-1] is None
    assert GENERIC_MISS not in result.message.casefold()


async def test_an_answer_cannot_build_a_rule_that_no_blocker_was_collecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A condition needs a grounded canonical requirement behind it, not just an answer.

    The question here says ``condition_creation`` and the draft holds no such blocker.
    The answer is still read and still owns the turn — but nothing may build a rule out
    of it, because there is no grounded incomplete requirement it could be completing.
    """

    contract = ClarificationContract(
        question_id="orphan_question",
        question="Which candle period should I use?",
        reason="one choice is still required",
        target_type="condition_creation",
        expected_answer_schema='{"type":"string"}',
        allowed_options=["1h", "4h", "1d"],
    )
    session = _Session(conversation=SetupConversationContext().with_question(contract))
    await session.say("1h")
    assert session.condition_count == 0, "no blocker, no rule"
    assert session.unresolved_ids == set()
    del monkeypatch


async def test_a_validation_generated_question_owns_its_answer() -> None:
    """A blocker the compiler raised behaves like every other question."""

    unresolved = UnresolvedFieldV2(
        unresolved_id="conditions.operator",
        source_turn_id="turn-0",
        source_fragment="alert me when BTC moves",
        target_type="condition_creation",
        expected_answer_schema={"type": "string"},
        allowed_options=["At the threshold or beyond", "Only beyond the threshold"],
        question="Should the alert trigger at the value, or only past it?",
        reason="the comparison is not determined",
    )
    contract = ClarificationContract(
        question_id="conditions.operator",
        question=unresolved.question,
        reason=unresolved.reason,
        target_type="condition_creation",
        target_field="comparator",
        expected_answer_schema='{"type":"string"}',
        allowed_options=list(unresolved.allowed_options),
    )
    session = _Session(
        draft=StrategyDraftV2(unresolved_fields=[unresolved]),
        conversation=SetupConversationContext().with_question(contract),
    )
    result = await session.say("at the threshld")
    assert session.conversation.active_question is not None, "a typo keeps it open"
    assert GENERIC_MISS not in result.message.casefold()
    assert session.planner.calls == 0, "and never reaches the model"
    assert "conditions.operator" in session.unresolved_ids, "the blocker is untouched"


async def test_cancelling_never_removes_a_rule_that_was_already_built() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    await session.say("1h")
    await session.say("Candle open")
    await session.say("at least")
    assert session.condition_count == 1
    settled = session.draft.executable_hash

    # A second unfinished rule, then cancel it. The finished one must not move.
    await session.say(ALERT_REQUEST)
    await session.say("cancel")
    assert session.condition_count == 1, "the built rule survives"
    assert session.draft.executable_hash == settled


# ---------------------------------------------------------------------------------
# Sessions that were open before any of this existed
# ---------------------------------------------------------------------------------


async def test_a_session_stored_before_workflows_existed_keeps_working() -> None:
    """No reset, no lost values. The old record is read once and then advances."""

    session = _Session()
    await session.say(ALERT_REQUEST)
    contract = session.conversation.active_question
    assert contract is not None

    # Exactly what a row written by the previous release looks like: the blocker and
    # the question are there, and nothing else is.
    legacy = session.conversation.model_copy(update={"pending_workflow": None})
    revived = _Session(draft=session.draft, conversation=legacy)
    await revived.say("1h")
    assert revived.accepted.get("trigger_timeframe") == "1h", "the answer still lands"
    assert revived.conversation.pending_workflow is not None, "the record is rebuilt"
    assert revived.condition_count == 0, "and nothing is invented to fill the gap"


async def test_a_legacy_question_without_a_stored_policy_still_cancels_correctly() -> None:
    """An old contract has no cancellation policy. It must not default to "drop it"."""

    stored = {
        "question_id": "sharia.universe_mode",
        "question": "Which screened assets should HilalMarkets watch?",
        "reason": "a screened universe must be chosen",
        "target_type": "universe",
        "expected_answer_schema": '{"type":"string"}',
        "mutating": True,
    }
    contract = ClarificationContract.model_validate(stored)
    assert contract.cancellation_policy is CancellationPolicy.PAUSE_PENDING_REQUIREMENT

    unresolved = UnresolvedFieldV2(
        unresolved_id="sharia.universe_mode",
        source_turn_id="turn-0",
        source_fragment="watch the screened market",
        target_type="universe",
        expected_answer_schema={"type": "string"},
        question=stored["question"],
        reason=stored["reason"],
    )
    session = _Session(
        draft=StrategyDraftV2(unresolved_fields=[unresolved]),
        conversation=SetupConversationContext().with_question(contract),
    )
    await session.say("cancel")
    assert "sharia.universe_mode" in session.unresolved_ids


async def test_an_answer_written_against_an_older_step_cannot_advance_a_newer_one() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    await session.say("1h")
    workflow = session.conversation.pending_workflow
    assert workflow is not None
    assert workflow.step_revision == 1

    decision = resolve_active_clarification_turn(
        message="Candle open",
        conversation=session.conversation,
        draft=session.draft,
        language=ConversationLanguage.ENGLISH,
        answered_question_id=workflow.step_question_id("trigger_timeframe", 0),
        answered_step_revision=0,
    )
    assert decision is not None
    assert decision.transition is TransitionOutcome.STALE_WORKFLOW
    assert decision.effects.commits_value is False, "a stale click cannot store"
    assert decision.contract.step_revision == 1, "the current step is what is re-asked"


async def test_a_paused_platform_requirement_can_be_picked_back_up() -> None:
    """Pausing is not dropping. "Continue" brings the same question back, intact."""

    unresolved = UnresolvedFieldV2(
        unresolved_id="sharia.universe_mode",
        source_turn_id="turn-0",
        source_fragment="watch the screened market",
        target_type="universe",
        expected_answer_schema={"type": "string"},
        question="Which screened assets should HilalMarkets watch?",
        reason="a screened universe must be chosen",
    )
    contract = ClarificationContract(
        question_id="sharia.universe_mode",
        question=unresolved.question,
        reason=unresolved.reason,
        target_type="universe",
        expected_answer_schema='{"type":"string"}',
    )
    session = _Session(
        draft=StrategyDraftV2(unresolved_fields=[unresolved]),
        conversation=SetupConversationContext().with_question(contract),
    )
    await session.say("cancel")
    assert session.conversation.active_question is None
    assert session.conversation.paused_question is not None, "it is retrievable"
    assert "sharia.universe_mode" in session.unresolved_ids

    result = await session.say("continue")
    assert session.conversation.active_question is not None
    assert session.conversation.active_question.question_id == "sharia.universe_mode"
    assert session.conversation.paused_question is None
    assert unresolved.question in result.message
    assert session.planner.calls == 0, "resuming needs no model"


@pytest.mark.parametrize("word", ["continue", "resume", "كمل", "continuer", "продолжить"])
async def test_resuming_is_understood_in_every_language(word: str) -> None:
    unresolved = UnresolvedFieldV2(
        unresolved_id="sharia.universe_mode",
        source_turn_id="turn-0",
        source_fragment="watch the screened market",
        target_type="universe",
        expected_answer_schema={"type": "string"},
        question="Which screened assets should HilalMarkets watch?",
        reason="a screened universe must be chosen",
    )
    contract = ClarificationContract(
        question_id="sharia.universe_mode",
        question=unresolved.question,
        reason=unresolved.reason,
        target_type="universe",
        expected_answer_schema='{"type":"string"}',
    )
    session = _Session(
        draft=StrategyDraftV2(unresolved_fields=[unresolved]),
        conversation=SetupConversationContext().with_question(contract),
    )
    await session.say("cancel")
    await session.say(word)
    assert session.conversation.active_question is not None, word


async def test_pausing_a_multi_step_workflow_keeps_every_value_already_chosen() -> None:
    session = _Session()
    await session.say(ALERT_REQUEST)
    await session.say("1h")
    assert session.accepted.get("trigger_timeframe") == "1h"
    # Force the pause policy for this workflow, the way a platform-owned question would
    # carry it, and prove the accepted values survive a pause and come back.
    contract = session.conversation.active_question
    assert contract is not None
    paused_contract = contract.model_copy(
        update={"cancellation_policy": CancellationPolicy.PAUSE_PENDING_REQUIREMENT}
    )
    session.conversation = session.conversation.model_copy(
        update={"active_question": paused_contract}
    )

    await session.say("cancel")
    assert session.conversation.active_question is None
    assert session.unresolved_ids, "the blocker stays"
    stored = session.conversation.paused_workflow
    assert stored is not None
    assert stored.accepted_values.get("trigger_timeframe") == "1h"

    await session.say("continue")
    assert session.accepted.get("trigger_timeframe") == "1h", "nothing is re-asked"


# ---------------------------------------------------------------------------------
# Just talking
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("greeting", ["hi", "hello", "hey", "أهلا", "bonjour", "hola"])
async def test_a_greeting_is_answered_like_a_person_not_like_a_form(greeting: str) -> None:
    session = _Session()
    result = await session.say(greeting)
    assert GENERIC_MISS not in result.message.casefold()
    assert result.message.strip()
    assert session.planner.calls == 0, "a hello costs nothing"


@pytest.mark.parametrize("word", ["thanks", "ok", "cool", "شكرا", "merci", "gracias"])
async def test_a_thank_you_is_not_answered_with_a_status_report(word: str) -> None:
    session = _Session()
    result = await session.say(word)
    assert GENERIC_MISS not in result.message.casefold()
    assert session.planner.calls == 0


@pytest.mark.parametrize(
    ("mode", "expected"), [("Monitor", "monitor"), ("Scanner", "scanner")]
)
async def test_choosing_a_mode_says_what_that_mode_does(mode: str, expected: str) -> None:
    session = _Session()
    result = await session.say(mode)
    lowered = result.message.casefold()
    assert expected in lowered, result.message
    assert GENERIC_MISS not in lowered
    assert len(result.message) > 80, "a bare form question is not a welcome"
    assert session.planner.calls == 0


async def test_choosing_a_mode_over_an_open_requirement_asks_before_switching() -> None:
    """The reversal of the old rule. A mode button used to bypass ownership entirely.

    It returned straight to the mode route, so the half-built rule's blocker stayed in
    the draft with nothing on screen mentioning it — a setup blocked for a reason the
    trader could not see or clear. The switch is now settled explicitly: nothing is
    applied, the requirement is still there, and the question is still on screen.
    """

    session = _Session()
    await session.say(ALERT_REQUEST)
    assert session.conversation.active_question is not None
    blocked = set(session.unresolved_ids)
    assert blocked, "the half-built rule is canonical while it is open"
    calls_before = session.planner.calls

    result = await session.say("Scanner")

    assert session.conversation.active_question is not None, "the question survives"
    assert session.unresolved_ids == blocked, "and so does its blocker"
    assert session.conversation.held_request == "Scanner", "the new request is kept"
    assert session.planner.calls == calls_before, "settling costs nothing"
    assert result.execution is None, "nothing canonical moved"


async def test_settling_the_old_requirement_then_runs_the_request_that_was_waiting() -> None:
    """Order is the whole point: the blocker goes first, then the new request runs."""

    session = _Session()
    await session.say(ALERT_REQUEST)
    assert session.unresolved_ids

    await session.say("Scanner")
    assert session.conversation.held_request == "Scanner"

    await session.say("cancel")

    assert session.unresolved_ids == set(), "the abandoned requirement really went"
    assert session.conversation.held_request is None, "the held request was used, not lost"
    assert session.condition_count == 0, "cancelling never builds a half-made rule"


async def test_a_mode_button_with_nothing_open_still_switches_immediately() -> None:
    """Nothing is stranded, so nothing needs settling."""

    session = _Session()
    result = await session.say("Scanner")
    assert "scanner" in result.message.casefold()
    assert session.conversation.held_request is None


async def test_a_confirmed_typo_applies_on_the_deterministic_route() -> None:
    """``qh`` -> *did you mean 1h?* -> ``yes`` on a question the workflow does not own.

    This is the case the grounding gate correctly used to refuse: the turn's words are
    only "yes", and nothing in "yes" says a candle period. The confirmation itself is
    the evidence, for exactly the field that was shown and exactly the value that was
    put to the trader — the gate is extended, never relaxed.
    """

    unresolved = UnresolvedFieldV2(
        unresolved_id="draft_exchange",
        source_turn_id="turn-0",
        source_fragment="watch it on binance",
        target_type="draft_field",
        target_field="exchange",
        expected_answer_schema={"type": "string"},
        question="Which exchange should I watch?",
        reason="the setup needs an exchange",
        allowed_options=["binance", "bybit"],
    )
    draft = StrategyDraftV2(unresolved_fields=[unresolved])
    continuation = continuation_for_unresolved(
        unresolved,
        draft,
        question_id="draft_exchange",
        step_revision=0,
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        allowed_values=["binance", "bybit"],
    )
    assert continuation is not None
    contract = ClarificationContract(
        question_id="draft_exchange",
        question=unresolved.question,
        reason=unresolved.reason,
        target_type="draft_field",
        target_field="exchange",
        expected_answer_schema='{"type":"string"}',
        allowed_options=["binance", "bybit"],
        canonical_values=["binance", "bybit"],
        cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        continuation=continuation,
    )
    session = _Session(
        draft=draft,
        conversation=SetupConversationContext().with_question(contract),
    )

    proposed = await session.say("bybt")
    assert session.conversation.active_question is not None, "a near miss is asked about"
    assert "bybit" in proposed.message
    assert session.conversation.proposed_value == "bybit", (
        "a one-question ask must have somewhere to keep its near miss"
    )

    confirmed = await session.say("yes")

    assert session.planner.calls == 0, "the whole lifecycle spends nothing"
    assert confirmed.execution is not None, "the confirmed value really landed"
    assert confirmed.draft.market_scope.exchange == "bybit"
    assert session.unresolved_ids == set(), "and the blocker it answered is closed"
