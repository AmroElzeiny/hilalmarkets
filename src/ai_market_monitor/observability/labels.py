"""One vocabulary for operational metric labels, and one redaction rule.

Two separate failures made this module necessary, and both of them are the
duplicate-parser failure this repository keeps repeating.

The first is *cardinality*. A metric label whose value comes from a user id, a
symbol list, a raw error string or a free-text message produces one time series
per distinct value. That is unbounded by construction: it does not fail, it just
degrades the metric store until queries stop answering. A label is only safe when
the set of values it can take is closed, or bounded by a ceiling that raises the
moment it is crossed.

The second is *disclosure*. An operational record travels further than the
request that produced it: into a metric store, into an issue row, into an alert
body. Anything a caller passes has therefore left the application boundary. Raw
prompts, model output, Watch Plan text, API keys and authorization headers must
never be able to make that trip, so they are refused here rather than trusted to
be absent.

Both rules live in this one module. A caller that wants a new label adds it here,
where the value set is visible next to every other one, instead of writing its own
idea of what is safe.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

__all__ = [
    "ENUMERATED_LABELS",
    "IDENTIFIER_LABEL_BUDGET",
    "IDENTIFIER_LABELS",
    "MetricLabelError",
    "SensitiveValueError",
    "assert_no_sensitive_content",
    "known_label_names",
    "validate_labels",
]


class MetricLabelError(ValueError):
    """A label name or value that must never reach an emitted record."""


class SensitiveValueError(ValueError):
    """Content that must never leave the application inside a record."""


#: Labels whose values are a closed set. Anything outside the set is a bug in the
#: caller, not a new value to accept: an unknown outcome means the caller and this
#: vocabulary disagree about what happened, and silently recording it would hide
#: that disagreement behind a plausible-looking metric.
ENUMERATED_LABELS: Final[Mapping[str, frozenset[str]]] = {
    "status_class": frozenset({"2xx", "3xx", "4xx", "5xx"}),
    "outcome": frozenset(
        {
            "success",
            "failure",
            "timeout",
            "refused",
            "rate_limited",
            "unauthorized",
            "forbidden",
            "circuit_open",
            "cancelled",
            "skipped",
        }
    ),
    "circuit_state": frozenset({"closed", "open", "half_open"}),
    "method": frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}),
    "channel": frozenset({"in_app", "telegram", "email", "whatsapp"}),
    "job_phase": frozenset({"claimed", "run", "failed", "recovered", "abandoned"}),
    "health": frozenset({"healthy", "degraded", "down", "unknown"}),
    "delivery_result": frozenset({"delivered", "retryable", "permanent"}),
    "refusal_reason": frozenset(
        {
            "not_screened",
            "no_active_passport",
            "methodology_inactive",
            "universe_unresolved",
            "capability_unsupported",
            "out_of_domain",
            "provider_unavailable",
        }
    ),
    "review_stage": frozenset({"open", "overdue", "approved", "published"}),
    "cost_kind": frozenset({"estimated", "actual"}),
}

#: Labels whose values are names rather than a fixed list: a provider, a model, a
#: route template. Each value still has to look like an identifier, and each label
#: carries a ceiling on how many distinct values it may ever record. The ceiling is
#: the part that matters: it is what turns "somebody labelled by user id" from a
#: slow leak into an immediate, attributable failure.
IDENTIFIER_LABELS: Final[Mapping[str, int]] = {
    "component": 60,
    "route": 400,
    "provider": 20,
    "operation": 120,
    "model": 40,
    "feature": 60,
    "queue": 20,
    "task": 120,
    "slo": 60,
    "exchange": 20,
    "timeframe": 24,
}

#: The single ceiling used when a caller asks about a label generically.
IDENTIFIER_LABEL_BUDGET: Final[int] = max(IDENTIFIER_LABELS.values())

#: A leading ``/`` is allowed because a route template is the commonest identifier
#: label and every one of them starts with it. Everything after the first character
#: stays restricted to identifier shapes, so a path is still refused the moment it
#: carries a query string, whitespace or an interpolated value.
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9/][A-Za-z0-9_./{}:-]{0,79}$"
)

#: A label *name* that promises to carry something unbounded or private. Refused by
#: name, before any value is inspected, so the mistake is caught at the call site
#: that introduced it rather than on the one request whose value happens to look
#: dangerous.
_FORBIDDEN_LABEL_NAME_PARTS: Final[tuple[str, ...]] = (
    "user",
    "email",
    "prompt",
    "message",
    "text",
    "token",
    "key",
    "secret",
    "password",
    "authorization",
    "header",
    "symbol",
    "plan_text",
    "content",
    "body",
    "phrase",
    "query",
)

#: Values that are never a label, whatever the label is called. A UUID is a user or
#: a row; an email is a customer; a long hex run is a hash or a key. Each of these
#: shapes is both unbounded and identifying, which is the exact combination a label
#: must never have.
_UUID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_LONG_HEX_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{24,}$")

#: Credential and payload shapes refused anywhere in a record, not only in labels.
#: These are matched against values that a caller believed were safe to attach, so
#: the patterns describe the *secret*, not the field it arrived in.
_SENSITIVE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{8,}")),
    ("basic_credentials", re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}")),
    ("telegram_bot_token", re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}")),
    ("json_web_token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")),
    ("private_key_block", re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("email_address", _EMAIL_PATTERN),
)

#: A seed phrase is twelve or more ordinary lowercase words in a row. Matching the
#: *shape* rather than a wordlist is deliberate: the BIP-39 list is not the only one
#: in use, and a record has no business carrying a dozen bare words regardless.
_SEED_PHRASE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:[a-z]{3,8}\s+){11,}[a-z]{3,8}\b"
)

#: Anything longer than this is prose, and prose in an operational record is a
#: prompt, a model reply or a customer's own words. None of the three belongs here,
#: so length alone is enough to refuse it.
_MAX_RECORD_VALUE_LENGTH: Final[int] = 200


def known_label_names() -> frozenset[str]:
    """Every label name any metric is allowed to carry."""

    return frozenset(ENUMERATED_LABELS) | frozenset(IDENTIFIER_LABELS)


def _validate_label_name(name: str) -> None:
    if name not in known_label_names():
        raise MetricLabelError(
            f"Unknown metric label {name!r}. Add it to observability.labels rather than "
            "attaching an ad-hoc label at the call site."
        )
    lowered = name.casefold()
    for part in _FORBIDDEN_LABEL_NAME_PARTS:
        if part in lowered:
            raise MetricLabelError(
                f"Metric label {name!r} names an unbounded or identifying value."
            )


def _validate_identifier_value(name: str, value: str) -> None:
    if not _IDENTIFIER_PATTERN.match(value):
        raise MetricLabelError(
            f"Metric label {name}={value!r} is not a bounded identifier."
        )
    if _UUID_PATTERN.match(value):
        raise MetricLabelError(f"Metric label {name} must not carry a UUID.")
    if _LONG_HEX_PATTERN.match(value):
        raise MetricLabelError(f"Metric label {name} must not carry a hash or key.")
    if _EMAIL_PATTERN.search(value):
        raise MetricLabelError(f"Metric label {name} must not carry an email address.")


def validate_labels(
    labels: Mapping[str, str],
    *,
    seen_values: Mapping[str, set[str]] | None = None,
) -> dict[str, str]:
    """Return the labels unchanged, or raise naming the first rule they break.

    ``seen_values`` is the recorder's running record of which values each label has
    already taken. It is what enforces the ceiling: without it each value looks
    individually reasonable, and a label by user id is only recognisable once the
    number of distinct values has grown past anything a real dimension could have.
    """

    validated: dict[str, str] = {}
    for name, raw in labels.items():
        _validate_label_name(name)
        value = str(raw)
        if not value:
            raise MetricLabelError(f"Metric label {name} must not be empty.")
        allowed = ENUMERATED_LABELS.get(name)
        if allowed is not None:
            if value not in allowed:
                raise MetricLabelError(
                    f"Metric label {name}={value!r} is not one of the declared values "
                    f"{sorted(allowed)}."
                )
        else:
            _validate_identifier_value(name, value)
            budget = IDENTIFIER_LABELS[name]
            if seen_values is not None:
                observed = seen_values.get(name, set())
                if value not in observed and len(observed) >= budget:
                    raise MetricLabelError(
                        f"Metric label {name} exceeded its cardinality budget of {budget} "
                        "distinct values. A label this wide is carrying an identifier."
                    )
        validated[name] = value
    return validated


def assert_no_sensitive_content(value: object, *, field: str) -> None:
    """Raise when a value carries a secret, a prompt, model output or customer text.

    Applied to everything an operational record holds — label values, numeric units,
    issue summaries, alert bodies. A record is allowed to say *that* a provider call
    failed with an unauthorized status. It is not allowed to say what was sent.
    """

    if isinstance(value, Mapping):
        for key, item in value.items():
            assert_no_sensitive_content(str(key), field=f"{field}.key")
            assert_no_sensitive_content(item, field=f"{field}.{key}")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            assert_no_sensitive_content(item, field=field)
        return
    if not isinstance(value, str):
        return
    if len(value) > _MAX_RECORD_VALUE_LENGTH:
        raise SensitiveValueError(
            f"{field} is {len(value)} characters. An operational record carries codes and "
            "counts, never prose, a prompt or a model reply."
        )
    for label, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(value):
            raise SensitiveValueError(f"{field} appears to contain a {label}.")
    if _SEED_PHRASE_PATTERN.search(value):
        raise SensitiveValueError(f"{field} appears to contain a seed phrase.")
