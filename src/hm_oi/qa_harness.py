"""Running an adversarial QA pass: what it is allowed to spend, and when it stops.

Three limits, all of them refusing rather than warning:

**Runs.** One pass per invocation. There is no loop that retries an attack until it
finds something, because an attack that only fails one time in twenty is measuring the
weather.

**Wall time.** A deadline set at the start. An attack that has not finished when the
deadline passes is recorded as ``NOT RUN``, never as ``passed`` — those are different
facts and only one of them is true.

**Spend.** A declared ceiling checked *before* each paid call, using the estimated cost
of the call about to be made. Checking afterwards means the ceiling is a thing you
discover you have crossed. :class:`SpendCap` therefore refuses the call that *would*
cross it, which is why validation case 8 can assert a full run stays under its cap
rather than near it.

Almost nothing here costs money. The corpus invariants, the copy scan, the boundary
registry checks and the launch-stage checks are all deterministic and free; only the
handful of attacks marked :attr:`AttackMethod.CONVERSATION` reach a provider. That is
deliberate — a safety check nobody can afford to run is a safety check nobody runs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from hm_oi.qa_attacks import (
    BOUNDARY_ATTACKS,
    CATALOGUE_VERSION,
    AttackMethod,
    BoundaryAttack,
)
from hm_oi.qa_corpus import CORPUS_VERSION
from hm_oi.qa_findings import BaselineSet, Finding, dedupe, rank, split_by_status
from hm_oi.qa_target import TargetProfile, TargetRefused

__all__ = [
    "AttackOutcome",
    "AttackStatus",
    "BudgetExceeded",
    "DEFAULT_BUDGET_USD",
    "DEFAULT_WALL_CLOCK_SECONDS",
    "QaRunReport",
    "RunLimits",
    "SpendCap",
    "attacks_runnable_against",
    "build_report",
]

#: What a pass may spend without somebody saying otherwise. Small on purpose: the
#: deterministic attacks are free and the paid ones are a handful of single turns.
DEFAULT_BUDGET_USD: Final[float] = 0.25

#: A pass that has not finished in this long has hit something unexpected.
DEFAULT_WALL_CLOCK_SECONDS: Final[float] = 1800.0


class BudgetExceeded(RuntimeError):
    """The next paid call would cross the declared ceiling, so it was not made."""


@dataclass
class SpendCap:
    """A ceiling checked before the call, not after.

    Mutable on purpose — it accumulates — and deliberately the only mutable thing in
    this module, so there is exactly one place where "how much have we spent" lives.
    """

    ceiling_usd: float = DEFAULT_BUDGET_USD
    spent_usd: float = 0.0
    calls: int = 0

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.ceiling_usd - self.spent_usd)

    def would_exceed(self, estimated_usd: float) -> bool:
        return (self.spent_usd + max(0.0, estimated_usd)) > self.ceiling_usd

    def reserve(self, estimated_usd: float, *, what: str) -> None:
        """Refuse the call if it would cross the ceiling; otherwise book it."""

        estimate = max(0.0, float(estimated_usd))
        if self.would_exceed(estimate):
            raise BudgetExceeded(
                f"Refusing {what}: it is estimated at ${estimate:.4f} and only "
                f"${self.remaining_usd:.4f} of the ${self.ceiling_usd:.2f} ceiling is "
                f"left. Nothing was sent. Raise the cap deliberately or run fewer "
                "paid attacks."
            )
        self.spent_usd += estimate
        self.calls += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "ceiling_usd": round(self.ceiling_usd, 4),
            "spent_usd": round(self.spent_usd, 4),
            "remaining_usd": round(self.remaining_usd, 4),
            "paid_calls": self.calls,
        }


@dataclass(frozen=True, slots=True)
class RunLimits:
    """Everything that stops a pass, in one object so a report can quote it."""

    budget: SpendCap = field(default_factory=SpendCap)
    wall_clock_seconds: float = DEFAULT_WALL_CLOCK_SECONDS
    #: One pass. Present as a field so the report states it rather than implying it.
    passes: int = 1
    started_at: float = field(default_factory=time.monotonic)

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.wall_clock_seconds - (time.monotonic() - self.started_at))

    @property
    def out_of_time(self) -> bool:
        return self.seconds_left <= 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passes": self.passes,
            "wall_clock_seconds": self.wall_clock_seconds,
            "seconds_left": round(self.seconds_left, 1),
            **self.budget.to_dict(),
        }


class AttackStatus:
    """Outcomes an attack can have. Strings, so a report reads plainly."""

    HELD = "held"
    VIOLATED = "violated"
    SKIPPED_UNSUPPORTED = "skipped_target_does_not_support_it"
    SKIPPED_OUT_OF_TIME = "not_run_out_of_time"
    SKIPPED_OUT_OF_BUDGET = "not_run_out_of_budget"


@dataclass(frozen=True, slots=True)
class AttackOutcome:
    """What one attack did, and why it did not run when it did not."""

    attack_id: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"attack_id": self.attack_id, "status": self.status, "detail": self.detail}


def attacks_runnable_against(
    profile: TargetProfile,
    *,
    allow_paid: bool,
    attacks: tuple[BoundaryAttack, ...] = BOUNDARY_ATTACKS,
) -> tuple[tuple[BoundaryAttack, ...], tuple[AttackOutcome, ...]]:
    """Split the catalogue into what this target can take and what it cannot.

    Returns the runnable attacks and, separately, an explicit outcome for each one that
    was left out. A skipped attack that is simply absent from the report reads as a pass,
    which is the single most misleading thing a QA tool can do.
    """

    if profile.is_production:
        raise TargetRefused(
            "Refusing to plan a run against production. "
            + "; ".join(profile.evidence)
        )

    runnable: list[BoundaryAttack] = []
    skipped: list[AttackOutcome] = []
    for attack in attacks:
        if attack.requires_fault_injection and not profile.supports_fault_injection:
            skipped.append(
                AttackOutcome(
                    attack.attack_id,
                    AttackStatus.SKIPPED_UNSUPPORTED,
                    f"This target is {profile.kind.value} with "
                    f"evaluator_fault_control_available="
                    f"{str(profile.fault_control_reported).casefold()}, so it cannot "
                    "accept an injected fault. The target's refusal is correct and is "
                    "not a finding.",
                )
            )
            continue
        if attack.method is AttackMethod.CONVERSATION and not allow_paid:
            skipped.append(
                AttackOutcome(
                    attack.attack_id,
                    AttackStatus.SKIPPED_OUT_OF_BUDGET,
                    "This attack calls a paid provider and the run was started without "
                    "--allow-paid. Nothing was sent.",
                )
            )
            continue
        runnable.append(attack)
    return tuple(runnable), tuple(skipped)


@dataclass(frozen=True, slots=True)
class QaRunReport:
    """One pass, written down so somebody else can check every claim in it."""

    run_id: str
    started_at: str
    finished_at: str
    head_sha: str
    target: TargetProfile
    limits: RunLimits
    baseline: BaselineSet
    findings: list[Finding]
    outcomes: tuple[AttackOutcome, ...] = ()
    corpus_version: str = CORPUS_VERSION
    catalogue_version: str = CATALOGUE_VERSION

    def to_dict(self) -> dict[str, Any]:
        ordered = rank(dedupe(list(self.findings)))
        split = split_by_status(ordered)
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "head_sha": self.head_sha,
            "corpus_version": self.corpus_version,
            "catalogue_version": self.catalogue_version,
            "target": self.target.to_dict(),
            "limits": self.limits.to_dict(),
            "baseline": self.baseline.to_dict(),
            "attack_outcomes": [item.to_dict() for item in self.outcomes],
            "counts": {
                "new": len(split.new),
                "baseline": len(split.baseline),
                "blocked_on_product_decision": len(split.blocked),
            },
            "findings": split.to_dict(),
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path


def build_report(
    *,
    run_id: str,
    head_sha: str,
    target: TargetProfile,
    limits: RunLimits,
    baseline: BaselineSet,
    findings: list[Finding],
    outcomes: tuple[AttackOutcome, ...] = (),
    started_at: str | None = None,
) -> QaRunReport:
    """Assemble a report, classifying nothing that was not already classified.

    Classification happens where the finding is made, not here. A report that
    re-classified would be a second opinion, and two opinions about whether something is
    a baseline failure is exactly how a baseline stops meaning anything.
    """

    now = datetime.now(UTC).isoformat()
    return QaRunReport(
        run_id=run_id,
        started_at=started_at or now,
        finished_at=now,
        head_sha=head_sha,
        target=target,
        limits=limits,
        baseline=baseline,
        findings=findings,
        outcomes=outcomes,
    )
