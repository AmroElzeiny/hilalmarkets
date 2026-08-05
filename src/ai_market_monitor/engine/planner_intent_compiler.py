"""Turn what the trader meant into the record the database will store.

One direction, one place:

    PlannerIntentEnvelope  ->  compile_planner_intents  ->  SetupAgentTurnPlan
                                                            (AuthorizedPatchOperation)

Everything after that is exactly what ran before: authorization, field-level grounding,
dry validation, canonical execution, compilation, screening, preflight. No second
writable path is created — this module produces the *same* operations the model used to
produce, and they meet the same gates.

What the server takes back from the model, and why each one is server-owned:

===========================  =========================================================
operation ids                derived from the turn, so two turns saying the same thing
                             cannot collide and a model cannot choose one at all
condition and question ids   canonical identity; a model-invented id points at nothing
source offsets               models cannot count characters; the server searches the
                             real message
provenance inside a node     the turn id and the authorizing span are already known here
registry operands            fixed by the named formula; a wrong shape compiles and then
                             monitors something else
platform defaults            the canonical models already declare them
unchanged inherited fields   an edit restates only what changed; the rest comes from the
                             rule being edited
answer schemas               the type of a slot comes from the slot's own declaration
===========================  =========================================================

Sanitation is deterministic and typed. Five classes, each with a fixed decision about
whether asking the model again could possibly help — because a repair call that cannot
succeed is a paid call that ends in the same failure.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Literal

from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    ShariaAssetStatus,
    ShariaUniverseMode,
)
from ai_market_monitor.engine.boolean_expression import BooleanNode
from ai_market_monitor.engine.boolean_topology import (
    BooleanTopology,
    BooleanTopologyError,
    TopologyComparison,
    compare_topology,
    parse_stated_topology,
    validate_boolean_topology,
)
from ai_market_monitor.engine.capability_shortlist import CapabilityShortlist
from ai_market_monitor.engine.operator_authority import (
    OperatorNormalizationKind,
    normalize_stated_comparator,
)
from ai_market_monitor.engine.planner_references import (
    EMPTY_PLANNER_REFERENCES,
    PlannerReferenceContext,
    semantic_key,
)
from ai_market_monitor.engine.semantic_grounding import (
    grounds_boolean,
    grounds_direction,
    grounds_formula,
    grounds_number,
    grounds_operator,
    grounds_strategy_bias,
    grounds_symbol,
    grounds_text_value,
    grounds_timeframe,
    grounds_timeframe_role,
    grounds_unit,
)
from ai_market_monitor.engine.setup_failure_taxonomy import SetupFailureClass
from ai_market_monitor.engine.turn_fragments import extract_timeframes
from ai_market_monitor.schemas.planner_intent import (
    AddConditionPayload,
    BooleanStrategyIntent,
    BooleanTopologyRepair,
    CapabilityParameterIntent,
    CapabilityParameterIntentValue,
    ConditionIntent,
    PlannerIntentEnvelope,
    RemoveConditionPayload,
    ReplaceBooleanPayload,
    RestoreSnapshotPayload,
    SemanticAction,
    SemanticIntent,
    SetExchangePayload,
    SetMarketTypePayload,
    SetModePayload,
    SetNamePayload,
    SetQuoteAssetPayload,
    ShariaPreferencePayload,
    SymbolPayload,
    UpdateConditionPayload,
)
from ai_market_monitor.schemas.setup_agent import (
    ACTIONABLE_SEGMENT_KINDS,
    SetupAgentTurnPlan,
)
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    FormulaKind,
    MovementDirection,
    StrategyBias,
    StrategyDraftV2,
)

SemanticTimeframeRole = Literal["trigger", "context", "confirmation", "reference"]
_TIMEFRAME_ROLES: Final[tuple[SemanticTimeframeRole, ...]] = (
    "trigger",
    "context",
    "confirmation",
    "reference",
)


class SemanticIntentOutcome(StrEnum):
    DETERMINISTIC_INTENT_NORMALIZATION = "DETERMINISTIC_INTENT_NORMALIZATION"
    SEMANTIC_INTENT_REPAIR_REQUIRED = "SEMANTIC_INTENT_REPAIR_REQUIRED"
    USER_INFORMATION_REQUIRED = "USER_INFORMATION_REQUIRED"
    UNSUPPORTED_REQUIREMENT = "UNSUPPORTED_REQUIREMENT"
    COMPILER_INVARIANT_VIOLATION = "COMPILER_INVARIANT_VIOLATION"
    NON_RECOVERABLE_FAILURE = "NON_RECOVERABLE_FAILURE"


SANITATION_CLASSES: Final[dict[str, SemanticIntentOutcome]] = {
    "INTENT_SEGMENT_NOT_IN_MESSAGE": SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED,
    "INTENT_TARGET_UNKNOWN": SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED,
    "INTENT_VALUE_UNREADABLE": SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED,
    # A planner omission is a model mistake with a named field, not an internal
    # compiler fault. Reporting several of them as COMPILER_INVARIANT_VIOLATION is
    # what made an ordinary sentence unanswerable in evaluator runs 10 and 11: the
    # class is terminal, so the turn ended in HTTP 422 with no repair and no
    # question, and the trader could only send the same words again.
    "PLANNER_SEMANTIC_OMISSION": SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED,
    "SOURCE_ASSOCIATION_MISMATCH": SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED,
    "BOOLEAN_TOPOLOGY_MISSING": SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED,
    "BOOLEAN_TOPOLOGY_AMBIGUOUS": SemanticIntentOutcome.USER_INFORMATION_REQUIRED,
    "INTENT_INCOMPLETE": SemanticIntentOutcome.USER_INFORMATION_REQUIRED,
    "INTENT_NOT_PERMITTED": SemanticIntentOutcome.NON_RECOVERABLE_FAILURE,
    "SHARIA_PREFERENCE_AMBIGUOUS": SemanticIntentOutcome.USER_INFORMATION_REQUIRED,
    "SHARIA_PREFERENCE_UNAVAILABLE": SemanticIntentOutcome.NON_RECOVERABLE_FAILURE,
    "SHARIA_FAIL_OPEN_UNSUPPORTED": SemanticIntentOutcome.UNSUPPORTED_REQUIREMENT,
    "UNSUPPORTED_REQUIREMENT": SemanticIntentOutcome.UNSUPPORTED_REQUIREMENT,
    "COMPILER_INVARIANT_VIOLATION": SemanticIntentOutcome.COMPILER_INVARIANT_VIOLATION,
}

#: The sanitation class each compiler code reports as, for the typed taxonomy the
#: turn persists. One table, so a code can never mean one thing to the compiler and
#: another to the record an operator reads.
FAILURE_CLASS_FOR_CODE: Final[dict[str, SetupFailureClass]] = {
    "TARGET_INVALID_JSON": SetupFailureClass.PLANNER_SCHEMA_INVALID,
    "TARGET_EMPTY_RESPONSE": SetupFailureClass.PLANNER_SCHEMA_INVALID,
    "TARGET_SCHEMA_VALIDATION": SetupFailureClass.PLANNER_SCHEMA_INVALID,
    "INTENT_SEGMENT_NOT_IN_MESSAGE": SetupFailureClass.SOURCE_ASSOCIATION_MISMATCH,
    "SOURCE_ASSOCIATION_MISMATCH": SetupFailureClass.SOURCE_ASSOCIATION_MISMATCH,
    "INTENT_TARGET_UNKNOWN": SetupFailureClass.PLANNER_VALUE_MISMATCH,
    "INTENT_VALUE_UNREADABLE": SetupFailureClass.PLANNER_VALUE_MISMATCH,
    "PLANNER_SEMANTIC_OMISSION": SetupFailureClass.PLANNER_SEMANTIC_OMISSION,
    "BOOLEAN_TOPOLOGY_MISSING": SetupFailureClass.BOOLEAN_TOPOLOGY_MISSING,
    "BOOLEAN_TOPOLOGY_AMBIGUOUS": SetupFailureClass.BOOLEAN_TOPOLOGY_AMBIGUOUS,
    "INTENT_INCOMPLETE": SetupFailureClass.USER_INFORMATION_REQUIRED,
    "INTENT_NOT_PERMITTED": SetupFailureClass.NON_RECOVERABLE_FAILURE,
    "SHARIA_PREFERENCE_AMBIGUOUS": SetupFailureClass.USER_INFORMATION_REQUIRED,
    "SHARIA_PREFERENCE_UNAVAILABLE": SetupFailureClass.NON_RECOVERABLE_FAILURE,
    "SHARIA_FAIL_OPEN_UNSUPPORTED": SetupFailureClass.UNSUPPORTED_REQUIREMENT,
    "UNSUPPORTED_REQUIREMENT": SetupFailureClass.UNSUPPORTED_REQUIREMENT,
    "VALUE_NOT_GROUNDED": SetupFailureClass.GROUNDING_MISMATCH,
    "COMPILER_INVARIANT_VIOLATION": SetupFailureClass.COMPILER_INVARIANT_VIOLATION,
}


def failure_class_for_code(code: str) -> SetupFailureClass:
    """The typed class one compiler or canonical code belongs to."""

    if code in FAILURE_CLASS_FOR_CODE:
        return FAILURE_CLASS_FOR_CODE[code]
    if code.startswith("TARGET_") or code in {
        "TURN_DEADLINE_EXCEEDED",
        "SETUP_AGENT_COST_LIMIT",
        "SETUP_AGENT_MODEL_PRICING_UNAVAILABLE",
    }:
        return SetupFailureClass.PROVIDER_FAILURE
    return SetupFailureClass.CANONICAL_VALIDATION_FAILURE


class IntentCompileError(ValueError):
    """One turn's intents could not be turned into canonical operations."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: tuple[str, ...] = (),
        intent_ref: str | None = None,
        target_path: str | None = None,
        target_paths: Sequence[str] = (),
        segment_ref: str | None = None,
    ) -> None:
        super().__init__(message)
        if code not in SANITATION_CLASSES:
            raise ValueError(f"unknown sanitation class: {code}")
        self.code = code
        self.details = details
        self.outcome = SANITATION_CLASSES[code]
        self.intent_ref = intent_ref
        # Several fields can be omitted from one rule at once, and each is
        # independently provable from the trader's own words. Carrying only the first
        # one is what made the second omission look like an internal fault.
        named = [*(target_paths or ()), *((target_path,) if target_path else ())]
        paths = tuple(dict.fromkeys(named))
        self.target_paths = paths
        self.target_path = paths[0] if paths else None
        self.segment_ref = segment_ref

    @property
    def failure_class(self) -> SetupFailureClass:
        return failure_class_for_code(self.code)

    @property
    def repairable(self) -> bool:
        return self.outcome == SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED


@dataclass(frozen=True, slots=True)
class IntentCompilation:
    """The canonical plan, plus what the server decided on the model's behalf."""

    plan: SetupAgentTurnPlan
    #: Server-owned choices worth showing an operator: inherited fields, derived
    #: identities, operands filled from a formula contract.
    derivations: tuple[str, ...] = field(default_factory=tuple)
    outcome: SemanticIntentOutcome = SemanticIntentOutcome.DETERMINISTIC_INTENT_NORMALIZATION
    operation_intent_refs: dict[str, str] = field(default_factory=dict)
    intent_segments: dict[str, str] = field(default_factory=dict)
    #: The structure the trader stated against the structure that was compiled, when
    #: they stated one. Approval eligibility requires this to match.
    topology_check: TopologyComparison | None = None


#: Which canonical operation each trader-level action becomes. One table, so an action
#: cannot be half-supported: adding a value here without a builder fails loudly.
_OPERATION_KIND: Final[dict[SemanticAction, str]] = {
    SemanticAction.SET_MODE: "set_fields",
    SemanticAction.SET_NAME: "set_fields",
    SemanticAction.SET_EXCHANGE: "set_fields",
    SemanticAction.SET_QUOTE_ASSET: "set_fields",
    SemanticAction.SET_MARKET_TYPE: "set_fields",
    SemanticAction.SET_SHARIA_PREFERENCES: "set_sharia_policy",
    SemanticAction.INCLUDE_SYMBOL: "add_inclusion",
    SemanticAction.EXCLUDE_SYMBOL: "add_exclusion",
    SemanticAction.REMOVE_INCLUDED_SYMBOL: "remove_inclusion",
    SemanticAction.REMOVE_EXCLUDED_SYMBOL: "remove_exclusion",
    SemanticAction.ADD_CONDITION: "add_condition",
    SemanticAction.UPDATE_CONDITION: "update_condition",
    SemanticAction.REMOVE_CONDITION: "remove_condition",
    SemanticAction.REPLACE_BOOLEAN_STRUCTURE: "replace_groups",
    SemanticAction.RESTORE_OWNED_VERSION: "restore_snapshot",
}

