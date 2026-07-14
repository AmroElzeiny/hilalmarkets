from functools import lru_cache
from typing import Any, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "HilalMarkets"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_secret_key: SecretStr = SecretStr("development-only-change-me-32-characters")
    database_url: str = "sqlite+aiosqlite:///./ai_market_monitor.db"
    redis_url: str = "redis://localhost:6379/0"
    public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    app_base_url: AnyHttpUrl | None = None
    log_level: str = "INFO"
    allow_mock_providers: bool = True
    scanning_enabled: bool = False
    sharia_screening_enforced: bool = False
    sharia_allow_legacy_unscreened_local: bool = True
    sharia_default_methodology_code: str | None = None
    sharia_universe_cache_ttl_seconds: int = Field(default=300, ge=30, le=86400)
    sharia_compliance_safety_under_review: bool = True
    sharia_compliance_digest_local_hour: int = Field(default=8, ge=0, le=23)
    tracedge_market_data_mode: Literal["ccxt", "fixture"] = "ccxt"
    tracedge_fixture_market_data_enabled: bool = False
    market_data_provider: Literal["ccxt", "memory"] = "ccxt"
    market_data_exchange: str = "binance"
    telegram_enabled: bool = False
    telegram_adapter: Literal["none", "http"] = "none"
    discord_enabled: bool = False
    discord_adapter: Literal["noop", "http"] = "noop"
    billing_enabled: bool = False
    billing_provider: Literal["static", "stripe", "nowpayments"] = "static"

    telegram_bot_username: str | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_polling_enabled: bool = True
    telegram_polling_interval_seconds: int = Field(default=5, ge=3, le=300)
    telegram_polling_limit: int = Field(default=20, ge=1, le=100)
    telegram_polling_clear_webhook: bool = True
    discord_client_id: str | None = None
    discord_client_secret: SecretStr | None = None
    discord_bot_token: SecretStr | None = None
    discord_webhook_public_key: SecretStr | None = None
    billing_webhook_secret: SecretStr | None = None
    stripe_secret_key: SecretStr | None = None
    stripe_price_ids: dict[str, str] = Field(default_factory=dict)
    stripe_api_base: AnyHttpUrl = AnyHttpUrl("https://api.stripe.com")
    nowpayments_api_key: SecretStr | None = None
    nowpayments_base_url: AnyHttpUrl = AnyHttpUrl("https://api.nowpayments.io")
    nowpay_email: str | None = None
    nowpay_password: SecretStr | None = None
    binance_api_key: SecretStr | None = None
    binance_api_secret: SecretStr | None = None
    binance_rest_base_url: AnyHttpUrl = AnyHttpUrl("https://api.binance.com")
    binance_ws_base_url: str = "wss://stream.binance.com:9443"
    binance_spot_api_base: AnyHttpUrl = AnyHttpUrl("https://api.binance.com")
    binance_futures_api_base: AnyHttpUrl = AnyHttpUrl("https://fapi.binance.com")
    binance_market_data_enabled: bool = True
    binance_order_book_enabled: bool = True
    binance_derivatives_enabled: bool = False
    bybit_api_key: SecretStr | None = None
    bybit_api_secret: SecretStr | None = None
    bybit_rest_base_url: AnyHttpUrl = AnyHttpUrl("https://api.bybit.com")
    bybit_ws_base_url: str = "wss://stream.bybit.com/v5/public/spot"
    coingecko_api_base: AnyHttpUrl = AnyHttpUrl("https://api.coingecko.com/api/v3")
    coingecko_api_key: SecretStr | None = None
    coingecko_plan: str = "none"
    coingecko_enabled: bool = False
    alternative_me_api_base: AnyHttpUrl = AnyHttpUrl("https://api.alternative.me")
    alternative_me_enabled: bool = False
    fred_api_base: AnyHttpUrl = AnyHttpUrl("https://api.stlouisfed.org/fred")
    fred_api_key: SecretStr | None = None
    fred_enabled: bool = False
    ai_interpreter_provider: Literal["rules", "openai"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    openai_model: str = "gpt-5.4-nano"
    openai_reasoning_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh"
    ] = "low"
    openai_timeout_seconds: int = Field(default=20, ge=1, le=120)
    openai_explanation_enabled: bool = True
    ai_agent_control_enabled: bool = False
    ai_agent_shadow_mode: bool = False
    ai_agent_rollout_percent: int = Field(default=0, ge=0, le=100)
    ai_agent_max_steps: int = Field(default=4, ge=1, le=12)
    ai_agent_max_tool_calls_per_turn: int = Field(default=4, ge=1, le=20)
    ai_agent_max_repeated_calls: int = Field(default=1, ge=0, le=3)
    ai_agent_timeout_seconds: int = Field(default=45, ge=5, le=180)
    ai_agent_tool_timeout_seconds: int = Field(default=30, ge=1, le=120)
    ai_agent_max_output_tokens: int = Field(default=1800, ge=128, le=8000)
    ai_agent_max_estimated_cost_usd_per_turn: float = Field(default=0.02, gt=0, le=5)
    ai_agent_parallel_tool_calls: bool = False
    ai_semantic_fallback_enabled: bool = False
    ai_semantic_fallback_model: str = "gpt-5.4-nano"
    ai_semantic_fallback_min_confidence: float = Field(default=0.85, ge=0, le=1)
    ai_semantic_fallback_review_confidence: float = Field(default=0.65, ge=0, le=1)
    ai_semantic_fallback_cache_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    ai_semantic_fallback_max_calls_per_prompt: int = Field(default=2, ge=0, le=10)
    ai_semantic_fallback_max_fragment_chars: int = Field(default=160, ge=20, le=1000)
    ai_capability_reranker_enabled: bool = True
    ai_capability_reranker_min_confidence: float = Field(default=0.86, ge=0.5, le=1)
    ai_capability_reranker_candidate_limit: int = Field(default=24, ge=5, le=30)
    capability_embeddings_enabled: bool = True
    capability_embedding_model: str = "text-embedding-3-small"
    capability_embedding_dimensions: int = Field(default=256, ge=64, le=3072)
    capability_extension_enabled: bool = True
    capability_extension_draft_model: str = "gpt-5.4-nano"
    capability_extension_draft_reasoning_effort: Literal[
        "none", "low", "medium", "high", "xhigh"
    ] = "low"
    capability_extension_implementation_model: str = "gpt-5.4-nano"
    capability_extension_review_model: str = "gpt-5.4-mini"
    capability_extension_repair_service_tier: Literal["default", "flex"] = "flex"
    capability_extension_ai_max_attempts: int = Field(default=3, ge=1, le=6)
    capability_extension_flex_timeout_seconds: int = Field(default=900, ge=120, le=1800)
    capability_extension_preflight_exchange: Literal["bybit", "binance"] = "bybit"
    capability_extension_preflight_max_symbols: int = Field(default=200, ge=10, le=5000)
    capability_extension_preflight_concurrency: int = Field(default=8, ge=1, le=30)
    capability_extension_candle_limit: int = Field(default=500, ge=100, le=2000)
    capability_extension_max_history_candles: int = Field(default=25_000, ge=500, le=50_000)
    capability_extension_market_test_candle_budget: int = Field(
        default=300_000,
        ge=50_000,
        le=5_000_000,
    )
    capability_extension_empty_scan_threshold: int = Field(default=5, ge=1, le=20)
    capability_extension_no_notification_threshold: int = Field(default=5, ge=1, le=20)
    capability_extension_min_candidate_rate: float = Field(default=0.0005, ge=0, le=0.1)
    capability_extension_max_candidate_rate: float = Field(default=0.35, ge=0.01, le=1)
    capability_extension_certification_score: float = Field(default=85, ge=0, le=100)
    capability_extension_max_expression_nodes: int = Field(default=80, ge=5, le=500)
    capability_extension_max_expression_depth: int = Field(default=12, ge=2, le=30)
    market_metadata_api_url: AnyHttpUrl | None = None
    market_metadata_api_key: SecretStr | None = None
    market_metadata_timeout_seconds: int = Field(default=15, ge=1, le=120)
    universe_metadata_concurrency: int = Field(default=8, ge=1, le=50)
    on_demand_scan_concurrency: int = Field(default=8, ge=1, le=50)
    crypto_index_api_url: AnyHttpUrl | None = None
    crypto_index_api_key: SecretStr | None = None
    macro_market_api_url: AnyHttpUrl | None = None
    macro_market_api_key: SecretStr | None = None
    event_feed_api_url: AnyHttpUrl | None = None
    event_feed_api_key: SecretStr | None = None
    token_category_api_url: AnyHttpUrl | None = None
    token_category_api_key: SecretStr | None = None
    derivatives_context_api_url: AnyHttpUrl | None = None
    derivatives_context_api_key: SecretStr | None = None
    context_provider_timeout_seconds: int = Field(default=15, ge=1, le=120)
    context_fetch_concurrency: int = Field(default=8, ge=1, le=50)
    market_breadth_max_symbols: int = Field(default=100, ge=10, le=1000)
    support_telegram_username: str | None = None
    support_email: str | None = "contact@trace-edge.com"
    admin_notify_telegram_user_id: str | None = None
    email_adapter: Literal["none", "smtp", "memory"] = "none"
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    auth_code_ttl_minutes: int = Field(default=10, ge=2, le=60)
    auth_code_max_attempts: int = Field(default=5, ge=1, le=10)
    auth_test_fixed_code: str | None = None
    email_test_outbox: list[dict[str, Any]] = Field(default_factory=list, exclude=True)
    system_brain_admin_username: str | None = None
    system_brain_admin_password_hash: SecretStr | None = None
    system_brain_otp_ttl_minutes: int = Field(default=10, ge=2, le=30)
    system_brain_otp_max_attempts: int = Field(default=5, ge=1, le=10)
    system_brain_session_hours: int = Field(default=8, ge=1, le=72)
    system_brain_login_attempts_per_15_minutes: int = Field(default=5, ge=1, le=20)
    openai_model_pricing_usd_per_million: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            "gpt-5.4-nano": {
                "input": 0.20,
                "cached_input": 0.02,
                "output": 1.25,
            },
            "gpt-5.4-mini": {
                "input": 0.75,
                "cached_input": 0.075,
                "output": 4.50,
            },
            "gpt-5-nano": {
                "input": 0.05,
                "cached_input": 0.005,
                "output": 0.40,
            }
        }
    )
    dashboard_export_directory: str = "./exports"
    chart_library_cdn_url: str | None = "/static/vendor/lightweight-charts.standalone.production.js"

    trial_days: int = Field(default=14, ge=0, le=90)
    trial_alerts_per_cycle: int = Field(default=500, ge=0, le=100000)
    delivery_settlement_grace_minutes: int = Field(default=60, ge=0, le=1440)
    scan_job_claim_timeout_seconds: int = Field(default=900, ge=60, le=86400)
    scan_job_max_attempts: int = Field(default=3, ge=1, le=10)
    disclaimer_version: str = "2026-06-01"
    continuation_token_ttl_minutes: int = Field(default=30, ge=5, le=1440)
    preview_candle_limit: int = Field(default=300, ge=100, le=1000)
    default_near_miss_threshold: int = Field(default=70, ge=1, le=100)
    default_alert_cooldown_seconds: int = Field(default=900, ge=0, le=86400)
    observability_detail_retention_days: int = Field(default=14, ge=1, le=365)
    observability_lifecycle_retention_days: int = Field(default=730, ge=30, le=3650)
    observability_aggregate_window_days: int = Field(default=30, ge=1, le=365)
    observability_minimum_sample_size: int = Field(default=20, ge=1, le=10000)
    observability_candidate_stale_seconds: int = Field(default=300, ge=30, le=86400)
    observability_live_poll_seconds: int = Field(default=15, ge=5, le=300)
    observability_max_candidates_per_user: int = Field(default=5000, ge=100, le=100000)

    @field_validator("app_secret_key")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("APP_SECRET_KEY must contain at least 32 characters")
        return value

    @model_validator(mode="after")
    def validate_capability_extension_bounds(self) -> "Settings":
        if self.ai_agent_parallel_tool_calls:
            raise ValueError("AI_AGENT_PARALLEL_TOOL_CALLS must remain false for bounded control")
        if self.ai_agent_tool_timeout_seconds > self.ai_agent_timeout_seconds:
            raise ValueError(
                "AI_AGENT_TOOL_TIMEOUT_SECONDS cannot exceed AI_AGENT_TIMEOUT_SECONDS"
            )
        if (
            self.capability_extension_min_candidate_rate
            >= self.capability_extension_max_candidate_rate
        ):
            raise ValueError(
                "CAPABILITY_EXTENSION_MIN_CANDIDATE_RATE must be below the maximum"
            )
        if (
            self.capability_extension_candle_limit
            > self.capability_extension_max_history_candles
        ):
            raise ValueError(
                "CAPABILITY_EXTENSION_CANDLE_LIMIT cannot exceed the history cap"
            )
        return self

    @field_validator(
        "market_metadata_api_url",
        "app_base_url",
        "crypto_index_api_url",
        "macro_market_api_url",
        "event_feed_api_url",
        "token_category_api_url",
        "derivatives_context_api_url",
        mode="before",
    )
    @classmethod
    def blank_optional_url(cls, value):
        return None if value in {"", None} else value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_deployed(self) -> bool:
        return self.app_env in {"staging", "production"}

    @property
    def support_inbox_email(self) -> str:
        return (self.support_email or "contact@trace-edge.com").strip()

    @property
    def system_brain_username(self) -> str | None:
        value = (self.system_brain_admin_username or "").strip().casefold()
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
