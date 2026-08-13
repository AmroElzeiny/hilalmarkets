"""The permanent record of what an autonomous task did.

One JSON object per task, appended to a file under ``reports/oi/``. That location is
already ignored by Git and already refused by ``scripts/check_release_invariants.py``,
so an audit record cannot reach a commit by accident.

**What is deliberately not recorded.** Raw customer conversation text, model reasoning,
and anything secret-shaped. The first two because this phase must not accumulate a
private copy of material the product itself is not yet allowed to retain — conversation
retention and deletion do not exist in the product, so a log here would be data with no
delete path. The third because a log is the least-guarded place a key can land.

Redaction runs over the whole record on the way out, not at each call site. Call-site
redaction works until somebody adds a field and forgets, and the failure is silent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from hm_oi.paths import repo_root, session_log_dir
from hm_oi.redaction import find_secrets, redact_structure

#: Keys that must never appear in a record, whatever their value. Checked by name as
#: well as by content, because a field called ``conversation_text`` is wrong even when
#: the text in it happens to look harmless.
FORBIDDEN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "conversation",
        "conversation_text",
        "customer_text",
        "user_message",
        "transcript",
        "reasoning",
        "model_reasoning",
        "thinking",
        "chain_of_thought",
        "api_key",
        "secret",
        "password",
        "token",
        "prompt",
        "completion",
    }
)


class AuditRefused(RuntimeError):
    """A record was rejected rather than written."""


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Everything one autonomous change is answerable for."""

    task_id: str
    description: str
    branch: str
    disposition: str
    changed_files: tuple[str, ...] = ()
    tests: tuple[dict[str, Any], ...] = ()
    adjacent_selection: tuple[str, ...] = ()
    regression_test: str = ""
    model_tier: str = ""
    tier_reason: str = ""
    cost_usd: float = 0.0
    review_verdict: str = ""
    review_reasons: tuple[str, ...] = ()
    escalation: dict[str, Any] = field(default_factory=dict)
    restrictions: tuple[str, ...] = ()
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "task_id": self.task_id,
            "description": self.description,
            "branch": self.branch,
            "disposition": self.disposition,
            "changed_files": list(self.changed_files),
            "regression_test": self.regression_test,
            "tests": list(self.tests),
            "adjacent_selection": list(self.adjacent_selection),
            "model_tier": self.model_tier,
            "tier_reason": self.tier_reason,
            "cost_usd": round(self.cost_usd, 6),
            "review_verdict": self.review_verdict,
            "review_reasons": list(self.review_reasons),
            "escalation": self.escalation,
            "restrictions": list(self.restrictions),
        }


def audit_log_path(root: Path | None = None) -> Path:
    directory = session_log_dir(root or repo_root())
    return directory / f"builder-{datetime.now(UTC).strftime('%Y%m%d')}.jsonl"


def _reject_forbidden_keys(payload: Any, path: str = "") -> None:
    """Refuse a record that carries a field which must never be logged."""

    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).casefold()
            if lowered in FORBIDDEN_KEYS:
                raise AuditRefused(
                    f"Refusing to write the audit record: field '{path}{key}' is on the "
                    "never-log list. Conversation text, model reasoning and credentials "
                    "are not recorded here."
                )
            _reject_forbidden_keys(value, f"{path}{key}.")
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _reject_forbidden_keys(item, path)


def write_record(record: AuditRecord, root: Path | None = None) -> Path:
    """Redact, check, and append. Returns the file written to.

    Unlike ``hm_oi.telemetry``, this does *not* swallow write errors. Telemetry is a
    convenience and losing a line costs nothing; this is the record of a change made to
    the repository without a person watching, and a change that could not be recorded is
    a change that should not be reported as done.
    """

    payload = record.to_dict()
    _reject_forbidden_keys(payload)
    safe = redact_structure(payload, limit=4000)

    # Belt and braces: prove the serialised line carries nothing secret-shaped before it
    # touches the disk. The structure walk above misses a secret embedded in a key name.
    line = json.dumps(safe, ensure_ascii=False, default=str)
    leaked = find_secrets(line)
    if leaked:
        raise AuditRefused(
            f"Refusing to write the audit record: it still contains {', '.join(leaked)} "
            "after redaction. This is a bug in hm_oi.redaction, not in the task."
        )

    path = audit_log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def read_records(root: Path | None = None) -> tuple[dict[str, Any], ...]:
    """Everything recorded today. For the report and for tests."""

    path = audit_log_path(root)
    if not path.exists():
        return ()
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return tuple(records)


def record_from_task(
    task: Any,
    *,
    branch: str,
    disposition: str,
    tier: str = "",
    tier_reason: str = "",
    cost_usd: float = 0.0,
    escalation: dict[str, Any] | None = None,
    restrictions: tuple[str, ...] = (),
) -> AuditRecord:
    """Build a record from a finished (or abandoned) task."""

    return AuditRecord(
        task_id=task.task_id,
        description=task.description,
        branch=branch,
        disposition=disposition,
        changed_files=tuple(task.changed_files),
        regression_test=task.regression_test,
        tests=tuple(run.to_dict() for run in task.all_runs()),
        adjacent_selection=tuple(task.adjacent_selection),
        model_tier=tier,
        tier_reason=tier_reason,
        cost_usd=cost_usd,
        review_verdict=task.review_verdict,
        review_reasons=tuple(task.review_reasons),
        escalation=escalation or {},
        restrictions=restrictions,
    )
