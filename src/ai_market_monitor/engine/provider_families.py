"""Which context feeds this product can read, and how each one is satisfied.

A capability that needs something beyond the candles names the **provider family** it
needs — ``order_book``, ``cross_market``, ``risk_context`` and so on. Whether that family
can actually be read is a property of the deployment, not of the capability.

That distinction was lost. ``capability_compatibility._availability`` returned
``provider_required`` the moment a capability carried the label, and never asked whether
the data was reachable. It could not ask: nothing in the codebase answered the question.
So 143 capabilities were hidden from the Builder and from the assistant's shortlist for
ever, including 84 whose values the scanner already computes on every candle of every
coin — the order book reader in ``services/market_preview.py``, the cross-market,
breadth and ranking builders in ``provider_context.py``, the risk numbers in
``engine/evaluator.py::_risk_context``, and the alert-budget and setup-age readers in
``engine/context_conditions.py::runtime_context_metric``. Every one of those is built,
wired into the live scan path, and covered by tests. The label alone kept them dark.

This module is the one owner of that question. Four separate places used to hold a piece
of the answer and disagree about it:

* ``engine/capabilities.py`` — the ``provider_required`` label on each spec;
* ``engine/builder_contract.py`` — ``FEED_IN_PLAIN_WORDS``, a hand-kept name list;
* ``engine/capability_shortlist.py`` — ``SETUP_*_PROVIDER_REQUIREMENTS``, a hand-kept
  set of contract names the Builder gated on;
* ``provider_context.py`` — the settings each family actually reads at scan time.

They now all read from here. Adding a family is one entry in :data:`PROVIDER_FAMILIES`,
not four edits in four files that a reviewer has to notice are related.

**How a family is satisfied** is the field that matters:

``platform``
    Hilal Markets computes it from data it already holds. No account, no key, no
    external call beyond the exchange the trader already chose. These are served
    everywhere, including in tests and offline tooling.

``external_api``
    A third-party address must be configured before the family can answer. Unconfigured
    means unavailable, and the Builder says which feed is missing in plain words.

``no_adapter``
    Nothing serves this yet — there is no reader to configure. It is listed so the name
    resolves and so the Builder can explain itself, never so it can be switched on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ServedBy = Literal["platform", "external_api", "no_adapter"]


@dataclass(frozen=True, slots=True)
class SettingRequirement:
    """One ``Settings`` field that must be truthy before a family can answer.

    ``default_is_met`` is what ``Settings`` declares when nothing is configured, so
    availability can be resolved without a ``Settings`` instance — which is what the
    engine, the documentation generator and most tests have.
    """

    name: str
    default_is_met: bool


@dataclass(frozen=True, slots=True)
class ProviderFamily:
    key: str
    #: What the feed is, for somebody who has never read this code. Shown in the
    #: Builder when a rule cannot run because the feed is missing.
    plain_words: str
    served_by: ServedBy
    #: Every requirement must be met. Empty means nothing to configure.
    requires: tuple[SettingRequirement, ...] = ()


#: Contract names carried by every market-data adapter. Not families: a capability that
#: asks for ``ohlcv`` is asking for the candles the scanner already fetches.
BASE_MARKET_DATA_CONTRACTS = frozenset({"", "ohlcv", "market_data", "candles"})

#: Added when the configured adapter is CCXT, which is every launch deployment.
CCXT_MARKET_DATA_CONTRACTS = frozenset({"ccxt"})


PROVIDER_FAMILIES: tuple[ProviderFamily, ...] = (
    # ── Served by the platform itself ────────────────────────────────────────────
    #
    # `_risk_context` builds these into the condition context *before* the condition
    # tree is evaluated, so a rule reading one of them is reading a number the same
    # evaluation already produced. Nothing external is involved.
    ProviderFamily("risk_context", "your trade size and stop settings", "platform"),
    # `runtime_context_metric` answers both of these from the alert history and the
    # setup row the scanner already carries.
    ProviderFamily("alert_behavior", "your own past alerts", "platform"),
    ProviderFamily("setup_lifecycle", "how long a setup has been running", "platform"),
    # `ProviderContextService._cross_market` fetches BTC and ETH candles from the same
    # exchange the trader already picked, through the same adapter.
    ProviderFamily("cross_market", "the prices of other coins", "platform"),
    # Both are computed from one universe snapshot built out of the candles the scan
    # already pulls. `market_breadth_max_symbols` bounds it.
    ProviderFamily("market_breadth", "how many coins are rising or falling", "platform"),
    ProviderFamily("universe_ranking", "how coins rank against each other", "platform"),
    # `CcxtMarketDataProvider.fetch_order_book_context` is a complete reader: spread,
    # depth, imbalance, walls, trade flow. The switch defaults to on.
    ProviderFamily(
        "order_book",
        "the live buy and sell orders",
        "platform",
        (SettingRequirement("binance_order_book_enabled", True),),
    ),
    # ── Served once an address is configured ─────────────────────────────────────
    #
    # Binance USD-M futures is public and free, but futures context is a deliberate
    # product decision on a spot-only product, so it stays off until switched on.
    ProviderFamily(
        "derivatives",
        "futures market numbers",
        "external_api",
        (SettingRequirement("binance_derivatives_enabled", False),),
    ),
    ProviderFamily(
        "crypto_index",
        "a whole-market index",
        "external_api",
        (SettingRequirement("crypto_index_api_url", False),),
    ),
    ProviderFamily(
        "macro_market",
        "the wider financial markets",
        "external_api",
        (SettingRequirement("macro_market_api_url", False),),
    ),
    ProviderFamily(
        "event_feed",
        "news and events",
        "external_api",
        (SettingRequirement("event_feed_api_url", False),),
    ),
    ProviderFamily(
        "token_categories",
        "which group a coin belongs to",
        "external_api",
        (SettingRequirement("token_category_api_url", False),),
    ),
    # ── Nothing reads this yet ───────────────────────────────────────────────────
    #
    # There is no market-cap reader anywhere in the product, which is why
    # `capability_compatibility` also marks the `market_cap` operand unsupported. Listed
    # so the name resolves and the Builder can say what is missing.
    ProviderFamily("market_cap_provider", "how big a coin is by market value", "no_adapter"),
)


PROVIDER_FAMILY_BY_KEY: dict[str, ProviderFamily] = {
    family.key: family for family in PROVIDER_FAMILIES
}


def plain_feed_name(family: str) -> str:
    """What this feed is, in words a beginner can act on."""

    known = PROVIDER_FAMILY_BY_KEY.get(str(family))
    return known.plain_words if known else str(family).replace("_", " ")


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    """Which provider families this deployment can actually read.

    Frozen and hashable so it can key the compatibility cache. Two deployments with the
    same configured feeds share one computed registry; changing a feed produces a
    different value and therefore a different entry, rather than a stale one.
    """

    served: frozenset[str]

    def serves(self, family: str | None) -> bool:
        if not family:
            return True
        return str(family) in self.served

    def missing(self, families: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item for item in families if not self.serves(item))

    def contract_names(self, *, market_data_provider: str = "ccxt") -> frozenset[str]:
        """Every provider contract name a Builder mechanic may ask for and still run.

        The base market-data contracts plus the families this deployment serves, in one
        set, because the Builder checks a capability's ``provider_requirements`` against
        a single set and those requirements mix the two kinds freely.
        """

        contracts = set(BASE_MARKET_DATA_CONTRACTS) | set(self.served)
        if market_data_provider.strip().casefold() == "ccxt":
            contracts |= CCXT_MARKET_DATA_CONTRACTS
        return frozenset(contracts)


def _requirement_met(requirement: SettingRequirement, settings: Any | None) -> bool:
    if settings is None:
        return requirement.default_is_met
    return bool(getattr(settings, requirement.name, None))


def availability_from_settings(settings: Any | None = None) -> ProviderAvailability:
    """Resolve which families answer, for this deployment's configuration.

    ``settings`` is read by attribute name rather than typed as ``Settings`` so the
    engine keeps no import edge to the application's configuration layer. Passing
    ``None`` resolves every requirement to the default ``Settings`` declares, which is
    the correct answer for documentation, tooling and tests.
    """

    served = {
        family.key
        for family in PROVIDER_FAMILIES
        if family.served_by != "no_adapter"
        and all(_requirement_met(item, settings) for item in family.requires)
    }
    return ProviderAvailability(served=frozenset(served))


#: What the families answer when nothing has configured otherwise: everything the
#: platform serves on its own, and nothing that needs an address.
DEFAULT_AVAILABILITY = availability_from_settings(None)

_runtime_availability: ProviderAvailability = DEFAULT_AVAILABILITY


def set_runtime_availability(availability: ProviderAvailability) -> None:
    """Record what this process can read. Called once, from application startup.

    A process that never calls this — a test, a script, the documentation generator —
    keeps :data:`DEFAULT_AVAILABILITY`, which is the honest answer for a process with no
    configured third-party feeds.
    """

    global _runtime_availability
    _runtime_availability = availability


def runtime_availability() -> ProviderAvailability:
    return _runtime_availability
