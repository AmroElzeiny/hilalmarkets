"""What counts as a finding, and the three things a finding can turn out to be.

An adversarial harness that reports everything it notices is worse than no harness. The
reader stops believing it, and the one real defect sits in a list of ninety items nobody
finished reading. Three rules do the filtering here, and all three are enforced in code
rather than asked for in a prompt:

**A finding without a reproduction command is not a finding.** :class:`Finding` refuses
to be constructed without one. Same for the confidence and for the falsifying evidence —
"what would show me I am wrong" is the question that separates an observation from an
accusation, and a field that is optional is a field that is empty.

**A failure that was already failing is not news.** The browser suite's failing set is
captured before any attack runs. Anything matching it is reported as ``BASELINE``, in its
own section, and never as something this phase found. :class:`BaselineSet` holds that
capture; it is loaded from a file written by two separate runs, so a flaky test that
failed once cannot become a permanent excuse.

**A disagreement about what the product should do is not a defect.** Two of those are
known and named in :data:`OPEN_PRODUCT_DECISIONS`. Anything touching them is reported as
``BLOCKED_ON_PRODUCT_DECISION`` — the harness surfaces the question and does not answer
it, because both answers are legitimate and neither is an engineer's to pick.

Deduplication is by ``dedupe_key``, which is deliberately *not* the whole finding: the
same boundary crossed on four pages is one problem with four examples, and listing it
four times makes it look like four.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from hm_oi.qa_attacks import SEVERITY_ORDER, FailureClass, Severity
from hm_oi.redaction import find_secrets, redact_structure

__all__ = [
    "BaselineSet",
    "Confidence",
    "Finding",
    "FindingStatus",
    "IncompleteFinding",
    "OPEN_PRODUCT_DECISIONS",
    "OpenProductDecision",
    "PromotionRefused",
    "RegressionCandidate",
    "classify",
    "dedupe",
    "rank",
    "split_by_status",
]


class FindingStatus(StrEnum):
    """Whether this phase found it, inherited it, or cannot decide it."""

    NEW = "new"
    BASELINE = "baseline"
    BLOCKED_ON_PRODUCT_DECISION = "blocked_on_product_decision"


class Confidence(StrEnum):
    """How sure the harness is, stated so a reader can weigh it.

    ``OBSERVED`` means the harness saw the wrong behaviour happen. ``INFERRED`` means it
    read code or copy and reasoned. The distinction matters because an inferred finding
    can be wrong about whether the path is reachable at all.
    """

    OBSERVED = "observed"
    INFERRED = "inferred"


class IncompleteFinding(ValueError):
    """A finding is missing something that makes it checkable."""


class PromotionRefused(PermissionError):
    """Something tried to move a regression candidate into the authoritative suite."""


@dataclass(frozen=True, slots=True)
class OpenProductDecision:
    """A question the product has not answered, and how to recognise it.

    ``patterns`` are matched against a finding's title, summary and dedupe key. They are
    written broadly on purpose: over-matching here turns a real defect into a blocked
    question, which is visible and recoverable, while under-matching produces a
    confident finding about something nobody has decided, which is the noise this whole
    mechanism exists to stop.
    """

    key: str
    question: str
    #: What is true at HEAD, so the report does not repeat a stale description.
    state_at_head: str
    #: Who decides. Never an engineer.
    owner: str
    patterns: tuple[str, ...]

    def matches(self, text: str) -> bool:
        haystack = str(text or "").casefold()
        return any(re.search(pattern, haystack, re.IGNORECASE) for pattern in self.patterns)


#: The two decisions the phase brief names. Both were re-checked against HEAD, because a
#: harness that reports a stale description of an open question is telling the reader
#: something false about their own product.
OPEN_PRODUCT_DECISIONS: Final[tuple[OpenProductDecision, ...]] = (
    OpenProductDecision(
        key="lifecycles_url_name",
        question=(
            "What should the page listing a customer's setups and their alerts be "
            "called in the address bar - activity, lifecycles, or opportunities?"
        ),
        state_at_head=(
            "The three-handler problem is gone. At 211aecc5 there is one handler at "
            "/dashboard/opportunities (api/routers/dashboard.py:2589) and the other two "
            "addresses are 308 permanent redirects to it "
            "(api/routers/dashboard.py:2616 and :2621). What is still open is the "
            "customer-facing name: the code calls the constant LIFECYCLES_PATH, the URL "
            "says opportunities, and the template is activity.html. Three words for one "
            "page is a naming decision, not a bug."
        ),
        owner="Product",
        patterns=(
            r"/dashboard/(?:activity|lifecycles|opportunities)",
            r"\blifecycles?\b",
            r"\bopportunit",
            r"canonical url",
            r"url alias",
        ),
    ),
    OpenProductDecision(
        key="landing_layout_reference",
        question=(
            "Is the landing page wrong, or is the layout reference it is compared "
            "against out of date?"
        ),
        state_at_head=(
            "Commit 610cb4ad lined the hero up with the rest of the page. The reference "
            "screenshots this was measured against cannot be regenerated - that is "
            "blocked upstream and is a stated non-goal of this phase - so any remaining "
            "pixel difference cannot be attributed to either side. Nothing here reports "
            "a landing layout defect."
        ),
        owner="Product / Design",
        patterns=(
            r"landing.{0,24}layout",
            r"layout.{0,24}reference",
            r"hero.{0,16}(?:offset|position|align)",
            r"\b374\s*px\b",
            r"reference screenshot",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class RegressionCandidate:
    """A test this phase suggests. Never a test this phase adds.

    ``promoted`` exists so the state is visible in the report rather than implied by its
    absence, and it is never settable to ``True``: :meth:`promote` refuses. Promotion is
    a person reading the candidate and deciding, and it is recorded as their decision.
    """

    candidate_id: str
    title: str
    #: Where it would go if a person decided to take it.
    suggested_path: str
    #: What it would assert, in one sentence.
    asserts: str
    #: The test body, or a sketch of it. Kept so a person can read it before deciding.
    sketch: str
    promoted: bool = False

    def promote(self) -> None:
        raise PromotionRefused(
            f"Refusing to promote {self.candidate_id!r} into the authoritative suite. "
            "A regression candidate becomes a real test when a person reads it and "
            "decides, and that decision is recorded with their name on it. This harness "
            "proposes; it never promotes."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "suggested_path": self.suggested_path,
            "asserts": self.asserts,
            "sketch": self.sketch,
            "promoted": self.promoted,
        }


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem, stated so somebody else can check it.

    Every field below is required because every one of them has been the missing piece
    in a report somebody could not act on.
    """

    finding_id: str
    title: str
    severity: Severity
    failure_class: FailureClass
    confidence: Confidence
    #: One sentence: what is wrong.
    summary: str
    #: What was seen. For an inferred finding, what was read, with file and line.
    evidence: str
    #: What would prove this finding wrong. Required - see the module note.
    falsifying_evidence: str
    #: The exact command that shows it. Required.
    reproduction: str
    #: Which target it was seen on, in words: "isolated_test (APP_ENV=test)".
    environment_label: str
    #: What collapses duplicates. Same key, same problem.
    dedupe_key: str
    status: FindingStatus = FindingStatus.NEW
    #: Set when the status is BLOCKED_ON_PRODUCT_DECISION.
    blocked_on: str | None = None
    #: Set when the status is BASELINE.
    baseline_match: str | None = None
    regression_candidate: RegressionCandidate | None = None
    #: Paths to screenshots, traces and saved payloads. Relative to the repository.
    artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        missing = [
            name
            for name in (
                "finding_id",
                "title",
                "summary",
                "evidence",
                "falsifying_evidence",
                "reproduction",
                "environment_label",
                "dedupe_key",
            )
            if not str(getattr(self, name) or "").strip()
        ]
        if missing:
            raise IncompleteFinding(
                "This is not a finding yet - it is missing "
                + ", ".join(missing)
                + ". A finding without a reproduction command cannot be checked by "
                "anyone else, and one without falsifying evidence cannot be disproved."
            )
        leaked = find_secrets(f"{self.summary}\n{self.evidence}\n{self.reproduction}")
        if leaked:
            raise IncompleteFinding(
                f"Refusing this finding: it appears to contain {', '.join(leaked)}. "
                "Nothing was recorded. Remove the secret from the evidence and record "
                "it again."
            )

    @property
    def is_new(self) -> bool:
        return self.status is FindingStatus.NEW

    def to_dict(self) -> dict[str, Any]:
        """The report shape. Redacted again on the way out.

        Belt and braces: ``__post_init__`` already refuses a finding carrying a secret,
        and this runs anyway, because the cost of the second pass is nothing and the
        cost of being wrong once is a key in a committed report.
        """

        payload = {
            "finding_id": self.finding_id,
            "title": self.title,
            "severity": str(self.severity),
            "failure_class": str(self.failure_class),
            "confidence": str(self.confidence),
            "status": str(self.status),
            "summary": self.summary,
            "evidence": self.evidence,
            "falsifying_evidence": self.falsifying_evidence,
            "reproduction": self.reproduction,
            "environment_label": self.environment_label,
            "dedupe_key": self.dedupe_key,
            "blocked_on": self.blocked_on,
            "baseline_match": self.baseline_match,
            "artifacts": list(self.artifacts),
            "regression_candidate": (
                self.regression_candidate.to_dict() if self.regression_candidate else None
            ),
        }
        return dict(redact_structure(payload, limit=4000))


