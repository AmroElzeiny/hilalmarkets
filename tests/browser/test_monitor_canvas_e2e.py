"""The visual canvas, driven the way a person drives it.

Every check here is something only a running browser can answer: that the board draws,
that a card can be created, filled in, moved, re-attached, removed and brought back,
that both canvas sizes work, and that the whole thing is reachable from the keyboard.

The shared `page` fixture fails the test on any console error, any page error and any
failed request, so "no bugs" is enforced by the harness rather than by reading.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from ai_market_monitor.core.dashboard_paths import MONITOR_PATH
from tests.browser.conftest import (
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    close_any_open_guide,
    signup,
)

#: The endpoint the canvas cannot draw without. Named once so the tests that make it
#: stall, fail or answer late all point at the same address the page really reads.
CONTRACT_URL = "**/api/v1/dashboard/setup-chat/builder-contract"


def _open_canvas(page: Page, base_url: str) -> None:
    signup(page, base_url)
    close_any_open_guide(page)
    page.goto(f"{base_url}{MONITOR_PATH}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    # The board draws itself once the contract has been read.
    page.locator("[data-loading]").wait_for(state="hidden", timeout=30_000)
    expect(page.locator("[data-node='universe']")).to_be_visible()
    expect(page.locator("[data-node='alert']")).to_be_visible()
    assert_no_raw_traceback(page)


def _add_first_condition(page: Page) -> None:
    page.locator("[data-open-library]").click()
    expect(page.locator("[data-library]")).to_be_visible()
    page.locator("[data-library-search]").fill("candle moves by a percentage")
    expect(page.locator("[data-library-list] .m-lib-item").first).to_be_visible()
    page.locator("[data-library-list] .m-lib-item").first.click()
    page.locator("[data-library-add]").click()
    expect(page.locator("[data-library]")).to_be_hidden()


def test_the_canvas_draws_itself_from_the_contract(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)

    # The three cards every monitor has, wired together.
    expect(page.locator("[data-nodes] [data-node]")).to_have_count(3)
    expect(page.locator("[data-wires] [data-wire]")).to_have_count(2)
    expect(page.locator(".m-invite")).to_be_visible()

    # The list offers what the platform can really do, not a sample.
    page.locator("[data-open-library]").click()
    count = page.locator("[data-library-count]").inner_text()
    offered = int(re.search(r"\d+", count).group(0))
    assert offered > 300, f"the library only offered {offered} conditions"
    page.keyboard.press("Escape")
    expect(page.locator("[data-library]")).to_be_hidden()


def test_escape_closes_the_list_even_with_something_typed(page: Page, base_url: str) -> None:
    """A search box quietly eats Escape to clear itself. The dialog must still close."""
    _open_canvas(page, base_url)

    page.locator("[data-open-library]").click()
    expect(page.locator("[data-library]")).to_be_visible()
    page.locator("[data-library-search]").fill("volume")
    page.wait_for_timeout(250)
    page.keyboard.press("Escape")
    expect(page.locator("[data-library]")).to_be_hidden()

    # And focus goes back to whatever opened it.
    focused = page.evaluate("() => document.activeElement.dataset.openLibrary !== undefined")
    assert focused, "focus was left nowhere after the dialog closed"


def test_a_condition_can_be_added_filled_in_and_checked(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)
    _add_first_condition(page)

    card = page.locator("[data-nodes] [data-node][data-kind='rule']")
    expect(card).to_have_count(1)

    # Fail closed: the comparison has no server default, so the card says so rather
    # than quietly picking one.
    expect(card).to_have_attribute("data-incomplete", "true")
    expect(page.locator("[data-inspector]")).to_be_visible()

    checks = page.locator("[data-checks-toggle]")
    expect(checks).to_contain_text("to fix")

    # Every one of the three required values is empty, and every one has to be filled
    # by hand — that is the point. Filling them all finishes the card.
    page.locator("[data-inspector-body] [data-set='direction'][data-value='up']").click()
    page.locator("[data-inspector-body] [data-set='comparator'][data-value='gte']").click()
    page.locator("[data-inspector-body] input[data-set='threshold']").fill("5")
    page.wait_for_timeout(500)
    expect(card).to_have_attribute("data-incomplete", "false")

    # The card now reads as a sentence, in the words the server ships.
    expect(card.locator(".m-node-line")).to_contain_text("goes up")
    expect(card.locator(".m-node-line")).to_contain_text("at least")
    expect(card.locator(".m-node-line")).to_contain_text("5%")

    # And the whole monitor reads as one sentence.
    expect(page.locator("[data-sentence-text]")).to_contain_text("Watch")
    expect(page.locator("[data-sentence-text]")).to_contain_text("Tell me when")


def test_a_card_can_be_removed_and_brought_back(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)
    _add_first_condition(page)
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(1)

    page.locator("[data-node][data-kind='rule'] [data-act='remove']").click()
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(0)

    # The way back is offered where the change happened, not only in a menu.
    page.locator("[data-coach-undo]").click()
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(1)

    # And the toolbar's own undo still works after that.
    page.locator("[data-undo]").click()
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(0)
    page.locator("[data-redo]").click()
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(1)


def test_a_card_can_be_moved_into_a_group_without_dragging(page: Page, base_url: str) -> None:
    """WCAG 2.2 SC 2.5.7: every drag has a single-pointer equal."""
    _open_canvas(page, base_url)
    _add_first_condition(page)

    page.locator("[data-add-group]").click()
    expect(page.locator("[data-node][data-kind='group']")).to_have_count(2)

    card = page.locator("[data-node][data-kind='rule']")
    card.click()
    picker = page.locator("[data-inspector-body] [data-parent-select]")
    expect(picker).to_be_visible()

    values = picker.locator("option").evaluate_all("nodes => nodes.map(n => n.value)")
    # Two groups, plus "nothing" — the non-drag way to cancel a connection.
    assert values[-1] == "__aside__", "there is no way to take the card off its wire"
    assert len(values) == 3, "the new group should be somewhere the card can go"
    picker.select_option(values[1])

    page.wait_for_timeout(400)
    moved = page.locator("[data-node][data-kind='group']").last
    expect(moved.locator(".t-pill")).to_contain_text("1 card")


def test_the_connector_lines_are_actually_painted(page: Page, base_url: str) -> None:
    """Present in the document is not the same as visible on the board.

    Every line was in the DOM, correctly placed and correctly coloured, and none of
    them appeared: the product-wide `img, svg { max-width: 100% }` collapsed the
    drawing surface to zero width, because the surface sits inside a layer that has no
    width of its own. Counting the paths passed. Nobody could see a single line.
    """
    _open_canvas(page, base_url)
    _add_first_condition(page)
    page.locator("[data-inspector-close]").click()
    page.wait_for_timeout(400)

    measured = page.evaluate(
        """() => {
            const layer = document.querySelector('[data-wires]');
            const layerBox = layer.getBoundingClientRect();
            const board = document.querySelector('[data-board]').getBoundingClientRect();
            const paths = [...document.querySelectorAll('[data-wire]')].map(path => {
                const style = getComputedStyle(path);
                const box = path.getBoundingClientRect();
                return {
                    width: Math.round(box.width),
                    stroke: style.stroke,
                    strokeWidth: Number.parseFloat(style.strokeWidth),
                    visible: style.visibility === 'visible' && style.display !== 'none',
                };
            });
            return {
                layerWidth: Math.round(layerBox.width),
                boardWidth: Math.round(board.width),
                paths,
            };
        }"""
    )

    assert measured["layerWidth"] >= measured["boardWidth"], (
        f"the wire layer is {measured['layerWidth']}px wide inside a "
        f"{measured['boardWidth']}px board, so lines are being clipped away"
    )
    assert measured["paths"], "no connector was drawn at all"
    for path in measured["paths"]:
        assert path["visible"], path
        assert path["width"] > 0, path
        assert path["strokeWidth"] >= 1.5, path


def test_a_connector_can_be_told_apart_from_the_board_behind_it(page: Page, base_url: str) -> None:
    """A line that carries meaning has to reach 3:1, like any other meaningful graphic.

    It was drawn in the hairline grey used for card borders, which measures about
    1.3:1 against the board. Technically painted; effectively invisible.
    """
    _open_canvas(page, base_url)

    contrast = page.evaluate(
        """() => {
            const channel = (value) => {
                const part = value / 255;
                return part <= 0.03928 ? part / 12.92 : Math.pow((part + 0.055) / 1.055, 2.4);
            };
            const luminance = (colour) => {
                const [r, g, b] = colour.match(/[\\d.]+/g).map(Number);
                return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
            };
            const wire = getComputedStyle(document.querySelector('[data-wire]')).stroke;
            const board = getComputedStyle(document.querySelector('[data-board]')).backgroundColor;
            const a = luminance(wire);
            const b = luminance(board);
            const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
            return { wire, board, ratio: Math.round(ratio * 100) / 100 };
        }"""
    )
    assert contrast["ratio"] >= 3, f"connectors are not distinguishable: {contrast}"


def test_the_drag_handles_are_big_enough_to_take_hold_of(page: Page, base_url: str) -> None:
    """WCAG 2.2 SC 2.5.8. They were 13px dots, which is why they could not be grabbed."""
    _open_canvas(page, base_url)

    sizes = page.locator("[data-port]").evaluate_all(
        """nodes => nodes.map(node => {
            const box = node.getBoundingClientRect();
            return [Math.round(box.width), Math.round(box.height)];
        })"""
    )
    assert sizes, "no drag handle was drawn"
    too_small = [size for size in sizes if size[0] < 24 or size[1] < 24]
    assert not too_small, f"drag handles smaller than 24px: {too_small}"


def test_a_wire_can_be_dragged_from_a_card_into_a_group(page: Page, base_url: str) -> None:
    """The drag half of re-attaching. The non-drag half is the test above it."""
    _open_canvas(page, base_url)
    _add_first_condition(page)
    page.locator("[data-inspector-close]").click()
    page.locator("[data-add-group]").click()
    page.locator("[data-inspector-close]").click()
    page.locator("[data-tidy]").click()
    page.wait_for_timeout(700)

    card = page.locator("[data-node][data-kind='rule']")
    group = page.locator("[data-node][data-kind='group']").last
    expect(group.locator(".t-pill")).to_contain_text("0 cards")

    card.hover()
    page.wait_for_timeout(150)
    port = card.locator(".m-port-in").bounding_box()
    target = group.bounding_box()
    page.mouse.move(port["x"] + port["width"] / 2, port["y"] + port["height"] / 2)
    page.mouse.down()
    page.mouse.move(
        port["x"] + port["width"] / 2 + 40,
        port["y"] + port["height"] / 2 + 40,
        steps=6,
    )

    # The line has to follow the pointer while the drag is happening, and be visible
    # while it does. It used to be drawn into a surface clamped to zero width, so
    # nothing appeared and the drag felt like it had not started.
    drafts = page.locator(".m-wire-draft")
    expect(drafts).to_have_count(1)
    first = drafts.evaluate_all(
        "nodes => nodes.map(node => node.getBoundingClientRect().width)"
    )[0]
    assert first > 0, "the line being dragged is not painted"

    page.mouse.move(target["x"] + target["width"] / 2, target["y"] + target["height"] / 2, steps=10)
    second = drafts.evaluate_all(
        "nodes => nodes.map(node => node.getBoundingClientRect().width)"
    )[0]
    assert second != first, "the line did not follow the pointer"

    # While the wire is out, the board says which cards would accept it.
    expect(group).to_have_attribute("data-droppable", "true")
    page.mouse.up()
    page.wait_for_timeout(600)

    expect(group.locator(".t-pill")).to_contain_text("1 card")
    expect(page.locator(".m-wire-draft")).to_have_count(0)


# ── Cancelling a connection ──────────────────────────────────────────────────
#
# A wire could be *moved* from one group to another and never taken off: letting go
# over nothing simply put it back where it came from. These four tests cover every way
# out — the drag, the button on the card, the button on the line itself, and the
# picker — plus the escape hatch that changes nothing. A fix that only made one of them
# work has to fail here.


def _empty_point_on_board(page: Page) -> tuple[float, float]:
    """A spot on the visible board with nothing on it.

    Found rather than guessed. A fixed offset put the pointer past the bottom of the
    window, where there is no element at all, and the drag read it as "not over the
    board" — a false failure that says nothing about the product.
    """
    point = page.evaluate(
        """() => {
          const board = document.querySelector('[data-board]');
          const rect = board.getBoundingClientRect();
          const top = Math.max(rect.top, 0) + 14;
          const bottom = Math.min(rect.bottom, window.innerHeight) - 14;
          for (let y = bottom; y > top; y -= 14) {
            for (let x = rect.left + 14; x < rect.right - 14; x += 14) {
              const found = document.elementFromPoint(x, y);
              if (!found || !board.contains(found)) continue;
              if (found.closest('[data-node], .m-coach, .m-cut, .m-invite')) continue;
              return { x, y };
            }
          }
          return null;
        }"""
    )
    assert point, "there is nowhere empty on the board to drop a wire"
    return point["x"], point["y"]


def _card_and_wire(page: Page) -> tuple[Any, Any]:
    """One condition on the board, with the line that joins it to the main group."""
    _add_first_condition(page)
    page.locator("[data-inspector-close]").click()
    page.wait_for_timeout(300)
    card = page.locator("[data-nodes] [data-node][data-kind='rule']")
    expect(card).to_have_count(1)
    expect(page.locator("[data-aside] [data-node]")).to_have_count(0)
    return card, page.locator("[data-wires] [data-wire]")


def test_a_connection_can_be_cancelled_by_dragging_the_line_onto_empty_space(
    page: Page, base_url: str
) -> None:
    """The direct-manipulation way out, and the one that was missing entirely."""
    _open_canvas(page, base_url)
    card, wires = _card_and_wire(page)
    before = wires.count()

    card.hover()
    page.wait_for_timeout(150)
    port = card.locator(".m-port-in").bounding_box()
    empty_x, empty_y = _empty_point_on_board(page)

    page.mouse.move(port["x"] + port["width"] / 2, port["y"] + port["height"] / 2)
    page.mouse.down()
    page.mouse.move(empty_x, empty_y, steps=10)

    # The line says what letting go would do, before it is let go.
    expect(page.locator(".m-wire-draft")).to_have_attribute("data-mode", "cut")
    page.mouse.up()
    page.wait_for_timeout(600)

    # The card is still on the board, with everything it had — it is simply not part
    # of the monitor any more.
    expect(page.locator("[data-aside] [data-node]")).to_have_count(1)
    expect(page.locator("[data-nodes] [data-node][data-kind='rule']")).to_have_count(0)
    expect(page.locator("[data-shelf]")).to_be_visible()
    assert wires.count() == before - 1, "the connection is still drawn"


def test_the_card_offers_a_button_that_cancels_its_connection(
    page: Page, base_url: str
) -> None:
    """WCAG 2.2 SC 2.5.7: the drag above has to have a single-pointer equal."""
    _open_canvas(page, base_url)
    card, wires = _card_and_wire(page)
    before = wires.count()

    card.click()
    card.locator("[data-act='cut']").click()
    page.wait_for_timeout(500)

    expect(page.locator("[data-aside] [data-node]")).to_have_count(1)
    assert wires.count() == before - 1

    # And it says so in words, not only by where it sits.
    expect(page.locator("[data-aside] [data-node] .t-pill").first).to_contain_text(
        "Set aside"
    )


def test_the_line_itself_carries_the_button_that_cancels_it(
    page: Page, base_url: str
) -> None:
    """"Cancel *this* line" has to be reachable on the line a person is looking at."""
    _open_canvas(page, base_url)
    card, wires = _card_and_wire(page)
    before = wires.count()

    card.click()
    page.wait_for_timeout(300)
    cut = page.locator(".m-cut[data-shown='true']")
    expect(cut).to_have_count(1)

    # It names which connection. Four buttons all called "cancel the connection" tell a
    # screen-reader user nothing about which one they are on.
    label = cut.get_attribute("aria-label")
    assert label and label != "Cancel the connection to ", label
    cut.click()
    page.wait_for_timeout(500)

    expect(page.locator("[data-aside] [data-node]")).to_have_count(1)
    assert wires.count() == before - 1


def test_escape_leaves_a_wire_being_dragged_exactly_as_it_was(
    page: Page, base_url: str
) -> None:
    """A drag with no way out is a trap. Escape must change nothing at all."""
    _open_canvas(page, base_url)
    card, wires = _card_and_wire(page)
    before = wires.count()

    card.hover()
    page.wait_for_timeout(150)
    port = card.locator(".m-port-in").bounding_box()
    empty_x, empty_y = _empty_point_on_board(page)
    page.mouse.move(port["x"] + port["width"] / 2, port["y"] + port["height"] / 2)
    page.mouse.down()
    page.mouse.move(empty_x, empty_y, steps=8)
    expect(page.locator(".m-wire-draft")).to_have_count(1)

    page.keyboard.press("Escape")
    page.mouse.up()
    page.wait_for_timeout(400)

    expect(page.locator(".m-wire-draft")).to_have_count(0)
    expect(page.locator("[data-aside] [data-node]")).to_have_count(0)
    expect(page.locator("[data-nodes] [data-node][data-kind='rule']")).to_have_count(1)
    assert wires.count() == before, "Escape changed the board"


def test_a_cancelled_connection_can_be_undone_and_joined_back(
    page: Page, base_url: str
) -> None:
    """Cancelling is a change like any other: reversible, and never a deletion."""
    _open_canvas(page, base_url)
    card, wires = _card_and_wire(page)
    title = card.locator(".m-node-title").inner_text()
    before = wires.count()

    card.click()
    card.locator("[data-act='cut']").click()
    page.wait_for_timeout(500)
    expect(page.locator("[data-aside] [data-node]")).to_have_count(1)

    # Undo puts the wire back.
    page.locator("[data-undo]").click()
    page.wait_for_timeout(500)
    expect(page.locator("[data-aside] [data-node]")).to_have_count(0)
    assert wires.count() == before

    # And so does the "join" button on the card, with the settings it had all along.
    card.click()
    card.locator("[data-act='cut']").click()
    page.wait_for_timeout(500)
    aside = page.locator("[data-aside] [data-node]")
    assert aside.locator(".m-node-title").inner_text() == title, "the card lost its identity"
    aside.locator("[data-act='join']").click()
    page.wait_for_timeout(500)

    expect(page.locator("[data-aside] [data-node]")).to_have_count(0)
    expect(page.locator("[data-nodes] [data-node][data-kind='rule']")).to_have_count(1)
    assert wires.count() == before


def test_a_set_aside_card_never_blocks_the_monitor_it_left(
    page: Page, base_url: str
) -> None:
    """A card outside the rule is not a fault in the rule.

    Its missing value used to be reported as something standing between the draft and a
    monitor that could run — an error about a card the monitor never reads, which no
    answer could ever clear.
    """
    _open_canvas(page, base_url)
    card, _ = _card_and_wire(page)

    card.click()
    card.locator("[data-act='cut']").click()
    page.wait_for_timeout(500)

    page.locator("[data-checks-toggle]").click()
    checks = page.locator("[data-check-list]")
    expect(checks).to_be_visible()
    text = checks.inner_text()
    assert "set aside" in text.lower(), text
    assert "still needs" not in text.lower(), (
        "a card outside the monitor is being reported as unfinished work inside it"
    )


def test_the_shelf_is_not_part_of_the_tree_a_screen_reader_hears(
    page: Page, base_url: str
) -> None:
    """A `tree` may only hold `treeitem`s, and a set-aside card is not an item of the
    monitor. Putting it in the tree would misdescribe the very thing that changed."""
    _open_canvas(page, base_url)
    card, _ = _card_and_wire(page)

    card.click()
    card.locator("[data-act='cut']").click()
    page.wait_for_timeout(500)

    roles = page.locator("[data-nodes] [data-node]").evaluate_all(
        "nodes => nodes.map(node => node.getAttribute('role'))"
    )
    assert set(roles) == {"treeitem"}, roles
    aside_roles = page.locator("[data-aside] [data-node]").evaluate_all(
        "nodes => nodes.map(node => node.getAttribute('role'))"
    )
    assert aside_roles == ["listitem"], aside_roles
    # And nothing on the shelf claims a selection state the shelf does not have.
    selected = page.locator("[data-aside] [data-node][aria-selected]")
    expect(selected).to_have_count(0)


def test_the_card_can_be_dragged_around_the_board(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)
    _add_first_condition(page)

    card = page.locator("[data-node][data-kind='rule']")
    before = card.bounding_box()
    page.mouse.move(before["x"] + before["width"] / 2, before["y"] + 12)
    page.mouse.down()
    page.mouse.move(before["x"] + before["width"] / 2 + 120, before["y"] + 92, steps=8)
    page.mouse.up()
    page.wait_for_timeout(400)

    after = card.bounding_box()
    assert abs(after["x"] - before["x"]) > 40, "the card did not move"

    # Tidy up puts it back where the layout says it belongs.
    page.locator("[data-tidy]").click()
    page.wait_for_timeout(600)
    tidied = card.bounding_box()
    assert abs(tidied["x"] - after["x"]) > 10 or abs(tidied["y"] - after["y"]) > 10


def test_the_canvas_has_two_sizes(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)
    stage = page.locator("[data-stage]")
    expect(stage).to_have_attribute("data-mode", "page")

    page.locator("[data-canvas-mode='full']").click()
    expect(stage).to_have_attribute("data-mode", "full")
    box = stage.bounding_box()
    viewport = page.viewport_size
    assert box["height"] >= viewport["height"] - 2, "full screen did not fill the screen"
    assert box["width"] >= viewport["width"] - 2

    # Filling the screen means covering the shell around it. The side menu and the top
    # bar sat over the board until the board was lifted above them.
    covered = page.evaluate(
        """() => {
            const middle = [window.innerWidth / 2, window.innerHeight / 2];
            const corner = [80, 120];
            const on = (point) => {
                const found = document.elementFromPoint(...point);
                return found !== null && found.closest('[data-stage]') !== null;
            };
            return { middle: on(middle), corner: on(corner) };
        }"""
    )
    assert covered == {"middle": True, "corner": True}, f"the shell showed through: {covered}"

    page.keyboard.press("Escape")
    expect(stage).to_have_attribute("data-mode", "page")
    assert stage.bounding_box()["height"] < viewport["height"]


def test_the_whole_board_works_from_the_keyboard(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)

    # Open the list, search, choose and add — without touching the mouse.
    page.keyboard.press("Control+Enter")
    expect(page.locator("[data-library]")).to_be_visible()
    page.keyboard.type("candle moves by a percentage")
    page.wait_for_timeout(300)
    page.keyboard.press("Enter")
    expect(page.locator("[data-library]")).to_be_hidden()
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(1)

    # Move around the tree and open a card's settings with the keyboard.
    page.locator("[data-node='universe']").focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    focused = page.evaluate("() => document.activeElement.dataset.node")
    assert focused and focused.startswith("r"), f"the arrow keys landed on {focused}"

    page.keyboard.press("Enter")
    expect(page.locator("[data-inspector]")).to_be_visible()

    # Delete removes the focused card, and Ctrl+Z brings it back.
    page.locator(f"[data-node='{focused}']").focus()
    page.keyboard.press("Delete")
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(0)
    page.keyboard.press("Control+z")
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(1)


def test_every_card_carries_its_place_in_the_tree(page: Page, base_url: str) -> None:
    """A screen reader is told the same shape a sighted person sees."""
    _open_canvas(page, base_url)
    _add_first_condition(page)

    levels = page.locator("[data-nodes] [data-node]").evaluate_all(
        """nodes => nodes.map(node => ({
            role: node.getAttribute('role'),
            level: node.getAttribute('aria-level'),
            position: node.getAttribute('aria-posinset'),
            size: node.getAttribute('aria-setsize'),
        }))"""
    )
    assert all(item["role"] == "treeitem" for item in levels)
    assert all(item["level"] and item["position"] and item["size"] for item in levels)
    # Coins to watch, the main group, and how you hear about it — plus the rule inside.
    assert [item["level"] for item in levels] == ["1", "1", "2", "1"]


def test_a_starting_point_fills_the_board_in_one_step(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)

    starter = page.locator(".m-invite .m-starter").first
    expect(starter).to_be_visible()
    starter.click()

    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(1)
    expect(page.locator(".m-invite")).to_have_count(0)
    # One press of undo takes the whole starting point back off.
    page.locator("[data-undo]").click()
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(0)


def test_the_draft_survives_a_reload(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)
    _add_first_condition(page)
    expect(page.locator("[data-saved-pill]")).to_have_attribute("data-state", "saved")

    page.reload(wait_until="domcontentloaded")
    page.locator("[data-loading]").wait_for(state="hidden", timeout=30_000)
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(1)


def test_a_problem_points_at_the_card_it_is_about(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)
    _add_first_condition(page)

    page.locator("[data-checks-toggle]").click()
    expect(page.locator("[data-check-list] .m-check")).not_to_have_count(0)
    show = page.locator("[data-check-list] [data-show]").first
    expect(show).to_be_visible()
    show.click()

    page.wait_for_timeout(400)
    selected = page.locator("[data-node][aria-selected='true']")
    expect(selected).to_have_count(1)


# ── Choosing which coins ─────────────────────────────────────────────────────


def _open_coins_card(page: Page) -> None:
    page.locator("[data-node='universe']").click()
    expect(page.locator("[data-inspector]")).to_be_visible()
    expect(page.locator("[data-inspector-kind]")).to_contain_text("Coins to watch")


def test_naming_coins_yourself_opens_a_real_search(page: Page, base_url: str) -> None:
    """"Coins I name myself" used to store a mode and ask nothing else.

    The board then said a person had named coins when they had named none, and the
    monitor would have been refused the moment it was switched on — with a reason they
    could not act on from the page they were standing on.
    """

    _open_canvas(page, base_url)
    _open_coins_card(page)

    page.locator("[data-inspector-body] [data-universe='explicit_assets']").click()
    search = page.locator("[data-coin-search]")
    expect(search).to_be_visible()

    # Nothing is chosen, and the board says so rather than looking finished.
    expect(page.locator("[data-check-list]")).to_contain_text("Add at least one coin")
    expect(page.locator("[data-node='universe']")).to_have_attribute("data-incomplete", "true")

    search.fill("bt")
    page.wait_for_timeout(900)
    suggestions = page.locator("[data-coin-add]")
    if suggestions.count() == 0:
        pytest.skip("this database has no screened coins to search")

    ticker = suggestions.first.get_attribute("data-coin-add")
    suggestions.first.click()
    page.wait_for_timeout(400)

    # It becomes a chip, the card says how many, and the readout says which.
    expect(page.locator(f"[data-coin-remove='{ticker}']")).to_be_visible()
    expect(page.locator("[data-node='universe']")).to_have_attribute("data-incomplete", "false")
    expect(page.locator("[data-sentence-text]")).to_contain_text(ticker)

    # And one can be taken back out.
    page.locator(f"[data-coin-remove='{ticker}']").click()
    page.wait_for_timeout(400)
    expect(page.locator(f"[data-coin-remove='{ticker}']")).to_have_count(0)
    expect(page.locator("[data-node='universe']")).to_have_attribute("data-incomplete", "true")


def test_the_favorites_picker_says_what_is_missing_when_there_are_no_lists(
    page: Page, base_url: str
) -> None:
    _open_canvas(page, base_url)
    _open_coins_card(page)

    page.locator("[data-inspector-body] [data-universe='approved_watchlist']").click()
    page.wait_for_timeout(700)
    body = page.locator("[data-inspector-body]")
    # A fresh account has no Favorites list. The panel says so and offers the way to
    # make one, instead of showing an empty box with nothing in it.
    expect(body).to_contain_text("Favorites")
    expect(page.locator("[data-node='universe']")).to_have_attribute("data-incomplete", "true")


def test_email_is_offered_as_a_way_of_being_told(page: Page, base_url: str) -> None:
    """Email was deliverable everywhere else and namable nowhere. It is a choice now."""

    _open_canvas(page, base_url)
    # The invitation covers the board until there is something on it.
    _add_first_condition(page)
    page.locator("[data-node='alert']").click()
    expect(page.locator("[data-inspector-kind]")).to_contain_text("How you hear about it")
    expect(page.locator("[data-inspector-body] [data-channel='email']")).to_be_visible()


# ── The last step ────────────────────────────────────────────────────────────


def _complete_the_open_card(page: Page) -> None:
    """Fill the three values the card opened on. None of them has a default."""

    page.locator("[data-inspector-body] [data-set='direction'][data-value='up']").click()
    page.locator("[data-inspector-body] [data-set='comparator'][data-value='gte']").click()
    page.locator("[data-inspector-body] input[data-set='threshold']").fill("5")
    page.wait_for_timeout(500)


def _choose_a_way_of_being_told(page: Page) -> None:
    page.locator("[data-node='alert']").click()
    page.locator("[data-inspector-body] [data-channel='web']").click()
    page.wait_for_timeout(400)


def _finish_the_board(page: Page) -> None:
    """Every check passing: one complete condition and one way of being told."""

    _add_first_condition(page)
    _complete_the_open_card(page)
    _choose_a_way_of_being_told(page)


def test_the_next_step_appears_only_when_nothing_is_blocking(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)
    expect(page.locator("[data-next-step]")).to_be_hidden()

    _add_first_condition(page)
    # A card with nothing set is blocking, so there is still no next step.
    expect(page.locator("[data-next-step]")).to_be_hidden()

    _complete_the_open_card(page)
    # And a monitor with no way of telling anybody is still blocking.
    expect(page.locator("[data-next-step]")).to_be_hidden()

    _choose_a_way_of_being_told(page)
    expect(page.locator("[data-next-step]")).to_be_visible()
    expect(page.locator("[data-meter-text]")).to_contain_text("Ready")


def test_the_last_step_reads_the_setup_back_in_plain_words(page: Page, base_url: str) -> None:
    _open_canvas(page, base_url)
    _finish_the_board(page)

    page.locator("[data-next-step]").click()
    dialog = page.locator("[data-launch]")
    expect(dialog).to_be_visible()

    readback = dialog.locator("[data-launch-readback]")
    expect(readback).to_contain_text("Coins to watch")
    expect(readback).to_contain_text("Tell me when")
    expect(readback).to_contain_text("How you hear about it")
    # The rule is read back as the sentence the card prints, never as a key or a number
    # nobody chose.
    expect(readback).to_contain_text("goes up")
    expect(readback).to_contain_text("5%")

    text = readback.inner_text()
    for internal in ("gte", "close_to_close", "eligible_market", "mechanic", "null"):
        assert internal not in text, f"the popup showed an internal word: {internal}"

    # Nothing has happened yet, and it says so.
    expect(dialog).to_contain_text("Nothing is watching yet")
    page.locator("[data-launch-cancel]").click()
    expect(dialog).to_be_hidden()


def test_switching_it_on_sends_one_test_message_and_says_what_happened(
    page: Page, base_url: str
) -> None:
    """The whole point of the step: it is on, and this is what reached you.

    Both outcomes are real answers and both are checked. What must never happen is a
    popup that stops halfway, or one that says it worked without saying where.
    """

    _open_canvas(page, base_url)
    _finish_the_board(page)
    page.locator("[data-next-step]").click()
    dialog = page.locator("[data-launch]")
    expect(dialog).to_be_visible()

    page.locator("[data-launch-name]").fill("My first monitor")

    # The risk note is asked once, and the button waits for it. A monitor cannot start
    # without an acceptance on file, and nothing on the website used to ask — so every
    # monitor built here failed at the last step with a word from inside the machine.
    go = page.locator("[data-launch-go]")
    expect(go).to_be_disabled()
    page.locator("[data-launch-accept]").check()
    expect(go).to_be_enabled()
    go.click()

    # It reaches an answer, and the bar stops on one of the three real states.
    done = dialog.locator("[data-launch-step='done']")
    expect(done).to_be_visible(timeout=60_000)
    tone = dialog.locator("[data-launch-outcome]").get_attribute("data-tone")
    assert tone == "ok", (
        "the monitor was not switched on: "
        f"{dialog.locator('[data-launch-outcome]').inner_text()}"
    )

    expect(dialog).to_contain_text("is watching now")
    # One row per way of being told, each with a word beside its mark.
    rows = dialog.locator("[data-launch-results] .m-send")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("In the dashboard")
    expect(rows.first).to_contain_text("Sent")
    expect(dialog).to_contain_text("settings")

    # And the finish button takes them there.
    page.locator("[data-launch-finish]").click()
    page.wait_for_url(re.compile(r"/dashboard/settings"), timeout=30_000)

    # The monitor is real: it is on the Monitors page, watching, under its own name.
    page.goto(f"{base_url}/dashboard/monitors", wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("[data-w-card]").filter(has_text="My first monitor")).to_have_count(1)


@pytest.mark.parametrize("width", [1440, 1024, 760])
def test_the_page_never_scrolls_sideways(page: Page, base_url: str, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    _open_canvas(page, base_url)
    _add_first_condition(page)
    assert_no_horizontal_overflow(page)


def test_nothing_moves_when_a_person_asks_for_less_motion(page: Page, base_url: str) -> None:
    page.emulate_media(reduced_motion="reduce")
    _open_canvas(page, base_url)
    _add_first_condition(page)

    # The board still works, and nothing on it moves for long or repeats.
    expect(page.locator("[data-node][data-kind='rule']")).to_have_count(1)
    moving = page.evaluate(
        """() => document.getAnimations()
            .map(item => {
                const timing = item.effect ? item.effect.getTiming() : {};
                return {
                    name: item.animationName || item.transitionProperty || 'script',
                    duration: Number(timing.duration) || 0,
                    iterations: timing.iterations,
                };
            })
            .filter(item => item.duration > 60 || item.iterations === Infinity)"""
    )
    assert moving == [], f"movement survived reduced motion: {moving[:6]}"


# ── When the contract cannot be read ─────────────────────────────────────────
#
# The canvas cannot draw a single card until the server has said what this platform can
# watch for. Everything below is about the ways that read can go wrong, because they all
# used to end in the same place: a page that says "Reading what this platform can watch
# for…" and never says anything else.


def test_a_contract_that_never_answers_ends_in_a_message_not_a_wait(
    page: Page, base_url: str
) -> None:
    """The reported fault, as a test.

    The request is accepted and then never answered — a stalled connection, a proxy
    holding it open, a laptop that went to sleep. `fetch` neither resolves nor rejects,
    so the canvas used to sit on its loading sentence for ever with no error and no way
    out. It gives up now, and says which of the two things happened.
    """

    signup(page, base_url)
    close_any_open_guide(page)
    # Accepted, never answered. A route handler that never replies holds it open.
    page.route(CONTRACT_URL, lambda route: None)
    page.goto(f"{base_url}{MONITOR_PATH}", wait_until="domcontentloaded")
    close_any_open_guide(page)

    banner = page.locator("[data-contract-error]")
    # Longer than the wait itself, so what is being asserted is that the page gives up,
    # not how fast the machine running the test is.
    expect(banner).to_be_visible(timeout=45_000)
    expect(page.locator("[data-loading]")).to_be_hidden()
    expect(page.locator("[data-contract-reason]")).to_contain_text("did not answer in time")
    # And it says the draft is safe, because that is a person's first worry.
    expect(banner).to_contain_text("draft is untouched")
    assert_no_raw_traceback(page)


@pytest.mark.deliberate_console_errors("Failed to load resource", "503")
def test_the_try_again_button_really_tries_again(page: Page, base_url: str) -> None:
    """The only way out of a failed read has to have something behind it.

    The click handler used to be registered inside the function that runs *after* the
    contract has been read. On the one path where the button is shown, it had no handler
    at all: pressing it did nothing, for ever.
    """

    signup(page, base_url)
    close_any_open_guide(page)

    attempts = {"count": 0}

    def answer(route: Any) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            route.fulfill(status=503, content_type="application/json", body="{}")
        else:
            route.fallback()

    page.route(CONTRACT_URL, answer)
    page.goto(f"{base_url}{MONITOR_PATH}", wait_until="domcontentloaded")
    close_any_open_guide(page)

    expect(page.locator("[data-contract-error]")).to_be_visible(timeout=30_000)
    page.locator("[data-contract-retry]").click()

    close_any_open_guide(page)
    page.locator("[data-loading]").wait_for(state="hidden", timeout=30_000)
    expect(page.locator("[data-node='universe']")).to_be_visible()
    assert attempts["count"] >= 2, "pressing the button did not ask the server again"


def test_the_page_puts_a_time_limit_on_every_request_it_sends(
    page: Page, base_url: str
) -> None:
    """The limit is installed once, for the whole page, before anything else runs.

    Checked in a real browser rather than by reading the template, because what matters
    is that `fetch` is really wrapped by the time any page code uses it.
    """

    _open_canvas(page, base_url)

    waits = page.evaluate(
        "() => window.hmWait && { reading: hmWait.reading, changing: hmWait.changing }"
    )
    assert waits, "nothing bounded the requests this page sends"
    assert 5_000 <= waits["reading"] <= 30_000
    assert waits["reading"] < waits["changing"]

    # Asking a question gives up quickly, because nothing was changed and trying again
    # is safe. Anything that makes something happen is given far longer, because the
    # server may already have done it.
    assert page.evaluate("() => window.hmWait.forMethod('GET')") == waits["reading"]
    assert page.evaluate("() => window.hmWait.forMethod('POST')") == waits["changing"]
    assert page.evaluate("() => window.hmWait.forMethod('delete')") == waits["changing"]


# ── Opening a monitor somebody already has ───────────────────────────────────
#
# "Change it" on the Monitors page opens this page with a monitor id in the address. The
# board it draws has to be that monitor's own, and where there is none the page has to
# say so — an empty board under somebody's monitor name can be switched on, and doing
# that would replace their rules with nothing.


BOARD_URL = "**/api/v1/dashboard/monitor-canvas/monitors/*"


def _open_change(page: Page, base_url: str, monitor_id: str) -> None:
    signup(page, base_url)
    close_any_open_guide(page)
    page.goto(
        f"{base_url}{MONITOR_PATH}?monitor={monitor_id}",
        wait_until="domcontentloaded",
    )
    close_any_open_guide(page)
    page.locator("[data-loading]").wait_for(state="hidden", timeout=30_000)


@pytest.mark.deliberate_console_errors("Failed to load resource", "404")
def test_a_monitor_with_no_saved_board_says_so_and_offers_a_new_one(
    page: Page, base_url: str
) -> None:
    """The board that must never be drawn: an empty one under a monitor's name.

    The 404 in the console is the point, not a fault: asking for a monitor that is not on
    this account is answered by refusing, and the page turns that refusal into a sentence
    instead of an empty board.
    """

    _open_change(page, base_url, "11111111-1111-1111-1111-111111111111")

    notice = page.locator("[data-open-notice]")
    expect(notice).to_be_visible(timeout=20_000)
    expect(notice).to_contain_text("cannot be opened here")
    # And a way forward that leaves that monitor alone.
    expect(page.locator("[data-open-notice-fresh]")).to_be_visible()
    assert_no_horizontal_overflow(page)
    assert_no_raw_traceback(page)


def test_a_saved_board_is_drawn_as_the_monitor_it_belongs_to(
    page: Page, base_url: str
) -> None:
    """The round trip, in a browser: a stored board becomes cards on the canvas.

    The board is served by a stubbed reply rather than by building a real monitor first,
    because what is being checked is the *drawing* — that the shape the server keeps
    becomes the shape a person sees, with the right card, the right group and the right
    coins.
    """

    monitor_id = "22222222-2222-2222-2222-222222222222"
    board = {
        "monitor": {"id": monitor_id, "name": "My saved monitor"},
        "reason": None,
        "plan": {
            "name": "My saved monitor",
            "root": {
                "kind": "group",
                "op": "and",
                "children": [
                    {
                        "kind": "rule",
                        "mechanic": "open_to_close_percentage",
                        "values": {
                            "threshold": 3,
                            "timeframe": "15m",
                            "comparator": "gte",
                            "direction": "up",
                        },
                        "required": True,
                        "children": [],
                    }
                ],
            },
            "universe": {
                "mode": "explicit_assets",
                "watchlist_id": None,
                "symbols": ["BTC"],
            },
            "alert": {"channels": ["web"], "cooldown_minutes": 45},
        },
    }
    page.route(
        BOARD_URL,
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(board),
        ),
    )

    _open_change(page, base_url, monitor_id)

    # The monitor's own name on the page, so "Change it" can never land somewhere that
    # could be about any monitor.
    expect(page.locator("[data-page-title]")).to_have_text("My saved monitor")
    # The rule that was saved, its group, and the two cards every monitor has. An empty
    # board draws three of those; the fourth is the card that came back.
    expect(page.locator("[data-nodes] [data-node]")).to_have_count(4)
    expect(page.locator("[data-open-notice]")).to_be_hidden()
    # And what was stored is what is read back, not a default.
    expect(page.locator("[data-node='universe']")).to_contain_text("BTC")
    assert_no_horizontal_overflow(page)
    assert_no_raw_traceback(page)


def test_a_request_to_somebody_elses_server_keeps_its_own_behaviour(
    page: Page, base_url: str
) -> None:
    """This limit is about our own server, not a third party's.

    A payment page or an analytics script decides how long it waits for itself, and this
    product is not in a position to overrule it. Proved by holding a request to another
    origin open for longer than our own limit and showing it is still waiting.
    """

    _open_canvas(page, base_url)
    page.route("https://example.invalid/**", lambda route: None)

    settled = page.evaluate(
        """async () => {
            let done = false;
            fetch('https://example.invalid/probe').then(
                () => { done = true; },
                () => { done = true; },
            );
            const past = window.hmWait.reading + 3000;
            await new Promise((resolve) => window.setTimeout(resolve, past));
            return done;
        }""",
    )
    assert settled is False, "our own time limit was applied to another origin's request"
