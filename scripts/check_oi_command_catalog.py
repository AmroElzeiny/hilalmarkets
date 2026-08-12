"""Keep the command catalog honest against the release gate.

Before ``.agents/commands.json`` existed there were four answers to "how do I run the
checks". ``CLAUDE.md`` said ``ruff check src tests scripts``. ``docs/LOCAL_DEVELOPMENT.md``
said ``ruff check src tests alembic/env.py``. The release gate — the only one that can
stop a change shipping — said ``ruff check .``. Three subsets, and following either of the
first two would pass locally and fail in CI.

A single list fixes that once. This script is what keeps it fixed: when somebody adds a
step to the release gate, the catalog stops matching and this fails, so the list cannot
quietly fall behind the thing it claims to describe.

Checks:

1. Every command the release gate runs is represented in the catalog.
2. Nothing is marked as runnable unattended when it costs money or touches a deployed
   environment.
3. Every script named in the catalog exists on disk.
4. Every area named in ``test_selection`` resolves to a real path or a real command id.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / ".agents" / "commands.json"
WORKFLOW = ROOT / ".github" / "workflows" / "release-gate.yml"

#: Gate steps that need no catalog entry, and why. Kept explicit so that "it is not in
#: the catalog" is always a decision somebody wrote down, never an omission.
GATE_EXEMPT = {
    "python -m pip install": "environment setup, not an engineering command",
    "pip install": "environment setup, not an engineering command",
    "python -m playwright install": "covered by the browser-install entry",
    "python -m pip_audit": "runs only in CI; there is no local equivalent to document",
    "docker build": "container build, covered by the production entries",
}

#: The tools a gate step can invoke that we expect to find in the catalog. A line that
#: mentions none of these is infrastructure, not an engineering command.
INTERESTING = re.compile(
    r"\b(ruff|mypy|pytest|alembic|python\s+scripts/|hm-chatbot-eval|git\s+diff)\b"
)


def _gate_commands() -> list[str]:
    """The shell lines the release gate runs.

    Read as text rather than parsed as YAML: PyYAML is not a dependency of this project,
    and adding one so a checker can read a file it only greps would be the wrong trade.
    ``run:`` steps are the only place the gate executes anything.
    """

    if not WORKFLOW.exists():
        return []
    lines: list[str] = []
    in_run_block = False
    run_indent = 0
    for raw in WORKFLOW.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())

        if in_run_block:
            if stripped and indent <= run_indent:
                in_run_block = False
            elif stripped:
                lines.append(stripped)
                continue

        if stripped.startswith("- run:") or stripped.startswith("run:"):
            _, _, remainder = stripped.partition("run:")
            remainder = remainder.strip()
            if remainder in {"|", ">", "|-", ">-"}:
                in_run_block = True
                run_indent = indent
            elif remainder:
                lines.append(remainder)
    return lines


def _normalise(command: str) -> str:
    """Reduce a command to something two spellings of it share.

    ``.venv/Scripts/python -m ruff check .`` and ``ruff check .`` are the same command on
    two machines. Comparing them literally would report a difference that is not one.
    """

    text = command.strip()
    text = re.sub(r"^\.venv[/\\]Scripts[/\\]", "", text)
    text = re.sub(r"^python(?:3)?\s+-m\s+", "", text)
    text = re.sub(r"^python(?:3)?\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def main() -> int:
    problems: list[str] = []

    try:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read {CATALOG.relative_to(ROOT).as_posix()}: {exc}")
        return 1

    entries = [item for item in payload.get("commands", []) if isinstance(item, dict)]
    known = {_normalise(str(item.get("command", ""))) for item in entries}
    known |= {
        _normalise(str(item["ci_equivalent"]))
        for item in entries
        if item.get("ci_equivalent")
    }

    # 1. Gate coverage.
    for line in _gate_commands():
        if not INTERESTING.search(line):
            continue
        if any(line.startswith(prefix) for prefix in GATE_EXEMPT):
            continue
        normalised = _normalise(line)
        # A gate line is covered when the catalog holds it, or holds something it starts
        # with — `pytest tests/evaluator` covers `pytest tests/evaluator --junitxml=...`.
        if any(normalised.startswith(item) or item.startswith(normalised) for item in known):
            continue
        problems.append(
            f"the release gate runs `{line}` but .agents/commands.json does not list it"
        )

    # 2. Nothing dangerous marked unattended.
    for item in entries:
        safety = str(item.get("safety", ""))
        if item.get("auto_run") and safety not in {"safe_local", "test_only"}:
            problems.append(
                f"`{item.get('id')}` is marked auto_run but its safety is `{safety}`"
            )

    # 3. Scripts exist.
    for item in entries:
        for match in re.finditer(r"(scripts/[\w.\-]+\.(?:py|ps1))", str(item.get("command", ""))):
            if not (ROOT / match.group(1)).exists():
                problems.append(f"`{item.get('id')}` names {match.group(1)}, which does not exist")

    # 4. Test selection points at something real.
    command_ids = {str(item.get("id")) for item in entries}
    for area, plan in payload.get("test_selection", {}).get("areas", {}).items():
        for stage in ("first", "then"):
            for target in plan.get(stage, []):
                if target in command_ids:
                    continue
                if (ROOT / target).exists():
                    continue
                problems.append(
                    f"test_selection.{area}.{stage} names `{target}`, "
                    "which is neither a command id nor a path that exists"
                )

    if problems:
        print("The command catalog does not match the repository:\n")
        for problem in problems:
            print(f"  - {problem}")
        print(f"\n{len(problems)} problem(s).")
        return 1

    print(
        f"Command catalog matches the release gate: {len(entries)} commands, "
        f"{sum(1 for item in entries if item.get('auto_run'))} runnable unattended."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