@dataclass(frozen=True, slots=True)
class BaselineSet:
    """The tests that were already failing before this phase touched anything.

    Two captures, not one. A test that failed in only one of them is *flaky*, which is a
    third thing: not a stable baseline the harness may excuse, and not a defect this
    phase caused. Flaky tests are listed separately and excused, because attributing one
    to an attack would be wrong just as often as it was right.
    """

    #: Failing in both captures.
    stable: frozenset[str] = frozenset()
    #: Failing in one capture only.
    flaky: frozenset[str] = frozenset()
    captured_at_sha: str = ""
    captures: int = 0

    @property
    def is_stable(self) -> bool:
        """Whether two captures agreed. A phase run on an unstable baseline is invalid."""

        return self.captures >= 2 and not self.flaky

    @property
    def excused(self) -> frozenset[str]:
        """Everything the harness will not report as new."""

        return self.stable | self.flaky

    def matches(self, text: str) -> str | None:
        """The baseline entry this text names, if any.

        Matching is on the test id as a substring, in both directions, because a finding
        may quote ``test_x`` while the baseline holds
        ``tests/browser/test_y.py::test_x``.
        """

        haystack = str(text or "")
        for entry in sorted(self.excused, key=len, reverse=True):
            if entry in haystack:
                return entry
            node = entry.rsplit("::", 1)[-1]
            if node and node in haystack:
                return entry
        return None

    @classmethod
    def from_captures(
        cls, first: set[str], second: set[str], *, sha: str
    ) -> BaselineSet:
        return cls(
            stable=frozenset(first & second),
            flaky=frozenset(first ^ second),
            captured_at_sha=sha,
            captures=2,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "captured_at_sha": self.captured_at_sha,
            "captures": self.captures,
            "is_stable": self.is_stable,
            "stable": sorted(self.stable),
            "flaky": sorted(self.flaky),
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> BaselineSet:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            stable=frozenset(payload.get("stable", ())),
            flaky=frozenset(payload.get("flaky", ())),
            captured_at_sha=str(payload.get("captured_at_sha") or ""),
            captures=int(payload.get("captures") or 0),
        )


