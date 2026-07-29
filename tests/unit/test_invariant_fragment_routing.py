"""INV-04: only wording that carries a market mechanic enters capability resolution.

Every fragment below was reported as a *blocking* `instruction_not_converted`
trading capability by run 20260727T081613Z. None of them describes a market
mechanic, so no answer the trader could give would ever clear the finding — which
left the draft permanently ineligible for approval.

The cases assert the classification rule, not the sentences: approval gating,
labelling policy, rollback requests, open questions and instructions about the
conversation are separate categories, and only `TRADING_MECHANIC` reaches the
resolver.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.turn_fragments import (
    classify_fragment,
    classify_turn,
    names_market_mechanic,
)


@pytest.mark.parametrize(
    ("text", "category"),
    [
        # Approval is application state, never a trading capability (INV-10).
        ("don't sneak in any auto-approval", "APPROVAL_INSTRUCTION"),
        ("I need explicit approval from you before we finalize", "APPROVAL_INSTRUCTION"),
        ("do not lock/finalize until I say approved", "APPROVAL_INSTRUCTION"),
        ("absolutely no auto-greenlight", "APPROVAL_INSTRUCTION"),
        ("approval must stay explicit", "APPROVAL_INSTRUCTION"),
        # Sharia and labelling policy is enforced by the platform, not compiled.
        ("no extra tags/labels/statuses of any kind", "PRODUCT_POLICY"),
        ("do not attach any religious status", "PRODUCT_POLICY"),
        ("confirm you will not assign any ethical status", "PRODUCT_POLICY"),
        # Rolling state back is a state operation (INV-09).
        ("just roll back cleanly to the previous state", "REVERSION"),
        ("revert to the previous value", "REVERSION"),
        # An open choice records a decision the trader has not made (INV-02).
        ("the precise trigger definition on 1m (close-to-close vs high/low)", "DECISION_REQUEST"),
        ("confirm whether it's a single trigger-bar close", "DECISION_REQUEST"),
        # Instructions about the dialogue itself.
        ("it must be measurable", "CONVERSATION_CONTROL"),
        ("I won't guess N", "CONVERSATION_CONTROL"),
        ("quick check: if I change my mind", "CONVERSATION_CONTROL"),
        ("if you later change any parameter", "CONVERSATION_CONTROL"),
    ],
)
def test_non_mechanic_wording_never_enters_capability_resolution(
    text: str, category: str
) -> None:
    fragment = classify_fragment(text)
    assert fragment.category == category, fragment.category
    assert fragment.enters_capability_resolution is False


@pytest.mark.parametrize(
    "text",
    [
        "on 15m require a bearish move of at least 1.0%",
        "RSI must be under 30 on the 1h",
        "volume at least 2x the 20 candle average",
        "price crosses above the 50 EMA",
        "notify me on a liquidity sweep of the previous day low",
        "the 4h candle must close above the weekly open",
    ],
)
def test_market_mechanics_still_enter_capability_resolution(text: str) -> None:
    """Real conditions are either resolved or compiled as deterministic formulas."""
    fragment = classify_fragment(text)
    assert fragment.category in {"FORMULA", "TRADING_MECHANIC"}
    assert fragment.enters_capability_resolution is (fragment.category == "TRADING_MECHANIC")


@pytest.mark.parametrize(
    "text",
    [
        "confirm you will not attach any labels/statuses to LTCUSDT",
        "no religious status for BTCUSDT please",
    ],
)
def test_policy_wording_naming_a_symbol_does_not_edit_the_universe(text: str) -> None:
    """`don't label LTCUSDT` protects LTC; reading the negation as an exclusion
    would drop the one asset the trader asked for (INV-05)."""
    fragment = classify_fragment(text)
    assert fragment.excluded_symbols == ()
    assert fragment.symbols == ()


@pytest.mark.parametrize(
    "text",
    [
        "don't sneak in any auto-approval, I need explicit approval before we finalize",
        "absolutely no auto-greenlight; you only confirm after I say I approve",
        "do not finalize until I say approved",
        "wait for my approval before activating",
    ],
)
def test_describing_the_approval_gate_never_grants_approval(text: str) -> None:
    """The model may request approval but can never grant it (INV-10)."""
    assert classify_turn(text).is_approval is False


@pytest.mark.parametrize(
    "text",
    ["approved", "I approve", "yes, approve", "approved - go ahead"],
)
def test_an_actual_approval_is_still_recognised(text: str) -> None:
    assert classify_turn(text).is_approval is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("if I change my mind", False),
        ("change the watchlist", True),
        ("percent change of 5%", True),
        ("% change over the last hour", True),
        ("price change since the open", True),
        ("RSI below 30", True),
        ("roll back to the previous version", False),
    ],
)
def test_market_vocabulary_gate_distinguishes_market_wording(text: str, expected: bool) -> None:
    """Bare `change` means "modify" far more often than it names a market quantity."""
    assert names_market_mechanic(text) is expected


@pytest.mark.parametrize(
    "text",
    [
        # Underscored identifiers: `_` is a word character, so a `\b` boundary never
        # matched inside them and a real close-to-close rule read as naming nothing
        # about the market. This is the `ve**rsi**on` boundary defect from the other
        # side — the same reason `names_indicator` exists.
        "latest_5m_close / prev_5m_close - 1 >= 0.05",
        "percentage_change gte 5%",
        "compare current_price to reference_close",
        "(close - open) / open >= 0.01",
    ],
)
def test_identifier_wording_still_names_the_market(text: str) -> None:
    assert names_market_mechanic(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # An instruction aimed at the assistant's own reading or output. Each quotes
        # market words, and each was compiled as a market mechanic before: the first
        # produced a `strong_swing_high` condition from the sentence that forbade it.
        'Don’t interpret it as “Strong Swing High” specifically',
        'No—don’t build a new “verified candle-data rule”',
        "you must map the operators exactly (above/below/at least/at most)",
        "never rename the price fields in your reply",
        "do not assume the timeframe",
        "I’m not asking about capability resolution, I’m asking for a compile",
        'If you write stuff like “ba3s” or “trigger 4h” with typos',
    ],
)
def test_instructions_about_the_answer_are_not_market_mechanics(text: str) -> None:
    """A market noun inside a meta-instruction does not make it a market mechanic."""
    assert classify_fragment(text).enters_capability_resolution is False


@pytest.mark.parametrize(
    "text",
    [
        # The same verbs, now stating a real requirement. The meta rule is guarded by
        # `_states_measurable_condition`, so a rule riding along with a note about the
        # dialogue survives.
        "don't guess — RSI must be under 30",
        "build me a monitor that fires when RSI is under 30",
        "show me coins that dropped 5% today",
        "don't alert unless price closes above 100",
    ],
)
def test_a_rule_stated_beside_a_meta_instruction_survives(text: str) -> None:
    fragment = classify_fragment(text)
    assert fragment.category in {"FORMULA", "TRADING_MECHANIC"}


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        # Text that is not a mechanic can still carry settled configuration. Dropping
        # straight to `conversational` silently shrank the watch list and lost the
        # timeframe the trader stated.
        ("add SOLUSDT", "symbol"),
        # `go ahead` is deliberately absent: in this product it reads as approval, and
        # `classify_turn` must keep recognising it as such.
        ("now run it on the 4h", "timeframe"),
        ("then run the SOLUSDT-only check", "symbol"),
        ('do not rename the fields in your reply about SOLUSDT', "conversation_control"),
    ],
)
def test_non_mechanic_wording_keeps_the_state_it_carries(text: str, kind: str) -> None:
    fragment = classify_fragment(text)
    assert fragment.enters_capability_resolution is False
    assert fragment.kind == kind, fragment.kind


@pytest.mark.parametrize(
    "text",
    [
        # Registered capability wording.
        "head and shoulders neckline break",
        # Wording the registry does *not* know. It must still reach the resolver:
        # asking about it is the whole point, and an approved alias may resolve it
        # later. A "must name known market vocabulary" gate was tried here and
        # reverted precisely because it silenced these.
        "raid the weekly floor",
        "store the max favorable low achieved",
        "only if the chart feels unusually optimistic",
    ],
)
def test_unknown_market_wording_still_reaches_the_resolver(text: str) -> None:
    """An unrecognised word is what the capability registry exists to interpret."""
    assert classify_fragment(text).enters_capability_resolution is True


@pytest.mark.parametrize(
    "text",
    [
        # A clause joined on with `and`/`but`/`then` carries the whole requirement.
        # Rejecting it for its opening word dropped real rules.
        "and the entry condition is a bearish move of at least 7.5%",
        "but use 15m for the context and require RSI under 30",
        "then look for a liquidity sweep of the previous day low",
    ],
)
def test_a_clause_joined_with_a_conjunction_is_still_an_instruction(text: str) -> None:
    fragment = classify_fragment(text)
    assert fragment.category in {"FORMULA", "TRADING_MECHANIC"}


@pytest.mark.parametrize("opener", ["And", "Then", "Now", "Alright,", "So:", "OK,", "Actually"])
@pytest.mark.parametrize(
    "body",
    [
        "confirm the rest in one line",
        "paste the final nested boolean expression",
        "test it exactly as specified",
    ],
)
def test_a_discourse_opener_does_not_change_what_a_fragment_is(opener: str, body: str) -> None:
    """`And confirm…` is the same instruction as `Confirm…`. Every anchored dialogue
    pattern stopped matching when an opener was present, so the identical sentence was
    routed one way with `And` and another way without it."""
    assert classify_fragment(f"{opener} {body}").enters_capability_resolution is False
    assert classify_fragment(body).enters_capability_resolution is False


@pytest.mark.parametrize(
    "text",
    [
        # The approval gate stated without the word "approval". Reporting it as an
        # unconvertible mechanic handed the trader a blocking finding for the very
        # safeguard they were asking for (INV-10).
        "nothing runs until I say yes",
        "nothing happens until I approve",
        "don't run anything until I confirm",
        "no alerts until I give the word",
    ],
)
def test_an_approval_gate_without_the_word_approval_is_still_approval_traffic(
    text: str,
) -> None:
    fragment = classify_fragment(text)
    assert fragment.enters_capability_resolution is False
    assert classify_turn(text).is_approval is False, "describing the gate never grants it"


@pytest.mark.parametrize(
    "text",
    [
        # Asking for the artefact, not for a market condition. The mechanics come from
        # the rest of the conversation.
        "Now actually build the SOLUSDT-only watchlist",
        "Alright—build me a watchlist filter: ETHUSDT only",
        "set up a scanner for me",
        "give me the rule set",
    ],
)
def test_a_request_for_the_artefact_is_not_a_market_mechanic(text: str) -> None:
    assert classify_fragment(text).enters_capability_resolution is False


@pytest.mark.parametrize(
    "text",
    [
        # The same building verbs, now carrying a rule. Guarded by
        # `_states_measurable_condition`, so the request keeps its mechanic.
        "build me a monitor that fires when RSI is under 30",
        "set up a scanner for coins that dropped 5% today",
        "test the RSI below 30 rule on the 4h",
        "confirm the trend with a 200 EMA above price",
    ],
)
def test_a_rule_inside_a_build_request_survives(text: str) -> None:
    fragment = classify_fragment(text)
    assert fragment.category in {"FORMULA", "TRADING_MECHANIC"}
