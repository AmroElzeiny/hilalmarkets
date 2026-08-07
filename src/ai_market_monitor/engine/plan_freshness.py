"""Is a plan still about the draft it was planned against?

A Setup Chat turn reads the draft, spends a paid call working out what to do, and only
then writes. Between the read and the write, another tab, another device or a recovery
worker may have changed the same draft. Applying the plan anyway is how a user loses an
edit they never saw fail.

The old check compared two hashes. That caught a changed rule but missed everything
else a plan depends on: which question was open, whether the target condition still
exists, whether the screening methodology moved, whether an approval was granted. Each
of those can make the same operation mean something different.

So this module records **every authority a plan depends on**, compares two recordings,
and answers one question: apply, re-aim, or refuse.

Re-aiming is deliberately deterministic and free. A plan whose operations all name
targets that still exist, and whose governed authorities have not moved, can be applied
to the newer draft without asking a model anything — the operation was never about the
rest of the draft. Anything else is refused for the user to look at. There is no paid
re-planning path here on purpose: a second paid call to reinterpret words the user has
already moved on from is worse value than showing them what changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_market_monitor.schemas.setup_agent import SetupConversationContext
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    StrategyDraftV2,
)

#: Operation kinds that act on the draft as a whole. None of them can be re-aimed,
#: because "all of it" means something different once "it" has changed.
WHOLESALE_KINDS: frozenset[str] = frozenset(
    {"replace_groups", "restore_snapshot", "set_sharia_policy"}
)

#: Operation kinds that name an existing condition. Re-aiming these is safe only while
#: that exact condition is still there and still says what it said.
TARGETED_KINDS: frozenset[str] = frozenset({"update_condition", "remove_condition"})


@dataclass(frozen=True, slots=True)
class PlanningAuthority:
    """Everything a plan silently assumed was true when it was built."""

    executable_hash: str
    workflow_state_hash: str
    #: Which question was open, and which step of it. An answer planned against step 2
    #: must not be applied to step 3.
    active_question_id: str | None
    active_step_revision: int | None
    #: Every condition that existed, by id, with a fingerprint of what it said. Ids
    #: alone were not enough: a rule can be edited in place and keep its id.
    conditions: tuple[tuple[str, str], ...]
    #: The order rules appear in. "The second condition" is only meaningful while this
    #: holds, which is why a reorder refuses a positional edit rather than re-aiming it.
    condition_order: tuple[str, ...]
    methodology_id: str | None
    methodology_version: str | None
    watchlist_id: str | None
    watchlist_version: str | None
    universe_mode: str
    #: The capability registry the plan was resolved against. A capability that changed
    #: meaning between planning and execution must not be executed on the old reading.
    capability_registry_version: str
    #: True when the draft was approved at planning time. Approval granted or revoked
    #: mid-turn changes what a mutation is allowed to do.
    approved: bool
    approval_hash: str | None
    mode: str

    @classmethod
    def read(
        cls,
        draft: StrategyDraftV2,
        conversation: SetupConversationContext | None = None,
        *,
        capability_registry_version: str = "",
    ) -> PlanningAuthority:
        """Take one recording of every authority, from canonical state only."""

        nodes = _conditions(draft)
        question = conversation.active_question if conversation is not None else None
        policy = draft.sharia_policy
        return cls(
            executable_hash=draft.executable_hash,
            workflow_state_hash=draft.workflow_state_hash,
            active_question_id=question.question_id if question is not None else None,
            active_step_revision=question.step_revision if question is not None else None,
            conditions=tuple((node_id, _fingerprint(node)) for node_id, node in nodes),
            condition_order=tuple(node_id for node_id, _ in nodes),
            methodology_id=str(policy.methodology_id) if policy.methodology_id else None,
            methodology_version=policy.methodology_version,
            watchlist_id=(
                str(policy.approved_watchlist_id) if policy.approved_watchlist_id else None
            ),
            watchlist_version=(
                str(policy.approved_watchlist_version)
                if policy.approved_watchlist_version is not None
                else None
            ),
            universe_mode=str(policy.universe_mode),
            capability_registry_version=capability_registry_version,
            approved=draft.approval.approved,
            approval_hash=draft.approval.executable_hash,
            mode=str(draft.mode),
        )


def _conditions(draft: StrategyDraftV2) -> list[tuple[str, ConditionNodeV2]]:
    if draft.condition_ast is None:
        return []
    return [
        (node.node_id, node)
        for node in draft.condition_ast.walk()
        if node.node_type == ConditionNodeType.CONDITION
    ]


def _fingerprint(node: ConditionNodeV2) -> str:
    """What this rule says, as one comparable string.

    Only the fields that decide when the rule fires. Provenance and source text are
    excluded: a re-recorded fragment is not a change to what is being watched.
    """

    parts: list[Any] = [
        node.formula.value if node.formula is not None else "",
        node.operator.value if node.operator is not None else "",
        node.threshold,
        node.unit,
        node.trigger_timeframe,
        node.direction.value if node.direction is not None else "",
    ]
    return "|".join("" if item is None else str(item) for item in parts)


#: Authorities whose change always refuses a plan, whatever it was going to do. Each is
#: governed: a change here means the user or an administrator moved a boundary, and a
#: plan built before that must not sneak across it.
_GOVERNED_FIELDS: tuple[str, ...] = (
    "methodology_id",
    "methodology_version",
    "watchlist_id",
    "watchlist_version",
    "universe_mode",
    "capability_registry_version",
    "mode",
)


def _relative_order_changed(before: tuple[str, ...], now: tuple[str, ...]) -> bool:
    """True only when rules that still exist swapped places.

    Adding a rule at the end changes the raw list but changes nothing about which rule
    is "the second one" among the rules that were already there. Treating an addition as
    a reorder refused every edit made while another tab added an unrelated rule — which
    is exactly the case that is supposed to be safely re-aimed.

    So the comparison is restricted to the rules present both before and after. If their
    relative order is the same, nothing a positional phrase pointed at has moved.
    """

    shared = set(before) & set(now)
    return tuple(item for item in before if item in shared) != tuple(
        item for item in now if item in shared
    )


def moved_authorities(before: PlanningAuthority, now: PlanningAuthority) -> tuple[str, ...]:
    """Everything that changed between planning and execution, named plainly.

    The names are for logs and tests. Nothing user-facing reads them, because "the
    workflow state hash moved" means nothing to a trader.
    """

    moved: list[str] = []
    if before.executable_hash != now.executable_hash:
        moved.append("executable_state")
    if before.workflow_state_hash != now.workflow_state_hash:
        moved.append("workflow_state")
    if (before.active_question_id, before.active_step_revision) != (
        now.active_question_id,
        now.active_step_revision,
    ):
        moved.append("active_question")
    if dict(before.conditions) != dict(now.conditions):
        moved.append("conditions")
    if _relative_order_changed(before.condition_order, now.condition_order):
        moved.append("condition_order")
    for field in _GOVERNED_FIELDS:
        if getattr(before, field) != getattr(now, field):
            moved.append(field)
    if (before.approved, before.approval_hash) != (now.approved, now.approval_hash):
        moved.append("approval")
    return tuple(moved)


@dataclass(frozen=True, slots=True)
class FreshnessVerdict:
    """What to do with a plan whose draft moved underneath it."""

    #: ``apply`` — nothing relevant moved, run it as planned.
    #: ``rebase`` — something moved, but not anything this plan touches. Run it against
    #: the newer draft, deterministically and with no extra model call.
    #: ``refuse`` — the plan cannot be honestly re-aimed. Show the user what changed.
    decision: str
    moved: tuple[str, ...] = ()
    reason: str = ""

    @property
    def is_refusal(self) -> bool:
        return self.decision == "refuse"


def plan_freshness(
    before: PlanningAuthority,
    now: PlanningAuthority,
    *,
    operation_kinds: tuple[str, ...],
    target_condition_ids: tuple[str, ...],
) -> FreshnessVerdict:
    """Decide whether a plan may still run, and against which draft.

    The rule is narrow on purpose. A plan may be re-aimed only when every single one of
    these holds:

    * no governed authority moved (methodology, watchlist, universe, mode, capability
      registry, approval)
    * the open question is the same one, or there was never one
    * the plan contains no wholesale operation
    * every condition the plan names still exists and still says the same thing
    * the order of rules did not change, if the plan removes one

    Anything else is refused. There is no "probably fine" branch: a wrong guess here
    silently edits a rule the user did not mean.
    """

    moved = moved_authorities(before, now)
    if not moved:
        return FreshnessVerdict(decision="apply")

    governed = tuple(
        item
        for item in moved
        if item in set(_GOVERNED_FIELDS) | {"approval", "active_question"}
    )
    if governed:
        return FreshnessVerdict(
            decision="refuse",
            moved=moved,
            reason=f"governed authority changed: {', '.join(governed)}",
        )

    wholesale = tuple(kind for kind in operation_kinds if kind in WHOLESALE_KINDS)
    if wholesale:
        return FreshnessVerdict(
            decision="refuse",
            moved=moved,
            reason=f"whole-draft operation cannot be re-aimed: {', '.join(sorted(set(wholesale)))}",
        )

    current = dict(now.conditions)
    original = dict(before.conditions)
    for condition_id in target_condition_ids:
        if condition_id not in current:
            return FreshnessVerdict(
                decision="refuse",
                moved=moved,
                reason="the rule this change points at no longer exists",
            )
        if original.get(condition_id) != current[condition_id]:
            return FreshnessVerdict(
                decision="refuse",
                moved=moved,
                reason="the rule this change points at was edited by something else",
            )

    if "condition_order" in moved and any(
        kind in TARGETED_KINDS for kind in operation_kinds
    ):
        # "Remove the second condition" was resolved to an id under the old ordering.
        # Under a new ordering that id may no longer be the rule the user meant.
        return FreshnessVerdict(
            decision="refuse",
            moved=moved,
            reason="the rules were reordered, so this change may point at the wrong one",
        )

    return FreshnessVerdict(
        decision="rebase",
        moved=moved,
        reason="the change does not touch what moved",
    )
