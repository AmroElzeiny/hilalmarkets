"""What one Setup Chat turn contains, and what the server did about it.

The previous design gave a whole free-text message exactly one intent, chosen by
regular expressions before the model saw anything. A real message is not like that.
``hey, thanks — also drop LTC from the list, and why did the timeframe change?``
carries a greeting, an executable instruction and a question at once. Forcing one
label on it meant two of the three were discarded, and an unrecognised mechanic was
labelled "conversation" and answered with a fixed sentence.

So a turn is modelled as *segments*, each pinned to an exact span of the user's own
words. The model divides the turn; the server decides what may be executed. The two
jobs stay separate:

* :class:`SetupAgentTurnPlan` is what the model proposes. It is never authoritative.
* :class:`SetupTurnExecutionResult` is what the server actually did. It is the only
  thing a reply may claim as fact.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_market_monitor.schemas.strategy_draft_v2 import (
    STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    StrategyPatch,
)
from ai_market_monitor.schemas.strict_mode import drop_absent_nulls


class _StrictModel(BaseModel):
    """A model the provider fills under a strict schema.

    Strict schemas require every key, so a model with nothing to say sends `null`.
    That means "no opinion", so the declared default applies.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _absent_nulls_use_defaults(cls, data: object) -> object:
        return drop_absent_nulls(cls, data)

#: A reply is composed, not templated, so it needs room. Still bounded: an assistant
#: turn that runs past this is padding, not content.
SETUP_REPLY_MAX_LENGTH = 2000

#: How many recent messages the agent may see. Enough to resolve "the one we just
#: added" and "the second option"; far short of an unbounded transcript.
DIALOGUE_WINDOW_MIN = 12
DIALOGUE_WINDOW_MAX = 20


class SegmentKind(StrEnum):
    """What one span of the user's message is doing.

    Only ``STRATEGY_INSTRUCTION`` and ``CLARIFICATION_ANSWER`` can change executable
    state, and even then only through the deterministic tool. The rest exist so that
    conversation can be answered without being compiled, and so that compiling
    something never requires ignoring the conversation around it.
    """

    SOCIAL_REPLY = "SOCIAL_REPLY"
    ACKNOWLEDGEMENT_NO_ACTION = "ACKNOWLEDGEMENT_NO_ACTION"
    CONVERSATIONAL_CONTEXT = "CONVERSATIONAL_CONTEXT"
    STRATEGY_INSTRUCTION = "STRATEGY_INSTRUCTION"
    CLARIFICATION_ANSWER = "CLARIFICATION_ANSWER"
    USER_QUESTION = "USER_QUESTION"
    EXPLANATION_REQUEST = "EXPLANATION_REQUEST"
    PRODUCT_QUESTION = "PRODUCT_QUESTION"
    APPROVAL_INTENT = "APPROVAL_INTENT"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"


#: Segment kinds that may lead to a change in executable state. Everything else is
#: answered in words and can never reach the compiler.
ACTIONABLE_SEGMENT_KINDS: frozenset[SegmentKind] = frozenset(
    {SegmentKind.STRATEGY_INSTRUCTION, SegmentKind.CLARIFICATION_ANSWER}
)

#: Segment kinds that expect words back. A turn whose segments are all silent would
#: leave the user with no reply at all.
REPLY_BEARING_SEGMENT_KINDS: frozenset[SegmentKind] = frozenset(
    {
        SegmentKind.SOCIAL_REPLY,
        SegmentKind.ACKNOWLEDGEMENT_NO_ACTION,
        SegmentKind.CONVERSATIONAL_CONTEXT,
        SegmentKind.USER_QUESTION,
        SegmentKind.EXPLANATION_REQUEST,
        SegmentKind.PRODUCT_QUESTION,
        SegmentKind.APPROVAL_INTENT,
        SegmentKind.UNSUPPORTED_REQUEST,
    }
)


