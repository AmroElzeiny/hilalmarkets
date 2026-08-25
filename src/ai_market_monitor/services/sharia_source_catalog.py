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

**The layers.** A link can be known in five ways, and they are not equally trustworthy:

======================  ==================================================  ==========
Layer                   How it knows                                        Confidence
======================  ==================================================  ==========
``CURATED``             Written down here by a person who checked it          0.95/0.80
``IDENTITY``            Derived from the identity a reviewer already          0.75
                        approved: the official site, the docs, the
                        provider profile
``SOCIAL``              A channel the project's own website links to —        0.70
                        its Telegram channel, its X account, its forum
``SEARCH``              Found by searching the open web for the project's     0.55/0.45
                        own news, and kept only if it is provably the
                        project's own page or handle
``CONVENTION``          Guessed from the official domain — ``/blog``,         0.45
                        ``/news``, ``/community``
======================  ==================================================  ==========

Nothing here touches the network or the database. A candidate produced by this module is
a **proposal**, never a fact. It becomes a usable source only after
``sharia_source_resolution`` has fetched it and proved it is alive, permitted, readable
and — for news — recent. That ordering is what makes a guessed URL safe to propose: a
wrong guess cannot promote itself, it can only fail its proof and fall through to the
next layer, and then to a person.

The two layers that need the network are still decided here. ``sharia_source_discovery``
does the fetching and the searching; it hands the raw links and the raw search results
back to this module, and **this module alone** decides whether an address is the
project's own and which category it belongs to. Splitting it the other way round is how
the duplicate-vocabulary failure starts: the searcher would learn one set of rules about
what "official" means and the catalog another.

The confidence numbers are deliberately coarse. They rank candidates and decide when to
give up on the machine and ask a human; they are not a probability of anything, and no
Sharia status is ever read from them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
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

#: Everything a source row's ``verification_state`` may say, and the plain words for it.
#:
#: The states are vocabulary, so they live here with the categories rather than beside
#: the code that writes them. ``sharia_source_resolution`` is still the only module
#: allowed to *write* one; this is the only module that decides what they are called.
#:
#: ``not_permitted`` is the newest and the reason the plain words exist. It means the
#: page is real and the site's own rules say the product may not read it — which is a
#: completely different job for a person than a dead link, and both used to be shown as
#: "unreachable". A reviewer went hunting for a replacement page that already existed.
VERIFIED = "verified"
CANDIDATE = "candidate"
UNREACHABLE = "unreachable"
NOT_PERMITTED = "not_permitted"

VERIFICATION_STATES: dict[str, str] = {
    VERIFIED: "working",
    CANDIDATE: "not proved yet",
    UNREACHABLE: "gone",
    NOT_PERMITTED: "blocked to us",
}

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
    SOCIAL = "social"
    SEARCH = "search"
    CONVENTION = "convention"
    #: A language model asked, in the exact words a person would use, where a project
    #: publishes. The **last** layer and the only paid one: it runs for a coin only when
    #: every free way of finding a page has already produced nothing. Its answers are
    #: proposals like every other layer's — they are filtered by
    #: :func:`search_candidates`, which keeps only addresses provably the project's own,
    #: and then fetched and proved. A model that invents an address cannot promote it.
    ASSISTED = "assisted"


#: The order the layers are tried in. A layer runs only for the categories the
#: layers before it could not settle.
LAYER_ORDER: tuple[DiscoveryLayer, ...] = (
    DiscoveryLayer.CURATED,
    DiscoveryLayer.IDENTITY,
    DiscoveryLayer.SOCIAL,
    DiscoveryLayer.SEARCH,
    DiscoveryLayer.CONVENTION,
    DiscoveryLayer.ASSISTED,
)

#: The layers that cannot answer from what is already known. They need somebody to go
#: and look: fetch the project's own site, ask a search engine, or ask a model.
#: ``candidates_for`` still decides what their findings mean — see the module note.
NETWORK_LAYERS: frozenset[DiscoveryLayer] = frozenset(
    {DiscoveryLayer.SOCIAL, DiscoveryLayer.SEARCH, DiscoveryLayer.ASSISTED}
)

#: The layer that costs money every time it runs. Kept separate from the rest so the one
#: gate that matters — "everything free has already failed" — is written once.
PAID_LAYERS: frozenset[DiscoveryLayer] = frozenset({DiscoveryLayer.ASSISTED})

