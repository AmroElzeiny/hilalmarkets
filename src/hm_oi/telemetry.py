"""A record of what the assistant decided, written where it cannot be committed.

Two things are recorded: which tier a task routed to and why, and every command the
permission policy stopped. Both answer questions that come up later — "why did that
cheap answer happen?" and "what did it try to run?" — and neither can be reconstructed
afterwards.

**Nothing here may contain a secret.** The command text is not written, only a short hash
of it. A refused command is refused precisely because it touched something sensitive, so
writing the command into a log file would defeat the refusal. The same applies to model
output and to any reasoning the provider returns: none of it is written.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hm_oi.paths import repo_root, session_log_dir

#: Set to a false-like value to turn the log off entirely.
ENABLED_ENV = "HM_OI_SESSION_LOG"


def _enabled(env: dict[str, str] | None = None) -> bool:
    environment = os.environ if env is None else env
    raw = str(environment.get(ENABLED_ENV, "1") or "1").strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def fingerprint(text: str) -> str:
    """A short, stable hash of a command.

    Enough to notice that the same command was tried five times in a row. Not enough to
    recover what it was, which is the point.
    """

    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


def session_log_path(root: Path | None = None) -> Path:
    directory = session_log_dir(root or repo_root())
    return directory / f"session-{datetime.now(UTC).strftime('%Y%m%d')}.jsonl"


def record(event: str, payload: dict[str, Any], root: Path | None = None) -> Path | None:
    """Append one event. Never raises.

    A log that can fail a session is worse than no log. If the directory cannot be
    created or the file cannot be written, the event is dropped silently — the engineer
    is in the middle of something, and a logging error is not their problem.
    """

    if not _enabled():
        return None
    path = session_log_path(root)
    line = json.dumps(
        {"at": datetime.now(UTC).isoformat(), "event": str(event), **payload},
        ensure_ascii=False,
        default=str,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        return None
    return path


def record_routing(decision: Any, root: Path | None = None) -> Path | None:
    """Record a tier choice. The task text is deliberately not included."""

    return record("routing", {"decision": decision.to_dict()}, root)


def record_permission(
    verdict: Any, command: str, language: str = "", root: Path | None = None
) -> Path | None:
    """Record a policy verdict against a command fingerprint."""

    return record(
        "permission",
        {
            "language": str(language),
            "command_fingerprint": fingerprint(command),
            "command_length": len(str(command or "")),
            **verdict.to_dict(),
        },
        root,
    )
