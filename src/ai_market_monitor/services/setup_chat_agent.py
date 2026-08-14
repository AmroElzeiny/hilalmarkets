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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from time import monotonic
from typing import Any, Final, Literal, cast

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.active_clarification import (
    ApplyRoute,
    ClarificationTurn,
    TransitionOutcome,
    resolve_active_clarification_turn,
)
from ai_market_monitor.engine.active_question import (
    AnswerDomain,
    AnswerResolution,
    AnswerStage,
    canonical_values,
    display_options,
    domain_for_field,
    is_resume_request,
    labels_for,
    resolve_active_answer,
)
from ai_market_monitor.engine.capability_shortlist import (
    CapabilityShortlist,
    build_capability_shortlist,
    configured_runtime_provider_requirements,
)
from ai_market_monitor.engine.claim_evidence import (
    EvidenceLedger,
    build_evidence_ledger,
    deterministic_claim_text,
    requires_factual_answer,
    validate_claims,
)
from ai_market_monitor.engine.clarification_continuation import (
    CLARIFICATION_ANSWER_SEGMENT_ID,
    ContinuationAnswer,
    ContinuationRefused,
    build_continuation_operations,
    governed_continuation,
    governed_control_for,
    governed_option_selection,
    scan_continuation,
    workflow_continuation,
)
from ai_market_monitor.engine.comparators import detect_comparator
from ai_market_monitor.engine.conversation_intent import (
    ConversationIntent,
    IntentReading,
    ScanSlots,
    classify_turn,
    is_confusion_signal,
    scan_window_answer,
    scan_window_is_explicit,
)
from ai_market_monitor.engine.conversation_language import (
    ConversationLanguage,
    LanguageDecision,
    language_of,
    localized,
    localized_options,
    resolve_conversation_language,
    response_matches_language,
)
from ai_market_monitor.engine.planner_intent_compiler import (
    SANITATION_CLASSES,
    IntentCompileError,
    SemanticIntentOutcome,
    apply_repair_deltas,
    apply_topology_repair,
    compile_planner_intents,
    failure_class_for_code,
    intent_fingerprint,
    normalize_planner_envelope,
    semantic_value_is_grounded,
)
from ai_market_monitor.engine.planner_references import (
    EMPTY_PLANNER_REFERENCES,
    PlannerReferenceContext,
    SnapshotReference,
)
from ai_market_monitor.engine.price_movement import movement_direction
from ai_market_monitor.engine.repair_eligibility import RepairDecision, decide_repair
from ai_market_monitor.engine.requirement_state import (
    active_requirement_states,
    unresolved_target_path,
)
from ai_market_monitor.engine.response_reconciliation import (
    ConversationalGoal,
    Proposition,
    RenderedPart,
    RenderSource,
    confusion_recovery_reply,
    enforce_language,
    reconcile_reply,
    response_fingerprint,
)
from ai_market_monitor.engine.semantic_grounding import grounds_number
from ai_market_monitor.engine.setup_failure_taxonomy import (
    SetupFailureClass,
    TurnFailureRecord,
    failure_fingerprint,
    is_operator_alertable,
    owner_for,
)
from ai_market_monitor.engine.setup_turn_execution import (
    ConfirmedAnswer,
    ProviderGate,
    RuntimePreflight,
    ScreeningGate,
    SetupTurnRejected,
    SetupTurnRequest,
    apply_setup_turn,
    conversation_from_segments,
    validate_setup_turn_plan,
    validated_clarification,
)
from ai_market_monitor.engine.strategy_draft_v2 import validate_draft_semantics
from ai_market_monitor.engine.supported_incomplete import (
    MissingChoice,
    RequestAssessment,
    RequestCompleteness,
    assess_request,
    clarification_for_choice,
)
from ai_market_monitor.engine.timeframes import SUPPORTED_TIMEFRAMES
from ai_market_monitor.engine.turn_fragments import (
    extract_symbols,
    extract_timeframes,
    timeframe_role_is_explicit,
)
from ai_market_monitor.engine.turn_timing import (
    TurnDeadline,
    TurnTelemetry,
    null_telemetry,
)
from ai_market_monitor.engine.validated_intent_snapshot import (
    GroundedRequirement,
    RepeatState,
    ValidatedIntentSnapshot,
    normalized_intent_hash,
)
from ai_market_monitor.schemas.clarification_continuation import (
    ClarificationCompletionContract,
    ReplacementPolicy,
    clean_answer_shape,
    completion_contract_from_metadata,
    metadata_from_completion_contract,
)
from ai_market_monitor.schemas.planner_intent import (
    FORBIDDEN_SCHEMA_MODELS,
    BooleanTopologyRepair,
    PlannerIntentEnvelope,
    PlannerRepairEnvelope,
    ReadOnlyPercentageScanIntent,
    ReplaceBooleanPayload,
    SemanticIntent,
    SupportedIncompleteIntent,
    compact_json_schema,
    schema_complexity,
)
from ai_market_monitor.schemas.screening_execution import PreflightManifest
from ai_market_monitor.schemas.setup_agent import (
    DIALOGUE_WINDOW_MAX,
    SETUP_REPLY_MAX_LENGTH,
    CapabilityRoutingCandidate,
    CapabilityRoutingContext,
    ClarificationAnswer,
    ComposedReply,
    FactualClaim,
    PendingClarificationWorkflow,
    SegmentKind,
    SetupAgentReply,
    SetupAgentTurnPlan,
    SetupConversationContext,
    SetupTurnExecutionResult,
    StrategyInstructionPlan,
    TurnSegment,
)
from ai_market_monitor.schemas.setup_authorization import (
    AuthorizedPatchOperation,
    CancellationPolicy,
    ClarificationContract,
    ClarificationTargetType,
)
from ai_market_monitor.schemas.strategy import CapabilityParameterValue, Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    FORMULA_CONTRACTS,
    ConditionNodeType,
    ConditionNodeV2,
    DraftMode,
    FormulaKind,
    MovementDirection,
    OperandV2,
    StrategyDraftV2,
    UnresolvedFieldV2,
)
from ai_market_monitor.services.ai_model_routing import select_setup_model
from ai_market_monitor.services.openai_structured_call import (
    StructuredCallError,
    estimate_structured_call_cost,
    structured_call,
)

#: Time kept back from a provider call for the deterministic work that must follow it:
#: validation, canonical execution, compilation and persistence. A planner call allowed
#: to consume the whole budget leaves a turn that cannot be saved.
_POST_PLANNER_RESERVE_SECONDS = 6.0

#: Below this there is no point starting a provider call: it cannot return in time, and
#: an attempt that cannot land is a paid failure.
_MINIMUM_PROVIDER_SECONDS = 1.5

#: Composition happens after the mutation is already canonical, so only persistence has
#: to fit after it.
_POST_COMPOSER_RESERVE_SECONDS = 2.0

#: How long the shared circuit breaker may take to answer before the turn stops waiting
#: for it. It is a coordination hint between workers, not a correctness check: an answer
#: that arrives later than this costs more than the outage it reports.
_CIRCUIT_REDIS_TIMEOUT_SECONDS = 0.25

#: After a miss, stop asking for this long. Without it every provider call in the turn
#: pays the timeout again, which is how one unreachable cache became ten seconds.
_CIRCUIT_REDIS_COOLDOWN_SECONDS = 30.0


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
        usage: dict[str, Any] | None = None,
        failure_record: TurnFailureRecord | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.details = details
        # A provider call can succeed before deterministic authorization rejects the
        # proposed operation. Preserve that paid usage across the error boundary so
        # the launch service and evaluator cannot report the turn as costing $0.
        self.usage = dict(usage or {})
        # The typed forensics for this failure: who owns it, which field, which of the
        # trader's own words, and whether a correction was even possible. Persisted by
        # the caller so an operator can read it without a stack trace and a customer
        # gets a reference instead of "say that again".
        self.failure_record = failure_record

    @property
    def failure_class(self) -> SetupFailureClass:
        if self.failure_record is not None:
            return self.failure_record.failure_class
        return failure_class_for_code(self.code)

    @property
    def operator_alertable(self) -> bool:
        return is_operator_alertable(self.failure_class)


@dataclass(frozen=True, slots=True)
class _PlanFailure:
    code: str
    details: tuple[str, ...]
    outcome: SemanticIntentOutcome
    intent_ref: str | None = None
    target_path: str | None = None
    segment_ref: str | None = None
    #: Every model-owned field the same failure names. One rule can lose several
    #: stated values at once, and each is independently provable from the trader's
    #: words, so one bounded correction may address all of them.
    target_paths: tuple[str, ...] = ()

    @property
    def failure_class(self) -> SetupFailureClass:
        return failure_class_for_code(self.code)

    @property
    def paths(self) -> tuple[str, ...]:
        if self.target_paths:
            return self.target_paths
        return (self.target_path,) if self.target_path else ()


@dataclass(frozen=True, slots=True)
class _ServerClarification:
    """A typed question derived from a validated missing/ambiguous requirement."""

    contract: ClarificationContract
    envelope: PlannerIntentEnvelope
    usage: dict[str, Any]


class _PlannerRepairState(StrEnum):
    UNUSED = "unused"
    SHAPE_RECOVERY = "shape_recovery"
    SEMANTIC_REPAIR = "semantic_repair"


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
    #: The authenticated chat/session identity. It is distinct from the message id and
    #: is used only for persisted retry evidence and operational correlation.
    session_id: str = ""
    #: Recent user/assistant messages, oldest first, **excluding this turn** — it is
    #: already supplied as `current_user_turn`, and sending it twice invited the model
    #: to treat its own echo as prior context.
    dialogue: tuple[dict[str, str], ...] = ()
    conversation: SetupConversationContext = field(default_factory=SetupConversationContext)
    history: tuple[dict[str, Any], ...] = ()
    setup_mode: DraftMode = DraftMode.MONITOR
    #: Server-owned response language derived from the latest meaningful user turn.
    active_language: str = "en"
    #: True when the previous turn failed, so this one routes to the better model.
    previous_turn_failed: bool = False
    #: Final gates, supplied by the service that owns a database session.
    screening: ScreeningGate | None = None
    providers: ProviderGate | None = None
    runtime_preflight: RuntimePreflight | None = None
    #: Reads back what the preflight above actually checked, so the promise shown to the
    #: user and the promise bound into approval are the same recorded fact.
    preflight_manifest: Callable[[], PreflightManifest | None] | None = None
    stage_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None
    #: The turn's one clock: what each stage spent, and how much time is left.
    #:
    #: Supplied by the request boundary that owns the budget. A turn created without one
    #: records nothing and never expires, so helper and test call sites keep working
    #: without inheriting another turn's remaining time.
    telemetry: TurnTelemetry = field(default_factory=null_telemetry)
    #: Failure classes this chat has already hit, newest last. A class that has already
    #: survived a repair twice will not be given a third paid attempt: the answer is
    #: known, and spending on it again only makes the user wait longer for it.
    repeated_failure_codes: tuple[str, ...] = ()
    #: What earlier turns already established about this exact request: how many times
    #: it has been sent, which failures were already tried, and which values are already
    #: proved. Supplied by the service that owns the chat's stored context.
    repeats: RepeatState = field(default_factory=RepeatState)
    #: Governed identities hidden behind public, turn-local aliases. Conditions,
    #: clarifications and snapshots are added from the authoritative draft/history.
    planner_references: PlannerReferenceContext = EMPTY_PLANNER_REFERENCES
    #: Where the previous turn went, so a follow-up stays on the same road.
    previous_intent: str | None = None
    #: The conversation's language as last persisted. The latest user turn wins over
    #: it; this is what stops ``??`` or ``ok`` from resetting the language.
    session_language: str | None = None
    #: Fingerprints of replies already sent in this chat. A confusion signal must not
    #: be answered with something the trader has already read.
    previous_response_fingerprints: tuple[str, ...] = ()

    @property
    def deadline(self) -> TurnDeadline:
        return self.telemetry.deadline

    @property
    def active_mode(self) -> str:
        """Scanner or Monitor, read from the draft that governed execution also reads.

        This used to be a separate field carried beside ``setup_mode``. Two values for
        one fact is two answers: the router believed Scanner was active while the
        governed scan checked ``draft.mode`` and found Monitor, so a scan the trader had
        set up refused itself.
        """

        return str(self.setup_mode.value if self.setup_mode else DraftMode.MONITOR.value)

    @property
    def pending_scan(self) -> dict[str, object]:
        """The half-collected live scan, or an empty mapping.

        The one place this is kept is ``conversation.pending_read_only_scan``. Telemetry
        notes and a per-turn ``pending_goal`` field held copies of it before, and the
        copies were what the next turn read — so the direction and size the trader had
        already given were asked for a second time.
        """

        return dict(self.conversation.pending_read_only_scan or {})

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
    active_language: str = "en"
    response_fingerprint: str = ""
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
            "active_language": self.active_language,
            "response_fingerprint": self.response_fingerprint,
            "dropped_clarification": self.dropped_clarification,
            "model_intent_summaries_diagnostic_only": list(self.model_intent_summaries),
        }


@dataclass(frozen=True, slots=True)
class SetupAgentTurnResult:
    """Everything the caller needs to persist one completed turn."""

    reply: ComposedReply
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
    #: What this turn established, kept whether it succeeded or not.
    snapshot: ValidatedIntentSnapshot | None = None
    #: A planner-classified, server-validated current-market request.  The launch
    #: service executes it through the durable governed Scanner after this result is
    #: returned.  It is never a strategy mutation or approval instruction.
    read_only_scan_request: dict[str, object] | None = None

    @property
    def message(self) -> str:
        if self.clarification is None:
            return self.reply.message_without_question
        return f"{self.reply.message_without_question.rstrip()}\n\n{self.clarification.question}"


def _turn_request(
    turn: SetupAgentTurnInput,
    plan: SetupAgentTurnPlan,
    allowed_capability_keys: frozenset[str],
    *,
    include_gates: bool,
    cancelled_requirement_keys: frozenset[str] = frozenset(),
    confirmed_answer: ConfirmedAnswer | None = None,
    confirmed_paths: frozenset[str] = frozenset(),
) -> SetupTurnRequest:
    return SetupTurnRequest(
        server_option_confirmed_paths=confirmed_paths,
        plan=plan,
        message=turn.message,
        draft=turn.draft,
        source_turn_id=turn.source_turn_id,
        allowed_capability_keys=allowed_capability_keys,
        history=list(turn.history),
        conversation=turn.conversation,
        screening=turn.screening if include_gates else None,
        providers=turn.providers if include_gates else None,
        runtime_preflight=turn.runtime_preflight if include_gates else None,
        preflight_manifest=turn.preflight_manifest if include_gates else None,
        cancelled_requirement_keys=cancelled_requirement_keys,
        confirmed_answer=confirmed_answer,
        telemetry=turn.telemetry,
        planner_references=_planner_references(turn),
    )


