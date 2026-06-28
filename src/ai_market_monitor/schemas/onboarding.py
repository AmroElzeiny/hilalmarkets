from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from ai_market_monitor.db.models.enums import IdentityProvider, OnboardingStatus, OnboardingStep
from ai_market_monitor.schemas.strategy import InterpretationPreview, StrategyDefinition


class AttributionInput(BaseModel):
    source: str | None = Field(default=None, max_length=100)
    medium: str | None = Field(default=None, max_length=100)
    campaign: str | None = Field(default=None, max_length=160)
    referrer: str | None = Field(default=None, max_length=2000)
    referral_code: str | None = Field(default=None, max_length=64)
    landing_path: str | None = Field(default=None, max_length=500)
    consented: bool = False
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class IdentityInput(BaseModel):
    provider: IdentityProvider
    provider_subject: str = Field(min_length=1, max_length=255)
    email: EmailStr | None = None
    display_identifier: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, max_length=120)
    verified: bool = False
    profile_data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_email(self) -> "IdentityInput":
        if self.provider == IdentityProvider.EMAIL and self.email is None:
            raise ValueError("email identity requires email")
        return self


class StartOnboardingRequest(BaseModel):
    identity: IdentityInput
    entry_channel: Literal["web", "telegram", "discord"]
    attribution: AttributionInput = Field(default_factory=AttributionInput)
    identity_assertion: str | None = Field(default=None, min_length=20, max_length=2000)


class ContinuationRequest(BaseModel):
    token: str = Field(min_length=20, max_length=4000)


class OnboardingSessionResponse(BaseModel):
    session_id: UUID
    user_id: UUID
    status: OnboardingStatus
    current_step: OnboardingStep
    state_data: dict[str, Any]
    continuation_token: str | None = None
    session_token: str | None = None
    action_required: str | None = None


class DisclaimerRequest(BaseModel):
    identity_id: UUID
    accepted: Literal[True]
    acceptance_source: Literal["web", "telegram", "discord"]
    disclaimer_version: str


class GuidedSetupRequest(BaseModel):
    exchange: str = Field(min_length=2, max_length=40)
    quote_currency: str = Field(min_length=2, max_length=10)
    timeframe: str
    symbols: list[str] = Field(default_factory=list, max_length=100000)
    setup_mode: Literal["template", "free_text"]
    setup_text: str | None = Field(default=None, max_length=5000)
    template_key: str | None = Field(default=None, max_length=100)
    trigger_mode: Literal["candle_close", "intrabar"]
    maximum_stop_percent: float | None = Field(default=None, gt=0, le=100)
    minimum_reward_to_risk: float | None = Field(default=None, gt=0, le=50)
    minimum_quote_volume_24h: float | None = Field(default=None, ge=0)
    minimum_liquidity: float | None = Field(default=None, ge=0)
    maximum_spread_bps: float | None = Field(default=None, ge=0, le=1000)
    forming_alerts: bool = True
    near_miss_threshold: float = Field(default=70, ge=1, le=100)
    delivery_channels: list[Literal["telegram", "discord", "web"]] = Field(min_length=1)
    maximum_alerts_per_hour: int = Field(default=50, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_setup_source(self) -> "GuidedSetupRequest":
        if self.setup_mode == "free_text" and not self.setup_text:
            raise ValueError("free-text mode requires setup_text")
        if self.setup_mode == "template" and not self.template_key:
            raise ValueError("template mode requires template_key")
        return self


class InterpretationResponse(BaseModel):
    strategy_id: UUID
    strategy_version_id: UUID
    preview: InterpretationPreview


class ApprovalRequest(BaseModel):
    approved: Literal[True]
    expected_schema_hash: str = Field(min_length=64, max_length=64)


class StrategyEditRequest(BaseModel):
    strategy: StrategyDefinition


class MarketPreviewResponse(BaseModel):
    status: Literal["succeeded", "failed"]
    symbols_checked: int
    candles_checked: int
    sample_matches: list[dict[str, Any]]
    warnings: list[str]
    data_as_of: str | None


class ActivationRequest(BaseModel):
    strategy_name: str = Field(min_length=1, max_length=160)
    confirm_usage_impact: Literal[True]


class ActivationResponse(BaseModel):
    strategy_id: UUID
    strategy_version_id: UUID
    status: str
    activated_at: str
