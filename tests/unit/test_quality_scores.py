from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ai_market_monitor.cockpit_service import _decay_findings
from ai_market_monitor.db.models import Alert
from ai_market_monitor.db.models.enums import AlertType
from ai_market_monitor.engine.quality import (
    alert_trust_score_from_proof,
    market_coverage_score,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.alert_presentation import AlertPresentation
from ai_market_monitor.strategy_cockpit import (
    forecast_from_structure,
    schema_diff,
    suggest_schema_adjustment,
    validate_strategy_conflicts,
)
from tests.factories import load_strategy


def test_alert_trust_score_is_deterministic_and_explainable():
    proof = {
        "conditions": [
            {"name": "Above EMA 200", "state": "passed", "mandatory": True},
            {"name": "Volume expansion", "state": "failed", "mandatory": True},
            {"name": "Sweep confirmation", "state": "passed", "mandatory": False},
        ],
        "data_latency_ms": 1200,
        "candle_closed": True,
        "liquidity_information": {
            "spread_bps": 4,
            "quote_volume_24h": 2_500_000,
        },
        "risk_validation": {"state": "passed"},
        "risk_calculation": {"stop_price": 98},
        "reward_to_risk": 2.4,
    }

    first = alert_trust_score_from_proof(proof)
    second = alert_trust_score_from_proof(proof)

    assert first == second
    assert first["deterministic"] is True
    assert 0 < first["score"] <= 100
    assert {factor["name"] for factor in first["factors"]} >= {
        "Mandatory rule pass rate",
        "Data freshness",
        "Market liquidity",
        "Risk validity",
    }


def test_market_coverage_score_zero_when_nothing_scanned():
    score = market_coverage_score(
        symbols_eligible=0,
        symbols_scanned=0,
        timeframes_required=1,
        timeframes_covered=0,
    )

    assert score["score"] == 0
    assert score["coverage_percentage"] == 0
    assert score["factors"][0]["status"] == "missing"


def test_market_coverage_score_penalizes_stale_scan():
    recent = market_coverage_score(
        symbols_eligible=10,
        symbols_scanned=10,
        data_failures=0,
        timeframes_required=1,
        timeframes_covered=1,
        last_scan_at=datetime.now(UTC),
    )
    stale = market_coverage_score(
        symbols_eligible=10,
        symbols_scanned=10,
        data_failures=0,
        timeframes_required=1,
        timeframes_covered=1,
        last_scan_at=datetime.now(UTC) - timedelta(hours=2),
    )

    assert stale["score"] == recent["score"] - 10
    assert stale["warnings"] == ["Last scan is older than one hour."]


def test_conflict_detector_finds_duplicates_and_impossible_thresholds():
    payload = load_strategy().model_dump(mode="json")
    first = deepcopy(payload["conditions"]["children"][1])
    first["key"] = "volume_minimum"
    first["left"] = {
        "kind": "market_metric",
        "name": "volume_multiplier",
        "parameters": {"period": 20},
    }
    first["comparator"] = "gte"
    first["right"] = {"kind": "constant", "value": 2.0}
    duplicate = deepcopy(first)
    duplicate["key"] = "volume_minimum_duplicate"
    upper = deepcopy(first)
    upper["key"] = "volume_maximum"
    upper["label"] = "Volume below one times average"
    upper["comparator"] = "lte"
    upper["right"] = {"kind": "constant", "value": 1.0}
    payload["conditions"]["children"] = [first, duplicate, upper]
    definition = StrategyDefinition.model_validate(payload)

    findings = validate_strategy_conflicts(definition)
    codes = {finding.code for finding in findings}

    assert "duplicate_condition" in codes
    assert "contradictory_thresholds" in codes
    assert any(finding.severity == "critical" for finding in findings)


def test_frequency_forecast_is_deterministic_and_cautious():
    strategy = load_strategy()

    first = forecast_from_structure(
        strategy,
        historical_matches=4,
        observation_days=14,
        symbols_observed=100,
    )
    second = forecast_from_structure(
        strategy,
        historical_matches=4,
        observation_days=14,
        symbols_observed=100,
    )

    assert first == second
    assert first["estimated_min_per_week"] <= first["estimated_max_per_week"]
    assert first["confidence"] == "medium"
    assert "profit" not in str(first).lower()


def test_safe_suggestion_produces_schema_valid_reviewable_diff():
    strategy = load_strategy()

    proposed, reason = suggest_schema_adjustment(
        strategy,
        "add_volume_confirmation",
    )
    changes = schema_diff(strategy, proposed)

    assert proposed.canonical_hash() != strategy.canonical_hash()
    assert any(change["section"] == "conditions" for change in changes)
    assert "volume" in reason.lower()
    assert proposed.alerts.channels == strategy.alerts.channels


def test_explain_bottleneck_does_not_mutate_schema():
    strategy = load_strategy()

    proposed, reason = suggest_schema_adjustment(
        strategy,
        "explain_bottleneck",
        bottleneck_key="volume_multiplier",
    )

    assert proposed.canonical_hash() == strategy.canonical_hash()
    assert "volume_multiplier" in reason


def test_compact_alert_actions_include_feedback_replay_and_improvement():
    alert_id = uuid4()
    alert = Alert(
        id=alert_id,
        user_id=uuid4(),
        strategy_version_id=uuid4(),
        alert_type=AlertType.CONFIRMED,
        deduplication_key="cockpit-action-test",
        title="SOL/USDT confirmed",
        body="Proof attached.",
        proof_receipt={
            "strategy_name": "Test monitor",
            "strategy_version": "1",
            "symbol": "SOL/USDT",
            "conditions": [],
        },
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    presentation = AlertPresentation.from_alert(
        alert,
        public_base_url="https://example.test",
    )
    labels = {action.label for action in presentation.actions}

    assert {"Good Alert", "Too Early", "Too Late", "False Alert"}.issubset(labels)
    assert {"Open Replay", "Improve Monitor", "View Full Proof"}.issubset(labels)
    assert any(
        action.action_id == f"feedback:false_alert:{alert_id}"
        for action in presentation.actions
    )


def test_research_only_alert_copy_does_not_show_entry_or_rr_context():
    alert = Alert(
        id=uuid4(),
        user_id=uuid4(),
        strategy_version_id=uuid4(),
        alert_type=AlertType.CONFIRMED,
        deduplication_key="research-copy-test",
        title="SOL/USDT confirmed",
        body="Proof attached.",
        proof_receipt={
            "strategy_name": "Research monitor",
            "strategy_version": "1",
            "symbol": "SOL/USDT",
            "exchange": "binance",
            "timeframe": "15m",
            "setup_completion_score": 100,
            "required_completion_percent": 100,
            "conditions": [
                {
                    "condition_id": "rsi_below_30",
                    "name": "RSI below 30",
                    "state": "passed",
                    "actual_value": 24,
                    "required_value": 30,
                }
            ],
        },
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    text = AlertPresentation.from_alert(alert).telegram_text()

    assert "Research match confirmed" in text
    assert "Research-only monitor" in text
    assert "Entry zone:" not in text
    assert "R:R:" not in text


def test_strategy_decay_detector_flags_spike_and_universe_shrinkage():
    current = {
        "scan_count": 20,
        "confirmed_count": 8,
        "error_count": 0,
        "alert_count": 8,
        "invalidation_count": 1,
        "universe_size": 20,
        "last_alert_at": datetime.now(UTC).isoformat(),
    }
    baseline = {
        "scan_count": 200,
        "confirmed_count": 8,
        "error_count": 0,
        "alert_count": 4,
        "invalidation_count": 4,
        "universe_size": 100,
        "last_alert_at": datetime.now(UTC).isoformat(),
    }

    events = _decay_findings(current, baseline, datetime.now(UTC))
    event_types = {event["event_type"] for event in events}

    assert "alert_spike" in event_types
    assert "universe_shrinkage" in event_types
