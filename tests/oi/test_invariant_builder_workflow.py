"""Validation cases 2, 3, 4 and 6: the harness must refuse bad work.

These assert the *rule*, not one example. A fix with no test is refused whatever the fix
is; a weakened assertion is refused whatever file it is in. Each case in the phase brief
appears here with the same number it has in the report, so the two can be read together.
"""

from __future__ import annotations

import pytest

from hm_oi.escalation import (
    EscalationAction,
    Ladder,
    LadderExhausted,
    StopReason,
)
from hm_oi.reviewer import Verdict, review_diff
from hm_oi.routing import Tier
from hm_oi.workflow import (
    Stage,
    SuiteRun,
    Task,
    WorkflowViolation,
    discover_adjacent_tests,
    parse_pytest_output,
)


def _run(*, failed: int = 0, passed: int = 1, exit_code: int | None = None) -> SuiteRun:
    return SuiteRun(
        command="pytest x",
        passed=passed,
        failed=failed,
        errors=0,
        skipped=0,
        exit_code=(1 if failed else 0) if exit_code is None else exit_code,
        raw_tail="",
    )


def _reproduced(task_id: str = "t-1") -> Task:
    task = Task(task_id=task_id, description="a defect")
    task.record_reproduction("Ran the probe and it printed 15 instead of 17.")
    return task


# ----------------------------------------------------------------------------------
# Validation case 2 — a fix with no reproducing test must be rejected.
# ----------------------------------------------------------------------------------


def test_case_2_fix_without_reproducing_test_is_rejected() -> None:
    task = _reproduced()
    with pytest.raises(WorkflowViolation, match="no reproducing test"):
        task.record_fix(["src/ai_market_monitor/engine/comparators.py"], "diff")


@pytest.mark.parametrize(
    "files",
    (
        ["src/ai_market_monitor/engine/comparators.py"],
        ["src/ai_market_monitor/services/ai_setup_chat.py", "docs/NOTES.md"],
        ["tests/unit/test_something.py"],
    ),
)
def test_case_2_holds_for_any_change(files: list[str]) -> None:
    """The gate is about the missing test, not about which files were touched."""

    task = _reproduced()
    with pytest.raises(WorkflowViolation):
        task.record_fix(files, "diff")


def test_a_test_that_passes_before_the_fix_is_refused() -> None:
    """A green 'regression' test does not reproduce anything."""

    task = _reproduced()
    with pytest.raises(WorkflowViolation, match="passed before the fix"):
        task.record_failing_test("tests/unit/test_x.py::test_y", _run(failed=0))


def test_reproduction_must_actually_say_something() -> None:
    task = Task(task_id="t", description="d")
    with pytest.raises(WorkflowViolation):
        task.record_reproduction("nope")


# ----------------------------------------------------------------------------------
# Validation case 3 — a weakened assertion must be rejected by the reviewer.
# ----------------------------------------------------------------------------------


def _reviewable_task(diff: str, files: tuple[str, ...]) -> Task:
    task = _reproduced()
    task.record_failing_test("tests/unit/test_x.py::test_y", _run(failed=1))
    task.record_fix(list(files), diff)
    task.record_focused_tests(_run())
    task.record_adjacent_tests(("tests/unit/test_x.py",), _run())
    return task


WEAKENED_DIFFS = {
    "assertion_deleted": (
        "--- a/tests/unit/test_x.py\n+++ b/tests/unit/test_x.py\n"
        "-    assert result == 17\n+    result = compute()\n"
    ),
    "assert_true_stub": (
        "--- a/tests/unit/test_x.py\n+++ b/tests/unit/test_x.py\n+    assert True\n"
    ),
    "equality_to_membership": (
        "--- a/tests/unit/test_x.py\n+++ b/tests/unit/test_x.py\n"
        "-    assert result == 17\n+    assert result in (15, 17)\n"
    ),
}


@pytest.mark.parametrize("name,diff", sorted(WEAKENED_DIFFS.items()))
def test_case_3_weakened_assertion_is_rejected(name: str, diff: str) -> None:
    task = _reviewable_task(diff, ("tests/unit/test_x.py",))
    review = review_diff(task, allowed_paths=("tests/unit/",))
    assert review.verdict is Verdict.REJECTED, f"{name} was approved"
    assert any(
        finding.rule_id == "weakened_assertion" for finding in review.findings
    ), f"{name} was rejected for the wrong reason: {review.reasons()}"


