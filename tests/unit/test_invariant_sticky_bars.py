"""A bar that sticks while the page scrolls stops under the topbar, never behind it.

The dashboard's topbar is held near the top of the window (`hm-shell.css`) and drawn
above the page. The Passport's section links (`.t-tabs`) and the Settings page's jump
links (`.a-jump`) and save line (`.g-saved`) were each written with `top: 0`, so once
the page scrolled they slid up into the same place and the topbar covered them — and on
Settings the two bars covered each other as well.

The rule now: every page-level sticky bar in the redesigned sheets stops at
`--hm-sticky-top`, the one value `hm-shell.css` derives from the topbar's own position
and height. On the public website the same value is set from the site's fixed header
(`hm-jump.js`), so a bar written once stops in the right place on both.

`top: 0` is still right for a sticky element inside its *own* scroll box — a table head
in a scrolling table, an inspector head in a scrolling panel — because there the top of
the box is not the top of the window. Those are named below; anything else fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor" / "static"

#: The redesigned dashboard's sheets: every page with the topbar is styled by these.
SHEETS = ("hm-dashboard-test.css", "hm-account-test.css", "hm-monitor-test.css")

#: Sticky inside their own scroll box, where `top: 0` is the top of that box.
INSIDE_A_SCROLL_BOX = {
    "body.hilal-dashboard .hm-s .s-compare thead th",  # in `.s-compare-wrap`, overflow-x
    "body.hilal-dashboard .hm-t .t-table thead th",  # in `.t-table-wrap`, overflow-x
    "body.hilal-dashboard .hm-m .m-inspector-head",  # in `.m-inspector`, overflow auto
}

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _sticky_rules() -> list[tuple[str, str, str]]:
    rules: list[tuple[str, str, str]] = []
    for name in SHEETS:
        css = re.sub(r"/\*.*?\*/", "", (STATIC / name).read_text(encoding="utf-8"), flags=re.S)
        for selector, body in RULE.findall(css):
            if re.search(r"position:\s*sticky", body):
                top = re.search(r"(?<![-\w])top:\s*([^;]+);", body)
                rules.append(
                    (name, " ".join(selector.split()), top.group(1).strip() if top else "")
                )
    return rules


def test_the_redesigned_sheets_have_sticky_bars_to_check():
    """Guards the guard: a parser that found nothing would pass everything."""
    selectors = {selector for _sheet, selector, _top in _sticky_rules()}
    assert "body.hilal-dashboard .hm-t .t-tabs" in selectors
    assert "body.hilal-dashboard .hm-t .a-jump" in selectors
    assert "body.hilal-dashboard .hm-g .g-sticky" in selectors


@pytest.mark.parametrize(
    ("sheet", "selector", "top"),
    _sticky_rules(),
    ids=[f"{sheet}:{selector}" for sheet, selector, _top in _sticky_rules()],
)
def test_every_page_level_sticky_bar_stops_under_the_topbar(sheet, selector, top):
    if selector in INSIDE_A_SCROLL_BOX:
        return
    assert top.startswith("var(--hm-sticky-top"), (
        f"{sheet}: `{selector}` sticks at `top: {top or 'unset'}`. The topbar is held "
        "there and drawn above it, so the bar would slide under it. Use "
        "`top: var(--hm-sticky-top, 0px)`."
    )


def test_the_stop_line_follows_the_topbar():
    """One value, made from the same two tokens that place and size the topbar."""
    shell = (STATIC / "hm-shell.css").read_text(encoding="utf-8")
    assert re.search(
        r"--hm-sticky-top:\s*calc\(var\(--hm-top-stick\)\s*\+\s*var\(--hm-top-h\)", shell
    )
    top_rule = re.search(r"body\.hilal-dashboard \.hm-top \{([^}]*)\}", shell)
    assert top_rule, "the topbar rule moved"
    assert re.search(r"top:\s*var\(--hm-top-stick\)", top_rule.group(1))
    assert re.search(r"height:\s*var\(--hm-top-h\)", top_rule.group(1))
    # A screen size that moves or resizes the topbar does it through the tokens, so the
    # stop line moves with it. A raw `top:` or `height:` on the bar would leave it behind.
    for block in re.findall(r"body\.hilal-dashboard \.hm-top \{([^}]*)\}", shell)[1:]:
        assert not re.search(r"(?<![-\w])(top|height):", block), block


def test_the_two_settings_bars_are_held_as_one():
    """Held separately, the save line and the jump links stuck in the same place."""
    sheet = (STATIC / "hm-account-test.css").read_text(encoding="utf-8")
    saved = re.search(r"body\.hilal-dashboard \.hm-g \.g-saved \{([^}]*)\}", sheet)
    assert saved and "sticky" not in saved.group(1)
    assert re.search(r"\.g-sticky \.a-jump \{\s*position:\s*static;", sheet)


def test_a_public_page_stops_its_bars_under_the_website_header():
    sheet = (STATIC / "hm-dashboard-test.css").read_text(encoding="utf-8")
    assert re.search(r"body\.hm-public-market \{\s*--hm-sticky-top:\s*\d+px;", sheet)
    script = (STATIC / "hm-jump.js").read_text(encoding="utf-8")
    assert "export function holdBelowFixedHeader" in script
    assert '"--hm-sticky-top"' in script
    passport = (STATIC / "hm-passport-test.js").read_text(encoding="utf-8")
    assert "holdBelowFixedHeader()" in passport
    # One tracker for both pages' jump links, measured against the bar itself.
    assert "followSections(links, document, { bar: tabs })" in passport
    assert "rootMargin" not in passport
