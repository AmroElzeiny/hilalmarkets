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
        base = (market.base_asset or symbol.split("/")[0]).upper()
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
        if universe.exclude_stablecoins and base in STABLECOIN_BASES:
            reasons.append("stablecoin_base_excluded")
        if universe.exclude_leveraged_tokens and self._is_leveraged_token(base):
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
            if market.listed_at is None:
                reasons.append("listing_age_unavailable")
            else:
                age_days = (evaluation_time - market.listed_at).total_seconds() / 86400
                if age_days < universe.min_listing_age_days:
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

    @staticmethod
    def _is_leveraged_token(base_asset: str) -> bool:
        return any(base_asset.endswith(marker) for marker in LEVERAGED_TOKEN_MARKERS)
