"""The words a coin's public Passport opens with, and the other Passports it links to.

**One template for every coin.** `/passports/<coin>` is one Jinja page for all of them;
this module is where its search-facing words are made — the label above the heading, the
heading itself, the one-line answer under it, the browser title and the search-result
description. Each is built from the Passport record the page shows, never written per
coin, so 180 Passports cannot drift into 180 hand-edited pages, and no coin can say
something its own record does not.

The question in the heading is the one people type ("Is Bitcoin halal?"). The answer
under it is never a ruling. It names the one Shariah standard the page is showing, that
standard's result and its version, and says plainly that it is a screening result under
that standard, not a universal religious ruling. A coin reviewed by several standards
gets a different answer for each, because each standard reaches its own result and
nothing is averaged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from ai_market_monitor.core.asset_kinds import KIND_NOT_RECORDED
from ai_market_monitor.core.dashboard_paths import passport_path
from ai_market_monitor.schemas.sharia import AssetPassportResponse
from ai_market_monitor.services.hilal_methodology import is_automated

__all__ = [
    "RELATED_PASSPORT_COUNT",
    "PassportHeadline",
    "PublicPassport",
    "passport_headline",
    "related_passports",
    "standard_name",
]

#: How many other Passports a Passport links to.
RELATED_PASSPORT_COUNT: Final[int] = 6

#: Said under every answer, word for word, on every coin and every standard.
_NOT_A_RULING: Final[str] = (
    "This is a methodology-specific screening result, not a universal religious ruling."
)


def standard_name(name: str, version: str, code: str) -> str:
    """A Shariah standard as a reader sees it: its name and version.

    The machine-made standard says so in its own name, so nobody reads its result
    believing a Shariah board decided it. The standard picker and the answer under the
    heading both use this, so the two always name the standard the same way.
    """

    kind = " (automated, no Shariah advisor)" if is_automated(code) else ""
    return f"{name} v{version}{kind}"


@dataclass(frozen=True, slots=True)
class PassportHeadline:
    """Everything at the top of one Passport, and its title and description."""

    #: The coin's name ("Bitcoin"), or its symbol when no name is recorded.
    name: str
    #: The coin's symbol ("BTC").
    symbol: str
    #: "Bitcoin (BTC)", or just "XRP" when the name and the symbol are the same word.
    coin: str
    #: The small label above the heading: "BTC Evidence Passport".
    eyebrow: str
    #: The heading, as a reader would ask it: "Is Bitcoin (BTC) Halal?"
    question: str
    #: "BTC · Bitcoin · Native coin": the symbol, the network and the kind of coin.
    identity_line: str
    #: The standard being shown, as the picker names it.
    standard: str
    #: The one-line answer under the heading, for the standard being shown.
    answer: str
    #: The browser title, without the site name the page adds after it.
    page_title: str
    #: The description a search result shows under the title.
    description: str
    #: The printable report's title.
    report_title: str


def passport_headline(passport: AssetPassportResponse) -> PassportHeadline:
    """The words at the top of this Passport, from this Passport's own record."""

    assessment = passport.assessment
    identity = passport.identity
    symbol = identity.symbol if identity else assessment.canonical_asset
    name = (identity.name if identity else assessment.asset_name) or symbol
    coin = symbol if name.casefold() == symbol.casefold() else f"{name} ({symbol})"
    standard = standard_name(
        assessment.methodology_name, assessment.methodology_version, assessment.methodology_code
    )
    status = assessment.status_label
    if passport.historical.is_historical:
        # An older version is read as what it said then. "Currently" would be untrue.
        verdict = f"Under {standard}, this older record classified {name} as {status}."
    elif passport.main_qualification:
        verdict = (
            f"Under {standard}, {name} is currently classified as {status}, "
            "with the condition shown below."
        )
    else:
        verdict = f"Under {standard}, {name} is currently classified as {status}."
    identity_parts = [symbol]
    if identity and identity.network_label:
        identity_parts.append(identity.network_label)
    identity_parts.append(identity.kind_label if identity else KIND_NOT_RECORDED)
    return PassportHeadline(
        name=name,
        symbol=symbol,
        coin=coin,
        eyebrow=f"{symbol} Evidence Passport",
        question=f"Is {coin} Halal?",
        identity_line=" · ".join(identity_parts),
        standard=standard,
        answer=f"{verdict} {_NOT_A_RULING}",
        page_title=f"Is {name} Halal? {symbol} Shariah Screening & Evidence",
        description=(
            f"{coin} is classified as {status} under {standard}. Read the reasons, "
            "the sources and the evidence dates in its Hilal Markets Evidence Passport."
        ),
        report_title=f"{coin} Evidence report",
    )


@dataclass(frozen=True, slots=True)
class PublicPassport:
    """One coin with a public Passport, as another Passport links to it."""

    symbol: str
    name: str
    #: "Native coin", "Token" or "Network" (`core/asset_kinds.py`).
    kind: str
    #: The network's name, or None when none is recorded.
    network: str | None

    @property
    def coin(self) -> str:
        if self.name.casefold() == self.symbol.casefold():
            return self.symbol
        return f"{self.name} ({self.symbol})"

    @property
    def path(self) -> str:
        return passport_path(self.symbol)


def related_passports(
    symbol: str,
    passports: Sequence[PublicPassport],
    *,
    limit: int = RELATED_PASSPORT_COUNT,
) -> list[PublicPassport]:
    """The other Passports this coin's page links to.

    Closest first: the same kind of coin on the same network (USDC → other Ethereum
    tokens), then the same kind of coin (BTC → other native coins), then any other. Inside
    each group the list starts just after this coin in alphabetical order and wraps
    round, so every Passport is linked from its neighbours and none is left with no link
    pointing at it. The same coin always gets the same links.
    """

    ordered = sorted(passports, key=lambda item: item.symbol)
    me = next((item for item in ordered if item.symbol.casefold() == symbol.casefold()), None)
    after = [item for item in ordered if item.symbol.casefold() > symbol.casefold()]
    before = [
        item
        for item in ordered
        if item.symbol.casefold() < symbol.casefold()
    ]
    rotated = after + before

    def closeness(item: PublicPassport) -> int:
        if me is None:
            return 0
        if item.kind != me.kind:
            return 2
        if me.network and item.network == me.network:
            return 0
        return 1

    return sorted(rotated, key=closeness)[:limit]
