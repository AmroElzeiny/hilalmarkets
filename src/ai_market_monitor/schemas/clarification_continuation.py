"""What the server will do with the answer, decided *before* the question is asked.

The defect this module removes
------------------------------

A question used to be asked on the strength of knowing what was missing. What would
happen when the trader answered was decided later, by whichever code path happened to
see the next turn — and for most question kinds that path was the planner. So the same
message went to a model twice: once to notice it was an answer, once to build the
operation. A model that had never seen the question read ``1h`` as a bare timeframe with
nothing attached, and the answer was lost.

A continuation is the missing half. It is built and validated at the moment the question
is created, and it says exactly:

* which canonical object the answer lands on;
* which authorized operation will carry it;
* what the draft looked like when the question was asked;
* which values are acceptable;
* what cancelling and replacing mean.

Filling one typed hole in a stored continuation needs no language understanding, so a
clarification answer never costs a model call, whichever builder asked the question.

This module holds the *types*. The operation builders live in
``engine/clarification_continuation.py`` — schemas may not import the engine, and the
builders need the authorized-operation schema, so the two are deliberately separate.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Final, Literal

from pydantic import Field, TypeAdapter, model_validator

from ai_market_monitor.schemas.strict_mode import StrictModel

__all__ = [
    "CONTINUATION_SCHEMA_VERSION",
    "DEFAULT_CANCELLATION_POLICY",
    "SUPPORTED_REQUEST_SCHEMA_KEY",
    "BooleanStructureContinuation",
    "CancellationPolicy",
    "STEP_FIELDS",
    "ClarificationCompletionContract",
    "clean_answer_shape",
    "completion_contract_from_metadata",
    "metadata_from_completion_contract",
    "CapabilityParameterContinuation",
    "ClarificationContinuation",
    "ClarificationTargetType",
    "ContinuationKind",
    "DraftFieldContinuation",
    "ExistingConditionFieldContinuation",
    "GovernedOptionContinuation",
    "NewConditionContinuation",
    "PendingScanContinuation",
    "ReferenceDefinitionContinuation",
    "ReplacementPolicy",
    "SupportedWorkflowContinuation",
    "UnsupportedResolutionContinuation",
    "answer_schema_text",
    "continuation_id_for",
    "validate_continuation",
]

#: Bumped when the meaning of a stored continuation changes. A record written under an
#: older version is migrated or paused; it is never reinterpreted under new rules.
CONTINUATION_SCHEMA_VERSION: Final[int] = 1


#: What kind of thing a clarification is asking about, so an answer can be checked
#: against the slot it claims to fill.
ClarificationTargetType = Literal[
    "conversational",
    "draft_field",
    "condition_field",
    "condition_creation",
    "universe",
    "market_scope",
    "sharia_policy",
    "boolean_structure",
    "capability_parameter",
    "reference_definition",
    "unsupported_requirement",
    "unsupported_resolution",
]


class CancellationPolicy(StrEnum):
    """What ``cancel`` must do to the canonical state behind one question.

    Cancelling used to mean only "stop showing the question". The blocker that caused
    it stayed in the draft, so the setup was still blocked for a reason nothing on
    screen mentioned any more — hidden blocked state a trader could not see or clear.

    So every question now says, when it is created, what cancelling it means. There are
    exactly three honest answers and none of them is "clear the question and say
    nothing about the blocker".
    """

    #: The trader is abandoning a rule they themselves started. The canonical blocker
    #: goes with it, and no half-built condition is created in its place.
    REMOVE_PENDING_REQUIREMENT = "remove_pending_requirement"
    #: The platform requires this before the setup can run — a screened universe, an
    #: approved methodology. It is put down, not thrown away: the blocker stays, and the
    #: reply says plainly that the setup is still incomplete and how to come back to it.
    PAUSE_PENDING_REQUIREMENT = "pause_pending_requirement"
    #: Nothing canonical is behind it. A read-only Scanner question is the whole family:
    #: dropping it changes no draft, no rule and no governed policy.
    CANCEL_CONVERSATION_ONLY = "cancel_conversation_only"


#: What cancelling means for a question that did not say. Chosen per target type and
#: deliberately fail-closed: anything the platform itself requires is *paused*, so a
#: cancellation can never quietly claim to have removed a requirement that is still
#: blocking the draft. Only questions whose blocker the trader authored are removable.
DEFAULT_CANCELLATION_POLICY: Final[dict[str, CancellationPolicy]] = {
    "conversational": CancellationPolicy.CANCEL_CONVERSATION_ONLY,
    "condition_creation": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "condition_field": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "boolean_structure": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "capability_parameter": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "reference_definition": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "unsupported_requirement": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "unsupported_resolution": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "draft_field": CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    "universe": CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    "market_scope": CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    "sharia_policy": CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
}


#: The JSON-Schema extension key old drafts kept their workflow progress under. Read
#: once, during migration, and never written again. Progress that lives inside an answer
#: *shape* is progress two code paths can disagree about — which is exactly what happened:
#: the reply was composed from a freshly derived step while the durable copy inside this
#: key could be rewound by reconciliation, so the assistant showed step two and validated
#: step one.
SUPPORTED_REQUEST_SCHEMA_KEY: Final[str] = "x-hilal-supported-request"

#: Values a completed step may hold. Deliberately narrow: a workflow stores answers, not
#: objects, so nothing structural can hide inside an "accepted value".
CompletionValue = str | int | float | bool | None


class ClarificationCompletionContract(StrictModel):
    """The one canonical record of a multi-step requirement in progress.

    Progress used to live in two writable places at once: this data inside an unresolved
    field's ``expected_answer_schema``, and a parallel copy on the conversation. Two
    writable copies of one fact is the defect class this repository keeps paying for —
    they were written at different moments and drifted, so a correct answer was validated
    against a step the trader was no longer looking at.

    So this is the authority, and it lives on the canonical draft where executable state
    belongs. ``PendingClarificationWorkflow`` is now a conversational *projection* of it:
    it carries this contract's id and hash, and a mismatch fails closed rather than
    picking a winner.
    """

    #: Stable for the life of the requirement. Matches the unresolved record's own id, so
    #: the conversational projection and the executable blocker are provably one thing.
    contract_id: str = Field(min_length=1, max_length=120)
    #: What is being completed. Routing and telemetry only; never a capability decision.
    workflow_kind: str = Field(default="supported_rule", min_length=1, max_length=60)
    #: Every field still to settle, in the exact order they will be asked. Stored whole
    #: rather than as "current plus the rest": rebuilding the order from two fields let
    #: the reconstructed list differ from the stored one, and the authorization gate
    #: compares those lists element by element.
    pending_fields: list[str] = Field(default_factory=list, max_length=24)
    #: Which of ``pending_fields`` is being asked right now.
    current_field: str = Field(default="", max_length=120)
    #: What the trader has settled, one entry per answered step.
    accepted_values: dict[str, CompletionValue] = Field(default_factory=dict)
    #: Facts grounded from the trader's original instruction — the direction, the size of
    #: the move, the mechanic. Server-owned and never re-asked.
    grounded_values: dict[str, CompletionValue] = Field(default_factory=dict)
    #: The markets this requirement named, when it named any.
    symbols: list[str] = Field(default_factory=list, max_length=1000)
    #: The trader's own words that opened and advanced this requirement, oldest first.
    evidence_fragments: list[str] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def one_authority_per_value(self) -> ClarificationCompletionContract:
        if self.pending_fields and self.current_field not in self.pending_fields:
            raise ValueError("the field being asked is not one of the fields still pending")
        both = sorted(set(self.accepted_values) & set(self.grounded_values))
        if both:
            # A value that is both "grounded from the instruction" and "chosen in a step"
            # has two writers, and the next answer would have to pick one of them.
            raise ValueError(f"these values have two writable owners: {both}")
        settled = sorted(set(self.accepted_values) & set(self.pending_fields))
        if settled:
            # An answered field that is still queued would be asked twice, and the second
            # answer would overwrite the first without anything saying so.
            raise ValueError(f"these values are answered and still queued: {settled}")
        return self

    @property
    def remaining_fields(self) -> list[str]:
        """The fields queued after the one being asked now."""

        return [item for item in self.pending_fields if item != self.current_field]

    @property
    def contract_hash(self) -> str:
        """A stable fingerprint of the canonical progress.

        The conversational projection carries this value. When the two differ, the
        projection is stale and is rebuilt from here — never the other way round.
        """

        payload = {
            "contract_id": self.contract_id,
            "workflow_kind": self.workflow_kind,
            "current_field": self.current_field,
            "pending_fields": list(self.pending_fields),
            "accepted_values": dict(sorted(self.accepted_values.items())),
            "grounded_values": dict(sorted(self.grounded_values.items())),
            "symbols": list(self.symbols),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()

    @property
    def flat_values(self) -> dict[str, Any]:
        """Both value maps as one plain reading, for code that consumes a rule's facts.

        A read model, never a write target. Everything that *writes* goes to one of the
        two typed maps above, which is what keeps "one canonical owner" true rather than
        merely intended.
        """

        return {**dict(self.grounded_values), **dict(self.accepted_values)}


#: The choices a clarification step can ask a trader to make. Everything else a
#: requirement holds was grounded from the original instruction and is never re-asked, so
#: the two maps have no overlap and no value has two writers.
STEP_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "trigger_timeframe",
        "reference_point",
        "comparator",
        "threshold",
        "capability_parameter",
    }
)

#: Keys of the old flat record that described *progress* rather than a value.
_STRUCTURAL_KEYS: Final[frozenset[str]] = frozenset(
    {"missing_slots", "next_field", "evidence_fragments", "symbols"}
)


def completion_contract_from_metadata(
    contract_id: str,
    metadata: Mapping[str, Any],
    *,
    workflow_kind: str = "supported_rule",
) -> ClarificationCompletionContract:
    """Build the canonical contract from the flat record the agent still speaks.

    One adapter, in one place. The flat shape is what the rule assembler and the
    authorization gate read; the typed contract is what is *stored*. Keeping the
    conversion here means there is exactly one rule for which map a value belongs to,
    instead of each caller deciding again.
    """

    missing = [str(item) for item in list(metadata.get("missing_slots") or [])]
    current = str(metadata.get("next_field") or (missing[0] if missing else ""))
    if current and current not in missing:
        # The flat record named a field it had already dropped from the queue. Trust the
        # queue: it is the list the authorization gate compares against.
        current = missing[0] if missing else ""
    accepted: dict[str, CompletionValue] = {}
    grounded: dict[str, CompletionValue] = {}
    for key, value in metadata.items():
        if key in _STRUCTURAL_KEYS or not isinstance(value, str | int | float | bool):
            continue
        if key in STEP_FIELDS and key not in missing:
            accepted[key] = value
        elif key not in STEP_FIELDS:
            grounded[key] = value
    return ClarificationCompletionContract(
        contract_id=contract_id,
        workflow_kind=workflow_kind,
        pending_fields=missing,
        current_field=current,
        accepted_values=accepted,
        grounded_values=grounded,
        symbols=[str(item) for item in list(metadata.get("symbols") or [])][:1000],
        evidence_fragments=[
            str(item)
            for item in list(metadata.get("evidence_fragments") or [])
            if str(item).strip()
        ][-24:],
    )


def metadata_from_completion_contract(
    contract: ClarificationCompletionContract | None,
) -> dict[str, Any]:
    """The canonical contract, read back in the flat shape callers already speak.

    Derived on every read and never stored. That is the whole point: a derived reading
    cannot drift from its source, and a second stored copy always eventually does.
    """

    if contract is None:
        return {}
    metadata: dict[str, Any] = dict(contract.flat_values)
    metadata["missing_slots"] = list(contract.pending_fields)
    if contract.current_field:
        metadata["next_field"] = contract.current_field
    if contract.symbols:
        metadata["symbols"] = list(contract.symbols)
    if contract.evidence_fragments:
        metadata["evidence_fragments"] = list(contract.evidence_fragments)
    return metadata


def clean_answer_shape(schema: Mapping[str, Any]) -> dict[str, Any]:
    """One answer schema with every trace of workflow progression removed."""

    return {key: value for key, value in schema.items() if key != SUPPORTED_REQUEST_SCHEMA_KEY}


class ContinuationKind(StrEnum):
    """Which deterministic completion a question is holding.

    Each member has exactly one registered builder. There is no "other" and no default:
    a question whose completion does not fit one of these is not a question this server
    is allowed to ask.
    """

    #: The agent's own multi-step rule workflow. One accepted value per step, and the
    #: finished rule is assembled from the canonical completion contract.
    SUPPORTED_WORKFLOW = "supported_workflow"
    #: One typed field on a rule that already exists in the draft.
    EXISTING_CONDITION_FIELD = "existing_condition_field"
    #: One typed field on a rule the answer will bring into existence.
    NEW_CONDITION = "new_condition"
    #: One field of the draft itself — its name, its exchange, its quote asset.
    DRAFT_FIELD = "draft_field"
    #: How several rules combine. Answerable only from a bounded set of stored shapes.
    BOOLEAN_STRUCTURE = "boolean_structure"
    #: One parameter of a registered mechanic on an existing rule.
    CAPABILITY_PARAMETER = "capability_parameter"
    #: What a named price level means, on an existing rule.
    REFERENCE_DEFINITION = "reference_definition"
    #: A requirement the platform cannot express. The only deterministic answer is to
    #: drop it; describing it differently is a new request, not an answer.
    UNSUPPORTED_RESOLUTION = "unsupported_resolution"
    #: A governed control. The answer is mapped to the application's own allowlisted
    #: option and applied by that route, never from chat text.
    GOVERNED_OPTION = "governed_option"
    #: A read-only Scanner question. Nothing in the strategy draft moves.
    PENDING_SCAN = "pending_scan"


class ReplacementPolicy(StrEnum):
    """What may happen to this question when the trader asks for something else.

    A new request used to simply win, and the question vanished with its blocker still
    in the draft — hidden blocked state. Every question now says which settlement it
    permits, so "I changed my mind" always leaves a state the trader can see.
    """

    #: Ask which the trader wants: finish this, or put it down and start the new thing.
    REQUIRE_EXPLICIT_CHOICE = "require_explicit_choice"
    #: Nothing canonical is behind it, so a new request may simply take over.
    REPLACE_SILENTLY = "replace_silently"


class ContinuationBase(StrictModel):
    """Everything every continuation must carry, whatever it completes."""

    #: Stable identity of this completion, independent of the question's wording.
    continuation_id: str = Field(min_length=1, max_length=120)
    question_id: str = Field(min_length=1, max_length=120)
    workflow_id: str | None = Field(default=None, max_length=120)
    step_revision: int = Field(default=0, ge=0)
    target_type: ClarificationTargetType
    target_field: str | None = Field(default=None, max_length=120)
    target_condition_id: str | None = Field(default=None, max_length=120)
    #: What the draft's executable state was when this question was asked. An answer
    #: arriving against a different one is refused rather than applied to a draft the
    #: question was never about. Empty means the draft had no executable content yet.
    expected_executable_hash: str = Field(default="", max_length=64)
    expected_workflow_state_hash: str = Field(default="", max_length=64)
    #: Every value this step may resolve to. Empty means the answer shape below is the
    #: only constraint — used for a free-text field such as a monitor's name.
    allowed_canonical_values: list[str] = Field(default_factory=list, max_length=64)
    #: Plain description of a usable answer. Shape only: never workflow progression.
    answer_schema: str = Field(default="", max_length=200)
    #: The trader's own words that opened and advanced this requirement, oldest first.
    source_evidence: list[str] = Field(default_factory=list, max_length=24)
    cancellation_policy: CancellationPolicy
    replacement_policy: ReplacementPolicy = ReplacementPolicy.REQUIRE_EXPLICIT_CHOICE
    schema_version: int = Field(default=CONTINUATION_SCHEMA_VERSION, ge=1)

    @property
    def operation_builder(self) -> str:
        """The registered builder that turns an answer into canonical operations."""

        return str(getattr(self, "kind", ""))

    def accepts(self, value: object) -> bool:
        """Whether this step can execute that exact canonical value."""

        if not self.allowed_canonical_values:
            return True
        return str(value) in set(self.allowed_canonical_values)


class SupportedWorkflowContinuation(ContinuationBase):
    """One step of the agent's own multi-step rule workflow."""

    kind: Literal[ContinuationKind.SUPPORTED_WORKFLOW] = ContinuationKind.SUPPORTED_WORKFLOW
    #: The canonical unresolved record this workflow is completing.
    unresolved_id: str = Field(min_length=1, max_length=120)
    #: Fields still to ask about after this one, in the order they will be asked.
    remaining_fields: list[str] = Field(default_factory=list, max_length=24)


