import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_market_monitor.services.setup_chat_evaluation import (
    build_setup_chat_evaluation_contract,
)
from hm_chatbot_eval.compare import compare_runs
from hm_chatbot_eval.config import Settings, process_openai_key_overrides_dotenv
from hm_chatbot_eval.doctor import checks
from hm_chatbot_eval.evaluate import semantic_field_metrics, validate_schema
from hm_chatbot_eval.models import CaseResult, ScenarioContract
from hm_chatbot_eval.report import aggregate
from hm_chatbot_eval.scenarios import build_scenario
from hm_chatbot_eval.topics import TOPICS
from hm_chatbot_eval.util import get_path, redact, stable_hash
from tests.factories import load_strategy


def test_topic_catalog_has_at_least_50_and_required_counts():
    assert len(TOPICS) >= 50
    assert all(20 <= topic.default_cases <= 30 for topic in TOPICS)
    assert all(topic.criteria for topic in TOPICS)


def test_scenarios_are_deterministic():
    left = build_scenario(TOPICS[0], 1, 42)
    right = build_scenario(TOPICS[0], 1, 42)
    assert left == right
    assert left.id == right.id
    assert isinstance(left.expected_contract, ScenarioContract)


def test_doctor_keeps_unconfigured_drift_honestly_not_measured():
    settings = Settings(
        _env_file=None,
        openai_api_key="",
        target_mode="ui",
        target_variants_json="[]",
    )
    drift_check = next(item for item in checks(settings) if item[0] == "Drift variants")

    assert drift_check[1] is True
    assert "NOT_MEASURED" in drift_check[2]


def test_schema_and_semantic_checks():
    schema = {
        "type": "object",
        "required": ["symbol"],
        "properties": {"symbol": {"type": "string"}},
    }
    assert validate_schema({"symbol": "BTCUSDT"}, schema) == []
    assert validate_schema({}, schema)
    metrics = semantic_field_metrics(
        {"symbols": ["BTCUSDT"], "thresholds": [5.0]},
        {"symbol": "BTCUSDT", "threshold_percent": 5},
        {
            "symbol": {"path": "symbols", "match": "contains"},
            "threshold_percent": {
                "path": "thresholds",
                "match": "contains_numeric",
            },
        },
    )
    assert metrics["mapped_field_accuracy"] == 1


def test_redaction_and_paths():
    value = {"Authorization": "Bearer abcdefghijklmnop", "nested": {"ok": 1}}
    safe = redact(value, {"authorization"})
    assert safe["Authorization"] == "[REDACTED]"
    assert get_path(value, "nested.ok") == 1
    assert stable_hash(value) == stable_hash(value)


def test_language_is_not_overweighted():
    language = [topic for topic in TOPICS if topic.category == "language"]
    assert len(language) <= max(5, len(TOPICS) // 10)
    assert all(topic.weight < 1 for topic in language)


def test_exported_contract_matches_validated_strategy_and_canvas():
    strategy = load_strategy()
    contract = build_setup_chat_evaluation_contract(
        strategy,
        session_status="ready_for_approval",
        approval_eligible=True,
        assumptions=["Test assumption"],
        confidence=[{"rule_key": "relative_volume", "score": 0.91}],
        unsupported_capabilities=[],
    )
    schema = json.loads(
        Path("tests/evaluator/contracts/setup_chat_evaluation_contract.schema.json").read_text(
            encoding="utf-8"
        )
    )
    errors = list(Draft202012Validator(schema).iter_errors(contract.model_dump(mode="json")))
    assert errors == []
    assert contract.strategy == strategy
    assert contract.canonical_hash == strategy.canonical_hash()
    assert {item.id for item in contract.canvas.nodes} == {
        "group:entry_conditions",
        "condition:price_above_4h_ema_200",
        "condition:bullish_liquidity_sweep",
        "condition:relative_volume",
    }
    assert len(contract.canvas.edges) == len(contract.canvas.nodes) - 1
    assert contract.requires_explicit_approval is True
    assert contract.sharia_status_assignment_authorized is False
    assert contract.approval.approved is False
    assert contract.approval.terminal is False


def test_canvas_contract_rejects_include_exclude_overlap() -> None:
    strategy = load_strategy()
    symbol = strategy.universe.include_symbols[0]
    unsafe = strategy.model_copy(
        update={"universe": strategy.universe.model_copy(update={"exclude_symbols": [symbol]})}
    )
    with pytest.raises(ValueError, match="disjoint"):
        build_setup_chat_evaluation_contract(
            unsafe,
            session_status="ready_for_approval",
            approval_eligible=True,
            assumptions=[],
            confidence=[],
            unsupported_capabilities=[],
        )


def test_canonical_field_map_covers_the_required_contract_surface():
    field_map = json.loads(
        Path("tests/evaluator/contracts/field_map.json").read_text(encoding="utf-8")
    )
    required = {
        "universe",
        "symbols",
        "exclusions",
        "direction",
        "timeframes",
        "operators",
        "thresholds",
        "nested_groups",
        "filters",
        "alerts",
        "assumptions",
        "confidence",
        "unsupported_capabilities",
        "provider_required_capabilities",
        "approval_state",
        "strategy_version",
        "version_hash",
        "canonical_hash",
        "canvas_nodes",
        "canvas_groups",
        "canvas_edges",
    }
    assert required <= set(field_map)


def test_compare_runs_symbol_remains_importable():
    assert callable(compare_runs)


def test_process_openai_key_override_detection_is_redacted(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=project-key\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "global-key")
    assert process_openai_key_overrides_dotenv(env_file) is True

    monkeypatch.setenv("OPENAI_API_KEY", "project-key")
    assert process_openai_key_overrides_dotenv(env_file) is False

    monkeypatch.delenv("OPENAI_API_KEY")
    assert process_openai_key_overrides_dotenv(env_file) is False
    assert "project-key" not in repr(process_openai_key_overrides_dotenv(env_file))


def test_target_authentication_requires_credentials_or_nonempty_cookie():
    empty = Settings(
        _env_file=None,
        target_backend_email="",
        target_backend_password="",
        target_session_cookie="",
    )
    assert empty.target_session_cookie is None
    assert empty.target_authentication_configured is False

    credentials = Settings(
        _env_file=None,
        target_backend_email="evaluator@example.test",
        target_backend_password="secret",
    )
    assert credentials.target_authentication_configured is True

    cookie = Settings(_env_file=None, target_session_cookie="short-lived-session")
    assert cookie.target_authentication_configured is True


def test_errored_cases_fail_instead_of_appearing_pending():
    scenario = build_scenario(TOPICS[0], 1, 42)
    case = CaseResult(
        run_id="failed-run",
        scenario=scenario,
        target_kind="backend",
        target_variant="current",
        started_at="2026-07-23T00:00:00Z",
        finished_at="2026-07-23T00:00:01Z",
        turns=[],
        deterministic_metrics={},
        judge=None,
        structured_output=None,
        structured_hash=None,
        schema_errors=[],
        total_latency_ms=0,
        target_cost_usd=0,
        test_ai_cost_usd=0,
        passed=False,
        error="HTTPStatusError: 401 Unauthorized",
    )
    summary = aggregate([case])
    assert summary["release_gate"] == "FAIL"
    assert summary["errored_cases"] == 1
    assert summary["pending_judges"] == 0
