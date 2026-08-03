from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from pydantic import ValidationError

from ai_market_monitor.engine.capability_contract import derive_provider_requirements
from ai_market_monitor.schemas.strategy_draft_v2 import (
    FORMULA_CONTRACTS,
    ApprovalBindingV2,
    ConditionNodeType,
    ConditionNodeV2,
    DraftMode,
    FormulaKind,
    SourceProvenanceV2,
    StrategyDraftV2,
    StrategyPatch,
    StrategyUniverseV2,
    UnresolvedFieldV2,
    UnsupportedRequirementV2,
)


class DraftPatchError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DraftPatchResult:
    draft: StrategyDraftV2
    executable_change: bool
    workflow_change: bool
    changed_fields: tuple[str, ...]

    @property
    def material_change(self) -> bool:
        """Compatibility name: material means executable, never workflow-only."""

        return self.executable_change


def new_strategy_draft(*, mode: DraftMode = DraftMode.MONITOR) -> StrategyDraftV2:
    name = "Untitled Scanner" if mode == DraftMode.SCANNER else "Untitled Monitor"
    return StrategyDraftV2(mode=mode, name=name)


def apply_strategy_patch(
    draft: StrategyDraftV2,
    patch: StrategyPatch,
    *,
    history: list[dict[str, Any]] | None = None,
    finalize_versions: bool = True,
) -> DraftPatchResult:
    """Apply one validated patch.

    ``finalize_versions=False`` is reserved for the atomic Setup Chat turn executor.
    It lets that executor validate sequential operations against a working draft while
    consuming public version numbers only once, after the complete turn succeeds.
    """

    if patch.reversion is not None:
        return _revert_to_snapshot(
            draft,
            patch,
            history or [],
            finalize_versions=finalize_versions,
        )

    original_executable_hash = draft.executable_hash
    original_workflow_hash = draft.workflow_state_hash
    changed: list[str] = []
    fields = patch.set_fields
    draft_update: dict[str, Any] = {}
    if fields.mode is not None and fields.mode != draft.mode:
        draft_update["mode"] = fields.mode
        changed.append("mode")
    if fields.name is not None and fields.name != draft.name:
        draft_update["name"] = fields.name
        changed.append("name")

    scope_update: dict[str, Any] = {}
    for key in ("exchange", "quote_asset", "market_type"):
        value = getattr(fields, key)
        if value is not None and value != getattr(draft.market_scope, key):
            scope_update[key] = value
            changed.append(f"market_scope.{key}")
    market_scope = (
        draft.market_scope.model_copy(update=scope_update) if scope_update else draft.market_scope
    )
    sharia_policy = draft.sharia_policy
    if (
        patch.set_sharia_policy is not None
        and patch.set_sharia_policy != draft.sharia_policy
    ):
        sharia_policy = patch.set_sharia_policy
        changed.append("sharia_policy")

    included = list(draft.universe.included_symbols)
    excluded = list(draft.universe.excluded_symbols)
    normalized_additions = StrategyUniverseV2(
        included_symbols=patch.add_inclusions,
    ).included_symbols
    normalized_exclusions = StrategyUniverseV2(
        excluded_symbols=patch.add_exclusions,
    ).excluded_symbols
    patch_overlap = set(normalized_additions) & set(normalized_exclusions)
    if patch_overlap:
        raise DraftPatchError(
            "one patch cannot include and exclude the same symbol: "
            + ", ".join(sorted(patch_overlap))
        )
    for symbol in normalized_additions:
        if symbol not in included:
            included.append(symbol)
            changed.append(f"universe.include:{symbol}")
    for symbol in normalized_exclusions:
        if symbol not in excluded:
            excluded.append(symbol)
            changed.append(f"universe.exclude:{symbol}")
    remove_inclusions = set(
        StrategyUniverseV2(included_symbols=patch.remove_inclusions).included_symbols
    )
    remove_exclusions = set(
        StrategyUniverseV2(excluded_symbols=patch.remove_exclusions).excluded_symbols
    )
    if remove_inclusions:
        retained = [item for item in included if item not in remove_inclusions]
        if retained != included:
            changed.append("universe.remove_inclusions")
        included = retained
    if remove_exclusions:
        retained = [item for item in excluded if item not in remove_exclusions]
        if retained != excluded:
            changed.append("universe.remove_exclusions")
        excluded = retained
    # A correction can move a symbol from include to exclude (or vice versa), but no
    # symbol may survive in both sets.
    if patch.add_exclusions:
        included = [item for item in included if item not in set(excluded)]
    if patch.add_inclusions:
        excluded = [item for item in excluded if item not in set(included)]
    universe = StrategyUniverseV2(
        included_symbols=included,
        excluded_symbols=excluded,
    )

    condition_ast = draft.condition_ast
    if patch.replace_groups is not None:
        condition_ast = patch.replace_groups
        changed.append("condition_ast.replace")
    if patch.update_conditions:
        for update in patch.update_conditions:
            condition_ast, found = _replace_node(
                condition_ast,
                update.node_id,
                update.replacement,
            )
            if not found:
                raise DraftPatchError(f"condition {update.node_id!r} does not exist")
            changed.append(f"condition_ast.update:{update.node_id}")
    if patch.remove_conditions:
        for node_id in patch.remove_conditions:
            condition_ast, removed = _remove_node(condition_ast, node_id)
            if not removed:
                raise DraftPatchError(f"condition {node_id!r} does not exist")
            changed.append(f"condition_ast.remove:{node_id}")
    if patch.add_conditions:
        before = condition_ast
        condition_ast = _append_conditions(condition_ast, patch.add_conditions)
        if condition_ast is not before:
            changed.extend(f"condition_ast.add:{item.node_id}" for item in patch.add_conditions)

    unresolved: dict[str, UnresolvedFieldV2] = {
        unresolved_item.key: unresolved_item for unresolved_item in draft.unresolved_fields
    }
    for unresolved_item in patch.unresolved_references:
        unresolved[unresolved_item.key] = unresolved_item
        changed.append(f"unresolved.added:{unresolved_item.key}")
    # Typed, explicitly named targets are the only clarification authority. Legacy
    # correction prose is still readable but cannot clear V2 state.
    resolved_keys = set(patch.remove_unresolved_keys)
    for key in resolved_keys:
        if key in unresolved:
            unresolved.pop(key)
            changed.append(f"unresolved.resolved:{key}")

    unsupported: dict[str, UnsupportedRequirementV2] = {
        unsupported_item.key: unsupported_item
        for unsupported_item in draft.unsupported_requirements
    }
    for unsupported_item in patch.unsupported_requirements:
        unsupported[unsupported_item.key] = unsupported_item
        changed.append(f"unsupported.added:{unsupported_item.key}")
    for key in patch.remove_unsupported_keys:
        if key in unsupported:
            unsupported.pop(key)
            changed.append(f"unsupported.resolved:{key}")
    # An unsupported requirement is a blocker the platform could not express. It used to
    # be swept away whenever the *same turn* also touched a condition:
    #
    #     if patch.update_conditions or ...:
    #         drop every unsupported item whose source_turn_id == this turn
    #
    # Nothing there proved the requirement was now represented. Editing an unrelated
    # rule in the same turn cleared it, and whether a blocker survived depended on the
    # order the operations happened to run in. It can now only be removed by an
    # authorized `remove_unsupported_key`, by a reconciliation that binds it to a typed
    # target, or by snapshot restoration.

    provenance = list(draft.source_provenance)
    if changed or patch.unresolved_references or patch.unsupported_requirements:
        fragments = _patch_fragments(patch)
        provenance.extend(
            SourceProvenanceV2(
                turn_id=patch.source_turn_id,
                fragment=fragment,
                applied_fields=list(dict.fromkeys(changed)) or ["unresolved"],
            )
            for fragment in fragments
        )

    candidate = draft.model_copy(
        update={
            **draft_update,
            "universe": universe,
            "market_scope": market_scope,
            "sharia_policy": sharia_policy,
            "condition_ast": condition_ast,
            "static_provider_requirements": derive_provider_requirements(condition_ast),
            "unresolved_fields": list(unresolved.values()),
            "unsupported_requirements": list(unsupported.values()),
            "source_provenance": provenance[-1000:],
            # Validate the candidate semantics before deciding whether the old
            # approval can survive. An executable edit cannot validate against the
            # prior binding, while a workflow-only edit restores it below.
            "approval": ApprovalBindingV2(),
            "executable_hash": "",
            "workflow_state_hash": "",
        }
    )
    candidate = StrategyDraftV2.model_validate(candidate.model_dump(mode="json"))
    executable_change = candidate.executable_hash != original_executable_hash
    workflow_change = candidate.workflow_state_hash != original_workflow_hash
    if not executable_change and not workflow_change:
        return DraftPatchResult(
            draft=draft,
            executable_change=False,
            workflow_change=False,
            changed_fields=(),
        )
    candidate = StrategyDraftV2.model_validate(
        candidate.model_copy(
            update={
                "executable_version": (
                    draft.executable_version + 1
                    if executable_change and finalize_versions
                    else draft.executable_version
                ),
                "workflow_revision": (
                    draft.workflow_revision + 1
                    if workflow_change and finalize_versions
                    else draft.workflow_revision
                ),
                "approval": (
                    ApprovalBindingV2() if executable_change else draft.approval
                ),
                "executable_hash": "",
                "workflow_state_hash": "",
            }
        ).model_dump(mode="json")
    )
    return DraftPatchResult(
        draft=candidate,
        executable_change=executable_change,
        workflow_change=workflow_change,
        changed_fields=tuple(dict.fromkeys(changed)),
    )


