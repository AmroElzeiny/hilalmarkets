"""Start an Open Interpreter session with the product's credentials taken away.

The permission policy screens commands. This module removes the *ability*, which is the
stronger of the two: a session that never receives ``DATABASE_URL`` cannot reach a
database whatever it types, and a rule that is never tested against a real production
string cannot be got around by rewording the command.

Both matter, and neither is enough alone. The policy stops a session that reasons its way
towards production; this stops one that finds a credential lying in the environment
because the engineer had just been running the application in the same terminal.

The scrubbing list lives here, in Python, and the Windows and Linux launchers are one
line each that call this. Two shell scripts each maintaining their own list of secrets to
strip is exactly the duplicate-authority problem this repository keeps finding: the
second copy drifts, and the drift is invisible until something leaks.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

from hm_oi.models import SHARED_KEY_OPT_IN_ENV
from hm_oi.paths import repo_root

#: Variable names that always go, whatever their value. Matched exactly.
_SCRUB_EXACT: Final[frozenset[str]] = frozenset(
    {
        "DATABASE_URL",
        "REDIS_URL",
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "APP_SECRET_KEY",
        "SESSION_SECRET",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "SENTRY_DSN",
        "CLOUDFLARE_API_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    }
)

#: Name fragments that mark a variable as a secret. Broad on purpose: a variable that is
#: removed but was harmless costs the session nothing, and a variable that is kept but
#: was a key costs everything.
_SCRUB_FRAGMENTS: Final[tuple[str, ...]] = (
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "APIKEY",
    "API_KEY",
    "PRIVATE_KEY",
    "ACCESS_TOKEN",
    "AUTH_TOKEN",
    "CREDENTIAL",
    "WEBHOOK_SECRET",
    "SIGNING_KEY",
    "STRIPE",
    "NOWPAYMENTS",
    "CREEM",
    "TELEGRAM_BOT",
    "WHATSAPP",
    "SMTP",
    "TWILIO",
)

#: Never removed. The assistant's own configuration, and the variables a shell needs to
#: work at all. Checked before the fragment list, so ``HM_OI_API_KEY`` survives despite
#: containing ``API_KEY``.
_KEEP_PREFIXES: Final[tuple[str, ...]] = ("HM_OI_",)


def scrub_environment(
    env: dict[str, str] | None = None, *, allow_shared_key: bool | None = None
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Return a copy of the environment with the product's secrets removed.

    Also returns the names that were removed, so the launcher can say how many went
    without printing any of the values.
    """

    source = dict(os.environ if env is None else env)
    if allow_shared_key is None:
        raw = str(source.get(SHARED_KEY_OPT_IN_ENV, "") or "").strip().casefold()
        allow_shared_key = raw in {"1", "true", "yes", "on"}

    kept: dict[str, str] = {}
    removed: list[str] = []
    for name, value in source.items():
        upper = name.upper()
        if any(upper.startswith(prefix) for prefix in _KEEP_PREFIXES):
            kept[name] = value
            continue
        # The one deliberate exception. Sharing the product's key is opted into by
        # setting HM_OI_ALLOW_SHARED_KEY, and it is the only reason a product credential
        # survives this function.
        if allow_shared_key and upper == "OPENAI_API_KEY":
            kept[name] = value
            continue
        if upper in _SCRUB_EXACT or any(part in upper for part in _SCRUB_FRAGMENTS):
            removed.append(name)
            continue
        kept[name] = value

    return kept, tuple(sorted(removed))


def interpreter_executable(root: Path | None = None) -> Path | None:
    """The Open Interpreter binary in the isolated environment, if it is installed."""

    root = root or repo_root()
    candidates = (
        root / ".oi-venv" / "Scripts" / "interpreter.exe",
        root / ".oi-venv" / "bin" / "interpreter",
    )
    return next((item for item in candidates if item.exists()), None)


def profile_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "tools" / "oi" / "hilalmarkets_profile.py"


def build_command(root: Path | None = None, extra: list[str] | None = None) -> list[str]:
    root = root or repo_root()
    executable = interpreter_executable(root)
    if executable is None:
        raise FileNotFoundError(
            "Open Interpreter is not installed. Run tools/oi/bootstrap.ps1 (Windows) or "
            "tools/oi/bootstrap.sh (Linux) first."
        )
    return [str(executable), "--profile", str(profile_path(root)), *(extra or [])]


def main(argv: list[str] | None = None) -> int:
    """Start a session. Everything after ``--`` is passed to Open Interpreter."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    root = repo_root()

    environment, removed = scrub_environment()
    environment["HM_OI_REPO_ROOT"] = str(root)

    try:
        command = build_command(root, arguments)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"  environment  {len(removed)} product credential(s) removed from this session")
    if removed:
        print(f"               ({', '.join(removed[:6])}{'...' if len(removed) > 6 else ''})")

    # ``subprocess.run`` rather than ``os.execve``: on Windows the latter leaves the
    # parent shell confused about which process owns the console, and the terminal
    # interface needs the console to read the engineer's confirmations.
    return subprocess.run(command, env=environment, cwd=str(root), check=False).returncode


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
