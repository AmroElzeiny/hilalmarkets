from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "TraceEdge"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_secret_key: SecretStr = SecretStr("development-only-change-me-32-characters")
    database_url: str = "sqlite+aiosqlite:///./ai_market_monitor.db"
    redis_url: str = "redis://localhost:6379/0"
    public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    app_base_url: AnyHttpUrl | None = None
    log_level: str = "INFO"
    allow_mock_providers: bool = True
    scanning_enabled: bool = False
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
    openai_model: str = "gpt-5-nano"
    openai_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "minimal"
    openai_timeout_seconds: int = Field(default=20, ge=1, le=120)
    openai_explanation_enabled: bool = True
    ai_semantic_fallback_enabled: bool = False
    ai_semantic_fallback_model: str = "gpt-5-nano"
    ai_semantic_fallback_min_confidence: float = Field(default=0.85, ge=0, le=1)
    ai_semantic_fallback_review_confidence: float = Field(default=0.65, ge=0, le=1)
    ai_semantic_fallback_cache_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    ai_semantic_fallback_max_calls_per_prompt: int = Field(default=2, ge=0, le=10)
    ai_semantic_fallback_max_fragment_chars: int = Field(default=160, ge=20, le=1000)
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
    support_email: str | None = None
    admin_notify_telegram_user_id: str | None = None
    email_adapter: Literal["none", "smtp", "memory"] = "none"
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True
    auth_code_ttl_minutes: int = Field(default=10, ge=2, le=60)
    auth_code_max_attempts: int = Field(default=5, ge=1, le=10)
    email_test_outbox: list[dict[str, str]] = Field(default_factory=list, exclude=True)
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

    @field_validator("app_secret_key")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("APP_SECRET_KEY must contain at least 32 characters")
        return value

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
