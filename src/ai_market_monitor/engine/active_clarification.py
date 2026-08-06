"""One dispatcher for every visible question, whatever asked it.

The defect this module removes
------------------------------

The rule "an open question owns the next turn" was real but it was only wired up for
*one* kind of question. The check that ran first asked whether the draft held a
supported-incomplete blocker, and if it did not, the whole ownership rule was skipped.

Every other question in the product therefore leaked. A Scanner window question, a
screened-scope question, a methodology question, a question the planner itself asked —
each was displayed, and then the trader's answer to it was handed to general intent
classification, which had never heard of that question and read the answer as a brand
new request. ``1h`` became "a bare timeframe, nothing to build"; ``all`` became "a new
instruction with no market named"; and the reply said *nothing is set up yet* while the
question it was answering was still on screen.

So: one dispatcher, one registry, and a handler for every ``target_type`` there is.
:func:`resolve_active_clarification_turn` is the only thing allowed to decide whether a
message answered the visible question, and it decides for all of them the same way.

What this module will not do
----------------------------

* It never lets a question lose the turn except for the three explicit reasons below.
* It never applies anything. It reads, and returns a typed decision; the caller owns
  every canonical write, so reading and mutating cannot drift apart.
* It never invents a handler. A ``target_type`` with no registered handler is a
  programming error caught by an invariant test, not a question shown to a trader with
  no way to answer it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final, get_args

from ai_market_monitor.engine.active_question import (
    AnswerDomain,
    AnswerOutcome,
    AnswerResolution,
    AnswerStage,
    canonical_values,
    display_options,
    domain_for_field,
    labels_for,
    resolve_active_answer,
)
from ai_market_monitor.engine.conversation_language import ConversationLanguage
from ai_market_monitor.schemas.setup_agent import (
    PendingClarificationWorkflow,
    SetupConversationContext,
)
from ai_market_monitor.schemas.setup_authorization import (
    CancellationPolicy,
    ClarificationContract,
    ClarificationTargetType,
)
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2

__all__ = [
    "GOVERNED_QUESTION_IDS",
    "ApplyRoute",
    "CanonicalBlocker",
    "ClarificationHandler",
    "ClarificationTurn",
    "ResumePolicy",
    "TransitionEffects",
    "TransitionOutcome",
    "answer_domain_for",
    "answer_field_for",
    "canonical_blocker_for",
    "cancellation_policy_for",
    "effects_of",
    "handler_for",
    "has_registered_handler",
    "registered_target_types",
    "resolve_active_clarification_turn",
    "resolved_handler",
    "stale_step",
    "workflow_invariants",
]


class TransitionOutcome(StrEnum):
    """What one message did to the visible question, as a state-machine transition.

    Distinct from :class:`~ai_market_monitor.engine.active_question.AnswerOutcome`, and
    deliberately so. That enum answers "what does this text *mean* as a value?" — it
    knows nothing about workflows, blockers or confirmation sub-states. This one answers
    "what must now happen to the conversation and to the draft?", which is a different
    question with a different set of answers.

    Collapsing the two is what produced the original defect: a rejected spelling
    suggestion and an unreadable word were both "ambiguous", so both were answered with
    the same shrug, and neither cleared the proposal that was still sitting in state.

    Every member has an entry in :data:`_EFFECTS` stating exactly what it permits. That
    table is the specification; the code below reads it rather than restating it.
    """

    #: A value from the question's own domain, read with no doubt.
    RESOLVED = "RESOLVED"
    #: One near miss. Ask before storing anything. Nothing is committed.
    CONFIRM_CANDIDATE = "CONFIRM_CANDIDATE"
    #: "Yes" to a *did you mean X?*. The proposal becomes the answer.
    CONFIRMATION_ACCEPTED = "CONFIRMATION_ACCEPTED"
    #: "No" to a *did you mean X?*. The proposal goes; every accepted value stays.
    CONFIRMATION_REJECTED = "CONFIRMATION_REJECTED"
    #: Several readings are equally good. List them; never pick one.
    AMBIGUOUS = "AMBIGUOUS"
    #: A real, well-formed value this question is not allowed to take.
    UNSUPPORTED = "UNSUPPORTED"
    #: Nothing in the message answers the question at all.
    INVALID = "INVALID"
    #: Stop, and remove the canonical requirement the trader was building.
    CANCELLED = "CANCELLED"
    #: Stop asking, and keep the requirement — the platform still needs it.
    PAUSED = "PAUSED"
    #: Not an answer: a different, complete request. Routing takes the turn.
    NEW_REQUEST = "NEW_REQUEST"
    #: Written against a question that is no longer the one on screen.
    STALE_WORKFLOW = "STALE_WORKFLOW"


@dataclass(frozen=True, slots=True)
class TransitionEffects:
    """Exactly what one outcome is allowed to do. The table, not the prose."""

    #: Does the trader still have this question in front of them afterwards?
    question_stays_active: bool
    #: Is a canonical value stored for the current field?
    commits_value: bool
    #: May this transition change executable draft state?
    changes_draft_state: bool
    #: May it change the pending workflow — advance it, hold a proposal, drop it?
    changes_workflow_state: bool
    #: May the planner build the canonical operation for this turn? Never true for a
    #: transition that stores nothing, which is the whole "invalid answers do not escape
    #: into planning" rule stated once, as data.
    planner_may_build_the_operation: bool
    #: Always ``False``. Whether a server-owned question was answered is decided by the
    #: deterministic resolver and by nothing else — this field exists so that rule is
    #: written down and testable rather than merely intended.
    planner_may_decide_the_answer: bool
    #: The catalogue key family the reply is built from. Named here so the wording and
    #: the state change can never describe different things.
    response_key: str


#: One row per member of :class:`TransitionOutcome`, checked exhaustive by a test.
_EFFECTS: Final[dict[TransitionOutcome, TransitionEffects]] = {
    TransitionOutcome.RESOLVED: TransitionEffects(
        question_stays_active=False,
        commits_value=True,
        changes_draft_state=True,
        changes_workflow_state=True,
        planner_may_build_the_operation=True,
        planner_may_decide_the_answer=False,
        response_key="status.question_answered",
    ),
    TransitionOutcome.CONFIRM_CANDIDATE: TransitionEffects(
        question_stays_active=True,
        commits_value=False,
        changes_draft_state=False,
        changes_workflow_state=True,
        planner_may_build_the_operation=False,
        planner_may_decide_the_answer=False,
        response_key="ask.confirm_candidate",
    ),
    TransitionOutcome.CONFIRMATION_ACCEPTED: TransitionEffects(
        question_stays_active=False,
        commits_value=True,
        changes_draft_state=True,
        changes_workflow_state=True,
        planner_may_build_the_operation=True,
        planner_may_decide_the_answer=False,
        response_key="status.question_answered",
    ),
    TransitionOutcome.CONFIRMATION_REJECTED: TransitionEffects(
        question_stays_active=True,
        commits_value=False,
        changes_draft_state=False,
        changes_workflow_state=True,
        planner_may_build_the_operation=False,
        planner_may_decide_the_answer=False,
        response_key="ask.confirm_rejected",
    ),
    TransitionOutcome.AMBIGUOUS: TransitionEffects(
        question_stays_active=True,
        commits_value=False,
        changes_draft_state=False,
        changes_workflow_state=False,
        planner_may_build_the_operation=False,
        planner_may_decide_the_answer=False,
        response_key="ask.pick_one_reading",
    ),
    TransitionOutcome.UNSUPPORTED: TransitionEffects(
        question_stays_active=True,
        commits_value=False,
        changes_draft_state=False,
        changes_workflow_state=False,
        planner_may_build_the_operation=False,
        planner_may_decide_the_answer=False,
        response_key="ask.unsupported_value",
    ),
    TransitionOutcome.INVALID: TransitionEffects(
        question_stays_active=True,
        commits_value=False,
        changes_draft_state=False,
        changes_workflow_state=False,
        planner_may_build_the_operation=False,
        planner_may_decide_the_answer=False,
        response_key="ask.repeat_options",
    ),
    TransitionOutcome.CANCELLED: TransitionEffects(
        question_stays_active=False,
        commits_value=False,
        # A cancellation removes the canonical requirement the trader abandoned. That is
        # a draft change, and it goes through the same authorized operation path as
        # every other one.
        changes_draft_state=True,
        changes_workflow_state=True,
        planner_may_build_the_operation=False,
        planner_may_decide_the_answer=False,
        response_key="status.rule_cancelled",
    ),
    TransitionOutcome.PAUSED: TransitionEffects(
        question_stays_active=False,
        commits_value=False,
        # Nothing canonical moves. That is the point: the blocker is still there and the
        # reply says so, so the UI and the draft cannot disagree about it.
        changes_draft_state=False,
        changes_workflow_state=True,
        planner_may_build_the_operation=False,
        planner_may_decide_the_answer=False,
        response_key="status.setup_paused",
    ),
    TransitionOutcome.NEW_REQUEST: TransitionEffects(
        question_stays_active=False,
        commits_value=False,
        changes_draft_state=True,
        changes_workflow_state=True,
        planner_may_build_the_operation=True,
        planner_may_decide_the_answer=False,
        response_key="",
    ),
    TransitionOutcome.STALE_WORKFLOW: TransitionEffects(
        question_stays_active=True,
        commits_value=False,
        changes_draft_state=False,
        changes_workflow_state=False,
        planner_may_build_the_operation=False,
        planner_may_decide_the_answer=False,
        response_key="ask.stale_answer",
    ),
}


def effects_of(outcome: TransitionOutcome) -> TransitionEffects:
    """What this outcome is allowed to do."""

    return _EFFECTS[outcome]


class ResumePolicy(StrEnum):
    """How a paused question comes back."""

    #: The trader asks to continue, and the same question is put back on screen with
    #: every value they had already chosen intact.
    RESUME_ON_REQUEST = "resume_on_request"
    #: Nothing canonical is behind it, so there is nothing to resume.
    NO_RESUME = "no_resume"


class ApplyRoute(StrEnum):
    """Who turns a resolved answer into canonical state.

    Reading an answer is always deterministic and always happens here. *Applying* it is
    not the same job: storing a candle period on a half-built rule, choosing a governed
    screened universe and filling a field the planner invented a question for are three
    different authorities. Naming the authority per question is what stops one of them
    quietly doing another's work.
    """

    #: The agent's own multi-step rule workflow. Deterministic end to end: no model call
    #: is made for the answer, the value, the next question, or the finished rule.
    SUPPORTED_RULE_WORKFLOW = "supported_rule_workflow"
    #: A read-only Scanner question. The answer is folded into the pending scan and the
    #: scan continues. Nothing about the strategy draft is touched.
    PENDING_SCAN = "pending_scan"
    #: A governed control — a screened universe, a methodology. Only the application's
    #: own allowlisted option route may apply it, so a *resolved* answer is handed
    #: straight to that route and never applied from chat. Everything else about the
    #: question (a typo, an ambiguity, a value the platform cannot take, a cancellation)
    #: still belongs here, which is what keeps the question answerable in words.
    GOVERNED_OPTION = "governed_option"
    #: A question the planner or the compiler authored, about a field only they can
    #: build an operation for. The answer is still read here — an unreadable one keeps
    #: the question and never reaches the planner — and the canonical value is handed to
    #: the planner as a *decided* answer, so it cannot re-read the message as a new
    #: request.
    PLANNER_OPERATION = "planner_operation"


@dataclass(frozen=True, slots=True)
class ClarificationHandler:
    """Everything the server needs to know to run one kind of question.

    Deliberately *not* carrying a "closing operation" name. Which canonical operation
    ends a requirement depends on which list actually holds it — an unsupported blocker
    is closed by ``remove_unsupported_key`` and everything else by
    ``resolve_unresolved_key`` — and :func:`canonical_blocker_for` reads that from the
    draft. A second static answer here would be a copy that could disagree with it.
    """

    target_type: str
    apply_route: ApplyRoute
    #: What cancelling means when the stored question did not say. The contract's own
    #: ``cancellation_policy`` always wins; this is the fallback for a legacy record.
    default_cancellation: CancellationPolicy
    #: What kind of value to read when the question names no field of its own.
    fallback_domain: AnswerDomain
    #: How a paused question comes back.
    resume_policy: ResumePolicy

    @property
    def is_deterministic(self) -> bool:
        """True when a resolved answer is applied without any model call."""

        return self.apply_route in {
            ApplyRoute.SUPPORTED_RULE_WORKFLOW,
            ApplyRoute.PENDING_SCAN,
        }


#: One entry per ``ClarificationTargetType``. An invariant test proves the two lists are
#: the same length and the same members: a question type with no handler is a question a
#: trader can be shown and then cannot answer, which is the bug this file exists for.
_HANDLERS: Final[dict[str, ClarificationHandler]] = {
    "conversational": ClarificationHandler(
        target_type="conversational",
        apply_route=ApplyRoute.PENDING_SCAN,
        default_cancellation=CancellationPolicy.CANCEL_CONVERSATION_ONLY,
        fallback_domain=AnswerDomain.ENUMERATED,
        resume_policy=ResumePolicy.NO_RESUME,
    ),
    "condition_creation": ClarificationHandler(
        target_type="condition_creation",
        apply_route=ApplyRoute.SUPPORTED_RULE_WORKFLOW,
        default_cancellation=CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.ENUMERATED,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
    "condition_field": ClarificationHandler(
        target_type="condition_field",
        apply_route=ApplyRoute.PLANNER_OPERATION,
        default_cancellation=CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.ENUMERATED,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
    "capability_parameter": ClarificationHandler(
        target_type="capability_parameter",
        apply_route=ApplyRoute.PLANNER_OPERATION,
        default_cancellation=CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.ENUMERATED,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
    "boolean_structure": ClarificationHandler(
        target_type="boolean_structure",
        apply_route=ApplyRoute.PLANNER_OPERATION,
        default_cancellation=CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.FREE_TEXT,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
    "reference_definition": ClarificationHandler(
        target_type="reference_definition",
        apply_route=ApplyRoute.PLANNER_OPERATION,
        default_cancellation=CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.FREE_TEXT,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
    "unsupported_requirement": ClarificationHandler(
        target_type="unsupported_requirement",
        apply_route=ApplyRoute.PLANNER_OPERATION,
        default_cancellation=CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.FREE_TEXT,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
    "unsupported_resolution": ClarificationHandler(
        target_type="unsupported_resolution",
        apply_route=ApplyRoute.PLANNER_OPERATION,
        default_cancellation=CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.FREE_TEXT,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
    "draft_field": ClarificationHandler(
        target_type="draft_field",
        apply_route=ApplyRoute.PLANNER_OPERATION,
        default_cancellation=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.FREE_TEXT,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
    # "Which markets?" is answered two ways and both must work: by naming a governed
    # scope, and by naming coins. The symbol reader covers both — it resolves "all" to
    # the whole screened market and a list of tickers to that list — while a question
    # that really is only about the governed mode names that field and gets its domain.
    "universe": ClarificationHandler(
        target_type="universe",
        apply_route=ApplyRoute.GOVERNED_OPTION,
        default_cancellation=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.SYMBOL_SCOPE,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
    "market_scope": ClarificationHandler(
        target_type="market_scope",
        apply_route=ApplyRoute.GOVERNED_OPTION,
        default_cancellation=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.SYMBOL_SCOPE,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
    # A governed methodology is named in words and its identity is resolved by the
    # governed reference resolver, not here. Free text unless the question itself offers
    # a bounded list — this module must never decide which methodology a name means.
    "sharia_policy": ClarificationHandler(
        target_type="sharia_policy",
        apply_route=ApplyRoute.GOVERNED_OPTION,
        default_cancellation=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        fallback_domain=AnswerDomain.FREE_TEXT,
        resume_policy=ResumePolicy.RESUME_ON_REQUEST,
    ),
}


#: Fail closed at import, not at render time. A new ``ClarificationTargetType`` added
#: without a handler stops the process from starting, so it can never reach a trader as
#: a question with no way to answer it. This is the strongest form the rule can take:
#: there is no code path between here and a rendered question that could skip it.
_UNREGISTERED: Final[tuple[str, ...]] = tuple(
    sorted(set(get_args(ClarificationTargetType)) - set(_HANDLERS))
)
if _UNREGISTERED:  # pragma: no cover - a build-time contract, not a runtime branch
    raise RuntimeError(
        "every clarification target type needs a registered handler; missing: "
        + ", ".join(_UNREGISTERED)
    )


#: Question ids that are governed choices even though their contract is a plain
#: conversational one. The screened-scope question is asked inside a read-only Scanner
#: flow, so it cannot be ``mutating``; but the answer changes governed Sharia policy and
#: may only be applied by the allowlisted option route. One constant, read here and by
#: the launch service, so the two cannot disagree about which question that is.
GOVERNED_QUESTION_IDS: Final[frozenset[str]] = frozenset({"scan_screened_scope"})


def registered_target_types() -> frozenset[str]:
    """Every clarification kind the server knows how to run."""

    return frozenset(_HANDLERS)


def handler_for(target_type: str) -> ClarificationHandler | None:
    """The handler for one clarification kind, or ``None`` when there is none."""

    return _HANDLERS.get(str(target_type or ""))


def has_registered_handler(contract: ClarificationContract | None) -> bool:
    """Whether this question has a canonical answer handler. Always true in practice.

    Asserted after every persisted turn. A visible question without one cannot be
    answered by anything, so persisting it would strand the conversation.
    """

    return contract is None or handler_for(contract.target_type) is not None


def resolved_handler(
    contract: ClarificationContract,
    *,
    scan_is_pending: bool = False,
) -> ClarificationHandler:
    """The handler that owns this exact question.

    Mostly the ``target_type``'s, with two state-dependent corrections:

    * a governed choice asked through a conversational contract is applied by the
      option route whatever its target type says — pretending otherwise would let chat
      text move Sharia policy;
    * ``conversational`` covers both a live Scanner question and an ordinary yes/no the
      planner asked. Only the first belongs to the scan, so the pending scan decides.
      Sending a plain confirmation to the scan collector would answer "did I read that
      right?" by running a market check.
    """

    base = _HANDLERS[contract.target_type]
    if contract.question_id in GOVERNED_QUESTION_IDS:
        return replace(
            base,
            apply_route=ApplyRoute.GOVERNED_OPTION,
            fallback_domain=AnswerDomain.UNIVERSE_MODE,
        )
    if base.apply_route is ApplyRoute.PENDING_SCAN and not scan_is_pending:
        return replace(base, apply_route=ApplyRoute.PLANNER_OPERATION)
    return base


@dataclass(frozen=True, slots=True)
class CanonicalBlocker:
    """The open item a question is about, and the operation that closes it."""

    #: ``resolve_unresolved_key`` or ``remove_unsupported_key``.
    operation_kind: str
    key: str


def canonical_blocker_for(
    draft: StrategyDraftV2,
    contract: ClarificationContract,
    workflow: PendingClarificationWorkflow | None = None,
) -> CanonicalBlocker | None:
    """The canonical open item behind this question, and how to close it.

    A question names its blocker in one of three places, because three different
    builders create questions: the workflow's id, the contract's workflow id, and the
    question id itself — a compiler-built question *is* its blocker's key. All three are
    checked in that order, so cancelling never leaves the blocker behind merely because
    the question came from a different builder.

    Which operation closes it is read from the draft rather than from a table: an
    unsupported blocker lives in a different list from an unresolved one, and the list
    that actually holds the key is the only reliable answer.

    ``None`` means nothing canonical is behind this question — a read-only Scanner ask,
    or one raised before any blocker was written. There is then nothing to remove, and
    a reply claiming otherwise would be false.
    """

    candidates = (
        workflow.workflow_id if workflow is not None else "",
        contract.workflow_id or "",
        contract.question_id,
    )
    unresolved = {item.unresolved_id for item in draft.unresolved_fields}
    unsupported = {item.key for item in draft.unsupported_requirements}
    for candidate in candidates:
        if candidate and candidate in unresolved:
            return CanonicalBlocker("resolve_unresolved_key", candidate)
        if candidate and candidate in unsupported:
            return CanonicalBlocker("remove_unsupported_key", candidate)
    return None


def cancellation_policy_for(contract: ClarificationContract) -> CancellationPolicy:
    """What ``cancel`` must do to the canonical state behind this question.

    The contract carries it, because the code that knew whether the blocker was the
    trader's or the platform's was the code that created the question. A legacy record
    without one is reconstructed from its target type by the schema itself, so this
    never has to guess and never returns "drop it" by accident.
    """

    return contract.cancellation_policy


def answer_field_for(
    contract: ClarificationContract,
    workflow: PendingClarificationWorkflow | None,
) -> str:
    """Which canonical field this question is waiting on.

    The workflow wins when there is one: it is the record that will validate the answer,
    so reading the field from anywhere else is how the displayed question and the
    validating state came apart in the first place.
    """

    if workflow is not None and workflow.current_field:
        return workflow.current_field
    return str(contract.target_field or "")


def answer_domain_for(
    contract: ClarificationContract,
    workflow: PendingClarificationWorkflow | None = None,
) -> AnswerDomain:
    """The kind of value this question is waiting for.

    Read from the field when the field names one, and from the handler otherwise. A
    question with a bounded ``canonical_values`` list is enumerated by definition, which
    is what lets a planner-authored question be answered deterministically without the
    planner having to describe its own domain.
    """

    field_name = answer_field_for(contract, workflow)
    domain = domain_for_field(field_name)
    if domain is not AnswerDomain.FREE_TEXT:
        return domain
    handler = handler_for(contract.target_type)
    fallback = handler.fallback_domain if handler is not None else AnswerDomain.FREE_TEXT
    # The handler's own domain comes before "it has options, so it is a list". A
    # screened-scope question carries three option labels *and* a real domain with a
    # vocabulary behind it; reading it as a bare list would accept the three labels and
    # refuse every other way a trader says the same thing.
    if fallback not in {AnswerDomain.ENUMERATED, AnswerDomain.FREE_TEXT}:
        return fallback
    if contract.canonical_values or contract.allowed_options:
        return AnswerDomain.ENUMERATED
    # A question with nothing to enumerate is not an enumerated question. Calling it one
    # gives the resolver an empty list of acceptable values, so every possible answer is
    # refused — a question the trader cannot get past however carefully they reply.
    return AnswerDomain.FREE_TEXT


def stale_step(
    contract: ClarificationContract,
    *,
    question_id: str | None,
    step_revision: int | None,
) -> bool:
    """Whether an answer was written against a question that is no longer on screen.

    An option button carries the identity of the question it was rendered under. If the
    workflow has moved on since, that click is an answer to a question the trader can no
    longer see, and applying it would fill the *current* field with the *previous*
    step's choice.
    """

    if question_id is not None and question_id != contract.question_id:
        return True
    return step_revision is not None and step_revision != contract.step_revision


def _transition_for(
    resolution: AnswerResolution,
    *,
    cancellation: CancellationPolicy,
) -> TransitionOutcome:
    """Turn one value reading into one state-machine transition.

    The reading knows what the words meant. Only here is it known what that means for
    the conversation — which is why a rejected suggestion and an unreadable word, both
    "ambiguous" to the reader, become two different transitions with two different
    replies and two different effects on stored state.
    """

    if resolution.outcome is AnswerOutcome.NEW_REQUEST:
        return TransitionOutcome.NEW_REQUEST
    if resolution.outcome is AnswerOutcome.CANCELLED:
        # Stopping means different things depending on whose requirement it was. The
        # policy was decided when the question was created, by the code that knew.
        return (
            TransitionOutcome.PAUSED
            if cancellation is CancellationPolicy.PAUSE_PENDING_REQUIREMENT
            else TransitionOutcome.CANCELLED
        )
    confirming = resolution.stage is AnswerStage.CONFIRMATION
    if resolution.outcome is AnswerOutcome.RESOLVED:
        return (
            TransitionOutcome.CONFIRMATION_ACCEPTED
            if confirming
            else TransitionOutcome.RESOLVED
        )
    if resolution.outcome is AnswerOutcome.CONFIRM_CANDIDATE:
        return TransitionOutcome.CONFIRM_CANDIDATE
    if resolution.outcome is AnswerOutcome.UNSUPPORTED:
        return TransitionOutcome.UNSUPPORTED
    if confirming and resolution.proposed_value is None:
        return TransitionOutcome.CONFIRMATION_REJECTED
    return (
        TransitionOutcome.AMBIGUOUS
        if resolution.plausible_readings
        else TransitionOutcome.INVALID
    )


@dataclass(frozen=True, slots=True)
class ClarificationTurn:
    """What one message did to the visible question. One typed transition, always."""

    #: The state-machine transition. This is the authority for what happens next.
    transition: TransitionOutcome
    #: The value-level reading it came from, kept as evidence for the operator trace.
    outcome: AnswerOutcome
    contract: ClarificationContract
    handler: ClarificationHandler
    workflow: PendingClarificationWorkflow | None
    field_name: str
    domain: AnswerDomain
    cancellation: CancellationPolicy
    #: The canonical open item behind this question, when there is one.
    blocker: CanonicalBlocker | None = None
    stage: AnswerStage = AnswerStage.ANSWER
    canonical_value: str | float | None = None
    candidates: tuple[str, ...] = field(default_factory=tuple)
    #: The near miss the caller must store on the workflow after this turn — including
    #: ``None``, which clears one.
    proposed_value: str | None = None
    #: What a beginner should see as choices, already in their language.
    display_labels: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    @property
    def effects(self) -> TransitionEffects:
        """Exactly what this transition may do. Read, never re-derived."""

        return _EFFECTS[self.transition]

    @property
    def owns_the_turn(self) -> bool:
        """Whether the question keeps this turn rather than handing it to routing."""

        return self.transition is not TransitionOutcome.NEW_REQUEST

    @property
    def stores_a_value(self) -> bool:
        return self.effects.commits_value

    @property
    def keeps_the_question(self) -> bool:
        return self.effects.question_stays_active

    @property
    def is_confirmation(self) -> bool:
        """Whether the trader is looking at a yes/no rather than a list of choices."""

        return self.transition is TransitionOutcome.CONFIRM_CANDIDATE

    @property
    def rejected_candidate(self) -> bool:
        return self.transition is TransitionOutcome.CONFIRMATION_REJECTED

    @property
    def ends_the_question(self) -> bool:
        """Cancelled or paused: the question goes, for two different reasons."""

        return self.transition in {
            TransitionOutcome.CANCELLED,
            TransitionOutcome.PAUSED,
        }


def resolve_active_clarification_turn(
    *,
    message: str,
    conversation: SetupConversationContext,
    draft: StrategyDraftV2,
    language: ConversationLanguage,
    mode_selected: bool = False,
    looks_like_new_request: bool = False,
    answered_question_id: str | None = None,
    answered_step_revision: int | None = None,
) -> ClarificationTurn | None:
    """Read one message against whatever question is currently on screen.

    The one entry point for every active clarification, whichever builder asked it. It
    reads, and returns a typed transition; it applies nothing, so the reading and the
    canonical write can never drift apart.

    ``None`` means there was no question to answer, or the product's own mode button was
    pressed — the two cases where general routing is the right destination. Every other
    message comes back as a typed transition, including one this reader could not use.

    ``draft`` supplies the canonical open item behind the question, so a caller never
    has to work out for itself which blocker a cancellation should close.

    ``answered_question_id`` and ``answered_step_revision`` come from an option button,
    which carries the identity of the question it was drawn under. When they name a step
    that has since advanced the click is refused and the current question is asked
    again, rather than the old choice landing on the new field.
    """

    contract = conversation.active_question
    if contract is None:
        return None
    if mode_selected:
        # The product's own start button. It is a destination, not an answer, and it has
        # always been allowed to take the conversation somewhere else.
        return None
    if handler_for(contract.target_type) is None:  # pragma: no cover - see _UNREGISTERED
        return None
    handler = resolved_handler(
        contract, scan_is_pending=bool(conversation.pending_read_only_scan)
    )
    workflow = conversation.pending_workflow
    field_name = answer_field_for(contract, workflow)
    domain = answer_domain_for(contract, workflow)
    allowed = (
        list(contract.canonical_values)
        or list(canonical_values(domain))
        or list(contract.allowed_options)
    )
    offered = list(workflow.offered_values) if workflow is not None else []
    if not offered:
        offered = list(display_options(domain, allowed))
    labels = labels_for(domain, allowed, language)
    shown = [labels.get(item, item) for item in offered] or list(contract.allowed_options)
    cancellation = cancellation_policy_for(contract)
    blocker = canonical_blocker_for(draft, contract, workflow)

    def decided(
        resolution: AnswerResolution,
        *,
        transition: TransitionOutcome | None = None,
    ) -> ClarificationTurn:
        return ClarificationTurn(
            transition=(
                transition
                if transition is not None
                else _transition_for(resolution, cancellation=cancellation)
            ),
            outcome=resolution.outcome,
            contract=contract,
            handler=handler,
            workflow=workflow,
            field_name=field_name,
            domain=domain,
            cancellation=cancellation,
            blocker=blocker,
            stage=resolution.stage,
            canonical_value=resolution.canonical_value,
            candidates=resolution.candidates,
            proposed_value=resolution.proposed_value,
            display_labels=tuple(shown),
            reason=resolution.reason,
        )

    if stale_step(
        contract,
        question_id=answered_question_id,
        step_revision=answered_step_revision,
    ):
        # Fail closed. The answer is dropped, nothing advances, and the question the
        # trader is actually looking at is asked again. A proposal in flight is kept,
        # because a stale click says nothing about it either way.
        return decided(
            AnswerResolution(
                AnswerOutcome.AMBIGUOUS,
                candidates=tuple(allowed),
                reason="the answer was written against an older step",
                proposed_value=(
                    workflow.proposed_value if workflow is not None else None
                ),
            ),
            transition=TransitionOutcome.STALE_WORKFLOW,
        )

    return decided(
        resolve_active_answer(
            message,
            domain=domain,
            allowed_options=allowed,
            offered_values=offered,
            display_labels=labels,
            looks_like_new_request=looks_like_new_request,
            proposed_value=workflow.proposed_value if workflow is not None else None,
        )
    )


def workflow_invariants(conversation: SetupConversationContext) -> tuple[str, ...]:
    """Every state rule this conversation currently breaks. Empty is the only pass.

    A read-only audit, called after each persisted turn in the tests. It does not
    *enforce* these rules — each is already enforced at the write site, which is the
    only place it can be enforced without a diagnostic becoming the failure:

    * the workflow/question pairing by ``SetupConversationContext._check_one_step``,
      which runs on construction **and** on every write;
    * the handler requirement by :func:`has_registered_handler`, which drops an
      unanswerable question before it can be offered.

    This function exists so a test can state the whole rule in one place instead of
    re-deriving it, and so a future producer that bypasses those write sites is caught.
    """

    problems: list[str] = []
    contract = conversation.active_question
    workflow = conversation.pending_workflow
    if contract is not None and handler_for(contract.target_type) is None:
        problems.append(f"no registered handler for {contract.target_type}")
    if workflow is not None and contract is None:
        problems.append("a pending workflow has no question on screen")
    if workflow is not None and contract is not None and not workflow.matches(contract):
        problems.append("the pending workflow is not the question on screen")
    if workflow is not None and workflow.proposed_value is not None and contract is None:
        problems.append("a proposed value with no question to confirm it")
    return tuple(problems)


def _registry_covers_every_target_type() -> tuple[str, ...]:
    """Target types with no handler. Read by the invariant test, and by nothing else."""

    declared: Mapping[str, object] = dict.fromkeys(get_args(ClarificationTargetType))
    return tuple(sorted(set(declared) - registered_target_types()))
