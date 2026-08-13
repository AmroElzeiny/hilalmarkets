"""Remove secrets from anything this harness writes down or sends onward.

Every audit record, every branch name, every commit message and every prompt passes
through :func:`redact`. The rule is one-directional and deliberately blunt: it is far
better to redact a harmless string than to write a live key into a file that gets
committed, or into a prompt that gets posted to a model provider.

**Why this file duplicates patterns the product already has.**
``ai_market_monitor.observability.labels`` holds a very similar list, and this module
cannot import it: ``scripts/check_oi_boundary.py`` fails the build if ``hm_oi`` imports
the product or the product imports ``hm_oi``, which is what keeps this AGPL-licensed
tool out of the shipped code. So the vocabulary genuinely cannot be shared as code.

Two copies of a pattern list is the exact failure ``CLAUDE.md`` warns about — two readers
each understanding a different subset. The resolution here is a shared *contract* instead
of shared code: ``tests/oi/test_invariant_builder_redaction.py`` asserts that every secret
shape the product refuses, this module also redacts. The copies may not drift, because a
test fails when they do.

**What this cannot do.** It matches shapes. A secret that looks like ordinary prose is not
caught, and nothing here should be described as making a transcript safe to publish. It
makes an *accident* — a pasted key, a seed phrase in a fixture — stop being propagated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

#: Marker written in place of a secret. Deliberately loud: a reader scanning an audit
#: record should see immediately that something was removed rather than wonder.
PLACEHOLDER: Final[str] = "[REDACTED:{kind}]"


@dataclass(frozen=True, slots=True)
class SecretPattern:
    """One shape of secret, and the name reported when it is found."""

    kind: str
    pattern: re.Pattern[str]


#: Ordered most specific first. ``seed_phrase`` sits last on purpose: it is the broadest
#: rule and would otherwise swallow the text around a more precisely named secret, which
#: would hide *which* kind of secret was present from the person reading the log.
SECRET_PATTERNS: Final[tuple[SecretPattern, ...]] = (
    SecretPattern(
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    # A truncated block still proves a key was pasted, and the closing line is often
    # lost when text is copied out of a terminal.
    SecretPattern(
        "private_key_header",
        re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ),
    SecretPattern("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    SecretPattern("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    SecretPattern(
        "json_web_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
    ),
    SecretPattern("telegram_bot_token", re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")),
    SecretPattern("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    SecretPattern("basic_credentials", re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}")),
    # An extended private key. Distinct from a seed phrase and far more specific.
    SecretPattern("extended_private_key", re.compile(r"\b(?:xprv|yprv|zprv)[A-Za-z0-9]{20,}\b")),
    SecretPattern(
        "secret_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|authorization|cookie|"
            r"session[_-]?key|private[_-]?key)\s*[:=]\s*[\"']?([^\s,;\"']{6,})"
        ),
    ),
    #: Twelve or more ordinary lowercase words in a row. The shape is matched rather
    #: than the BIP-39 wordlist, because BIP-39 is not the only list in use and because
    #: a log line has no business carrying a dozen bare words in any case.
    SecretPattern("seed_phrase", re.compile(r"\b(?:[a-z]{3,8}\s+){11,}[a-z]{3,8}\b")),
)


def find_secrets(value: Any) -> tuple[str, ...]:
    """Which kinds of secret appear in this text. Never returns the secret itself."""

    text = str(value or "")
    return tuple(
        pattern.kind for pattern in SECRET_PATTERNS if pattern.pattern.search(text)
    )


def contains_secret(value: Any) -> bool:
    return bool(find_secrets(value))


def redact(value: Any, *, limit: int | None = None) -> str:
    """Return the text with every recognised secret replaced.

    ``limit`` truncates afterwards. Truncation happens *after* redaction on purpose:
    cutting first could split a key in half and leave the first half readable while the
    pattern no longer matches it.
    """

    text = str(value or "")
    for secret in SECRET_PATTERNS:
        text = secret.pattern.sub(PLACEHOLDER.format(kind=secret.kind), text)
    if limit is not None and len(text) > limit:
        text = text[:limit] + "...[truncated]"
    return text


def redact_structure(value: Any, *, limit: int | None = 2000) -> Any:
    """Redact every string inside a nested structure, keeping its shape.

    Audit records are dictionaries of lists of dictionaries. Redacting only the top level
    would leave a key sitting one level down, which is precisely where a payload ends up.
    """

    if isinstance(value, str):
        return redact(value, limit=limit)
    if isinstance(value, dict):
        return {str(key): redact_structure(item, limit=limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_structure(item, limit=limit) for item in value]
    return value


class SecretLeak(RuntimeError):
    """Raised when a secret reaches something that must never carry one.

    Used for the places where redacting quietly would be wrong — a commit message or a
    pull-request body. There, the correct outcome is to stop and make a person look,
    not to write a tidy ``[REDACTED]`` marker and carry on as though nothing happened.
    """


def refuse_if_secret(value: Any, *, where: str) -> str:
    """Return the text unchanged, or refuse it outright.

    ``where`` names the destination in plain words, because this message is read by
    somebody who is mid-task and needs to know what to do next.
    """

    kinds = find_secrets(value)
    if kinds:
        raise SecretLeak(
            f"Refusing to write to {where}: it appears to contain "
            f"{', '.join(kinds)}. Nothing was written. Remove the secret from the "
            "source material and run the task again."
        )
    return str(value or "")
