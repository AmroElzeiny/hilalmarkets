"""Named lifecycle state for an AI Setup Chat session.

The session already stores a coarse ``status`` string and, separately, an approved
strategy version. Neither alone tells a client whether a turn reached a terminal
state, which is why the evaluator's UI target could not detect completion. This
module derives one explicit state from the observable facts.

The chain is ``collecting -> needs_clarification -> ready_for_confirmation ->
awaiting_approval -> approved -> compiled -> activated``. Each state is derived, not
stored: there is no second source of truth to drift from the session row, and
nothing here can move a session forward. Only the user's approval action, through
the application's own route, advances past ``awaiting_approval``.
"""

from __future__ import annotations

from typing import Literal

SetupLifecycleState = Literal[
    "collecting",
    "needs_clarification",
    "ready_for_confirmation",
    "awaiting_approval",
    "approved",
    "compiled",
    "activated",
]

#: States where the session has finished producing the current assistant turn. This
#: is transport/UI state, not approval authority.
TURN_COMPLETE_STATES: frozenset[SetupLifecycleState] = frozenset(
    {
        "collecting",
        "needs_clarification",
        "ready_for_confirmation",
        "awaiting_approval",
        "approved",
        "compiled",
        "activated",
    }
)

#: Final strategy lifecycle states. Keeping these separate from turn completion
#: prevents contracts such as ``terminal=true, approved=false`` while still letting
#: clients stop polling after a clarification or approval prompt has rendered.
TERMINAL_STATES: frozenset[SetupLifecycleState] = frozenset(
    {"approved", "compiled", "activated"}
)

#: States in which the user has been shown the exact draft and its hash. Only the
#: application's approval route may advance out of these, and only with a matching
#: hash, so a model can never approve on the user's behalf.
AWAITING_USER_STATES: frozenset[SetupLifecycleState] = frozenset(
    {"needs_clarification", "ready_for_confirmation", "awaiting_approval"}
)


def setup_lifecycle_state(
    *,
    session_status: str,
    has_draft: bool,
    approval_eligible: bool,
    blocking_findings: bool = False,
    immutable_version_hash: str | None = None,
    version_status: str | None = None,
) -> SetupLifecycleState:
    """Derive the lifecycle state from the session's observable facts.

    ``version_status`` is the approved :class:`StrategyVersion` status, which is what
    makes ``activated`` distinguishable from ``compiled``. Activation happens on the
    strategy, not in the chat, so the chat only reports it.

    ``blocking_findings`` covers a draft that exists but carries a critical lint
    finding. Reporting that as ready for confirmation would overstate it.
    """
    if version_status == "active":
        return "activated"
    if session_status == "approved":
        # Approval mints an immutable version; until that hash exists the approval is
        # recorded but nothing has been compiled to an artifact yet.
        return "compiled" if immutable_version_hash else "approved"
    if session_status == "ready_for_approval":
        # A blocking lint finding means the draft is not presentable for approval, so
        # the honest state is that something still needs resolving.
        return "awaiting_approval" if approval_eligible else "needs_clarification"
    if session_status == "needs_clarification" or blocking_findings:
        return "needs_clarification"
    if has_draft:
        # A compiled but unconfirmed draft: the reading is complete and shown for
        # review. It is inactive and carries no approval.
        return "ready_for_confirmation"
    return "collecting"


def is_terminal(state: SetupLifecycleState) -> bool:
    """True only after authenticated approval reaches a final lifecycle state."""

    return state in TERMINAL_STATES


def is_turn_complete(state: SetupLifecycleState) -> bool:
    """True when a client may stop waiting for assistant output for this turn.

    Setup-chat message requests are synchronous and return only after their
    database-backed turn has reached a persisted reply (or deterministic fallback).
    ``collecting`` describes the strategy lifecycle after a completed conversational
    turn; it does not mean that assistant output is still being produced.
    """

    return state in TURN_COMPLETE_STATES
