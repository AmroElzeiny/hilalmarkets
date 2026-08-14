"""Every OI-4 finding, as a permanent test named to its finding id.

These were promoted from the adversarial QA run's regression *candidates*. Each
one fails at ``211aecc5`` - the commit the findings were reported against - and
passes after the fixes in this branch.

The tests assert the **rule**, not the reported sentence. A fix that only helps
the exact wording in the finding must fail here: every rejection phrase is tried
against every setting, in every language the product accepts, and the
over-refusal cases are asserted just as hard as the under-refusal ones.

Symbols introduced by the fixes are imported inside the tests that need them, so
this file still imports cleanly against the commit the findings were reported
against. That matters for the promotion evidence: at ``211aecc5`` the behavioural
tests below fail on their own assertions - showing the defect - rather than all of
them collapsing into one collection error that proves nothing about behaviour.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.engine.strategy_state import StrategyDraftState, patches_for_turn
from ai_market_monitor.schemas.strategy import StrategyDirection


def _play(*turns: str) -> StrategyDraftState:
    state = StrategyDraftState()
    for index, turn in enumerate(turns, start=1):
        state = state.apply(patches_for_turn(turn, state, turn=index))
    return state


# ---------------------------------------------------------------------------
# OI4-001 - a question never authors or repoints a rule
# ---------------------------------------------------------------------------

#: Every shape of question a beginner actually types, including the ones with no
#: question mark at all. Punctuation is not what makes a question.
QUESTION_TURNS = (
    "What does RSI 30 mean, and is 15m the same as 15 minutes?",
    "Is 15m the same as 15 minutes?",
    "is 15m the same as 15 minutes",
    "What is a good timeframe, 15m or 1h?",
    "how does 15m context work?",
    "Can you explain why 15m is popular?",
    "Why do people use 15m",
    "Which is better, 15m or 4h?",
)


@pytest.mark.parametrize("question", QUESTION_TURNS)
def test_oi4_001_a_question_never_repoints_the_monitored_timeframe(question: str) -> None:
    state = _play("Watch RSI below 30 on 1h.", question)
    assert state.value("base_timeframe") == "1h", (
        f"the question {question!r} moved a live monitor onto a timeframe it only asked about"
    )


def test_oi4_001_the_readme_worked_example_holds() -> None:
    """The README states this guarantee with this exact sentence.

    Both halves matter. The question's values must not author, and the
    instruction's must - ignoring the whole turn would be the same defect wearing
    the opposite sign.
    """

    state = _play(
        "Watch BTCUSDT and LTCUSDT when RSI is below 30 on 1h.",
        "drop LTC, and is 5% a lot on a 15m candle?",
    )
    assert state.value("base_timeframe") == "1h", "the question's 15m became the trigger timeframe"
    assert state.value("threshold") is None, "the question's 5% became the threshold"
    assert "LTC/USDT" in (state.value("exclude_symbols") or ()), (
        "the instruction half of the turn stopped working; a question must be ignored, "
        "not the whole turn"
    )
    assert "BTC/USDT" in (state.value("include_symbols") or ())


def test_oi4_001_an_instruction_still_authors() -> None:
    """The guard must not silence real instructions."""

    state = _play("Watch RSI below 30 on 1h.", "Actually use 15m.")
    assert state.value("base_timeframe") == "15m"


# ---------------------------------------------------------------------------
# OI4-002 to OI4-005 - one rejection authority
# ---------------------------------------------------------------------------

#: The five phrasings from the OI-4 run.
REJECTION_PHRASINGS = (
    "Not {value}.",
    "Never use {value}.",
    "Don't use {value}.",
    "Anything but {value}.",
    "No {value}.",
)

#: The five settings from the OI-4 run. ``history`` establishes a different value
#: so an adopted refusal is visible, and ``refused`` is what must never land.
REFUSAL_SETTINGS = (
    ("base_timeframe", "Watch RSI below 30 on 1h.", "15m", "15m"),
    ("direction", "Watch RSI below 30 on 1h for long setups.", "short", StrategyDirection.SHORT),
    ("include_symbols", "Watch RSI below 30 on 1h.", "BTCUSDT", "BTC/USDT"),
    ("threshold", "Alert when the move is at least 2%.", "5%", 5.0),
    ("exchange", "Watch RSI below 30 on 1h on bybit.", "binance", "binance"),
)


@pytest.mark.parametrize("phrasing", REJECTION_PHRASINGS)
@pytest.mark.parametrize(
    ("field", "history", "spoken", "refused"),
    REFUSAL_SETTINGS,
    ids=[setting[0] for setting in REFUSAL_SETTINGS],
)
def test_oi4_003_to_005_a_refused_value_never_lands(
    phrasing: str, field: str, history: str, spoken: str, refused: object
) -> None:
    """All twenty-five combinations from the OI-4 run.

    Thirteen of these ended holding exactly what the trader refused. Every one of
    them must now end holding what they asked for, or nothing.
    """

    state = _play(history, phrasing.format(value=spoken))
    current = state.value(field)
    if field == "include_symbols":
        assert refused not in (current or ()), (
            f"{phrasing.format(value=spoken)!r} put the refused symbol on the watchlist"
        )
        return
    assert current != refused, (
        f"{phrasing.format(value=spoken)!r} left the draft holding the refused value"
    )


@pytest.mark.parametrize("phrasing", REJECTION_PHRASINGS)
def test_oi4_005_a_refused_symbol_is_excluded_rather_than_dropped(phrasing: str) -> None:
    """Refusing a symbol has a destination. Silence would lose the instruction."""

    state = _play("Watch RSI below 30 on 1h.", phrasing.format(value="BTCUSDT"))
    assert "BTC/USDT" in (state.value("exclude_symbols") or ()), (
        f"{phrasing.format(value='BTCUSDT')!r} neither included nor excluded the symbol"
    )


@pytest.mark.parametrize("word", ("drop", "exclude", "omit", "skip", "remove", "without", "no"))
def test_oi4_005_every_exclusion_word_removes_a_bare_asset(word: str) -> None:
    """One vocabulary, or the trader learns which synonym the product happens to know."""

    state = _play("Watch BTCUSDT and LTCUSDT when RSI is below 30 on 1h.", f"{word} LTC")
    assert "LTC/USDT" in (state.value("exclude_symbols") or ()), (
        f"{word!r} did not remove the asset the trader asked to remove"
    )


def test_oi4_005_an_indicator_name_is_never_turned_into_an_excluded_market() -> None:
    """``remove the RSI condition`` names a mechanic, not a coin."""

    state = _play("Watch BTCUSDT when RSI is below 30 on 1h.", "remove the RSI condition")
    assert "RSI/USDT" not in (state.value("exclude_symbols") or ())


@pytest.mark.parametrize(
    "turn",
    (
        "Great, thanks. One change though - use 1h, not 15m.",
        "not 15m, use 1h",
        "use 1h instead of 15m",
        "1h rather than 15m",
        "anything but 15m - make it 1h",
    ),
)
def test_oi4_002_a_two_value_correction_lands_the_accepted_value(turn: str) -> None:
    """Failing closed means landing nothing, never landing the refused value."""

    state = _play("Watch RSI below 30 on 15m.", turn)
    assert state.value("base_timeframe") == "1h", (
        f"{turn!r} did not adopt the timeframe the trader asked for"
    )


def test_oi4_002_a_refusal_with_no_replacement_clears_the_field_and_does_not_guess() -> None:
    """The trader refused what the draft holds and named nothing to replace it."""

    state = _play("Watch RSI below 30 on 15m.", "Not 15m.")
    assert state.value("base_timeframe") is None, (
        "the draft kept monitoring the one timeframe the trader ruled out"
    )


@pytest.mark.parametrize(
    ("turn", "expected"),
    (
        ("don't worry, use 15m", "15m"),
        ("no problem, use 15m", "15m"),
        ("I'm not sure, but use 15m", "15m"),
        ("there is no rush, use 15m", "15m"),
    ),
)
def test_oi4_003_refusal_wording_that_refuses_nothing_is_not_a_refusal(
    turn: str, expected: str
) -> None:
    """Over-refusal is its own defect and is tested as hard as under-refusal."""

    state = _play("Watch RSI below 30 on 1h.", turn)
    assert state.value("base_timeframe") == expected, (
        f"{turn!r} was read as refusing a value the trader was actually choosing"
    )


def test_oi4_003_a_negated_comparator_is_not_a_refused_threshold() -> None:
    """``not below 30`` compares. It does not refuse the number 30."""

    from ai_market_monitor.engine.rejection import rejects_following

    assert not rejects_following("alert me when RSI is not below ")
    assert not rejects_following("RSI is not above ")


#: The same refusal in Modern Standard Arabic, Egyptian Arabic and Arabizi.
REFUSAL_PREFIXES_BY_LANGUAGE = {
    "egyptian_mesh": "مش ",
    "arabic_la_use": "لا تستخدم ",
    "egyptian_balash": "بلاش ",
    "arabic_min_gheir": "من غير ",
    "arabizi_msh": "msh ",
    "arabizi_mish": "mish ",
    "arabizi_mesh_3ayez": "mesh 3ayez ",
    "arabizi_balash": "balash ",
}


@pytest.mark.parametrize("language", sorted(REFUSAL_PREFIXES_BY_LANGUAGE))
def test_oi4_003_a_refusal_is_understood_in_every_language_the_product_accepts(
    language: str,
) -> None:
    """A refusal written in Arabic, Egyptian Arabic or Arabizi refuses just as hard."""

    from ai_market_monitor.engine.rejection import rejects_following

    prefix = REFUSAL_PREFIXES_BY_LANGUAGE[language]
    assert rejects_following(prefix), (
        f"{language} wording was not read as a refusal, so the guard is off for it"
    )


def test_oi4_003_there_is_one_rejection_vocabulary_and_the_universe_list_is_part_of_it() -> None:
    """Two lists that drift apart is the failure this module exists to end."""

    from ai_market_monitor.engine.rejection import REJECTION_TERMS, UNIVERSE_EXCLUSION_TERMS

    missing = sorted(set(UNIVERSE_EXCLUSION_TERMS) - set(REJECTION_TERMS))
    assert not missing, (
        f"the universe exclusion list has drifted from the rejection vocabulary: {missing}"
    )


# ---------------------------------------------------------------------------
# OI4-006 - the capability guard never switches off
# ---------------------------------------------------------------------------

#: "Alert me when RSI drops below 30 on 15m", written four ways.
SAME_REQUEST_IN_EVERY_LANGUAGE = {
    "english": "Alert me when RSI drops below 30 on 15m",
    "arabic": (
        "راقب مؤشر RSI "
        "تحت 30 على "
        "فريم 15 دقيقة"
    ),
    "egyptian": (
        "نبهني لما "
        "الـ RSI ينزل "
        "تحت 30 على 15 "
        "دقيقة"
    ),
    "arabizi": "3ayez alert lama el RSI yenzel ta7t 30 3ala 15 de2i2a",
}


@pytest.mark.parametrize("language", sorted(SAME_REQUEST_IN_EVERY_LANGUAGE))
def test_oi4_006_capability_resolution_answers_in_every_supported_language(language: str) -> None:
    """The same request must resolve to the same capability whatever it is written in.

    When resolution returned nothing, the model's schema lost its ``capability_key``
    enum - so the guard against an invented capability was switched off for most of
    the audience this product is built for.
    """

    report = CapabilityResolver().resolve_prompt(SAME_REQUEST_IN_EVERY_LANGUAGE[language])
    assert "rsi_threshold" in report.candidate_keys, (
        f"{language} resolved to {list(report.candidate_keys)}, "
        "so the schema guard would be dropped"
    )


def test_oi4_006_an_empty_resolution_refuses_instead_of_dropping_the_enum() -> None:
    """There is no argument that builds a schema the model may fill in freely."""

    from ai_market_monitor.services.openai_interpreter import (
        CapabilityGuardUnavailable,
        _strategy_draft_schema,
    )

    with pytest.raises(CapabilityGuardUnavailable):
        _strategy_draft_schema([])

    def enums_for_capability_key(node: object) -> list[list[str]]:
        """Every ``capability_key`` anywhere in the schema, however it is nested."""

        found: list[list[str]] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "capability_key" and isinstance(value, dict):
                    found.append(value.get("enum", []))
                found.extend(enums_for_capability_key(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(enums_for_capability_key(item))
        return found

    enums = enums_for_capability_key(_strategy_draft_schema(["rsi_threshold"]))
    assert enums, "the schema no longer has a capability_key to constrain"
    for enum in enums:
        assert enum == ["rsi_threshold"], (
            "a capability_key in the schema is unconstrained, so the model may name "
            "a capability that does not exist"
        )


# ---------------------------------------------------------------------------
# OI4-007 - an unsupported request is refused by name
# ---------------------------------------------------------------------------

UNSUPPORTED_REQUESTS = (
    ("leverage_and_margin", "Watch BTCUSDT with 10x leverage on 15m."),
    ("trade_execution", "Set a stop loss at 2% and take profit at 5%."),
    ("trade_execution", "Can you place the order for me?"),
    ("stocks_and_forex", "Watch Apple stock and EURUSD when RSI is under 30."),
    ("stocks_and_forex", "Watch EURUSD when RSI is under 30."),
    ("buy_sell_recommendations", "Just tell me which coin to buy right now."),
)


@pytest.mark.parametrize(("expected_key", "turn"), UNSUPPORTED_REQUESTS)
def test_oi4_007_an_unsupported_request_is_refused_by_name(expected_key: str, turn: str) -> None:
    from ai_market_monitor.engine.strategy_state import unsupported_capability_requests

    refusals = {refusal.key for refusal in unsupported_capability_requests(turn)}
    assert expected_key in refusals, (
        f"{turn!r} produced {sorted(refusals)} instead of naming {expected_key}"
    )


@pytest.mark.parametrize(("expected_key", "turn"), UNSUPPORTED_REQUESTS)
def test_oi4_007_an_unsupported_request_leaves_no_value_on_the_draft(
    expected_key: str, turn: str
) -> None:
    """A refusal that still fills in half the draft looks to a customer like it worked."""

    del expected_key
    patches = patches_for_turn(turn, StrategyDraftState(), turn=1)
    kept = {
        patch.field: patch.value
        for patch in patches
        if patch.field not in {"mechanic_fragments", "boolean_groups", "formula_fragments"}
    }
    assert kept == {}, f"{turn!r} was refused and still wrote {kept} to the draft"


def test_oi4_007_a_currency_pair_is_never_accepted_as_a_crypto_market() -> None:
    """EURUSD has the shape of a symbol this product understands and is not one."""

    from ai_market_monitor.engine.turn_fragments import extract_symbols

    assert extract_symbols("Watch EURUSD when RSI is under 30.") == ()
    assert extract_symbols("Watch BTCUSDT when RSI is under 30.") == ("BTCUSDT",)


def test_oi4_007_a_supported_request_is_never_refused() -> None:
    """The refusal must not fire on the product working normally."""

    from ai_market_monitor.engine.strategy_state import unsupported_capability_requests

    for turn in (
        "Watch BTCUSDT when RSI is under 30 on 15m.",
        "Alert me when ETHUSDT crosses above the 200 EMA.",
        "Monitor BTC/USDT when the 15m candle rises by at least 3%",
    ):
        assert unsupported_capability_requests(turn) == (), f"{turn!r} was refused wrongly"


def test_oi4_007_every_refusable_boundary_can_actually_be_asked_for() -> None:
    """A boundary nobody can trigger refuses nothing.

    Not every unsupported entry needs wording - some are only ever reached through
    a settings page - but the four the adversarial run exercised must be reachable
    from a sentence, or the refusal is decorative.
    """

    from ai_market_monitor.core.product_boundaries import BOUNDARY_REGISTRY, SupportState

    reachable = {entry.key for entry in BOUNDARY_REGISTRY if entry.request_phrases}
    assert {
        "leverage_and_margin",
        "trade_execution",
        "stocks_and_forex",
        "buy_sell_recommendations",
    } <= reachable
    for entry in BOUNDARY_REGISTRY:
        if entry.support is SupportState.SUPPORTED:
            assert not entry.request_phrases, (
                f"{entry.key} is supported and must not carry refusal wording"
            )
