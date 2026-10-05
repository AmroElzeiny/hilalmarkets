"""The pages an assistant answer may point to, drawn as preview cards.

When an assistant answers from one of this product's pages, the answer ends with a card
for that page: its name, a picture of it and one line saying what it holds. Pressing
the card opens the page in a new tab.

**One catalog, every chat.** The public assistant and Hilal both build their cards
here, from the same entries, so a page has one name, one description and one picture
wherever it is cited. A chat cannot cite a page that is not in :data:`SOURCE_PAGES`:
an address the model made up has no entry, and so no card.

**Only real sources.** A card is shown for the record an answer was actually built
from — a knowledge document it cited, a tool result it read, an evidence row it named
in ``grounded_in``. It is never shown for a page the model merely thought related.

**The pictures are taken, not drawn.** ``scripts/capture_source_previews.py`` opens each
page in a real browser and saves a screenshot to ``static/source-previews/<key>.jpg``.
A page shown for one coin (a Passport) is photographed once, with the coin's name,
price and status covered, so a card about one coin never shows another coin's details.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from ai_market_monitor.core.app_links import app_link, site_link
from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.dashboard_paths import (
    MARKET_PATH,
    MONITORS_PATH,
    PASSPORTS_PATH,
    PRICING_PATH,
    SUBSCRIPTION_PATH,
)
from ai_market_monitor.core.site_content import (
    DASHBOARD_NAVIGATION,
    PUBLIC_PAGE_BY_PAGE,
    SITE_DESCRIPTION,
    SITE_NAME,
)
from ai_market_monitor.schemas.source_preview import SourcePreview

STATIC_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "static"
#: Where the screenshots live, under ``static/``.
PREVIEW_DIR: Final[str] = "source-previews"


@dataclass(frozen=True, slots=True)
class SourcePage:
    """One page a chat may cite."""

    key: str
    #: The page's name as a person reads it. ``{asset}`` is the coin, for a coin's page.
    title: str
    #: One plain sentence about what the page holds.
    description: str
    #: The address. ``{asset}`` is the coin's symbol in lower case.
    path: str
    #: Needs an account. Built on the product's own hostname when it has one.
    account_only: bool = False

    @property
    def per_asset(self) -> bool:
        return "{asset}" in self.path


def _public(key: str, page: str | None = None) -> SourcePage:
    """A public page, named and described by its own page metadata — never a second copy."""

    metadata = PUBLIC_PAGE_BY_PAGE[page or key]
    return SourcePage(key, metadata.title, metadata.description, metadata.path)


#: Every page a chat may cite, by key.
#:
#: The public-page keys are the public assistant's route ids, so the route a knowledge
#: document belongs to is the card it produces. How We Screen is deliberately absent:
#: it is served but linked from nowhere, and a card would be a link.
SOURCE_PAGES: Final[dict[str, SourcePage]] = {
    "home": SourcePage("home", SITE_NAME, SITE_DESCRIPTION, "/"),
    **{
        key: _public(key)
        for key in (
            "features",
            "how_it_works",
            "market",
            "hilal_methodology",
            "help",
            "contact",
            "about",
            "trust_safety",
            "risk_disclosure",
            "privacy",
            "terms",
            "cookies",
        )
    },
    # The Pricing section of the home page. There is no Pricing page any more: it went
    # stale and was taken down on 4 October 2026, and its old address only forwards here.
    "pricing": SourcePage(
        "pricing",
        "Pricing",
        "Every Hilal Markets plan, what it includes, and what it costs today.",
        PRICING_PATH,
    ),
    "dashboard_entry": SourcePage(
        "dashboard_entry",
        "Your Hilal Markets dashboard",
        "Where your monitors, alerts and followed coins live once you have an account.",
        # The same address the assistant's own "Dashboard" link uses: it opens the
        # dashboard for a signed-in visitor and sign-up for everyone else, where the
        # dashboard's own address would only bounce a visitor without an account.
        "/dashboard-entry",
        account_only=True,
    ),
    "halal_assets": SourcePage(
        "halal_assets",
        "Halal Assets",
        "Every coin that passed a published Shariah standard, with live prices and the "
        "review behind each one.",
        MARKET_PATH,
        account_only=True,
    ),
    "passport": SourcePage(
        "passport",
        "{asset} Evidence Passport",
        "The full review of {asset}: the standard used, the reasons, the sources and the "
        "date it was reviewed.",
        # Public, on the website: `/passports/<coin>`. It used to sit in the dashboard
        # and need an account.
        f"{PASSPORTS_PATH}/{{asset}}",
    ),
    "monitors": SourcePage(
        "monitors",
        "Your monitors",
        "The market conditions you asked Hilal Markets to watch, and what each one found.",
        MONITORS_PATH,
        account_only=True,
    ),
    "subscription": SourcePage(
        "subscription",
        "Plan and billing",
        "Your plan, what it includes, and how you pay for it.",
        SUBSCRIPTION_PATH,
        account_only=True,
    ),
}

# Every other page in the dashboard's side menu, named and described by the menu itself
# — the same entry the sidebar draws — so "where is Support" can end with a card that
# opens Support. A menu page already in the catalog above keeps its entry there.
SOURCE_PAGES.update(
    {
        item.page: SourcePage(item.page, item.label, item.about, item.path, account_only=True)
        for group in DASHBOARD_NAVIGATION
        for item in group.items
        if item.path not in {page.path for page in SOURCE_PAGES.values()}
    }
)


def source_key_for_path(path: str) -> str | None:
    """The catalog key of the page at this address, or ``None`` when it has no card."""

    return next((key for key, page in SOURCE_PAGES.items() if page.path == path), None)


def screenshot_path(key: str) -> Path:
    return STATIC_DIR / PREVIEW_DIR / f"{key}.jpg"


@cache
def _image_version(key: str) -> str | None:
    """A short fingerprint of the screenshot, so a new picture is never served stale."""

    path = screenshot_path(key)
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def source_preview(
    key: str,
    settings: Settings,
    *,
    asset: str | None = None,
) -> SourcePreview | None:
    """The card for one catalog page, or ``None`` when the key is not in the catalog.

    A per-coin page needs its coin; without one there is no address to open, so there is
    no card. The coin is written the way the listings write it — it came from a record,
    not from the model.
    """

    page = SOURCE_PAGES.get(key)
    if page is None:
        return None
    symbol = (asset or "").strip().upper()
    if page.per_asset and not symbol:
        return None
    path = page.path.format(asset=symbol.lower())
    # A public page goes on the website's own hostname. Hilal answers inside the
    # dashboard, on the product's hostname, where `/` is the dashboard itself — so a plain
    # path there opened the dashboard instead of the home page, and a public page such as
    # a Passport stayed inside the dashboard instead of on the website.
    url = app_link(settings, path) if page.account_only else site_link(settings, path)
    version = _image_version(key)
    image_url = f"/static/{PREVIEW_DIR}/{key}.jpg?v={version}" if version else None
    use_app_host = page.account_only and settings.app_base_url is not None
    base = str(settings.app_base_url if use_app_host else settings.public_base_url)
    parts = urlsplit(url if "//" in url else f"{base.rstrip('/')}{url}")
    address = f"{parts.netloc}{parts.path}".removeprefix("www.")
    address = address.rstrip("/") or address
    if parts.fragment:
        address = f"{address}/#{parts.fragment}"
    return SourcePreview(
        key=key,
        title=page.title.format(asset=symbol),
        description=page.description.format(asset=symbol),
        url=url,
        image_url=image_url,
        address=address,
    )


def source_previews(
    wanted: list[tuple[str, str | None]],
    settings: Settings,
    *,
    limit: int = 3,
) -> list[SourcePreview]:
    """Cards for several sources, in order, without repeats, at most ``limit``.

    Three at most: a card is a large thing to put under a short answer, and an answer
    that rests on more than three pages is better served by the first three than by a
    wall of pictures.
    """

    cards: list[SourcePreview] = []
    seen: set[str] = set()
    for key, asset in wanted:
        card = source_preview(key, settings, asset=asset)
        if card is None or card.url in seen:
            continue
        seen.add(card.url)
        cards.append(card)
        if len(cards) >= limit:
            break
    return cards
