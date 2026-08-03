from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Fresh authenticated backend and browser sessions deliberately receive different
# database, message, source-turn and snapshot identities. They are evidence for the
# same trader wording, not a different semantic strategy. The evaluator compares a
# normalized contract so it reports a real route disagreement instead of treating
# server-owned provenance as a product difference.
_EVALUATION_TRANSIENT_KEYS = frozenset(
    {
        "id",
        "draft_id",
        "source_turn_id",
        "source_segment_id",
        "target_condition_id",
        "operation_id",
        "condition_id",
        "node_id",
        "requirement_id",
        "unresolved_id",
        "snapshot_id",
        "assistant_message_id",
        "message_id",
        "session_id",
        "chat_session_id",
        "client_message_id",
        "strategy_id",
        "strategy_version_id",
        "approved_version_id",
        "reviewed_snapshot_id",
        "schema_hash",
        "canonical_hash",
        "workflow_state_hash",
        "conversation_snapshot_hash",
        "immutable_version_hash",
        "approved_at",
        "created_at",
        "updated_at",
        "timestamp",
        "start_offset",
        "end_offset",
    }
)
_UNORDERED_EVALUATION_LIST_KEYS = frozenset(
    {
        "requirement_states",
        "semantic_role_assignments",
        "unresolved_fields",
        "unsupported_requirements",
    }
)
_GENERATED_CONDITION_REF = re.compile(r"condition_[0-9a-f]{8,}")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalized_evaluation_contract(value: Any, *, _parent_key: str | None = None) -> Any:
    """Remove only server/session identities from an evaluator semantic comparison.

    This is intentionally not a redaction function. Trader-controlled values,
    capability identity, governed Sharia identity, formulas, thresholds, timeframe
    roles, approval state and executable hashes remain comparable. Only IDs and hashes
    whose contract is inherently tied to a fresh authenticated session are removed.
    """

    if isinstance(value, dict):
        normalized_dict = {
            key: normalized_evaluation_contract(item, _parent_key=key)
            for key, item in value.items()
            if key not in _EVALUATION_TRANSIENT_KEYS
        }
        return normalized_dict
    if isinstance(value, list):
        normalized_list = [
            normalized_evaluation_contract(item, _parent_key=_parent_key) for item in value
        ]
        if _parent_key in _UNORDERED_EVALUATION_LIST_KEYS:
            return sorted(
                normalized_list,
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            )
        return normalized_list
    if isinstance(value, str):
        # Generated condition IDs can appear in boolean membership and target paths;
        # the tree shape and every non-generated field remain in the comparison.
        return _GENERATED_CONDITION_REF.sub("condition_<generated>", value)
    return value


def semantic_contract_hash(value: Any) -> str:
    """Stable hash of the normalized backend/UI contract."""

    return stable_hash(normalized_evaluation_contract(value))


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
