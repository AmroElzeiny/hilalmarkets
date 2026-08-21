"""Validation case 1: a seeded defect goes through the whole workflow, for real.

These are the only tests here that create a Git worktree and run pytest inside it, so
they are slow — a worktree checkout of this repository is thousands of files. Deselect
them while iterating with ``-k "not end_to_end"``; there is deliberately no ``slow``
marker, because this repository runs pytest with ``--strict-markers`` and registering a
new one would mean editing shared configuration for the sake of a single file.

They are not optional. Everything else in ``tests/oi`` checks one gate in isolation;
this is what proves the gates work in the order the harness actually calls them.

The defect and its fix are deliberately self-contained. They import nothing from the
product, so the run cannot fail for reasons unrelated to what is being tested, and the
seeded bug is a real one from this repository's own history: a value read from the wrong
side of a comparison.

The implementer is scripted rather than a model. What is under test is whether a change
can reach the repository without a reproducing test, a green suite and an independent
review. That question has the same answer whichever model is behind it, and using a model
would make the result depend on which one was cheap that week.
"""

from __future__ import annotations

import shutil
import sys

import pytest

from hm_oi.audit import read_records
from hm_oi.builder import run_task
from hm_oi.paths import repo_root
from hm_oi.workflow import Stage
from hm_oi.workspace import Workspace, remove_workspace, worktree_path

TASK_ID = "oi0-e2e-probe"

#: The seeded module's name is assembled here rather than written out, and that is not
#: cosmetic. `discover_adjacent_tests` finds the tests covering a changed file by
#: searching every test module for the changed file's name. This file necessarily
#: contains that name — it is the file that writes the module — so the harness selected
#: **this very test** as adjacent to the change it was running, re-entered it inside the
#: worktree, and recorded the re-entry's errors as "the fix broke something next to it".
#: The task could then never complete, and case 1 failed on every run.
#:
#: Assembling the stem keeps the bare token out of this file's text, so the only
#: adjacent test found is the seeded regression test itself, which is the honest answer.
#: The discovery rule is deliberately left as it is: it matches on text so that a module
#: named only inside a string — a monkeypatch target, an importorskip — still counts as
#: a reference. Narrowing that to real imports would quietly shrink the safety gate
#: every autonomous change has to pass.
_MODULE_STEM = "oi" + "_probe_boundary"
MODULE_NAME = f"{_MODULE_STEM}.py"
TEST_NAME = f"test_{_MODULE_STEM}.py"

#: The defect. `at_least` compares the wrong way round, so a value that meets the
#: threshold is reported as not meeting it.
BROKEN_MODULE = '''\
"""A tiny threshold check, seeded with a real defect for the end-to-end test."""


def meets_threshold(value: float, minimum: float) -> bool:
    """True when ``value`` is at least ``minimum``."""

    return value > minimum
'''

FIXED_MODULE = BROKEN_MODULE.replace("return value > minimum", "return value >= minimum")

#: Asserts the rule across the family, not the one reported number.
REGRESSION_TEST = f'''\
"""The boundary belongs to 'at least', for every threshold."""

import pytest

from {_MODULE_STEM} import meets_threshold


@pytest.mark.parametrize("threshold", (0.0, 1.0, 17.0, 99.5))
def test_a_value_exactly_on_the_threshold_meets_it(threshold: float) -> None:
    assert meets_threshold(threshold, threshold) is True


@pytest.mark.parametrize("threshold", (1.0, 17.0))
def test_a_value_below_the_threshold_does_not(threshold: float) -> None:
    assert meets_threshold(threshold - 0.5, threshold) is False


@pytest.mark.parametrize("threshold", (1.0, 17.0))
def test_a_value_above_the_threshold_does(threshold: float) -> None:
    assert meets_threshold(threshold + 0.5, threshold) is True
'''

