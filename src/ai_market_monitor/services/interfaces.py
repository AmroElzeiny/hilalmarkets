from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from ai_market_monitor.schemas.onboarding import GuidedSetupRequest, MarketPreviewResponse
from ai_market_monitor.schemas.strategy import InterpretationPreview, StrategyDefinition


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True
    quote_volume: float | None = None


class StrategyInterpreter(Protocol):
    async def interpret(self, guided_setup: GuidedSetupRequest) -> InterpretationPreview: ...


class MarketDataProvider(Protocol):
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]: ...

    async def fetch_ohlcv(
        self, exchange: str, symbol: str, timeframe: str, limit: int
    ) -> list[Candle]: ...

    async def fetch_universe_metadata(
        self,
        exchange: str,
        symbols: list[str],
        *,
        include_listing_dates: bool = False,
    ) -> dict[str, dict[str, Any]]: ...

    async def fetch_order_book_context(
        self,
        exchange: str,
        symbol: str,
        *,
        depth: int = 50,
    ) -> dict[str, Any]: ...

    async def fetch_derivatives_context(
        self,
        exchange: str,
        symbol: str,
    ) -> dict[str, Any]: ...


class CrossMarketContextProvider(Protocol):
    async def snapshot(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        benchmark_symbols: list[str],
        evaluated_at: datetime,
    ) -> dict[str, float | bool]: ...


class EventContextProvider(Protocol):
    async def events(
        self,
        *,
        symbols: list[str],
        start: datetime,
        end: datetime,
        categories: list[str],
    ) -> list[dict[str, Any]]: ...


class OrderBookContextProvider(Protocol):
    async def snapshot(
        self,
        *,
        exchange: str,
        symbol: str,
        depth: int = 50,
    ) -> dict[str, float | bool]: ...


class DerivativesContextProvider(Protocol):
    async def snapshot(
        self,
        *,
        exchange: str,
        symbol: str,
    ) -> dict[str, float | bool]: ...


class UniverseRankingProvider(Protocol):
    async def rank(
        self,
        *,
        exchange: str,
        symbols: list[str],
        timeframe: str,
        metrics: list[str],
        evaluated_at: datetime,
    ) -> dict[str, dict[str, float]]: ...

    async def fetch_ohlcv_range(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[Candle]: ...


class RecentMarketPreviewer(Protocol):
    async def run(self, strategy: StrategyDefinition) -> MarketPreviewResponse: ...


class RuleEngine(Protocol):
    def evaluate(
        self,
        strategy: StrategyDefinition,
        candle_sets: dict[str, list[Candle]],
        evaluated_at: datetime,
    ) -> dict[str, Any]: ...


class NearMissScorer(Protocol):
    def score(self, condition_proofs: list[dict[str, Any]]) -> dict[str, Any]: ...


class SetupLifecycleManager(Protocol):
    async def apply_evaluation(
        self, setup_instance_id: UUID, evaluation: dict[str, Any]
    ) -> str: ...


class EntitlementAuthorizer(Protocol):
    async def authorize(self, user_id: UUID, capability: str, requested_units: int = 1) -> bool: ...


class AuditLogger(Protocol):
    async def record(
        self,
        action: str,
        actor_user_id: UUID | None,
        target_type: str,
        target_id: str | None,
        metadata: dict[str, Any],
    ) -> None: ...


class NotificationGateway(Protocol):
    channel: str

    async def send(self, destination: str, message: str, metadata: dict[str, Any]) -> str: ...


class BillingGateway(Protocol):
    async def create_checkout(self, user_id: UUID, plan_code: str) -> str: ...

    async def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]: ...


class ChartRenderer(Protocol):
    async def render_setup(self, setup_instance_id: UUID) -> str: ...


class TaskDispatcher(Protocol):
    async def enqueue(self, task_name: str, payload: dict[str, Any]) -> str: ...
