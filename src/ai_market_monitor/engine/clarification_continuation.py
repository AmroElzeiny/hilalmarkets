"""How one stored continuation becomes canonical operations, with no model call.

The types live in ``schemas/clarification_continuation.py``. This is the half that
*acts*: one registered builder per continuation kind, and a registry that fails closed
at import so a kind can never reach a trader as a question whose answer has nowhere to
go.

Nothing here interprets language. It receives a canonical value that the deterministic
answer resolver already produced, checks that the question really promised to accept it,
and fills the one typed hole the question was asked about. That is why a clarification
answer costs nothing: there is no reading left to do.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from ai_market_monitor.schemas.clarification_continuation import (
    CONTINUATION_SCHEMA_VERSION,
    BooleanStructureContinuation,
    CancellationPolicy,
    CapabilityParameterContinuation,
    ContinuationBase,
    ContinuationKind,
    DraftFieldContinuation,
    ExistingConditionFieldContinuation,
    GovernedOptionContinuation,
    NewConditionContinuation,
    PendingScanContinuation,
    ReferenceDefinitionContinuation,
    ReplacementPolicy,
    SupportedWorkflowContinuation,
    UnsupportedResolutionContinuation,
    continuation_id_for,
)
from ai_market_monitor.schemas.setup_authorization import AuthorizedPatchOperation
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeV2,
    DraftFieldPatch,
    StrategyDraftV2,
)

__all__ = [
    "CLARIFICATION_ANSWER_SEGMENT_ID",
    "ContinuationAnswer",
    "ContinuationRefused",
    "build_continuation_operations",
    "continuation_for_unresolved",
    "continuation_is_deterministic",
    "delegated_kinds",
    "governed_continuation",
    "governed_option_selection",
    "is_delegated",
    "mutates_executable_draft",
    "registered_builders",
    "scan_continuation",
    "workflow_continuation",
]

#: Every operation a continuation builds names this one segment. The segment's text is
#: the trader's own words, supplied by the caller; the id is fixed so the authorization
#: gate always knows which span authorised a clarification answer.
CLARIFICATION_ANSWER_SEGMENT_ID: Final[str] = "clarification_answer"


class ContinuationRefused(ValueError):
    """The stored continuation cannot produce an operation for this answer.

    Raised, never swallowed into a default. Every reason is a state the trader must be
    told about honestly: the draft moved under the question, the value is not one this
    step can execute, or the stored record is from an older schema.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ContinuationAnswer:
    """One canonical value, and the trader's own words behind it.

    ``evidence`` is what makes the value groundable. A confirmed near miss is written
    across two turns — ``qh`` and then ``yes`` — and the words that carry the value are
    the first ones. Storing "yes" as the evidence for ``1h`` is what the grounding gate
    correctly refuses, so both spans travel together.
    """

    canonical_value: str | float | int | bool
    evidence: str
    #: The earlier words a confirmation is standing in for, when there are any.
    proposal_evidence: str = ""

    @property
    def evidence_spans(self) -> tuple[str, ...]:
        spans = [item for item in (self.proposal_evidence, self.evidence) if item.strip()]
        return tuple(dict.fromkeys(spans))


def _operation_id(prefix: str, continuation: ContinuationBase) -> str:
    return f"{prefix}_{continuation.continuation_id}"[:80]


def _template_node(
    template: Mapping[str, Any],
    *,
    updates: Mapping[str, Any],
) -> ConditionNodeV2:
    """The stored partial rule with one typed hole filled, validated as canonical.

    Validation happens here rather than at execution time so that a continuation which
    cannot produce a legal rule is caught while the question is being *created*, not
    after the trader has answered it.
    """

    merged = {**dict(template), **dict(updates)}
    return ConditionNodeV2.model_validate(merged)


def _build_supported_workflow(
    continuation: ContinuationBase,
    answer: ContinuationAnswer,
    draft: StrategyDraftV2,
) -> tuple[AuthorizedPatchOperation, ...]:
    """The multi-step workflow keeps its own assembler; this proves it is reachable.

    The workflow's own step machinery already accepts a value, advances the record and
    builds the finished rule without a model call. Returning no operations here means
    "the workflow owns this write", not "nothing happens" — the caller routes to it and
    an invariant test proves that route exists for every workflow continuation.
    """

    del continuation, answer, draft
    return ()