def finalize_strategy_turn(
    before: StrategyDraftV2,
    working: StrategyDraftV2,
) -> DraftPatchResult:
    """Publish one canonical revision after all turn-local operations validate."""

    executable_change = working.executable_hash != before.executable_hash
    workflow_change = working.workflow_state_hash != before.workflow_state_hash
    if not executable_change and not workflow_change:
        return DraftPatchResult(
            draft=before,
            executable_change=False,
            workflow_change=False,
            changed_fields=(),
        )
    finalized = StrategyDraftV2.model_validate(
        working.model_copy(
            update={
                "executable_version": (
                    before.executable_version + 1
                    if executable_change
                    else before.executable_version
                ),
                "workflow_revision": (
                    before.workflow_revision + 1
                    if workflow_change
                    else before.workflow_revision
                ),
                "approval": (
                    ApprovalBindingV2() if executable_change else before.approval
                ),
                "executable_hash": "",
                "workflow_state_hash": "",
            }
        ).model_dump(mode="json")
    )
    return DraftPatchResult(
        draft=finalized,
        executable_change=executable_change,
        workflow_change=workflow_change,
        changed_fields=tuple(
            name
            for name, changed_now in (
                ("executable_state", executable_change),
                ("workflow_state", workflow_change),
            )
            if changed_now
        ),
    )


