"""The order autonomous work must happen in, enforced by the harness.

    reproduce -> failing regression test -> fix -> focused tests -> adjacent tests
    -> diff -> independent review -> draft pull request

Asking a model to follow this order in a prompt does not work. It follows it most of the
time, and "most of the time" applied to "did you actually prove the bug existed" produces
a codebase full of fixes for problems nobody demonstrated. So the order is a state
machine here, and a stage that has not recorded its evidence cannot be left.

Two gates carry most of the weight:

**No fix without a reproducing test.** :meth:`Task.record_fix` refuses unless a test was
recorded failing first. Not "a test exists" — a test that *ran* and *failed*, with its
output kept. A test written after the fix proves only that the code does what it does.

**No completion on a red suite.** :meth:`Task.complete` refuses while any recorded run
has a failure, including runs the model would rather not mention. The harness reads the
run records, not the model's summary of them.

Adjacent tests are discovered by searching the repository for modules that import what
the diff touched. Asking the model which tests are adjacent returns a plausible list,
and a plausible list is exactly the wrong tool for finding the test nobody remembered.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from hm_oi.paths import repo_root


class Stage(StrEnum):
    """Where a task has got to. Order is defined by :data:`STAGE_ORDER`."""

    CREATED = "created"
    REPRODUCED = "reproduced"
    FAILING_TEST = "failing_test"
    FIXED = "fixed"
    FOCUSED_TESTS = "focused_tests"
    ADJACENT_TESTS = "adjacent_tests"
    REVIEWED = "reviewed"
    COMPLETE = "complete"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"


STAGE_ORDER: Final[tuple[Stage, ...]] = (
    Stage.CREATED,
    Stage.REPRODUCED,
    Stage.FAILING_TEST,
    Stage.FIXED,
    Stage.FOCUSED_TESTS,
    Stage.ADJACENT_TESTS,
    Stage.REVIEWED,
    Stage.COMPLETE,
)

_RANK: Final[dict[Stage, int]] = {stage: index for index, stage in enumerate(STAGE_ORDER)}


class WorkflowViolation(RuntimeError):
    """The harness refused to let a task skip a step."""


@dataclass(frozen=True, slots=True)
class SuiteRun:
    """One recorded execution of the test suite, kept whole.

    ``raw_tail`` holds the end of pytest's own output. It is what a person reads when
    they want to know whether a claimed pass was real, so it is stored rather than
    summarised.
    """

    command: str
    passed: int
    failed: int
    errors: int
    skipped: int
    exit_code: int
    raw_tail: str
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def green(self) -> bool:
        return self.exit_code == 0 and self.failed == 0 and self.errors == 0

    @property
    def red(self) -> bool:
        return not self.green

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "exit_code": self.exit_code,
            "green": self.green,
            "at": self.at,
        }


_COUNT = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")
_FAILED_LINE = re.compile(r"^FAILED\s+\S+", re.MULTILINE)
_ERROR_LINE = re.compile(r"^ERROR\s+\S+", re.MULTILINE)


def parse_pytest_output(command: str, exit_code: int, output: str) -> SuiteRun:
    """Turn pytest's console output into counts.

    Counts come from pytest's own summary line rather than from the exit code alone,
    because exit code 1 covers "one test failed" and "the whole run collapsed during
    collection", and those need different responses.
    """

    text = output or ""
    counts = {"passed": 0, "failed": 0, "error": 0, "errors": 0, "skipped": 0}
    for value, label in _COUNT.findall(text):
        if label in counts:
            counts[label] += int(value)

    # Fallback: count the per-test lines when there is no summary to read.
    #
    # This repository sets `-q` in pyproject's addopts, so a run that adds its own `-q`
    # becomes `-qq` and pytest stops printing the "4 failed, 4 passed" line entirely.
    # The run above no longer passes a second `-q`, but a caller with a different
    # configuration can still land here, and a red run silently parsed as
    # "0 failed" is the worst possible failure for this module: it reads as green.
    if counts["failed"] == 0 and counts["error"] + counts["errors"] == 0 and exit_code != 0:
        counts["failed"] = len(_FAILED_LINE.findall(text))
        counts["errors"] = len(_ERROR_LINE.findall(text))

    tail = "\n".join(text.strip().splitlines()[-40:])
    return SuiteRun(
        command=command,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["error"] + counts["errors"],
        skipped=counts["skipped"],
        exit_code=exit_code,
        raw_tail=tail,
    )


def run_pytest(
    selection: list[str], cwd: Path, *, python: str = ".venv/Scripts/python", timeout: int = 3600
) -> SuiteRun:
    """Run a pytest selection in a worktree and record the result.

    ``-p no:randomly`` matches how this repository runs its suites, so a failure here
    reproduces for a person typing the same command. ``--timeout`` is deliberately not
    passed: ``pytest-timeout`` is not installed and passing it turns every run into an
    argument error.

    ``-q`` is deliberately *not* passed either. This project already sets it in
    ``pyproject.toml``, and a second one makes ``-qq``, which stops pytest printing the
    "4 failed, 4 passed" line that :func:`parse_pytest_output` reads. A red run then
    parses as zero failures, which reads as green — the one mistake this module must
    never make.
    """

    command = [python, "-m", "pytest", *selection, "-p", "no:randomly"]
    result = subprocess.run(
        command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )
    return parse_pytest_output(
        " ".join(command), result.returncode, (result.stdout or "") + (result.stderr or "")
    )


#: Directories whose contents are tests. Used to decide whether a changed file *is* a
#: test rather than something that needs one.
TEST_ROOTS: Final[tuple[str, ...]] = ("tests/",)


def is_test_file(path: str) -> bool:
    """Whether a path is a test module. **The one owner of this question.**

    It was decided in three places before this existed — adjacent-test discovery, the
    reviewer's "does this diff contain a test" rule, and the discovery loop's own prefix
    check — and the three disagreed. Discovery keyed on the ``tests/`` prefix while the
    reviewer keyed on the prefix too, so a test written anywhere else was invisible to
    both: the harness reported "no adjacent tests" and the reviewer rejected the diff
    for having no test, while the test sat right there in the diff.

    A file is a test because it is named like one, wherever it lives. The ``tests/``
    prefix is kept as a second way in, for a helper module inside the suite that does
    not start with ``test_``.
    """

    posix = str(path or "").replace("\\", "/")
    if not posix.endswith(".py"):
        return False
    if posix.rsplit("/", 1)[-1].startswith("test_"):
        return True
    return any(posix.startswith(prefix) for prefix in TEST_ROOTS)


def _module_paths(changed_file: str) -> tuple[str, ...]:
    """The import spellings a test would use to reach this file.

    ``src/ai_market_monitor/engine/comparators.py`` is imported as
    ``ai_market_monitor.engine.comparators``, and also referenced as ``comparators`` in
    a ``from ... import`` line. Both spellings are searched, because a test that imports
    only the leaf name is still an adjacent test.
    """

    posix = changed_file.replace("\\", "/")
    if not posix.endswith(".py"):
        return ()
    stem = posix[:-3]
    for prefix in ("src/", ""):
        if stem.startswith(prefix) and prefix:
            stem = stem[len(prefix) :]
            break
    dotted = stem.replace("/", ".")
    leaf = dotted.rsplit(".", 1)[-1]
    return tuple({dotted, leaf} - {""})


def _reference_pattern(needle: str) -> re.Pattern[str]:
    """How a test file actually *uses* a module, rather than merely containing its name.

    A plain word-boundary search was the first attempt and it was too loose. Searching
    for ``comparators`` matched this harness's own test files, which mention the word
    only inside example data — so a change to ``engine/comparators.py`` would have
    dragged unrelated modules into the adjacent selection, made every run slower, and
    let a failure with nothing to do with the change block the task.

    A dotted path is specific enough on its own: nothing says
    ``ai_market_monitor.engine.comparators`` by accident. A bare leaf name is not, so it
    counts only where it reads as a reference — an import, or an attribute access.
    """

    escaped = re.escape(needle)
    if "." in needle:
        return re.compile(rf"\b{escaped}\b")
    return re.compile(
        # `import comparators` / `from ...comparators import x`, on one line.
        rf"(?:^[ \t]*(?:from|import)\s+[\w.]*\b{escaped}\b"
        # `from x import (\n    comparators,\n)` - bounded, so it cannot run away
        # across the whole file and match an unrelated word far below.
        rf"|import\s*\(\s*[^)]{{0,400}}\b{escaped}\b"
        # `comparators.something` - an attribute access, but NOT the `.py` of a file
        # name. Without that exclusion the string "engine/comparators.py", which
        # appears in ordinary test data, reads as a reference to the module.
        rf"|\b{escaped}\.(?!py\b)\w)",
        re.MULTILINE,
    )


def discover_adjacent_tests(
    changed_files: list[str], root: Path | None = None
) -> tuple[str, ...]:
    """Every test module that references something the diff touched.

    Found by searching the repository, not by asking. A changed test file counts as its
    own adjacent test, which is what stops a diff that only edits tests from reporting
    "no adjacent tests" and skipping verification entirely.

    "References" is stricter than "mentions" — see :func:`_reference_pattern`.
    """

    root = (root or repo_root()).resolve()
    tests_dir = root / "tests"
    if not tests_dir.exists():
        return ()

    wanted: set[str] = set()
    direct: set[str] = set()
    for changed in changed_files:
        posix = changed.replace("\\", "/")
        if is_test_file(posix):
            if (root / posix).exists():
                direct.add(posix)
            continue
        wanted.update(_module_paths(posix))

    if not wanted:
        return tuple(sorted(direct))

    found: set[str] = set(direct)
    patterns = [_reference_pattern(needle) for needle in sorted(wanted, key=len, reverse=True)]
    for path in tests_dir.rglob("test_*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in patterns:
            if pattern.search(text):
                found.add(path.relative_to(root).as_posix())
                break
    return tuple(sorted(found))


@dataclass
class Task:
    """One unit of autonomous work, and the evidence it has produced.

    Mutable on purpose: it accumulates evidence as the work happens, and the audit
    record is written from it at the end.
    """

    task_id: str
    description: str
    stage: Stage = Stage.CREATED
    reproduction: str = ""
    regression_test: str = ""
    failing_run: SuiteRun | None = None
    passing_run: SuiteRun | None = None
    focused_runs: list[SuiteRun] = field(default_factory=list)
    adjacent_runs: list[SuiteRun] = field(default_factory=list)
    adjacent_selection: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    diff: str = ""
    review_verdict: str = ""
    review_reasons: tuple[str, ...] = ()
    history: list[str] = field(default_factory=list)

    def _note(self, message: str) -> None:
        self.history.append(f"{datetime.now(UTC).isoformat()} {message}")

    def _require(self, minimum: Stage, action: str) -> None:
        if _RANK.get(self.stage, -1) < _RANK[minimum]:
            raise WorkflowViolation(
                f"Cannot {action}: this task is at '{self.stage.value}' and must reach "
                f"'{minimum.value}' first. The order exists so that a change is never "
                "made before the problem it fixes has been shown to be real."
            )

    def record_reproduction(self, evidence: str) -> None:
        """Say how the defect was observed. Free text, but it must say something."""

        if len(str(evidence or "").strip()) < 10:
            raise WorkflowViolation(
                "Reproduction evidence is required, in words. Say what was run and what "
                "it printed."
            )
        self.reproduction = str(evidence).strip()
        self.stage = Stage.REPRODUCED
        self._note("reproduced")

    def record_failing_test(self, test_id: str, run: SuiteRun) -> None:
        """Record the regression test, proven failing *before* any fix.

        A run that passes is refused. A test that passes before the fix does not
        reproduce the defect, whatever it is named.
        """

        self._require(Stage.REPRODUCED, "record a regression test")
        if not str(test_id or "").strip():
            raise WorkflowViolation("The regression test needs an identifier.")
        if run.green:
            raise WorkflowViolation(
                f"The regression test '{test_id}' passed before the fix was made. A test "
                "that already passes does not demonstrate the defect. Write one that "
                "fails for the reason being fixed."
            )
        self.regression_test = str(test_id).strip()
        self.failing_run = run
        self.stage = Stage.FAILING_TEST
        self._note(f"failing test recorded: {test_id}")

    def record_fix(self, changed_files: list[str], diff: str) -> None:
        """Record the change. Refused unless a failing test came first."""

        if self.failing_run is None:
            raise WorkflowViolation(
                "REJECTED: a fix was submitted with no reproducing test.\n"
                "The harness requires a regression test that ran and failed before the "
                "change. Without one there is no evidence the defect existed and nothing "
                "to stop it coming back."
            )
        self._require(Stage.FAILING_TEST, "record a fix")
        if not changed_files:
            raise WorkflowViolation("A fix that changes no file is not a fix.")
        self.changed_files = tuple(sorted(changed_files))
        self.diff = str(diff or "")
        self.stage = Stage.FIXED
        self._note(f"fix recorded across {len(self.changed_files)} file(s)")

    def record_focused_tests(self, run: SuiteRun) -> None:
        """The regression test, re-run after the fix. It must now pass."""

        self._require(Stage.FIXED, "record focused tests")
        if run.red:
            self.focused_runs.append(run)
            raise WorkflowViolation(
                f"The focused tests are still failing after the fix "
                f"({run.failed} failed, {run.errors} errors). The task stays open."
            )
        self.passing_run = run
        self.focused_runs.append(run)
        self.stage = Stage.FOCUSED_TESTS
        self._note("focused tests green")

    def record_adjacent_tests(self, selection: tuple[str, ...], run: SuiteRun) -> None:
        """The tests covering everything the diff touched."""

        self._require(Stage.FOCUSED_TESTS, "record adjacent tests")
        self.adjacent_selection = selection
        self.adjacent_runs.append(run)
        if run.red:
            raise WorkflowViolation(
                f"Adjacent tests failed ({run.failed} failed, {run.errors} errors). The "
                "fix broke something next to it. The task stays open."
            )
        self.stage = Stage.ADJACENT_TESTS
        self._note(f"adjacent tests green across {len(selection)} module(s)")

    def record_review(self, verdict: str, reasons: tuple[str, ...]) -> None:
        self._require(Stage.ADJACENT_TESTS, "record a review")
        self.review_verdict = str(verdict or "").strip().casefold()
        self.review_reasons = reasons
        if self.review_verdict != "approved":
            self.stage = Stage.FIXED  # back to the implementer, evidence retained
            self._note(f"review rejected: {'; '.join(reasons) or 'no reason given'}")
            raise WorkflowViolation(
                "REJECTED by the independent reviewer:\n"
                + "\n".join(f"  - {reason}" for reason in reasons)
            )
        self.stage = Stage.REVIEWED
        self._note("review approved")

    def all_runs(self) -> tuple[SuiteRun, ...]:
        runs = [*self.focused_runs, *self.adjacent_runs]
        if self.failing_run is not None:
            runs.append(self.failing_run)
        return tuple(runs)

    def red_runs(self) -> tuple[SuiteRun, ...]:
        """Failing runs that are not the deliberate pre-fix one."""

        return tuple(
            run
            for run in (*self.focused_runs, *self.adjacent_runs)
            if run.red
        )

    def complete(self) -> None:
        """Mark the task done. Refused on any red suite, or a missing proof."""

        self._require(Stage.REVIEWED, "complete the task")
        if self.failing_run is None or self.passing_run is None:
            raise WorkflowViolation(
                "Cannot complete: the before-and-after proof is incomplete. A task needs "
                "the regression test recorded failing, then the same test recorded "
                "passing."
            )
        red = self.red_runs()
        if red:
            raise WorkflowViolation(
                "Cannot complete on a red suite. Still failing:\n"
                + "\n".join(f"  - {run.command} ({run.failed} failed)" for run in red)
            )
        self.stage = Stage.COMPLETE
        self._note("complete")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "stage": str(self.stage),
            "regression_test": self.regression_test,
            "reproduction": self.reproduction,
            "changed_files": list(self.changed_files),
            "adjacent_selection": list(self.adjacent_selection),
            "failing_run": self.failing_run.to_dict() if self.failing_run else None,
            "passing_run": self.passing_run.to_dict() if self.passing_run else None,
            "focused_runs": [run.to_dict() for run in self.focused_runs],
            "adjacent_runs": [run.to_dict() for run in self.adjacent_runs],
            "review_verdict": self.review_verdict,
            "review_reasons": list(self.review_reasons),
            "history": list(self.history),
        }
