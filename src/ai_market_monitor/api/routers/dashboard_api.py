import base64
import binascii
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.api.dependencies import (
    UserPrincipal,
    get_market_data_provider,
)
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    AuditEvent,
    BacktestJob,
    BacktestResult,
    ChartSnapshot,
    DashboardNotification,
    DashboardPreference,
    DiscordConnection,
    DiscordDeliveryDestination,
    IntegrationTestResult,
    NearMissSnapshot,
    SetupConditionResult,
    SetupInstance,
    SetupLifecycleEvent,
    SetupReplayJob,
    SetupReplayResult,
    Strategy,
    StrategyCondition,
    StrategyTemplate,
    StrategyVersion,
    SupportRequest,
    SupportTicketMessage,
    TelegramConnection,
    TelegramConversationState,
    Trial,
    User,
    UserExportJob,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConditionOutcome,
    DeliveryChannel,
    DeliveryStatus,
    IdentityProvider,
    StrategyStatus,
    StrategyVersionStatus,
    UserStatus,
)
from ai_market_monitor.engine.condition_registry import condition_registry_payload
from ai_market_monitor.engine.models import ensure_aware
from ai_market_monitor.schemas.on_demand import (
    LightScanRequest,
    OnDemandScanRequest,
    OnDemandScanResponse,
)
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import (
    InterpretationIssue,
    InterpretationPreview,
    StrategyDefinition,
)
from ai_market_monitor.services.admin_notifications import AdminNotificationService
from ai_market_monitor.services.coverage import market_coverage_for_user
from ai_market_monitor.services.dashboard_jobs import DashboardJobService, export_file_path
from ai_market_monitor.services.email_delivery import AuthEmailService, EmailDeliveryError
from ai_market_monitor.services.entitlements import EntitlementError, EntitlementService
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.lifecycle_dashboard import state_label
from ai_market_monitor.services.market_preview import timeframe_duration
from ai_market_monitor.services.on_demand_scans import OnDemandScanError, OnDemandScanService
from ai_market_monitor.services.openai_interpreter import configured_strategy_interpreter
from ai_market_monitor.services.strategy import StrategyGateError, StrategyService
from ai_market_monitor.services.template_catalog import builtin_template_payloads
from ai_market_monitor.services.web_auth import SESSION_COOKIE_NAME, WebAuthService

router = APIRouter(prefix="/dashboard", tags=["dashboard-api"])
logger = structlog.get_logger(__name__)


class StrategyCreateRequest(BaseModel):
    definition: StrategyDefinition
    source_text: str | None = Field(default=None, max_length=5000)
    interpreter: str = Field(default="dashboard-builder-v1", max_length=120)
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    ambiguities: list[InterpretationIssue] = Field(default_factory=list, max_length=50)
    unsupported_conditions: list[InterpretationIssue] = Field(default_factory=list, max_length=50)


class StrategyPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    status: StrategyStatus | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "StrategyPatchRequest":
        if self.name is None and self.description is None and self.status is None:
            raise ValueError("at least one field is required")
        return self


class StrategyVersionCreateRequest(BaseModel):
    definition: StrategyDefinition
    source_text: str | None = Field(default=None, max_length=5000)
    interpreter: str = Field(default="dashboard-builder-v1", max_length=120)
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    ambiguities: list[InterpretationIssue] = Field(default_factory=list, max_length=50)
    unsupported_conditions: list[InterpretationIssue] = Field(default_factory=list, max_length=50)


class StrategyApproveRequest(BaseModel):
    strategy_version_id: UUID | None = None
    expected_schema_hash: str | None = Field(default=None, min_length=64, max_length=64)


class StrategyCompareRequest(BaseModel):
    left_version_id: UUID
    right_version_id: UUID


class StrategyTemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    category: str = Field(default="custom", min_length=1, max_length=60)
    tags: list[str] = Field(default_factory=list, max_length=20)
    definition: StrategyDefinition
    source_strategy_id: UUID | None = None
    source_strategy_version_id: UUID | None = None
    shared_scope: str = Field(default="private", max_length=40)


class SetupReplayCreateRequest(BaseModel):
    strategy_id: UUID | None = None
    strategy_version_id: UUID | None = None
    exchange: str = Field(default="binance", min_length=2, max_length=40)
    symbol: str = Field(min_length=3, max_length=40)
    timeframe: str = Field(min_length=2, max_length=16)
    approximate_time: datetime
    window_before_minutes: int = Field(default=14_400, ge=1, le=525_600)
    window_after_minutes: int = Field(default=240, ge=0, le=43_200)
    user_question: str | None = Field(default=None, max_length=1000)


class ScanPromptInterpretRequest(BaseModel):
    prompt: str = Field(min_length=5, max_length=5000)
    exchange: str = Field(default="binance", min_length=2, max_length=40)
    quote_currency: str = Field(default="USDT", min_length=2, max_length=10)
    timeframe: str = Field(default="15m", min_length=2, max_length=16)
    trigger_mode: str = Field(default="candle_close", pattern="^(candle_close|intrabar)$")
    symbols: list[str] = Field(default_factory=list, max_length=5000)
    maximum_stop_percent: float | None = Field(default=None, gt=0, le=100)
    minimum_reward_to_risk: float | None = Field(default=None, gt=0, le=50)
    minimum_quote_volume_24h: float | None = Field(default=None, ge=0)
    maximum_spread_bps: float | None = Field(default=None, ge=0, le=1000)
    near_miss_threshold: float = Field(default=70, ge=1, le=100)


class StrategyBuilderInterpretRequest(BaseModel):
    prompt_parts: dict[str, str] = Field(default_factory=dict)
    raw_prompt: str | None = Field(default=None, max_length=7000)
    current_schema: StrategyDefinition | None = None
    exchange: str = Field(default="binance", min_length=2, max_length=40)
    quote_currency: str = Field(default="USDT", min_length=2, max_length=10)
    symbols: list[str] = Field(default_factory=list, max_length=100000)
    timeframe: str = Field(default="15m", min_length=2, max_length=16)
    trigger_mode: str = Field(default="candle_close", pattern="^(candle_close|intrabar)$")
    builder_mode: str = Field(default="prompt", max_length=40)
    user_preferences: dict[str, Any] = Field(default_factory=dict)
    plan_limits: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_prompt(self) -> "StrategyBuilderInterpretRequest":
        if not self.prompt_text().strip():
            raise ValueError("raw_prompt or prompt_parts is required")
        return self

    def prompt_text(self) -> str:
        parts = [
            f"{key.replace('_', ' ').title()}: {value.strip()}"
            for key, value in self.prompt_parts.items()
            if isinstance(value, str) and value.strip()
        ]
        raw = (self.raw_prompt or "").strip()
        return raw or "\n".join(parts)


class BuilderInterpretationFeedbackRequest(BaseModel):
    feedback_type: Literal[
        "correct",
        "wrong_timeframe",
        "missed_condition",
        "wrong_direction",
        "too_strict",
        "too_loose",
        "start_over",
    ]
    raw_prompt: str | None = Field(default=None, max_length=7000)
    prompt_coverage_report: dict[str, Any] = Field(default_factory=dict)
    strategy: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = Field(default=None, max_length=1000)


class BacktestCreateRequest(BaseModel):
    strategy_id: UUID
    strategy_version_id: UUID | None = None
    exchange: str = Field(default="binance", min_length=2, max_length=40)
    symbols: list[str] = Field(min_length=1, max_length=1000)
    timeframe: str = Field(min_length=2, max_length=16)
    started_at_range: datetime
    ended_at_range: datetime
    parameters: dict[str, Any] = Field(default_factory=dict)


class ExportCreateRequest(BaseModel):
    export_type: str = Field(default="dashboard", min_length=2, max_length=40)
    format: Literal["json", "csv"] = "json"
    filters: dict[str, Any] = Field(default_factory=dict)


class ThemePreferenceRequest(BaseModel):
    theme: Literal["dark", "light"]


class SupportScreenshot(BaseModel):
    filename: str = Field(min_length=1, max_length=160)
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    data_base64: str = Field(min_length=4, max_length=7_000_000)


class SupportTicketCreateRequest(BaseModel):
    category: str = Field(default="general", min_length=2, max_length=50)
    email: EmailStr | None = None
    subject: str = Field(min_length=3, max_length=240)
    description: str = Field(min_length=3, max_length=5000)
    context: dict[str, Any] = Field(default_factory=dict)
    screenshots: list[SupportScreenshot] = Field(default_factory=list, max_length=3)


class LifecycleChartAnnotation(BaseModel):
    model_config = {"extra": "forbid"}

    id: str = Field(min_length=1, max_length=80)
    type: Literal["line", "horizontal", "text"]
    time1: int | None = Field(default=None, ge=0)
    price1: float
    time2: int | None = Field(default=None, ge=0)
    price2: float | None = None
    text: str | None = Field(default=None, max_length=80)
    color: str = Field(default="#60a5fa", pattern=r"^#[0-9a-fA-F]{6}$")

    @field_validator("text")
    @classmethod
    def validate_short_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned.split()) > 5:
            raise ValueError("Annotation text must contain 1 to 5 words")
        return cleaned

    @model_validator(mode="after")
    def validate_geometry(self) -> "LifecycleChartAnnotation":
        if self.type == "line" and (
            self.time1 is None
            or self.time2 is None
            or self.price2 is None
        ):
            raise ValueError("Line annotations require two time and price points")
        if self.type == "text" and (self.time1 is None or self.text is None):
            raise ValueError("Text annotations require a time, price, and label")
        return self


class LifecycleAnnotationSaveRequest(BaseModel):
    model_config = {"extra": "forbid"}

    timeframe: str = Field(pattern=r"^(1|3|5|15|30)m$|^(1|2|4|6|8|12)h$|^1d$")
    annotations: list[LifecycleChartAnnotation] = Field(default_factory=list, max_length=200)


class TradingViewStorageRequest(BaseModel):
    model_config = {"extra": "forbid"}

    timeframe: str = Field(default="1m", pattern=r"^(1|3|5|15|30)m$|^(1|2|4|6|8|12)h$|^1d$")
    symbol: str | None = Field(default=None, min_length=3, max_length=40)
    chart_id: str | None = Field(default=None, max_length=120)
    layout_id: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=160)
    chart_data: dict[str, Any] | list[Any] | str | None = None
    line_tools_state: dict[str, Any] | list[Any] | None = None


def _now() -> datetime:
    return datetime.now(UTC)