#: Formulas whose left operand is a price the trader named. Percentage and sweep formulas
#: are not here: their operands are fixed by the formula and filled by the canonical
#: binder on ``SetupAgentTurnPlan``, so this module must not build a second copy.
_PRICE_OPERAND_FORMULAS: Final[frozenset[FormulaKind]] = frozenset(
    {
        FormulaKind.PREVIOUS_CANDLE_REFERENCE,
        FormulaKind.FIXED_REFERENCE_LEVEL,
        FormulaKind.LOOKBACK_REFERENCE_LEVEL,
        FormulaKind.CROSS,
    }
)

_BOOLEAN_NODE_TYPE: Final[dict[str, ConditionNodeType]] = {
    "and": ConditionNodeType.AND,
    "or": ConditionNodeType.OR,
    "not": ConditionNodeType.NOT,
}


def _identity(*parts: object) -> str:
    return hashlib.sha256("|".join(str(item) for item in parts).encode()).hexdigest()


def normalize_planner_envelope(envelope: PlannerIntentEnvelope) -> PlannerIntentEnvelope:
    """Remove only byte-identical semantic proposals before operations exist.

    The planner can repeat one interpretation in two output slots. Executing both is
    especially unsafe for conditions because it creates two evaluator journeys and two
    alerts from one trader instruction. This normalization chooses no meaning: equal
    typed payloads under the same segment kind are the same proposal, so the first owns
    the evidence and later copies are removed.
    """

    segment_kind = {item.segment_ref: item.segment_kind.value for item in envelope.segments}

    def unique(items: Sequence[Any], fingerprint: Any) -> list[Any]:
        rows: list[Any] = []
        seen: set[str] = set()
        for item in items:
            key = json.dumps(fingerprint(item), sort_keys=True, separators=(",", ":"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
        return rows

    intents = unique(
        envelope.semantic_intents,
        lambda item: {
            "segment_kind": segment_kind[item.segment_ref],
            "payload": item.payload.model_dump(mode="json", exclude_none=True),
        },
    )
    answers = unique(
        envelope.clarification_answers,
        lambda item: {
            "clarification_ref": item.clarification_ref,
            "answer_text": item.answer_text,
        },
    )
    supported_incomplete = unique(
        envelope.supported_incomplete_intents,
        lambda item: {
            "segment_kind": segment_kind[item.segment_ref],
            "missing_fields": sorted(item.missing_fields),
            "capability_key": item.capability_key,
        },
    )
    read_only_scans = unique(
        envelope.read_only_percentage_scans,
        lambda item: {
            "segment_kind": segment_kind[item.segment_ref],
            "movement_direction": item.movement_direction,
            "threshold_percent": item.threshold_percent,
            "measurement_window": item.measurement_window,
        },
    )
    non_unsupported_segments = {
        *(item.segment_ref for item in supported_incomplete),
        *(item.segment_ref for item in read_only_scans),
    }
    # A known supported mechanic that needs a user choice must never also become a
    # permanent unsupported blocker. Structured models can occasionally populate both
    # slots for the same span; the safer, more specific classification wins.
    unsupported = unique(
        [
            item
            for item in envelope.unsupported_intents
            if item.segment_ref not in non_unsupported_segments
        ],
        lambda item: {
            "segment_kind": segment_kind[item.segment_ref],
            "missing_contract": item.missing_contract,
        },
    )
    questions: list[str] = []
    question_texts: set[str] = set()
    segment_text = {item.segment_ref: item.exact_source_text for item in envelope.segments}
    for reference in envelope.questions_to_answer:
        text = segment_text[reference]
        if text in question_texts:
            continue
        question_texts.add(text)
        questions.append(reference)
    return envelope.model_copy(
        update={
            "semantic_intents": intents,
            "clarification_answers": answers,
            "questions_to_answer": questions,
            "supported_incomplete_intents": supported_incomplete,
            "read_only_percentage_scans": read_only_scans,
            "unsupported_intents": unsupported,
        }
    )


def normalize_planner_segment_boundaries(
    envelope: PlannerIntentEnvelope,
    message: str,
) -> PlannerIntentEnvelope:
    """Remove only duplicated connective text at adjacent segment boundaries.

    Structured planners sometimes quote the conjunction at the end of one span and at
    the start of the next (``"include BTC and"`` / ``"and exclude LTC"``). Both are
    exact substrings, but canonical span validation correctly rejects their overlap.
    Trimming that duplicated connective from the later quote changes no semantic value
    or association. Substantive overlapping text, repeated ambiguous quotes, and nested
    spans remain untouched and fail closed.
    """

    segments = list(envelope.segments)
    positioned: list[tuple[int, int, int]] = []
    for index, segment in enumerate(segments):
        matches = [
            match.start()
            for match in re.finditer(re.escape(segment.exact_source_text), message)
        ]
        if len(matches) != 1:
            return envelope
        start = matches[0]
        positioned.append((start, start + len(segment.exact_source_text), index))

    changed = False
    for (_start, end, _index), (next_start, next_end, next_index) in zip(
        sorted(positioned), sorted(positioned)[1:], strict=False
    ):
        if next_start >= end:
            continue
        overlap_end = min(end, next_end)
        overlap = message[next_start:overlap_end]
        if not _duplicated_boundary_connector(overlap):
            continue
        current = segments[next_index]
        trimmed = current.exact_source_text[len(overlap) :].lstrip()
        if not trimmed or trimmed not in message[overlap_end:next_end]:
            continue
        segments[next_index] = current.model_copy(update={"exact_source_text": trimmed})
        changed = True
    return envelope.model_copy(update={"segments": segments}) if changed else envelope


def _duplicated_boundary_connector(text: str) -> bool:
    normalized = " ".join(text.casefold().split()).strip(" ,.;:()[]{}-/")
    return normalized in {
        "and",
        "or",
        "then",
        "also",
        "but",
        "و",
        "او",
        "أو",
        "وبعدين",
        "w",
        "we",
        "aw",
    }


def _supported_incomplete_target_path(field_name: str) -> str:
    """Map a public missing choice to the semantic field used by clarification logic."""

    return {
        "universe": "universe",
        "formula": "condition.formula_key",
        "comparator": "condition.comparator",
        "threshold": "condition.threshold",
        "trigger_timeframe": "condition.trigger_timeframe",
        "reference_point": "condition.reference_definition",
        "capability_parameter": "condition.capability_parameters",
    }.get(field_name, f"condition.{field_name}")


def compile_planner_intents(
    envelope: PlannerIntentEnvelope,
    *,
    draft: StrategyDraftV2,
    message: str,
    source_turn_id: str,
    shortlist: CapabilityShortlist | None = None,
    history: Sequence[Mapping[str, Any]] = (),
    references: PlannerReferenceContext = PlannerReferenceContext(),
) -> IntentCompilation:
    """Build the canonical plan for one turn, or refuse with a typed class.

    Refusing is a real outcome, not a failure to try. When a value cannot be read or a
    target does not exist, inventing a nearest match would monitor the wrong market
    quietly; a typed refusal keeps the misunderstanding where someone can see it.
    """

    # The production agent normalizes in its named telemetry stage. Keep the compiler
    # boundary idempotently defensive as well so no direct caller can create duplicate
    # conditions or alerts from repeated model output.
    envelope = normalize_planner_segment_boundaries(
        normalize_planner_envelope(envelope),
        message,
    )
    # The production agent normally turns these into one typed clarification before
    # calling the compiler. Keep the compiler boundary defensive for direct callers:
    # incomplete supported intent is user information required, never unsupported and
    # never an empty plan that disagrees with ``requires_tool``.
    if envelope.supported_incomplete_intents:
        incomplete = envelope.supported_incomplete_intents[0]
        field_paths = tuple(
            _supported_incomplete_target_path(item)
            for item in incomplete.missing_fields
        )
        raise IntentCompileError(
            "INTENT_INCOMPLETE",
            "That supported rule needs one more choice before it can be executed.",
            details=tuple(
                f"supported_incomplete:{item}"
                for item in incomplete.missing_fields
            ),
            target_paths=field_paths,
            segment_ref=incomplete.segment_ref,
        )
    derivations: list[str] = []
    envelope = assemble_stated_boolean_structure(envelope, message, derivations)
    segments = _compiled_segments(envelope, message, source_turn_id=source_turn_id)
    operations: list[dict[str, Any]] = []
    operation_intent_refs: dict[str, str] = {}
    intent_segments: dict[str, str] = {}
    existing = _existing_conditions(draft)
    parameter_schemas = _registry_parameter_schemas(shortlist)
    capability_versions = _registry_capability_versions(shortlist)
    #: An actionable span with no intent of its own may carry evidence for an adjacent
    #: condition.  It is never an unbounded message-wide fallback: the helper below
    #: accepts only a contiguous, exact, unclaimed span that grounds a role the planner
    #: has already proposed for that exact condition.
    claimed_semantic_segment_refs = frozenset(
        item.segment_ref for item in envelope.semantic_intents
    )
    condition_segment_refs = frozenset(
        item.segment_ref
        for item in envelope.semantic_intents
        if item.action
        in {
            SemanticAction.ADD_CONDITION,
            SemanticAction.UPDATE_CONDITION,
            SemanticAction.REMOVE_CONDITION,
            SemanticAction.REPLACE_BOOLEAN_STRUCTURE,
        }
    )

    for index, intent in enumerate(envelope.semantic_intents):
        intent_ref = f"intent_{index + 1}"
        _reject_omitted_explicit_role(
            intent,
            intent_ref=intent_ref,
            selected_ref=intent.segment_ref,
            segments=segments,
            claimed_semantic_segment_refs=claimed_semantic_segment_refs,
            condition_segment_refs=condition_segment_refs,
            message=message,
        )
        segment = _condition_evidence_segment(
            intent,
            intent_ref=intent_ref,
            selected_ref=intent.segment_ref,
            segments=segments,
            claimed_semantic_segment_refs=claimed_semantic_segment_refs,
            selected_reference_is_exclusive=(
                sum(item.segment_ref == intent.segment_ref for item in envelope.semantic_intents)
                == 1
            ),
            message=message,
            source_turn_id=source_turn_id,
            derivations=derivations,
        )
        segment_kind = segment["kind"]
        if segment_kind not in {item.value for item in ACTIONABLE_SEGMENT_KINDS}:
            raise IntentCompileError(
                "INTENT_NOT_PERMITTED",
                "Part of that turn tried to change the setup without instructing it.",
                details=(f"{intent_ref}:{segment_kind}:cannot_authorize",),
                intent_ref=intent_ref,
                segment_ref=intent.segment_ref,
            )
        operation_id = f"op_{_identity(source_turn_id, intent_ref)[:20]}"
        try:
            operation = _operation(
                intent,
                intent_ref=intent_ref,
                segment_text=str(segment["exact_source_text"]),
                authorizing_segment_id=str(segment["segment_id"]),
                operation_id=operation_id,
                source_turn_id=source_turn_id,
                draft=draft,
                existing=existing,
                parameter_schemas=parameter_schemas,
                capability_versions=capability_versions,
                history=history,
                references=references,
                derivations=derivations,
            )
        except IntentCompileError as exc:
            # A typed-value reader may fail several stack frames below this loop.  The
            # loop is the last boundary that still knows which compact intent and
            # verified segment owned that value.  Preserve that provenance here so a
            # repair can never be authorized merely from an operation id or an opaque
            # canonical exception.
            if exc.repairable and exc.target_path and not exc.intent_ref:
                raise IntentCompileError(
                    exc.code,
                    str(exc),
                    details=exc.details,
                    intent_ref=intent_ref,
                    target_path=exc.target_path,
                    segment_ref=intent.segment_ref,
                ) from exc
            raise
        if operation is None:
            # An exact confirmation of the already-current governed Sharia policy is
            # useful conversational evidence, but it is not a mutation.  In
            # particular, never emit a canonical no-op merely because the compact
            # planner used a mutation-shaped preference intent.
            continue
        operations.append(operation)
        operation_intent_refs[operation_id] = intent_ref
        intent_segments[intent_ref] = intent.segment_ref

    for index, unsupported in enumerate(envelope.unsupported_intents):
        segment = segments[unsupported.segment_ref]
        seed = _identity(source_turn_id, unsupported.segment_ref, index)
        operations.append(
            {
                "operation_id": f"op_unsupported_{seed[:14]}",
                "authorizing_segment_id": segment["segment_id"],
                "kind": "add_unsupported",
                "missing_contract": unsupported.missing_contract,
            }
        )

    # The compact model names an offered turn-local condition reference.  Once the
    # server has resolved that reference, bind the canonical target to the internal
    # authorizing segment as well as to the operation.  The downstream deletion gate
    # deliberately requires both records to agree; previously update operations were
    # independently field-grounded while a valid remove_condition was rejected because
    # its segment retained ``target_condition_id=None``.  A shared segment pointing at
    # two different rules violates the planner's one-actionable-segment-per-action
    # contract and cannot be resolved by choosing one target.
    segment_by_id = {
        str(segment["segment_id"]): segment for segment in segments.values()
    }
    for operation in operations:
        target_condition_id = operation.get("target_condition_id")
        if not target_condition_id:
            continue
        segment = segment_by_id[str(operation["authorizing_segment_id"])]
        prior_target = segment.get("target_condition_id")
        if prior_target not in (None, target_condition_id):
            raise IntentCompileError(
                "COMPILER_INVARIANT_VIOLATION",
                "One semantic segment was assigned to more than one existing rule.",
                details=("segment:multiple_condition_targets",),
            )
        segment["target_condition_id"] = target_condition_id

    clarification_answers: list[dict[str, Any]] = []
    for answer in envelope.clarification_answers:
        question_id = references.clarification_id(answer.clarification_ref)
        if question_id is None:
            raise IntentCompileError(
                "INTENT_TARGET_UNKNOWN",
                "That answer referred to a question that is not currently open.",
                details=(f"clarification:{answer.clarification_ref}",),
                target_path="clarification_ref",
                segment_ref=answer.segment_ref,
            )
        clarification_answers.append(
            {
                "segment_id": segments[answer.segment_ref]["segment_id"],
                "question_id": question_id,
                "answer_text": answer.answer_text,
                "resolves_question": True,
            }
        )

    questions = [
        str(segments[reference]["exact_source_text"]) for reference in envelope.questions_to_answer
    ]
    response_points = [
        {
            "point": question,
            "kind": "answer_question",
            "segment_id": segments[reference]["segment_id"],
        }
        for reference, question in zip(envelope.questions_to_answer, questions, strict=True)
    ]
    payload: dict[str, Any] = {
        "source_turn_id": source_turn_id,
        "segments": list(segments.values()),
        "operations": operations,
        "strategy_instructions": _strategy_instructions(envelope, segments, references),
        "clarification_answers": clarification_answers,
        "questions_to_answer": questions,
        "clarifications_to_ask": [],
        "approval_intent": (
            {
                "segment_id": segments[envelope.approval_intent.segment_ref]["segment_id"],
                "accompanied_by_material_edit": bool(operations),
            }
            if envelope.approval_intent is not None
            else None
        ),
        "unsupported_segments": [
            {
                "segment_id": segments[item.segment_ref]["segment_id"],
                "missing_contract": item.missing_contract,
                "blocking": True,
            }
            for item in envelope.unsupported_intents
        ],
        "response_points": response_points,
        "overall_confidence": envelope.overall_confidence,
    }
    try:
        # Validating rather than constructing is deliberate: `SetupAgentTurnPlan` already
        # owns condition provenance binding and the registry operand metadata for every
        # core formula. Building those here would be a second copy of a rule that has to
        # stay identical.
        plan = SetupAgentTurnPlan.model_validate(payload)
    except ValueError as exc:
        raise IntentCompileError(
            "COMPILER_INVARIANT_VIOLATION",
            "The semantic compiler produced an invalid internal operation.",
            details=(str(exc)[:400],),
        ) from exc
    if plan.requires_tool != envelope.requires_tool:
        # The two readings must agree, or a turn judged conversational by one and
        # mutating by the other would skip the checks the other side assumed had run.
        raise IntentCompileError(
            "COMPILER_INVARIANT_VIOLATION",
            "The semantic compiler disagreed with the envelope about mutation.",
            details=("requires_tool:disagreement",),
        )
    topology_check = _check_stated_topology(envelope, message=message)
    return IntentCompilation(
        plan=plan,
        derivations=tuple(derivations),
        operation_intent_refs=operation_intent_refs,
        intent_segments=intent_segments,
        topology_check=topology_check,
    )


def assemble_stated_boolean_structure(
    envelope: PlannerIntentEnvelope,
    message: str,
    derivations: list[str],
) -> PlannerIntentEnvelope:
    """Join separate rules the way the trader's own words join them.

    The common failure is not that the model misunderstood the rules. In evaluator run
    20260803T000036Z it read ``A AND (B OR C)`` as three correct, fully grounded rules —
    and then returned them as unrelated ``add_condition`` intents, so the draft joined
    all three with AND and the OR disappeared.

    Nothing about that needs a second model call. The rules are the model's, already
    grounded, and untouched here. The *shape* comes from the trader's own text, read
    deterministically by ``engine/boolean_topology``. This is normalization, not
    invention: no field is read from a new source, no rule is added, and if every
    operand cannot be matched to exactly one rule the envelope is returned unchanged
    and the mismatch is reported as a typed failure instead.
    """

    expected = parse_stated_topology(message)
    if expected is None:
        return envelope
    if any(isinstance(item.payload, ReplaceBooleanPayload) for item in envelope.semantic_intents):
        # The planner already described the structure. Its arrangement is checked
        # against the trader's words later; overwriting it here would hide a real
        # disagreement between what was written and what was understood.
        return envelope
    conditions: list[tuple[int, str, ConditionIntent]] = [
        (index, item.segment_ref, item.payload.condition)
        for index, item in enumerate(envelope.semantic_intents)
        if isinstance(item.payload, AddConditionPayload)
    ]
    if len(conditions) < 2:
        return envelope
    assignments: dict[int, int] = {}
    used: set[int] = set()
    for leaf_index, leaf in enumerate(expected.root.leaves):
        matches = [
            index
            for index, _segment_ref, condition in conditions
            if index not in used and _quote_matches(leaf.text, condition.source_quote or "")
        ]
        if len(matches) != 1:
            return envelope
        assignments[leaf_index] = matches[0]
        used.add(matches[0])
    if len(used) != len(conditions):
        # A rule the stated expression does not mention would silently be dropped from
        # the structure, or joined by an implicit AND nobody wrote.
        return envelope

    leaves: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    counter = [0]
    leaf_refs: dict[int, str] = {}
    by_index = {index: (segment_ref, condition) for index, segment_ref, condition in conditions}
    for leaf_index, intent_index in assignments.items():
        segment_ref, condition = by_index[intent_index]
        reference = f"leaf_{leaf_index + 1}"
        leaf_refs[leaf_index] = reference
        leaves.append(
            {
                "leaf_ref": reference,
                "segment_ref": segment_ref,
                "condition": condition.model_dump(mode="json", exclude_none=True),
            }
        )

    def build(node: BooleanNode) -> str:
        if node.is_leaf:
            index = counter[0]
            counter[0] += 1
            return leaf_refs[index]
        children = [build(child) for child in node.children]
        reference = f"group_{len(groups) + 1}"
        groups.append(
            {
                "group_ref": reference,
                "operator": node.operator,
                "child_refs": children,
                "source_quote": message[expected.span[0] : expected.span[1]][:_QUOTE_LIMIT],
            }
        )
        return reference

    root_ref = build(expected.root)
    structure = {
        "condition_leaves": leaves,
        "boolean_groups": groups,
        "root_ref": root_ref,
    }
    kept = [
        item.model_dump(mode="json", exclude_none=True)
        for index, item in enumerate(envelope.semantic_intents)
        if index not in used
    ]
    first = envelope.semantic_intents[min(used)]
    document = envelope.model_dump(mode="json")
    document["semantic_intents"] = [
        *kept,
        {
            "segment_ref": first.segment_ref,
            "payload": {
                "action": "replace_boolean_structure",
                "boolean_structure": structure,
            },
        },
    ]
    try:
        assembled = PlannerIntentEnvelope.model_validate(document)
    except ValueError:
        # A shape the flat contract refuses is not something to force through. The
        # unchanged envelope will be reported as a topology mismatch, which is honest.
        return envelope
    derivations.append(f"boolean:deterministic_assembly:{expected.shape}")
    return assembled


#: Group quotes are trimmed to the same ceiling every source fragment uses.
_QUOTE_LIMIT: Final[int] = 600


def _quote_matches(operand: str, quote: str) -> bool:
    left = " ".join(operand.split()).casefold().strip(" ,.;:-–—")
    right = " ".join(quote.split()).casefold().strip(" ,.;:-–—")
    return bool(left) and bool(right) and (left in right or right in left)


def _check_stated_topology(
    envelope: PlannerIntentEnvelope,
    *,
    message: str,
) -> TopologyComparison | None:
    """Refuse a turn that lost the combination the trader wrote.

    This is the check that was missing. When a trader writes ``A AND (B OR C)`` and the
    planner returns two unrelated rules, the draft still validates: each rule is
    complete, each is grounded, and the registry joins whatever exists with AND. The
    artifact looks correct and monitors something else.

    So the server reads the stated structure itself, deterministically, and compares.
    A mismatch is a model failure with a named cause, not an internal fault: the leaves
    and operators are all in the trader's message, so one bounded structure-only
    correction can fix it.
    """

    expected = parse_stated_topology(message)
    if expected is None:
        return None
    structures = [
        (f"intent_{index + 1}", intent.segment_ref, intent.payload.boolean_structure)
        for index, intent in enumerate(envelope.semantic_intents)
        if isinstance(intent.payload, ReplaceBooleanPayload)
    ]
    if len(structures) > 1:
        raise IntentCompileError(
            "BOOLEAN_TOPOLOGY_AMBIGUOUS",
            "That turn described the combination of rules more than once.",
            details=(f"boolean_structure:count:{len(structures)}",),
            target_paths=("boolean_structure",),
        )
    intent_ref = structures[0][0] if structures else None
    segment_ref = structures[0][1] if structures else None
    topology: BooleanTopology | None = None
    if structures:
        try:
            topology = validate_boolean_topology(structures[0][2])
        except BooleanTopologyError as exc:
            raise IntentCompileError(
                exc.code,
                str(exc),
                details=exc.details,
                intent_ref=intent_ref,
                target_paths=("boolean_structure",),
                segment_ref=segment_ref,
            ) from exc
    comparison = compare_topology(expected, topology)
    if comparison.matches:
        return comparison
    raise IntentCompileError(
        "BOOLEAN_TOPOLOGY_MISSING",
        "The rules were understood, but not the way they were combined.",
        details=(
            f"stated:{comparison.expected_shape[:160]}",
            f"compiled:{comparison.compiled_shape[:160] or 'no_structure'}",
            *comparison.details,
        ),
        # Attribution only exists when the planner really returned a structure. With no
        # structure there is nothing to rearrange, so no correction is authorized and
        # the turn reports the mismatch instead of paying for a hopeless call.
        intent_ref=intent_ref,
        target_paths=("boolean_structure",),
        segment_ref=segment_ref,
    )


#: Fields a repair delta may touch. Structure is not on the list: changing which rules
#: exist, or how they are joined, is a different reading of the turn, not a correction to
#: one — and a repair that can restructure is a repair that can invent.
_REPAIRABLE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "mode",
        "name",
        "exchange",
        "quote_asset",
        "market_type",
        "methodology_family",
        "methodology_identifier",
        "screened_assets_only",
        "approved_watchlist_only",
        "fail_closed_preference",
        "symbol",
        "target_reference",
        *(f"condition.{name}" for name in ConditionIntent.model_fields),
    }
)


