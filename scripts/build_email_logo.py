"""Make the white Hilal Markets logo that every email header shows.

A mail client cannot draw an SVG. Gmail, Outlook and Yahoo all drop it, so the one
picture in an email has to be a PNG. But a PNG drawn by hand is a second copy of the
logo, and a second copy is how the site and the emails end up with different marks.

So this script does not draw anything. It reads ``static/hilal-markets-logo.svg`` — the
same file the website, the dashboard side menu and the sign-in page use — paints it
white, and photographs it with the browser the test suite already installs. Change the
site logo and re-run this, and the emails follow. Nothing is ever redrawn.

    .venv/Scripts/python scripts/build_email_logo.py

It is written at three times the size it is shown at, so it stays sharp on a phone.
"""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import sync_playwright

REPOSITORY = Path(__file__).resolve().parents[1]
STATIC = REPOSITORY / "src" / "ai_market_monitor" / "static"
SOURCE = STATIC / "hilal-markets-logo.svg"
TARGET = STATIC / "email" / "hilal-markets-logo-white.png"

#: What the header shows it at, in CSS pixels, and how many real pixels per one of them.
DISPLAY_WIDTH = 178
DISPLAY_HEIGHT = 28
SCALE = 3


def white_logo_markup() -> str:
    """The site logo with every fill turned white, and nothing else changed."""

    markup = SOURCE.read_text(encoding="utf-8")
    markup = re.sub(r'fill="#2B2E35"', 'fill="#ffffff"', markup, flags=re.IGNORECASE)
    if 'fill="#ffffff"' not in markup:
        raise SystemExit(
            f"{SOURCE.name} no longer paints its letters #2B2E35. "
            "Check what colour it uses now before trusting this picture."
        )
    return markup


def main() -> int:
    markup = white_logo_markup()
    page_html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:transparent}"
        f"svg{{display:block;width:{DISPLAY_WIDTH}px;height:{DISPLAY_HEIGHT}px}}</style>"
        f"</head><body>{markup}</body></html>"
    )
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": DISPLAY_WIDTH, "height": DISPLAY_HEIGHT},
            device_scale_factor=SCALE,
        )
        page.set_content(page_html)
        page.locator("svg").screenshot(path=str(TARGET), omit_background=True)
        browser.close()
    print(
        f"{TARGET.relative_to(REPOSITORY)} written at "
        f"{DISPLAY_WIDTH * SCALE}x{DISPLAY_HEIGHT * SCALE} "
        f"({TARGET.stat().st_size} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
