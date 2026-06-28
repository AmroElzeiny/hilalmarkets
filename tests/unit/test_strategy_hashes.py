from datetime import UTC, datetime
from types import SimpleNamespace

from ai_market_monitor.services.strategy_hashes import (
    ensure_current_approved_schema_hash,
    stored_schema_hash,
)
from tests.factories import load_strategy


def test_approved_schema_hash_repairs_legacy_normalization_drift():
    definition = load_strategy()
    legacy_schema = definition.model_dump(mode="json", exclude_none=True)
    legacy_hash = stored_schema_hash(legacy_schema)
    version = SimpleNamespace(
        schema_json=legacy_schema,
        schema_hash=legacy_hash,
        approved_schema_hash=legacy_hash,
        approved_at=datetime.now(UTC),
    )

    assert ensure_current_approved_schema_hash(version, definition) is True
    assert version.schema_hash == definition.canonical_hash()
    assert version.approved_schema_hash == definition.canonical_hash()
    assert version.schema_json == definition.model_dump(mode="json")


def test_approved_schema_hash_rejects_mutated_payload():
    definition = load_strategy()
    legacy_schema = definition.model_dump(mode="json", exclude_none=True)
    legacy_hash = stored_schema_hash(legacy_schema)
    mutated_schema = {**legacy_schema, "name": "Mutated without approval"}
    version = SimpleNamespace(
        schema_json=mutated_schema,
        schema_hash=legacy_hash,
        approved_schema_hash=legacy_hash,
        approved_at=datetime.now(UTC),
    )

    assert ensure_current_approved_schema_hash(version, definition) is False
    assert version.schema_hash == legacy_hash
    assert version.approved_schema_hash == legacy_hash
