"""Build the change records from two canonical drafts.

The records themselves live in :mod:`ai_market_monitor.schemas.setup_change_review`,
because the public request model imports them. Building one needs the diff engine and
the destructive-change classifier, which is why the two halves are apart: importing the
engine from a schema module pulls the whole strategy evaluator into every schema import.

Everything here reads canonical state. Nothing reads an assistant sentence, a plan's
intent summary, or any other description a model wrote of its own work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ai_market_monitor.engine.destructive_change import DestructiveVerdict
from ai_market_monitor.engine.draft_diff import DraftChange, diff_drafts
from ai_market_monitor.schemas.setup_authorization import AuthorizedPatchOperation
from ai_market_monitor.schemas.setup_change_review import (
    PENDING_CHANGE_CONTEXT_KEY,
    PENDING_CHANGE_TTL_MINUTES,
    DiffConditionEntry,
    DiffFieldChange,
    PendingDestructiveChange,
    SetupDraftDiff,
)
from ai_market_monitor.schemas.strategy_draft_v2 import ConditionNodeType, StrategyDraftV2

__all__ = ["build_draft_diff", "build_pending_change", "load_pending_change"]

def _condition_summaries(draft: StrategyDraftV2) -> dict[str, str]:
    from ai_market_monitor.engine.draft_diff import _label

    if draft.condition_ast is None:
        return {}
    return {
        node.node_id: _label(node)
        for node in draft.condition_ast.walk()
        if node.node_type == ConditionNodeType.CONDITION
    }


def _topology(draft: StrategyDraftV2) -> str:
    node = draft.condition_ast
    if node is None or not node.children:
        return ""
    return str(node.node_type.value)


def build_draft_diff(before: StrategyDraftV2, after: StrategyDraftV2) -> SetupDraftDiff:
    """Compare two canonical drafts and describe the difference exactly.

    Every entry here is a difference that exists in the stored state. Nothing is
    inferred from an instruction, a plan, or an assistant sentence.
    """

    changes: list[DraftChange] = diff_drafts(before, after)
    old_names = _condition_summaries(before)
    new_names = _condition_summaries(after)

    added: list[DiffConditionEntry] = []
    removed: list[DiffConditionEntry] = []
    fields: list[DiffFieldChange] = []
    universe: list[DiffFieldChange] = []
    methodology: list[DiffFieldChange] = []
    scope: list[DiffFieldChange] = []
    providers: list[DiffFieldChange] = []
    unresolved_added: list[str] = []
    unresolved_resolved: list[str] = []
    unresolved_advanced: list[str] = []
    unsupported_added: list[str] = []
    unsupported_removed: list[str] = []
    approval_invalidated = False

    for change in changes:
        target = change.target or ""
        if change.kind == "condition_added":
            added.append(
                DiffConditionEntry(
                    condition_id=target or "condition",
                    summary=new_names.get(target, change.detail or ""),
                )
            )
        elif change.kind == "condition_removed":
            removed.append(
                DiffConditionEntry(
                    condition_id=target or "condition",
                    summary=old_names.get(target, change.detail or ""),
                )
            )
        elif change.kind in {
            "timeframe_changed",
            "operator_changed",
            "threshold_changed",
            "direction_changed",
            "formula_changed",
            "condition_updated",
        }:
            fields.append(
                DiffFieldChange(
                    target=target or "condition",
                    label=change.detail or change.kind.replace("_", " "),
                    before=change.before,
                    after=change.after,
                )
            )
        elif change.kind in {
            "symbol_included",
            "symbol_excluded",
            "symbol_include_removed",
            "symbol_exclude_removed",
        }:
            universe.append(
                DiffFieldChange(
                    target=target or "symbol",
                    label=change.kind.replace("_", " "),
                    before=target if "removed" in change.kind else None,
                    after=None if "removed" in change.kind else target,
                )
            )
        elif change.kind == "sharia_policy_changed":
            entry = DiffFieldChange(
                target=target or "sharia_policy",
                label=change.detail or "policy",
                before=change.before,
                after=change.after,
            )
            if "methodology" in target:
                methodology.append(entry)
            else:
                universe.append(entry)
        elif change.kind in {"market_scope_changed", "mode_changed"}:
            scope.append(
                DiffFieldChange(
                    target=target or "market_scope",
                    label=change.detail or change.kind.replace("_", " "),
                    before=change.before,
                    after=change.after,
                )
            )
        elif change.kind == "unresolved_added":
            unresolved_added.append(change.detail or target)
        elif change.kind == "unresolved_resolved":
            unresolved_resolved.append(change.detail or target)
        elif change.kind == "unresolved_advanced":
            unresolved_advanced.append(change.detail or target)
        elif change.kind == "unsupported_added":
            unsupported_added.append(change.detail or target)
        elif change.kind == "unsupported_resolved":
            unsupported_removed.append(change.detail or target)
        elif change.kind == "approval_invalidated":
            approval_invalidated = True

    old_providers = {
        (item.provider, item.capability) for item in before.provider_requirements
    }
    new_providers = {(item.provider, item.capability) for item in after.provider_requirements}
    for provider, capability in sorted(new_providers - old_providers):
        providers.append(
            DiffFieldChange(target=f"{provider}:{capability}", label="added", after=capability)
        )
    for provider, capability in sorted(old_providers - new_providers):
        providers.append(
            DiffFieldChange(target=f"{provider}:{capability}", label="removed", before=capability)
        )

    topology_before = _topology(before)
    topology_after = _topology(after)
    diff = SetupDraftDiff(
        added_conditions=added,
        removed_conditions=removed,
        changed_fields=fields,
        boolean_topology_before=topology_before,
        boolean_topology_after=topology_after,
        boolean_topology_changed=bool(
            topology_before and topology_after and topology_before != topology_after
        ),
        universe_changes=universe,
        methodology_changes=methodology,
        market_scope_changes=scope,
        unresolved_added=unresolved_added,
        unresolved_resolved=unresolved_resolved,
        unresolved_advanced=unresolved_advanced,
        unsupported_added=unsupported_added,
        unsupported_removed=unsupported_removed,
        provider_requirement_changes=providers,
        approval_invalidated=approval_invalidated,
        ready_before=not before.authoring_blocking,
        ready_after=not after.authoring_blocking,
        executable_version_before=before.executable_version,
        executable_version_after=after.executable_version,
        executable_hash_before=before.executable_hash,
        executable_hash_after=after.executable_hash,
        workflow_state_hash_before=before.workflow_state_hash,
        workflow_state_hash_after=after.workflow_state_hash,
        empty=not changes and old_providers == new_providers,
    )
    return diff


def build_pending_change(
    *,
    proposal_id: str,
    source_turn_id: str,
    client_message_id: str,
    draft: StrategyDraftV2,
    projected: StrategyDraftV2,
    operations: list[AuthorizedPatchOperation],
    verdict: DestructiveVerdict,
    governance_notes: tuple[str, ...] = (),
    now: datetime | None = None,
    ttl_minutes: int = PENDING_CHANGE_TTL_MINUTES,
) -> PendingDestructiveChange:
    """Record a destructive change as a proposal, with its diff already computed.

    ``projected`` is what the draft *would* become. It is produced by running the same
    operations against a copy, so the diff the user is shown is the diff confirming
    will produce — not a description of one.
    """

    created = now or datetime.now(UTC)
    return PendingDestructiveChange(
        proposal_id=proposal_id,
        source_turn_id=source_turn_id,
        client_message_id=client_message_id,
        executable_hash=draft.executable_hash,
        workflow_state_hash=draft.workflow_state_hash,
        executable_version=draft.executable_version,
        operations=list(operations),
        diff=build_draft_diff(draft, projected),
        reasons=[str(item) for item in verdict.reasons],
        summary_lines=list(verdict.summary_lines),
        invalidates_approval=verdict.invalidates_approval,
        governance_notes=list(governance_notes),
        created_at=created,
        expires_at=created + timedelta(minutes=ttl_minutes),
        status="pending",
    )


def load_pending_change(context: dict[str, Any]) -> PendingDestructiveChange | None:
    """Read the stored proposal, or ``None`` when there is none that can be trusted.

    A payload that no longer validates is treated as absent. It can never be applied,
    which is the only safe reading of a record we cannot fully understand.
    """

    payload = context.get(PENDING_CHANGE_CONTEXT_KEY)
    if not isinstance(payload, dict):
        return None
    try:
        return PendingDestructiveChange.model_validate(payload)
    except Exception:
        return None

