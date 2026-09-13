"""Every tag a page opens is closed where it was opened.

A browser never reports a stray end tag. It repairs the page without a word: an extra
``</div>`` closes every element back to the nearest open ``<div>``, and everything after
it moves out of its container. The settings page carried one for a release — every group
from "How much we send" down fell out of the list that lays the groups out — and nothing
failed, because the page still rendered and every element was still there.

This reads the HTML the server sent, before any browser repair, and names each end tag
that does not close the element opened last, and each element never closed at all.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Final

#: Elements that never have an end tag.
_VOID: Final[frozenset[str]] = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Elements whose end tag HTML lets an author leave out. They close when their parent
#: does, so an open one is not an error when its parent's end tag arrives.
_OPTIONAL_END: Final[frozenset[str]] = frozenset(
    {
        "caption",
        "colgroup",
        "dd",
        "dt",
        "li",
        "optgroup",
        "option",
        "p",
        "rp",
        "rt",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
    }
)


class _TagBalance(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[tuple[str, int]] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID:
            self.open.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<path />` inside an SVG, or `<br />`: opened and closed in one.
        return

    def handle_endtag(self, tag: str) -> None:
        line = self.getpos()[0]
        if tag in _VOID:
            self.problems.append(f"line {line}: </{tag}> ends an element that has no end")
            return
        while self.open and self.open[-1][0] != tag and self.open[-1][0] in _OPTIONAL_END:
            self.open.pop()
        if self.open and self.open[-1][0] == tag:
            self.open.pop()
            return
        # The stray end tag is named and set aside, not obeyed, so what follows it is
        # judged as written and one mistake is reported once.
        if self.open:
            name, opened_on = self.open[-1]
            self.problems.append(
                f"line {line}: </{tag}> does not close the <{name}> opened on line {opened_on}"
            )
        else:
            self.problems.append(f"line {line}: </{tag}> with nothing open")


def unbalanced_tags(html: str) -> list[str]:
    """Each end tag that closes the wrong element, and each element never closed."""

    checker = _TagBalance()
    checker.feed(html)
    checker.close()
    never_closed = [
        f"line {line}: <{name}> is never closed"
        for name, line in checker.open
        if name not in _OPTIONAL_END
    ]
    return checker.problems + never_closed
