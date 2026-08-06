"""The two production conversations, replayed through the real agent.

Both are transcripts a customer actually got. They run here through the whole
production turn — the real router, the real language decision, the real clarification
contract, the real reply assembly — with no model call needed, because neither turn
should ever have needed one.

Before
------

    user      create me an alert to alert me when a coin increases 5%
    assistant the request could not be expressed ... comparison operator is missing ...
              percentage formula is missing ... timeframe is missing ... Not
              expressible exactly: ... [the same explanation, three more times]

    user      Scanner
    assistant Which screened assets should HilalMarkets watch?
    user      what coins are up at least 5% now?
    assistant No strategy state changed. Scanner checks a strategy on demand; Monitor
              keeps evaluating an explicitly approved strategy. Neither path places
              trades.
    user      ??
    assistant [the same sentence again]

After
-----

    assistant Sure. Should I watch all screened coins or one specific coin?

    assistant Should I use the provider's rolling 24-hour percentage change?
    assistant Sorry — that didn't answer your question. You want to scan screened
              coins that are up at least 5%. [and the same one question]

The window question offers one choice because the provider exposes one window. The
three-turn continuation that follows it is covered in
``test_setup_chat_scanner_flow.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.conversation_language import (
    ConversationLanguage,
    response_matches_language,
)
from ai_market_monitor.schemas.strategy_draft_v2 import DraftMode, StrategyDraftV2
from ai_market_monitor.services.setup_chat_agent import (
    SetupAgentTurnInput,
    SetupChatAgent,
)


def scanner_draft() -> StrategyDraftV2:
    """A Scanner draft whose screened scope is already governed and ready."""

    base = StrategyDraftV2()
    return base.model_copy(
        update={
            "mode": DraftMode.SCANNER,
            "sharia_policy": base.sharia_policy.model_copy(
                update={"methodology_id": uuid4(), "methodology_version": "2026.1"}
            ),
        }
    )

#: The generic answer that used to be returned for a live market question.
BROCHURE = "Scanner checks a strategy on demand"

#: Wording a beginner has never met. None of it may reach a customer message.
INTERNAL_JARGON = (
    "comparison operator",
    "formula",
    "semantic intent",
    "executable contract",
    "canonical requirement",
    "compiler",
    "unsupported intent",
    "not expressible",
    "could not be expressed",
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="setup-agent-secret-with-at-least-32-characters",
        openai_api_key=SecretStr("test-key"),
        sharia_screening_enforced=False,
        setup_agent_max_estimated_cost_usd_per_turn=5,
    )


async def _turn(message: str, **kwargs) -> object:
    """One production turn. No transport is supplied: a model call would fail loudly."""

    agent = SetupChatAgent(_settings())
    return await agent.run_turn(
        SetupAgentTurnInput(
            message=message,
            source_turn_id="turn-conversation-0001",
            draft=kwargs.pop("draft", None) or StrategyDraftV2(),
            **kwargs,
        )
    )


# ---------------------------------------------------------------------------------
# Case A
# ---------------------------------------------------------------------------------

CASE_A = "create me an alert to alert me when a coin increases 5%"


async def test_case_a_asks_one_natural_question_and_changes_nothing() -> None:
    result = await _turn(CASE_A)
    reply = result.message

    assert reply == "Sure. Should I watch all screened coins or one specific coin?"
    # Exactly one question, and short enough for a beginner to read.
    assert reply.count("?") == 1
    assert len(reply) <= 500
    # A question is not an edit.
    assert result.execution is None
    assert result.draft.condition_ast is None
    assert not result.draft.approval.approved


async def test_case_a_is_never_recorded_as_unsupported() -> None:
    result = await _turn(CASE_A)
    assert result.draft.unsupported_requirements == []


async def test_case_a_carries_no_internal_wording_and_no_repetition() -> None:
    result = await _turn(CASE_A)
    lowered = result.message.casefold()
    assert not [word for word in INTERNAL_JARGON if word in lowered]
    # The reported defect was the same explanation appearing more than once.
    sentences = [item.strip() for item in result.message.split(".") if item.strip()]
    assert len(sentences) == len(set(sentences))


async def test_case_a_preserves_the_direction_and_size_already_given() -> None:
    """The 5% and the "increases" were in the sentence; asking again is not listening."""

    turn_input = SetupAgentTurnInput(
        message=CASE_A, source_turn_id="turn-a", draft=StrategyDraftV2()
    )
    agent = SetupChatAgent(_settings())
    result = await agent.run_turn(turn_input)
    notes = turn_input.telemetry.notes
    assert notes["request_completeness"] == "SUPPORTED_INCOMPLETE"
    assert notes["request_supplied_values"]["direction"] == "up"
    assert notes["request_supplied_values"]["threshold_percent"] == "5"
    assert notes["clarification_target"] == "symbol_scope"
    assert notes["final_mutation_status"] == "no_mutation"
    assert result.conversation.active_question_id


@pytest.mark.parametrize(
    ("message", "language"),
    (
        (CASE_A, ConversationLanguage.ENGLISH),
        ("عايز تنبيه لما عملة تطلع 5%", ConversationLanguage.ARABIC),
        ("crée-moi une alerte quand une pièce monte de 5%", ConversationLanguage.FRENCH),
        ("créame una alerta cuando una moneda suba 5%", ConversationLanguage.SPANISH),
    ),
)
async def test_case_a_answers_in_the_language_it_was_asked_in(
    message: str, language: ConversationLanguage
) -> None:
    result = await _turn(message)
    assert response_matches_language(result.message, language)
    assert result.message.count("?") + result.message.count("؟") == 1


# ---------------------------------------------------------------------------------
# Case B
# ---------------------------------------------------------------------------------

CASE_B_SCAN = "what coins are up at least 5% now?"


async def test_case_b_live_question_never_returns_the_brochure() -> None:
    turn_input = SetupAgentTurnInput(
        message=CASE_B_SCAN,
        source_turn_id="turn-scan",
        draft=scanner_draft(),
        setup_mode=DraftMode.SCANNER,
    )
    result = await SetupChatAgent(_settings()).run_turn(turn_input)
    notes = turn_input.telemetry.notes

    assert BROCHURE not in result.message
    assert notes["conversation_intent"] == "ON_DEMAND_SCAN"
    assert notes["conversation_route"] == "on_demand_scan_clarification"
    # Only the genuinely missing choice is asked about.
    assert notes["scan_missing_slots"] == ["window"]
    assert notes["scan_threshold_percent"] == 5.0
    assert notes["scan_direction"] == "up"
    assert result.message.count("?") == 1
    # The size and the side are kept where the next turn will look for them.
    pending = result.conversation.pending_read_only_scan
    assert pending["threshold_percent"] == 5.0
    assert pending["movement_direction"] == "up"
    assert pending["measurement_window"] is None
    assert result.conversation.active_goal == "read_only_percentage_scan"
    assert result.conversation.active_question_id
    # A read-only question mutates nothing.
    assert result.execution is None
    assert notes["final_mutation_status"] == "no_mutation"


async def test_case_b_confusion_does_not_repeat_the_previous_answer() -> None:
    agent = SetupChatAgent(_settings())
    draft = scanner_draft()
    scan_turn = SetupAgentTurnInput(
        message=CASE_B_SCAN,
        source_turn_id="turn-scan",
        draft=draft,
        setup_mode=DraftMode.SCANNER,
    )
    first = await agent.run_turn(scan_turn)
    fingerprint = scan_turn.telemetry.notes["response_fingerprint"]

    confused = SetupAgentTurnInput(
        message="??",
        source_turn_id="turn-confused",
        draft=draft,
        setup_mode=DraftMode.SCANNER,
        conversation=first.conversation,
        previous_intent="ON_DEMAND_SCAN",
        previous_response_fingerprints=(fingerprint,),
    )
    recovered = await agent.run_turn(confused)
    notes = confused.telemetry.notes

    assert notes["conversation_intent"] == "CONFUSION_SIGNAL"
    assert notes["confusion_recovery"] is True
    assert recovered.message != first.message
    assert notes["response_fingerprint"] != fingerprint
    # It admits the miss and returns to the real goal.
    assert "didn't answer" in recovered.message
    assert "scan" in recovered.message.casefold()
    assert BROCHURE not in recovered.message
    assert recovered.execution is None
    # And the goal it was confused about is still waiting.
    assert recovered.conversation.pending_read_only_scan["threshold_percent"] == 5.0
    assert recovered.conversation.active_question_id == first.conversation.active_question_id


async def test_a_confusion_signal_is_never_planned_as_a_strategy_edit() -> None:
    turn_input = SetupAgentTurnInput(
        message="??", source_turn_id="turn-q", draft=StrategyDraftV2()
    )
    result = await SetupChatAgent(_settings()).run_turn(turn_input)
    assert turn_input.telemetry.notes["conversation_read_only"] is True
    assert result.execution is None
    assert result.draft.condition_ast is None


# ---------------------------------------------------------------------------------
# Safety boundaries
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    (CASE_A, CASE_B_SCAN, "??", "Scanner", "what is Scanner?"),
)
async def test_no_conversational_turn_ever_mutates_or_approves(message: str) -> None:
    before = scanner_draft()
    turn_input = SetupAgentTurnInput(
        message=message,
        source_turn_id="turn-safety",
        draft=before,
        setup_mode=DraftMode.SCANNER,
    )
    result = await SetupChatAgent(_settings()).run_turn(turn_input)
    assert result.draft.executable_hash == before.executable_hash
    assert not result.draft.approval.approved
    assert result.execution is None
    assert turn_input.telemetry.notes.get("final_mutation_status") == "no_mutation"


@pytest.mark.parametrize("message", (CASE_A, CASE_B_SCAN))
async def test_no_conversational_turn_invents_a_sharia_status(message: str) -> None:
    result = await _turn(message, draft=scanner_draft(), setup_mode=DraftMode.SCANNER)
    lowered = result.message.casefold()
    assert "halal" not in lowered
    assert "haram" not in lowered
    assert "حلال" not in result.message
    assert "حرام" not in result.message


@pytest.mark.parametrize("message", (CASE_A, CASE_B_SCAN))
async def test_no_conversational_turn_fabricates_a_market_result(message: str) -> None:
    """A question about the market is never answered with invented prices."""

    result = await _turn(message, draft=scanner_draft(), setup_mode=DraftMode.SCANNER)
    # No coin is named as matching, because no scan has run yet.
    assert "BTC" not in result.message
    assert "ETH" not in result.message
