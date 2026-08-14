"""One lifecycle vocabulary, shared by the Guided Builder and Setup Chat.

Two surfaces now write the same draft. If each decided for itself what "ready" means,
a person could finish a setup in the Builder, open the chat, and be told it is not
finished — the exact class of duplicate-reader defect this codebase keeps removing.

So there is one state list and one resolver. Both surfaces read it, neither computes
it. ``setup_turn_execution._final_chat_status`` delegates here rather than keeping its
own copy of the rule.

The states are ordered by how far a setup has travelled. Nothing here decides
approval, activation or Sharia status: it *reports* facts the canonical draft, the
approval binding and the session lifecycle already hold.
"""

from __future__ import annotations

from enum import StrEnum

from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2


class LifecycleState(StrEnum):
    """Every state a Watch Plan can be in, named once."""

    #: Being written. Nothing is blocking it, but it is not complete either.
    DRAFT = "draft"
    #: One or more open questions must be answered before it can run.
    NEEDS_INFORMATION = "needs_information"
    #: The person described something the platform cannot express exactly.
    UNSUPPORTED = "unsupported"
    #: A rule needs a data feed this account cannot reach.
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    #: Complete and compilable. Waiting for the person to review and approve.
    READY_FOR_REVIEW = "ready_for_review"
    #: Approved, bound to this exact executable version and hash.
    APPROVED = "approved"
    #: Approved and running.
    ACTIVE = "active"
    #: Was running; the person or the platform stopped it.
    PAUSED = "paused"
    #: Was approved, then edited in a way that changes what it watches.
    INVALIDATED_BY_EDIT = "invalidated_by_edit"
    #: Closed. Kept for the record, never evaluated.
    ARCHIVED = "archived"


#: What each state means, in words a beginner reads once and understands. Never a field
#: name, never an internal code.
STATE_LABELS: dict[LifecycleState, str] = {
    LifecycleState.DRAFT: "Draft",
    LifecycleState.NEEDS_INFORMATION: "Needs information",
    LifecycleState.UNSUPPORTED: "Not supported yet",
    LifecycleState.PROVIDER_UNAVAILABLE: "Market data unavailable",
    LifecycleState.READY_FOR_REVIEW: "Ready for review",
    LifecycleState.APPROVED: "Approved",
    LifecycleState.ACTIVE: "Active",
    LifecycleState.PAUSED: "Paused",
    LifecycleState.INVALIDATED_BY_EDIT: "Changed since approval",
    LifecycleState.ARCHIVED: "Archived",
}

STATE_EXPLANATIONS: dict[LifecycleState, str] = {
    LifecycleState.DRAFT: "You are still building this. Add the rules you want to watch.",
    LifecycleState.NEEDS_INFORMATION: (
        "A few answers are missing. Fill them in and this can be reviewed."
    ),
    LifecycleState.UNSUPPORTED: (
        "Part of this asks for something Hilal Markets cannot watch yet. "
        "Remove or change that part to continue."
    ),
    LifecycleState.PROVIDER_UNAVAILABLE: (
        "One rule needs market data this account cannot use yet."
    ),
    LifecycleState.READY_FOR_REVIEW: "Everything is filled in. Read it through, then approve.",
    LifecycleState.APPROVED: "You approved this exact version.",
    LifecycleState.ACTIVE: "This is running and watching the market for you.",
    LifecycleState.PAUSED: "This is not watching the market right now.",
    LifecycleState.INVALIDATED_BY_EDIT: (
        "You changed a rule after approving. Review it again and approve the new version."
    ),
    LifecycleState.ARCHIVED: "This is closed. It is kept for your records only.",
}

#: States in which the person can still edit the draft.
EDITABLE_STATES: frozenset[LifecycleState] = frozenset(
    {
        LifecycleState.DRAFT,
        LifecycleState.NEEDS_INFORMATION,
        LifecycleState.UNSUPPORTED,
        LifecycleState.PROVIDER_UNAVAILABLE,
        LifecycleState.READY_FOR_REVIEW,
        LifecycleState.APPROVED,
        LifecycleState.INVALIDATED_BY_EDIT,
    }
)

#: The legacy session-status strings this project already persists, and the state each
#: one means. Kept as a translation table rather than a rename: those strings are in the
#: database and in stored turn records, and renaming them would make old rows unreadable.
_SESSION_STATUS_STATES: dict[str, LifecycleState] = {
    "archived": LifecycleState.ARCHIVED,
    "paused": LifecycleState.PAUSED,
    "active": LifecycleState.ACTIVE,
    "monitoring": LifecycleState.ACTIVE,
}

#: How ``_final_chat_status`` names each state. The chat session column keeps its own
#: vocabulary, so the mapping is written down once here instead of being re-derived.
_CHAT_STATUS: dict[LifecycleState, str] = {
    LifecycleState.DRAFT: "needs_clarification",
    LifecycleState.NEEDS_INFORMATION: "needs_clarification",
    LifecycleState.UNSUPPORTED: "needs_clarification",
    LifecycleState.PROVIDER_UNAVAILABLE: "needs_clarification",
    LifecycleState.READY_FOR_REVIEW: "ready_for_approval",
    LifecycleState.APPROVED: "approved",
    LifecycleState.ACTIVE: "approved",
    LifecycleState.PAUSED: "approved",
    LifecycleState.INVALIDATED_BY_EDIT: "ready_for_approval",
    LifecycleState.ARCHIVED: "archived",
}


