"""Gather everything a project says about itself, into one folder of readable text.

Knowing a project's website is not the same as knowing what the project does. The
address is where the reading starts; the answer is spread across the pages behind it —
the front page says what it is, the documentation says how it works, the tokenomics page
says where a holder's money comes from, and the blog says what changed last month.

So this walks the project's own site and collects those pages as plain text. The result
is an :class:`EvidenceFolder`: a set of documents, each one with the address it came
from, the kind of page it is, and when it was read. Nothing here judges anything. It
fetches and it files, and it is the *input* to a decision made elsewhere.

**It reuses the product's one fetcher and its one document reader.** Robots policy,
timeouts, PDF detection, HTML-to-text and link extraction all already exist in
``sharia_research`` and are imported, not rewritten. A second crawler with its own idea
of robots.txt is exactly the duplicate this codebase keeps paying for.

**Three rules that keep it honest and cheap.**

*The project's own voice only.* A page is collected when it lives on the project's own
site, decided by :func:`sharia_source_catalog.is_same_project_site` — the same rule the
source resolver uses. A whitepaper hosted elsewhere is collected only because the
provider named it explicitly, never because a page linked to it. Anyone can link to
anything; what a project publishes under its own name is a different kind of statement,
and the difference matters when the text is about to be used to refuse a coin.

*Primary pages before commentary.* The front page, the documentation and the whitepaper
are what the project asserts. A blog post is a thing it said once. Both are collected,
both are kept apart, and a refusal is only ever allowed to rest on the first kind.

*Bounded.* A budget of pages per coin, a budget per host, one pass of link-following.
A crawler with no ceiling meets a documentation site with ten thousand pages and stops
being a research tool.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.sharia_page_render import BrowserPageRenderer
from ai_market_monitor.services.sharia_research import (
    FetchTarget,
    OfficialEvidenceFetcher,
    ShariaResearchError,
    extract_document,
    extract_links,
)
from ai_market_monitor.services.sharia_source_catalog import (
    DOCUMENTATION,
    NEWS,
    PROVIDER_FIELD_CATEGORY,
    WEBSITE,
    is_aggregator_url,
    is_same_project_site,
    page_category,
)

logger = logging.getLogger(__name__)

#: Page kinds a project asserts about itself. A refusal may only rest on these.
#:
#: The distinction is not stylistic. "This protocol lends money" on a documentation page
#: is the project describing its own product; the same words in a news post may be
#: describing somebody else's. Letting commentary block a coin is how a blockchain that
#: once wrote about the lending market gets refused for running one.
PRIMARY_CATEGORIES: frozenset[str] = frozenset({WEBSITE, DOCUMENTATION})

#: How many pages are worth reading for one coin.
#:
#: **Eighty, and this number is the definition of the screen's reach.** A rule that
#: cannot be settled from eighty of a project's own pages is not a rule this screen
#: attempts — see :func:`sharia_conditions.unverifiable_approved_conditions` and the
#: methodology note. Fixing the boundary here is what lets the product say plainly what
#: it does and does not look at, instead of returning a queue of questions nobody can
#: answer.
#:
#: It was twelve until 31 August 2026, sized to "what a person would read". That was the
#: wrong measure: a person reads twelve pages because they are slow, not because the
#: thirteenth is worthless. The costs that actually bind are the per-host cap below and
#: the browser renderer's memory, and neither scales with this number alone.
DEFAULT_PAGE_BUDGET = 80

#: Pages from any one host. Stops a documentation site consuming the whole budget and
#: leaving no room for the whitepaper.
#:
#: Raised with the page budget, and deliberately by less than proportionally: the point
#: of the wider budget is to read *more of the project*, not more of whichever host
#: happens to have the deepest link tree.
DEFAULT_PER_HOST_BUDGET = 30

#: The shortest run of text worth filing. Below this a page is a redirect stub, a cookie
#: notice or a JavaScript shell, and filing it would make an empty folder look full —
#: which matters here more than usual, because "how many documents were read" is what
#: decides whether a coin can be judged at all.
MINIMUM_DOCUMENT_CHARACTERS = 400

#: Addresses that are never worth fetching, whatever links to them.
_UNREADABLE_SUFFIXES: tuple[str, ...] = (
    ".zip", ".tar", ".gz", ".exe", ".dmg", ".apk", ".mp4", ".mp3", ".wav",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".woff", ".woff2", ".ttf", ".xml", ".rss", ".atom",
)

#: Path words that lead away from what a project says about itself.
_UNINFORMATIVE_WORDS: frozenset[str] = frozenset(
    {
        "careers", "jobs", "privacy", "terms", "legal", "cookie", "cookies",
        "login", "signin", "signup", "register", "account", "cart", "checkout",
        "brand", "press-kit", "media-kit", "logo", "logos", "assets",
        "unsubscribe", "sitemap", "search", "tag", "tags", "author", "authors",
    }
)


#: Pages on a project's own site that are about **other people's** projects.
#:
#: A chain's ecosystem directory, its partner list and its case studies all live under
#: its own domain and are all written by it — and none of them describes the chain. They
#: describe what was built on top of it, which is the opposite claim. Reading them as
#: self-description refused Cardano for an interview with a lending protocol and
#: Avalanche for a fund manager's money market fund.
ECOSYSTEM_PATH_WORDS: frozenset[str] = frozenset(
    {
        "ecosystem", "ecosystems", "partners", "partnership", "partnerships",
        "case-studies", "casestudies", "customers", "showcase", "spotlight",
        "projects", "dapps", "apps", "directory", "grants", "grantees",
        "portfolio", "interviews", "stories", "testimonials", "integrations",
        "marketplace", "explore",
        # A directory of software other people wrote is the ecosystem page in another
        # costume. `ethereum.org/developers/tools` lists hundreds of third-party
        # projects a line at a time — "Seamless Protocol is the largest native lending
        # and borrowing DeFi platform on Base" — and read as Ethereum describing itself
        # it helped refuse Ethereum for running a lending business on 31 August 2026.
        "tools", "libraries", "sdks", "plugins", "extensions", "wallets",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    """One page, read and filed. Text only — no judgement of any kind."""

    url: str
    category: str
    title: str
    text: str
    fetched_at: datetime
    #: Named by the market-data provider or a reviewer, rather than found by following a
    #: link. A seeded address is the project's, on the provider's authority.
    seeded: bool = False

    @property
    def is_primary(self) -> bool:
        """Does this page carry the project's own description of itself?

        Two conditions, and the second is the one that was missing. Being on the
        project's own site is not enough: an ecosystem directory is written by the
        project and is entirely about somebody else.
        """

        if self.category not in PRIMARY_CATEGORIES:
            return False
        words = {part.casefold() for part in urlsplit(self.url).path.split("/") if part}
        return not (words & ECOSYSTEM_PATH_WORDS)

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "category": self.category,
            "title": self.title,
            "characters": len(self.text),
            "fetched_at": self.fetched_at.isoformat(),
            "seeded": self.seeded,
            "primary": self.is_primary,
        }


@dataclass(slots=True)
class EvidenceFolder:
    """Everything gathered for one coin, plus what could not be gathered."""

    symbol: str
    documents: list[EvidenceDocument] = field(default_factory=list)
    #: Address -> why it could not be read. Kept because "the site refuses robots" and
    #: "the site is gone" are different jobs for whoever looks next.
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def primary_documents(self) -> list[EvidenceDocument]:
        return [item for item in self.documents if item.is_primary]

    @property
    def total_characters(self) -> int:
        return sum(len(item.text) for item in self.documents)

    @property
    def is_empty(self) -> bool:
        """Nothing was read at all.

        This is the only condition that means "we cannot judge this coin". A folder with
        one document in it is a folder that can be reasoned about, however thin.
        """

        return not self.documents

    def text_for(self, categories: Iterable[str] | None = None) -> str:
        wanted = set(categories) if categories is not None else None
        return "\n\n".join(
            item.text
            for item in self.documents
            if wanted is None or item.category in wanted
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "documents": [item.as_dict() for item in self.documents],
            "document_count": len(self.documents),
            "primary_count": len(self.primary_documents),
            "characters": self.total_characters,
            "failures": dict(self.failures),
        }


def _normalise(url: str) -> str:
    """One spelling per page, so the same page is never fetched twice.

    Drops the fragment and any trailing slash: ``/docs``, ``/docs/`` and ``/docs#intro``
    are one page, and fetching each of them separately would spend a third of the budget
    reading the same words three times.

    **And drops a leading ``www.``**, which cost more than a wasted fetch. A blind run on
    31 August 2026 refused Ethereum for running a lending business, and the third page
    that carried it over the corroboration bar was ``www.ethereum.org/developers/tools``
    — the same page as ``ethereum.org/developers/tools``, already counted. Two spellings
    of one page do not corroborate each other; they are one page saying something once.
    """

    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    host = parts.netloc.casefold()
    host = host.removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, host, path, parts.query, ""))


def _is_readable(url: str) -> bool:
    parts = urlsplit(url)
    lowered = parts.path.casefold()
    if lowered.endswith(_UNREADABLE_SUFFIXES):
        return False
    words = {part.casefold() for part in lowered.split("/") if part}
    return not (words & _UNINFORMATIVE_WORDS)


class CoinEvidenceCrawler:
    """Reads a project's own pages and files them as text."""

    def __init__(
        self,
        settings: Settings,
        *,
        fetcher: OfficialEvidenceFetcher | None = None,
        renderer: BrowserPageRenderer | None = None,
        page_budget: int = DEFAULT_PAGE_BUDGET,
        per_host_budget: int = DEFAULT_PER_HOST_BUDGET,
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher or OfficialEvidenceFetcher(settings)
        self.renderer = renderer or BrowserPageRenderer(settings)
        self.page_budget = max(1, page_budget)
        self.per_host_budget = max(1, per_host_budget)

    async def gather(
        self,
        symbol: str,
        *,
        website: str | None = None,
        seeds: Sequence[str] = (),
        provider_links: Mapping[str, Sequence[str]] | None = None,
    ) -> EvidenceFolder:
        """Collect what one project publishes about itself.

        ``website`` decides what counts as "the project's own site" for everything found
        by following a link. ``seeds`` and ``provider_links`` are addresses somebody
        already vouched for, so they are read even when they sit on another host — a
        whitepaper on IPFS or a docs subdomain is still the project's own document.
        """

        folder = EvidenceFolder(symbol=symbol.upper())
        seeded = self._seed_addresses(website, seeds, provider_links)
        if not seeded:
            return folder

        seen: set[str] = set()
        per_host: dict[str, int] = {}
        # Two passes, and only two. The first reads what somebody vouched for; the second
        # reads what those pages link to. Following links from followed links is how a
        # crawler ends up on the far side of a documentation site with no budget left for
        # the tokenomics page it was sent to find.
        discovered = await self._read(folder, seeded, seen, per_host, seeded=True)
        followed = self._follow(discovered, website=website, seen=seen)
        await self._read(folder, followed, seen, per_host, seeded=False)

        folder.documents.sort(key=lambda item: (not item.is_primary, not item.seeded))
        return folder

    def _seed_addresses(
        self,
        website: str | None,
        seeds: Sequence[str],
        provider_links: Mapping[str, Sequence[str]] | None,
    ) -> list[tuple[str, str | None]]:
        """Each seeded address, paired with the category its source field implies.

        Carrying the field is what stops a guess. Jupiter's announcement address is
        ``jupresear.ch/t/jup-the-genesis-post/478``: no word in that path or host is in
        any list, so guessing from the address called it the project's own website — and
        a forum post then counted as the project describing itself. A comment on that
        forum saying "wen meme coin" was read as the project admitting it has no product.

        The provider said which field the address came from. Using that is not a
        refinement; it is refusing to re-derive something already known.
        """

        raw: list[tuple[str, str | None]] = []
        if website:
            raw.append((website, WEBSITE))
        raw.extend((value, None) for value in seeds)
        # The fields that carry the project's own words. A chat room and a social
        # account are real sources and neither is a paragraph describing what the
        # protocol does, so they stay out.
        #
        # ``source_code`` is in, and that was a correction. It was excluded as
        # navigation furniture, which is true of a large project and exactly wrong for a
        # small one: Templar's whole website is a splash screen, and its repository
        # README is the only prose anybody has written about it. The coins with the
        # least marketing are the coins whose repository matters most, and they were the
        # ones the exclusion silenced.
        for field_name in (
            "website",
            "whitepaper",
            "technical_doc",
            "source_code",
            "announcement",
        ):
            category = PROVIDER_FIELD_CATEGORY.get(field_name)
            for value in (provider_links or {}).get(field_name, ()):
                raw.append((str(value), category))

        ordered: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        for value, category in raw:
            normalised = _normalise(str(value or ""))
            if not normalised or normalised in seen or not _is_readable(normalised):
                continue
            if is_aggregator_url(normalised):
                # A market-data site's page about the project is not the project.
                continue
            seen.add(normalised)
            ordered.append((normalised, category))
        return ordered

    async def _read(
        self,
        folder: EvidenceFolder,
        addresses: Sequence[tuple[str, str | None]],
        seen: set[str],
        per_host: dict[str, int],
        *,
        seeded: bool,
    ) -> list[tuple[str, str | bytes]]:
        """Fetch each address, file what reads, and hand back the raw bodies."""

        bodies: list[tuple[str, str | bytes]] = []
        for url, declared_category in addresses:
            if len(folder.documents) >= self.page_budget:
                break
            if url in seen:
                continue
            seen.add(url)
            host = urlsplit(url).netloc.casefold()
            if per_host.get(host, 0) >= self.per_host_budget:
                continue
            try:
                body, headers, _status = await self.fetcher.fetch(FetchTarget(source_url=url))
                title, _headings, text = extract_document(
                    body, url, content_type=headers.get("content-type")
                )
            except ShariaResearchError as exc:
                folder.failures[url] = exc.code
                text, title, body = "", "", ""
            except Exception as exc:  # noqa: BLE001 - one bad page must not end the crawl
                logger.info(
                    "evidence_page_unreadable", extra={"url": url, "error": type(exc).__name__}
                )
                folder.failures[url] = "unreadable"
                continue

            if len(text) < MINIMUM_DOCUMENT_CHARACTERS:
                # A page that answered but rendered almost nothing is usually a site
                # whose words only exist after JavaScript has run. That is most of the
                # newer projects, and skipping them meant the coins with the least
                # written about them were also the coins we could never read — so they
                # all landed in "not enough data" for a reason that was ours, not theirs.
                rendered = await self._render(url)
                if rendered is None:
                    folder.failures.setdefault(url, "too_short")
                    # Thin, but not useless: a splash page is a poor document and a
                    # perfectly good map. Templar's front page is 208 characters and a
                    # navigation bar, and discarding it discarded the links to the pages
                    # that *do* describe the project — so the coin was filed as
                    # unreadable because of its menu.
                    if body:
                        bodies.append((url, body))
                    continue
                title, text, body = rendered
                folder.failures.pop(url, None)

            per_host[host] = per_host.get(host, 0) + 1
            folder.documents.append(
                EvidenceDocument(
                    url=url,
                    category=declared_category or page_category(url),
                    title=title,
                    text=text,
                    fetched_at=datetime.now(UTC),
                    seeded=seeded,
                )
            )
            bodies.append((url, body))
        return bodies

    async def aclose(self) -> None:
        """Shut down anything the crawl started. Always call this when a sweep ends.

        Only the browser needs it, and only when a page actually required one — but a
        caller cannot know that, so this is unconditional and safe to call twice. A
        sweep that skips it leaves Chromium running: on a 3.9 GB server with no swap
        that is not an untidy process, it is the next out-of-memory kill.
        """

        await self.renderer.aclose()

    async def __aenter__(self) -> CoinEvidenceCrawler:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def _render(self, url: str) -> tuple[str, str, str] | None:
        """Read a page through a real browser, when a plain request found nothing.

        Uses the product's one renderer. A second browser here would mean two answers to
        "can this page be read", and the source resolver already depends on the first.
        """

        if self.renderer is None:
            return None
        try:
            rendered = await self.renderer.render(url)
        except Exception as exc:  # noqa: BLE001 - a renderer failure is an ordinary miss
            logger.info(
                "evidence_render_failed", extra={"url": url, "error": type(exc).__name__}
            )
            return None
        if not rendered.ok:
            return None
        try:
            title, _headings, text = extract_document(
                rendered.html, url, content_type="text/html"
            )
        except ShariaResearchError:
            return None
        if len(text) < MINIMUM_DOCUMENT_CHARACTERS:
            return None
        return title, text, rendered.html

    def _follow(
        self,
        bodies: Sequence[tuple[str, str | bytes]],
        *,
        website: str | None,
        seen: set[str],
    ) -> list[tuple[str, str | None]]:
        """Which links from the pages just read are worth reading next.

        Ordered by what a Shariah reviewer needs: the documentation first, because that
        is where a protocol states what it does; then the project's own announcements.
        A page that is neither is left alone — the front page was already read, and its
        other links are navigation.
        """

        ranked: dict[str, tuple[int, str]] = {}
        for source_url, body in bodies:
            for link in extract_links(body, source_url):
                normalised = _normalise(link)
                if not normalised or normalised in seen or normalised in ranked:
                    continue
                if not _is_readable(normalised):
                    continue
                if not is_same_project_site(normalised, website):
                    continue
                category = page_category(normalised)
                if category == DOCUMENTATION:
                    ranked[normalised] = (0, category)
                elif category == NEWS:
                    ranked[normalised] = (1, category)
        return [
            (url, ranked[url][1])
            for url in sorted(ranked, key=lambda item: (ranked[item][0], len(item)))
        ]


__all__ = [
    "CoinEvidenceCrawler",
    "EvidenceDocument",
    "EvidenceFolder",
    "MINIMUM_DOCUMENT_CHARACTERS",
    "PRIMARY_CATEGORIES",
]
