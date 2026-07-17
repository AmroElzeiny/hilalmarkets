from dataclasses import asdict

from fastapi import APIRouter, Depends

from ai_market_monitor.api.dependencies import UserPrincipal, get_user_principal
from ai_market_monitor.db.models.enums import StrategyStatus
from ai_market_monitor.engine.forensics import AlertEvidence, ForensicInvestigationService
from ai_market_monitor.schemas.investigation import WhyNoAlertRequest, WhyNoAlertResponse
from ai_market_monitor.services.interfaces import Candle

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("/why-no-alert", response_model=WhyNoAlertResponse)
async def why_no_alert(
    request: WhyNoAlertRequest,
    _: UserPrincipal = Depends(get_user_principal),
) -> WhyNoAlertResponse:
    candle_sets = {
        timeframe: [
            Candle(
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                is_closed=candle.is_closed,
                quote_volume=candle.quote_volume,
            )
            for candle in candles
        ]
        for timeframe, candles in request.candles.items()
    }
    result = ForensicInvestigationService().investigate(
        strategy=request.strategy,
        strategy_version=request.strategy_version,
        strategy_status=StrategyStatus(request.strategy_status),
        market=request.market,
        candle_sets=candle_sets,
        approximate_time=request.approximate_timestamp,
        subscription_allowed=request.subscription_allowed,
        evidence=AlertEvidence(**request.alert_evidence),
        previous_score=request.previous_score,
        chart_reference=request.chart_reference,
    )
    return WhyNoAlertResponse(**asdict(result))
