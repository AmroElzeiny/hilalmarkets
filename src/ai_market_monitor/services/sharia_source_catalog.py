"""One owner for what an official source is, and for where a link may come from.

Before this module the product knew two kinds of official link — the website and the
documentation — and both were written straight into ``official_sources`` marked
``verified`` without anybody ever fetching them. "Verified" meant "somebody typed it",
which is the weakest possible claim to hang evidence on.

Two ideas live here, and only here.

**The vocabulary.** A category is not a free string. ``official_news`` is the page a
project publishes its own announcements on; ``official_community`` is the place its
holders actually talk. Both are things a Sharia reviewer reads to answer "did this
project change what it does". Every caller imports the names from here rather than
re-typing them, because the recurring failure in this codebase is two modules that each
understood a different subset of the same word list.

**The layers.** A link can be known in three ways, and they are not equally trustworthy:

======================  ==================================================  ==========
Layer                   How it knows                                        Confidence
======================  ==================================================  ==========
``CURATED``             Written down here by a person who checked it          0.95/0.80
``IDENTITY``            Derived from the identity a reviewer already          0.75
                        approved: the official site, the docs, the
                        provider profile
``CONVENTION``          Guessed from the official domain — ``/blog``,         0.45
                        ``/news``, ``/community``
======================  ==================================================  ==========

Nothing here touches the network or the database. A candidate produced by this module is
a **proposal**, never a fact. It becomes a usable source only after
``sharia_source_resolution`` has fetched it and proved it is alive, permitted, readable
and — for news — recent. That ordering is what makes a guessed URL safe to propose: a
wrong guess cannot promote itself, it can only fail its proof and fall through to the
next layer, and then to a person.

The confidence numbers are deliberately coarse. They rank candidates and decide when to
give up on the machine and ask a human; they are not a probability of anything, and no
Sharia status is ever read from them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

#: The official website of the project. Registered by identity approval.
WEBSITE = "official_website"
#: The project's own technical documentation. Registered by identity approval.
DOCUMENTATION = "official_documentation"
#: Where the project publishes its own announcements — a blog, a newsroom, a
#: release feed. What a reviewer reads to learn the project changed.
NEWS = "official_news"
#: Where the project's holders and maintainers talk in public — a governance
#: forum, a subreddit, a community site.
COMMUNITY = "official_community"

#: Every category the product knows, and how it is said to a person. No caller
#: builds this mapping again; a category missing from here is not a category.
SOURCE_CATEGORIES: dict[str, str] = {
    WEBSITE: "official website",
    DOCUMENTATION: "official documentation",
    NEWS: "official news",
    COMMUNITY: "official community",
}

#: What every asset is required to be able to show. The website and the
#: documentation say what a project *is*; these two say what it has been *doing*,
#: which is the half a Sharia review has to keep watching after publication.
REQUIRED_CATEGORIES: tuple[str, ...] = (NEWS, COMMUNITY)

#: Ordering inside an asset's source list. Lower is read first.
CATEGORY_PRIORITY: dict[str, int] = {
    WEBSITE: 10,
    DOCUMENTATION: 20,
    NEWS: 30,
    COMMUNITY: 40,
}


class DiscoveryLayer(StrEnum):
    """How a candidate link was arrived at."""

    CURATED = "curated"
    IDENTITY = "identity"
    CONVENTION = "convention"


#: The order the layers are tried in. A layer runs only for the categories the
#: layers before it could not settle.
LAYER_ORDER: tuple[DiscoveryLayer, ...] = (
    DiscoveryLayer.CURATED,
    DiscoveryLayer.IDENTITY,
    DiscoveryLayer.CONVENTION,
)

#: A curated link somebody checked against the project's own site.
CURATED_CONFIDENT = 0.95
#: A curated link for the right project whose exact address is less certain —
#: usually a site that has moved once. Still has to survive its proof.
CURATED_LIKELY = 0.80

LAYER_CONFIDENCE: dict[DiscoveryLayer, float] = {
    DiscoveryLayer.CURATED: CURATED_CONFIDENT,
    DiscoveryLayer.IDENTITY: 0.75,
    DiscoveryLayer.CONVENTION: 0.45,
}

#: Below this a link is not good enough to stand as evidence on its own. It is not
#: thrown away — it sends the asset down to the next layer, and when the layers run
#: out, to a person.
CONFIDENCE_FLOOR = 0.70

#: Proving a link is alive, permitted and readable is worth this much on top of the
#: layer it came from. It is what lets a guessed ``/blog`` become usable at all.
PROOF_BONUS = 0.30

#: A news page whose newest dated item is older than this is stale. The project may
#: be fine; the page has stopped being a way to hear about it.
NEWS_MAXIMUM_AGE_DAYS = 400


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    """A proposed link. Not a source until it has been fetched and proved."""

    category: str
    title: str
    url: str
    layer: DiscoveryLayer
    confidence: float

    @property
    def normalized_url(self) -> str:
        return normalized_url(self.url)


@dataclass(frozen=True, slots=True)
class CuratedSource:
    category: str
    url: str
    confidence: float = CURATED_CONFIDENT


def _sources(
    *,
    news: str | tuple[str, ...] = (),
    community: str | tuple[str, ...] = (),
    confidence: float = CURATED_CONFIDENT,
) -> tuple[CuratedSource, ...]:
    """One coin's curated links. More than one link per category is expected."""

    def _many(value: str | tuple[str, ...]) -> tuple[str, ...]:
        return (value,) if isinstance(value, str) else value

    return tuple(
        CuratedSource(category=category, url=url, confidence=confidence)
        for category, urls in ((NEWS, _many(news)), (COMMUNITY, _many(community)))
        for url in urls
    )