def validate_draft_semantics(draft: StrategyDraftV2) -> list[str]:
    errors: list[str] = []
    policy = draft.sharia_policy
    if (policy.methodology_id is None) != (policy.methodology_version is None):
        errors.append("sharia_methodology_identity_incomplete")
    if policy.universe_mode.value == "approved_watchlist" and (
        policy.approved_watchlist_id is None
        or not policy.approved_watchlist_version
    ):
        errors.append("approved_watchlist_identity_missing")
    if policy.universe_mode.value == "explicit_assets" and not policy.explicit_symbols:
        errors.append("explicit_sharia_symbols_missing")
    overlap = set(draft.universe.included_symbols) & set(draft.universe.excluded_symbols)
    if overlap:
        errors.append(f"include_exclude_overlap:{','.join(sorted(overlap))}")
    nodes = draft.condition_ast.walk() if draft.condition_ast else []
    condition_nodes = [item for item in nodes if item.node_type == ConditionNodeType.CONDITION]
    ids = [item.node_id for item in nodes]
    if len(ids) != len(set(ids)):
        errors.append("duplicate_condition_node_id")
    errors.extend(_boolean_structure_errors(draft.condition_ast))
    excluded = set(draft.universe.excluded_symbols)
    for node in condition_nodes:
        if not node.source_turn_id or not node.source_fragment:
            errors.append(f"missing_provenance:{node.node_id}")
        if node.operator is None or node.formula is None:
            errors.append(f"incomplete_condition:{node.node_id}")
        if (
            node.operator is not None
            and node.operator.value not in {"is_true", "is_false"}
            and node.threshold is None
            and len(node.operands) < 2
        ):
            errors.append(f"missing_threshold:{node.node_id}")
        errors.extend(_formula_contract_errors(node))
        errors.extend(_operand_contract_errors(node))
        errors.extend(_timeframe_role_errors(node))
        serialized = json.dumps(
            node.model_dump(
                mode="json",
                exclude={"source_fragment", "source_turn_id"},
            ),
            sort_keys=True,
        ).upper()
        for symbol in excluded:
            if symbol.upper() in serialized or symbol.replace("/", "").upper() in serialized:
                errors.append(f"excluded_symbol_leak:{node.node_id}:{symbol}")
    if draft.approval.approved and (
        draft.approval.executable_version != draft.executable_version
        or draft.approval.executable_hash != draft.executable_hash
    ):
        errors.append("approval_binding_mismatch")
    return list(dict.fromkeys(errors))


