"""Canonical requirement, satisfaction and semantic-role authority for Setup Chat.

The planner proposes language structure.  This module alone decides what the canonical
draft already contains, which user evidence authorized it, and whether an open
requirement may close.  Planner validation, clarification selection, response evidence
and approval eligibility all consume the records produced here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Literal, cast

from pydantic_core import PydanticUndefined

from ai_market_monitor.db.models.enums import ShariaUniverseMode
from ai_market_monitor.engine.action_grounding import action_is_grounded
from ai_market_monitor.engine.capability_contract import (
    capability_parameter_metadata,
    parameter_value_is_grounded,
)
from ai_market_monitor.engine.semantic_grounding import (
    grounds_boolean,
    grounds_direction,
    grounds_formula,
    grounds_lookback,
    grounds_number,
    grounds_operator,
    grounds_strategy_bias,
    grounds_symbol,
    grounds_text_value,
    grounds_timeframe_role,
    grounds_unit,
)
from ai_market_monitor.engine.turn_fragments import extract_symbols, extract_timeframes
from ai_market_monitor.schemas.setup_agent import (
    OperationExecutionResult,
    SetupAgentTurnPlan,
    TurnSegment,
)
from ai_market_monitor.schemas.setup_authorization import (
    ClarificationContract,
)
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    FormulaKind,
    MarketScopeV2,
    MovementDirection,
    RequirementAssessmentV2,
    RequirementStateV2,
    SemanticRoleAssignmentV2,
    StrategyBias,
    StrategyDraftV2,
    UnresolvedFieldV2,
    UnresolvedTargetType,
)


@dataclass(frozen=True, slots=True)
class _Assignment:
    target_path: str
    semantic_type: str
    value: Any
    role: str
    condition_id: str | None
    operation_id: str
    segment_id: str
    turn_id: str
    source: str
    grounded: bool
    changed: bool
    registry_owned: bool = False
    inherited_existing: bool = False


@dataclass(frozen=True, slots=True)
class RequirementReconciliation:
    draft: StrategyDraftV2
    assessments: tuple[RequirementAssessmentV2, ...]
    answered_question_ids: tuple[str, ...]


_CONDITION_FIELDS: tuple[str, ...] = (
    "required",
    "movement_direction",
    "strategy_bias",
    "formula",
    "operands",
    "operator",
    "threshold",
    "unit",
    "trigger_timeframe",
    "context_timeframes",
    "confirmation_timeframes",
    "reference_timeframe",
    "reference_definition",
    "lookback",
    "capability_key",
    "capability_version",
    "condition_symbols",
)

_ROLE_BY_FIELD: dict[str, str] = {
    "trigger_timeframe": "trigger",
    "context_timeframes": "context",
    "confirmation_timeframes": "confirmation",
    "reference_timeframe": "reference",
    "movement_direction": "movement_direction",
    "strategy_bias": "strategy_bias",
    "threshold": "threshold",
    "lookback": "lookback",
    "formula": "formula",
    "reference_definition": "reference_value",
    "included_symbols": "include",
    "excluded_symbols": "exclude",
}

_REGISTRY_FIELDS = frozenset({"capability_version", "operands"})

#: The canonical requirement path that carries "which screened universe is this".
UNIVERSE_MODE_PATH: Final[str] = "sharia_policy.universe_mode"

#: Provenance kinds that mean a person chose the value, not the platform.
USER_OWNED_PROVENANCE: Final[frozenset[str]] = frozenset({"user_explicit", "user_confirmed"})


def universe_mode_is_user_selected(
    draft: StrategyDraftV2,
    *,
    assignments: Iterable[_Assignment] = (),
) -> bool:
    """Did a person choose this screened universe, or is it only the safe default?

    ``ELIGIBLE_MARKET`` is both a legitimate answer and the platform default, so the
    stored value proves nothing on its own.  Provenance does: either an explicit
    selection inside this turn, or a user-owned requirement state written by an earlier
    one.  Reading the value alone would auto-satisfy every new draft.
    """

    mode = draft.sharia_policy.universe_mode.value
    for assignment in assignments:
        if (
            assignment.target_path == UNIVERSE_MODE_PATH
            and assignment.grounded
            and not assignment.registry_owned
            and not assignment.inherited_existing
            and _json_value(assignment.value) == mode
        ):
            return True
    return any(
        state.target_path == UNIVERSE_MODE_PATH
        and state.provenance_kind in USER_OWNED_PROVENANCE
        and state.normalized_value == mode
        for state in draft.requirement_states
    )


def universe_scope_is_settled(
    draft: StrategyDraftV2,
    *,
    assignments: Iterable[_Assignment] = (),
) -> bool:
    """Is "which markets do we watch" answered?

    Three different answers settle it, and only these three:

    * a bounded symbol list — included, excluded or screened explicit symbols;
    * an approved watchlist;
    * an explicit choice of the whole screened market.

    The third one stores no symbols at all, so it can only be proved by provenance.
    Every other caller asks this question through here so the rule cannot drift.
    """

    policy = draft.sharia_policy
    if (
        draft.universe.included_symbols
        or draft.universe.excluded_symbols
        or policy.explicit_symbols
        or policy.approved_watchlist_id
    ):
        return True
    return policy.universe_mode is ShariaUniverseMode.ELIGIBLE_MARKET and (
        universe_mode_is_user_selected(draft, assignments=assignments)
    )


def reconcile_requirement_state(
    *,
    before: StrategyDraftV2,
    working: StrategyDraftV2,
    plan: SetupAgentTurnPlan,
    segments: dict[str, TurnSegment],
    operation_results: list[OperationExecutionResult],
    active_clarification: ClarificationContract | None = None,
    confirmed_paths: frozenset[str] = frozenset(),
) -> RequirementReconciliation:
    """Reconcile every open target against the completed, authorized working turn.

    This runs before public version finalization.  A model-authored resolution operation
    can therefore remove a row temporarily, but the row is restored unless its final
    canonical value is both satisfied and grounded by this turn.
    """

    applied = {
        item.operation_id: item for item in operation_results if item.applied and not item.rejected
    }
    assignments = _operation_assignments(
        before,
        working,
        plan,
        segments,
        applied,
        confirmed_paths=confirmed_paths,
    )
    working, supported_assessments = _reconcile_supported_requirements(
        working,
        assignments=assignments,
    )
    unresolved_values = list(working.unresolved_fields)
    known_ids = {
        item.unresolved_id for item in (*before.unresolved_fields, *unresolved_values)
    }
    # A step's question id is unique to that step, while the blocker it belongs to
    # keeps one stable identity for the whole workflow. Matching the workflow id as
    # well is what stops step two synthesising a second, duplicate blocker for the
    # same semantic object.
    already_open = active_clarification is not None and (
        active_clarification.question_id in known_ids
        or (active_clarification.workflow_id or "\x00") in known_ids
    )
    if (
        active_clarification is not None
        and active_clarification.mutating
        and not already_open
        and active_clarification.target_type
        in {
            "draft_field",
            "condition_field",
            "condition_creation",
            "universe",
            "market_scope",
            "sharia_policy",
            "boolean_structure",
            "capability_parameter",
            "reference_definition",
            "unsupported_resolution",
        }
    ):
        try:
            expected_schema = json.loads(active_clarification.expected_answer_schema)
        except (TypeError, ValueError):
            expected_schema = {"type": "string"}
        if not isinstance(expected_schema, dict) or expected_schema.get("type") not in {
            "string",
            "number",
            "integer",
            "boolean",
            "array",
            "object",
        }:
            expected_schema = {"type": "string"}
        unresolved_values.append(
            UnresolvedFieldV2(
                unresolved_id=active_clarification.question_id,
                source_fragment=active_clarification.question,
                target_type=cast(UnresolvedTargetType, active_clarification.target_type),
                target_field=active_clarification.target_field,
                target_condition_id=active_clarification.target_condition_id,
                expected_answer_schema=expected_schema,
                allowed_options=active_clarification.allowed_options,
                question=active_clarification.question,
                reason=active_clarification.reason,
            )
        )
    pending = _coalesced_unresolved(before.unresolved_fields, unresolved_values)
    assessments: list[RequirementAssessmentV2] = list(supported_assessments)
    retained: list[UnresolvedFieldV2] = []
    answered: list[str] = []

    for representative, aliases in pending:
        assessment = _assess_unresolved(
            representative,
            aliases,
            before=before,
            working=working,
            plan=plan,
            segments=segments,
            assignments=assignments,
            active_question_id=(
                active_clarification.question_id if active_clarification is not None else None
            ),
        )
        assessments.append(assessment)
        if assessment.satisfied:
            answered.extend(item.unresolved_id for item in aliases)
            continue
        retained.append(_merge_unresolved(representative, aliases))

    retained_paths = {unresolved_target_path(item) for item in retained}
    for conflict in _conflicting_assignment_requirements(assignments, plan):
        if unresolved_target_path(conflict) not in retained_paths:
            retained.append(conflict)
            retained_paths.add(unresolved_target_path(conflict))

    approval_intent = plan.approval_intent is not None or (
        before.approval_intent_received and working.executable_hash == before.executable_hash
    )
    staged = _rehash(
        working,
        unresolved_fields=retained,
        approval_intent_received=approval_intent,
    )
    state_assignments = list(assignments)
    if plan.approval_intent is not None and not before.approval_intent_received:
        approval_segment = segments.get(plan.approval_intent.segment_id)
        if approval_segment is not None:
            state_assignments.append(
                _Assignment(
                    target_path="workflow.approval_intent_received",
                    semantic_type="approval_intent",
                    value=True,
                    role="intent_only",
                    condition_id=None,
                    operation_id=f"approval-intent:{plan.source_turn_id}",
                    segment_id=approval_segment.segment_id,
                    turn_id=plan.source_turn_id,
                    source=approval_segment.exact_source_text,
                    grounded=True,
                    changed=not before.approval_intent_received,
                )
            )
    for assessment in assessments:
        if not assessment.satisfied or not assessment.explicitly_confirmed_this_turn:
            continue
        for segment_id in assessment.satisfying_segment_ids[:1]:
            segment = segments.get(segment_id)
            if segment is None:
                continue
            state_assignments.append(
                _Assignment(
                    target_path=assessment.target_path,
                    semantic_type="confirmation",
                    value=assessment.final_value,
                    role=assessment.target_path.rsplit(".", 1)[-1],
                    condition_id=(
                        assessment.target_path.split(".")[1]
                        if assessment.target_path.startswith("condition_ast.")
                        and len(assessment.target_path.split(".")) > 2
                        else None
                    ),
                    operation_id=f"confirmation:{assessment.requirement_id}",
                    segment_id=segment_id,
                    turn_id=plan.source_turn_id,
                    source=segment.exact_source_text,
                    grounded=True,
                    changed=False,
                )
            )
    states, roles = build_requirement_state(
        staged,
        previous=before.requirement_states,
        assignments=state_assignments,
        assessments=assessments,
    )
    staged = _rehash(
        staged,
        requirement_states=states,
        semantic_role_assignments=roles,
    )
    return RequirementReconciliation(
        draft=staged,
        assessments=tuple(assessments),
        answered_question_ids=tuple(dict.fromkeys(answered)),
    )


def _reconcile_supported_requirements(
    draft: StrategyDraftV2,
    *,
    assignments: list[_Assignment],
) -> tuple[StrategyDraftV2, tuple[RequirementAssessmentV2, ...]]:
    """Close an older unsupported blocker only when this turn now implements it.

    An incomplete but recognizable core rule can initially be preserved as an
    unsupported workflow blocker so the independent mode/symbol instructions in the
    same turn are not discarded.  Once a later, fully grounded condition supplies the
    missing values, leaving that blocker in place makes a valid draft permanently
    unapprovable.  The model has no authority to delete it, so final requirement
    reconciliation proves the relationship from the old source and the newly applied
    canonical condition.

    The proof needs a grounded mechanic plus another matching semantic anchor.  A
    generic phrase such as "some custom rule" therefore cannot be erased by an
    unrelated condition, and a conflicting direction, comparator, timeframe or symbol
    keeps the blocker open.
    """

    if not draft.unsupported_requirements:
        return draft, ()
    conditions = _conditions(draft)
    by_condition: dict[str, list[_Assignment]] = {}
    for assignment in assignments:
        if assignment.condition_id and assignment.changed and assignment.grounded:
            by_condition.setdefault(assignment.condition_id, []).append(assignment)
    if not by_condition:
        return draft, ()

    retained = []
    assessments: list[RequirementAssessmentV2] = []
    for blocker in draft.unsupported_requirements:
        matched: tuple[str, list[_Assignment]] | None = None
        for condition_id, evidence in by_condition.items():
            node = conditions.get(condition_id)
            if node is not None and _condition_satisfies_unsupported_source(
                blocker.source_fragment,
                node,
                draft,
            ):
                matched = (condition_id, evidence)
                break
        if matched is None:
            retained.append(blocker)
            continue
        condition_id, evidence = matched
        assessments.append(
            RequirementAssessmentV2(
                requirement_id=_requirement_id(
                    "unsupported_requirement",
                    f"unsupported.{blocker.key}",
                    None,
                ),
                target_path=f"unsupported.{blocker.key}",
                target_exists=False,
                final_value=None,
                expected_or_authorized_value=condition_id,
                satisfied=True,
                changed_this_turn=True,
                explicitly_confirmed_this_turn=False,
                authorization_grounded=True,
                satisfying_operation_ids=list(
                    dict.fromkeys(item.operation_id for item in evidence)
                ),
                satisfying_segment_ids=list(
                    dict.fromkeys(item.segment_id for item in evidence)
                ),
                unresolved_reason=None,
            )
        )
    if len(retained) == len(draft.unsupported_requirements):
        return draft, tuple(assessments)
    return _rehash(draft, unsupported_requirements=retained), tuple(assessments)


def _condition_satisfies_unsupported_source(
    source: str,
    node: ConditionNodeV2,
    draft: StrategyDraftV2,
) -> bool:
    """Prove that one new canonical condition covers an older stated mechanic."""

    anchors = 0
    mechanic_grounded = bool(
        node.formula is not None
        and node.formula != FormulaKind.CAPABILITY
        and grounds_formula(source, node.formula)
    )
    if node.capability_key:
        mechanic_grounded = mechanic_grounded or grounds_text_value(
            source,
            node.capability_key,
        ) or grounds_text_value(source, node.capability_key.split("_", 1)[0])
    if not mechanic_grounded:
        return False
    anchors += 1

    directions = [
        value
        for value in (MovementDirection.UP, MovementDirection.DOWN)
        if grounds_direction(source, value)
    ]
    if directions:
        if len(directions) != 1 or node.movement_direction != directions[0]:
            return False
        anchors += 1

    biases = [
        value
        for value in (StrategyBias.LONG, StrategyBias.SHORT)
        if grounds_strategy_bias(source, value)
    ]
    if biases:
        if len(biases) != 1 or node.strategy_bias != biases[0]:
            return False
        anchors += 1

    comparators = [
        value
        for value in Comparator
        if value not in {Comparator.IS_TRUE, Comparator.IS_FALSE}
        and grounds_operator(source, value)
    ]
    if comparators:
        if len(comparators) != 1 or node.operator != comparators[0]:
            return False
        anchors += 1

    if node.threshold is not None and grounds_number(
        source,
        node.threshold,
        unit=node.unit or "plain",
    ):
        anchors += 1

    for timeframe in extract_timeframes(source):
        matches = (
            bool(
                node.trigger_timeframe == timeframe
                and grounds_timeframe_role(source, timeframe, "trigger")
            )
            or bool(
                timeframe in node.context_timeframes
                and grounds_timeframe_role(source, timeframe, "context")
            )
            or bool(
                timeframe in node.confirmation_timeframes
                and grounds_timeframe_role(source, timeframe, "confirmation")
            )
            or bool(
                node.reference_timeframe == timeframe
                and grounds_timeframe_role(source, timeframe, "reference")
            )
        )
        if not matches:
            return False
        anchors += 1

    allowed_symbols = {
        *node.condition_symbols,
        *draft.universe.included_symbols,
        *draft.sharia_policy.explicit_symbols,
    }
    symbols = set(extract_symbols(source))
    if symbols:
        canonical_allowed = {
            value.upper().replace("/", "").replace("-", "").replace("_", "")
            for value in allowed_symbols
        }
        if any(
            symbol.upper().replace("/", "").replace("-", "").replace("_", "")
            not in canonical_allowed
            for symbol in symbols
        ):
            return False
        anchors += len(symbols)

    return anchors >= 2


def build_requirement_state(
    draft: StrategyDraftV2,
    *,
    previous: Iterable[RequirementStateV2] = (),
    assignments: Iterable[_Assignment] = (),
    assessments: Iterable[RequirementAssessmentV2] = (),
) -> tuple[list[RequirementStateV2], list[SemanticRoleAssignmentV2]]:
    """Build the canonical read model used by every downstream Setup Chat reader."""

    previous_by_path = {item.target_path: item for item in previous}
    assignment_by_path: dict[str, _Assignment] = {}
    for item in assignments:
        assignment_by_path[item.target_path] = item
    assessment_by_id = {item.requirement_id: item for item in assessments}
    assessment_by_path = {item.target_path: item for item in assessments}
    states: list[RequirementStateV2] = []

    def add(
        path: str,
        semantic_type: str,
        value: Any,
        *,
        role: str = "value",
        condition_id: str | None = None,
        default: bool = False,
        registry_owned: bool = False,
        supported: bool = True,
        blocking: bool = False,
        reason: str = "",
        requirement_id: str | None = None,
        conflicting: bool = False,
    ) -> None:
        normalized = _json_value(value)
        assignment = assignment_by_path.get(path)
        old = previous_by_path.get(path)
        preserved_old = bool(
            assignment is None and old is not None and old.normalized_value == normalized
        )
        explicit = bool(
            (
                assignment
                and assignment.grounded
                and not assignment.registry_owned
                and not assignment.inherited_existing
            )
            or (preserved_old and old and old.explicit)
        )
        inherited = bool(
            (assignment and assignment.inherited_existing)
            or (preserved_old and old and old.inherited)
        )
        inherited_registry = bool(
            preserved_old and old is not None and old.provenance_kind == "registry_owned"
        )
        provenance = (
            "registry_owned"
            if registry_owned
            or inherited_registry
            or bool(assignment and assignment.registry_owned)
            else "inherited_existing"
            if assignment and assignment.inherited_existing
            else "user_explicit"
            if explicit
            else old.provenance_kind
            if preserved_old and old is not None
            else "platform_default"
            if default
            else "model_proposed"
        )
        source_turn_id = assignment.turn_id if assignment else old.source_turn_id if old else None
        source_segment_id = (
            assignment.segment_id if assignment else old.source_segment_id if old else None
        )
        source = assignment.source if assignment else old.exact_source_text if old else None
        grounded = bool(
            explicit
            or inherited
            or registry_owned
            or bool(assignment and assignment.registry_owned)
            or inherited_registry
            or default
            or (preserved_old and old and old.grounded)
        )
        effective_blocking = bool(
            blocking
            or (
                normalized not in (None, "", [], {})
                and supported
                and not grounded
                and not conflicting
            )
        )
        authorized_absence = bool(
            assignment
            and assignment.changed
            and assignment.grounded
            and normalized in (None, "", [], {})
        )
        state_id = requirement_id or _requirement_id(semantic_type, path, condition_id)
        assessment = assessment_by_id.get(state_id) or assessment_by_path.get(path)
        if assessment is not None:
            grounded = assessment.authorization_grounded
            if assessment.explicitly_confirmed_this_turn and assessment.satisfied:
                provenance = "user_confirmed"
                explicit = True
                inherited = False
                if assessment.satisfying_segment_ids:
                    source_segment_id = assessment.satisfying_segment_ids[0]
        states.append(
            RequirementStateV2(
                requirement_id=state_id,
                semantic_type=semantic_type,
                target_path=path,
                target_condition_id=condition_id,
                normalized_value=normalized,
                value_role=role,
                source_turn_id=source_turn_id,
                source_segment_id=source_segment_id,
                exact_source_text=source,
                provenance_kind=provenance,
                explicit=explicit,
                inherited=inherited,
                platform_default=default and not explicit,
                supported=supported,
                grounded=grounded,
                conflicting=conflicting,
                satisfied=(
                    not effective_blocking
                    and (value not in (None, "", [], {}) or authorized_absence)
                    and grounded
                ),
                blocking=effective_blocking,
                reason=(
                    reason
                    if reason
                    else "The canonical value has no authorized user or server provenance."
                    if effective_blocking
                    else ""
                ),
            )
        )

    add("mode", "mode", draft.mode, default=draft.mode == StrategyDraftV2().mode)
    add("name", "workflow", draft.name, default=draft.name == StrategyDraftV2().name)
    for field in MarketScopeV2.model_fields:
        value = getattr(draft.market_scope, field)
        add(
            f"market_scope.{field}",
            "market_scope",
            value,
            default=_is_default(MarketScopeV2, field, value),
        )
    for field in type(draft.sharia_policy).model_fields:
        value = getattr(draft.sharia_policy, field)
        add(
            f"sharia_policy.{field}",
            "sharia_policy",
            value,
            role=field,
            default=_is_default(type(draft.sharia_policy), field, value),
        )
    for symbol in draft.universe.included_symbols:
        add(
            f"universe.included_symbols.{symbol}",
            "symbol",
            symbol,
            role="include",
        )
    for symbol in draft.universe.excluded_symbols:
        add(
            f"universe.excluded_symbols.{symbol}",
            "symbol",
            symbol,
            role="exclude",
        )
    if draft.condition_ast is not None:
        add(
            "condition_ast.boolean_structure",
            "boolean_structure",
            _boolean_shape(draft.condition_ast),
            role="boolean_membership",
            registry_owned=draft.condition_ast.node_type == ConditionNodeType.CONDITION,
        )
        for node in _conditions(draft).values():
            for field in _CONDITION_FIELDS:
                value = getattr(node, field)
                path = f"condition_ast.{node.node_id}.{field}"
                add(
                    path,
                    _semantic_type(field),
                    value,
                    role=_ROLE_BY_FIELD.get(field, field),
                    condition_id=node.node_id,
                    default=_is_default(ConditionNodeV2, field, value),
                    registry_owned=field in _REGISTRY_FIELDS,
                )
            for key, value in sorted(node.capability_parameters.items()):
                _rules, _unit, has_default, default_value = capability_parameter_metadata(
                    node.capability_key or "",
                    key,
                )
                add(
                    f"condition_ast.{node.node_id}.capability_parameters.{key}",
                    "capability_parameter",
                    value,
                    role=(
                        "period"
                        if "period" in key
                        else "confirmation_count"
                        if "confirm" in key and isinstance(value, int)
                        else "parameter"
                    ),
                    condition_id=node.node_id,
                    registry_owned=has_default and _equal(value, default_value),
                )
    for unresolved_item in draft.unresolved_fields:
        path = unresolved_target_path(unresolved_item)
        add(
            path,
            unresolved_item.target_type,
            None,
            role=unresolved_item.target_field or "condition_requirement",
            condition_id=unresolved_item.target_condition_id,
            blocking=unresolved_item.blocking,
            reason=unresolved_item.reason,
            requirement_id=_unresolved_requirement_id(unresolved_item),
            conflicting="conflict" in unresolved_item.reason.casefold(),
        )
    for unsupported_item in draft.unsupported_requirements:
        add(
            f"unsupported.{unsupported_item.key}",
            "unsupported_requirement",
            unsupported_item.missing_contract,
            supported=False,
            blocking=unsupported_item.blocking,
            reason=unsupported_item.missing_contract,
        )
    if draft.approval_intent_received:
        add(
            "workflow.approval_intent_received",
            "approval_intent",
            True,
            role="intent_only",
        )
    recorded_paths = {item.target_path for item in states}
    for assignment in assignment_by_path.values():
        if assignment.target_path in recorded_paths:
            continue
        add(
            assignment.target_path,
            assignment.semantic_type,
            assignment.value,
            role=assignment.role,
            condition_id=assignment.condition_id,
            registry_owned=assignment.registry_owned,
        )
        recorded_paths.add(assignment.target_path)
    states.sort(key=lambda item: (item.target_path, item.requirement_id))
    roles = [
        SemanticRoleAssignmentV2(
            semantic_type=item.semantic_type,
            role=item.value_role,
            normalized_value=item.normalized_value,
            exact_source_text=item.exact_source_text,
            source_segment_id=item.source_segment_id,
            source_turn_id=item.source_turn_id,
            target_path=item.target_path,
            explicit=item.explicit,
            inherited=item.inherited,
        )
        for item in states
        if item.normalized_value is not None
        and item.value_role
        in {
            "trigger",
            "context",
            "confirmation",
            "reference",
            "movement_direction",
            "strategy_bias",
            "threshold",
            "lookback",
            "period",
            "confirmation_count",
            "include",
            "exclude",
            "formula",
            "reference_value",
            "boolean_membership",
            "intent_only",
        }
    ]
    return states, roles


def active_requirement_states(draft: StrategyDraftV2) -> list[RequirementStateV2]:
    """Compatibility reader: synthesize state for legacy JSON without writing it."""

    if draft.requirement_states:
        return list(draft.requirement_states)
    return build_requirement_state(draft)[0]


def blocking_requirement_states(draft: StrategyDraftV2) -> list[RequirementStateV2]:
    return [item for item in active_requirement_states(draft) if item.blocking]


def unresolved_target_path(item: UnresolvedFieldV2) -> str:
    if item.target_type in {"condition_field", "capability_parameter", "reference_definition"}:
        field = item.target_field or "reference_definition"
        return f"condition_ast.{item.target_condition_id}.{field}"
    if item.target_type == "market_scope":
        return f"market_scope.{item.target_field or 'scope'}"
    if item.target_type == "draft_field":
        return item.target_field or f"draft.{item.unresolved_id}"
    if item.target_type == "sharia_policy":
        return f"sharia_policy.{item.target_field or 'policy'}"
    if item.target_type == "universe":
        return "universe"
    if item.target_type == "boolean_structure":
        return "condition_ast.boolean_structure"
    if item.target_type == "condition_creation":
        return f"condition_requirement.{_condition_requirement_key(item)}"
    if item.target_type == "unsupported_resolution":
        return f"unsupported.{item.unresolved_id}"
    return f"{item.target_type}.{item.unresolved_id}"


def _assess_unresolved(
    item: UnresolvedFieldV2,
    aliases: list[UnresolvedFieldV2],
    *,
    before: StrategyDraftV2,
    working: StrategyDraftV2,
    plan: SetupAgentTurnPlan,
    segments: dict[str, TurnSegment],
    assignments: list[_Assignment],
    active_question_id: str | None,
) -> RequirementAssessmentV2:
    path = unresolved_target_path(item)
    linked_ids = {candidate.unresolved_id for candidate in aliases}
    answer_segments = {
        answer.segment_id
        for answer in plan.clarification_answers
        if answer.resolves_question and answer.question_id in linked_ids
    }
    resolve_segments = {
        operation.authorizing_segment_id
        for operation in plan.operations
        if operation.kind == "resolve_unresolved_key" and operation.target_key in linked_ids
    }
    same_turn_segments = {
        operation.authorizing_segment_id
        for operation in plan.operations
        if operation.unresolved is not None and operation.unresolved.unresolved_id in linked_ids
    }
    authorized_segments = answer_segments | resolve_segments | same_turn_segments

    if item.target_type == "condition_creation":
        if active_question_id in linked_ids:
            # A complete instruction can answer the active semantic object even when
            # the planner omitted redundant clarification/resolution metadata. The
            # operation still has to ground every missing slot below.
            authorized_segments.update(
                operation.authorizing_segment_id for operation in plan.operations
            )
        relevant = [
            assignment
            for assignment in assignments
            if assignment.condition_id
            and assignment.segment_id in authorized_segments
            and assignment.semantic_type != "condition_creation"
        ]
        condition_ids = {assignment.condition_id for assignment in relevant}
        missing_slots = set(item.missing_slots)
        for alias in aliases:
            missing_slots.update(alias.missing_slots)
            if alias.target_field:
                missing_slots.add(alias.target_field)
        candidates = _conditions(working)
        satisfied_ids = [
            node_id
            for node_id in condition_ids
            if node_id in candidates
            and _condition_satisfies_slots(
                candidates[node_id],
                missing_slots,
                {assignment.target_path: assignment for assignment in relevant},
            )
        ]
        grounded = bool(satisfied_ids)
        changed = any(
            assignment.changed and assignment.condition_id in satisfied_ids
            for assignment in relevant
        )
        confirmed = grounded and bool(answer_segments)
        satisfied = grounded and (changed or confirmed or bool(relevant))
        satisfied_values: list[str | int | float | bool] = list(satisfied_ids)
        return RequirementAssessmentV2(
            requirement_id=_unresolved_requirement_id(item),
            target_path=path,
            target_exists=bool(satisfied_ids),
            final_value=satisfied_values,
            expected_or_authorized_value=satisfied_values,
            satisfied=satisfied,
            changed_this_turn=changed,
            explicitly_confirmed_this_turn=confirmed,
            authorization_grounded=grounded,
            satisfying_operation_ids=list(
                dict.fromkeys(
                    assignment.operation_id
                    for assignment in relevant
                    if assignment.condition_id in satisfied_ids
                )
            ),
            satisfying_segment_ids=list(
                dict.fromkeys(
                    assignment.segment_id
                    for assignment in relevant
                    if assignment.condition_id in satisfied_ids
                )
            ),
            unresolved_reason=None if satisfied else item.reason,
        )

    exists, value = _target_value(item, working, assignments=assignments)
    relevant = [
        assignment
        for assignment in assignments
        if _assignment_matches_unresolved(assignment, item, path)
    ]
    grounded_ops = [
        assignment
        for assignment in relevant
        if assignment.grounded and _assignment_satisfies_final(assignment, item, value, working)
    ]
    confirmation_segments: list[str] = []
    for answer in plan.clarification_answers:
        if answer.question_id not in linked_ids or not answer.resolves_question:
            continue
        segment = segments.get(answer.segment_id)
        if segment is not None and _grounds_target_value(
            segment.exact_source_text,
            value,
            field=item.target_field or "",
            node=_conditions(working).get(item.target_condition_id or ""),
        ):
            confirmation_segments.append(answer.segment_id)
    changed = _target_value(item, before) != (exists, value)
    confirmed = bool(confirmation_segments)
    grounded = bool(grounded_ops or confirmed)
    supported = True
    satisfied = bool(exists and supported and grounded and (changed or confirmed or grounded_ops))
    return RequirementAssessmentV2(
        requirement_id=_unresolved_requirement_id(item),
        target_path=path,
        target_exists=exists,
        final_value=_json_value(value),
        expected_or_authorized_value=_json_value(value) if grounded else None,
        satisfied=satisfied,
        changed_this_turn=changed,
        explicitly_confirmed_this_turn=confirmed,
        authorization_grounded=grounded,
        satisfying_operation_ids=list(dict.fromkeys(x.operation_id for x in grounded_ops)),
        satisfying_segment_ids=list(
            dict.fromkeys([*(x.segment_id for x in grounded_ops), *confirmation_segments])
        ),
        unresolved_reason=None if satisfied else item.reason,
    )


def _operation_assignments(
    before: StrategyDraftV2,
    working: StrategyDraftV2,
    plan: SetupAgentTurnPlan,
    segments: dict[str, TurnSegment],
    applied: dict[str, OperationExecutionResult],
    *,
    confirmed_paths: frozenset[str] = frozenset(),
) -> list[_Assignment]:
    assignments: list[_Assignment] = []
    before_conditions = _conditions(before)
    final_conditions = _conditions(working)
    for operation in plan.operations:
        segment = segments.get(operation.authorizing_segment_id)
        if segment is None:
            continue
        source = segment.exact_source_text
        changed = operation.operation_id in applied

        def record(
            path: str,
            semantic_type: str,
            value: Any,
            *,
            role: str = "value",
            condition_id: str | None = None,
            registry_owned: bool = False,
            inherited_existing: bool = False,
            force_grounded: bool | None = None,
            _source: str = source,
            _operation_id: str = operation.operation_id,
            _segment_id: str = segment.segment_id,
            _changed: bool = changed,
        ) -> None:
            grounded = (
                force_grounded
                if force_grounded is not None
                else _grounds_target_value(
                    _source,
                    value,
                    field=path.rsplit(".", 1)[-1],
                    node=final_conditions.get(condition_id or ""),
                )
            )
            assignments.append(
                _Assignment(
                    target_path=path,
                    semantic_type=semantic_type,
                    value=value,
                    role=role,
                    condition_id=condition_id,
                    operation_id=_operation_id,
                    segment_id=_segment_id,
                    turn_id=plan.source_turn_id,
                    source=_source,
                    grounded=bool(grounded or registry_owned),
                    changed=_changed,
                    registry_owned=registry_owned,
                    inherited_existing=inherited_existing,
                )
            )

        if operation.kind == "set_fields" and operation.fields is not None:
            for field, value in operation.fields.model_dump(exclude_none=True).items():
                path = field if field in {"mode", "name"} else f"market_scope.{field}"
                record(path, "mode" if field == "mode" else "market_scope", value)
        elif operation.kind == "set_sharia_policy" and operation.sharia_policy is not None:
            registry_policy_fields = {
                "methodology_version",
                "approved_watchlist_version",
                "allowed_statuses",
                "qualification_policy",
                "disputed_asset_policy",
                "compliance_change_behavior",
                "explicit_symbols",
            }
            for field in operation.sharia_policy.model_dump(mode="python"):
                final_value = getattr(working.sharia_policy, field)
                old_value = getattr(before.sharia_policy, field)
                # A server-owned control names the exact paths the person picked. Re-
                # picking the value that was already there is still a choice, and it is
                # the only evidence that separates a chosen default from a defaulted one.
                if (
                    final_value != old_value
                    or f"sharia_policy.{field}" in confirmed_paths
                    or _grounds_target_value(source, final_value, field=field)
                ):
                    record(
                        f"sharia_policy.{field}",
                        "sharia_policy",
                        final_value,
                        role=field,
                        # The dedicated Sharia grounding gate has already verified the
                        # complete changed policy field.  It remains deterministic.
                        force_grounded=True,
                        registry_owned=field in registry_policy_fields,
                    )
        elif operation.kind == "add_inclusion" and operation.symbol:
            symbol = _canonical_member(operation.symbol, working.universe.included_symbols)
            record(
                f"universe.included_symbols.{symbol}",
                "symbol",
                symbol,
                role="include",
            )
        elif operation.kind == "add_exclusion" and operation.symbol:
            symbol = _canonical_member(operation.symbol, working.universe.excluded_symbols)
            record(
                f"universe.excluded_symbols.{symbol}",
                "symbol",
                symbol,
                role="exclude",
            )
        elif operation.kind == "remove_inclusion" and operation.symbol:
            symbol = _canonical_member(operation.symbol, before.universe.included_symbols)
            record(
                f"universe.included_symbols.{symbol}",
                "symbol",
                None,
                role="include_removal",
                force_grounded=True,
            )
        elif operation.kind == "remove_exclusion" and operation.symbol:
            symbol = _canonical_member(operation.symbol, before.universe.excluded_symbols)
            record(
                f"universe.excluded_symbols.{symbol}",
                "symbol",
                None,
                role="exclude_removal",
                force_grounded=True,
            )
        elif operation.kind == "remove_condition" and operation.target_condition_id:
            record(
                f"condition_ast.{operation.target_condition_id}.membership",
                "condition_membership",
                None,
                role="condition_removal",
                condition_id=operation.target_condition_id,
                force_grounded=True,
            )
        elif operation.kind == "remove_unsupported_key" and operation.target_key:
            record(
                f"unsupported.{operation.target_key}",
                "unsupported_resolution",
                None,
                role="unsupported_removal",
                force_grounded=True,
            )
        elif operation.kind in {"add_condition", "update_condition", "replace_groups"}:
            nodes = [
                node
                for node in (operation.condition.walk() if operation.condition else [])
                if node.node_type == ConditionNodeType.CONDITION
            ]
            for proposed in nodes:
                condition_id = (
                    operation.target_condition_id
                    if operation.kind == "update_condition"
                    else proposed.node_id
                )
                final = final_conditions.get(condition_id or "")
                if final is None:
                    continue
                old = before_conditions.get(condition_id or "")
                for field in _CONDITION_FIELDS:
                    value = getattr(final, field)
                    old_value = getattr(old, field) if old is not None else None
                    registry_owned = field in _REGISTRY_FIELDS or (
                        field == "reference_timeframe"
                        and value == final.trigger_timeframe
                        and isinstance(value, str)
                        and not grounds_timeframe_role(source, value, "reference")
                        and grounds_timeframe_role(source, value, "trigger")
                    )
                    cleared = bool(
                        old is not None
                        and old_value != value
                        and action_is_grounded(source, "clear")
                        and (
                            value in (None, "", [], {})
                            or field
                            in {
                                "context_timeframes",
                                "confirmation_timeframes",
                                "condition_symbols",
                                "operands",
                            }
                            or (
                                field == "movement_direction"
                                and value
                                in {
                                    MovementDirection.NEUTRAL,
                                    MovementDirection.NOT_APPLICABLE,
                                }
                            )
                            or field == "strategy_bias"
                            and value == StrategyBias.NEUTRAL
                        )
                    )
                    path = f"condition_ast.{final.node_id}.{field}"
                    if old is not None and old_value == value:
                        if not any(item.target_path == path for item in before.requirement_states):
                            if registry_owned:
                                record(
                                    path,
                                    _semantic_type(field),
                                    value,
                                    role=_ROLE_BY_FIELD.get(field, field),
                                    condition_id=final.node_id,
                                    registry_owned=True,
                                )
                            elif not _is_default(ConditionNodeV2, field, value):
                                record(
                                    path,
                                    _semantic_type(field),
                                    value,
                                    role=_ROLE_BY_FIELD.get(field, field),
                                    condition_id=final.node_id,
                                    force_grounded=True,
                                    inherited_existing=True,
                                )
                        continue
                    if old is None or old_value != value:
                        record(
                            path,
                            _semantic_type(field),
                            value,
                            role=_ROLE_BY_FIELD.get(field, field),
                            condition_id=final.node_id,
                            registry_owned=registry_owned,
                            force_grounded=True if cleared else None,
                        )
                for key, value in final.capability_parameters.items():
                    old_value = old.capability_parameters.get(key) if old is not None else None
                    rules, semantic_unit, has_default, default_value = (
                        capability_parameter_metadata(final.capability_key or "", key)
                    )
                    registry_default = has_default and _equal(value, default_value)
                    path = f"condition_ast.{final.node_id}.capability_parameters.{key}"
                    if old is not None and old_value == value:
                        if not any(item.target_path == path for item in before.requirement_states):
                            record(
                                path,
                                "capability_parameter",
                                value,
                                role="parameter",
                                condition_id=final.node_id,
                                registry_owned=registry_default,
                                force_grounded=True,
                                inherited_existing=not registry_default,
                            )
                        continue
                    if old is None or old_value != value:
                        record(
                            path,
                            "capability_parameter",
                            value,
                            role="parameter",
                            condition_id=final.node_id,
                            registry_owned=registry_default,
                            force_grounded=(
                                registry_default
                                or parameter_value_is_grounded(
                                    source,
                                    value,
                                    rules,
                                    semantic_unit=semantic_unit,
                                )
                            ),
                        )
                if old is not None:
                    for key in sorted(
                        set(old.capability_parameters) - set(final.capability_parameters)
                    ):
                        record(
                            f"condition_ast.{final.node_id}.capability_parameters.{key}",
                            "capability_parameter",
                            None,
                            role="parameter",
                            condition_id=final.node_id,
                            force_grounded=action_is_grounded(source, "clear"),
                        )
            if operation.kind == "replace_groups" and operation.condition is not None:
                record(
                    "condition_ast.boolean_structure",
                    "boolean_structure",
                    _boolean_shape(working.condition_ast),
                    role="boolean_membership",
                    force_grounded=True,
                )
    return assignments


def _conflicting_assignment_requirements(
    assignments: list[_Assignment],
    plan: SetupAgentTurnPlan,
) -> list[UnresolvedFieldV2]:
    """Turn incompatible assignments to one semantic role into one typed blocker."""

    by_path: dict[str, list[_Assignment]] = {}
    for item in assignments:
        if item.registry_owned or not item.grounded:
            continue
        by_path.setdefault(item.target_path, []).append(item)
    conflicts: list[UnresolvedFieldV2] = []
    for path, values in by_path.items():
        distinct = {_stable_value(item.value) for item in values}
        if len(distinct) < 2:
            continue
        last = values[-1]
        if action_is_grounded(last.source, "change"):
            # An explicit correction authorizes the final assignment; it is not an
            # ambiguity merely because the old value also appears in the turn.
            continue
        field = path.rsplit(".", 1)[-1]
        condition_id = last.condition_id
        target_type: UnresolvedTargetType = (
            "capability_parameter"
            if ".capability_parameters." in path
            else "condition_field"
            if condition_id
            else "market_scope"
            if path.startswith("market_scope.")
            else "draft_field"
        )
        sample = last.value
        schema_type = (
            "boolean"
            if isinstance(sample, bool)
            else "integer"
            if isinstance(sample, int)
            else "number"
            if isinstance(sample, float)
            else "array"
            if isinstance(sample, list)
            else "object"
            if isinstance(sample, dict)
            else "string"
        )
        source = " | ".join(dict.fromkeys(item.source for item in values))[:5000]
        digest = hashlib.sha256(f"{plan.source_turn_id}:{path}".encode()).hexdigest()[:20]
        conflicts.append(
            UnresolvedFieldV2(
                unresolved_id=f"conflict_{digest}",
                source_turn_id=plan.source_turn_id,
                source_fragment=source,
                target_type=target_type,
                target_field=field,
                target_condition_id=condition_id,
                expected_answer_schema={"type": schema_type},
                missing_slots=[field],
                question=f"Which value should {field.replace('_', ' ')} use?",
                reason="Conflicting grounded values target the same semantic role.",
            )
        )
    return conflicts


def _assignment_matches_unresolved(
    assignment: _Assignment,
    item: UnresolvedFieldV2,
    path: str,
) -> bool:
    if item.target_type == "universe":
        return assignment.target_path.startswith("universe.") or assignment.target_path.startswith(
            "sharia_policy."
        )
    if item.target_type == "market_scope" and not item.target_field:
        return assignment.target_path.startswith("market_scope.")
    return assignment.target_path == path


def _assignment_satisfies_final(
    assignment: _Assignment,
    item: UnresolvedFieldV2,
    final_value: Any,
    draft: StrategyDraftV2,
) -> bool:
    if item.target_type == "unsupported_resolution":
        return assignment.value is None and bool(final_value)
    if item.target_type == "universe":
        symbol = str(assignment.value)
        if assignment.target_path.startswith("universe.included_symbols."):
            return symbol in draft.universe.included_symbols
        if assignment.target_path.startswith("universe.excluded_symbols."):
            return symbol in draft.universe.excluded_symbols
        return assignment.target_path.startswith("sharia_policy.")
    return _equal(assignment.value, final_value)


def _target_value(
    item: UnresolvedFieldV2,
    draft: StrategyDraftV2,
    *,
    assignments: Iterable[_Assignment] = (),
) -> tuple[bool, Any]:
    target_type = item.target_type
    field = item.target_field or ""
    if target_type in {"condition_field", "reference_definition", "capability_parameter"}:
        node = _conditions(draft).get(item.target_condition_id or "")
        if node is None:
            return False, None
        if target_type == "capability_parameter":
            value = node.capability_parameters.get(field)
        else:
            value = getattr(node, field or "reference_definition", None)
        return _determined(value, model=ConditionNodeV2, field=field), value
    if target_type == "draft_field":
        value = getattr(draft, field, None)
        return _determined(value, model=StrategyDraftV2, field=field), value
    if target_type == "sharia_policy":
        value = getattr(draft.sharia_policy, field, None)
        return _determined(value, model=type(draft.sharia_policy), field=field), value
    if target_type == "market_scope":
        value = getattr(draft.market_scope, field, None)
        return _determined(value, model=MarketScopeV2, field=field, defaults_are_empty=True), value
    if target_type == "universe":
        universe_value = {
            "included": list(draft.universe.included_symbols),
            "excluded": list(draft.universe.excluded_symbols),
            "mode": draft.sharia_policy.universe_mode.value,
        }
        return universe_scope_is_settled(draft, assignments=assignments), universe_value
    if target_type == "boolean_structure":
        value = _boolean_shape(draft.condition_ast)
        return bool(draft.condition_ast and draft.condition_ast.children), value
    if target_type == "unsupported_resolution":
        remains = any(
            item.unresolved_id == candidate.key for candidate in draft.unsupported_requirements
        )
        return not remains, not remains
    return False, None


def _grounds_target_value(
    text: str,
    value: Any,
    *,
    field: str,
    node: ConditionNodeV2 | None = None,
) -> bool:
    if value is None:
        return False
    if isinstance(value, Enum):
        value = value.value
    if field == "trigger_timeframe":
        return isinstance(value, str) and grounds_timeframe_role(text, value, "trigger")
    if field == "reference_timeframe":
        return isinstance(value, str) and grounds_timeframe_role(text, value, "reference")
    if field in {"context_timeframes", "confirmation_timeframes"}:
        role: Literal["context", "confirmation"] = (
            "context" if field == "context_timeframes" else "confirmation"
        )
        return (
            isinstance(value, list)
            and bool(value)
            and all(
                isinstance(item, str) and grounds_timeframe_role(text, item, role) for item in value
            )
        )
    if field == "movement_direction":
        try:
            direction = MovementDirection(str(value))
            if direction in {MovementDirection.NEUTRAL, MovementDirection.NOT_APPLICABLE}:
                return action_is_grounded(text, "clear")
            return grounds_direction(text, direction)
        except ValueError:
            return False
    if field == "strategy_bias":
        try:
            bias = StrategyBias(str(value))
            if bias == StrategyBias.NEUTRAL:
                return action_is_grounded(text, "clear")
            return grounds_strategy_bias(text, bias)
        except ValueError:
            return False
    if field == "formula":
        try:
            return grounds_formula(text, FormulaKind(str(value)))
        except ValueError:
            return False
    if field == "operator":
        from ai_market_monitor.schemas.strategy import Comparator

        try:
            return grounds_operator(text, Comparator(str(value)))
        except ValueError:
            return False
    if field == "threshold" and isinstance(value, int | float) and not isinstance(value, bool):
        unit = str(node.unit if node is not None else "plain")
        return grounds_number(text, float(value), unit={"ratio": "multiple"}.get(unit, unit))
    if field == "lookback" and isinstance(value, int):
        return grounds_lookback(
            text,
            value,
            timeframe=node.trigger_timeframe if node is not None else None,
        )
    if field == "unit" and isinstance(value, str):
        return grounds_unit(text, value)
    if field == "reference_definition" and isinstance(value, str):
        return bool(
            node is not None and node.formula is not None and grounds_formula(text, node.formula)
        )
    if field == "required" and isinstance(value, bool):
        return grounds_boolean(text, value)
    if field in {"condition_symbols", "explicit_symbols", "included_symbols", "excluded_symbols"}:
        values = value if isinstance(value, list) else [value]
        return bool(values) and all(grounds_symbol(text, str(item)) for item in values)
    if isinstance(value, bool):
        return grounds_boolean(text, value)
    if isinstance(value, int | float):
        return grounds_number(text, float(value), unit="plain")
    if isinstance(value, str):
        if "/" in value or value.upper().endswith(("USDT", "USD", "BTC", "ETH")):
            return grounds_symbol(text, value)
        return grounds_text_value(text, value)
    if isinstance(value, list):
        return bool(value) and all(
            _grounds_target_value(text, item, field=field, node=node) for item in value
        )
    if isinstance(value, dict):
        return bool(value) and all(
            _grounds_target_value(text, item, field=str(key), node=node)
            for key, item in value.items()
        )
    return False


def _condition_satisfies_slots(
    node: ConditionNodeV2,
    slots: set[str],
    assignments: dict[str, _Assignment],
) -> bool:
    required = slots or {"formula", "operator", "trigger_timeframe"}
    for slot in required:
        field = "reference_definition" if slot == "reference_definition" else slot
        parameter_key = field.removeprefix("capability_parameters.")
        value = (
            node.capability_parameters.get(parameter_key)
            if field.startswith("capability_parameters.")
            else getattr(node, field, None)
        )
        path = f"condition_ast.{node.node_id}.{field}"
        assignment = assignments.get(path)
        if not _determined(value, model=ConditionNodeV2, field=field):
            return False
        if assignment is None or not assignment.grounded:
            return False
    return True


def _coalesced_unresolved(
    before_values: Iterable[UnresolvedFieldV2],
    working_values: Iterable[UnresolvedFieldV2],
) -> list[tuple[UnresolvedFieldV2, list[UnresolvedFieldV2]]]:
    """Group open items by what they are about, newest canonical record winning.

    Both the pre-turn and the post-turn records are considered, because an item this
    turn deleted must come back unless it was really answered.  But when the *same*
    item exists on both sides, the post-turn one is the truth: an authorized
    ``update_unresolved`` is a workflow step forward, and reviving the pre-turn copy
    silently rewound the question, the answer shape and the offered options.

    That rewind is how the assistant ended up displaying step two while the stored
    contract it would validate against was still step one.
    """

    newest: dict[str, dict[str, UnresolvedFieldV2]] = {}
    for values in (before_values, working_values):
        for item in values:
            identity = unresolved_target_path(item)
            # Same path and same id: later assignment overwrites, so `working` wins.
            newest.setdefault(identity, {})[item.unresolved_id] = item
    return [
        (next(iter(bucket.values())), list(bucket.values()))
        for _, bucket in sorted(newest.items())
    ]


def _merge_unresolved(
    representative: UnresolvedFieldV2,
    aliases: list[UnresolvedFieldV2],
) -> UnresolvedFieldV2:
    """Keep the newest step's wording and shape; union only the evidence.

    ``missing_slots`` is deliberately *not* unioned across a stale copy of the same
    item — that is what restored slots the trader had already answered.  Distinct
    items that genuinely share one target still contribute theirs.
    """

    # The representative is the newest record, so its own remaining work leads. Other
    # distinct items sharing this target may add to it, never resurrect what it dropped.
    others = [item for item in aliases if item.unresolved_id != representative.unresolved_id]
    slots = [
        *representative.missing_slots,
        *(
            slot
            for item in others
            for slot in [*item.missing_slots, *([item.target_field] if item.target_field else [])]
            if slot
        ),
    ]
    if not representative.missing_slots and representative.target_field:
        slots.insert(0, representative.target_field)
    return representative.model_copy(update={"missing_slots": list(dict.fromkeys(slots))})


def _condition_requirement_key(item: UnresolvedFieldV2) -> str:
    if item.semantic_object_id:
        return item.semantic_object_id
    source = " ".join(item.source_fragment.casefold().split())
    basis = f"{item.source_turn_id or ''}:{source}"
    return hashlib.sha256(basis.encode()).hexdigest()[:20]


def _unresolved_requirement_id(item: UnresolvedFieldV2) -> str:
    return _requirement_id(item.target_type, unresolved_target_path(item), item.target_condition_id)


def _requirement_id(semantic_type: str, path: str, condition_id: str | None) -> str:
    digest = hashlib.sha256(f"{semantic_type}:{path}:{condition_id or ''}".encode()).hexdigest()[
        :24
    ]
    return f"req_{digest}"


def _semantic_type(field: str) -> str:
    if "timeframe" in field:
        return "timeframe"
    if field in {"movement_direction", "strategy_bias"}:
        return "direction"
    if field in {"threshold", "lookback"}:
        return "numeric_requirement"
    if field.startswith("capability"):
        return "capability"
    if field == "formula" or field == "operands":
        return "formula"
    if field == "condition_symbols":
        return "symbol"
    return "condition_field"


def _canonical_member(symbol: str, values: Iterable[str]) -> str:
    key = "".join(character for character in symbol.upper() if character.isalnum())
    return next(
        (
            value
            for value in values
            if "".join(character for character in value.upper() if character.isalnum()) == key
        ),
        symbol,
    )


def _conditions(draft: StrategyDraftV2) -> dict[str, ConditionNodeV2]:
    if draft.condition_ast is None:
        return {}
    return {
        node.node_id: node
        for node in draft.condition_ast.walk()
        if node.node_type == ConditionNodeType.CONDITION
    }


def _boolean_shape(node: ConditionNodeV2 | None) -> str:
    if node is None:
        return ""
    if node.node_type == ConditionNodeType.CONDITION:
        return node.node_id
    children = ",".join(_boolean_shape(child) for child in node.children)
    return f"{node.node_type.value}({children})"


def _determined(
    value: Any,
    *,
    model: type[Any],
    field: str,
    defaults_are_empty: bool = False,
) -> bool:
    if value in (None, "", [], {}):
        return False
    if not field or field not in model.model_fields:
        return True
    return not (defaults_are_empty and _is_default(model, field, value))


def _is_default(model: type[Any], field: str, value: Any) -> bool:
    info = model.model_fields.get(field)
    if info is None:
        return False
    if info.default_factory is not None:
        try:
            return value == info.default_factory()
        except TypeError:
            return False
    return info.default is not PydanticUndefined and value == info.default


def _equal(left: Any, right: Any) -> bool:
    return _json_value(left) == _json_value(right)


def _stable_value(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"))


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        rendered_list = [_json_value(item) for item in value]
        if all(isinstance(item, str | int | float | bool) for item in rendered_list):
            return rendered_list
        return json.dumps(rendered_list, sort_keys=True, separators=(",", ":"))
    if isinstance(value, dict):
        rendered_dict = {str(key): _json_value(item) for key, item in value.items()}
        if all(isinstance(item, str | int | float | bool) for item in rendered_dict.values()):
            return rendered_dict
        return json.dumps(rendered_dict, sort_keys=True, separators=(",", ":"))
    return str(value)


def _rehash(draft: StrategyDraftV2, **updates: Any) -> StrategyDraftV2:
    return StrategyDraftV2.model_validate(
        draft.model_copy(
            update={**updates, "executable_hash": "", "workflow_state_hash": ""}
        ).model_dump(mode="json")
    )