#: Curated news and community links, keyed by the asset symbol as the canonical
#: identity records it.
#:
#: This table is a starting point, not the whole answer, and it is not required to
#: cover every asset — the layers below it exist precisely because it cannot. An
#: entry here is still only a proposal: every URL is fetched and proved before it is
#: allowed to stand as evidence, so an address that has moved since this was written
#: fails its proof and falls through rather than quietly becoming a bad source.
CURATED_SOURCES: dict[str, tuple[CuratedSource, ...]] = {
    # ---- Layer-1 networks -------------------------------------------------
    "BTC": _sources(
        news="https://bitcoincore.org/en/blog/",
        community="https://bitcointalk.org/",
    ),
    "ETH": _sources(
        news="https://blog.ethereum.org/",
        community="https://ethereum-magicians.org/",
    ),
    "LTC": _sources(
        news="https://litecoin.org/news",
        community="https://www.reddit.com/r/litecoin/",
    ),
    "DOGE": _sources(
        news="https://github.com/dogecoin/dogecoin/releases",
        community="https://www.reddit.com/r/dogecoin/",
    ),
    "BCH": _sources(
        news="https://bitcoincashnode.org/en/releases.html",
        community="https://www.reddit.com/r/Bitcoincash/",
        confidence=CURATED_LIKELY,
    ),
    "XLM": _sources(
        news="https://stellar.org/blog",
        community="https://stellar.org/community",
    ),
    "ADA": _sources(
        news="https://cardanofoundation.org/blog",
        community="https://forum.cardano.org/",
    ),
    "SOL": _sources(
        news="https://solana.com/news",
        community="https://forum.solana.com/",
    ),
    "DOT": _sources(
        news="https://polkadot.com/blog",
        community="https://forum.polkadot.network/",
    ),
    "AVAX": _sources(
        news="https://www.avax.network/blog",
        community="https://www.reddit.com/r/Avax/",
        confidence=CURATED_LIKELY,
    ),
    "ATOM": _sources(
        news="https://blog.cosmos.network/",
        community="https://forum.cosmos.network/",
    ),
    "ALGO": _sources(
        news="https://algorand.co/blog",
        community="https://forum.algorand.org/",
    ),
    "XTZ": _sources(
        news="https://tezos.com/blog",
        community="https://forum.tezosagora.org/",
    ),
    "NEAR": _sources(
        news="https://near.org/blog",
        community="https://gov.near.org/",
    ),
    "ICP": _sources(
        news="https://internetcomputer.org/blog",
        community="https://forum.dfinity.org/",
    ),
    "HBAR": _sources(
        news="https://hedera.com/blog",
        community="https://hedera.com/discord",
        confidence=CURATED_LIKELY,
    ),
    "EGLD": _sources(
        news="https://multiversx.com/blog",
        community="https://www.reddit.com/r/elrondnetwork/",
        confidence=CURATED_LIKELY,
    ),
    "VET": _sources(
        news="https://vechain.org/blog/",
        community="https://www.reddit.com/r/Vechain/",
        confidence=CURATED_LIKELY,
    ),
    "FIL": _sources(
        news="https://filecoin.io/blog",
        community="https://github.com/filecoin-project/community",
    ),
    "CKB": _sources(
        news="https://blog.nervos.org/",
        community="https://talk.nervos.org/",
    ),
    "ROSE": _sources(
        news="https://oasisprotocol.org/blog",
        community="https://www.reddit.com/r/oasisnetwork/",
        confidence=CURATED_LIKELY,
    ),
    "SUI": _sources(
        news="https://blog.sui.io/",
        community="https://forums.sui.io/",
    ),
    "APT": _sources(
        news="https://aptosfoundation.org/currents",
        community="https://forum.aptosfoundation.org/",
    ),
    "TIA": _sources(
        news="https://blog.celestia.org/",
        community="https://forum.celestia.org/",
    ),
    "INJ": _sources(
        news="https://injective.com/blog/",
        community="https://www.reddit.com/r/injective/",
        confidence=CURATED_LIKELY,
    ),
    "KAS": _sources(
        news="https://kaspa.org/blog/",
        community="https://www.reddit.com/r/kaspa/",
        confidence=CURATED_LIKELY,
    ),
    # ---- Scaling networks -------------------------------------------------
    "ARB": _sources(
        news="https://arbitrum.foundation/blog",
        community="https://forum.arbitrum.foundation/",
    ),
    "OP": _sources(
        news="https://optimism.mirror.xyz/",
        community="https://gov.optimism.io/",
    ),
    "POL": _sources(
        news="https://polygon.technology/blog",
        community="https://forum.polygon.technology/",
    ),
    "MATIC": _sources(
        news="https://polygon.technology/blog",
        community="https://forum.polygon.technology/",
    ),
    "STRK": _sources(
        news="https://www.starknet.io/blog/",
        community="https://community.starknet.io/",
    ),
    "BASE_NETWORK": _sources(
        news="https://base.mirror.xyz/",
        community="https://www.base.org/discord",
        confidence=CURATED_LIKELY,
    ),
    "SONIC": _sources(
        news="https://blog.soniclabs.com/",
        community="https://www.reddit.com/r/0xSonic/",
        confidence=CURATED_LIKELY,
    ),
    # ---- Application protocols -------------------------------------------
    "LINK": _sources(
        news="https://blog.chain.link/",
        community="https://www.reddit.com/r/Chainlink/",
    ),
    "UNI": _sources(
        news="https://blog.uniswap.org/",
        community="https://gov.uniswap.org/",
    ),
    "AAVE": _sources(
        news="https://aave.com/blog",
        community="https://governance.aave.com/",
    ),
    "MKR": _sources(
        news="https://blog.makerdao.com/",
        community="https://forum.makerdao.com/",
    ),
    "SKY": _sources(
        news="https://sky.money/blog",
        community="https://forum.sky.money/",
        confidence=CURATED_LIKELY,
    ),
    "COMP": _sources(
        news="https://compound.finance/blog",
        community="https://www.comp.xyz/",
        confidence=CURATED_LIKELY,
    ),
    "CRV": _sources(
        news="https://news.curve.finance/",
        community="https://gov.curve.finance/",
        confidence=CURATED_LIKELY,
    ),
    "SNX": _sources(
        news="https://blog.synthetix.io/",
        community="https://gov.synthetix.io/",
    ),
    "SUSD": _sources(
        news="https://blog.synthetix.io/",
        community="https://gov.synthetix.io/",
    ),
    "GRT": _sources(
        news="https://thegraph.com/blog/",
        community="https://forum.thegraph.com/",
    ),
    "LDO": _sources(
        news="https://blog.lido.fi/",
        community="https://research.lido.fi/",
    ),
    "RPL": _sources(
        news="https://medium.com/rocket-pool",
        community="https://dao.rocketpool.net/",
        confidence=CURATED_LIKELY,
    ),
    "SUSHI": _sources(
        news="https://www.sushi.com/blog",
        community="https://forum.sushi.com/",
        confidence=CURATED_LIKELY,
    ),
    "1INCH": _sources(
        news="https://blog.1inch.io/",
        community="https://gov.1inch.io/",
    ),
    "BAL": _sources(
        news="https://medium.com/balancer-protocol",
        community="https://forum.balancer.fi/",
        confidence=CURATED_LIKELY,
    ),
    "ENS": _sources(
        news="https://blog.ens.domains/",
        community="https://discuss.ens.domains/",
    ),
    "BAT": _sources(
        news="https://brave.com/blog/",
        community="https://community.brave.com/",
    ),
    "ZRX": _sources(
        news="https://blog.0x.org/",
        community="https://forum.0x.org/",
        confidence=CURATED_LIKELY,
    ),
    "ANKR": _sources(
        news="https://www.ankr.com/blog/",
        community="https://www.reddit.com/r/Ankr/",
    ),
    "LPT": _sources(
        news="https://blog.livepeer.org/",
        community="https://forum.livepeer.org/",
    ),
    "FET": _sources(
        news="https://fetch.ai/blog",
        community="https://www.reddit.com/r/FetchAI_Community/",
    ),
    "RENDER": _sources(
        news="https://rendernetwork.com/blog/",
        community="https://www.reddit.com/r/rendernetwork/",
        confidence=CURATED_LIKELY,
    ),
    "EIGEN": _sources(
        news="https://blog.eigencloud.xyz/",
        community="https://forum.eigenlayer.xyz/",
        confidence=CURATED_LIKELY,
    ),
    "AERO": _sources(
        news="https://aerodrome.finance/blog",
        community="https://www.reddit.com/r/AerodromeFi/",
        confidence=CURATED_LIKELY,
    ),
    "W": _sources(
        news="https://wormhole.com/blog",
        community="https://forum.wormhole.com/",
        confidence=CURATED_LIKELY,
    ),
    "ARKM": _sources(
        news="https://www.arkhamintelligence.com/blog",
        community="https://www.reddit.com/r/Arkham_Intelligence/",
        confidence=CURATED_LIKELY,
    ),
    "WMT": _sources(
        news="https://worldmobile.io/blog",
        community="https://www.reddit.com/r/WorldMobileToken/",
        confidence=CURATED_LIKELY,
    ),
    "OCEAN": _sources(
        news="https://blog.oceanprotocol.com/",
        community="https://www.reddit.com/r/oceanprotocol/",
        confidence=CURATED_LIKELY,
    ),
    "STORJ": _sources(
        news="https://www.storj.io/blog",
        community="https://forum.storj.io/",
    ),
    "AR": _sources(
        news="https://arweave.org/",
        community="https://www.reddit.com/r/Arweave/",
        confidence=CURATED_LIKELY,
    ),
    "IOTA": _sources(
        news="https://blog.iota.org/",
        community="https://govern.iota.org/",
        confidence=CURATED_LIKELY,
    ),
    "QNT": _sources(
        news="https://quant.network/blog/",
        community="https://www.reddit.com/r/QuantNetwork/",
        confidence=CURATED_LIKELY,
    ),
    "GALA": _sources(
        news="https://blog.gala.com/",
        community="https://www.reddit.com/r/GalaGames/",
        confidence=CURATED_LIKELY,
    ),
    "SAND": _sources(
        news="https://medium.com/sandbox-game",
        community="https://www.reddit.com/r/TheSandboxGaming/",
        confidence=CURATED_LIKELY,
    ),
    "MANA": _sources(
        news="https://decentraland.org/blog/",
        community="https://forum.decentraland.org/",
    ),
    "AXS": _sources(
        news="https://blog.axieinfinity.com/",
        community="https://www.reddit.com/r/AxieInfinity/",
        confidence=CURATED_LIKELY,
    ),
    "IMX": _sources(
        news="https://www.immutable.com/blog",
        community="https://forum.immutable.com/",
        confidence=CURATED_LIKELY,
    ),
    "FLOW": _sources(
        news="https://flow.com/blog",
        community="https://forum.flow.com/",
        confidence=CURATED_LIKELY,
    ),
    "CHZ": _sources(
        news="https://www.chiliz.com/blog/",
        community="https://www.reddit.com/r/Chiliz/",
        confidence=CURATED_LIKELY,
    ),
    "THETA": _sources(
        news="https://medium.com/theta-network",
        community="https://www.reddit.com/r/theta_network/",
        confidence=CURATED_LIKELY,
    ),
    "ZIL": _sources(
        news="https://blog.zilliqa.com/",
        community="https://www.reddit.com/r/zilliqa/",
        confidence=CURATED_LIKELY,
    ),
    "KAVA": _sources(
        news="https://www.kava.io/news",
        community="https://www.reddit.com/r/KavaUSDX/",
        confidence=CURATED_LIKELY,
    ),
    "SKL": _sources(
        news="https://skale.space/blog",
        community="https://forum.skale.network/",
        confidence=CURATED_LIKELY,
    ),
    "CELO": _sources(
        news="https://blog.celo.org/",
        community="https://forum.celo.org/",
    ),
    "MINA": _sources(
        news="https://minaprotocol.com/blog",
        community="https://forums.minaprotocol.com/",
    ),
    "SCRT": _sources(
        news="https://scrt.network/blog/",
        community="https://forum.scrt.network/",
        confidence=CURATED_LIKELY,
    ),
    "GLMR": _sources(
        news="https://moonbeam.network/blog/",
        community="https://forum.moonbeam.foundation/",
        confidence=CURATED_LIKELY,
    ),
    "ASTR": _sources(
        news="https://astar.network/blog",
        community="https://forum.astar.network/",
        confidence=CURATED_LIKELY,
    ),
    "KSM": _sources(
        news="https://polkadot.com/blog",
        community="https://forum.polkadot.network/",
        confidence=CURATED_LIKELY,
    ),
    "DASH": _sources(
        news="https://www.dash.org/blog/",
        community="https://www.dash.org/forum/",
        confidence=CURATED_LIKELY,
    ),
    "ZEC": _sources(
        news="https://electriccoin.co/blog/",
        community="https://forum.zcashcommunity.com/",
    ),
    "ETC": _sources(
        news="https://ethereumclassic.org/blog",
        community="https://www.reddit.com/r/EthereumClassic/",
    ),
    "XEC": _sources(
        news="https://www.bitcoinabc.org/releases/",
        community="https://www.reddit.com/r/ecash/",
        confidence=CURATED_LIKELY,
    ),
    "CORE": _sources(
        news="https://coredao.org/blogs",
        community="https://www.reddit.com/r/Core_DAO/",
        confidence=CURATED_LIKELY,
    ),
    "BEAM": _sources(
        news="https://meritcircle.mirror.xyz/",
        community="https://www.reddit.com/r/BeamNetwork/",
        confidence=CURATED_LIKELY,
    ),
    # ---- Exchange and issuer tokens --------------------------------------
    "KCS": _sources(
        news="https://www.kucoin.com/blog",
        community="https://www.reddit.com/r/kucoin/",
        confidence=CURATED_LIKELY,
    ),
    "HT": _sources(
        news="https://www.htx.com/en-us/support/notice/",
        community="https://www.reddit.com/r/HTX_Global/",
        confidence=CURATED_LIKELY,
    ),
    "OKB": _sources(
        news="https://www.okx.com/help/section/announcements-latest-announcements",
        community="https://www.reddit.com/r/OKX/",
        confidence=CURATED_LIKELY,
    ),
    "CRO": _sources(
        news="https://crypto.com/company-news",
        community="https://www.reddit.com/r/Crypto_com/",
        confidence=CURATED_LIKELY,
    ),
    "USDP": _sources(
        news="https://www.paxos.com/blog",
        community="https://www.reddit.com/r/PaxosStandard/",
        confidence=CURATED_LIKELY,
    ),
    "USDC": _sources(
        news="https://www.circle.com/blog",
        community="https://www.reddit.com/r/circle/",
        confidence=CURATED_LIKELY,
    ),
}


