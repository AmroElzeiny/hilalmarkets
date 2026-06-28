from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ai_market_monitor.engine.models import MarketSnapshot
from ai_market_monitor.schemas.strategy import StrategyDefinition


class CandleInput(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True
    quote_volume: float | None = None


class WhyNoAlertRequest(BaseModel):
    user_id: str
    strategy_id: str
    strategy_version: str
    strategy_status: str = "active"
    symbol: str
    approximate_timestamp: datetime
    exchange: str | None = None
    timeframe: str | None = None
    question: str | None = Field(default=None, max_length=1000)
    strategy: StrategyDefinition
    market: MarketSnapshot
    candles: dict[str, list[CandleInput]]
    subscription_allowed: bool = True
    alert_evidence: dict[str, Any] = Field(default_factory=dict)
    previous_score: float | None = None
    chart_reference: str | None = None


class WhyNoAlertResponse(BaseModel):
    evaluated: bool
    strategy_version: str | None
    data_available: bool
    conditions_passed: list[str]
    conditions_failed: list[str]
    near_miss_threshold_reached: bool
    alert_created: bool
    delivery_failed: bool
    cooldown_prevented_delivery: bool
    symbol_excluded: bool
    subscription_limited: bool
    strategy_inactive: bool
    system_error: str | None
    explanation: str
    proof: dict[str, Any] | None = None
    chart_reference: str | None = None
