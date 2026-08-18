"""Give every environment file the same key set without changing what the app reads.

    python scripts/sync_env_keys.py              # report only, writes nothing
    python scripts/sync_env_keys.py --apply      # write, after taking a backup

Why this is not a copy-paste job
--------------------------------
A key absent from a file is not "unset". It is running on its code default. Writing the
example's value into it therefore *replaces a live value*, and wherever the two disagree
that is a silent behaviour change dressed up as tidying. Two settings in this project
disagree today, and one of them halves a compiler bound.

So each file is filled from its own example, the result is loaded through ``Settings``,
and any setting that would come out different is rewritten with the value the application
was already using:

    .env             <- .env.example             then .env.production.example
    .env.production  <- .env.production.example  then .env.example

Filling ``.env.production`` from ``.env.example`` could switch on a development setting in
production, which is why the order is per-file rather than a single merged pool.

One equivalence is allowed. A field typed ``str | None`` defaulting to ``None`` becomes
``""`` once any line exists for it, because a written line cannot express "absent". Every
such field here is read as ``(value or "").strip()``, so the two are the same to the code.
Anything else counts as a difference and is reverted.

Values are never printed. Only key names, line numbers, and counts.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_market_monitor.core.config import Settings  # noqa: E402

KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=")

#: Each real file, and the examples it may borrow a value from, most trusted first.
FILL_ORDER: dict[str, tuple[str, ...]] = {
    ".env": (".env.example", ".env.production.example"),
    ".env.production": (".env.production.example", ".env.example"),
}
EVERY_FILE = (".env", ".env.production", ".env.example", ".env.production.example")


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def keys_with_lines(lines: list[str]) -> dict[str, int]:
    """Key -> 1-based line of its first definition. Later duplicates are ignored."""

    found: dict[str, int] = {}
    for number, line in enumerate(lines, start=1):
        match = KEY_LINE.match(line)
        if match and match.group(1) not in found:
            found[match.group(1)] = number
    return found


def value_of(lines: list[str], key: str) -> str | None:
    for line in lines:
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None


def serialise(value: object) -> str:
    """Render a loaded setting back into the text form an env file uses."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | dict | tuple | set):
        return json.dumps(value, default=str)
    return str(value)


def settings_from(path: Path) -> dict:
    return Settings(_env_file=str(path)).model_dump()


def merged_lines(base: list[str], additions: list[tuple[str, str]], stamp: str) -> list[str]:
    body = list(base)
    while body and not body[-1].strip():
        body.pop()
    header = [
        "",
        f"# --- Keys added {stamp} so every environment file declares the same set. ---",
        "# Each was previously absent and running on its code default. The value written",
        "# here is that same default, so nothing the application reads has changed.",
    ]
    return body + header + [f"{key}={value}" for key, value in additions]


def sync_one(root: Path, name: str, stamp: str, apply: bool) -> int:
    path = root / name
    current = read_lines(path)
    if not current:
        print(f"{name}: not on disk, skipped\n")
        return 0

    union: set[str] = set()
    for other in EVERY_FILE:
        union |= set(keys_with_lines(read_lines(root / other)))
    missing = sorted(union - set(keys_with_lines(current)))
    if not missing:
        print(f"{name}: already complete ({len(union)} keys)\n")
        return 0

    sources = FILL_ORDER.get(name, (".env.production.example", ".env.example"))
    additions = [
        (
            key,
            next(
                (v for s in sources if (v := value_of(read_lines(root / s), key)) is not None),
                "",
            ),
        )
        for key in missing
    ]

    before = settings_from(path)

    def preview(rows: list[tuple[str, str]]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.env"
            probe.write_text("\n".join(merged_lines(current, rows, stamp)) + "\n", "utf-8")
            return settings_from(probe)

    after = preview(additions)
    reverted: list[str] = []
    equivalent: list[str] = []
    for field in before:
        if repr(before[field]) == repr(after[field]):
            continue
        if before[field] is None and after[field] == "":
            equivalent.append(field.upper())
            continue
        env_key = field.upper()
        additions = [
            (k, serialise(before[field]) if k == env_key else v) for k, v in additions
        ]
        reverted.append(env_key)

    if reverted:
        after = preview(additions)
        remaining = [
            f
            for f in before
            if repr(before[f]) != repr(after[f])
            and not (before[f] is None and after[f] == "")
        ]
        if remaining:
            print(f"{name}: STOP. Still differs after correction: {remaining}")
            return 1

    print(f"=== {name} ===")
    print(f"  keys to add:                 {len(additions)}")
    print(f"  reverted to the live value:  {len(reverted)} {reverted}")
    print(f"  None -> '' (same to code):   {len(equivalent)} {sorted(equivalent)}")

    if not apply:
        print("  DRY RUN, nothing written\n")
        return 0

    backup = root / f"{name}.backup.{stamp}"
    shutil.copy2(path, backup)
    final = merged_lines(current, additions, stamp)
    path.write_text("\n".join(final) + "\n", encoding="utf-8")
    placed = keys_with_lines(final)
    print(f"  backup: {backup.name}")
    for key, _ in additions:
        print(f"      line {placed[key]:>5}  {key}")
    print()
    return 0


def main() -> int:
    apply = "--apply" in sys.argv
    root = Path.cwd()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    print("=== BEFORE ===")
    for name in EVERY_FILE:
        count = len(keys_with_lines(read_lines(root / name)))
        print(f"  {name:28} {count if count else 'not on disk'}")
    print()

    status = 0
    for name in FILL_ORDER:
        status |= sync_one(root, name, stamp, apply)

    print("=== AFTER ===")
    tables = {
        name: set(keys_with_lines(read_lines(root / name)))
        for name in EVERY_FILE
        if (root / name).exists()
    }
    if tables:
        reference = max(tables.values(), key=len)
        for name, table in tables.items():
            note = "identical" if table == reference else f"missing {len(reference - table)}"
            print(f"  {name:28} {len(table):>4} keys  {note}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
