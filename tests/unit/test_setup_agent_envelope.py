from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_market_monitor.schemas.setup_agent import SetupAgentPlanEnvelope


def test_pure_reply_accepts_only_harmless_misplaced_plan_metadata() -> None:
    envelope = SetupAgentPlanEnvelope.model_validate(
        {
            "plan": None,
            "direct_reply": "Yes, I can hear you.",
            "response_points": [],
            "questions_to_answer": [],
            "overall_confidence": 0.99,
            "segments": [],
            "operations": [],
            "clarifications_to_ask": [],
            "approval_intent": None,
        }
    )

    assert envelope.plan is None
    assert envelope.direct_reply == "Yes, I can hear you."
    assert set(envelope.model_dump()) == {"plan", "direct_reply"}


@pytest.mark.parametrize(
    ("unsafe_key", "unsafe_value"),
    [
        ("operations", [{"kind": "set_fields"}]),
        ("segments", [{"segment_id": "s1"}]),
        ("clarifications_to_ask", [{"question_id": "q1"}]),
        ("approval_intent", {"segment_id": "s1"}),
    ],
)
def test_pure_reply_never_discards_misplaced_semantic_or_mutating_fields(
    unsafe_key: str,
    unsafe_value: object,
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SetupAgentPlanEnvelope.model_validate(
            {
                "plan": None,
                "direct_reply": "Hello.",
                unsafe_key: unsafe_value,
            }
        )