class TurnSegment(_StrictModel):
    """One span of the current message, quoted in the user's exact words.

    ``exact_source_text`` is what makes a claim checkable: the server looks for that
    text in the real message, and a paraphrase, a quote from an earlier turn, or an
    invented one is refused.

    The offsets are the model's *estimate* of where the quote sits, kept because they
    are useful in the operator trace. They are not the grounding check and are not
    trusted. A real model quotes accurately and then miscounts the position — language
    models cannot count characters — so making a correct quote fail on arithmetic
    rejected good turns for no safety gain. :func:`apply_setup_turn` locates every
    span itself and overwrites these values with what it found.
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=80)
    exact_source_text: str = Field(min_length=1, max_length=STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    kind: SegmentKind
    reply_required: bool = False
    action_required: bool = False
    confidence: float = Field(ge=0, le=1)
    #: The existing condition this segment talks about, for `make that stricter`.
    target_condition_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_span(self) -> TurnSegment:
        if self.action_required and self.kind not in ACTIONABLE_SEGMENT_KINDS:
            raise ValueError(f"{self.kind.value} segments cannot require an action")
        return self

    def located_in(self, message: str, *, search_from: int = 0) -> TurnSegment | None:
        """This segment with server-found offsets, or ``None`` if the quote is absent.

        Searching forward from ``search_from`` keeps two segments that quote the same
        words from claiming the same characters, and matches the order a turn reads in.
        """
        quoted = self.exact_source_text
        start = message.find(quoted, search_from)
        if start < 0:
            start = message.find(quoted)
        if start < 0:
            return None
        return self.model_copy(update={"start_offset": start, "end_offset": start + len(quoted)})


class StrategyInstructionPlan(_StrictModel):
    """One executable instruction the model read out of the turn.

    The plan says which segment authorised it and, when a registered mechanic is
    involved, which shortlisted key it chose. It may not name a key the server did
    not offer, and it may not describe a mechanic in prose instead of choosing.
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=80)
    #: Plain-language restatement, for the operator trace. Never compiled.
    intent_summary: str = Field(min_length=1, max_length=400)
    #: A key from the server-supplied shortlist, or null for a core primitive.
    capability_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    target_condition_id: str | None = Field(default=None, max_length=120)


class ClarificationAnswer(_StrictModel):
    """The user answering the question the assistant last asked.

    An answer resolves that question. It does not become a new condition: ``yes`` is
    not a market rule, and treating it as one is how a draft grew a condition nobody
    described.
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=80)
    #: The unresolved field or question this answers.
    question_id: str = Field(min_length=1, max_length=120)
    #: What the user chose, in their own words.
    answer_text: str = Field(min_length=1, max_length=500)
    resolves_question: bool = True


class ClarificationRequest(_StrictModel):
    """The smallest question that would unblock the draft."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=500)
    #: Why it is needed, in the user's terms rather than a field name.
    reason: str = Field(min_length=1, max_length=400)
    #: The segment that raised it, when the turn itself was ambiguous.
    segment_id: str | None = Field(default=None, max_length=80)
    #: Concrete choices when the answer is one of a few. Never invented values.
    options: list[str] = Field(default_factory=list, max_length=6)


class ResponseDirective(_StrictModel):
    """One thing the final reply has to cover.

    The reply is written by the model, so the plan records *what* must be said rather
    than the sentence to say. That keeps replies natural without letting the model
    decide to skip the part the user actually asked about.
    """

    model_config = ConfigDict(extra="forbid")

    point: str = Field(min_length=1, max_length=400)
    kind: Literal[
        "acknowledge",
        "answer_question",
        "explain_change",
        "explain_refusal",
        "ask_clarification",
        "state_next_step",
    ]
    segment_id: str | None = Field(default=None, max_length=80)


class UnsupportedSegment(_StrictModel):
    """Something the platform cannot express exactly.

    Recorded as unsupported with the user's own wording rather than approximated with
    the nearest available mechanic. A near miss monitors the wrong market silently;
    a refusal keeps the gap visible.
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=80)
    #: What exactly is missing, in plain words.
    missing_contract: str = Field(min_length=1, max_length=500)
    #: True when the draft cannot run until it is resolved.
    blocking: bool = True


class ApprovalIntent(_StrictModel):
    """The user asking to approve.

    Recorded, never acted on. Approval happens only through the authenticated
    endpoint, bound to an exact draft version and semantic hash.
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=80)
    #: True when the same turn also changes the draft, which invalidates approval.
    accompanied_by_material_edit: bool = False


