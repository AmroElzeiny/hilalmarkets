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

Comparing against the final state fixed *when* a change counted but not *whose* it was.
Every operation of a kind was handed every change of that kind, so in

    add BTC and ETH

both ``add_inclusion`` operations claimed both symbols. Each operation now carries its
target — the symbol, the condition id, the key, the field path — and only changes about
that target become its evidence. When an operation is superseded or cancelled, the
operation that did it is recorded by id, so "what happened to my first instruction?" has
an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_market_monitor.engine.draft_diff import DraftChange, diff_drafts
from ai_market_monitor.engine.operation_target import (
    OperationTarget,
    serialise_targets,
)
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
    #: The net changes attributed to this operation, from the turn-level diff. Only
    #: changes about **this operation's own target**.
    net_changes: tuple[DraftChange, ...] = ()
    #: What this operation aimed at, so its evidence can be checked rather than assumed.
    targets: tuple[OperationTarget, ...] = ()
    #: The later operation that replaced or undid this one, when there was one. Without
    #: it, "overwritten" is a verdict with no explanation.
    superseded_by: str | None = None
    safe_error: str | None = None

    @property
    def is_effective(self) -> bool:
        return self.net_effect == "effective"

    @property
    def target_identities(self) -> tuple[str, ...]:
        return tuple(item.identity for item in self.targets)

    def serialised_targets(self) -> list[dict[str, str]]:
        return serialise_targets(self.targets)


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

#: Operation kinds whose real target is the condition id the patch layer wrote.
_CONDITION_KINDS = frozenset({"add_condition", "update_condition", "remove_condition"})


def _owned_and_targeted(
    net_changes: tuple[DraftChange, ...],
    owned: frozenset[str],
    targets: tuple[OperationTarget, ...],
) -> tuple[DraftChange, ...]:
    """The changes that are this operation's kind **and** about its own target.

    With no targets — an operation shape that carries no identity — the kind filter is
    all there is, and that is stated rather than silently assumed to be precise.
    """

    candidates = tuple(change for change in net_changes if change.kind in owned)
    if not targets:
        return candidates
    return tuple(
        change
        for change in candidates
        if any(target.matches(change) for target in targets)
    )


def _superseding_operation(
    results: list[OperationExecutionResult],
    index: int,
    targets: tuple[OperationTarget, ...],
    targets_by_id: dict[str, tuple[OperationTarget, ...]],
) -> str | None:
    """The first later operation that acted on the same target, if any."""

    if not targets:
        return None
    wanted = {(item.kind, item.identity) for item in targets}
    for later in results[index + 1 :]:
        if later.rejected or not later.applied:
            continue
        for item in targets_by_id.get(later.operation_id, ()):
            if (item.kind, item.identity) in wanted:
                return later.operation_id
    return None


def reconcile_turn(
    before: StrategyDraftV2,
    after: StrategyDraftV2,
    operation_results: list[OperationExecutionResult],
    targets_by_id: dict[str, tuple[OperationTarget, ...]] | None = None,
) -> TurnReconciliation:
    """Judge every operation against the turn's final state, target by target.

    ``before`` is the authoritative draft as the turn started; ``after`` is the finalised
    draft. Everything a user is told comes from the difference between those two.

    ``targets_by_id`` comes from :func:`operation_target.targets_by_operation_id`. It is
    optional so a caller with only execution results still works, but without it two
    operations of one kind share their evidence again — so the production path always
    passes it.
    """

    targets_by_id = targets_by_id or {}
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
    for index, result in enumerate(operation_results):
        targets = targets_by_id.get(result.operation_id, ())
        if result.rejected or not result.applied:
            reconciled.append(
                ReconciledOperation(
                    operation_id=result.operation_id,
                    authorizing_segment_id=result.authorizing_segment_id,
                    operation_kind=result.operation_kind,
                    net_effect="rejected",
                    targets=targets,
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
            matched = _owned_and_targeted(net_changes, owned, targets)
            if matched:
                effect = "effective"
                final_ids = tuple(
                    node_id
                    for change in matched
                    for node_id in change.condition_ids
                    if node_id in after_ids
                )
            elif targets and any(
                change.kind in owned for change in net_changes
            ):
                # A change of this kind happened, but about somebody else's target. That
                # is precisely the case that used to be reported as this operation's own.
                effect = "no_net_effect"
            else:
                effect = "no_net_effect"

        # For condition operations the authoritative identity is what the patch layer
        # actually wrote, not what the plan proposed: the node id can be rewritten while
        # the operation is applied, and attributing on the proposed id would then match
        # nothing at all.
        attribution_targets = (
            tuple(OperationTarget("condition", item) for item in claimed)
            if claimed and kind in _CONDITION_KINDS
            else targets
        )
        superseded = (
            _superseding_operation(
                operation_results,
                index,
                attribution_targets or targets,
                {**targets_by_id, result.operation_id: attribution_targets or targets},
            )
            if effect in {"overwritten", "cancelled"}
            else None
        )
        attributed = _owned_and_targeted(net_changes, owned, attribution_targets)
        reconciled.append(
            ReconciledOperation(
                operation_id=result.operation_id,
                authorizing_segment_id=result.authorizing_segment_id,
                operation_kind=kind,
                net_effect=effect,
                final_condition_ids=tuple(dict.fromkeys(final_ids)),
                # Already narrowed to this operation's own target, so no second filter is
                # needed. The old one dropped a removal's evidence, because a removal has
                # no surviving condition id to filter against.
                net_changes=attributed,
                targets=attribution_targets or targets,
                superseded_by=superseded,
            )
        )

    return TurnReconciliation(
        operations=tuple(reconciled),
        net_changes=net_changes,
        executable_changed=executable_changed,
    )