def _build_existing_condition_field(
    continuation: ContinuationBase,
    answer: ContinuationAnswer,
    draft: StrategyDraftV2,
) -> tuple[AuthorizedPatchOperation, ...]:
    item = cast(ExistingConditionFieldContinuation, continuation)
    del draft
    node = _template_node(
        item.condition_template,
        updates={item.field_path: answer.canonical_value},
    )
    return (
        AuthorizedPatchOperation(
            operation_id=_operation_id("answer", item),
            authorizing_segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
            kind="update_condition",
            target_condition_id=item.target_condition_id or node.node_id,
            condition=node,
        ),
    )


def _build_capability_parameter(
    continuation: ContinuationBase,
    answer: ContinuationAnswer,
    draft: StrategyDraftV2,
) -> tuple[AuthorizedPatchOperation, ...]:
    item = cast(CapabilityParameterContinuation, continuation)
    del draft
    template = dict(item.condition_template)
    raw = template.get("capability_parameters")
    parameters = dict(cast(Mapping[str, Any], raw)) if isinstance(raw, Mapping) else {}
    parameters[item.parameter_name] = answer.canonical_value
    node = _template_node(template, updates={"capability_parameters": parameters})
    return (
        AuthorizedPatchOperation(
            operation_id=_operation_id("answer", item),
            authorizing_segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
            kind="update_condition",
            target_condition_id=item.target_condition_id or node.node_id,
            condition=node,
        ),
    )


def _build_reference_definition(
    continuation: ContinuationBase,
    answer: ContinuationAnswer,
    draft: StrategyDraftV2,
) -> tuple[AuthorizedPatchOperation, ...]:
    item = cast(ReferenceDefinitionContinuation, continuation)
    del draft
    node = _template_node(
        item.condition_template,
        updates={"reference_definition": str(answer.canonical_value)},
    )
    return (
        AuthorizedPatchOperation(
            operation_id=_operation_id("answer", item),
            authorizing_segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
            kind="update_condition",
            target_condition_id=item.target_condition_id or node.node_id,
            condition=node,
        ),
    )


def _build_new_condition(
    continuation: ContinuationBase,
    answer: ContinuationAnswer,
    draft: StrategyDraftV2,
) -> tuple[AuthorizedPatchOperation, ...]:
    item = cast(NewConditionContinuation, continuation)
    del draft
    node = _template_node(
        item.condition_template,
        updates={item.field_path: answer.canonical_value},
    )
    return (
        AuthorizedPatchOperation(
            operation_id=_operation_id("create", item),
            authorizing_segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
            kind="add_condition",
            condition=node,
        ),
        AuthorizedPatchOperation(
            operation_id=_operation_id("close", item),
            authorizing_segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
            kind="resolve_unresolved_key",
            target_key=item.unresolved_id,
        ),
    )


def _build_draft_field(
    continuation: ContinuationBase,
    answer: ContinuationAnswer,
    draft: StrategyDraftV2,
) -> tuple[AuthorizedPatchOperation, ...]:
    item = cast(DraftFieldContinuation, continuation)
    del draft
    patch = DraftFieldPatch.model_validate({item.field_name: answer.canonical_value})
    operations = [
        AuthorizedPatchOperation(
            operation_id=_operation_id("answer", item),
            authorizing_segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
            kind="set_fields",
            fields=patch,
        )
    ]
    if item.unresolved_id:
        operations.append(
            AuthorizedPatchOperation(
                operation_id=_operation_id("close", item),
                authorizing_segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
                kind="resolve_unresolved_key",
                target_key=item.unresolved_id,
            )
        )
    return tuple(operations)


def _build_boolean_structure(
    continuation: ContinuationBase,
    answer: ContinuationAnswer,
    draft: StrategyDraftV2,
) -> tuple[AuthorizedPatchOperation, ...]:
    item = cast(BooleanStructureContinuation, continuation)
    del draft
    topology = item.topology_by_value.get(str(answer.canonical_value))
    if topology is None:
        raise ContinuationRefused(
            "CONTINUATION_VALUE_NOT_EXECUTABLE",
            "That choice has no stored shape, so nothing may be built from it.",
        )
    operations = [
        AuthorizedPatchOperation(
            operation_id=_operation_id("answer", item),
            authorizing_segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
            kind="replace_groups",
            condition=ConditionNodeV2.model_validate(topology),
        )
    ]
    if item.unresolved_id:
        operations.append(
            AuthorizedPatchOperation(
                operation_id=_operation_id("close", item),
                authorizing_segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
                kind="resolve_unresolved_key",
                target_key=item.unresolved_id,
            )
        )
    return tuple(operations)


