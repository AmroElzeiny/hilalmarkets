"""Go and look: read the project's own site, and ask a search engine.

``sharia_source_catalog`` knows what an official source *is*. It cannot know that a
project moved its blog to a Telegram channel in 2025, because nothing in the identity a
reviewer approved says so. Two ways of finding that out live here, and only the finding
lives here — every judgement about what was found is made back in the catalog.

**Reading the project's own site.** A project that runs a Telegram announcement channel
or an X account almost always links to it from its own homepage, usually in the footer.
That link is the project saying "this is ours", which is a far stronger claim than any
search result, and it costs one request to a site the product already fetches.

**Asking a search engine.** For the rest. The query is the plain question a person would
type — ``"Solana" SOL crypto official news`` — and the answers are handed to
``search_candidates``, which throws away everything that is not provably the project's
own. That filtering is the important half: a search for a coin's news returns market-data
sites, exchange listings and press coverage long before it returns the project, and none
of those is an official source however true the article is.

Search is **off until it is configured**. With no key the searcher returns nothing, the
layer offers no candidates, and the rest of the system behaves exactly as it did before.
Nothing here ever raises into the sweep: a search engine having a bad day must not stop
the product proving the links it already has.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.provider_reliability import ProviderCallError
from ai_market_monitor.services.provider_runtime import provider_request
from ai_market_monitor.services.sharia_research import (
    FetchTarget,
    OfficialEvidenceFetcher,
    ShariaResearchError,
    extract_links,
)
from ai_market_monitor.services.sharia_source_catalog import (
    SearchResult,
    is_official_url,
    search_queries,
)

logger = logging.getLogger(__name__)

#: Google's own endpoint for a Programmable Search Engine. It is the documented way to
#: ask Google a question from a program; scraping the results page is not.
GOOGLE_SEARCH_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
#: Brave's search API, kept as an alternative because it needs one key rather than a
#: key plus a search-engine id, which is one less thing to get wrong.
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

#: How many links one homepage may contribute. A footer holds a dozen; a page with
#: hundreds is a directory, and reading all of it would cost far more than it is worth.
MAXIMUM_HARVESTED_LINKS = 400


class WebSourceDiscovery:
    """Finds addresses. Judges nothing."""

    def __init__(
        self,
        settings: Settings,
        *,
        fetcher: OfficialEvidenceFetcher | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher or OfficialEvidenceFetcher(settings, transport=transport)
        self.transport = transport
        self._links: dict[str, tuple[str, ...]] = {}
        self._searches: dict[str, tuple[SearchResult, ...]] = {}

    # -- the project's own site ------------------------------------------------

    async def channel_links(self, official_website: str | None) -> tuple[str, ...]:
        """Every address the approved official website points at.

        Answers with nothing rather than raising. A homepage that is down means this
        layer has no opinion this sweep, which is different from the homepage being
        wrong, and the resolver already knows how to carry on to the next layer.
        """

        site = (official_website or "").strip()
        if not site or not is_official_url(site):
            return ()
        cached = self._links.get(site)
        if cached is not None:
            return cached
        try:
            body, _headers, _status = await self.fetcher.fetch(FetchTarget(site))
        except ShariaResearchError as exc:
            logger.info("Could not read %s for its own links: %s", site, exc.code)
            self._links[site] = ()
            return ()
        except (httpx.HTTPError, ProviderCallError):
            logger.info("Could not reach %s for its own links", site)
            self._links[site] = ()
            return ()
        found = extract_links(body, site)[:MAXIMUM_HARVESTED_LINKS]
        self._links[site] = found
        return found

    # -- the open web ----------------------------------------------------------

    @property
    def search_configured(self) -> bool:
        """Whether a search engine is actually reachable with what is configured."""

        if not self.settings.sharia_source_search_enabled:
            return False
        provider = self.settings.sharia_source_search_provider
        if provider == "google":
            return (
                self.settings.google_search_api_key is not None
                and bool((self.settings.google_search_engine_id or "").strip())
            )
        if provider == "brave":
            return self.settings.brave_search_api_key is not None
        return False

    def search_requirement(self) -> str:
        """What is missing, said plainly, for a person reading a report."""

        if not self.settings.sharia_source_search_enabled:
            return "Web search is switched off (SHARIA_SOURCE_SEARCH_ENABLED=false)."
        provider = self.settings.sharia_source_search_provider
        if provider == "google":
            return (
                "Web search needs GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID. "
                "Make a Programmable Search Engine at programmablesearchengine.google.com, "
                "set it to search the whole web, and take its ID."
            )
        if provider == "brave":
            return "Web search needs BRAVE_SEARCH_API_KEY."
        return (
            "No web search provider is chosen. Set SHARIA_SOURCE_SEARCH_PROVIDER to "
            "google or brave."
        )

    async def search(self, *, asset_name: str, symbol: str) -> tuple[SearchResult, ...]:
        """Ask the configured engine every question the catalog wants asked."""

        if not self.search_configured:
            return ()
        key = f"{asset_name}|{symbol}".casefold()
        cached = self._searches.get(key)
        if cached is not None:
            return cached
        collected: list[SearchResult] = []
        seen: set[str] = set()
        for query in search_queries(asset_name=asset_name, symbol=symbol):
            for result in await self._one_query(query):
                if result.url in seen:
                    continue
                seen.add(result.url)
                collected.append(result)
        answer = tuple(collected)
        self._searches[key] = answer
        return answer

    async def _one_query(self, query: str) -> tuple[SearchResult, ...]:
        provider = self.settings.sharia_source_search_provider
        try:
            if provider == "google":
                return await self._google(query)
            if provider == "brave":
                return await self._brave(query)
        except (httpx.HTTPError, ProviderCallError, ValueError) as exc:
            # A search engine that will not answer is not a reason to stop proving the
            # links the product already has.
            logger.info("Web search for %r failed: %s", query, type(exc).__name__)
        return ()

    async def _google(self, query: str) -> tuple[SearchResult, ...]:
        api_key = self.settings.google_search_api_key
        engine_id = (self.settings.google_search_engine_id or "").strip()
        if api_key is None or not engine_id:
            return ()
        response = await provider_request(
            self.settings,
            "GET",
            GOOGLE_SEARCH_ENDPOINT,
            provider="google_search",
            operation="official_source_search",
            timeout=30,
            mutation_committed=False,
            transport=self.transport,
            follow_redirects=True,
            headers={"Accept": "application/json"},
            params={
                "key": api_key.get_secret_value(),
                "cx": engine_id,
                "q": query,
                "num": str(self.settings.sharia_source_search_results),
                "safe": "active",
            },
        )
        if response.status_code >= 400:
            logger.info("Google search answered HTTP %s", response.status_code)
            return ()
        payload = response.json()
        rows = payload.get("items") if isinstance(payload, dict) else None
        return _rows_to_results(rows, url_key="link", title_key="title")

    async def _brave(self, query: str) -> tuple[SearchResult, ...]:
        api_key = self.settings.brave_search_api_key
        if api_key is None:
            return ()
        response = await provider_request(
            self.settings,
            "GET",
            BRAVE_SEARCH_ENDPOINT,
            provider="brave_search",
            operation="official_source_search",
            timeout=30,
            mutation_committed=False,
            transport=self.transport,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key.get_secret_value(),
            },
            params={
                "q": query,
                "count": str(self.settings.sharia_source_search_results),
            },
        )
        if response.status_code >= 400:
            logger.info("Brave search answered HTTP %s", response.status_code)
            return ()
        payload = response.json()
        web = payload.get("web") if isinstance(payload, dict) else None
        rows = web.get("results") if isinstance(web, dict) else None
        return _rows_to_results(rows, url_key="url", title_key="title")


def _rows_to_results(rows: Any, *, url_key: str, title_key: str) -> tuple[SearchResult, ...]:
    """Turn one engine's answer into the shape the catalog judges.

    Every engine is read through this, so an engine that changes its field names breaks
    in one place rather than producing a silently empty result set somewhere downstream.
    """

    if not isinstance(rows, list):
        return ()
    produced: list[SearchResult] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get(url_key) or "").strip()
        if not url or not urlsplit(url).netloc:
            continue
        produced.append(SearchResult(url=url, title=str(raw.get(title_key) or "").strip()[:300]))
    return tuple(produced)