def normalized_url(value: str) -> str:
    """The comparison form of a URL. One owner, for everything.

    ``official_sources`` is unique on ``(canonical_asset_id, normalized_url)``, so this
    function decides when two addresses are the same page.

    There were four copies of it before this one, and **they disagreed**. Two kept a
    trailing slash and two stripped it, so ``https://site.example/blog/`` and
    ``https://site.example/blog`` were the same page to the importers and two different
    pages to the governance and identity code. The consequences were real: the same page
    could be stored twice under one asset, fetched twice, and counted twice in a
    dossier's evidence completeness, while the importer's own duplicate check said there
    was only one.

    The stripping form wins because it merges more: treating the two spellings as one
    page is right, and treating one page as two never was.
    """

    parsed = urlsplit(value.strip())
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, "", ""))


def is_official_url(value: str) -> bool:
    """Whether a string is safe to register and fetch as an official source.

    HTTPS only, a real host, and no user-info section — ``https://real.site@evil.tld``
    reads as ``real.site`` to a person and resolves to ``evil.tld`` in a client.
    """

    parsed = urlsplit(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc) and "@" not in parsed.netloc


def category_label(category: str) -> str:
    """The plain words for a category, for anything a person reads."""

    return SOURCE_CATEGORIES.get(category, category.replace("_", " "))


