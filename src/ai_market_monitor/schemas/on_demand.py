from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_market_monitor.schemas.strategy import StrategyDefinition


class OnDemandScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_version_id: UUID | None = None
    strategy: StrategyDefinition | None = None
    approved_schema_hash: str | None = Field(default=None, min_length=64, max_length=64)
    symbols: list[str] = Field(default_factory=list, max_length=100000)
    max_symbols: int = Field(default=100000, ge=1, le=100000)
    account_balance: float | None = Field(default=None, gt=0)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)
    light_scan: bool = False
    include_non_confirmed: bool = False

    @model_validator(mode="after")
    def validate_strategy_source(self) -> "OnDemandScanRequest":
        if bool(self.strategy_version_id) == bool(self.strategy):
            raise ValueError("Provide exactly one of strategy_version_id or strategy")
        if (
            self.strategy is not None
            and not self.light_scan
            and self.approved_schema_hash != self.strategy.canonical_hash()
        ):
            raise ValueError("Inline strategy must include the exact approved_schema_hash")
        return self


class LightScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=5, max_length=5000)
    exchange: str = Field(default="binance", min_length=2, max_length=40)
    quote_currency: str = Field(default="USDT", min_length=2, max_length=10)
    timeframe: str = Field(default="15m", min_length=2, max_length=16)
    trigger_mode: Literal["candle_close", "intrabar"] = "candle_close"
    symbols: list[str] = Field(default_factory=list, max_length=100000)
    max_results: int = Field(default=100, ge=1, le=500)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class OnDemandConditionSummary(BaseModel):
    condition_id: str
    name: str
    state: str
    required_value: Any
    actual_value: Any
    proximity_score: float


class OnDemandScanMarketResult(BaseModel):
    exchange: str
    symbol: str
    timeframe: str
    direction: str
    outcome: str
    completion_score: float
    match_percentage: float = Field(ge=0, le=100)
    trend: str
    passed_conditions: list[OnDemandConditionSummary]
    missing_conditions: list[OnDemandConditionSummary]
    closest_missing_condition: OnDemandConditionSummary | None
    proof_receipt: dict[str, Any]


class OnDemandScanResponse(BaseModel):
    status: Literal["succeeded", "partial", "failed"]
    plan_code: str
    quota_limit: int
    quota_used: int
    quota_remaining: int
    symbols_requested: int
    symbols_scanned: int
    results: list[OnDemandScanMarketResult]
    warnings: list[str] = Field(default_factory=list)
    evaluated_at: datetime
    usage_record_id: UUID | None = None
    screened_assets_considered: int = 0
    assets_excluded_by_sharia_policy: int = 0
    assets_with_insufficient_screening_data: int = 0
    eligible_assets_scanned: int = 0
    sharia_methodology_id: UUID | None = None
    sharia_methodology_code: str | None = None
    sharia_methodology_version: str | None = None
    sharia_universe_snapshot_id: UUID | None = None
    sharia_universe_snapshot_hash: str | None = None
