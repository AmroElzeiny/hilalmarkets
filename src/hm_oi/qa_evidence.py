"""What is kept for every finding, and the gate everything passes on the way out.

A finding somebody cannot check is an opinion. The schema below is the list of things
that turn one into a fact another person can verify without asking the harness anything:
what was sent, what came back, which stage failed, what the state was before and after,
which target it was, and the one command that shows it again.

**The redaction gate refuses; it does not clean.** ``hm_oi.redaction.redact`` replaces a
secret and carries on, which is right for a log line. It is wrong here. This store is
what gets committed and read, so a secret arriving at it means something upstream is
carrying one, and quietly writing ``[REDACTED:openai_api_key]`` into an evidence file
would hide that. :func:`store` redacts *and then* checks, and raises if anything
secret-shaped survived — validation case 7 depends on exactly this ordering.

**Screenshots and traces are referenced, never inlined.** A base64 PNG inside a JSON
record is unreadable, unreviewable, and large enough that people stop opening the file.
Artifacts stay where Playwright wrote them and the record points at them.

**Nothing here is a customer's words.** The corpus is synthetic and committed. That is
not a promise made in a docstring: ``hm_oi.conversation_source`` will only open the
files on its allowlist, and it is the only way conversation material enters this phase.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from hm_oi.qa_attacks import FailureClass
from hm_oi.redaction import find_secrets, redact_structure

__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "EvidenceLeak",
    "EvidenceRecord",
    "EvidenceStore",
    "FailureStage",
    "Interaction",
    "StateSnapshot",
]

#: Bumped when a field is added or its meaning changes. Records carry it, so an old
#: bundle stays readable against the schema it was written with.
EVIDENCE_SCHEMA_VERSION: Final[str] = "2026-08-14.1"


class EvidenceLeak(RuntimeError):
    """Something secret-shaped reached the evidence store.

    Deliberately fatal. The right response is a person looking at where it came from,
    not a tidier file.
    """


class FailureStage(StrEnum):
    """Where in a turn the wheels came off.

    Mirrors the product's own ``engine/setup_failure_taxonomy.SetupFailureClass`` in
    intent but not in code — the boundary check refuses the import. The names are
    deliberately plainer, because this string is quoted in a report a non-engineer reads.
    """

    #: Nothing failed. Recorded so a passing attack still leaves a record.
    NONE = "none"
    #: The request never reached the application.
    TRANSPORT = "transport"
    #: The application refused before doing any work.
    REFUSED_AT_ENTRY = "refused_at_entry"
    #: The model or an upstream service failed.
    PROVIDER = "provider"
    #: The turn was cut up or labelled wrongly.
    UNDERSTANDING = "understanding"
    #: A capability could not be matched, or the wrong one was.
    CAPABILITY = "capability"
    #: The rules were built and do not mean what was written.
    COMPILATION = "compilation"
    #: The answer reached the screen and the screen is wrong.
    PRESENTATION = "presentation"


@dataclass(frozen=True, slots=True)
class Interaction:
    """One thing sent and one thing that came back.

    ``response_excerpt`` rather than the whole body: a rendered dashboard page is
    hundreds of kilobytes and nobody reads it. The excerpt is the part the finding is
    about, and ``artifacts`` points at the full capture for anyone who wants it.
    """

    surface: str
    #: What the harness did, in words a person can follow.
    action: str
    #: Exactly what was sent. For a conversation, the turn text.
    sent: str
    #: The status code, where there was one.
    status_code: int | None = None
    #: The part of the answer the finding is about.
    response_excerpt: str = ""
    #: Turns that came before, for a multi-turn attack.
    history: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "action": self.action,
            "sent": self.sent,
            "status_code": self.status_code,
            "response_excerpt": self.response_excerpt,
            "history": list(self.history),
        }


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """What the product held before and after the attack.

    Only the fields that decide whether a boundary was crossed: whether anything is
    approved, whether anything is running, and what the draft says. Never the customer's
    text, and never an identifier that could be joined to a person.
    """

    approved: bool
    monitor_active: bool
    #: Capability keys in the draft, sorted. Names of mechanics, not the words that
    #: produced them.
    capability_keys: tuple[str, ...] = ()
    #: Free-form, for anything else the attack needed to prove. Redacted like the rest.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "monitor_active": self.monitor_active,
            "capability_keys": list(self.capability_keys),
            "extra": dict(self.extra),
        }


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Everything kept about one attack attempt.

    The phase brief lists nine things a finding must carry. All nine are here, and the
    store refuses a record that is missing one, because a schema whose fields are
    optional is a schema that describes nothing.
    """

    record_id: str
    attack_id: str
    #: "isolated_test (APP_ENV=test)". Always present, because a result read against the
    #: wrong target is worse than no result.
    environment_label: str
    interaction: Interaction
    failure_stage: FailureStage
    failure_class: FailureClass
    before: StateSnapshot
    after: StateSnapshot
    reproduction: str
    #: Whether the product did the right thing. A passing attack is still recorded.
    product_held: bool
    #: Playwright screenshots, traces, saved payloads. Repository-relative paths.
    artifacts: tuple[str, ...] = ()
    #: The trace id or run id, so this ties back to the application's own logs.
    trace_ref: str = ""
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: str = EVIDENCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "attack_id": self.attack_id,
            "environment_label": self.environment_label,
            "recorded_at": self.recorded_at,
            "product_held": self.product_held,
            "failure_stage": str(self.failure_stage),
            "failure_class": str(self.failure_class),
            "interaction": self.interaction.to_dict(),
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "reproduction": self.reproduction,
            "trace_ref": self.trace_ref,
            "artifacts": list(self.artifacts),
        }


