"""What operational evidence may be looked at, where it came from, and what is stripped.

Three controls live here, and they run in this order for every piece of evidence:

1. **Allowlist.** A source not named in :data:`EVIDENCE_SOURCES` is refused. Default deny,
   so a source added to the product later is invisible here until somebody adds it on
   purpose and a reviewer sees the line.
2. **Environment.** Every piece carries where it came from, and production may only ever
   arrive as an exported snapshot. There is no code path that connects to a live
   production database or Redis, so there is nothing to misconfigure.
3. **Sanitization.** Everything passes :func:`sanitize` before it can reach an agent
   context, a prompt, an issue record or a report.

**On reusing the product's redactor.** The instruction for this phase was to reuse the
prompt-12 redaction path rather than write a second one. That is not possible and the
reason matters: ``scripts/check_oi_boundary.py`` fails the build if ``hm_oi`` imports
``ai_market_monitor``, which is what keeps this AGPL-licensed tooling out of the shipped
product. Prompt 12 is also unfinished — ``services/system_brain_privacy.redact_customer_text``
has no seed-phrase pattern and no bare API-key pattern, and the customer Setup Chat path
never calls it.

So this module reuses :mod:`hm_oi.redaction`, which already exists from OI-2 and is the
*same* redactor this tooling used before. There is no third copy. ``hm_oi.redaction``'s
own docstring records the shared-contract test that stops it drifting from the product's
list.

**What sanitization cannot do.** It removes shapes it recognises and whole field kinds it
knows the names of. A customer's own words sitting in a field nobody declared are not
recognised, which is why :data:`DENIED_FIELDS` refuses whole payloads rather than trying
to clean them.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from hm_oi.redaction import find_secrets, redact_structure


class Environment(StrEnum):
    """Where a piece of evidence came from.

    ``PRODUCTION_SNAPSHOT`` is deliberately not called ``PRODUCTION``. The distinction is
    the control: a snapshot is a file somebody exported and sanitized, and there is no
    enum member meaning "connected to production right now" because no such path exists.
    """

    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION_SNAPSHOT = "production_snapshot"


#: Environments a live connection may be opened to. Production is absent on purpose.
CONNECTABLE: Final[frozenset[Environment]] = frozenset(
    {Environment.LOCAL, Environment.STAGING}
)


class EvidenceRefused(PermissionError):
    """Evidence was not allowed, or could not be made safe."""


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """One permitted place to look."""

    key: str
    description: str
    #: What it is, for the skills: metric, endpoint, log, record.
    kind: str
    #: Where in the repository this signal is produced, so a claim can cite it.
    produced_by: str


#: The allowlist. Every entry was checked against HEAD (211aecc5) - a source that does
#: not exist is worse than a missing one, because a skill will cite it confidently.
EVIDENCE_SOURCES: Final[dict[str, EvidenceSource]] = {
    source.key: source
    for source in (
        EvidenceSource(
            "metrics.durable",
            "Counters, gauges and histograms, across every process, surviving restarts.",
            "metric",
            "observability/durable_metrics.py::load_recorder",
        ),
        EvidenceSource(
            "metrics.slo",
            "Objective attainment and error budget.",
            "metric",
            "observability/slos.py",
        ),
        EvidenceSource(
            "alerts.rules",
            "Which alert rules exist and which have fired.",
            "record",
            "observability/alerts.py::ALERT_RULES",
        ),
        EvidenceSource(
            "alerts.delivery",
            "Delivery attempts, routes, retries and fallbacks. Never message bodies.",
            "record",
            "observability/alert_delivery.py",
        ),
        EvidenceSource(
            "issues.operational",
            "Operational issue records and their dedupe keys.",
            "record",
            "observability/issues.py::OperationalIssueService",
        ),
        EvidenceSource(
            "endpoint.health",
            "GET /health - liveness.",
            "endpoint",
            "api/routers/public.py:728",
        ),
        EvidenceSource(
            "endpoint.admin_health",
            "GET /api/v1/admin/health - component health.",
            "endpoint",
            "api/routers/admin.py:106",
        ),
        EvidenceSource(
            "endpoint.admin_activity",
            "GET /api/v1/admin/activity - recent operational activity.",
            "endpoint",
            "api/routers/admin.py:115",
        ),
        EvidenceSource(
            "endpoint.observability_health",
            "GET /api/v1/dashboard/observability/health - monitor health.",
            "endpoint",
            "api/routers/dashboard_api.py:2959",
        ),
        EvidenceSource(
            "provider.circuit",
            "Provider circuit state, retries, timeouts and status-code counts.",
            "metric",
            "services/provider_runtime.py",
        ),
        EvidenceSource(
            "ai.usage",
            "Token counts, estimated spend, budget and quota state.",
            "metric",
            "services/ai_budget.py",
        ),
        EvidenceSource(
            "ai.routing",
            "Which model tier a turn routed to and why.",
            "record",
            "services/ai_model_routing.py",
        ),
        EvidenceSource(
            "ai.failure_stage",
            "Typed failure stage and owner for a Setup Chat turn.",
            "record",
            "engine/setup_failure_taxonomy.py::SetupFailureClass",
        ),
        EvidenceSource(
            "worker.health",
            "Queue depth, task failures, scheduler liveness.",
            "metric",
            "worker.py",
        ),
        EvidenceSource(
            "scanner.runs",
            "Scan claim, run, failure, recovery and due-window misses.",
            "record",
            "services/scanner.py",
        ),
        EvidenceSource(
            "logs.application",
            "Application log lines, already redacted by core/logging.py.",
            "log",
            "core/logging.py::redact_sensitive_values",
        ),
        EvidenceSource(
            "setup_chat.trace",
            "Setup Chat turn traces: stages and timings only, never the words.",
            "record",
            "services/setup_chat_agent.py",
        ),
    )
}


#: Field names whose *contents* are the thing we must not have. These are refused
#: outright rather than cleaned: a raw prompt cannot be made safe by removing the parts
#: that look like keys, because the customer's own sentences are the sensitive part.
DENIED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "prompt",
        "prompts",
        "system_prompt",
        "messages",
        "completion",
        "completions",
        "model_output",
        "response_text",
        "reasoning",
        "thinking",
        "chain_of_thought",
        "conversation",
        "conversation_text",
        "transcript",
        "user_message",
        "customer_text",
        "strategy_text",
        "watchlist",
        "watchlist_text",
        "plan_text",
        "email_body",
        "body",
        "notes",
        "answer",
    }
)

#: Personal identifiers. Present as names, refused wherever they appear.
IDENTIFYING_FIELDS: Final[frozenset[str]] = frozenset(
    {"email", "email_address", "phone", "phone_number", "full_name", "name", "address"}
)

#: Fields that may carry a Sharia status. A status is a governed decision with evidence
#: behind it; repeated inside an operational diagnosis it becomes a claim of fact that
#: no one reviewed. Refused, not redacted.
SHARIA_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "sharia_status",
        "shariah_status",
        "halal",
        "haram",
        "compliance_status",
        "sharia_ruling",
        "shariah_ruling",
        "sharia_verdict",
    }
)

_SHARIA_CLAIM: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(?:is|are|was|were|marked|declared|confirmed)\s+(?:as\s+)?(?:halal|haram|"
    r"shariah?[- ]compliant|non[- ]compliant)\b"
)

#: Identifier fields that a correlation genuinely needs. Pseudonymised, never dropped -
#: without them two lines cannot be tied to the same session, and the investigation
#: cannot say "the same user hit this twice".
CORRELATION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "user_id",
        "account_id",
        "session_id",
        "conversation_id",
        "strategy_id",
        "request_id",
        "task_id",
        "scan_id",
        "trace_id",
    }
)


class Pseudonymiser:
    """Stable fake identifiers, for one investigation only.

    The salt is generated per investigation and never written down. Within one
    investigation ``user_id`` 42 is always ``user-a3f1c2``, so "the same account appears
    in both incidents" is answerable. Across investigations, and to anybody holding the
    report, the mapping cannot be reversed or joined - a new salt makes a different name
    for the same person.
    """

    def __init__(self, salt: bytes | None = None) -> None:
        self._salt = salt or secrets.token_bytes(32)
        self._seen: dict[tuple[str, str], str] = {}

    def pseudonym(self, field_name: str, value: Any) -> str:
        raw = str(value or "")
        if not raw:
            return ""
        key = (field_name, raw)
        if key not in self._seen:
            digest = hmac.new(self._salt, f"{field_name}:{raw}".encode(), hashlib.sha256)
            prefix = field_name.removesuffix("_id") or "id"
            self._seen[key] = f"{prefix}-{digest.hexdigest()[:8]}"
        return self._seen[key]

    @property
    def count(self) -> int:
        return len(self._seen)


def sanitize(
    payload: Any,
    *,
    pseudonymiser: Pseudonymiser,
    path: str = "",
) -> Any:
    """Make one piece of evidence safe to put in front of a model.

    Refuses rather than cleans where cleaning would be a lie: a field holding a raw
    prompt is dropped whole, because the sensitive part is the meaning, not a pattern.
    """

    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            name = str(key)
            lowered = name.casefold()
            if lowered in DENIED_FIELDS:
                cleaned[name] = "[WITHHELD: raw text is never read by this tool]"
                continue
            if lowered in IDENTIFYING_FIELDS:
                cleaned[name] = "[WITHHELD: personal identifier]"
                continue
            if lowered in SHARIA_FIELDS:
                cleaned[name] = "[WITHHELD: Sharia status is a governed decision]"
                continue
            if lowered in CORRELATION_FIELDS:
                cleaned[name] = pseudonymiser.pseudonym(lowered, value)
                continue
            cleaned[name] = sanitize(value, pseudonymiser=pseudonymiser, path=f"{path}{name}.")
        return cleaned

    if isinstance(payload, (list, tuple)):
        return [sanitize(item, pseudonymiser=pseudonymiser, path=path) for item in payload]

    if isinstance(payload, str):
        text = _SHARIA_CLAIM.sub("[WITHHELD: Sharia claim]", payload)
        return redact_structure(text, limit=2000)

    return payload


@dataclass(frozen=True, slots=True)
class Evidence:
    """One sanitized observation, labelled with where it came from."""

    source: str
    environment: Environment
    payload: Any
    collected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def produced_by(self) -> str:
        return EVIDENCE_SOURCES[self.source].produced_by

    def cite(self) -> str:
        """How a skill refers to this in a diagnosis."""

        return f"{self.source} ({self.environment.value}) <- {self.produced_by}"


def collect(
    source: str,
    payload: Any,
    environment: Environment,
    *,
    pseudonymiser: Pseudonymiser,
) -> Evidence:
    """Take one observation in, allowlist it, label it, and sanitize it.

    This is the only way evidence enters an investigation. Sanitization happens here
    rather than at the point of use, because a caller who forgets at the point of use
    leaks, and there is no way to notice afterwards.
    """

    if source not in EVIDENCE_SOURCES:
        raise EvidenceRefused(
            f"'{source}' is not an allowed evidence source. This phase reads only from "
            "the allowlist in hm_oi.evidence.EVIDENCE_SOURCES. Allowed:\n"
            + "\n".join(f"  {key}" for key in sorted(EVIDENCE_SOURCES))
        )

    clean = sanitize(payload, pseudonymiser=pseudonymiser)

    # Last check, on the serialised form: if anything secret-shaped survived, the
    # sanitizer has a hole and the right answer is to stop, not to ship the evidence.
    leaked = find_secrets(repr(clean))
    if leaked:
        raise EvidenceRefused(
            f"Refusing evidence from '{source}': {', '.join(leaked)} survived "
            "sanitization. This is a hole in hm_oi.evidence, not a problem with the "
            "investigation."
        )

    return Evidence(source=source, environment=environment, payload=clean)


def assert_single_environment(items: list[Evidence]) -> Environment:
    """Refuse a diagnosis built from more than one environment.

    Mixing them is a defect, not a nuisance. Staging error rates next to production
    latency reads as one story and is two, and the reader cannot see the seam.
    """

    environments = {item.environment for item in items}
    if not environments:
        raise EvidenceRefused("There is no evidence here to draw a conclusion from.")
    if len(environments) > 1:
        raise EvidenceRefused(
            "This evidence comes from more than one environment: "
            + ", ".join(sorted(env.value for env in environments))
            + ". Separate the investigation by environment, or say plainly that the "
            "comparison is across environments and what that makes uncertain."
        )
    return environments.pop()


def refuse_live_production(environment: Environment, operation: str) -> None:
    """No live connection to production, ever, by any path."""

    if environment not in CONNECTABLE:
        raise EvidenceRefused(
            f"Refusing to {operation} against {environment.value}. Production evidence "
            "is read from an exported, sanitized snapshot only. Nothing here opens a "
            "connection to a production database, Redis, or service."
        )