DISABLED_DIFFS = {
    "skip": "+++ b/tests/unit/test_x.py\n+    @pytest.mark.skip(reason='flaky')\n",
    "xfail": "+++ b/tests/unit/test_x.py\n+    @pytest.mark.xfail\n",
    "skipif": "+++ b/tests/unit/test_x.py\n+    @pytest.mark.skipif(True, reason='x')\n",
    "inline_skip": "+++ b/tests/unit/test_x.py\n+    pytest.skip('not now')\n",
    "test_removed": "+++ b/tests/unit/test_x.py\n-    def test_the_rule():\n",
}


@pytest.mark.parametrize("name,diff", sorted(DISABLED_DIFFS.items()))
def test_case_3_disabling_a_test_is_rejected(name: str, diff: str) -> None:
    task = _reviewable_task(diff, ("tests/unit/test_x.py",))
    review = review_diff(task, allowed_paths=("tests/unit/",))
    assert review.verdict is Verdict.REJECTED, f"{name} was approved"
    assert any(finding.rule_id == "disabled_test" for finding in review.findings)


# ----------------------------------------------------------------------------------
# Validation case 4 — scope creep must be rejected.
# ----------------------------------------------------------------------------------


def test_case_4_unrelated_files_are_rejected() -> None:
    diff = (
        "--- a/src/ai_market_monitor/engine/numeric_clause.py\n"
        "+++ b/src/ai_market_monitor/engine/numeric_clause.py\n+    fixed = True\n"
        "--- a/src/ai_market_monitor/templates/dashboard.html\n"
        "+++ b/src/ai_market_monitor/templates/dashboard.html\n+  <div>tidy up</div>\n"
        "--- a/tests/unit/test_numeric_clause.py\n"
        "+++ b/tests/unit/test_numeric_clause.py\n+    assert parsed.level == 17\n"
    )
    task = _reviewable_task(
        diff,
        (
            "src/ai_market_monitor/engine/numeric_clause.py",
            "src/ai_market_monitor/templates/dashboard.html",
            "tests/unit/test_numeric_clause.py",
        ),
    )
    review = review_diff(
        task,
        allowed_paths=("src/ai_market_monitor/engine/", "tests/unit/"),
    )
    assert review.verdict is Verdict.REJECTED
    creep = [f for f in review.findings if f.rule_id == "scope_creep"]
    assert creep, review.reasons()
    assert "templates/dashboard.html" in creep[0].detail


def test_a_clean_in_scope_diff_is_approved() -> None:
    """The control. If this fails, the reviewer refuses everything and proves nothing."""

    diff = (
        "--- a/src/ai_market_monitor/engine/numeric_clause.py\n"
        "+++ b/src/ai_market_monitor/engine/numeric_clause.py\n"
        "+    level = nearest_operator_to_the_left(clause)\n"
        "--- a/tests/unit/test_numeric_clause.py\n"
        "+++ b/tests/unit/test_numeric_clause.py\n"
        "+    assert parsed.level == 17\n"
    )
    task = _reviewable_task(
        diff,
        ("src/ai_market_monitor/engine/numeric_clause.py", "tests/unit/test_numeric_clause.py"),
    )
    review = review_diff(
        task, allowed_paths=("src/ai_market_monitor/engine/", "tests/unit/")
    )
    assert review.verdict is Verdict.APPROVED, review.reasons()


@pytest.mark.parametrize(
    "path",
    (
        "src/ai_market_monitor/services/sharia_governance.py",
        "src/ai_market_monitor/services/billing.py",
        "src/ai_market_monitor/engine/grounded_patch.py",
        "src/ai_market_monitor/services/entitlements.py",
    ),
)
def test_governed_authority_is_always_rejected(path: str) -> None:
    """Sharia, billing, grounding and entitlement authority are never routine."""

    diff = f"--- a/{path}\n+++ b/{path}\n+    changed = True\n"
    task = _reviewable_task(diff, (path, "tests/unit/test_x.py"))
    review = review_diff(task, allowed_paths=(path.rsplit("/", 1)[0] + "/", "tests/"))
    assert review.verdict is Verdict.REJECTED
    assert any(f.rule_id == "governed_authority" for f in review.findings)


def test_a_secret_in_the_diff_is_rejected() -> None:
    diff = (
        "--- a/src/x.py\n+++ b/src/x.py\n"
        "+    client = OpenAI(api_key='sk-abcdefghijklmnopqrstuvwxyz012345')\n"
        "--- a/tests/unit/test_x.py\n+++ b/tests/unit/test_x.py\n+    assert True is True\n"
    )
    task = _reviewable_task(diff, ("src/x.py", "tests/unit/test_x.py"))
    review = review_diff(task)
    assert review.verdict is Verdict.REJECTED
    assert any(f.rule_id == "secret_in_diff" for f in review.findings)