#: A curated link somebody checked against the project's own site.
CURATED_CONFIDENT = 0.95
#: A curated link for the right project whose exact address is less certain —
#: usually a site that has moved once. Still has to survive its proof.
CURATED_LIKELY = 0.80

#: A search result on the project's own domain, or a channel whose address the
#: project's own site also carries. The address is the project's; which page it is
#: still has to be proved.
SEARCH_OWN_DOMAIN = 0.55
#: A channel on somebody else's platform whose handle matches the project's name and
#: nothing else vouches for it. The weakest thing this module will propose at all.
SEARCH_NAME_MATCHED = 0.45

#: The same two numbers for an address a model suggested, one step lower. A model is a
#: cheaper witness than a search engine — it is recalling rather than looking — so its
#: proposals start below one, and like every guess they only become usable by surviving
#: the proof. Both still clear ``CONFIDENCE_FLOOR`` once proved, and neither clears it
#: unproved, which is the property that matters.
ASSISTED_OWN_DOMAIN = 0.45
ASSISTED_NAME_MATCHED = 0.40

LAYER_CONFIDENCE: dict[DiscoveryLayer, float] = {
    DiscoveryLayer.CURATED: CURATED_CONFIDENT,
    DiscoveryLayer.IDENTITY: 0.75,
    DiscoveryLayer.SOCIAL: 0.70,
    DiscoveryLayer.SEARCH: SEARCH_OWN_DOMAIN,
    DiscoveryLayer.CONVENTION: 0.45,
    DiscoveryLayer.ASSISTED: ASSISTED_OWN_DOMAIN,
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

#: How many proved links each required category should end up with. One working page
#: is a single point of failure: the day it moves, the asset has no way to hear about
#: the project at all. Layers keep running until a category has this many, which is
#: why a coin is expected to carry six or more links rather than two.
LINKS_WANTED_PER_CATEGORY = 3

#: How few proved links a category may fall to before a person is asked. Below the
#: wanted number the machine simply keeps looking; below *this* number the asset has
#: a real gap and a task is opened. Keeping the two apart is what stops the review
#: queue filling with "this coin has two news pages instead of three".
LINKS_REQUIRED_PER_CATEGORY = 1


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


def state_label(state: str) -> str:
    """The plain words for a source's state, for anything a person reads.

    The System Brain used to print the stored word straight onto the page, so a
    reviewer read "candidate" and "unreachable". Neither says what to do about it.
    """

    return VERIFICATION_STATES.get(state, str(state).replace("_", " "))


def categories_below(counts: Mapping[str, int], wanted: int) -> tuple[str, ...]:
    """Which required categories have fewer than ``wanted`` proved links.

    One owner for "is this asset short", asked with two different numbers. The
    resolver asks with :data:`LINKS_WANTED_PER_CATEGORY` to decide whether to keep
    looking, and with :data:`LINKS_REQUIRED_PER_CATEGORY` to decide whether to ask a
    person. Two copies of this arithmetic would eventually disagree about which of
    those two questions a caller was asking.
    """

    return tuple(
        category for category in REQUIRED_CATEGORIES if counts.get(category, 0) < wanted
    )


def missing_categories(present: Iterable[str] | None) -> tuple[str, ...]:
    """Which required categories an asset cannot show even one link for."""

    held = {str(item) for item in present} if present is not None else set()
    return categories_below({category: 1 for category in held}, 1)


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


# ---------------------------------------------------------------------------
# Channels: the places a project talks that are not its own website
# ---------------------------------------------------------------------------
#
# A Shariah reviewer needs to know when a project changed what it does. More and more
# projects say that first on Telegram or on X and only later, if ever, on a blog. So
# those channels are official sources in exactly the same sense the blog is — and they
# are held to exactly the same proof.
#
# Two rules keep this safe, and both are here rather than in the searcher:
#
# 1. **A channel is only official if the project itself vouches for it.** Either the
#    project's own website links to it, or the handle is the project's own name. A
#    Telegram group somebody set up about a coin is not the coin's announcement channel,
#    and a search engine cannot tell the difference.
# 2. **A single post is never a source.** ``x.com/foo/status/123`` is one message;
#    ``x.com/foo`` is the feed that keeps producing them. Only the feed is registered.

#: Hosts that let anybody publish under them. The project's own domain is decided by
#: comparing registrable domains, and on these hosts that comparison is meaningless —
#: ``evil.github.io`` and ``project.github.io`` share a registrable domain and share
#: nothing else. On these, only the exact host counts as the same project.
SHARED_PUBLISHING_HOSTS: frozenset[str] = frozenset(
    {
        "blogspot.com",
        "gitbook.io",
        "github.io",
        "medium.com",
        "mirror.xyz",
        "netlify.app",
        "notion.site",
        "pages.dev",
        "substack.com",
        "vercel.app",
        "webflow.io",
        "wixsite.com",
        "wordpress.com",
    }
)

#: Two-part public suffixes this product actually meets. Not the whole public suffix
#: list — that is a downloadable file that goes stale, and being wrong here only ever
#: makes the same-project test *stricter*, never looser.
_COMPOUND_SUFFIXES: frozenset[str] = frozenset(
    {
        "ac.uk", "co.id", "co.il", "co.in", "co.jp", "co.kr", "co.nz", "co.uk",
        "co.za", "com.ar", "com.au", "com.br", "com.cn", "com.hk", "com.mx",
        "com.my", "com.sg", "com.tr", "com.tw", "gov.uk", "ne.jp", "net.au",
        "net.cn", "or.jp", "org.au", "org.cn", "org.uk",
    }
)

#: Words in a subdomain or a path that mean "this is where the project publishes".
NEWS_PATH_WORDS: tuple[str, ...] = (
    "announcement",
    "announcements",
    "article",
    "articles",
    "blog",
    "changelog",
    "currents",
    "insights",
    "media",
    "news",
    "newsroom",
    "post",
    "posts",
    "press",
    "release",
    "releases",
    "update",
    "updates",
)

#: Words that mean "this is where the project's people talk".
COMMUNITY_PATH_WORDS: tuple[str, ...] = (
    "community",
    "dao",
    "discuss",
    "discussion",
    "discussions",
    "forum",
    "forums",
    "gov",
    "governance",
    "research",
    "talk",
)

#: Handles a platform keeps for itself. ``x.com/i/flow/login`` is not a project.
_RESERVED_HANDLES: frozenset[str] = frozenset(
    {
        "about", "account", "explore", "help", "home", "i", "intent", "jobs",
        "login", "messages", "notifications", "privacy", "search", "settings",
        "share", "signup", "support", "tos", "compose", "hashtag", "status",
        "s", "c", "user", "users", "joinchat", "addstickers", "proxy", "socks",
    }
)

#: Suffixes a project bolts onto its own name when it registers a handle. Used only to
#: decide that ``@aptos_network`` belongs to Aptos — never to decide anything else.
_HANDLE_SUFFIXES: tuple[str, ...] = (
    "ann",
    "announcements",
    "app",
    "chain",
    "coin",
    "community",
    "dao",
    "en",
    "eng",
    "english",
    "fdn",
    "foundation",
    "global",
    "hq",
    "io",
    "labs",
    "network",
    "news",
    "official",
    "officialchannel",
    "org",
    "project",
    "protocol",
    "token",
)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One row a search engine returned. Raw, unjudged."""

    url: str
    title: str = ""


def _host_of(value: str) -> str:
    return urlsplit(value.strip()).netloc.casefold().partition(":")[0]


def registrable_domain(value: str) -> str:
    """The part of a host that identifies who owns it.

    ``blog.ethereum.org`` and ``ethereum.org`` are the same project; ``ethereum.org``
    and ``ethereum-airdrop.example`` are not. Comparing whole hosts would miss the
    first, and comparing a bare word would accept the second.
    """

    host = _host_of(value)
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in _COMPOUND_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_same_project_site(value: str, official_website: str | None) -> bool:
    """Whether an address belongs to the site a reviewer already approved.

    On a host anybody can publish under, only the exact host counts. That is the
    difference between "the project's own blog" and "a page somebody put on the same
    free hosting".
    """

    site = (official_website or "").strip()
    if not site or not value.strip():
        return False
    site_domain = registrable_domain(site)
    if not site_domain:
        return False
    if site_domain in SHARED_PUBLISHING_HOSTS:
        return _host_of(value) == _host_of(site)
    return registrable_domain(value) == site_domain


def _identity_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def handle_matches_project(handle: str, *, asset_name: str, symbol: str) -> bool:
    """Whether a platform handle is plainly the project's own.

    Deliberately strict. ``@aptos``, ``@aptos_network`` and ``@AptosOfficial`` are
    Aptos; ``@aptosnews_daily`` is somebody's fan account and is refused. Refusing is
    always safe here — the address simply is not proposed, and the next layer runs.
    """

    wanted = {_identity_text(asset_name), _identity_text(symbol)}
    wanted.discard("")
    found = _identity_text(handle)
    if not found or not wanted:
        return False
    if found in wanted:
        return True
    return any(
        found == f"{stem}{suffix}" or found == f"{suffix}{stem}"
        for stem in wanted
        for suffix in _HANDLE_SUFFIXES
    )


def _path_parts(value: str) -> list[str]:
    return [part for part in urlsplit(value.strip()).path.split("/") if part]


def _word_category(words: Iterable[str]) -> str | None:
    """Which category a set of address words points at, if any."""

    lowered = {str(word).casefold() for word in words}
    if lowered & set(NEWS_PATH_WORDS):
        return NEWS
    if lowered & set(COMMUNITY_PATH_WORDS):
        return COMMUNITY
    return None


@dataclass(frozen=True, slots=True)
class Channel:
    """A public channel, said the one way the product stores it."""

    category: str
    url: str
    #: The account name on the platform, when there is one. Empty for a plain web page.
    handle: str = ""
    #: The platform's own name, for the reviewer's benefit.
    platform: str = ""


def classify_channel(value: str, *, official_website: str | None = None) -> Channel | None:
    """What kind of source an address is, or ``None`` if it is not one at all.

    The one owner of the question "is this a news page, a community page, or neither".
    Everything that finds an address — the harvester, the searcher, a reviewer's paste —
    asks this rather than keeping its own idea of what a Telegram link means.
    """

    text = value.strip()
    if not is_official_url(text):
        return None
    host = _host_of(text)
    parts = _path_parts(text)
    root = host[4:] if host.startswith("www.") else host

    if root in {"t.me", "telegram.me", "telegram.dog"}:
        # ``/s/<name>`` is Telegram's own public web view of a channel: it renders the
        # posts, with their dates, to an ordinary HTTP client. ``/<name>`` shows a join
        # box and nothing else, so it is rewritten to the view that can actually be
        # read and dated. An invite link is a private group and is refused outright.
        names = [part for part in parts if part.casefold() != "s"]
        if not names or names[0].startswith("+") or names[0].casefold() in _RESERVED_HANDLES:
            return None
        handle = names[0]
        return Channel(
            category=NEWS, url=f"https://t.me/s/{handle}", handle=handle, platform="Telegram"
        )

    if root in {"x.com", "twitter.com"}:
        if not parts or parts[0].casefold() in _RESERVED_HANDLES or len(parts) > 1:
            return None  # a single post, a search, or a platform page — not a feed
        handle = parts[0]
        return Channel(category=NEWS, url=f"https://x.com/{handle}", handle=handle, platform="X")

    if root == "reddit.com" or root.endswith(".reddit.com"):
        if len(parts) < 2 or parts[0].casefold() != "r":
            return None
        handle = parts[1]
        return Channel(
            category=COMMUNITY,
            url=f"https://www.reddit.com/r/{handle}/",
            handle=handle,
            platform="Reddit",
        )

    if root == "discord.gg" or (root == "discord.com" and parts[:1] == ["invite"]):
        code = parts[-1] if parts else ""
        if not code:
            return None
        return Channel(
            category=COMMUNITY, url=f"https://discord.gg/{code}", platform="Discord"
        )

    if root == "github.com" and len(parts) >= 2:
        # The organisation is the handle. A repository under ``aptos-labs`` is Aptos's
        # own; one under ``some-fan`` is not, and without a handle to check the search
        # layer had no way to tell them apart and refused both.
        repository = f"https://github.com/{parts[0]}/{parts[1]}"
        if len(parts) >= 3 and parts[2].casefold() in {"discussions", "issues"}:
            return Channel(
                category=COMMUNITY,
                url=f"{repository}/discussions",
                handle=parts[0],
                platform="GitHub",
            )
        return Channel(
            category=NEWS,
            url=f"{repository}/releases",
            handle=parts[0],
            platform="GitHub",
        )

    if root == "medium.com" and parts:
        return Channel(
            category=NEWS,
            url=f"https://medium.com/{parts[0]}",
            handle=parts[0].lstrip("@"),
            platform="Medium",
        )

    if root.endswith(".medium.com"):
        return Channel(
            category=NEWS, url=f"https://{host}/", handle=root.split(".")[0], platform="Medium"
        )

    if root == "mirror.xyz" and parts:
        return Channel(
            category=NEWS, url=f"https://mirror.xyz/{parts[0]}", handle=parts[0], platform="Mirror"
        )

    if root.endswith(".mirror.xyz"):
        return Channel(
            category=NEWS, url=f"https://{host}/", handle=root.split(".")[0], platform="Mirror"
        )

    if root.endswith(".substack.com"):
        return Channel(
            category=NEWS, url=f"https://{host}/", handle=root.split(".")[0], platform="Substack"
        )

    if root in {"youtube.com", "youtu.be"} and parts:
        if parts[0].startswith("@"):
            return Channel(
                category=NEWS,
                url=f"https://www.youtube.com/{parts[0]}",
                handle=parts[0].lstrip("@"),
                platform="YouTube",
            )
        return None

    # Anything else is only a channel when it sits on the project's own site and its
    # address says what it is. A bare homepage is already registered as the website.
    #
    # The word has to be the **last** part of the address, and that is the whole rule:
    # ``solana.com/news`` is the feed, ``solana.com/news/the-token-supercycle`` is one
    # article inside it. Reading the words anywhere in the path filed eleven separate
    # Solana articles as eleven news sources, which is the same defect as filing one X
    # post as a feed — a single item stops being updated the moment it is published.
    if is_same_project_site(text, official_website):
        if parts:
            category = _word_category([parts[-1]])
        else:
            subdomain_words = root.split(".")[:-2] if root.count(".") >= 2 else []
            category = _word_category(subdomain_words)
        if category is not None:
            return Channel(category=category, url=text, platform="")
    return None


def channel_candidates(
    *,
    asset_name: str,
    official_website: str | None,
    links: Iterable[str],
    limit: int = 16,
) -> tuple[SourceCandidate, ...]:
    """Layer 3 — the channels the project's own website points at.

    Not a guess and not a search result: the project published these addresses itself,
    on the page a reviewer approved. That is the strongest claim to "this is really
    theirs" that exists short of somebody checking by hand, which is why it sits just
    below the identity layer.
    """

    confidence = LAYER_CONFIDENCE[DiscoveryLayer.SOCIAL]
    produced: list[SourceCandidate] = []
    seen: set[str] = set()
    # Shallowest address first. When a site links to both ``/community`` and
    # ``/community/events``, the first is the one that keeps producing, and the cap
    # below should never be spent on the second.
    ordered = sorted(links, key=lambda item: (len(_path_parts(item)), item))
    for link in ordered:
        channel = classify_channel(link, official_website=official_website)
        if channel is None:
            continue
        key = normalized_url(channel.url)
        if key in seen:
            continue
        seen.add(key)
        produced.append(
            SourceCandidate(
                category=channel.category,
                title=_channel_title(asset_name, channel),
                url=channel.url,
                layer=DiscoveryLayer.SOCIAL,
                confidence=confidence,
            )
        )
        if len(produced) >= limit:
            break
    return tuple(produced)


#: What the search layer asks for. One coin, several questions, because a project's
#: announcements do not all live in one place: the blog, the Telegram channel and the
#: X account are three different answers to "where does this project say things".
SEARCH_QUERIES: tuple[str, ...] = (
    '"{name}" {symbol} crypto official news',
    '"{name}" {symbol} official blog announcements',
    '"{name}" crypto official Telegram announcement channel',
    '"{name}" {symbol} official X account',
    '"{name}" crypto official community forum',
)


def search_queries(*, asset_name: str, symbol: str) -> tuple[str, ...]:
    """The exact questions the searcher asks about one coin."""

    name = asset_name.strip()
    ticker = symbol.strip().upper()
    if not name:
        return ()
    return tuple(query.format(name=name, symbol=ticker).strip() for query in SEARCH_QUERIES)


def search_candidates(
    *,
    asset_name: str,
    symbol: str,
    official_website: str | None,
    results: Iterable[SearchResult],
    limit: int = 16,
    layer: DiscoveryLayer = DiscoveryLayer.SEARCH,
) -> tuple[SourceCandidate, ...]:
    """Layer 4 — what an open-web search turned up, after the un-official half is dropped.

    A search engine answers "pages about this coin". An official source is "pages *by*
    this coin". Those are very different sets, and the second is a small part of the
    first: a market-data site, an exchange listing page and a news outlet's coverage all
    rank highly and none of them is the project speaking.

    So a result is kept only when it is provably the project's own — its own domain, or
    a handle that is its own name — and everything else is dropped without comment. A
    third-party article can be perfectly true and still must never be filed as an
    official source, because the whole point of the register is that it holds what the
    project itself said.
    """

    produced: list[SourceCandidate] = []
    seen: set[str] = set()
    for result in results:
        candidate = _search_candidate(
            asset_name=asset_name,
            symbol=symbol,
            official_website=official_website,
            result=result,
            layer=layer,
        )
        if candidate is None:
            continue
        key = candidate.normalized_url
        if key in seen:
            continue
        seen.add(key)
        produced.append(candidate)
        if len(produced) >= limit:
            break
    return tuple(produced)


def _search_candidate(
    *,
    asset_name: str,
    symbol: str,
    official_website: str | None,
    result: SearchResult,
    layer: DiscoveryLayer = DiscoveryLayer.SEARCH,
) -> SourceCandidate | None:
    own_domain, name_matched = (
        (ASSISTED_OWN_DOMAIN, ASSISTED_NAME_MATCHED)
        if layer is DiscoveryLayer.ASSISTED
        else (SEARCH_OWN_DOMAIN, SEARCH_NAME_MATCHED)
    )
    channel = classify_channel(result.url, official_website=official_website)
    if channel is None:
        return None
    if is_same_project_site(channel.url, official_website):
        confidence = own_domain
    elif channel.handle and handle_matches_project(
        channel.handle, asset_name=asset_name, symbol=symbol
    ):
        confidence = name_matched
    else:
        return None
    return SourceCandidate(
        category=channel.category,
        title=_channel_title(asset_name, channel),
        url=channel.url,
        layer=layer,
        confidence=confidence,
    )


def _channel_title(asset_name: str, channel: Channel) -> str:
    if channel.platform:
        return f"{asset_name} {channel.platform} {category_label(channel.category)}"[:300]
    return _title(asset_name, channel.category)


def candidates_for(
    layer: DiscoveryLayer,
    *,
    symbol: str,
    asset_name: str,
    official_website: str | None,
    official_documentation: str | None,
    channel_links: Iterable[str] = (),
    search_results: Iterable[SearchResult] = (),
) -> tuple[SourceCandidate, ...]:
    """Every candidate one layer can offer for one asset.

    The single entry point. A caller walks ``LAYER_ORDER`` and asks this for each
    layer rather than knowing which function belongs to which layer.

    ``channel_links`` and ``search_results`` are what somebody went and fetched for the
    two network layers. Passing nothing is not an error: a layer with no input offers
    no candidates, which is exactly what happens when web search is not configured.
    """

    if layer is DiscoveryLayer.CURATED:
        return curated_candidates(symbol, asset_name)
    if layer is DiscoveryLayer.IDENTITY:
        return identity_candidates(
            asset_name=asset_name,
            official_website=official_website,
            official_documentation=official_documentation,
        )
    if layer is DiscoveryLayer.SOCIAL:
        return channel_candidates(
            asset_name=asset_name,
            official_website=official_website,
            links=channel_links,
        )
    if layer in {DiscoveryLayer.SEARCH, DiscoveryLayer.ASSISTED}:
        # The model's answers go through the **same** filter a search engine's answers go
        # through — kept only when provably the project's own domain or its own handle.
        # Giving the assisted layer its own judgement is precisely the duplicate-vocabulary
        # failure this module exists to prevent: one of the two would eventually learn a
        # looser idea of "official" than the other.
        return search_candidates(
            asset_name=asset_name,
            symbol=symbol,
            official_website=official_website,
            results=search_results,
            layer=layer,
        )
    return convention_candidates(
        asset_name=asset_name,
        official_website=official_website,
    )