def missing_categories(present: Iterable[str] | None) -> tuple[str, ...]:
    """Which required categories an asset still cannot show."""

    held = {str(item) for item in present} if present is not None else set()
    return tuple(category for category in REQUIRED_CATEGORIES if category not in held)


def _title(asset_name: str, category: str) -> str:
    return f"{asset_name} {category_label(category)}"[:300]


def curated_candidates(symbol: str, asset_name: str) -> tuple[SourceCandidate, ...]:
    """Layer 0 — what a person already wrote down for this symbol."""

    entries = CURATED_SOURCES.get(symbol.strip().upper(), ())
    return tuple(
        SourceCandidate(
            category=entry.category,
            title=_title(asset_name, entry.category),
            url=entry.url,
            layer=DiscoveryLayer.CURATED,
            confidence=entry.confidence,
        )
        for entry in entries
        if is_official_url(entry.url)
    )


#: Hosts whose project pages are a community in their own right, keyed by the host
#: the identity record already holds. Reading a link out of an approved identity is
#: not guessing — the address was part of what a reviewer approved.
_DOCUMENTATION_COMMUNITY_HOSTS = {
    "github.com": "discussions",
    "www.github.com": "discussions",
}


def identity_candidates(
    *,
    asset_name: str,
    official_website: str | None,
    official_documentation: str | None,
) -> tuple[SourceCandidate, ...]:
    """Layer 1 — links carried by the identity a reviewer already approved.

    A GitHub documentation link is the clearest case: the same repository publishes
    releases, which is a project's most reliable "we changed something" feed, and
    hosts discussions, which is a real community. Neither is invented here; both are
    parts of an address that was already reviewed.
    """

    candidates: list[SourceCandidate] = []
    documentation = (official_documentation or "").strip()
    if documentation and is_official_url(documentation):
        parsed = urlsplit(documentation)
        host = parsed.netloc.casefold()
        parts = [part for part in parsed.path.split("/") if part]
        if host in _DOCUMENTATION_COMMUNITY_HOSTS and len(parts) >= 2:
            repository = f"https://{host}/{parts[0]}/{parts[1]}"
            candidates.append(
                SourceCandidate(
                    category=NEWS,
                    title=_title(asset_name, NEWS),
                    url=f"{repository}/releases",
                    layer=DiscoveryLayer.IDENTITY,
                    confidence=LAYER_CONFIDENCE[DiscoveryLayer.IDENTITY],
                )
            )
            candidates.append(
                SourceCandidate(
                    category=COMMUNITY,
                    title=_title(asset_name, COMMUNITY),
                    url=f"{repository}/{_DOCUMENTATION_COMMUNITY_HOSTS[host]}",
                    layer=DiscoveryLayer.IDENTITY,
                    confidence=LAYER_CONFIDENCE[DiscoveryLayer.IDENTITY],
                )
            )
    return tuple(candidates)


