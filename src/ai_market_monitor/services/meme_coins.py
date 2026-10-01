"""Which coins are meme coins, for the Hilal Markets Methodology. One owner.

**The rule.** The Hilal Markets Methodology does not cover meme coins: every meme coin is
*not suitable* under it, whatever else its pages say. The product owner decided this on
1 October 2026. It is approved condition ``WG-01`` ("there is no protocol, product or
service behind the token"), whose own register entry already names a meme coin as what
it looks like, and which until now had no way of being established at all.

**Three ways a coin is known to be one. Any one is enough.**

``OWNER_LIST``
    The coin is on ``hilal_methodology_meme_coins.json``, a file a person edits and
    commits — the same kind of act as ``sharia_condition_decisions.json``.
``PROVIDER_TAG``
    CoinMarketCap files the coin under a meme tag. CoinMarketCap's own words for it are
    ``memes`` and tags ending in ``-memes`` (``animal-memes``, ``political-memes``).
``SELF_DESCRIBED``
    The project calls itself a meme coin on its own pages. That is condition ``WG-07``
    in the register, read by the same page reader as every other condition, so a denial
    ("not just a meme coin") or a sentence about somebody else's coin does not count.
    It is not decided here; this module only names its code.

**What this module refuses to do.** It never guesses a coin is a meme from its name, its
price or its age. A label nobody wrote down is not a label.

Every place that needs to know "is this a meme" asks here: the automated screen
(:func:`sharia_evidence_screen.decide`), the published coin list
(:mod:`hilal_methodology`), and the market evaluator's "no meme coins" card. Three
separate word lists for one idea is the failure this codebase keeps paying for.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import Any

#: The file a person edits to name a meme coin. Beside this module.
MEME_LIST_FILE = "hilal_methodology_meme_coins.json"

#: The approved condition a meme coin is refused under. It names the meme coin as what
#: it looks like, and it is the one the published record cites.
MEME_CONDITION = "WG-01"

#: The text condition for a project that calls itself a meme coin. Lives in the
#: register with its phrases; named here so a reader can follow the rule end to end.
SELF_DESCRIBED_CONDITION = "WG-07"

#: Where CoinMarketCap shows a coin's tags, for the source of a provider-tag finding.
PROVIDER_PAGE = "https://coinmarketcap.com/currencies/{slug}/"

#: The plain sentence shown wherever this rule refuses a coin.
MEME_RULE_SENTENCE = (
    "The Hilal Markets Methodology does not cover meme coins, so every meme coin is "
    "marked not suitable under it."
)


class MemeSignal(StrEnum):
    """How a coin came to be known as a meme coin. Never inferred."""

    OWNER_LIST = "owner_list"
    PROVIDER_TAG = "provider_tag"
    SELF_DESCRIBED = "self_described"


#: A label that means "meme coin", as a whole word inside a tag or category.
#: Whole pieces only. CoinMarketCap tags are slugs (``animal-memes``) and categories are
#: words (``Meme coin``); both are split on anything that is not a letter or a digit, and
#: one of the pieces must be exactly one of these words. A slug that merely contains the
#: letters, such as ``memento``, never counts.
MEME_WORDS: frozenset[str] = frozenset({"meme", "memes", "memecoin", "memecoins"})

_SPLIT = re.compile(r"[^a-z0-9]+")


def is_meme_label(value: str | None) -> bool:
    """Whether one tag or category says "meme coin". The single reader of that word."""

    text = (value or "").strip().casefold()
    if not text:
        return False
    return any(piece in MEME_WORDS for piece in _SPLIT.split(text) if piece)


def meme_labels(tags: Iterable[str] | None, category: str | None = None) -> tuple[str, ...]:
    """The provider labels, of those given, that say "meme coin"."""

    found = [item for item in (tags or ()) if is_meme_label(item)]
    if is_meme_label(category):
        found.append(str(category))
    return tuple(dict.fromkeys(item.strip() for item in found))


class MemeListError(RuntimeError):
    """The meme list is missing or malformed. Raised before anything is decided."""


@dataclass(frozen=True, slots=True)
class ListedMeme:
    """One coin a person put on the meme list, and why."""

    symbol: str
    name: str
    decided_on: date
    why: str
    source_url: str


@lru_cache(maxsize=1)
def owner_list() -> dict[str, ListedMeme]:
    """Every coin on the meme list, by symbol. Read once per process, like the register."""

    try:
        raw = (
            resources.files("ai_market_monitor.services")
            .joinpath(MEME_LIST_FILE)
            .read_text(encoding="utf-8")
        )
        payload: Any = json.loads(raw)
    except (OSError, ModuleNotFoundError, json.JSONDecodeError) as exc:
        raise MemeListError(f"{MEME_LIST_FILE} could not be read.") from exc
    rows = payload.get("coins") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise MemeListError(f"{MEME_LIST_FILE} must hold a list under 'coins'.")
    listed: dict[str, ListedMeme] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise MemeListError("A meme list row is not an object.")
        symbol = str(row.get("symbol") or "").strip().upper()
        why = str(row.get("why") or "").strip()
        url = str(row.get("source_url") or "").strip()
        if not symbol or not why or not url.startswith("https://"):
            raise MemeListError(
                f"{symbol or 'A row'}: every listed coin needs a symbol, a reason and a "
                "source a reader can open."
            )
        if symbol in listed:
            raise MemeListError(f"{symbol} is on the meme list twice.")
        try:
            decided = date.fromisoformat(str(row.get("decided_on")))
        except ValueError as exc:
            raise MemeListError(f"{symbol}: decided_on is not a date.") from exc
        listed[symbol] = ListedMeme(
            symbol=symbol,
            name=str(row.get("name") or symbol).strip(),
            decided_on=decided,
            why=why,
            source_url=url,
        )
    return listed


def is_listed(symbol: str | None) -> bool:
    return (symbol or "").strip().upper() in owner_list()


@dataclass(frozen=True, slots=True)
class MemeFinding:
    """Why one coin is a meme coin, in a sentence and a link a reader can check."""

    symbol: str
    signal: MemeSignal
    reason: str
    source_url: str
    labels: tuple[str, ...] = ()

    @property
    def condition(self) -> str:
        return (
            SELF_DESCRIBED_CONDITION
            if self.signal is MemeSignal.SELF_DESCRIBED
            else MEME_CONDITION
        )


def meme_finding(
    symbol: str,
    *,
    tags: Iterable[str] | None = None,
    category: str | None = None,
    provider_slug: str | None = None,
) -> MemeFinding | None:
    """Whether this coin is known to be a meme coin from a list or a provider label.

    The third signal — the project calling itself one — is read from its pages by the
    evidence screen, as condition :data:`SELF_DESCRIBED_CONDITION`, and is not decided
    here.
    """

    wanted = (symbol or "").strip().upper()
    listed = owner_list().get(wanted)
    if listed is not None:
        return MemeFinding(
            symbol=wanted,
            signal=MemeSignal.OWNER_LIST,
            reason=f"{MEME_RULE_SENTENCE} Hilal Markets lists {listed.name} as a meme "
            f"coin: {listed.why}",
            source_url=listed.source_url,
        )
    labels = meme_labels(tags, category)
    if labels:
        slug = (provider_slug or "").strip().lower()
        return MemeFinding(
            symbol=wanted,
            signal=MemeSignal.PROVIDER_TAG,
            reason=(
                f"{MEME_RULE_SENTENCE} CoinMarketCap files {wanted} under "
                + ", ".join(f"'{item}'" for item in labels)
                + "."
            ),
            source_url=PROVIDER_PAGE.format(slug=slug)
            if slug
            else "https://coinmarketcap.com/view/memes/",
            labels=labels,
        )
    return None


__all__ = [
    "MEME_CONDITION",
    "MEME_LIST_FILE",
    "MEME_RULE_SENTENCE",
    "MEME_WORDS",
    "SELF_DESCRIBED_CONDITION",
    "ListedMeme",
    "MemeFinding",
    "MemeListError",
    "MemeSignal",
    "is_listed",
    "is_meme_label",
    "meme_finding",
    "meme_labels",
    "owner_list",
]
