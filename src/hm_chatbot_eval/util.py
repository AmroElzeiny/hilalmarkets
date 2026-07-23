from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_path(value: Any, path: str, default: Any = None) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        elif isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        else:
            return default
    return current


def set_path(value: dict[str, Any], path: str, replacement: Any) -> None:
    parts = path.split(".")
    current: Any = value
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = replacement


def render_template(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        result = value
        for key, replacement in variables.items():
            result = result.replace("{{" + key + "}}", replacement)
        return result
    if isinstance(value, list):
        return [render_template(x, variables) for x in value]
    if isinstance(value, dict):
        return {k: render_template(v, variables) for k, v in value.items()}
    return value


def redact(value: Any, sensitive_keys: set[str]) -> Any:
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            key_lower = str(key).lower()
            output[key] = (
                "[REDACTED]"
                if any(s in key_lower for s in sensitive_keys)
                else redact(item, sensitive_keys)
            )
        return output
    if isinstance(value, list):
        return [redact(x, sensitive_keys) for x in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)(sk-[A-Za-z0-9_-]{12,})", "[REDACTED_API_KEY]", value)
    return value


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