class ExistingConditionFieldContinuation(ContinuationBase):
    """One typed field on a rule the draft already holds.

    ``condition_template`` is the rule exactly as it was when the question was asked —
    the partial canonical object the answer completes. It is stored rather than re-read
    so that a draft edited in between fails the identity check instead of silently
    applying the answer to a rule the trader was never shown.
    """

    kind: Literal[ContinuationKind.EXISTING_CONDITION_FIELD] = (
        ContinuationKind.EXISTING_CONDITION_FIELD
    )
    condition_template: dict[str, Any]
    #: The one field on that rule this answer fills.
    field_path: str = Field(min_length=1, max_length=120)


class NewConditionContinuation(ContinuationBase):
    """One typed field on a rule that does not exist yet, plus the rule it completes."""

    kind: Literal[ContinuationKind.NEW_CONDITION] = ContinuationKind.NEW_CONDITION
    condition_template: dict[str, Any]
    field_path: str = Field(min_length=1, max_length=120)
    #: The canonical blocker closed when the rule is created.
    unresolved_id: str = Field(min_length=1, max_length=120)


class DraftFieldContinuation(ContinuationBase):
    """One field of the draft itself."""

    kind: Literal[ContinuationKind.DRAFT_FIELD] = ContinuationKind.DRAFT_FIELD
    field_name: str = Field(min_length=1, max_length=120)
    unresolved_id: str = Field(default="", max_length=120)


