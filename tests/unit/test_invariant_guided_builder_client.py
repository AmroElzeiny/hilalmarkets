"""The shipped Builder invents nothing and never blocks on the assistant.

The client is in this repository: ``static/ai-setup-chat.js`` and the page that hosts
it. There is no JavaScript test runner here, so these read the shipped files as text and
assert the properties that matter. Weaker than executing it, stronger than assuming it —
and each one catches a regression that would be invisible on the server:

* a frontend that hard-codes a timeframe, a comparison or a capability, so the form
  offers something the compiler refuses;
* a Builder that hides when the assistant fails, which is the whole problem this work
  exists to remove;
* a composer that stays open while the assistant is off, so a person types a message
  that can only be refused.

The matching server behaviour is proved against the real service in
``tests/integration/test_guided_builder.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_market_monitor.engine.builder_contract import COMPARATOR_LABELS
from ai_market_monitor.schemas.timeframes import ORDERED_TIMEFRAMES

CLIENT = Path("src/ai_market_monitor/static/ai-setup-chat.js")
PAGE = Path("src/ai_market_monitor/templates/hilal/dashboard/builder.html")
STYLES = Path("src/ai_market_monitor/static/hilalmarkets-builder.css")

SOURCE = CLIENT.read_text(encoding="utf-8")
MARKUP = PAGE.read_text(encoding="utf-8")
CSS = STYLES.read_text(encoding="utf-8")


def test_the_builder_is_rendered_by_the_shipped_client() -> None:
    assert "data-ai-guided-builder" in MARKUP, "the page has nowhere to draw the Builder"
    assert "function renderGuidedBuilder(" in SOURCE
    assert "renderGuidedBuilder();" in SOURCE, "the Builder is never drawn"


def test_the_form_is_drawn_from_the_server_contract() -> None:
    """Fields come from one fetch, not from anything written into the page."""

    assert "async function loadBuilderContract(" in SOURCE
    assert '"/builder-contract"' in SOURCE
    assert "builderContract?.mechanics" in SOURCE
    assert "builderContract?.modes" in SOURCE
    assert "builderContract?.universes" in SOURCE
    assert "builderContract?.starters" in SOURCE


def test_builder_changes_are_posted_through_one_function_with_a_key() -> None:
    """One send path, and every send carries its own idempotency key.

    A double-clicked button must act once. That is only checkable if there is a single
    place where a builder change leaves the page.
    """

    assert SOURCE.count("async function sendBuilderAction(") == 1
    posts = re.findall(r"/builder-actions`", SOURCE)
    assert len(posts) == 1, "builder changes must be posted from exactly one place"
    body = SOURCE.split("async function sendBuilderAction(", 1)[1].split("\n  }", 1)[0]
    assert "client_message_id: newClientMessageId()" in body


@pytest.mark.parametrize("timeframe", list(ORDERED_TIMEFRAMES))
def test_the_client_does_not_carry_its_own_timeframe_list(timeframe: str) -> None:
    """A hard-coded list is a second vocabulary, and it drifts.

    Checked for every timeframe individually so a fix that removes one and leaves the
    rest still fails. The only timeframes the client may show are the ones the contract
    sends it.
    """

    builder = SOURCE.split("// The Guided Watch Plan Builder.", 1)[1]
    literal = re.compile(rf'["\']{re.escape(timeframe)}["\']')
    assert not literal.search(builder), (
        f"the Builder hard-codes the timeframe {timeframe}; it must come from the contract"
    )


@pytest.mark.parametrize("comparator", sorted(item.value for item in COMPARATOR_LABELS))
def test_the_client_does_not_carry_its_own_comparison_list(comparator: str) -> None:
    """Same rule for comparisons: the compiler owns which ones a rule can use."""

    builder = SOURCE.split("// The Guided Watch Plan Builder.", 1)[1]
    literal = re.compile(rf'["\']{re.escape(comparator)}["\']')
    assert not literal.search(builder), (
        f"the Builder hard-codes the comparison {comparator}; it must come from the contract"
    )


def test_a_mechanic_the_platform_withholds_is_shown_with_its_reason() -> None:
    """Never silently missing. A person must see "not yet", not an empty list."""

    assert "mechanic.available" in SOURCE
    assert "unavailable_reason" in SOURCE
    assert "not available yet" in SOURCE


def test_the_assistant_being_off_closes_the_composer_and_nothing_else() -> None:
    """The Builder must keep working while the assistant cannot."""

    send_state = SOURCE.split("function updateSendState(", 1)[1].split("\n  }", 1)[0]
    assert "assistant_available === false" in send_state
    assert "input.disabled" in send_state
    guided = SOURCE.split("function renderGuidedBuilder(", 1)[1].split("\n  }", 1)[0]
    assert "availability.builder === false" in guided, (
        "the Builder must hide only on its own switch, never on an assistant failure"
    )
    assert "assistant_available" not in guided, (
        "the Builder disappears when the assistant fails, which is the bug this removes"
    )


def test_a_degraded_assistant_is_explained_once_without_blaming_the_setup() -> None:
    assert "data-ai-degraded" in MARKUP
    assert "function renderDegradedNotice(" in SOURCE
    notice = SOURCE.split("function renderDegradedNotice(", 1)[1].split("\n  }", 1)[0]
    assert "availability.message" in notice, "the sentence must come from the server"
    for invented in ("Something went wrong", "Error", "failed"):
        assert invented not in notice, "the client must not write its own outage wording"


def test_the_lifecycle_badge_comes_from_the_server() -> None:
    """One vocabulary. A client that names states itself will disagree with the server."""

    review = SOURCE.split("function renderBuilderReview(", 1)[1].split("\n  }", 1)[0]
    assert "chat?.lifecycle?.label" in review
    assert "chat?.lifecycle?.explanation" in review
    for invented in ("Ready", "Draft", "Approved"):
        assert f'"{invented}"' not in review, "the client invented a lifecycle name"


def test_removing_a_rule_asks_first_and_names_what_goes() -> None:
    """A delete a person did not mean is work lost with nothing to undo it from."""

    card = SOURCE.split("function renderRuleCard(", 1)[1].split("\n  }", 1)[0]
    assert "window.confirm(" in card
    assert "condition.sentence" in card, "the question must name the rule being removed"


def test_a_rule_the_form_cannot_show_is_marked_rather_than_offered() -> None:
    """An editable card that drops a value on save loses work silently."""

    card = SOURCE.split("function renderRuleCard(", 1)[1].split("\n  }", 1)[0]
    assert "condition.editable" in card
    assert "condition.not_editable_reason" in card


def test_the_builder_is_reachable_by_keyboard_and_usable_on_a_phone() -> None:
    """Touch targets, visible focus, and a layout that wraps rather than scrolls."""

    assert ":focus-visible" in CSS, "keyboard users cannot see where they are"
    assert "@media (max-width: 720px)" in CSS, "no mobile layout"
    assert "min-height: 44px" in CSS, "form controls are too small to tap"
    assert 'button.type = "button"' in SOURCE, (
        "a button inside a form defaults to submit and would reload the page"
    )
    assert 'setAttribute("aria-pressed"' in SOURCE, "chips do not report their state"
    assert 'setAttribute("aria-label", "Kind of rule")' in SOURCE


def test_no_page_reload_is_needed_after_a_builder_change() -> None:
    """The response to a change is the new state, rendered in place."""

    body = SOURCE.split("async function sendBuilderAction(", 1)[1].split("\n  }", 1)[0]
    assert "render();" in body
    assert "window.location.reload" not in SOURCE
