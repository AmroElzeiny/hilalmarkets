"""What a turn's operations actually left behind, after all of them ran.

Operation evidence was built from each operation's *own* before/after step. Within one
turn that is a sequence of intermediate states, and a step can be undone by a later one:

* add a rule, then remove it — the first step reported ``condition_added`` and its id
  went into ``last_changed_condition_ids``, so the next turn could resolve "that one" to
  a rule that no longer exists
* set a threshold to 8, then to 5 — both steps claimed a change; the reply could quote 8
* remove a rule, then restore a snapshot that contains it — nothing net changed, yet the
  turn reported a removal and could invalidate an approval for it

So the sequential diffs stay for audit, and the user-facing evidence is rebuilt by
comparing the draft *before the turn* with the draft *after everything*. An operation is
reported as effective only when its intent survives into that final state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_market_monitor.engine.draft_diff import DraftChange, diff_drafts
from ai_market_monitor.schemas.setup_agent import OperationExecutionResult
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    StrategyDraftV2,
)

#: What became of one operation once the whole turn had run.
#:
#: ``effective``     — its intended target is reflected in the final draft
#: ``overwritten``   — a later operation replaced its effect
#: ``cancelled``     — a later operation undid it entirely (add-then-remove)
#: ``no_net_effect`` — it applied, but the final draft is indistinguishable
#: ``rejected``      — it never applied
NetEffect = Literal["effective", "overwritten", "cancelled", "no_net_effect", "rejected"]


@dataclass(frozen=True, slots=True)
class ReconciledOperation:
    """One operation, judged against the final state rather than its own step."""

    operation_id: str
    authorizing_segment_id: str
    operation_kind: str
    net_effect: NetEffect
    #: Only ids present or materially changed in the final draft. An id that no longer
    #: exists cannot be a reference the next turn resolves.
    final_condition_ids: tuple[str, ...] = ()
    #: The net changes attributed to this operation, from the turn-level diff.
    net_changes: tuple[DraftChange, ...] = ()
    safe_error: str | None = None

    @property
    def is_effective(self) -> bool:
        return self.net_effect == "effective"


@dataclass(frozen=True, slots=True)
class TurnReconciliation:
    """The whole turn's net position."""

    operations: tuple[ReconciledOperation, ...]
    net_changes: tuple[DraftChange, ...]
    #: True when the executable identity moved. Approval invalidation depends on this,
    #: never on a temporary intermediate state.
    executable_changed: bool

    @property
    def effective(self) -> tuple[ReconciledOperation, ...]:
        return tuple(item for item in self.operations if item.is_effective)

    @property
    def final_condition_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in self.effective:
            for node_id in item.final_condition_ids:
                if node_id not in seen:
                    seen.append(node_id)
        return tuple(seen)


def _condition_ids(draft: StrategyDraftV2) -> set[str]:
    if draft.condition_ast is None:
        return set()
    return {
        node.node_id
        for node in draft.condition_ast.walk()
        if node.node_type == ConditionNodeType.CONDITION
    }


def _conditions(draft: StrategyDraftV2) -> dict[str, ConditionNodeV2]:
    if draft.condition_ast is None:
        return {}
    return {
        node.node_id: node
        for node in draft.condition_ast.walk()
        if node.node_type == ConditionNodeType.CONDITION
    }


#: Which turn-level change kinds each operation kind is responsible for.
_OWNED_CHANGES: dict[str, frozenset[str]] = {
    "add_condition": frozenset({"condition_added"}),
    "update_condition": frozenset(
        {
            "condition_updated",
            "timeframe_changed",
            "operator_changed",
            "threshold_changed",
            "direction_changed",
            "formula_changed",
        }
    ),
    "remove_condition": frozenset({"condition_removed"}),
    "replace_groups": frozenset({"group_replaced", "condition_added", "condition_removed"}),
    "add_inclusion": frozenset({"symbol_included"}),
    "add_exclusion": frozenset({"symbol_excluded"}),
    "remove_inclusion": frozenset({"symbol_include_removed"}),
    "remove_exclusion": frozenset({"symbol_exclude_removed"}),
    "set_fields": frozenset({"mode_changed", "market_scope_changed"}),
    "set_sharia_policy": frozenset({"sharia_policy_changed", "market_scope_changed"}),
    "add_unsupported": frozenset({"unsupported_added"}),
    "add_unresolved": frozenset({"unresolved_added"}),
    "update_unresolved": frozenset({"unresolved_added", "unresolved_resolved"}),
    "resolve_unresolved_key": frozenset({"unresolved_resolved"}),
    "remove_unsupported_key": frozenset({"unsupported_resolved"}),
    "restore_snapshot": frozenset(),
}