def apply_repair_deltas(
    envelope: PlannerIntentEnvelope,
    deltas: Sequence[Any],
    *,
    message: str,
    validation_code: str,
    invalid_intent_ref: str,
    invalid_target_path: str | None = None,
    invalid_target_paths: Sequence[str] = (),
    references: PlannerReferenceContext = PlannerReferenceContext(),
) -> PlannerIntentEnvelope:
    """Apply minimal corrections to already-parsed intents.

    Every delta has to name words from the real message that permit the replacement, and
    may only touch a field on the allowlist. A delta that fails either test is dropped —
    the turn then keeps its typed blocker open, which is the honest outcome, rather than
    accepting a correction nothing authorised.

    Several fields may be corrected in one envelope when the same failure named several.
    Each is still proved on its own against the same verified span. Accepting only the
    first named field is what made a two-omission turn unrecoverable even after a paid
    correction: the second omission survived, the recompile failed identically, and the
    turn reported the original problem with the money already spent.
    """

    named_paths = [
        *invalid_target_paths,
        *((invalid_target_path,) if invalid_target_path else ()),
    ]
    allowed_paths = {path.removeprefix("payload.") for path in named_paths}
    by_ref = {
        f"intent_{index + 1}": item.model_dump(mode="json")
        for index, item in enumerate(envelope.semantic_intents)
    }
    order = list(by_ref)
    known_segments = {item.segment_ref: item for item in envelope.segments}
    dropped: set[str] = set()
    unsupported = [item.model_dump(mode="json") for item in envelope.unsupported_intents]
    evidence_merges: list[tuple[str, str, str]] = []

    for delta in deltas:
        if delta.validation_code != validation_code or delta.intent_ref != invalid_intent_ref:
            continue
        normalized_delta_path = delta.target_path.removeprefix("payload.")
        if allowed_paths and normalized_delta_path not in allowed_paths:
            continue
        intent = by_ref.get(delta.intent_ref)
        if intent is None:
            continue
        segment_ref = delta.source_segment_ref or str(intent.get("segment_ref") or "")
        segment = known_segments.get(segment_ref)
        if segment is None or segment.exact_source_text not in message:
            continue
        if delta.repair_kind == "remove_intent":
            dropped.add(delta.intent_ref)
            continue
        if delta.repair_kind == "relink_source_segment":
            intent["segment_ref"] = segment_ref
            continue
        if delta.repair_kind == "preserve_as_unsupported":
            unsupported.append(
                {
                    "segment_ref": segment_ref,
                    "missing_contract": segment.exact_source_text,
                }
            )
            dropped.add(delta.intent_ref)
            continue
        path = delta.target_path.removeprefix("payload.")
        if delta.repair_kind == "replace_target_reference" and not path:
            path = "target_reference"
        if path not in _REPAIRABLE_PATHS and not path.startswith(
            "condition.capability_parameters."
        ):
            continue
        payload = dict(intent.get("payload") or {})
        if delta.repair_kind in {"remove_field", "inherit_existing_value"}:
            _remove_payload_path(payload, path)
        elif delta.repair_kind in {
            "replace_with_grounded_value",
            "correct_semantic_role",
            "replace_target_reference",
        }:
            replacement_contract = delta.replacement_value
            replacement = (
                replacement_contract.semantic_value() if replacement_contract is not None else None
            )
            if not _repair_value_is_grounded(
                replacement,
                segment.exact_source_text,
                path=path,
                references=references,
                replacement_kind=(
                    replacement_contract.kind if replacement_contract is not None else None
                ),
            ):
                continue
            if path.startswith("condition.capability_parameters."):
                assert replacement_contract is not None
                _set_capability_parameter_repair(payload, path, replacement_contract)
            else:
                _set_payload_path(payload, path, _replacement_shape_for_path(path, replacement))
            if (
                delta.repair_kind == "correct_semantic_role"
                and segment_ref != str(intent.get("segment_ref") or "")
            ):
                primary_ref = str(intent.get("segment_ref") or "")
                merged_text = _contiguous_repair_evidence(
                    message,
                    known_segments.get(primary_ref),
                    segment,
                )
                if merged_text is None:
                    # The replacement was grounded, but its evidence cannot be joined
                    # to the rest of this intent without crossing unrelated text. Drop
                    # this delta so the repaired envelope fails closed unchanged.
                    _remove_payload_path(payload, path)
                    intent["payload"] = payload
                    continue
                evidence_merges.append((primary_ref, segment_ref, merged_text))
        intent["payload"] = payload

    repaired = envelope.model_dump(mode="json")
    repaired["semantic_intents"] = [
        by_ref[intent_ref] for intent_ref in order if intent_ref not in dropped
    ]
    repaired["unsupported_intents"] = unsupported
    for primary_ref, supporting_ref, merged_text in evidence_merges:
        _merge_repaired_segment_references(
            repaired,
            primary_ref=primary_ref,
            supporting_ref=supporting_ref,
            merged_text=merged_text,
        )
    try:
        return PlannerIntentEnvelope.model_validate(repaired)
    except ValueError as exc:
        raise IntentCompileError(
            "INTENT_VALUE_UNREADABLE",
            "The correction could not be read as a valid change.",
            details=(str(exc)[:400],),
            intent_ref=invalid_intent_ref,
        ) from exc


