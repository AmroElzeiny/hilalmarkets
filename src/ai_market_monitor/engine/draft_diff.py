"""What actually changed between two canonical drafts.

The applied-change evidence used to come from ``StrategyInstructionPlan.intent_summary``
— the model's own sentence about what it meant to do. That is a description of an
intention, not a record of an outcome, and a reply built on it could confidently
describe a change the compiler had refused.

This module compares the draft before and the draft after. Every operation it reports
is a difference that exists in the canonical state, and every condition id it attaches
is one that really was created, changed or removed. The model's summary survives only in
the trace, labelled as a diagnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    StrategyDraftV2,
)

ChangeKind = Literal[
    "condition_added",
    "condition_updated",
    "condition_removed",
    "symbol_included",
    "symbol_excluded",
    "symbol_include_removed",
    "symbol_exclude_removed",
    "timeframe_changed",
    "operator_changed",
    "threshold_changed",
    "direction_changed",
    "formula_changed",
    "group_replaced",
    "mode_changed",
    "market_scope_changed",
    "approval_invalidated",
    "unsupported_added",
    "unsupported_resolved",
    "unresolved_added",
    "unresolved_resolved",
]

#: Fields whose change is worth naming on its own. A trader who asked to raise a
#: threshold wants to be told the threshold moved, not that "a condition changed".
_FIELD_CHANGES: tuple[tuple[str, ChangeKind], ...] = (
    ("trigger_timeframe", "timeframe_changed"),
    ("operator", "operator_changed"),
    ("threshold", "threshold_changed"),
    ("direction", "direction_changed"),
    ("formula", "formula_changed"),
)


@dataclass(frozen=True, slots=True)
class DraftChange:
    """One difference between two drafts, with the ids it actually touched."""

    kind: ChangeKind
    #: Only the conditions this change created, altered or deleted. Never the whole
    #: draft: attaching every condition to one instruction made the reply claim edits
    #: to rules the turn never mentioned.
    condition_ids: tuple[str, ...] = ()
    #: The field or symbol involved, when the kind alone is not specific enough.
    detail: str | None = None
    before: str | None = None
    after: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "condition_ids": list(self.condition_ids),
            "detail": self.detail,
            "before": self.before,
            "after": self.after,
        }

    def describe(self) -> str:
        """A plain sentence about this one change, built from the values themselves."""
        if self.kind == "condition_added":
            return f"added the rule {self.detail}" if self.detail else "added a rule"
        if self.kind == "condition_removed":
            return f"removed the rule {self.detail}" if self.detail else "removed a rule"
        if self.kind == "symbol_included":
            return f"added {self.detail} to the watchlist"
        if self.kind == "symbol_excluded":
            return f"excluded {self.detail}"
        if self.kind == "symbol_include_removed":
            return f"removed {self.detail} from the watchlist"
        if self.kind == "symbol_exclude_removed":
            return f"stopped excluding {self.detail}"
        if self.kind == "group_replaced":
            return "replaced the rule grouping"
        if self.kind == "mode_changed":
            return f"switched to {self.after}"
        if self.kind == "market_scope_changed":
            return f"set {self.detail} to {self.after}"
        if self.kind == "approval_invalidated":
            return "cleared the earlier approval because the rules changed"
        if self.kind == "unsupported_added":
            return f"could not express this exactly: {self.detail}"
        if self.kind == "unsupported_resolved":
            return f"resolved the unsupported item {self.detail}"
        if self.kind == "unresolved_added":
            return f"still needs {self.detail}"
        if self.kind == "unresolved_resolved":
            return f"answered {self.detail}"
        if self.before is not None and self.after is not None:
            return f"changed {self.detail} from {self.before} to {self.after}"
        return f"changed {self.detail or self.kind}"


def _conditions(draft: StrategyDraftV2) -> dict[str, ConditionNodeV2]:
    if draft.condition_ast is None:
        return {}
    return {
        node.node_id: node
        for node in draft.condition_ast.walk()
        if node.node_type == ConditionNodeType.CONDITION
    }


def _shape(node: ConditionNodeV2 | None) -> str:
    if node is None:
        return ""
    if not node.children:
        return node.node_type.value
    return f"{node.node_type.value}(" + ",".join(_shape(c) for c in node.children) + ")"


def _label(node: ConditionNodeV2) -> str:
    """A short, factual name for a rule, read off its own fields."""
    parts: list[str] = []
    if node.formula is not None:
        parts.append(node.formula.value.replace("_", " "))
    if node.operator is not None:
        parts.append(node.operator.value)
    if node.threshold is not None:
        parts.append(f"{node.threshold:g}{'%' if node.unit == 'percent' else ''}")
    if node.trigger_timeframe:
        parts.append(f"on {node.trigger_timeframe}")
    return " ".join(parts) or node.node_id


def _rendered(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def diff_drafts(before: StrategyDraftV2, after: StrategyDraftV2) -> list[DraftChange]:
    """Every difference between two canonical drafts, in a stable order."""

    changes: list[DraftChange] = []
    old = _conditions(before)
    new = _conditions(after)

    for node_id, node in new.items():
        if node_id not in old:
            changes.append(
                DraftChange(
                    kind="condition_added",
                    condition_ids=(node_id,),
                    detail=_label(node),
                )
            )
    for node_id, node in old.items():
        if node_id not in new:
            changes.append(
                DraftChange(
                    kind="condition_removed",
                    condition_ids=(node_id,),
                    detail=_label(node),
                )
            )
    for node_id, node in new.items():
        previous = old.get(node_id)
        if previous is None:
            continue
        field_changed = False
        for field, kind in _FIELD_CHANGES:
            was = getattr(previous, field)
            now = getattr(node, field)
            if was == now:
                continue
            field_changed = True
            changes.append(
                DraftChange(
                    kind=kind,
                    condition_ids=(node_id,),
                    detail=field.replace("_", " "),
                    before=_rendered(was),
                    after=_rendered(now),
                )
            )
        if not field_changed and previous != node:
            changes.append(
                DraftChange(
                    kind="condition_updated",
                    condition_ids=(node_id,),
                    detail=_label(node),
                )
            )

    old_shape = _shape(before.condition_ast)
    new_shape = _shape(after.condition_ast)
    if old_shape and new_shape and old_shape != new_shape and old.keys() == new.keys():
        # The same rules, regrouped. Reported separately because the rules themselves
        # did not change but when they fire together did.
        changes.append(
            DraftChange(kind="group_replaced", before=old_shape, after=new_shape)
        )

    for symbol in after.universe.included_symbols:
        if symbol not in before.universe.included_symbols:
            changes.append(DraftChange(kind="symbol_included", detail=symbol))
    for symbol in after.universe.excluded_symbols:
        if symbol not in before.universe.excluded_symbols:
            changes.append(DraftChange(kind="symbol_excluded", detail=symbol))
    for symbol in before.universe.included_symbols:
        if symbol not in after.universe.included_symbols:
            changes.append(DraftChange(kind="symbol_include_removed", detail=symbol))
    for symbol in before.universe.excluded_symbols:
        if symbol not in after.universe.excluded_symbols:
            changes.append(DraftChange(kind="symbol_exclude_removed", detail=symbol))

    if before.mode != after.mode:
        changes.append(
            DraftChange(
                kind="mode_changed",
                before=before.mode.value,
                after=after.mode.value,
            )
        )
    for field in ("exchange", "quote_asset", "market_type"):
        was = getattr(before.market_scope, field)
        now = getattr(after.market_scope, field)
        if was != now:
            changes.append(
                DraftChange(
                    kind="market_scope_changed",
                    detail=field.replace("_", " "),
                    before=str(was),
                    after=str(now),
                )
            )

    old_unsupported = {item.key for item in before.unsupported_requirements}
    new_unsupported = {item.key: item for item in after.unsupported_requirements}
    for key, item in new_unsupported.items():
        if key not in old_unsupported:
            changes.append(
                DraftChange(kind="unsupported_added", detail=item.missing_contract)
            )
    for key in old_unsupported - set(new_unsupported):
        changes.append(DraftChange(kind="unsupported_resolved", detail=key))

    old_unresolved = {item.key for item in before.unresolved_fields}
    new_unresolved = {item.key: item for item in after.unresolved_fields}
    for key, unresolved in new_unresolved.items():
        if key not in old_unresolved:
            changes.append(DraftChange(kind="unresolved_added", detail=unresolved.question))
    for key in old_unresolved - set(new_unresolved):
        changes.append(DraftChange(kind="unresolved_resolved", detail=key))

    if before.approval.approved and not after.approval.approved:
        changes.append(DraftChange(kind="approval_invalidated"))

    return changes


def is_material(changes: list[DraftChange]) -> bool:
    """True when something a monitor would act on actually changed.

    Answering an open question or clearing an unsupported note is real progress, but it
    is not a change to what the monitor fires on, so it must not reset an approval.
    """
    return any(
        change.kind
        not in {
            "unresolved_added",
            "unresolved_resolved",
            "unsupported_resolved",
            "approval_invalidated",
        }
        for change in changes
    )
