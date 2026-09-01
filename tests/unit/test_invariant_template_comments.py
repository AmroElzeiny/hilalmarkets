"""A comment that quotes Jinja's closing sequence stops being a comment there.

`{# … #}` ends at the **first** `#}`. A comment explaining Jinja syntax, and quoting the
closing marker while it does so, therefore ends early — and everything the author still
believed was commentary becomes literal text in the rendered page.

That happened on 31 August 2026 in `hilal/public/react_site.html`, in a comment warning
the next editor about Jinja comments. A paragraph of prose printed **above the doctype**
of every React page: the landing page, features, how it works, contact, all three legal
pages and the methodology page.

Nothing caught it. The template is not malformed, so `check_jinja_templates.py` loaded it
happily; the page still rendered, so the browser tests passed; and the leaked text sat at
the very top of the document where no assertion was looking. Only a screenshot found it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor" / "templates"
COMMENT = re.compile(r"\{#-?(.*?)-?#\}", re.DOTALL)

ALL = sorted(TEMPLATES.rglob("*.html"))


def test_there_are_templates_to_check():
    """Without this the rules below pass by finding no files at all."""

    assert len(ALL) > 50


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_no_comment_quotes_the_syntax_that_ends_it(path):
    """A comment that contains `{#` has almost certainly already ended somewhere.

    Explaining Jinja comment syntax is a reasonable thing for a template to do. Doing it
    by writing the markers out is not: describe them in words instead.
    """

    text = path.read_text(encoding="utf-8")
    offenders = [
        text[: match.start()].count("\n") + 1
        for match in COMMENT.finditer(text)
        if "{#" in match.group(1)
    ]
    assert offenders == [], (
        f"{path.name} has a comment quoting Jinja's own markers at line(s) {offenders}. "
        "The comment ends at the first `#}` inside it and the rest becomes page text."
    )


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_no_closing_marker_is_left_loose_in_a_page(path):
    """The symptom, checked directly: a `#}` outside any comment.

    The rule above catches the cause. This catches it however it arose — a deleted
    opening marker, a bad merge, a copied fragment — because what actually harms a
    reader is the stray text, not the reason for it.
    """

    outside = COMMENT.sub("", path.read_text(encoding="utf-8"))
    assert "#}" not in outside, (
        f"{path.name} has a `#}}` that closes no comment. Whatever follows the comment "
        "it was meant to close is being printed into the page."
    )