def apply_topology_repair(
    envelope: PlannerIntentEnvelope,
    repair: BooleanTopologyRepair,
    *,
    invalid_intent_ref: str,
) -> PlannerIntentEnvelope:
    """Rearrange already-grounded rules, and change nothing else.

    The proof is structural, so it is complete. The repaired expression must name
    exactly the leaves that already exist — not a subset, not one more — and every leaf
    keeps its own fields, its own quote and its own segment untouched. There is no path
    through this function that can add a rule, a symbol, a timeframe, a comparator, a
    threshold or a Sharia preference, because none of those are in its input.
    """

    index = _intent_index_number(invalid_intent_ref)
    if index is None or index >= len(envelope.semantic_intents):
        raise IntentCompileError(
            "BOOLEAN_TOPOLOGY_MISSING",
            "The correction named a part of the turn that does not exist.",
            details=(f"intent_ref:{invalid_intent_ref}",),
        )
    intent = envelope.semantic_intents[index]
    payload = intent.payload
    if not isinstance(payload, ReplaceBooleanPayload):
        raise IntentCompileError(
            "BOOLEAN_TOPOLOGY_MISSING",
            "The correction named something that is not a combination of rules.",
            details=(f"intent_ref:{invalid_intent_ref}:not_boolean",),
        )
    current = payload.boolean_structure
    existing_refs = {leaf.leaf_ref for leaf in current.condition_leaves}
    if set(repair.existing_leaf_refs) != existing_refs:
        # A different set of leaves is a different strategy, not a rearrangement.
        raise IntentCompileError(
            "BOOLEAN_TOPOLOGY_MISSING",
            "The correction changed which rules the setup contains.",
            details=(
                f"leaves:before:{','.join(sorted(existing_refs))}",
                f"leaves:after:{','.join(sorted(repair.existing_leaf_refs))}",
            ),
            intent_ref=invalid_intent_ref,
            target_paths=("boolean_structure",),
        )
    repaired_structure = current.model_copy(
        update={"boolean_groups": list(repair.groups), "root_ref": repair.root_ref}
    )
    document = envelope.model_dump(mode="json")
    document["semantic_intents"][index]["payload"]["boolean_structure"] = (
        repaired_structure.model_dump(mode="json", exclude_none=True)
    )
    try:
        return PlannerIntentEnvelope.model_validate(document)
    except ValueError as exc:
        raise IntentCompileError(
            "BOOLEAN_TOPOLOGY_MISSING",
            "The corrected arrangement could not be read as valid logic.",
            details=(str(exc)[:400],),
            intent_ref=invalid_intent_ref,
            target_paths=("boolean_structure",),
        ) from exc


def _intent_index_number(intent_ref: str | None) -> int | None:
    if not intent_ref or not intent_ref.startswith("intent_"):
        return None
    suffix = intent_ref.removeprefix("intent_")
    return int(suffix) - 1 if suffix.isdigit() and int(suffix) >= 1 else None


def _contiguous_repair_evidence(
    message: str,
    primary: Any,
    supporting: Any,
) -> str | None:
    """Join two unique adjacent exact spans after a grounded role repair.

    This never reads or creates the repaired value. The model has already returned the
    value and its own verified supporting segment has grounded it. The server only
    keeps all operations on one non-overlapping exact span so canonical grounding can
    verify the completed intent without broad message-wide evidence.
    """

    if primary is None or supporting is None:
        return None
    actionable = {item.value for item in ACTIONABLE_SEGMENT_KINDS}
    if (
        primary.segment_kind.value not in actionable
        or supporting.segment_kind.value not in actionable
    ):
        return None
    primary_text = primary.exact_source_text
    supporting_text = supporting.exact_source_text
    primary_positions = [match.start() for match in re.finditer(re.escape(primary_text), message)]
    supporting_positions = [
        match.start() for match in re.finditer(re.escape(supporting_text), message)
    ]
    if len(primary_positions) != 1 or len(supporting_positions) != 1:
        return None
    primary_start = primary_positions[0]
    supporting_start = supporting_positions[0]
    primary_end = primary_start + len(primary_text)
    supporting_end = supporting_start + len(supporting_text)
    if primary_end <= supporting_start:
        gap = message[primary_end:supporting_start]
    elif supporting_end <= primary_start:
        gap = message[supporting_end:primary_start]
    else:
        return None
    if not _neutral_evidence_gap(gap):
        return None
    return message[min(primary_start, supporting_start) : max(primary_end, supporting_end)]


def _merge_repaired_segment_references(
    envelope: dict[str, Any],
    *,
    primary_ref: str,
    supporting_ref: str,
    merged_text: str,
) -> None:
    """Collapse repaired adjacent spans and keep every turn-local reference valid."""

    if not primary_ref or not supporting_ref or primary_ref == supporting_ref:
        return
    merged_segments: list[dict[str, Any]] = []
    found_primary = False
    found_supporting = False
    for raw in envelope.get("segments") or []:
        segment = dict(raw)
        reference = segment.get("segment_ref")
        if reference == primary_ref:
            segment["exact_source_text"] = merged_text
            found_primary = True
            merged_segments.append(segment)
        elif reference == supporting_ref:
            found_supporting = True
        else:
            merged_segments.append(segment)
    if not (found_primary and found_supporting):
        return
    envelope["segments"] = merged_segments

    for collection_name in (
        "semantic_intents",
        "clarification_answers",
        "questions_to_answer",
        "unsupported_intents",
    ):
        for item in envelope.get(collection_name) or []:
            if isinstance(item, dict) and item.get("segment_ref") == supporting_ref:
                item["segment_ref"] = primary_ref
    approval_intent = envelope.get("approval_intent")
    if isinstance(approval_intent, dict) and approval_intent.get("segment_ref") == supporting_ref:
        approval_intent["segment_ref"] = primary_ref


def intent_fingerprint(envelope: PlannerIntentEnvelope) -> str:
    """A stable identity for what this turn proposes to change.

    Used to notice that a repair returned the same reading it was asked to fix. Repeating
    a call whose answer is already known to fail is the loop this stops.
    """

    return _identity(envelope.model_dump_json(exclude_none=True))[:32]


def _remove_payload_path(payload: dict[str, Any], path: str) -> None:
    if path.startswith("condition."):
        condition = dict(payload.get("condition") or {})
        condition.pop(path.split(".", 1)[1], None)
        payload["condition"] = condition
    else:
        payload.pop(path, None)


def _set_payload_path(payload: dict[str, Any], path: str, value: Any) -> None:
    if path.startswith("condition."):
        condition = dict(payload.get("condition") or {})
        condition[path.split(".", 1)[1]] = value
        payload["condition"] = condition
    else:
        payload[path] = value


def _replacement_shape_for_path(path: str, value: Any) -> Any:
    """Normalize only the container shape declared by the compact semantic field."""

    list_paths = {
        "condition.context_timeframes",
        "condition.confirmation_timeframes",
        "condition.condition_symbols",
    }
    if path in list_paths and not isinstance(value, list):
        return [value]
    return value


def _set_capability_parameter_repair(payload: dict[str, Any], path: str, value: Any) -> None:
    """Replace one named compact parameter without accepting arbitrary JSON."""

    name = path.rsplit(".", 1)[-1]
    condition = dict(payload.get("condition") or {})
    parameters = [
        item
        for item in list(condition.get("capability_parameters") or [])
        if isinstance(item, dict) and item.get("name") != name
    ]
    parameters.append(value.capability_parameter_payload(name))
    condition["capability_parameters"] = parameters
    payload["condition"] = condition


def _repair_value_is_grounded(
    value: Any,
    source: str,
    *,
    path: str,
    references: PlannerReferenceContext,
    replacement_kind: str | None,
) -> bool:
    if value is None:
        return False
    if path == "target_reference" and isinstance(value, str):
        return bool(
            references.condition_id(value)
            or references.snapshot(value)
            or references.clarification_id(value)
        )
    values = value if isinstance(value, list) else [value]
    if isinstance(value, dict):
        values = list(value.values())
    for item in values:
        if isinstance(item, bool):
            if not grounds_boolean(source, item):
                return False
        elif isinstance(item, (int, float)):
            unit = "plain"
            if "lookback" in path or "period" in path or replacement_kind == "integer":
                unit = "count"
            elif "threshold" in path:
                unit = "percent" if "%" in source or "percent" in source.casefold() else "plain"
            if not grounds_number(source, float(item), unit=unit):
                return False
        elif replacement_kind == "symbol" or "symbol" in path:
            if not grounds_symbol(source, str(item)):
                return False
        elif replacement_kind == "timeframe" or "timeframe" in path:
            role = next(
                (candidate for candidate in _TIMEFRAME_ROLES if candidate in path),
                None,
            )
            if role is not None:
                if not grounds_timeframe_role(source, str(item), role):
                    return False
            elif not grounds_timeframe(source, str(item)):
                return False
        elif path.endswith("comparator") or path.endswith("operator"):
            try:
                if not grounds_operator(source, Comparator(str(item))):
                    return False
            except ValueError:
                return False
        elif path.endswith("movement_direction"):
            try:
                direction = MovementDirection(str(item))
                if direction in {
                    MovementDirection.NEUTRAL,
                    MovementDirection.NOT_APPLICABLE,
                } and not _explicit_neutral_role(source, role="movement_direction"):
                    return False
                if not grounds_direction(source, direction):
                    return False
            except ValueError:
                return False
        elif path.endswith("strategy_bias"):
            try:
                bias = StrategyBias(str(item))
                if bias == StrategyBias.NEUTRAL and not _explicit_neutral_role(
                    source, role="strategy_bias"
                ):
                    return False
                if not grounds_strategy_bias(source, bias):
                    return False
            except ValueError:
                return False
        elif path.endswith("formula_key") or path.endswith("formula"):
            try:
                if not grounds_formula(source, FormulaKind(str(item))):
                    return False
            except ValueError:
                return False
        elif path.endswith("unit"):
            if not grounds_unit(source, str(item)):
                return False
        elif not grounds_text_value(source, str(item)):
            return False
    return True