class BooleanStructureContinuation(ContinuationBase):
    """How several rules combine, chosen from stored shapes.

    Every acceptable answer maps to one complete stored topology. A topology cannot be
    derived from a word, so a question with nothing stored here is not askable — which
    is the whole point: the alternative was to hand "any of them" to a model and hope.
    """

    kind: Literal[ContinuationKind.BOOLEAN_STRUCTURE] = ContinuationKind.BOOLEAN_STRUCTURE
    #: canonical answer -> the complete group tree that answer selects.
    topology_by_value: dict[str, dict[str, Any]] = Field(default_factory=dict)
    unresolved_id: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def every_answer_has_a_shape(self) -> BooleanStructureContinuation:
        missing = [
            item for item in self.allowed_canonical_values if item not in self.topology_by_value
        ]
        if missing:
            raise ValueError(f"these choices have no stored shape: {sorted(missing)}")
        if not self.topology_by_value:
            raise ValueError("a boolean-structure question needs at least one stored shape")
        return self


class CapabilityParameterContinuation(ContinuationBase):
    """One parameter of a registered mechanic on an existing rule."""

    kind: Literal[ContinuationKind.CAPABILITY_PARAMETER] = ContinuationKind.CAPABILITY_PARAMETER
    condition_template: dict[str, Any]
    parameter_name: str = Field(min_length=1, max_length=120)


