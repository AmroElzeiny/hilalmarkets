"""What happens when a task does not work, and when to stop trying.

    attempt 1  gather more evidence, same tier
    attempt 2  escalate to a stronger model
    attempt 3  hand to the architecture and security reviewer
    after that stop, and give a person everything collected

The ladder matters less than the ceiling. An agent that retries until it succeeds will,
on a task it cannot do, retry until the money runs out — and the most expensive failure
mode is not a wrong answer but a loop that looks like progress. So every task carries
three independent limits, and reaching *any* of them stops the work:

* attempts
* wall-clock time
* spend

Each escalation records why it happened. "Attempt 2 failed" is not a reason; "the
regression test still failed after the fix" is. Without the reason, a person reading the
audit log later cannot tell a hard problem from a broken harness.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from hm_oi.routing import RANK, Tier

#: Hard ceilings. Chosen to be survivable rather than generous: a task that has burned
#: three attempts and twenty minutes is not one more attempt away from working.
MAX_ATTEMPTS: Final[int] = 3
MAX_WALL_SECONDS: Final[float] = 1800.0
MAX_SPEND_USD: Final[float] = 2.00


class StopReason(StrEnum):
    """Why a task stopped. Every value except ``SUCCEEDED`` needs a person."""

    SUCCEEDED = "succeeded"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    TIME_EXHAUSTED = "time_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NEEDS_HUMAN = "needs_human"


class EscalationAction(StrEnum):
    GATHER_EVIDENCE = "gather_evidence"
    STRONGER_MODEL = "stronger_model"
    ARCHITECTURE_REVIEW = "architecture_review"
    STOP_FOR_HUMAN = "stop_for_human"


@dataclass(frozen=True, slots=True)
class EscalationStep:
    attempt: int
    action: EscalationAction
    tier: Tier
    reason: str
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "action": str(self.action),
            "tier": str(self.tier),
            "reason": self.reason,
            "at": self.at,
        }


class LadderExhausted(RuntimeError):
    """The task stopped without succeeding, and a person is now required."""


@dataclass
class Ladder:
    """One task's attempt counter, clock and purse."""

    task_id: str
    starting_tier: Tier = Tier.NORMAL
    max_attempts: int = MAX_ATTEMPTS
    max_wall_seconds: float = MAX_WALL_SECONDS
    max_spend_usd: float = MAX_SPEND_USD
    attempts: int = 0
    spend_usd: float = 0.0
    steps: list[EscalationStep] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    stop_reason: StopReason | None = None

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def current_tier(self) -> Tier:
        """The tier in force now: the starting tier, raised by any escalation."""

        tier = self.starting_tier
        for step in self.steps:
            if RANK[step.tier] > RANK[tier]:
                tier = step.tier
        return tier

    def _stronger(self) -> Tier:
        """One tier up, or DEEP if already there."""

        order = sorted(Tier, key=lambda item: RANK[item])
        index = order.index(self.current_tier)
        return order[min(index + 1, len(order) - 1)]

    def record_spend(self, amount: float) -> None:
        self.spend_usd += max(0.0, float(amount))

    def exhausted(self) -> StopReason | None:
        """Which ceiling, if any, has been reached."""

        if self.attempts >= self.max_attempts:
            return StopReason.ATTEMPTS_EXHAUSTED
        if self.elapsed_seconds >= self.max_wall_seconds:
            return StopReason.TIME_EXHAUSTED
        if self.spend_usd >= self.max_spend_usd:
            return StopReason.BUDGET_EXHAUSTED
        return None

    def next_step(self, reason: str) -> EscalationStep:
        """Advance the ladder. Raises :class:`LadderExhausted` at the ceiling.

        ``reason`` must say what actually went wrong. It is refused if it is empty,
        because an audit log full of blank reasons is the same as no audit log.
        """

        detail = str(reason or "").strip()
        if len(detail) < 10:
            raise ValueError(
                "An escalation needs a reason saying what failed, in words. "
                "'attempt failed' is not a reason."
            )

        limit = self.exhausted()
        if limit is not None:
            self.stop_reason = limit
            step = EscalationStep(
                attempt=self.attempts,
                action=EscalationAction.STOP_FOR_HUMAN,
                tier=self.current_tier,
                reason=f"{detail} (stopping: {limit.value})",
            )
            self.steps.append(step)
            raise LadderExhausted(
                f"Task {self.task_id!r} stopped after {self.attempts} attempt(s), "
                f"{self.elapsed_seconds:.0f}s and ${self.spend_usd:.4f}. "
                f"Reason: {limit.value}.\n{detail}\n\n"
                "Everything gathered is in the audit record. A person needs to look."
            )

        self.attempts += 1
        if self.attempts == 1:
            action, tier = EscalationAction.GATHER_EVIDENCE, self.current_tier
        elif self.attempts == 2:
            action, tier = EscalationAction.STRONGER_MODEL, self._stronger()
        else:
            action, tier = EscalationAction.ARCHITECTURE_REVIEW, Tier.DEEP

        step = EscalationStep(attempt=self.attempts, action=action, tier=tier, reason=detail)
        self.steps.append(step)
        return step

    def succeed(self) -> None:
        self.stop_reason = StopReason.SUCCEEDED

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempts": self.attempts,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "spend_usd": round(self.spend_usd, 6),
            "final_tier": str(self.current_tier),
            "stop_reason": str(self.stop_reason) if self.stop_reason else None,
            "steps": [step.to_dict() for step in self.steps],
        }
