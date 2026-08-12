"""Where the repository is, decided once.

Every other module needs the repository root, and every module that worked it out for
itself would eventually disagree — the launcher runs from ``tools/oi``, pytest runs from
the repository root, and Open Interpreter runs from wherever the engineer happened to be
standing. A permission check that resolves ``.env`` against the wrong root is a
permission check that passes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

#: Files that only exist together at the top of this repository. Checked as a set
#: because any one of them can appear alone somewhere else on a developer's disk.
_ROOT_MARKERS: Final[tuple[str, ...]] = ("pyproject.toml", "src", "alembic.ini")

#: Set by the launcher so a session started from another directory still resolves the
#: same tree. An explicit value is trusted only if it really looks like the repository.
_ROOT_ENV_VAR: Final[str] = "HM_OI_REPO_ROOT"


def _looks_like_repo(candidate: Path) -> bool:
    return all((candidate / marker).exists() for marker in _ROOT_MARKERS)


def repo_root(start: Path | None = None) -> Path:
    """The HilalMarkets repository root.

    Resolution order: the launcher's environment variable, then this file's own
    location, then the caller's directory walked upwards. The environment variable is
    first so a wrapper can be explicit, but it still has to point at something that
    looks like the repository — a stale value from a previous checkout must not silently
    aim the permission policy at a directory that has no ``.env`` to protect.
    """

    declared = os.environ.get(_ROOT_ENV_VAR, "").strip()
    if declared:
        candidate = Path(declared).expanduser().resolve()
        if _looks_like_repo(candidate):
            return candidate

    # ``src/hm_oi/paths.py`` -> ``src/hm_oi`` -> ``src`` -> repository root.
    here = Path(__file__).resolve().parents[2]
    if _looks_like_repo(here):
        return here

    current = (start or Path.cwd()).expanduser().resolve()
    for candidate in (current, *current.parents):
        if _looks_like_repo(candidate):
            return candidate

    # No marker anywhere. Returning the package's own grandparent is the least
    # surprising answer and keeps callers from having to handle ``None``; the doctor
    # command reports it so a broken checkout is visible rather than mysterious.
    return here


def agents_dir(root: Path | None = None) -> Path:
    """Where the shared agent convention lives: instructions, skills, catalog."""

    return (root or repo_root()) / ".agents"


def skills_dir(root: Path | None = None) -> Path:
    return agents_dir(root) / "skills"


def catalog_path(root: Path | None = None) -> Path:
    return agents_dir(root) / "commands.json"


def policy_path(root: Path | None = None) -> Path:
    return agents_dir(root) / "permissions.json"


def instructions_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "AGENTS.md"


def session_log_dir(root: Path | None = None) -> Path:
    """Where routing decisions are written.

    Under ``reports/`` because that path is already ignored by Git and already excluded
    by ``check_release_invariants.py``. A log the release gate would refuse to see
    committed is a log that cannot be committed by accident.
    """

    return (root or repo_root()) / "reports" / "oi"