class ReferenceDefinitionContinuation(ContinuationBase):
    """What a named price level means, on an existing rule."""

    kind: Literal[ContinuationKind.REFERENCE_DEFINITION] = ContinuationKind.REFERENCE_DEFINITION
    condition_template: dict[str, Any]


class UnsupportedResolutionContinuation(ContinuationBase):
    """A requirement the platform cannot express exactly.

    The only answer this continuation can execute is dropping it. A different wording of
    the same rule is a fresh instruction and goes through planning as one — it is not an
    answer that this question can apply, and pretending otherwise is how a near miss got
    compiled in place of what the trader asked for.
    """

    kind: Literal[ContinuationKind.UNSUPPORTED_RESOLUTION] = (
        ContinuationKind.UNSUPPORTED_RESOLUTION
    )
    unsupported_key: str = Field(min_length=1, max_length=120)


class GovernedOptionContinuation(ContinuationBase):
    """A governed control, answered in words and applied by the allowlisted route.

    The mapping is stored, not inferred. Routing a governed answer by recognising a
    question id was a second, invisible registry: a new governed question was governed
    only if somebody remembered to add its id to a constant, and if nobody did, chat text
    moved Sharia policy. Here it is a field of the question itself.
    """

    kind: Literal[ContinuationKind.GOVERNED_OPTION] = ContinuationKind.GOVERNED_OPTION
    #: The application's own allowlisted control that owns this change.
    option_key: str = Field(min_length=1, max_length=80)
    #: canonical answer -> the exact value that control expects. Empty means the
    #: canonical answer *is* that value.
    option_value_by_answer: dict[str, str] = Field(default_factory=dict)

    def option_value_for(self, value: object) -> str:
        return self.option_value_by_answer.get(str(value), str(value))


