"""Where conversation material may come from — and, today, where it may not.

**This module exists because a precondition failed.**

The autonomous builder was scheduled to run after two pieces of product work:

* secret detection and redaction, running *before* a conversation is stored and *before*
  it is sent to a model provider;
* conversation retention, export, delete, and role-based access with auditing.

Checked at commit ``211aecc5``, neither is finished:

* ``services/system_brain_privacy.redact_customer_text`` is real, but it is called only
  from the internal System Brain admin paths. The customer Setup Chat path never calls
  it, so a pasted seed phrase is stored and forwarded exactly as typed. The function also
  has no seed-phrase pattern and no bare API-key pattern at all.
* Conversation retention, export and delete do not exist. Admin *access* to a
  conversation is audited — ``services/system_brain_conversations`` writes an
  ``AuditEvent`` and demands a stated reason — but that is one of the four things
  required, not all four.

The instruction for that situation is exact: this phase gets no real user conversations,
no production logs and no customer database, and the restriction must be **technically
enforced rather than written down**. Hence this module. Every read of conversation
material goes through :func:`load_conversations`, and it will only open a file on the
allowlist below.

Loosening this is a product decision that depends on the two pieces of work above
landing. It is not a configuration setting, and there is deliberately no environment
variable that turns it off — a switch would be found and used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from hm_oi.paths import repo_root
from hm_oi.redaction import find_secrets, redact_structure

#: The only files this phase may read conversation material from. Repository-relative,
#: committed to Git, and synthetic. Adding to this list is a deliberate act that shows up
#: in review, which is the point of it being a list in code rather than a directory scan.
ALLOWED_CORPORA: Final[tuple[str, ...]] = (
    "tests/fixtures/setup_chat_language_quality_corpus.jsonl",
    "tests/fixtures/prompt_understanding_corpus.jsonl",
    "tests/fixtures/agent_control_corpus.jsonl",
    # Added by the adversarial QA phase (OI-4). Written by hand for this repository,
    # committed, and containing no customer material - which is exactly why adding it
    # here is a two-line reviewed edit rather than a new switch. See
    # docs/OI_ADVERSARIAL_QA.md.
    "tests/fixtures/oi_adversarial_qa_corpus.jsonl",
    # The deliberately poisoned fixture for validation case 7. It holds a synthetic
    # seed phrase and a synthetic API key so the redaction gate can be proved to stop
    # them. It is on the allowlist because the test has to be able to open it; the
    # guarantee is that nothing it contains ever reaches a report.
    "tests/fixtures/oi_adversarial_qa_secret_probe.jsonl",
)

#: Shapes that mean "this is real customer data" and must never be opened here. Checked
#: in addition to the allowlist, so a future edit that widens the allowlist by mistake
#: still cannot reach a database or a production export.
FORBIDDEN_MARKERS: Final[tuple[str, ...]] = (
    ".db",
    ".sqlite",
    ".sqlite3",
    ".bak",
    ".dump",
    ".backup",
    "postgres://",
    "postgresql://",
    "redis://",
    "/var/log",
    "production",
)


class ConversationSourceRefused(PermissionError):
    """Raised when something asks for conversation data this phase may not have.

    A ``PermissionError`` rather than a plain ``ValueError`` because that is what it is:
    the request may be perfectly reasonable, and the answer is still no.
    """


@dataclass(frozen=True, slots=True)
class ConversationCase:
    """One synthetic conversation, already redacted."""

    case_id: str
    source: str
    prompt: str
    history: tuple[str, ...]
    expected: dict[str, Any]

    @property
    def text(self) -> str:
        """Everything the user said, joined. Convenient for searching."""

        return "\n".join([*self.history, self.prompt])


def _reject(reason: str) -> ConversationSourceRefused:
    return ConversationSourceRefused(
        f"{reason}\n\n"
        "This phase may only read committed synthetic fixtures, because conversation "
        "redaction and retention are not finished in the product yet. See "
        "docs/OI_AUTONOMOUS_BUILDER.md, section 'Precondition gate'. Allowed files:\n"
        + "\n".join(f"  {name}" for name in ALLOWED_CORPORA)
    )


def resolve_corpus(name: str, root: Path | None = None) -> Path:
    """Turn a requested name into a real path, or refuse it.

    Refusal is checked three ways: the literal string, the resolved path's position
    inside the repository, and the allowlist. A caller that assembles a path with
    ``..`` to climb out of ``tests/fixtures`` is stopped by the second check.
    """

    root = (root or repo_root()).resolve()
    requested = str(name or "").strip()
    if not requested:
        raise _reject("No conversation source was named.")

    lowered = requested.casefold().replace("\\", "/")
    for marker in FORBIDDEN_MARKERS:
        if marker in lowered:
            raise _reject(
                f"Refused {requested!r}: it names {marker!r}, which indicates real "
                "customer data or a live service."
            )

    candidate = (root / requested).resolve()
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError:
        raise _reject(f"Refused {requested!r}: it points outside the repository.") from None

    if relative not in ALLOWED_CORPORA:
        raise _reject(f"Refused {relative!r}: it is not one of the allowed fixtures.")
    if not candidate.exists():
        raise _reject(f"{relative} is on the allowlist but does not exist in this checkout.")
    return candidate


def load_conversations(
    name: str, root: Path | None = None, *, limit: int | None = None
) -> tuple[ConversationCase, ...]:
    """Read a fixture corpus, redacted, or refuse.

    Redaction runs even though these files are synthetic. Validation case 7 feeds a
    fixture containing a synthetic seed phrase and a synthetic API key deliberately, and
    the guarantee being tested is that nothing downstream ever sees them — which is only
    true if redaction happens at the point of reading rather than at the point of
    writing, where somebody will eventually forget it.
    """

    path = resolve_corpus(name, root)
    cases: list[ConversationCase] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            safe = redact_structure(raw, limit=8000)
            history = safe.get("history") or []
            cases.append(
                ConversationCase(
                    case_id=str(safe.get("case_id") or f"{path.stem}:{number}"),
                    source=path.name,
                    prompt=str(safe.get("prompt") or ""),
                    history=tuple(str(item) for item in history)
                    if isinstance(history, list)
                    else (),
                    expected={
                        key: value
                        for key, value in safe.items()
                        if key.startswith("expected_")
                    },
                )
            )
            if limit is not None and len(cases) >= limit:
                break
    return tuple(cases)


def audit_corpus_for_secrets(name: str, root: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Which cases in a corpus contain something secret-shaped, by kind.

    Reads the file *before* redaction, so it reports what is actually in the fixture.
    Returns kinds only — never the matched text.
    """

    path = resolve_corpus(name, root)
    found: dict[str, tuple[str, ...]] = {}
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            kinds = find_secrets(line)
            if kinds:
                try:
                    case_id = str(json.loads(line).get("case_id") or number)
                except (json.JSONDecodeError, AttributeError):
                    case_id = str(number)
                found[case_id] = kinds
    return found