#: How deeply a trader's own expression may nest. A person does not hand-write eight
#: levels; anything past this is a structure nobody described.
_MAX_BOOLEAN_DEPTH = 6


def _boolean_structure_errors(root: ConditionNodeV2 | None) -> list[str]:
    """Refuse a combination that does not actually combine anything.

    A *nested* ``AND`` or ``OR`` holding one child is not a choice or a requirement — it
    is the shape left behind when a real expression was flattened. It compiles, it
    validates, and it monitors something narrower than the trader asked for, with
    nothing in the artifact showing that a branch went missing.

    The root is exempt. Every draft's outermost node is the registry's own ``all
    conditions`` group, and it legitimately holds one rule when the trader has written
    only one rule so far.
    """

    if root is None:
        return []
    errors: list[str] = []

    def walk(node: ConditionNodeV2, depth: int) -> None:
        if depth > _MAX_BOOLEAN_DEPTH:
            errors.append(f"boolean_group_too_deep:{node.node_id}:{depth}")
            return
        if node.node_type == ConditionNodeType.CONDITION:
            return
        if (
            depth > 1
            and node.node_type in {ConditionNodeType.AND, ConditionNodeType.OR}
            and len(node.children) < 2
        ):
            errors.append(f"boolean_group_single_child:{node.node_id}:{node.node_type.value}")
        for child in node.children:
            walk(child, depth + 1)

    walk(root, 1)
    return errors


def _formula_contract_errors(node: ConditionNodeV2) -> list[str]:
    """Refuse a condition whose parts do not belong to the formula it names.

    A formula is not just a label: it fixes which comparisons mean anything, what
    the threshold counts, and which side it can measure. ``cross`` compared with
    ``gte``, ``sweep_and_reclaim`` carrying a percentage, or a ``high_to_low`` move
    called long each serialize cleanly and each monitor a different market event
    from the one the trader described.
    """

    if node.formula is None:
        return []
    contract = FORMULA_CONTRACTS.get(node.formula)
    if contract is None:
        # An unregistered formula has no contract to check against, so nothing here
        # can prove it safe. Refuse rather than let it through unchecked.
        return [f"formula_contract_unknown:{node.node_id}:{node.formula.value}"]
    errors: list[str] = []
    if node.operator is not None and node.operator not in contract.operators:
        errors.append(
            f"formula_operator_mismatch:{node.node_id}:"
            f"{node.formula.value}:{node.operator.value}"
        )
    if node.unit not in contract.units:
        errors.append(
            f"formula_unit_mismatch:{node.node_id}:{node.formula.value}:{node.unit}"
        )
    if node.movement_direction in contract.forbidden_directions:
        errors.append(
            f"formula_direction_mismatch:{node.node_id}:"
            f"{node.formula.value}:{node.movement_direction.value}"
        )
    return errors


