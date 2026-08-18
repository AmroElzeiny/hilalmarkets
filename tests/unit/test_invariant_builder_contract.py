"""The Builder's form contract is derived, never written a second time.

The recurring defect in this codebase is two modules deciding what a word means and each
understanding a different subset. A guided form is a fresh chance to make that mistake:
the moment it holds its own list of timeframes, comparisons or capabilities, it starts
offering rules the compiler will refuse — or, worse, accepting ones that compile into
something else.

Every test here asserts the rule across the whole family, not for one example.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.builder_contract import (
    COMPARATOR_LABELS,
    DIRECTION_LABELS,
    LOGIC_CHOICES,
    MODE_CHOICES,
    UNIVERSE_CHOICES,
    builder_mechanics,
    core_mechanics,
    find_mechanic,
)
from ai_market_monitor.engine.builder_operations import (
    BuilderActionError,
    _probe_values,
    build_condition,
    describe_condition,
    mechanic_catalog,
    offered_mechanics,
)
from ai_market_monitor.engine.capability_contract import DIRECTION_WORDS
from ai_market_monitor.engine.setup_lifecycle import (
    STATE_EXPLANATIONS,
    STATE_LABELS,
    LifecycleState,
    chat_status_for,
    resolve_lifecycle,
    turn_lifecycle_state,
)
from ai_market_monitor.schemas.strategy import UNARY_COMPARATORS, Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    FORMULA_CONTRACTS,
    FormulaKind,
    MovementDirection,
    StrategyDraftV2,
)
from ai_market_monitor.schemas.timeframes import ORDERED_TIMEFRAMES

# ---------------------------------------------------------------------------
# The contract is derived from the compiler's own tables.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "formula",
    [item for item in FormulaKind if item is not FormulaKind.CAPABILITY],
    ids=lambda item: item.value,
)
def test_every_core_formula_is_offered_exactly_once(formula: FormulaKind) -> None:
    """A formula the compiler runs but the form does not offer is a gap in the product."""

    matches = [item for item in core_mechanics() if item.formula == formula.value]
    assert len(matches) == 1, f"{formula.value} is offered {len(matches)} times"


@pytest.mark.parametrize(
    "mechanic",
    [item for item in core_mechanics()],
    ids=[item.key for item in core_mechanics()],
)
def test_a_core_mechanic_offers_exactly_the_comparisons_its_formula_owns(mechanic) -> None:
    """Not a subset, not a superset. The contract table is the only authority."""

    formula = FormulaKind(mechanic.formula)
    expected = {item.value for item in FORMULA_CONTRACTS[formula].operators}
    assert {item.value for item in mechanic.operators} == expected


@pytest.mark.parametrize(
    "mechanic",
    [item for item in core_mechanics()],
    ids=[item.key for item in core_mechanics()],
)
def test_a_core_mechanic_never_offers_a_side_its_formula_forbids(mechanic) -> None:
    """A high-to-low move is a fall. Offering "goes up" would invert the alert."""

    formula = FormulaKind(mechanic.formula)
    forbidden = {item.value for item in FORMULA_CONTRACTS[formula].forbidden_directions}
    assert not ({item.value for item in mechanic.directions} & forbidden)


@pytest.mark.parametrize(
    "mechanic",
    [item for item in core_mechanics()],
    ids=[item.key for item in core_mechanics()],
)
def test_a_core_mechanic_offers_the_platform_timeframes(mechanic) -> None:
    assert tuple(mechanic.timeframes) == tuple(ORDERED_TIMEFRAMES)


@pytest.mark.parametrize("comparator", list(Comparator), ids=lambda item: item.value)
def test_every_comparison_has_words_a_beginner_can_read(comparator: Comparator) -> None:
    """A missing label renders an empty option; somebody picks it and builds the wrong rule."""

    label, explanation = COMPARATOR_LABELS[comparator]
    assert label and explanation
    assert comparator.value not in label, "the internal name leaked into the label"


@pytest.mark.parametrize("direction", list(MovementDirection), ids=lambda item: item.value)
def test_every_side_has_words_a_beginner_can_read(direction: MovementDirection) -> None:
    label, explanation = DIRECTION_LABELS[direction]
    assert label and explanation


@pytest.mark.parametrize(
    "choices",
    [MODE_CHOICES, UNIVERSE_CHOICES, LOGIC_CHOICES],
    ids=["modes", "universes", "logic"],
)
def test_every_offered_choice_explains_itself(choices) -> None:
    """A beginner will not know what Scanner means. Each choice says so."""

    for choice in choices:
        assert choice.label and choice.explanation, f"{choice.value} has no plain wording"


# ---------------------------------------------------------------------------
# Nothing is substituted, clamped or inverted.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mechanic",
    list(offered_mechanics()),
    ids=[item.key for item in offered_mechanics()],
)
def test_every_offered_mechanic_round_trips_through_its_own_form(mechanic) -> None:
    """Built from the form, read back into the form, and the same values come out.

    A rule that cannot be read back is a rule the person cannot edit — the card would
    either be missing or would drop a value the moment they saved it.
    """

    node, sentence = build_condition(
        mechanic_key=mechanic.key,
        values=_probe_values(mechanic),
        source_turn_id="round-trip",
    )
    view = describe_condition(node)
    assert view.mechanic_key == mechanic.key
    assert view.editable, view.not_editable_reason
    assert view.sentence == sentence


@pytest.mark.parametrize(
    "mechanic",
    list(offered_mechanics()),
    ids=[item.key for item in offered_mechanics()],
)
def test_a_comparison_a_mechanic_does_not_own_is_refused_not_replaced(mechanic) -> None:
    """Across every mechanic, so a fix for one cannot pass this."""

    unavailable = [
        item for item in Comparator if item.value not in {c.value for c in mechanic.operators}
    ]
    if not unavailable:
        pytest.skip("this mechanic accepts every comparison")
    values = {**_probe_values(mechanic), "comparator": unavailable[0].value}
    with pytest.raises(BuilderActionError) as raised:
        build_condition(mechanic_key=mechanic.key, values=values, source_turn_id="refuse")
    assert raised.value.code == "COMPARISON_NOT_OFFERED"


@pytest.mark.parametrize(
    "mechanic",
    [item for item in offered_mechanics() if item.parameter("threshold") is not None],
    ids=[
        item.key for item in offered_mechanics() if item.parameter("threshold") is not None
    ],
)
def test_a_value_out_of_range_is_refused_rather_than_clamped(mechanic) -> None:
    """`RSI at least 999` is out of domain. Refuse it; never quietly make it 100."""

    parameter = mechanic.parameter("threshold")
    assert parameter is not None
    if parameter.maximum is None:
        pytest.skip("this value has no declared upper bound")
    values = {**_probe_values(mechanic), "threshold": parameter.maximum + 1}
    with pytest.raises(BuilderActionError) as raised:
        build_condition(mechanic_key=mechanic.key, values=values, source_turn_id="clamp")
    assert raised.value.code == "VALUE_OUT_OF_RANGE"


@pytest.mark.parametrize(
    "mechanic",
    list(offered_mechanics()),
    ids=[item.key for item in offered_mechanics()],
)
def test_a_mechanic_offers_a_value_box_only_when_it_compares_against_a_value(mechanic) -> None:
    """A yes/no rule has nothing to compare against, so it gets nothing to type into.

    Every mechanic used to be given a "Value" field, including the ones whose only
    comparisons are "happens" and "does not happen" — a box where anything a person
    typed could never be read. The registry can tell the two apart now, so the form
    can too, and the rule is checked over the whole catalogue rather than the handful
    that were noticed.
    """

    measures = bool({item.value for item in mechanic.operators} - UNARY_COMPARATORS)
    has_box = mechanic.parameter("threshold") is not None
    if measures:
        return
    assert not has_box, (
        f"{mechanic.key} only answers yes or no, but offers a value box that nothing reads"
    )


def test_a_mechanic_the_platform_withholds_cannot_be_built() -> None:
    """Listed with a reason, and still refused if somebody posts it anyway."""

    withheld = [item for item in mechanic_catalog() if not item.available]
    if not withheld:
        pytest.skip("every mechanic is currently offerable")
    mechanic = withheld[0]
    assert mechanic.unavailable_reason, "withheld without saying why"
    with pytest.raises(BuilderActionError) as raised:
        build_condition(
            mechanic_key=mechanic.key,
            values=_probe_values(mechanic),
            source_turn_id="withheld",
        )
    assert raised.value.code == "MECHANIC_UNAVAILABLE"


def test_an_unknown_mechanic_is_refused_rather_than_matched_to_the_nearest_one() -> None:
    assert find_mechanic("something_that_does_not_exist") is None
    with pytest.raises(BuilderActionError) as raised:
        build_condition(
            mechanic_key="something_that_does_not_exist",
            values={},
            source_turn_id="unknown",
        )
    assert raised.value.code == "MECHANIC_UNKNOWN"


@pytest.mark.parametrize(
    "mechanic",
    list(builder_mechanics()),
    ids=[item.key for item in builder_mechanics()],
)
def test_a_capability_mechanic_reports_the_feeds_it_needs(mechanic) -> None:
    """A rule that needs a data feed says so before it is chosen, not after."""

    if not mechanic.capability_key:
        assert not mechanic.provider_requirements
        return
    from ai_market_monitor.engine.capabilities import all_capabilities

    spec = next(item for item in all_capabilities() if item.key == mechanic.capability_key)
    expected = spec.provider_requirements or (
        (spec.provider_required,) if spec.provider_required else ()
    )
    assert tuple(mechanic.provider_requirements) == tuple(expected)


# ---------------------------------------------------------------------------
# One direction vocabulary, shared by the compiler and the registry.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", list(MovementDirection), ids=lambda item: item.value)
def test_the_draft_and_the_registry_agree_on_what_a_direction_is_called(
    direction: MovementDirection,
) -> None:
    """The draft says ``up``; the registry says ``bullish``. One translation, imported.

    The compiler used to compare the two lists directly, so a capability that fully
    supported a rising move refused every rule asking for one. This asserts the
    translation exists for every direction, not just the reported one.
    """

    word = DIRECTION_WORDS[direction]
    assert word in {"bullish", "bearish", "neutral"}


def test_a_directional_capability_rule_compiles() -> None:
    """The end-to-end proof of the fix above, on a real capability rule."""

    from ai_market_monitor.engine.capabilities import all_capabilities
    from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2

    directional = [
        item
        for item in offered_mechanics()
        if item.capability_key
        and "bullish"
        in next(
            spec for spec in all_capabilities() if spec.key == item.capability_key
        ).direction_support
        and item.parameter("direction") is not None
    ]
    if not directional:
        pytest.skip("no directional capability is currently offered")
    mechanic = directional[0]
    node, _ = build_condition(
        mechanic_key=mechanic.key,
        values={**_probe_values(mechanic), "direction": "up"},
        source_turn_id="direction",
    )
    compile_strategy_draft_v2(StrategyDraftV2(condition_ast=node))


# ---------------------------------------------------------------------------
# One lifecycle, read the same way by both surfaces.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", list(LifecycleState), ids=lambda item: item.value)
def test_every_lifecycle_state_is_complete(state: LifecycleState) -> None:
    """A state with no words shows a blank badge; a person cannot act on that."""

    assert STATE_LABELS[state]
    assert STATE_EXPLANATIONS[state]
    assert chat_status_for(state)


def test_a_blocked_draft_never_reads_as_ready() -> None:
    """Whatever else is true, a blocker beats readiness."""

    from ai_market_monitor.schemas.strategy_draft_v2 import UnsupportedRequirementV2

    blocked = StrategyDraftV2(
        unsupported_requirements=[
            UnsupportedRequirementV2(
                key="leverage",
                source_fragment="use 10x leverage",
                missing_contract="leverage is not offered",
            )
        ]
    )
    assert resolve_lifecycle(blocked) is LifecycleState.UNSUPPORTED
    assert chat_status_for(resolve_lifecycle(blocked)) == "needs_clarification"


@pytest.mark.parametrize(
    ("approval_status", "approval_eligible", "expected"),
    [
        ("approved", True, "approved"),
        ("eligible", True, "ready_for_approval"),
        ("invalidated_by_edit", True, "ready_for_approval"),
        ("not_eligible", False, "needs_clarification"),
        ("eligible", False, "needs_clarification"),
    ],
)
def test_the_turn_status_matches_what_the_session_column_has_always_held(
    approval_status: str, approval_eligible: bool, expected: str
) -> None:
    """The vocabulary moved into one owner; the stored strings did not change.

    Renaming them would make every stored turn record unreadable, so the mapping is
    pinned here.
    """

    draft = StrategyDraftV2()
    state = turn_lifecycle_state(
        draft,
        approval_status=approval_status,
        approval_eligible=approval_eligible,
    )
    assert chat_status_for(state) == expected
