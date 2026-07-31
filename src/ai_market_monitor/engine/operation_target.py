"""What one operation was actually aimed at, as an identity a change can be matched to.

Reconciliation matched an operation to a change by *kind alone*. In a turn like

    add BTC and ETH, and drop LTC

three operations run. Two are ``add_inclusion``, and both of them claimed every
``symbol_included`` change in the turn — so the evidence for "I added BTC" cited the ETH
change as well, and the reply could say either symbol under either operation. The same
held for two threshold edits on different rules, two answered questions, and two policy
fields set in one turn.

An operation has a target. It is written down in the operation itself: the symbol, the
condition id, the unresolved key, the field path. This module reads it out, once, and
gives reconciliation a way to ask "is this change mine?" that cannot answer yes for
somebody else's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_market_monitor.engine.draft_diff import DraftChange
from ai_market_monitor.schemas.setup_authorization import AuthorizedPatchOperation
from ai_market_monitor.schemas.strategy_draft_v2 import (
    StrategyDraftV2,
    UnresolvedFieldV2,
)

#: What kind of thing an operation aims at.
TargetKind = Literal[
    "condition",
    "symbol",
    "field_path",
    "unresolved",
    "unsupported",
    "sharia_policy",
    "snapshot",
    "structure",
]


@dataclass(frozen=True, slots=True)
class OperationTarget:
    """One thing an operation aims at, named the same way the diff names it."""

    kind: TargetKind
    #: The identity, normalised. A market symbol is upper-cased; everything else is used
    #: exactly as the draft stores it.
    identity: str

    def matches(self, change: DraftChange) -> bool:
        """Is this change about *this* target?"""
        if self.kind == "condition":
            return self.identity in change.condition_ids or change.target == self.identity
        if self.kind == "structure":
            # Regrouping has no single target; it is about the whole tree.
            return True
        if change.target is None:
            return False
        if self.kind == "symbol":
            return change.target.strip().upper() == self.identity
        return change.target == self.identity


def _symbol_identity(value: str | None) -> str:
    return (value or "").strip().upper()


def unsupported_key_for(operation: AuthorizedPatchOperation) -> str:
    """The key an ``add_unsupported`` operation will land under.

    Derived here rather than in the patch builder so the identity an operation is matched
    on and the identity it actually writes are the same expression, not two copies of one
    format string that can drift apart.
    """

    return f"unsupported_{operation.authorizing_segment_id}"


def merged_unresolved_key(
    item: UnresolvedFieldV2,
    draft: StrategyDraftV2,
) -> str:
    """The key a new unresolved item will land under, after identity merging.

    One typed target keeps one identity: asking about the same field twice reuses the
    first question's key rather than opening a second one. Reconciliation has to know
    that, or an operation would look for a key the draft never gained.
    """

    identity = (item.target_type, item.target_field, item.target_condition_id)
    for candidate in draft.unresolved_fields:
        if (
            candidate.target_type,
            candidate.target_field,
            candidate.target_condition_id,
        ) == identity:
            return candidate.unresolved_id
    return item.unresolved_id


#: Field-patch attributes and the draft path each one writes. The diff names market-scope
#: changes by path, so the operation has to name them the same way — two spellings of one
#: concept is how these mismatches start.
_FIELD_PATHS: dict[str, str] = {
    "mode": "mode",
    "exchange": "market_scope.exchange",
    "quote_asset": "market_scope.quote_asset",
    "market_type": "market_scope.market_type",
}

#: Sharia-policy attributes and the draft path each writes.
_POLICY_PATHS: tuple[str, ...] = (
    "universe_mode",
    "methodology_id",
    "methodology_version",
    "approved_watchlist_id",
    "approved_watchlist_version",
    "explicit_symbols",
    "allowed_statuses",
    "qualification_policy",
    "disputed_asset_policy",
    "compliance_change_behavior",
    "advanced_override_acknowledged",
)


def operation_targets(
    operation: AuthorizedPatchOperation,
    draft: StrategyDraftV2 | None = None,
) -> tuple[OperationTarget, ...]:
    """Every identity this operation aims at. Empty only when it truly has none.

    ``draft`` is the state the turn started from. It is needed only for unresolved items,
    whose key can be merged into an existing question about the same typed target.
    """

    kind = operation.kind
    if kind in {"add_inclusion", "add_exclusion", "remove_inclusion", "remove_exclusion"}:
        return (OperationTarget("symbol", _symbol_identity(operation.symbol)),)
    if kind == "add_condition":
        node = operation.condition
        return (
            (OperationTarget("condition", node.node_id),) if node is not None else ()
        )
    if kind in {"update_condition", "remove_condition"}:
        target = operation.target_condition_id
        return (OperationTarget("condition", target),) if target else ()
    if kind == "replace_groups":
        return (OperationTarget("structure", "condition_ast"),)
    if kind == "add_unresolved":
        item = operation.unresolved
        if item is None:
            return ()
        key = merged_unresolved_key(item, draft) if draft is not None else item.key
        return (OperationTarget("unresolved", key),)
    if kind == "update_unresolved":
        # The key it replaces *and* the key it writes. Both belong to this operation, and
        # a rename would otherwise leave one of them unattributed.
        targets = []
        if operation.target_key:
            targets.append(OperationTarget("unresolved", operation.target_key))
        if operation.unresolved is not None:
            key = (
                merged_unresolved_key(operation.unresolved, draft)
                if draft is not None
                else operation.unresolved.key
            )
            targets.append(OperationTarget("unresolved", key))
        return tuple(dict.fromkeys(targets))
    if kind == "resolve_unresolved_key":
        target = operation.target_key
        return (OperationTarget("unresolved", target),) if target else ()
    if kind == "remove_unsupported_key":
        target = operation.target_key
        return (OperationTarget("unsupported", target),) if target else ()
    if kind == "add_unsupported":
        return (OperationTarget("unsupported", unsupported_key_for(operation)),)
    if kind == "set_fields":
        patch = operation.fields
        if patch is None:
            return ()
        return tuple(
            OperationTarget("field_path", path)
            for name, path in _FIELD_PATHS.items()
            if getattr(patch, name, None) is not None
        )
    if kind == "set_sharia_policy":
        policy = operation.sharia_policy
        if policy is None:
            return ()
        return tuple(
            OperationTarget("sharia_policy", f"sharia_policy.{name}")
            for name in _POLICY_PATHS
            if hasattr(policy, name)
        )
    if kind == "restore_snapshot":
        target = operation.target_snapshot_id
        return (OperationTarget("snapshot", target),) if target else ()
    return ()


def targets_by_operation_id(
    operations: list[AuthorizedPatchOperation],
    draft: StrategyDraftV2 | None = None,
) -> dict[str, tuple[OperationTarget, ...]]:
    """Read every operation's targets once, keyed by operation id."""

    return {item.operation_id: operation_targets(item, draft) for item in operations}


def serialise_targets(targets: tuple[OperationTarget, ...]) -> list[dict[str, str]]:
    """The targets as plain data, for the persisted execution record."""

    return [{"kind": item.kind, "identity": item.identity} for item in targets]
