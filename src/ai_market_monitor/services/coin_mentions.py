"""Which coin a person named, read from this platform's own listings.

One owner for the question "is this word a coin, and which one?". It used to have two
answers. Hilal's knowledge reader matched a message against every listing — symbol,
name and exchange pair — while the public assistant's Passport lookup kept a regex and a
hand-written table of three nicknames (bitcoin, ethereum, solana). The same question,
"is chainlink halal?", found LINK in the dashboard and found nothing on the public site,
because the public copy only knew the three names somebody had typed in.

So the reading lives here, and both assistants import it:

* :func:`spelling_keys` — every mechanical spelling of one name.
* :func:`names_in` — every word or two-word phrase that might be a coin, in order.
* :func:`tickers_in` — the words a person *marked* as a coin symbol (``$LTC``, ``LTC``).
* :class:`CoinListingIndex` — every spelling of every listed coin, from the listings.

Nothing here is a nickname table. "bitcoin" resolves to BTC because a row says the
symbol BTC is named Bitcoin, and the only spellings written here are mechanical — case,
``$``, punctuation and filler words.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import AssetShariaAssessment, CanonicalAsset, ExchangeMarket

#: Words that are part of how people say a coin's name rather than part of the name.
#: Removing them is spelling, not vocabulary — "the bitcoin coin" and "Bitcoin" are the
#: same listing, and no table of nicknames is needed to know it.
_NOISE = re.compile(
    r"\b(coin|coins|token|tokens|crypto|cryptocurrency|the|a|an|is|are|about|status)\b",
    re.IGNORECASE,
)
_PUNCTUATION = re.compile(r"[^a-z0-9\s]+")

#: One word as a person wrote it, keeping the marks that say "this is a ticker".
#:
#: A leading ``$`` and an inner ``/`` are both part of how a coin is written — ``$LTC``
#: and ``LTC/USDT`` — so both stay attached to the word rather than splitting it in two.
_TOKEN = re.compile(r"\$?[A-Za-z0-9][A-Za-z0-9/._-]*")


def spelling_keys(value: str) -> set[str]:
    """Every mechanical spelling of one name, for matching what a person typed.

    Mechanical only: lower case, no ``$``, no punctuation, no filler words. This does
    not know that "the king" means Bitcoin, and deliberately so — that would be an
    opinion, and opinions about what a coin is called belong in the listing.
    """

    lowered = _PUNCTUATION.sub(" ", value.lower())
    squeezed = " ".join(lowered.split())
    if not squeezed:
        return set()
    keys = {squeezed}
    without_noise = " ".join(_NOISE.sub(" ", squeezed).split())
    if without_noise:
        keys.add(without_noise)
    keys.add(squeezed.replace(" ", ""))
    return {key for key in keys if key}


def names_in(message: str) -> list[str]:
    """The words in a message that might be a coin, longest phrase first.

    Generous on purpose. Anything that turns out not to be a listing simply finds
    nothing, and finding nothing is itself reported — so a wide net costs a lookup,
    while a narrow one costs a wrong answer.

    Ordered, not a set. The order decides which coins survive a cap on how many may be
    looked up, and an unordered set made that a coin toss: the same question could
    gather a different coin on a second run. Two-word names come first, so
    "bitcoin cash" is preferred over the "bitcoin" inside it.

    Written as the person wrote it, not lowered. Lowering here threw away the two marks
    that say "I mean this as a ticker" — capitals and a leading ``$``. Matching does not
    care about case; :func:`spelling_keys` handles that further down.

    A pair with a filler word in it is not a phrase. "is bitcoin" loses "is" to
    :func:`spelling_keys` and becomes "bitcoin" — so in "is bitcoin cash halal" it was
    read as Bitcoin *before* "bitcoin cash" was tried, and the two-word name lost to the
    word inside it. The single word is still read on its own, after every real phrase.
    """

    words = [token for token in _TOKEN.findall(message) if 2 <= len(token.lstrip("$")) <= 24]
    pairs = [
        f"{first} {second}"
        for first, second in zip(words, words[1:], strict=False)
        if not _NOISE.fullmatch(first) and not _NOISE.fullmatch(second)
    ]
    ordered: list[str] = []
    for candidate in [*pairs, *words]:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def tickers_in(message: str) -> list[str]:
    """The words a person meant as a coin symbol, rather than as English.

    One owner for that judgement, because it decides what may be reported as "not listed
    here" and what the public assistant treats as a question about one coin — and
    treating every ordinary word as a coin would bury the one that mattered.

    A message written entirely in capitals is not a message full of tickers. "IS BTC
    HALAL" would otherwise announce that IS and HALAL are coins.
    """

    shouting = message == message.upper()
    found: list[str] = []
    for token in _TOKEN.findall(message):
        # Judged on the coin half. "LTC/USDT" is as deliberate a way of naming a coin
        # as "LTC" is, and reading the whole pair as one word made it neither
        # alphanumeric nor short enough to count.
        base = token.lstrip("$").partition("/")[0]
        if not base or len(base) > 12:
            continue
        marked = token.startswith("$")
        capitals = base.isupper() and base.isalnum() and len(base) >= 3 and not shouting
        if (marked or capitals) and token not in found:
            found.append(token)
    return found


class CoinListingIndex:
    """Every spelling of every listed coin, pointing at its symbol.

    Built from the listings themselves. All three tables are read:

    * ``CanonicalAsset`` — the identity, its symbol and its name;
    * ``AssetShariaAssessment`` — a coin can be reviewed before it has an identity row,
      and a person asking about it should get its recorded status rather than "not
      listed";
    * ``ExchangeMarket`` — the market symbols this platform actually covers.

    The third one is the answer to somebody typing **LTCUSDT**. A trader reads a pair
    off a chart and types it whole. No list of quote currencies is written anywhere for
    this: there is a row saying the market ``LTC/USDT`` exists, and the mechanical
    spellings of that row already include ``ltcusdt``.
    """

    def __init__(self, keys: dict[str, str]) -> None:
        self._keys = keys

    @classmethod
    def build(
        cls,
        identities: list[tuple[str, str | None]],
        markets: list[tuple[str, str]] | None = None,
    ) -> CoinListingIndex:
        """The index from listing rows: ``(symbol, name)`` pairs, then ``(pair, symbol)``.

        Market pairs are added last, so a market symbol can never take a key that a
        coin's own symbol or name already owns.
        """

        index: dict[str, str] = {}
        for symbol, name in identities:
            for key in spelling_keys(symbol):
                index.setdefault(key, symbol)
            if name:
                for key in spelling_keys(name):
                    index.setdefault(key, symbol)
        for market_symbol, symbol in markets or []:
            if not market_symbol:
                continue
            for key in spelling_keys(str(market_symbol)):
                index.setdefault(key, symbol)
        return cls(index)

    @classmethod
    async def load(cls, session: AsyncSession) -> CoinListingIndex:
        identities: list[tuple[str, str | None]] = []
        for symbol, name in (
            await session.execute(select(CanonicalAsset.symbol, CanonicalAsset.name))
        ).all():
            identities.append((str(symbol), name))
        for symbol, name in (
            await session.execute(
                select(
                    AssetShariaAssessment.canonical_asset,
                    AssetShariaAssessment.asset_name,
                ).distinct()
            )
        ).all():
            identities.append((str(symbol), name))
        markets: list[tuple[str, str]] = []
        for market, symbol in (
            await session.execute(
                select(ExchangeMarket.market_symbol, CanonicalAsset.symbol)
                .join(CanonicalAsset, CanonicalAsset.id == ExchangeMarket.canonical_asset_id)
                .where(ExchangeMarket.is_active.is_(True))
                .distinct()
            )
        ).all():
            if market:
                markets.append((str(market), str(symbol)))
        return cls.build(identities, markets)

    def get(self, key: str) -> str | None:
        return self._keys.get(key)

    def symbol_for(self, text: str) -> str | None:
        """The listed coin one word or phrase names, or ``None``."""

        for key in spelling_keys(text):
            symbol = self._keys.get(key)
            if symbol:
                return symbol
        return None

    def symbols_in(self, texts: list[str]) -> list[str]:
        """Every listed coin named by these words, in the order they were given."""

        symbols: list[str] = []
        for item in texts:
            for key in spelling_keys(item):
                symbol = self._keys.get(key)
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        return symbols

    def asked_about(self, message: str) -> list[str]:
        """The coins one message is about, best evidence first.

        A coin the person *marked* as a symbol (``$LINK``, ``LINK``) is the strongest
        evidence there is, and when there is one it is the answer on its own: "Is BTC
        not allowed?" is a question about BTC, not also about a coin whose symbol
        happens to be the English word "not". Only a message with no marked symbol is
        read word by word, phrases first.
        """

        marked = self.symbols_in(tickers_in(message))
        if marked:
            return marked
        # A word that is part of a matched two-word name is not read again on its own:
        # "bitcoin cash" is one coin, not Bitcoin Cash and also Bitcoin.
        found: list[str] = []
        inside_a_name: set[str] = set()
        for candidate in names_in(message):
            symbol = self.symbol_for(candidate)
            if symbol is None:
                continue
            parts = candidate.lower().split(" ")
            if len(parts) == 1 and parts[0] in inside_a_name:
                continue
            if len(parts) > 1:
                inside_a_name.update(parts)
            if symbol not in found:
                found.append(symbol)
        return found
