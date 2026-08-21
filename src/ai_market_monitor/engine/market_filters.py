from datetime import datetime

from ai_market_monitor.engine.models import MarketFilterResult, MarketSnapshot
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import Candle

STABLECOIN_BASES = {
    "USDC",
    "USDT",
    "FDUSD",
    "TUSD",
    "DAI",
    "USDP",
    "PYUSD",
    "BUSD",
}
LEVERAGED_TOKEN_MARKERS = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")


def is_stablecoin_base(base_asset: str) -> bool:
    """Is this coin a stablecoin? One owner, because two lists disagreed.

    The Cockpit's own preview of "which coins will this monitor watch" carried a second,
    shorter list. A coin the scanner excluded could therefore be shown to the owner as
    included, which is a promise the product then broke on every scan.
    """

    return base_asset.upper() in STABLECOIN_BASES


def is_leveraged_token(base_asset: str) -> bool:
    """Is this a leveraged token — a 3x or 5x product rather than the coin itself?

    Matters twice over here: leverage is outside what this product covers at all, and
    the Cockpit's copy of this test knew only six of the eight markers. It missed ``5L``
    and ``5S``, so five-times leveraged tokens were filtered out by the scanner while
    the screen that explains the filtering said they were kept.
    """

    return any(base_asset.upper().endswith(marker) for marker in LEVERAGED_TOKEN_MARKERS)


def base_asset_of(market: MarketSnapshot) -> str:
    """The coin being watched, however the snapshot happens to spell it."""

    return (market.base_asset or market.symbol.upper().split("/")[0]).upper()


def listing_age_days(market: MarketSnapshot, evaluation_time: datetime) -> float | None:
    """How many days this market has existed, or ``None`` when nobody recorded it.

    ``None`` is never turned into a number. A market whose listing date is unknown is
    unknown, and a rule about listing age must say so rather than treat "not recorded"
    as "old enough".
    """

    if market.listed_at is None:
        return None
    return (evaluation_time - market.listed_at).total_seconds() / 86400


class MarketFilterEngine:
    def evaluate(
        self,
        strategy: StrategyDefinition,
        market: MarketSnapshot,
        candle_sets: dict[str, list[Candle]],
        evaluation_time: datetime,
    ) -> MarketFilterResult:
        reasons: list[str] = []
        universe = strategy.universe
        symbol = market.symbol.upper()
        quote = market.quote_asset.upper()
        base = base_asset_of(market)
        if market.exchange.lower() != universe.exchange.lower():
            reasons.append("exchange_not_selected")
        if quote not in {item.upper() for item in universe.quote_currencies}:
            reasons.append("quote_asset_not_selected")
        if universe.include_symbols and symbol not in {
            item.upper() for item in universe.include_symbols
        }:
            reasons.append("symbol_not_in_allowlist")
        if symbol in {item.upper() for item in universe.exclude_symbols}:
            reasons.append("symbol_blocklisted")
        if universe.exclude_stablecoins and is_stablecoin_base(base):
            reasons.append("stablecoin_base_excluded")
        if universe.exclude_leveraged_tokens and is_leveraged_token(base):
            reasons.append("leveraged_token_excluded")
        if universe.min_quote_volume_24h is not None and (
            market.quote_volume_24h is None
            or market.quote_volume_24h < universe.min_quote_volume_24h
        ):
            reasons.append("quote_volume_below_minimum")
        if universe.min_average_candle_volume is not None and (
            market.average_candle_volume is None
            or market.average_candle_volume < universe.min_average_candle_volume
        ):
            reasons.append("average_candle_volume_below_minimum")
        if (
            universe.max_spread_bps is not None
            and market.spread_bps is not None
            and market.spread_bps > universe.max_spread_bps
        ):
            reasons.append("spread_above_maximum")
        if universe.min_listing_age_days is not None:
            age_days = listing_age_days(market, evaluation_time)
            if age_days is None:
                reasons.append("listing_age_unavailable")
            elif age_days < universe.min_listing_age_days:
                reasons.append("listing_too_new")
        if universe.min_market_cap is not None and (
            market.market_cap is None or market.market_cap < universe.min_market_cap
        ):
            reasons.append("market_cap_below_minimum")
        if not market.exchange_available:
            reasons.append("exchange_unavailable")
        if not market.data_quality_ok:
            reasons.append("data_quality_failed")
        base_history = candle_sets.get(strategy.base_timeframe, [])
        if len(base_history) < universe.min_historical_candles:
            reasons.append("insufficient_historical_candles")
        metrics = {
            "quote_volume_24h": market.quote_volume_24h,
            "average_candle_volume": market.average_candle_volume,
            "spread_bps": market.spread_bps,
            "market_cap": market.market_cap,
            "historical_candles": len(base_history),
        }
        return MarketFilterResult(passed=not reasons, reasons=reasons, metrics=metrics)