def _planner_references(turn: SetupAgentTurnInput) -> PlannerReferenceContext:
    """Build stable turn-local aliases without exposing canonical identities."""

    condition_ids: dict[str, str] = {}
    if turn.draft.condition_ast is not None:
        for node in turn.draft.condition_ast.walk():
            if node.node_type == ConditionNodeType.CONDITION:
                condition_ids[f"condition_{len(condition_ids) + 1}"] = node.node_id

    clarification_ids: dict[str, str] = {}
    for item in turn.draft.unresolved_fields:
        if item.blocking and item.unresolved_id not in clarification_ids.values():
            clarification_ids[f"clarification_{len(clarification_ids) + 1}"] = item.unresolved_id
    active_id = turn.conversation.active_question_id
    if active_id and active_id not in clarification_ids.values():
        clarification_ids[f"clarification_{len(clarification_ids) + 1}"] = active_id

    snapshots = tuple(
        SnapshotReference(
            reference=f"snapshot_{index + 1}",
            snapshot_id=str(item["snapshot_id"]),
            executable_version=int(item["executable_version"]),
        )
        for index, item in enumerate(turn.history[-20:])
        if item.get("snapshot_id") and item.get("executable_version") is not None
    )
    return PlannerReferenceContext(
        condition_ids=condition_ids,
        clarification_ids=clarification_ids,
        snapshots=snapshots,
        methodologies=turn.planner_references.methodologies,
        watchlists=turn.planner_references.watchlists,
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

    # The circuit breaker used to live here: its own state, its own Redis coordination,
    # its own key scheme. That made two breakers for one provider — this one and the
    # shared one inside every guarded provider call — so a failure one of them saw was a
    # failure the other had never heard of. There is one breaker now, in
    # ``provider_reliability``, consulted by the call itself, and its refusal arrives here
    # as ``SETUP_AGENT_CIRCUIT_OPEN`` exactly as before.

    def _conversational_route(
        self,
        turn: SetupAgentTurnInput,
        reading: IntentReading,
        language: LanguageDecision,
    ) -> SetupAgentTurnResult | None:
        """Answer the turns that never needed a planner call, or return ``None``.

        Three destinations are settled here, each of which used to be handled by
        planning the sentence as a strategy edit and then explaining why that failed:

        * a confusion signal — answered by recovery, never by repeating;
        * a live market question with a missing choice — answered by asking for that
          one choice, with Scanner context intact;
        * a supported request missing a trader's choice — answered by asking for it,
          instead of recording it as an unsupported requirement.

        Nothing here mutates the draft, approves anything, or invents a value. Every
        one of these paths returns ``execution=None``, which is the existing contract
        for a read-only turn.
        """

        telemetry = turn.telemetry

        if "mode_selection" in reading.reasons:
            # A bare mode word is the product's own start button, not a sentence to
            # plan. This must test what *this turn* chose, never merely which mode is
            # active: every conversation has an active mode, so reading that as a
            # selection answered "yes" and "hello" with the Scanner start question.
            #
            # It is also the first thing a beginner does, and it used to be answered
            # with a bare form question. Saying what they just chose, and why it is a
            # good thing to have chosen, costs one sentence and is the difference
            # between a product and a form.
            welcome = localized(
                f"mode.{reading.selected_mode or 'monitor'}_welcome", language.language
            )
            question = localized(
                "ask.symbol_scope"
                if reading.selected_mode != "scanner"
                else "ask.movement_kind",
                language.language,
            )
            reconciled = reconcile_reply((), clarification=question)
            telemetry.notes["selected_mode"] = reading.selected_mode
            telemetry.notes.update(reconciled.to_dict())
            return self._read_only_result(
                turn,
                f"{welcome}\n\n{reconciled.message}",
                language,
                reading,
                route="mode_selection",
            )

        if (
            reading.intent is ConversationIntent.SOCIAL
            and turn.conversation.active_question is None
        ):
            # Just talking. There is nothing to compile and nothing to refuse, so the
            # assistant talks back. This used to fall through to the planner and come
            # out as "Nothing is set up yet", which answers a greeting with a complaint.
            #
            # Only while no question is open. A bare "yes" or "ok" is small talk on its
            # own and an *answer* the moment something asked — treating it as small talk
            # then is the exact defect this whole change removes, wearing a friendlier
            # face.
            content = _social_reply(turn.message, turn.draft, language.language)
            telemetry.notes.update(reconcile_reply(()).to_dict())
            return self._read_only_result(
                turn, content, language, reading, route="social_chat"
            )

        if reading.intent is ConversationIntent.PRODUCT_EXPLANATION and _asks_scanner_vs_monitor(
            turn.message
        ):
            # The one product question this chat gets constantly, answered from
            # server-owned product fact. This is the *right* place for the
            # Scanner-versus-Monitor sentence — it was only ever wrong as the answer to
            # a live market question.
            answer = localized("product.scanner_vs_monitor", language.language)
            telemetry.notes.update(reconcile_reply(()).to_dict())
            return self._read_only_result(
                turn, answer, language, reading, route="product_explanation"
            )

        pending = turn.pending_scan

        if reading.intent is ConversationIntent.CONFUSION_SIGNAL:
            goal = _goal_from_pending(pending, turn.conversation)
            recovered = confusion_recovery_reply(
                goal,
                language=language.language,
                previous_fingerprints=turn.previous_response_fingerprints,
            )
            telemetry.notes["confusion_recovery"] = True
            telemetry.notes.update(recovered.to_dict())
            # A miss does not lose the goal. The pending scan and its open question are
            # carried through untouched, so the next answer still lands on them.
            return self._read_only_result(
                turn, recovered.message, language, reading, route="confusion_recovery"
            )

        if reading.intent is ConversationIntent.ON_DEMAND_SCAN:
            return self._route_read_only_scan(turn, reading, language, pending)

        if reading.intent in {
            ConversationIntent.STRATEGY_EDIT,
            ConversationIntent.CONTINUOUS_MONITOR,
        }:
            assessment = assess_request(
                turn.message,
                offered_capability_keys=None,
                known_symbols=turn.draft.universe.included_symbols,
                known_window=_draft_trigger_timeframe(turn.draft),
            )
            telemetry.notes.update(assessment.to_dict())
            if not assessment.is_supported_incomplete:
                # Complete, or genuinely outside what the registry can express. Both
                # belong to the planner and the canonical path exactly as before.
                return None
            if not _too_bare_to_plan(turn, assessment):
                # The planner can still read something useful out of this turn, so it
                # gets to. Pre-empting a turn the model would have compiled correctly
                # would trade one product defect for another: an assistant that asks
                # questions instead of doing the work.
                return None
            choice = assessment.next_question
            if choice is None:  # pragma: no cover - `missing` is non-empty here
                return None
            clarification = clarification_for_choice(
                choice,
                language=language.language,
                source_turn_id=turn.source_turn_id,
                threshold_percent=assessment.supplied.get("threshold_percent"),
            )
            reconciled = reconcile_reply((), clarification=clarification.question)
            telemetry.notes.update(reconciled.to_dict())
            telemetry.notes["supported_incomplete"] = True
            conversation = turn.conversation.model_copy(
                update={"last_assistant_summary": reconciled.message[:1000]}
            ).with_question(clarification)
            # The question is the whole reply, so it is the body. The contract is
            # carried on the conversation — where the next turn verifies that an
            # answer really resolved it — and deliberately *not* on the result, because
            # ``SetupAgentTurnResult.message`` appends a result-level clarification and
            # would print the same question a second time.
            result = self._read_only_result(
                turn,
                reconciled.message,
                language,
                reading,
                route="supported_incomplete_clarification",
            )
            return replace(result, conversation=conversation)

        return None

    def _route_read_only_scan(
        self,
        turn: SetupAgentTurnInput,
        reading: IntentReading,
        language: LanguageDecision,
        pending: dict[str, object],
    ) -> SetupAgentTurnResult | None:
        """Collect a live scan, one missing choice at a time, then hand it to execution.

        Everything this collects is written to ``conversation.pending_read_only_scan``
        and the question is a real :class:`ClarificationContract` attached with
        ``with_question``. That is the state the continuation turn, the governed
        execution path and the option-button path all already read. Keeping it in
        telemetry instead was why answering "24 hours" started a new conversation
        rather than finishing the scan that had asked.
        """

        telemetry = turn.telemetry
        merged = _merge_scan_request(pending, reading.slots, turn.message)
        threshold = merged.get("threshold_percent")
        direction = merged.get("movement_direction")

        if threshold is None or direction is None:
            # Nothing usable to hold on to. The planner still gets this turn, because a
            # sentence this reader could not resolve is exactly what a model is for.
            telemetry.notes["scan_execution"] = "not_enough_to_hold"
            return None

        if merged.get("measurement_window") is None:
            question = localized("ask.scan_window_24h", language.language)
            clarification = scan_window_contract(question, turn.source_turn_id)
            reconciled = reconcile_reply((), clarification=question)
            telemetry.notes["scan_execution"] = "awaiting_user_choice"
            telemetry.notes["scan_pending_request"] = dict(merged)
            telemetry.notes.update(reconciled.to_dict())
            conversation = turn.conversation.model_copy(
                update={
                    "active_language": language.language.value,
                    "active_goal": READ_ONLY_SCAN_GOAL,
                    "pending_read_only_scan": merged,
                    "last_assistant_summary": reconciled.message[:1000],
                }
            ).with_question(clarification)
            result = self._read_only_result(
                turn,
                reconciled.message,
                language,
                reading,
                route="on_demand_scan_clarification",
            )
            return replace(result, conversation=conversation)

        if turn.setup_mode != DraftMode.SCANNER:
            # The values are kept, so choosing Scanner next finishes the same scan
            # rather than asking for all of it again.
            content = localized("scan.mode_required", language.language)
            telemetry.notes["scan_execution"] = "mode_required"
            conversation = turn.conversation.model_copy(
                update={
                    "active_language": language.language.value,
                    "active_goal": READ_ONLY_SCAN_GOAL,
                    "pending_read_only_scan": merged,
                    "last_assistant_summary": content[:1000],
                }
            )
            result = self._read_only_result(
                turn, content, language, reading, route="on_demand_scan_mode_required"
            )
            return replace(result, conversation=conversation)

        if not _scan_universe_is_ready(turn.draft):
            scope = scan_scope_clarification(language.language)
            # The body is the reason, and only the reason. `SetupAgentTurnResult.message`
            # appends the attached question itself, so putting it here as well printed
            # "Which screened assets should Hilal Markets watch?" twice in one reply.
            content = localized("scan.scope_required", language.language)
            telemetry.notes["scan_execution"] = "scope_required"
            # The half-collected scan is kept, and the scope becomes a question the next
            # message can answer. Attempting execution and then dropping the goal is
            # what made a recoverable gap look like a dead end.
            conversation = turn.conversation.model_copy(
                update={
                    "active_language": language.language.value,
                    "active_goal": READ_ONLY_SCAN_GOAL,
                    "pending_read_only_scan": merged,
                    "last_assistant_summary": f"{content}\n\n{scope.question}"[:1000],
                }
            ).with_question(scope)
            result = self._read_only_result(
                turn,
                content,
                language,
                reading,
                route="on_demand_scan_scope_required",
                clarification=scope,
            )
            return replace(result, conversation=conversation)

        # Complete and inside Scanner. The scan itself runs in the service that owns the
        # authenticated session, the screened universe and the provider quota; this agent
        # has none of those, so it hands over a request rather than inventing a result.
        content = localized("scan.running", language.language)
        telemetry.notes["scan_execution"] = "requested"
        telemetry.notes["scan_request"] = dict(merged)
        # Collecting is finished, so the goal is released here and the values are left
        # for the service that runs the scan. It clears them on success or on a refusal
        # the trader cannot act on, and keeps them when choosing a screened scope would
        # let the same scan finish. Holding the goal open past hand-over would make
        # every later message re-enter the scan and run it again.
        conversation = turn.conversation.model_copy(
            update={
                "active_language": language.language.value,
                "active_goal": None,
                "pending_read_only_scan": merged,
                "last_assistant_summary": content[:1000],
            }
        ).cleared_question()
        result = self._read_only_result(
            turn, content, language, reading, route="on_demand_scan_authorized"
        )
        return replace(
            result, conversation=conversation, read_only_scan_request=dict(merged)
        )

    def _read_only_result(
        self,
        turn: SetupAgentTurnInput,
        message: str,
        language: LanguageDecision,
        reading: IntentReading,
        *,
        route: str,
        clarification: ClarificationContract | None = None,
    ) -> SetupAgentTurnResult:
        """One read-only turn: words back, and no change to any persisted state.

        When a clarification is attached, ``message`` must be the body *without* it:
        ``SetupAgentTurnResult.message`` appends the question itself, and passing a
        body that already contains it prints the same question twice — the very
        duplication this work exists to remove.
        """

        safe = enforce_language(message, language.language) if message else ""
        telemetry = turn.telemetry
        telemetry.notes["conversation_route"] = route
        telemetry.notes["final_mutation_status"] = "no_mutation"
        telemetry.notes["response_fingerprint"] = response_fingerprint(
            f"{safe} {clarification.question}".strip() if clarification else safe
        )
        conversation = turn.conversation.model_copy(
            update={"last_assistant_summary": safe[:1000]}
        )
        return SetupAgentTurnResult(
            reply=deterministic_reply(safe),
            execution=None,
            draft=turn.draft,
            conversation=conversation,
            plan=None,
            trace=SetupAgentTrace(
                source_turn_id=turn.source_turn_id,
                planner_model="deterministic_router",
                patch_validation="not_attempted",
                compile_status="not_attempted",
                response_model=route,
                model_calls=0,
            ),
            clarification=clarification,
            usage={},
        )

    async def run_turn(self, turn: SetupAgentTurnInput) -> SetupAgentTurnResult:
        self.model_call_count = 0
        self.last_usage = {}
        telemetry = turn.telemetry
        telemetry.notes.setdefault("compiler_invariant_violation_count", 0)
        telemetry.notes.setdefault("planner_repair_mode", _PlannerRepairState.UNUSED.value)
        telemetry.notes.setdefault("planner_repair_attempt_count", 0)
        telemetry.notes.setdefault("planner_repair_success_count", 0)
        telemetry.notes.update(turn.repeats.to_dict())
        # Even an unreadable/empty provider response gets an immutable turn snapshot.
        # It contains no claimed requirements until fields are independently grounded.
        telemetry.notes["validated_intent_snapshot"] = ValidatedIntentSnapshot(
            session_id=turn.session_id or turn.source_turn_id,
            source_turn_id=turn.source_turn_id,
            canonical_draft_hash=turn.draft.executable_hash or "",
            normalized_user_intent_hash=_normalized_intent_hash(turn.normalized_message),
        ).to_dict()

        # Where is this turn going? Decided before anything is planned, because the
        # answer changes whether a planner call happens at all. A confusion signal, a
        # live market question and a request that is simply missing one choice are
        # each answered without asking a model to re-read a sentence the server has
        # already understood.
        language = resolve_conversation_language(
            turn.message,
            session_language=turn.session_language or turn.active_language,
        )
        reading = classify_turn(
            turn.message,
            active_mode=turn.active_mode,
            previous_intent=(
                ConversationIntent(turn.previous_intent) if turn.previous_intent else None
            ),
            has_open_question=bool(turn.conversation.active_question_id),
            # A scan is only "pending" while its own question is still open. Without
            # that second half, a finished scan kept swallowing the next turn.
            pending_scan=bool(
                turn.conversation.pending_read_only_scan
                and turn.conversation.active_goal == READ_ONLY_SCAN_GOAL
            ),
        )
        telemetry.notes.update(language.to_dict())
        telemetry.notes.update(reading.to_dict())

        # An open question owns the next message. This runs before classification,
        # planning and extraction on purpose: a short, misspelled or simply unexpected
        # answer used to be re-read as a brand new request and answered with "Nothing
        # is set up yet", while the question it was answering stayed open.
        early_trace = SetupAgentTrace(
            source_turn_id=turn.source_turn_id,
            planner_model="deterministic_active_question",
            plan_confidence=1.0,
            active_language=turn.active_language,
        )
        answered = await self._answer_active_question(
            turn, reading, language, trace=early_trace
        )
        if answered is not None:
            return answered

        conversational = self._conversational_route(turn, reading, language)
        if conversational is not None:
            return conversational

        with telemetry.stage("context_selection"):
            shortlist = build_capability_shortlist(
                turn.normalized_message,
                available_provider_requirements=configured_runtime_provider_requirements(
                    self.settings.market_data_provider
                ),
            )
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
        started = monotonic()
        repair_state = _PlannerRepairState.UNUSED
        try:
            envelope, plan_usage = await self._plan_once(turn, shortlist, route)
        except StructuredCallError as exc:
            if not _answer_did_not_parse(exc):
                record = _structured_failure_record(turn, exc)
                telemetry.notes["turn_failure_record"] = record.to_dict()
                raise SetupAgentError(
                    exc.code,
                    str(exc),
                    stage="planning",
                    retryable=exc.retryable,
                    details=exc.details,
                    usage={**exc.usage, **route.usage_metadata()},
                    failure_record=record,
                ) from exc
            # Nothing came back that can be corrected field by field, so a delta has
            # nothing to name. One more attempt at the same compact contract is the only
            # recovery available, and it replaces the repair this turn is allowed.
            try:
                envelope, plan_usage = await self._plan_once(
                    turn, shortlist, route, prior_usage=exc.usage, retry_after_bad_shape=True
                )
            except StructuredCallError as retry_exc:
                record = _structured_failure_record(turn, retry_exc)
                telemetry.notes["turn_failure_record"] = record.to_dict()
                raise SetupAgentError(
                    retry_exc.code,
                    str(retry_exc),
                    stage="planner_repair",
                    retryable=retry_exc.retryable,
                    details=retry_exc.details,
                    usage={**retry_exc.usage, **route.usage_metadata()},
                    failure_record=record,
                ) from retry_exc
            plan_usage["_setup_repair_attempts"] = 1
            repair_state = _PlannerRepairState.SHAPE_RECOVERY
            telemetry.notes["planner_repair_mode"] = repair_state.value
        planner_model = route.model
        planner_reasons = route.reasons
        plan_usage = {**plan_usage, **route.usage_metadata()}
        _record_cost_telemetry(telemetry, plan_usage, self.settings, route.model)
        planner_latency = (monotonic() - started) * 1000
        # `_plan_once` already counted its model call. Counting again here would make
        # the per-turn cost and latency telemetry inaccurate.
        self.last_usage = plan_usage

        trace = SetupAgentTrace(
            source_turn_id=turn.source_turn_id,
            planner_model=planner_model,
            planner_reasons=planner_reasons,
            planner_latency_ms=planner_latency,
            plan_confidence=envelope.overall_confidence,
            segments=_segment_trace(envelope),
            shortlist_keys=tuple(sorted(shortlist.allowed_keys)),
            lexical_hint="",
            model_calls=self.model_call_count,
            active_language=turn.active_language,
        )

        # Record what this reading established *before* anything can refuse it. A turn
        # that fails still proved most of what the trader wrote, and keeping that is
        # what lets the next turn answer without asking for the whole instruction
        # again. Written to telemetry so the success and failure paths both persist it.
        telemetry.notes["validated_intent_snapshot"] = ValidatedIntentSnapshot(
            session_id=turn.session_id or turn.source_turn_id,
            source_turn_id=turn.source_turn_id,
            canonical_draft_hash=turn.draft.executable_hash or "",
            normalized_user_intent_hash=_normalized_intent_hash(turn.normalized_message),
            grounded_requirements=grounded_requirements_from(
                envelope,
                turn.message,
                _planner_references(turn),
            ),
        ).to_dict()

        # These routes depend on the planner's typed envelope.  They must run only
        # after planning, trace construction and grounded-snapshot capture; placing
        # them earlier would reference undefined state and would also bypass AI-first
        # classification.
        read_only = await self._handle_read_only_scan_intent(
            turn,
            envelope,
            trace=trace,
            usage=plan_usage,
        )
        if read_only is not None:
            return read_only

        continued = await self._continue_supported_incomplete(
            turn,
            envelope,
            trace=trace,
            usage=plan_usage,
        )
        if continued is not None:
            return continued

        # A registered/core mechanic with missing user-controlled values is not an
        # unsupported capability. Start one typed clarification and keep the grounded
        # parts in conversation context so a confusion signal can recover the real goal.
        incomplete_requests = list(envelope.supported_incomplete_intents)
        if not incomplete_requests:
            promoted = _promote_known_incomplete_request(turn, envelope)
            if promoted is not None:
                incomplete_requests.append(promoted)
                telemetry.notes["promoted_unsupported_to_supported_incomplete"] = True
        if incomplete_requests:
            return await self._start_supported_incomplete(
                turn,
                incomplete_requests[0],
                envelope,
                trace=trace,
                usage=plan_usage,
            )

        # Sanitation, compilation and dry validation are one bounded attempt, and at most
        # one repair call for the whole turn. Both failure sources — a reading the server
        # cannot compile, and a plan the authorization gates refuse — go through the same
        # narrow correction, so a turn can never spend two repairs or loop between them.
        settled = await self._settled_plan(
            turn,
            shortlist,
            route,
            envelope,
            plan_usage,
            repair_state=repair_state,
        )
        if isinstance(settled, _ServerClarification):
            clarification = settled.contract
            intro = localized("ask.intro", language_of(turn.active_language))
            reply = deterministic_reply(
                intro,
                selected_clarification_id=clarification.question_id,
            )
            trace = _with(
                trace,
                model_calls=self.model_call_count,
                patch_validation="user_information_required",
                response_model="deterministic_clarification",
            )
            fingerprint = response_fingerprint(
                f"{intro}\n\n{clarification.question}"
            )
            conversation = turn.conversation.model_copy(
                update={"active_language": turn.active_language}
            ).with_question(clarification)
            conversation = conversation.model_copy(
                update={"last_response_fingerprint": fingerprint}
            )
            trace = _with(trace, response_fingerprint=fingerprint)
            return SetupAgentTurnResult(
                reply=reply,
                execution=None,
                draft=turn.draft,
                conversation=conversation,
                plan=None,
                trace=trace,
                clarification=clarification,
                usage=settled.usage,
            )
        plan, envelope, plan_usage, repaired = settled
        self.last_usage = plan_usage
        trace = _with(
            trace,
            plan_confidence=plan.overall_confidence,
            segments=tuple(
                {
                    "segment_id": item.segment_id,
                    "kind": item.kind.value,
                    "text": item.exact_source_text,
                    "confidence": item.confidence,
                    "action_required": item.action_required,
                }
                for item in plan.segments
            ),
            model_calls=self.model_call_count,
            patch_validation="repaired" if repaired else "dry_validated",
        )
        if turn.stage_callback is not None:
            await turn.stage_callback(
                "EXECUTING" if plan.requires_tool else "COMPOSING",
                {
                    "planner_model": planner_model,
                    "plan": plan.model_dump(mode="json"),
                },
            )
        if not plan.requires_tool:
            # Pure conversation. No tool, no new version, no status change, and still a
            # real answer. A greeting cannot touch approval because it never gets here.
            # A pure-conversation turn asserts nothing about the platform: no operations
            # ran, no gates were evaluated, and there is no evidence ledger to check
            # against. The planner's own wording is the reply.
            reply = deterministic_reply(
                _deterministic_conversation_reply(
                    turn.draft,
                    envelope=envelope,
                    language=turn.active_language,
                    message=turn.message,
                )
            )
            conversation = conversation_from_segments(
                turn.conversation,
                list(plan.segments),
                assistant_summary=reply.message,
            ).model_copy(
                update={
                    "active_language": turn.active_language,
                    "last_response_fingerprint": response_fingerprint(reply.message),
                }
            )
            trace = _with(
                trace, response_fingerprint=response_fingerprint(reply.message)
            )
            return SetupAgentTurnResult(
                reply=reply,
                execution=None,
                draft=turn.draft,
                conversation=conversation,
                plan=plan,
                trace=trace,
                usage=self.last_usage,
            )

        try:
            with telemetry.stage("canonical_execution"):
                outcome = await apply_setup_turn(
                    _turn_request(turn, plan, shortlist.allowed_keys, include_gates=True)
                )
        except SetupTurnRejected as exc:
            raise SetupAgentError(
                exc.code,
                str(exc),
                stage="tool_validation",
                details=exc.details,
                usage=self.last_usage,
            ) from exc
        if repaired:
            self.last_usage["_setup_repair_successes"] = 1
            _record_cost_telemetry(telemetry, self.last_usage, self.settings, route.model)

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
        reply = deterministic_reply(
            deterministic_summary(outcome.result, language=turn.active_language),
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
                    service_tier=route.service_tier,
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
        speaks_the_conversation = response_matches_language(
            reply.message_without_question, language_of(turn.active_language)
        )
        if not speaks_the_conversation:
            reply = deterministic_reply(
                deterministic_summary(outcome.result, language=turn.active_language),
                selected_clarification_id=reply.selected_clarification_id,
            )
            response_model = "deterministic_language_fallback"
        fingerprint = response_fingerprint(reply.message_without_question)
        trace = _with(
            trace,
            response_model=response_model,
            response_latency_ms=(monotonic() - composed_started) * 1000,
            model_calls=self.model_call_count,
            response_fingerprint=fingerprint,
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
            update={
                "last_assistant_summary": final_message[:1000],
                "active_language": turn.active_language,
                "last_response_fingerprint": response_fingerprint(final_message),
            }
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

    async def _handle_read_only_scan_intent(
        self,
        turn: SetupAgentTurnInput,
        envelope: PlannerIntentEnvelope,
        *,
        trace: SetupAgentTrace,
        usage: dict[str, Any],
    ) -> SetupAgentTurnResult | None:
        """Route a planner-classified current-market question without mutating a draft.

        Arbitrary text has already passed through the planner.  Deterministic code only
        validates the typed intent, preserves an unfinished request, and decides whether
        the governed Scanner has enough server-owned context to run.
        """

        pending = dict(turn.conversation.pending_read_only_scan or {})
        if pending and is_confusion_signal(turn.message):
            question = localized("ask.scan_window_24h", language_of(turn.active_language))
            content = localized("confusion.scan_retry", language_of(turn.active_language),
                threshold=f"{float(str(pending.get('threshold_percent') or 0)):g}%",
                direction=str(pending.get("movement_direction") or "up"),
                question=question,
            )
            fingerprint = response_fingerprint(content)
            conversation = turn.conversation.model_copy(
                update={
                    "active_language": turn.active_language,
                    "active_goal": "read_only_percentage_scan",
                    "confusion_recovery_count": (
                        turn.conversation.confusion_recovery_count + 1
                    ),
                    "last_response_fingerprint": fingerprint,
                }
            )
            return SetupAgentTurnResult(
                reply=deterministic_reply(content),
                execution=None,
                draft=turn.draft,
                conversation=conversation,
                plan=None,
                trace=_with(
                    trace,
                    patch_validation="read_only_confusion_recovery",
                    response_model="deterministic_read_only",
                    response_fingerprint=fingerprint,
                ),
                usage=usage,
            )

        scan: ReadOnlyPercentageScanIntent | None = (
            envelope.read_only_percentage_scans[0]
            if envelope.read_only_percentage_scans
            else None
        )
        continued_pending_window = False
        if scan is None and pending and _planner_answered_active_question(turn, envelope):
            window = scan_window_answer(turn.message)
            if window is None:
                question = localized("ask.scan_window_24h", language_of(turn.active_language))
                fingerprint = response_fingerprint(question)
                conversation = turn.conversation.model_copy(
                    update={
                        "active_language": turn.active_language,
                        "active_goal": "read_only_percentage_scan",
                        "last_response_fingerprint": fingerprint,
                    }
                )
                return SetupAgentTurnResult(
                    reply=deterministic_reply(question),
                    execution=None,
                    draft=turn.draft,
                    conversation=conversation,
                    plan=None,
                    trace=_with(
                        trace,
                        patch_validation="read_only_window_unresolved",
                        response_model="deterministic_read_only",
                        response_fingerprint=fingerprint,
                    ),
                    usage=usage,
                )
            continued_pending_window = True
            scan = ReadOnlyPercentageScanIntent(
                segment_ref=(
                    envelope.clarification_answers[0].segment_ref
                    if envelope.clarification_answers
                    else envelope.segments[0].segment_ref
                ),
                movement_direction=cast(
                    Any, str(pending.get("movement_direction") or "up")
                ),
                threshold_percent=float(str(pending.get("threshold_percent") or 0)),
                # `scan_window_answer` returns the one supported window or nothing, and
                # `window` is not None here, so this is that window.
                measurement_window=cast(Literal["24h"], window),
            )

        if scan is None:
            return None
        source_segment = next(
            (item for item in envelope.segments if item.segment_ref == scan.segment_ref),
            None,
        )
        prior_source = str(pending.get("source_text") or "")
        prior_scan = (
            ReadOnlyPercentageScanIntent(
                segment_ref=scan.segment_ref,
                movement_direction=scan.movement_direction,
                threshold_percent=scan.threshold_percent,
                measurement_window=None,
            )
            if continued_pending_window
            else None
        )
        values_grounded = bool(
            source_segment is not None
            and (
                (
                    continued_pending_window
                    and prior_scan is not None
                    and _read_only_scan_values_are_grounded(prior_scan, prior_source)
                    and scan_window_answer(source_segment.exact_source_text)
                    == scan.measurement_window
                )
                or (
                    not continued_pending_window
                    and _read_only_scan_values_are_grounded(
                        scan, source_segment.exact_source_text
                    )
                )
            )
        )
        if not values_grounded:
            raise SetupAgentError(
                "VALUE_NOT_GROUNDED",
                "The current-market scan values did not match the user's exact words.",
                stage="tool_validation",
                details=("read_only_percentage_scan:source_mismatch",),
                usage=usage,
            )
        if turn.setup_mode != DraftMode.SCANNER:
            request: dict[str, object] = {
                "movement_direction": scan.movement_direction,
                "threshold_percent": scan.threshold_percent,
                "measurement_window": scan.measurement_window,
                "source_text": next(
                    (
                        item.exact_source_text
                        for item in envelope.segments
                        if item.segment_ref == scan.segment_ref
                    ),
                    turn.message,
                )[:500],
            }
            content = localized("scan.mode_required", language_of(turn.active_language))
            fingerprint = response_fingerprint(content)
            return SetupAgentTurnResult(
                reply=deterministic_reply(content),
                execution=None,
                draft=turn.draft,
                conversation=turn.conversation.model_copy(
                    update={
                        "active_language": turn.active_language,
                        "active_goal": "read_only_percentage_scan",
                        "pending_read_only_scan": request,
                        "last_response_fingerprint": fingerprint,
                    }
                ),
                plan=None,
                trace=_with(
                    trace,
                    patch_validation="read_only_wrong_mode",
                    response_model="deterministic_read_only",
                    response_fingerprint=fingerprint,
                ),
                usage=usage,
            )

        request = {
            "movement_direction": scan.movement_direction,
            "threshold_percent": scan.threshold_percent,
            "measurement_window": scan.measurement_window,
            "source_text": next(
                (
                    item.exact_source_text
                    for item in envelope.segments
                    if item.segment_ref == scan.segment_ref
                ),
                turn.message,
            )[:500],
        }
        if not _scan_universe_is_ready(turn.draft):
            request["measurement_window"] = scan.measurement_window
            scope = scan_scope_clarification(language_of(turn.active_language))
            content = (
                f"{localized('scan.scope_required', language_of(turn.active_language))}"
                f"\n\n{scope.question}"
            )
            fingerprint = response_fingerprint(content)
            conversation = turn.conversation.model_copy(
                update={
                    "active_language": turn.active_language,
                    "active_goal": "read_only_percentage_scan",
                    "pending_read_only_scan": request,
                    "last_response_fingerprint": fingerprint,
                }
            ).with_question(scope)
            return SetupAgentTurnResult(
                reply=deterministic_reply(
                    content, selected_clarification_id=scope.question_id
                ),
                execution=None,
                draft=turn.draft,
                conversation=conversation,
                plan=None,
                trace=_with(
                    trace,
                    patch_validation="read_only_scope_required",
                    response_model="deterministic_read_only",
                    response_fingerprint=fingerprint,
                ),
                clarification=scope,
                usage=usage,
            )

        if scan.measurement_window is None:
            question = localized("ask.scan_window_24h", language_of(turn.active_language))
            # One builder for this question, shared with the governed path. Two copies
            # lived here before and they disagreed: this one named no field and no
            # canonical values, so only the literal string `24h` could answer it and
            # "24 hours" fell through as a brand new request.
            clarification = scan_window_contract(question, turn.source_turn_id)
            request["measurement_window"] = None
            fingerprint = response_fingerprint(question)
            conversation = turn.conversation.model_copy(
                update={
                    "active_language": turn.active_language,
                    "active_goal": "read_only_percentage_scan",
                    "pending_read_only_scan": request,
                }
            ).with_question(clarification)
            conversation = conversation.model_copy(
                update={"last_response_fingerprint": fingerprint}
            )
            return SetupAgentTurnResult(
                reply=deterministic_reply(
                    localized("scan.acknowledged", language_of(turn.active_language)),
                    selected_clarification_id=clarification.question_id,
                ),
                execution=None,
                draft=turn.draft,
                conversation=conversation,
                plan=None,
                trace=_with(
                    trace,
                    patch_validation="read_only_window_required",
                    response_model="deterministic_read_only",
                    response_fingerprint=fingerprint,
                ),
                clarification=clarification,
                usage=usage,
            )

        content = localized("scan.running", language_of(turn.active_language))
        fingerprint = response_fingerprint(content)
        conversation = turn.conversation.model_copy(
            update={
                "active_language": turn.active_language,
                "active_goal": "read_only_percentage_scan",
                "pending_read_only_scan": request,
                "last_response_fingerprint": fingerprint,
            }
        ).cleared_question()
        return SetupAgentTurnResult(
            reply=deterministic_reply(content),
            execution=None,
            draft=turn.draft,
            conversation=conversation,
            plan=None,
            trace=_with(
                trace,
                patch_validation="read_only_scan_authorized",
                response_model="governed_scanner",
                response_fingerprint=fingerprint,
            ),
            usage=usage,
            read_only_scan_request=request,
        )

    async def _start_supported_incomplete(
        self,
        turn: SetupAgentTurnInput,
        request: SupportedIncompleteIntent,
        envelope: PlannerIntentEnvelope,
        *,
        trace: SetupAgentTrace,
        usage: dict[str, Any],
    ) -> SetupAgentTurnResult:
        metadata = _supported_request_context(turn, request, envelope)
        if not _is_canonical_percentage_request(metadata):
            clarification = _clarification_for_supported_incomplete(turn, request, envelope)
            intro = localized("ask.intro", language_of(turn.active_language))
            final = f"{intro}\n\n{clarification.question}"
            fingerprint = response_fingerprint(final)
            conversation = turn.conversation.model_copy(
                update={
                    "active_language": turn.active_language,
                    "active_goal": "complete_supported_setup",
                    "pending_supported_request": metadata,
                    "last_response_fingerprint": fingerprint,
                }
            ).with_question(clarification)
            return SetupAgentTurnResult(
                reply=deterministic_reply(
                    intro, selected_clarification_id=clarification.question_id
                ),
                execution=None,
                draft=turn.draft,
                conversation=conversation,
                plan=None,
                trace=_with(
                    trace,
                    patch_validation="supported_incomplete_non_percentage",
                    response_model="deterministic_clarification",
                    response_fingerprint=fingerprint,
                ),
                clarification=clarification,
                usage=usage,
            )
        missing = _supported_missing_slots(metadata, request.missing_fields)
        if not missing:
            raise SetupAgentError(
                "INTENT_INCOMPLETE",
                "That supported rule is missing a value I could not identify safely.",
                stage="tool_validation",
                usage=usage,
            )
        field_name = _next_supported_field(missing)
        segment = next(
            item for item in envelope.segments if item.segment_ref == request.segment_ref
        )
        unresolved_id = "supported_" + hashlib.sha256(
            f"{turn.source_turn_id}:{request.segment_ref}".encode()
        ).hexdigest()[:24]
        # The workflow and its first question are built together, and the workflow's id
        # is the blocker's id — so the conversational record and the executable one are
        # provably about the same thing, while each *step* keeps its own question id.
        workflow, clarification = _new_supported_workflow(
            workflow_id=unresolved_id,
            fields=[field_name, *(item for item in missing if item != field_name)],
            evidence=segment.exact_source_text,
            language=language_of(turn.active_language),
            draft=turn.draft,
        )
        semantic_object_id = f"object_{unresolved_id.removeprefix('supported_')}"
        metadata.update(
            {
                "schema_version": 1,
                "source_turn_id": turn.source_turn_id,
                "semantic_object_id": semantic_object_id,
                "missing_slots": missing,
                "next_field": field_name,
                "evidence_fragments": [segment.exact_source_text],
            }
        )
        completion = _supported_completion(unresolved_id, metadata)
        workflow = workflow.bound_to(completion)
        unresolved = UnresolvedFieldV2(
            unresolved_id=unresolved_id,
            semantic_object_id=semantic_object_id,
            source_turn_id=turn.source_turn_id,
            source_fragment=segment.exact_source_text,
            target_type="condition_creation",
            expected_answer_schema=_supported_answer_schema(clarification, metadata),
            completion_contract=completion,
            missing_slots=_canonical_supported_slots(missing),
            allowed_options=list(clarification.allowed_options),
            question=clarification.question,
            reason=clarification.reason,
            created_workflow_revision=turn.draft.workflow_revision,
        )
        plan = _supported_workflow_plan(
            turn,
            source_text=segment.exact_source_text,
            segment_kind=SegmentKind.STRATEGY_INSTRUCTION,
            operation=AuthorizedPatchOperation(
                operation_id=f"add_{unresolved_id}"[:80],
                authorizing_segment_id="supported_request",
                kind="add_unresolved",
                unresolved=unresolved,
            ),
            summary="Preserve a supported incomplete rule as canonical unresolved state.",
        )
        return await self._execute_supported_workflow_plan(
            turn,
            plan,
            trace=trace,
            usage=usage,
            clarification=clarification,
            intro=localized("ask.intro", language_of(turn.active_language)),
            include_gates=False,
            pending_metadata=metadata,
            workflow=workflow,
        )

    async def _answer_active_question(
        self,
        turn: SetupAgentTurnInput,
        reading: IntentReading,
        language: LanguageDecision,
        *,
        trace: SetupAgentTrace,
    ) -> SetupAgentTurnResult | None:
        """Let the open question have the turn, before anything else reads the message.

        This is the architectural fix. An open question used to be checked *after* the
        planner had already classified the message as something else, so a short,
        misspelled or simply unexpected answer became a fresh general request and got
        "Nothing is set up yet" — while the question it was answering was still open.

        Only three things may take the turn away from an open question, and each is
        explicit: the trader cancels, the trader presses a mode button, or the trader
        types a different complete request. Everything else is an attempt to answer,
        and an attempt the reader cannot use keeps every accepted value exactly as it
        was and asks again.
        """

        if turn.conversation.active_question is None:
            # Nothing on screen — but something may be waiting to be picked back up.
            return self._resume_paused_question(turn, language, trace=trace)
        # A confusion signal is handled by recovery, which repeats this same question.
        if is_confusion_signal(turn.message):
            return None

        # A whole new instruction, complete enough to build on its own, is allowed to
        # take the turn. Anything less is an attempt to answer, however badly typed —
        # that is the line between "changed the subject" and "did not understand".
        fresh = assess_request(
            turn.message,
            offered_capability_keys=None,
            known_symbols=turn.draft.universe.included_symbols,
            known_window=_draft_trigger_timeframe(turn.draft),
        )
        decision = resolve_active_clarification_turn(
            message=turn.message,
            conversation=turn.conversation,
            draft=turn.draft,
            language=language.language,
            # A mode button is the product's own control and always wins.
            mode_selected="mode_selection" in reading.reasons,
            looks_like_new_request=fresh.completeness is RequestCompleteness.COMPLETE,
        )
        if decision is None:
            return None
        effects = decision.effects
        turn.telemetry.notes["active_question_transition"] = decision.transition.value
        turn.telemetry.notes["active_question_outcome"] = decision.outcome.value
        turn.telemetry.notes["active_question_field"] = decision.field_name
        turn.telemetry.notes["active_question_target"] = decision.contract.target_type
        turn.telemetry.notes["active_question_route"] = decision.handler.apply_route.value
        turn.telemetry.notes["active_question_stage"] = decision.stage.value
        if decision.transition is TransitionOutcome.NEW_REQUEST:
            return None
        if decision.transition is TransitionOutcome.REPLACEMENT_REQUIRED:
            return self._hold_new_request(turn, language, decision, trace=trace)
        if decision.ends_the_question:
            settled = await self._end_active_question(turn, language, decision, trace=trace)
            return await self._route_held_request(turn, settled)
        if effects.question_stays_active:
            return self._repeat_active_question(turn, language, decision, trace=trace)
        applied = await self._apply_clarification_answer(
            turn, reading, language, decision, trace=trace
        )
        if applied is None:
            return None
        # The trader chose to finish this requirement instead of abandoning it. If the
        # requirement is now closed, the request they sent earlier is no longer waiting
        # on anything and runs. Holding it forever would lose it as surely as dropping
        # it did, just more quietly.
        if applied.conversation.active_question is None:
            return await self._route_held_request(turn, applied)
        return applied

    def _hold_new_request(
        self,
        turn: SetupAgentTurnInput,
        language: LanguageDecision,
        decision: ClarificationTurn,
        *,
        trace: SetupAgentTrace,
    ) -> SetupAgentTurnResult:
        """Keep the new request, change nothing, and ask which the trader wants.

        A complete new request used to simply win the turn. The old question disappeared
        and its blocker stayed in the draft, so the setup was blocked for a reason
        nothing on screen mentioned any more — and the trader had no way to see it or
        clear it. Nothing is applied here: the new request is stored, the open question
        stays exactly as it was, and the next turn settles one of them explicitly.
        """

        contract = decision.contract
        content = localized(
            decision.effects.response_key,
            language.language,
            question=contract.question,
        )
        fingerprint = response_fingerprint(content)
        turn.telemetry.notes["active_question_transition"] = decision.transition.value
        turn.telemetry.notes["held_new_request"] = True
        conversation = turn.conversation.model_copy(
            update={
                "active_language": turn.active_language,
                "held_request": turn.message[:5000],
                "last_assistant_summary": content[:1000],
                "last_response_fingerprint": fingerprint,
            }
        )
        return SetupAgentTurnResult(
            reply=deterministic_reply(
                content, selected_clarification_id=contract.question_id
            ),
            execution=None,
            draft=turn.draft,
            conversation=conversation,
            plan=None,
            trace=_with(
                trace,
                patch_validation="active_question_replacement_required",
                response_model="deterministic_clarification",
                response_fingerprint=fingerprint,
            ),
            clarification=contract,
            usage={},
        )

    async def _route_held_request(
        self,
        turn: SetupAgentTurnInput,
        settled: SetupAgentTurnResult,
    ) -> SetupAgentTurnResult:
        """Now that the old requirement is settled, run the request that was waiting.

        The order is the whole point. The blocker is removed or visibly paused *first*,
        by an authorized operation, and only then does the held instruction reach normal
        routing — with the settled draft and conversation, so it cannot be built on top
        of state the trader has just abandoned.
        """

        held = (turn.conversation.held_request or "").strip()
        if not held:
            return settled
        conversation = settled.conversation.model_copy(update={"held_request": None})
        turn.telemetry.notes["held_request_routed"] = True
        replayed = await self.run_turn(
            replace(
                turn,
                message=held,
                draft=settled.draft,
                conversation=conversation,
            )
        )
        # The settlement really happened, so its canonical result is kept whenever the
        # replayed request did not itself change the draft. Reporting only the second
        # half would lose the removal the trader just asked for.
        if replayed.execution is None and settled.execution is not None:
            return replace(replayed, execution=settled.execution, draft=settled.draft)
        return replayed

    def _resume_paused_question(
        self,
        turn: SetupAgentTurnInput,
        language: LanguageDecision,
        *,
        trace: SetupAgentTrace,
    ) -> SetupAgentTurnResult | None:
        """Put a paused requirement back on screen when the trader asks to continue.

        Pausing keeps the blocker canonical and the question retrievable. Without a way
        back, "paused" would be indistinguishable from "silently dropped" — which is the
        state this whole policy exists to make impossible.
        """

        paused = turn.conversation.paused_question
        if paused is None or not is_resume_request(turn.message):
            return None
        content = localized("status.setup_resumed", language.language)
        conversation = turn.conversation.resumed().model_copy(
            update={
                "active_language": turn.active_language,
                "last_assistant_summary": f"{content}\n\n{paused.question}"[:1000],
                "last_response_fingerprint": response_fingerprint(
                    f"{content}\n\n{paused.question}"
                ),
            }
        )
        turn.telemetry.notes["active_question_transition"] = "RESUMED"
        return SetupAgentTurnResult(
            reply=deterministic_reply(
                content, selected_clarification_id=paused.question_id
            ),
            execution=None,
            draft=turn.draft,
            conversation=conversation,
            plan=None,
            trace=_with(
                trace,
                patch_validation="active_question_resumed",
                response_model="deterministic_clarification",
                response_fingerprint=response_fingerprint(content),
            ),
            clarification=paused,
            usage={},
        )

    async def _apply_clarification_answer(
        self,
        turn: SetupAgentTurnInput,
        reading: IntentReading,
        language: LanguageDecision,
        decision: ClarificationTurn,
        *,
        trace: SetupAgentTrace,
    ) -> SetupAgentTurnResult | None:
        """Turn one resolved answer into canonical state, through its own authority.

        Reading the answer was the same job for every question. Applying it is not, and
        each route below names who is allowed to do it. ``None`` means "this authority
        is not in this process" — never "nobody understood the message".
        """

        route = decision.handler.apply_route
        if route is ApplyRoute.SUPPORTED_RULE_WORKFLOW:
            if _active_supported_unresolved(turn.draft, turn.conversation) is not None:
                # An answer to our own question never needs a model to notice it is one.
                # The value goes with it: re-reading the message here is what let a
                # confirmed ``yes`` be read a second time as a candle period and thrown
                # away, because the second reader had never heard of the proposal.
                return await self._continue_supported_incomplete(
                    turn, None, trace=trace, usage={}, decision=decision
                )
            # The question was asked before any canonical blocker existed — the first
            # "what should I watch?" of a brand new conversation. The planner builds the
            # rule; it is told below that this turn is the answer, so it cannot read the
            # bare value as a fresh instruction with nothing in it.
            turn.telemetry.notes["active_question_handoff"] = "planner_no_blocker"
            return None
        if route is ApplyRoute.PENDING_SCAN:
            return self._resume_pending_scan(turn, reading, language, decision)
        if route is ApplyRoute.GOVERNED_OPTION:
            # A screened universe or an approved methodology is governed Sharia policy.
            # Only the application's own allowlisted option route may set one, so a
            # *resolved* choice is handed straight to it — chat text never applies it.
            # The hand-off is a canonical value plus the control named by this question's
            # own stored continuation, so it is still a zero-model turn.
            turn.telemetry.notes["active_question_handoff"] = "governed_option_route"
            selection = governed_option_selection(
                decision.contract.continuation, decision.canonical_value
            )
            if selection is not None:
                turn.telemetry.notes["governed_option_selection"] = {
                    "option_key": selection[0],
                    "option_value": selection[1],
                }
            return None
        return await self._apply_stored_continuation(turn, language, decision, trace=trace)

    async def _close_non_mutating_question(
        self,
        turn: SetupAgentTurnInput,
        language: LanguageDecision,
        decision: ClarificationTurn,
        *,
        trace: SetupAgentTrace,
    ) -> SetupAgentTurnResult:
        """Close a question that never promised to change anything.

        "Did I read that right?" is a real question with a real answer, and closing it is
        a real event a later reply may cite — so it goes through the canonical tool and
        is recorded in ``answered_questions``. It carries no operations, because there is
        nothing to change, and it costs no model call, because there is nothing to read.
        """

        contract = decision.contract
        segment_text = turn.message.strip() or contract.question
        plan = SetupAgentTurnPlan(
            source_turn_id=turn.source_turn_id,
            segments=[
                TurnSegment(
                    segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
                    exact_source_text=segment_text,
                    start_offset=0,
                    end_offset=len(segment_text),
                    kind=SegmentKind.CLARIFICATION_ANSWER,
                    action_required=False,
                    confidence=1.0,
                )
            ],
            operations=[],
            clarification_answers=[
                ClarificationAnswer(
                    segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
                    question_id=contract.question_id,
                    answer_text=segment_text,
                )
            ],
            overall_confidence=1.0,
        )
        return await self._execute_supported_workflow_plan(
            turn,
            plan,
            trace=_with(trace, patch_validation="non_mutating_question_closed"),
            usage={},
            clarification=None,
            intro=localized("status.question_answered", language.language),
            include_gates=False,
            pending_metadata={},
            attach_next_question=False,
        )

    def _refuse_continuation(
        self,
        turn: SetupAgentTurnInput,
        language: LanguageDecision,
        decision: ClarificationTurn,
        refused: ContinuationRefused,
        *,
        trace: SetupAgentTrace,
    ) -> SetupAgentTurnResult:
        """Say honestly that the answer could not be applied, and leave nothing hidden.

        Three things can refuse here, and none of them means "answered":

        * the stored question has no completion at all — legacy state, or a producer that
          bypassed the invariant. It is **paused**, not cleared: the blocker is still real
          and the trader must be able to come back to it;
        * the draft moved after the question was asked, so the answer belongs to a state
          that no longer exists;
        * the value is not one this step said it could run.

        The last two keep the question on screen and ask again. Reusing the *resolved*
        wording here would tell the trader their answer landed when nothing happened —
        which is the exact class of false claim this codebase refuses to make.
        """

        turn.telemetry.notes["continuation_refused"] = refused.code
        if refused.code == "CONTINUATION_MISSING":
            turn.telemetry.notes["orphan_question_paused"] = decision.contract.question_id
            content = localized("status.setup_paused", language.language)
            return self._closed_question_result(
                turn,
                content,
                trace=_with(trace, patch_validation="continuation_missing_paused"),
                patch_validation="continuation_missing_paused",
                pause=True,
            )
        content = localized("ask.stale_answer", language.language)
        contract = decision.contract
        final = f"{content}\n\n{contract.question}"
        fingerprint = response_fingerprint(final)
        return SetupAgentTurnResult(
            reply=deterministic_reply(
                content, selected_clarification_id=contract.question_id
            ),
            execution=None,
            draft=turn.draft,
            conversation=turn.conversation.model_copy(
                update={
                    "active_language": turn.active_language,
                    "last_assistant_summary": final[:1000],
                    "last_response_fingerprint": fingerprint,
                }
            ),
            plan=None,
            trace=_with(
                trace,
                patch_validation=f"continuation_refused_{refused.code.casefold()}",
                response_model="deterministic_clarification",
                response_fingerprint=fingerprint,
            ),
            clarification=contract,
            usage={},
        )

    async def _apply_stored_continuation(
        self,
        turn: SetupAgentTurnInput,
        language: LanguageDecision,
        decision: ClarificationTurn,
        *,
        trace: SetupAgentTrace,
    ) -> SetupAgentTurnResult | None:
        """Fill the one typed hole the question promised, and apply it. No model call.

        Everything needed was decided when the question was created: which object the
        answer lands on, which authorized operation carries it, and what the draft looked
        like at the time. Filling a hole in that record needs no language understanding,
        so this is the route that replaced "hand the answer to the planner".

        A refusal here is honest and visible: the draft moved under the question, or the
        value is not one this step said it could run. Both keep the question open and say
        so, rather than passing an answer to a reader that never saw the question.
        """

        continuation = decision.contract.continuation
        proposal_evidence = turn.conversation.active_proposal[1]
        if continuation is None and not decision.contract.mutating:
            # A question that promised to change nothing has nothing to apply, so there
            # is nothing missing. It is closed and recorded as answered — the canonical
            # evidence a reply may cite — without any operation and without a model call.
            return await self._close_non_mutating_question(turn, language, decision, trace=trace)
        answer = ContinuationAnswer(
            canonical_value=cast(Any, decision.canonical_value),
            evidence=turn.message,
            proposal_evidence=(
                proposal_evidence if decision.stage is AnswerStage.CONFIRMATION else ""
            ),
        )
        try:
            operations = build_continuation_operations(
                continuation, answer, draft=turn.draft
            )
        except ContinuationRefused as refused:
            return self._refuse_continuation(turn, language, decision, refused, trace=trace)
        if not operations:
            # A registered kind whose canonical write belongs to another authority. The
            # caller above routes those; reaching here means nothing to apply.
            turn.telemetry.notes["active_question_handoff"] = "delegated_continuation"
            return None
        turn.telemetry.notes["active_question_handoff"] = "deterministic_continuation"
        # The segment quotes what the trader typed *this* turn, because the server
        # locates every span in the real message. The earlier words that produced a
        # confirmed near miss travel separately, in the confirmation proof below, which
        # is the one channel allowed to say "this value came from an earlier turn".
        plan = _supported_workflow_plan(
            turn,
            source_text=turn.message.strip() or decision.contract.question,
            segment_kind=SegmentKind.CLARIFICATION_ANSWER,
            operation=list(operations),
            summary="Apply one clarification answer through its stored continuation.",
            question_id=decision.contract.question_id,
        )
        return await self._execute_supported_workflow_plan(
            turn,
            plan,
            trace=_with(trace, patch_validation="clarification_continuation_applied"),
            usage={},
            clarification=None,
            intro=localized("status.question_answered", language.language),
            include_gates=True,
            pending_metadata={},
            confirmed_answer=(
                ConfirmedAnswer(
                    field_name=decision.field_name,
                    canonical_value=str(decision.canonical_value),
                    proposal_evidence=answer.proposal_evidence,
                )
                if answer.proposal_evidence
                else None
            ),
            # Reconciliation re-checks grounding for itself, and a confirmation turn's
            # words are only "yes". This is the same server-owned channel the option
            # controls use to say "a person really chose this", and it is opened for one
            # path only: the exact requirement this confirmed answer just filled.
            confirmed_paths=(
                _confirmed_requirement_paths(turn.draft, continuation)
                if decision.stage is AnswerStage.CONFIRMATION
                else frozenset()
            ),
        )

    def _resume_pending_scan(
        self,
        turn: SetupAgentTurnInput,
        reading: IntentReading,
        language: LanguageDecision,
        decision: ClarificationTurn,
    ) -> SetupAgentTurnResult | None:
        """Fold one answer into the live scan that asked for it, and carry on.

        The scan collector reads its values out of ``pending_read_only_scan``, so the
        answer is written there and the same collector continues. It is the one place
        that knows what a scan still needs; re-deciding that here would be the second
        implementation this codebase keeps having to remove.
        """

        merged = dict(turn.pending_scan)
        # Only the fields a scan actually has. Writing whatever the question named would
        # let one clarification put a key into the scan request that the governed
        # Scanner has no argument for.
        scan_field = _SCAN_FIELD_BY_DOMAIN.get(decision.domain)
        if scan_field is not None:
            merged[scan_field] = decision.canonical_value
        if not merged:
            return None
        return self._route_read_only_scan(
            turn,
            replace(reading, slots=ScanSlots()),
            language,
            merged,
        )

    async def _end_active_question(
        self,
        turn: SetupAgentTurnInput,
        language: LanguageDecision,
        decision: ClarificationTurn,
        *,
        trace: SetupAgentTrace,
    ) -> SetupAgentTurnResult:
        """Stop asking — and do to the canonical state exactly what the policy says.

        Clearing the question alone used to be the whole of cancellation. The blocker
        that caused it stayed in the draft, so the setup was still blocked for a reason
        nothing on screen mentioned any more, and the trader had no way to see it or
        clear it. Each of the three outcomes below leaves a state a trader can act on:

        * the requirement is really gone, removed by an authorized operation;
        * it is put down and says so, and can be picked up again;
        * there was nothing canonical behind it, and nothing pretends otherwise.
        """

        turn.telemetry.notes["cancellation_policy"] = decision.cancellation.value
        blocker = decision.blocker
        if decision.transition is TransitionOutcome.PAUSED and blocker is not None:
            # Put down, never thrown away. The blocker stays canonical, the reply says
            # so, and the question is stored whole so "continue" brings it back with
            # every earlier choice intact.
            content = localized("status.setup_paused", language.language)
            return self._closed_question_result(
                turn,
                content,
                trace=trace,
                patch_validation="active_question_paused",
                pause=True,
            )
        if (
            decision.transition is TransitionOutcome.CANCELLED
            and decision.cancellation is CancellationPolicy.REMOVE_PENDING_REQUIREMENT
            and blocker is not None
        ):
            plan = _supported_workflow_plan(
                turn,
                source_text=turn.message,
                segment_kind=SegmentKind.CLARIFICATION_ANSWER,
                operation=AuthorizedPatchOperation(
                    operation_id=f"cancel_{blocker.key}"[:80],
                    authorizing_segment_id="cancel_request",
                    kind=cast(Any, blocker.operation_kind),
                    target_key=blocker.key,
                ),
                summary="Remove the unfinished requirement the trader cancelled.",
            )
            return await self._execute_supported_workflow_plan(
                turn,
                plan,
                trace=_with(trace, patch_validation="active_question_cancelled"),
                usage={},
                clarification=None,
                intro=localized("status.rule_cancelled", language.language),
                include_gates=False,
                pending_metadata={},
                attach_next_question=False,
                cancelled_requirement_keys=frozenset({blocker.key}),
            )
        # Nothing canonical behind it. A read-only Scanner ask, or a question raised
        # before any blocker was written — dropping it changes no draft.
        content = localized(
            "status.scan_cancelled"
            if decision.handler.apply_route is ApplyRoute.PENDING_SCAN
            else "status.question_cancelled",
            language.language,
        )
        return self._closed_question_result(
            turn,
            content,
            trace=trace,
            patch_validation="active_question_cancelled",
            clear_pending_scan=True,
        )

    def _closed_question_result(
        self,
        turn: SetupAgentTurnInput,
        content: str,
        *,
        trace: SetupAgentTrace,
        patch_validation: str,
        clear_pending_scan: bool = False,
        pause: bool = False,
    ) -> SetupAgentTurnResult:
        """One question taken off screen, and nothing canonical left inconsistent."""

        fingerprint = response_fingerprint(content)
        updates: dict[str, object] = {
            "active_language": turn.active_language,
            "active_goal": None,
            "last_response_fingerprint": fingerprint,
            "last_assistant_summary": content[:1000],
        }
        if not pause:
            # A paused workflow keeps its collected values; a cancelled one does not.
            updates["pending_supported_request"] = {}
        if clear_pending_scan:
            updates["pending_read_only_scan"] = {}
        closed = turn.conversation.paused() if pause else turn.conversation.cleared_question()
        return SetupAgentTurnResult(
            reply=deterministic_reply(content),
            execution=None,
            draft=turn.draft,
            conversation=closed.model_copy(update=updates),
            plan=None,
            trace=_with(
                trace,
                patch_validation=patch_validation,
                response_model="deterministic_clarification",
                response_fingerprint=fingerprint,
            ),
            usage={},
        )

    def _repeat_active_question(
        self,
        turn: SetupAgentTurnInput,
        language: LanguageDecision,
        decision: ClarificationTurn,
        *,
        trace: SetupAgentTrace,
    ) -> SetupAgentTurnResult:
        """Say precisely what went wrong, then ask the right question again.

        Nothing is cleared and nothing is reset. The workflow, the accepted values and
        the question all survive an answer this reader could not use — that is the
        whole difference between "I did not understand" and "let us start again".

        "The right question" is not always the original one. After a near miss the
        trader is looking at *did you mean 1h?*, so an unclear reply gets that same
        yes/no back rather than the list of periods they already failed to hit.
        """

        contract = decision.contract
        labels = list(decision.display_labels) or list(contract.allowed_options)
        options = ", ".join(labels) if labels else contract.expected_answer_schema
        confirming = decision.is_confirmation
        # The wording comes from the transition's own row in the effects table, so a
        # reply can never describe a different state change from the one that happened.
        key = decision.effects.response_key
        if confirming and decision.stage is AnswerStage.CONFIRMATION:
            # Already asked once and still not a yes or a no. Ask it more plainly.
            key = "ask.confirm_yes_no"
        if confirming:
            content = localized(
                key, language.language, candidate=str(decision.canonical_value)
            )
        else:
            content = localized(key, language.language, options=options)
        # A yes/no is a whole question on its own. Appending the original list under it
        # asks two different things at once, which is what a beginner answers wrongly.
        attached = None if confirming else contract
        final = f"{content}\n\n{contract.question}" if attached is not None else content
        fingerprint = response_fingerprint(final)
        # The words that produced the proposal are kept with it. They are the evidence
        # for the value: a later "yes" contains no period, and a rule may not hold a
        # value that appears in no message the trader wrote.
        #
        # ``holding`` puts them in the one home that applies — the workflow's when there
        # is a workflow, the conversation's when there is not. A one-question ask used to
        # have nowhere to keep a near miss, so the trader was asked "did you mean
        # binance?", said yes, and the yes was read against the original list and refused.
        evidence = (
            turn.message
            if decision.stage is AnswerStage.ANSWER
            else turn.conversation.active_proposal[1]
        )
        conversation = turn.conversation.holding(
            decision.proposed_value, evidence[:500]
        ).model_copy(
            update={
                "active_language": turn.active_language,
                "active_goal": turn.conversation.active_goal or "complete_supported_setup",
                "last_response_fingerprint": fingerprint,
                "last_assistant_summary": final[:1000],
            }
        )
        return SetupAgentTurnResult(
            reply=deterministic_reply(content, selected_clarification_id=contract.question_id),
            execution=None,
            draft=turn.draft,
            conversation=conversation,
            plan=None,
            trace=_with(
                trace,
                patch_validation=f"active_question_{decision.transition.value.casefold()}",
                response_model="deterministic_clarification",
                response_fingerprint=fingerprint,
            ),
            clarification=attached,
            usage={},
        )

    async def _continue_supported_incomplete(
        self,
        turn: SetupAgentTurnInput,
        envelope: PlannerIntentEnvelope | None,
        *,
        trace: SetupAgentTrace,
        usage: dict[str, Any],
        decision: ClarificationTurn | None = None,
    ) -> SetupAgentTurnResult | None:
        """Fold one answer into the rule being built.

        ``envelope`` is ``None`` when this runs before the planner, which is the normal
        case: an open question owns its turn and does not need a model to notice that
        the message answers it.

        ``decision`` is the reading the dispatcher already made. It is passed in rather
        than re-derived because a second reading of the same message is how this
        codebase's oldest defect keeps coming back: the dispatcher knew ``yes`` meant
        *the 1h you proposed*, and a reader here that had never heard of the proposal
        read the same word as a candle period, found none, and dropped the answer.
        """

        unresolved = _active_supported_unresolved(turn.draft, turn.conversation)
        if unresolved is None:
            return None
        if is_confusion_signal(turn.message):
            content = localized("confusion.unclear_retry", language_of(turn.active_language),
                question=unresolved.question,
            )
            fingerprint = response_fingerprint(content)
            conversation = turn.conversation.model_copy(
                update={
                    "active_language": turn.active_language,
                    "active_goal": "complete_supported_setup",
                    "confusion_recovery_count": (
                        turn.conversation.confusion_recovery_count + 1
                    ),
                    "last_response_fingerprint": fingerprint,
                }
            )
            return SetupAgentTurnResult(
                reply=deterministic_reply(content),
                execution=None,
                draft=turn.draft,
                conversation=conversation,
                plan=None,
                trace=_with(
                    trace,
                    patch_validation="supported_confusion_recovery",
                    response_model="deterministic_clarification",
                    response_fingerprint=fingerprint,
                ),
                usage=usage,
            )
        metadata = _supported_metadata(unresolved)
        stored = turn.conversation.pending_workflow
        contract = turn.conversation.active_question
        if decision is not None:
            field_name = decision.field_name
            resolution = AnswerResolution(
                decision.outcome,
                canonical_value=decision.canonical_value,
                candidates=decision.candidates,
                reason=decision.reason,
                stage=decision.stage,
            )
        else:
            field_name = (
                stored.current_field
                if stored is not None
                else str(metadata.get("next_field") or "")
            )
            resolution = _read_answer(
                field_name,
                turn.message,
                language=language_of(turn.active_language),
                allowed=list(contract.canonical_values) if contract is not None else [],
                offered=list(stored.offered_values) if stored is not None else [],
                proposed=stored.proposed_value if stored is not None else None,
            )
        if not resolution.stores_a_value:
            # Every other outcome was already answered before the planner ran. Reaching
            # here means the planner claimed an answer the resolver cannot use, and the
            # resolver is the authority — the question simply stays open.
            if envelope is not None and not _planner_answered_active_question(turn, envelope):
                return None
            content = localized(
                "ask.retry_invalid",
                language_of(turn.active_language),
                question=unresolved.question,
            )
            fingerprint = response_fingerprint(content)
            return SetupAgentTurnResult(
                reply=deterministic_reply(content),
                execution=None,
                draft=turn.draft,
                conversation=turn.conversation.model_copy(
                    update={
                        "active_language": turn.active_language,
                        "last_response_fingerprint": fingerprint,
                    }
                ),
                plan=None,
                trace=_with(
                    trace,
                    patch_validation="supported_answer_invalid",
                    response_model="deterministic_clarification",
                    response_fingerprint=fingerprint,
                ),
                usage=usage,
            )
        value = cast(object, resolution.canonical_value)

        _apply_supported_answer(metadata, field_name, value)
        missing = [
            item
            for item in cast(list[str], metadata.get("missing_slots") or [])
            if item != field_name
        ]
        metadata["missing_slots"] = missing
        segment_text = _answer_segment_text(turn, envelope)
        # A confirmed near miss was written across two turns: the words the value was
        # read from, and the yes. Both are recorded, so the rule's evidence really
        # contains the value it holds — "yes" on its own names no candle period.
        confirmed = (
            ConfirmedAnswer(
                field_name=field_name,
                canonical_value=str(value),
                proposal_evidence=stored.proposed_evidence,
            )
            if resolution.stage is AnswerStage.CONFIRMATION
            and stored is not None
            and stored.proposed_evidence
            else None
        )
        evidence_fragments = [
            str(item)
            for item in cast(list[object], metadata.get("evidence_fragments") or [])
            if str(item).strip()
        ]
        if confirmed is not None:
            evidence_fragments.append(confirmed.proposal_evidence)
        evidence_fragments.append(segment_text)
        metadata["evidence_fragments"] = evidence_fragments
        if missing:
            next_field = _next_supported_field(missing)
            metadata["next_field"] = next_field
            # One atomic step: accept this answer, advance the record, and derive the
            # next question *from the advanced record*. The question the trader is about
            # to see and the state that will validate their reply are now the same
            # object, with a question id unique to this step.
            advanced = (stored or _workflow_from_metadata(unresolved, metadata)).model_copy(
                update={"current_field": field_name, "remaining_fields": missing}
            )
            advanced, clarification = _workflow_step(
                advanced.accepting(
                    cast(str | int | float | bool | None, value), evidence=segment_text
                ).model_copy(update={"current_field": next_field}),
                language=language_of(turn.active_language),
                draft=turn.draft,
            )
            updated = unresolved.model_copy(
                update={
                    "expected_answer_schema": _supported_answer_schema(
                        clarification, metadata
                    ),
                    "completion_contract": _supported_completion(
                        unresolved.unresolved_id, metadata
                    ),
                    "missing_slots": _canonical_supported_slots(missing),
                    "allowed_options": list(clarification.allowed_options),
                    "question": clarification.question,
                    "reason": clarification.reason,
                }
            )
            # Stamp the projection with the canonical record it now describes. Without
            # this the conversation held its own copy of the progress with nothing tying
            # the two together, so a divergence had no way of being noticed — it just
            # quietly validated the next answer against the older of the two.
            advanced = advanced.bound_to(updated.completion_contract)
            operation = AuthorizedPatchOperation(
                operation_id=f"update_{unresolved.unresolved_id}_{turn.source_turn_id}"[:80],
                authorizing_segment_id="supported_answer",
                kind="update_unresolved",
                target_key=unresolved.unresolved_id,
                unresolved=updated,
            )
            plan = _supported_workflow_plan(
                turn,
                source_text=segment_text,
                segment_kind=SegmentKind.CLARIFICATION_ANSWER,
                operation=operation,
                summary="Store one grounded answer for the pending supported rule.",
                question_id=unresolved.unresolved_id,
            )
            return await self._execute_supported_workflow_plan(
                turn,
                plan,
                trace=trace,
                usage=usage,
                clarification=clarification,
                intro=localized("status.question_answered", language_of(turn.active_language)),
                include_gates=False,
                pending_metadata=metadata,
                workflow=advanced,
                confirmed_answer=confirmed,
            )

        if not _supported_rule_is_assemblable(metadata):
            # Every value the trader was asked for is in, and the rule still cannot be
            # built: the requirement was recorded without the facts the assembler needs.
            # Fail closed. The blocker stays, the trader is told plainly, and nothing is
            # invented to fill the gap — a rule built from a guess would monitor the
            # wrong market silently.
            turn.telemetry.notes["supported_rule_not_assemblable"] = sorted(
                item for item in _REQUIRED_RULE_FACTS if metadata.get(item) is None
            )
            content = localized("status.rule_not_buildable", language_of(turn.active_language))
            fingerprint = response_fingerprint(content)
            return SetupAgentTurnResult(
                reply=deterministic_reply(content),
                execution=None,
                draft=turn.draft,
                conversation=turn.conversation.cleared_question().model_copy(
                    update={
                        "active_language": turn.active_language,
                        "last_assistant_summary": content[:1000],
                        "last_response_fingerprint": fingerprint,
                    }
                ),
                plan=None,
                trace=_with(
                    trace,
                    patch_validation="supported_rule_not_assemblable",
                    response_model="deterministic_clarification",
                    response_fingerprint=fingerprint,
                ),
                usage=usage,
            )
        node = _condition_from_supported_metadata(
            metadata,
            source_turn_id=turn.source_turn_id,
            source_fragment="\n".join(
                str(item)
                for item in cast(
                    list[object], metadata.get("evidence_fragments") or [segment_text]
                )
            ),
        )
        add_operation = AuthorizedPatchOperation(
            operation_id=f"complete_{unresolved.unresolved_id}"[:80],
            authorizing_segment_id="supported_answer",
            kind="add_condition",
            condition=node,
        )
        resolve_operation = AuthorizedPatchOperation(
            operation_id=f"resolve_{unresolved.unresolved_id}"[:80],
            authorizing_segment_id="supported_answer",
            kind="resolve_unresolved_key",
            target_key=unresolved.unresolved_id,
        )
        plan = _supported_workflow_plan(
            turn,
            source_text=segment_text,
            segment_kind=SegmentKind.CLARIFICATION_ANSWER,
            operation=[add_operation, resolve_operation],
            summary="Complete the previously grounded supported rule.",
            question_id=unresolved.unresolved_id,
        )
        return await self._execute_supported_workflow_plan(
            turn,
            plan,
            trace=trace,
            usage=usage,
            clarification=None,
            intro=localized("status.rule_completed", language_of(turn.active_language)),
            include_gates=True,
            pending_metadata={},
            confirmed_answer=confirmed,
        )

    async def _execute_supported_workflow_plan(
        self,
        turn: SetupAgentTurnInput,
        plan: SetupAgentTurnPlan,
        *,
        trace: SetupAgentTrace,
        usage: dict[str, Any],
        clarification: ClarificationContract | None,
        intro: str,
        include_gates: bool,
        pending_metadata: dict[str, object],
        workflow: PendingClarificationWorkflow | None = None,
        attach_next_question: bool = True,
        cancelled_requirement_keys: frozenset[str] = frozenset(),
        confirmed_answer: ConfirmedAnswer | None = None,
        confirmed_paths: frozenset[str] = frozenset(),
    ) -> SetupAgentTurnResult:
        if turn.stage_callback is not None:
            await turn.stage_callback(
                "EXECUTING",
                {"planner_model": trace.planner_model, "plan": plan.model_dump(mode="json")},
            )
        try:
            outcome = await apply_setup_turn(
                _turn_request(
                    turn,
                    plan,
                    frozenset(),
                    include_gates=include_gates,
                    cancelled_requirement_keys=cancelled_requirement_keys,
                    confirmed_answer=confirmed_answer,
                    confirmed_paths=confirmed_paths,
                )
            )
        except SetupTurnRejected as exc:
            raise SetupAgentError(
                exc.code,
                str(exc),
                stage="tool_validation",
                details=exc.details,
                usage=usage,
            ) from exc
        if turn.stage_callback is not None:
            await turn.stage_callback(
                "COMPOSING",
                {
                    "planner_model": trace.planner_model,
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
        selected = clarification
        if selected is None and attach_next_question and outcome.result.allowed_clarifications:
            # A cancellation must not answer itself with the next open item. The trader
            # asked to stop; opening a different question in the same breath is how one
            # cancelled workflow started another one nobody chose.
            selected = outcome.result.allowed_clarifications[0]
        conversation = outcome.conversation.model_copy(
            update={
                "active_language": turn.active_language,
                "active_goal": (
                    "complete_supported_setup" if selected is not None else None
                ),
                "pending_supported_request": pending_metadata,
            }
        )
        if selected is not None:
            # The workflow is attached only when it really describes this question.
            # Attaching a mismatched one would trip the conversation's own invariant,
            # which is exactly what it is there to catch.
            paired = workflow if workflow is not None and workflow.matches(selected) else None
            conversation = conversation.with_question(selected, workflow=paired)
        else:
            conversation = conversation.cleared_question()
        final = f"{intro}\n\n{selected.question}" if selected is not None else intro
        fingerprint = response_fingerprint(final)
        conversation = conversation.model_copy(
            update={
                "last_assistant_summary": final[:1000],
                "last_response_fingerprint": fingerprint,
            }
        )
        return SetupAgentTurnResult(
            reply=deterministic_reply(
                intro,
                selected_clarification_id=(selected.question_id if selected else None),
            ),
            execution=outcome.result,
            draft=outcome.draft,
            conversation=conversation,
            plan=plan,
            trace=_with(
                trace,
                tool_called=True,
                patch_validation="supported_incomplete_canonical",
                semantic_diff=tuple(outcome.result.semantic_diff),
                compile_status=outcome.result.compile_status,
                response_model="deterministic_clarification",
                response_fingerprint=fingerprint,
            ),
            definition=outcome.definition,
            history_snapshot=outcome.history_snapshot,
            clarification=selected,
            material_change=outcome.material_change,
            usage=usage,
        )

    async def _settled_plan(
        self,
        turn: SetupAgentTurnInput,
        shortlist: CapabilityShortlist,
        route: Any,
        envelope: PlannerIntentEnvelope,
        plan_usage: dict[str, Any],
        *,
        repair_state: _PlannerRepairState,
    ) -> (
        tuple[SetupAgentTurnPlan, PlannerIntentEnvelope, dict[str, Any], bool]
        | _ServerClarification
    ):
        """One reading, checked; and at most one narrow correction for the whole turn.

        Two things can go wrong after the model answers: the server cannot turn the
        reading into a canonical change, or it can but the authorization gates refuse it.
        Both used to have their own repair path, which is how a turn could pay for two
        corrections and still fail. They share one here, and it runs once.

        The repair call is skipped entirely when it cannot help: an unrepairable class,
        a turn that already repaired, or a failure this chat has already seen fail after
        a repair. Spending a paid call on a known-hopeless retry is the loop this closes.
        """

        with turn.telemetry.stage("intent_normalization"):
            semantic_count_before = len(envelope.semantic_intents)
            envelope = normalize_planner_envelope(envelope)
            turn.telemetry.notes["deduplicated_semantic_intent_count"] = (
                semantic_count_before - len(envelope.semantic_intents)
            )
        failure = self._checked_plan(turn, shortlist, envelope)
        if not isinstance(failure, _PlanFailure):
            return failure, envelope, plan_usage, False

        code, details = failure.code, failure.details
        turn.telemetry.notes["turn_failure_class"] = failure.failure_class.value
        turn.telemetry.notes["turn_failure_owner"] = owner_for(failure.failure_class).value
        if failure.outcome == SemanticIntentOutcome.USER_INFORMATION_REQUIRED:
            return _ServerClarification(
                contract=_clarification_for_failure(turn, failure),
                envelope=envelope,
                usage=plan_usage,
            )

        # Decide, deterministically and before spending anything, whether a correction
        # is possible at all. Runs 9-11 attempted 18 corrections and recovered none;
        # every one of those calls was decidable as hopeless from facts already in hand.
        fingerprint = failure_fingerprint(
            canonical_draft_hash=turn.draft.executable_hash or "",
            normalized_user_intent_hash=_normalized_intent_hash(turn.normalized_message),
            failure_class=failure.failure_class,
            failure_paths=failure.paths,
        )
        plan_decision = decide_repair(
            failure.failure_class,
            intent_parsed=True,
            target_paths=failure.paths,
            intent_ref=failure.intent_ref,
            segment_ref=failure.segment_ref,
            source_verified=_segment_is_in_message(envelope, failure.segment_ref, turn.message),
            replacement_is_groundable=bool(failure.paths),
            seconds_remaining=(
                turn.deadline.remaining_seconds
                if turn.deadline.budget_seconds > 0
                else _UNBOUNDED_TURN_SECONDS
            ),
            budget_remaining_usd=max(
                0.0,
                self.settings.setup_agent_max_estimated_cost_usd_per_turn
                - float(plan_usage.get("_setup_reserved_cost_usd") or 0.0),
            ),
            # Every fingerprint this chat has already spent a correction on. Paying
            # again for a problem that already survived one correction is the loop that
            # turned 18 attempts into 0 recoveries.
            attempted_fingerprints=turn.repeats.attempted_fingerprints,
            fingerprint=fingerprint,
            repair_already_used=repair_state is not _PlannerRepairState.UNUSED,
        )
        turn.telemetry.notes.update(plan_decision.to_dict())
        turn.telemetry.notes.update(turn.repeats.to_dict())
        turn.telemetry.notes["turn_failure_fingerprint"] = fingerprint
        record = _failure_record(
            turn,
            envelope,
            failure,
            fingerprint=fingerprint,
            repair_decision=plan_decision.decision.value,
            repair_eligible=plan_decision.spends_model_call,
        )
        turn.telemetry.notes["turn_failure_record"] = record.to_dict()
        stored = turn.telemetry.notes.get("validated_intent_snapshot")
        if isinstance(stored, dict):
            # Same snapshot, now carrying why this turn failed and where. The next turn
            # reads it to see that this exact problem was already tried.
            turn.telemetry.notes["validated_intent_snapshot"] = {
                **stored,
                "failure_class": failure.failure_class.value,
                "failure_paths": list(failure.paths),
                "failure_fingerprint": fingerprint,
            }
        if not plan_decision.spends_model_call:
            raise SetupAgentError(
                code,
                _loop_aware_refusal(code, turn.repeats, failure),
                stage="tool_validation",
                details=details,
                usage=plan_usage,
                failure_record=record,
            )
        repair_state = _PlannerRepairState.SEMANTIC_REPAIR
        turn.telemetry.notes["planner_repair_mode"] = repair_state.value
        turn.telemetry.notes["planner_repair_attempt_count"] = (
            int(turn.telemetry.notes.get("planner_repair_attempt_count") or 0) + 1
        )
        topology_repair = plan_decision.decision is RepairDecision.BOOLEAN_TOPOLOGY_REPAIR
        try:
            correction, repair_usage = await self._repair_once(
                turn,
                shortlist,
                route,
                envelope=envelope,
                validation_code=code,
                validation_details=details,
                invalid_intent_ref=failure.intent_ref,
                target_path=failure.target_path,
                target_paths=failure.paths,
                source_segment_ref=failure.segment_ref,
                prior_usage=plan_usage,
                topology_only=topology_repair,
            )
        except StructuredCallError as repair_exc:
            raise SetupAgentError(
                repair_exc.code,
                str(repair_exc),
                stage="planner_repair",
                retryable=repair_exc.retryable,
                details=repair_exc.details,
                usage={**repair_exc.usage, **route.usage_metadata()},
                failure_record=record,
            ) from None
        repair_usage = {**repair_usage, **route.usage_metadata()}

        before = intent_fingerprint(envelope)
        with turn.telemetry.stage("repair_delta_application"):
            try:
                if isinstance(correction, BooleanTopologyRepair):
                    repaired_envelope = (
                        envelope
                        if correction.cannot_repair
                        else apply_topology_repair(
                            envelope,
                            correction,
                            invalid_intent_ref=failure.intent_ref or "",
                        )
                    )
                else:
                    repaired_envelope = apply_repair_deltas(
                        envelope,
                        correction.deltas,
                        message=turn.message,
                        validation_code=code,
                        invalid_intent_ref=failure.intent_ref or "",
                        invalid_target_path=failure.target_path,
                        invalid_target_paths=failure.paths,
                        references=_planner_references(turn),
                    )
            except IntentCompileError as exc:
                raise SetupAgentError(
                    exc.code,
                    _refusal_message(exc.code),
                    stage="planner_repair",
                    details=exc.details,
                    usage=repair_usage,
                    failure_record=record,
                ) from None
        with turn.telemetry.stage("intent_normalization"):
            repaired_envelope = normalize_planner_envelope(repaired_envelope)
        if correction.cannot_repair or intent_fingerprint(repaired_envelope) == before:
            # Nothing changed, so re-running every gate would reach the same answer at
            # the same cost. Report the original problem instead of hiding it behind a
            # second identical failure.
            turn.telemetry.notes["planner_repair_result"] = "no_change"
            raise SetupAgentError(
                code,
                _loop_aware_refusal(code, turn.repeats, failure),
                stage="planner_repair",
                details=details,
                usage=repair_usage,
                failure_record=record,
            )

        # Verify the correction: the whole path runs again — semantic validation,
        # canonical compilation, grounding, dry validation. A correction is successful
        # only when the original failure is gone, not when a delta was merely returned.
        second = self._checked_plan(turn, shortlist, repaired_envelope, recompiling=True)
        if isinstance(second, _PlanFailure):
            turn.telemetry.notes["planner_repair_result"] = (
                "same_failure" if second.code == code else f"new_failure:{second.code}"
            )
            if second.outcome == SemanticIntentOutcome.USER_INFORMATION_REQUIRED:
                return _ServerClarification(
                    contract=_clarification_for_failure(turn, second),
                    envelope=repaired_envelope,
                    usage=repair_usage,
                )
            raise SetupAgentError(
                second.code,
                _loop_aware_refusal(second.code, turn.repeats, second),
                stage="planner_repair",
                details=second.details,
                usage=repair_usage,
                failure_record=_failure_record(
                    turn,
                    repaired_envelope,
                    second,
                    fingerprint=fingerprint,
                    repair_decision=plan_decision.decision.value,
                    repair_eligible=False,
                ),
            )
        turn.telemetry.notes["planner_repair_result"] = "applied"
        turn.telemetry.notes["planner_repair_success_count"] = (
            int(turn.telemetry.notes.get("planner_repair_success_count") or 0) + 1
        )
        repair_usage["_setup_repair_successes"] = 1
        return second, repaired_envelope, repair_usage, True

    def _checked_plan(
        self,
        turn: SetupAgentTurnInput,
        shortlist: CapabilityShortlist,
        envelope: PlannerIntentEnvelope,
        *,
        recompiling: bool = False,
    ) -> SetupAgentTurnPlan | _PlanFailure:
        """Compile the reading and dry-run it, or return the typed reason it failed."""

        telemetry = turn.telemetry
        try:
            with telemetry.stage("intent_validation"):
                envelope = PlannerIntentEnvelope.model_validate(envelope.model_dump(mode="json"))
            references = _planner_references(turn)
            with telemetry.stage(
                "semantic_recompilation" if recompiling else "semantic_compilation"
            ):
                compilation = compile_planner_intents(
                    envelope,
                    draft=turn.draft,
                    message=turn.message,
                    source_turn_id=turn.source_turn_id,
                    shortlist=shortlist,
                    history=list(turn.history),
                    references=references,
                )
        except IntentCompileError as exc:
            failure = _classify_plan_failure(
                code=exc.code,
                details=exc.details,
                envelope=envelope,
                message=turn.message,
                declared_outcome=exc.outcome,
                intent_ref=exc.intent_ref,
                target_path=exc.target_path,
                # Every field the compiler named, not only the first. Forwarding one
                # path made a two-omission turn spend its single correction on one
                # field and then fail identically on the other.
                target_paths=exc.target_paths,
                segment_ref=exc.segment_ref,
            )
            if failure.outcome == SemanticIntentOutcome.COMPILER_INVARIANT_VIOLATION:
                telemetry.notes["compiler_invariant_violation_count"] = (
                    int(telemetry.notes.get("compiler_invariant_violation_count") or 0) + 1
                )
            return failure
        plan = compilation.plan
        with telemetry.stage("canonical_operation_validation"):
            # SetupAgentTurnPlan validation already occurred at the compiler boundary;
            # recording this explicit hand-off keeps internal-schema failures distinct.
            plan = SetupAgentTurnPlan.model_validate(plan.model_dump(mode="json"))
        telemetry.notes["intent_derivations"] = list(compilation.derivations)
        intent_count = len(envelope.semantic_intents)
        operation_count = len(plan.operations)
        telemetry.notes.update(
            {
                "semantic_intent_count": intent_count,
                "compiled_operation_count": operation_count,
                "semantic_to_operation_expansion_ratio": (
                    round(operation_count / intent_count, 4) if intent_count else 0.0
                ),
            }
        )
        if not plan.requires_tool:
            return plan
        try:
            with telemetry.stage("dry_validation"):
                dry = validate_setup_turn_plan(
                    _turn_request(turn, plan, shortlist.allowed_keys, include_gates=False)
                )
        except SetupTurnRejected as exc:
            failure = _classify_plan_failure(
                code=exc.code,
                details=exc.details,
                envelope=envelope,
                message=turn.message,
                operation_intent_refs=compilation.operation_intent_refs,
                intent_segments=compilation.intent_segments,
            )
            if failure.outcome == SemanticIntentOutcome.COMPILER_INVARIANT_VIOLATION:
                telemetry.notes["compiler_invariant_violation_count"] = (
                    int(telemetry.notes.get("compiler_invariant_violation_count") or 0) + 1
                )
            return failure
        return dry.plan

    async def _plan_once(
        self,
        turn: SetupAgentTurnInput,
        shortlist: CapabilityShortlist,
        route: Any,
        *,
        prior_usage: dict[str, Any] | None = None,
        retry_after_bad_shape: bool = False,
    ) -> tuple[PlannerIntentEnvelope, dict[str, Any]]:
        """Make at most one bounded structured model call for a free-text turn."""
        telemetry = turn.telemetry
        instructions = (
            f"{_PLANNER_INSTRUCTIONS}\n{_SHAPE_RETRY_NOTE}"
            if retry_after_bad_shape
            else _PLANNER_INSTRUCTIONS
        )
        with telemetry.stage("planner_schema_serialization"):
            wire_schema = compact_json_schema(PlannerIntentEnvelope)
            complexity = schema_complexity(wire_schema)
            definitions = set((wire_schema.get("$defs") or {}).keys())
            telemetry.notes.update(
                {
                    "model_facing_schema_bytes": complexity["minified_schema_bytes"],
                    "model_facing_schema_depth": complexity["maximum_nesting_depth"],
                    "model_facing_definition_count": complexity["definition_count"],
                    "canonical_models_exposed_to_model": sorted(
                        definitions & FORBIDDEN_SCHEMA_MODELS
                    ),
                }
            )
        with telemetry.stage("planner_payload_serialization"):
            planner_payload = self._planner_payload(turn, shortlist)
            for section, value in planner_payload.items():
                telemetry.record_payload(
                    section,
                    len(json.dumps(value, ensure_ascii=False, default=str)),
                )
            telemetry.record_payload("_instructions", len(instructions))
        prior_reserved = float((prior_usage or {}).get("_setup_reserved_cost_usd") or 0.0)
        reserved_cost = prior_reserved + estimate_structured_call_cost(
            self.settings,
            schema_model=PlannerIntentEnvelope,
            instructions=instructions,
            payload=planner_payload,
            model=route.model,
            max_output_tokens=self.settings.setup_agent_planner_max_output_tokens,
            service_tier=route.service_tier,
        )
        if reserved_cost > self.settings.setup_agent_max_estimated_cost_usd_per_turn:
            raise StructuredCallError(
                "SETUP_AGENT_COST_LIMIT",
                "The planner call would exceed the configured per-turn AI budget.",
                stage="planning",
            )
        provider_attempted = False
        # A call that cannot finish inside the turn's remaining time is not started.
        # Starting it is what produces a client timeout plus a paid answer nobody reads.
        planner_timeout = turn.deadline.timeout_for(
            self.settings.setup_agent_planner_timeout_seconds,
            reserve_seconds=_POST_PLANNER_RESERVE_SECONDS,
        )
        if turn.deadline.budget_seconds > 0 and planner_timeout < _MINIMUM_PROVIDER_SECONDS:
            raise StructuredCallError(
                "TURN_DEADLINE_EXCEEDED",
                "There was not enough time left in this turn to plan it.",
                stage="planning",
            )
        try:
            # The breaker check is inside the measured window on purpose: it is time
            # spent getting a provider answer, and measuring only the HTTP call is how
            # 2.7 seconds of it stayed invisible.
            with telemetry.stage("planner_provider_wait"):
                provider_attempted = True
                telemetry.record_provider_call()
                telemetry.record_model_call("planning")
                envelope, usage = await structured_call(
                    self.settings,
                    schema_model=PlannerIntentEnvelope,
                    schema_name="hilalmarkets_setup_turn_intent",
                    instructions=instructions,
                    payload=planner_payload,
                    model=route.model,
                    reasoning_effort=route.reasoning_effort,
                    max_output_tokens=self.settings.setup_agent_planner_max_output_tokens,
                    service_tier=route.service_tier,
                    timeout_seconds=planner_timeout
                    or self.settings.setup_agent_planner_timeout_seconds,
                    estimated_cost_limit=(
                        self.settings.setup_agent_max_estimated_cost_usd_per_turn
                    ),
                    stage="planning",
                    transport=self.transport,
                )
        except StructuredCallError as exc:
            self.model_call_count += 1
            if provider_attempted:
                # A timeout has no response usage, but it may still be billed. Carry
                # the pessimistic reservation so the account and evaluator do not
                # present a paid attempt as free.
                exc.usage = _merged_usage(prior_usage or {}, exc.usage)
                exc.usage["_setup_reserved_cost_usd"] = reserved_cost
                exc.usage["_setup_planner_attempts"] = (
                    int((prior_usage or {}).get("_setup_planner_attempts") or 0) + 1
                )
                _record_cost_telemetry(telemetry, exc.usage, self.settings, route.model)
            raise
        self.model_call_count += 1
        with telemetry.stage("intent_deserialization"):
            telemetry.record_output(
                "plan_envelope",
                len(envelope.model_dump_json(exclude_none=True)),
            )
        merged_usage = _merged_usage(prior_usage or {}, usage)
        merged_usage["_setup_reserved_cost_usd"] = reserved_cost
        merged_usage["_setup_planner_attempts"] = (
            int((prior_usage or {}).get("_setup_planner_attempts") or 0) + 1
        )
        _record_cost_telemetry(telemetry, merged_usage, self.settings, route.model)
        return envelope, merged_usage

    async def _repair_once(
        self,
        turn: SetupAgentTurnInput,
        shortlist: CapabilityShortlist,
        route: Any,
        *,
        envelope: PlannerIntentEnvelope,
        validation_code: str,
        validation_details: tuple[str, ...],
        invalid_intent_ref: str | None,
        target_path: str | None,
        target_paths: tuple[str, ...] = (),
        source_segment_ref: str | None,
        prior_usage: dict[str, Any],
        topology_only: bool = False,
    ) -> tuple[PlannerRepairEnvelope | BooleanTopologyRepair, dict[str, Any]]:
        """Make the single bounded pre-mutation repair call allowed for this turn.

        Two contracts, chosen by what actually went wrong. A structure-only failure
        gets a contract that cannot express a semantic value at all, so the correction
        physically cannot become a second reading of the turn.
        """

        telemetry = turn.telemetry
        schema_model: type[Any] = BooleanTopologyRepair if topology_only else PlannerRepairEnvelope
        instructions = _TOPOLOGY_REPAIR_INSTRUCTIONS if topology_only else _REPAIR_INSTRUCTIONS
        schema_name = (
            "hilalmarkets_setup_boolean_topology_repair"
            if topology_only
            else "hilalmarkets_setup_intent_repair"
        )
        with telemetry.stage("repair_context_build"):
            payload = (
                self._topology_repair_payload(
                    turn,
                    envelope=envelope,
                    invalid_intent_ref=invalid_intent_ref,
                    validation_details=validation_details,
                )
                if topology_only
                else self._repair_payload(
                    turn,
                    shortlist,
                    envelope=envelope,
                    validation_code=validation_code,
                    validation_details=validation_details,
                    invalid_intent_ref=invalid_intent_ref,
                    target_path=target_path,
                    target_paths=target_paths,
                    source_segment_ref=source_segment_ref,
                )
            )
            for section, value in payload.items():
                telemetry.record_payload(
                    f"repair.{section}",
                    len(json.dumps(value, ensure_ascii=False, default=str)),
                )
        reserved = estimate_structured_call_cost(
            self.settings,
            schema_model=schema_model,
            instructions=instructions,
            payload=payload,
            model=route.model,
            max_output_tokens=self.settings.setup_agent_planner_max_output_tokens,
            service_tier=route.service_tier,
        )
        prior_reserved = float(prior_usage.get("_setup_reserved_cost_usd") or 0.0)
        if prior_reserved + reserved > self.settings.setup_agent_max_estimated_cost_usd_per_turn:
            raise StructuredCallError(
                "SETUP_AGENT_COST_LIMIT",
                "The single planner repair would exceed the per-turn AI budget.",
                stage="planner_repair",
                usage=prior_usage,
            )
        repair_timeout = turn.deadline.timeout_for(
            self.settings.setup_agent_planner_timeout_seconds,
            reserve_seconds=_POST_PLANNER_RESERVE_SECONDS,
        )
        if turn.deadline.budget_seconds > 0 and repair_timeout < _MINIMUM_PROVIDER_SECONDS:
            raise StructuredCallError(
                "TURN_DEADLINE_EXCEEDED",
                "There was not enough time left in this turn to repair the plan.",
                stage="planner_repair",
                usage=prior_usage,
            )
        try:
            with telemetry.stage("repair_provider_wait"):
                telemetry.record_provider_call()
                telemetry.record_model_call("planner_repair")
                deltas, usage = await structured_call(
                    self.settings,
                    schema_model=schema_model,
                    schema_name=schema_name,
                    instructions=instructions,
                    payload=payload,
                    model=route.model,
                    reasoning_effort=route.reasoning_effort,
                    max_output_tokens=self.settings.setup_agent_planner_max_output_tokens,
                    service_tier=route.service_tier,
                    timeout_seconds=repair_timeout
                    or self.settings.setup_agent_planner_timeout_seconds,
                    estimated_cost_limit=max(
                        0.000001,
                        self.settings.setup_agent_max_estimated_cost_usd_per_turn - prior_reserved,
                    ),
                    stage="planner_repair",
                    transport=self.transport,
                )
        except StructuredCallError as exc:
            self.model_call_count += 1
            exc.usage = _merged_usage(
                prior_usage,
                {
                    **exc.usage,
                    "_setup_reserved_cost_usd": prior_reserved + reserved,
                    "_setup_repair_attempts": 1,
                },
            )
            _record_cost_telemetry(telemetry, exc.usage, self.settings, route.model)
            raise
        self.model_call_count += 1
        merged = _merged_usage(prior_usage, usage)
        merged["_setup_reserved_cost_usd"] = prior_reserved + reserved
        merged["_setup_repair_attempts"] = 1
        _record_cost_telemetry(telemetry, merged, self.settings, route.model)
        return deltas, merged

    def _repair_payload(
        self,
        turn: SetupAgentTurnInput,
        shortlist: CapabilityShortlist,
        *,
        envelope: PlannerIntentEnvelope,
        validation_code: str,
        validation_details: tuple[str, ...],
        invalid_intent_ref: str | None,
        target_path: str | None,
        target_paths: tuple[str, ...] = (),
        source_segment_ref: str | None,
    ) -> dict[str, Any]:
        """What the repair call sees: the reading, the words, and what went wrong.

        No internal mutation schema, no canonical requirement dump. The old repair
        payload re-sent the whole 12 KB operation schema and the entire requirement
        state — around 19,000 characters of it — which made the call meant to recover
        from a failure larger than the call that failed.
        """

        del shortlist
        index = _intent_index(invalid_intent_ref)
        invalid_intent = (
            envelope.semantic_intents[index]
            if index is not None and index < len(envelope.semantic_intents)
            else None
        )
        segment_ref = source_segment_ref or (
            invalid_intent.segment_ref if invalid_intent is not None else None
        )
        source_segment = next(
            (item for item in envelope.segments if item.segment_ref == segment_ref),
            None,
        )
        return {
            "invalid_intent": (
                invalid_intent.model_dump(mode="json", exclude_none=True)
                if invalid_intent is not None
                else None
            ),
            "verified_source_segment": (
                source_segment.model_dump(mode="json") if source_segment else None
            ),
            "relevant_existing_value": _relevant_existing_value(
                turn.draft,
                target_path,
                invalid_intent,
                _planner_references(turn),
            ),
            "validation": {
                "code": validation_code,
                "path": target_path or "",
                # Every field this failure named. Correcting one and leaving the rest
                # is what made a paid correction end in the identical refusal.
                "paths": list(target_paths or ((target_path,) if target_path else ())),
                "sanitized_paths": _sanitized_validation_paths(validation_details)[:3],
            },
            "allowed_repair_kinds": [
                "remove_intent",
                "remove_field",
                "replace_with_grounded_value",
                "relink_source_segment",
                "inherit_existing_value",
                "correct_semantic_role",
                "replace_target_reference",
                "preserve_as_unsupported",
            ],
            "minimum_target_references": _minimum_reference_context(
                invalid_intent, _planner_references(turn)
            ),
        }

    def _topology_repair_payload(
        self,
        turn: SetupAgentTurnInput,
        *,
        envelope: PlannerIntentEnvelope,
        invalid_intent_ref: str | None,
        validation_details: tuple[str, ...],
    ) -> dict[str, Any]:
        """What a structure-only correction sees: the rules, and how they were joined.

        Not the whole plan. The rules are already correct and already grounded; sending
        them back for re-authoring is how a correction turns into a second reading.
        Only references and the shape are on the table here.
        """

        index = _intent_index(invalid_intent_ref)
        intent = (
            envelope.semantic_intents[index]
            if index is not None and index < len(envelope.semantic_intents)
            else None
        )
        payload = intent.payload if intent is not None else None
        structure = getattr(payload, "boolean_structure", None)
        leaves = (
            [
                {"leaf_ref": leaf.leaf_ref, "exact_words": leaf.source_quote}
                for leaf in structure.condition_leaves
            ]
            if structure is not None
            else []
        )
        return {
            "user_message": turn.message,
            "rules_already_understood": leaves,
            "arrangement_you_returned": (
                {
                    "groups": [
                        item.model_dump(mode="json", exclude_none=True)
                        for item in structure.boolean_groups
                    ],
                    "root_ref": structure.root_ref,
                }
                if structure is not None
                else None
            ),
            "why_it_was_refused": _sanitized_validation_paths(validation_details)[:4],
        }

    async def _compose_once(
        self,
        turn: SetupAgentTurnInput,
        plan: SetupAgentTurnPlan,
        result: SetupTurnExecutionResult,
        *,
        model: str,
        service_tier: str,
        planner_usage: dict[str, Any],
    ) -> tuple[ComposedReply, dict[str, Any]]:
        knowledge = product_knowledge()
        ledger = build_evidence_ledger(
            reconciled_operations=[
                item.model_dump(mode="json") for item in result.reconciled_operations
            ],
            execution=result.model_dump(mode="json"),
            draft_read_model=result.draft_read_model,
            screening_evidence=result.screening_evidence,
            preflight_evidence=result.preflight_manifest,
            product_knowledge=knowledge,
        )
        payload = {
            "final_execution_result": result.model_dump(mode="json"),
            # Kept for context, but a reply may not cite one: these are intermediate
            # steps, and a step undone later in the same turn did not happen.
            "operation_specific_diffs": [
                item.model_dump(mode="json") for item in result.operation_results
            ],
            # What actually survived the turn. Only these carry an evidence id.
            "net_effect_of_each_operation": [
                item.model_dump(mode="json") for item in result.reconciled_operations
            ],
            "final_compiled_status": result.compile_status,
            "screening_status": result.screening_status,
            "provider_status": result.provider_status,
            "final_chat_status": result.final_chat_status,
            "draft_read_model": result.draft_read_model,
            "screening_evidence": result.screening_evidence,
            "market_data_check": result.preflight_manifest,
            "response_points": [item.model_dump(mode="json") for item in plan.response_points],
            "questions_to_answer": list(plan.questions_to_answer),
            "grounded_product_knowledge": knowledge,
            "authorized_clarification_list": [
                item.model_dump(mode="json") for item in result.allowed_clarifications
            ],
            # Every id a factual claim may cite this turn. Nothing outside this list is
            # citable, so nothing outside it can be asserted.
            "citable_evidence_ids": ledger.ids(),
            "user_message": turn.message,
            "response_language": turn.active_language,
        }
        reserved = estimate_structured_call_cost(
            self.settings,
            schema_model=SetupAgentReply,
            instructions=_COMPOSER_INSTRUCTIONS,
            payload=payload,
            model=model,
            max_output_tokens=self.settings.setup_agent_composer_max_output_tokens,
            service_tier=service_tier,
        )
        planner_reserved = float(planner_usage.get("_setup_reserved_cost_usd") or 0.0)
        if planner_reserved + reserved > self.settings.setup_agent_max_estimated_cost_usd_per_turn:
            raise StructuredCallError(
                "SETUP_AGENT_COST_LIMIT",
                "Contextual wording would exceed the configured per-turn AI budget.",
                stage="response_composition",
            )
        telemetry = turn.telemetry
        for section, value in payload.items():
            telemetry.record_payload(
                f"composer.{section}",
                len(json.dumps(value, ensure_ascii=False, default=str)),
            )
        # Wording is the one thing a turn can finish without. When the budget is nearly
        # spent the deterministic summary is used instead of starting a call that would
        # arrive after the client gave up.
        composer_timeout = turn.deadline.timeout_for(
            self.settings.setup_agent_composer_timeout_seconds,
            reserve_seconds=_POST_COMPOSER_RESERVE_SECONDS,
        )
        if turn.deadline.budget_seconds > 0 and composer_timeout < _MINIMUM_PROVIDER_SECONDS:
            raise StructuredCallError(
                "TURN_DEADLINE_EXCEEDED",
                "There was not enough time left in this turn to word the reply.",
                stage="response_composition",
            )
        try:
            with telemetry.stage("response_composition"):
                telemetry.record_provider_call()
                telemetry.record_model_call("response_composition")
                reply, usage = await structured_call(
                    self.settings,
                    schema_model=SetupAgentReply,
                    schema_name="hilalmarkets_setup_turn_reply",
                    instructions=_COMPOSER_INSTRUCTIONS,
                    payload=payload,
                    model=model,
                    reasoning_effort="low",
                    max_output_tokens=self.settings.setup_agent_composer_max_output_tokens,
                    service_tier=service_tier,
                    timeout_seconds=composer_timeout
                    or self.settings.setup_agent_composer_timeout_seconds,
                    estimated_cost_limit=max(
                        0.000001,
                        self.settings.setup_agent_max_estimated_cost_usd_per_turn
                        - planner_reserved,
                    ),
                    stage="response_composition",
                    transport=self.transport,
                )
        except StructuredCallError:
            self.model_call_count += 1
            raise
        self.model_call_count += 1
        # Structural check first, and it decides. Every factual claim has to state a
        # proposition that matches the evidence; anything that does not is replaced by
        # deterministic text built from the evidence. Reading ids and values rather than
        # sentences is what makes this behave the same for an Arabic reply as an English
        # one. The message itself is assembled here — the model never returns one.
        composed = compose_final_reply(
            reply,
            ledger,
            owes_a_fact=requires_factual_answer(
                reconciled_operations=[
                    item.model_dump(mode="json") for item in result.reconciled_operations
                ],
                response_points=[item.model_dump(mode="json") for item in plan.response_points],
                questions_to_answer=list(plan.questions_to_answer),
            ),
            fallback_message=deterministic_summary(
                result, language=turn.active_language
            ),
        )
        # The older English phrase gate is kept as an extra filter. It can only reject,
        # never accept, so it adds coverage for English without becoming the authority.
        if not _composer_reply_is_grounded(composed, result, payload):
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
        _record_cost_telemetry(telemetry, merged, self.settings, model)
        return composed, merged

    def _planner_payload(
        self,
        turn: SetupAgentTurnInput,
        shortlist: CapabilityShortlist,
    ) -> dict[str, Any]:
        """Everything the model needs to read this turn, and nothing it cannot act on.

        Measured on a one-condition draft, ``requirement_states`` was **18,026 of the
        24,595 characters** sent — 73% of the payload — for 34 records of which 9 were
        blocking and 10 were governed Sharia-policy internals. Between 39% and 44%
        of the characters were ``null``, ``false``, ``""`` or ``[]``.

        What replaces it is the same facts in the form a reader can use: the open
        questions that are actually blocking, in plain words.
        """

        draft = turn.draft
        references = _planner_references(turn)
        return {
            "current_user_turn": turn.message,
            "recent_dialogue": list(turn.dialogue)[-DIALOGUE_WINDOW_MAX:],
            "setup_mode": turn.setup_mode.value,
            "response_language": turn.active_language,
            "draft": {
                "name": draft.name,
                "included_symbols": draft.universe.included_symbols[:50],
                "excluded_symbols": draft.universe.excluded_symbols[:50],
                "market_scope": draft.market_scope.model_dump(mode="json"),
                "sharia_preferences": _public_sharia_preferences(draft, references),
                "conditions": _condition_labels(draft, references),
                "boolean_shape": _boolean_shape(draft, references),
            },
            "open_questions": _open_questions(draft, references),
            "unsupported_requirements": [
                {"missing": item.missing_contract} for item in draft.unsupported_requirements
            ],
            "available_snapshots": [
                {"snapshot_ref": item.reference} for item in references.snapshots
            ],
            "conversation_context": _conversation_context(
                turn.conversation,
                references,
            ),
            "governed_sharia_choices": {
                "methodologies": [item.prompt_dict() for item in references.methodologies],
                "approved_watchlists": [item.prompt_dict() for item in references.watchlists],
            },
            "approval_eligible": draft.approval_eligible,
            "semantic_violations": _public_semantic_violations(draft, references),
            # Read-only evidence from a materially identical earlier request. These
            # values were independently proved against the trader's own source text;
            # they are not canonical state and cannot bypass this turn's semantic,
            # grounding, dry-validation, or execution gates.
            "prior_grounded_retry_evidence": [
                item.to_dict() for item in turn.repeats.reusable_requirements[:20]
            ]
            if turn.repeats.is_repeat
            else [],
            "core_primitives": _core_primitives(),
            "capability_shortlist": _model_capability_shortlist(shortlist),
            "product_boundaries": _PRODUCT_BOUNDARIES,
            "product_knowledge": product_knowledge(),
        }


def _open_questions(
    draft: StrategyDraftV2,
    references: PlannerReferenceContext,
) -> list[dict[str, Any]]:
    """The questions that actually block this draft, in the words a reader needs.

    Only blocking requirements and only fields a planner can act on. Governed Sharia
    identities and assessments stay out of this list: the planner may identify an
    explicit public preference, while only the registry may resolve policy identity or
    asset status.
    """

    blocking = [item for item in active_requirement_states(draft) if item.blocking]
    open_by_path = {item.target_path: item for item in blocking}
    rows: list[dict[str, Any]] = []
    clarification_by_id = {
        canonical: reference for reference, canonical in references.clarification_ids.items()
    }
    condition_by_id = {
        canonical: reference for reference, canonical in references.condition_ids.items()
    }
    for item in draft.unresolved_fields:
        if not item.blocking:
            continue
        rows.append(
            {
                "clarification_ref": clarification_by_id.get(item.unresolved_id),
                "about": item.target_field or item.target_type,
                "rule_ref": condition_by_id.get(item.target_condition_id or ""),
                "question": item.question,
                "options": list(item.allowed_options[:6]),
            }
        )
    for path, state in open_by_path.items():
        if state.semantic_type.startswith("sharia") or any(row["about"] == path for row in rows):
            continue
        rows.append(
            {
                "clarification_ref": clarification_by_id.get(state.requirement_id),
                "about": _public_requirement_path(path, condition_by_id),
                "rule_ref": condition_by_id.get(state.target_condition_id or ""),
                "question": state.reason,
                "options": [],
            }
        )
    return rows[:20]


def _public_requirement_path(
    path: str,
    condition_by_id: dict[str, str],
) -> str:
    public = path
    for canonical, reference in condition_by_id.items():
        public = public.replace(canonical, reference)
    return public


def _public_semantic_violations(
    draft: StrategyDraftV2,
    references: PlannerReferenceContext,
) -> list[str]:
    condition_by_id = {
        canonical: reference for reference, canonical in references.condition_ids.items()
    }
    return [
        _public_requirement_path(item, condition_by_id) for item in validate_draft_semantics(draft)
    ]


def _model_capability_shortlist(shortlist: CapabilityShortlist) -> dict[str, Any]:
    """Public mechanics only; registry identity remains a server validation input."""

    candidates: list[dict[str, Any]] = []
    for item in shortlist.candidates:
        candidate = item.to_prompt_dict()
        candidate.pop("capability_version", None)
        candidates.append(candidate)
    return {
        "candidates": candidates,
        "unknown_terms": list(shortlist.unknown_terms),
        "rule": (
            "Choose only a listed capability_key. If none is exact, preserve the "
            "request as unsupported. Never invent or substitute a nearby mechanic."
        ),
    }


def _conversation_context(
    conversation: SetupConversationContext,
    references: PlannerReferenceContext,
) -> dict[str, Any]:
    """What the last few turns were about. Language only, never executable.

    There is deliberately no "decided answer" field here any more. It existed to tell the
    planner what the deterministic resolver had already read, because the planner then
    had to build the operation. Nothing does that now: an answer to a server-owned
    question is applied from the question's own stored continuation and the planner is
    not called at all, so there is nothing to tell it.
    """

    return {
        "active_clarification_ref": next(
            (
                reference
                for reference, canonical in references.clarification_ids.items()
                if canonical == conversation.active_question_id
            ),
            None,
        ),
        "question_text": conversation.question_text,
        "valid_answer_shape": conversation.valid_answer_shape,
        "last_assistant_summary": conversation.last_assistant_summary,
        "active_language": conversation.active_language,
        "active_goal": conversation.active_goal,
        "pending_read_only_scan": conversation.pending_read_only_scan,
        "pending_supported_request": conversation.pending_supported_request,
    }


def _segment_trace(envelope: PlannerIntentEnvelope) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "segment_ref": item.segment_ref,
            "kind": item.segment_kind.value,
            "text": item.exact_source_text,
        }
        for item in envelope.segments
    )


def _repair_can_help(code: str, details: tuple[str, ...]) -> bool:
    """Would asking the model once more have any chance of a different answer?

    A paid call that cannot succeed is worse than a refusal: it costs money, it costs the
    user seconds, and it ends in the same message. Two families can never be talked out
    of — the sanitation classes marked unrepairable, and any refusal whose reason is a
    boundary rather than a mistake.
    """

    if code in SANITATION_CLASSES:
        return SANITATION_CLASSES[code] == SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED
    if code not in _REPAIRABLE_VALIDATION_CODES:
        return False
    boundary_markers = {
        "authentication",
        "authorization",
        "ownership",
        "approval",
        "screening",
        "provider",
        "capability_not_offered",
        "capability_not_registered",
        "unsupported_requirement",
        "unsupported_mechanic",
    }
    joined = " ".join(details).casefold()
    return not any(marker in joined for marker in boundary_markers)


def _classify_plan_failure(
    *,
    code: str,
    details: tuple[str, ...],
    envelope: PlannerIntentEnvelope,
    message: str,
    declared_outcome: SemanticIntentOutcome | None = None,
    intent_ref: str | None = None,
    target_path: str | None = None,
    target_paths: tuple[str, ...] = (),
    segment_ref: str | None = None,
    operation_intent_refs: dict[str, str] | None = None,
    intent_segments: dict[str, str] | None = None,
) -> _PlanFailure:
    """Classify every semantic/compiler/canonical failure at one boundary.

    Repair is a narrow proof, never the default.  For compiler failures the compiler
    must supply one exact compact intent, one or more named fields, and a verified
    segment.  For canonical failures every structured detail must independently map
    back to the same intent.  An operation id, an exception string, or a canonical
    default is not enough.

    What this must **not** do is call an unproven attribution a compiler invariant.
    ``COMPILER_INVARIANT_VIOLATION`` is terminal: no repair, no question, HTTP 422 and
    a message saying nothing changed. Using it as the catch-all is what turned an
    ordinary instruction into an unanswerable one in evaluator runs 20260802T232050Z
    and 20260803T000036Z. A failure the server cannot attribute is a
    ``CANONICAL_VALIDATION_FAILURE``: still not repairable, but truthfully named, and
    it reaches the trader as a support reference rather than as "rephrase this".
    """

    terminal_outcomes = {
        SemanticIntentOutcome.USER_INFORMATION_REQUIRED,
        SemanticIntentOutcome.UNSUPPORTED_REQUIREMENT,
        SemanticIntentOutcome.NON_RECOVERABLE_FAILURE,
        SemanticIntentOutcome.COMPILER_INVARIANT_VIOLATION,
    }
    if declared_outcome in terminal_outcomes:
        return _PlanFailure(
            code=code,
            details=(
                tuple(_sanitized_validation_paths(details))
                if declared_outcome == SemanticIntentOutcome.COMPILER_INVARIANT_VIOLATION
                else details
            ),
            outcome=declared_outcome,
            intent_ref=intent_ref,
            target_path=target_path,
            target_paths=target_paths,
            segment_ref=segment_ref,
        )

    if declared_outcome == SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED:
        candidate = _verified_repair_attribution(
            code=code,
            details=details,
            envelope=envelope,
            message=message,
            intent_ref=intent_ref,
            target_path=target_path,
            target_paths=target_paths,
            segment_ref=segment_ref,
            allow_omitted_field=code
            in {"INTENT_VALUE_UNREADABLE", "PLANNER_SEMANTIC_OMISSION"},
        )
        return candidate or _unattributed_failure(code, details)

    if code not in _REPAIRABLE_VALIDATION_CODES or not details:
        return _unattributed_failure(code, details)
    operation_intent_refs = operation_intent_refs or {}
    intent_segments = intent_segments or {}
    attributed_intents: set[str] = set()
    attributed_paths: set[str] = set()
    for detail in details:
        parts = str(detail).split(":")
        if len(parts) < 3:
            return _unattributed_failure(code, details)
        attributed_intent = operation_intent_refs.get(parts[0])
        if attributed_intent is None:
            return _unattributed_failure(code, details)
        index = _intent_index(attributed_intent)
        if index is None or index >= len(envelope.semantic_intents):
            return _unattributed_failure(code, details)
        path = _model_owned_semantic_path(envelope.semantic_intents[index], parts)
        if path is None:
            return _unattributed_failure(code, details)
        attributed_intents.add(attributed_intent)
        attributed_paths.add(path)
    # Several canonical complaints about the *same* intent are one correction, not an
    # internal fault. Requiring exactly one path here is what sent a two-field refusal
    # down the terminal branch.
    if len(attributed_intents) != 1 or not attributed_paths:
        return _unattributed_failure(code, details)
    attributed_intent = next(iter(attributed_intents))
    ordered_paths = tuple(sorted(attributed_paths))
    candidate = _verified_repair_attribution(
        code=code,
        details=details,
        envelope=envelope,
        message=message,
        intent_ref=attributed_intent,
        target_path=ordered_paths[0],
        target_paths=ordered_paths,
        segment_ref=intent_segments.get(attributed_intent),
    )
    return candidate or _unattributed_failure(code, details)


def _unattributed_failure(code: str, details: tuple[str, ...]) -> _PlanFailure:
    """A real refusal the server could not pin on one model-owned field.

    This is not a compiler invariant. The compiler produced something a canonical gate
    refused, which is a canonical validation failure — reportable, alertable, and never
    recoverable by asking the trader to say it differently.
    """

    return _PlanFailure(
        code=code if code != "COMPILER_INVARIANT_VIOLATION" else "CANONICAL_VALIDATION_FAILURE",
        details=tuple(_sanitized_validation_paths(details)),
        outcome=SemanticIntentOutcome.NON_RECOVERABLE_FAILURE,
    )


def _compiler_invariant_failure(details: tuple[str, ...]) -> _PlanFailure:
    """Reserved for the one thing the name means: the server built something invalid."""

    return _PlanFailure(
        code="COMPILER_INVARIANT_VIOLATION",
        details=tuple(_sanitized_validation_paths(details)),
        outcome=SemanticIntentOutcome.COMPILER_INVARIANT_VIOLATION,
    )


def _verified_repair_attribution(
    *,
    code: str,
    details: tuple[str, ...],
    envelope: PlannerIntentEnvelope,
    message: str,
    intent_ref: str | None,
    target_path: str | None,
    target_paths: tuple[str, ...] = (),
    segment_ref: str | None,
    allow_omitted_field: bool = False,
) -> _PlanFailure | None:
    """Return a repair failure only after its complete provenance proof succeeds.

    Every named field is proved separately. A path the intent does not own is dropped
    rather than failing the whole attribution: correcting three of four stated values
    is still progress, and refusing all four because of one is how a turn that had a
    correct partial reading ended with nothing.
    """

    if not _repair_can_help(code, details) or not intent_ref or not segment_ref:
        return None
    named = tuple(dict.fromkeys([*target_paths, *((target_path,) if target_path else ())]))
    if not named:
        return None
    index = _intent_index(intent_ref)
    if index is None or index >= len(envelope.semantic_intents):
        return None
    intent = envelope.semantic_intents[index]
    if intent.segment_ref != segment_ref and not allow_omitted_field:
        return None
    segment = next((item for item in envelope.segments if item.segment_ref == segment_ref), None)
    if segment is None or segment.exact_source_text not in message:
        return None
    owned = tuple(
        path
        for path in named
        if _intent_owns_path(intent, path, allow_omitted=allow_omitted_field)
    )
    if not owned:
        return None
    return _PlanFailure(
        code=code,
        details=tuple(_sanitized_validation_paths(details)),
        outcome=SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED,
        intent_ref=intent_ref,
        target_path=owned[0],
        target_paths=owned,
        segment_ref=segment_ref,
    )


def _intent_owns_path(
    intent: SemanticIntent,
    target_path: str,
    *,
    allow_omitted: bool,
) -> bool:
    """Whether one compact field, not canonical metadata, owns ``target_path``."""

    payload = intent.payload
    if target_path == "boolean_structure":
        # Structure is model-owned, and it is the one path a correction may rearrange
        # without touching any rule's meaning.
        return isinstance(payload, ReplaceBooleanPayload)
    if target_path.startswith("condition."):
        condition = getattr(payload, "condition", None)
        if condition is None:
            return False
        field_name = target_path.removeprefix("condition.").split(".", 1)[0]
        supported = field_name in type(condition).model_fields
        return supported and (allow_omitted or field_name in condition.model_fields_set)
    field_name = target_path.removeprefix("payload.").split(".", 1)[0]
    supported = field_name in type(payload).model_fields
    return supported and (allow_omitted or field_name in payload.model_fields_set)


def _model_owned_semantic_path(intent: SemanticIntent, parts: list[str]) -> str | None:
    """Map one safe canonical error path back to a field the model supplied."""

    payload = intent.payload
    operation_kind = parts[1]
    field_name = parts[2]
    if operation_kind == "set_fields":
        return field_name if field_name in payload.model_fields_set else None
    if operation_kind in {
        "add_inclusion",
        "add_exclusion",
        "remove_inclusion",
        "remove_exclusion",
    }:
        return "symbol" if "symbol" in payload.model_fields_set else None
    if operation_kind == "sharia_policy":
        candidates = {
            "universe_mode": ("screened_assets_only", "approved_watchlist_only"),
            "approved_watchlist": ("approved_watchlist_only",),
            "methodology_identifier": ("methodology_identifier", "methodology_family"),
            "allowed_statuses": ("fail_closed_preference",),
        }.get(field_name, (field_name,))
        supplied = [name for name in candidates if name in payload.model_fields_set]
        return supplied[0] if len(supplied) == 1 else None
    if operation_kind not in {"condition", "add_condition", "update_condition"}:
        return None
    condition = getattr(payload, "condition", None)
    if condition is None:
        return None
    # Canonical names and model-facing names are not the same word for the same thing.
    # A missing entry here is not cosmetic: the path resolves to nothing, the failure
    # loses its attribution, and a perfectly repairable grounding problem becomes an
    # unrecoverable refusal. `operator`/`comparator` and `formula`/`formula_key` were
    # both missing, which made every comparator and formula grounding failure terminal.
    canonical_to_semantic = {
        "context_timeframe": "context_timeframes",
        "confirmation_timeframe": "confirmation_timeframes",
        "condition_symbol": "condition_symbols",
        "operator": "comparator",
        "formula": "formula_key",
        "source_fragment": "source_quote",
        "capability_version": "capability_key",
    }
    semantic_field = canonical_to_semantic.get(field_name, field_name)
    if semantic_field not in condition.model_fields_set:
        return None
    return f"condition.{semantic_field}"


def _intent_index(intent_ref: str | None) -> int | None:
    match = re.fullmatch(r"intent_(\d+)", str(intent_ref or ""))
    return int(match.group(1)) - 1 if match else None


def _relevant_existing_value(
    draft: StrategyDraftV2,
    target_path: str | None,
    intent: SemanticIntent | None,
    references: PlannerReferenceContext,
) -> Any:
    if intent is None or not target_path:
        return None
    payload = intent.payload
    reference = getattr(payload, "target_reference", None)
    condition = getattr(payload, "condition", None)
    reference = reference or getattr(condition, "target_reference", None)
    condition_id = references.condition_id(reference)
    if condition_id and draft.condition_ast is not None:
        node = next(
            (item for item in draft.condition_ast.walk() if item.node_id == condition_id),
            None,
        )
        field_name = target_path.removeprefix("condition.")
        if node is not None and field_name in node.model_fields:
            value = getattr(node, field_name)
            return value.value if hasattr(value, "value") else value
    return None


def _minimum_reference_context(
    intent: SemanticIntent | None,
    references: PlannerReferenceContext,
) -> dict[str, Any]:
    if intent is None:
        return {}
    action = intent.action.value
    if "condition" in action or action == "replace_boolean_structure":
        return {"condition_refs": sorted(references.condition_ids)}
    if action == "restore_owned_version":
        return {"snapshot_refs": [item.reference for item in references.snapshots]}
    if action == "set_sharia_preferences":
        return {
            "methodologies": [item.prompt_dict() for item in references.methodologies],
            "watchlists": [item.prompt_dict() for item in references.watchlists],
        }
    return {}


#: What the user reads when a turn is refused. Plain words, one per class, and never a
#: code or a field path — those go to the operator trace.
_REFUSAL_MESSAGES: dict[str, str] = {
    "INTENT_SEGMENT_NOT_IN_MESSAGE": (
        "I could not match part of that to your exact words. Could you say it again?"
    ),
    "INTENT_TARGET_UNKNOWN": (
        "That pointed at a rule or a question this setup does not have right now."
    ),
    "INTENT_VALUE_UNREADABLE": "I could not read one of the values in that message.",
    "INTENT_INCOMPLETE": "That message is missing something I need before I can set it up.",
    "INTENT_NOT_PERMITTED": (
        "Part of that would change the setup, but nothing in the message asked for it."
    ),
    "PLANNER_SEMANTIC_OMISSION": (
        "I read your rule but lost part of what you wrote. Nothing was changed."
    ),
    "SOURCE_ASSOCIATION_MISMATCH": (
        "I matched part of that to the wrong words in your message. Nothing was changed."
    ),
    "BOOLEAN_TOPOLOGY_MISSING": (
        "I understood each rule, but not the way you joined them. Nothing was changed."
    ),
    "BOOLEAN_TOPOLOGY_AMBIGUOUS": (
        "That logic can be read in more than one way, so I did not choose one."
    ),
    # A canonical gate refused what the server built. The trader wrote nothing wrong,
    # so never ask them to rephrase: give them something support can look up.
    "CANONICAL_VALIDATION_FAILURE": (
        "Something on my side would not accept that change, so I made none. "
        "This is not a problem with how you wrote it — our team can see the details."
    ),
}


#: A turn created without a budget (helpers, tests, replay) never runs out of time.
#: Reporting it as "no time left" would refuse corrections that are perfectly possible.
_UNBOUNDED_TURN_SECONDS = 3600.0


def _normalized_intent_hash(normalized_message: str) -> str:
    """One key for "the trader is asking for the same thing again"."""

    return normalized_intent_hash(normalized_message)


def grounded_requirements_from(
    envelope: PlannerIntentEnvelope,
    message: str,
    references: PlannerReferenceContext = EMPTY_PLANNER_REFERENCES,
) -> tuple[GroundedRequirement, ...]:
    """Every stated value in this reading that the trader's own words authorise.

    Kept even when the turn ends in a refusal. That is the whole point: a turn that
    lost one value out of six still established the other five, and throwing them away
    is what made the trader retype the whole instruction — and what made the retry cost
    another full planner call to reach the same place.
    """

    rows: dict[str, GroundedRequirement] = {}
    segment_text = {item.segment_ref: item.exact_source_text for item in envelope.segments}

    def keep(path: str, value: Any, source: str, *, kind: str | None = None) -> None:
        rendered_value = value.value if hasattr(value, "value") else value
        if not semantic_value_is_grounded(
            rendered_value,
            source,
            path=path,
            references=references,
            replacement_kind=kind,
        ):
            return
        rendered = (
            json.dumps(rendered_value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            if isinstance(rendered_value, (list, dict))
            else str(rendered_value)
        )
        rows[path] = GroundedRequirement(
            semantic_path=path,
            value=rendered[:120],
            source_excerpt=source,
        )

    for intent_index, intent in enumerate(envelope.semantic_intents):
        source = segment_text.get(intent.segment_ref, "")
        if not source or source not in message:
            continue
        payload = intent.payload
        action = intent.action.value
        intent_path = f"semantic_intents[{intent_index}].payload"
        if action in {
            "include_symbol",
            "exclude_symbol",
            "remove_included_symbol",
            "remove_excluded_symbol",
        }:
            keep(f"{intent_path}.symbol", cast(Any, payload).symbol, source, kind="symbol")
            continue
        if action.startswith("set_") and action != "set_sharia_preferences":
            field_name = action.removeprefix("set_")
            keep(f"{intent_path}.{field_name}", getattr(payload, field_name), source)
            continue
        conditions: list[tuple[Any, str]] = []
        condition = getattr(payload, "condition", None)
        if condition is not None:
            conditions.append((condition, f"{intent_path}.condition"))
        structure = getattr(payload, "boolean_structure", None)
        if structure is not None:
            conditions.extend(
                (
                    leaf.condition,
                    f"{intent_path}.boolean_structure.condition_leaves[{leaf_index}].condition",
                )
                for leaf_index, leaf in enumerate(structure.condition_leaves)
            )
        for condition, condition_path in conditions:
            condition_source = condition.source_quote or source
            if condition_source not in source and condition_source not in message:
                continue
            for name in type(condition).model_fields:
                if name not in condition.model_fields_set or name in {
                    "target_reference",
                    "source_quote",
                }:
                    continue
                value = getattr(condition, name, None)
                if value in (None, [], {}):
                    continue
                if name == "capability_parameters":
                    for parameter in cast(list[Any], value):
                        keep(
                            f"{condition_path}.capability_parameters.{parameter.name}",
                            parameter.semantic_value(),
                            condition_source,
                        )
                    continue
                if name == "formula_key" and getattr(value, "value", value) == "capability":
                    # ``capability`` is server/registry metadata. The explicitly named
                    # capability key below is the trader-authored requirement.
                    continue
                kind = (
                    "symbol"
                    if name == "condition_symbols"
                    else "timeframe"
                    if "timeframe" in name
                    else None
                )
                keep(f"{condition_path}.{name}", value, condition_source, kind=kind)

    for answer in envelope.clarification_answers:
        source = segment_text.get(answer.segment_ref, "")
        if source and answer.answer_text in source:
            rows[f"clarification.{answer.clarification_ref}"] = GroundedRequirement(
                semantic_path=f"clarification.{answer.clarification_ref}",
                value=answer.answer_text[:120],
                source_excerpt=source,
            )
    return tuple(rows.values())


def _segment_is_in_message(
    envelope: PlannerIntentEnvelope,
    segment_ref: str | None,
    message: str,
) -> bool:
    """Whether the words that would authorise a correction are really in this turn."""

    if not segment_ref:
        return False
    segment = next((item for item in envelope.segments if item.segment_ref == segment_ref), None)
    return segment is not None and segment.exact_source_text in message


def _failure_record(
    turn: SetupAgentTurnInput,
    envelope: PlannerIntentEnvelope,
    failure: _PlanFailure,
    *,
    fingerprint: str,
    repair_decision: str,
    repair_eligible: bool,
) -> TurnFailureRecord:
    """The typed forensics one failed turn persists.

    Everything here is either the server's own classification or the trader's own
    words. No model reasoning, no provider payload, no prompt, no credential — so the
    record is safe to store, safe to show an operator, and safe to reference back to
    the customer.
    """

    segment = next(
        (item for item in envelope.segments if item.segment_ref == failure.segment_ref),
        None,
    )
    observed: str | None = None
    observed_rows: list[tuple[str, str]] = []
    index = _intent_index(failure.intent_ref)
    if index is not None and index < len(envelope.semantic_intents):
        payload = envelope.semantic_intents[index].payload
        holder = getattr(payload, "condition", payload)
        for path in failure.paths:
            field = path.removeprefix("condition.").removeprefix("payload.").split(".", 1)[0]
            value = getattr(holder, field, None)
            rendered = "absent" if value in (None, [], {}) else str(value)[:80]
            observed_rows.append((path, rendered))
        observed = observed_rows[0][1] if observed_rows else None
    expected_rows = tuple(
        (path, value)
        for path in failure.paths
        if (value := _expected_grounded_value(segment.exact_source_text if segment else "", path))
        is not None
    )
    failure_class = failure.failure_class
    return TurnFailureRecord(
        failure_class=failure_class,
        owner=owner_for(failure_class),
        intent_ref=failure.intent_ref,
        segment_ref=failure.segment_ref,
        semantic_path=failure.target_path,
        semantic_paths=failure.paths,
        source_excerpt=segment.exact_source_text if segment else "",
        expected_value=expected_rows[0][1] if expected_rows else None,
        expected_values=expected_rows,
        observed_value=observed,
        observed_values=tuple(observed_rows),
        repair_eligible=repair_eligible,
        repair_decision=repair_decision,
        support_reference=fingerprint,
        details=failure.details,
    )


def _structured_failure_record(
    turn: SetupAgentTurnInput,
    error: StructuredCallError,
) -> TurnFailureRecord:
    """Safe persisted taxonomy for failures that produced no parseable envelope."""

    failure_class = failure_class_for_code(error.code)
    support_reference = failure_fingerprint(
        canonical_draft_hash=turn.draft.executable_hash or "",
        normalized_user_intent_hash=_normalized_intent_hash(turn.normalized_message),
        failure_class=failure_class,
        failure_paths=(),
    )
    return TurnFailureRecord(
        failure_class=failure_class,
        owner=owner_for(failure_class),
        repair_eligible=False,
        repair_decision="SHAPE_RECOVERY_EXHAUSTED"
        if failure_class is SetupFailureClass.PLANNER_SCHEMA_INVALID
        else "PROVIDER_BOUNDARY",
        support_reference=support_reference,
        details=tuple(_sanitized_validation_paths(error.details)),
    )


def _expected_grounded_value(source: str, path: str) -> str | None:
    """Derive a safe expected value only when the user's words make it unique."""

    field = path.removeprefix("condition.").removeprefix("payload.")
    if field in {
        "trigger_timeframe",
        "context_timeframes",
        "confirmation_timeframes",
        "reference_timeframe",
    }:
        role = field.removesuffix("_timeframes").removesuffix("_timeframe")
        matches = [
            item
            for item in extract_timeframes(source)
            if timeframe_role_is_explicit(source, item, cast(Any, role))
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 and field.endswith("timeframes"):
            return json.dumps(matches, separators=(",", ":"))
        return None
    if field in {"comparator", "operator"}:
        comparator = detect_comparator(source)
        return comparator.value if comparator is not None else None
    if field == "movement_direction":
        return movement_direction(source)
    if field == "strategy_bias":
        lowered = source.casefold()
        matches = [value for value in ("long", "short", "neutral") if value in lowered]
        return matches[0] if len(matches) == 1 else None
    if field == "threshold":
        numeric_matches = {
            float(item)
            for item in re.findall(
                r"(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*(?:%|percent\b|pct\b)",
                source,
                re.I,
            )
        }
        return str(next(iter(numeric_matches))) if len(numeric_matches) == 1 else None
    if field == "unit" and re.search(r"%|\bpercent(?:age)?\b|\bpct\b", source, re.I):
        return "percent"
    if "symbol" in field:
        symbols = extract_symbols(source)
        return symbols[0] if len(symbols) == 1 else None
    return None


def _refusal_message(code: str) -> str:
    return _REFUSAL_MESSAGES.get(
        code,
        "I could not turn that into an exact change. Nothing in your setup was altered.",
    )


def _loop_aware_refusal(code: str, repeats: RepeatState, failure: _PlanFailure) -> str:
    """The refusal to show when the trader has already sent this instruction.

    Repeating "I could not turn that into an exact change" is what produced eight
    identical turns in evaluator run 20260803T000036Z. The second time, the honest
    answer is different: say what *was* understood, name the one thing that is not, and
    stop implying the message needs rewriting.
    """

    base = _refusal_message(code)
    if not repeats.is_repeat:
        return base
    understood = ", ".join(
        sorted(
            {
                item.semantic_path.rsplit(".", 1)[-1]
                for item in repeats.reusable_requirements
            }
        )
    )
    missing = ", ".join(path.removeprefix("condition.") for path in failure.paths)
    lines = ["I have kept everything you already told me, so you do not need to send it again."]
    if understood:
        lines.append(f"Already saved from your earlier messages: {understood}.")
    if missing:
        lines.append(f"The one part I still cannot place is: {missing}.")
    else:
        lines.append(base)
    return " ".join(lines)


def _planner_answered_active_question(
    turn: SetupAgentTurnInput,
    envelope: PlannerIntentEnvelope,
) -> bool:
    """Whether the AI-first reading classified this turn as the open answer.

    The model receives a turn-local clarification reference rather than the canonical
    question id.  The compiler resolves that alias later; here the presence of one
    answer is enough to enter the deterministic value validator because only one
    question can be active at a time.
    """

    return bool(turn.conversation.active_question and envelope.clarification_answers)


def _read_only_scan_values_are_grounded(
    scan: ReadOnlyPercentageScanIntent,
    source_text: str,
) -> bool:
    if _supported_direction(source_text) != scan.movement_direction:
        return False
    if not grounds_number(source_text, float(scan.threshold_percent), unit="percent"):
        return False
    return scan.measurement_window is None or scan_window_is_explicit(source_text)


def _scan_universe_is_ready(draft: StrategyDraftV2) -> bool:
    open_scope_ids = {
        "sharia.universe_mode",
        "sharia.approved_watchlist",
        "sharia.explicit_symbols",
    }
    if any(item.unresolved_id in open_scope_ids for item in draft.unresolved_fields):
        return False
    policy = draft.sharia_policy
    if policy.methodology_id is None or not policy.methodology_version:
        return False
    if policy.universe_mode.value == "approved_watchlist":
        return bool(policy.approved_watchlist_id and policy.approved_watchlist_version)
    if policy.universe_mode.value == "explicit_assets":
        return bool(policy.explicit_symbols or draft.universe.included_symbols)
    # A read-only look at the whole screened market needs no extra confirmation. The
    # open blocker above is the gate: where the product governs the choice it adds one,
    # and that blocker only closes on a real selection. Demanding a fresh confirmation
    # here as well would ask for scope in setups that never govern it.
    return True


#: The one identity of the "which markets?" question inside a live scan. Stable so a
#: late or repeated answer is recognised, and so the launch service knows which
#: governed control a typed answer belongs to.
SCAN_SCOPE_QUESTION: Final[str] = "scan_screened_scope"


def scan_scope_clarification(
    language: ConversationLanguage,
    draft: StrategyDraftV2 | None = None,
) -> ClarificationContract:
    """Ask which screened markets to look at — as a real, answerable question.

    This used to be a sentence. A sentence cannot be answered: the next message had no
    open question to land on, so "all" was re-read as a brand new request and the scan
    the trader had already half-described was quietly dropped.

    It is deliberately **not** mutating. Choosing a screened universe changes governed
    Sharia policy, and only the application's own allowlisted control may do that. This
    question collects the choice; the governed route applies it.
    """

    values = list(display_options(AnswerDomain.UNIVERSE_MODE))
    labels = labels_for(AnswerDomain.UNIVERSE_MODE, values, language)
    # A fresh draft has empty hashes, which is exactly "not bound to a draft state". That
    # is right for this question: it collects a choice for the governed control and
    # changes nothing itself, so a draft edit in between does not invalidate the answer.
    draft = draft if draft is not None else StrategyDraftV2()
    return ClarificationContract(
        question_id=SCAN_SCOPE_QUESTION,
        question=localized("ask.universe", language),
        reason="A live scan runs over a screened universe the trader has chosen.",
        target_type="conversational",
        #: Names the governed control this question collects for, so the one answer
        #: resolver reads it with the screened-scope vocabulary rather than as a
        #: three-item list of button labels.
        target_field="sharia_policy.universe_mode",
        expected_answer_schema=json.dumps(
            {"type": "string", "enum": values}, sort_keys=True, separators=(",", ":")
        ),
        mutating=False,
        allowed_options=list(labels.values())[:6],
        canonical_values=values,
        # The route to the governed control travels *with* the question. It used to be
        # rediscovered later from a constant listing "question ids that are really
        # governed" — a registry nobody could see, and one a new governed question would
        # simply be missing from, letting chat text move Sharia policy.
        continuation=governed_continuation(
            question_id=SCAN_SCOPE_QUESTION,
            step_revision=0,
            target_type="conversational",
            target_field="sharia_policy.universe_mode",
            option_key="screened_universe_mode",
            allowed_values=values,
            draft=draft,
            cancellation_policy=CancellationPolicy.CANCEL_CONVERSATION_ONLY,
            # A live scan holds nothing canonical of its own, so a different request may
            # take over without stranding a blocker.
            replacement_policy=ReplacementPolicy.REPLACE_SILENTLY,
        ),
    )


def _supported_direction(text: str) -> str | None:
    """Up or down, decided only by the direction reader.

    This used to keep a second word list beside ``movement_direction`` for Arabic,
    French, Spanish and Russian. Two lists is how the two disagreed: the reader knew
    ``ارتفعت`` and this one knew ``ترتفع``, so the same sentence read as a rise in one
    place and as no direction in the other. Those words now live in the reader and
    every caller gets all of them.
    """

    direction = movement_direction(text)
    return direction if direction in {"up", "down"} else None


def _is_canonical_percentage_request(metadata: dict[str, object]) -> bool:
    return bool(
        metadata.get("movement_direction") in {"up", "down"}
        and isinstance(metadata.get("threshold"), int | float)
        and metadata.get("unit") == "percent"
        and not metadata.get("capability_key")
    )


def _supported_missing_slots(
    metadata: dict[str, object],
    declared: list[Any],
) -> list[str]:
    """Canonical missing choices for one supported percentage rule.

    ``formula`` is intentionally represented by the human reference choice rather
    than a second implementation question.  Choosing candle-open versus previous-close
    deterministically selects the registered core formula.
    """

    # Universe selection is already a separate governed Sharia-policy workflow.
    # Mixing it into condition creation duplicates the question and lets one answer
    # ambiguously target two different canonical objects.
    missing = [
        str(item)
        for item in declared
        if str(item) not in {"universe", "formula"}
    ]
    if metadata.get("threshold") is not None:
        missing = [item for item in missing if item != "threshold"]
    if metadata.get("comparator") is not None:
        missing = [item for item in missing if item != "comparator"]
    elif "comparator" not in missing:
        missing.append("comparator")
    if metadata.get("trigger_timeframe"):
        missing = [item for item in missing if item != "trigger_timeframe"]
    if metadata.get("threshold") is None and "threshold" not in missing:
        missing.append("threshold")
    if metadata.get("movement_direction") not in {"up", "down"}:
        # This specialised workflow must never guess the direction.  The caller falls
        # back to the ordinary supported-incomplete path when this invariant is false.
        return []
    if not metadata.get("trigger_timeframe") and "trigger_timeframe" not in missing:
        missing.append("trigger_timeframe")
    missing = ["reference_point" if item == "formula" else item for item in missing]
    # Reference point determines the exact core percentage formula; keep only one slot.
    if metadata.get("reference_point"):
        missing = [item for item in missing if item != "reference_point"]
    elif "reference_point" not in missing:
        missing.append("reference_point")
    return list(dict.fromkeys(missing))


def _canonical_supported_slots(missing: list[str]) -> list[str]:
    """Map conversational choices to canonical condition fields.

    The unresolved record is consumed by the generic requirement reconciler, which
    understands canonical fields rather than UI wording.  Keeping this mapping at the
    server boundary prevents ``reference_point`` and ``comparator`` from becoming
    imaginary condition attributes.
    """

    field_map = {
        "reference_point": "formula",
        "comparator": "operator",
    }
    return list(dict.fromkeys(field_map.get(item, item) for item in missing))


def _next_supported_field(missing: list[str]) -> str:
    priority = (
        "trigger_timeframe",
        "reference_point",
        "comparator",
        "threshold",
        "capability_parameter",
        "universe",
    )
    return next((item for item in priority if item in missing), missing[0])


#: Which value a live scan is waiting for, per answer domain. The governed Scanner has
#: exactly these arguments, so an answer to any other kind of question cannot put a key
#: into a scan request.
_SCAN_FIELD_BY_DOMAIN: Final[dict[AnswerDomain, str]] = {
    AnswerDomain.SCAN_WINDOW: "measurement_window",
    AnswerDomain.PERCENT: "threshold_percent",
    AnswerDomain.MOVEMENT_DIRECTION: "movement_direction",
}


#: The name of the goal a half-collected live scan is working towards. One constant,
#: because the launch service, the option-button path and the agent all test for it.
READ_ONLY_SCAN_GOAL: Final[str] = "read_only_percentage_scan"

#: The only measurement window the provider exposes. Offering a period the backend
#: cannot measure is a promise the scan then breaks, so the question offers this and
#: nothing else until another window is actually implemented.
SUPPORTED_SCAN_WINDOWS: Final[tuple[str, ...]] = ("24h",)


def _merge_scan_request(
    pending: Mapping[str, object],
    slots: ScanSlots,
    message: str,
) -> dict[str, object]:
    """Fold this turn's answer into the scan already being collected.

    Only fields this turn actually supplied are written. A stored direction, threshold
    or symbol list is never overwritten with ``None`` — that is how answering the window
    question used to erase the 5% the trader had given one message earlier.
    """

    merged: dict[str, object] = dict(pending)
    if slots.threshold_percent is not None:
        merged["threshold_percent"] = float(slots.threshold_percent)
    if slots.direction is not None:
        merged["movement_direction"] = str(slots.direction)
    if slots.window is not None:
        merged["measurement_window"] = str(slots.window)
    merged.setdefault("measurement_window", None)
    # Kept as evidence of what the trader asked for, never as a filter: the governed
    # scan has no symbol argument and runs over the draft's screened universe. Storing
    # them so the reply could claim a per-symbol scan would be describing something the
    # platform does not do.
    if slots.symbols:
        merged["symbols"] = list(slots.symbols)
    if slots.screened_universe:
        merged["screened_universe"] = True
    # Provenance is the words that first stated the scan, not the word "yes" that
    # finished it. Overwriting it would make the stored evidence disagree with the
    # values it is supposed to justify.
    merged.setdefault("source_text", " ".join((message or "").split())[:500])
    return merged


def scan_window_contract(question: str, source_turn_id: str) -> ClarificationContract:
    """The window question, as the same non-mutating contract the governed path builds."""

    digest = hashlib.sha256(f"{source_turn_id}:read-only-window".encode()).hexdigest()[:20]
    question_id = f"scan_window_{digest}"
    schema_text = json.dumps(
        {"type": "string", "enum": list(SUPPORTED_SCAN_WINDOWS)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return ClarificationContract(
        question_id=question_id,
        question=question,
        reason="The provider's rolling percentage field needs an explicit window.",
        target_type="conversational",
        # Naming the field is what tells the one answer resolver which vocabulary this
        # question speaks. Without it the question carried a bare option list, so only
        # the literal string `24h` answered it and "24 hours" did not.
        target_field="scan_window",
        expected_answer_schema=schema_text,
        mutating=False,
        allowed_options=list(SUPPORTED_SCAN_WINDOWS),
        canonical_values=list(SUPPORTED_SCAN_WINDOWS),
        continuation=scan_continuation(
            question_id=question_id,
            target_field="scan_window",
            scan_field="measurement_window",
            allowed_values=list(SUPPORTED_SCAN_WINDOWS),
            answer_schema=schema_text,
        ),
    )


def _goal_from_pending(
    pending: Mapping[str, object],
    conversation: SetupConversationContext,
) -> ConversationalGoal:
    """What the trader is still trying to do, read from the one stored copy of it.

    The open question comes with it, so a confusion signal restates the goal *and* asks
    the same question again rather than leaving the trader with an apology and nothing
    to answer.
    """

    question = conversation.question_text or ""
    if not pending:
        return ConversationalGoal(kind="unknown", pending_question=question)
    threshold = pending.get("threshold_percent")
    return ConversationalGoal(
        kind="scan",
        threshold_percent=(
            f"{float(str(threshold)):g}" if threshold is not None else None
        ),
        pending_question=question,
    )


#: Which question a missing field asks. One map, read by both clarification builders:
#: they held separate copies, so a wording fixed in one still asked the old question
#: from the other.
_CLARIFICATION_KEY_BY_FIELD: Final[dict[str, str]] = {
    "trigger_timeframe": "ask.candle_period",
    "reference_point": "ask.move_reference",
    "comparator": "ask.comparator",
    "threshold": "ask.threshold",
    "universe": "ask.symbol_scope",
    "formula": "ask.rule_mechanic",
    "capability_parameter": "ask.rule_mechanic",
}


def _supported_clarification(
    turn: SetupAgentTurnInput,
    *,
    field_name: str,
    source_seed: str,
) -> ClarificationContract:
    question = localized(
        _CLARIFICATION_KEY_BY_FIELD.get(field_name, "ask.rule_mechanic"),
        language_of(turn.active_language),
    )
    options = localized_options(field_name, language_of(turn.active_language))
    schema_type = "number" if field_name == "threshold" else "string"
    expected: dict[str, object] = {"type": schema_type}
    if options:
        expected["enum"] = options
    digest = hashlib.sha256(
        f"{turn.source_turn_id}:{source_seed}:{field_name}".encode()
    ).hexdigest()[:20]
    question_id = f"clarification_{digest}"
    schema_text = json.dumps(expected, sort_keys=True, separators=(",", ":"))
    return ClarificationContract(
        question_id=question_id,
        question=question,
        reason="One user-controlled choice is still required for this supported rule.",
        target_type="condition_creation",
        target_field=field_name,
        expected_answer_schema=schema_text,
        mutating=True,
        allowed_options=options,
        continuation=workflow_continuation(
            question_id=question_id,
            workflow_id=source_seed,
            step_revision=0,
            current_field=field_name,
            remaining_fields=(),
            allowed_values=list(canonical_values(domain_for_field(field_name))),
            answer_schema=schema_text,
            source_evidence=[turn.message],
            draft=turn.draft,
        ),
    )


def _workflow_step(
    workflow: PendingClarificationWorkflow,
    *,
    language: ConversationLanguage,
    draft: StrategyDraftV2,
    mutating: bool = True,
) -> tuple[PendingClarificationWorkflow, ClarificationContract]:
    """The question for a workflow's current step, and the record that validates it.

    Both are produced by one call, deliberately. Building them apart is exactly how the
    assistant came to display step two while still validating against step one: the
    reply was composed from a freshly derived question, and the durable copy was
    written by a different code path that could be rewound.

    The offered options come from what the domain can execute, so a displayed choice is
    always answerable and always compilable.
    """

    field_name = workflow.current_field
    domain = domain_for_field(field_name)
    values = list(canonical_values(domain))
    shown = list(display_options(domain, values))
    labels = labels_for(domain, shown, language)
    question = localized(
        _CLARIFICATION_KEY_BY_FIELD.get(field_name, "ask.rule_mechanic"), language
    )
    expected: dict[str, object] = {
        "type": "number" if domain is AnswerDomain.PERCENT else "string"
    }
    if labels:
        expected["enum"] = list(labels.values())
    committed = workflow.model_copy(
        update={"canonical_values": values[:64], "offered_values": shown[:8]}
    )
    schema_text = json.dumps(expected, sort_keys=True, separators=(",", ":"))
    contract = ClarificationContract(
        question_id=committed.question_id,
        question=question,
        reason="One user-controlled choice is still required for this supported rule.",
        target_type="condition_creation",
        target_field=field_name,
        expected_answer_schema=schema_text,
        mutating=mutating,
        allowed_options=list(labels.values())[:6],
        workflow_id=committed.workflow_id,
        step_revision=committed.step_revision,
        canonical_values=values[:64],
        # Built here, with the step, so the question and the thing that will apply its
        # answer are one object. Deriving the completion later — from whatever code
        # happened to see the next message — is what sent clarification answers to the
        # planner and let a bare `1h` be re-read as a request with nothing in it.
        continuation=workflow_continuation(
            question_id=committed.question_id,
            workflow_id=committed.workflow_id,
            step_revision=committed.step_revision,
            current_field=field_name,
            remaining_fields=committed.remaining_fields,
            allowed_values=values[:64],
            answer_schema=schema_text,
            source_evidence=committed.source_evidence,
            draft=draft,
            mutating=mutating,
        ),
    )
    return committed, contract


def _new_supported_workflow(
    *,
    workflow_id: str,
    fields: Sequence[str],
    evidence: str,
    language: ConversationLanguage,
    draft: StrategyDraftV2,
) -> tuple[PendingClarificationWorkflow, ClarificationContract]:
    """Open a multi-step question at its first field."""

    ordered = list(dict.fromkeys(fields))
    first = ordered[0]
    skeleton = PendingClarificationWorkflow(
        workflow_id=workflow_id,
        workflow_kind="supported_rule",
        step_revision=0,
        question_id="pending",
        current_field=first,
        remaining_fields=ordered[1:],
        source_evidence=[evidence][:24],
    )
    skeleton = skeleton.model_copy(
        update={"question_id": skeleton.step_question_id(first, 0)}
    )
    return _workflow_step(skeleton, language=language, draft=draft)


def _supported_answer_schema(
    clarification: ClarificationContract,
    metadata: dict[str, object],
) -> dict[str, object]:
    """The answer's *shape*, with no workflow progression in it.

    ``metadata`` is deliberately unused for the schema now: progress belongs to the
    canonical ``completion_contract``, and writing a second copy in here is the exact
    duplication that let the displayed step and the validated step disagree.
    """

    del metadata
    try:
        schema = json.loads(clarification.expected_answer_schema)
    except (TypeError, ValueError):
        schema = {"type": "string"}
    if not isinstance(schema, dict):
        schema = {"type": "string"}
    return clean_answer_shape(schema)


def _supported_completion(
    unresolved_id: str,
    metadata: dict[str, object],
) -> ClarificationCompletionContract:
    """The canonical progress record this requirement will carry from now on."""

    return completion_contract_from_metadata(unresolved_id, metadata)


def _active_supported_unresolved(
    draft: StrategyDraftV2,
    conversation: SetupConversationContext,
) -> UnresolvedFieldV2 | None:
    active_id = conversation.active_question_id
    candidates = [
        item for item in draft.unresolved_fields if item.completion_contract is not None
    ]
    return next((item for item in candidates if item.unresolved_id == active_id), None) or (
        candidates[0] if len(candidates) == 1 else None
    )


def _supported_metadata(item: UnresolvedFieldV2) -> dict[str, object]:
    """One requirement's progress, read from its canonical contract and nowhere else."""

    return metadata_from_completion_contract(item.completion_contract)


def _workflow_from_metadata(
    unresolved: UnresolvedFieldV2,
    metadata: Mapping[str, object],
) -> PendingClarificationWorkflow:
    """Rebuild a canonical workflow record for a draft written before it existed.

    Old sessions kept their progress inside the unresolved field's answer schema. They
    are read once, here, and from then on the canonical record is what advances.
    """

    missing = [str(item) for item in cast(list[object], metadata.get("missing_slots") or [])]
    current = str(metadata.get("next_field") or (missing[0] if missing else "trigger_timeframe"))
    remaining = [item for item in missing if item != current]
    accepted: dict[str, str | int | float | bool | None] = {
        key: cast(str | int | float | bool | None, metadata[key])
        for key in ("trigger_timeframe", "reference_point", "comparator", "threshold")
        if isinstance(metadata.get(key), str | int | float | bool)
    }
    skeleton = PendingClarificationWorkflow(
        workflow_id=unresolved.unresolved_id,
        workflow_kind="supported_rule",
        step_revision=0,
        question_id="pending",
        current_field=current,
        remaining_fields=remaining,
        accepted_values=accepted,
        source_evidence=[
            str(item)
            for item in cast(list[object], metadata.get("evidence_fragments") or [])
        ][:24],
    )
    return skeleton.model_copy(
        update={"question_id": skeleton.step_question_id(current, 0)}
    )


def _answer_segment_text(
    turn: SetupAgentTurnInput,
    envelope: PlannerIntentEnvelope | None,
) -> str:
    """The exact words that answered the question, preferring the planner's span."""

    if envelope is not None and envelope.clarification_answers:
        answer = envelope.clarification_answers[0]
        return next(
            (
                item.exact_source_text
                for item in envelope.segments
                if item.segment_ref == answer.segment_ref
            ),
            turn.message,
        )
    return turn.message


def _read_answer(
    field_name: str,
    message: str,
    *,
    language: ConversationLanguage,
    allowed: Sequence[str] = (),
    offered: Sequence[str] = (),
    new_request: bool = False,
    proposed: str | None = None,
) -> AnswerResolution:
    """Read one answer through the single resolver.

    Every clarification in the product — Scanner scope, alert setup, methodology,
    watchlists, symbols, timeframes, reference points, comparators — arrives here.
    Four private readers used to do this, each knowing a different, smaller set of
    words, so the same answer was accepted in one flow and thrown away in another.
    """

    domain = domain_for_field(field_name)
    values = allowed or canonical_values(domain)
    shown = offered or display_options(domain, values)
    return resolve_active_answer(
        message,
        domain=domain,
        allowed_options=values,
        offered_values=shown,
        display_labels=labels_for(domain, values, language),
        looks_like_new_request=new_request,
        proposed_value=proposed,
    )


def _supported_answer_value(
    field_name: str,
    message: str,
    *,
    language: ConversationLanguage = ConversationLanguage.ENGLISH,
) -> object | None:
    """Backwards-compatible reader: a stored value, or nothing at all.

    Only a fully resolved answer becomes a value. A near miss, an ambiguity or an
    unsupported value all return ``None`` here, and the caller keeps the question —
    which is why the richer typed result is what new callers should use.
    """

    resolution = _read_answer(field_name, message, language=language)
    return resolution.canonical_value if resolution.stores_a_value else None


def _apply_supported_answer(
    metadata: dict[str, object],
    field_name: str,
    value: object,
) -> None:
    if field_name == "trigger_timeframe":
        metadata["trigger_timeframe"] = value
    elif field_name == "reference_point":
        metadata["reference_point"] = value
        metadata["formula"] = (
            FormulaKind.OPEN_TO_CLOSE_PERCENTAGE.value
            if value == "candle_open"
            else FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE.value
        )
    elif field_name == "comparator":
        metadata["comparator"] = value
    elif field_name == "threshold":
        metadata["threshold"] = value
    else:
        metadata[field_name] = value


#: What the rule assembler needs before it can build anything. Named once, and checked
#: before the assembler runs — reaching it without these raised a bare ``KeyError``, and
#: a diagnostic must never become the failure.
_REQUIRED_RULE_FACTS: Final[tuple[str, ...]] = (
    "formula",
    "movement_direction",
    "comparator",
    "threshold",
    "trigger_timeframe",
)


def _confirmed_requirement_paths(
    draft: StrategyDraftV2,
    continuation: object,
) -> frozenset[str]:
    """The one canonical path a confirmed answer just filled, named by the draft itself.

    Read through ``unresolved_target_path`` rather than rebuilt here. Where a value lives
    already had two different answers in this codebase once; a third would be worse.
    """

    wanted = str(getattr(continuation, "unresolved_id", "") or "")
    if not wanted:
        return frozenset()
    return frozenset(
        unresolved_target_path(item)
        for item in draft.unresolved_fields
        if item.unresolved_id == wanted
    )


def _supported_rule_is_assemblable(metadata: Mapping[str, object]) -> bool:
    """Whether every fact the assembler needs is really present."""

    return all(metadata.get(item) is not None for item in _REQUIRED_RULE_FACTS)


def _condition_from_supported_metadata(
    metadata: dict[str, object],
    *,
    source_turn_id: str,
    source_fragment: str,
) -> ConditionNodeV2:
    formula = FormulaKind(str(metadata["formula"]))
    direction = MovementDirection(str(metadata["movement_direction"]))
    comparator = Comparator(str(metadata["comparator"]))
    threshold = float(str(metadata["threshold"]))
    timeframe = str(metadata["trigger_timeframe"])
    # The capability-parameter type has one owner. Writing the union out here is how the
    # two copies in this repo already drifted apart once.
    parameters: dict[str, CapabilityParameterValue] = {
        "formula": (
            "open_to_close"
            if formula == FormulaKind.OPEN_TO_CLOSE_PERCENTAGE
            else "close_to_close"
        ),
        "reference_field": (
            "open" if formula == FormulaKind.OPEN_TO_CLOSE_PERCENTAGE else "close"
        ),
        "current_field": "close",
        "lookback": 1,
        "scale": "percent",
        "closed_only": True,
    }
    return ConditionNodeV2(
        node_type=cast(Any, "condition"),
        source_turn_id=source_turn_id,
        source_fragment=source_fragment,
        movement_direction=direction,
        formula=formula,
        operands=[
            OperandV2(
                role="measured_value",
                kind="market_metric",
                name="percentage_change",
                unit="percent",
                parameters=parameters,
            )
        ],
        operator=comparator,
        threshold=threshold,
        unit="percent",
        trigger_timeframe=cast(Any, timeframe),
        condition_symbols=[
            str(item) for item in cast(list[object], metadata.get("symbols") or [])
        ],
    )


def _supported_workflow_plan(
    turn: SetupAgentTurnInput,
    *,
    source_text: str,
    segment_kind: SegmentKind,
    operation: AuthorizedPatchOperation | list[AuthorizedPatchOperation],
    summary: str,
    question_id: str | None = None,
) -> SetupAgentTurnPlan:
    operations = operation if isinstance(operation, list) else [operation]
    segment_id = operations[0].authorizing_segment_id
    clarification_answers = []
    if question_id is not None:
        from ai_market_monitor.schemas.setup_agent import ClarificationAnswer

        clarification_answers = [
            ClarificationAnswer(
                segment_id=segment_id,
                question_id=question_id,
                answer_text=source_text,
            )
        ]
    return SetupAgentTurnPlan(
        source_turn_id=turn.source_turn_id,
        segments=[
            TurnSegment(
                segment_id=segment_id,
                exact_source_text=source_text,
                start_offset=0,
                end_offset=len(source_text),
                kind=segment_kind,
                action_required=True,
                confidence=1.0,
            )
        ],
        operations=operations,
        strategy_instructions=[
            StrategyInstructionPlan(segment_id=segment_id, intent_summary=summary)
        ],
        clarification_answers=clarification_answers,
        overall_confidence=1.0,
    )


def _promote_known_incomplete_request(
    turn: SetupAgentTurnInput,
    envelope: PlannerIntentEnvelope,
) -> SupportedIncompleteIntent | None:
    """Recover a core percentage request the planner mislabeled as unsupported.

    This is intentionally narrow and deterministic. It never promotes an unknown
    indicator or capability; it only recognizes direction + one percentage threshold,
    which is already a core primitive in the planner contract.
    """

    for unsupported in envelope.unsupported_intents:
        segment = next(
            (item for item in envelope.segments if item.segment_ref == unsupported.segment_ref),
            None,
        )
        if segment is None:
            continue
        source = segment.exact_source_text
        has_percent = bool(re.search(r"%|\bpercent(?:age)?\b|\bpct\b", source, re.I))
        thresholds = re.findall(
            r"(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*(?:%|percent\b|pct\b)",
            source,
            re.I,
        )
        direction = _supported_direction(source)
        if not has_percent or len(thresholds) != 1 or direction not in {"up", "down"}:
            continue
        missing: list[str] = []
        if not extract_timeframes(source):
            missing.append("trigger_timeframe")
        if detect_comparator(source) is None:
            missing.append("comparator")
        # A percentage move still needs a reference point even when ordinary wording
        # makes the mechanic obvious. Ask in plain language rather than exposing a
        # formula-key error.
        missing.append("reference_point")
        return SupportedIncompleteIntent(
            segment_ref=unsupported.segment_ref,
            missing_fields=cast(Any, list(dict.fromkeys(missing))),
        )
    return None


def _supported_request_context(
    turn: SetupAgentTurnInput,
    request: SupportedIncompleteIntent,
    envelope: PlannerIntentEnvelope,
) -> dict[str, object]:
    segment = next(
        (item for item in envelope.segments if item.segment_ref == request.segment_ref),
        None,
    )
    source = segment.exact_source_text if segment is not None else turn.message
    threshold_matches = re.findall(
        r"(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*(?:%|percent\b|pct\b)", source, re.I
    )
    comparator = detect_comparator(source)
    timeframes = extract_timeframes(source)
    explicit_trigger = [
        item for item in timeframes if timeframe_role_is_explicit(source, item, "trigger")
    ]
    other_roles: tuple[Literal["context", "confirmation", "reference"], ...] = (
        "context",
        "confirmation",
        "reference",
    )
    explicit_other_role = any(
        timeframe_role_is_explicit(source, item, role)
        for item in timeframes
        for role in other_roles
    )
    trigger_timeframe = (
        explicit_trigger[0]
        if len(explicit_trigger) == 1
        else timeframes[0]
        if len(timeframes) == 1 and not explicit_other_role
        else None
    )
    reference_point = _supported_answer_value("reference_point", source)
    formula = (
        FormulaKind.OPEN_TO_CLOSE_PERCENTAGE.value
        if reference_point == "candle_open"
        else FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE.value
        if reference_point == "previous_close"
        else None
    )
    return {
        "source_text": source[:500],
        "missing_fields": list(request.missing_fields),
        "capability_key": request.capability_key,
        "movement_direction": _supported_direction(source),
        "threshold": float(threshold_matches[0]) if len(threshold_matches) == 1 else None,
        "unit": "percent" if len(threshold_matches) == 1 else None,
        "comparator": comparator.value if comparator is not None else None,
        "timeframes": timeframes,
        "trigger_timeframe": trigger_timeframe,
        "formula": formula,
        "reference_point": reference_point,
        "symbols": extract_symbols(source),
    }


def _clarification_for_supported_incomplete(
    turn: SetupAgentTurnInput,
    request: SupportedIncompleteIntent,
    envelope: PlannerIntentEnvelope,
) -> ClarificationContract:
    pending = _supported_request_context(turn, request, envelope)
    fields = set(request.missing_fields)
    # Percentage movement in ordinary wording already names the mechanic and value.
    # Ask for the human decision that materially changes its meaning before exposing
    # implementation terms such as formula keys.
    source = str(pending.get("source_text") or "")
    # The direction reader owns the up/down vocabulary. A hand-written list here knew
    # only fifteen English words, so the same sentence in Arabic, French or Spanish was
    # not recognised as a percentage move and got asked a different question.
    percent_movement = bool(
        re.search(r"%|\bpercent(?:age)?\b|\bpct\b", source, re.I)
        and movement_direction(source) in {"up", "down"}
    )
    priority = [
        "universe",
        "trigger_timeframe",
        "reference_point",
        "comparator",
        "threshold",
        "formula",
        "capability_parameter",
    ]
    if percent_movement and "trigger_timeframe" in fields:
        selected = "trigger_timeframe"
    else:
        selected = next((item for item in priority if item in fields), "formula")
    question = localized(
        _CLARIFICATION_KEY_BY_FIELD.get(selected, "ask.rule_mechanic"),
        language_of(turn.active_language),
    )
    options: list[str] = []
    expected: dict[str, object] = {"type": "string"}
    target_type: ClarificationTargetType = "condition_creation"
    target_field: str | None = selected
    if selected == "universe":
        target_type = "universe"
        options = ["All screened coins", "One coin", "One approved list"]
        expected = {"type": "string", "enum": options}
    elif selected in {"trigger_timeframe", "reference_point", "comparator"}:
        # One authority for every option-based question: what the domain can execute,
        # narrowed to what is worth showing, then worded. Three separate lists lived
        # here before, and the timeframe one disagreed with the compiler's registry.
        domain = domain_for_field(selected)
        shown = display_options(domain)
        options = list(labels_for(domain, shown, language_of(turn.active_language)).values())
        expected = {"type": "string", "enum": options}
    elif selected == "threshold":
        expected = {"type": "number"}
    digest = hashlib.sha256(
        f"{turn.source_turn_id}:supported:{request.segment_ref}:{selected}".encode()
    ).hexdigest()[:20]
    question_id = f"clarification_{digest}"
    schema_text = json.dumps(expected, sort_keys=True, separators=(",", ":"))
    mutating = turn.setup_mode != DraftMode.SCANNER
    continuation: Any
    if target_type == "universe":
        continuation = governed_continuation(
            question_id=question_id,
            step_revision=0,
            target_type=target_type,
            target_field=target_field or "",
            option_key="screened_universe_mode",
            allowed_values=list(canonical_values(AnswerDomain.UNIVERSE_MODE)),
            draft=turn.draft,
            cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        )
    else:
        continuation = workflow_continuation(
            question_id=question_id,
            workflow_id=f"supported_{digest}",
            step_revision=0,
            current_field=target_field or selected,
            remaining_fields=(),
            allowed_values=list(canonical_values(domain_for_field(selected))),
            answer_schema=schema_text,
            source_evidence=[turn.message],
            draft=turn.draft,
            mutating=mutating,
        )
    return ClarificationContract(
        question_id=question_id,
        question=question,
        reason="A supported request is missing one user-controlled choice.",
        target_type=target_type,
        target_field=target_field,
        expected_answer_schema=schema_text,
        mutating=mutating,
        allowed_options=options,
        continuation=continuation,
    )


def _clarification_for_failure(
    turn: SetupAgentTurnInput,
    failure: _PlanFailure,
) -> ClarificationContract:
    """Derive one minimal typed question from a validated requirement failure."""

    path = (failure.target_path or "").removeprefix("payload.")
    references = _planner_references(turn)
    sharia_universe_choice = any(
        marker in failure.details
        for marker in (
            "sharia_preferences:universe_conflict",
            "sharia_preferences:negative_universe_without_alternative",
        )
    )
    if sharia_universe_choice:
        question = "Should this setup use the screened market or one approved watchlist?"
        reason = (
            "A negative preference is usable only when the same instruction selects "
            "the governed alternative."
        )
        target_type: ClarificationTargetType = "sharia_policy"
        target_field = "universe_mode"
        options = ["Screened market", "Approved watchlist"]
        expected = {"type": "string", "enum": options}
    elif path in {"methodology_family", "methodology_identifier"}:
        question = "Which offered Sharia methodology should this setup use?"
        reason = "More than one governed methodology matches that preference."
        target_type = "sharia_policy"
        target_field = "methodology_id"
        options = [item.public_identifier for item in references.methodologies][:6]
        expected = {"type": "string", "enum": options} if options else {"type": "string"}
    elif path == "approved_watchlist_only":
        question = "Which offered approved watchlist should this setup use?"
        reason = "More than one executable owned watchlist matches that preference."
        target_type = "sharia_policy"
        target_field = "approved_watchlist_id"
        options = [item.public_name for item in references.watchlists][:6]
        expected = {"type": "string", "enum": options} if options else {"type": "string"}
    else:
        field = path.removeprefix("condition.") or "rule"
        question = {
            "formula_key": localized("ask.rule_mechanic", language_of(turn.active_language)),
            "comparator": localized("ask.comparator", language_of(turn.active_language)),
            "threshold": localized("ask.threshold", language_of(turn.active_language)),
            "trigger_timeframe": localized("ask.candle_period", language_of(turn.active_language)),
        }.get(field, localized("ask.rule_mechanic", language_of(turn.active_language)))
        reason = "That value is required to compile the rule without guessing."
        target_type = "condition_creation"
        target_field = None
        options = []
        expected_type = (
            "number" if field in {"threshold"} else "integer" if field in {"lookback"} else "string"
        )
        expected = {"type": expected_type}
    digest = hashlib.sha256(
        f"{turn.source_turn_id}:{target_type}:{target_field or path}".encode()
    ).hexdigest()[:20]
    question_id = f"clarification_{digest}"
    schema_text = json.dumps(expected, sort_keys=True, separators=(",", ":"))
    # Built here, at the moment the question is raised, from the failure the server
    # already validated. A compiler-authored question that could not carry a completion
    # used to be shown anyway and its answer handed back to the planner; now the
    # completion exists before the question does, or the question is not asked.
    failure_continuation: Any
    if target_type == "sharia_policy":
        failure_continuation = governed_continuation(
            question_id=question_id,
            step_revision=0,
            target_type=target_type,
            target_field=target_field or "",
            option_key=governed_control_for(target_field or "", "screened_universe_mode"),
            allowed_values=options,
            draft=turn.draft,
            cancellation_policy=CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        )
    else:
        failure_continuation = workflow_continuation(
            question_id=question_id,
            workflow_id=f"failure_{digest}",
            step_revision=0,
            current_field=target_field or path or "rule",
            remaining_fields=(),
            allowed_values=options,
            answer_schema=schema_text,
            source_evidence=[turn.message],
            draft=turn.draft,
        )
    return ClarificationContract(
        question_id=question_id,
        question=question,
        reason=reason,
        target_type=target_type,
        target_field=target_field,
        expected_answer_schema=schema_text,
        mutating=True,
        allowed_options=options,
        continuation=failure_continuation,
    )


def _requires_contextual_composer(
    plan: SetupAgentTurnPlan,
    result: SetupTurnExecutionResult,
) -> bool:
    """Use a second model call only when the evidence cannot answer the turn itself.

    Wording is the one thing a turn can finish without, and the second call is the whole
    difference between a one-call turn and a two-call one. It is worth paying for exactly
    when the user asked something that the before/after diff does not answer.

    A change with no question attached is *not* one of those cases. The deterministic
    summary states what changed from the canonical diff, which is the same fact the
    composer would have to cite anyway — and it states it without a second wait.
    """

    asks_a_question = {
        SegmentKind.USER_QUESTION,
        SegmentKind.PRODUCT_QUESTION,
        SegmentKind.EXPLANATION_REQUEST,
        SegmentKind.UNSUPPORTED_REQUEST,
    }
    return bool(
        plan.questions_to_answer
        or any(item.kind in asks_a_question for item in plan.segments)
        or any(item.kind in {"explain_refusal", "answer_question"} for item in plan.response_points)
        or plan.unsupported_segments
        or result.safe_errors
    )


def deterministic_summary(
    result: SetupTurnExecutionResult,
    *,
    language: ConversationLanguage | str = ConversationLanguage.ENGLISH,
) -> str:
    """A factual reply built only from what the server did.

    Used when composing fails after a successful execution. Plain, not templated
    small talk, and never a claim the result does not support.

    Two things changed here. Every sentence is now localized, because a deterministic
    fallback used to switch an Arabic conversation to English at exactly the moment
    something had already gone wrong. And every sentence carries the proposition it
    asserts, so the same fact rendered by another part of the pipeline collapses into
    one instead of stacking up.

    ``language`` accepts a raw code as well as the enum. Callers hold the conversation
    language as a plain string on the turn, and a summary is the last thing that should
    fail because a caller passed ``"ar"`` instead of the enum member for it.
    """

    tongue = ConversationLanguage.parse(str(language)) or ConversationLanguage.ENGLISH
    parts = list(deterministic_summary_parts(result, language=tongue))
    reconciled = reconcile_reply(parts)
    return reconciled.message or localized("status.no_change", tongue)


def deterministic_summary_parts(
    result: SetupTurnExecutionResult,
    *,
    language: ConversationLanguage | str = ConversationLanguage.ENGLISH,
) -> list[RenderedPart]:
    """The server's own sentences about this turn, each tagged with what it asserts.

    Tagging is what makes duplication visible. The same unsupported requirement used
    to be rendered here, again by ``deterministic_claim_text``, again by a validated
    composer claim, and again as a safe error — four sentences for one fact, because
    nothing compared them. Now they share a :class:`Proposition` and only the most
    authoritative wording survives.
    """

    tongue = ConversationLanguage.parse(str(language)) or ConversationLanguage.ENGLISH
    parts: list[RenderedPart] = []
    if result.applied_instructions:
        for item in result.applied_instructions[:6]:
            parts.append(
                RenderedPart(
                    text=str(item.summary),
                    source=RenderSource.DETERMINISTIC_SUMMARY,
                    proposition=Proposition(
                        subject=str(item.operation_id),
                        predicate="applied",
                        value=str(item.summary),
                    ),
                )
            )
    elif result.status == "no_change":
        parts.append(
            RenderedPart(
                text=localized("status.no_change", tongue),
                source=RenderSource.DETERMINISTIC_SUMMARY,
                proposition=Proposition("turn", "no_change"),
            )
        )
    if result.answered_questions:
        parts.append(
            RenderedPart(
                text="That answered the open question.",
                source=RenderSource.DETERMINISTIC_SUMMARY,
                proposition=Proposition("question", "answered", "true"),
            )
        )
    if result.strategy_mutated:
        # A distinct fact, not a duplicate of anything: the trader needs to know a new
        # version exists, because approval is bound to it.
        parts.append(
            RenderedPart(
                text=localized(
                    "status.draft_version", tongue, version=result.current_version
                ),
                source=RenderSource.DETERMINISTIC_SUMMARY,
                proposition=Proposition(
                    "draft", "version", str(result.current_version)
                ),
            )
        )
    for requirement in result.unsupported_requirements[:3]:
        contract = str(requirement.get("missing_contract", ""))
        parts.append(
            RenderedPart(
                # One plain sentence. The internal contract text names schema fields a
                # beginner has never heard of, so it goes to the operator trace only.
                text=localized("refuse.unsupported", tongue),
                source=RenderSource.DETERMINISTIC_SUMMARY,
                proposition=Proposition(
                    subject="requirement",
                    predicate="unsupported",
                    value=contract,
                    requirement_id=str(requirement.get("key", "")),
                ),
            )
        )
    for message in result.safe_errors[:2]:
        parts.append(
            RenderedPart(
                text=str(message),
                source=RenderSource.SAFE_ERROR,
                proposition=Proposition("turn", "safe_error", str(message)),
            )
        )
    if result.approval_eligible:
        parts.append(
            RenderedPart(
                text=localized("status.preview_ready", tongue),
                source=RenderSource.DETERMINISTIC_SUMMARY,
                proposition=Proposition("draft", "approval_eligible", "true"),
            )
        )
    elif result.approval_status == "invalidated_by_edit":
        # An edit after approval is its own fact, not a version fact. The trader has to
        # know the old approval no longer covers what the draft now says.
        parts.append(
            RenderedPart(
                text=localized("status.reapprove", tongue),
                source=RenderSource.DETERMINISTIC_SUMMARY,
                proposition=Proposition("draft", "approval_status", "invalidated_by_edit"),
            )
        )
    return parts


#: Segment kinds that ask for nothing and change nothing. A turn made only of these is
#: conversation, and conversation is answered by talking back.
_SMALL_TALK_KINDS: Final[frozenset[SegmentKind]] = frozenset(
    {
        SegmentKind.SOCIAL_REPLY,
        SegmentKind.ACKNOWLEDGEMENT_NO_ACTION,
        SegmentKind.CONVERSATIONAL_CONTEXT,
    }
)


def _deterministic_conversation_reply(
    draft: StrategyDraftV2,
    *,
    envelope: PlannerIntentEnvelope,
    language: str = "en",
    message: str = "",
) -> str:
    """Last-resort words for a read-only turn, without unrelated product boilerplate.

    A turn that only chats is answered as chat. It used to fall through to the draft
    status line, so "hope you're well" came back as *Nothing is set up yet* — a status
    report nobody asked for, which reads as a complaint about the person typing.
    """

    kinds = {item.segment_kind for item in envelope.segments}
    if SegmentKind.APPROVAL_INTENT in kinds:
        return localized("status.chat_cannot_approve", language_of(language))
    if envelope.questions_to_answer:
        return localized("status.question_fallback", language_of(language))
    count = _condition_count(draft)
    if kinds and kinds <= _SMALL_TALK_KINDS:
        return _social_reply(message, draft, language_of(language))
    if count == 0:
        return localized("status.nothing_set_up", language_of(language))
    return localized(
        "status.draft_count",
        language_of(language),
        count=count,
        suffix="s" if count != 1 else "",
        version=draft.version,
    )


#: Turns that are thanks or agreement rather than a first hello. Answering "thanks"
#: with "Hey! Good to see you" reads as if the assistant was not listening.
_ACKNOWLEDGEMENTS: Final[frozenset[str]] = frozenset(
    {
        "thanks", "thank you", "ok", "okay", "k", "cool", "great", "nice",
        "got it", "sure", "yes", "no", "bye",
        "شكرا", "تمام", "ماشي", "اوك",
        "merci", "d'accord", "gracias", "vale",
    }
)


def _social_reply(
    message: str,
    draft: StrategyDraftV2,
    language: ConversationLanguage,
) -> str:
    """Words for a turn that asks for nothing.

    A greeting, a thank-you, an "ok". None of them assert anything about the platform
    and none of them can change it, so there is no evidence to check and no reason to
    spend a model call. What matters is that the reply sounds like a person answering,
    and that it never tells someone with three working rules that nothing is set up.
    """

    collapsed = " ".join((message or "").split()).casefold().strip(" .!?،؟")
    if collapsed in _ACKNOWLEDGEMENTS:
        return localized("chat.acknowledged", language)
    count = _condition_count(draft)
    if count:
        return localized(
            "chat.greeting_with_setup",
            language,
            count=count,
            suffix="s" if count != 1 else "",
            version=draft.version,
        )
    return localized("chat.greeting", language)


#: Asking what Scanner or Monitor *is*, or how they differ. Deliberately narrow: any
#: other product question still goes to the model, which answers it from the same
#: server-owned product knowledge.
_SCANNER_VS_MONITOR = re.compile(
    r"\b(?:scanner|monitor|monitoring)\b"
    r"|(?:سكانر|مراقبة)"
    r"|\b(?:escáner|moniteur)\b",
    re.IGNORECASE,
)


def _asks_scanner_vs_monitor(message: str) -> bool:
    return bool(_SCANNER_VS_MONITOR.search(message or ""))


def _too_bare_to_plan(turn: SetupAgentTurnInput, assessment: RequestAssessment) -> bool:
    """Whether planning this turn could not produce a rule, however well it went.

    Deliberately narrow. The deterministic router exists to stop a beginner's first
    sentence from being answered with a refusal, not to take work away from the
    planner. It steps in only when the turn names *no* market to watch and *no* period
    to measure over, and there is nothing on the draft to inherit either — the shape of
    ``create me an alert to alert me when a coin increases 5%``. Anything richer than
    that is a turn the compact planner can read, and it is left alone.
    """

    if turn.draft.condition_ast is not None:
        return False
    if turn.conversation.active_question_id:
        # An answer to our own question belongs to the flow that asked it.
        return False
    return (
        MissingChoice.SYMBOL_SCOPE in assessment.missing
        and MissingChoice.MEASUREMENT_WINDOW in assessment.missing
    )


def _draft_trigger_timeframe(draft: StrategyDraftV2) -> str | None:
    """A measurement period the draft has already settled, if any.

    Read so a follow-up turn is never asked for a period the trader supplied earlier.
    """

    if draft.condition_ast is None:
        return None
    for node in draft.condition_ast.walk():
        if node.node_type is ConditionNodeType.CONDITION and node.trigger_timeframe:
            return str(node.trigger_timeframe)
    return None


def _condition_count(draft: StrategyDraftV2) -> int:
    if draft.condition_ast is None:
        return 0
    return sum(node.node_type == ConditionNodeType.CONDITION for node in draft.condition_ast.walk())


def _condition_labels(
    draft: StrategyDraftV2,
    references: PlannerReferenceContext,
) -> list[dict[str, Any]]:
    """Short, stable labels so the model can refer to a rule the user means."""

    if draft.condition_ast is None:
        return []
    labels: list[dict[str, Any]] = []
    condition_by_id = {
        canonical: reference for reference, canonical in references.condition_ids.items()
    }
    for node in draft.condition_ast.walk():
        if node.node_type != ConditionNodeType.CONDITION:
            continue
        labels.append(
            {
                "condition_ref": condition_by_id[node.node_id],
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


def _boolean_shape(
    draft: StrategyDraftV2,
    references: PlannerReferenceContext,
) -> str:
    condition_by_id = {
        canonical: reference for reference, canonical in references.condition_ids.items()
    }

    def shape(node: Any) -> str:
        if not node.children:
            return condition_by_id.get(node.node_id, "condition")
        return f"{node.node_type.value}(" + ", ".join(shape(child) for child in node.children) + ")"

    return shape(draft.condition_ast) if draft.condition_ast is not None else ""


def _public_sharia_preferences(
    draft: StrategyDraftV2,
    references: PlannerReferenceContext,
) -> dict[str, Any]:
    policy = draft.sharia_policy
    methodology = next(
        (
            item.reference
            for item in references.methodologies
            if item.methodology_id == str(policy.methodology_id)
            and item.methodology_version == policy.methodology_version
        ),
        None,
    )
    watchlist = next(
        (
            item.reference
            for item in references.watchlists
            if item.watchlist_id == str(policy.approved_watchlist_id)
            and item.watchlist_version == policy.approved_watchlist_version
        ),
        None,
    )
    return {
        "universe_choice": policy.universe_mode.value,
        "methodology_ref": methodology,
        "approved_watchlist_ref": watchlist,
        "fail_closed": (
            {item.value for item in policy.allowed_statuses} == {"eligible"}
            and policy.qualification_policy == "exclude"
            and policy.disputed_asset_policy == "exclude"
        ),
    }


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
            "Building and previewing a draft changes nothing in the market and places no orders."
        ),
        "if_unsure": (
            "If a fact is not listed here, say you are not certain and offer to point "
            "the user at support rather than guessing."
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
You are the Hilal Markets Setup Chat planner for a beginner-friendly, Halal
crypto-monitoring product. You read one authenticated user turn and divide it into
segments. You never execute anything: a deterministic server tool applies whatever
survives its own checks. `response_language` is server-owned; all conversational intent
and clarification meaning must stay in that language unless the user explicitly changes it.

DIVIDE THE TURN
A single message can do several things at once — greet, instruct, correct, ask. Split
it into segments. Each segment's exact_source_text must be a substring of the current
message, copied character for character — not a paraphrase, not a normalised copy, not
a quote from an earlier turn. The server searches the real message for that text; if it
is not there, the whole turn is refused. Give each segment only a turn-local
`segment_ref`; the server finds offsets and persisted identity. Never let two actionable
segments cover the same words.

Never force the whole message into one kind. Never discard technical content because
conversation surrounds it. Never turn conversation into a rule.

WHAT YOU MAY PROPOSE
You say what the trader meant. You never write the platform's own records: no ids, no
character positions, no versions, no hashes, no defaults. The server builds all of that.

Every change is one action-specific payload in `semantic_intents`, linked only by the
turn-local `segment_ref`. Do not create intent ids, condition ids, question ids,
versions, hashes or database identities. Only a STRATEGY_INSTRUCTION or a
CLARIFICATION_ANSWER segment may author a change.

Every threshold, timeframe, symbol, comparator, movement_direction, explicit
strategy_bias and formula in an intent must appear in **that segment's own text** — not
merely somewhere in the message. A number written inside a question does not authorize a
rule. The exception is `update_condition`: leave out every field the trader did not
restate and the server inherits it from the rule you name, so `change that to at least
8%` needs only the comparator and the number.

Use one actionable segment for one complete semantic action, not one segment per
grammatical clause. If one new rule is described across adjacent clauses — for example,
one clause supplies its timeframe role and the next supplies its formula or threshold —
the `add_condition` intent must use one exact, contiguous segment that covers all of
those clauses. Never attach a rule to a narrow clause while putting one of that rule's
timeframes, symbols, or threshold in another segment. Separate segments are for
independent actions, not for different pieces of the same rule.

condition_symbols is a symbol-specific restriction, not the draft watchlist. Keep it
empty unless the same segment explicitly names that symbol for the rule. Never copy
symbols from the draft into condition_symbols merely because the watchlist has one.

If a value is not in the segment's words, leave it absent. The server derives and
reconciles any clarification from final canonical requirement state.

Do not propose an `add_condition` that cannot yet form an executable rule. When the
user named a registered/core mechanic but genuinely omitted a required user-controlled
choice, put that segment in `supported_incomplete_intents` and list only the missing
fields. This starts a clarification flow; it is NOT unsupported. Preserve every value
the user supplied and never list it as missing. Reserve `unsupported_intents` only for
a mechanic the supplied capability shortlist and core primitives cannot express at all.
Still return every independent, complete intent in the same turn.

CURRENT-MARKET SCANNER QUESTIONS
When the user asks which coins currently moved up or down by an explicitly stated
percentage, put that segment in `read_only_percentage_scans` regardless of the current
setup mode. The server may ask the user to select Scanner before execution. This is a
read-only on-demand request, not a strategy
instruction, product explanation, or unsupported mechanic. Copy only the stated
movement_direction and positive threshold_percent. Set measurement_window to `24h`
only when the same segment explicitly says 24 hours, one day, 1d, or rolling 24h.
Words such as "now", "currently", or "today" do not choose a measurement window.
Never put the same segment in semantic_intents, supported_incomplete_intents,
questions_to_answer, or unsupported_intents. The server resolves the selected screened
universe, methodology, entitlement, quota, and provider before execution; never guess
those values.

For a registered mechanic, choose a capability_key from capability_shortlist and nothing
else. If no candidate expresses the request exactly, add an entry to
`unsupported_intents` with the trader's own wording.
Never invent a key. Never substitute a mechanic that is merely similar — a near miss
watches the wrong market and looks like success.

For the core primitives listed in core_primitives, use no capability_key at all. When a
rule compares a price directly, say which price in `measured_price_field`; only a price
the trader named.

`required` is true unless the trader called that rule optional. Do not set it to false
because a rule sounds secondary — an optional rule does not have to match for an alert,
which is a different setup from the one they described.

Give every rule inside a multi-rule instruction its own `source_quote`: the exact words
of that one rule. "15m ... AND (1h ... OR NOT 4h ...)" names three timeframes, and
without a per-rule quote nothing shows which timeframe belongs to which rule.

HOW RULES COMBINE
When the trader says how rules combine — with brackets, with AND, with OR, with NOT —
use ONE `replace_boolean_structure` intent that carries the whole expression, and no
separate `add_condition` intents for its parts.

The expression is flat, not nested JSON:

* `condition_leaves` — one entry per rule. Give each a `leaf_ref` you invent for this
  turn ("l1", "l2"), the `segment_ref` its words came from, and a `condition` holding
  that rule's own fields and its own exact `source_quote`.
* `boolean_groups` — one entry per bracket or connector. Give each a `group_ref` you
  invent ("g1"), `operator` of "and", "or" or "not", and `child_refs` naming the
  leaves or groups directly inside it. "not" takes exactly one child; "and" and "or"
  take at least two.
* `root_ref` — the outermost group.

`A AND (B OR C)` is: leaves l1, l2, l3; group g1 = or[l2, l3]; group g2 = and[l1, g1];
root g2. `(A OR B) AND C` is a different expression and must come back differently.

These refs are yours for this turn only. They are never database ids and never appear
again.

Use this ONLY for executable market logic. A watchlist is not an expression: "watch
ETHUSDT, not BTCUSDT" is one `include_symbol` and one `exclude_symbol`. A timeframe
role is not an expression: "1m for context and 1h for the trigger" is two fields on one
rule. The word "and" between two of those is English, not logic. One rule on its own
stays one `add_condition`; never wrap it in a group.

If the trader stated how rules combine but you cannot tell which reading they meant,
do not guess a shape — say so in `unsupported_intents` with their exact words.

CLARIFICATION ANSWERS
If conversation_context has an active_clarification_ref and this turn answers it, record a
clarification_answers entry. An answer resolves that question; it does not become a
new condition. "yes" is not a market rule. Include every grounded intent needed to make
the final value correct. Do not add an operation merely to close the question;
but the server—not that intent—decides whether the requirement is satisfied and closes
it after everything else has been applied.

For an active condition_creation question, a complete explicit supported rule is the
operation that fills the target: propose add_condition. One complete grounded operation
may satisfy every missing slot in that condition requirement; do not emit duplicate
questions or require one resolution operation per slot. The user's latest exact
operator wins over an assumption embedded in an older question. For example, if the
question asked for a minimum but the answer says "at most 0.5%", use lte 0.5; do not
reinterpret it as gte, ask the same question again, or resolve the blocker without
adding the condition. When first asking about a vague word such as "strong", do not
presume minimum or maximum: ask neutrally for the missing threshold and comparator.

SEMANTIC ROLES
Preserve each value in the role stated by the user. Trigger, context, confirmation and
reference timeframes are separate fields. Movement direction and strategy bias are
separate. Threshold, lookback, period and confirmation count are separate. Include and
exclude are opposite symbol actions. Never assign a role from timeframe size, token
order, proximity alone or market convention. Context is metadata for the named
condition unless the user separately states executable contextual logic; do not invent
an EMA, candle count, ratio or extra condition from a context label.

REFERENCES
Use recent_dialogue and conversation_context to resolve "that one", "the second
option", "the one we just added", "make it stricter". Point at the existing
condition_ref rather than rebuilding the rule. For undo or restore language, use
`restore_owned_version`; the server, not you, resolves and verifies the history target.

target_reference names a rule that ALREADY EXISTS in draft.conditions. Leave it null
when you are creating a new rule — there is no id for a rule that does not exist yet. To
change an existing rule use `update_condition` with its offered reference; to delete one
use `remove_condition`. An unoffered reference is refused.

SHARIA PREFERENCES
The planner may identify only an explicit user preference with
`set_sharia_preferences`: a stated methodology family or public identifier, screened
assets only, an approved watchlist only, or a fail-closed preference. Choose only from
the compact governed shortlist. Never create policy records, methodology UUIDs or
versions, watchlist database ids, governance decisions, screening results, rulings,
evidence conclusions, publication state, or asset statuses. Asking whether an asset is
halal is a question, not a policy change. Only governed server services resolve policy
identity or asset status.

DIRECTION
movement_direction is up, down, neutral or not_applicable. strategy_bias is long,
short or neutral. Leave strategy_bias absent unless the trader explicitly states long,
short or neutral; the server owns the canonical neutral default. A falling market does
not imply short, and a rising market does not imply long. Likewise, "bullish"
authorizes movement_direction=up only; it never authorizes strategy_bias=long.

For directional percentage rules, keep the user's comparator and positive magnitude
exactly as stated. "Bearish move of at least 2.5%" means movement_direction=down,
operator=gte and threshold=2.5. Never encode direction by negating the threshold or
flipping the comparator (for example, never rewrite it as lte -2.5). When asking what
"strong" means, ask for a comparator and positive percent magnitude without suggesting
a signed negative convention.

COMPARISONS
Copy the comparison the trader wrote, including whether it includes the number itself.

  at most / no more than / not more than / up to / not exceeding / capped at / <=  -> lte
  at least / no less than / not less than / minimum of / >=                        -> gte
  below / less than / strictly below / <                                           -> lt
  above / greater than / strictly above / >                                        -> gt
  exactly / equal to / =                                                           -> eq

"At most 1%" includes 1% and "below 1%" does not; a monitor built from the wrong one
stays silent on the exact move the trader asked to see. The server checks your answer
against the trader's own words and the words win, so guessing here only wastes a turn.

APPROVAL
You may record approval_intent. You can never approve. Approval happens only through
the authenticated Review and approve control.

OUTPUT
Always return the segments. Add semantic_intents only when the turn asks the server to
change or re-check something. When the turn is purely conversational and nothing needs
applying, return no intents. Put the segment_ref of every user or product question in
`questions_to_answer`. Never claim anything changed; that is decided after you.
"""


#: Appended for the one retry allowed when an answer could not be read at all.
_SHAPE_RETRY_NOTE = """\

YOUR LAST ANSWER DID NOT MATCH THE REQUIRED SHAPE
Return the same reading again, in exactly the shape the schema describes, and nothing
else. Do not change what you understood; only the shape was wrong.
"""


_REPAIR_INSTRUCTIONS = """\
You correct one Hilal Markets Setup Chat reading, before anything is saved.

You are given only the invalid semantic intent, its verified exact source segment, at
most one relevant existing value, a sanitized code/path, and the minimum turn-local
references. Return the **smallest** correction that would make it valid—not a new
reading.

Each correction names the server-assigned `intent_ref`, one `target_path`, the supplied
`validation_code`, and one permitted `repair_kind`:

- remove_intent
- remove_field
- replace_with_grounded_value
- relink_source_segment
- inherit_existing_value
- correct_semantic_role
- replace_target_reference
- preserve_as_unsupported

`target_path` is a field name, or `condition.<field>` for a rule's field. You cannot
change which rules exist or how they are joined; drop the intent instead.

Every replacement must be authorized by the verified source segment itself, not merely
by words elsewhere in the complete message.

Never add a symbol, timeframe, semantic role, formula, movement direction, strategy
bias, comparator, threshold, unit, lookback, capability or Sharia preference that the
trader did not write. You may remove, relink or correct an already-grounded Sharia
preference, but may never introduce a new one. Never replace an unknown capability with
a nearby one. Never approve, activate, create policy identity or assign Sharia status.

If the reading cannot be corrected honestly, set cannot_repair to true and return no
corrections. Keeping the blocker open is the right answer; inventing a value is not.

`validation.paths` lists every field this failure named. Return one correction for
each of them that you can prove from the verified source segment. Correcting only the
first one leaves the turn failing for the same reason with the correction already
spent.
"""


_TOPOLOGY_REPAIR_INSTRUCTIONS = """\
The rules in this Hilal Markets setup were understood correctly. Only the way they were
joined together was wrong. Fix the arrangement and nothing else.

You are given the trader's message, the rules already understood — each with the exact
words it came from and a `leaf_ref` — and the arrangement that was refused.

Return:

* `existing_leaf_refs` — exactly the leaf_refs you were given. Not a subset, not one
  more. You may not add or remove a rule here.
* `groups` — one entry per bracket or connector, each with a `group_ref` you invent
  ("g1"), an `operator` of "and", "or" or "not", `child_refs` naming the leaves or
  groups directly inside it, and the exact words of the message it comes from.
  "not" takes exactly one child; "and" and "or" take at least two.
* `root_ref` — the outermost group.

`A AND (B OR C)` is: g1 = or[l2, l3], g2 = and[l1, g1], root g2.
`(A OR B) AND C` is: g1 = or[l1, l2], g2 = and[g1, l3], root g2.
These are different setups and must stay different.

You cannot add a rule, a symbol, a timeframe, a comparator, a threshold, or a Sharia
preference, and you cannot approve anything. Those fields are not in this contract.

If the trader's words support more than one arrangement, set cannot_repair to true.
Asking them which one they meant is correct; guessing is not.
"""


_COMPOSER_INSTRUCTIONS = """\
You write the final assistant message for one Hilal Markets Setup Chat turn.

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

Split what you write into two fields.

conversational_text is anything that asserts nothing about the platform: greeting the
user, acknowledging what they said, offering to help next. It needs no evidence.

factual_claims is every statement of fact, one entry each. Each entry carries both the
sentence and the proposition behind it:

- claim_id        a short label, unique inside this reply
- claim_type      which family of fact it is (below)
- subject_type    what the fact is about
- subject_id      one id from citable_evidence_ids — the thing the fact is about
- predicate       what is being said about it, from the list below
- asserted_value  the exact value being stated
- text            the sentence itself, in the user's language
- evidence_ids    every id this rests on, including subject_id

The server compares subject_id, predicate and asserted_value with its own record. A
sentence whose proposition does not match is dropped, whatever it says.

Predicates by subject:

- operation:...   symbol_included, symbol_excluded, symbol_include_removed,
                  symbol_exclude_removed, condition_added, condition_removed,
                  condition_updated, threshold_changed, timeframe_changed,
                  operator_changed, direction_changed, formula_changed,
                  sharia_policy_changed, market_scope_changed, mode_changed,
                  applied, operation_kind
- condition:...   threshold_equals, timeframe_equals, operator_equals,
                  direction_equals, formula_equals, unit_equals, exists
- status:...      equals
- approval:...    equals
- universe:...    contains, count_equals, equals
- screening:...   contains, count_equals, equals
- preflight:...   contains, count_equals, equals
- unresolved:..., unsupported:...  exists, question_equals,
                  missing_contract_equals
- product:...     states

Match the evidence family to the claim:

- mutation           `operation:...` — only ids in net_effect_of_each_operation whose
                     net_effect is `effective`. A step that was undone or replaced later
                     in the same turn did not happen, so it has no id and cannot be
                     claimed.
- readiness          all four of `status:compile`, `status:screening`, `status:provider`
                     and `status:approval_eligible`. Readiness is every gate, not one.
- approval           `approval:status`
- condition_explanation  `condition:...`
- universe           `screening:...` or `universe:...`
- provider           `provider:...` or `preflight:...`
- open_item          `unresolved:...` or `unsupported:...`
- product_fact       `product:...`

There is no message field. The server joins conversational_text and the claims that pass
validation, in that order, and that is what the user reads. So put every fact in a claim:
a fact left out of factual_claims is a fact the user never sees. A claim you cannot
support costs the user your wording, not the truth — the server states the fact plainly
from its own record instead.

Write for a beginner in the exact `response_language` supplied by the server. Do not
switch languages unless the current user turn explicitly requested it. Use short
sentences, everyday words, no field names, no error-template phrasing, and no bullet
lists unless results genuinely need them. For an ordinary setup turn use at most four
short sentences and one clarification. Each claim's text must read as a whole sentence.

Write no clarification question in any field. To ask one, set selected_clarification_id
to the question_id of exactly one entry in allowed_clarifications. The server appends
that contract's canonical question after validating the id. Ask nothing when
allowed_clarifications is empty, and never ask the user to describe their setup when they
already have.

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
    service_tier = str(usage.get("_setup_service_tier") or "default")
    pricing = (
        settings.openai_fast_model_pricing_usd_per_million.get(model)
        if service_tier in {"fast", "priority"}
        else settings.openai_model_pricing_usd_per_million.get(model)
    ) or {}
    return (
        input_tokens * float(pricing.get("input", 0))
        + output_tokens * float(pricing.get("output", 0))
    ) / 1_000_000


def _record_cost_telemetry(
    telemetry: TurnTelemetry,
    usage: dict[str, Any],
    settings: Settings,
    model: str,
) -> None:
    """Store one combined cost reading using the estimator that guards the turn."""

    telemetry.notes["combined_estimated_cost_usd"] = round(
        float(usage.get("_setup_reserved_cost_usd") or 0.0), 9
    )
    actual = float(usage.get("_setup_combined_actual_cost_usd") or 0.0)
    if actual <= 0:
        actual = _estimated_usage_cost(usage, settings, model)
    telemetry.notes["combined_actual_cost_usd"] = round(actual, 9)
    telemetry.notes["planner_attempt_count"] = int(usage.get("_setup_planner_attempts") or 0)
    telemetry.notes["planner_repair_attempt_count"] = int(usage.get("_setup_repair_attempts") or 0)
    telemetry.notes["planner_repair_success_count"] = int(usage.get("_setup_repair_successes") or 0)


#: Failures where the provider never gave a usable answer at all: the connection,
#: the timeout budget, or the provider's own status code. Nothing about the
#: customer's request is wrong in any of these.
#:
#: Shared with the error envelope so a customer is told the same thing the circuit
#: breaker believes. Reported as the step that happened to be running, an outage
#: read as "we could not read your rules", which is both false and alarming.
PROVIDER_FAILURE_CODES: frozenset[str] = frozenset(
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

_CIRCUIT_FAILURE_CODES = PROVIDER_FAILURE_CODES

#: Failures where the answer arrived but could not be read at all. There are no intents
#: to correct, so the only recovery is one more attempt at the same compact contract.
_UNPARSEABLE_ANSWER_CODES = frozenset(
    {"TARGET_INVALID_JSON", "TARGET_SCHEMA_VALIDATION", "TARGET_EMPTY_RESPONSE"}
)
_REPAIRABLE_VALIDATION_CODES = frozenset(
    {
        # This is the sole canonical gate whose structured details name an
        # operation, canonical field and grounding result. Generic PATCH_REJECTED,
        # authorization, span, condition-target and semantic failures can contain
        # operation ids, but they do not prove one model-owned field caused them.
        "VALUE_NOT_GROUNDED",
    }
)


def _answer_did_not_parse(exc: StructuredCallError) -> bool:
    return exc.code in _UNPARSEABLE_ANSWER_CODES and not exc.retryable


def _sanitized_validation_paths(details: tuple[str, ...]) -> list[str]:
    """Keep codes/paths, never raw exception strings or model-authored prose."""

    cleaned: list[str] = []
    for detail in details[:12]:
        tokens = [
            token
            for token in re.split(r"[:\s]+", str(detail))
            if re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,160}", token)
        ]
        if tokens:
            cleaned.append(":".join(tokens[:3]))
    return cleaned


def _authoritative_source_segments(
    message: str,
    candidate: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Retain only candidate spans the server can locate in the original text."""

    plan = candidate.get("plan") if isinstance(candidate, dict) else None
    raw_segments = plan.get("segments") if isinstance(plan, dict) else None
    authoritative: list[dict[str, Any]] = []
    for item in raw_segments if isinstance(raw_segments, list) else []:
        if not isinstance(item, dict):
            continue
        text = item.get("exact_source_text")
        if not isinstance(text, str) or not text or text not in message:
            continue
        authoritative.append(
            {
                "segment_id": str(item.get("segment_id") or "")[:80],
                "exact_source_text": text,
                "kind": str(item.get("kind") or "")[:80],
            }
        )
    return authoritative


def _counts_toward_circuit(exc: StructuredCallError) -> bool:
    """Only routed provider availability failures affect the breaker."""

    return exc.code in _CIRCUIT_FAILURE_CODES


def compose_final_reply(
    reply: SetupAgentReply,
    ledger: EvidenceLedger,
    *,
    owes_a_fact: bool,
    fallback_message: str,
) -> ComposedReply:
    """Build one concise reply and deduplicate facts by authoritative proposition."""

    validated = validate_claims(list(reply.factual_claims), ledger)
    accepted_raw = [item for item in validated if item.accepted]
    refused = [item for item in validated if not item.accepted]
    accepted: list[Any] = []
    proposition_keys: set[tuple[str, str, str]] = set()
    text_keys: set[str] = set()
    for item in accepted_raw:
        proposition = (
            str(item.subject_id or ""),
            str(item.predicate or ""),
            " ".join(str(item.asserted_value or "").casefold().split()),
        )
        text_key = " ".join(item.text.casefold().split())
        if proposition in proposition_keys or text_key in text_keys:
            continue
        proposition_keys.add(proposition)
        text_keys.add(text_key)
        accepted.append(item)

    conversational = _trimmed(reply.conversational_text)
    parts: list[str] = []
    if conversational:
        parts.append(conversational)
    parts.extend(item.text for item in accepted)

    accepted_open_items = {
        item.subject_id
        for item in accepted
        if str(item.subject_id).startswith(("unsupported:", "unresolved:"))
    }
    # A refused duplicate or malformed claim is diagnostic only.  Falling back merely
    # because *one* claim was refused can restate an already accepted proposition in
    # different words.  Deterministic facts are added only when the turn owes a fact and
    # no validated factual proposition survived.
    if owes_a_fact and not accepted:
        for line in deterministic_claim_text(ledger):
            # Do not render an unsupported/unresolved fact a second time when a validated
            # claim already represented that authoritative evidence item.
            lowered = " ".join(line.casefold().split())
            says_inexpressible = (
                "not expressible exactly" in lowered or "could not express" in lowered
            )
            if accepted_open_items and says_inexpressible:
                continue
            parts.append(line)

    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = " ".join(part.casefold().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(part.strip())
    message = " ".join(deduped).strip() or conversational or fallback_message
    return ComposedReply(
        message_without_question=message[:SETUP_REPLY_MAX_LENGTH],
        conversational_text=conversational[:SETUP_REPLY_MAX_LENGTH],
        factual_claims=[
            FactualClaim(
                claim_id=item.claim_id,
                claim_type=item.claim_type,
                subject_id=item.subject_id,
                predicate=item.predicate,
                asserted_value=item.asserted_value,
                text=item.text,
                evidence_ids=list(item.evidence_ids),
            )
            for item in accepted
        ],
        refused_claims=[
            f"{item.claim_id or item.claim_type}: {item.reason}"[:200] for item in refused
        ][:12],
        selected_clarification_id=reply.selected_clarification_id,
    )


def deterministic_reply(
    message: str,
    *,
    selected_clarification_id: str | None = None,
) -> ComposedReply:
    """A server-authored reply: no model wording, nothing to validate."""

    return ComposedReply(
        message_without_question=message[:SETUP_REPLY_MAX_LENGTH],
        selected_clarification_id=selected_clarification_id,
    )


def _composer_reply_is_grounded(
    reply: ComposedReply,
    result: SetupTurnExecutionResult,
    payload: dict[str, Any],
) -> bool:
    """Reject obvious new facts; deterministic execution stays the authority.

    English-only by construction, and therefore never the primary check — see
    :func:`_rebuild_reply_from_validated_claims`, which runs first and is language
    independent. This stays as an extra filter because it costs nothing and catches
    English wording that happens to cite valid evidence for the wrong sentence.
    """

    message = reply.message_without_question
    lowered = message.casefold()
    if re.search(r"\b(?:halal|haram)\b|(?:حلال|حرام)", lowered):
        return False
    if re.search(r"\b(?:running|activated|active now|live now)\b", lowered):
        return False
    positive_ready = bool(re.search(r"\b(?:is|it's|it is|now|fully|setup is)\s+ready\b", lowered))
    if positive_ready and not result.approval_eligible:
        return False
    positive_approved = bool(re.search(r"\b(?:is|it's|it is|now|has been)\s+approved\b", lowered))
    if positive_approved and result.approval_status != "approved":
        return False
    if re.search(
        r"\b(?:verified|provider available|runtime available)\b", lowered
    ) and result.provider_status not in {"available", "not_required"}:
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
    """The exact schema the planner call sends, for the report and operator tooling."""

    return json.dumps(compact_json_schema(PlannerIntentEnvelope), indent=2, sort_keys=True)