def _operand_contract_errors(node: ConditionNodeV2) -> list[str]:
    """Core formulas own their operand layout; arbitrary layouts are not executable."""

    if node.formula == FormulaKind.CAPABILITY:
        return []
    percentage_formulas = {
        FormulaKind.OPEN_TO_CLOSE_PERCENTAGE,
        FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
        FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE,
        FormulaKind.HIGH_TO_LOW_PERCENTAGE,
        FormulaKind.LOW_TO_HIGH_PERCENTAGE,
    }
    if node.formula in percentage_formulas:
        if len(node.operands) != 1:
            return [f"formula_operand_mismatch:{node.node_id}:percentage_count"]
        operand = node.operands[0]
        expected = {
            FormulaKind.OPEN_TO_CLOSE_PERCENTAGE: "open_to_close",
            FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE: "close_to_close",
            FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE: "reference_to_current",
            FormulaKind.HIGH_TO_LOW_PERCENTAGE: "high_to_low",
            FormulaKind.LOW_TO_HIGH_PERCENTAGE: "low_to_high",
        }[node.formula]
        if (
            operand.kind != "market_metric"
            or operand.name != "percentage_change"
            or operand.parameters.get("formula") not in {expected, node.formula.value}
        ):
            return [f"formula_operand_mismatch:{node.node_id}:percentage_shape"]
    if node.formula == FormulaKind.SWEEP_AND_RECLAIM:
        if len(node.operands) != 1:
            return [f"formula_operand_mismatch:{node.node_id}:sweep_count"]
        operand = node.operands[0]
        if (
            operand.kind != "market_metric"
            or operand.name != "sweep_and_reclaim"
            or operand.parameters.get("pierce_required") is not True
            or operand.parameters.get("reclaim_required") is not True
        ):
            return [f"formula_operand_mismatch:{node.node_id}:sweep_shape"]
    if node.formula in {
        FormulaKind.PREVIOUS_CANDLE_REFERENCE,
        FormulaKind.FIXED_REFERENCE_LEVEL,
        FormulaKind.LOOKBACK_REFERENCE_LEVEL,
        FormulaKind.CROSS,
    }:
        if not node.operands or node.operands[0].kind != "price":
            return [f"formula_operand_mismatch:{node.node_id}:left_price"]
        if len(node.operands) > 2:
            return [f"formula_operand_mismatch:{node.node_id}:reference_count"]
        if len(node.operands) == 2 and node.operands[1].kind != "reference":
            return [f"formula_operand_mismatch:{node.node_id}:right_reference"]
    return []


def _timeframe_role_errors(node: ConditionNodeV2) -> list[str]:
    """Keep each timeframe in exactly one role.

    Trigger, context, confirmation and reference answer different questions: which
    candle fires the alert, which one sets the bias, which one confirms it, and which
    one the move is measured against. The same timeframe in two roles is how a
    context timeframe ends up firing the alert — the substitution section 6 forbids.
    """

    roles: dict[str, list[str]] = {}
    if node.trigger_timeframe:
        roles.setdefault(node.trigger_timeframe, []).append("trigger")
    for timeframe in node.context_timeframes:
        roles.setdefault(timeframe, []).append("context")
    for timeframe in node.confirmation_timeframes:
        roles.setdefault(timeframe, []).append("confirmation")
    errors: list[str] = []
    for timeframe, held in roles.items():
        if len(held) > 1:
            errors.append(
                f"timeframe_role_collision:{node.node_id}:{timeframe}:{'+'.join(held)}"
            )
    for name, values in (
        ("context", node.context_timeframes),
        ("confirmation", node.confirmation_timeframes),
    ):
        if len(values) != len(set(values)):
            errors.append(f"duplicate_{name}_timeframe:{node.node_id}")
    # A reference timeframe may legitimately equal the trigger one — `today's move`
    # measured on the candle that fires it — so it is checked for existence only.
    if node.node_type == ConditionNodeType.CONDITION and not node.trigger_timeframe:
        errors.append(f"missing_trigger_timeframe:{node.node_id}")
    return errors


def conversation_snapshot_hash(messages: Iterable[tuple[str, str]]) -> str:
    payload = [{"role": role, "content": content} for role, content in messages]
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _semantic_fingerprint(node: ConditionNodeV2) -> str:
    """What a condition *means*, ignoring its id and which turn wrote it.

    Two rules that differ only in timeframe are two real rules. Two that agree on
    every measured field are the same rule said twice.
    """
    return json.dumps(
        node.model_dump(
            mode="json",
            exclude={"node_id", "source_turn_id", "source_fragment"},
        ),
        sort_keys=True,
        separators=(",", ":"),
    )


