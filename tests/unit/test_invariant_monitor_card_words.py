"""The Monitors card may not invent a health reading, nor repeat an engineer's sentence.

Two failures, one cause: the card and the thing it reads never agreed on a shape.

`StrategyCockpitService.edge_health` returns a plain **dictionary**. The card read it
with `getattr`, which never finds a dictionary key. So every monitor in the product
scored 0 and every card that had finished a check said:

    Needs a look. Most checks are not arriving.

— including a monitor whose 1419 checks had every one arrived. Nothing raised, nothing
logged; a wrong default simply stood in for a value that was right there. The tests did
not catch it because their stand-in for the payload was an *object with attributes*,
which is the one shape `getattr` does work on.

The second failure was waiting behind it. The payload's own sentences are written for an
engineer reading the cockpit — "Average recorded latency is 653784 ms." — and the moment
the card could read the payload, those sentences would have gone straight to a beginner.

So two rules, held here:

* **the keys the card reads are keys the payload writes**, checked against both sources
  rather than against a fake; and
* **the words on the card come from `product_language`**, never from the payload.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ai_market_monitor.services.product_language import (
    monitor_issue_words,
    monitor_working_words,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/ai_market_monitor"

CARD = SOURCE / "api/routers/dashboard_test.py"
COCKPIT = SOURCE / "cockpit_service.py"


def _function(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    )


def _payload_keys() -> set[str]:
    """Every key `_health_payload` puts into the dictionary it returns."""

    function = _function(COCKPIT, "_health_payload")
    return {
        key.value
        for node in ast.walk(function)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _card_health_reads() -> set[str]:
    """Every key the Monitors card reads out of that dictionary."""

    function = _function(CARD, "_watchlist_view")
    return {
        node.slice.value
        for node in ast.walk(function)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "health"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    }


def test_the_card_reads_only_keys_the_health_payload_writes():
    """Checked against the producer, never against a stand-in built in a test."""

    missing = _card_health_reads() - _payload_keys()
    assert missing == set(), (
        f"the Monitors card reads health keys that _health_payload never writes: {missing}"
    )


def test_the_card_reads_the_payload_as_the_dictionary_it_is():
    """`getattr` on this payload is always the wrong answer, silently.

    It cannot fail: it returns the default. That is why one wrong sentence stood on
    every card in the product for as long as it did.
    """

    body = ast.unparse(_function(CARD, "_watchlist_view"))
    assert "getattr(health" not in body
    # And it really does read it, rather than having quietly stopped scoring at all.
    assert "health['score']" in body


def test_the_score_is_read_from_the_payload_and_not_defaulted_away():
    """A default of 0 puts every working monitor into the worst answer the page has."""

    function = _function(CARD, "_watchlist_view")
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in {"health", "scan_state"}
        ):  # pragma: no cover - the assertion below is the report
            raise AssertionError(
                f"{node.args[0].id} is read with getattr at line {node.lineno}; a missing "
                "value must be loud, not defaulted"
            )


# ── The words themselves ───────────────────────────────────────────────────────────


#: Every component `_health_components` can name as the weakest one.
#:
#: Read from the source rather than listed here, so a new component that nobody gave
#: plain words to fails this file instead of reaching a beginner as engineer text.
def _component_names() -> set[str]:
    function = _function(COCKPIT, "_health_components")
    return {
        node.args[0].value
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_component"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


@pytest.mark.parametrize("component", sorted(_component_names()))
def test_every_health_component_has_plain_words_for_a_beginner(component):
    """No component may fall through to the "something needs a look" answer.

    The fallback exists so an unknown name cannot crash a page. It is not a place for a
    component that really exists to sit.
    """

    words = monitor_issue_words(component)
    assert words != monitor_issue_words(None), f"{component!r} has no plain words"
    assert words.endswith("."), words


@pytest.mark.parametrize("component", sorted(_component_names()))
def test_the_plain_words_carry_no_machine_language(component):
    """Milliseconds, percentages of nothing and internal names are not plain words."""

    words = monitor_issue_words(component)
    for banned in ("ms.", "latency", "evaluation", "ratio", "component", "%", "/100"):
        assert banned not in words.lower(), f"{component!r} says {banned!r}: {words}"


@pytest.mark.parametrize("component", sorted(_component_names()))
def test_a_known_blocker_never_reads_as_waiting_for_history(component):
    """"Not enough history yet" is advice to wait, and it was printed for monitors
    that had months of history and a rule that was never once true. When the payload
    knows what is in the way, the words must not tell the owner to wait.
    """

    words = monitor_issue_words(component, blocker_known=True)
    assert "not enough history" not in words.lower(), f"{component!r}: {words}"
    assert words.endswith("."), words


def test_an_unknown_component_still_gets_an_answer_and_never_the_old_untrue_one():
    """"Most checks are not arriving." was printed for monitors where every one had."""

    fallback = monitor_issue_words("Something Nobody Has Written Yet")
    assert fallback == monitor_issue_words(None)
    assert "not arriving" not in fallback


# ── The card and the weekly note answer the same question the same way ─────────────


@pytest.mark.parametrize(
    "score", [0, 1, 49, 49.9, 50, 50.1, 79, 79.9, 80, 80.1, 99, 100]
)
def test_the_score_turns_into_words_in_one_place_only(score):
    """Both the card and the weekly note read this, so both must read *this*.

    They disagreed before: the card said "Needs a look" and the note, about the same
    monitor in the same week, said "Edge Health: 40/100."
    """

    said = monitor_working_words(score)
    assert said.label in {"Working well", "Working, with a gap", "Needs a look"}
    assert said.tone in {"success", "warning", "danger"}
    # Never the number itself. That is the point of the function.
    assert str(int(score)) not in said.label


def test_no_surface_writes_its_own_score_boundaries():
    """A second copy of "80 means working well" is a second product decision."""

    owner = "services/product_language.py"
    labels = {"Working well", "Working, with a gap", "Needs a look"}
    offenders: list[str] = []
    for path in SOURCE.rglob("*.py"):
        relative = path.relative_to(SOURCE).as_posix()
        if relative == owner:
            continue
        source = path.read_text(encoding="utf-8")
        if not any(label in source for label in labels):
            continue
        tree = ast.parse(source)
        # Docstrings and comments may name a label — explaining what went wrong is not
        # deciding it again. Only a string the code actually uses counts.
        told = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        offenders.extend(
            f"{relative}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and node.value in labels
            and id(node) not in told
        )

    assert offenders == [], (
        f"these write the health labels themselves instead of asking {owner}: {offenders}"
    )


def test_the_weekly_note_never_carries_a_score_or_an_engineers_sentence():
    """It goes to the person who owns the monitor, in their notification list."""

    body = ast.unparse(_function(COCKPIT, "create_weekly_health_summary"))

    assert "Edge Health" not in body
    assert "/100" not in body
    assert "main_issue']" not in body and 'main_issue"]' not in body
    assert "monitor_issue_words" in body
    assert "monitor_working_words" in body
