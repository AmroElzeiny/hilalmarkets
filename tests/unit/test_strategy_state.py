"""Canonical typed strategy state.

Recompiling the joined setup text makes the *first* mention of a field win, so a
session that says `use the 15m` and later `actually make it 1h` still compiles 15m.
Run 20260725T122105Z covered this under `repeated_correction_cycles` and
`revert_correction`.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.strategy_state import (
    COLLECTION_FIELDS,
    STATE_FIELDS,
    FieldPatch,
    StrategyDraftState,
    canonical_compiler_text,
    is_reversion_request,
    patches_for_turn,
    revert_patches,
)
from ai_market_monitor.schemas.strategy import Comparator, StrategyDirection


def _play(*turns: str) -> StrategyDraftState:
    state = StrategyDraftState()
    for text in turns:
        state = state.apply(patches_for_turn(text, state))
    return state


def test_numbered_conversation_does_not_reverse_the_universe() -> None:
    state = _play(
        "Use ETHUSDT only and exclude BTCUSDT.",
        (
            "Answer directly:\n"
            "1) Watchlist scope: confirm ETHUSDT only.\n"
            "2) BTCUSDT is excluded.\n"
            "3) Explain the 15m context and 1h trigger."
        ),
    )
    assert state.value("include_symbols") == ("ETH/USDT",)
    assert state.value("exclude_symbols") == ("BTC/USDT",)
    assert state.value("base_timeframe") == "1h"
    assert state.value("context_timeframes") == ("15m",)


def test_failure_boundary_does_not_replace_primary_percentage_rule() -> None:
    state = _play(
        "Short move must be at most 1.0% on 5m.",
        "Exactly -1.0% passes; -1.01% fails.",
    )
    assert state.value("comparator") is Comparator.LESS_THAN_OR_EQUAL
    assert state.value("threshold") == 1.0


def test_the_latest_correction_wins_over_every_earlier_one() -> None:
    state = _play(
        "watch BTCUSDT on the 15m",
        "actually make it the 1h",
        "no, use the 4h",
    )
    assert state.value("base_timeframe") == "4h"


def test_a_field_the_turn_never_mentions_keeps_its_value() -> None:
    """Silence is not a correction. A later turn about direction must not erase the
    timeframe the user already chose."""
    state = _play("watch BTCUSDT on the 15m", "make it short")
    assert state.value("base_timeframe") == "15m"
    assert state.value("direction") is StrategyDirection.SHORT


def test_restating_the_same_value_records_no_patch() -> None:
    state = _play("watch BTCUSDT on the 15m", "yes, the 15m")
    assert len(state.history("base_timeframe")) == 1


def test_reversion_restores_the_exact_previous_value() -> None:
    state = _play("watch BTCUSDT on the 15m", "change it to the 1h")
    state = state.apply(revert_patches(state, fields=("base_timeframe",)))
    assert state.value("base_timeframe") == "15m"


def test_reversion_with_no_field_reverts_only_the_last_change() -> None:
    state = _play("watch BTCUSDT on the 15m", "make it short", "change it to the 1h")
    state = state.apply(revert_patches(state))
    assert state.value("base_timeframe") == "15m"
    assert state.value("direction") is StrategyDirection.SHORT


def test_reversion_with_nothing_to_restore_changes_nothing() -> None:
    """There is no earlier value to go back to, so no value is invented."""
    state = _play("watch BTCUSDT on the 15m")
    assert revert_patches(state, fields=("base_timeframe",)) == ()
    assert revert_patches(StrategyDraftState()) == ()


def test_reverting_twice_walks_back_through_the_history() -> None:
    state = _play("BTCUSDT on the 15m", "make it the 1h")
    state = state.apply(revert_patches(state, fields=("base_timeframe",)))
    assert state.value("base_timeframe") == "15m"
    state = state.apply(revert_patches(state, fields=("base_timeframe",)))
    assert state.value("base_timeframe") == "1h"


@pytest.mark.parametrize(
    "text",
    [
        "go back to the previous timeframe",
        "revert that",
        "undo that",
        "scratch that",
        "never mind",
        "put it back as it was",
        "restore the original setting",
        "forget the last change",
    ],
)
def test_reversion_wording_is_recognised(text: str) -> None:
    assert is_reversion_request(text) is True


@pytest.mark.parametrize(
    "text",
    ["make it the 1h", "watch BTCUSDT", "RSI below 30", "add SOLUSDT"],
)
def test_ordinary_instructions_are_not_reversions(text: str) -> None:
    assert is_reversion_request(text) is False


def test_exclusions_survive_many_later_turns() -> None:
    state = _play(
        "scan all coins but exclude BTCUSDT",
        "use the 1h",
        "make it short",
        "RSI below 30",
        "add a volume filter above 2x average",
    )
    assert state.value("exclude_symbols") == ("BTC/USDT",)


def test_a_second_exclusion_adds_to_the_first() -> None:
    state = _play("exclude BTCUSDT", "also exclude ETHUSDT")
    assert set(state.value("exclude_symbols")) == {"BTC/USDT", "ETH/USDT"}


def test_an_added_symbol_widens_the_universe() -> None:
    state = _play("watch BTCUSDT", "add SOLUSDT")
    assert set(state.value("include_symbols")) == {"BTC/USDT", "SOL/USDT"}


def test_only_narrows_the_universe_to_what_was_named() -> None:
    state = _play("watch BTCUSDT and ETHUSDT", "only SOLUSDT now")
    assert state.value("include_symbols") == ("SOL/USDT",)


def test_an_excluded_symbol_is_removed_from_the_universe() -> None:
    state = _play("watch BTCUSDT and ETHUSDT", "exclude ETHUSDT")
    assert "ETH/USDT" not in state.value("include_symbols")
    assert "ETH/USDT" in state.value("exclude_symbols")


def test_repeated_symbol_in_an_approval_line_cannot_cancel_a_hard_exclusion() -> None:
    state = _play(
        "watch LTCUSDT and exclude BTCUSDT",
        "APPROVE exclusion mapping BTCUSDT. EXCLUDE: BTCUSDT",
    )
    assert state.value("include_symbols") == ("LTC/USDT",)
    assert state.value("exclude_symbols") == ("BTC/USDT",)


def test_yes_no_question_does_not_exclude_the_symbol_after_the_slash() -> None:
    state = _play(
        "watch ETHUSDT only and exclude LTCUSDT",
        "Also yes/no: ETHUSDT only and LTCUSDT excluded?",
    )
    assert state.value("include_symbols") == ("ETH/USDT",)
    assert state.value("exclude_symbols") == ("LTC/USDT",)


def test_bare_excluded_asset_inherits_the_settled_quote() -> None:
    state = _play("calculate XRPUSDT only (no ETH)")
    assert state.value("include_symbols") == ("XRP/USDT",)
    assert state.value("exclude_symbols") == ("ETH/USDT",)


def test_including_a_previously_excluded_symbol_lifts_the_exclusion() -> None:
    """The newest statement about a symbol is the one that counts."""
    state = _play("all coins, exclude ETHUSDT", "stop excluding ETHUSDT")
    assert "ETH/USDT" not in state.value("exclude_symbols")


def test_include_and_exclude_can_never_disagree() -> None:
    state = _play("watch BTCUSDT, SOLUSDT and ETHUSDT", "drop ETHUSDT", "add ADAUSDT")
    include = set(state.value("include_symbols"))
    exclude = set(state.value("exclude_symbols"))
    assert include.isdisjoint(exclude)


def test_symbols_are_stored_in_canonical_pair_form() -> None:
    state = _play("watch BTCUSDT and ETH/USDT")
    for symbol in state.value("include_symbols"):
        assert symbol.count("/") == 1, symbol


def test_trigger_and_context_timeframes_are_kept_apart() -> None:
    state = _play("use 4h context and a 1h trigger")
    assert state.value("base_timeframe") == "1h"
    assert state.value("context_timeframes") == ("4h",)


def test_operator_and_threshold_corrections_both_apply() -> None:
    state = _play("move of at least 5%", "actually at most 2.5%")
    assert state.value("comparator") is Comparator.LESS_THAN_OR_EQUAL
    assert state.value("threshold") == pytest.approx(2.5)


def test_formula_is_typed_and_latest_field_corrections_overlay_it() -> None:
    state = _play(
        (
            "short on 15m where percent_change = "
            "(close_now - close_prev) / close_prev * 100 and operator lte 0.5%"
        ),
        "actually use operator gte 1%",
    )
    formula = state.value("formula")
    assert formula["formula"] == "close_to_close"
    assert formula["reference_field"] == "close"
    assert formula["current_field"] == "close"
    assert formula["timeframe"] == "15m"
    assert state.value("comparator") is Comparator.GREATER_THAN_OR_EQUAL
    assert state.value("threshold") == pytest.approx(1)


def test_operator_glossary_does_not_overwrite_the_settled_formula_comparator() -> None:
    state = _play(
        "long move_pct = (close/open - 1) gte 0.005 on 1m",
        (
            "above = current > level; below = current < level; "
            "at least = current gte level; at most = current lte level; "
            "crosses uses previous and current; sweeps means touch and reclaim"
        ),
    )
    assert state.value("comparator") is Comparator.GREATER_THAN_OR_EQUAL


def test_measurement_alternatives_do_not_overwrite_a_settled_formula() -> None:
    state = _play(
        "bullish move at most 7.5% on 1d",
        "Is the candle bullish (close > open), or is it enough that price rose?",
    )
    assert state.value("comparator") is Comparator.LESS_THAN_OR_EQUAL
    assert state.value("formula")["comparator"] == "lte"


def test_invalid_boolean_question_does_not_hide_a_settled_formula() -> None:
    state = _play(
        "bullish move at most 7.5% from daily open to current on 1d",
        "Which option: (a) close-to-close or (b) open-to-current?",
    )
    compiler_text = canonical_compiler_text(state, fallback="")
    assert "percentage_change" in compiler_text
    assert "7.5%" in compiler_text


def test_first_turn_measurement_choices_keep_the_declared_numeric_contract() -> None:
    state = _play(
        "bullish move of at most 7.5%: is it (a) open to current, "
        "(b) close to close, or (c) low to high? Direction is long on 1d."
    )
    assert state.value("comparator") is Comparator.LESS_THAN_OR_EQUAL
    assert state.value("threshold") == pytest.approx(7.5)
    assert state.value("formula")["comparator"] == "lte"


def test_precise_formula_patch_replaces_generic_ratio_in_the_same_turn() -> None:
    state = _play(
        "short bearish move at least 5% on 4h",
        "(4h open - 4h close) / 4h open >= 0.05",
    )
    assert state.value("threshold") == pytest.approx(5)
    assert len(state.history("threshold")) == 1


def test_only_fields_the_user_spoke_about_are_resolved() -> None:
    """The compiler must not receive a value the user never gave."""
    state = _play("watch BTCUSDT on the 1h")
    resolved = state.resolved()
    assert "include_symbols" in resolved
    assert "base_timeframe" in resolved
    assert "direction" not in resolved
    assert "threshold" not in resolved


def test_state_survives_a_json_round_trip() -> None:
    state = _play(
        "short SOLUSDT only, exclude BTCUSDT",
        "4h context and a 1h trigger",
        "at least 7.5%",
    )
    restored = StrategyDraftState.from_dict(state.to_dict())
    assert restored.resolved() == state.resolved()
    assert restored.value("direction") is StrategyDirection.SHORT
    assert restored.value("comparator") is state.value("comparator")
    assert restored.previous("base_timeframe") == state.previous("base_timeframe")


def test_an_unreadable_patch_is_dropped_without_losing_the_rest() -> None:
    state = _play("watch BTCUSDT on the 1h")
    payload = state.to_dict()
    payload["patches"].append({"field": "not_a_field", "value": "x", "turn": 9})
    restored = StrategyDraftState.from_dict(payload)
    assert restored.value("base_timeframe") == "1h"


@pytest.mark.parametrize("payload", [None, {}, {"patches": "nope"}, {"patches": [None]}])
def test_malformed_state_loads_as_empty(payload: object) -> None:
    state = StrategyDraftState.from_dict(payload)  # type: ignore[arg-type]
    assert state.patches == ()
    assert state.resolved() == {}


def test_every_collection_field_defaults_to_an_empty_tuple() -> None:
    state = StrategyDraftState()
    for name in STATE_FIELDS:
        expected = () if name in COLLECTION_FIELDS else None
        assert state.value(name) == expected
        assert state.previous(name) == expected


def test_patches_carry_the_turn_and_the_wording_that_caused_them() -> None:
    state = StrategyDraftState()
    patches = patches_for_turn("watch BTCUSDT on the 1h", state, turn=3)
    assert patches
    assert all(patch.turn == 3 for patch in patches)
    assert all("BTCUSDT" in patch.source_text for patch in patches)


def test_an_empty_turn_produces_no_patches() -> None:
    assert patches_for_turn("   ", StrategyDraftState()) == ()


def test_approval_is_bound_to_hash_version_and_conversation_snapshot() -> None:
    state = _play("watch BTCUSDT on the 1h")
    ready = state.with_compilation(
        canonical_hash="a" * 64,
        conversation_snapshot_hash="b" * 64,
    )
    assert ready.approval_state == "READY_FOR_CONFIRMATION"
    awaiting = ready.awaiting_approval()
    assert awaiting.approval_state == "AWAITING_APPROVAL"

    with pytest.raises(ValueError, match="hash"):
        awaiting.with_approval(
            canonical_hash="c" * 64,
            draft_version=awaiting.draft_version,
            conversation_snapshot_hash="b" * 64,
            user_id="user-1",
        )
    with pytest.raises(ValueError, match="version"):
        awaiting.with_approval(
            canonical_hash="a" * 64,
            draft_version=awaiting.draft_version + 1,
            conversation_snapshot_hash="b" * 64,
            user_id="user-1",
        )
    with pytest.raises(ValueError, match="snapshot"):
        awaiting.with_approval(
            canonical_hash="a" * 64,
            draft_version=awaiting.draft_version,
            conversation_snapshot_hash="d" * 64,
            user_id="user-1",
        )

    approved = awaiting.with_approval(
        canonical_hash="a" * 64,
        draft_version=awaiting.draft_version,
        conversation_snapshot_hash="b" * 64,
        user_id="user-1",
    )
    assert approved.approval_state == "APPROVED"
    assert approved.approved_user_id == "user-1"
    assert approved.approved_conversation_snapshot_hash == "b" * 64
    assert approved.mark_compiled().mark_activated().approval_state == "ACTIVATED"


def test_material_edit_invalidates_an_existing_approval() -> None:
    state = (
        _play("watch BTCUSDT on the 1h")
        .with_compilation(
            canonical_hash="a" * 64,
            conversation_snapshot_hash="b" * 64,
        )
        .awaiting_approval()
    )
    approved = state.with_approval(
        canonical_hash="a" * 64,
        draft_version=state.draft_version,
        conversation_snapshot_hash="b" * 64,
        user_id="user-1",
    )
    edited = approved.apply(patches_for_turn("make it the 4h", approved))

    assert edited.draft_version == approved.draft_version + 1
    assert edited.approval_state == "COLLECTING"
    assert edited.canonical_hash is None
    assert edited.approved_hash is None
    assert edited.approved_version is None
    assert edited.conversation_snapshot_hash is None
    assert edited.approved_conversation_snapshot_hash is None
    assert edited.approved_user_id is None


def test_blocking_compilation_can_never_reach_approval() -> None:
    blocked = _play("watch BTCUSDT").with_compilation(
        canonical_hash="a" * 64,
        conversation_snapshot_hash="b" * 64,
        unsupported_capabilities=("unknown_mechanic",),
    )
    assert blocked.approval_state == "NEEDS_CLARIFICATION"
    with pytest.raises(ValueError, match="complete reviewed draft"):
        blocked.awaiting_approval()


def test_history_is_ordered_oldest_first() -> None:
    state = _play("on the 15m", "make it the 1h", "no, the 4h")
    values = [patch.value for patch in state.history("base_timeframe")]
    assert values == ["15m", "1h", "4h"]


def test_a_patch_records_what_it_replaced() -> None:
    state = _play("on the 15m", "make it the 1h")
    latest = state.history("base_timeframe")[-1]
    assert isinstance(latest, FieldPatch)
    assert latest.previous_value == "15m"
    assert state.previous("base_timeframe") == "15m"


def test_formula_scale_factor_cannot_replace_the_authored_threshold() -> None:
    state = _play(
        "short ETHUSDT on 4h when the close-to-close move is at least 5%",
        "bearish_move_pct = (Cprev - C0) / Cprev * 100",
    )
    assert state.value("threshold") == pytest.approx(5)
    assert state.value("formula")["threshold_percent"] == pytest.approx(5)
    compiler_text = canonical_compiler_text(state, fallback="")
    assert "gte 5.0%" in compiler_text
    assert "gte 100" not in compiler_text


def test_structured_not_group_preserves_exclusion_isolation() -> None:
    state = _play(
        "ETHUSDT only and exclude XRPUSDT",
        (
            'AND(SYMBOL_GROUP(EQ(symbol, "ETHUSDT")), '
            'EXCLUDE_GROUP(NOT(EQ(symbol, "XRPUSDT"))))'
        ),
    )
    assert state.value("include_symbols") == ("ETH/USDT",)
    assert state.value("exclude_symbols") == ("XRP/USDT",)


def test_provider_contract_prose_does_not_enter_formula_compilation() -> None:
    state = _play(
        "short ETHUSDT on 4h when the close-to-close move is at least 5%",
        (
            "Provider requirements: 1m and 4h OHLCV fields with open_time, "
            "close_time, one venue, and a hard block when data is stale."
        ),
    )
    compiler_text = canonical_compiler_text(state, fallback="")
    assert "provider" not in compiler_text.casefold()
    assert "open_time" not in compiler_text
    assert "percentage_change gte 5.0%" in compiler_text


def test_live_confirmation_restatements_cannot_reintroduce_an_excluded_symbol() -> None:
    state = _play(
        (
            "I want ETHUSDT only, explicitly **NOT** LTCUSDT. Use 1h context "
            "and a 4h trigger. Short when close-to-close falls at least 0.5%."
        ),
        "Alright - quick setup.",
        "Can you confirm the logic?",
        "Next time the system prints the revision and hash.",
        "Keep LTCUSDT out, ETHUSDT-only.",
    )

    assert state.value("include_symbols") == ("ETH/USDT",)
    assert state.value("exclude_symbols") == ("LTC/USDT",)
    assert state.value("context_timeframes") == ("1h",)
    assert state.value("base_timeframe") == "4h"
    assert state.value("direction") is StrategyDirection.SHORT
    assert state.value("comparator") is Comparator.GREATER_THAN_OR_EQUAL
    assert state.value("threshold") == pytest.approx(0.5)


def test_approval_metadata_and_rejected_parameter_example_do_not_change_draft() -> None:
    original = _play(
        "ETHUSDT only, exclude LTCUSDT; use 1h context and trigger on 4h; "
        "short when the close-to-close move is at least 0.5%."
    )
    restated = original.apply(
        patches_for_turn(
            "Bind approval to the exact reviewed hash with no carry-over; "
            "do not invent setup_age_minutes=60.",
            original,
        )
    )

    assert restated.resolved() == original.resolved()
    assert restated.draft_version == original.draft_version


def test_threshold_only_correction_updates_canonical_formula_text() -> None:
    original = _play(
        "ETHUSDT only; use 1h context and trigger on 4h; "
        "short when close-to-close falls at least 0.5%."
    )
    corrected = original.apply(
        patches_for_turn("Change only the percentage threshold to 2.5%.", original)
    )

    compiler_text = canonical_compiler_text(corrected, fallback="")

    assert corrected.value("threshold") == 2.5
    assert "2.5%" in compiler_text
    assert "0.5%" not in compiler_text
