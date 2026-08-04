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

import hashlib
import json
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_market_monitor.schemas.setup_authorization import (
    AuthorizedPatchOperation,
    ClarificationContract,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    FormulaKind,
    RequirementAssessmentV2,
    RequirementStateV2,
)
from ai_market_monitor.schemas.strict_mode import StrictModel as _StrictModel

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


class CapabilityRoutingCandidate(_StrictModel):
    key: str = Field(min_length=1, max_length=120)
    score: float = Field(ge=0)
    normalized_confidence: float = Field(ge=0, le=1)
    availability: str = Field(min_length=1, max_length=40)
    executable: bool
    source_fragment: str = Field(default="", max_length=500)


class CapabilityRoutingContext(_StrictModel):
    candidate_count: int = Field(ge=0)
    candidate_keys: list[str] = Field(default_factory=list, max_length=100)
    top_candidate_key: str | None = Field(default=None, max_length=120)
    top_candidate_score: float = Field(default=0, ge=0)
    selection_confidence: float = Field(default=0, ge=0, le=1)
    top_score_margin: float = Field(default=0, ge=0)
    unknown_terms: list[str] = Field(default_factory=list, max_length=50)
    candidates: list[CapabilityRoutingCandidate] = Field(default_factory=list, max_length=100)


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


def _bind_condition_tree_provenance(
    raw_node: dict[str, object],
    *,
    source_turn_id: str,
    source_fragment: str,
    node_id_seed: str | None = None,
    node_path: tuple[int, ...] = (),
    preserve_existing: bool = False,
) -> dict[str, object]:
    """Return a copied proposed tree with authoritative server-owned metadata."""

    node = dict(raw_node)
    if node_id_seed:
        rendered_path = ".".join(map(str, node_path)) or "root"
        node["node_id"] = (
            "condition_"
            + hashlib.sha256(f"{node_id_seed}:{rendered_path}".encode()).hexdigest()[:16]
        )
    proposed_turn = node.get("source_turn_id")
    proposed_fragment = node.get("source_fragment")
    inherited_provenance = (
        preserve_existing
        and isinstance(proposed_turn, str)
        and bool(proposed_turn.strip())
        and isinstance(proposed_fragment, str)
        and bool(proposed_fragment.strip())
    )
    if not inherited_provenance:
        node["source_turn_id"] = source_turn_id
        grounded_fragment = (
            proposed_fragment
            if isinstance(proposed_fragment, str)
            and proposed_fragment.strip()
            and " ".join(proposed_fragment.split()).casefold()
            in " ".join(source_fragment.split()).casefold()
            else source_fragment
        )
        node["source_fragment"] = grounded_fragment
    node["formula"] = _canonical_formula_identifier(node.get("formula"))
    node["operands"] = _canonicalize_core_operand_metadata(node)
    children = node.get("children")
    if isinstance(children, list):
        node["children"] = [
            _bind_condition_tree_provenance(
                child,
                source_turn_id=source_turn_id,
                source_fragment=source_fragment,
                node_id_seed=node_id_seed,
                node_path=(*node_path, index),
                preserve_existing=preserve_existing,
            )
            if isinstance(child, dict)
            else child
            for index, child in enumerate(children)
        ]
    return node


def _canonical_formula_identifier(value: object) -> object:
    """Normalize only one unambiguous known formula from malformed AI wrapping."""

    if isinstance(value, FormulaKind):
        return value.value
    if isinstance(value, str):
        return value
    known = {item.value for item in FormulaKind}
    found: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, str) and item in known:
            found.add(item)
        elif isinstance(item, dict):
            for key, nested in item.items():
                visit(key)
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return next(iter(found)) if len(found) == 1 else value