def _append_conditions(
    root: ConditionNodeV2 | None,
    additions: list[ConditionNodeV2],
) -> ConditionNodeV2 | None:
    # A rule the draft already holds, identical in every measured field, is the same
    # rule stated twice. Appending it produced two nodes that always fire together —
    # visible to the trader as a duplicate they never asked for.
    if root is not None:
        existing = {
            _semantic_fingerprint(node)
            for node in root.walk()
            if node.node_type == ConditionNodeType.CONDITION
        }
        additions = [
            node
            for node in additions
            if node.node_type != ConditionNodeType.CONDITION
            or _semantic_fingerprint(node) not in existing
        ]
        if not additions:
            return root
    if root is None and len(additions) == 1:
        return additions[0]
    if root is None and not additions:
        return None
    if root is None:
        return ConditionNodeV2(
            node_id="root_and",
            node_type=ConditionNodeType.AND,
            children=additions,
        )
    if root.node_type == ConditionNodeType.AND:
        return root.model_copy(update={"children": [*root.children, *additions]})
    return ConditionNodeV2(
        node_id=f"and_{sha256((root.node_id + additions[0].node_id).encode()).hexdigest()[:12]}",
        node_type=ConditionNodeType.AND,
        children=[root, *additions],
    )


def _replace_node(
    node: ConditionNodeV2 | None,
    node_id: str,
    replacement: ConditionNodeV2,
) -> tuple[ConditionNodeV2 | None, bool]:
    if node is None:
        return None, False
    if node.node_id == node_id:
        return replacement, True
    children: list[ConditionNodeV2] = []
    found = False
    for child in node.children:
        updated, child_found = _replace_node(child, node_id, replacement)
        found |= child_found
        if updated is not None:
            children.append(updated)
    return (node.model_copy(update={"children": children}) if found else node), found


def _remove_node(
    node: ConditionNodeV2 | None,
    node_id: str,
) -> tuple[ConditionNodeV2 | None, bool]:
    if node is None:
        return None, False
    if node.node_id == node_id:
        return None, True
    children: list[ConditionNodeV2] = []
    found = False
    for child in node.children:
        updated, child_found = _remove_node(child, node_id)
        found |= child_found
        if updated is not None:
            children.append(updated)
    if not found:
        return node, False
    if node.node_type in {ConditionNodeType.AND, ConditionNodeType.OR} and len(children) == 1:
        return children[0], True
    if not children:
        return None, True
    return node.model_copy(update={"children": children}), True


def _patch_fragments(patch: StrategyPatch) -> list[str]:
    fragments = [
        item.source_fragment or ""
        for item in [
            *patch.add_conditions,
            *(update.replacement for update in patch.update_conditions),
        ]
    ]
    fragments.extend(item.source_fragment for item in patch.unresolved_references)
    fragments.extend(item.source_fragment for item in patch.unsupported_requirements)
    return list(dict.fromkeys(item for item in fragments if item)) or ["structured field update"]


def _revert_to_snapshot(
    draft: StrategyDraftV2,
    patch: StrategyPatch,
    history: list[dict[str, Any]],
    *,
    finalize_versions: bool,
) -> DraftPatchResult:
    assert patch.reversion is not None
    for item in history:
        payload = item.get("draft") if isinstance(item.get("draft"), dict) else item
        try:
            candidate = StrategyDraftV2.model_validate(payload)
        except ValidationError:
            continue
        if candidate.executable_version != patch.reversion.target_version:
            continue
        restored = StrategyDraftV2.model_validate(
            candidate.model_copy(
                update={
                    "workflow_revision": (
                        draft.workflow_revision + 1
                        if finalize_versions
                        else draft.workflow_revision
                    ),
                    "executable_version": (
                        draft.executable_version + 1
                        if finalize_versions
                        else draft.executable_version
                    ),
                    "approval": ApprovalBindingV2(),
                    "runtime_state": draft.runtime_state,
                    "executable_hash": "",
                    "workflow_state_hash": "",
                }
            ).model_dump(mode="json")
        )
        return DraftPatchResult(
            draft=restored,
            executable_change=restored.executable_hash != draft.executable_hash,
            workflow_change=True,
            changed_fields=("reversion",),
        )
    raise DraftPatchError(f"draft version {patch.reversion.target_version} is unavailable")