# ----------------------------------------------------------------------------------
# A red suite blocks completion, and a review rejection returns the task.
# ----------------------------------------------------------------------------------


def test_completion_is_refused_on_a_red_suite() -> None:
    task = _reproduced()
    task.record_failing_test("tests/unit/test_x.py::test_y", _run(failed=1))
    task.record_fix(["src/x.py"], "diff")
    with pytest.raises(WorkflowViolation, match="still failing"):
        task.record_focused_tests(_run(failed=2))
    assert task.stage is Stage.FIXED


def test_completion_is_refused_without_the_before_and_after_proof() -> None:
    task = _reproduced()
    task.record_failing_test("tests/unit/test_x.py::test_y", _run(failed=1))
    task.record_fix(["src/x.py"], "diff")
    task.record_focused_tests(_run())
    task.record_adjacent_tests((), _run())
    task.record_review("approved", ())
    task.passing_run = None  # simulate a lost proof
    with pytest.raises(WorkflowViolation, match="before-and-after proof"):
        task.complete()


def test_a_rejected_review_sends_the_task_back_with_reasons() -> None:
    task = _reviewable_task("+++ b/src/x.py\n+ x = 1\n", ("src/x.py",))
    with pytest.raises(WorkflowViolation, match="REJECTED by the independent reviewer"):
        task.record_review("rejected", ("[no_test_in_diff] no test",))
    assert task.stage is Stage.FIXED
    assert task.review_reasons


def test_stages_cannot_be_skipped() -> None:
    task = Task(task_id="t", description="d")
    with pytest.raises(WorkflowViolation, match="must reach"):
        task.record_failing_test("tests/x.py::y", _run(failed=1))


# ----------------------------------------------------------------------------------
# Validation case 6 — escalation is bounded and stops at a human.
# ----------------------------------------------------------------------------------


def test_case_6_escalation_climbs_then_stops() -> None:
    ladder = Ladder(task_id="unsolvable", starting_tier=Tier.FAST, max_attempts=3)

    first = ladder.next_step("the reproduction did not show the reported behaviour")
    assert first.action is EscalationAction.GATHER_EVIDENCE

    second = ladder.next_step("the fix did not make the regression test pass")
    assert second.action is EscalationAction.STRONGER_MODEL
    assert second.tier is Tier.NORMAL

    third = ladder.next_step("the second attempt broke two adjacent test modules")
    assert third.action is EscalationAction.ARCHITECTURE_REVIEW
    assert third.tier is Tier.DEEP

    with pytest.raises(LadderExhausted, match="attempts_exhausted"):
        ladder.next_step("the architecture review could not identify a safe change")
    assert ladder.stop_reason is StopReason.ATTEMPTS_EXHAUSTED


def test_case_6_does_not_loop_forever() -> None:
    """However many times it is asked, it stops. This is the anti-loop assertion."""

    ladder = Ladder(task_id="loop", max_attempts=3)
    stopped = False
    for _ in range(50):
        try:
            ladder.next_step("still failing for the same reason as the last attempt")
        except LadderExhausted:
            stopped = True
            break
    assert stopped, "the ladder never stopped"
    assert ladder.attempts <= 3


def test_spend_ceiling_stops_a_task() -> None:
    ladder = Ladder(task_id="pricey", max_attempts=99, max_spend_usd=0.10)
    ladder.record_spend(0.11)
    with pytest.raises(LadderExhausted, match="budget_exhausted"):
        ladder.next_step("the model spent the budget without producing a change")
    assert ladder.stop_reason is StopReason.BUDGET_EXHAUSTED


def test_time_ceiling_stops_a_task() -> None:
    ladder = Ladder(task_id="slow", max_attempts=99, max_wall_seconds=0.0)
    with pytest.raises(LadderExhausted, match="time_exhausted"):
        ladder.next_step("the task ran past its wall-clock limit while retrying")


def test_an_escalation_needs_a_real_reason() -> None:
    ladder = Ladder(task_id="t")
    with pytest.raises(ValueError, match="reason"):
        ladder.next_step("failed")


