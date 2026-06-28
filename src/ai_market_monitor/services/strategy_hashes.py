import hashlib
import json
from typing import Any

from ai_market_monitor.schemas.strategy import StrategyDefinition


def stored_schema_hash(schema_json: dict[str, Any]) -> str:
    payload = json.dumps(
        schema_json,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ensure_current_approved_schema_hash(version: Any, definition: StrategyDefinition) -> bool:
    current_hash = definition.canonical_hash()
    if version.schema_hash == current_hash:
        return True
    raw_hash = stored_schema_hash(version.schema_json)
    if (
        version.approved_at is not None
        and version.approved_schema_hash == version.schema_hash
        and raw_hash == version.schema_hash
    ):
        version.schema_json = definition.model_dump(mode="json")
        version.schema_hash = current_hash
        version.approved_schema_hash = current_hash
        return True
    return False