def semantic_value_is_grounded(
    value: Any,
    source: str,
    *,
    path: str,
    references: PlannerReferenceContext = EMPTY_PLANNER_REFERENCES,
    replacement_kind: str | None = None,
) -> bool:
    """Public, read-only proof used by retry snapshots and repair.

    A persisted ``ValidatedIntentSnapshot`` must never trust a value merely because a
    model returned it.  Both snapshots and repair therefore use this exact field/type
    grounding authority.  It creates no intent, operation, or canonical state.
    """

    return _repair_value_is_grounded(
        value,
        source,
        path=path,
        references=references,
        replacement_kind=replacement_kind,
    )


def _compiled_segments(
    envelope: PlannerIntentEnvelope,
    message: str,
    *,
    source_turn_id: str,
) -> dict[str, dict[str, Any]]:
    """Every span, located in the user's real message by the server.

    Offsets are set here from a search, never from the model. They are re-derived by
    ``_locate_spans`` before validation as well; running the same search twice is
    harmless, and it means the plan this module returns is already self-consistent for
    anything that reads it, such as the operator trace.
    """

    located: dict[str, dict[str, Any]] = {}
    cursor = 0
    missing: list[str] = []
    for index, segment in enumerate(envelope.segments):
        quoted = segment.exact_source_text
        start = message.find(quoted, cursor)
        if start < 0:
            start = message.find(quoted)
        if start < 0:
            missing.append(f"{segment.segment_ref}:{quoted[:60]!r}")
            start = 0
        else:
            cursor = start + len(quoted)
        segment_id = f"segment_{_identity(source_turn_id, segment.segment_ref, index)[:16]}"
        located[segment.segment_ref] = {
            "segment_id": segment_id,
            "exact_source_text": quoted,
            "start_offset": start,
            "end_offset": start + len(quoted) if start or quoted in message else 0,
            "kind": segment.segment_kind.value,
            "reply_required": segment.segment_kind not in ACTIONABLE_SEGMENT_KINDS,
            "action_required": segment.segment_kind in ACTIONABLE_SEGMENT_KINDS,
            "confidence": envelope.overall_confidence,
            "target_condition_id": None,
        }
    if missing:
        raise IntentCompileError(
            "INTENT_SEGMENT_NOT_IN_MESSAGE",
            "Part of that turn could not be matched to your exact words.",
            details=tuple(missing[:8]),
        )
    return located


def _condition_evidence_segment(
    intent: SemanticIntent,
    *,
    intent_ref: str,
    selected_ref: str,
    segments: dict[str, dict[str, Any]],
    claimed_semantic_segment_refs: frozenset[str],
    selected_reference_is_exclusive: bool,
    message: str,
    source_turn_id: str,
    derivations: list[str],
) -> dict[str, Any]:
    """Return the one exact span that may author this semantic intent.

    A planner occasionally divides one rule into adjacent exact strategy spans, for
    example ``Use 4h as context and 5m as trigger.`` followed by ``Require a
    close-to-close move ...``.  The model has supplied all values and their roles, but
    attaching the condition to only the latter span makes the already-grounded roles
    look absent.  This is a source-association error, not an invitation for a
    deterministic parser to create a condition.

    The server may repair that association only when an *unclaimed*, directly adjacent
    actionable segment explicitly grounds one of the role values the condition already
    contains.  It creates a server-owned contiguous evidence span from those exact
    user words.  A claimed segment, a question/conversation segment, ordinary text
    between spans, or a segment that supplies no missing role stops the search.  No
    semantic value is read from a new source, inferred, or added to the intent.
    """

    selected = segments[selected_ref]
    payload = intent.payload
    condition = getattr(payload, "condition", None)
    if not isinstance(condition, ConditionIntent) or not selected_reference_is_exclusive:
        return selected

    required_role_rows: list[tuple[str, SemanticTimeframeRole]] = []
    if condition.trigger_timeframe:
        required_role_rows.append((condition.trigger_timeframe, "trigger"))
    required_role_rows.extend((timeframe, "context") for timeframe in condition.context_timeframes)
    required_role_rows.extend(
        (timeframe, "confirmation") for timeframe in condition.confirmation_timeframes
    )
    if condition.reference_timeframe:
        required_role_rows.append((condition.reference_timeframe, "reference"))
    required_roles = tuple(required_role_rows)
    if not required_roles:
        return selected

    def missing_from(text: str) -> set[tuple[str, SemanticTimeframeRole]]:
        return {
            (timeframe, role)
            for timeframe, role in required_roles
            if not grounds_timeframe_role(text, timeframe, role)
        }

    missing = missing_from(str(selected["exact_source_text"]))
    if not missing:
        return selected

    ordered_refs = sorted(
        (reference for reference in segments if not reference.startswith("__evidence_")),
        key=lambda reference: int(segments[reference]["start_offset"]),
    )
    selected_index = ordered_refs.index(selected_ref)
    included_refs = [selected_ref]
    start = int(selected["start_offset"])
    end = int(selected["end_offset"])

    def collect(direction: int) -> None:
        nonlocal start, end, missing
        index = selected_index + direction
        while 0 <= index < len(ordered_refs):
            candidate_ref = ordered_refs[index]
            candidate = segments[candidate_ref]
            candidate_start = int(candidate["start_offset"])
            candidate_end = int(candidate["end_offset"])
            gap = message[candidate_end:start] if direction < 0 else message[end:candidate_start]
            if not _neutral_evidence_gap(gap):
                return
            if candidate_ref in claimed_semantic_segment_refs or candidate["kind"] not in {
                item.value for item in ACTIONABLE_SEGMENT_KINDS
            }:
                return
            candidate_grounded = {
                (timeframe, role)
                for timeframe, role in missing
                if grounds_timeframe_role(str(candidate["exact_source_text"]), timeframe, role)
            }
            if not candidate_grounded:
                # The candidate supplies none of the exact missing roles. Do not
                # traverse through an unrelated instruction to reach later text.
                return
            included_refs.append(candidate_ref)
            missing -= candidate_grounded
            start = min(start, candidate_start)
            end = max(end, candidate_end)
            if not missing:
                return
            index += direction

    # Search both directions because users may put the scope before or after the
    # formula. Each direction is constrained by the same exact-source boundary.
    collect(-1)
    if missing:
        collect(1)
    if missing:
        return selected

    exact_source_text = message[start:end]
    # Defensive recheck over the server-derived exact span. The branch above can only
    # shrink ``missing``, never fill a role by assumption; this makes that guarantee
    # explicit at the identity boundary.
    if missing_from(exact_source_text):
        return selected
    evidence_id = f"segment_{_identity(source_turn_id, intent_ref, 'condition_evidence')[:16]}"
    evidence = {
        **selected,
        "segment_id": evidence_id,
        "exact_source_text": exact_source_text,
        "start_offset": start,
        "end_offset": end,
        "reply_required": False,
        "action_required": True,
    }
    # Replacing the selected segment (and removing its unclaimed supporting spans)
    # keeps the canonical plan free of overlapping actionable segments. Nothing else
    # can point at a supporting span because it was explicitly unclaimed; the selected
    # span is only replaced when this semantic intent is its sole owner.
    segments[selected_ref] = evidence
    for reference in included_refs:
        if reference != selected_ref:
            segments.pop(reference, None)
    derivations.append(
        f"condition:{intent_ref}:contiguous_evidence_span:{','.join(sorted(included_refs))}"
    )
    return evidence


def _reject_omitted_explicit_role(
    intent: SemanticIntent,
    *,
    intent_ref: str,
    selected_ref: str,
    segments: Mapping[str, Mapping[str, Any]],
    claimed_semantic_segment_refs: frozenset[str],
    condition_segment_refs: frozenset[str],
    message: str,
    scope_to_source_quote: bool = False,
) -> None:
    """Require the semantic plan—not the compiler—to carry authored semantic roles.

    This is a post-AI cross-check.  It may notice that the model omitted an exact role,
    but it never writes that role into the intent.  Every omission it finds is named,
    each with the exact span of the trader's words that authorises it, so one bounded
    correction can address all of them at once.

    Boolean leaves are checked against their own exact source quote: comparing a leaf
    with the whole message would report a neighbouring rule's timeframe as missing from
    this one.
    """

    payload = intent.payload
    if isinstance(payload, ReplaceBooleanPayload):
        for leaf in payload.boolean_structure.condition_leaves:
            _reject_omitted_role_on_condition(
                leaf.condition,
                intent_ref=intent_ref,
                selected_ref=selected_ref,
                segments=segments,
                condition_segment_refs=condition_segment_refs,
                message=message,
                scope_to_source_quote=True,
            )
        return
    if not isinstance(payload, AddConditionPayload):
        return
    _reject_omitted_role_on_condition(
        payload.condition,
        intent_ref=intent_ref,
        selected_ref=selected_ref,
        segments=segments,
        condition_segment_refs=condition_segment_refs,
        message=message,
        scope_to_source_quote=scope_to_source_quote,
    )


def _reject_omitted_role_on_condition(
    condition: ConditionIntent,
    *,
    intent_ref: str,
    selected_ref: str,
    segments: Mapping[str, Mapping[str, Any]],
    condition_segment_refs: frozenset[str],
    message: str,
    scope_to_source_quote: bool,
) -> None:
    represented: dict[SemanticTimeframeRole, set[str]] = {
        "trigger": ({condition.trigger_timeframe} if condition.trigger_timeframe else set()),
        "context": set(condition.context_timeframes),
        "confirmation": set(condition.confirmation_timeframes),
        "reference": ({condition.reference_timeframe} if condition.reference_timeframe else set()),
    }
    ordered_refs = sorted(segments, key=lambda ref: int(segments[ref]["start_offset"]))
    selected_index = ordered_refs.index(selected_ref)
    candidates: list[str] = [selected_ref]

    def collect(direction: int) -> None:
        start = int(segments[selected_ref]["start_offset"])
        end = int(segments[selected_ref]["end_offset"])
        index = selected_index + direction
        while 0 <= index < len(ordered_refs):
            reference = ordered_refs[index]
            candidate = segments[reference]
            candidate_start = int(candidate["start_offset"])
            candidate_end = int(candidate["end_offset"])
            gap = message[candidate_end:start] if direction < 0 else message[end:candidate_start]
            if not _neutral_evidence_gap(gap):
                return
            # This pass only detects a role the planner omitted; it never uses the
            # neighbouring text as mutation evidence or inserts a value. A segment
            # claimed by a symbol/name/workflow intent may still be the second clause
            # of this complete instruction. Another condition boundary may not.
            if (
                (reference in condition_segment_refs and reference != selected_ref)
                or candidate["kind"]
                not in {
                item.value for item in ACTIONABLE_SEGMENT_KINDS
                }
            ):
                return
            candidates.append(reference)
            start = min(start, candidate_start)
            end = max(end, candidate_end)
            index += direction

    collect(-1)
    collect(1)
    omissions: dict[str, tuple[str, str]] = {}
    role_field = {
        "trigger": "trigger_timeframe",
        "context": "context_timeframes",
        "confirmation": "confirmation_timeframes",
        "reference": "reference_timeframe",
    }
    for reference in candidates:
        segment_text = str(segments[reference]["exact_source_text"])
        # A single condition owns its verified action segment. A narrower, optional
        # model-authored source_quote must not be able to hide another trader-controlled
        # value in that segment: that is exactly the omission this post-AI check exists
        # to catch. Boolean children are the exception because their per-child quotes
        # are what keep roles from neighbouring child rules from being swapped.
        text = segment_text
        if (
            scope_to_source_quote
            and condition.source_quote
            and condition.source_quote in segment_text
        ):
            text = condition.source_quote
        if condition.formula_key is None and condition.capability_key is None:
            formulas = [
                value
                for value in FormulaKind
                if value != FormulaKind.CAPABILITY and grounds_formula(text, value)
            ]
            if len(formulas) == 1:
                omissions["condition.formula_key"] = (formulas[0].value, reference)
        for timeframe in extract_timeframes(text):
            for role in _TIMEFRAME_ROLES:
                if timeframe not in represented[role] and grounds_timeframe_role(
                    text, timeframe, role
                ):
                    path = f"condition.{role_field[role]}"
                    previous = omissions.get(path)
                    if previous is not None and previous[0] != timeframe:
                        raise IntentCompileError(
                            "INTENT_INCOMPLETE",
                            "More than one timeframe was assigned to a singular role.",
                            details=(f"{intent_ref}:add_condition:{path}:ambiguous",),
                            intent_ref=intent_ref,
                            target_path=path,
                            segment_ref=reference,
                        )
                    omissions[path] = (timeframe, reference)
        if condition.movement_direction is None:
            directions = [
                value
                for value in (MovementDirection.UP, MovementDirection.DOWN)
                if grounds_direction(text, value)
            ]
            if len(directions) == 1:
                omissions["condition.movement_direction"] = (
                    directions[0].value,
                    reference,
                )
            elif _explicit_neutral_role(text, role="movement_direction"):
                omissions["condition.movement_direction"] = (
                    MovementDirection.NEUTRAL.value,
                    reference,
                )
        if condition.strategy_bias is None:
            biases = [
                value
                for value in (StrategyBias.LONG, StrategyBias.SHORT)
                if grounds_strategy_bias(text, value)
            ]
            if len(biases) == 1:
                omissions["condition.strategy_bias"] = (biases[0].value, reference)
            elif _explicit_neutral_role(text, role="strategy_bias"):
                omissions["condition.strategy_bias"] = (
                    StrategyBias.NEUTRAL.value,
                    reference,
                )
        if condition.required is None and re.search(
            r"\b(?:optional|not required)\b", text.casefold()
        ):
            omissions["condition.required"] = ("false", reference)
    if not omissions:
        return
    # Every omission is a model mistake with an exact field name and an exact span of
    # the trader's own words behind it. One or five, the reason is the same and so is
    # the recovery: name them all in one bounded correction.
    #
    # Reporting more than one as COMPILER_INVARIANT_VIOLATION is the defect measured in
    # evaluator runs 20260802T232050Z and 20260803T000036Z. That class is terminal, so
    # the turn returned HTTP 422 with no repair and no question, and the trader could do
    # nothing but send the same sentence again. `precedence_grouping-013-1996163001`
    # shows eight identical refusals to one ordinary instruction.
    ordered = sorted(omissions)
    evidence_refs = {omissions[path][1] for path in ordered}
    raise IntentCompileError(
        "PLANNER_SEMANTIC_OMISSION",
        (
            "The semantic plan left out one explicitly authored role."
            if len(ordered) == 1
            else "The semantic plan left out roles the trader stated."
        ),
        details=tuple(f"{intent_ref}:add_condition:{path}:omitted" for path in ordered),
        intent_ref=intent_ref,
        target_paths=tuple(ordered),
        # A correction is authorised by one verified span. When the omissions were
        # found across several adjacent spans there is no single authorising span, so
        # the selected action segment — which the intent already owns — is used.
        segment_ref=(
            next(iter(evidence_refs)) if len(evidence_refs) == 1 else selected_ref
        ),
    )


