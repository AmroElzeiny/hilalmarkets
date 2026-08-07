"""Whether a change is big enough to ask before doing it.

The model cannot be asked this. It would be judging its own work, and a planner that
misread "change my threshold" as "replace all my rules" would also report the result as
a small edit. So the question is answered from two things the model does not author:

* the canonical before/after diff — what really changed in the draft
* the typed operation set — what the turn was authorized to do

Both are server-owned facts. A destructive verdict here is a statement about state, not
about wording, so it holds no matter how the request was phrased or in which language.

This module only classifies. It never mutates, never asks, and never approves. The
caller turns a destructive verdict into a proposal the user must confirm.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ai_market_monitor.engine.draft_diff import DraftChange
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2


class DestructiveReason(StrEnum):
    """Why one change needs the user to confirm it. Each maps to one canonical fact."""

    #: More than one rule disappears.
    MULTIPLE_CONDITIONS_REMOVED = "multiple_conditions_removed"
    #: The whole rule tree is swapped for a different one.
    CONDITION_TREE_REPLACED = "condition_tree_replaced"
    #: How the rules combine flips, so a setup that needed everything now needs anything.
    BOOLEAN_TOPOLOGY_CHANGED = "boolean_topology_changed"
    #: The Sharia screening methodology changes.
    METHODOLOGY_CHANGED = "methodology_changed"
    #: What the setup watches changes wholesale.
    UNIVERSE_MODE_CHANGED = "universe_mode_changed"
    #: A hand-picked asset list or Watchlist is emptied.
    ASSET_SELECTION_CLEARED = "asset_selection_cleared"
    #: An older version is put back in place of the current one.
    SNAPSHOT_RESTORED = "snapshot_restored"
    #: The draft is returned to its starting state.
    DRAFT_RESET = "draft_reset"
    #: A rule another operation in the same turn points at is being deleted.
    REFERENCED_CONDITION_REMOVED = "referenced_condition_removed"
    #: Several timeframes or thresholds move at once.
    MANY_PARAMETERS_CHANGED = "many_parameters_changed"
    #: An approved setup stops being approved because of a wide edit.
    APPROVED_SETUP_INVALIDATED = "approved_setup_invalidated"
    #: The exchange or quote currency changes, so every rule reads a different market.
    MARKET_SCOPE_CHANGED = "market_scope_changed"


#: Plain sentences for the user. Never an internal name, never a field path.
_REASON_TEXT: dict[DestructiveReason, str] = {
    DestructiveReason.MULTIPLE_CONDITIONS_REMOVED: "This removes more than one of your rules.",
    DestructiveReason.CONDITION_TREE_REPLACED: "This replaces all of your rules with new ones.",
    DestructiveReason.BOOLEAN_TOPOLOGY_CHANGED: (
        "This changes whether your rules must all be true together or any one of them."
    ),
    DestructiveReason.METHODOLOGY_CHANGED: (
        "This changes the Sharia screening method your setup uses."
    ),
    DestructiveReason.UNIVERSE_MODE_CHANGED: "This changes which coins your setup watches.",
    DestructiveReason.ASSET_SELECTION_CLEARED: "This empties the list of coins you chose.",
    DestructiveReason.SNAPSHOT_RESTORED: "This puts back an older version of your setup.",
    DestructiveReason.DRAFT_RESET: "This clears your setup and starts it again.",
    DestructiveReason.REFERENCED_CONDITION_REMOVED: (
        "This deletes a rule that another part of the same change still uses."
    ),
    DestructiveReason.MANY_PARAMETERS_CHANGED: (
        "This changes several numbers or timeframes at the same time."
    ),
    DestructiveReason.APPROVED_SETUP_INVALIDATED: (
        "Your setup is approved. This change makes it need approval again."
    ),
    DestructiveReason.MARKET_SCOPE_CHANGED: (
        "This changes the exchange or currency every rule is measured against."
    ),
}

_MISSING_TEXT = tuple(sorted(str(item) for item in DestructiveReason if item not in _REASON_TEXT))
if _MISSING_TEXT:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "every destructive reason needs plain wording; missing: " + ", ".join(_MISSING_TEXT)
    )

#: Operation kinds that put an older or empty state in place of the current one. These
#: are destructive by what they are, not by how much they happen to change.
_WHOLESALE_KINDS: frozenset[str] = frozenset({"restore_snapshot", "replace_groups"})

#: Diff kinds that count as "one parameter moved". Several at once is a wide edit even
#: when each on its own is small.
_PARAMETER_KINDS: frozenset[str] = frozenset(
    {"timeframe_changed", "threshold_changed", "operator_changed", "direction_changed"}
)

#: How many parameter moves in one turn stop being an edit and start being a rewrite.
PARAMETER_CHANGE_LIMIT = 3

#: How many rule deletions in one turn need confirming.
CONDITION_REMOVAL_LIMIT = 1


@dataclass(frozen=True, slots=True)
class DestructiveVerdict:
    """Whether this change must be confirmed, and the exact facts that decided it."""

    reasons: tuple[DestructiveReason, ...] = ()
    #: The rules that would disappear, by id. Empty when nothing is removed.
    removed_condition_ids: tuple[str, ...] = ()
    #: True when an approved setup stops being approved.
    invalidates_approval: bool = False
    #: Sentences to show the user, in the order the reasons were found.
    summary_lines: tuple[str, ...] = ()

    @property
    def requires_confirmation(self) -> bool:
        return bool(self.reasons)

    def to_dict(self) -> dict[str, object]:
        return {
            "requires_confirmation": self.requires_confirmation,
            "reasons": [str(item) for item in self.reasons],
            "removed_condition_ids": list(self.removed_condition_ids),
            "invalidates_approval": self.invalidates_approval,
            "summary_lines": list(self.summary_lines),
        }


@dataclass
class _Found:
    reasons: list[DestructiveReason] = field(default_factory=list)

    def add(self, reason: DestructiveReason) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)


def _condition_count(draft: StrategyDraftV2) -> int:
    from ai_market_monitor.schemas.strategy_draft_v2 import ConditionNodeType

    if draft.condition_ast is None:
        return 0
    return sum(
        1 for node in draft.condition_ast.walk() if node.node_type == ConditionNodeType.CONDITION
    )


def _root_operator(draft: StrategyDraftV2) -> str:
    """How the top of the rule tree joins its children, as plain text.

    A single rule has no join at all, which is why this returns an empty string rather
    than defaulting to ``and``. Defaulting made a first rule look like a topology flip.
    """

    node = draft.condition_ast
    if node is None or not node.children:
        return ""
    return str(node.node_type.value)


def classify_destructive_change(
    *,
    before: StrategyDraftV2,
    after: StrategyDraftV2,
    changes: list[DraftChange],
    operation_kinds: tuple[str, ...] = (),
    referenced_condition_ids: tuple[str, ...] = (),
    is_reset: bool = False,
) -> DestructiveVerdict:
    """Decide whether this change needs the user's explicit confirmation.

    ``before``/``after`` are canonical drafts and ``changes`` is their diff, so nothing
    here depends on the model's own description of what it did. ``operation_kinds`` and
    ``referenced_condition_ids`` come from the typed authorized plan.

    ``is_reset`` is passed by the reset route, which has no meaningful "after" draft to
    compare against until the user confirms.
    """

    found = _Found()

    removed = tuple(
        change.target or ""
        for change in changes
        if change.kind == "condition_removed" and change.target
    )
    if len(removed) > CONDITION_REMOVAL_LIMIT:
        found.add(DestructiveReason.MULTIPLE_CONDITIONS_REMOVED)

    before_count = _condition_count(before)
    if (
        before_count > 0
        and removed
        and len(removed) >= before_count
        and any(change.kind == "condition_added" for change in changes)
    ):
        # Every rule replaced by different rules. Counted separately from a plain
        # multi-delete because it is the whole setup being rewritten, not trimmed.
        found.add(DestructiveReason.CONDITION_TREE_REPLACED)
    # `replace_groups` is how the tree shape is written, so it is also how a *reorder*
    # and a *regroup* are written. Treating the operation kind alone as a rewrite meant
    # dragging one rule above another asked "this replaces all of your rules" — which is
    # both untrue and the fastest way to teach somebody to click Confirm without reading.
    # The rules themselves have to have moved.
    tree_content_changed = any(
        change.kind in {"condition_added", "condition_removed", "condition_updated"}
        for change in changes
    )
    if "replace_groups" in operation_kinds and before_count > 0 and tree_content_changed:
        found.add(DestructiveReason.CONDITION_TREE_REPLACED)

    old_root = _root_operator(before)
    new_root = _root_operator(after)
    if old_root and new_root and old_root != new_root:
        found.add(DestructiveReason.BOOLEAN_TOPOLOGY_CHANGED)

    for change in changes:
        if change.kind != "sharia_policy_changed":
            continue
        target = change.target or ""
        if target in {"sharia_policy.methodology_id", "sharia_policy.methodology_version"}:
            found.add(DestructiveReason.METHODOLOGY_CHANGED)
        elif target == "sharia_policy.universe_mode":
            found.add(DestructiveReason.UNIVERSE_MODE_CHANGED)
        elif (
            target
            in {
                "sharia_policy.explicit_symbols",
                "sharia_policy.approved_watchlist_id",
            }
            # Emptying a hand-picked list loses work the user did by hand. Adding to it
            # does not, so only the clearing direction is destructive.
            and change.before
            and not change.after
        ):
            found.add(DestructiveReason.ASSET_SELECTION_CLEARED)

    if any(change.kind == "market_scope_changed" for change in changes):
        found.add(DestructiveReason.MARKET_SCOPE_CHANGED)

    if "restore_snapshot" in operation_kinds:
        found.add(DestructiveReason.SNAPSHOT_RESTORED)
    if is_reset:
        found.add(DestructiveReason.DRAFT_RESET)

    referenced = set(referenced_condition_ids)
    if referenced & set(removed):
        found.add(DestructiveReason.REFERENCED_CONDITION_REMOVED)

    parameter_moves = sum(1 for change in changes if change.kind in _PARAMETER_KINDS)
    if parameter_moves > PARAMETER_CHANGE_LIMIT:
        found.add(DestructiveReason.MANY_PARAMETERS_CHANGED)

    invalidates = before.approval.approved and not after.approval.approved
    if invalidates and (found.reasons or parameter_moves > 1 or len(removed) > 0):
        # An approved setup losing its approval matters most when the edit was wide.
        # A single deliberate threshold change also invalidates approval, and asking
        # to confirm every one of those would train the user to click through.
        found.add(DestructiveReason.APPROVED_SETUP_INVALIDATED)

    reasons = tuple(found.reasons)
    return DestructiveVerdict(
        reasons=reasons,
        removed_condition_ids=removed,
        invalidates_approval=invalidates,
        summary_lines=tuple(_REASON_TEXT[item] for item in reasons),
    )


def unknown_wholesale_kinds(operation_kinds: tuple[str, ...]) -> tuple[str, ...]:
    """Wholesale operation kinds present in a plan, for callers that need the list."""

    return tuple(sorted({kind for kind in operation_kinds if kind in _WHOLESALE_KINDS}))


#: Draft fields whose change re-points every rule at a different market. A `set_fields`
#: that touches one of these is worth looking at closely; a `set_fields` that renames
#: the plan is not.
_SCOPE_FIELDS: frozenset[str] = frozenset({"exchange", "quote_asset", "market_type", "mode"})


def may_be_destructive(operations: Sequence[Any]) -> bool:
    """A cheap first look at the typed plan: is this worth examining closely?

    Working out exactly what a change would do means running it against a copy of the
    draft, which is real work. Almost every turn adds one rule or answers one question
    and can never be destructive, so this screens those out from the typed operations
    alone — no draft copy, no gates, nothing.

    It is generous but not blind. An earlier version screened in *every* `set_fields`,
    which meant renaming a plan and answering a clarification both paid for a full
    projection, and every one of them came back "not destructive". Now the payload
    decides: only a `set_fields` that moves the exchange, the quote currency or the mode
    is worth the work.

    Saying "maybe" costs one projection. Saying "no" when the answer was "yes" would
    apply a destructive change with no confirmation, so it never guesses that way.
    """

    counts: dict[str, int] = {}
    for operation in operations:
        kind = str(getattr(operation, "kind", "") or "")
        counts[kind] = counts.get(kind, 0) + 1
    if counts.get("replace_groups") or counts.get("restore_snapshot"):
        return True
    if counts.get("remove_condition", 0) > CONDITION_REMOVAL_LIMIT:
        return True
    if counts.get("set_sharia_policy"):
        return True
    if counts.get("update_condition", 0) > PARAMETER_CHANGE_LIMIT:
        return True
    for operation in operations:
        if str(getattr(operation, "kind", "")) != "set_fields":
            continue
        fields = getattr(operation, "fields", None)
        if fields is None:
            continue
        named = {
            key
            for key, value in fields.model_dump(exclude_none=True).items()
            if value is not None
        }
        if named & _SCOPE_FIELDS:
            return True
    return False