def _guard_complete_vocabulary() -> None:
    """Every state must carry a label, an explanation and a chat name.

    A state added without one of those shows a blank badge, or falls through to a
    default that reads as "fine" — which is how a blocked setup would look ready.
    """

    for state in LifecycleState:
        if state not in STATE_LABELS:
            raise RuntimeError(f"lifecycle state {state.value} has no label")
        if state not in STATE_EXPLANATIONS:
            raise RuntimeError(f"lifecycle state {state.value} has no explanation")
        if state not in _CHAT_STATUS:
            raise RuntimeError(f"lifecycle state {state.value} has no chat status")


_guard_complete_vocabulary()


def resolve_lifecycle(
    draft: StrategyDraftV2,
    *,
    session_status: str | None = None,
    approval_invalidated: bool = False,
) -> LifecycleState:
    """The one lifecycle answer, from canonical facts only.

    ``session_status`` is the session's own runtime column: archived, paused and active
    are facts about the *monitor*, not about the draft, and only the session knows them.
    ``approval_invalidated`` is set by the turn that removed an approval by editing.

    Order matters. Running and closed states win, because they describe what the
    platform is doing right now. After that a blocker always beats readiness — a setup
    that cannot run must never read as ready, whatever else is true about it.
    """

    runtime = _SESSION_STATUS_STATES.get((session_status or "").strip().casefold())
    if runtime is not None:
        return runtime

    if _approval_is_current(draft):
        return LifecycleState.APPROVED

    blocked = blocking_state(draft)
    if blocked is not None:
        return blocked
    if approval_invalidated:
        return LifecycleState.INVALIDATED_BY_EDIT
    return LifecycleState.READY_FOR_REVIEW


def blocking_state(draft: StrategyDraftV2) -> LifecycleState | None:
    """Why this draft cannot be reviewed yet, or ``None`` when nothing stops it.

    The order is the order a person should fix things in: something the platform cannot
    express at all, then a missing answer, then a data feed, then simply having written
    no rules. Only the first is reported, because showing four blockers at once to a
    beginner is the same as showing none.
    """

    if any(item.blocking for item in draft.unsupported_requirements):
        return LifecycleState.UNSUPPORTED
    if any(item.blocking for item in draft.unresolved_fields):
        return LifecycleState.NEEDS_INFORMATION
    if _provider_blocked(draft):
        return LifecycleState.PROVIDER_UNAVAILABLE
    if draft.condition_ast is None:
        return LifecycleState.DRAFT
    return None


def turn_lifecycle_state(
    draft: StrategyDraftV2,
    *,
    approval_status: str,
    approval_eligible: bool,
) -> LifecycleState:
    """The state one completed turn leaves the setup in.

    This exists beside :func:`resolve_lifecycle` because a turn has already decided two
    things the draft alone cannot answer: whether the approval survived, and whether
    every runtime gate passed *this turn*. ``approval_eligible`` is stricter than
    :func:`blocking_state` — an unverified data feed counts against it — so it is used
    as given rather than recomputed. Recomputing it here would be a second reader with
    a looser rule, which is exactly the failure this module exists to prevent.
    """

    if approval_status == "approved":
        return LifecycleState.APPROVED
    if not approval_eligible:
        # The draft's own blockers name the reason. When the only thing standing in the
        # way is an unverified feed, no blocker is recorded, so say so plainly.
        return blocking_state(draft) or LifecycleState.PROVIDER_UNAVAILABLE
    if approval_status == "invalidated_by_edit":
        return LifecycleState.INVALIDATED_BY_EDIT
    return LifecycleState.READY_FOR_REVIEW


def chat_status_for(state: LifecycleState) -> str:
    """The session-column string that goes with a lifecycle state."""

    return _CHAT_STATUS[state]


def describe(state: LifecycleState) -> dict[str, str]:
    """Label and explanation for one state, ready to render."""

    return {
        "state": state.value,
        "label": STATE_LABELS[state],
        "explanation": STATE_EXPLANATIONS[state],
    }


def _approval_is_current(draft: StrategyDraftV2) -> bool:
    """True when the approval on file is for exactly this version and these rules."""

    return (
        draft.approval.approved
        and draft.approval.executable_version == draft.executable_version
        and draft.approval.executable_hash == draft.executable_hash
    )


def _provider_blocked(draft: StrategyDraftV2) -> bool:
    """True when a rule needs a feed this account is known not to reach.

    ``unknown`` is deliberately not treated as blocked here. It means the check has not
    run yet, and reporting "unavailable" for a check nobody performed would tell a
    person their setup is broken when nothing is known either way. The runtime preflight
    still refuses to approve on an unverified feed — that gate is unchanged.
    """

    statuses = {
        (item.provider, item.capability): item.status
        for item in draft.runtime_state.provider_status
    }
    return any(
        statuses.get((requirement.provider, requirement.capability)) == "unavailable"
        for requirement in draft.static_provider_requirements
    )
