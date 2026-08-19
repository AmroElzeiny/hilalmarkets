"""Every request this product sends to its own server has to end.

The monitor canvas sat on "Reading what this platform can watch for…" for ever, because
a request that is accepted and then never answered never resolves and never rejects: the
line after the `await` never runs, so no error is ever shown and no button is ever
offered. Nothing about that was special to the canvas. Every one of the forty-odd
requests in this product was written the same way.

The fix is one owner — `static/hm-request.js` — that puts a time limit on `fetch` itself.
That only works while two things stay true, and neither is visible when reading any
single page, so they are checked here:

1. every page that ships scripts loads the owner, and loads it **first**;
2. nothing else invents a second time limit of its own.

The second rule is the one this codebase keeps paying for: two modules deciding the same
thing separately, and drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src/ai_market_monitor"
TEMPLATES = SRC / "templates"
STATIC = SRC / "static"

#: The one file that decides how long this product waits for its own server.
OWNER = STATIC / "hm-request.js"

#: Third-party code we ship but did not write. It brings its own behaviour and this rule
#: is not about it.
VENDORED = ("vendor/", "landing/")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _pages_with_scripts() -> list[Path]:
    """Every template that owns a ``<head>`` and ships at least one script.

    A page that extends a base inherits the base's scripts, so only the templates that
    own the document can load anything before them.
    """

    pages = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = _read(path)
        if "<head>" in text and "<script" in text:
            pages.append(path)
    return pages


def _first_script(text: str) -> str:
    match = re.search(r"<script\b[^>]*>", text)
    assert match is not None
    return match.group(0)


def test_the_owner_exists() -> None:
    assert OWNER.is_file(), "the file that bounds every request is missing"


@pytest.mark.parametrize("page", _pages_with_scripts(), ids=lambda path: path.name)
def test_every_page_that_ships_scripts_loads_the_owner(page: Path) -> None:
    assert "hm-request.js" in _read(page), (
        f"{page.name} ships scripts but not the file that stops a request waiting for"
        " ever, so every request it sends can hang the page it is on"
    )


@pytest.mark.parametrize("page", _pages_with_scripts(), ids=lambda path: path.name)
def test_the_owner_is_the_first_script_on_the_page(page: Path) -> None:
    """It can only bound a request that is sent after it has run.

    A script placed later still works for most pages, and that is the trap: it would
    quietly miss whichever request happened to be sent by the script above it.
    """

    assert "hm-request.js" in _first_script(_read(page)), (
        f"{page.name} loads another script before hm-request.js, so anything that"
        " script sends is unbounded"
    )


@pytest.mark.parametrize("page", _pages_with_scripts(), ids=lambda path: path.name)
def test_the_owner_is_never_deferred(page: Path) -> None:
    """`defer` and `type="module"` both postpone a script until after parsing.

    Either one would put the owner behind every ordinary script on the page, which is
    the same failure as loading it last.
    """

    tag = _first_script(_read(page))
    assert "defer" not in tag and 'type="module"' not in tag, (
        f"{page.name} defers hm-request.js, so the scripts below it run first"
    )


def _shipped_scripts() -> list[Path]:
    return sorted(
        path
        for path in STATIC.rglob("*.js")
        if path != OWNER
        and not any(part in path.as_posix() for part in VENDORED)
    )


@pytest.mark.parametrize("script", _shipped_scripts(), ids=lambda path: path.name)
def test_no_second_time_limit_is_invented(script: Path) -> None:
    """One number, in one place.

    A page that set its own limit would be a second opinion about how long this product
    waits, and the two would disagree the first time either changed.
    """

    text = _read(script)
    assert "AbortSignal.timeout" not in text, (
        f"{script.name} sets its own request time limit; hm-request.js owns that"
    )


def test_the_two_waits_are_named_and_ordered() -> None:
    """A question waits a short time; something that changes the world waits longer."""

    text = _read(OWNER)
    reading = int(re.search(r"reading:\s*([\d_]+)", text).group(1).replace("_", ""))
    changing = int(re.search(r"changing:\s*([\d_]+)", text).group(1).replace("_", ""))

    assert 5_000 <= reading <= 30_000, "a read either answers quickly or has failed"
    assert reading < changing <= 180_000, "a change needs longer, and still has to end"


def test_which_wait_applies_is_decided_by_the_request_not_by_a_list() -> None:
    """The rule has to be about the request itself.

    A list of slow addresses is the shape this codebase keeps paying for: one endpoint
    added and not added here, or one path misspelt, and the wrong limit fires with
    nothing anywhere to say so. `GET` and `HEAD` only ask a question — nothing was
    changed, so giving up is safe. Everything else may already have happened on the
    server, and cutting it off early would tell a person it failed when it did not.
    """

    text = _read(OWNER)
    assert '=== "GET"' in text and '=== "HEAD"' in text, (
        "the short wait is no longer decided by the method"
    )
    assert "SLOW_PATHS" not in text, "a list of addresses came back"
