from datetime import UTC, datetime

from ai_market_monitor.db.models import SetupInstance, SetupLifecycleEvent
from ai_market_monitor.db.models.enums import (
    ALLOWED_SETUP_TRANSITIONS,
    TERMINAL_SETUP_STATES,
    SetupLifecycleState,
)


class InvalidLifecycleTransition(ValueError):
    pass


def transition_setup(
    setup: SetupInstance,
    target: SetupLifecycleState,
    *,
    reason_code: str,
    evidence: dict | None = None,
    occurred_at: datetime | None = None,
) -> SetupLifecycleEvent:
    current = setup.state
    if current in TERMINAL_SETUP_STATES:
        raise InvalidLifecycleTransition(f"{current.value} is terminal")
    allowed = ALLOWED_SETUP_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidLifecycleTransition(f"Cannot transition {current.value} to {target.value}")
    timestamp = occurred_at or datetime.now(UTC)
    setup.state = target
    setup.last_evaluated_at = timestamp
    if target == SetupLifecycleState.CONFIRMED:
        setup.confirmed_at = timestamp
    if target in TERMINAL_SETUP_STATES:
        setup.closed_at = timestamp
        setup.close_reason = reason_code
    return SetupLifecycleEvent(
        setup_instance_id=setup.id,
        from_state=current,
        to_state=target,
        reason_code=reason_code,
        evidence=evidence or {},
        occurred_at=timestamp,
    )


def record_target_milestone(
    setup: SetupInstance,
    *,
    target_index: int,
    target_count: int,
    target_price: float | None = None,
    occurred_at: datetime | None = None,
) -> SetupLifecycleEvent:
    if target_count < 1 or target_index < 1 or target_index > target_count:
        raise InvalidLifecycleTransition("Target index must be inside the configured target range")
    if setup.state in TERMINAL_SETUP_STATES:
        raise InvalidLifecycleTransition(f"{setup.state.value} is terminal")
    if target_index <= (setup.targets_reached or 0):
        raise InvalidLifecycleTransition("Target milestone was already recorded")
    evidence = {
        "target_index": target_index,
        "target_count": target_count,
        "target_price": target_price,
    }
    setup.targets_reached = target_index
    if target_index == target_count:
        return transition_setup(
            setup,
            SetupLifecycleState.TARGET_REACHED,
            reason_code="final_target_reached",
            evidence=evidence,
            occurred_at=occurred_at,
        )
    target_state = (
        SetupLifecycleState.TARGET_1_REACHED
        if target_index == 1
        else SetupLifecycleState.TARGET_2_REACHED
    )
    if setup.state != target_state:
        return transition_setup(
            setup,
            target_state,
            reason_code="partial_target_reached",
            evidence=evidence,
            occurred_at=occurred_at,
        )
    timestamp = occurred_at or datetime.now(UTC)
    setup.last_evaluated_at = timestamp
    return SetupLifecycleEvent(
        setup_instance_id=setup.id,
        from_state=setup.state,
        to_state=setup.state,
        reason_code="partial_target_reached",
        evidence=evidence,
        occurred_at=timestamp,
    )