def _canonicalize_core_operand_metadata(
    raw_node: dict[str, object],
) -> list[object]:
    """Fill metadata fixed by a named core formula, never trader-controlled values."""

    operands = raw_node.get("operands")
    if operands is None:
        operands = []
    elif not isinstance(operands, list):
        return []
    formula = raw_node.get("formula")
    if not isinstance(formula, str):
        # Let ConditionNodeV2 reject the malformed formula. Canonical metadata must
        # never throw before schema validation can produce the classified failure.
        return list(operands)
    percentage_formulas: dict[object, dict[str, object]] = {
        "open_to_close_percentage": {
            "formula": "open_to_close",
            "reference_field": "open",
            "current_field": "close",
            "lookback": 1,
            "scale": "percent",
            "closed_only": True,
        },
        "close_to_close_percentage": {
            "formula": "close_to_close",
            "reference_field": "close",
            "current_field": "close",
            "lookback": 1,
            "scale": "percent",
            "closed_only": True,
        },
        "reference_to_current_percentage": {
            "formula": "reference_to_current",
            "scale": "percent",
        },
        "high_to_low_percentage": {
            "formula": "high_to_low",
            "reference_field": "high",
            "current_field": "low",
            "scale": "percent",
        },
        "low_to_high_percentage": {
            "formula": "low_to_high",
            "reference_field": "low",
            "current_field": "high",
            "scale": "percent",
        },
    }
    if formula in percentage_formulas:
        return [
            {
                "role": "measured_value",
                "kind": "market_metric",
                "name": "percentage_change",
                "unit": "percent",
                "parameters": percentage_formulas[formula],
            }
        ]
    if formula == "sweep_and_reclaim":
        return [
            {
                "role": "sweep_state",
                "kind": "market_metric",
                "name": "sweep_and_reclaim",
                "unit": "boolean",
                "parameters": {
                    "pierce_required": True,
                    "reclaim_required": True,
                },
            }
        ]
    canonical: list[object] = []
    for raw_operand in operands:
        if not isinstance(raw_operand, dict):
            canonical.append(raw_operand)
            continue
        operand = dict(raw_operand)
        if operand.get("kind") == "market_metric":
            parameters = (
                dict(operand["parameters"]) if isinstance(operand.get("parameters"), dict) else {}
            )
            if formula in percentage_formulas:
                if not operand.get("field") and not operand.get("name"):
                    operand["name"] = "percentage_change"
                for key, value in percentage_formulas[formula].items():
                    parameters.setdefault(key, value)
            elif formula == "sweep_and_reclaim":
                if not operand.get("field") and not operand.get("name"):
                    operand["name"] = "sweep_and_reclaim"
                parameters.setdefault("pierce_required", True)
                parameters.setdefault("reclaim_required", True)
            operand["parameters"] = parameters
        canonical.append(operand)
    return canonical


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
    # A user can answer one clarification with a complete multi-rule strategy turn.
    # Preserve the exact valid message instead of imposing a smaller hidden limit.
    answer_text: str = Field(
        min_length=1,
        max_length=STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    )
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
    #: Every state change, each naming the one segment that authorises it. This
    #: replaced a single free-floating patch: a patch grounded against the whole
    #: message let a value written inside a *question* justify a rule.
    operations: list[AuthorizedPatchOperation] = Field(default_factory=list, max_length=24)
    strategy_instructions: list[StrategyInstructionPlan] = Field(
        default_factory=list, max_length=24
    )
    clarification_answers: list[ClarificationAnswer] = Field(default_factory=list, max_length=8)
    questions_to_answer: list[str] = Field(default_factory=list, max_length=8)
    clarifications_to_ask: list[ClarificationRequest] = Field(default_factory=list, max_length=3)
    approval_intent: ApprovalIntent | None = None
    unsupported_segments: list[UnsupportedSegment] = Field(default_factory=list, max_length=12)
    response_points: list[ResponseDirective] = Field(default_factory=list, max_length=12)
    overall_confidence: float = Field(ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def bind_condition_provenance(cls, data: object) -> object:
        """Replace model-authored condition provenance with server-known spans.

        The planner chooses an authorizing segment, but it is not authoritative for
        provenance fields inside the proposed condition tree. Requiring it to repeat
        those fields caused otherwise valid strict-schema plans to fail before the
        deterministic operation-grounding layer could inspect them. The plan already
        carries the current turn id and exact segment spans, so bind those facts here.
        All executable condition fields remain subject to typed grounding later.
        """

        if not isinstance(data, dict):
            return data
        source_turn_id = data.get("source_turn_id")
        raw_segments = data.get("segments")
        raw_operations = data.get("operations")
        if (
            not isinstance(source_turn_id, str)
            or not isinstance(raw_segments, list)
            or not isinstance(raw_operations, list)
        ):
            return data
        segment_text = {
            item.get("segment_id"): item.get("exact_source_text")
            for item in raw_segments
            if isinstance(item, dict)
            and isinstance(item.get("segment_id"), str)
            and isinstance(item.get("exact_source_text"), str)
        }
        migrated = dict(data)
        operations: list[object] = []
        for item in raw_operations:
            if not isinstance(item, dict):
                operations.append(item)
                continue
            exact_text = segment_text.get(item.get("authorizing_segment_id"))
            if not exact_text:
                operations.append(item)
                continue
            operation = dict(item)
            if isinstance(item.get("condition"), dict):
                operation["condition"] = _bind_condition_tree_provenance(
                    item["condition"],
                    source_turn_id=source_turn_id,
                    source_fragment=exact_text,
                    node_id_seed=(
                        f"{source_turn_id}:{item.get('operation_id')}"
                        if item.get("kind") == "add_condition"
                        else None
                    ),
                    preserve_existing=item.get("kind") == "replace_groups",
                )
            if isinstance(item.get("unresolved"), dict):
                unresolved = dict(item["unresolved"])
                semantic_seed = (
                    f"{source_turn_id}:{item.get('authorizing_segment_id')}:"
                    f"{unresolved.get('target_type')}:"
                    f"{unresolved.get('target_condition_id') or ''}"
                )
                unresolved["semantic_object_id"] = (
                    f"object_{hashlib.sha256(semantic_seed.encode()).hexdigest()[:24]}"
                )
                operation["unresolved"] = unresolved
            operations.append(operation)
        migrated["operations"] = operations
        return migrated

    @model_validator(mode="after")
    def validate_internal_references(self) -> SetupAgentTurnPlan:
        ids = [segment.segment_id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("segment_id values must be unique within one plan")
        known = set(ids)
        operation_ids = [item.operation_id for item in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operation_id values must be unique within one plan")
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
        for operation in self.operations:
            if operation.authorizing_segment_id not in known:
                raise ValueError(
                    f"operation {operation.kind} names unknown segment "
                    f"{operation.authorizing_segment_id!r}"
                )
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
            self.operations
            or self.clarification_answers
            or self.unsupported_segments
            or any(item.action_required for item in self.segments)
        )


class AppliedInstruction(BaseModel):
    """One operation the server applied, and the canonical change it produced.

    ``summary`` is written from the before/after drafts, not from the model's own
    sentence about what it meant to do. ``condition_ids`` holds only the rules this
    operation actually touched — attaching the whole draft made a reply claim edits to
    rules the turn never mentioned.
    """

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=80)
    segment_id: str = Field(min_length=1, max_length=80)
    source_text: str = Field(min_length=1, max_length=STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH)
    summary: str = Field(min_length=1, max_length=400)
    condition_ids: list[str] = Field(default_factory=list, max_length=24)
    #: The operation kind that produced this change, for the operator trace.
    operation: str = Field(default="", max_length=60)
    #: Typed change records from `draft_diff`, the authoritative evidence.
    changes: list[dict[str, object]] = Field(default_factory=list, max_length=24)

    @model_validator(mode="before")
    @classmethod
    def migrate_missing_operation_id(cls, data: object) -> object:
        if not isinstance(data, dict) or data.get("operation_id"):
            return data
        migrated = dict(data)
        encoded = json.dumps(
            migrated,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        migrated["operation_id"] = f"legacy_{hashlib.sha256(encoded).hexdigest()[:24]}"
        return migrated


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


class OperationExecutionResult(BaseModel):
    """Canonical before/after evidence for one authorized operation."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=80)
    authorizing_segment_id: str = Field(min_length=1, max_length=80)
    operation_kind: str = Field(min_length=1, max_length=60)
    applied: bool
    rejected: bool
    before_executable_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    after_executable_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    workflow_revision_before: int = Field(ge=1)
    workflow_revision_after: int = Field(ge=1)
    changes: list[dict[str, object]] = Field(default_factory=list, max_length=100)
    affected_condition_ids: list[str] = Field(default_factory=list, max_length=100)
    safe_error: str | None = Field(default=None, max_length=500)


class ReconciledOperationRecord(BaseModel):
    """One operation judged against the turn's *final* state, not its own step.

    A step can be undone later in the same turn. Reporting each step's own diff let an
    add-then-remove announce a rule that no longer exists, and put its id into the
    references the next turn resolves.
    """

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=80)
    authorizing_segment_id: str = Field(min_length=1, max_length=80)
    operation_kind: str = Field(min_length=1, max_length=60)
    net_effect: Literal["effective", "overwritten", "cancelled", "no_net_effect", "rejected"]
    #: Only ids present or materially changed in the final draft.
    final_condition_ids: list[str] = Field(default_factory=list, max_length=100)
    summary: str = Field(default="", max_length=400)
    changes: list[dict[str, object]] = Field(default_factory=list, max_length=40)
    #: Exactly what this operation aimed at — a symbol, a condition id, a key, a field
    #: path. Persisted so the attribution can be audited instead of trusted.
    targets: list[dict[str, str]] = Field(default_factory=list, max_length=24)
    #: The later operation that replaced or undid this one. Recorded because
    #: "overwritten" with no explanation is not evidence a user can act on.
    superseded_by: str | None = Field(default=None, max_length=80)
    safe_error: str | None = Field(default=None, max_length=500)


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
    previous_executable_version: int = Field(ge=1)
    current_executable_version: int = Field(ge=1)
    previous_workflow_revision: int = Field(ge=1)
    current_workflow_revision: int = Field(ge=1)
    previous_executable_hash: str = Field(pattern=r"^$|^[a-f0-9]{64}$")
    current_executable_hash: str = Field(pattern=r"^$|^[a-f0-9]{64}$")
    semantic_diff: list[str] = Field(default_factory=list, max_length=200)
    applied_instructions: list[AppliedInstruction] = Field(default_factory=list, max_length=24)
    #: Per-operation before/after steps. Kept for audit; they are intermediate states
    #: and must not be the source of a user-facing claim.
    operation_results: list[OperationExecutionResult] = Field(
        default_factory=list,
        max_length=24,
    )
    #: The same operations judged against the turn's final state. This is what a reply
    #: and the next turn's references are allowed to use.
    reconciled_operations: list[ReconciledOperationRecord] = Field(
        default_factory=list,
        max_length=24,
    )
    ignored_non_actionable_segments: list[IgnoredSegment] = Field(
        default_factory=list, max_length=24
    )
    answered_questions: list[str] = Field(default_factory=list, max_length=100)
    requirement_states: list[RequirementStateV2] = Field(default_factory=list, max_length=2000)
    requirement_assessments: list[RequirementAssessmentV2] = Field(
        default_factory=list,
        max_length=100,
    )
    unresolved_fields: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    unsupported_requirements: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    semantic_violations: list[str] = Field(default_factory=list, max_length=100)
    compile_status: Literal["compiled", "blocked", "not_attempted", "failed"]
    #: Whether the Sharia policy and screened universe resolved. Every gate runs inside
    #: execution now: the reply used to be written from a *pre*-screening compile, so a
    #: message could announce a ready draft that screening then blocked.
    screening_status: Literal["passed", "blocked", "not_required", "not_attempted"] = "not_required"
    provider_status: Literal[
        "available",
        "unavailable",
        "runtime_unverified",
        "not_required",
    ] = "not_required"
    #: The status the chat will actually carry after this turn. Decided here, so the
    #: composer and the persisted session can never disagree.
    final_chat_status: str = Field(default="", max_length=40)
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
    #: The only questions the composer may ask. It cannot invent an executable one.
    allowed_clarifications: list[ClarificationContract] = Field(default_factory=list, max_length=6)
    #: Deterministic read model for answering questions about the current draft.
    draft_read_model: dict[str, object] = Field(default_factory=dict)
    #: The universe the Sharia resolver actually permitted, and the identity of that
    #: resolution. Cited by approval and by any reply that mentions the market scope.
    screening_evidence: dict[str, object] = Field(default_factory=dict)
    #: Exactly which market/timeframe pairs were checked, and under which promise.
    #: Without it, "runtime verified" was an unqualified claim one sample could satisfy.
    preflight_manifest: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_identity(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        aliases = {
            "previous_version": "previous_executable_version",
            "current_version": "current_executable_version",
            "previous_semantic_hash": "previous_executable_hash",
            "current_semantic_hash": "current_executable_hash",
        }
        for old, new in aliases.items():
            if old in migrated:
                migrated.setdefault(new, migrated.pop(old))
        migrated.setdefault(
            "previous_workflow_revision",
            migrated.get("previous_executable_version", 1),
        )
        migrated.setdefault(
            "current_workflow_revision",
            migrated.get("current_executable_version", 1),
        )
        return migrated

    @model_validator(mode="after")
    def validate_consistency(self) -> SetupTurnExecutionResult:
        if self.applied and not self.applied_instructions and not self.answered_questions:
            raise ValueError("an applied result must record what it applied")
        if (
            self.strategy_mutated
            and self.current_executable_version <= self.previous_executable_version
        ):
            raise ValueError("a mutated draft must carry a newer version")
        if (
            not self.strategy_mutated
            and self.current_executable_version != self.previous_executable_version
        ):
            raise ValueError("an unchanged draft cannot change version")
        if self.approval_eligible and self.compile_status != "compiled":
            raise ValueError("approval cannot be eligible before the draft compiles")
        if self.approval_eligible and self.screening_status == "blocked":
            raise ValueError("approval cannot be eligible while screening blocks the draft")
        if self.approval_eligible and self.provider_status in {
            "unavailable",
            "runtime_unverified",
        }:
            raise ValueError("approval cannot be eligible while a provider is unavailable")
        # An approval that survived this turn must not also be reported as newly
        # eligible or as invalidated: the three states are mutually exclusive facts.
        if self.approval_status == "approved" and self.strategy_mutated:
            raise ValueError("a mutated draft cannot keep its previous approval")
        if self.approval_status == "invalidated_by_edit" and not self.strategy_mutated:
            raise ValueError("approval cannot be invalidated without a material change")
        return self

    @property
    def previous_version(self) -> int:
        return self.previous_executable_version

    @property
    def current_version(self) -> int:
        return self.current_executable_version

    @property
    def previous_semantic_hash(self) -> str:
        return self.previous_executable_hash

    @property
    def current_semantic_hash(self) -> str:
        return self.current_executable_hash


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
    #: The full contract of the open question, so the next turn can check that an
    #: answer actually resolved the thing it claimed to.
    active_question: ClarificationContract | None = None
    #: Questions already answered in this draft. Prevents re-asking a closed one.
    answered_question_ids: list[str] = Field(default_factory=list, max_length=24)
    #: How many questions this draft has asked, against the per-draft limit.
    clarifications_asked: int = Field(default=0, ge=0)
    #: Server-owned language for all user-facing wording in this conversation. The
    #: model may suggest prose, but it may not silently switch this value.
    active_language: str = Field(default="en", min_length=2, max_length=12)
    #: The user goal currently being completed, for confusion recovery and routing.
    active_goal: str | None = Field(default=None, max_length=120)
    #: Read-only scan request awaiting one missing choice. This never authorises a
    #: strategy mutation or approval; it only preserves the user's scan goal.
    pending_read_only_scan: dict[str, object] = Field(default_factory=dict)
    #: A supported rule request whose user-controlled fields are not complete yet.
    #: Stored as language/context evidence only; executable state remains in the draft.
    pending_supported_request: dict[str, object] = Field(default_factory=dict)
    #: Fingerprint of the last rendered assistant response. Used to prevent a confusion
    #: signal from producing the same boilerplate again.
    last_response_fingerprint: str | None = Field(default=None, max_length=64)
    confusion_recovery_count: int = Field(default=0, ge=0)

    def with_question(self, contract: ClarificationContract) -> SetupConversationContext:
        return self.model_copy(
            update={
                "active_question_id": contract.question_id,
                "question_text": contract.question,
                "question_target": contract.target_field or contract.question_id,
                "valid_answer_shape": contract.expected_answer_schema,
                "active_question": contract,
                "clarifications_asked": self.clarifications_asked + 1,
            }
        )

    def cleared_question(self) -> SetupConversationContext:
        """Close the open question, remembering that it was answered.

        Recording the id is what stops the composer asking it again next turn.
        """
        answered = list(self.answered_question_ids)
        if self.active_question_id and self.active_question_id not in answered:
            answered.append(self.active_question_id)
        return self.model_copy(
            update={
                "active_question_id": None,
                "question_text": None,
                "question_target": None,
                "valid_answer_shape": None,
                "active_question": None,
                "answered_question_ids": answered[-24:],
            }
        )


#: What kind of thing a claim is about. Paired with a predicate and a value, this turns
#: "the sentence sounds right" into "the proposition matches the evidence".
ClaimSubjectType = Literal[
    "operation",
    "status",
    "condition",
    "universe",
    "provider",
    "approval",
    "open_item",
    "product",
]


class FactualClaim(_StrictModel):
    """One statement of fact, as a proposition the server can check.

    A reply mixes two different things. "Got it, that makes sense" asserts nothing and
    needs no evidence. "I set the RSI level to 30 and this is ready to approve" asserts
    two facts about the platform, and either could be wrong.

    Carrying evidence ids proved the claim *rested on something real*. It did not prove
    the sentence described that thing: a mutation claim could cite a genuinely effective
    operation and then describe a different one, and citing valid evidence for the wrong
    sentence passed every check.

    So a claim now states its proposition in fields — subject, predicate, value — and the
    server compares that proposition with the authoritative evidence. The wording is free
    and may be in any language; the proposition is what is checked.
    """

    model_config = ConfigDict(extra="forbid")

    #: Stable within one reply, so a refusal can name the claim it refused.
    claim_id: str = Field(default="", max_length=80)
    claim_type: Literal[
        "mutation",
        "readiness",
        "approval",
        "condition_explanation",
        "universe",
        "provider",
        "product_fact",
        "open_item",
    ]
    subject_type: ClaimSubjectType | None = None
    #: The evidence id the proposition is *about*, e.g. `operation:add_btc`.
    subject_id: str = Field(default="", max_length=160)
    #: What is being asserted about the subject, e.g. `symbol_included`.
    predicate: str = Field(default="", max_length=60)
    #: The asserted value, as text. Compared with the authoritative value, normalised.
    asserted_value: str = Field(default="", max_length=200)
    #: The sentence itself, in the user's language.
    text: str = Field(min_length=1, max_length=600)
    #: Ids from this turn's evidence ledger. At least one, and it has to be the right
    #: family for the claim type: readiness rests on gate statuses, a mutation on an
    #: operation that actually survived the turn.
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="before")
    @classmethod
    def accept_natural_language_alias(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        if "natural_language_text" in migrated:
            migrated.setdefault("text", migrated.pop("natural_language_text"))
        return migrated


class SetupAgentReply(_StrictModel):
    """What the composer model is allowed to return.

    ``message_without_question`` used to be here, and it was the whole message. That made
    the structured claims optional in practice: a model could put every fact in the free
    message field, return ``factual_claims: []``, and nothing was checked. The field is
    gone. The server builds the message from the two things below, so there is exactly one
    place a fact can live and it is validated.
    """

    model_config = ConfigDict(extra="forbid")

    #: Wording that asserts nothing about the platform: greeting, acknowledgement, an
    #: offer to help. Free, because there is nothing here that could be false.
    conversational_text: str = Field(default="", max_length=SETUP_REPLY_MAX_LENGTH)
    #: Every statement of fact, each carrying its proposition and its evidence. A claim
    #: that does not check out is dropped and replaced with text built from the evidence
    #: itself, so the user still learns what happened.
    factual_claims: list[FactualClaim] = Field(default_factory=list, max_length=12)
    #: The `question_id` of one server-authorised clarification, or null.
    #:
    #: The composer used to return a free-form question, so it could invent an
    #: executable clarification the server never agreed was needed — and re-ask one
    #: already answered. It may now only choose from `allowed_clarifications`.
    selected_clarification_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_reply(cls, data: object) -> object:
        """Accept older payloads without letting their free text become authoritative.

        A stored reply from before this change carries `message`/`message_without_question`.
        It is read as *conversational* text and still passes through claim validation,
        rather than being trusted as the final message.
        """
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        legacy = migrated.pop("message", None) or migrated.pop("message_without_question", None)
        if isinstance(legacy, str) and legacy.strip() and not migrated.get("conversational_text"):
            migrated["conversational_text"] = legacy
        if "clarification_question_id" in migrated:
            migrated.setdefault(
                "selected_clarification_id",
                migrated.pop("clarification_question_id"),
            )
        return migrated


class ComposedReply(_StrictModel):
    """The final assistant message, built by the server.

    Every factual sentence in ``message_without_question`` came from a claim that passed
    validation, or from deterministic text built out of the evidence. Nothing the model
    wrote reaches a user without going through one of those two paths.
    """

    model_config = ConfigDict(extra="forbid")

    message_without_question: str = Field(
        min_length=1,
        max_length=SETUP_REPLY_MAX_LENGTH,
    )
    conversational_text: str = Field(default="", max_length=SETUP_REPLY_MAX_LENGTH)
    #: Only the claims that were accepted.
    factual_claims: list[FactualClaim] = Field(default_factory=list, max_length=12)
    #: Why each refused claim was refused, for the operator trace.
    refused_claims: list[str] = Field(default_factory=list, max_length=12)
    selected_clarification_id: str | None = Field(default=None, max_length=120)

    @property
    def message(self) -> str:
        return self.message_without_question

    @property
    def clarification_question_id(self) -> str | None:
        return self.selected_clarification_id


class SetupAgentPlanEnvelope(_StrictModel):
    """The planner's whole answer: a reply, a plan, or both.

    A pure conversation turn returns ``direct_reply`` and no plan, and no tool runs.
    A mixed turn returns a plan; the reply is composed afterwards from what the
    server actually did, so it cannot describe an unapplied change.
    """

    model_config = ConfigDict(extra="forbid")

    plan: SetupAgentTurnPlan | None = None
    direct_reply: str | None = Field(default=None, max_length=SETUP_REPLY_MAX_LENGTH)

    @model_validator(mode="before")
    @classmethod
    def discard_harmless_misplaced_direct_reply_metadata(cls, data: object) -> object:
        """Recover a pure reply only when misplaced fields cannot encode mutation.

        Some otherwise schema-constrained provider responses put plan-only reply
        metadata beside ``direct_reply``. Refusing a greeting for an unused
        confidence score makes the authenticated chat unavailable without adding a
        safety boundary. Drop only the explicitly non-executable fields below, and
        only when ``plan`` is null and a real direct reply exists. Segments,
        operations, clarifications, approval intent, or any unknown key still fail
        strict validation instead of being silently discarded.
        """

        if not isinstance(data, dict):
            return data
        if data.get("plan") is not None or not str(data.get("direct_reply") or "").strip():
            return data
        harmless_metadata = {
            "plan",
            "direct_reply",
            "response_points",
            "questions_to_answer",
            "overall_confidence",
        }
        empty_plan_collections = {
            "segments",
            "operations",
            "strategy_instructions",
            "clarification_answers",
            "clarifications_to_ask",
            "unsupported_segments",
        }
        allowed = harmless_metadata | empty_plan_collections | {"approval_intent"}
        if not set(data).issubset(allowed):
            return data
        if any(data.get(key) not in (None, []) for key in empty_plan_collections):
            return data
        if data.get("approval_intent") is not None:
            return data
        return {"plan": None, "direct_reply": data.get("direct_reply")}

    @model_validator(mode="after")
    def validate_one_of(self) -> SetupAgentPlanEnvelope:
        if self.plan is None and not (self.direct_reply or "").strip():
            raise ValueError("the planner must return a plan, a direct reply, or both")
        return self