def _build_unsupported_resolution(
    continuation: ContinuationBase,
    answer: ContinuationAnswer,
    draft: StrategyDraftV2,
) -> tuple[AuthorizedPatchOperation, ...]:
    item = cast(UnsupportedResolutionContinuation, continuation)
    del answer, draft
    return (
        AuthorizedPatchOperation(
            operation_id=_operation_id("drop", item),
            authorizing_segment_id=CLARIFICATION_ANSWER_SEGMENT_ID,
            kind="remove_unsupported_key",
            target_key=item.unsupported_key,
        ),
    )


def _build_governed_option(
    continuation: ContinuationBase,
    answer: ContinuationAnswer,
    draft: StrategyDraftV2,
) -> tuple[AuthorizedPatchOperation, ...]:
    """Governed policy is never written from chat text, so this builds nothing.

    The answer is carried to the application's own allowlisted control by
    :func:`governed_option_selection`, and that route builds the operation with its own
    provenance. Building operations here would be a second way to move Sharia policy.
    """

    del continuation, answer, draft
    return ()


def _build_pending_scan(
    continuation: ContinuationBase,
    answer: ContinuationAnswer,
    draft: StrategyDraftV2,
) -> tuple[AuthorizedPatchOperation, ...]:
    """A Scanner answer changes no strategy state. Zero operations is the whole point."""

    del continuation, answer, draft
    return ()


_Builder = Callable[
    [ContinuationBase, ContinuationAnswer, StrategyDraftV2],
    tuple[AuthorizedPatchOperation, ...],
]

#: One builder per kind. Checked exhaustive at import: a kind added without a builder
#: stops the process from starting rather than producing a question whose answer has
#: nowhere to go.
_BUILDERS: Final[dict[ContinuationKind, _Builder]] = {
    ContinuationKind.SUPPORTED_WORKFLOW: _build_supported_workflow,
    ContinuationKind.EXISTING_CONDITION_FIELD: _build_existing_condition_field,
    ContinuationKind.NEW_CONDITION: _build_new_condition,
    ContinuationKind.DRAFT_FIELD: _build_draft_field,
    ContinuationKind.BOOLEAN_STRUCTURE: _build_boolean_structure,
    ContinuationKind.CAPABILITY_PARAMETER: _build_capability_parameter,
    ContinuationKind.REFERENCE_DEFINITION: _build_reference_definition,
    ContinuationKind.UNSUPPORTED_RESOLUTION: _build_unsupported_resolution,
    ContinuationKind.GOVERNED_OPTION: _build_governed_option,
    ContinuationKind.PENDING_SCAN: _build_pending_scan,
}

_MISSING_BUILDERS: Final[tuple[str, ...]] = tuple(
    sorted(str(item) for item in ContinuationKind if item not in _BUILDERS)
)
if _MISSING_BUILDERS:  # pragma: no cover - a build-time contract, not a runtime branch
    raise RuntimeError(
        "every continuation kind needs a registered operation builder; missing: "
        + ", ".join(_MISSING_BUILDERS)
    )

#: The kinds that route away from chat rather than building operations here. Named so a
#: test can tell "this builder returns nothing because another authority owns the write"
#: apart from "this builder forgot to build anything".
_DELEGATED_KINDS: Final[frozenset[ContinuationKind]] = frozenset(
    {
        ContinuationKind.SUPPORTED_WORKFLOW,
        ContinuationKind.GOVERNED_OPTION,
        ContinuationKind.PENDING_SCAN,
    }
)

