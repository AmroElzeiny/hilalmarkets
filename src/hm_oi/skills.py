"""Reusable engineering procedures, discovered from ``.agents/skills/``.

Each skill is a directory with a ``SKILL.md`` inside it, following the shared agent
convention: a small block of metadata at the top between ``---`` lines, then the
procedure written in Markdown for whoever — or whatever — is going to follow it.

The metadata is parsed by hand rather than with a YAML library. The product's virtual
environment does not depend on one, and adding a dependency to the shipped project so
that an engineering tool can read its own configuration would be the wrong trade. The
subset supported here is ``key: value`` and ``key: [a, b, c]``, which is all these files
use; anything else is ignored rather than raised on, so a skill with an unusual line
still loads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hm_oi.paths import repo_root, skills_dir
from hm_oi.routing import Tier

#: The metadata block: three dashes, content, three dashes, at the very top of the file.
_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_LIST_RE = re.compile(r"\A\[(.*)\]\Z", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Skill:
    """One engineering procedure."""

    name: str
    description: str
    path: Path
    #: The lowest tier this skill should be run at. A skill whose whole purpose is
    #: careful reasoning must not be answered by the cheap model just because the
    #: sentence that triggered it was short.
    minimum_tier: Tier
    areas: tuple[str, ...]
    #: Whether this skill is allowed to change files. Four of the five are read-only, and
    #: saying so is part of what makes them safe to run without watching.
    read_only: bool
    body: str

    def summary_line(self) -> str:
        return f"{self.name} — {self.description}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "minimum_tier": str(self.minimum_tier),
            "areas": list(self.areas),
            "read_only": self.read_only,
        }


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    block = match.group(1)
    body = text[match.end() :]

    data: dict[str, Any] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, raw = stripped.partition(":")
        value = raw.strip().strip('"').strip("'")
        listed = _LIST_RE.match(value)
        if listed:
            data[key.strip()] = [
                item.strip().strip('"').strip("'")
                for item in listed.group(1).split(",")
                if item.strip()
            ]
        else:
            data[key.strip()] = value
    return data, body


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in {"true", "yes", "on", "1"}:
        return True
    if text in {"false", "no", "off", "0"}:
        return False
    return default


def load_skill(path: Path) -> Skill | None:
    """Read one ``SKILL.md``. Returns ``None`` if it is unreadable or unnamed."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _parse_front_matter(text)
    name = str(meta.get("name") or path.parent.name).strip()
    if not name:
        return None
    try:
        minimum_tier = Tier(str(meta.get("minimum_tier") or "normal").strip().casefold())
    except ValueError:
        minimum_tier = Tier.NORMAL
    areas_raw = meta.get("areas") or []
    areas = tuple(areas_raw) if isinstance(areas_raw, list) else (str(areas_raw),)
    return Skill(
        name=name,
        description=str(meta.get("description") or "").strip(),
        path=path,
        minimum_tier=minimum_tier,
        areas=tuple(str(item) for item in areas if str(item).strip()),
        # Read-only unless a skill says otherwise. The safe default is the one that
        # cannot quietly acquire write access by leaving a line out.
        read_only=_bool(meta.get("read_only"), True),
        body=body.strip(),
    )


def load_skills(root: Path | None = None) -> tuple[Skill, ...]:
    """Every skill in the repository, sorted by name so listings are stable."""

    directory = skills_dir(root or repo_root())
    if not directory.is_dir():
        return ()
    candidates = (
        load_skill(item / "SKILL.md") for item in sorted(directory.iterdir()) if item.is_dir()
    )
    found = [skill for skill in candidates if skill is not None]
    return tuple(sorted(found, key=lambda item: item.name))


def skill_index(skills: tuple[Skill, ...]) -> str:
    """The list the model sees, in the order it should consider them.

    Only names and one-line descriptions. Putting five full procedures into every system
    prompt would spend tokens on four of them being ignored, and would push the actual
    task further from the top of the context.
    """

    if not skills:
        return "No skills are installed."
    lines = [
        f"- `{skill.name}` ({'read-only' if skill.read_only else 'may change files'}, "
        f"minimum tier {skill.minimum_tier.value}): {skill.description}"
        for skill in skills
    ]
    return "\n".join(lines)
