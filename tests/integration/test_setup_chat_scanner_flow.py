"""The three-turn Scanner conversation, from "Scanner" to a governed scan request.

The reported failure was that the third turn threw away the first two::

    user      Scanner
    user      what coins are up at least 5% now?
    assistant Over what period ... 1 hour, 4 hours, 24 hours, or since today's open?
    user      24 hours
    assistant [compiled as a new strategy rule; the 5% and the "up" were gone]

Three separate causes, each with a test below:

* the half-collected scan was kept in telemetry, so the next turn could not find it;
* the question offered four windows and the provider has one;
* with nothing pending, "24 hours" reads exactly like a strategy edit, so it became one.

No model call is made anywhere in this file. Every turn here is one the server was
always able to answer by itself, and needing a paid call to finish a scan the user had
already described was part of the defect.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.conversation_intent import (
    ConversationIntent,
    classify_turn,
    scan_window_answer,
    selected_mode_word,
)
from ai_market_monitor.engine.conversation_language import (
    ConversationLanguage,
    localized,
    response_matches_language,
)
from ai_market_monitor.engine.supported_incomplete import (
    OFFERED_WINDOW_TIMEFRAMES,
    MissingChoice,
    clarification_for_choice,
)
from ai_market_monitor.engine.timeframes import SUPPORTED_TIMEFRAMES
from ai_market_monitor.schemas.setup_agent import SetupConversationContext
from ai_market_monitor.schemas.strategy_draft_v2 import DraftMode, StrategyDraftV2
from ai_market_monitor.services.setup_chat_agent import (
    READ_ONLY_SCAN_GOAL,
    SUPPORTED_SCAN_WINDOWS,
    SetupAgentTurnInput,
    SetupChatAgent,
)

#: The generic answer this flow used to end in.
BROCHURE = "Scanner checks a strategy on demand"

SCAN_QUESTION = "what coins are up at least 5% now?"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="setup-agent-secret-with-at-least-32-characters",
        openai_api_key=SecretStr("test-key"),
        sharia_screening_enforced=False,
        setup_agent_max_estimated_cost_usd_per_turn=5,
    )


def _scanner_draft() -> StrategyDraftV2:
    """A Scanner draft whose governed screened scope is already resolved."""

    base = StrategyDraftV2()
    return base.model_copy(
        update={
            "mode": DraftMode.SCANNER,
            "sharia_policy": base.sharia_policy.model_copy(
                update={"methodology_id": uuid4(), "methodology_version": "2026.1"}
            ),
        }
    )


class Conversation:
    """A chat that carries its own state forward, the way the launch service does."""

    def __init__(self, draft: StrategyDraftV2 | None = None) -> None:
        self.agent = SetupChatAgent(_settings())
        self.draft = draft if draft is not None else _scanner_draft()
        self.context = SetupConversationContext()
        self.notes: dict[str, object] = {}
        self.fingerprints: tuple[str, ...] = ()
        self.previous_intent: str | None = None

    async def say(self, message: str):
        turn = SetupAgentTurnInput(
            message=message,
            source_turn_id=f"turn-{len(self.fingerprints)}",
            draft=self.draft,
            setup_mode=self.draft.mode,
            conversation=self.context,
            previous_intent=self.previous_intent,
            previous_response_fingerprints=self.fingerprints,
        )
        result = await self.agent.run_turn(turn)
        self.context = result.conversation
        self.notes = dict(turn.telemetry.notes)
        intent = self.notes.get("conversation_intent")
        if intent and intent != "CONFUSION_SIGNAL":
            self.previous_intent = str(intent)
        fingerprint = self.notes.get("response_fingerprint")
        if fingerprint:
            self.fingerprints = (*self.fingerprints, str(fingerprint))
        return result


# ---------------------------------------------------------------------------------
# The whole flow
# ---------------------------------------------------------------------------------


async def test_the_three_turn_scanner_flow_reaches_a_governed_scan_request() -> None:
    """Scanner, then the question, then the window — and the scan actually runs."""

    chat = Conversation()

    asked = await chat.say(SCAN_QUESTION)
    assert BROCHURE not in asked.message
    assert chat.notes["conversation_intent"] == "ON_DEMAND_SCAN"
    assert asked.read_only_scan_request is None

    finished = await chat.say("24 hours")

    request = finished.read_only_scan_request
    assert request is not None, "the third turn must hand a request to governed execution"
    assert request["movement_direction"] == "up"
    assert float(str(request["threshold_percent"])) == 5.0
    assert request["measurement_window"] == "24h"
    # The words that stated the scan are what justify it, not the word that finished it.
    assert request["source_text"] == SCAN_QUESTION


async def test_the_window_answer_is_never_compiled_as_a_strategy_edit() -> None:
    chat = Conversation()
    await chat.say(SCAN_QUESTION)
    before = chat.draft.executable_hash

    answered = await chat.say("24 hours")

    assert chat.notes["conversation_intent"] == "ON_DEMAND_SCAN"
    assert "answers_scan_window" in chat.notes["conversation_intent_reasons"]
    assert answered.execution is None
    assert answered.draft.executable_hash == before
    assert answered.draft.condition_ast is None
    assert not answered.draft.approval.approved


async def test_the_first_turn_stores_the_scan_where_the_next_turn_looks() -> None:
    """Canonical state, not telemetry. This is what the continuation reads."""

    chat = Conversation()
    result = await chat.say(SCAN_QUESTION)
    pending = result.conversation.pending_read_only_scan

    assert pending["movement_direction"] == "up"
    assert pending["threshold_percent"] == 5.0
    assert pending["measurement_window"] is None
    assert pending["source_text"] == SCAN_QUESTION
    assert result.conversation.active_goal == READ_ONLY_SCAN_GOAL
    # A real contract, and one that cannot change anything.
    contract = result.conversation.active_question
    assert contract is not None
    assert contract.mutating is False
    assert contract.allowed_options == ["24h"]


async def test_handing_the_scan_over_does_not_leave_it_running_forever() -> None:
    """Collecting is done, so a later message must not re-enter and re-run the scan."""

    chat = Conversation()
    await chat.say(SCAN_QUESTION)
    handed_over = await chat.say("24 hours")
    assert handed_over.read_only_scan_request is not None
    assert handed_over.conversation.active_goal is None
    assert handed_over.conversation.active_question_id is None

    after = await chat.say("what is Scanner?")
    assert after.read_only_scan_request is None
    assert chat.notes["conversation_intent"] == "PRODUCT_EXPLANATION"


async def test_nothing_already_given_is_asked_for_again() -> None:
    chat = Conversation()
    first = await chat.say(SCAN_QUESTION)
    second = await chat.say("24 hours")

    # The size and the side appear in neither question, because neither was missing.
    assert first.message.count("?") == 1
    assert "5%" not in second.message
    assert chat.notes.get("scan_execution") == "requested"


# ---------------------------------------------------------------------------------
# Options must match what the backend can do
# ---------------------------------------------------------------------------------


UNSUPPORTED_WINDOW_WORDS: tuple[str, ...] = (
    "1 hour",
    "4 hours",
    "since today's open",
    "since the daily open",
    "1 heure",
    "1 hora",
)


@pytest.mark.parametrize("language", list(ConversationLanguage))
def test_the_scan_question_offers_only_the_window_that_exists(
    language: ConversationLanguage,
) -> None:
    """One provider window, one offered choice. Anything else is a broken promise."""

    assert SUPPORTED_SCAN_WINDOWS == ("24h",)
    question = localized("ask.scan_window_24h", language)
    assert question
    assert response_matches_language(question, language)


async def test_no_unsupported_window_is_ever_offered_to_the_user() -> None:
    chat = Conversation()
    result = await chat.say(SCAN_QUESTION)
    lowered = result.message.casefold()
    for word in UNSUPPORTED_WINDOW_WORDS:
        assert word.casefold() not in lowered, word
    contract = result.conversation.active_question
    assert contract is not None
    assert contract.allowed_options == list(SUPPORTED_SCAN_WINDOWS)


def test_every_offered_rule_window_is_a_real_timeframe() -> None:
    """The Monitor question had the same defect: it offered "since the daily open"."""

    for choice, timeframes in OFFERED_WINDOW_TIMEFRAMES.items():
        for timeframe in timeframes:
            assert timeframe in SUPPORTED_TIMEFRAMES, (choice, timeframe)
    contract = clarification_for_choice(
        MissingChoice.MEASUREMENT_WINDOW,
        language=ConversationLanguage.ENGLISH,
        source_turn_id="turn-window",
    )
    assert len(contract.allowed_options) == len(
        OFFERED_WINDOW_TIMEFRAMES[MissingChoice.MEASUREMENT_WINDOW]
    )
    assert "daily open" not in contract.question.casefold()


WINDOW_ANSWERS: tuple[str, ...] = (
    "24h",
    "24 hours",
    "last 24 hours",
    "1d",
    "yes",
    "24 heures",
    "oui",
    "24 horas",
    "sí",
    "24 часа",
    "да",
    "نعم",
    "24 ساعة",
)


@pytest.mark.parametrize("answer", WINDOW_ANSWERS)
def test_every_natural_way_of_saying_24_hours_is_accepted(answer: str) -> None:
    assert scan_window_answer(answer) == "24h"


@pytest.mark.parametrize("answer", ("1 hour", "4h", "since today's open", "maybe", ""))
def test_a_window_the_backend_lacks_is_not_quietly_rounded_to_24h(answer: str) -> None:
    """Refusing to recognise it keeps the mismatch visible; rounding hides it."""

    assert scan_window_answer(answer) is None


# ---------------------------------------------------------------------------------
# Confusion, before the question is answered
# ---------------------------------------------------------------------------------


async def test_confusion_before_answering_keeps_the_scan_and_asks_again() -> None:
    chat = Conversation()
    asked = await chat.say(SCAN_QUESTION)
    question_id = asked.conversation.active_question_id

    confused = await chat.say("??")

    assert chat.notes["conversation_intent"] == "CONFUSION_SIGNAL"
    assert BROCHURE not in confused.message
    assert confused.message != asked.message
    # It admits the miss, restates the goal, and repeats the same question once.
    assert "didn't answer" in confused.message
    assert "5%" in confused.message
    assert confused.message.count("?") == 1
    # And the scan is exactly where it was.
    assert confused.conversation.pending_read_only_scan == (
        asked.conversation.pending_read_only_scan
    )
    assert confused.conversation.active_question_id == question_id


async def test_a_scan_survives_confusion_and_still_completes() -> None:
    chat = Conversation()
    await chat.say(SCAN_QUESTION)
    await chat.say("??")
    finished = await chat.say("24h")

    request = finished.read_only_scan_request
    assert request is not None
    assert request["movement_direction"] == "up"
    assert float(str(request["threshold_percent"])) == 5.0
    assert request["measurement_window"] == "24h"


# ---------------------------------------------------------------------------------
# Routing that must not change
# ---------------------------------------------------------------------------------


def test_a_typed_mode_word_is_the_same_choice_as_the_button() -> None:
    """Both must reach `option_key="setup_mode"`, which is what sets `draft.mode`."""

    for typed in ("Scanner", "scanner", "  Scanner  ", "scan", "سكانر", "сканер"):
        assert selected_mode_word(typed) == "scanner", typed
    for typed in ("Monitor", "monitoring", "мonitor".replace("м", "m")):
        assert selected_mode_word(typed) == "monitor", typed
    # A sentence that merely mentions Scanner is not a mode choice.
    for typed in ("what is Scanner?", "scanner please show coins", ""):
        assert selected_mode_word(typed) is None, typed


def test_a_product_question_inside_scanner_is_still_a_product_question() -> None:
    reading = classify_turn("what is Scanner?", active_mode="scanner")
    assert reading.intent is ConversationIntent.PRODUCT_EXPLANATION


async def test_a_product_question_does_not_hijack_a_pending_scan() -> None:
    chat = Conversation()
    await chat.say(SCAN_QUESTION)
    explained = await chat.say("what is Scanner?")

    # Answering it is fine; losing the scan is not.
    assert explained.conversation.pending_read_only_scan["threshold_percent"] == 5.0
    assert explained.execution is None


def test_choosing_monitor_abandons_the_pending_scan_deliberately() -> None:
    reading = classify_turn("Monitor", pending_scan=True, active_mode="scanner")
    assert reading.intent is ConversationIntent.CONTINUOUS_MONITOR
    assert "abandons_pending_scan" in reading.reasons


@pytest.mark.parametrize(
    "message",
    (
        "actually, create a rule for BTCUSDT 15m up 2%",
        "monitor ETHUSDT when the 1h candle falls 3%",
        "set up an alert instead",
    ),
)
def test_a_trader_can_always_walk_away_from_a_pending_scan(message: str) -> None:
    """Otherwise every later message comes back as the same unanswered question."""

    reading = classify_turn(message, pending_scan=True, active_mode="scanner")
    assert reading.intent is not ConversationIntent.ON_DEMAND_SCAN
    assert "abandons_pending_scan" in reading.reasons


def test_walking_away_still_requires_asking_for_something_built() -> None:
    """A vague reply is not an exit; it is still an unclear answer to the question."""

    reading = classify_turn("hmm not sure", pending_scan=True, active_mode="scanner")
    assert reading.intent is ConversationIntent.ON_DEMAND_SCAN
    assert "scan_answer_unclear" in reading.reasons


# ---------------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------------


CONTINUATIONS: tuple[tuple[str, str, ConversationLanguage], ...] = (
    ("what coins are up at least 5% now?", "24 hours", ConversationLanguage.ENGLISH),
    ("ما العملات التي ترتفع 5% الآن؟", "نعم", ConversationLanguage.ARABIC),
    (
        "quelles cryptos montent d'au moins 5% maintenant ?",
        "24 heures",
        ConversationLanguage.FRENCH,
    ),
    ("¿qué monedas suben al menos 5% ahora?", "24 horas", ConversationLanguage.SPANISH),
    ("какие монеты вырос на 5% сейчас?", "24 часа", ConversationLanguage.RUSSIAN),
)


@pytest.mark.parametrize(("question", "answer", "language"), CONTINUATIONS)
async def test_a_scan_never_changes_language_half_way_through(
    question: str, answer: str, language: ConversationLanguage
) -> None:
    chat = Conversation()
    asked = await chat.say(question)
    assert response_matches_language(asked.message, language), asked.message

    finished = await chat.say(answer)
    assert response_matches_language(finished.message, language), finished.message
    assert finished.conversation.active_language == language.value


# ---------------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ("24 hours", "??", "yes", "what is Scanner?"))
async def test_no_turn_of_a_scan_ever_mutates_or_approves(answer: str) -> None:
    chat = Conversation()
    before = chat.draft.executable_hash
    await chat.say(SCAN_QUESTION)
    result = await chat.say(answer)

    assert result.draft.executable_hash == before
    assert result.draft.condition_ast is None
    assert not result.draft.approval.approved
    assert result.execution is None


async def test_a_scan_turn_never_invents_a_result_a_status_or_a_price() -> None:
    chat = Conversation()
    await chat.say(SCAN_QUESTION)
    finished = await chat.say("24 hours")

    lowered = finished.message.casefold()
    for invented in ("btc", "eth", "halal", "haram", "%"):
        assert invented not in lowered or invented == "%", finished.message
    # It says it is running the scan; it does not report one.
    assert "matched" not in lowered
    assert finished.execution is None


async def test_a_scan_outside_scanner_mode_is_held_not_run() -> None:
    """The values are kept so choosing Scanner finishes the same scan."""

    monitor_draft = StrategyDraftV2().model_copy(update={"mode": DraftMode.MONITOR})
    chat = Conversation(draft=monitor_draft)
    result = await chat.say("what coins are up at least 5% now, 24h?")

    assert result.read_only_scan_request is None
    assert result.conversation.pending_read_only_scan["threshold_percent"] == 5.0
    assert result.conversation.pending_read_only_scan["measurement_window"] == "24h"
    assert result.execution is None


async def test_a_scan_without_a_screened_scope_is_held_not_run() -> None:
    """Governed screening is never bypassed to answer a question faster."""

    bare = StrategyDraftV2().model_copy(update={"mode": DraftMode.SCANNER})
    chat = Conversation(draft=bare)
    result = await chat.say("what coins are up at least 5% now, 24h?")

    assert result.read_only_scan_request is None
    assert chat.notes.get("scan_execution") == "scope_required"
    assert result.conversation.pending_read_only_scan["threshold_percent"] == 5.0
