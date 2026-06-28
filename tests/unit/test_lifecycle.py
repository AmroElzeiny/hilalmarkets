from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_market_monitor.db.models import SetupInstance
from ai_market_monitor.db.models.enums import SetupLifecycleState
from ai_market_monitor.services.lifecycle import InvalidLifecycleTransition, transition_setup
from ai_market_monitor.services.lifecycle_dashboard import LIFECYCLE_STAGES, stage_index


def setup_instance(state: SetupLifecycleState) -> SetupInstance:
    now = datetime.now(UTC)
    return SetupInstance(
        id=uuid4(),
        user_id=uuid4(),
        strategy_version_id=uuid4(),
        exchange="binance",
        symbol="SOL/USDT",
        timeframe="15m",
        setup_key="swing-low-20260614T1200",
        state=state,
        completion_score=80,
        first_detected_at=now,
        last_evaluated_at=now,
    )


def test_lifecycle_transition_creates_persistent_event():
    setup = setup_instance(SetupLifecycleState.FORMING)
    event = transition_setup(
        setup,
        SetupLifecycleState.NEAR_CONFIRMATION,
        reason_code="completion_score_crossed",
        evidence={"score": 86},
    )
    assert setup.state == SetupLifecycleState.NEAR_CONFIRMATION
    assert event.setup_instance_id == setup.id
    assert event.from_state == SetupLifecycleState.FORMING
    assert event.to_state == SetupLifecycleState.NEAR_CONFIRMATION


def test_terminal_lifecycle_state_cannot_reopen():
    setup = setup_instance(SetupLifecycleState.EXPIRED)
    with pytest.raises(InvalidLifecycleTransition, match="terminal"):
        transition_setup(
            setup,
            SetupLifecycleState.FORMING,
            reason_code="late_data",
        )


def test_partial_targets_remain_open_until_final_target():
    setup = setup_instance(SetupLifecycleState.ENTRY_TOUCHED)
    first = transition_setup(
        setup,
        SetupLifecycleState.TARGET_1_REACHED,
        reason_code="target_reached",
        evidence={"target_index": 1, "target_count": 3},
    )
    assert first.to_state == SetupLifecycleState.TARGET_1_REACHED
    assert setup.closed_at is None

    second = transition_setup(
        setup,
        SetupLifecycleState.TARGET_2_REACHED,
        reason_code="target_reached",
        evidence={"target_index": 2, "target_count": 3},
    )
    assert second.to_state == SetupLifecycleState.TARGET_2_REACHED
    assert setup.closed_at is None

    transition_setup(
        setup,
        SetupLifecycleState.TARGET_REACHED,
        reason_code="final_target_reached",
        evidence={"target_index": 3, "target_count": 3},
    )
    assert setup.closed_at is not None


def test_confirmed_setup_can_record_suppression_then_alert_delivery():
    setup = setup_instance(SetupLifecycleState.CONFIRMED)

    suppressed = transition_setup(
        setup,
        SetupLifecycleState.SUPPRESSED,
        reason_code="strategy_cooldown",
    )
    delivered = transition_setup(
        setup,
        SetupLifecycleState.ALERT_SENT,
        reason_code="alert_created",
    )

    assert suppressed.to_state == SetupLifecycleState.SUPPRESSED
    assert delivered.from_state == SetupLifecycleState.SUPPRESSED
    assert setup.state == SetupLifecycleState.ALERT_SENT


def test_default_lifecycle_stages_are_research_monitoring_first():
    labels = [label.lower() for _, label in LIFECYCLE_STAGES]

    assert "entry zone" not in labels
    assert "conditions complete" in labels
    assert stage_index(SetupLifecycleState.CONFIRMED) == 2
    assert stage_index(SetupLifecycleState.ALERT_SENT) == 3