class SetupAgentTurnPlan(_StrictModel):
    """The model's reading of one turn. A proposal, never an authority.

    Every list here is checked by the deterministic tool before anything is applied,
    and nothing in it can grant approval, activation or a capability the server did
    not offer.
    """

    model_config = ConfigDict(extra="forbid")

    source_turn_id: str = Field(min_length=1, max_length=80)
    segments: list[TurnSegment] = Field(min_length=1, max_length=24)
    strategy_patch: StrategyPatch | None = None
    strategy_instructions: list[StrategyInstructionPlan] = Field(
        default_factory=list, max_length=24
    )
    clarification_answers: list[ClarificationAnswer] = Field(default_factory=list, max_length=8)
    questions_to_answer: list[str] = Field(default_factory=list, max_length=8)
    clarifications_to_ask: list[ClarificationRequest] = Field(
        default_factory=list, max_length=3
    )
    approval_intent: ApprovalIntent | None = None
    unsupported_segments: list[UnsupportedSegment] = Field(default_factory=list, max_length=12)
    response_points: list[ResponseDirective] = Field(default_factory=list, max_length=12)
    overall_confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_internal_references(self) -> SetupAgentTurnPlan:
        ids = [segment.segment_id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("segment_id values must be unique within one plan")
        known = set(ids)
        for collection, label in (
            (self.strategy_instructions, "strategy_instructions"),
            (self.clarification_answers, "clarification_answers"),
            (self.unsupported_segments, "unsupported_segments"),
        ):
            for item in collection:
                if item.segment_id not in known:
                    raise ValueError(f"{label} references unknown segment {item.segment_id!r}")
        if self.approval_intent is not None and self.approval_intent.segment_id not in known:
            raise ValueError("approval_intent references an unknown segment")
        for directive in self.response_points:
            if directive.segment_id is not None and directive.segment_id not in known:
                raise ValueError("response_points references an unknown segment")
        for request in self.clarifications_to_ask:
            if request.segment_id is not None and request.segment_id not in known:
                raise ValueError("clarifications_to_ask references an unknown segment")
        if self.strategy_patch is not None and self.strategy_patch.source_turn_id != (
            self.source_turn_id
        ):
            raise ValueError("the patch must carry this turn's source_turn_id")
        return self

    @property
    def actionable_segments(self) -> list[TurnSegment]:
        return [item for item in self.segments if item.kind in ACTIONABLE_SEGMENT_KINDS]

    @property
    def requires_tool(self) -> bool:
        """True when this turn asks the server to change or re-check state.

        A turn that only talks is answered directly, with no tool call and no new
        draft version.
        """
        return bool(
            self.strategy_patch is not None
            or self.clarification_answers
            or self.unsupported_segments
            or any(item.action_required for item in self.segments)
        )


class AppliedInstruction(BaseModel):
    """One instruction the server actually applied, with the words that caused it."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=80)
    source_text: str = Field(min_length=1, max_length=STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH)
    summary: str = Field(min_length=1, max_length=400)
    condition_ids: list[str] = Field(default_factory=list, max_length=24)


class IgnoredSegment(BaseModel):
    """A segment that changed nothing, and why.

    Recorded so an operator can answer "why was this phrase ignored?" without
    guessing, and so a reply never implies that chatter was compiled.
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=80)
    source_text: str = Field(min_length=1, max_length=STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH)
    kind: SegmentKind
    reason: str = Field(min_length=1, max_length=300)


