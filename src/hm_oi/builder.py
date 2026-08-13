"""Drive one autonomous task from start to finish, through every gate.

The orchestration is deliberately separated from whatever produces the code. An
``Implementer`` is any callable that takes a step and does the work; in a real session it
is an Open Interpreter conversation, and in the tests it is a scripted stand-in.

That separation is what makes the harness testable. The thing being validated here is not
"can a model fix a bug" — it is "can a change reach the repository without a reproducing
test, a green suite and an independent review". Those questions are answerable with no
model at all, and answering them with a model would make the result depend on which model
was cheap that week.

Nothing here decides whether a change is *merged*. The end of a successful task is a
branch and a written summary. A person opens the pull request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from hm_oi import audit
from hm_oi.builder_permissions import builder_policy
from hm_oi.escalation import Ladder, LadderExhausted, StopReason
from hm_oi.paths import repo_root
from hm_oi.reviewer import review_diff
from hm_oi.routing import Tier
from hm_oi.workflow import (
    Stage,
    SuiteRun,
    Task,
    WorkflowViolation,
    discover_adjacent_tests,
    run_pytest,
)
from hm_oi.workspace import Workspace, create_workspace

#: Why this phase is restricted, recorded on every audit line. Written once here so the
#: reason travels with the evidence rather than living only in a document.
RESTRICTIONS: tuple[str, ...] = (
    "conversation-material-fixtures-only: product secret redaction (P1) incomplete",
    "no-customer-database: conversation retention and delete (P2) do not exist",
)


class Implementer(Protocol):
    """Whatever actually does the work of a step."""

    def __call__(self, step: str, task: Task, workspace: Workspace) -> Any: ...


@dataclass
class BuilderResult:
    task: Task
    workspace: Workspace
    ladder: Ladder
    disposition: str
    audit_path: Path | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.task.stage is Stage.COMPLETE


def _focused_selection(task: Task) -> list[str]:
    return [task.regression_test] if task.regression_test else []


def run_task(
    task_id: str,
    description: str,
    implementer: Implementer,
    *,
    root: Path | None = None,
    allowed_paths: tuple[str, ...] = (),
    tier: Tier = Tier.NORMAL,
    python: str = ".venv/Scripts/python",
    base: str = "HEAD",
) -> BuilderResult:
    """Take one task through the whole workflow, or stop and explain why not.

    ``allowed_paths`` is the scope. The reviewer refuses anything outside it, so a task
    given no scope is reviewed for everything except scope creep — which is why callers
    should always pass one.
    """

    root = root or repo_root()
    workspace = create_workspace(task_id, root, base=base)
    task = Task(task_id=task_id, description=description)
    ladder = Ladder(task_id=task_id, starting_tier=tier)
    messages: list[str] = []

    def finish(disposition: str) -> BuilderResult:
        record = audit.record_from_task(
            task,
            branch=workspace.branch,
            disposition=disposition,
            tier=str(ladder.current_tier),
            tier_reason="; ".join(step.reason for step in ladder.steps) or "starting tier",
            cost_usd=ladder.spend_usd,
            escalation=ladder.to_dict(),
            restrictions=RESTRICTIONS,
        )
        path = audit.write_record(record, root)
        return BuilderResult(
            task=task,
            workspace=workspace,
            ladder=ladder,
            disposition=disposition,
            audit_path=path,
            messages=messages,
        )

    while True:
        try:
            step = ladder.next_step(
                "starting the task" if ladder.attempts == 0 else "the previous attempt "
                "did not produce a change that passed review"
            )
        except LadderExhausted as exhausted:
            messages.append(str(exhausted))
            ladder.stop_reason = ladder.stop_reason or StopReason.NEEDS_HUMAN
            return finish("escalated_to_human")

        messages.append(f"attempt {step.attempt}: {step.action.value} at {step.tier.value}")

        try:
            # 1. Reproduce.
            if task.stage is Stage.CREATED:
                evidence = implementer("reproduce", task, workspace)
                task.record_reproduction(str(evidence))

            # 2. A test that fails for the right reason, before anything is changed.
            if task.stage is Stage.REPRODUCED:
                test_id = str(implementer("write_failing_test", task, workspace))
                run = run_pytest([test_id], workspace.path, python=python)
                task.record_failing_test(test_id, run)
                messages.append(f"regression test failing as required: {test_id}")

            # 3. The change itself.
            if task.stage is Stage.FAILING_TEST:
                implementer("fix", task, workspace)
                changed = list(workspace.changed_files())
                task.record_fix(changed, workspace.diff())

            # 4. The same test, now passing.
            if task.stage is Stage.FIXED:
                run = run_pytest(_focused_selection(task), workspace.path, python=python)
                task.record_focused_tests(run)

            # 5. Everything next to what was touched, discovered from the repository.
            #
            # Discovery searches the *worktree*, not the engineer's checkout: the files
            # the diff touched exist there and may not exist here at all. Searching the
            # wrong tree found nothing every time, which then triggered the fallback
            # below and ran a whole unrelated suite.
            if task.stage is Stage.FOCUSED_TESTS:
                selection = discover_adjacent_tests(
                    list(task.changed_files), workspace.path
                )
                if selection:
                    run = run_pytest(list(selection), workspace.path, python=python)
                else:
                    # No adjacent coverage found. Recording that honestly is better than
                    # running an unrelated suite and calling its green a verification of
                    # this change.
                    run = SuiteRun(
                        command="(no adjacent tests discovered)",
                        passed=0,
                        failed=0,
                        errors=0,
                        skipped=0,
                        exit_code=0,
                        raw_tail="Nothing in the repository references the changed files.",
                    )
                    messages.append(
                        "no adjacent tests found for: " + ", ".join(task.changed_files)
                    )
                task.record_adjacent_tests(selection, run)

            # 6. A reviewer that never saw how any of it was decided.
            if task.stage is Stage.ADJACENT_TESTS:
                review = review_diff(task, allowed_paths=allowed_paths)
                task.record_review(str(review.verdict), review.reasons())

            # 7. Done means every gate passed, not that the model said so.
            task.complete()
            ladder.succeed()
            messages.append(f"complete on branch {workspace.branch}")
            return finish("completed")

        except WorkflowViolation as violation:
            messages.append(str(violation))
            # The task keeps its evidence and goes round again at the next rung.
            continue
        except LadderExhausted as exhausted:
            messages.append(str(exhausted))
            return finish("escalated_to_human")


def check_command(command: str) -> tuple[str, str]:
    """What autonomous mode would do with a command, and why.

    Exposed for the runbook and for the permission validation case, so the answer people
    read comes from the same policy the harness enforces.
    """

    verdict = builder_policy().evaluate(command)
    return str(verdict.decision), verdict.reason
