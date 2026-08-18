"""Where a coin's picture comes from. One resolver, one address, one owner.

Two separate duplications lived here, and each one hid a different missing logo.

**The address.** The catalogue address was written out seven times — four in Python,
three more in templates and browser code — each with the version pin `@4.0.53` typed in
by hand. Raising the version meant finding all seven, and missing one would leave a page
quietly asking for a version that may not exist.

**The sources.** Worse, and the reason coins such as Mubarak showed three letters
instead of a logo: *which pictures exist for this coin* was answered independently in
eight places, and each one knew a different subset.

    lifecycle_dashboard      stored picture + catalogue
    sharia_passports         stored picture + catalogue
    dashboard (home)         stored picture + catalogue
    sharia_screening         stored picture only
    live_market_quotes       catalogue only
    dashboard_test (radar)   catalogue only, and `logo_url` hard-coded to None

A coin the catalogue has never heard of — every small or new token — has exactly one
picture: the one the platform stored on its own asset record when the identity was
verified. The three readers that never looked at that record could therefore never show
it. The same coin had a logo on one page and a monogram on the next, which is why this
reads as "some coins have no logo" rather than as a bug in a named place.

So the question is answered once, here, and every reader asks it. `asset_logo()` returns
*every* source in the order they should be tried, and `static/asset-logos.js` walks that
order in the browser, falling through to the next one whenever a picture genuinely fails
to load.

`tests/unit/test_asset_logo_catalogue.py` fails if a Python file writes the address out
again, if a front-end copy names a different version, or if any source is dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: The icon catalogue this product draws coin logos from, pinned to one version.
LOGO_CATALOG = "https://cdn.jsdelivr.net/npm/@web3icons/core@4.0.53/dist/svgs/tokens/branded"

#: A ticker we are willing to put inside a URL.
#:
#: Not a guess about which coins exist — it is the boundary between a symbol and a
#: request. Anything outside it gets no catalogue address at all rather than a URL built
#: from unchecked text.
_SAFE_TICKER = re.compile(r"^[A-Z0-9]{1,24}$")

#: How many letters the fallback monogram shows.
#:
#: Three fits the round container at every size the product uses it at. Four overflows,
#: and two reads as an abbreviation of nothing.
_MONOGRAM_LETTERS = 3


@dataclass(frozen=True, slots=True)
class AssetLogo:
    """Every picture that exists for one coin, in the order to try them.

    ``monogram`` is not a failure state. It is the designed appearance of a coin with no
    picture anywhere, and it is always filled in, so a card never has nothing to draw.
    """

    ticker: str
    monogram: str
    #: The picture stored on the asset's own record when its identity was verified.
    #: Present for far more coins than the catalogue covers, and the only source for a
    #: small or newly listed token.
    image_url: str | None
    #: The shared icon catalogue, addressed by ticker. Covers the well-known coins.
    module_url: str | None

    @property
    def sources(self) -> tuple[str, ...]:
        """Every address to try, best first. Empty when only the monogram is available."""

        return tuple(url for url in (self.image_url, self.module_url) if url)


def asset_logo_module_url(symbol: str) -> str | None:
    """The catalogue entry for one coin, or ``None`` when the ticker cannot be one.

    ``symbol`` is the coin on its own — "SOL", not "SOL/USDT". The catalogue files are
    named in capitals, so the case is settled here rather than by each caller.
    """

    ticker = _ticker(symbol)
    return f"{LOGO_CATALOG}/{ticker}.svg.js" if ticker else None


def asset_logo(symbol: str, provider_ids: Any = None) -> AssetLogo:
    """Every picture for one coin, from its ticker and its stored record.

    ``provider_ids`` is the asset record's own ``provider_ids`` mapping — pass it
    whenever the caller has the record, and the picture stored there is preferred over
    the catalogue. A caller that has no record passes nothing and still gets the
    catalogue address and the monogram; what it must never do is pass ``None`` for the
    stored picture *while holding a record that has one*, which is how the Opportunities
    page ended up drawing letters for coins the platform had a logo for.
    """

    ticker = _ticker(symbol)
    return AssetLogo(
        ticker=ticker,
        monogram=(ticker or str(symbol or "").upper())[:_MONOGRAM_LETTERS] or "?",
        image_url=stored_logo_url(provider_ids),
        module_url=f"{LOGO_CATALOG}/{ticker}.svg.js" if ticker else None,
    )


def stored_logo_url(provider_ids: Any) -> str | None:
    """The picture recorded on an asset, if there is a usable one.

    Only ``https`` is accepted. A stored value that is blank, relative, or plain
    ``http`` is treated as absent rather than written into the page: a mixed-content
    image is blocked by the browser and shows as a broken picture, which looks worse
    than the monogram it replaced.
    """

    if not isinstance(provider_ids, dict):
        return None
    url = str(provider_ids.get("logo_url") or "").strip()
    return url if url.lower().startswith("https://") else None


def _ticker(symbol: Any) -> str:
    normalized = str(symbol or "").strip().upper()
    return normalized if _SAFE_TICKER.fullmatch(normalized) else ""
