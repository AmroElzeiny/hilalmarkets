"""The isolated place where autonomous changes happen.

Nothing this harness does touches the engineer's working tree. Every task gets its own
Git worktree on its own branch, created from a named base commit, and the harness may
commit there and nowhere else.

**Why a worktree and not a clone.** A clone of this repository copies 794 MB of history
per task, which on a machine with 2 GB of free memory and a shared CPU is the difference
between a usable tool and one nobody starts. A worktree shares the object store and costs
only the checked-out files.

That sharing is exactly why :func:`guard_worktree_operation` exists. A worktree can reach
the real repository's refs — ``git push``, ``git branch -d``, a tag, a history rewrite all
operate on state the engineer owns. The permission rules refuse those by command text;
this module refuses them by *destination*, which is the check that still works when the
command is spelled in a way no pattern anticipated.

**Windows path length.** Worktrees live at ``C:\\hm-oi-wt\\<branch>`` rather than inside
the repository. This project's own directories are already deep enough that appending a
worktree path overflows the 260-character limit, and the failure arrives as a confusing
"cannot open file" from a tool three layers down rather than as a clear error.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from hm_oi.paths import repo_root

#: Where worktrees are created. Short on purpose - see the module docstring.
WORKTREE_ROOT_ENV: Final[str] = "HM_OI_WORKTREE_ROOT"
DEFAULT_WORKTREE_ROOT: Final[Path] = (
    Path("C:/hm-oi-wt") if os.name == "nt" else Path("/tmp/hm-oi-wt")
)

#: Every autonomous branch starts with this. Anything that does not is not ours, and the
#: harness refuses to commit to it - which is what stops a task that resolved the wrong
#: branch from writing onto ``main``.
BRANCH_PREFIX: Final[str] = "oi/"

#: Branches the harness must never operate on, whatever it was asked to do.
PROTECTED_BRANCHES: Final[frozenset[str]] = frozenset(
    {"main", "master", "develop", "release", "production", "HEAD"}
)

#: Git subcommands that reach beyond the worktree. Refused by destination, not by text.
FORBIDDEN_GIT_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"push", "merge", "rebase", "tag", "reset", "filter-branch", "reflog", "remote"}
)

#: Subcommands that *move or create* a ref. Only these are held to the branch-prefix
#: rule.
#:
#: The distinction matters and was got wrong first time. ``git diff <base-commit>`` and
#: ``git log main`` only *read* a ref, and refusing them because the ref was not an
#: ``oi/`` branch made the harness unable to compute its own diff — the guard blocking
#: the work it exists to supervise. Reading any commit is harmless; writing one is not.
REF_WRITING_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"checkout", "switch", "branch", "worktree", "update-ref", "symbolic-ref"}
)

_SLUG_UNSAFE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


class WorkspaceRefused(PermissionError):
    """The harness was asked to work somewhere it may not."""


def task_slug(task_id: str) -> str:
    """A short, stable, filesystem-safe name for a task.

    Deterministic: the same task identifier always produces the same branch, so a
    re-run continues the same work instead of scattering near-identical branches.
    """

    slug = _SLUG_UNSAFE.sub("-", str(task_id or "").casefold()).strip("-")
    if not slug:
        raise WorkspaceRefused("A task needs an identifier before it can get a branch.")
    return slug[:48]


def branch_name(task_id: str) -> str:
    """The branch for a task. Traceable back to the task by construction."""

    return f"{BRANCH_PREFIX}{task_slug(task_id)}"


def worktree_root() -> Path:
    declared = str(os.environ.get(WORKTREE_ROOT_ENV, "") or "").strip()
    return Path(declared).expanduser() if declared else DEFAULT_WORKTREE_ROOT


def worktree_path(task_id: str) -> Path:
    return worktree_root() / task_slug(task_id)


def guard_worktree_operation(operation: str, target: str = "") -> None:
    """Refuse a Git operation that would escape the worktree.

    Checked before the command is built, so a refusal costs nothing and cannot be
    reworded past. ``operation`` is the git subcommand; ``target`` is the branch or ref
    it names, when there is one.
    """

    op = str(operation or "").strip().casefold()
    if op in FORBIDDEN_GIT_OPERATIONS:
        raise WorkspaceRefused(
            f"'git {op}' is refused. The autonomous builder commits to its own branch "
            "and stops there. Merging, pushing and rewriting history are decisions with "
            "a person's name on them."
        )
    ref = str(target or "").strip()
    # A flag is not a ref.
    if ref.startswith("-"):
        ref = ""
    if not ref:
        return

    # A protected branch is refused for any operation that names it, read or write:
    # nothing this harness does needs to check out or branch from main by name.
    bare = ref.split("/")[-1]
    if bare.casefold() in PROTECTED_BRANCHES or ref.casefold() in PROTECTED_BRANCHES:
        raise WorkspaceRefused(
            f"'{ref}' is a protected branch. The builder may only touch branches "
            f"beginning with '{BRANCH_PREFIX}'."
        )

    # The prefix rule applies only where a ref is created or moved. See
    # REF_WRITING_OPERATIONS for why reads are exempt.
    if op in REF_WRITING_OPERATIONS and not ref.startswith(BRANCH_PREFIX):
        raise WorkspaceRefused(
            f"'{ref}' is not an autonomous-builder branch. Expected one starting "
            f"with '{BRANCH_PREFIX}'."
        )


@dataclass(frozen=True, slots=True)
class Workspace:
    """One task's isolated checkout."""

    task_id: str
    branch: str
    path: Path
    base_commit: str
    created_at: str

    def git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run git inside this worktree, with the escape hatches nailed shut."""

        if arguments:
            # The ref is the first argument that is not a flag. Passing arguments[1]
            # blindly made every flagged command look like it named a branch.
            ref = next((item for item in arguments[1:] if not item.startswith("-")), "")
            guard_worktree_operation(arguments[0], ref)
        return subprocess.run(
            ["git", *arguments],
            cwd=str(self.path),
            capture_output=True,
            text=True,
            check=check,
            timeout=600,
        )

    def changed_files(self) -> tuple[str, ...]:
        """Files changed against the base commit, committed or not."""

        tracked = self.git("diff", "--name-only", self.base_commit, check=False).stdout
        untracked = self.git(
            "ls-files", "--others", "--exclude-standard", check=False
        ).stdout
        names = {line.strip() for line in tracked.splitlines() if line.strip()}
        names |= {line.strip() for line in untracked.splitlines() if line.strip()}
        return tuple(sorted(names))

    def diff(self, *, staged_only: bool = False) -> str:
        """The unified diff this task has produced so far."""

        arguments = ["diff", self.base_commit] if not staged_only else ["diff", "--cached"]
        return self.git(*arguments, check=False).stdout

    def commit(self, message: str) -> str:
        """Commit everything in the worktree. Returns the new commit hash.

        The message is refused rather than redacted if it contains a secret: a commit is
        permanent and shared, and quietly rewriting what somebody wrote into history is
        worse than stopping.
        """

        from hm_oi.redaction import refuse_if_secret

        refuse_if_secret(message, where="a commit message")
        self.git("add", "-A", check=False)
        self.git("commit", "-m", message, check=False)
        return self.git("rev-parse", "HEAD", check=False).stdout.strip()


def _run(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments], cwd=str(cwd), capture_output=True, text=True, timeout=600
    )


def create_workspace(
    task_id: str, root: Path | None = None, *, base: str = "HEAD"
) -> Workspace:
    """Make (or reuse) the isolated worktree for a task.

    Reuse is deliberate. A task that escalates to a stronger model must continue in the
    same place, with the failing test it already wrote still present, or the escalation
    starts from nothing and the evidence is lost.
    """

    root = (root or repo_root()).resolve()
    branch = branch_name(task_id)
    guard_worktree_operation("worktree", branch)

    path = worktree_path(task_id)
    base_commit = _run(["rev-parse", base], root).stdout.strip()
    if not base_commit:
        raise WorkspaceRefused(f"Could not resolve the base commit {base!r}.")

    if path.exists():
        existing = _run(["rev-parse", "--abbrev-ref", "HEAD"], path).stdout.strip()
        if existing != branch:
            raise WorkspaceRefused(
                f"{path} already exists but is on '{existing}', not '{branch}'. "
                "Remove it by hand after checking what is in it."
            )
        return Workspace(
            task_id=task_id,
            branch=branch,
            path=path,
            base_commit=base_commit,
            created_at=datetime.now(UTC).isoformat(),
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    existing_branch = _run(["rev-parse", "--verify", branch], root).returncode == 0
    arguments = ["worktree", "add"]
    if not existing_branch:
        arguments += ["-b", branch]
    arguments += [str(path), base_commit if not existing_branch else branch]

    result = _run(arguments, root)
    if result.returncode != 0:
        raise WorkspaceRefused(
            f"Could not create the worktree for {task_id!r}: {result.stderr.strip()}"
        )

    return Workspace(
        task_id=task_id,
        branch=branch,
        path=path,
        base_commit=base_commit,
        created_at=datetime.now(UTC).isoformat(),
    )


def remove_workspace(task_id: str, root: Path | None = None) -> bool:
    """Take a finished worktree away. The branch is kept.

    Keeping the branch is the point: the work is the deliverable, and a person still has
    to read it. Deleting branches is on the forbidden list precisely so an automated
    tidy-up can never throw away a task's only output.
    """

    root = (root or repo_root()).resolve()
    path = worktree_path(task_id)
    if not path.exists():
        return False
    _run(["worktree", "remove", "--force", str(path)], root)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    _run(["worktree", "prune"], root)
    return not path.exists()