def _explicit_neutral_role(text: str, *, role: str) -> bool:
    """Require both the neutral value and its semantic role in the source.

    Canonical condition nodes have neutral defaults, so the general grounding helpers
    intentionally accept them without wording. The compact planner boundary is
    stricter: a model-authored neutral value is trader-controlled only when the source
    explicitly assigns neutral to movement/direction or to strategy bias.
    """

    normalized = " ".join(text.casefold().split())
    neutral = r"(?:neutral|not[ _-]?applicable|n/?a|محايد|غير قابل للتطبيق)"
    if role == "strategy_bias":
        role_words = r"(?:strategy\s+bias|trade\s+bias|bias|انحياز(?:\s+الاستراتيجية)?)"
    else:
        role_words = r"(?:movement(?:\s+direction)?|direction|trend|حركة|اتجاه)"
    return bool(
        re.search(rf"{role_words}\s*(?:is|=|:)?\s*{neutral}", normalized)
        or re.search(rf"{neutral}\s*{role_words}", normalized)
    )


def _neutral_evidence_gap(text: str) -> bool:
    """Only punctuation and whitespace may lie between associated exact spans."""

    return not bool(re.search(r"[^\s.,;:()\[\]{}\-–—/]", text))


def _strategy_instructions(
    envelope: PlannerIntentEnvelope,
    segments: Mapping[str, Mapping[str, Any]],
    references: PlannerReferenceContext,
) -> list[dict[str, Any]]:
    """One trace entry per condition intent, naming the mechanic it chose.

    Never compiled. It exists so the capability gate can refuse a key the shortlist did
    not offer even when the intent produced no node, and so an operator can read what the
    model thought it was doing.
    """

    entries: list[dict[str, Any]] = []
    for index, intent in enumerate(envelope.semantic_intents):
        payload = intent.payload
        condition = getattr(payload, "condition", None)
        if not isinstance(condition, ConditionIntent):
            continue
        target_reference = getattr(payload, "target_reference", None)
        entries.append(
            {
                "segment_id": segments[intent.segment_ref]["segment_id"],
                "intent_summary": f"{intent.action.value}: intent_{index + 1}",
                "capability_key": condition.capability_key,
                "target_condition_id": references.condition_id(target_reference),
            }
        )
    return entries[:24]


def _existing_conditions(draft: StrategyDraftV2) -> dict[str, ConditionNodeV2]:
    if draft.condition_ast is None:
        return {}
    return {node.node_id: node for node in draft.condition_ast.walk()}


def _registry_parameter_schemas(
    shortlist: CapabilityShortlist | None,
) -> dict[str, dict[str, Any]]:
    """Declared parameter schemas keyed by the exact offered capability and name.

    The compact planner reports a shallow typed value.  Whether that value belongs in
    an integer, enum, array, or object slot is still decided by the governed registry,
    not by a model heuristic or the Python representation of the text.
    """

    schemas: dict[str, dict[str, Any]] = {}
    for candidate in shortlist.candidates if shortlist is not None else ():
        schema = candidate.parameter_schema or {}
        properties = schema.get("properties") if isinstance(schema, dict) else None
        for name, body in (properties or {}).items():
            if isinstance(body, dict):
                # No bare-name fallback: two capabilities can legitimately call a
                # parameter "period" while giving it a different type or enum.
                schemas[f"{candidate.capability_key}.{name}"] = dict(body)
    return schemas


def _registry_capability_versions(
    shortlist: CapabilityShortlist | None,
) -> dict[str, str]:
    """Server-owned versions for the exact governed capabilities offered this turn."""

    if shortlist is None:
        return {}
    return {
        candidate.capability_key: candidate.capability_version
        for candidate in shortlist.candidates
    }


def _operation(
    intent: SemanticIntent,
    *,
    intent_ref: str,
    segment_text: str,
    authorizing_segment_id: str,
    operation_id: str,
    source_turn_id: str,
    draft: StrategyDraftV2,
    existing: Mapping[str, ConditionNodeV2],
    parameter_schemas: Mapping[str, Mapping[str, Any]],
    capability_versions: Mapping[str, str],
    history: Sequence[Mapping[str, Any]],
    references: PlannerReferenceContext,
    derivations: list[str],
) -> dict[str, Any] | None:
    del history
    payload = intent.payload
    base = {
        "operation_id": operation_id,
        "authorizing_segment_id": authorizing_segment_id,
        "kind": _OPERATION_KIND[intent.action],
    }
    if isinstance(payload, SymbolPayload):
        return {**base, "symbol": payload.symbol.strip()}
    if isinstance(
        payload,
        (
            SetModePayload,
            SetNamePayload,
            SetExchangePayload,
            SetQuoteAssetPayload,
            SetMarketTypePayload,
        ),
    ):
        return {**base, "fields": _draft_patch(payload)}
    if isinstance(payload, ShariaPreferencePayload):
        _validate_sharia_universe_choice(
            payload,
            intent_ref=intent_ref,
            segment_ref=intent.segment_ref,
        )
        _validate_sharia_preference_source(
            payload,
            segment_text,
            references=references,
            intent_ref=intent_ref,
            segment_ref=intent.segment_ref,
        )
        resolved_policy = _policy_patch(
            payload,
            draft,
            references,
            source_text=segment_text,
            intent_ref=intent_ref,
            segment_ref=intent.segment_ref,
            derivations=derivations,
        )
        if resolved_policy == draft.sharia_policy.model_dump(mode="json"):
            derivations.append("sharia_policy:exact_current_policy:no_operation")
            return None
        return {
            **base,
            "sharia_policy": resolved_policy,
        }
    if isinstance(payload, RemoveConditionPayload):
        target = _known_condition(
            payload.target_reference,
            existing,
            references,
            intent_ref=intent_ref,
            segment_ref=intent.segment_ref,
        )
        return {**base, "target_condition_id": target}
    if isinstance(payload, UpdateConditionPayload):
        target = _known_condition(
            payload.target_reference,
            existing,
            references,
            intent_ref=intent_ref,
            segment_ref=intent.segment_ref,
        )
        condition = _resolve_condition_references(
            payload.condition, references, intent_ref=intent_ref, segment_ref=intent.segment_ref
        )
        return {
            **base,
            "target_condition_id": target,
            "condition": _inherited_node(
                condition,
                existing[target],
                parameter_schemas=parameter_schemas,
                capability_versions=capability_versions,
                derivations=derivations,
                authorizing_text=segment_text,
            ),
        }
    if isinstance(payload, AddConditionPayload):
        condition = _resolve_condition_references(
            payload.condition, references, intent_ref=intent_ref, segment_ref=intent.segment_ref
        )
        _validate_new_condition(
            condition,
            existing=existing,
            intent_ref=intent_ref,
            segment_ref=intent.segment_ref,
        )
        if condition.source_quote != segment_text:
            # A simple new rule owns the complete verified action segment. Keeping a
            # narrower model-authored quote here would let it hide evidence for a value
            # that the repaired intent now carries, and the canonical grounding gate
            # would then reject the same valid turn. Boolean leaves keep their own
            # per-leaf quotes because those quotes are the role-separation boundary.
            condition = condition.model_copy(update={"source_quote": segment_text})
            derivations.append(f"condition:{intent_ref}:verified_segment_as_source_fragment")
        return {
            **base,
            "condition": _new_node(
                condition,
                source_turn_id=source_turn_id,
                operation_id=operation_id,
                existing=existing,
                parameter_schemas=parameter_schemas,
                capability_versions=capability_versions,
                path=(),
                derivations=derivations,
                segment_text=segment_text,
            ),
        }
    if isinstance(payload, ReplaceBooleanPayload):
        return {
            **base,
            "condition": _boolean_structure_node(
                payload.boolean_structure,
                intent_ref=intent_ref,
                segment_ref=intent.segment_ref,
                segment_text=segment_text,
                source_turn_id=source_turn_id,
                operation_id=operation_id,
                existing=existing,
                parameter_schemas=parameter_schemas,
                capability_versions=capability_versions,
                references=references,
                derivations=derivations,
            ),
        }
    if isinstance(payload, RestoreSnapshotPayload):
        return {
            **base,
            **_snapshot_target(
                payload,
                references,
                intent_ref=intent_ref,
                segment_ref=intent.segment_ref,
                derivations=derivations,
            ),
        }
    raise IntentCompileError(
        "COMPILER_INVARIANT_VIOLATION",
        "The semantic compiler has no mapping for an accepted action.",
        details=(intent.action.value,),
        intent_ref=intent_ref,
        segment_ref=intent.segment_ref,
    )


def _draft_patch(
    payload: SetModePayload
    | SetNamePayload
    | SetExchangePayload
    | SetQuoteAssetPayload
    | SetMarketTypePayload,
) -> dict[str, Any]:
    if isinstance(payload, SetModePayload):
        return {"mode": payload.mode.value}
    if isinstance(payload, SetNamePayload):
        return {"name": payload.name}
    if isinstance(payload, SetExchangePayload):
        return {"exchange": payload.exchange}
    if isinstance(payload, SetQuoteAssetPayload):
        return {"quote_asset": payload.quote_asset}
    return {"market_type": payload.market_type}


def _validate_sharia_universe_choice(
    payload: ShariaPreferencePayload,
    *,
    intent_ref: str,
    segment_ref: str,
) -> None:
    """Reject a missing/conflicting governed alternative before polarity grounding."""

    screened = payload.screened_assets_only
    watchlist_only = payload.approved_watchlist_only
    if screened is True and watchlist_only is True:
        raise IntentCompileError(
            "SHARIA_PREFERENCE_AMBIGUOUS",
            "Choose either the screened market or one approved watchlist.",
            details=("sharia_preferences:universe_conflict",),
            intent_ref=intent_ref,
            target_path="approved_watchlist_only",
            segment_ref=segment_ref,
        )
    if (screened is False and watchlist_only is not True) or (
        watchlist_only is False and screened is not True
    ):
        raise IntentCompileError(
            "SHARIA_PREFERENCE_AMBIGUOUS",
            "Choose the governed universe you want to use.",
            details=("sharia_preferences:negative_universe_without_alternative",),
            intent_ref=intent_ref,
            target_path=(
                "screened_assets_only" if screened is False else "approved_watchlist_only"
            ),
            segment_ref=segment_ref,
        )