class PendingScanContinuation(ContinuationBase):
    """A read-only Scanner question. No executable draft state moves, ever."""

    kind: Literal[ContinuationKind.PENDING_SCAN] = ContinuationKind.PENDING_SCAN
    #: The field of the pending scan request this answer fills.
    scan_field: str = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def read_only(self) -> PendingScanContinuation:
        if self.cancellation_policy is CancellationPolicy.REMOVE_PENDING_REQUIREMENT:
            raise ValueError("a read-only scan question has no canonical requirement to remove")
        return self


#: The typed union. Deliberately not a dictionary: a continuation that can be any shape
#: is a continuation nothing can validate, and validating it before the question is
#: shown is the entire safety property this file provides.
ClarificationContinuation = Annotated[
    SupportedWorkflowContinuation
    | ExistingConditionFieldContinuation
    | NewConditionContinuation
    | DraftFieldContinuation
    | BooleanStructureContinuation
    | CapabilityParameterContinuation
    | ReferenceDefinitionContinuation
    | UnsupportedResolutionContinuation
    | GovernedOptionContinuation
    | PendingScanContinuation,
    Field(discriminator="kind"),
]

_ADAPTER: Final[TypeAdapter[Any]] = TypeAdapter(ClarificationContinuation)


def continuation_id_for(*parts: object) -> str:
    """A stable identity for one completion, from the facts that define it."""

    digest = hashlib.sha256("|".join(str(item) for item in parts).encode()).hexdigest()
    return f"cont_{digest[:24]}"


def answer_schema_text(shape: Mapping[str, object]) -> str:
    """One compact JSON description of an answer's *shape*, and nothing else.

    Shape only, deliberately. Accepted values, remaining fields and operation intent are
    workflow progression: they belong to the canonical completion contract, and keeping
    a second copy in a JSON-Schema extension is what let the two disagree.
    """

    return json.dumps(dict(shape), sort_keys=True, separators=(",", ":"))[:200]


def validate_continuation(payload: object) -> Any:
    """Parse a stored continuation, or refuse it. Never a partially trusted object."""

    return _ADAPTER.validate_python(payload)