#: The paths a project is most likely to publish under, in the order they are worth
#: trying. Every one of these is a guess and is scored as one.
_CONVENTION_PATHS: dict[str, tuple[str, ...]] = {
    NEWS: ("blog", "news", "blog/", "announcements"),
    COMMUNITY: ("community", "forum"),
}
#: Subdomains projects conventionally publish under.
_CONVENTION_HOSTS: dict[str, tuple[str, ...]] = {
    NEWS: ("blog",),
    COMMUNITY: ("forum", "gov"),
}


def convention_candidates(
    *,
    asset_name: str,
    official_website: str | None,
) -> tuple[SourceCandidate, ...]:
    """Layer 2 — the addresses a project usually publishes under.

    Pure guesswork, priced accordingly. A guess is only ever worth proposing because
    nothing downstream trusts it: it has to be fetched, allowed by robots, readable
    and recent before it counts, and the same proof would reject a wrong guess.
    """

    website = (official_website or "").strip()
    if not website or not is_official_url(website):
        return ()
    parsed = urlsplit(website)
    host = parsed.netloc.casefold()
    root = host[4:] if host.startswith("www.") else host
    confidence = LAYER_CONFIDENCE[DiscoveryLayer.CONVENTION]
    candidates: list[SourceCandidate] = []
    seen: set[str] = set()
    for category in REQUIRED_CATEGORIES:
        for path in _CONVENTION_PATHS[category]:
            url = f"https://{host}/{path.strip('/')}"
            if url in seen:
                continue
            seen.add(url)
            candidates.append(
                SourceCandidate(
                    category=category,
                    title=_title(asset_name, category),
                    url=url,
                    layer=DiscoveryLayer.CONVENTION,
                    confidence=confidence,
                )
            )
        for subdomain in _CONVENTION_HOSTS[category]:
            url = f"https://{subdomain}.{root}/"
            if url in seen:
                continue
            seen.add(url)
            candidates.append(
                SourceCandidate(
                    category=category,
                    title=_title(asset_name, category),
                    url=url,
                    layer=DiscoveryLayer.CONVENTION,
                    confidence=confidence,
                )
            )
    return tuple(candidates)


def candidates_for(
    layer: DiscoveryLayer,
    *,
    symbol: str,
    asset_name: str,
    official_website: str | None,
    official_documentation: str | None,
) -> tuple[SourceCandidate, ...]:
    """Every candidate one layer can offer for one asset.

    The single entry point. A caller walks ``LAYER_ORDER`` and asks this for each
    layer rather than knowing which function belongs to which layer.
    """

    if layer is DiscoveryLayer.CURATED:
        return curated_candidates(symbol, asset_name)
    if layer is DiscoveryLayer.IDENTITY:
        return identity_candidates(
            asset_name=asset_name,
            official_website=official_website,
            official_documentation=official_documentation,
        )
    return convention_candidates(
        asset_name=asset_name,
        official_website=official_website,
    )
