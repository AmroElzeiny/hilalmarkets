"""A second opinion that never sees how the first one was reached.

The reviewer is given the task description, the diff, and the test runs. It is *not*
given the implementer's reasoning, and that is the whole design. An implementer that has
just spent twenty minutes convincing itself a change is correct writes a very persuasive
explanation, and a reviewer who reads it is reviewing the explanation.

Every rule below runs deterministically on the diff text before any model is asked
anything. That ordering matters: the four rejections this phase must demonstrate — no
reproducing test, weakened assertion, skipped test, scope creep — are all decidable by
reading the diff, and a rule that does not need a model cannot be talked out of its
answer. The model tier is used afterwards, for judgement the rules cannot make.

The reviewer never approves silently and never rejects silently. Every verdict carries
its reasons, and a rejection sends the task back to the implementer with them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from hm_oi.redaction import find_secrets
from hm_oi.workflow import Task, is_test_file


class Verdict(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """One reason a diff was refused."""

    rule_id: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.rule_id}] {self.detail}"


@dataclass(frozen=True, slots=True)
class Review:
    verdict: Verdict
    findings: tuple[ReviewFinding, ...]

    @property
    def approved(self) -> bool:
        return self.verdict is Verdict.APPROVED

    def reasons(self) -> tuple[str, ...]:
        return tuple(str(finding) for finding in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": str(self.verdict),
            "findings": [
                {"rule_id": f.rule_id, "detail": f.detail} for f in self.findings
            ],
        }


#: Files whose meaning is governed elsewhere. A change here is never routine, whatever
#: the task was about, because these decide Sharia status, who may approve, what a
#: capability means, and who owns a strategy.
GOVERNED_PATHS: Final[tuple[str, ...]] = (
    "services/sharia",
    "services/sharia_governance",
    "services/sharia_passports",
    "sharia_universe",
    "engine/grounded_patch",
    "engine/comparators",
    "engine/capability",
    "services/entitlements",
    "services/billing",
    "core/plans",
    "approval",
    "activation",
)

#: An assertion being loosened rather than met. Each is a real way a suite is made green
#: without the code being made right.
_WEAKENED_ASSERTION: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "assert_deleted",
        re.compile(r"^-\s*assert\s+", re.MULTILINE),
    ),
    (
        "assert_true_stub",
        re.compile(r"^\+\s*assert\s+(?:True|1)\s*(?:#.*)?$", re.MULTILINE),
    ),
    (
        "equality_to_membership",
        re.compile(r"^\+\s*assert\s+.*\b(?:in|>=|<=|any\(|approx)\b.*$", re.MULTILINE),
    ),
    (
        "exception_swallowed",
        re.compile(
            r"^\+\s*except\s+\w*(?:Exception|BaseException)?\s*(?:as\s+\w+)?\s*:"
            r"\s*(?:#.*)?$\s*^\+\s*pass",
            re.MULTILINE,
        ),
    ),
)

#: Turning a test off. Distinct from weakening one, and reported separately, because the
#: honest fix is different: a skipped test usually means the change was abandoned midway.
_DISABLED_TEST: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("test_skipped", re.compile(r"^\+\s*@pytest\.mark\.skip", re.MULTILINE)),
    ("test_xfailed", re.compile(r"^\+\s*@pytest\.mark\.xfail", re.MULTILINE)),
    ("test_skipif_added", re.compile(r"^\+\s*@pytest\.mark\.skipif", re.MULTILINE)),
    ("skip_call_added", re.compile(r"^\+\s*pytest\.skip\(", re.MULTILINE)),
    ("test_renamed_out", re.compile(r"^-\s*def\s+test_\w+", re.MULTILINE)),
)


def _changed_paths(diff: str) -> tuple[str, ...]:
    """Files named in a unified diff header."""

    return tuple(
        sorted({match.group(1) for match in re.finditer(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE)})
    )


def review_diff(
    task: Task,
    *,
    allowed_paths: tuple[str, ...] = (),
) -> Review:
    """Judge a diff on its own merits.

    ``allowed_paths`` is the scope the task was given. Anything outside it is scope
    creep, which is refused even when the extra change is an improvement — an unrelated
    improvement belongs to its own task, with its own test and its own review.
    """

    findings: list[ReviewFinding] = []
    diff = task.diff or ""
    paths = _changed_paths(diff) or task.changed_files

    # 1. The gate that cannot be argued with.
    if task.failing_run is None:
        findings.append(
            ReviewFinding(
                "no_reproducing_test",
                "There is no regression test recorded failing before the fix. A change "
                "without one proves nothing and protects nothing.",
            )
        )
    elif not task.regression_test:
        findings.append(
            ReviewFinding("no_test_named", "The regression test was not identified by name.")
        )

    # 2. A fix that touches no test at all.
    if paths and not any(is_test_file(path) for path in paths):
        findings.append(
            ReviewFinding(
                "no_test_in_diff",
                "The diff changes code but adds no test. Every fix carries the test that "
                "would have caught it.",
            )
        )

    # 3. Green by subtraction rather than by correction.
    for rule_id, pattern in _WEAKENED_ASSERTION:
        if pattern.search(diff):
            # An equality relaxed to membership is only suspicious on an existing test.
            if rule_id == "equality_to_membership" and not re.search(
                r"^-\s*assert\s+.*==", diff, re.MULTILINE
            ):
                continue
            findings.append(
                ReviewFinding(
                    "weakened_assertion",
                    f"An assertion appears to have been loosened or removed ({rule_id}). "
                    "If the assertion was wrong, say why and change it deliberately; do "
                    "not widen it until the suite goes green.",
                )
            )
            break

    for rule_id, pattern in _DISABLED_TEST:
        if pattern.search(diff):
            findings.append(
                ReviewFinding(
                    "disabled_test",
                    f"A test was skipped, marked expected-to-fail, or deleted ({rule_id}). "
                    "Reaching green by switching a test off is not reaching green.",
                )
            )
            break

    # 4. Scope.
    if allowed_paths:
        outside = [
            path
            for path in paths
            if not any(path.replace("\\", "/").startswith(prefix) for prefix in allowed_paths)
        ]
        if outside:
            findings.append(
                ReviewFinding(
                    "scope_creep",
                    "These files are outside the task's scope: "
                    + ", ".join(sorted(outside)[:8])
                    + ". Unrelated changes need their own task and their own test.",
                )
            )

    # 5. Authority that is not the implementer's to change.
    governed = [
        path
        for path in paths
        if any(marker in path.replace("\\", "/").casefold() for marker in GOVERNED_PATHS)
    ]
    if governed:
        findings.append(
            ReviewFinding(
                "governed_authority",
                "This diff touches Sharia, approval, capability, ownership or billing "
                "authority: "
                + ", ".join(sorted(governed)[:6])
                + ". These are governed decisions and a person must make them.",
            )
        )

    # 6. Secrets.
    added = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    kinds = find_secrets(added)
    if kinds:
        findings.append(
            ReviewFinding(
                "secret_in_diff",
                f"The added lines contain something secret-shaped ({', '.join(kinds)}). "
                "Nothing resembling a credential goes into the repository.",
            )
        )

    # 7. A red suite can never be approved.
    red = task.red_runs()
    if red:
        findings.append(
            ReviewFinding(
                "red_suite",
                "Tests are still failing: "
                + "; ".join(f"{run.command} ({run.failed} failed)" for run in red[:3]),
            )
        )

    return Review(
        verdict=Verdict.REJECTED if findings else Verdict.APPROVED,
        findings=tuple(findings),
    )


def reviewer_instructions() -> str:
    """The system message for the reviewing agent.

    Deliberately short and free of any account of how the change was produced. The
    reviewer is told what to look for and what it may not see.
    """

    return (
        "You are reviewing a change to the HilalMarkets repository. You did not write it "
        "and you will not be told how it was written.\n\n"
        "You see only: the task, the diff, and the recorded test runs.\n\n"
        "Reject the change if any of these is true:\n"
        "- there is no regression test that failed before the fix and passes after it\n"
        "- an existing assertion was loosened, deleted, or replaced with a weaker one\n"
        "- a test was skipped, marked expected-to-fail, or removed\n"
        "- files unrelated to the task were changed\n"
        "- it changes Sharia status, approval, activation, capability meaning, ownership "
        "or billing authority\n"
        "- anything resembling a credential appears in the added lines\n"
        "- any recorded test run is still failing\n\n"
        "Approving a change you are unsure about is worse than rejecting one that turns "
        "out fine. A rejection costs one more round; a wrong approval reaches the "
        "repository.\n\n"
        "Answer with APPROVED or REJECTED, then one line per reason."
    )