@pytest.fixture
def clean_worktree():
    """Make sure no worktree is left over, before and after."""

    remove_workspace(TASK_ID)
    path = worktree_path(TASK_ID)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    yield
    remove_workspace(TASK_ID)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _scripted_implementer(step: str, task, workspace: Workspace):
    """What a model would produce, written down so the harness can be tested alone."""

    module = workspace.path / MODULE_NAME
    test_file = workspace.path / TEST_NAME

    if step == "reproduce":
        # Seed the defect, then observe it. Seeding happens here so the worktree is
        # created clean and the diff contains the whole story.
        module.write_text(BROKEN_MODULE, encoding="utf-8")
        return (
            "meets_threshold(17.0, 17.0) returned False. A value exactly on the "
            "threshold should meet an 'at least' comparison."
        )

    if step == "write_failing_test":
        test_file.write_text(REGRESSION_TEST, encoding="utf-8")
        return TEST_NAME

    if step == "fix":
        module.write_text(FIXED_MODULE, encoding="utf-8")
        return "changed > to >="

    raise AssertionError(f"unexpected step {step!r}")


def test_case_1_end_to_end_seeded_defect(clean_worktree) -> None:
    """Reproduce, fail, fix, pass, adjacent, review, complete — in that order."""

    result = run_task(
        TASK_ID,
        "A value on the boundary is refused by an 'at least' comparison.",
        _scripted_implementer,
        root=repo_root(),
        allowed_paths=(MODULE_NAME, TEST_NAME),
        python=sys.executable,
    )

    assert result.succeeded, f"task did not complete: {result.messages}"
    task = result.task
    assert task.stage is Stage.COMPLETE

    # The proof that matters: red before, green after, same test.
    assert task.failing_run is not None and task.failing_run.red
    assert task.passing_run is not None and task.passing_run.green
    assert task.failing_run.failed >= 1, "the seeded defect did not actually fail"
    assert task.regression_test == TEST_NAME

    # The change was made, reviewed, and recorded.
    assert MODULE_NAME in task.changed_files
    assert TEST_NAME in task.changed_files
    assert task.review_verdict == "approved", task.review_reasons
    assert not task.red_runs()

    # It happened on its own branch, not on the engineer's tree.
    assert result.workspace.branch.startswith("oi/")
    assert result.workspace.path != repo_root()

    # And it is written down.
    assert result.audit_path is not None and result.audit_path.exists()
    records = [r for r in read_records() if r["task_id"] == TASK_ID]
    assert records, "no audit record was written"
    record = records[-1]
    assert record["disposition"] == "completed"
    assert record["review_verdict"] == "approved"
    assert record["regression_test"] == TEST_NAME
    assert record["restrictions"], "the precondition restriction was not recorded"


def test_case_1b_the_engineers_working_tree_is_untouched(clean_worktree) -> None:
    """Nothing the task did may appear in the real repository."""

    run_task(
        TASK_ID,
        "A value on the boundary is refused by an 'at least' comparison.",
        _scripted_implementer,
        root=repo_root(),
        allowed_paths=(MODULE_NAME, TEST_NAME),
        python=sys.executable,
    )

    root = repo_root()
    assert not (root / MODULE_NAME).exists(), "the seeded module leaked into the repo"
    assert not (root / TEST_NAME).exists(), "the seeded test leaked into the repo"


def test_case_1c_a_fix_that_does_not_work_cannot_complete(clean_worktree) -> None:
    """The same run, with a fix that changes nothing, must not reach COMPLETE."""

    def broken_implementer(step: str, task, workspace: Workspace):
        if step == "fix":
            # Pretend to fix it, change nothing that matters.
            (workspace.path / MODULE_NAME).write_text(
                BROKEN_MODULE + "\n# tried something\n", encoding="utf-8"
            )
            return "no real change"
        return _scripted_implementer(step, task, workspace)

    result = run_task(
        TASK_ID,
        "A fix that does not fix it.",
        broken_implementer,
        root=repo_root(),
        allowed_paths=(MODULE_NAME, TEST_NAME),
        python=sys.executable,
    )

    assert not result.succeeded
    assert result.disposition == "escalated_to_human"
    assert result.task.stage is not Stage.COMPLETE
    # It stopped rather than looping.
    assert result.ladder.attempts <= result.ladder.max_attempts
    assert result.ladder.stop_reason is not None
