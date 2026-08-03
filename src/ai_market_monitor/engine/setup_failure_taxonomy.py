"""What went wrong in one Setup Chat turn, who owns it, and whether it can recover.

The single sentence this module exists to make true:

    ``COMPILER_INVARIANT_VIOLATION`` means the server accepted a valid semantic
    envelope and then produced an internally invalid canonical operation or state.
    Nothing else.

Before this module, that code was the catch-all. Anything the classifier could not
prove belonged to one model field became a compiler invariant, and a compiler
invariant is terminal: no repair, no clarification, HTTP 422, and a message that
tells the user nothing was changed. Evaluator runs 20260802T232050Z and
20260803T000036Z show the cost. In ``precedence_grouping-013-1996163001`` a trader
wrote a complete, ordinary instruction eight times and received eight identical
refusals. In ``operator_mapping-026-512624184`` five refusals pushed a trader into
restating ``at most 1%`` as ``strictly below`` — and that is what finally compiled.

The measured cause was narrow and fixable: two or more values omitted by the planner
on the same rule. One omission was repairable; two were reported as an internal
compiler fault. Nothing about the second omission makes the turn less understandable
than the first.

Ownership decides what happens next
-----------------------------------

============================  =============================================
owner                          what the product does
============================  =============================================
``model``                      one bounded, provable repair may be attempted
``compiler``                   change nothing, alert an operator, give the
                               user a support reference — never ask them to
                               rephrase a sentence the server mishandled
``canonical_validator``        change nothing; repairable only when exactly
                               one model-owned field is provably at fault
``user``                       ask one specific question
``provider``                   a boundary, not a mistake; retry or report
============================  =============================================
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

__all__ = [
    "FAILURE_OWNER",
    "REPAIRABLE_FAILURES",
    "FailureOwner",
    "SetupFailureClass",
    "TurnFailureRecord",
    "failure_fingerprint",
    "is_operator_alertable",
    "owner_for",
]


class SetupFailureClass(StrEnum):
    """Every way one turn can fail, named for what actually happened."""

    #: The model's answer did not parse against the compact contract at all.
    PLANNER_SCHEMA_INVALID = "PLANNER_SCHEMA_INVALID"
    #: The answer parsed, but left out a value the trader's words plainly state.
    PLANNER_SEMANTIC_OMISSION = "PLANNER_SEMANTIC_OMISSION"
    #: The answer carries a value the trader's words do not support.
    PLANNER_VALUE_MISMATCH = "PLANNER_VALUE_MISMATCH"
    #: An intent was attached to the wrong span of the message.
    SOURCE_ASSOCIATION_MISMATCH = "SOURCE_ASSOCIATION_MISMATCH"
    #: The trader stated a combination of rules and none came back.
    BOOLEAN_TOPOLOGY_MISSING = "BOOLEAN_TOPOLOGY_MISSING"
    #: The stated combination has more than one possible reading.
    BOOLEAN_TOPOLOGY_AMBIGUOUS = "BOOLEAN_TOPOLOGY_AMBIGUOUS"
    #: Only the trader can settle this; ask one specific question.
    USER_INFORMATION_REQUIRED = "USER_INFORMATION_REQUIRED"
    #: The platform cannot express this at all. A boundary, not a mistake.
    UNSUPPORTED_REQUIREMENT = "UNSUPPORTED_REQUIREMENT"
    #: A value could not be found in the trader's own words.
    GROUNDING_MISMATCH = "GROUNDING_MISMATCH"
    #: The server built something internally invalid from a valid reading.
    COMPILER_INVARIANT_VIOLATION = "COMPILER_INVARIANT_VIOLATION"
    #: A canonical gate refused the operation.
    CANONICAL_VALIDATION_FAILURE = "CANONICAL_VALIDATION_FAILURE"
    #: Authentication, ownership, screening, budget: refused by design.
    NON_RECOVERABLE_FAILURE = "NON_RECOVERABLE_FAILURE"
    #: The model or market provider did not answer.
    PROVIDER_FAILURE = "PROVIDER_FAILURE"


class FailureOwner(StrEnum):
    MODEL = "model"
    COMPILER = "compiler"
    CANONICAL_VALIDATOR = "canonical_validator"
    USER = "user"
    PROVIDER = "provider"


FAILURE_OWNER: Final[dict[SetupFailureClass, FailureOwner]] = {
    SetupFailureClass.PLANNER_SCHEMA_INVALID: FailureOwner.MODEL,
    SetupFailureClass.PLANNER_SEMANTIC_OMISSION: FailureOwner.MODEL,
    SetupFailureClass.PLANNER_VALUE_MISMATCH: FailureOwner.MODEL,
    SetupFailureClass.SOURCE_ASSOCIATION_MISMATCH: FailureOwner.MODEL,
    SetupFailureClass.BOOLEAN_TOPOLOGY_MISSING: FailureOwner.MODEL,
    SetupFailureClass.BOOLEAN_TOPOLOGY_AMBIGUOUS: FailureOwner.USER,
    SetupFailureClass.USER_INFORMATION_REQUIRED: FailureOwner.USER,
    SetupFailureClass.UNSUPPORTED_REQUIREMENT: FailureOwner.COMPILER,
    SetupFailureClass.GROUNDING_MISMATCH: FailureOwner.MODEL,
    SetupFailureClass.COMPILER_INVARIANT_VIOLATION: FailureOwner.COMPILER,
    SetupFailureClass.CANONICAL_VALIDATION_FAILURE: FailureOwner.CANONICAL_VALIDATOR,
    SetupFailureClass.NON_RECOVERABLE_FAILURE: FailureOwner.COMPILER,
    SetupFailureClass.PROVIDER_FAILURE: FailureOwner.PROVIDER,
}

#: Classes where asking the model again, with an exact named field, can succeed.
#: Eligibility is still proved case by case in ``engine/repair_eligibility.py``;
#: membership here only says the class is not excluded on principle.
REPAIRABLE_FAILURES: Final[frozenset[SetupFailureClass]] = frozenset(
    {
        SetupFailureClass.PLANNER_SEMANTIC_OMISSION,
        SetupFailureClass.PLANNER_VALUE_MISMATCH,
        SetupFailureClass.SOURCE_ASSOCIATION_MISMATCH,
        SetupFailureClass.BOOLEAN_TOPOLOGY_MISSING,
        SetupFailureClass.GROUNDING_MISMATCH,
        SetupFailureClass.CANONICAL_VALIDATION_FAILURE,
    }
)

#: Classes an operator must see. A real compiler fault and a canonical validator
#: refusal are product defects: the user did nothing wrong and cannot fix them by
#: rephrasing, so they go to a queue rather than back to the customer.
_OPERATOR_ALERTABLE: Final[frozenset[SetupFailureClass]] = frozenset(
    {
        SetupFailureClass.COMPILER_INVARIANT_VIOLATION,
        SetupFailureClass.CANONICAL_VALIDATION_FAILURE,
    }
)


def owner_for(failure: SetupFailureClass) -> FailureOwner:
    return FAILURE_OWNER[failure]


def is_operator_alertable(failure: SetupFailureClass) -> bool:
    return failure in _OPERATOR_ALERTABLE


@dataclass(frozen=True, slots=True)
class TurnFailureRecord:
    """Everything one failed turn must persist, and nothing a customer may not see.

    ``source_excerpt`` is the trader's own words, so it is always safe to show back.
    Model reasoning, provider payloads, prompts and credentials never appear here.
    """

    failure_class: SetupFailureClass
    owner: FailureOwner
    #: The compact intent that owned the problem, when exactly one did.
    intent_ref: str | None = None
    segment_ref: str | None = None
    #: The model-owned field path, such as ``condition.context_timeframes``.
    semantic_path: str | None = None
    #: Every field implicated by this one failure. ``semantic_path`` remains the
    #: compatibility primary path; this tuple is the authoritative complete set.
    semantic_paths: tuple[str, ...] = field(default_factory=tuple)
    #: The trader's own words that authorise this field.
    source_excerpt: str = ""
    #: What the words state, when the server can derive it without a model.
    expected_value: str | None = None
    expected_values: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    #: What the model actually returned.
    observed_value: str | None = None
    observed_values: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    repair_eligible: bool = False
    repair_decision: str = "NOT_EVALUATED"
    #: A stable reference a customer can quote to support. Never a stack trace.
    support_reference: str = ""
    details: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class.value,
            "failure_owner": self.owner.value,
            "intent_ref": self.intent_ref,
            "segment_ref": self.segment_ref,
            "semantic_path": self.semantic_path,
            "semantic_paths": list(self.semantic_paths),
            "source_excerpt": self.source_excerpt[:240],
            "expected_value": self.expected_value,
            "expected_values": {path: value for path, value in self.expected_values},
            "observed_value": self.observed_value,
            "observed_values": {path: value for path, value in self.observed_values},
            "repair_eligible": self.repair_eligible,
            "repair_decision": self.repair_decision,
            "support_reference": self.support_reference,
            "details": list(self.details[:8]),
            "operator_alertable": is_operator_alertable(self.failure_class),
        }


def failure_fingerprint(
    *,
    canonical_draft_hash: str,
    normalized_user_intent_hash: str,
    failure_class: SetupFailureClass | str,
    failure_paths: tuple[str, ...],
) -> str:
    """One stable key for "this exact problem, on this exact draft, again".

    Two turns share a fingerprint when the trader is asking for the same thing, the
    draft has not moved, and the same thing goes wrong in the same place. That is the
    signal that repeating the work — another paid model call, another question the
    trader has already answered — cannot help.
    """

    body = "|".join(
        [
            canonical_draft_hash,
            normalized_user_intent_hash,
            str(getattr(failure_class, "value", failure_class)),
            ",".join(sorted(failure_paths)),
        ]
    )
    return hashlib.sha256(body.encode()).hexdigest()[:32]
