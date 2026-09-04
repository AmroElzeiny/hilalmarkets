"""Asking the system to go and look is not a verdict, and must not be built like one.

A reviewer pressed "Run research" on the Cases page and was answered "this one is a job
to find a missing official page, not a case with a verdict". That answer came from the
**decision** path, and it came from there because the button was part of it: the same
row as Approve and Reject, inside the same form, posting to the address whose whole job
is "record one decision on the selected cases", held to the decision's required sentence
and answered with the decision's refusals.

Moving the button would have fixed the mis-click and left every other half in place. So
the separation is pinned here at each layer it has to hold:

* **the form** — its own ``<form>``, so nothing it sends reaches the decision address and
  nothing the decision sends reaches it;
* **the place** — under the case table, not in the row of buttons that decide;
* **the words** — no required justification, nothing that reads as a religious answer,
  and it says out loud that it decides nothing;
* **the server** — its own route and its own service method, and the decision path
  refuses the word "research" by name rather than quietly accepting it again.

One more thing is pinned here because it is what put the decision bar in front of the
reviewer at all times: this page has no reset stylesheet behind it, so ``hidden`` on an
element the sheet gives a ``display`` to does nothing at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_market_monitor.services.system_brain_bulk_review import (
    BULK_ACTIONS,
    BULK_DECISION_ACTIONS,
    RESEARCH_ACTION,
)

ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

DECISION_ROUTE = "/dashboard/system-brain/cases/bulk-decision"
RESEARCH_ROUTE = "/dashboard/system-brain/cases/research"


def _template() -> str:
    return (ROOT / "templates" / "system_brain.html").read_text(encoding="utf-8")


def _block(marker: str) -> str:
    """One form from the Cases page, from its opening marker to its own ``</form>``."""

    template = _template()
    start = template.index(marker)
    return template[start : template.index("</form>", start)]


def _decision_block() -> str:
    return _block('id="bulk-decision-form"')


def _research_block() -> str:
    return _block('id="case-research-form"')


# ---------------------------------------------------------------------------
# The form, and the place
# ---------------------------------------------------------------------------


def test_research_is_not_a_button_in_the_quick_decision_form() -> None:
    """The mis-click, closed at the source.

    Nothing inside the decision form may carry the research address or the research
    label. A button placed there inherits the form's address, its required sentence and
    its refusals however far away on screen it is drawn.
    """

    decision = _decision_block()
    assert "Run research" not in decision
    assert RESEARCH_ROUTE not in decision
    assert RESEARCH_ACTION not in decision


def test_the_two_forms_post_to_two_different_addresses() -> None:
    """Separate in function, not only in position."""

    assert DECISION_ROUTE in _decision_block()
    research = _research_block()
    assert RESEARCH_ROUTE in research
    assert DECISION_ROUTE not in research


def test_the_research_form_stands_below_the_case_table() -> None:
    """Far from the sticky decision bar, which is the whole point of moving it.

    The decision bar is pinned to the top of the screen while the reviewer scrolls. A
    research button drawn anywhere above the table would end up beside it again.
    """

    template = _template()
    assert template.index("system_brain_case_table.html") < template.index(
        'id="case-research-form"'
    )


def test_the_research_form_carries_a_csrf_token() -> None:
    """Like every other posting form on this page."""

    assert "csrf_token" in _research_block()


def test_the_ticked_cases_reach_the_research_form_as_its_own_fields() -> None:
    """A checkbox belongs to one form, so the second form needs the ids written into it.

    They are written by the same function that counts the selection, in the same call, so
    the number on screen and the cases sent cannot be different numbers.
    """

    assert "data-research-ids" in _research_block()
    script = (ROOT / "static" / "system-brain.js").read_text(encoding="utf-8")
    marker = script.index("const selected = boxes.filter((box) => box.checked);")
    refresh = script[marker : script.index("boxes.forEach((box) => box.addEventListener")]
    assert "researchIds" in refresh, "the ids are written somewhere other than the counter"
    assert "researchCount" in refresh, "the two forms can now show different numbers"


def test_the_ceiling_guards_both_forms_that_carry_the_selection() -> None:
    """One limit, both routes. Guarding one leaves the other able to send a refused batch."""

    script = (ROOT / "static" / "system-brain.js").read_text(encoding="utf-8")
    assert "event.target !== bar && event.target !== researchForm" in script


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


#: Words that would make a button read as a religious verdict. None may appear on one
#: that only asks for evidence to be gathered.
FORBIDDEN_WORDS = (
    "halal",
    "haram",
    "haraam",
    "permissible",
    "impermissible",
    "compliant",
    "verdict",
    "ruling",
    "approve",
    "reject",
)


@pytest.mark.parametrize("word", FORBIDDEN_WORDS)
def test_the_research_panel_never_reads_as_a_decision(word: str) -> None:
    assert word not in _research_block().casefold(), (
        f"the research panel must not say {word!r}"
    )


def test_the_research_panel_says_out_loud_that_it_decides_nothing() -> None:
    """The reassurance is the copy, not an omission a reader has to infer."""

    block = _research_block().casefold()
    assert "nothing is decided" in block
    assert "nothing is published" in block


def test_no_written_sentence_is_demanded_for_research() -> None:
    """A decision needs a person's words; asking a machine to go and look does not.

    The decision form's sentence is ``required`` and has a floor. Requiring one here is
    what made a search feel like a verdict the reviewer had to justify.
    """

    research = _research_block()
    assert "required" not in research
    assert "minlength" not in research
    assert "Optional" in research
    # And the decision still demands one, so this test cannot pass by weakening that.
    assert "required" in _decision_block()


def test_a_research_button_does_not_say_it_is_recording_anything() -> None:
    """The shared submit handler says "Recording:" while a request is in flight."""

    assert "data-busy-label" in _research_block()
    script = (ROOT / "static" / "system-brain.js").read_text(encoding="utf-8")
    assert "submitter.dataset.busyLabel" in script


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


def test_research_is_not_one_of_the_quick_decisions() -> None:
    """The one line that made the page and the server treat "go and look" as a verdict."""

    assert RESEARCH_ACTION not in BULK_ACTIONS
    assert RESEARCH_ACTION not in BULK_DECISION_ACTIONS


def test_the_route_exists_and_uses_the_service_method_that_decides_nothing() -> None:
    """Button, address, service: one chain with no missing link."""

    router = (ROOT / "api" / "routers" / "system_brain.py").read_text(encoding="utf-8")
    assert RESEARCH_ROUTE in router
    assert "service.research(" in router
    assert "_verify_csrf" in router


def test_the_decision_route_never_starts_research_on_its_own_action() -> None:
    """The decision route may only queue the sweep for an approval it had to interrupt.

    It used to queue research whenever the posted action *was* research. With that value
    refused, any surviving reference to it here would be a second door into the same
    confusion.
    """

    router = (ROOT / "api" / "routers" / "system_brain.py").read_text(encoding="utf-8")
    decision = router[
        router.index("async def system_brain_bulk_case_decision") : router.index(
            "async def system_brain_start_case_research"
        )
    ]
    assert f'"{RESEARCH_ACTION}"' not in decision
    assert "queue_source_hunt" not in decision, (
        "the decision route must not start the source hunt; a decision never asks for one"
    )


def test_a_switched_off_page_hunt_is_said_out_loud() -> None:
    """A search that cannot run must not be reported as a search that started.

    The worker stops on its own when ``SHARIA_SOURCE_RESOLUTION_ENABLED`` is off, and it
    says so only in its log. Without this the reviewer reads "3 coins are being looked up
    again" about work that never began, and goes looking for a result that will never
    arrive.
    """

    router = (ROOT / "api" / "routers" / "system_brain.py").read_text(encoding="utf-8")
    research = router[router.index("async def system_brain_start_case_research") :]
    assert "sharia_source_resolution_enabled" in research
    assert "switched off" in research


# ---------------------------------------------------------------------------
# `hidden` has to actually hide
# ---------------------------------------------------------------------------


def test_hidden_beats_every_display_rule_this_page_writes() -> None:
    """Otherwise the decision bar is on screen with nothing selected.

    The System Brain loads the brand tokens and its own stylesheet, and nothing else.
    There is no reset behind them, so the browser's ``[hidden] { display: none }`` is the
    weakest rule in the cascade and **any** author ``display`` beats it. The decision bar
    says ``display: grid``, so ``hidden`` on it did nothing and the Approve / Reject row
    sat in front of the reviewer permanently.
    """

    styles = (ROOT / "static" / "system-brain.css").read_text(encoding="utf-8")
    assert re.search(
        r"\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}", styles
    ), "nothing in this stylesheet makes the `hidden` attribute win"


def test_the_two_forms_start_hidden_and_are_shown_by_the_selection() -> None:
    """Neither bar may greet a reviewer who has ticked nothing."""

    assert re.search(r"id=\"bulk-decision-form\"[\s\S]*?\n\s*hidden\n", _template())
    assert "hidden" in _research_block()
