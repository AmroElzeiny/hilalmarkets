"""Deterministic turn-fragment classification.

These cases are drawn from the evaluator run 20260723T152343Z, where every one of
the nine backend cases looped on clarification questions because symbols,
timeframes, thresholds and even the word ``APPROVED`` were routed into capability
resolution as if they were unrecognised market mechanics.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.capability_index import get_capability_index
from ai_market_monitor.engine.formula_compiler import (
    compile_percentage_formula,
    parse_percentage_formula,
)
from ai_market_monitor.engine.turn_fragments import (
    classify_fragment,
    classify_turn,
    detect_comparator,
    detect_direction,
    extract_explicit_exclusions,
    extract_symbols,
    extract_timeframe_roles,
    extract_timeframes,
    is_approval_instruction,
)
from ai_market_monitor.schemas.strategy import Comparator, StrategyDirection


@pytest.mark.parametrize(
    "symbol",
    ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT", "LTCUSDT"],
)
def test_known_symbols_are_never_unknown_capabilities(symbol: str) -> None:
    fragment = classify_fragment(symbol)
    assert fragment.kind == "symbol"
    assert fragment.enters_capability_resolution is False
    assert fragment.symbols == (symbol,)


@pytest.mark.parametrize(
    "text",
    ["BTCUSDT", "BTC/USDT", "BTC-USDT", "BTC_USDT", "btcusdt"],
)
def test_symbol_separator_forms_normalise_to_one_market(text: str) -> None:
    assert extract_symbols(text) == ("BTCUSDT",)


def test_duplicated_quote_asset_is_never_created_as_a_market() -> None:
    assert extract_symbols("watch BTCUSDTUSDT") == ()


def test_symbols_do_not_reach_the_capability_resolver() -> None:
    """The resolver produced `What do you mean by 'ETHUSDT' in this setup?` before."""
    report = get_capability_index().resolver.resolve_prompt("Watch ETHUSDT only")
    assert report.fragments == ()


@pytest.mark.parametrize(
    "word",
    ["entry", "country", "industry", "registry", "sentry", "poetry", "amateur", "teeth"],
)
def test_english_words_ending_in_a_quote_asset_are_not_symbols(word: str) -> None:
    """`ENTRY` ends in TRY and `AMATEUR` in EUR; neither names a market."""
    assert extract_symbols(word) == ()
    assert classify_fragment(f"the {word} price").kind == "trading_condition"


@pytest.mark.parametrize(
    ("text", "included", "excluded"),
    [
        ("BTCUSDT only, exclude ETHUSDT", ("BTCUSDT",), ("ETHUSDT",)),
        ("ADAUSDT only and exclude XRPUSDT", ("ADAUSDT",), ("XRPUSDT",)),
        ("SOLUSDT only with BTCUSDT never included", ("SOLUSDT",), ("BTCUSDT",)),
        ("BTCUSDT with LTCUSDT fully excluded", ("BTCUSDT",), ("LTCUSDT",)),
    ],
)
def test_exclusions_are_retained_and_kept_out_of_the_universe(
    text: str, included: tuple[str, ...], excluded: tuple[str, ...]
) -> None:
    report = classify_turn(text)
    assert report.symbols == included
    assert report.excluded_symbols == excluded
    assert set(report.symbols).isdisjoint(report.excluded_symbols)


def test_only_is_an_inclusion_not_an_exclusion() -> None:
    report = classify_turn("BTCUSDT only")
    assert report.symbols == ("BTCUSDT",)
    assert report.excluded_symbols == ()


@pytest.mark.parametrize(
    ("phrase", "comparator"),
    [
        ("above", Comparator.GREATER_THAN),
        ("below", Comparator.LESS_THAN),
        ("at least", Comparator.GREATER_THAN_OR_EQUAL),
        ("at most", Comparator.LESS_THAN_OR_EQUAL),
        ("crosses above", Comparator.CROSSES_ABOVE),
        ("crosses below", Comparator.CROSSES_BELOW),
        ("greater than", Comparator.GREATER_THAN),
        ("less than", Comparator.LESS_THAN),
        ("no more than", Comparator.LESS_THAN_OR_EQUAL),
        ("no less than", Comparator.GREATER_THAN_OR_EQUAL),
    ],
)
def test_operator_wording_maps_to_exact_comparators(phrase: str, comparator: Comparator) -> None:
    assert detect_comparator(f"price {phrase} the level") is comparator


def test_longer_operator_phrases_win_over_their_substrings() -> None:
    """`at least` must never degrade to `least`, `crosses above` never to `above`."""
    assert detect_comparator("at least 5%") is Comparator.GREATER_THAN_OR_EQUAL
    assert detect_comparator("at most 5%") is Comparator.LESS_THAN_OR_EQUAL
    assert detect_comparator("crosses above the 20 EMA") is Comparator.CROSSES_ABOVE
    assert detect_comparator("crosses below the 20 EMA") is Comparator.CROSSES_BELOW


def test_sweep_is_recorded_distinctly_from_a_plain_cross() -> None:
    sweep = classify_turn("sweeps the previous day low")
    cross = classify_turn("crosses below the previous day low")
    assert sweep.is_sweep is True
    assert cross.is_sweep is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("15m", ("15m",)),
        ("4h", ("4h",)),
        ("1d", ("1d",)),
        ("60 minutes", ("1h",)),
        ("15m trigger with 1m context", ("15m", "1m")),
    ],
)
def test_timeframes_are_extracted_and_canonicalised(text: str, expected: tuple[str, ...]) -> None:
    assert extract_timeframes(text) == expected


def test_context_and_trigger_timeframes_are_both_captured_in_order() -> None:
    """`15m trigger with 1m context` must not collapse or swap the two roles."""
    report = classify_turn("15m trigger with 1m context")
    assert report.timeframes == ("15m", "1m")
    assert all(item.enters_capability_resolution is False for item in report.fragments)


@pytest.mark.parametrize(
    ("text", "trigger", "context"),
    [
        ("We'll use 4h context and a 1h trigger", "1h", ("4h",)),
        ("1h trigger with 4h context", "1h", ("4h",)),
        ("15m trigger, 1m context", "15m", ("1m",)),
        ("4h trigger with 1m context", "4h", ("1m",)),
        ("use the 1d for bias and fire on the 1h", "1h", ("1d",)),
        ("5m used only as context; 1m is the trigger timeframe", "1m", ("5m",)),
        ("bias from the daily, entry on the 4h", "4h", ("1d",)),
    ],
)
def test_trigger_and_context_timeframe_roles_are_separated(
    text: str, trigger: str, context: tuple[str, ...]
) -> None:
    """Whichever timeframe is written first must not become the trigger by default.

    Run 20260725T122105Z compiled `4h context and a 1h trigger` with
    base_timeframe=4h and supporting_timeframes=[], losing the 1h trigger entirely.
    """
    roles = extract_timeframe_roles(text)
    assert roles.trigger == trigger
    assert roles.context == context


def test_timeframe_without_a_role_word_is_left_unassigned() -> None:
    """No role wording means no claim about roles; the caller keeps its default."""
    roles = extract_timeframe_roles("RSI below 30 on the 15m")
    assert roles.trigger is None
    assert roles.context == ()
    assert roles.resolved is False


@pytest.mark.parametrize(
    ("text", "direction"),
    [
        ("long", StrategyDirection.LONG),
        ("short", StrategyDirection.SHORT),
        ("bullish", StrategyDirection.LONG),
        ("bearish", StrategyDirection.SHORT),
        ("both directions", StrategyDirection.BOTH),
    ],
)
def test_direction_words_map_deterministically(text: str, direction: StrategyDirection) -> None:
    assert detect_direction(text) is direction


@pytest.mark.parametrize(
    "text",
    [
        "APPROVED",
        "approved",
        "I approve",
        "✅ APPROVED. Now finalize the watchlist using those exact rules.",
        "go ahead",
        "yes, proceed",
    ],
)
def test_explicit_approval_is_detected_semantically(text: str) -> None:
    assert is_approval_instruction(text) is True
    assert classify_turn(text).is_approval is True


@pytest.mark.parametrize(
    "text",
    [
        "موافق",
        "أوافق",
        "تمت الموافقة",
        "نعم، أوافق على القواعد",
        "تمام، اعتمد القواعد",
    ],
)
def test_arabic_approval_is_detected_deterministically(text: str) -> None:
    assert is_approval_instruction(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Do not set/confirm the final trigger rules until I say “Approved”.",
        "Does this need my approval?",
        "wait for my explicit GO before creating or activating the alert",
        "approval needs to be explicit; don't auto-approve anything",
        "I want it to require my approval before it activates",
        "never approve this automatically",
    ],
)
def test_requests_for_approval_gating_are_not_approvals(text: str) -> None:
    """Asking for an approval gate must not itself be read as granting approval."""
    assert is_approval_instruction(text) is False
    assert classify_turn(text).is_approval is False


@pytest.mark.parametrize(
    "text",
    [
        (
            "Approval should apply only to the exact reviewed version and hash. "
            "Confirm the logic and then I'll review/approve it."
        ),
        "I will approve after you show the exact draft hash.",
        "The approved version must not carry over after a material change.",
        "Make approval explicit and bind it to this version.",
    ],
)
def test_future_approval_and_approval_policy_do_not_grant_approval(text: str) -> None:
    assert is_approval_instruction(text) is False
    assert classify_turn(text).is_approval is False


@pytest.mark.parametrize(
    "text",
    ["hi", "thanks", "ok", "that looks right", "got it", "sounds good"],
)
def test_conversational_text_is_not_a_trading_condition(text: str) -> None:
    fragment = classify_fragment(text)
    assert fragment.kind == "conversational"
    assert fragment.enters_capability_resolution is False


@pytest.mark.parametrize(
    "text",
    [
        "Your last reply dodged the actual rules",
        "Stop asking generic questions",
        "Bro, you are looping",
        "Re-sending it clean, no internal-error nonsense",
        "The last reply was blank",
    ],
)
def test_process_discussion_never_becomes_a_trading_mechanic(text: str) -> None:
    fragment = classify_fragment(text)
    assert fragment.category == "CONVERSATIONAL_TEXT"
    assert fragment.enters_capability_resolution is False


def test_market_snapshot_request_does_not_become_a_trading_mechanic() -> None:
    report = classify_turn(
        "Show me how the market looks, then find coins with RSI below 30 on 15m."
    )
    assert report.fragments[0].category == "CONVERSATIONAL_TEXT"
    assert [item.text for item in report.trading_conditions] == [
        "then find coins with RSI below 30 on 15m"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "RSI below 30",
        "price crosses above the 20 EMA",
        "volume 3x the average",
        "head and shoulders neckline break",
    ],
)
def test_real_trading_conditions_still_reach_capability_resolution(text: str) -> None:
    """Unrecognised wording is exactly what the registry exists to interpret."""
    fragment = classify_fragment(text)
    assert fragment.kind == "trading_condition"
    assert fragment.enters_capability_resolution is True


def test_core_percentage_move_bypasses_capability_resolution() -> None:
    fragment = classify_fragment("bullish move of at least 2.5% on the 1m")
    assert fragment.category == "FORMULA"
    assert fragment.enters_capability_resolution is False


def test_complete_percentage_formula_bypasses_capability_resolution() -> None:
    fragment = classify_fragment("coins up 5% today")
    assert fragment.category == "FORMULA"
    assert fragment.enters_capability_resolution is False

    spec = parse_percentage_formula(
        fragment.text,
        default_timeframe="15m",
        default_direction=StrategyDirection.BOTH,
    )
    assert spec is not None
    condition = compile_percentage_formula(spec)
    assert condition.left.name == "percentage_change"
    assert condition.left.parameters["formula"] == "reference_to_current"
    assert condition.left.parameters["reference_timeframe"] == "1d"
    assert condition.comparator is Comparator.GREATER_THAN_OR_EQUAL
    assert condition.right.value == pytest.approx(5)


def test_ratio_formula_normalizes_decimal_to_percent_without_a_false_price_rule() -> None:
    spec = parse_percentage_formula(
        "(4h open - 4h close) / 4h open >= 0.05",
        default_timeframe="4h",
        default_direction=StrategyDirection.SHORT,
    )
    assert spec is not None
    assert spec.formula == "open_to_close"
    assert spec.direction == "down"
    assert spec.comparator is Comparator.GREATER_THAN_OR_EQUAL
    assert spec.threshold_percent == pytest.approx(5)


def test_threshold_is_parsed_without_consuming_a_timeframe() -> None:
    report = classify_turn("at least 7.5% on the 15m")
    assert report.threshold == pytest.approx(7.5)
    assert report.comparator is Comparator.GREATER_THAN_OR_EQUAL
    assert "15m" in report.timeframes


def test_approval_word_is_not_an_unknown_capability() -> None:
    """`APPROVED` previously produced `What do you mean by 'APPROVED' in this setup?`."""
    assert get_capability_index().resolver.resolve_prompt("APPROVED").fragments == ()


def test_symbolic_comparator_is_not_overwritten_by_meta_exactly() -> None:
    report = classify_turn("Short when the bearish move is ≤ 0.5%. Restate the config exactly.")
    assert report.comparator is Comparator.LESS_THAN_OR_EQUAL
    assert report.threshold == pytest.approx(0.5)


def test_explicit_include_and_exclude_labels_do_not_swap_symbols() -> None:
    report = classify_turn(
        "symbol: ADAUSDT - excluded_symbol: XRPUSDT - operator: lte - threshold: 7.5%"
    )
    assert report.symbols == ("ADAUSDT",)
    assert report.excluded_symbols == ("XRPUSDT",)


def test_arabic_universe_markers_are_structural() -> None:
    report = classify_turn("BTCUSDT فقط، واستبعاد LTCUSDT")
    assert report.symbols == ("BTCUSDT",)
    assert report.excluded_symbols == ("LTCUSDT",)


def test_labeled_timeframes_bind_to_the_preceding_role() -> None:
    roles = extract_timeframe_roles("Context: 15m - Trigger: 1d - Direction: short")
    assert roles.context == ("15m",)
    assert roles.trigger == "1d"


def test_both_timeframes_is_not_misread_as_both_directions() -> None:
    assert detect_direction("provider must return both 1m and 4h series") is None


def test_timeframe_labels_and_arabic_link_words_preserve_roles() -> None:
    labeled = extract_timeframe_roles("Context timeframe: 1m - Trigger timeframe: 1d")
    assert labeled.context == ("1m",)
    assert labeled.trigger == "1d"

    underscored = extract_timeframe_roles("context_timeframe: 1m - trigger_timeframe: 1d")
    assert underscored.context == ("1m",)
    assert underscored.trigger == "1d"

    arabic = extract_timeframe_roles("15m context + Trigger على 1m")
    assert arabic.context == ("15m",)
    assert arabic.trigger == "1m"


def test_a_later_explicit_exclusion_wins_over_an_earlier_symbol_mention() -> None:
    assert extract_explicit_exclusions("APPROVE exclusion mapping BTCUSDT. EXCLUDE: BTCUSDT") == (
        "BTCUSDT",
    )


@pytest.mark.parametrize(
    "text",
    [
        "LTCUSDT must not appear anywhere",
        "Exclusions: only XRPUSDT",
        "calculate XRPUSDT only (no ETHUSDT)",
    ],
)
def test_direct_exclusion_wording_never_becomes_an_inclusion(text: str) -> None:
    assert extract_explicit_exclusions(text)


@pytest.mark.parametrize(
    "text",
    [
        "I want ETHUSDT only, explicitly **NOT** LTCUSDT.",
        "Keep LTCUSDT out and use ETHUSDT only.",
        "Keeping XRPUSDT out; scan SOLUSDT only.",
    ],
)
def test_formatted_and_keep_out_exclusions_bind_to_the_named_symbol(text: str) -> None:
    report = classify_turn(text)
    assert report.excluded_symbols
    assert set(report.symbols).isdisjoint(report.excluded_symbols)
    assert all(
        symbol not in report.symbols
        for symbol in report.excluded_symbols
    )


@pytest.mark.parametrize(
    "text",
    [
        "Yeah, let's not overcomplicate the BTC part.",
        "Not heavy formulas?",
        "No more questions.",
        "It ensures we're not accidentally mixing other pairs/data.",
        "Let's keep the explanation short and readable.",
        "That just clarifies why the two timeframes stay separate.",
        "Alright - quick setup.",
        "Can you confirm the logic?",
        "Next time the system prints the revision and hash.",
        "Could you summarize the current setup?",
        "This time the app displays the version.",
        "Brief recap.",
        "Nah, that's not clean enough.",
        "Yep - clean confirm.",
        "In one concise block.",
        "You need to confirm this cleanly.",
        "You already have the context requirement; answer it.",
        "Confirmation block (final).",
        "No carry-over.",
        "Bind it to the exact reviewed version and canonical hash.",
        "If the draft hash changes, approval must be re-requested.",
        "Do not introduce any extra setup_age_minutes=60 assumption.",
        "No extra made-up parameters like setup_age_minutes=60.",
        "Don't introduce any other context rule or assumption.",
    ],
)
def test_conversational_qualifiers_never_create_strategy_mechanics(text: str) -> None:
    report = classify_turn(text)
    assert report.trading_conditions == ()
    assert all(fragment.enters_capability_resolution is False for fragment in report.fragments)


@pytest.mark.parametrize(
    "text",
    [
        "Before we compile, give me the short version.",
        "No additional indicators.",
        "Without extra rules, please.",
        "This keeps us from mixing pairs accidentally.",
        "Let's avoid overcomplicating that market part.",
    ],
)
def test_unseen_conversation_paraphrases_remain_non_blocking(text: str) -> None:
    report = classify_turn(text)
    assert report.trading_conditions == ()


def test_one_explicit_trigger_label_assigns_other_timeframe_as_context() -> None:
    roles = extract_timeframe_roles("Return the 1m setup / 1h trigger parameters.")
    assert roles.trigger == "1h"
    assert roles.context == ("1m",)


def test_quoted_approval_prompt_is_not_a_market_formula() -> None:
    report = classify_turn(
        'Ask: "Reply CONFIRM to finalize the 1H bias + 5M trigger (+1.0%)."'
    )
    assert report.trading_conditions == ()


def test_confirming_a_fully_excluded_symbol_preserves_exclusion_intent() -> None:
    text = "ADAUSDT: confirm 100% excluded: yes/no."
    assert extract_explicit_exclusions(text) == ("ADAUSDT",)
    report = classify_turn(text)
    assert all(not fragment.enters_capability_resolution for fragment in report.fragments)


@pytest.mark.parametrize(
    ("text", "symbol"),
    [
        ("\u0648\u0623\u0633\u062a\u0628\u0639\u062f XRPUSDT", "XRPUSDT"),
        (
            "\u0627\u0633\u062a\u0628\u0639\u062f LTCUSDT "
            "\u0628\u0634\u0643\u0644 \u0635\u0631\u064a\u062d",
            "LTCUSDT",
        ),
    ],
)
def test_arabic_exclusions_round_trip_without_becoming_inclusions(
    text: str,
    symbol: str,
) -> None:
    report = classify_turn(text)
    assert report.excluded_symbols == (symbol,)
    assert symbol not in report.symbols


def test_approval_policy_and_rejected_examples_cannot_mutate_strategy_fields() -> None:
    report = classify_turn(
        "Approval is bound to the exact reviewed hash; do not carry it over. "
        "Do not invent setup_age_minutes=60."
    )

    assert report.trading_conditions == ()
    assert report.comparator is None
    assert report.threshold is None
    assert report.timeframes == ()
    assert report.is_approval is False


def test_context_and_trigger_restatement_is_configuration_not_a_new_mechanic() -> None:
    report = classify_turn(
        "Context/setup: use 1h data to evaluate setup validity; "
        "trigger/signal: compute on 4h; "
        "condition: SHORT when bearish move % >= 0.5."
    )

    assert report.trading_conditions == ()
    assert report.timeframes == ("1h", "4h")
    assert report.direction is StrategyDirection.SHORT
    assert report.comparator is Comparator.GREATER_THAN_OR_EQUAL
    assert report.threshold == pytest.approx(0.5)


@pytest.mark.parametrize(
    "text",
    [
        "\u0623\u0648\u0627\u0641\u0642",
        "\u0646\u0639\u0645\u060c \u0623\u0648\u0627\u0641\u0642 "
        "\u0639\u0644\u0649 \u0627\u0644\u0642\u0648\u0627\u0639\u062f",
        "\u062a\u0645\u062a \u0627\u0644\u0645\u0648\u0627\u0641\u0642\u0629",
        "mowafe2",
        "tamam, e3temed",
    ],
)
def test_real_arabic_and_arabizi_approval_round_trip(text: str) -> None:
    assert is_approval_instruction(text) is True
    assert classify_turn(text).is_approval is True


@pytest.mark.parametrize(
    "text",
    [
        "Please reply exactly: I approve.",
        "I will not proceed without your explicit go-ahead.",
        "Ask me to type approved after the preview.",
    ],
)
def test_quoted_or_requested_approval_never_grants_approval(text: str) -> None:
    assert is_approval_instruction(text) is False


def test_function_style_not_group_is_an_explicit_exclusion() -> None:
    text = 'AND(SYMBOL_GROUP(EQ(symbol, "ETHUSDT")), EXCLUDE_GROUP(NOT(EQ(symbol, "XRPUSDT"))))'
    assert extract_explicit_exclusions(text) == ("XRPUSDT",)


def test_formula_scale_factor_is_not_a_threshold() -> None:
    report = classify_turn("move_pct = ((end - start) / start) * 100")
    assert report.threshold is None


def test_named_canonical_field_correction_does_not_enter_capability_resolution() -> None:
    report = classify_turn("Change only the percentage threshold to 2.5%.")

    assert report.trading_conditions == ()
    assert report.threshold == pytest.approx(2.5)
    assert report.fragments[0].category == "THRESHOLD"
