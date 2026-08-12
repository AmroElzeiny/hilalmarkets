"""The engineering commands this repository actually uses.

There were four different answers to "how do I run the checks" before this file existed:
``CLAUDE.md`` said ``ruff check src tests scripts``, ``docs/LOCAL_DEVELOPMENT.md`` said
``ruff check src tests alembic/env.py``, and the release gate — the one that decides
whether a change ships — said ``ruff check .``. Three lists, three subsets, and only the
last one could fail a pull request.

That is the same failure this codebase keeps finding in its compiler: two readers of one
concept, each understanding a different part of it. The fix is the same. This is the one
list, ``scripts/check_oi_command_catalog.py`` fails when the release gate grows a step
that is not in it, and the documents point here instead of repeating it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from hm_oi.paths import catalog_path, repo_root


class CommandSafety(StrEnum):
    """How much trouble running a command can cause.

    The order matters and is used for comparison: everything up to ``TEST_ONLY`` may run
    unattended, everything after it may not.
    """

    SAFE_LOCAL = "safe_local"
    TEST_ONLY = "test_only"
    CREDENTIALED_PAID = "credentialed_paid"
    STAGING_ONLY = "staging_only"
    PRODUCTION = "production"


#: The line the assistant may not cross on its own. A paid call, a deployed environment
#: and a production stack all need a person to say why first.
UNATTENDED_SAFETY: frozenset[CommandSafety] = frozenset(
    {CommandSafety.SAFE_LOCAL, CommandSafety.TEST_ONLY}
)


class CatalogError(RuntimeError):
    """The catalog file is missing or cannot be read."""


@dataclass(frozen=True, slots=True)
class CommandEntry:
    """One command, and what running it implies."""

    id: str
    title: str
    command: str
    safety: CommandSafety
    #: Whether the assistant may run this without asking. Always ``False`` for anything
    #: above ``TEST_ONLY``, whatever the file says — see ``_entry_from``.
    auto_run: bool
    areas: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    notes: str = ""
    ci_equivalent: str | None = None

    @property
    def is_placeholder(self) -> bool:
        """Whether the command still has ``<PLACEHOLDER>`` text to fill in."""

        return "<" in self.command and ">" in self.command

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "command": self.command,
            "safety": str(self.safety),
            "auto_run": self.auto_run,
            "areas": list(self.areas),
            "requires": list(self.requires),
            "notes": self.notes,
            "ci_equivalent": self.ci_equivalent,
        }


@dataclass(frozen=True, slots=True)
class Catalog:
    """Every command, plus how to choose tests for an area."""

    entries: tuple[CommandEntry, ...]
    test_selection: dict[str, dict[str, list[str]]]

    def by_id(self, command_id: str) -> CommandEntry | None:
        return next((item for item in self.entries if item.id == command_id), None)

    def for_area(self, area: str) -> tuple[CommandEntry, ...]:
        """Commands relevant to one area of the repository.

        ``all`` matches everything on purpose: a lint failure is relevant to whatever you
        just touched, whatever that was.
        """

        wanted = str(area).strip().casefold()
        return tuple(
            entry
            for entry in self.entries
            if wanted in {item.casefold() for item in entry.areas} or "all" in entry.areas
        )

    def with_safety(self, *safety: CommandSafety) -> tuple[CommandEntry, ...]:
        allowed = set(safety)
        return tuple(entry for entry in self.entries if entry.safety in allowed)

    @property
    def unattended(self) -> tuple[CommandEntry, ...]:
        """Everything the assistant may run without asking first."""

        return tuple(entry for entry in self.entries if entry.auto_run)

    def test_plan(self, area: str) -> dict[str, list[str]]:
        """The smallest authoritative set for an area, then the adjacent regressions.

        An unknown area falls back to the offline suites rather than to nothing. "I did
        not recognise the area so I ran no tests" is the worst possible answer.
        """

        plan = self.test_selection.get(str(area).strip().casefold())
        if plan:
            return {"first": list(plan.get("first", [])), "then": list(plan.get("then", []))}
        return {
            "first": ["tests/unit"],
            "then": ["tests/engine", "tests/interpreter", "tests/services"],
        }


def _entry_from(raw: dict[str, Any]) -> CommandEntry | None:
    try:
        safety = CommandSafety(str(raw["safety"]))
        command_id = str(raw["id"]).strip()
        command = str(raw["command"]).strip()
    except (KeyError, ValueError):
        return None
    if not command_id or not command:
        return None

    # The file may say ``auto_run: true`` for a paid or production command. It is
    # overruled here rather than trusted, because that field is the one an editing
    # session is most likely to get wrong, and the cost of getting it wrong is a
    # provider bill or a touched deployment.
    declared = bool(raw.get("auto_run", False))
    auto_run = declared and safety in UNATTENDED_SAFETY

    return CommandEntry(
        id=command_id,
        title=str(raw.get("title") or command_id),
        command=command,
        safety=safety,
        auto_run=auto_run,
        areas=tuple(str(item) for item in raw.get("areas", ())),
        requires=tuple(str(item) for item in raw.get("requires", ())),
        notes=str(raw.get("notes") or ""),
        ci_equivalent=(
            str(raw["ci_equivalent"]) if raw.get("ci_equivalent") is not None else None
        ),
    )


def load_catalog(root: Path | None = None) -> Catalog:
    """Read the catalog. Raises if it is missing — a silent empty list is worse."""

    path = catalog_path(root or repo_root())
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogError(f"the command catalog is missing at {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"the command catalog at {path} is not valid JSON: {exc}") from exc

    entries = tuple(
        entry
        for entry in (
            _entry_from(raw)
            for raw in payload.get("commands", [])
            if isinstance(raw, dict)
        )
        if entry is not None
    )
    selection_raw = payload.get("test_selection", {}).get("areas", {})
    selection = {
        str(area).casefold(): {
            "first": [str(item) for item in value.get("first", [])],
            "then": [str(item) for item in value.get("then", [])],
        }
        for area, value in selection_raw.items()
        if isinstance(value, dict)
    }
    return Catalog(entries=entries, test_selection=selection)
