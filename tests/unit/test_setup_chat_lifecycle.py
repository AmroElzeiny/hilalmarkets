"""Named lifecycle states for a setup chat session.

The evaluator's UI target could not detect that a turn had finished because the
coarse ``status`` string does not distinguish a session still gathering
requirements from one already holding an inactive compiled draft.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_market_monitor.schemas.strategy_draft_v2 import (
    ApprovalBindingV2,
    DraftMode,
    StrategyDraftV2,
)
from ai_market_monitor.services.ai_setup_chat import v2_scanner_approval_matches
from ai_market_monitor.services.setup_chat_lifecycle import (
    AWAITING_USER_STATES,
    TERMINAL_STATES,
    TURN_COMPLETE_STATES,
    is_terminal,
    is_turn_complete,
    setup_lifecycle_state,
)

_HASH = "a" * 64


@pytest.mark.parametrize(
    ("session_status", "has_draft", "eligible", "expected"),
    [
        ("interviewing", False, False, "collecting"),
        ("interviewing", True, False, "ready_for_confirmation"),
        ("needs_clarification", False, False, "needs_clarification"),
        ("needs_clarification", True, False, "needs_clarification"),
        ("ready_to_scan", True, False, "ready_for_confirmation"),
        ("ready_for_approval", True, True, "awaiting_approval"),
    ],
)
def test_states_follow_the_observable_session_facts(
    session_status: str, has_draft: bool, eligible: bool, expected: str
) -> None:
    assert (
        setup_lifecycle_state(
            session_status=session_status,
            has_draft=has_draft,
            approval_eligible=eligible,
        )
        == expected
    )


def test_a_blocked_approval_reports_needing_clarification_not_awaiting_approval() -> None:
    """A critical lint finding means the draft is not presentable for approval."""
    assert (
        setup_lifecycle_state(
            session_status="ready_for_approval",
            has_draft=True,
            approval_eligible=False,
        )
        == "needs_clarification"
    )


def test_approval_becomes_compiled_once_an_immutable_hash_exists() -> None:
    assert (
        setup_lifecycle_state(session_status="approved", has_draft=True, approval_eligible=False)
        == "approved"
    )
    assert (
        setup_lifecycle_state(
            session_status="approved",
            has_draft=True,
            approval_eligible=False,
            immutable_version_hash=_HASH,
        )
        == "compiled"
    )


def test_activation_is_reported_from_the_strategy_version_not_the_chat() -> None:
    assert (
        setup_lifecycle_state(
            session_status="approved",
            has_draft=True,
            approval_eligible=False,
            immutable_version_hash=_HASH,
            version_status="active",
        )
        == "activated"
    )


def test_a_draft_with_a_blocking_finding_is_not_ready_for_confirmation() -> None:
    """A draft carrying a critical lint finding still needs something resolved."""
    assert (
        setup_lifecycle_state(
            session_status="interviewing",
            has_draft=True,
            approval_eligible=False,
            blocking_findings=True,
        )
        == "needs_clarification"
    )


def test_an_inactive_draft_is_never_reported_as_approved_or_activated() -> None:
    """A draft compiled while questions are open must not imply any approval."""
    for status in ("interviewing", "needs_clarification", "ready_to_scan"):
        state = setup_lifecycle_state(
            session_status=status, has_draft=True, approval_eligible=False
        )
        assert state not in {"approved", "compiled", "activated", "awaiting_approval"}


def test_only_approved_lifecycle_states_are_terminal() -> None:
    assert is_terminal("collecting") is False
    assert is_terminal("awaiting_approval") is False
    assert set(TERMINAL_STATES) == {"approved", "compiled", "activated"}


def test_every_synchronous_response_lifecycle_is_a_complete_assistant_turn() -> None:
    assert is_turn_complete("collecting") is True
    assert set(TURN_COMPLETE_STATES) == {
        "collecting",
        "needs_clarification",
        "ready_for_confirmation",
        "awaiting_approval",
        "approved",
        "compiled",
        "activated",
    }


def test_states_waiting_on_the_user_are_all_terminal() -> None:
    """Anything waiting on a person has finished producing assistant output."""
    assert AWAITING_USER_STATES <= TURN_COMPLETE_STATES


def test_v2_scanner_requires_the_exact_authenticated_approval_binding() -> None:
    user_id = uuid4()
    schema_hash = "b" * 64
    draft = StrategyDraftV2(mode=DraftMode.SCANNER)
    draft = draft.model_copy(
        update={
            "approval": ApprovalBindingV2(
                approved=True,
                user_id=user_id,
                executable_version=draft.executable_version,
                executable_hash=draft.executable_hash,
                schema_hash=schema_hash,
                conversation_snapshot_hash="c" * 64,
                approved_at=datetime.now(UTC),
            )
        }
    )
    chat = SimpleNamespace(
        context_json={"strategy_state_authority": "v2"},
        status="approved",
        approved_strategy_id=uuid4(),
        approved_strategy_version_id=uuid4(),
    )
    definition = SimpleNamespace(canonical_hash=lambda: schema_hash)

    assert v2_scanner_approval_matches(chat, draft, definition, user_id=user_id)
    chat.status = "ready_for_approval"
    assert not v2_scanner_approval_matches(chat, draft, definition, user_id=user_id)
    chat.status = "approved"
    changed = draft.model_copy(update={"executable_hash": "d" * 64})
    assert not v2_scanner_approval_matches(chat, changed, definition, user_id=user_id)
