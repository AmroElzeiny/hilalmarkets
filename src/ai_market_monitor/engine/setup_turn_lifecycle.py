"""Where one Setup Chat turn got to, and what may be done about it.

A turn is a paid, mutating unit of work. Three different questions used to be answered
by three different readers of the same ``status`` string:

* is this turn finished?  (the replay path)
* may another message start? (nothing asked this at all)
* what should a crash recovery do? (the replay path guessed from two states)

Each reader knew a different subset of the states, so a turn stuck in ``EXECUTING``
after a crash was "in progress" forever to one reader and "safe to reprocess" to
another. This module is the single owner: the state set, which states are terminal,
which hold the session, how long each stage may stay silent before it is presumed
dead, and exactly one recovery policy per state.

Nothing here touches the database or calls a model. It is a table plus the rules that
read it, so the same answer is given to the HTTP path and to the recovery worker.

The two failure names are ``RETRYABLE_FAILURE`` and ``PERMANENT_FAILURE`` rather than
``FAILED_RETRYABLE``/``FAILED_FINAL``. These exact strings are already persisted in
``setup_chat_turns.status``; renaming them would orphan every stored row and make old
turns unreadable, which is a worse outcome than a naming difference.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TurnStatus(StrEnum):
    """Where one keyed turn got to. Durable, so a retry knows what already happened."""

    #: The request was accepted and the user message stored. Nothing else has run.
    RECEIVED = "RECEIVED"
    #: A planner call is in flight. It may or may not have reached the provider.
    PLANNING = "PLANNING"
    #: An authorized plan is persisted. No operation has been applied yet.
    PLANNED = "PLANNED"
    #: Deterministic execution is running under the draft lock.
    EXECUTING = "EXECUTING"
    #: Execution committed. The canonical draft already reflects this turn.
    EXECUTED = "EXECUTED"
    #: The reply is being written. The mutation is already committed.
    COMPOSING = "COMPOSING"
    #: A recovery owner holds this turn under a lease.
    RECOVERING = "RECOVERING"

    #: Finished, with a stored reply that every replay returns unchanged.
    COMPLETED = "COMPLETED"
    #: Failed with nothing committed. The same key may be sent again.
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    #: Failed in a way that resending cannot fix.
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    #: The user settled it themselves (cancelled a pending change, for example).
    CANCELLED = "CANCELLED"
    #: Presumed dead after its lease expired with nothing committed.
    ABANDONED = "ABANDONED"
    #: A later turn took ownership of the session after this one stalled.
    SUPERSEDED = "SUPERSEDED"


#: States in which work is still owed. A turn in one of these has not settled, so it
#: still counts against the one-active-mutating-turn rule.
NON_TERMINAL_STATUSES: frozenset[TurnStatus] = frozenset(
    {
        TurnStatus.RECEIVED,
        TurnStatus.PLANNING,
        TurnStatus.PLANNED,
        TurnStatus.EXECUTING,
        TurnStatus.EXECUTED,
        TurnStatus.COMPOSING,
        TurnStatus.RECOVERING,
    }
)

#: States in which nothing further is owed. Every one of these releases the session.
TERMINAL_STATUSES: frozenset[TurnStatus] = frozenset(
    {
        TurnStatus.COMPLETED,
        TurnStatus.RETRYABLE_FAILURE,
        TurnStatus.PERMANENT_FAILURE,
        TurnStatus.CANCELLED,
        TurnStatus.ABANDONED,
        TurnStatus.SUPERSEDED,
    }
)

#: States after which the canonical draft may already carry this turn's change. A
#: recovery here must never re-apply anything; it reconciles and reports.
COMMITTED_OR_LATER: frozenset[TurnStatus] = frozenset(
    {
        TurnStatus.EXECUTING,
        TurnStatus.EXECUTED,
        TurnStatus.COMPOSING,
        TurnStatus.COMPLETED,
    }
)

_CLASSIFIED = NON_TERMINAL_STATUSES | TERMINAL_STATUSES
_MISSING = tuple(sorted(str(item) for item in TurnStatus if item not in _CLASSIFIED))
if _MISSING:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "every turn status must be classified terminal or non-terminal; missing: "
        + ", ".join(_MISSING)
    )
_BOTH = tuple(sorted(str(item) for item in NON_TERMINAL_STATUSES & TERMINAL_STATUSES))
if _BOTH:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "a turn status cannot be both terminal and non-terminal: " + ", ".join(_BOTH)
    )


class RecoveryAction(StrEnum):
    """What a recovery owner is permitted to do with a stalled turn."""

    #: Nothing was committed and no provider result is known to exist. Release the
    #: turn so the same client message id may be sent again.
    ABANDON = "abandon"
    #: A planner call may have completed without its answer being stored. Release the
    #: turn, but record the ambiguity rather than claiming a call did or did not happen.
    ABANDON_AMBIGUOUS = "abandon_ambiguous"
    #: An authorized plan exists. Re-check freshness, then execute at most once.
    REVALIDATE_AND_EXECUTE = "revalidate_and_execute"
    #: The mutation may already be committed. Compare against the canonical draft and
    #: never re-apply an operation that is already reflected there.
    RECONCILE_EXECUTION = "reconcile_execution"
    #: The mutation is committed. Build the reply from the stored result, with no
    #: planner call and no second mutation.
    COMPOSE_DETERMINISTIC = "compose_deterministic"
    #: Another owner's lease expired. Take the turn back and apply its real policy.
    RECLAIM_LEASE = "reclaim_lease"
    #: Already settled. Recovery does nothing.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    """The one recovery rule for a state, and the promises that come with it."""

    status: TurnStatus
    action: RecoveryAction
    #: How long this stage may stay silent before it is presumed dead, in seconds.
    #: Stages that hold a paid call in flight get a longer lease than stages that do
    #: not, so a slow provider is never mistaken for a crash.
    lease_seconds: int
    #: True when the canonical draft may already carry this turn's change.
    mutation_may_be_committed: bool
    #: True when recovery is allowed to call the planner. Only a bounded rebase of a
    #: stale plan may do this, and that decision is made on the request path, not here.
    planner_calls_allowed: bool = False
    #: True when recovery may call the composer. The default everywhere is a
    #: deterministic reply built from stored facts, so recovery costs nothing.
    composer_calls_allowed: bool = False


_POLICIES: dict[TurnStatus, RecoveryPolicy] = {
    TurnStatus.RECEIVED: RecoveryPolicy(
        status=TurnStatus.RECEIVED,
        action=RecoveryAction.ABANDON,
        lease_seconds=120,
        mutation_may_be_committed=False,
    ),
    TurnStatus.PLANNING: RecoveryPolicy(
        status=TurnStatus.PLANNING,
        action=RecoveryAction.ABANDON_AMBIGUOUS,
        # A planner call is in flight. The lease outlives the turn deadline so a slow
        # answer is never recovered out from under itself.
        lease_seconds=300,
        mutation_may_be_committed=False,
    ),
    TurnStatus.PLANNED: RecoveryPolicy(
        status=TurnStatus.PLANNED,
        action=RecoveryAction.REVALIDATE_AND_EXECUTE,
        lease_seconds=120,
        mutation_may_be_committed=False,
    ),
    TurnStatus.EXECUTING: RecoveryPolicy(
        status=TurnStatus.EXECUTING,
        action=RecoveryAction.RECONCILE_EXECUTION,
        lease_seconds=180,
        mutation_may_be_committed=True,
    ),
    TurnStatus.EXECUTED: RecoveryPolicy(
        status=TurnStatus.EXECUTED,
        action=RecoveryAction.COMPOSE_DETERMINISTIC,
        lease_seconds=60,
        mutation_may_be_committed=True,
    ),
    TurnStatus.COMPOSING: RecoveryPolicy(
        status=TurnStatus.COMPOSING,
        action=RecoveryAction.COMPOSE_DETERMINISTIC,
        lease_seconds=60,
        mutation_may_be_committed=True,
    ),
    TurnStatus.RECOVERING: RecoveryPolicy(
        status=TurnStatus.RECOVERING,
        action=RecoveryAction.RECLAIM_LEASE,
        lease_seconds=300,
        mutation_may_be_committed=True,
    ),
}

for _terminal in TERMINAL_STATUSES:
    _POLICIES[_terminal] = RecoveryPolicy(
        status=_terminal,
        action=RecoveryAction.NONE,
        lease_seconds=0,
        mutation_may_be_committed=_terminal is TurnStatus.COMPLETED,
    )

_MISSING_POLICIES = tuple(sorted(str(item) for item in TurnStatus if item not in _POLICIES))
if _MISSING_POLICIES:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "every turn status needs exactly one recovery policy; missing: "
        + ", ".join(_MISSING_POLICIES)
    )

#: The most attempts a single turn may be recovered before it is given up on and
#: reported to the operator queue. Without this a turn that fails the same way every
#: cycle is retried forever and the queue never learns about it.
MAX_RECOVERY_ATTEMPTS = 3


def read_status(value: str | TurnStatus | None) -> TurnStatus | None:
    """Read a stored status string. Unknown values are ``None``, never guessed at.

    A row written by a newer deployment, or corrupted, must not be silently treated as
    a state this code understands — that is how a committed turn would get re-run.
    """

    if value is None:
        return None
    if isinstance(value, TurnStatus):
        return value
    try:
        return TurnStatus(str(value))
    except ValueError:
        return None


def is_terminal(value: str | TurnStatus | None) -> bool:
    """True only for a state that owes no further work.

    An unreadable status is **not** terminal. Treating it as finished would release
    the session and let a second turn plan against a draft the first may still write.
    """

    status = read_status(value)
    return status is not None and status in TERMINAL_STATUSES


def holds_session(value: str | TurnStatus | None) -> bool:
    """True when this turn owns the session and blocks another mutating turn.

    An unreadable status holds the session, on purpose. Fail closed: refusing a new
    turn is recoverable by waiting; letting two turns write the same draft is not.
    """

    status = read_status(value)
    if status is None:
        return True
    return status in NON_TERMINAL_STATUSES


def recovery_policy(value: str | TurnStatus | None) -> RecoveryPolicy:
    """The one rule for what recovery may do with a turn in this state.

    An unreadable status gets the most cautious policy there is: assume the mutation
    may be committed, and do nothing automatic.
    """

    status = read_status(value)
    if status is None:
        return RecoveryPolicy(
            status=TurnStatus.RECOVERING,
            action=RecoveryAction.NONE,
            lease_seconds=0,
            mutation_may_be_committed=True,
        )
    return _POLICIES[status]


def lease_seconds(value: str | TurnStatus | None) -> int:
    """How long this stage may stay silent before a recovery owner may claim it."""

    return recovery_policy(value).lease_seconds