#: Kinds whose answer changes executable strategy state. Read by the transition planner
#: so a reply can never claim a rule changed when the answer only moved a scan.
_EXECUTABLE_KINDS: Final[frozenset[ContinuationKind]] = frozenset(
    {
        ContinuationKind.SUPPORTED_WORKFLOW,
        ContinuationKind.EXISTING_CONDITION_FIELD,
        ContinuationKind.NEW_CONDITION,
        ContinuationKind.DRAFT_FIELD,
        ContinuationKind.BOOLEAN_STRUCTURE,
        ContinuationKind.CAPABILITY_PARAMETER,
        ContinuationKind.REFERENCE_DEFINITION,
        ContinuationKind.UNSUPPORTED_RESOLUTION,
        ContinuationKind.GOVERNED_OPTION,
    }
)


def registered_builders() -> frozenset[str]:
    """Every continuation kind this server can complete."""

    return frozenset(str(item) for item in _BUILDERS)


def delegated_kinds() -> frozenset[str]:
    """Kinds whose canonical write belongs to a different named authority."""

    return frozenset(str(item) for item in _DELEGATED_KINDS)


def continuation_is_deterministic(continuation: object) -> bool:
    """Whether an answer here is applied without any model call.

    True for every registered kind. It is a function rather than an assumption so the
    claim is testable, and so a future kind that needs a model fails that test instead
    of quietly reintroducing a paid clarification.
    """

    return getattr(continuation, "kind", None) in _BUILDERS


def mutates_executable_draft(continuation: object) -> bool:
    """Whether applying an answer here may change executable strategy state."""

    return getattr(continuation, "kind", None) in _EXECUTABLE_KINDS


def is_delegated(continuation: object) -> bool:
    """Whether another named authority performs the canonical write for this kind."""

    return getattr(continuation, "kind", None) in _DELEGATED_KINDS


def governed_option_selection(
    continuation: object,
    value: object,
) -> tuple[str, str] | None:
    """The allowlisted control and value a governed answer maps to, or ``None``.

    Read from the stored continuation, never from the question's id. Recognising a
    governed question by its id was a second registry nobody could see: a new governed
    question was governed only if somebody remembered to add its id to a constant.
    """

    if not isinstance(continuation, GovernedOptionContinuation):
        return None
    return continuation.option_key, continuation.option_value_for(value)


#: Which allowlisted control owns each governed field. Read when a question about
#: governed policy is created, so the mapping travels *with* the question instead of
#: being rediscovered later by whatever code happens to see the answer.
_GOVERNED_CONTROL_BY_FIELD: Final[dict[str, str]] = {
    "universe_mode": "screened_universe_mode",
    "sharia_policy.universe_mode": "screened_universe_mode",
    "methodology_id": "sharia_methodology",
    "sharia_policy.methodology_id": "sharia_methodology",
    "approved_watchlist_id": "screened_watchlist",
    "sharia_policy.approved_watchlist_id": "screened_watchlist",
    "explicit_symbols": "screened_explicit_assets",
    "sharia_policy.explicit_symbols": "screened_explicit_assets",
}

#: Fields of the draft itself that a question may fill. Anything else cannot be written
#: by ``set_fields``, so a question about it is not deterministically completable and is
#: not asked.
_DRAFT_FIELDS: Final[frozenset[str]] = frozenset(DraftFieldPatch.model_fields)


def governed_control_for(field_name: str, default: str = "") -> str:
    """The allowlisted control that owns one governed field, or ``default``."""

    return _GOVERNED_CONTROL_BY_FIELD.get(str(field_name or ""), default)


def _node_by_id(draft: StrategyDraftV2, node_id: str) -> ConditionNodeV2 | None:
    """One rule, found anywhere in the draft's tree."""

    def walk(node: ConditionNodeV2 | None) -> ConditionNodeV2 | None:
        if node is None:
            return None
        if node.node_id == node_id:
            return node
        for child in node.children:
            found = walk(child)
            if found is not None:
                return found
        return None

    return walk(draft.condition_ast)


