from __future__ import annotations

import re
from typing import Any

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|token|authorization|cookie|session)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_customer_text(value: str, *, limit: int = 20_000) -> str:
    """Redact known credential shapes without reinterpreting customer language."""

    text = _PRIVATE_KEY.sub("[PRIVATE KEY REDACTED]", str(value or ""))
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text[:limit]


def safe_telemetry(value: dict[str, Any] | None) -> dict[str, Any]:
    """Allowlist operational measurements; never return prompts or provider payloads."""

    source = dict(value or {})
    allowed = {
        "model",
        "latency_ms",
        "turn_duration_ms",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "estimated_cost_usd",
        "model_call_count",
        "failure_code",
        "failure_stage",
        "status",
    }
    return {key: source[key] for key in allowed if key in source}
