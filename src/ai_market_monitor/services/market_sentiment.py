"""Market-wide readings the product did not have before: mood, breadth, dominance.

Everything already in the product answers a question about **one** coin. These answer a
question about the market as a whole — how fearful it is, how much of it is Bitcoin,
how much money moved today. A beginner watching one coin fall has no way to tell
whether that coin is in trouble or whether everything fell together, and that is the
difference between a reason to worry and a reason to ignore it.

**None of this is advice, and none of it is a signal.** A Fear & Greed reading is a
description of what other people are doing. It is shown with its plain meaning attached
and it never appears as a suggestion to buy, sell, or wait.

**It is also not a Shariah input.** No number here touches eligibility. A coin's status
comes from an authority's assessment and from nowhere else.

Cached deliberately. These readings change slowly and the whole market shares one
answer, so one call every few minutes serves every reader — which is what keeps a
page-load off the credit budget entirely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.coinmarketcap import (
    CoinMarketCapClient,
    CoinMarketCapError,
)

logger = logging.getLogger(__name__)

#: The provider publishes a number and its own one-word label. The plain sentence is
#: ours, written for somebody who has never seen the index before, and it lives here
#: with the bands rather than in a template so every surface says the same thing.
FEAR_AND_GREED_BANDS: tuple[tuple[int, str, str], ...] = (
    (
        24,
        "Extreme fear",
        "Most people are selling or sitting out. Prices often move sharply.",
    ),
    (
        44,
        "Fear",
        "More people are worried than confident right now.",
    ),
    (
        55,
        "Neutral",
        "Buying and selling are roughly balanced.",
    ),
    (
        74,
        "Greed",
        "More people are buying than usual. Prices can be above their calm level.",
    ),
    (
        100,
        "Extreme greed",
        "Buying is very heavy. Sharp falls are more common after readings like this.",
    ),
)


def describe_fear_and_greed(value: int | None) -> tuple[str, str]:
    """The label and the plain sentence for a reading. One owner for both."""

    if value is None:
        return ("Not available", "This reading could not be fetched.")
    for ceiling, label, meaning in FEAR_AND_GREED_BANDS:
        if value <= ceiling:
            return (label, meaning)
    return FEAR_AND_GREED_BANDS[-1][1], FEAR_AND_GREED_BANDS[-1][2]


@dataclass(frozen=True, slots=True)
class MarketSentiment:
    """One reading of the whole market. Every field may be ``None``."""

    fear_greed_value: int | None = None
    fear_greed_label: str = "Not available"
    fear_greed_meaning: str = "This reading could not be fetched."
    btc_dominance: float | None = None
    eth_dominance: float | None = None
    total_market_cap_usd: float | None = None
    total_volume_24h_usd: float | None = None
    active_cryptocurrencies: int | None = None
    taken_at: datetime | None = None
    available: bool = False
    #: Said on the page. The reading is somebody else's measurement, not ours.
    attribution: str = "Market-wide readings from CoinMarketCap."

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "fear_greed": {
                "value": self.fear_greed_value,
                "label": self.fear_greed_label,
                "meaning": self.fear_greed_meaning,
            },
            "btc_dominance": self.btc_dominance,
            "eth_dominance": self.eth_dominance,
            "total_market_cap_usd": self.total_market_cap_usd,
            "total_volume_24h_usd": self.total_volume_24h_usd,
            "active_cryptocurrencies": self.active_cryptocurrencies,
            "taken_at": self.taken_at.isoformat() if self.taken_at else None,
            "attribution": self.attribution,
        }


class MarketSentimentService:
    """Reads the market-wide numbers, cached, and never fails a page for them."""

    #: Process-wide, because every reader shares one answer.
    _cache: MarketSentiment | None = None
    _cached_at: datetime | None = None

    def __init__(self, settings: Settings, *, client: CoinMarketCapClient | None = None):
        self.settings = settings
        self.client = client or CoinMarketCapClient(settings)

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache = None
        cls._cached_at = None

    def _fresh_enough(self, now: datetime) -> bool:
        if MarketSentimentService._cache is None or MarketSentimentService._cached_at is None:
            return False
        age = now - MarketSentimentService._cached_at
        return age < timedelta(seconds=self.settings.coinmarketcap_quote_cache_seconds)

    async def read(self) -> MarketSentiment:
        """The current reading, or an unavailable one. Never raises.

        A market page must render when a data provider is down. An unavailable reading
        is shown as unavailable — it is never filled in with a stale number presented
        as current, and never with a guess.
        """

        now = datetime.now(UTC)
        if self._fresh_enough(now):
            cached = MarketSentimentService._cache
            if cached is not None:
                return cached
        if not self.client.enabled:
            return MarketSentiment(taken_at=now, available=False)

        value: int | None = None
        globals_payload: dict[str, Any] = {}
        try:
            reading = await self.client.fear_and_greed()
            raw = reading.get("value")
            value = int(raw) if isinstance(raw, int | float) else None
        except (CoinMarketCapError, ValueError, TypeError):
            logger.info("market_sentiment_fear_greed_unavailable")
        try:
            globals_payload = await self.client.global_metrics()
        except CoinMarketCapError:
            logger.info("market_sentiment_globals_unavailable")

        label, meaning = describe_fear_and_greed(value)
        sentiment = MarketSentiment(
            fear_greed_value=value,
            fear_greed_label=label,
            fear_greed_meaning=meaning,
            btc_dominance=_float(globals_payload.get("btc_dominance")),
            eth_dominance=_float(globals_payload.get("eth_dominance")),
            total_market_cap_usd=_float(globals_payload.get("total_market_cap")),
            total_volume_24h_usd=_float(globals_payload.get("total_volume_24h")),
            active_cryptocurrencies=(
                int(globals_payload["active_cryptocurrencies"])
                if isinstance(globals_payload.get("active_cryptocurrencies"), int)
                else None
            ),
            taken_at=now,
            available=value is not None or bool(globals_payload),
        )
        if sentiment.available:
            MarketSentimentService._cache = sentiment
            MarketSentimentService._cached_at = now
        return sentiment


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


__all__ = [
    "FEAR_AND_GREED_BANDS",
    "MarketSentiment",
    "MarketSentimentService",
    "describe_fear_and_greed",
]