class SetupTurnExecutionResult(BaseModel):
    """What the server did. The only source of truth for any claim in the reply.

    A reply may say "I removed LTC" only if this object says so. Before it existed,
    replies were composed from templates chosen around the compiler, which is how a
    confident sentence could describe a change that never landed.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "applied",
        "no_change",
        "rejected",
        "blocked",
        "conversation_only",
    ]
    applied: bool
    strategy_mutated: bool
    draft_id: UUID
    previous_version: int = Field(ge=1)
    current_version: int = Field(ge=1)
    previous_semantic_hash: str = Field(pattern=r"^$|^[a-f0-9]{64}$")
    current_semantic_hash: str = Field(pattern=r"^$|^[a-f0-9]{64}$")
    semantic_diff: list[str] = Field(default_factory=list, max_length=200)
    applied_instructions: list[AppliedInstruction] = Field(default_factory=list, max_length=24)
    ignored_non_actionable_segments: list[IgnoredSegment] = Field(
        default_factory=list, max_length=24
    )
    answered_questions: list[str] = Field(default_factory=list, max_length=8)
    unresolved_fields: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    unsupported_requirements: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    semantic_violations: list[str] = Field(default_factory=list, max_length=100)
    compile_status: Literal["compiled", "blocked", "not_attempted", "failed"]
    approval_eligible: bool = False
    approval_status: Literal[
        "not_eligible",
        "eligible",
        "approved",
        "invalidated_by_edit",
    ] = "not_eligible"
    #: Sanitised messages safe to show a user. Never a stack trace or internal path.
    safe_errors: list[str] = Field(default_factory=list, max_length=20)
    suggested_next_actions: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_consistency(self) -> SetupTurnExecutionResult:
        if self.applied and not self.applied_instructions and not self.answered_questions:
            raise ValueError("an applied result must record what it applied")
        if self.strategy_mutated and self.current_version <= self.previous_version:
            raise ValueError("a mutated draft must carry a newer version")
        if not self.strategy_mutated and self.current_version != self.previous_version:
            raise ValueError("an unchanged draft cannot change version")
        if self.approval_eligible and self.compile_status != "compiled":
            raise ValueError("approval cannot be eligible before the draft compiles")
        return self


class SetupConversationContext(BaseModel):
    """What the last few turns were about, for language only.

    This exists so ``no, the other one`` and ``keep the first condition`` can be
    understood. Nothing here is executable: it records which condition was discussed,
    never what a condition is. Executable state stays in ``StrategyDraftV2``.
    """

    model_config = ConfigDict(extra="forbid")

    active_question_id: str | None = Field(default=None, max_length=120)
    question_text: str | None = Field(default=None, max_length=500)
    #: The field the open question is about, for example `timeframe`.
    question_target: str | None = Field(default=None, max_length=120)
    #: What a usable answer looks like, so `yes` can be read against a yes/no ask.
    valid_answer_shape: str | None = Field(default=None, max_length=200)
    #: Things the user referred to recently, newest first.
    recent_references: list[str] = Field(default_factory=list, max_length=12)
    last_explained_condition_ids: list[str] = Field(default_factory=list, max_length=24)
    last_changed_condition_ids: list[str] = Field(default_factory=list, max_length=24)
    last_assistant_summary: str | None = Field(default=None, max_length=1000)

    def with_question(self, request: ClarificationRequest) -> SetupConversationContext:
        return self.model_copy(
            update={
                "active_question_id": request.question_id,
                "question_text": request.question,
                "question_target": request.question_id,
                "valid_answer_shape": (
                    "one of: " + "; ".join(request.options) if request.options else "free text"
                ),
            }
        )

    def cleared_question(self) -> SetupConversationContext:
        return self.model_copy(
            update={
                "active_question_id": None,
                "question_text": None,
                "question_target": None,
                "valid_answer_shape": None,
            }
        )


class SetupAgentReply(_StrictModel):
    """The final assistant message, composed from the execution result."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=SETUP_REPLY_MAX_LENGTH)
    #: A question to carry into the next turn, when one is genuinely needed.
    clarification: ClarificationRequest | None = None


class SetupAgentPlanEnvelope(_StrictModel):
    """The planner's whole answer: a reply, a plan, or both.

    A pure conversation turn returns ``direct_reply`` and no plan, and no tool runs.
    A mixed turn returns a plan; the reply is composed afterwards from what the
    server actually did, so it cannot describe an unapplied change.
    """

    model_config = ConfigDict(extra="forbid")

    plan: SetupAgentTurnPlan | None = None
    direct_reply: str | None = Field(default=None, max_length=SETUP_REPLY_MAX_LENGTH)

    @model_validator(mode="after")
    def validate_one_of(self) -> SetupAgentPlanEnvelope:
        if self.plan is None and not (self.direct_reply or "").strip():
            raise ValueError("the planner must return a plan, a direct reply, or both")
        return self
