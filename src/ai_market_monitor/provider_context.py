from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta
from statistics import fmean, pstdev
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.engine.indicators import IndicatorWarmupError, adx, ema
from ai_market_monitor.engine.models import ensure_aware
from ai_market_monitor.schemas.strategy import (
    ConditionGroup,
    ConditionRule,
    OperandKind,
    StrategyDefinition,
)
from ai_market_monitor.services.interfaces import Candle, MarketDataProvider

EXTERNAL_CONTEXT_CATEGORIES = {
    "crypto_index",
    "macro_market",
    "event_feed",
    "token_categories",
}
PUBLIC_CONTEXT_CATEGORIES = {
    "cross_market",
    "market_breadth",
    "order_book",
    "universe_ranking",
    "derivatives",
}


class ProviderContextService:
    """Builds deterministic condition context without inventing missing values."""

    def __init__(
        self,
        provider: MarketDataProvider,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or get_settings()
        self.transport = transport
        self._cache: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._universe_cache: dict[tuple[Any, ...], dict[str, dict[str, float]]] = {}

    async def build(
        self,
        definition: StrategyDefinition,
        symbol: str,
        candle_sets: dict[str, list[Candle]],
        evaluated_at: datetime,
        *,
        base_context: dict[str, Any] | None = None,
        universe_symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        context = dict(base_context or {})
        requests = requested_context_operands(definition)
        if not requests:
            return context
        evaluated_at = ensure_aware(evaluated_at)
        exchange = definition.universe.exchange
        timeframe = definition.base_timeframe

        if "cross_market" in requests:
            cross_timeframe = str(
                next(iter(requests["cross_market"].values())).get(
                    "_timeframe",
                    timeframe,
                )
            )
            context["cross_market"] = await self._cross_market(
                exchange,
                symbol,
                cross_timeframe,
                candle_sets.get(cross_timeframe, candle_sets.get(timeframe, [])),
                evaluated_at,
                requests["cross_market"],
            )
        if "market_breadth" in requests or "universe_ranking" in requests:
            symbols = universe_symbols or await self.provider.list_symbols(
                exchange,
                definition.universe.quote_currencies,
            )
            snapshot = await self._universe_snapshot(
                exchange,
                symbols,
                timeframe,
                evaluated_at,
            )
            if "market_breadth" in requests:
                context["market_breadth"] = self._breadth_values(
                    snapshot,
                    requests["market_breadth"],
                )
            if "universe_ranking" in requests:
                context["universe_ranking"] = self._ranking_values(
                    symbol,
                    snapshot,
                    requests["universe_ranking"],
                )
        if "order_book" in requests:
            context["order_book"] = await self._order_book(
                exchange,
                symbol,
                requests["order_book"],
            )
        if "derivatives" in requests:
            context["derivatives"] = await self._derivatives(
                exchange,
                symbol,
                timeframe,
                candle_sets.get(timeframe, []),
                evaluated_at,
                requests["derivatives"],
            )
        for category in EXTERNAL_CONTEXT_CATEGORIES & requests.keys():
            context[category] = await self._external(
                category,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                quote_assets=definition.universe.quote_currencies,
                evaluated_at=evaluated_at,
                requested_keys=list(requests[category]),
            )
        return context

    async def rank_symbols(
        self,
        definition: StrategyDefinition,
        symbols: list[str],
        evaluated_at: datetime,
    ) -> list[str]:
        requests = requested_context_operands(definition)
        ranking = requests.get("universe_ranking")
        if not ranking or len(symbols) < 2:
            return symbols
        snapshot = await self._universe_snapshot(
            definition.universe.exchange,
            symbols,
            definition.base_timeframe,
            ensure_aware(evaluated_at),
        )
        key = next(iter(ranking))
        metric_name, descending = _ranking_metric(key)
        return sorted(
            symbols,
            key=lambda item: (
                -snapshot.get(item, {}).get(metric_name, float("-inf"))
                if descending
                else snapshot.get(item, {}).get(metric_name, float("inf")),
                item,
            ),
        )

    async def _cross_market(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        symbol_candles: list[Candle],
        evaluated_at: datetime,
        requests: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        cache_key = (
            "cross_market",
            exchange,
            _symbol(symbol),
            timeframe,
            int(evaluated_at.timestamp())
            // max(60, int(timeframe_duration(timeframe).total_seconds())),
            tuple(sorted(requests)),
        )
        if cache_key in self._cache:
            return self._cache[cache_key]
        quote = symbol.partition("/")[2] or "USDT"
        btc_symbol = f"BTC/{quote}"
        eth_symbol = f"ETH/{quote}"
        btc, eth = await asyncio.gather(
            self._candles(exchange, btc_symbol, timeframe, evaluated_at, 240),
            self._candles(exchange, eth_symbol, timeframe, evaluated_at, 240),
        )
        values: dict[str, Any] = {}
        if btc:
            btc_return = _return_percent(btc, 24)
            values["btc_usdt_trend_filter"] = _trend_up(btc)
            values["btc_trend_filter"] = values["btc_usdt_trend_filter"]
        else:
            btc_return = None
        if eth:
            eth_return = _return_percent(eth, 24)
            values["eth_usdt_trend_filter"] = _trend_up(eth)
            values["eth_trend_filter"] = values["eth_usdt_trend_filter"]
        else:
            eth_return = None
        symbol_return = _return_percent(symbol_candles, 24) if symbol_candles else None
        if eth_return is not None and btc_return is not None:
            values["eth_btc_relative_strength"] = eth_return > btc_return
        if symbol_return is not None and btc_return is not None:
            values["symbol_outperforming_btc"] = symbol_return > btc_return
            values["symbol_underperforming_btc"] = symbol_return < btc_return
            values["pair_move_relative_btc"] = symbol_return - btc_return > 0
        if symbol_return is not None and eth_return is not None:
            values["symbol_outperforming_eth"] = symbol_return > eth_return
        pair_returns = _returns(symbol_candles[-100:])
        btc_returns = _returns(btc[-100:])
        aligned = min(len(pair_returns), len(btc_returns))
        if aligned >= 10:
            pair_sample = pair_returns[-aligned:]
            btc_sample = btc_returns[-aligned:]
            correlation = _correlation(pair_sample, btc_sample)
            beta = _beta(pair_sample, btc_sample)
            btc_volatility = pstdev(btc_sample)
            volatility_ratio = (
                pstdev(pair_sample) / btc_volatility if btc_volatility else None
            )
            values["pair_correlation_btc"] = abs(correlation) >= float(
                requests.get("pair_correlation_btc", {}).get("threshold", 0.7)
            )
            values["correlation_filter"] = values["pair_correlation_btc"]
            values["pair_beta_btc"] = beta >= float(
                requests.get("pair_beta_btc", {}).get("minimum_beta", 1)
            )
            if volatility_ratio is not None:
                values["pair_volatility_vs_btc"] = volatility_ratio >= float(
                    requests.get("pair_volatility_vs_btc", {}).get("minimum_ratio", 1)
                )
            values["_metrics"] = {
                "correlation_btc": correlation,
                "beta_btc": beta,
                "volatility_ratio_btc": volatility_ratio,
                "symbol_return_percent": symbol_return,
                "btc_return_percent": btc_return,
                "eth_return_percent": eth_return,
            }
        result = {
            key: value
            for key, value in values.items()
            if key in requests or key == "_metrics"
        }
        self._cache[cache_key] = result
        return result

    async def _universe_snapshot(
        self,
        exchange: str,
        symbols: list[str],
        timeframe: str,
        evaluated_at: datetime,
    ) -> dict[str, dict[str, float]]:
        normalized = sorted({_symbol(item) for item in symbols})[
            : self.settings.market_breadth_max_symbols
        ]
        bucket = int(evaluated_at.timestamp()) // max(
            60,
            int(timeframe_duration(timeframe).total_seconds()),
        )
        cache_key = (exchange, timeframe, bucket, tuple(normalized))
        if cache_key in self._universe_cache:
            return self._universe_cache[cache_key]
        metadata_loader = getattr(self.provider, "fetch_universe_metadata", None)
        metadata: dict[str, dict[str, Any]] = {}
        if callable(metadata_loader):
            try:
                metadata = await metadata_loader(exchange, normalized)
            except Exception:
                metadata = {}
        semaphore = asyncio.Semaphore(self.settings.context_fetch_concurrency)

        async def one(symbol: str) -> tuple[str, dict[str, float]]:
            async with semaphore:
                try:
                    candles = await self._candles(
                        exchange,
                        symbol,
                        timeframe,
                        evaluated_at,
                        220,
                    )
                    return symbol, _ranking_metrics(candles, metadata.get(symbol, {}))
                except Exception:
                    return symbol, {}

        snapshot = dict(await asyncio.gather(*(one(symbol) for symbol in normalized)))
        self._universe_cache[cache_key] = snapshot
        return snapshot

    @staticmethod
    def _breadth_values(
        snapshot: dict[str, dict[str, float]],
        requests: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        usable = [values for values in snapshot.values() if values]
        if not usable:
            return {}
        total = len(usable)
        percentages = {
            "universe_above_ema50_percent": _percent(
                sum(item.get("above_ema50", 0) > 0 for item in usable),
                total,
            ),
            "universe_above_ema200_percent": _percent(
                sum(item.get("above_ema200", 0) > 0 for item in usable),
                total,
            ),
            "universe_positive_24h_percent": _percent(
                sum(item.get("momentum_24h", 0) > 0 for item in usable),
                total,
            ),
            "universe_n_day_high_percent": _percent(
                sum(item.get("near_24h_high", 0) > 0 for item in usable),
                total,
            ),
            "universe_volume_spike_percent": _percent(
                sum(item.get("relative_volume", 0) >= 1.5 for item in usable),
                total,
            ),
        }
        result: dict[str, Any] = {}
        for key, parameters in requests.items():
            if key in percentages:
                threshold = float(parameters.get("threshold", 50))
                result[key] = percentages[key] >= threshold
        ema50 = percentages["universe_above_ema50_percent"]
        ema200 = percentages["universe_above_ema200_percent"]
        positive = percentages["universe_positive_24h_percent"]
        if "breadth_thrust" in requests:
            result["breadth_thrust"] = positive >= float(
                requests["breadth_thrust"].get("threshold", 65)
            ) and ema50 >= 60
        if "market_breadth_improving" in requests:
            result["market_breadth_improving"] = ema50 >= ema200 and positive >= 50
        if "market_breadth_deteriorating" in requests:
            result["market_breadth_deteriorating"] = ema200 > ema50 and positive < 50
        result["_metrics"] = percentages
        return result

    @staticmethod
    def _ranking_values(
        symbol: str,
        snapshot: dict[str, dict[str, float]],
        requests: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        symbol = _symbol(symbol)
        current = snapshot.get(symbol, {})
        if not current:
            return {}
        result: dict[str, Any] = {}
        for key, parameters in requests.items():
            metric, descending = _ranking_metric(key)
            values = [
                item[metric]
                for item in snapshot.values()
                if metric in item and math.isfinite(item[metric])
            ]
            actual = current.get(metric)
            if actual is None or not values:
                continue
            if key in {"near_24h_high", "near_24h_low"}:
                result[key] = bool(actual)
                continue
            percentile = max(1, min(100, float(parameters.get("percentile", 20))))
            ordered = sorted(values, reverse=descending)
            count = max(1, math.ceil(len(ordered) * percentile / 100))
            selected = ordered[:count]
            result[key] = actual >= min(selected) if descending else actual <= max(selected)
        result["_metrics"] = current
        return result

    async def _order_book(
        self,
        exchange: str,
        symbol: str,
        requests: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        loader = getattr(self.provider, "fetch_order_book_context", None)
        if not callable(loader):
            return {}
        try:
            raw = await loader(exchange, symbol, depth=100)
        except Exception:
            return {}
        result: dict[str, Any] = {}
        for key, parameters in requests.items():
            result[key] = _order_book_condition(key, raw, parameters)
        result["_metrics"] = raw
        return {key: value for key, value in result.items() if value is not None}

    async def _derivatives(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        spot_candles: list[Candle],
        evaluated_at: datetime,
        requests: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        loader = getattr(self.provider, "fetch_derivatives_context", None)
        if callable(loader):
            try:
                raw = await loader(exchange, symbol)
            except Exception:
                raw = {}
        external = await self._external(
            "derivatives",
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            quote_assets=[symbol.partition("/")[2] or "USDT"],
            evaluated_at=evaluated_at,
            requested_keys=list(requests),
        )
        raw.update({key: value for key, value in external.items() if key != "_metadata"})
        if spot_candles:
            raw.setdefault("price_change_percent", _return_percent(spot_candles, 2))
        result: dict[str, Any] = {}
        for key, parameters in requests.items():
            direct = raw.get(key)
            result[key] = (
                direct
                if isinstance(direct, bool)
                else _derivatives_condition(key, raw, parameters)
            )
        result["_metrics"] = raw
        return {key: value for key, value in result.items() if value is not None}

    async def _external(
        self,
        category: str,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        quote_assets: list[str],
        evaluated_at: datetime,
        requested_keys: list[str],
    ) -> dict[str, Any]:
        url, key = self._external_configuration(category)
        if url is None:
            return {}
        cache_key = (
            category,
            exchange,
            symbol,
            timeframe,
            int(evaluated_at.timestamp()) // 60,
            tuple(sorted(requested_keys)),
        )
        if cache_key in self._cache:
            return self._cache[cache_key]
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["Authorization"] = f"Bearer {key.get_secret_value()}"
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.context_provider_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    str(url),
                    headers=headers,
                    json={
                        "category": category,
                        "requested_keys": requested_keys,
                        "exchange": exchange,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "quote_assets": quote_assets,
                        "evaluated_at": evaluated_at.isoformat(),
                    },
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return {}
        values = payload.get("values", {}) if isinstance(payload, dict) else {}
        if not isinstance(values, dict):
            return {}
        result = {
            name: value
            for name, value in values.items()
            if name in requested_keys and isinstance(value, (bool, int, float, str))
        }
        result["_metadata"] = {
            "provider_category": category,
            "as_of": payload.get("as_of"),
        }
        self._cache[cache_key] = result
        return result

    def _external_configuration(self, category: str):
        mapping = {
            "crypto_index": (
                self.settings.crypto_index_api_url,
                self.settings.crypto_index_api_key,
            ),
            "macro_market": (
                self.settings.macro_market_api_url,
                self.settings.macro_market_api_key,
            ),
            "event_feed": (
                self.settings.event_feed_api_url,
                self.settings.event_feed_api_key,
            ),
            "token_categories": (
                self.settings.token_category_api_url,
                self.settings.token_category_api_key,
            ),
            "derivatives": (
                self.settings.derivatives_context_api_url,
                self.settings.derivatives_context_api_key,
            ),
        }
        return mapping.get(category, (None, None))

    async def _candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        evaluated_at: datetime,
        limit: int,
    ) -> list[Candle]:
        evaluated_at = ensure_aware(evaluated_at)
        range_loader = getattr(self.provider, "fetch_ohlcv_range", None)
        if callable(range_loader):
            start = evaluated_at - timeframe_duration(timeframe) * (limit + 5)
            candles = await range_loader(
                exchange,
                symbol,
                timeframe,
                start,
                evaluated_at,
                limit,
            )
        else:
            candles = await self.provider.fetch_ohlcv(exchange, symbol, timeframe, limit)
        return [
            candle
            for candle in candles
            if candle.is_closed and ensure_aware(candle.timestamp) <= evaluated_at
        ]


def requested_context_operands(
    definition: StrategyDefinition,
) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for condition in _conditions(definition.conditions):
        for operand in (condition.left, condition.right):
            if operand is None or operand.kind != OperandKind.MARKET_METRIC:
                continue
            category = str(
                operand.parameters.get("context_category")
                or operand.parameters.get("provider")
                or ""
            )
            if category in PUBLIC_CONTEXT_CATEGORIES | EXTERNAL_CONTEXT_CATEGORIES:
                parameters = dict(operand.parameters)
                parameters.setdefault("_timeframe", condition.timeframe)
                result.setdefault(category, {})[operand.name or condition.key] = parameters
    return result


def _conditions(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionRule):
        return [node]
    return [condition for child in node.children for condition in _conditions(child)]


def _ranking_metrics(
    candles: list[Candle],
    metadata: dict[str, Any],
) -> dict[str, float]:
    if len(candles) < 20:
        return {}
    closes = [float(candle.close) for candle in candles]
    volumes = [float(candle.volume) for candle in candles]
    returns = _returns(candles)
    close = closes[-1]
    average_volume = fmean(volumes[-21:-1]) if len(volumes) >= 21 else fmean(volumes[:-1])
    ema50 = ema(candles, period=min(50, len(candles)))
    ema200 = ema(candles, period=min(200, len(candles)))
    high24 = max(candle.high for candle in candles[-24:])
    low24 = min(candle.low for candle in candles[-24:])
    average_range = fmean(candle.high - candle.low for candle in candles[-20:])
    recent_range = max(candle.high for candle in candles[-10:]) - min(
        candle.low for candle in candles[-10:]
    )
    try:
        trend_strength = adx(candles, period=min(14, max(2, len(candles) // 3)))
    except (IndicatorWarmupError, ValueError, ZeroDivisionError):
        trend_strength = abs(ema50 - ema200) / close * 100 if close else 0
    return {
        "quote_volume_24h": _float(metadata.get("quote_volume_24h")) or 0,
        "volume_change_1h": (
            volumes[-1] / average_volume if average_volume else 0
        ),
        "relative_volume": volumes[-1] / average_volume if average_volume else 0,
        "momentum_24h": _return_percent(candles, 24) or 0,
        "volatility": pstdev(returns[-48:]) if len(returns) >= 2 else 0,
        "trend_strength": trend_strength,
        "distance_ema": abs(close - ema50) / ema50 * 100 if ema50 else 0,
        "near_24h_high": float(close >= high24 * 0.98),
        "near_24h_low": float(close <= low24 * 1.02),
        "volume_expansion": volumes[-1] / average_volume if average_volume else 0,
        "compression_score": (
            average_range / recent_range if recent_range else float("inf")
        ),
        "breakout_score": (
            (close - max(candle.high for candle in candles[-21:-1])) / average_range
            if average_range
            else 0
        ),
        "pullback_score": (
            max(0, 100 - abs(close - ema50) / ema50 * 100) if ema50 else 0
        ),
        "btc_relative_strength": _float(metadata.get("relative_strength_btc")) or 0,
        "above_ema50": float(close > ema50),
        "above_ema200": float(close > ema200),
    }


def _ranking_metric(key: str) -> tuple[str, bool]:
    return {
        "top_percent_24h_volume": ("quote_volume_24h", True),
        "top_percent_1h_volume_change": ("volume_change_1h", True),
        "top_percent_relative_volume": ("relative_volume", True),
        "top_percent_momentum": ("momentum_24h", True),
        "top_percent_volatility": ("volatility", True),
        "bottom_percent_volatility": ("volatility", False),
        "top_percent_trend_strength": ("trend_strength", True),
        "top_percent_distance_ema": ("distance_ema", True),
        "near_24h_high": ("near_24h_high", True),
        "near_24h_low": ("near_24h_low", True),
        "highest_volume_expansion": ("volume_expansion", True),
        "highest_compression_score": ("compression_score", True),
        "strongest_breakout_score": ("breakout_score", True),
        "strongest_pullback_score": ("pullback_score", True),
        "strongest_btc_relative_strength": ("btc_relative_strength", True),
    }[key]


def _order_book_condition(
    key: str,
    raw: dict[str, Any],
    parameters: dict[str, Any],
) -> bool | None:
    threshold = float(parameters.get("threshold", 10))
    mapping = {
        "spread_below_threshold": lambda: raw.get("spread_bps") <= threshold,
        "spread_above_threshold": lambda: raw.get("spread_bps") >= threshold,
        "order_book_depth_above": lambda: raw.get("total_depth_quote") >= threshold,
        "bid_ask_depth_imbalance": lambda: abs(raw.get("depth_imbalance", 0)) >= threshold,
        "large_wall_above_price": lambda: bool(raw.get("large_wall_above")),
        "large_wall_below_price": lambda: bool(raw.get("large_wall_below")),
        "liquidity_wall_pulled": lambda: bool(raw.get("liquidity_wall_pulled")),
        "liquidity_wall_added": lambda: bool(raw.get("liquidity_wall_added")),
        "approaching_liquidity_wall": lambda: bool(raw.get("approaching_liquidity_wall")),
        "slippage_below_threshold": lambda: raw.get("slippage_bps") <= threshold,
        "trade_count_spike": lambda: raw.get("trade_count_ratio") >= threshold,
        "average_trade_size_spike": lambda: raw.get("average_trade_size_ratio") >= threshold,
        "aggressive_buy_volume_proxy": lambda: raw.get("buy_volume_ratio") >= threshold,
        "aggressive_sell_volume_proxy": lambda: raw.get("sell_volume_ratio") >= threshold,
        "trade_buy_sell_imbalance": lambda: abs(raw.get("trade_imbalance", 0)) >= threshold,
        "volume_burst_seconds": lambda: raw.get("recent_trade_volume") >= threshold,
    }
    try:
        return mapping[key]()
    except (KeyError, TypeError):
        return None


def _derivatives_condition(
    key: str,
    raw: dict[str, Any],
    parameters: dict[str, Any],
) -> bool | None:
    funding = _float(raw.get("funding_rate"))
    current_oi = _float(raw.get("open_interest"))
    previous_oi = _float(raw.get("previous_open_interest"))
    price_change = _float(raw.get("price_change_percent"))
    threshold = float(parameters.get("threshold", 0))
    if key == "funding_rate_positive" and funding is not None:
        return funding > 0
    if key == "funding_rate_negative" and funding is not None:
        return funding < 0
    if key == "funding_rate_extreme" and funding is not None:
        return abs(funding) >= float(parameters.get("absolute_threshold", 0.0005))
    if key == "open_interest_rising" and None not in {current_oi, previous_oi}:
        return current_oi > previous_oi
    if key == "open_interest_falling" and None not in {current_oi, previous_oi}:
        return current_oi < previous_oi
    if key in {"long_liquidation_spike", "short_liquidation_spike"}:
        value = _float(raw.get(key.removesuffix("_spike") + "_value"))
        return value >= threshold if value is not None else None
    if None not in {price_change, current_oi, previous_oi}:
        oi_up = current_oi > previous_oi
        price_up = price_change > 0
        return {
            "price_up_oi_up": price_up and oi_up,
            "price_up_oi_down": price_up and not oi_up,
            "price_down_oi_up": not price_up and oi_up,
            "price_down_oi_down": not price_up and not oi_up,
        }.get(key)
    return None


def _trend_up(candles: list[Candle]) -> bool:
    period = min(200, len(candles))
    return bool(candles and candles[-1].close > ema(candles, period=period))


def _returns(candles: list[Candle]) -> list[float]:
    closes = [float(candle.close) for candle in candles]
    return [
        current / previous - 1
        for previous, current in zip(closes[:-1], closes[1:], strict=False)
        if previous
    ]


def _return_percent(candles: list[Candle], periods: int) -> float | None:
    if len(candles) < 2:
        return None
    start = candles[-min(len(candles), periods + 1)].close
    return ((candles[-1].close / start) - 1) * 100 if start else None


def _correlation(left: list[float], right: list[float]) -> float:
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left, right, strict=True)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left)
        * sum((value - right_mean) ** 2 for value in right)
    )
    return numerator / denominator if denominator else 0


def _beta(asset: list[float], benchmark: list[float]) -> float:
    benchmark_mean = fmean(benchmark)
    asset_mean = fmean(asset)
    covariance = fmean(
        (asset_value - asset_mean) * (benchmark_value - benchmark_mean)
        for asset_value, benchmark_value in zip(asset, benchmark, strict=True)
    )
    variance = fmean((value - benchmark_mean) ** 2 for value in benchmark)
    return covariance / variance if variance else 0


def _percent(count: int, total: int) -> float:
    return count / total * 100 if total else 0


def _symbol(value: str) -> str:
    return value.upper().replace("-", "/").strip().split(":", 1)[0]


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def timeframe_duration(timeframe: str) -> timedelta:
    value = int(timeframe[:-1])
    unit = timeframe[-1]
    if unit == "m":
        return timedelta(minutes=value)
    if unit == "h":
        return timedelta(hours=value)
    if unit == "d":
        return timedelta(days=value)
    raise ValueError(f"Unsupported timeframe: {timeframe}")