def reconcile_turn(
    before: StrategyDraftV2,
    after: StrategyDraftV2,
    operation_results: list[OperationExecutionResult],
) -> TurnReconciliation:
    """Judge every operation against the turn's final state.

    ``before`` is the authoritative draft as the turn started; ``after`` is the finalised
    draft. Everything a user is told comes from the difference between those two.
    """

    net_changes = tuple(diff_drafts(before, after))
    after_ids = _condition_ids(after)
    after_nodes = _conditions(after)
    before_nodes = _conditions(before)
    executable_changed = after.executable_hash != before.executable_hash

    #: Which ids the net diff says were really added, removed or altered.
    net_added = {
        node_id for change in net_changes if change.kind == "condition_added"
        for node_id in change.condition_ids
    }
    net_removed = {
        node_id for change in net_changes if change.kind == "condition_removed"
        for node_id in change.condition_ids
    }
    net_touched = {
        node_id
        for change in net_changes
        if change.kind in _OWNED_CHANGES["update_condition"]
        for node_id in change.condition_ids
    }

    reconciled: list[ReconciledOperation] = []
    for result in operation_results:
        if result.rejected or not result.applied:
            reconciled.append(
                ReconciledOperation(
                    operation_id=result.operation_id,
                    authorizing_segment_id=result.authorizing_segment_id,
                    operation_kind=result.operation_kind,
                    net_effect="rejected",
                    safe_error=result.safe_error,
                )
            )
            continue

        kind = result.operation_kind
        owned = _OWNED_CHANGES.get(kind, frozenset())
        claimed = tuple(result.affected_condition_ids)
        effect: NetEffect
        final_ids: tuple[str, ...] = ()

        if kind == "add_condition":
            survived = tuple(item for item in claimed if item in after_ids and item in net_added)
            if survived:
                effect, final_ids = "effective", survived
            elif any(item not in after_ids for item in claimed):
                # It was added and is gone: a later operation undid it.
                effect = "cancelled"
            else:
                effect = "no_net_effect"
        elif kind == "remove_condition":
            gone = tuple(item for item in claimed if item in net_removed)
            if gone:
                effect, final_ids = "effective", ()
            elif any(item in after_ids for item in claimed):
                # Removed, then put back — by a restore or a later add.
                effect = "cancelled"
            else:
                effect = "no_net_effect"
        elif kind == "update_condition":
            still_changed = tuple(
                item
                for item in claimed
                if item in net_touched
                or (item in after_nodes and after_nodes[item] != before_nodes.get(item))
            )
            if still_changed:
                effect, final_ids = "effective", still_changed
            elif any(item not in after_ids for item in claimed):
                effect = "cancelled"
            elif claimed:
                # The value it wrote is not what the final draft holds: a later
                # operation on the same rule replaced it.
                effect = "overwritten"
            else:
                effect = "no_net_effect"
        elif kind == "restore_snapshot":
            effect = "effective" if net_changes else "no_net_effect"
            final_ids = tuple(sorted(after_ids))[:24]
        else:
            matched = tuple(change for change in net_changes if change.kind in owned)
            if matched:
                effect = "effective"
                final_ids = tuple(
                    node_id
                    for change in matched
                    for node_id in change.condition_ids
                    if node_id in after_ids
                )
            else:
                effect = "no_net_effect"

        reconciled.append(
            ReconciledOperation(
                operation_id=result.operation_id,
                authorizing_segment_id=result.authorizing_segment_id,
                operation_kind=kind,
                net_effect=effect,
                final_condition_ids=tuple(dict.fromkeys(final_ids)),
                net_changes=tuple(
                    change
                    for change in net_changes
                    if change.kind in owned
                    and (
                        not change.condition_ids
                        or any(item in final_ids for item in change.condition_ids)
                    )
                ),
            )
        )

    return TurnReconciliation(
        operations=tuple(reconciled),
        net_changes=net_changes,
        executable_changed=executable_changed,
    )
