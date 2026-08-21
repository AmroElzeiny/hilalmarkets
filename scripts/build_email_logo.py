"""Make every Hilal Markets logo picture that an email shows.

A mail client cannot draw an SVG. Gmail, Outlook and Yahoo all drop it, so a picture in
an email has to be a PNG. But a PNG drawn by hand is a second copy of the logo, and a
second copy is how the site and the emails end up with different marks.

So this script does not draw anything. It reads ``static/hilal-markets-logo.svg`` — the
same file the website, the dashboard side menu and the sign-in page use — and
photographs it with the browser the test suite already installs. Change the site logo
and re-run this, and the emails follow. Nothing is ever redrawn.

Two pictures come out, the same shape at the same size, differing only in colour:

============================================  =============================  ==========
Picture                                       Where it is shown              Colour
============================================  =============================  ==========
``email/hilal-markets-logo-white.png``        the header of every sent email  white
``email/hilal-markets-logo-dark.png``         the signature a person pastes   near-black
============================================  =============================  ==========

They are built by one function on purpose. The signature used to carry a picture of its
own — a rounded square with the symbol knocked out of it — which is exactly the drift
this file exists to prevent.

    .venv/Scripts/python scripts/build_email_logo.py

Each is written at three times the size it is shown at, so it stays sharp on a phone.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from ai_market_monitor.services.email_branding import (  # noqa: E402
    EMAIL_LOGO_HEIGHT,
    EMAIL_LOGO_PATH,
    EMAIL_LOGO_WIDTH,
    INK,
    SIGNATURE_LOGO_PATH,
    SURFACE,
)

STATIC = REPOSITORY / "src" / "ai_market_monitor" / "static"
SOURCE = STATIC / "hilal-markets-logo.svg"

#: The colour the site logo paints its own letters. Everything below is that colour
#: swapped for another one; nothing else about the file is touched.
SOURCE_FILL = "#2B2E35"

#: How many real pixels per CSS pixel. Three keeps it sharp on every phone screen.
SCALE = 3


@dataclass(frozen=True, slots=True)
class Picture:
    """One PNG: where it goes, and what colour the logo is painted in it."""

    path: str
    fill: str
    what_shows_it: str

    @property
    def target(self) -> Path:
        return STATIC / self.path.removeprefix("/static/")


#: Both sizes come from `email_branding`, so the picture and the `width`/`height` the
#: markup declares can never say two different things.
PICTURES = (
    Picture(EMAIL_LOGO_PATH, SURFACE, "the header of every email the product sends"),
    Picture(SIGNATURE_LOGO_PATH, INK, "the signature a person pastes into their mail"),
)


def recoloured(fill: str) -> str:
    """The site logo with every fill swapped for `fill`, and nothing else changed."""

    markup = SOURCE.read_text(encoding="utf-8")
    swapped, changes = re.subn(
        re.escape(SOURCE_FILL), fill, markup, flags=re.IGNORECASE
    )
    if not changes:
        raise SystemExit(
            f"{SOURCE.name} no longer paints its letters {SOURCE_FILL}. "
            "Check what colour it uses now before trusting these pictures."
        )
    return swapped


def main() -> int:
    pages = [
        (
            picture,
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<style>html,body{margin:0;padding:0;background:transparent}"
            f"svg{{display:block;width:{EMAIL_LOGO_WIDTH}px;"
            f"height:{EMAIL_LOGO_HEIGHT}px}}</style>"
            f"</head><body>{recoloured(picture.fill)}</body></html>",
        )
        for picture in PICTURES
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": EMAIL_LOGO_WIDTH, "height": EMAIL_LOGO_HEIGHT},
            device_scale_factor=SCALE,
        )
        for picture, markup in pages:
            picture.target.parent.mkdir(parents=True, exist_ok=True)
            page.set_content(markup)
            page.locator("svg").screenshot(
                path=str(picture.target), omit_background=True
            )
            print(
                f"{picture.target.relative_to(REPOSITORY)} written at "
                f"{EMAIL_LOGO_WIDTH * SCALE}x{EMAIL_LOGO_HEIGHT * SCALE} "
                f"({picture.target.stat().st_size} bytes) — {picture.what_shows_it}"
            )
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