def classify(finding: Finding, baseline: BaselineSet) -> Finding:
    """Decide whether this is new, inherited, or somebody's decision to make.

    Order matters. The product decision is checked *first*: a finding about the
    lifecycles URL that also happens to name a baseline test is still a question nobody
    has answered, and calling it a baseline failure would file it under "already known
    and being lived with", which is not the same thing at all.
    """

    haystack = f"{finding.title}\n{finding.summary}\n{finding.dedupe_key}\n{finding.evidence}"

    for decision in OPEN_PRODUCT_DECISIONS:
        if decision.matches(haystack):
            return _replace(
                finding,
                status=FindingStatus.BLOCKED_ON_PRODUCT_DECISION,
                blocked_on=f"{decision.key}: {decision.question}",
                baseline_match=None,
            )

    match = baseline.matches(haystack)
    if match is not None:
        return _replace(
            finding,
            status=FindingStatus.BASELINE,
            baseline_match=match,
            blocked_on=None,
        )

    return _replace(finding, status=FindingStatus.NEW, blocked_on=None, baseline_match=None)


def _replace(finding: Finding, **changes: Any) -> Finding:
    """``dataclasses.replace`` for a slotted frozen dataclass, spelled out.

    Written by hand rather than imported because ``replace`` re-runs ``__post_init__``,
    which is what we want, and doing it explicitly keeps the required-field checking on
    the one path.
    """

    values: dict[str, Any] = {
        "finding_id": finding.finding_id,
        "title": finding.title,
        "severity": finding.severity,
        "failure_class": finding.failure_class,
        "confidence": finding.confidence,
        "summary": finding.summary,
        "evidence": finding.evidence,
        "falsifying_evidence": finding.falsifying_evidence,
        "reproduction": finding.reproduction,
        "environment_label": finding.environment_label,
        "dedupe_key": finding.dedupe_key,
        "status": finding.status,
        "blocked_on": finding.blocked_on,
        "baseline_match": finding.baseline_match,
        "regression_candidate": finding.regression_candidate,
        "artifacts": finding.artifacts,
    }
    values.update(changes)
    return Finding(**values)


