"""A database must never be committed, whatever is stuck on the end of its name.

The rule this asserts is *not* "reject `ai_market_monitor.db.bak-20260803`". That file
is only the instance that was found. The rule is: a file whose name says it holds a
database, a database backup, or a log is refused no matter what suffix follows the
extension.

Why it needed a test at all. `scripts/check_release_invariants.py` anchored its pattern
straight to `$`:

    \\.(db|sqlite|sqlite3|log)$

so `app.db` was refused and `app.db.bak-20260803` sailed through. A real 7.7 MB SQLite
database — one real user identity, a live password hash, 46 audit events — was tracked in
Git and shipped in every clone. The gate reported success the whole time, because a
backup is the one copy nobody thinks to name.

The same gap existed in `.gitignore`, which had `*.bak` and `*.db-*` but nothing that
matched `.db.bak-<date>`. Both are fixed; this test covers the gate, and the last case
below covers the two staying in agreement.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from scripts.check_release_invariants import FORBIDDEN_TRACKED_PATTERNS

ROOT = Path(__file__).resolve().parents[2]

#: Every name shape a database or log copy actually arrives in. Parametrised across the
#: whole family on purpose: a fix that only rescues the reported filename must fail here.
DATABASE_EXTENSIONS = ("db", "sqlite", "sqlite3", "log")

SUFFIXES = (
    "",  # the plain file, which the old pattern did catch
    ".bak",
    ".bak-20260803",
    ".backup",
    ".old",
    ".orig",
    ".save",
    ".gz",
    ".zip",
    ".1",
    ".2026-08-03",
    "-20260803",
    "~",
)

#: Names that merely begin with the same letters. These are ordinary files and a rule
#: that refuses them would be widened past the point of usefulness.
INNOCENT = (
    "schema.dbml",
    "notes.logic",
    "src/ai_market_monitor/core/plans.py",
    "docs/ARCHITECTURE.md",
    "logbook.md",
    "database.py",
)


def _refused(path: str) -> bool:
    return any(pattern.search(path) for pattern in FORBIDDEN_TRACKED_PATTERNS)


@pytest.mark.parametrize("extension", DATABASE_EXTENSIONS)
@pytest.mark.parametrize("suffix", SUFFIXES)
@pytest.mark.parametrize("directory", ("", "data/", "src/ai_market_monitor/"))
def test_every_database_copy_is_refused(directory: str, extension: str, suffix: str) -> None:
    """Extension x suffix x location. Every combination must be refused."""

    path = f"{directory}ai_market_monitor.{extension}{suffix}"
    assert _refused(path), f"{path} would be allowed into a commit"


@pytest.mark.parametrize("path", INNOCENT)
def test_ordinary_files_are_still_allowed(path: str) -> None:
    """The rule must not swallow files that only share a prefix."""

    assert not _refused(path), f"{path} is an ordinary file and must not be refused"


@pytest.mark.parametrize(
    "path",
    (
        "VvvebJs/demo.db",
        "VvvebJs/storage/sqlite/demo.sqlite",
        "VvvebJs/storage/sqlite/demo.sqlite3.bak",
        "VvvebJs/debug.log",
    ),
)
def test_the_vendored_page_builder_keeps_its_exemption(path: str) -> None:
    """VvvebJs is vendored third-party code and is exempt at any depth.

    The exemption was written as `(^|/)(?!VvvebJs/)`, which never worked for a nested
    path: `re.search` skipped to the `/` inside the path and matched from there. So the
    line looked like an exemption, and every file under VvvebJs/ was refused anyway.
    Nothing exposed it, because VvvebJs happens to track no database files today.
    """

    assert not _refused(path), f"{path} is vendored third-party code and is exempt"


def test_the_exemption_does_not_leak_to_other_directories() -> None:
    """Only VvvebJs is exempt, and only as a real top-level directory."""

    assert _refused("vendor/VvvebJs-copy/demo.db")
    assert _refused("src/VvvebJs/demo.db")


def test_no_database_is_tracked_right_now() -> None:
    """The live check. This is the assertion that was silently passing before."""

    tracked = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout.splitlines()

    offenders = [path for path in tracked if _refused(path)]
    assert not offenders, (
        "These files are tracked and must not be. A database in Git means real user "
        f"data in every clone: {offenders}"
    )


def test_gitignore_covers_the_same_family() -> None:
    """The gate and .gitignore must not drift apart.

    The gate stops a commit in CI; .gitignore stops the file being staged in the first
    place. When only one of them knows about dated backups, the other is the hole.
    """

    ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for needed in ("*.db.*", "*.bak-*", "*.sqlite3.*", "*.log.*"):
        assert re.search(rf"^{re.escape(needed)}$", ignore_text, re.MULTILINE), (
            f".gitignore is missing {needed}, so a dated database backup can still be "
            "staged by hand"
        )
