"""Give every environment file the same key set without changing what the app reads.

    python scripts/sync_env_keys.py              # report only, writes nothing
    python scripts/sync_env_keys.py --apply      # write, after taking a backup

    # change a value on purpose, in every real file at once
    python scripts/sync_env_keys.py --apply --set SCAN_HISTORY_RETENTION_DAYS=3
    python scripts/sync_env_keys.py --apply --set KEY=VALUE --only .env.production

This is the tool for the deployed file too. ``.env.production`` on the server is not in
git, so a new setting never arrives with a ``git pull`` — but the two *examples* do, and
this fills the real file from them. Running it on the server after a deploy is the
supported way to add a release's new keys, rather than typing them by hand.

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

``--set`` is the deliberate exception
-------------------------------------
The rule above makes changing a value impossible by design, which is right for a tool that
runs on its own. But some releases really do mean to change one — the scan-history window
went from 30 days to 3 on 24 August 2026 because fifty monitors on one-minute candles write
about 6.7 GB a day against a 40 GB disk.

``--set`` allows exactly that, and only that: the key and the value are both typed out by a
person, so nothing is silent. It runs as its own pass, after the fill, and never borrows the
fill's revert logic — reverting is precisely what a deliberate change must not do. The new
file is loaded through ``Settings`` before it is kept, so a value the application cannot
parse restores the backup instead of leaving the server with a file it will not start on.

Values are never printed, with one exception: a value passed to ``--set`` is echoed back,
because the person running the command has just typed it. An existing value is never
printed, so replacing a secret shows that it changed and never what it was.
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
        # Named, not counted. A dry run exists to be *reviewed*, and "12 keys will be
        # added" to a production file is not something a person can agree or disagree
        # with. The names are safe to print; these keys are absent from this file, so
        # there is no live value here to leak — and the value shown is the example's,
        # which is in git already.
        print("  DRY RUN, nothing written. It would add:")
        for key, value in additions:
            print(f"      {key}={value}")
        print()
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


def set_one(root: Path, name: str, overrides: dict[str, str], stamp: str, apply: bool) -> int:
    """Write named values into one real file, or say why it will not.

    A value change is not something this tool may decide, so every part of it is typed by
    a person. What is left to the tool is doing it safely: back up, rewrite only the lines
    named, load the result through ``Settings``, and put the backup back if it will not
    load. A server left holding an env file the application cannot parse does not start.
    """

    path = root / name
    lines = read_lines(path)
    if not lines:
        print(f"{name}: not on disk, skipped\n")
        return 0

    present = keys_with_lines(lines)
    changing: dict[str, str] = {}
    unchanged: list[str] = []
    adding: list[str] = []
    for key, value in overrides.items():
        if key not in present:
            adding.append(key)
            changing[key] = value
        elif value_of(lines, key) == value:
            unchanged.append(key)
        else:
            changing[key] = value

    print(f"=== {name} ===")
    for key in sorted(unchanged):
        print(f"  {key}: already {overrides[key]}, nothing to do")
    for key in sorted(adding):
        print(f"  {key}: absent, will be added as {overrides[key]}")
    for key in sorted(set(changing) - set(adding)):
        # The old value is deliberately not shown: this same command replaces secrets.
        print(f"  {key}: will change to {overrides[key]}")
    if not changing:
        print("  nothing to change\n")
        return 0
    if not apply:
        print("  DRY RUN, nothing written\n")
        return 0

    backup = root / f"{name}.backup.{stamp}"
    shutil.copy2(path, backup)

    written = list(lines)
    for index, line in enumerate(written):
        match = KEY_LINE.match(line)
        if match and match.group(1) in changing and match.group(1) not in adding:
            written[index] = f"{match.group(1)}={changing[match.group(1)]}"
    if adding:
        while written and not written[-1].strip():
            written.pop()
        written += [
            "",
            f"# --- Set on purpose {stamp}. ---",
            *[f"{key}={changing[key]}" for key in sorted(adding)],
        ]
    path.write_text("\n".join(written) + "\n", encoding="utf-8")

    try:
        loaded = settings_from(path)
    except Exception as error:  # noqa: BLE001 - any parse failure means put it back
        shutil.copy2(backup, path)
        print(f"  STOP. The result would not load, backup restored: {error}\n")
        return 1

    wrong = [
        key
        for key, value in changing.items()
        if key.lower() in loaded and serialise(loaded[key.lower()]) != value
    ]
    if wrong:
        shutil.copy2(backup, path)
        print(f"  STOP. Read back differently, backup restored: {sorted(wrong)}\n")
        return 1

    after = keys_with_lines(read_lines(path))
    print(f"  backup: {backup.name}")
    print(f"  keys before: {len(present)}   keys after: {len(after)}")
    for key in sorted(changing):
        print(f"      line {after[key]:>5}  {key}")
    print()
    return 0


def parse_overrides(argv: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for index, argument in enumerate(argv):
        if argument != "--set":
            continue
        if index + 1 >= len(argv) or "=" not in argv[index + 1]:
            raise SystemExit("--set needs KEY=VALUE")
        key, _, value = argv[index + 1].partition("=")
        if not KEY_LINE.match(key + "="):
            raise SystemExit(f"--set: {key!r} is not a usable environment key name")
        overrides[key] = value
    return overrides


def main() -> int:
    apply = "--apply" in sys.argv
    overrides = parse_overrides(sys.argv)
    only = (
        sys.argv[sys.argv.index("--only") + 1]
        if "--only" in sys.argv and sys.argv.index("--only") + 1 < len(sys.argv)
        else None
    )
    targets = [only] if only else list(FILL_ORDER)
    root = Path.cwd()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    print("=== BEFORE ===")
    for name in EVERY_FILE:
        count = len(keys_with_lines(read_lines(root / name)))
        print(f"  {name:28} {count if count else 'not on disk'}")
    print()

    status = 0
    for name in targets:
        status |= sync_one(root, name, stamp, apply)

    if overrides:
        print("=== SET ON PURPOSE ===")
        for name in targets:
            status |= set_one(root, name, overrides, stamp, apply)

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