async def get_dashboard_principal(
    request: Request,
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UserPrincipal:
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        user = await WebAuthService(session, settings).current_user(session_token)
        if user is not None and user.status != UserStatus.SUSPENDED:
            return UserPrincipal(user_id=user.id, role=user.role)
    if x_user_id:
        if settings.app_env not in {"development", "test"}:
            raise HTTPException(
                status_code=401,
                detail="Header principals are disabled outside development/test",
            )
        try:
            user_id = UUID(x_user_id)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Invalid user principal") from exc
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid user principal")
        if user.status == UserStatus.SUSPENDED:
            raise HTTPException(status_code=403, detail="User account is suspended")
        return UserPrincipal(user_id=user.id, role=user.role)
    raise HTTPException(status_code=401, detail="Dashboard session required")


@router.put("/preferences/theme")
async def save_theme_preference(
    payload: ThemePreferenceRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    preference = await session.scalar(
        select(DashboardPreference).where(
            DashboardPreference.user_id == principal.user_id
        )
    )
    if preference is None:
        preference = DashboardPreference(
            user_id=principal.user_id,
            theme=payload.theme,
            default_timezone="UTC",
            notification_preferences={"theme": payload.theme},
        )
        session.add(preference)
    else:
        preference.theme = payload.theme
        preference.notification_preferences = {
            **(preference.notification_preferences or {}),
            "theme": payload.theme,
        }
    await session.commit()
    return {"theme": payload.theme}


async def _owned_strategy(
    session: AsyncSession,
    user_id: UUID,
    strategy_id: UUID,
) -> Strategy:
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None or strategy.user_id != user_id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


async def _owned_version(
    session: AsyncSession,
    user_id: UUID,
    version_id: UUID,
) -> StrategyVersion:
    version = await session.get(StrategyVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Strategy version not found")
    strategy = await session.get(Strategy, version.strategy_id)
    if strategy is None or strategy.user_id != user_id:
        raise HTTPException(status_code=404, detail="Strategy version not found")
    return version


async def _owned_setup(
    session: AsyncSession,
    user_id: UUID,
    setup_id: UUID,
) -> SetupInstance:
    setup = await session.get(SetupInstance, setup_id)
    if setup is None or setup.user_id != user_id:
        raise HTTPException(status_code=404, detail="Setup not found")
    return setup


def _symbol_workspace_id(exchange: str, symbol: str) -> str:
    return f"{exchange.lower()}:{symbol.upper()}"


async def _symbol_chart_snapshot(
    session: AsyncSession,
    user_id: UUID,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> ChartSnapshot | None:
    return await session.scalar(
        select(ChartSnapshot)
        .where(
            ChartSnapshot.user_id == user_id,
            ChartSnapshot.subject_type == "symbol_workspace",
            ChartSnapshot.subject_id == _symbol_workspace_id(exchange, symbol),
            ChartSnapshot.timeframe == timeframe,
        )
        .order_by(ChartSnapshot.updated_at.desc())
        .limit(1)
    )


async def _ensure_symbol_chart_snapshot(
    session: AsyncSession,
    user_id: UUID,
    exchange: str,
    symbol: str,
    timeframe: str,
    *,
    setup_id: UUID | None = None,
    strategy_version_id: UUID | None = None,
) -> ChartSnapshot:
    snapshot = await _symbol_chart_snapshot(session, user_id, exchange, symbol, timeframe)
    if snapshot is not None:
        return snapshot
    snapshot = ChartSnapshot(
        user_id=user_id,
        subject_type="symbol_workspace",
        subject_id=_symbol_workspace_id(exchange, symbol),
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        chart_config={},
        proof_reference={
            "setup_instance_id": str(setup_id) if setup_id else None,
            "strategy_version_id": str(strategy_version_id) if strategy_version_id else None,
        },
    )
    session.add(snapshot)
    return snapshot


def _short_marker_label(value: str) -> str:
    words = value.replace("_", " ").split()
    return " ".join(words[:5])


def _lifecycle_condition_payload(
    result: SetupConditionResult,
    definition: StrategyCondition,
) -> dict[str, Any]:
    return {
        "key": result.condition_key,
        "name": definition.label,
        "timeframe": definition.timeframe,
        "state": result.outcome.value,
        "actual": (result.actual_value or {}).get("value"),
        "required": (result.required_value or {}).get("value"),
        "candle_timestamp": result.candle_timestamp,
        "evaluated_at": result.evaluated_at,
    }


async def _latest_version(session: AsyncSession, strategy_id: UUID) -> StrategyVersion | None:
    return await session.scalar(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy_id)
        .order_by(StrategyVersion.version_number.desc())
        .limit(1)
    )


async def _user_email(session: AsyncSession, user_id: UUID) -> str | None:
    identity = await session.scalar(
        select(UserIdentity)
        .where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == IdentityProvider.EMAIL,
        )
        .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
        .limit(1)
    )
    return identity.display_identifier if identity else None


def _strategy_payload(strategy: Strategy, version: StrategyVersion | None = None) -> dict[str, Any]:
    effective_status = strategy.status.value
    if strategy.active_version_id and strategy.status == StrategyStatus.DRAFT:
        effective_status = StrategyStatus.ACTIVE.value
    return {
        "id": strategy.id,
        "name": strategy.name,
        "description": strategy.description,
        "status": effective_status,
        "active_version_id": strategy.active_version_id,
        "activated_at": strategy.activated_at,
        "paused_at": strategy.paused_at,
        "archived_at": strategy.archived_at,
        "created_at": strategy.created_at,
        "updated_at": strategy.updated_at,
        "latest_version": _version_payload(version) if version else None,
    }


def _version_payload(version: StrategyVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "strategy_id": version.strategy_id,
        "version_number": version.version_number,
        "status": version.status.value,
        "schema_hash": version.schema_hash,
        "schema_json": version.schema_json,
        "source_type": version.source_type,
        "source_text": version.source_text,
        "assumptions": version.assumptions,
        "ambiguities": version.ambiguities,
        "unsupported_conditions": version.unsupported_conditions,
        "approved_at": version.approved_at,
        "preview_status": version.preview_status,
        "previewed_at": version.previewed_at,
        "preview_summary": version.preview_summary,
        "activated_at": version.activated_at,
        "created_at": version.created_at,
    }


def _interpretation_payload(preview: InterpretationPreview) -> dict[str, Any]:
    strategy = preview.strategy
    rule_payloads = _condition_rule_payloads(strategy)
    coverage = preview.raw_metadata.get("prompt_coverage_report", {})
    ignored_optional = [
        issue.model_dump(mode="json")
        for issue in preview.unsupported_conditions
        if not issue.blocking
    ]
    blocking_unsupported = [
        issue.model_dump(mode="json") for issue in preview.unsupported_conditions if issue.blocking
    ]
    warnings = [*preview.assumptions]
    warnings.extend(
        f"Optional unsupported idea ignored: {issue['message']}" for issue in ignored_optional
    )
    return {
        "strategy": strategy.model_dump(mode="json"),
        "interpreter": preview.interpreter,
        "ai_used": preview.interpreter.startswith("openai-structured-v1:"),
        "approved_schema_hash": strategy.canonical_hash(),
        "activation_blocked": preview.activation_blocked,
        "assumptions": preview.assumptions,
        "ambiguities": [issue.model_dump(mode="json") for issue in preview.ambiguities],
        "unsupported_conditions": [
            issue.model_dump(mode="json") for issue in preview.unsupported_conditions
        ],
        "prompt_coverage_report": coverage,
        "ignored_fragments": (
            coverage.get("ignored_fragments", []) if isinstance(coverage, dict) else []
        ),
        "confidence_score": (
            coverage.get("confidence_score") if isinstance(coverage, dict) else None
        ),
        "coverage_score": coverage.get("coverage_score") if isinstance(coverage, dict) else None,
        "mapping_table": coverage.get("mapping_table", []) if isinstance(coverage, dict) else [],
        "understanding": _strategy_understanding_summary(strategy, preview),
        "interpreted_rules": rule_payloads,
        "required_rules": [rule for rule in rule_payloads if rule["required"]],
        "optional_rules": [rule for rule in rule_payloads if not rule["required"]],
        "ignored_optional_rules": ignored_optional,
        "blocking_unsupported_rules": blocking_unsupported,
        "warnings": warnings,
        "scan_safety_level": (
            "blocked" if preview.activation_blocked else "partial" if ignored_optional else "strict"
        ),
        "light_mode_compatible": (
            _has_executable_conditions(strategy)
            and not blocking_unsupported
            and strategy.universe.market_type.value == "spot"
        ),
        "suggested_clarifications": [
            *(issue.message for issue in preview.ambiguities),
            *(issue["message"] for issue in blocking_unsupported),
        ],
    }


def _condition_rule_payloads(strategy: StrategyDefinition) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if getattr(node, "node_type", None) == "condition":
            rules.append(
                {
                    "condition_id": node.key,
                    "name": node.label,
                    "condition_type": node.condition_type.value,
                    "operator": node.comparator.value,
                    "timeframe": node.timeframe,
                    "left": node.left.model_dump(mode="json"),
                    "right": node.right.model_dump(mode="json") if node.right else None,
                    "required": node.required,
                    "weight": node.weight,
                    "forming_tolerance_percent": node.forming_tolerance_percent,
                    "required_data": node.required_data,
                    "explanation_template": node.explanation_template,
                    "source_fragment": node.source_fragment,
                    "confidence": node.confidence,
                    "ai_interpreted": node.ai_interpreted,
                    "provider_required": node.provider_required,
                    "availability": node.availability,
                }
            )
            return
        for child in getattr(node, "children", []):
            walk(child)

    walk(strategy.conditions)
    return rules


def _guided_from_scan_prompt(
    payload: ScanPromptInterpretRequest | LightScanRequest,
) -> GuidedSetupRequest:
    return GuidedSetupRequest(
        exchange=payload.exchange,
        quote_currency=payload.quote_currency,
        timeframe=payload.timeframe,
        symbols=[symbol.upper().replace("-", "/") for symbol in payload.symbols],
        setup_mode="free_text",
        setup_text=payload.prompt,
        trigger_mode=payload.trigger_mode,
        maximum_stop_percent=getattr(payload, "maximum_stop_percent", None),
        minimum_reward_to_risk=getattr(payload, "minimum_reward_to_risk", None),
        minimum_quote_volume_24h=getattr(payload, "minimum_quote_volume_24h", None),
        maximum_spread_bps=getattr(payload, "maximum_spread_bps", None),
        forming_alerts=True,
        near_miss_threshold=getattr(payload, "near_miss_threshold", 70),
        delivery_channels=["web"],
        maximum_alerts_per_hour=50,
    )


def _guided_from_strategy_builder(
    payload: StrategyBuilderInterpretRequest,
    prompt: str,
) -> GuidedSetupRequest:
    current = payload.current_schema
    quote_currency = payload.quote_currency
    if not quote_currency and current and current.universe.quote_currencies:
        quote_currency = current.universe.quote_currencies[0]
    return GuidedSetupRequest(
        exchange=payload.exchange or (current.universe.exchange if current else "binance"),
        quote_currency=quote_currency or "USDT",
        timeframe=payload.timeframe or (current.base_timeframe if current else "15m"),
        symbols=[symbol.upper().replace("-", "/") for symbol in payload.symbols],
        setup_mode="free_text",
        setup_text=prompt,
        trigger_mode=payload.trigger_mode,
        maximum_stop_percent=(
            current.risk.maximum_stop_percent if current and current.risk.enabled else None
        ),
        minimum_reward_to_risk=(
            current.risk.minimum_reward_to_risk if current and current.risk.enabled else None
        ),
        minimum_quote_volume_24h=(
            current.universe.min_quote_volume_24h if current else None
        ),
        maximum_spread_bps=current.universe.max_spread_bps if current else None,
        forming_alerts=current.alerts.forming_alerts if current else True,
        near_miss_threshold=current.alerts.near_miss_threshold if current else 70,
        delivery_channels=current.alerts.channels if current else ["web"],
        maximum_alerts_per_hour=current.alerts.maximum_alerts_per_hour if current else 50,
    )


def _strategy_visual_diff(
    previous: StrategyDefinition | None,
    current: StrategyDefinition,
) -> dict[str, Any]:
    if previous is None:
        return {
            "changed_fields": ["strategy"],
            "summary": "New interpreted strategy draft.",
        }
    changes: list[str] = []
    if previous.name != current.name:
        changes.append("name")
    if previous.direction != current.direction:
        changes.append("direction")
    if previous.base_timeframe != current.base_timeframe:
        changes.append("base_timeframe")
    if previous.supporting_timeframes != current.supporting_timeframes:
        changes.append("supporting_timeframes")
    if previous.universe.model_dump(mode="json") != current.universe.model_dump(mode="json"):
        changes.append("universe")
    if previous.conditions.model_dump(mode="json") != current.conditions.model_dump(mode="json"):
        changes.append("conditions")
    if previous.alerts.model_dump(mode="json") != current.alerts.model_dump(mode="json"):
        changes.append("alerts")
    if previous.risk.model_dump(mode="json") != current.risk.model_dump(mode="json"):
        changes.append("risk")
    return {
        "changed_fields": changes,
        "summary": (
            "No structural changes detected."
            if not changes
            else "Changed: " + ", ".join(changes)
        ),
    }


def _has_executable_conditions(definition: StrategyDefinition) -> bool:
    executable = False

    def walk(node: Any) -> None:
        nonlocal executable
        if getattr(node, "node_type", None) == "condition":
            if getattr(node, "key", "") != "clarification_required":
                executable = True
            return
        for child in getattr(node, "children", []):
            walk(child)

    walk(definition.conditions)
    return executable


def _strategy_understanding_summary(
    strategy: StrategyDefinition,
    preview: InterpretationPreview,
) -> dict[str, Any]:
    conditions: list[str] = []

    def walk(node: Any) -> None:
        if getattr(node, "node_type", None) == "condition":
            conditions.append(f"{node.label} ({node.timeframe}, {node.comparator.value})")
            return
        for child in getattr(node, "children", []):
            walk(child)

    walk(strategy.conditions)
    universe = strategy.universe
    return {
        "name": strategy.name,
        "direction": strategy.direction.value,
        "exchange": universe.exchange,
        "market_type": universe.market_type.value,
        "pair_universe": (
            ", ".join(universe.include_symbols)
            if universe.include_symbols
            else f"All eligible {', '.join(universe.quote_currencies)} spot pairs"
        ),
        "timeframes": [strategy.base_timeframe, *strategy.supporting_timeframes],
        "trigger_mode": strategy.trigger_mode.value,
        "entry_conditions": conditions,
        "risk": (
            {
                "enabled": True,
                "stop_method": strategy.risk.stop_method,
                "maximum_stop_percent": strategy.risk.maximum_stop_percent,
                "minimum_reward_to_risk": strategy.risk.minimum_reward_to_risk,
            }
            if strategy.risk.enabled
            else {"enabled": False, "message": "No stop or R:R filter was requested."}
        ),
        "liquidity": {
            "minimum_quote_volume_24h": universe.min_quote_volume_24h,
            "maximum_spread_bps": universe.max_spread_bps,
        },
        "near_miss_threshold": strategy.alerts.near_miss_threshold,
        "alert_channels": strategy.alerts.channels,
        "requires_user_approval": True,
        "issues": {
            "ambiguities": len(preview.ambiguities),
            "unsupported_conditions": len(preview.unsupported_conditions),
        },
    }


def _diff_versions(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    changed_sections: list[dict[str, Any]] = []
    for key in [
        "name",
        "description",
        "direction",
        "base_timeframe",
        "supporting_timeframes",
        "trigger_mode",
        "universe",
        "conditions",
        "entry",
        "stop",
        "targets",
        "risk",
        "near_miss",
        "alerts",
        "expiry",
        "forward_test",
        "position_sizing",
    ]:
        if left.get(key) != right.get(key):
            changed_sections.append(
                {"section": key, "left": left.get(key), "right": right.get(key)}
            )
    return {
        "changed_sections": changed_sections,
        "changed_count": len(changed_sections),
        "summary": (
            "No schema changes detected."
            if not changed_sections
            else f"{len(changed_sections)} strategy section(s) changed."
        ),
    }


@router.get("/current-user")
async def current_user(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid user principal")
    return {
        "id": user.id,
        "display_name": user.display_name,
        "email": await _user_email(session, user.id),
        "role": user.role.value,
        "status": user.status.value,
        "timezone": user.timezone,
        "onboarding_completed_at": user.onboarding_completed_at,
    }


@router.get("/strategies")
async def list_strategies(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    strategies = (
        await session.scalars(
            select(Strategy)
            .where(
                Strategy.user_id == principal.user_id,
                Strategy.archived_at.is_(None),
                Strategy.status != StrategyStatus.ARCHIVED,
            )
            .order_by(Strategy.created_at.desc())
        )
    ).all()
    payload: list[dict[str, Any]] = []
    for strategy in strategies:
        version = await _latest_version(session, strategy.id)
        payload.append(_strategy_payload(strategy, version))
    return {"items": payload}


@router.post("/strategies", status_code=status.HTTP_201_CREATED)
async def create_strategy(
    payload: StrategyCreateRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    preview = InterpretationPreview(
        strategy=payload.definition,
        assumptions=payload.assumptions,
        ambiguities=payload.ambiguities,
        unsupported_conditions=payload.unsupported_conditions,
        interpreter=payload.interpreter,
    )
    strategy, version = await StrategyService(
        session, settings.disclaimer_version
    ).create_from_interpretation(
        principal.user_id,
        preview,
        source_text=payload.source_text,
    )
    await session.commit()
    return {"strategy": _strategy_payload(strategy, version), "version": _version_payload(version)}


@router.patch("/strategies/{strategy_id}")
async def update_strategy(
    strategy_id: UUID,
    payload: StrategyPatchRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    strategy = await _owned_strategy(session, principal.user_id, strategy_id)
    if payload.name is not None:
        strategy.name = payload.name
    if payload.description is not None:
        strategy.description = payload.description
    if payload.status is not None:
        if payload.status == StrategyStatus.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail="Use strategy activation flow to activate a monitor",
            )
        strategy.status = payload.status
    await session.commit()
    return {"strategy": _strategy_payload(strategy, await _latest_version(session, strategy.id))}


@router.get("/strategies/{strategy_id}/versions")
async def list_strategy_versions(
    strategy_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await _owned_strategy(session, principal.user_id, strategy_id)
    versions = (
        await session.scalars(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy_id)
            .order_by(StrategyVersion.version_number.desc())
        )
    ).all()
    return {"items": [_version_payload(version) for version in versions]}


@router.post("/strategies/{strategy_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_strategy_version(
    strategy_id: UUID,
    payload: StrategyVersionCreateRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    strategy = await _owned_strategy(session, principal.user_id, strategy_id)
    version = await StrategyService(session, settings.disclaimer_version).revise(
        strategy,
        payload.definition,
        user_id=principal.user_id,
        source_text=payload.source_text,
        assumptions=payload.assumptions,
        ambiguities=[issue.model_dump(mode="json") for issue in payload.ambiguities],
        unsupported=[issue.model_dump(mode="json") for issue in payload.unsupported_conditions],
        interpreter=payload.interpreter,
    )
    await session.commit()
    return {"version": _version_payload(version)}


@router.post("/strategies/{strategy_id}/approve")
async def approve_strategy_version(
    strategy_id: UUID,
    payload: StrategyApproveRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    await _owned_strategy(session, principal.user_id, strategy_id)
    version = (
        await _owned_version(session, principal.user_id, payload.strategy_version_id)
        if payload.strategy_version_id
        else await _latest_version(session, strategy_id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Strategy version not found")
    expected_hash = payload.expected_schema_hash or version.schema_hash
    try:
        approved = await StrategyService(session, settings.disclaimer_version).approve(
            version,
            user_id=principal.user_id,
            expected_schema_hash=expected_hash,
        )
    except (StrategyGateError, EntitlementError) as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    await session.commit()
    return {"version": _version_payload(approved)}


@router.post("/strategies/{strategy_id}/publish")
async def publish_strategy_version(
    strategy_id: UUID,
    payload: StrategyApproveRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    strategy = await _owned_strategy(session, principal.user_id, strategy_id)
    version = (
        await _owned_version(session, principal.user_id, payload.strategy_version_id)
        if payload.strategy_version_id
        else await _latest_version(session, strategy_id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Strategy version not found")
    expected_hash = payload.expected_schema_hash or version.schema_hash
    try:
        approved = await StrategyService(session, settings.disclaimer_version).approve(
            version,
            user_id=principal.user_id,
            expected_schema_hash=expected_hash,
        )
        definition = StrategyDefinition.model_validate(approved.schema_json)
        from ai_market_monitor.cockpit_service import StrategyCockpitService

        validation = await StrategyCockpitService(session).validate_definition(
            user_id=principal.user_id,
            definition=definition,
            strategy_id=strategy.id,
            strategy_version_id=approved.id,
        )
        if validation["blocking"]:
            raise StrategyGateError(
                "strategy_conflict_detected",
                "Critical strategy conflicts must be resolved before activation.",
            )
        await EntitlementService(session).enforce_strategy_activation(
            principal.user_id,
            definition,
            strategy_id=strategy.id,
        )
    except (StrategyGateError, EntitlementError) as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    now = _now()
    if strategy.active_version_id and strategy.active_version_id != approved.id:
        active_version = await session.get(StrategyVersion, strategy.active_version_id)
        if active_version is not None:
            active_version.status = StrategyVersionStatus.SUPERSEDED
    strategy.status = StrategyStatus.ACTIVE
    strategy.active_version_id = approved.id
    strategy.activated_at = now
    strategy.paused_at = None
    approved.status = StrategyVersionStatus.ACTIVE
    approved.activated_at = now
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="user",
            action="strategy.published",
            target_type="strategy",
            target_id=str(strategy.id),
            metadata_redacted={"version_id": str(approved.id), "schema_hash": approved.schema_hash},
            created_at=now,
        )
    )
    await session.commit()
    await session.refresh(strategy)
    await session.refresh(approved)
    return {
        "strategy": _strategy_payload(strategy, approved),
        "version": _version_payload(approved),
    }


@router.post("/strategies/compare")
async def compare_versions(
    payload: StrategyCompareRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    left = await _owned_version(session, principal.user_id, payload.left_version_id)
    right = await _owned_version(session, principal.user_id, payload.right_version_id)
    diff = _diff_versions(left.schema_json, right.schema_json)
    from ai_market_monitor.cockpit_service import StrategyCockpitService

    behavior = await StrategyCockpitService(session).compare_versions(left, right)
    return {
        "left": _version_payload(left),
        "right": _version_payload(right),
        "diff": diff,
        "behavior": behavior,
    }


@router.get("/templates")
async def list_templates(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    templates = (
        await session.scalars(
            select(StrategyTemplate)
            .where(
                StrategyTemplate.user_id == principal.user_id,
                StrategyTemplate.archived_at.is_(None),
            )
            .order_by(StrategyTemplate.category.asc(), StrategyTemplate.name.asc())
        )
    ).all()
    return {
        "items": [
            {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "category": template.category,
                "tags": template.tags,
                "schema_json": template.schema_json,
                "shared_scope": template.shared_scope,
                "last_used_at": template.last_used_at,
                "created_at": template.created_at,
            }
            for template in templates
        ]
    }


@router.get("/capabilities")
async def list_capabilities(
    _: UserPrincipal = Depends(get_dashboard_principal),
) -> dict[str, Any]:
    payload = condition_registry_payload()
    payload["builtin_templates"] = builtin_template_payloads()
    return payload


@router.post("/templates", status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: StrategyTemplateCreateRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    if payload.source_strategy_id is not None:
        await _owned_strategy(session, principal.user_id, payload.source_strategy_id)
    if payload.source_strategy_version_id is not None:
        await _owned_version(session, principal.user_id, payload.source_strategy_version_id)
    template = StrategyTemplate(
        user_id=principal.user_id,
        source_strategy_id=payload.source_strategy_id,
        source_strategy_version_id=payload.source_strategy_version_id,
        name=payload.name,
        description=payload.description,
        category=payload.category,
        tags=payload.tags,
        schema_json=payload.definition.model_dump(mode="json"),
        is_private=payload.shared_scope == "private",
        shared_scope=payload.shared_scope,
    )
    session.add(template)
    await session.commit()
    return {
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "category": template.category,
            "tags": template.tags,
            "schema_json": template.schema_json,
            "shared_scope": template.shared_scope,
            "created_at": template.created_at,
        }
    }


@router.get("/charts/candles")
async def chart_candles(
    exchange: str = Query(default="binance", min_length=2, max_length=40),
    symbol: str = Query(..., min_length=3, max_length=40),
    timeframe: str = Query(default="15m", min_length=2, max_length=16),
    limit: int = Query(default=200, ge=10, le=1000),
    _: UserPrincipal = Depends(get_dashboard_principal),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> dict[str, Any]:
    try:
        candles = await provider.fetch_ohlcv(exchange, symbol, timeframe, limit)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Market data unavailable: {type(exc).__name__}",
        ) from exc
    return {
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "items": [
            {
                "timestamp": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "quote_volume": candle.quote_volume,
                "is_closed": candle.is_closed,
            }
            for candle in candles
        ],
    }


@router.get("/charts/setup/{setup_id}")
async def setup_chart(
    setup_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    setup = await session.get(SetupInstance, setup_id)
    if setup is None or setup.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Setup not found")
    return {
        "setup": {
            "id": setup.id,
            "exchange": setup.exchange,
            "symbol": setup.symbol,
            "timeframe": setup.timeframe,
            "state": setup.state.value,
            "completion_score": float(setup.completion_score),
            "first_detected_at": setup.first_detected_at,
            "last_evaluated_at": setup.last_evaluated_at,
        },
        "overlays": {
            "entry_zone_low": float(setup.entry_zone_low) if setup.entry_zone_low else None,
            "entry_zone_high": float(setup.entry_zone_high) if setup.entry_zone_high else None,
            "stop_price": float(setup.stop_price) if setup.stop_price else None,
            "target_price": float(setup.target_price) if setup.target_price else None,
            "target_levels": setup.target_levels,
        },
        "markers": [
            {"time": setup.first_detected_at, "label": "detected"},
            {"time": setup.confirmed_at, "label": "confirmed"} if setup.confirmed_at else None,
        ],
    }


@router.get("/lifecycles/{setup_id}/chart")
async def lifecycle_chart(
    setup_id: UUID,
    timeframe: str = Query(
        default="1m",
        pattern=r"^(1|3|5|15|30)m$|^(1|2|4|6|8|12)h$|^1d$",
    ),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> dict[str, Any]:
    setup = await _owned_setup(session, principal.user_id, setup_id)
    condition_rows = (
        await session.execute(
            select(SetupConditionResult, StrategyCondition)
            .join(
                StrategyCondition,
                StrategyCondition.id == SetupConditionResult.strategy_condition_id,
            )
            .where(SetupConditionResult.setup_instance_id == setup.id)
            .order_by(SetupConditionResult.evaluated_at.asc())
        )
    ).all()
    events = (
        await session.scalars(
            select(SetupLifecycleEvent)
            .where(SetupLifecycleEvent.setup_instance_id == setup.id)
            .order_by(SetupLifecycleEvent.occurred_at.asc())
        )
    ).all()
    relevant_times = [
        ensure_aware(result.candle_timestamp)
        for result, definition in condition_rows
        if definition.timeframe == timeframe
    ]
    relevant_times.extend(ensure_aware(event.occurred_at) for event in events)
    earliest = min(
        relevant_times or [ensure_aware(setup.first_detected_at)],
    )
    latest = max(
        relevant_times or [ensure_aware(setup.last_evaluated_at)],
    )
    duration = timeframe_duration(timeframe)
    start = earliest - duration * 100
    end = latest + duration * 150
    if (end - start) / duration > 1000:
        start = end - duration * 1000

    try:
        range_fetcher = getattr(provider, "fetch_ohlcv_range", None)
        if callable(range_fetcher):
            candles = await range_fetcher(
                setup.exchange,
                setup.symbol,
                timeframe,
                start,
                end,
                1000,
            )
        else:
            candles = await provider.fetch_ohlcv(
                setup.exchange,
                setup.symbol,
                timeframe,
                1000,
            )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Market data unavailable: {type(exc).__name__}",
        ) from exc
    candles = [
        candle
        for candle in candles
        if start <= ensure_aware(candle.timestamp) <= end
    ]
    if not candles:
        raise HTTPException(status_code=404, detail="No candles available for this lifecycle")

    candle_start = ensure_aware(candles[0].timestamp)
    candle_end = ensure_aware(candles[-1].timestamp)
    marker_keys: set[tuple[str, datetime]] = set()
    condition_markers: list[dict[str, Any]] = []
    latest_conditions: dict[str, tuple[SetupConditionResult, StrategyCondition]] = {}
    for result, definition in reversed(condition_rows):
        latest_conditions.setdefault(result.condition_key, (result, definition))
    for result, definition in condition_rows:
        candle_time = ensure_aware(result.candle_timestamp)
        marker_key = (result.condition_key, candle_time)
        if (
            definition.timeframe != timeframe
            or result.outcome != ConditionOutcome.PASSED
            or marker_key in marker_keys
            or not candle_start <= candle_time <= candle_end
        ):
            continue
        marker_keys.add(marker_key)
        condition_markers.append(
            {
                "time": candle_time,
                "label": result.condition_key,
                "text": _short_marker_label(definition.label),
                "kind": "condition",
                "position": "belowBar",
            }
        )
    lifecycle_markers = [
        {
            "time": event.occurred_at,
            "label": event.to_state.value,
            "text": _short_marker_label(state_label(event.to_state)),
            "kind": "lifecycle",
            "position": "aboveBar",
        }
        for event in events
        if candle_start <= ensure_aware(event.occurred_at) <= candle_end
    ]
    conditions = [
        _lifecycle_condition_payload(result, definition)
        for result, definition in latest_conditions.values()
    ]
    snapshot = await _symbol_chart_snapshot(
        session,
        principal.user_id,
        setup.exchange,
        setup.symbol,
        timeframe,
    )
    configured_timeframes = {
        setup.timeframe,
        *(
            definition.timeframe
            for _, definition in condition_rows
            if definition.timeframe is not None
        ),
    }
    standard_timeframes = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"]
    return {
        "setup": {
            "id": setup.id,
            "symbol": setup.symbol,
            "exchange": setup.exchange,
            "timeframe": setup.timeframe,
            "selected_timeframe": timeframe,
            "direction": setup.direction,
            "state": setup.state.value,
            "state_label": state_label(setup.state),
            "completion_score": float(setup.completion_score),
        },
        "timeframes": [
            item
            for item in standard_timeframes
            if item in configured_timeframes or item in {"1m", "5m", "15m", "1h", "4h", "1d"}
        ],
        "candles": [
            {
                "timestamp": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "is_closed": candle.is_closed,
            }
            for candle in candles
        ],
        "markers": [*condition_markers[-120:], *lifecycle_markers],
        "overlays": {
            "entry_zone_low": float(setup.entry_zone_low) if setup.entry_zone_low else None,
            "entry_zone_high": float(setup.entry_zone_high) if setup.entry_zone_high else None,
            "stop_price": float(setup.stop_price) if setup.stop_price else None,
            "target_price": float(setup.target_price) if setup.target_price else None,
            "target_levels": setup.target_levels,
        },
        "completed_conditions": [
            condition for condition in conditions if condition["state"] == "passed"
        ],
        "missing_conditions": [
            condition for condition in conditions if condition["state"] != "passed"
        ],
        "annotations": (
            list((snapshot.chart_config or {}).get("annotations", []))
            if snapshot is not None
            else []
        ),
        "annotations_saved_at": snapshot.updated_at if snapshot is not None else None,
        "tradingview_layout": (
            (snapshot.chart_config or {}).get("tradingview_layout")
            if snapshot is not None
            else None
        ),
        "tradingview_drawings": (
            (snapshot.chart_config or {}).get("tradingview_drawings")
            if snapshot is not None
            else None
        ),
    }


@router.get("/lifecycles/{setup_id}/tradingview-layout")
async def lifecycle_tradingview_layout(
    setup_id: UUID,
    timeframe: str = Query(
        default="1m",
        pattern=r"^(1|3|5|15|30)m$|^(1|2|4|6|8|12)h$|^1d$",
    ),
    symbol: str | None = Query(default=None, min_length=3, max_length=40),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    setup = await _owned_setup(session, principal.user_id, setup_id)
    target_symbol = (symbol or setup.symbol).upper()
    snapshot = await _symbol_chart_snapshot(
        session,
        principal.user_id,
        setup.exchange,
        target_symbol,
        timeframe,
    )
    config = snapshot.chart_config if snapshot is not None else {}
    layout = dict(config.get("tradingview_layout") or {})
    chart_id = str(layout.get("chart_id") or f"lifecycle-{setup.id}-{target_symbol}-{timeframe}")
    return {
        "saved": bool(layout.get("chart_data")),
        "setup_id": setup.id,
        "symbol": target_symbol,
        "exchange": setup.exchange,
        "timeframe": timeframe,
        "chart_id": chart_id,
        "layout_id": str(layout.get("layout_id") or chart_id),
        "name": layout.get("name") or f"{target_symbol} lifecycle",
        "chart_data": layout.get("chart_data"),
        "saved_at": layout.get("saved_at"),
        "charts": [
            {
                "id": chart_id,
                "name": layout.get("name") or f"{target_symbol} lifecycle",
                "symbol": target_symbol,
                "resolution": timeframe,
                "timestamp": layout.get("saved_at"),
            }
        ]
        if layout.get("chart_data")
        else [],
    }


@router.put("/lifecycles/{setup_id}/tradingview-layout")
async def save_lifecycle_tradingview_layout(
    setup_id: UUID,
    payload: TradingViewStorageRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    setup = await _owned_setup(session, principal.user_id, setup_id)
    target_symbol = (payload.symbol or setup.symbol).upper()
    chart_id = payload.chart_id or f"lifecycle-{setup.id}-{target_symbol}-{payload.timeframe}"
    layout_id = payload.layout_id or chart_id
    snapshot = await _ensure_symbol_chart_snapshot(
        session,
        principal.user_id,
        setup.exchange,
        target_symbol,
        payload.timeframe,
        setup_id=setup.id,
        strategy_version_id=setup.strategy_version_id,
    )
    config = dict(snapshot.chart_config or {})
    config["tradingview_layout"] = {
        "chart_id": chart_id,
        "layout_id": layout_id,
        "name": payload.name or f"{target_symbol} lifecycle",
        "symbol": target_symbol,
        "timeframe": payload.timeframe,
        "chart_data": payload.chart_data,
        "saved_at": _now().isoformat(),
        "library": "tradingview-charting-library",
    }
    snapshot.chart_config = config
    snapshot.proof_reference = {
        "setup_instance_id": str(setup.id),
        "strategy_version_id": str(setup.strategy_version_id),
    }
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="user",
            action="lifecycle_chart.tradingview_layout_saved",
            target_type="setup_instance",
            target_id=str(setup.id),
            metadata_redacted={
                "symbol": target_symbol,
                "timeframe": payload.timeframe,
                "chart_id": chart_id,
            },
            created_at=_now(),
        )
    )
    await session.commit()
    await session.refresh(snapshot)
    return {
        "saved": True,
        "setup_id": setup.id,
        "symbol": target_symbol,
        "timeframe": payload.timeframe,
        "chart_id": chart_id,
        "layout_id": layout_id,
        "saved_at": snapshot.updated_at,
    }


@router.get("/lifecycles/{setup_id}/tradingview-drawings")
async def lifecycle_tradingview_drawings(
    setup_id: UUID,
    timeframe: str = Query(
        default="1m",
        pattern=r"^(1|3|5|15|30)m$|^(1|2|4|6|8|12)h$|^1d$",
    ),
    symbol: str | None = Query(default=None, min_length=3, max_length=40),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    setup = await _owned_setup(session, principal.user_id, setup_id)
    target_symbol = (symbol or setup.symbol).upper()
    snapshot = await _symbol_chart_snapshot(
        session,
        principal.user_id,
        setup.exchange,
        target_symbol,
        timeframe,
    )
    config = snapshot.chart_config if snapshot is not None else {}
    drawings = dict(config.get("tradingview_drawings") or {})
    return {
        "saved": bool(drawings.get("line_tools_state")),
        "setup_id": setup.id,
        "symbol": target_symbol,
        "exchange": setup.exchange,
        "timeframe": timeframe,
        "layout_id": drawings.get("layout_id"),
        "chart_id": drawings.get("chart_id"),
        "line_tools_state": drawings.get("line_tools_state"),
        "saved_at": drawings.get("saved_at"),
    }


@router.put("/lifecycles/{setup_id}/tradingview-drawings")
async def save_lifecycle_tradingview_drawings(
    setup_id: UUID,
    payload: TradingViewStorageRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    setup = await _owned_setup(session, principal.user_id, setup_id)
    target_symbol = (payload.symbol or setup.symbol).upper()
    chart_id = payload.chart_id or f"lifecycle-{setup.id}-{target_symbol}-{payload.timeframe}"
    layout_id = payload.layout_id or chart_id
    snapshot = await _ensure_symbol_chart_snapshot(
        session,
        principal.user_id,
        setup.exchange,
        target_symbol,
        payload.timeframe,
        setup_id=setup.id,
        strategy_version_id=setup.strategy_version_id,
    )
    config = dict(snapshot.chart_config or {})
    config["tradingview_drawings"] = {
        "chart_id": chart_id,
        "layout_id": layout_id,
        "symbol": target_symbol,
        "timeframe": payload.timeframe,
        "line_tools_state": payload.line_tools_state,
        "saved_at": _now().isoformat(),
        "library": "tradingview-charting-library",
    }
    snapshot.chart_config = config
    snapshot.proof_reference = {
        "setup_instance_id": str(setup.id),
        "strategy_version_id": str(setup.strategy_version_id),
    }
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="user",
            action="lifecycle_chart.tradingview_drawings_saved",
            target_type="setup_instance",
            target_id=str(setup.id),
            metadata_redacted={
                "symbol": target_symbol,
                "timeframe": payload.timeframe,
                "chart_id": chart_id,
            },
            created_at=_now(),
        )
    )
    await session.commit()
    await session.refresh(snapshot)
    return {
        "saved": True,
        "setup_id": setup.id,
        "symbol": target_symbol,
        "timeframe": payload.timeframe,
        "chart_id": chart_id,
        "layout_id": layout_id,
        "saved_at": snapshot.updated_at,
    }


@router.put("/lifecycles/{setup_id}/annotations")
async def save_lifecycle_annotations(
    setup_id: UUID,
    payload: LifecycleAnnotationSaveRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    setup = await _owned_setup(session, principal.user_id, setup_id)
    snapshot = await _symbol_chart_snapshot(
        session,
        principal.user_id,
        setup.exchange,
        setup.symbol,
        payload.timeframe,
    )
    annotation_payload = [
        annotation.model_dump(mode="json", exclude_none=True)
        for annotation in payload.annotations
    ]
    if snapshot is None:
        snapshot = ChartSnapshot(
            user_id=principal.user_id,
            subject_type="symbol_workspace",
            subject_id=_symbol_workspace_id(setup.exchange, setup.symbol),
            exchange=setup.exchange,
            symbol=setup.symbol,
            timeframe=payload.timeframe,
            chart_config={},
            proof_reference={"setup_instance_id": str(setup.id)},
        )
        session.add(snapshot)
    snapshot.chart_config = {
        "annotations": annotation_payload,
        "saved_at": _now().isoformat(),
        "library": "tradingview-lightweight-charts",
    }
    snapshot.proof_reference = {
        "setup_instance_id": str(setup.id),
        "strategy_version_id": str(setup.strategy_version_id),
    }
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="user",
            action="lifecycle_chart.annotations_saved",
            target_type="setup_instance",
            target_id=str(setup.id),
            metadata_redacted={
                "timeframe": payload.timeframe,
                "annotation_count": len(annotation_payload),
            },
            created_at=_now(),
        )
    )
    await session.commit()
    await session.refresh(snapshot)
    return {
        "saved": True,
        "setup_id": setup.id,
        "timeframe": payload.timeframe,
        "annotation_count": len(annotation_payload),
        "saved_at": snapshot.updated_at,
    }


@router.post("/lifecycles/{setup_id}/mute")
async def mute_lifecycle(
    setup_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    setup = await _owned_setup(session, principal.user_id, setup_id)
    preference = await session.scalar(
        select(DashboardPreference).where(DashboardPreference.user_id == principal.user_id)
    )
    if preference is None:
        preference = DashboardPreference(user_id=principal.user_id)
        session.add(preference)
        await session.flush()
    prefs = dict(preference.notification_preferences or {})
    muted = {str(item) for item in prefs.get("muted_setup_instance_ids", [])}
    muted.add(str(setup.id))
    prefs["muted_setup_instance_ids"] = sorted(muted)
    preference.notification_preferences = prefs
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="user",
            action="setup.lifecycle_muted",
            target_type="setup_instance",
            target_id=str(setup.id),
            metadata_redacted={"symbol": setup.symbol, "state": setup.state.value},
            created_at=_now(),
        )
    )
    await session.commit()
    return {"muted": True, "setup_id": setup.id}


@router.get("/charts/alert/{alert_id}")
async def alert_chart(
    alert_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    alert = await session.get(Alert, alert_id)
    if alert is None or alert.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {
        "alert": {
            "id": alert.id,
            "title": alert.title,
            "alert_type": alert.alert_type.value,
            "candle_timestamp": alert.candle_timestamp,
            "chart_snapshot_url": alert.chart_snapshot_url,
        },
        "proof": alert.proof_receipt,
        "overlays": {
            "entry_zone": alert.proof_receipt.get("entry_zone"),
            "invalidation": alert.proof_receipt.get("invalidation_level"),
            "stop": alert.proof_receipt.get("stop"),
            "targets": alert.proof_receipt.get("targets", []),
        },
    }


@router.get("/notifications/web")
async def web_notifications(
    limit: int = Query(default=5, ge=1, le=20),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(AlertDelivery, Alert)
            .join(Alert, Alert.id == AlertDelivery.alert_id)
            .where(
                Alert.user_id == principal.user_id,
                AlertDelivery.channel == DeliveryChannel.WEB,
                AlertDelivery.destination_key == f"dashboard:{principal.user_id}",
                AlertDelivery.status == DeliveryStatus.PENDING,
            )
            .order_by(AlertDelivery.created_at.desc())
            .limit(limit)
        )
    ).all()
    items: list[dict[str, Any]] = []
    now = _now()
    for delivery, alert in rows:
        proof = alert.proof_receipt or {}
        score = proof.get("setup_completion_score", proof.get("completion_score"))
        try:
            completion_rate = int(round(float(score))) if score is not None else None
        except (TypeError, ValueError):
            completion_rate = None
        symbol = str(proof.get("symbol") or "Market")
        quick_title = alert.title
        if alert.alert_type == AlertType.NEAR_MISS and completion_rate is not None:
            quick_title = f"{symbol} is {completion_rate}% complete"
        elif alert.alert_type == AlertType.CONFIRMED:
            quick_title = f"{symbol} setup confirmed"
        elif alert.alert_type == AlertType.LIFECYCLE:
            quick_title = f"{symbol} lifecycle update"
        items.append(
            {
                "id": delivery.id,
                "alert_id": alert.id,
                "title": quick_title,
                "symbol": symbol,
                "completion_rate": completion_rate,
                "alert_type": alert.alert_type.value,
                "created_at": alert.created_at,
            }
        )
        delivery.status = DeliveryStatus.DELIVERED
        delivery.delivered_at = now
    dashboard_rows = (
        await session.scalars(
            select(DashboardNotification)
            .where(
                DashboardNotification.user_id == principal.user_id,
                DashboardNotification.read_at.is_(None),
            )
            .order_by(DashboardNotification.created_at.desc())
            .limit(limit)
        )
    ).all()
    for notification in dashboard_rows:
        items.append(
            {
                "id": notification.id,
                "alert_id": None,
                "title": notification.title,
                "symbol": "Monitor Health",
                "completion_rate": None,
                "alert_type": "system_notice",
                "created_at": notification.created_at,
                "body": notification.body,
                "action_url": notification.action_url,
            }
        )
        notification.read_at = now
    if rows or dashboard_rows:
        await session.commit()
    return {"items": items}


async def create_setup_replay(
    payload: SetupReplayCreateRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    strategy_version_id = payload.strategy_version_id
    if payload.strategy_id is not None:
        await _owned_strategy(session, principal.user_id, payload.strategy_id)
    if strategy_version_id is not None:
        await _owned_version(session, principal.user_id, strategy_version_id)
    if strategy_version_id is None and payload.strategy_id is not None:
        latest = await _latest_version(session, payload.strategy_id)
        strategy_version_id = latest.id if latest else None
    job = SetupReplayJob(
        user_id=principal.user_id,
        strategy_id=payload.strategy_id,
        strategy_version_id=strategy_version_id,
        exchange=payload.exchange,
        symbol=payload.symbol.upper().replace("-", "/"),
        timeframe=payload.timeframe,
        approximate_time=payload.approximate_time,
        window_before_minutes=payload.window_before_minutes,
        window_after_minutes=payload.window_after_minutes,
        status="queued",
        requested_at=_now(),
        parameters={"user_question": payload.user_question},
    )
    session.add(job)
    await session.flush()
    session.add(
        SetupReplayResult(
            replay_job_id=job.id,
            summary={
                "status": "queued",
                "message": (
                    "Replay request queued. The dashboard worker will evaluate the candle "
                    "window and produce chart overlays plus condition proof rows."
                ),
            },
            timeline_points=[],
            candle_proofs=[],
            suggested_adjustments=[],
            created_at=_now(),
        )
    )
    await session.commit()
    return {"job": _replay_job_payload(job)}


@router.post("/scan-now", response_model=OnDemandScanResponse, status_code=status.HTTP_201_CREATED)
async def dashboard_scan_now(
    payload: OnDemandScanRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> OnDemandScanResponse:
    try:
        response = await OnDemandScanService(session, provider).run(principal.user_id, payload)
        await session.commit()
        return response
    except OnDemandScanError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.post("/scan-now/interpret")
async def dashboard_scan_prompt_interpret(
    payload: ScanPromptInterpretRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    preview = await configured_strategy_interpreter(settings).interpret(
        _guided_from_scan_prompt(payload)
    )
    from ai_market_monitor.cockpit_service import StrategyCockpitService

    preference = await StrategyCockpitService(session).strategy_preferences(
        principal.user_id
    )
    strategy = preview.strategy.model_copy(deep=True)
    prompt_text = payload.prompt.casefold()
    applied_preferences: list[str] = []
    preferred_trigger = preference.preferences.get("preferred_trigger_mode")
    if (
        preferred_trigger in {"candle_close", "intrabar"}
        and "candle close" not in prompt_text
        and "intrabar" not in prompt_text
    ):
        strategy.trigger_mode = type(strategy.trigger_mode)(preferred_trigger)
        applied_preferences.append(f"trigger mode: {preferred_trigger}")
    preferred_channels = preference.preferences.get("preferred_alert_channels")
    if (
        isinstance(preferred_channels, list)
        and preferred_channels
        and not any(channel in prompt_text for channel in ("telegram", "discord", "web"))
    ):
        strategy.alerts.channels = [
            channel
            for channel in preferred_channels
            if channel in {"telegram", "discord", "web"}
        ] or strategy.alerts.channels
        applied_preferences.append(
            "alert channels: " + ", ".join(strategy.alerts.channels)
        )
    typical_maximum = preference.preferences.get("typical_max_alerts_per_hour")
    if (
        isinstance(typical_maximum, int | float)
        and "alert" not in prompt_text
        and "frequency" not in prompt_text
    ):
        strategy.alerts.maximum_alerts_per_hour = max(
            1,
            min(1000, int(typical_maximum)),
        )
        applied_preferences.append(
            f"maximum alerts per hour: {strategy.alerts.maximum_alerts_per_hour}"
        )
    preview = preview.model_copy(
        update={
            "strategy": strategy,
            "assumptions": [
                *preview.assumptions,
                *(
                    ["Applied saved strategy preferences: " + "; ".join(applied_preferences)]
                    if applied_preferences
                    else []
                ),
            ],
        }
    )
    await session.commit()
    result = _interpretation_payload(preview)
    result["personal_preferences_applied"] = applied_preferences
    return result


@router.post("/strategies/interpret")
async def dashboard_strategy_builder_interpret(
    payload: StrategyBuilderInterpretRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    prompt = payload.prompt_text()
    context = await EntitlementService(session).current(principal.user_id)
    guided = _guided_from_strategy_builder(payload, prompt)
    preview = await configured_strategy_interpreter(settings).interpret(guided)
    await session.commit()
    result = _interpretation_payload(preview)
    result["visual_diff"] = _strategy_visual_diff(payload.current_schema, preview.strategy)
    result["builder_mode"] = payload.builder_mode
    result["plan_limits"] = payload.plan_limits or context.plan.limits
    result["user_preferences"] = payload.user_preferences
    return result


@router.post("/strategies/interpret/feedback", status_code=status.HTTP_201_CREATED)
async def dashboard_strategy_builder_feedback(
    payload: BuilderInterpretationFeedbackRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    event = AuditEvent(
        actor_user_id=principal.user_id,
        actor_type="dashboard_user",
        action="strategy_builder.interpretation_feedback",
        target_type="strategy_builder_interpretation",
        target_id=None,
        request_id=None,
        ip_hash=None,
        metadata_redacted={
            "feedback_type": payload.feedback_type,
            "comment": payload.comment,
            "raw_prompt": payload.raw_prompt,
            "coverage_score": payload.prompt_coverage_report.get("coverage_score"),
            "confidence_score": payload.prompt_coverage_report.get("confidence_score"),
            "activation_blocked": payload.prompt_coverage_report.get("activation_blocked"),
            "condition_count": len(
                payload.strategy.get("conditions", {}).get("children", [])
                if isinstance(payload.strategy, dict)
                else []
            ),
        },
        created_at=datetime.now(UTC),
    )
    session.add(event)
    await session.commit()
    return {"id": str(event.id), "status": "recorded"}


@router.post("/light-scan", status_code=status.HTTP_201_CREATED)
async def dashboard_light_scan(
    payload: LightScanRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    preview = await configured_strategy_interpreter(settings).interpret(
        _guided_from_scan_prompt(payload)
    )
    interpretation = _interpretation_payload(preview)
    if not _has_executable_conditions(preview.strategy):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "clarification_required",
                "message": (
                    "Quick Scan needs at least one supported deterministic condition. "
                    "Add an indicator, price-action event, timeframe, comparator, or threshold."
                ),
                "interpretation": interpretation,
            },
        )
    warnings = [*preview.assumptions]
    warnings.extend(
        (
            f"Blocking unsupported requirement not scanned: {item.message}"
            if item.blocking
            else f"Unsupported optional idea ignored: {item.message}"
        )
        for item in preview.unsupported_conditions
    )
    request = OnDemandScanRequest(
        strategy=preview.strategy,
        symbols=[symbol.upper().replace("-", "/") for symbol in payload.symbols],
        max_symbols=100000,
        idempotency_key=payload.idempotency_key,
        light_scan=True,
    )
    try:
        response = await OnDemandScanService(session, provider).run(principal.user_id, request)
    except OnDemandScanError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc), "interpretation": interpretation},
        ) from exc
    response.warnings.extend(warnings)
    response.results = response.results[: payload.max_results]
    await session.commit()
    return {
        "interpretation": interpretation,
        "scan": response,
        "warnings": response.warnings,
        "light_scan": True,
        "interpreted_rules": interpretation["interpreted_rules"],
        "required_rules": interpretation["required_rules"],
        "optional_rules": interpretation["optional_rules"],
        "ignored_optional_rules": interpretation["ignored_optional_rules"],
        "blocking_unsupported_rules": interpretation["blocking_unsupported_rules"],
        "scan_safety_level": interpretation["scan_safety_level"],
        "light_mode_compatible": interpretation["light_mode_compatible"],
        "suggested_clarifications": interpretation["suggested_clarifications"],
    }


async def list_setup_replays(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    jobs = (
        await session.scalars(
            select(SetupReplayJob)
            .where(SetupReplayJob.user_id == principal.user_id)
            .order_by(SetupReplayJob.created_at.desc())
            .limit(50)
        )
    ).all()
    return {"items": [_replay_job_payload(job) for job in jobs]}


async def run_setup_replay(
    job_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    job = await session.get(SetupReplayJob, job_id)
    if job is None or job.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Replay job not found")
    result = await DashboardJobService(session, provider, settings).run_replay_job(job.id)
    await session.commit()
    return {"job": _replay_job_payload(job), "result": _replay_result_payload(result)}


async def get_setup_replay(
    job_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    job = await session.get(SetupReplayJob, job_id)
    if job is None or job.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Replay job not found")
    result = await session.scalar(
        select(SetupReplayResult).where(SetupReplayResult.replay_job_id == job.id)
    )
    return {"job": _replay_job_payload(job), "result": _replay_result_payload(result)}


async def replay_chart(
    job_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    job = await session.get(SetupReplayJob, job_id)
    if job is None or job.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Replay job not found")
    result = await session.scalar(
        select(SetupReplayResult).where(SetupReplayResult.replay_job_id == job.id)
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Replay result not found")
    summary = result.summary or {}
    return {
        "job": _replay_job_payload(job),
        "status": summary.get("status", job.status),
        "candles": summary.get("candles", []),
        "overlays": summary.get("overlays", []),
        "markers": summary.get("markers", []),
        "timeline_points": result.timeline_points,
        "candle_proofs": result.candle_proofs,
        "best_result": summary.get("best_result"),
        "report": summary.get("report"),
        "suggested_adjustments": result.suggested_adjustments,
    }


async def create_backtest(
    payload: BacktestCreateRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await _owned_strategy(session, principal.user_id, payload.strategy_id)
    strategy_version_id = payload.strategy_version_id
    if strategy_version_id is not None:
        await _owned_version(session, principal.user_id, strategy_version_id)
    elif latest := await _latest_version(session, payload.strategy_id):
        strategy_version_id = latest.id
    job = BacktestJob(
        user_id=principal.user_id,
        strategy_id=payload.strategy_id,
        strategy_version_id=strategy_version_id,
        exchange=payload.exchange,
        symbols=payload.symbols,
        timeframe=payload.timeframe,
        started_at_range=payload.started_at_range,
        ended_at_range=payload.ended_at_range,
        status="queued",
        parameters=payload.parameters,
    )
    session.add(job)
    await session.flush()
    session.add(
        BacktestResult(
            backtest_job_id=job.id,
            metrics={"status": "queued", "message": "Backtest request recorded."},
            equity_curve=[],
            setup_results=[],
            created_at=_now(),
        )
    )
    await session.commit()
    return {"job": _backtest_job_payload(job)}


async def list_backtests(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    jobs = (
        await session.scalars(
            select(BacktestJob)
            .where(BacktestJob.user_id == principal.user_id)
            .order_by(BacktestJob.created_at.desc())
            .limit(50)
        )
    ).all()
    return {"items": [_backtest_job_payload(job) for job in jobs]}


async def get_backtest(
    job_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    job = await session.get(BacktestJob, job_id)
    if job is None or job.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Backtest job not found")
    result = await session.scalar(
        select(BacktestResult).where(BacktestResult.backtest_job_id == job.id)
    )
    return {"job": _backtest_job_payload(job), "result": _backtest_result_payload(result)}


async def run_backtest(
    job_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    job = await session.get(BacktestJob, job_id)
    if job is None or job.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Backtest job not found")
    result = await DashboardJobService(session, provider, settings).run_backtest_job(job.id)
    await session.commit()
    return {"job": _backtest_job_payload(job), "result": _backtest_result_payload(result)}


async def backtest_chart(
    job_id: UUID,
    timeframe: str | None = Query(
        default=None,
        pattern=r"^(1|3|5|15|30)m$|^(1|2|4|6|8|12)h$|^1d$",
    ),
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> dict[str, Any]:
    job = await session.get(BacktestJob, job_id)
    if job is None or job.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Backtest job not found")
    result = await session.scalar(
        select(BacktestResult).where(BacktestResult.backtest_job_id == job.id)
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Backtest result not found")
    stored_chart = dict((result.metrics or {}).get("chart", {}))
    symbol = str(stored_chart.get("symbol") or (job.symbols[0] if job.symbols else ""))
    selected_timeframe = timeframe or job.timeframe
    candles = list(stored_chart.get("candles", []))
    if selected_timeframe != str(stored_chart.get("timeframe") or job.timeframe):
        try:
            range_fetcher = getattr(provider, "fetch_ohlcv_range", None)
            if callable(range_fetcher):
                fetched = await range_fetcher(
                    job.exchange,
                    symbol,
                    selected_timeframe,
                    ensure_aware(job.started_at_range),
                    ensure_aware(job.ended_at_range),
                    1000,
                )
            else:
                fetched = await provider.fetch_ohlcv(
                    job.exchange,
                    symbol,
                    selected_timeframe,
                    1000,
                )
            candles = [
                {
                    "timestamp": candle.timestamp,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "is_closed": candle.is_closed,
                }
                for candle in fetched
                if ensure_aware(job.started_at_range)
                <= ensure_aware(candle.timestamp)
                <= ensure_aware(job.ended_at_range)
            ]
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Market data unavailable: {type(exc).__name__}",
            ) from exc
    if not candles:
        raise HTTPException(status_code=404, detail="No replay candles available")
    candle_times = [
        ensure_aware(datetime.fromisoformat(str(candle["timestamp"])))
        if isinstance(candle["timestamp"], str)
        else ensure_aware(candle["timestamp"])
        for candle in candles
    ]

    def snapped_time(raw_time: Any) -> datetime:
        parsed = (
            ensure_aware(datetime.fromisoformat(str(raw_time)))
            if isinstance(raw_time, str)
            else ensure_aware(raw_time)
        )
        return min(candle_times, key=lambda item: abs(item - parsed))

    score_markers = [
        {
            **marker,
            "time": snapped_time(marker.get("time")),
            "position": (
                "aboveBar" if marker.get("outcome") == "confirmed" else "belowBar"
            ),
            "shape": "arrowUp" if marker.get("outcome") == "confirmed" else "circle",
        }
        for marker in stored_chart.get("markers", [])
        if marker.get("time")
    ]
    condition_markers: list[dict[str, Any]] = []
    seen_condition_markers: set[tuple[str, datetime]] = set()
    for event in stored_chart.get("condition_events", []):
        if not event.get("timestamp"):
            continue
        marker_time = snapped_time(event["timestamp"])
        for condition in event.get("conditions", []):
            if condition.get("state") != "passed":
                continue
            label = _short_marker_label(str(condition.get("name") or condition.get("condition_id")))
            marker_key = (label, marker_time)
            if marker_key in seen_condition_markers:
                continue
            seen_condition_markers.add(marker_key)
            condition_markers.append(
                {
                    "time": marker_time,
                    "position": "belowBar",
                    "shape": "circle",
                    "text": label,
                    "kind": "condition",
                }
            )
    setup_result = next(
        (
            item
            for item in reversed(result.setup_results)
            if str(item.get("symbol")) == symbol
        ),
        {},
    )
    risk = setup_result.get("risk_calculation") or {}
    snapshot = await _symbol_chart_snapshot(
        session,
        principal.user_id,
        job.exchange,
        symbol,
        selected_timeframe,
    )
    standard_timeframes = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"]
    return {
        "job": _backtest_job_payload(job),
        "status": (result.metrics or {}).get("status", job.status),
        "metrics": result.metrics,
        "report": (result.metrics or {}).get("report"),
        "chart": {
            **stored_chart,
            "symbol": symbol,
            "timeframe": selected_timeframe,
            "candles": candles,
            "markers": [*score_markers[-120:], *condition_markers[-120:]],
            "overlays": {
                "entry_zone": setup_result.get("entry_zone"),
                "stop_price": risk.get("stop_price"),
                "targets": risk.get("targets", []),
            },
        },
        "timeframes": standard_timeframes,
        "annotations": (
            list((snapshot.chart_config or {}).get("annotations", []))
            if snapshot is not None
            else []
        ),
        "annotations_saved_at": snapshot.updated_at if snapshot is not None else None,
        "equity_curve": result.equity_curve,
        "setup_results": result.setup_results,
    }


async def save_backtest_annotations(
    job_id: UUID,
    payload: LifecycleAnnotationSaveRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    job = await session.get(BacktestJob, job_id)
    if job is None or job.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Backtest job not found")
    result = await session.scalar(
        select(BacktestResult).where(BacktestResult.backtest_job_id == job.id)
    )
    chart = dict((result.metrics or {}).get("chart", {})) if result is not None else {}
    symbol = str(chart.get("symbol") or (job.symbols[0] if job.symbols else ""))
    if not symbol:
        raise HTTPException(status_code=409, detail="Backtest chart symbol is unavailable")
    snapshot = await _symbol_chart_snapshot(
        session,
        principal.user_id,
        job.exchange,
        symbol,
        payload.timeframe,
    )
    annotations = [
        annotation.model_dump(mode="json", exclude_none=True)
        for annotation in payload.annotations
    ]
    if snapshot is None:
        snapshot = ChartSnapshot(
            user_id=principal.user_id,
            subject_type="symbol_workspace",
            subject_id=_symbol_workspace_id(job.exchange, symbol),
            exchange=job.exchange,
            symbol=symbol,
            timeframe=payload.timeframe,
            chart_config={},
            proof_reference={"backtest_job_id": str(job.id)},
        )
        session.add(snapshot)
    snapshot.chart_config = {
        "annotations": annotations,
        "saved_at": _now().isoformat(),
        "library": "tradingview-lightweight-charts",
    }
    snapshot.proof_reference = {
        **(snapshot.proof_reference or {}),
        "backtest_job_id": str(job.id),
    }
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="user",
            action="backtest_chart.annotations_saved",
            target_type="backtest_job",
            target_id=str(job.id),
            metadata_redacted={
                "timeframe": payload.timeframe,
                "annotation_count": len(annotations),
            },
            created_at=_now(),
        )
    )
    await session.commit()
    return {
        "saved": True,
        "job_id": job.id,
        "timeframe": payload.timeframe,
        "annotation_count": len(annotations),
    }


@router.post("/exports", status_code=status.HTTP_201_CREATED)
async def create_export(
    payload: ExportCreateRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    job = UserExportJob(
        user_id=principal.user_id,
        export_type=payload.export_type,
        format=payload.format,
        status="queued",
        requested_at=_now(),
        filters=payload.filters,
    )
    session.add(job)
    await session.commit()
    return {"job": _export_job_payload(job)}


@router.get("/exports")
async def list_exports(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    jobs = (
        await session.scalars(
            select(UserExportJob)
            .where(UserExportJob.user_id == principal.user_id)
            .order_by(UserExportJob.created_at.desc())
            .limit(50)
        )
    ).all()
    return {"items": [_export_job_payload(job) for job in jobs]}


@router.post("/exports/{job_id}/run")
async def run_export(
    job_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    provider: MarketDataProvider = Depends(get_market_data_provider),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    job = await session.get(UserExportJob, job_id)
    if job is None or job.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Export job not found")
    job = await DashboardJobService(session, provider, settings).run_export_job(job.id)
    await session.commit()
    return {"job": _export_job_payload(job)}


@router.get("/exports/{job_id}/download")
async def download_export(
    job_id: UUID,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    job = await session.get(UserExportJob, job_id)
    if job is None or job.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Export job not found")
    path = export_file_path(settings, job)
    if job.status != "succeeded" or not path.exists():
        raise HTTPException(status_code=409, detail="Export is not ready for download")
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8" if job.format == "csv" else "application/json",
        filename=f"ai-market-monitor-{job.export_type}-{job.id}.{job.format}",
    )


@router.get("/analytics/overview")
async def analytics_overview(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    setup_totals = await session.execute(
        select(
            func.count(SetupInstance.id),
            func.coalesce(
                func.sum(
                    case((SetupInstance.state.in_(["confirmed", "entry_active"]), 1), else_=0)
                ),
                0,
            ),
            func.coalesce(func.avg(SetupInstance.completion_score), 0),
        ).where(SetupInstance.user_id == principal.user_id)
    )
    setup_count, active_like_count, avg_score = setup_totals.one()
    alert_count = await session.scalar(
        select(func.count(Alert.id)).where(Alert.user_id == principal.user_id)
    )
    near_miss_count = await session.scalar(
        select(func.count(NearMissSnapshot.id))
        .join(StrategyVersion, NearMissSnapshot.strategy_version_id == StrategyVersion.id)
        .join(Strategy, StrategyVersion.strategy_id == Strategy.id)
        .where(
            Strategy.user_id == principal.user_id,
            NearMissSnapshot.completion_score < 100,
        )
    )
    return {
        "setup_count": setup_count or 0,
        "active_like_setup_count": active_like_count or 0,
        "average_completion_score": float(avg_score or 0),
        "alert_count": alert_count or 0,
        "near_miss_count": near_miss_count or 0,
        "generated_at": _now(),
    }


@router.get("/analytics/coverage")
async def analytics_coverage(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await market_coverage_for_user(session, principal.user_id)


@router.get("/analytics/symbols")
async def analytics_symbols(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(
                SetupInstance.symbol,
                func.count(SetupInstance.id).label("setups"),
                func.coalesce(func.avg(SetupInstance.completion_score), 0).label("avg_score"),
                func.max(SetupInstance.last_evaluated_at).label("last_seen"),
            )
            .where(SetupInstance.user_id == principal.user_id)
            .group_by(SetupInstance.symbol)
            .order_by(func.count(SetupInstance.id).desc())
            .limit(50)
        )
    ).all()
    return {
        "items": [
            {
                "symbol": row.symbol,
                "setup_count": row.setups,
                "average_completion_score": float(row.avg_score or 0),
                "last_seen": row.last_seen,
            }
            for row in rows
        ]
    }


@router.get("/analytics/setups")
async def analytics_setups(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(SetupInstance.state, func.count(SetupInstance.id))
            .where(SetupInstance.user_id == principal.user_id)
            .group_by(SetupInstance.state)
        )
    ).all()
    return {"items": [{"state": state.value, "count": count} for state, count in rows]}


@router.get("/billing/status")
async def billing_status(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    entitlement = await EntitlementService(session).current(principal.user_id)
    trial = await session.scalar(select(Trial).where(Trial.user_id == principal.user_id))
    return {
        "plan": {
            "code": entitlement.plan.code,
            "name": entitlement.plan.name,
            "limits": entitlement.plan.limits,
            "features": entitlement.plan.features,
        },
        "source": entitlement.source,
        "trial": {
            "status": trial.status.value,
            "starts_at": trial.starts_at,
            "ends_at": trial.ends_at,
        }
        if trial
        else None,
    }


@router.get("/integrations")
async def integrations(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    telegram = await session.scalar(
        select(TelegramConnection).where(TelegramConnection.user_id == principal.user_id)
    )
    discord = await session.scalar(
        select(DiscordConnection).where(DiscordConnection.user_id == principal.user_id)
    )
    destinations = (
        await session.scalars(
            select(DiscordDeliveryDestination).where(
                DiscordDeliveryDestination.user_id == principal.user_id
            )
        )
    ).all()
    tests = (
        await session.scalars(
            select(IntegrationTestResult)
            .where(IntegrationTestResult.user_id == principal.user_id)
            .order_by(IntegrationTestResult.created_at.desc())
            .limit(10)
        )
    ).all()
    return {
        "telegram": _telegram_payload(telegram),
        "discord": _discord_payload(discord),
        "discord_destinations": [
            {
                "id": destination.id,
                "mode": destination.mode,
                "guild_id": destination.guild_id,
                "channel_id": destination.channel_id,
                "permissions_status": destination.permissions_status,
                "test_status": destination.test_status,
                "status": destination.status,
                "is_default": destination.is_default,
            }
            for destination in destinations
        ],
        "recent_tests": [
            {
                "id": test.id,
                "integration": test.integration,
                "destination": test.destination,
                "status": test.status,
                "error_code": test.error_code,
                "created_at": test.created_at,
            }
            for test in tests
        ],
    }


@router.delete("/integrations/telegram")
async def disconnect_telegram(
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    connection = await session.scalar(
        select(TelegramConnection).where(TelegramConnection.user_id == principal.user_id)
    )
    if connection is None:
        return {"ok": True, "telegram": None, "already_disconnected": True}

    telegram_user_id = connection.telegram_user_id
    identities = (
        await session.scalars(
            select(UserIdentity).where(
                UserIdentity.user_id == principal.user_id,
                UserIdentity.provider == IdentityProvider.TELEGRAM,
            )
        )
    ).all()
    conversations = (
        await session.scalars(
            select(TelegramConversationState).where(
                TelegramConversationState.telegram_user_id == telegram_user_id
            )
        )
    ).all()
    for conversation in conversations:
        await session.delete(conversation)
    for identity in identities:
        await session.delete(identity)
    await session.delete(connection)
    session.add(
        AuditEvent(
            actor_user_id=principal.user_id,
            actor_type="dashboard_user",
            action="telegram.disconnected",
            target_type="telegram_connection",
            target_id=telegram_user_id,
            metadata_redacted={"source": "dashboard"},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return {"ok": True, "telegram": None}


@router.post("/support/tickets", status_code=status.HTTP_201_CREATED)
async def create_support_ticket(
    payload: SupportTicketCreateRequest,
    principal: UserPrincipal = Depends(get_dashboard_principal),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    decoded_screenshots: list[tuple[str, str, bytes]] = []
    extension_by_type = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    signature_checks = {
        "image/png": lambda value: value.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": lambda value: value.startswith(b"\xff\xd8\xff"),
        "image/webp": lambda value: (
            len(value) >= 12 and value.startswith(b"RIFF") and value[8:12] == b"WEBP"
        ),
    }
    for index, screenshot in enumerate(payload.screenshots):
        try:
            content = base64.b64decode(screenshot.data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid screenshot encoding") from exc
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Each screenshot must be 5 MB or smaller")
        if not signature_checks[screenshot.content_type](content):
            raise HTTPException(
                status_code=422,
                detail="Screenshot content does not match its declared image type",
            )
        extension = extension_by_type[screenshot.content_type]
        decoded_screenshots.append(
            (f"screenshot-{index + 1}{extension}", screenshot.content_type, content)
        )
    contact_email = str(payload.email) if payload.email else await _user_email(
        session,
        principal.user_id,
    )
    context = {
        **payload.context,
        "source": payload.context.get("source", "dashboard"),
        "contact_email": contact_email,
        "screenshot_count": len(decoded_screenshots),
    }
    ticket = SupportRequest(
        user_id=principal.user_id,
        category=payload.category,
        priority="normal",
        subject=payload.subject,
        description=payload.description,
        context=context,
    )
    session.add(ticket)
    await session.flush()
    attachment_rows: list[dict[str, Any]] = []
    if decoded_screenshots:
        storage_root = (
            Path(settings.dashboard_export_directory).resolve()
            / "support"
            / str(principal.user_id)
            / str(ticket.id)
        )
        storage_root.mkdir(parents=True, exist_ok=True)
        for filename, content_type, content in decoded_screenshots:
            stored_name = f"{uuid4().hex}-{filename}"
            path = storage_root / stored_name
            path.write_bytes(content)
            attachment_rows.append(
                {
                    "filename": filename,
                    "content_type": content_type,
                    "size_bytes": len(content),
                    "storage_key": str(
                        path.relative_to(
                            Path(settings.dashboard_export_directory).resolve()
                        )
                    ),
                }
            )
        ticket.context = {**context, "attachments": attachment_rows}
    message = SupportTicketMessage(
        support_request_id=ticket.id,
        author_user_id=principal.user_id,
        author_type="user",
        body=payload.description,
        attachments=attachment_rows,
        internal=False,
        created_at=_now(),
    )
    session.add(message)
    await session.commit()
    try:
        await AuthEmailService(settings).send_support_ticket(
            recipient=settings.support_inbox_email,
            ticket_id=ticket.id,
            user_id=principal.user_id,
            requester_email=contact_email,
            subject=payload.subject,
            description=payload.description,
            context=ticket.context,
            screenshots=decoded_screenshots,
        )
    except EmailDeliveryError as exc:
        logger.warning(
            "support.ticket_email_failed",
            ticket_id=str(ticket.id),
            error_code=exc.code,
        )
    await AdminNotificationService(settings).send_support_ticket(
        (
            f"Support request\n"
            f"Email: {contact_email or 'not provided'}\n"
            f"Subject: {payload.subject}\n"
            f"User: {principal.user_id}\n"
            f"Ticket: {ticket.id}\n"
            f"Summary: {payload.description[:400]}"
        ),
        decoded_screenshots,
    )
    return {
        "ticket": {
            "id": ticket.id,
            "category": ticket.category,
            "subject": ticket.subject,
            "status": ticket.status.value,
            "contact_email": contact_email,
            "screenshot_count": len(decoded_screenshots),
            "created_at": ticket.created_at,
        },
        "message": {"id": message.id, "created_at": message.created_at},
    }


def _replay_job_payload(job: SetupReplayJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "strategy_id": job.strategy_id,
        "strategy_version_id": job.strategy_version_id,
        "exchange": job.exchange,
        "symbol": job.symbol,
        "timeframe": job.timeframe,
        "approximate_time": job.approximate_time,
        "window_before_minutes": job.window_before_minutes,
        "window_after_minutes": job.window_after_minutes,
        "status": job.status,
        "requested_at": job.requested_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "error_code": job.error_code,
    }


def _replay_result_payload(result: SetupReplayResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "id": result.id,
        "summary": result.summary,
        "timeline_points": result.timeline_points,
        "candle_proofs": result.candle_proofs,
        "suggested_adjustments": result.suggested_adjustments,
        "created_at": result.created_at,
    }


def _backtest_job_payload(job: BacktestJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "strategy_id": job.strategy_id,
        "strategy_version_id": job.strategy_version_id,
        "exchange": job.exchange,
        "symbols": job.symbols,
        "timeframe": job.timeframe,
        "started_at_range": job.started_at_range,
        "ended_at_range": job.ended_at_range,
        "status": job.status,
        "created_at": job.created_at,
    }


def _backtest_result_payload(result: BacktestResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "id": result.id,
        "metrics": result.metrics,
        "equity_curve": result.equity_curve,
        "setup_results": result.setup_results,
        "created_at": result.created_at,
    }


def _export_job_payload(job: UserExportJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "export_type": job.export_type,
        "format": job.format,
        "status": job.status,
        "file_url": job.file_url,
        "requested_at": job.requested_at,
        "completed_at": job.completed_at,
        "error_code": job.error_code,
        "filters": job.filters,
    }


def _telegram_payload(connection: TelegramConnection | None) -> dict[str, Any] | None:
    if connection is None:
        return None
    return {
        "id": connection.id,
        "telegram_user_id": connection.telegram_user_id,
        "username": connection.username,
        "status": connection.status.value,
        "alerts_enabled": connection.alerts_enabled,
        "connected_at": connection.connected_at,
        "last_delivery_at": connection.last_delivery_at,
        "last_error_code": connection.last_error_code,
    }


def _discord_payload(connection: DiscordConnection | None) -> dict[str, Any] | None:
    if connection is None:
        return None
    return {
        "id": connection.id,
        "discord_user_id": connection.discord_user_id,
        "guild_id": connection.guild_id,
        "channel_id": connection.channel_id,
        "status": connection.status.value,
        "alerts_enabled": connection.alerts_enabled,
        "connected_at": connection.connected_at,
        "last_delivery_at": connection.last_delivery_at,
        "last_error_code": connection.last_error_code,
    }


# Imported last so the cockpit router can reuse the dashboard session principal
# without creating a circular import during module initialization.
from ai_market_monitor.cockpit_api import router as cockpit_router  # noqa: E402

router.include_router(cockpit_router)
