"""INV-CONV: where a turn goes, what language it comes back in, and never twice.

Two production conversations are the reason this file exists.

**A supported request was called unsupported.** ``create me an alert to alert me when a
coin increases 5%`` is an ordinary alert: the platform measures percentage moves. What
the sentence does not say is which coins and over what period — the trader's choices.
The planner was instructed to preserve a rule missing a required value in
``unsupported_intents``, so the draft recorded an unsupported requirement and four
different renderers each explained it. A beginner asking for a 5% alert was told at
length that their request could not be expressed.

**A live market question was answered with a product brochure.** After choosing
Scanner, ``what coins are up at least 5% now?`` returned "Scanner checks a strategy on
demand; Monitor keeps evaluating...". When the user typed ``??``, the same
classification produced the same sentence again.

These tests assert the rules, across the family rather than the two reported
sentences: every supported mechanic, every language the product answers in, and every
wording of confusion.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.conversation_intent import (
    ConversationIntent,
    classify_turn,
    is_confusion_signal,
)
from ai_market_monitor.engine.conversation_language import (
    ConversationLanguage,
    detect_language,
    localized,
    resolve_conversation_language,
    response_matches_language,
    translation_coverage,
)
from ai_market_monitor.engine.response_reconciliation import (
    ConversationalGoal,
    Proposition,
    RenderedPart,
    RenderSource,
    confusion_recovery_reply,
    enforce_language,
    reconcile_reply,
    response_fingerprint,
)
from ai_market_monitor.engine.supported_incomplete import (
    MissingChoice,
    RequestCompleteness,
    assess_request,
    clarification_for_choice,
)

# ---------------------------------------------------------------------------------
# 1. Supported but incomplete is never unsupported
# ---------------------------------------------------------------------------------

#: Every mechanic the product genuinely offers, written the way a beginner writes it
#: and missing something only the trader can supply. Not one of them is a boundary.
SUPPORTED_BUT_INCOMPLETE: tuple[str, ...] = (
    "create me an alert to alert me when a coin increases 5%",
    "alert me when a coin increases",
    "alert me when a coin drops 3%",
    "tell me when price crosses the 200 EMA",
    "alert me when RSI goes above 70",
    "alert me when a bullish engulfing candle appears",
    "alert me when volume spikes",
    "let me know on a breakout",
)


@pytest.mark.parametrize("message", SUPPORTED_BUT_INCOMPLETE)
def test_a_supported_request_missing_a_choice_is_not_unsupported(message: str) -> None:
    assessment = assess_request(message)
    assert assessment.completeness is RequestCompleteness.SUPPORTED_INCOMPLETE
    assert assessment.mechanic is not None
    assert assessment.missing
    assert assessment.next_question is not None


#: Market behaviour the registered capability system genuinely cannot express. The
#: word "unsupported" has to keep meaning this, or it means nothing.
GENUINELY_UNSUPPORTED: tuple[str, ...] = (
    "alert me when the moon is in retrograde",
    "alert me based on my broker order book depth imbalance",
    "notify me when my horoscope says to buy",
)


@pytest.mark.parametrize("message", GENUINELY_UNSUPPORTED)
def test_a_boundary_is_still_reported_as_a_boundary(message: str) -> None:
    assert assess_request(message).completeness is RequestCompleteness.UNSUPPORTED


def test_case_a_keeps_every_value_the_trader_already_gave() -> None:
    """The 5% and the direction were in the sentence. Asking again is not listening."""

    assessment = assess_request("create me an alert to alert me when a coin increases 5%")
    assert assessment.supplied["direction"] == "up"
    assert assessment.supplied["threshold_percent"] == "5"
    assert MissingChoice.MOVEMENT_SIZE not in assessment.missing
    assert MissingChoice.MOVEMENT_KIND not in assessment.missing
    # And the first thing asked is the scope, exactly as the product spec requires.
    assert assessment.next_question is MissingChoice.SYMBOL_SCOPE


def test_a_value_settled_earlier_is_never_asked_for_again() -> None:
    assessment = assess_request(
        "make it 5%",
        known_symbols=("BTC/USDT",),
        known_window="1h",
    )
    assert MissingChoice.SYMBOL_SCOPE not in assessment.missing
    assert MissingChoice.MEASUREMENT_WINDOW not in assessment.missing


#: Wording a beginner has never met. None of it may reach a customer message.
INTERNAL_JARGON: tuple[str, ...] = (
    "comparison operator",
    "formula key",
    "semantic intent",
    "executable contract",
    "canonical requirement",
    "compiler",
    "unsupported intent",
    "trigger timeframe",
    "target_field",
    "condition node",
)


@pytest.mark.parametrize("choice", list(MissingChoice))
@pytest.mark.parametrize("language", list(ConversationLanguage))
def test_no_question_ever_contains_internal_wording(
    choice: MissingChoice, language: ConversationLanguage
) -> None:
    contract = clarification_for_choice(
        choice, language=language, source_turn_id="turn-1", threshold_percent="5"
    )
    lowered = contract.question.casefold()
    assert not [word for word in INTERNAL_JARGON if word in lowered]
    assert contract.question.count("?") + contract.question.count("؟") == 1
    assert response_matches_language(contract.question, language)


# ---------------------------------------------------------------------------------
# 2. A live market question reaches the scan route
# ---------------------------------------------------------------------------------

#: The same request in every language the product answers in. Each one is a live look
#: at the market, and none of them is a question about how Scanner works.
LIVE_SCAN_REQUESTS: tuple[str, ...] = (
    "what coins are up at least 5% now?",
    "which cryptos are up 5% right now",
    "show me coins up 5% today",
    "ما هي العملات الصاعدة 5% الآن؟",
    "quelles pièces montent d'au moins 5% maintenant ?",
    "¿qué monedas suben al menos 5% ahora?",
)


@pytest.mark.parametrize("message", LIVE_SCAN_REQUESTS)
def test_a_live_market_question_is_a_scan_not_a_brochure(message: str) -> None:
    reading = classify_turn(message, active_mode="scanner")
    assert reading.intent is ConversationIntent.ON_DEMAND_SCAN
    # And it must never mutate anything: a question is not an edit.
    assert reading.is_read_only


def test_scanner_context_survives_into_the_next_turn() -> None:
    """The destination the trader chose stands until they change it."""

    chosen = classify_turn("Scanner")
    assert chosen.selected_mode == "scanner"
    followed = classify_turn("what coins are up at least 5%?", active_mode="scanner")
    assert followed.intent is ConversationIntent.ON_DEMAND_SCAN


def test_only_the_genuinely_missing_choice_is_asked_for() -> None:
    reading = classify_turn("what coins are up at least 5% now?", active_mode="scanner")
    assert reading.slots.threshold_percent == 5.0
    assert reading.slots.direction == "up"
    # The size and the side were stated. Only the period is missing.
    assert reading.slots.missing == ("window",)


def test_a_real_product_question_still_gets_a_product_answer() -> None:
    for message in (
        "what is Scanner?",
        "what's the difference between Scanner and Monitor?",
        "how does monitoring work?",
    ):
        assert (
            classify_turn(message).intent is ConversationIntent.PRODUCT_EXPLANATION
        ), message


# ---------------------------------------------------------------------------------
# 3. Confusion recovery
# ---------------------------------------------------------------------------------

CONFUSION_WORDINGS: tuple[str, ...] = (
    "??",
    "?",
    "what?",
    "huh?",
    "I don't understand",
    "that didn't answer me",
    "that's not what I asked",
    "مش فاهم",
    "لم تجب",
    "je ne comprends pas",
    "no entiendo",
    "eso no responde",
)


@pytest.mark.parametrize("message", CONFUSION_WORDINGS)
def test_every_wording_of_confusion_is_recognised(message: str) -> None:
    assert is_confusion_signal(message)
    assert classify_turn(message).intent is ConversationIntent.CONFUSION_SIGNAL


def test_a_market_instruction_is_never_mistaken_for_confusion() -> None:
    for message in ("what coins are up 5% now?", "what is Scanner?", "5%"):
        assert not is_confusion_signal(message), message


@pytest.mark.parametrize("language", list(ConversationLanguage))
def test_recovery_admits_the_miss_restates_the_goal_and_asks_once(
    language: ConversationLanguage,
) -> None:
    question = localized("ask.scan_window", language, threshold="5%")
    goal = ConversationalGoal(kind="scan", threshold_percent="5", pending_question=question)
    recovered = confusion_recovery_reply(goal, language=language)
    assert localized("confusion.acknowledge", language) in recovered.message
    assert question in recovered.message
    assert response_matches_language(recovered.message, language)
    assert recovered.message.count("?") + recovered.message.count("؟") == 1


def test_the_same_answer_is_never_sent_twice_to_a_confused_user() -> None:
    """Repeating the reply that already failed is the defect, not a fallback."""

    goal = ConversationalGoal(
        kind="scan",
        threshold_percent="5",
        pending_question=localized("ask.scan_window", ConversationLanguage.ENGLISH, threshold="5%"),
    )
    first = confusion_recovery_reply(goal, language=ConversationLanguage.ENGLISH)
    second = confusion_recovery_reply(
        goal,
        language=ConversationLanguage.ENGLISH,
        previous_fingerprints=[first.fingerprint],
    )
    assert second.fingerprint != first.fingerprint
    assert second.message


# ---------------------------------------------------------------------------------
# 4. Language consistency
# ---------------------------------------------------------------------------------

LANGUAGE_FIXTURES: tuple[tuple[str, ConversationLanguage], ...] = (
    ("create me an alert to alert me when a coin increases 5%", ConversationLanguage.ENGLISH),
    ("what coins are up at least 5% now?", ConversationLanguage.ENGLISH),
    ("عايز تنبيه لما عملة تطلع 5%", ConversationLanguage.ARABIC),
    ("ما هي العملات الصاعدة 5% الآن؟", ConversationLanguage.ARABIC),
    ("3ayez alert lama coin yetla3 5%", ConversationLanguage.ARABIC),
    ("crée-moi une alerte quand une pièce monte de 5%", ConversationLanguage.FRENCH),
    ("quelles pièces montent d'au moins 5% maintenant ?", ConversationLanguage.FRENCH),
    ("créame una alerta cuando una moneda suba 5%", ConversationLanguage.SPANISH),
    ("¿qué monedas suben al menos 5% ahora?", ConversationLanguage.SPANISH),
)


@pytest.mark.parametrize(("message", "expected"), LANGUAGE_FIXTURES)
def test_the_reply_language_follows_the_user(
    message: str, expected: ConversationLanguage
) -> None:
    decision = resolve_conversation_language(message)
    assert decision.language is expected
    # And every sentence the server writes itself follows it.
    for key in ("ask.symbol_scope", "confusion.acknowledge", "refuse.generic"):
        assert response_matches_language(localized(key, decision.language), decision.language)


def test_a_turn_with_no_language_signal_keeps_the_conversation_language() -> None:
    """``??`` and ``ok`` must not reset an Arabic conversation to English."""

    for message in ("??", "ok", "5%", "BTCUSDT"):
        decision = resolve_conversation_language(message, session_language="ar")
        assert decision.language is ConversationLanguage.ARABIC, message
        assert decision.source == "session"


def test_an_intentional_switch_changes_the_language() -> None:
    decision = resolve_conversation_language(
        "show me which coins are up now please", session_language="ar"
    )
    assert decision.language is ConversationLanguage.ENGLISH
    assert decision.changed


def test_every_server_sentence_exists_in_every_language() -> None:
    """A missing translation is a conversation that switches language mid-thread."""

    assert translation_coverage() == {}


def test_a_wrong_language_reply_is_replaced_not_shipped() -> None:
    """The check the product never had: enforcement, not a prompt instruction."""

    english = "Nothing changed on this turn."
    assert enforce_language(english, ConversationLanguage.ENGLISH) == english
    replaced = enforce_language(english, ConversationLanguage.ARABIC)
    assert replaced != english
    assert response_matches_language(replaced, ConversationLanguage.ARABIC)


@pytest.mark.parametrize("language", list(ConversationLanguage))
def test_detection_and_rendering_agree(language: ConversationLanguage) -> None:
    sentence = localized("status.nothing_set_up", language)
    detected = detect_language(sentence)
    # Detection may legitimately decline on a short sentence, but it must never
    # actively disagree — that would flip the language on the following turn.
    assert detected in {language, None}


# ---------------------------------------------------------------------------------
# 5. One fact, one sentence, one question
# ---------------------------------------------------------------------------------


def test_the_same_fact_from_four_renderers_becomes_one_sentence() -> None:
    """The Case A wall of text: one unsupported item, four different explanations."""

    fact = Proposition("requirement", "unsupported", "needs a timeframe", "req-1")
    parts = [
        RenderedPart("HilalMarkets cannot follow that yet.", RenderSource.COMPOSER_CLAIM, fact),
        RenderedPart(
            "Not expressible exactly: needs a timeframe.",
            RenderSource.DETERMINISTIC_CLAIM,
            fact,
        ),
        RenderedPart(
            "I could not express this exactly: needs a timeframe.",
            RenderSource.DETERMINISTIC_SUMMARY,
            fact,
        ),
        RenderedPart("The request could not be expressed.", RenderSource.SAFE_ERROR, fact),
    ]
    reconciled = reconcile_reply(parts)
    assert reconciled.duplicate_count == 3
    assert reconciled.message == "HilalMarkets cannot follow that yet."


def test_the_clarification_is_appended_exactly_once() -> None:
    question = "Should I watch all screened coins or one specific coin?"
    reconciled = reconcile_reply(
        [
            RenderedPart(
                "Saved so far: a 5% rise.",
                RenderSource.COMPOSER_CLAIM,
                Proposition("draft", "holds", "5%"),
            )
        ],
        clarification=question,
    )
    assert reconciled.message.count(question) == 1


def test_a_turn_never_asks_two_questions() -> None:
    reconciled = reconcile_reply(
        [
            RenderedPart(
                "Which coin did you mean?",
                RenderSource.DETERMINISTIC_SUMMARY,
                Proposition("a", "b"),
            )
        ],
        clarification="Over what period should the 5% rise be measured?",
    )
    assert reconciled.message.count("?") == 1


def test_an_ordinary_reply_stays_short_and_keeps_the_question() -> None:
    parts = [
        RenderedPart(
            f"Sentence {index} about the draft and its current rules.",
            RenderSource.DETERMINISTIC_SUMMARY,
            Proposition(f"s{index}", "stated"),
        )
        for index in range(40)
    ]
    reconciled = reconcile_reply(parts, clarification="Which coin?")
    assert len(reconciled.message) <= 500
    assert reconciled.message.endswith("Which coin?")


def test_the_fingerprint_ignores_spacing_but_not_meaning() -> None:
    assert response_fingerprint("Yes, done.") == response_fingerprint("  yes,   DONE.  ")
    assert response_fingerprint("Yes.") != response_fingerprint("Yes?")