def _policy_patch(
    payload: ShariaPreferencePayload,
    draft: StrategyDraftV2,
    references: PlannerReferenceContext,
    *,
    source_text: str,
    intent_ref: str,
    segment_ref: str,
    derivations: list[str],
) -> dict[str, Any]:
    """Resolve explicit preferences into the governed canonical policy."""

    policy = draft.sharia_policy.model_dump(mode="json")
    if payload.methodology_family or payload.methodology_identifier:
        matches = references.methodology_matches(
            family=payload.methodology_family,
            identifier=payload.methodology_identifier,
        )
        if len(matches) != 1:
            code = "SHARIA_PREFERENCE_UNAVAILABLE" if not matches else "SHARIA_PREFERENCE_AMBIGUOUS"
            raise IntentCompileError(
                code,
                (
                    "That methodology is not currently available."
                    if not matches
                    else "More than one active methodology matches that preference."
                ),
                details=("sharia_preferences:methodology",),
                intent_ref=intent_ref,
                target_path="methodology_identifier",
                segment_ref=segment_ref,
            )
        methodology = matches[0]
        policy["methodology_id"] = methodology.methodology_id
        policy["methodology_version"] = methodology.methodology_version
        derivations.append(f"sharia_methodology:{methodology.reference}:governed_resolution")

    if payload.screened_assets_only is True:
        policy.update(
            {
                "universe_mode": ShariaUniverseMode.ELIGIBLE_MARKET.value,
                "approved_watchlist_id": None,
                "approved_watchlist_version": None,
                "explicit_symbols": [],
            }
        )
    if payload.approved_watchlist_only is True:
        # An answer to a server-offered watchlist clarification can name the offered
        # watchlist directly (for example, "Core assets"). Resolve that public name
        # only after exact-source verification; the model never receives or returns an
        # owned database identity. A generic request remains valid only with one
        # executable owned watchlist.
        watchlist_matches = references.watchlist_matches_in_text(source_text)
        if not watchlist_matches:
            available_watchlists = references.watchlist_matches()
            watchlist_matches = available_watchlists
        if len(watchlist_matches) != 1:
            raise IntentCompileError(
                (
                    "SHARIA_PREFERENCE_UNAVAILABLE"
                    if not watchlist_matches
                    else "SHARIA_PREFERENCE_AMBIGUOUS"
                ),
                (
                    "No approved watchlist is available."
                    if not watchlist_matches
                    else "Choose one of your approved watchlists."
                ),
                details=("sharia_preferences:approved_watchlist",),
                intent_ref=intent_ref,
                target_path="approved_watchlist_only",
                segment_ref=segment_ref,
            )
        selected_watchlist = watchlist_matches[0]
        policy.update(
            {
                "universe_mode": ShariaUniverseMode.APPROVED_WATCHLIST.value,
                "approved_watchlist_id": selected_watchlist.watchlist_id,
                "approved_watchlist_version": selected_watchlist.watchlist_version,
                "explicit_symbols": [],
            }
        )
        derivations.append(f"sharia_watchlist:{selected_watchlist.reference}:governed_resolution")
    if payload.fail_closed_preference is False:
        raise IntentCompileError(
            "SHARIA_FAIL_OPEN_UNSUPPORTED",
            "Hilal Markets cannot include assets when required Sharia evidence is unsafe.",
            details=("sharia_preferences:fail_open",),
            intent_ref=intent_ref,
            target_path="fail_closed_preference",
            segment_ref=segment_ref,
        )
    if payload.fail_closed_preference is True:
        policy.update(
            {
                "allowed_statuses": [ShariaAssetStatus.ELIGIBLE.value],
                "qualification_policy": "exclude",
                "disputed_asset_policy": "exclude",
                "compliance_change_behavior": ComplianceChangeBehavior.PAUSE_ASSET.value,
            }
        )
    derivations.append("sharia_policy:inherited_unmentioned_governed_fields")
    return policy


def _validate_sharia_preference_source(
    payload: ShariaPreferencePayload,
    source: str,
    *,
    references: PlannerReferenceContext,
    intent_ref: str,
    segment_ref: str,
) -> None:
    """Prove each preference from its own segment before canonical policy exists."""

    lowered = source.casefold()
    missing: list[str] = []
    for field_name in ("methodology_family", "methodology_identifier"):
        value = getattr(payload, field_name)
        if value and semantic_key(value) not in semantic_key(source):
            missing.append(field_name)
    if payload.approved_watchlist_only is not None and references.watchlist_matches_in_text(source):
        # A named public choice in a server-offered clarification is stronger evidence
        # than the generic word "watchlist". Add the internal marker only for the
        # existing vocabulary check below; no identity crosses the planner boundary.
        lowered = f"{lowered} watchlist"
    if payload.screened_assets_only is not None and not _grounds_preference_polarity(
        lowered,
        ("screened", "eligible market", "eligible assets", "فحص", "mo2ahal"),
        payload.screened_assets_only,
    ):
        missing.append("screened_assets_only")
    named_watchlist = bool(references.watchlist_matches_in_text(source))
    if payload.approved_watchlist_only is not None and not (
        payload.approved_watchlist_only is True
        and named_watchlist
        or _grounds_preference_polarity(
            lowered,
            ("watchlist", "favorites", "favourites", "مفض", "mofadala"),
            payload.approved_watchlist_only,
        )
    ):
        missing.append("approved_watchlist_only")
    if payload.fail_closed_preference is not None:
        fail_closed = any(
            phrase in lowered
            for phrase in (
                "fail closed",
                "strict sharia",
                "eligible only",
                "screened only",
                "اقفل عند الشك",
                "ma t3adish el mashkook",
            )
        )
        fail_open = "fail open" in lowered
        if (payload.fail_closed_preference and not fail_closed) or (
            payload.fail_closed_preference is False and not fail_open
        ):
            missing.append("fail_closed_preference")
    if missing:
        raise IntentCompileError(
            "INTENT_VALUE_UNREADABLE",
            "A Sharia preference was not stated in its authorizing words.",
            details=tuple(f"sharia_preferences:{field}" for field in missing),
            intent_ref=intent_ref,
            target_path=missing[0],
            segment_ref=segment_ref,
        )


_PREFERENCE_NEGATION_RE = re.compile(
    r"(?:\bnot\b|\bno\b|\bwithout\b|\bdon't\b|\bdo not\b|\bavoid\b|"
    r"\bmesh\b|\bmsh\b|\bla2\b|\bla\b|مش|لا|بدون)",
    re.IGNORECASE,
)


def _grounds_preference_polarity(
    source: str,
    markers: tuple[str, ...],
    expected: bool,
) -> bool:
    """Prove both the governed choice and its positive/negative polarity.

    Merely seeing ``screened`` is not evidence for ``screened_assets_only=False``.
    That bug allowed the planner to invert a user's explicit universe preference while
    still passing the category-only grounding check.
    """

    readings: list[bool] = []
    for marker in markers:
        for match in re.finditer(re.escape(marker.casefold()), source.casefold()):
            # Negation may govern a coordinated object ("do not use screened assets
            # or a watchlist"), but must not leak across a new clause ("do not use a
            # watchlist; use screened assets").
            prefix = source[: match.start()]
            clause_start = max(prefix.rfind(token) for token in (";", ".", "!", "?", "\n"))
            prefix = prefix[clause_start + 1 :]
            readings.append(not bool(_PREFERENCE_NEGATION_RE.search(prefix)))
    return expected in readings


def _known_condition(
    target_reference: str,
    existing: Mapping[str, ConditionNodeV2],
    references: PlannerReferenceContext,
    *,
    intent_ref: str,
    segment_ref: str,
) -> str:
    target = references.condition_id(target_reference)
    if target is None or target not in existing:
        raise IntentCompileError(
            "INTENT_TARGET_UNKNOWN",
            "That change referred to a rule that is not in the current setup.",
            details=(f"{intent_ref}:condition:{target_reference[:60]}",),
            intent_ref=intent_ref,
            target_path="target_reference",
            segment_ref=segment_ref,
        )
    return target


def _snapshot_target(
    payload: RestoreSnapshotPayload,
    references: PlannerReferenceContext,
    *,
    intent_ref: str,
    segment_ref: str,
    derivations: list[str],
) -> dict[str, Any]:
    """Resolve an undo request against session-owned history, never model memory."""

    match = references.snapshot(payload.target_reference)
    if match is None:
        raise IntentCompileError(
            "INTENT_TARGET_UNKNOWN",
            "There is no earlier saved version of this setup to go back to.",
            details=(f"{intent_ref}:snapshot:{payload.target_reference[:60]}",),
            intent_ref=intent_ref,
            target_path="target_reference",
            segment_ref=segment_ref,
        )
    derivations.append(f"restore_snapshot:resolved:{match.reference}")
    return {
        "target_snapshot_id": match.snapshot_id,
        "target_executable_version": match.executable_version,
    }


def _resolve_condition_references(
    condition: ConditionIntent,
    references: PlannerReferenceContext,
    *,
    intent_ref: str,
    segment_ref: str,
) -> ConditionIntent:
    """Replace offered turn-local condition aliases with owned canonical identities."""

    target = condition.target_reference
    update: dict[str, Any] = {}
    if target is not None:
        canonical = references.condition_id(target)
        if canonical is None:
            raise IntentCompileError(
                "INTENT_TARGET_UNKNOWN",
                "That rule reference is not available in the current setup.",
                details=(f"{intent_ref}:condition:{target[:60]}",),
                intent_ref=intent_ref,
                target_path="condition.target_reference",
                segment_ref=segment_ref,
            )
        update["target_reference"] = canonical
    return condition.model_copy(update=update) if update else condition


def _validate_new_condition(
    condition: ConditionIntent,
    *,
    existing: Mapping[str, ConditionNodeV2],
    intent_ref: str,
    segment_ref: str,
) -> None:
    """Reject genuinely incomplete user meaning before an internal operation exists."""

    target_reference = condition.target_reference
    if target_reference and target_reference in existing:
        # Boolean restructuring may reference a complete owned rule without restating
        # it. `_new_node` preserves that immutable node exactly; requiring its formula
        # again would make "use the RSI rule OR the volume rule" impossible.
        return
    missing: list[str] = []
    if condition.formula_key is None and condition.capability_key is None:
        missing.append("formula_key")
    if condition.comparator is None:
        missing.append("comparator")
    if (
        condition.comparator is not None
        and condition.comparator.value
        not in {
            "is_true",
            "is_false",
        }
        and condition.threshold is None
    ):
        missing.append("threshold")
    # A monitor rule cannot fire without an explicit trigger timeframe.  Leaving
    # this to the canonical Pydantic model used to turn a model omission into a
    # misleading compiler-invariant violation after semantic compilation.  Detect it
    # while the intent is still compact so the same typed clarification machinery is
    # used for every other absent trader-controlled field.
    if condition.trigger_timeframe is None:
        missing.append("trigger_timeframe")
    if missing:
        raise IntentCompileError(
            "INTENT_INCOMPLETE",
            "That rule still needs " + ", ".join(missing) + ".",
            details=tuple(f"condition.{field}" for field in missing),
            intent_ref=intent_ref,
            target_path=f"condition.{missing[0]}",
            segment_ref=segment_ref,
        )


def _new_node(
    intent: ConditionIntent,
    *,
    source_turn_id: str,
    operation_id: str,
    existing: Mapping[str, ConditionNodeV2],
    parameter_schemas: Mapping[str, Mapping[str, Any]],
    capability_versions: Mapping[str, str],
    path: tuple[int, ...],
    derivations: list[str],
    segment_text: str = "",
) -> dict[str, Any]:
    """Build one executable rule from the trader's terms.

    Combining rules is not done here. A Boolean expression arrives as a flat graph and
    is turned into a tree by :func:`_boolean_structure_node`, which calls this once per
    leaf. Keeping node construction and structure construction apart is what stops a
    single rule from being wrapped in an invented group.
    """

    target_reference = intent.target_reference
    if target_reference and target_reference in existing:
        # A restructure keeps rules the trader did not change. Rebuilding them from the
        # intent would drop every field the intent did not restate.
        inherited = existing[target_reference]
        derivations.append(f"condition:{inherited.node_id}:kept_unchanged_in_structure")
        return inherited.model_dump(mode="json")
    node = _condition_fields(
        intent,
        parameter_schemas,
        capability_versions,
        derivations,
        # The trader's own words for this rule. The operator authority reads them so a
        # comparator the model guessed cannot outrank a comparison the trader wrote.
        authorizing_text=intent.source_quote or segment_text,
    )
    node["node_id"] = _child_node_id(intent, existing, source_turn_id, operation_id, path)
    node["node_type"] = ConditionNodeType.CONDITION.value
    return node


def _child_node_id(
    intent: ConditionIntent,
    existing: Mapping[str, ConditionNodeV2],
    source_turn_id: str,
    operation_id: str,
    path: tuple[int, ...],
) -> str:
    target_reference = intent.target_reference
    if target_reference and target_reference in existing:
        return target_reference
    rendered = ".".join(map(str, path)) or "root"
    return f"condition_{_identity(source_turn_id, operation_id, rendered)[:16]}"


