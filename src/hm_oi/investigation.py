"""How an operational conclusion is allowed to be stated.

A diagnosis here is not a sentence. It is a structure that cannot be built without the
things that make a diagnosis worth reading:

* which of the three kinds of problem it is;
* every claim carrying its own evidence;
* the alternatives that were considered and why they were set aside;
* what would prove it wrong;
* the environment it applies to.

:class:`Insufficient` is a first-class outcome, not a failure. The failure mode this
exists to stop is the confident answer with nothing behind it — an investigator that
always produces a diagnosis is an investigator whose diagnoses mean nothing, because it
has no way to say "I do not know" and will therefore say something else instead.

**Time-correlation is a hypothesis.** Two things that moved together are two things that
moved together. :meth:`Diagnosis.build` refuses ``HIGH`` confidence when the only support
is correlation, because that is precisely the claim that reads as causal and is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from hm_oi.evidence import Environment, Evidence, assert_single_environment


class ProblemKind(StrEnum):
    """The three kinds, which need three different responses.

    Getting this wrong is the expensive mistake. A provider outage read as a semantic
    problem sends somebody to rewrite a prompt for a week while the real cause is a
    circuit that never closed.
    """

    SEMANTIC = "semantic_model"
    APPLICATION = "application_logic"
    INFRASTRUCTURE = "provider_infrastructure"
    UNDETERMINED = "undetermined"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SupportKind(StrEnum):
    """What a claim rests on. Correlation is marked so it cannot be dressed up."""

    DIRECT = "direct"
    CORRELATION = "time_correlation"
    ABSENCE = "absence_of_signal"


class InvestigationRefused(RuntimeError):
    """A conclusion was refused because it was not supported the way it claimed."""


@dataclass(frozen=True, slots=True)
class Claim:
    """One statement, and the evidence for it."""

    statement: str
    evidence: tuple[Evidence, ...]
    support: SupportKind = SupportKind.DIRECT

    def __post_init__(self) -> None:
        if not self.evidence:
            raise InvestigationRefused(
                f"The claim {self.statement!r} has no evidence attached. Every claim "
                "cites the metric, record or file it came from, or it is not made."
            )

    def render(self) -> str:
        marks = "\n".join(f"      - {item.cite()}" for item in self.evidence)
        return f"  {self.statement}\n    support: {self.support.value}\n{marks}"


@dataclass(frozen=True, slots=True)
class Alternative:
    """Another explanation, and why it was set aside.

    Required, and required to have a reason. Listing an alternative without saying why
    it was rejected is a way of appearing thorough without being thorough.
    """

    explanation: str
    ruled_out_by: str

    def __post_init__(self) -> None:
        if len(self.ruled_out_by.strip()) < 10:
            raise InvestigationRefused(
                f"The alternative {self.explanation!r} was listed but not ruled out. Say "
                "what evidence sets it aside, or keep it as an open possibility and "
                "lower the confidence."
            )


@dataclass(frozen=True, slots=True)
class Insufficient:
    """There is not enough here to say. A complete answer, not a failed one."""

    question: str
    have: tuple[str, ...]
    missing: tuple[str, ...]
    environment: Environment | None = None
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    verdict: str = "INSUFFICIENT EVIDENCE"

    def render(self) -> str:
        lines = [
            "INSUFFICIENT EVIDENCE",
            f"question : {self.question}",
            f"environment : {self.environment.value if self.environment else 'not established'}",
            "what is available:",
            *[f"  - {item}" for item in self.have or ("nothing usable",)],
            "what is missing before this can be answered:",
            *[f"  - {item}" for item in self.missing],
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "question": self.question,
            "have": list(self.have),
            "missing": list(self.missing),
            "environment": self.environment.value if self.environment else None,
            "at": self.at,
        }


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """A supported conclusion about one environment."""

    question: str
    kind: ProblemKind
    summary: str
    claims: tuple[Claim, ...]
    alternatives: tuple[Alternative, ...]
    falsified_by: str
    confidence: Confidence
    environment: Environment
    recommendation: str = ""
    #: The exact command a person would run. Never run by this tool.
    operator_command: str = ""
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    verdict: str = "DIAGNOSIS"

    @staticmethod
    def build(
        *,
        question: str,
        kind: ProblemKind,
        summary: str,
        claims: tuple[Claim, ...],
        alternatives: tuple[Alternative, ...],
        falsified_by: str,
        confidence: Confidence,
        recommendation: str = "",
        operator_command: str = "",
    ) -> Diagnosis:
        """Assemble a diagnosis, refusing the ways one can be unsound."""

        if not claims:
            raise InvestigationRefused(
                "A diagnosis with no claims is an opinion. Return Insufficient instead."
            )

        every: list[Evidence] = [item for claim in claims for item in claim.evidence]
        environment = assert_single_environment(every)

        if not alternatives:
            raise InvestigationRefused(
                "No alternative explanation was considered. Name at least one and say "
                "what rules it out, or return Insufficient."
            )

        if len(falsified_by.strip()) < 15:
            raise InvestigationRefused(
                "Say what evidence would show this diagnosis to be wrong. A conclusion "
                "that nothing could falsify is not a finding."
            )

        # Correlation alone never supports a strong claim.
        supports = {claim.support for claim in claims}
        if (
            supports <= {SupportKind.CORRELATION, SupportKind.ABSENCE}
            and confidence is Confidence.HIGH
        ):
            raise InvestigationRefused(
                "This rests only on things moving at the same time, or on a signal "
                "being absent. That is a hypothesis. State it at medium confidence "
                "at most, and say what direct evidence would confirm it."
            )

        if kind is ProblemKind.UNDETERMINED:
            raise InvestigationRefused(
                "The kind of problem was not determined. Return Insufficient rather "
                "than a diagnosis that does not say whether this is the model, the "
                "code, or the provider."
            )

        return Diagnosis(
            question=question,
            kind=kind,
            summary=summary,
            claims=claims,
            alternatives=alternatives,
            falsified_by=falsified_by,
            confidence=confidence,
            environment=environment,
            recommendation=recommendation,
            operator_command=operator_command,
        )

    def render(self) -> str:
        lines = [
            f"DIAGNOSIS ({self.confidence.value} confidence)",
            f"environment : {self.environment.value}",
            f"kind        : {self.kind.value}",
            f"question    : {self.question}",
            "",
            self.summary,
            "",
            "claims:",
            *[claim.render() for claim in self.claims],
            "",
            "alternatives considered:",
            *[
                f"  - {alt.explanation}\n      ruled out by: {alt.ruled_out_by}"
                for alt in self.alternatives
            ],
            "",
            f"this would be wrong if: {self.falsified_by}",
        ]
        if self.recommendation:
            lines += ["", f"recommended: {self.recommendation}"]
        if self.operator_command:
            lines += [
                "",
                "a person runs this - the tool does not:",
                f"    {self.operator_command}",
            ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "question": self.question,
            "kind": str(self.kind),
            "summary": self.summary,
            "confidence": str(self.confidence),
            "environment": self.environment.value,
            "claims": [
                {
                    "statement": claim.statement,
                    "support": str(claim.support),
                    "evidence": [item.cite() for item in claim.evidence],
                }
                for claim in self.claims
            ],
            "alternatives": [
                {"explanation": alt.explanation, "ruled_out_by": alt.ruled_out_by}
                for alt in self.alternatives
            ],
            "falsified_by": self.falsified_by,
            "recommendation": self.recommendation,
            "operator_command": self.operator_command,
            "at": self.at,
        }


Outcome = Diagnosis | Insufficient