#: A record is refused without these. Named as a tuple so the message can list them.
_REQUIRED_TEXT_FIELDS: Final[tuple[str, ...]] = (
    "record_id",
    "attack_id",
    "environment_label",
    "reproduction",
)

#: Anything that looks like a real person rather than a synthetic fixture. The corpus is
#: synthetic by construction, so a match here means something outside it got in.
_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"[\w.+-]+@(?!example\.(?:com|org)\b|test\b|localhost\b)[\w-]+\.[a-z]{2,}"
    r"|\+\d{1,3}[\s-]?\d{6,}",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class EvidenceStore:
    """Where records are written, and the only way they get there."""

    root: Path

    def path_for(self, record_id: str) -> Path:
        return self.root / f"{record_id}.json"

    def store(self, record: EvidenceRecord) -> Path:
        """Check the raw record, redact, check again, then write.

        Two checks, because they catch different things and deserve different answers.

        The **first** reads the record exactly as the harness built it. A secret here
        means something upstream is carrying one — an attack captured a response body it
        should not have, or a fixture is not as synthetic as it looked. Quietly writing
        ``[REDACTED:openai_api_key]`` at that point would produce a tidy file and hide
        the actual problem, so this refuses and names it.

        The **second** reads the redacted form. A secret that survives redaction is a
        hole in ``hm_oi.redaction`` and needs the pattern fixed, not the record dropped.

        Redaction still runs between them rather than only on failure: it is what makes
        the written file safe, and it happens before any truncation so a length cap
        cannot cut a key in half and leave the readable part behind a pattern that no
        longer matches.
        """

        missing = [
            name for name in _REQUIRED_TEXT_FIELDS if not str(getattr(record, name) or "").strip()
        ]
        if missing:
            raise EvidenceLeak(
                "Refusing to store this record: it is missing "
                + ", ".join(missing)
                + ". Every record names its target and how to reproduce it."
            )

        raw = json.dumps(record.to_dict(), ensure_ascii=False)
        carried = find_secrets(raw)
        if carried:
            raise EvidenceLeak(
                f"Refusing to write evidence for {record.attack_id!r}: the record itself "
                f"carries {', '.join(carried)}. Nothing was written. Redacting it here "
                "would produce a clean file and hide the fact that something upstream is "
                "handling a secret - find where this came from first."
            )

        payload = redact_structure(record.to_dict(), limit=8000)
        serialised = json.dumps(payload, indent=2, ensure_ascii=False)

        leaked = find_secrets(serialised)
        if leaked:
            raise EvidenceLeak(
                f"Refusing to write evidence for {record.attack_id!r}: "
                f"{', '.join(leaked)} survived redaction. Nothing was written. This is a "
                "hole in hm_oi.redaction, not a problem with the attack - fix the "
                "pattern rather than the record."
            )
        identifier = _IDENTIFIER_RE.search(serialised)
        if identifier is not None:
            raise EvidenceLeak(
                f"Refusing to write evidence for {record.attack_id!r}: it contains what "
                "looks like a real email address or phone number. This phase runs on "
                "synthetic fixtures only, so anything real here came from somewhere it "
                "should not have. Nothing was written."
            )

        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.path_for(record.record_id)
        destination.write_text(serialised, encoding="utf-8")
        return destination

    def read(self, record_id: str) -> dict[str, Any]:
        return json.loads(self.path_for(record_id).read_text(encoding="utf-8"))

    def all_records(self) -> tuple[dict[str, Any], ...]:
        if not self.root.is_dir():
            return ()
        return tuple(
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.root.glob("*.json"))
        )
