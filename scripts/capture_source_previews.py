"""Take the screenshots the chat preview cards show.

Every page in ``services/source_previews.SOURCE_PAGES`` gets one picture, saved as
``src/ai_market_monitor/static/source-previews/<key>.jpg`` — 640 x 400 pixels, the page's
first screen at a 1280 x 800 desktop size, drawn at half scale so each file stays small.

Run it against any running copy of the product. Pages that need an account are opened
after signing in with the account given by ``HM_PREVIEW_EMAIL`` and
``HM_PREVIEW_PASSWORD``, so use a demonstration account on a test copy — never a real
customer's account, because whatever that account holds ends up in the pictures.

A coin's page (the Passport) is photographed once and shown for every coin, so the
coin's name, logo, result and dates are covered before the picture is taken. A card
about one coin must never show another coin's details.

    python scripts/capture_source_previews.py --base-url http://localhost:8000
    python scripts/capture_source_previews.py --base-url ... --only market help

Needs Playwright and Chromium, which the production image already contains.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import Page, sync_playwright  # noqa: E402

from ai_market_monitor.services.source_previews import (  # noqa: E402
    SOURCE_PAGES,
    screenshot_path,
)

#: The desktop size the picture is taken at, and the scale it is drawn at.
VIEWPORT = {"width": 1280, "height": 800}
SCALE = 0.5
QUALITY = 74

#: Things that sit on top of a page and are not the page: the assistants' buttons, the
#: back-to-top button, the cookie banner once it has been answered.
HIDE = """
.public-chat-launcher, .hm-to-top, [data-hilal-launcher], .hilal-launcher,
.hm-hilal-launcher, [data-cookie-banner], .cookie-banner, .hm-guide-launcher,
[data-hm-guide-launcher], [data-hm-guide-root], .hm-guide-root, .hilal-orb,
.hm-ask-tag { display: none !important; }
*, *::before, *::after { animation: none !important; transition: none !important; }
"""

#: The parts of a Passport that name its coin or state its result. Covered with the
#: page's own quiet surface colour, so the picture shows the page, not one coin.
PASSPORT_MASK = [
    ".t-passport-identity",
    ".t-pq-answer",
    ".t-facts",
    ".t-passport-hero .t-banner",
    "[data-passport-page] .t-coin-logo",
]


def _settle(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    # Hidden rather than answered: the first-visit guide on a new account sits over the
    # cookie banner and takes any click meant for it, and no choice needs recording for
    # a picture.
    page.add_style_tag(content=HIDE)
    page.wait_for_timeout(1200)


def _sign_in(page: Page, base_url: str, email: str, password: str) -> None:
    page.goto(f"{base_url}/signin", wait_until="networkidle")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", password)
    page.locator("form button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")
    if "/signin" in page.url:
        raise SystemExit("Signing in failed; check HM_PREVIEW_EMAIL and HM_PREVIEW_PASSWORD.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--only", nargs="*", default=None, help="Keys to take; all if omitted.")
    parser.add_argument(
        "--passport-asset", default="BTC", help="The coin whose Passport is photographed."
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    keys = args.only or sorted(SOURCE_PAGES)
    unknown = [key for key in keys if key not in SOURCE_PAGES]
    if unknown:
        raise SystemExit(f"Not in the catalog: {', '.join(unknown)}")

    email = os.environ.get("HM_PREVIEW_EMAIL", "")
    password = os.environ.get("HM_PREVIEW_PASSWORD", "")
    public = [key for key in keys if not SOURCE_PAGES[key].account_only]
    private = [key for key in keys if SOURCE_PAGES[key].account_only]
    if private and not (email and password):
        raise SystemExit(
            "Pages that need an account need HM_PREVIEW_EMAIL and HM_PREVIEW_PASSWORD."
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for group, signed_in in ((public, False), (private, True)):
            if not group:
                continue
            context = browser.new_context(
                viewport=VIEWPORT, device_scale_factor=SCALE, reduced_motion="reduce"
            )
            page = context.new_page()
            if signed_in:
                _sign_in(page, base_url, email, password)
            for key in group:
                source = SOURCE_PAGES[key]
                path = source.path.format(asset=args.passport_asset.lower())
                page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
                _settle(page)
                mask = (
                    [page.locator(selector) for selector in PASSPORT_MASK]
                    if source.per_asset
                    else []
                )
                target = screenshot_path(key)
                target.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(target),
                    type="jpeg",
                    quality=QUALITY,
                    mask=mask,
                    mask_color="#fafbfc",
                )
                print(f"{key:<18} {path:<32} {target.stat().st_size // 1024} KB")
            context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