def dedupe(findings: list[Finding]) -> list[Finding]:
    """One entry per ``dedupe_key``, keeping the worst and merging the artifacts.

    The worst is kept rather than the first because severity is what decides reading
    order, and the first one seen is an accident of iteration order.
    """

    best: dict[str, Finding] = {}
    extra_artifacts: dict[str, list[str]] = {}
    for item in findings:
        key = item.dedupe_key
        extra_artifacts.setdefault(key, []).extend(item.artifacts)
        current = best.get(key)
        if current is None or SEVERITY_ORDER[item.severity] < SEVERITY_ORDER[current.severity]:
            best[key] = item

    merged: list[Finding] = []
    for key, item in best.items():
        artifacts = tuple(dict.fromkeys(extra_artifacts[key]))
        merged.append(_replace(item, artifacts=artifacts) if artifacts != item.artifacts else item)
    return merged


def rank(findings: list[Finding]) -> list[Finding]:
    """Worst first, then by class, then by id so two runs order the same way."""

    return sorted(
        findings,
        key=lambda item: (
            SEVERITY_ORDER[item.severity],
            str(item.failure_class),
            item.finding_id,
        ),
    )


@dataclass(frozen=True, slots=True)
class SplitFindings:
    """The three lists a report is made of."""

    new: list[Finding] = field(default_factory=list)
    baseline: list[Finding] = field(default_factory=list)
    blocked: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "new": [item.to_dict() for item in self.new],
            "baseline": [item.to_dict() for item in self.baseline],
            "blocked_on_product_decision": [item.to_dict() for item in self.blocked],
        }


def split_by_status(findings: list[Finding]) -> SplitFindings:
    """Separate a ranked list into the three sections the report requires."""

    return SplitFindings(
        new=[item for item in findings if item.status is FindingStatus.NEW],
        baseline=[item for item in findings if item.status is FindingStatus.BASELINE],
        blocked=[
            item
            for item in findings
            if item.status is FindingStatus.BLOCKED_ON_PRODUCT_DECISION
        ],
    )
