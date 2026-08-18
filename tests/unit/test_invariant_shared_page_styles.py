"""Two pages sharing class names must share every rule for them.

Watchlists (`.hm-w`) and Opportunities (`.hm-o`) are built from one stylesheet and one
set of `w-*` class names. That is deliberate — the two pages are the same shapes with
different content — but it has a failure mode that is invisible in review and almost
invisible in use: a rule scoped to one page only.

That is exactly what happened to the "nothing found yet" panel. Every rule for it was
written under `.hm-w`, while the Opportunities template used the same class names. A
person with no Watchlists saw a designed empty state; a person whose lists had simply
not found anything yet saw the same words with no layout at all — bare text with the
arrows stacked underneath it. Both pages *have* the rules in the same file, so reading
the diff showed nothing wrong.

This test reads the stylesheet and fails on any `w-*` rule that only one of them gets.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STYLESHEET = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_market_monitor"
    / "static"
    / "hm-watch-test.css"
)

#: A selector that names one page and then a shared `w-*` class.
_ONE_PAGE_ONLY = re.compile(r"\.hm-(?P<page>[wo])\b(?![-,)])(?P<rest>[^,{]*)")

#: Class names that genuinely belong to one page.
#:
#: There are none today. The list exists so that a real single-page component can be
#: added deliberately, with its reason written down, rather than by a scope quietly
#: narrowing and nobody noticing.
ALLOWED_ONE_PAGE_CLASSES: frozenset[str] = frozenset()


def _selectors() -> list[str]:
    body = STYLESHEET.read_text(encoding="utf-8")
    # Comments carry example selectors; stripping them keeps prose out of the check.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return [
        part.strip()
        for block in re.findall(r"([^{}]+)\{", body)
        for part in block.split(",")
        if part.strip().startswith("body")
    ]


def test_the_stylesheet_is_actually_there():
    assert STYLESHEET.exists()
    assert _selectors(), "no selectors were found; this check needs updating"


@pytest.mark.parametrize("page", ["w", "o"])
def test_no_shared_class_is_styled_for_only_one_of_the_two_pages(page: str):
    offenders = []
    for selector in _selectors():
        # `:is(.hm-w, .hm-o)` is the correct form and never matches below, because the
        # page class is immediately followed by `,` inside the `:is(`.
        for match in _ONE_PAGE_ONLY.finditer(selector):
            if match.group("page") != page:
                continue
            classes = set(re.findall(r"\.(w-[a-z-]+)", match.group("rest")))
            unexpected = classes - ALLOWED_ONE_PAGE_CLASSES
            if unexpected:
                offenders.append((selector.strip(), sorted(unexpected)))
    assert offenders == [], (
        f"these style a shared class for .hm-{page} only, so the other page renders it "
        f"unstyled: {offenders}"
    )