def _boolean_structure_node(
    structure: BooleanStrategyIntent,
    *,
    intent_ref: str,
    segment_ref: str,
    segment_text: str,
    source_turn_id: str,
    operation_id: str,
    existing: Mapping[str, ConditionNodeV2],
    parameter_schemas: Mapping[str, Mapping[str, Any]],
    capability_versions: Mapping[str, str],
    references: PlannerReferenceContext,
    derivations: list[str],
) -> dict[str, Any]:
    """Turn one validated flat Boolean graph into the canonical condition tree.

    The graph is proved to be a single finite tree first. Only then is each leaf built
    with the same ``_new_node`` every simple rule uses, so a leaf inside ``(B OR C)``
    is validated, grounded and compiled exactly like a rule written on its own.
    """

    try:
        topology = validate_boolean_topology(structure)
    except BooleanTopologyError as exc:
        raise IntentCompileError(
            exc.code,
            str(exc),
            details=exc.details,
            intent_ref=intent_ref,
            target_paths=("boolean_structure",),
            segment_ref=segment_ref,
        ) from exc

    resolved: dict[str, ConditionIntent] = {}
    for leaf in structure.condition_leaves:
        condition = _resolve_condition_references(
            leaf.condition, references, intent_ref=intent_ref, segment_ref=segment_ref
        )
        _validate_new_condition(
            condition, existing=existing, intent_ref=intent_ref, segment_ref=segment_ref
        )
        resolved[leaf.leaf_ref] = condition

    groups = topology.groups

    def build(ref: str, path: tuple[int, ...]) -> dict[str, Any]:
        group = groups.get(ref)
        if group is None:
            return _new_node(
                resolved[ref],
                source_turn_id=source_turn_id,
                operation_id=operation_id,
                existing=existing,
                parameter_schemas=parameter_schemas,
                capability_versions=capability_versions,
                path=path,
                derivations=derivations,
                segment_text=segment_text,
            )
        rendered = ".".join(map(str, path)) or "root"
        return {
            "node_id": f"group_{_identity(source_turn_id, operation_id, rendered)[:16]}",
            "node_type": _BOOLEAN_NODE_TYPE[group.operator].value,
            "children": [
                build(child, (*path, index)) for index, child in enumerate(group.child_refs)
            ],
        }

    derivations.append(f"boolean:{intent_ref}:topology:{topology.shape()}")
    return build(topology.root_ref, ())


def _inherited_node(
    intent: ConditionIntent,
    current: ConditionNodeV2,
    *,
    parameter_schemas: Mapping[str, Mapping[str, Any]],
    capability_versions: Mapping[str, str],
    derivations: list[str],
    authorizing_text: str = "",
) -> dict[str, Any]:
    """The existing rule with only the fields the trader restated changed.

    This is what lets "change that to at least 8%" work. Everything the trader did not
    mention — timeframe, formula, direction, symbols — comes from the rule being edited,
    so an edit can never quietly widen into a rewrite.
    """

    node = current.model_dump(mode="json")
    stated = _condition_fields(
        intent,
        parameter_schemas,
        capability_versions,
        derivations,
        authorizing_text=authorizing_text,
    )
    unchanged = sorted(set(node) - set(stated) - {"node_id", "node_type", "children"})
    node.update(stated)
    node["node_id"] = current.node_id
    node["node_type"] = ConditionNodeType.CONDITION.value
    if unchanged:
        derivations.append(f"condition:{current.node_id}:inherited:{','.join(unchanged)}")
    return node


def _condition_fields(
    intent: ConditionIntent,
    parameter_schemas: Mapping[str, Mapping[str, Any]],
    capability_versions: Mapping[str, str],
    derivations: list[str],
    *,
    authorizing_text: str = "",
) -> dict[str, Any]:
    """Exactly the canonical fields the trader stated. Absent stays absent.

    Absent is not the same as a default. A field the trader did not mention is inherited
    on an edit and reported as missing on a create — filling it with a platform default
    here would be the platform choosing a rule the trader never described.

    One field is not simply passed through: the comparator. When the trader's own words
    state a comparison for this threshold, those words are authoritative and a different
    comparator from the model is corrected here, before any canonical operation exists.
    """

    stated: dict[str, Any] = {}
    if intent.source_quote:
        # Passed through, never trusted. `SetupAgentTurnPlan` keeps this quote only when
        # it really sits inside the authorizing segment, and replaces it with the segment
        # otherwise — the same check, in the one place that already performs it.
        stated["source_fragment"] = intent.source_quote
    if intent.formula_key is not None:
        stated["formula"] = intent.formula_key.value
    if intent.movement_direction is not None and (
        intent.movement_direction
        not in {MovementDirection.NEUTRAL, MovementDirection.NOT_APPLICABLE}
        or _explicit_neutral_role(authorizing_text, role="movement_direction")
    ):
        stated["movement_direction"] = intent.movement_direction.value
    elif intent.movement_direction is not None:
        derivations.append("condition:movement_direction:ungrounded_platform_default_removed")
    if intent.strategy_bias is not None and (
        intent.strategy_bias != StrategyBias.NEUTRAL
        or _explicit_neutral_role(authorizing_text, role="strategy_bias")
    ):
        stated["strategy_bias"] = intent.strategy_bias.value
    elif intent.strategy_bias is not None:
        derivations.append("condition:strategy_bias:ungrounded_platform_default_removed")
    comparator = _authoritative_comparator(intent, authorizing_text, derivations)
    if comparator is not None:
        stated["operator"] = comparator.value
    if intent.threshold is not None:
        stated["threshold"] = intent.threshold
    if intent.unit is not None:
        stated["unit"] = intent.unit
    if intent.trigger_timeframe is not None:
        stated["trigger_timeframe"] = intent.trigger_timeframe
    if intent.context_timeframes:
        stated["context_timeframes"] = list(intent.context_timeframes)
    if intent.confirmation_timeframes:
        stated["confirmation_timeframes"] = list(intent.confirmation_timeframes)
    if intent.reference_timeframe is not None:
        stated["reference_timeframe"] = intent.reference_timeframe
    if intent.reference_definition is not None:
        stated["reference_definition"] = intent.reference_definition
    if intent.lookback is not None:
        stated["lookback"] = intent.lookback
    if intent.capability_key is not None:
        stated["capability_key"] = intent.capability_key
        stated["formula"] = FormulaKind.CAPABILITY.value
        capability_version = capability_versions.get(intent.capability_key)
        if capability_version is None:
            raise IntentCompileError(
                "INTENT_NOT_PERMITTED",
                "That capability was not in the governed shortlist for this turn.",
                details=(f"capability_not_offered:{intent.capability_key}",),
                target_path="condition.capability_key",
            )
        stated["capability_version"] = capability_version
        derivations.append(
            f"capability:{intent.capability_key}:server_owned_version:{capability_version}"
        )
    if intent.capability_parameters:
        stated["capability_parameters"] = {
            item.name: _typed_parameter(item, intent, parameter_schemas)
            for item in intent.capability_parameters
        }
    if intent.required is not None:
        stated["required"] = intent.required
    if intent.condition_symbols:
        stated["condition_symbols"] = list(intent.condition_symbols)
    operands = _formula_operands(intent, derivations)
    if operands is not None:
        stated["operands"] = operands
    return stated


def _authoritative_comparator(
    intent: ConditionIntent,
    authorizing_text: str,
    derivations: list[str],
) -> Comparator | None:
    """The comparison this rule compiles with: the trader's words over the model's.

    ``at most 1%`` is an inclusive ceiling. A model that answers ``lt`` for it builds a
    monitor that stays silent on exactly 1% — the move the trader asked to see. In
    evaluator run 20260803T000036Z that is what shipped, and the only reason it did is
    that nothing checked the model's comparator against the words it came from.

    Correcting it is not a repair and costs nothing: no trader-controlled choice is
    still open, so a second model call could only agree or be wrong.
    """

    normalization = normalize_stated_comparator(
        authorizing_text,
        threshold=intent.threshold,
        proposed=intent.comparator,
    )
    if normalization.kind == OperatorNormalizationKind.AMBIGUOUS:
        raise IntentCompileError(
            "INTENT_INCOMPLETE",
            "That rule states more than one comparison for the same number.",
            details=(
                "condition.comparator:ambiguous:"
                + ",".join(item.value for item in normalization.competing),
            ),
            target_path="condition.comparator",
        )
    if normalization.corrected:
        derivations.append(f"condition:operator:{normalization.trace()}")
    return normalization.resolved


def _formula_operands(
    intent: ConditionIntent,
    derivations: list[str],
) -> list[dict[str, Any]] | None:
    """Operands the formula itself fixes, never operands the model chose.

    Percentage and sweep formulas are filled by the canonical binder on
    ``SetupAgentTurnPlan``; returning ``None`` for them leaves that single implementation
    in charge. The reference and level formulas read one price the trader named, and the
    contract requires it to be the first operand.
    """

    formula = intent.formula_key
    if formula is None or formula not in _PRICE_OPERAND_FORMULAS:
        return None
    if intent.measured_price_field is None:
        raise IntentCompileError(
            "INTENT_INCOMPLETE",
            "That rule compares a price, but did not say which price.",
            details=(f"{formula.value}:measured_price_field:missing",),
        )
    derivations.append(f"operands:{formula.value}:left_price:{intent.measured_price_field}")
    return [
        {
            "role": "left",
            "kind": "price",
            "field": intent.measured_price_field,
        }
    ]


def _typed_parameter(
    parameter: CapabilityParameterIntent,
    intent: ConditionIntent,
    parameter_schemas: Mapping[str, Mapping[str, Any]],
) -> CapabilityParameterIntentValue:
    """Read one planner parameter through its exact registry-declared shape.

    This is representation normalisation, not semantic completion: the planner must
    already have selected one typed value branch.  The registry says whether a written
    ``14`` is an integer or a number, which strings are legal enums, and whether a
    container is an array or a shallow object.  Unknown fields and type mismatches stay
    model-owned semantic errors, before an internal operation is created.
    """

    name = parameter.name
    if not intent.capability_key:
        raise _parameter_intent_error(name, "capability_missing")
    schema = parameter_schemas.get(f"{intent.capability_key}.{name}")
    if schema is None:
        raise _parameter_intent_error(name, "not_declared")
    return _coerce_parameter_value(
        parameter.semantic_value(),
        dict(schema),
        name=name,
    )


def _parameter_intent_error(name: str, reason: str) -> IntentCompileError:
    return IntentCompileError(
        "INTENT_VALUE_UNREADABLE",
        "One capability parameter does not match the offered mechanic.",
        details=(f"capability_parameter:{name}:{reason}",),
        target_path=f"condition.capability_parameters.{name}",
    )


def _coerce_parameter_value(
    value: object,
    schema: Mapping[str, Any],
    *,
    name: str,
) -> CapabilityParameterIntentValue:
    """Canonicalise a typed compact value only when its registry shape permits it."""

    declared = schema.get("type")
    declared_types = (
        {item for item in declared if isinstance(item, str)}
        if isinstance(declared, list)
        else {declared}
        if isinstance(declared, str)
        else set()
    )
    if isinstance(value, bool):
        if declared_types and "boolean" not in declared_types:
            raise _parameter_intent_error(name, "boolean_not_allowed")
        return value
    if isinstance(value, int | float):
        if declared_types and not ({"number", "integer"} & declared_types):
            raise _parameter_intent_error(name, "number_not_allowed")
        numeric = float(value)
        if "integer" in declared_types and "number" not in declared_types:
            if not numeric.is_integer():
                raise _parameter_intent_error(name, "integer_required")
            return int(numeric)
        return numeric
    if isinstance(value, str):
        if declared_types and "string" not in declared_types:
            raise _parameter_intent_error(name, "string_not_allowed")
        return value
    if isinstance(value, list):
        if declared_types and "array" not in declared_types:
            raise _parameter_intent_error(name, "array_not_allowed")
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            raise _parameter_intent_error(name, "array_items_not_declared")
        return [_coerce_parameter_scalar(item, item_schema, name=f"{name}[]") for item in value]
    if isinstance(value, dict):
        if declared_types and "object" not in declared_types:
            raise _parameter_intent_error(name, "object_not_allowed")
        raw_properties = schema.get("properties")
        if not isinstance(raw_properties, Mapping):
            raise _parameter_intent_error(name, "object_properties_not_declared")
        properties = {
            key: rules for key, rules in raw_properties.items() if isinstance(rules, Mapping)
        }
        unexpected = sorted(set(value) - set(properties))
        if unexpected:
            raise _parameter_intent_error(name, f"object_field_not_declared:{unexpected[0]}")
        return {
            key: _coerce_parameter_scalar(item, properties[key], name=f"{name}.{key}")
            for key, item in value.items()
        }
    raise _parameter_intent_error(name, "unsupported_value_type")


def _coerce_parameter_scalar(
    value: object,
    schema: Mapping[str, Any],
    *,
    name: str,
) -> float | str | bool:
    """Keep the compact contract shallow: arrays/objects may not nest further."""

    typed = _coerce_parameter_value(value, schema, name=name)
    if isinstance(typed, list | dict):
        raise _parameter_intent_error(name, "nested_container_not_supported")
    return typed
