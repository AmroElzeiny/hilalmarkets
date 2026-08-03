"""Decide, before spending anything, whether asking the model again can work.

Runs 9, 10 and 11 attempted 18 repairs and recovered zero. Every one of them was a
paid provider call, and every one ended in the same refusal the user had already
seen — with the waiting time doubled. A repair that cannot succeed is worse than no
repair: it costs money, it costs seconds, and it hides the real problem behind a
second identical failure.

So the decision is made deterministically first, and a provider call happens only
after the decision says a correction is both possible and provable.

Seven decisions
---------------

============================  =====================================================
``DETERMINISTIC_NORMALIZATION``  the server already knows the answer from the
                                 trader's own words; correct it and make no call
``SCALAR_SEMANTIC_DELTA``        one or more named fields, each independently
                                 groundable in a verified span; one bounded call
``BOOLEAN_TOPOLOGY_REPAIR``      the leaves and operators are all present and
                                 grounded, only their arrangement is wrong; one
                                 structure-only call that may not touch semantics
``USER_CLARIFICATION``           only the trader can settle it; ask one question
``UNSUPPORTED``                  a platform boundary; no call can move it
``INTERNAL_BUG``                 the server's own fault; alert an operator, leave
                                 state untouched, never ask the user to rephrase
``NO_REPAIR_VALUE``              a call is possible but provably pointless: no time,
                                 no budget, or this exact failure already failed
============================  =====================================================

Only ``SCALAR_SEMANTIC_DELTA`` and ``BOOLEAN_TOPOLOGY_REPAIR`` spend a model call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ai_market_monitor.engine.setup_failure_taxonomy import (
    REPAIRABLE_FAILURES,
    SetupFailureClass,
)

__all__ = [
    "MINIMUM_REPAIR_SECONDS",
    "RepairDecision",
    "RepairPlan",
    "decide_repair",
]


class RepairDecision(StrEnum):
    DETERMINISTIC_NORMALIZATION = "DETERMINISTIC_NORMALIZATION"
    SCALAR_SEMANTIC_DELTA = "SCALAR_SEMANTIC_DELTA"
    BOOLEAN_TOPOLOGY_REPAIR = "BOOLEAN_TOPOLOGY_REPAIR"
    USER_CLARIFICATION = "USER_CLARIFICATION"
    UNSUPPORTED = "UNSUPPORTED"
    INTERNAL_BUG = "INTERNAL_BUG"
    NO_REPAIR_VALUE = "NO_REPAIR_VALUE"


#: A repair call that cannot finish inside the turn is not started. Starting it is
#: what produces a client timeout plus a paid answer nobody reads.
MINIMUM_REPAIR_SECONDS: Final[float] = 4.0


@dataclass(frozen=True, slots=True)
class RepairPlan:
    """The decision, and the exact reason for it."""

    decision: RepairDecision
    reason: str
    #: Model-owned field paths the repair may replace. Empty for every decision
    #: that does not spend a call.
    target_paths: tuple[str, ...] = field(default_factory=tuple)
    intent_ref: str | None = None
    segment_ref: str | None = None

    @property
    def spends_model_call(self) -> bool:
        return self.decision in {
            RepairDecision.SCALAR_SEMANTIC_DELTA,
            RepairDecision.BOOLEAN_TOPOLOGY_REPAIR,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "repair_decision": self.decision.value,
            "repair_reason": self.reason,
            "repair_target_paths": list(self.target_paths),
            "repair_intent_ref": self.intent_ref,
            "repair_segment_ref": self.segment_ref,
            "repair_spends_model_call": self.spends_model_call,
        }


_TERMINAL_DECISION: Final[dict[SetupFailureClass, tuple[RepairDecision, str]]] = {
    SetupFailureClass.UNSUPPORTED_REQUIREMENT: (
        RepairDecision.UNSUPPORTED,
        "the platform cannot express this, so no answer can change it",
    ),
    SetupFailureClass.USER_INFORMATION_REQUIRED: (
        RepairDecision.USER_CLARIFICATION,
        "only the trader can settle this value",
    ),
    SetupFailureClass.BOOLEAN_TOPOLOGY_AMBIGUOUS: (
        RepairDecision.USER_CLARIFICATION,
        "the stated logic has more than one reading",
    ),
    SetupFailureClass.COMPILER_INVARIANT_VIOLATION: (
        RepairDecision.INTERNAL_BUG,
        "the server built something invalid from a valid reading",
    ),
    SetupFailureClass.NON_RECOVERABLE_FAILURE: (
        RepairDecision.INTERNAL_BUG,
        "a boundary refused this turn by design",
    ),
    SetupFailureClass.PROVIDER_FAILURE: (
        RepairDecision.NO_REPAIR_VALUE,
        "the provider did not answer; a second call has the same problem",
    ),
    SetupFailureClass.PLANNER_SCHEMA_INVALID: (
        RepairDecision.NO_REPAIR_VALUE,
        "nothing parsed, so no field can be named in a correction",
    ),
}


def decide_repair(
    failure: SetupFailureClass,
    *,
    intent_parsed: bool,
    target_paths: Sequence[str],
    intent_ref: str | None,
    segment_ref: str | None,
    source_verified: bool,
    replacement_is_groundable: bool,
    seconds_remaining: float,
    budget_remaining_usd: float,
    attempted_fingerprints: Sequence[str],
    fingerprint: str,
    deterministic_answer_known: bool = False,
    repair_already_used: bool = False,
) -> RepairPlan:
    """The one place that decides whether a repair call may start.

    Every argument is a fact the caller already has. Nothing here reads a model, and
    nothing here is a heuristic about how likely a correction is to work: the
    conditions are the ones that make a correction *possible* and *provable*.
    """

    if deterministic_answer_known:
        return RepairPlan(
            decision=RepairDecision.DETERMINISTIC_NORMALIZATION,
            reason="the trader's own words already state the correct value",
            target_paths=tuple(target_paths),
            intent_ref=intent_ref,
            segment_ref=segment_ref,
        )
    terminal = _TERMINAL_DECISION.get(failure)
    if terminal is not None:
        decision, reason = terminal
        return RepairPlan(decision=decision, reason=reason)
    if failure not in REPAIRABLE_FAILURES:
        return RepairPlan(
            decision=RepairDecision.INTERNAL_BUG,
            reason=f"{failure.value} has no defined recovery",
        )
    if repair_already_used:
        return RepairPlan(
            decision=RepairDecision.NO_REPAIR_VALUE,
            reason="this turn has already spent its one correction",
        )
    if fingerprint in set(attempted_fingerprints):
        return RepairPlan(
            decision=RepairDecision.NO_REPAIR_VALUE,
            reason="this exact failure was already corrected once without success",
        )
    if not intent_parsed:
        return RepairPlan(
            decision=RepairDecision.NO_REPAIR_VALUE,
            reason="nothing parsed, so no field can be named in a correction",
        )
    if seconds_remaining < MINIMUM_REPAIR_SECONDS:
        return RepairPlan(
            decision=RepairDecision.NO_REPAIR_VALUE,
            reason="not enough time is left in this turn to finish a correction",
        )
    if budget_remaining_usd <= 0:
        return RepairPlan(
            decision=RepairDecision.NO_REPAIR_VALUE,
            reason="this turn's AI budget is spent",
        )
    if failure == SetupFailureClass.BOOLEAN_TOPOLOGY_MISSING:
        if not source_verified:
            return RepairPlan(
                decision=RepairDecision.NO_REPAIR_VALUE,
                reason="the stated logic could not be matched to the trader's own words",
            )
        return RepairPlan(
            decision=RepairDecision.BOOLEAN_TOPOLOGY_REPAIR,
            reason="every rule and operator is present; only the arrangement is wrong",
            target_paths=("boolean_structure",),
            intent_ref=intent_ref,
            segment_ref=segment_ref,
        )
    if not target_paths or not intent_ref or not segment_ref:
        return RepairPlan(
            decision=RepairDecision.INTERNAL_BUG,
            reason="the failure could not be attributed to one model-owned field",
        )
    if not source_verified:
        return RepairPlan(
            decision=RepairDecision.NO_REPAIR_VALUE,
            reason="the words that would authorise a replacement are not in this message",
        )
    if not replacement_is_groundable:
        return RepairPlan(
            decision=RepairDecision.USER_CLARIFICATION,
            reason="no replacement could be proved from the trader's own words",
        )
    return RepairPlan(
        decision=RepairDecision.SCALAR_SEMANTIC_DELTA,
        reason="each named field can be replaced with a value proved from the message",
        target_paths=tuple(dict.fromkeys(target_paths)),
        intent_ref=intent_ref,
        segment_ref=segment_ref,
    )