def continuation_for_unresolved(
    item: Any,
    draft: StrategyDraftV2,
    *,
    question_id: str,
    step_revision: int,
    cancellation_policy: CancellationPolicy,
    allowed_values: Sequence[str] = (),
    answer_schema: str = "",
) -> Any | None:
    """The deterministic completion for one open blocker, or ``None``.

    ``None`` is a real answer and the important one: it means this server cannot promise
    to apply an answer to that blocker without asking a model, so **the question is not
    asked**. The blocker stays visible and is reported as operationally blocked. Asking
    anyway is what produced questions whose correct answers had nowhere to go.
    """

    target_type = str(getattr(item, "target_type", ""))
    key = str(getattr(item, "unresolved_id", "") or getattr(item, "key", ""))
    if not key:
        return None
    field_name = str(getattr(item, "target_field", "") or "")
    condition_id = str(getattr(item, "target_condition_id", "") or "")
    shared: dict[str, Any] = {
        "continuation_id": continuation_id_for(key, question_id, step_revision),
        "question_id": question_id,
        "step_revision": step_revision,
        "target_type": target_type,
        "target_field": field_name or None,
        "target_condition_id": condition_id or None,
        "expected_executable_hash": draft.executable_hash,
        "expected_workflow_state_hash": draft.workflow_state_hash,
        "allowed_canonical_values": list(allowed_values)[:64],
        "answer_schema": answer_schema[:200],
        "source_evidence": [str(getattr(item, "source_fragment", "") or "")][:24],
        "cancellation_policy": cancellation_policy,
        "replacement_policy": ReplacementPolicy.REQUIRE_EXPLICIT_CHOICE,
    }

    if target_type == "condition_creation":
        contract = getattr(item, "completion_contract", None)
        if contract is None:
            # Nothing records what this rule already knows, so no answer can finish it
            # deterministically. The blocker stays; the question does not get asked.
            return None
        return SupportedWorkflowContinuation(
            **shared,
            workflow_id=key,
            unresolved_id=key,
            remaining_fields=list(contract.remaining_fields),
        )

    if target_type == "draft_field":
        if field_name not in _DRAFT_FIELDS:
            return None
        return DraftFieldContinuation(**shared, field_name=field_name, unresolved_id=key)

    if target_type in {"condition_field", "capability_parameter", "reference_definition"}:
        node = _node_by_id(draft, condition_id) if condition_id else None
        if node is None:
            return None
        template = node.model_dump(mode="json")
        if target_type == "capability_parameter":
            return CapabilityParameterContinuation(
                **shared, condition_template=template, parameter_name=field_name or "value"
            )
        if target_type == "reference_definition":
            return ReferenceDefinitionContinuation(**shared, condition_template=template)
        if field_name not in ConditionNodeV2.model_fields:
            return None
        return ExistingConditionFieldContinuation(
            **shared, condition_template=template, field_path=field_name
        )

    if target_type in {"universe", "market_scope", "sharia_policy"}:
        control = _GOVERNED_CONTROL_BY_FIELD.get(field_name)
        if control is None and target_type in {"universe", "market_scope"}:
            control = "screened_universe_mode"
        if control is None:
            return None
        return GovernedOptionContinuation(**shared, option_key=control)

    if target_type in {"unsupported_requirement", "unsupported_resolution"}:
        return UnsupportedResolutionContinuation(**shared, unsupported_key=key)

    # boolean_structure and anything new: a topology cannot be derived from a word, so
    # there is no honest continuation and the question is not asked.
    return None


def governed_continuation(
    *,
    question_id: str,
    step_revision: int,
    target_type: str,
    target_field: str,
    option_key: str,
    allowed_values: Sequence[str],
    draft: StrategyDraftV2,
    cancellation_policy: CancellationPolicy,
    option_value_by_answer: Mapping[str, str] | None = None,
    replacement_policy: ReplacementPolicy = ReplacementPolicy.REQUIRE_EXPLICIT_CHOICE,
) -> GovernedOptionContinuation:
    """A governed question that carries its own route to the allowlisted control."""

    return GovernedOptionContinuation(
        continuation_id=continuation_id_for(option_key, question_id, step_revision),
        question_id=question_id,
        step_revision=step_revision,
        target_type=cast(Any, target_type),
        target_field=target_field or None,
        expected_executable_hash=draft.executable_hash,
        expected_workflow_state_hash=draft.workflow_state_hash,
        allowed_canonical_values=list(allowed_values)[:64],
        cancellation_policy=cancellation_policy,
        replacement_policy=replacement_policy,
        option_key=option_key,
        option_value_by_answer=dict(option_value_by_answer or {}),
    )