# ----------------------------------------------------------------------------------
# Adjacent tests are discovered, not guessed.
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    (
        ("tests/unit/test_x.py", True),
        ("tests/oi/test_invariant_oi_permissions.py", True),
        ("test_seeded_defect.py", True),          # a test outside tests/ is still a test
        ("some/where/test_thing.py", True),
        ("tests/conftest.py", True),              # inside the suite, helper module
        ("src/ai_market_monitor/engine/comparators.py", False),
        ("src/hm_oi/testing_helpers.py", False),  # 'test' in the name is not 'test_'
        ("docs/testing.md", False),
        ("tests/fixtures/corpus.jsonl", False),   # not Python
    ),
)
def test_what_counts_as_a_test_file_has_one_answer(path: str, expected: bool) -> None:
    """One owner for this question.

    It was decided in three places and they disagreed: adjacent-test discovery and the
    reviewer both keyed on the `tests/` prefix, so a regression test written anywhere
    else was invisible to both at once. The harness reported "no adjacent tests" while
    the reviewer rejected the same diff for containing no test.
    """

    from hm_oi.workflow import is_test_file

    assert is_test_file(path) is expected, path


def test_the_reviewer_and_discovery_share_that_one_answer() -> None:
    """Both callers must import it, not re-implement it."""

    import inspect

    from hm_oi import reviewer

    source = inspect.getsource(reviewer)
    assert "is_test_file" in source
    assert "def _is_test_file" not in source, "the reviewer grew its own copy again"


def test_adjacent_tests_are_found_by_searching_the_repository() -> None:
    found = discover_adjacent_tests(["src/hm_oi/permissions.py"])
    assert found, "no adjacent tests discovered for a module that certainly has some"
    assert any("test_invariant_oi_permissions" in name for name in found)


def test_a_changed_test_file_is_its_own_adjacent_test() -> None:
    found = discover_adjacent_tests(["tests/oi/test_invariant_oi_permissions.py"])
    assert "tests/oi/test_invariant_oi_permissions.py" in found


def test_adjacent_discovery_does_not_match_a_longer_name() -> None:
    """'comparators' must not pull in a module called 'comparators_legacy'."""

    found = discover_adjacent_tests(["src/hm_oi/routing.py"])
    assert all(name.endswith(".py") for name in found)


def test_adjacent_discovery_requires_a_reference_not_a_mention() -> None:
    """A file that merely contains the word is not adjacent to the module.

    Found by measuring: a change to `engine/comparators.py` pulled in this harness's own
    test files, which say "comparators" only inside example data. Unrelated modules in
    the adjacent selection make every run slower and let an unrelated failure block a
    task that did not cause it.
    """

    found = discover_adjacent_tests(["src/ai_market_monitor/engine/comparators.py"])
    assert "tests/oi/test_invariant_builder_workflow.py" not in found
    assert "tests/oi/test_invariant_builder_boundaries.py" not in found


def test_a_real_importer_is_still_found() -> None:
    """The control: tightening the rule must not stop it finding true importers."""

    found = discover_adjacent_tests(["src/hm_oi/permissions.py"])
    assert any("test_invariant_oi_permissions" in name for name in found), found


def test_a_red_run_is_never_parsed_as_green_when_the_summary_is_missing() -> None:
    """The `-qq` trap: no summary line, but the run failed.

    This project sets `-q` in pyproject. A caller that adds its own makes `-qq`, and
    pytest then prints no "4 failed" line at all. Counting zero failures from that would
    mark a red run green, which is the one mistake this parser must never make.
    """

    quiet_output = (
        "FAILED test_seeded_defect.py::test_a_value_exactly_on_the_threshold_meets_it[0.0]\n"
        "FAILED test_seeded_defect.py::test_a_value_exactly_on_the_threshold_meets_it[1.0]\n"
        "FAILED test_seeded_defect.py::test_a_value_exactly_on_the_threshold_meets_it[17.0]\n"
    )
    run = parse_pytest_output("pytest -qq x", 1, quiet_output)
    assert run.red
    assert run.failed == 3, "a failing run with no summary was counted as zero failures"


def test_collection_errors_are_counted_too() -> None:
    run = parse_pytest_output("pytest -qq x", 2, "ERROR tests/oi/test_x.py\n")
    assert run.red
    assert run.errors == 1


def test_a_genuinely_green_run_is_not_given_phantom_failures() -> None:
    """The fallback must not invent failures when the exit code is zero."""

    run = parse_pytest_output("pytest x", 0, "no summary printed at all")
    assert run.green
    assert run.failed == 0


def test_pytest_output_is_parsed_from_the_summary_not_the_exit_code() -> None:
    run = parse_pytest_output("pytest x", 1, "collected 5 items\n3 passed, 2 failed in 1.2s")
    assert (run.passed, run.failed) == (3, 2)
    assert run.red

    green = parse_pytest_output("pytest x", 0, "217 passed in 16.4s")
    assert green.passed == 217
    assert green.green
