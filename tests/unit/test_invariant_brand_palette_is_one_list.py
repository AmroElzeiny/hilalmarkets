"""The approved brand palette must be one list, not two that drift apart.

`hilalmarkets-brand.css` declares the brand's colours. The browser suite carries a
second, hand-written copy of them, and it is that copy which decides whether a page
"escapes the approved brand palette".

The two drifted. Four colours the brand file has always declared were missing from the
copy, and one of them — `--hm-control-line`, the edge of every secondary button — put
`/home` in permanent failure for painting itself in a brand colour.

The browser suite needs a real browser, so it does not run everywhere. This check is
plain text against two files, so it runs in the ordinary unit suite and catches the
drift the moment a colour is added to the brand file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_BRAND_CSS = _ROOT / "src" / "ai_market_monitor" / "static" / "hilalmarkets-brand.css"
_BROWSER_CONFTEST = _ROOT / "tests" / "browser" / "conftest.py"

#: `--hm-name: #rrggbb;` — only the tokens that state a colour of their own. A token
#: defined as `var(--other)` is already covered by whatever it points at.
_TOKEN = re.compile(r"(--hm-[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;")

#: The RGB triples the browser suite will accept, as written in its approved set.
_APPROVED = re.compile(r'"(\d{1,3},\d{1,3},\d{1,3})"')


def _rgb(hex_value: str) -> str | None:
    digits = hex_value.lstrip("#")
    if len(digits) == 3:
        digits = "".join(character * 2 for character in digits)
    if len(digits) < 6:
        return None
    return ",".join(str(int(digits[index : index + 2], 16)) for index in (0, 2, 4))


def _brand_tokens() -> dict[str, str]:
    tokens: dict[str, str] = {}
    for name, value in _TOKEN.findall(_BRAND_CSS.read_text(encoding="utf-8")):
        rgb = _rgb(value)
        if rgb is not None:
            tokens[name] = rgb
    return tokens


def _approved_colours() -> set[str]:
    return set(_APPROVED.findall(_BROWSER_CONFTEST.read_text(encoding="utf-8")))


def test_both_files_are_where_this_expects_them() -> None:
    """Guards the checks below against passing because a file moved and read empty."""

    assert _BRAND_CSS.is_file(), _BRAND_CSS
    assert _BROWSER_CONFTEST.is_file(), _BROWSER_CONFTEST
    assert len(_brand_tokens()) > 20
    assert len(_approved_colours()) > 20


@pytest.mark.parametrize("token", sorted(_brand_tokens()))
def test_the_approved_palette_holds_every_brand_token(token: str) -> None:
    """A colour the brand file declares can never be "not a brand colour"."""

    colour = _brand_tokens()[token]
    assert colour in _approved_colours(), (
        f"hilalmarkets-brand.css declares {token} as rgb({colour}), but the browser "
        "suite's approved palette does not list it. Any page that paints with this "
        "token fails the brand check for using the brand."
    )