def scan_continuation(
    *,
    question_id: str,
    target_field: str,
    scan_field: str,
    allowed_values: Sequence[str],
    answer_schema: str = "",
) -> PendingScanContinuation:
    """A read-only Scanner question. Nothing in the strategy draft can move.

    ``target_field`` is what the question is *about* in the answer resolver's vocabulary;
    ``scan_field`` is the key the answer is written under inside the pending scan. They
    are usually different words for the same choice, and conflating them made the stored
    plan disagree with the question that carried it.
    """

    return PendingScanContinuation(
        continuation_id=continuation_id_for("scan", question_id, scan_field),
        question_id=question_id,
        target_type="conversational",
        target_field=target_field,
        allowed_canonical_values=list(allowed_values)[:64],
        answer_schema=answer_schema[:200],
        cancellation_policy=CancellationPolicy.CANCEL_CONVERSATION_ONLY,
        # A scan holds nothing canonical, so a different request may simply take over.
        replacement_policy=ReplacementPolicy.REPLACE_SILENTLY,
        scan_field=scan_field,
    )


def workflow_continuation(
    *,
    question_id: str,
    workflow_id: str,
    step_revision: int,
    current_field: str,
    remaining_fields: Sequence[str],
    allowed_values: Sequence[str],
    answer_schema: str,
    source_evidence: Sequence[str],
    draft: StrategyDraftV2,
    mutating: bool = True,
) -> SupportedWorkflowContinuation:
    """One step of the agent's own rule workflow, as a stored completion."""

    return SupportedWorkflowContinuation(
        continuation_id=continuation_id_for(workflow_id, question_id, step_revision),
        question_id=question_id,
        workflow_id=workflow_id,
        step_revision=step_revision,
        target_type="condition_creation",
        target_field=current_field or None,
        expected_executable_hash=draft.executable_hash if mutating else "",
        expected_workflow_state_hash=draft.workflow_state_hash if mutating else "",
        allowed_canonical_values=list(allowed_values)[:64],
        answer_schema=answer_schema[:200],
        source_evidence=[item for item in source_evidence if str(item).strip()][-24:],
        cancellation_policy=CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
        replacement_policy=ReplacementPolicy.REQUIRE_EXPLICIT_CHOICE,
        unresolved_id=workflow_id,
        remaining_fields=list(remaining_fields)[:24],
    )


def build_continuation_operations(
    continuation: object,
    answer: ContinuationAnswer,
    *,
    draft: StrategyDraftV2,
    enforce_draft_identity: bool = True,
) -> tuple[AuthorizedPatchOperation, ...]:
    """Turn one canonical answer into the operations the question promised.

    Refuses rather than improvises. Three things are checked before anything is built,
    and each was a real way an answer could land somewhere it was never meant to:

    * the stored schema version — an old record is migrated or paused, never guessed at;
    * the draft identity — if the draft moved after the question was asked, the answer
      belongs to a state that no longer exists;
    * the value — a step may only execute what it said it could.
    """

    if not isinstance(continuation, ContinuationBase):
        raise ContinuationRefused(
            "CONTINUATION_MISSING",
            "That question has no stored continuation, so no answer can be applied.",
        )
    if continuation.schema_version != CONTINUATION_SCHEMA_VERSION:
        raise ContinuationRefused(
            "CONTINUATION_SCHEMA_OUTDATED",
            "That question was stored under older rules and must be asked again.",
        )
    kind = cast(ContinuationKind, continuation.operation_builder)
    builder = _BUILDERS.get(kind)
    if builder is None:  # pragma: no cover - see _MISSING_BUILDERS
        raise ContinuationRefused(
            "CONTINUATION_BUILDER_MISSING",
            "That question cannot be completed by this server.",
        )
    if (
        enforce_draft_identity
        and continuation.expected_executable_hash
        and continuation.expected_executable_hash != draft.executable_hash
    ):
        raise ContinuationRefused(
            "CONTINUATION_DRAFT_MOVED",
            "The setup changed after that question was asked, so it must be asked again.",
        )
    if not continuation.accepts(answer.canonical_value):
        raise ContinuationRefused(
            "CONTINUATION_VALUE_NOT_EXECUTABLE",
            "That value is not one this step can run.",
        )
    return builder(continuation, answer, draft)
