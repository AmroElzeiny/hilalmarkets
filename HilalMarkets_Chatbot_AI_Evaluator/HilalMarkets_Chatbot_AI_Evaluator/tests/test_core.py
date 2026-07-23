from pathlib import Path

from hm_chatbot_eval.compare import compare_runs
from hm_chatbot_eval.evaluate import semantic_field_metrics, validate_schema
from hm_chatbot_eval.scenarios import build_scenario
from hm_chatbot_eval.topics import TOPICS
from hm_chatbot_eval.util import get_path, redact, stable_hash


def test_topic_catalog_has_at_least_50_and_required_counts():
    assert len(TOPICS) >= 50
    assert all(20 <= t.default_cases <= 30 for t in TOPICS)
    assert all(t.criteria for t in TOPICS)


def test_scenarios_are_deterministic():
    a = build_scenario(TOPICS[0], 1, 42)
    b = build_scenario(TOPICS[0], 1, 42)
    assert a == b
    assert a.id == b.id


def test_schema_and_semantic_checks():
    schema = {"type": "object", "required": ["symbol"], "properties": {"symbol": {"type": "string"}}}
    assert validate_schema({"symbol": "BTCUSDT"}, schema) == []
    assert validate_schema({}, schema)
    metrics = semantic_field_metrics({"x": {"symbol": "BTCUSDT"}}, {"symbol": "BTCUSDT"}, {"symbol": "x.symbol"})
    assert metrics["mapped_field_accuracy"] == 1


def test_redaction_and_paths():
    value = {"Authorization": "Bearer abcdefghijklmnop", "nested": {"ok": 1}}
    safe = redact(value, {"authorization"})
    assert safe["Authorization"] == "[REDACTED]"
    assert get_path(value, "nested.ok") == 1
    assert stable_hash(value) == stable_hash(value)


def test_language_is_not_overweighted():
    language = [t for t in TOPICS if t.category == "language"]
    assert len(language) <= max(5, len(TOPICS) // 10)
    assert all(t.weight < 1 for t in language)
