"""The tool that edits the two real environment files must not damage what it touches.

``scripts/sync_env_keys.py`` is the only supported way to add a setting to ``.env`` and to
``.env.production`` -- the two files that are not in git, hold live secrets, and cannot be
regenerated from anything. Two faults in it were found while adding the Cloudflare keys on
30 August 2026, and both were silent:

* ``str()`` on a ``SecretStr`` is ``'**********'``. Every secret setting was therefore
  compared against, and written as, that mask. ``--set`` on one rolled a *correct* write
  back, and a secret missing from one file was refilled with ten asterisks over the live
  credential.
* A fill pass and a ``--set`` pass share one timestamp, so they name one backup file. The
  second pass copied over the first, and what it copied was the already-rewritten file --
  so a combined run destroyed the only way back to the state it started from.

Both are the same shape: a safety measure becoming the damage. These tests assert the rule
for every value kind the tool can meet, not only for the token that exposed it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sync_env_keys.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("sync_env_keys", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_env_keys = _load()


@pytest.mark.parametrize(
    "secret",
    ["cfut_example_token", "pbkdf2:sha256:x", "a", "with spaces and = signs", "*" * 10],
)
def test_a_secret_serialises_to_itself_and_never_to_its_mask(secret: str) -> None:
    """The whole family, including a value that legitimately *is* asterisks."""

    rendered = sync_env_keys.serialise(SecretStr(secret))
    assert rendered == secret
    assert rendered != "**********" or secret == "**********"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (True, "true"),
        (False, "false"),
        (3, "3"),
        (0.5, "0.5"),
        ("plain", "plain"),
        (SecretStr(""), ""),
    ],
)
def test_every_other_value_kind_still_renders_as_before(value: object, expected: str) -> None:
    assert sync_env_keys.serialise(value) == expected


def test_the_backup_keeps_the_state_the_run_started_from(tmp_path: Path) -> None:
    """Two passes, one timestamp. The first copy is the valuable one, so it must survive."""

    live = tmp_path / ".env"
    live.write_text("SECRET=original\n", encoding="utf-8")
    backup = tmp_path / ".env.backup.20260830-000000"

    sync_env_keys.backup_once(live, backup)
    live.write_text("SECRET=rewritten-by-the-fill-pass\n", encoding="utf-8")
    sync_env_keys.backup_once(live, backup)

    assert backup.read_text(encoding="utf-8") == "SECRET=original\n"


def test_a_first_backup_is_still_taken(tmp_path: Path) -> None:
    live = tmp_path / ".env"
    live.write_text("SECRET=original\n", encoding="utf-8")
    backup = tmp_path / ".env.backup.20260830-000000"

    assert sync_env_keys.backup_once(live, backup) == backup
    assert backup.read_text(encoding="utf-8") == "SECRET=original\n"
