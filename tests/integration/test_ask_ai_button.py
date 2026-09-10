"""The Ask AI button is rendered only when the assistant gate is on.

These tests use the real HTTP router so they exercise the same Jinja environment and
context the browser would see, without needing a browser.
"""

from __future__ import annotations

import re

import pytest
from lxml import html as lxml_html

from tests.integration.test_dashboard_test_opportunities import _with_one_opportunity
from tests.integration.test_dashboard_web import _signup_and_verify

_BUTTON_RE = re.compile(
    r'<button\s+[^>]*?data-hilal-ask[^>]*?>',
    re.IGNORECASE | re.DOTALL,
)


async def _market_page(test_context):
    await _signup_and_verify(test_context, email="askai-market@example.com")
    return await test_context["client"].get("/dashboard/market")


async def _settings_page(test_context):
    await _signup_and_verify(test_context, email="askai-settings@example.com")
    return await test_context["client"].get("/dashboard/settings")


async def _monitor_page(test_context):
    await _signup_and_verify(test_context, email="askai-monitor@example.com")
    return await test_context["client"].get("/dashboard/create-monitor")


async def _opportunities_page(test_context):
    # The filter bar, and with it the button, renders only when the page has something
    # to filter — so this account is seeded with one found coin first.
    await _with_one_opportunity(test_context, email="askai-opportunities@example.com")
    return await test_context["client"].get("/dashboard/opportunities")


def _ask_button_tags(html: str) -> list[str]:
    return _BUTTON_RE.findall(html)


@pytest.mark.parametrize(
    "fetch_page",
    [
        pytest.param(_market_page, id="market"),
        pytest.param(_settings_page, id="settings"),
    ],
)
async def test_ask_ai_button_renders_with_required_accessibility_attributes(
    test_context, fetch_page
):
    """A real button that opens the dialog, with the visible label in the name."""

    page = await fetch_page(test_context)
    assert page.status_code == 200

    tags = _ask_button_tags(page.text)
    assert len(tags) >= 1, "no Ask AI button rendered on a gated page"

    for tag in tags:
        assert 'type="button"' in tag or "type='button'" in tag, tag
        assert 'aria-haspopup="dialog"' in tag, tag
        assert 'aria-controls="hilal-window"' in tag, tag
        assert "aria-expanded" not in tag
        assert "data-hilal-ask" in tag
        label_match = re.search(r'aria-label="([^"]*)"', tag)
        assert label_match, f"no aria-label in {tag}"
        label = label_match.group(1)
        assert label.startswith("Ask AI"), (
            f"accessible name {label!r} does not start with visible label 'Ask AI'"
        )


def test_settings_page_has_eight_distinct_accessible_names() -> None:
    """The eight settings groups each carry a heading-derived accessible name."""

    from pathlib import Path

    source = Path(
        "src/ai_market_monitor/templates/hilal/dashboard_test/settings.html"
    ).read_text(encoding="utf-8")
    names = re.findall(r'ask_ai\("([^"]+)"', source)
    assert len(names) == 8, f"expected 8 settings Ask AI names, found {len(names)}"
    assert all(name.startswith("Ask AI ") for name in names)
    assert len(set(names)) == 8, "settings groups must have distinct accessible names"


async def test_no_ask_ai_button_or_dead_controls_when_chat_is_disabled(test_context):
    """With the assistant switched off, the page is clean: no button, no partial."""

    await _signup_and_verify(test_context, email="askai-off@example.com")
    test_context["settings"].hilal_chat_enabled = False

    page = await test_context["client"].get("/dashboard/market")
    assert page.status_code == 200
    assert "data-hilal-ask" not in page.text
    assert 'aria-controls="hilal-window"' not in page.text
    # The assistant root, orb, and its stylesheet are not included.
    assert 'class="hm-hilal"' not in page.text
    assert "data-hilal-window" not in page.text
    assert "hm-hilal-chat.css" not in page.text


# ── R1 / R6 / R7: the button is the last child of its bar ────────────────────
#
# The visual contract places the button at the END of each filter bar (S1 market:
# last child of `div.t-controls`; S2 monitor: last child of the first
# `.m-bar-group`; S3 opportunities: last child of `.w-bar`). The breathing
# animation may only sit beside a neighbour when a real gap is guaranteed — being
# last in its bar is what the contract accepts in exchange. Anything that later
# gets appended after the button (a note, a new filter) breaks the rule, so this
# asserts it on the rendered page, on every page that carries the button, not
# only on the one that broke.

_BAR_BY_PAGE = {
    "market": ("/dashboard/market", _market_page, "t-controls"),
    "monitor": ("/dashboard/create-monitor", _monitor_page, "m-bar-group"),
    "opportunities": ("/dashboard/opportunities", _opportunities_page, "w-bar"),
}


def _element_children(node) -> list:
    """Only the element children — comments and text are not children here."""

    return [child for child in node if isinstance(child.tag, str)]


@pytest.mark.parametrize(
    ("page_name", "fetch_page", "bar_class"),
    [
        pytest.param(*_BAR_BY_PAGE[name], id=name)
        for name in ("market", "monitor", "opportunities")
    ],
)
async def test_ask_ai_button_is_the_last_child_of_its_bar(
    test_context, page_name: str, fetch_page, bar_class: str
) -> None:
    """Every rendered Ask AI button must be the last element child of its bar."""

    page = await fetch_page(test_context)
    assert page.status_code == 200, f"{page_name}: page did not render"

    document = lxml_html.fromstring(page.text)
    buttons = document.xpath('//*[@data-hilal-ask]')
    assert buttons, f"{page_name}: no Ask AI button rendered"

    for button in buttons:
        bar = button.getparent()
        assert bar is not None
        assert bar.tag == "div", (
            f"{page_name}: button sits in <{bar.tag}>, not in a div"
        )
        classes = (bar.get("class") or "").split()
        assert bar_class in classes, (
            f"{page_name}: button's parent is div.{classes}, "
            f"expected it to carry '{bar_class}'"
        )
        siblings = _element_children(bar)
        assert siblings[-1] is button, (
            f"{page_name}: Ask AI button must be the last child of "
            f"div.{bar_class}; after it sits "
            f"<{siblings[-1].tag} class={siblings[-1].get('class')!r}>"
        )
