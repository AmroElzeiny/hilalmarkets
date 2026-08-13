import re
from functools import lru_cache
from typing import Any, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from ai_market_monitor.core.launch_stage import (
    LaunchStage,
    ResolvedStage,
    StageExposure,
    resolve_launch_stage,
)
from ai_market_monitor.observability.metrics import MetricRetentionPolicy

WHATSAPP_TEMPLATE_EVENTS = frozenset(
    {
        "connection_confirmation",
        "connection_test",
        "account_notice",
        "trial_update",
        "subscription_update",
        "compliance_change",
        "evidence_update",
        "watchlist_paused",
        "integration_failure",
        "lifecycle_update",
        "confirmed_research_event",
    }
)


class AISetupEvaluatorTargetVersion(BaseModel):
    """Server-owned model and prompt variant available only to test evaluators."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=120)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"]
    prompt_version: Literal["current", "context_guard_v1"] = "current"


#: Fields whose ``.env`` value outranks the ambient environment. Restricted to
#: credentials: a stale machine-wide copy causes an auth failure that is expensive
#: to diagnose, while non-secret settings must stay overridable per process.
CREDENTIAL_FIELDS: frozenset[str] = frozenset({"openai_api_key"})


class CredentialDotEnvSource(PydanticBaseSettingsSource):
    """Supplies only the credential fields, and only from the ``.env`` file."""

    def __init__(self, dotenv_settings: PydanticBaseSettingsSource) -> None:
        super().__init__(dotenv_settings.settings_cls)
        self._dotenv = dotenv_settings

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        raise NotImplementedError

    def __call__(self) -> dict[str, Any]:
        return {
            name: value
            for name, value in self._dotenv().items()
            if name.casefold() in CREDENTIAL_FIELDS
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Let ``.env`` win over the ambient environment for credentials only.

        A stale machine-wide credential must not outrank the project's own file.
        Every other setting keeps normal precedence so that per-process overrides
        such as ``DATABASE_URL=... alembic upgrade head`` continue to work.
        """
        return (
            init_settings,
            CredentialDotEnvSource(dotenv_settings),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    app_name: str = "HilalMarkets"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    application_version: str = "development"
    app_secret_key: SecretStr = SecretStr("development-only-change-me-32-characters")
    database_url: str = "sqlite+aiosqlite:///./ai_market_monitor.db"
    redis_url: str = "redis://localhost:6379/0"
    public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    app_base_url: AnyHttpUrl | None = None
    public_og_image_url: AnyHttpUrl | None = None
    site_legal_name: str | None = None
    site_company_address: str | None = None
    site_governing_law: str | None = None
    site_privacy_contact_email: str | None = None
    site_security_contact_email: str | None = None
    cookie_consent_version: int = Field(default=1, ge=1, le=1000)
    google_tag_manager_container_id: str | None = None
    optional_analytics_enabled: bool = False
    marketing_consent_enabled: bool = False
    vite_analytics_enabled: bool = False
    vite_gtm_id: str | None = None
    vite_ga4_measurement_id: str | None = None
    vite_meta_pixel_id: str | None = None
    vite_meta_pixel_enabled: bool = False
    vite_x_pixel_id: str | None = None
    vite_x_pixel_enabled: bool = False
    vite_site_url: AnyHttpUrl | None = None
    vite_analytics_debug: bool = False
    log_level: str = "INFO"
    allow_mock_providers: bool = True
    scanning_enabled: bool = False
    sharia_screening_enforced: bool = False
    sharia_allow_legacy_unscreened_local: bool = True
    sharia_live_quote_cache_seconds: float = Field(default=0.75, ge=0.5, le=10)
    sharia_default_methodology_code: str | None = None
    sharia_universe_cache_ttl_seconds: int = Field(default=300, ge=30, le=86400)
    sharia_abnormal_exclusion_rate_threshold: float = Field(default=0.8, ge=0, le=1)
    sharia_abnormal_exclusion_minimum_assets: int = Field(default=10, ge=1, le=10_000)
    sharia_compliance_safety_under_review: bool = True
    sharia_compliance_digest_local_hour: int = Field(default=8, ge=0, le=23)
    sharia_admin_telegram_chat_id: str | None = None
    sc_malaysia_digital_assets_url: AnyHttpUrl = AnyHttpUrl("https://www.sc.com.my/digital-assets")
    fasset_shariah_reports_url: AnyHttpUrl = AnyHttpUrl("https://www.fasset.com/shariah-reports")
    fasset_minimum_profile_count: int = Field(default=100, ge=1, le=1000)
    sharia_ai_model: str = "gpt-5.4-nano"
    sharia_ai_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] = "low"
    sharia_ai_service_tier: Literal["default", "flex"] = "flex"
    sharia_ai_timeout_seconds: int = Field(default=900, ge=60, le=1800)
    sharia_ai_max_retries: int = Field(default=5, ge=1, le=10)
    sharia_ai_allow_standard_fallback: bool = False
    sharia_review_reminder_hours: int = Field(default=6, ge=1, le=168)
    sharia_review_sla_hours: int = Field(default=48, ge=1, le=720)
    require_second_reviewer: bool = False
    sharia_source_scan_interval_hours: int = Field(default=24, ge=1, le=720)
    sharia_scraper_concurrency: int = Field(default=1, ge=1, le=4)
    sharia_scraper_obey_robots: bool = True
    sharia_scraper_download_delay_seconds: float = Field(default=1, ge=0.2, le=60)
    sharia_pilot_symbols: str = "BTC,ETH,SOL"
    sharia_process_remaining_imports: bool = True
    sharia_import_pack_path: str = (
        "HilalMarkets_Sharia_Methodology_Import_Pack/HilalMarkets_Sharia_Methodology_Import_Pack"
    )
    sharia_import_auto_publish: bool = False
    sharia_import_require_admin_review: bool = True
    sharia_import_metadata_only_publication: bool = False
    sharia_identity_discovery_batch_size: int = Field(default=250, ge=1, le=500)
    sharia_external_rights_enforcement: bool = True
    sharia_ai_enrichment_enabled: bool = True
    sharia_ai_enrichment_official_sources_only: bool = True
    sharia_ai_enrichment_store_as_external_reason: bool = False
    tracedge_market_data_mode: Literal["ccxt", "fixture"] = "ccxt"
    tracedge_fixture_market_data_enabled: bool = False
    market_data_provider: Literal["ccxt", "memory"] = "ccxt"
    market_data_exchange: str = "binance"
    telegram_enabled: bool = False
    telegram_adapter: Literal["none", "http"] = "none"
    whatsapp_enabled: bool = False
    whatsapp_adapter: Literal["none", "http"] = "none"
    billing_enabled: bool = False
    billing_provider: Literal["static", "stripe", "nowpayments", "creem"] = "static"
    billing_card_provider: Literal["disabled", "static", "stripe", "creem"] = "disabled"
    billing_crypto_provider: Literal["disabled", "nowpayments"] = "disabled"
    billing_checkout_ttl_minutes: int = Field(default=30, ge=5, le=1440)
    billing_terms_version: str = "2026-07"
    billing_payment_amount_tolerance_percent: float = Field(default=0, ge=0, le=5)
    billing_allow_overpayment: bool = False
    payment_email_max_attempts: int = Field(default=5, ge=1, le=20)
    payment_email_retry_minutes: int = Field(default=15, ge=1, le=1440)
    api_rate_limiting_enabled: bool = True
    api_rate_limit_fail_closed: bool = True
    api_rate_limits: dict[str, dict[str, int]] = Field(
        default_factory=lambda: {
            "authentication": {"limit": 10, "window_seconds": 900},
            "ai_chat": {"limit": 30, "window_seconds": 60},
            "market_check": {"limit": 20, "window_seconds": 60},
            "checkout": {"limit": 5, "window_seconds": 300},
            "portal": {"limit": 10, "window_seconds": 300},
            "support": {"limit": 5, "window_seconds": 3600},
            "passport_report": {"limit": 5, "window_seconds": 3600},
            "telegram_test": {"limit": 5, "window_seconds": 300},
            "whatsapp_test": {"limit": 5, "window_seconds": 300},
            "public_chat": {"limit": 20, "window_seconds": 60},
            "public_inquiry": {"limit": 5, "window_seconds": 3600},
            "public_waitlist": {"limit": 5, "window_seconds": 3600},
            "public_contact": {"limit": 5, "window_seconds": 3600},
            "admin_mutation": {"limit": 30, "window_seconds": 60},
        }
    )

    telegram_bot_username: str | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_polling_enabled: bool = True
    telegram_polling_interval_seconds: int = Field(default=5, ge=3, le=300)
    telegram_polling_limit: int = Field(default=20, ge=1, le=100)
    telegram_polling_clear_webhook: bool = True
    whatsapp_graph_api_version: str = ""
    whatsapp_access_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_business_phone_e164: str | None = None
    whatsapp_default_language: str = "en_US"
    whatsapp_http_timeout_seconds: int = Field(default=15, ge=1, le=120)
    whatsapp_max_delivery_attempts: int = Field(default=5, ge=1, le=20)
    whatsapp_opportunity_alerts_enabled: bool = False
    whatsapp_template_names: dict[str, str | dict[str, str]] = Field(default_factory=dict)
    whatsapp_opt_in_version: str = "2026-07"
    whatsapp_mark_inbound_read: bool = True
    whatsapp_webhook_receipt_retention_days: int = Field(default=30, ge=1, le=365)
    billing_webhook_secret: SecretStr | None = None
    stripe_secret_key: SecretStr | None = None
    stripe_price_ids: dict[str, str] = Field(default_factory=dict)
    stripe_api_base: AnyHttpUrl = AnyHttpUrl("https://api.stripe.com")
    creem_api_key: SecretStr | None = None
    creem_webhook_secret: SecretStr | None = None
    creem_product_ids: dict[str, str] = Field(default_factory=dict)
    creem_api_base: AnyHttpUrl = AnyHttpUrl("https://test-api.creem.io")
    creem_timeout_seconds: int = Field(default=20, ge=3, le=60)
    nowpayments_api_key: SecretStr | None = None
    nowpayments_ipn_secret: SecretStr | None = None
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
    openai_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] = "low"
    openai_timeout_seconds: int = Field(default=20, ge=1, le=120)
    openai_explanation_enabled: bool = True
    ai_setup_simple_model: str | None = None
    ai_setup_complex_model: str | None = None
    ai_setup_simple_reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None
    ) = None
    ai_setup_complex_reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None
    ) = None
    ai_setup_complex_condition_threshold: int = Field(default=4, ge=2, le=20)
    ai_setup_repeated_correction_threshold: int = Field(default=2, ge=1, le=10)
    ai_setup_low_capability_confidence: float = Field(default=0.72, ge=0, le=1)
    ai_setup_evaluator_enabled: bool = False
    ai_setup_evaluator_faults_enabled: bool = False
    ai_setup_evaluator_target_versions: dict[str, AISetupEvaluatorTargetVersion] = Field(
        default_factory=dict
    )
    #: The authenticated Setup Chat path. Kept true: the agent pipeline *is* the
    #: production path, and there is no other writable route to fall back to.
    setup_chat_launch_v2_enabled: bool = True
    #: Operational circuit breakers for the authenticated launch path. The emergency
    #: switch blocks every new turn without falling back to a legacy writer. A non-empty
    #: allowlist limits the private beta to the named user UUIDs; empty keeps the normal
    #: entitlement-controlled availability.
    setup_chat_emergency_disabled: bool = False
    setup_chat_private_beta_user_ids: list[str] = Field(default_factory=list, max_length=10000)
    setup_chat_legacy_test_compat_enabled: bool = False
    #: Bounded compatibility for clients deployed before question identity was required.
    #: While it is on, a message sent with no ``question_id``/``step_revision`` while a
    #: question is open is accepted and counted; while it is off, it is refused. It is
    #: forbidden in a deployed environment: a permanently optional identity means a
    #: message written against a screen that has moved on can still land on whatever
    #: field is current now, which is a field the trader was never asked about.
    setup_chat_allow_missing_answer_identity: bool = False
    #: Emergency switch for the crash-recovery worker. Turning it off stops new recovery
    #: work; it never hides a committed result, because a replay still answers from the
    #: stored record. Left on by default: without recovery, a crashed turn locks its own
    #: session and the user cannot send anything.
    setup_chat_recovery_disabled: bool = False
    #: How often stalled turns are looked for, in seconds. Shorter than the shortest
    #: stage lease so a dead turn is never left holding a session for long.
    setup_chat_recovery_interval_seconds: int = Field(default=60, ge=15, le=900)

    # --- Independent controls, one per surface. -----------------------------------
    #
    # These are separate on purpose. When the planner is failing, the answer is to turn
    # off the planner — not to take the whole product down. Each switch stands alone, so
    # the Builder keeps working while any AI part is off, and turning the Builder off
    # never affects live monitors.
    #
    #: Free-text messages in Setup Chat. Off means the composer is closed and the guided
    #: fields are the way in. Everything already saved stays exactly as it is.
    setup_free_text_enabled: bool = True
    #: The planner: the model call that reads a sentence into operations.
    setup_planner_enabled: bool = True
    #: The composer: the model call that writes the reply. Off means replies are built
    #: from the deterministic summary of what really changed.
    setup_composer_enabled: bool = True
    #: The Guided Watch Plan Builder. Off only for an emergency; with it off and AI off
    #: there is no way to author a setup at all.
    setup_builder_enabled: bool = True
    #: Running a Scanner sweep from a reviewed draft.
    setup_scanner_enabled: bool = True
    #: Creating and running Monitors from a reviewed draft.
    setup_monitor_enabled: bool = True
    #: Cohort rollout for the Builder, by user id. Empty means everybody who is allowed
    #: into Setup Chat at all. A non-empty list limits it to exactly those users.
    setup_builder_user_ids: list[str] = Field(default_factory=list, max_length=10000)
    #: Languages the assistant is offered in. Empty means every language the product
    #: supports. A person outside the list still gets the Builder, which needs no model.
    setup_ai_languages: list[str] = Field(default_factory=list, max_length=50)

    # --- Setup Agent bounds. These are the ones that control Setup Chat traffic. ---
    #: Retained for environment compatibility only. Authenticated Setup Chat never
    #: retries a model call inside one free-text turn.
    setup_agent_planner_max_output_tokens: int = Field(default=8000, ge=512, le=16000)
    setup_agent_composer_max_output_tokens: int = Field(default=1600, ge=256, le=4000)
    # Twelve seconds is the product p95 objective, not a hard kill switch. Complex
    # but valid turns can occasionally exceed it; keep the provider call bounded
    # while recording the latency regression instead of returning a false outage.
    setup_agent_planner_timeout_seconds: int = Field(default=60, ge=5, le=60)
    setup_agent_composer_timeout_seconds: int = Field(default=10, ge=5, le=12)
    #: The whole-turn budget, measured from the authenticated request boundary and
    #: shared by every stage. Per-stage timeouts bound one call; this bounds the turn,
    #: so a chain of individually-legal waits cannot add up past what the client will
    #: wait for. Raising it is not a fix for slow processing — it only moves the
    #: failure from the server to the browser.
    setup_turn_deadline_seconds: float = Field(default=45.0, ge=10.0, le=120.0)
    #: Setup Chat pins both routes explicitly so project-level ``auto`` routing cannot
    #: silently change latency/cost behavior. Standard processing is the stable
    #: launch default; Fast remains an explicit opt-in after provider canary evidence.
    setup_agent_simple_service_tier: Literal["default"] = "default"
    setup_agent_complex_service_tier: Literal["default", "fast"] = "default"
    setup_agent_max_estimated_cost_usd_per_turn: float = Field(default=0.10, gt=0, le=5)
    setup_agent_max_estimated_cost_usd_per_user_day: float = Field(default=2.0, gt=0, le=100)
    #: Consecutive provider failures before the agent stops trying for a while.
    setup_agent_circuit_breaker_failures: int = Field(default=5, ge=1, le=20)
    setup_agent_circuit_breaker_cooldown_seconds: int = Field(default=60, ge=5, le=900)
    setup_provider_preflight_ttl_seconds: int = Field(default=300, ge=30, le=3600)

    # -- Provider reliability -------------------------------------------------
    # Bounds on the shared HTTP pool. Every one of them is finite on purpose: an
    # unbounded pool opens as many sockets as there are concurrent calls, and an
    # unbounded wait makes a saturated pool hang every caller instead of failing in a
    # way the turn can report.
    provider_pool_max_connections: int = Field(default=40, ge=1, le=500)
    provider_pool_max_keepalive: int = Field(default=20, ge=0, le=500)
    provider_pool_keepalive_seconds: float = Field(default=30.0, ge=0.0, le=600.0)
    provider_connect_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    provider_read_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    provider_write_timeout_seconds: float = Field(default=10.0, gt=0.0, le=120.0)
    provider_pool_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    #: The ceiling on provider calls in flight across the whole process, so one slow
    #: upstream cannot consume the worker.
    provider_max_concurrency: int = Field(default=24, ge=1, le=500)
    provider_retry_max_attempts: int = Field(default=3, ge=1, le=6)
    provider_retry_base_delay_seconds: float = Field(default=0.5, gt=0.0, le=30.0)
    provider_retry_max_delay_seconds: float = Field(default=8.0, gt=0.0, le=120.0)
    provider_circuit_failure_threshold: int = Field(default=5, ge=1, le=100)
    provider_circuit_recovery_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)
    #: Share circuit state between workers through Redis. Coordination only: when Redis
    #: cannot answer, each worker falls back to its own breaker and calls still go out.
    #: Switching this off costs cross-worker knowledge, never correctness.
    provider_circuit_share_state: bool = True

    # -- AI budget ceilings ---------------------------------------------------
    # Money. Every one of these is enforced by the same authority, and a call whose cost
    # cannot be estimated is refused rather than guessed at.
    ai_budget_per_turn_max_usd: float = Field(default=1.0, ge=0.0)
    ai_budget_user_daily_usd: float | None = Field(default=5.0, ge=0.0)
    ai_budget_user_monthly_usd: float | None = Field(default=50.0, ge=0.0)
    ai_budget_global_daily_usd: float | None = Field(default=200.0, ge=0.0)
    ai_budget_global_monthly_usd: float | None = Field(default=2000.0, ge=0.0)
    ai_budget_max_concurrent_reservations: int = Field(default=3, ge=0, le=50)

    # -- Runtime rollout ------------------------------------------------------
    #: Features forced off by configuration, comma separated. A hard emergency ceiling:
    #: a runtime control can never switch these back on.
    ai_features_disabled: str = Field(default="")
    #: Stamped onto every AI turn so a past decision can be replayed against the same
    #: rollout rather than against whatever the configuration says afterwards.
    ai_rollout_version: str = Field(default="0", max_length=40)
    #: Individual capability keys to pause, comma separated. One wrong formula or one
    #: misbehaving feed can be switched off on its own, without taking the Builder, the
    #: assistant or the other capabilities with it. A paused capability is still *shown*,
    #: with a reason — one that vanished from the list would look like lost work to the
    #: person who used it yesterday.
    builder_capabilities_disabled: str = Field(default="")
    #: How many resolved symbols one turn may check against the data provider before the
    #: preflight stops promising per-symbol coverage and says so.
    #:
    #: A universe of "everything eligible" can be thousands of symbols; checking each one
    #: inside a chat turn is not possible. Below this many, the check is exhaustive and
    #: the turn promises every symbol has data (`verified_all`). Above it, the turn
    #: verifies the *policy* — the timeframes and capabilities the rules need — and says
    #: plainly that each symbol is confirmed when monitoring starts, failing closed for
    #: any symbol without data (`policy_verified_runtime_fail_closed`).
    setup_preflight_symbol_cap: int = Field(default=25, ge=1, le=200)
    #: How many provider checks run at once. Bounded so one wide universe cannot use up
    #: the provider's rate limit for every other user.
    setup_preflight_max_concurrency: int = Field(default=4, ge=1, le=16)
    setup_screening_resolution_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    setup_methodology_version_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    setup_approved_watchlist_ttl_seconds: int = Field(default=300, ge=30, le=3600)

    #: Bounded Agent Control is the *old* general coordinator. It has no authority over
    #: authenticated Setup Chat, and the `ai_agent_*` bounds below do not govern that
    #: traffic — the `setup_agent_*` values above do.
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
    capability_extension_enabled: bool = False
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
    capability_extension_daily_limit: int = Field(default=3, ge=1, le=50)
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
    public_chat_enabled: bool = True
    public_chat_ai_enabled: bool = False
    public_chat_inquiry_email: str = "office@hilalmarkets.com"
    public_chat_profile_version: int = Field(default=1, ge=1, le=1000)
    public_chat_message_max_length: int = Field(default=800, ge=100, le=4000)
    public_chat_inquiry_max_length: int = Field(default=4000, ge=500, le=10000)
    public_chat_answer_audit_retention_days: int = Field(default=90, ge=1, le=730)
    public_chat_inquiry_retention_days: int = Field(default=365, ge=30, le=3650)
    public_chat_email_max_attempts: int = Field(default=5, ge=1, le=20)
    public_chat_email_retry_minutes: int = Field(default=15, ge=1, le=1440)
    public_chat_email_claim_timeout_minutes: int = Field(default=10, ge=1, le=120)
    public_chat_ai_model: str | None = None
    public_chat_ai_reasoning_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh"
    ] = "low"
    public_chat_ai_timeout_seconds: int = Field(default=30, ge=3, le=120)
    public_chat_ai_max_output_tokens: int = Field(default=1200, ge=256, le=4000)
    public_chat_ai_max_estimated_cost_usd_per_turn: float = Field(
        default=0.015,
        gt=0,
        le=1,
    )
    public_chat_ai_min_confidence: float = Field(default=0.55, ge=0, le=1)
    public_chat_ai_max_history_messages: int = Field(default=12, ge=2, le=30)
    public_chat_ai_max_read_tools: int = Field(default=3, ge=0, le=6)
    public_chat_session_max_turns: int = Field(default=40, ge=5, le=200)
    public_chat_ai_provider_attempts: int = Field(default=2, ge=1, le=3)
    public_chat_ai_circuit_failure_threshold: int = Field(default=5, ge=1, le=20)
    public_chat_ai_circuit_reset_seconds: int = Field(default=60, ge=10, le=900)
    public_chat_session_retention_days: int = Field(default=30, ge=1, le=365)
    public_chat_notion_enabled: bool = True
    public_chat_notion_root: str = "./Notion"
    public_chat_notion_max_documents: int = Field(default=6, ge=1, le=12)
    public_chat_notion_max_characters: int = Field(default=12000, ge=1000, le=50000)
    public_chat_notion_max_file_bytes: int = Field(default=262144, ge=1024, le=2_000_000)
    public_forms_enabled: bool = True
    # -- Durable operational measurements ---------------------------------------
    #
    # Measurements are held in memory for speed and written down periodically, so
    # they survive a restart and add up across the API, the workers and the
    # scheduler. These four settings decide how coarse the stored windows are, how
    # often each process writes, when per-process rows are folded into one, and when
    # they are deleted. See `observability/durable_metrics.py`.
    #: Width of one stored window. Wider means fewer rows and a blunter timeline.
    observability_window_seconds: int = Field(default=300, ge=10, le=3600)
    #: How often a process writes its outstanding movement down. Never longer than a
    #: window, or a whole window can pass with nothing recorded from a quiet process.
    observability_flush_interval_seconds: int = Field(default=60, ge=5, le=900)
    #: After this age, every process's rows for one window become a single row.
    observability_rollup_after_hours: int = Field(default=6, ge=1, le=168)
    #: After this age, stored measurements are deleted. This is the only bound on
    #: growth, so it is required rather than optional.
    observability_retention_hours: int = Field(default=72, ge=2, le=8760)
    #: How open the product is. The authority for what every public surface shows:
    #: advertised routes, calls to action, pricing and checkout exposure, offered
    #: channels, and what the public assistant may claim. See
    #: `core/launch_stage.py`, which holds the exposure table and the legal moves.
    launch_stage: LaunchStage = LaunchStage.PUBLIC_WAITLIST
    # The emergency ceiling, kept as an environment variable on purpose.
    #
    # It is no longer an independent authority: no surface reads it directly any
    # more, they read `waitlist_mode`, which is derived from the resolved stage.
    # What it still does is cap exposure. While it is true the product can be no more
    # open than `public_waitlist`, whatever LAUNCH_STAGE says, so one variable can
    # pull the site back without a deploy. It only ever narrows.
    public_waitlist_mode: bool = True
    contact_form_sender_email: str = "office@hilalmarkets.com"
    contact_form_recipient_email: str = "office@hilalmarkets.com"
    public_form_email_max_attempts: int = Field(default=5, ge=1, le=20)
    public_form_email_retry_minutes: int = Field(default=15, ge=1, le=1440)
    public_form_delivery_claim_timeout_minutes: int = Field(default=10, ge=1, le=120)
    waitlist_google_sheets_enabled: bool = False
    waitlist_google_sheets_webhook_url: SecretStr | None = None
    waitlist_google_sheets_webhook_secret: SecretStr | None = None
    waitlist_google_sheets_timeout_seconds: int = Field(default=15, ge=2, le=60)
    waitlist_google_sheets_max_attempts: int = Field(default=8, ge=1, le=30)
    waitlist_google_sheets_retry_minutes: int = Field(default=15, ge=1, le=1440)
    waitlist_trust_cloudflare_country_header: bool = False
    system_brain_admin_username: str | None = None
    system_brain_admin_emails: str = ""
    system_brain_admin_password_hash: SecretStr | None = None
    system_brain_otp_ttl_minutes: int = Field(default=10, ge=2, le=30)
    system_brain_otp_max_attempts: int = Field(default=5, ge=1, le=10)
    system_brain_session_hours: int = Field(default=8, ge=1, le=72)
    system_brain_login_attempts_per_15_minutes: int = Field(default=5, ge=1, le=20)
    system_brain_cloudflare_access_required: bool = False
    system_brain_ai_enabled: bool = True
    system_brain_ai_model: str = "gpt-5.4-nano"
    system_brain_ai_reasoning_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh"
    ] = "low"
    system_brain_ai_timeout_seconds: int = Field(default=30, ge=3, le=120)
    system_brain_ai_max_output_tokens: int = Field(default=900, ge=256, le=2400)
    system_brain_ai_max_context_characters: int = Field(
        default=24_000,
        ge=4_000,
        le=80_000,
    )
    system_brain_ai_max_estimated_cost_usd_per_turn: float = Field(
        default=0.02,
        gt=0,
        le=1,
    )
    system_brain_agent_max_steps: int = Field(default=6, ge=1, le=12)
    system_brain_agent_max_tool_calls: int = Field(default=8, ge=1, le=20)
    system_brain_agent_max_repeated_calls: int = Field(default=1, ge=0, le=2)
    system_brain_agent_tool_timeout_seconds: int = Field(default=10, ge=1, le=60)
    system_brain_agent_turn_timeout_seconds: int = Field(default=50, ge=5, le=180)
    system_brain_agent_max_history_messages: int = Field(default=16, ge=2, le=40)
    system_brain_agent_evidence_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    system_brain_agent_max_turns_per_hour: int = Field(default=30, ge=1, le=300)
    system_brain_agent_max_cost_usd_per_day: float = Field(default=5.0, gt=0, le=1000)
    system_brain_agent_max_tool_payload_characters: int = Field(
        default=24_000, ge=2_000, le=100_000
    )
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
            "gpt-5-mini": {
                "input": 0.25,
                "cached_input": 0.025,
                "output": 2.00,
            },
            "gpt-5-nano": {
                "input": 0.05,
                "cached_input": 0.005,
                "output": 0.40,
            },
        }
    )
    openai_fast_model_pricing_usd_per_million: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            "gpt-5.4-mini": {
                "input": 1.50,
                "cached_input": 0.15,
                "output": 9.00,
            },
            "gpt-5-mini": {
                "input": 0.45,
                "cached_input": 0.045,
                "output": 3.60,
            },
        }
    )
    dashboard_export_directory: str = "./exports"
    chart_library_cdn_url: str | None = "/static/vendor/lightweight-charts.standalone.production.js"

    trial_days: int = Field(default=7, ge=0, le=90)
    trial_alerts_per_cycle: int = Field(default=350, ge=0, le=100000)
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
        if not self.setup_chat_launch_v2_enabled:
            raise ValueError("SETUP_CHAT_LAUNCH_V2_ENABLED must remain true")
        if self.is_deployed and self.setup_chat_legacy_test_compat_enabled:
            raise ValueError(
                "SETUP_CHAT_LEGACY_TEST_COMPAT_ENABLED is forbidden outside local tests"
            )
        if self.is_deployed and self.setup_chat_allow_missing_answer_identity:
            raise ValueError(
                "SETUP_CHAT_ALLOW_MISSING_ANSWER_IDENTITY is forbidden outside local tests"
            )
        invalid_beta_ids = [
            item
            for item in self.setup_chat_private_beta_user_ids
            if not re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
                r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
                item,
            )
        ]
        if invalid_beta_ids:
            raise ValueError("SETUP_CHAT_PRIVATE_BETA_USER_IDS contains an invalid UUID")
        if self.ai_agent_parallel_tool_calls:
            raise ValueError("AI_AGENT_PARALLEL_TOOL_CALLS must remain false for bounded control")
        if self.ai_agent_tool_timeout_seconds > self.ai_agent_timeout_seconds:
            raise ValueError("AI_AGENT_TOOL_TIMEOUT_SECONDS cannot exceed AI_AGENT_TIMEOUT_SECONDS")
        for label in self.ai_setup_evaluator_target_versions:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", label):
                raise ValueError(
                    "AI_SETUP_EVALUATOR_TARGET_VERSIONS contains an invalid version label"
                )
        if (
            self.capability_extension_min_candidate_rate
            >= self.capability_extension_max_candidate_rate
        ):
            raise ValueError("CAPABILITY_EXTENSION_MIN_CANDIDATE_RATE must be below the maximum")
        if self.capability_extension_candle_limit > self.capability_extension_max_history_candles:
            raise ValueError("CAPABILITY_EXTENSION_CANDLE_LIMIT cannot exceed the history cap")
        if self.sharia_scraper_concurrency != 1:
            raise ValueError(
                "SHARIA_SCRAPER_CONCURRENCY must remain 1 for sequential evidence retrieval"
            )
        if not self.sharia_pilot_symbol_set:
            raise ValueError("SHARIA_PILOT_SYMBOLS must include at least one reviewed symbol")
        if self.sharia_import_auto_publish and not self.sharia_import_metadata_only_publication:
            raise ValueError(
                "SHARIA_IMPORT_METADATA_ONLY_PUBLICATION must be true when "
                "SHARIA_IMPORT_AUTO_PUBLISH is enabled"
            )
        if not self.sharia_external_rights_enforcement:
            raise ValueError("SHARIA_EXTERNAL_RIGHTS_ENFORCEMENT must remain true")
        if not self.sharia_ai_enrichment_official_sources_only:
            raise ValueError("SHARIA_AI_ENRICHMENT_OFFICIAL_SOURCES_ONLY must remain true")
        if self.sharia_ai_enrichment_store_as_external_reason:
            raise ValueError("SHARIA_AI_ENRICHMENT_STORE_AS_EXTERNAL_REASON must remain false")
        required_rate_limits = {
            "authentication",
            "ai_chat",
            "market_check",
            "checkout",
            "portal",
            "support",
            "passport_report",
            "telegram_test",
            "whatsapp_test",
            "public_chat",
            "public_inquiry",
            "public_waitlist",
            "public_contact",
            "admin_mutation",
        }
        if set(self.api_rate_limits) != required_rate_limits:
            raise ValueError("API_RATE_LIMITS must define exactly the supported security scopes")
        for scope, values in self.api_rate_limits.items():
            if set(values) != {"limit", "window_seconds"}:
                raise ValueError(f"API rate limit {scope} has an invalid shape")
            if values["limit"] < 1 or values["window_seconds"] < 1:
                raise ValueError(f"API rate limit {scope} must use positive values")
        if self.whatsapp_graph_api_version and not re.fullmatch(
            r"v[1-9]\d*\.\d+", self.whatsapp_graph_api_version
        ):
            raise ValueError("WHATSAPP_GRAPH_API_VERSION must use a value such as v23.0")
        if self.whatsapp_business_phone_e164 and not re.fullmatch(
            r"\+[1-9]\d{7,14}", self.whatsapp_business_phone_e164
        ):
            raise ValueError("WHATSAPP_BUSINESS_PHONE_E164 must be a normalized E.164 number")
        if not re.fullmatch(r"[a-z]{2}(?:_[A-Z]{2})?", self.whatsapp_default_language):
            raise ValueError("WHATSAPP_DEFAULT_LANGUAGE must use a locale such as en_US")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", self.whatsapp_opt_in_version):
            raise ValueError("WHATSAPP_OPT_IN_VERSION contains unsupported characters")
        for event_type, configured in self.whatsapp_template_names.items():
            if event_type not in WHATSAPP_TEMPLATE_EVENTS:
                raise ValueError("WHATSAPP_TEMPLATE_NAMES contains an unknown event key")
            if isinstance(configured, dict) and any(
                locale != "default" and not re.fullmatch(r"[a-z]{2}(?:_[A-Z]{2})?", str(locale))
                for locale in configured
            ):
                raise ValueError("WHATSAPP_TEMPLATE_NAMES contains an invalid locale key")
            names = configured.values() if isinstance(configured, dict) else [configured]
            if any(not re.fullmatch(r"[a-z0-9_]{1,512}", str(name)) for name in names):
                raise ValueError("WHATSAPP_TEMPLATE_NAMES contains an invalid template name")
        return self

    @field_validator(
        "market_metadata_api_url",
        "app_base_url",
        "public_og_image_url",
        "vite_site_url",
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

    @field_validator("api_rate_limits")
    @classmethod
    def include_public_form_rate_limits(
        cls,
        value: dict[str, dict[str, int]],
    ) -> dict[str, dict[str, int]]:
        merged = dict(value)
        merged.setdefault("public_waitlist", {"limit": 5, "window_seconds": 3600})
        merged.setdefault("public_contact", {"limit": 5, "window_seconds": 3600})
        return merged

    @property
    def metric_retention_policy(self) -> MetricRetentionPolicy:
        """How long stored measurements live. Validated, so an incoherent pair fails."""

        policy = MetricRetentionPolicy(
            window_seconds=self.observability_window_seconds,
            rollup_after_hours=self.observability_rollup_after_hours,
            retention_hours=self.observability_retention_hours,
        )
        policy.validate()
        return policy

    @property
    def resolved_launch_stage(self) -> ResolvedStage:
        """The stage actually in force, after the environment ceiling is applied.

        When ``LAUNCH_STAGE`` is not set explicitly, the stage is *derived* from
        ``PUBLIC_WAITLIST_MODE``. That is the reconciliation the older switch needs,
        not a second authority: every deployment and test today configures exposure
        with that switch alone, and turning it off is how the product was always going
        to be opened. A stage that ignored it would leave an operator setting it to
        ``false`` watching nothing change — the site staying on the waitlist while the
        setting said otherwise, which is precisely the silent disagreement this layer
        exists to remove.

        Derived on read rather than at construction because the switch is also flipped
        at runtime, in tests and by an operator reloading configuration. A value
        computed once at startup would answer with the old exposure for the life of
        the process.

        The moment ``LAUNCH_STAGE`` is set explicitly it becomes the authority, and
        the switch narrows to being only a ceiling over it.
        """

        configured = self.launch_stage
        if "launch_stage" not in self.model_fields_set:
            configured = (
                LaunchStage.PUBLIC_WAITLIST
                if self.public_waitlist_mode
                else LaunchStage.PUBLIC_LAUNCH
            )
        return resolve_launch_stage(
            configured,
            waitlist_ceiling=self.public_waitlist_mode,
        )

    @property
    def stage_exposure(self) -> StageExposure:
        """What the effective stage shows, offers and permits."""

        return self.resolved_launch_stage.exposure

    @property
    def waitlist_mode(self) -> bool:
        """Whether the public site leads with the waitlist.

        A *derived view* of the stage, not a setting. Every surface that used to read
        ``public_waitlist_mode`` reads this, so a stage change reaches the header, the
        footer, the sitemap, the assistant and Telegram together instead of one at a
        time.
        """

        return self.stage_exposure.shows_waitlist

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
    def public_analytics_enabled(self) -> bool:
        return bool(self.vite_analytics_enabled or self.optional_analytics_enabled)

    @property
    def public_gtm_id(self) -> str | None:
        return (self.vite_gtm_id or self.google_tag_manager_container_id or "").strip() or None

    @property
    def public_site_url(self) -> str:
        return str(self.vite_site_url or self.public_base_url).rstrip("/")

    @property
    def sharia_pilot_symbol_set(self) -> set[str]:
        return {
            value.strip().upper() for value in self.sharia_pilot_symbols.split(",") if value.strip()
        }

    @property
    def system_brain_username(self) -> str | None:
        value = (self.system_brain_admin_username or "").strip().casefold()
        return value or None

    @property
    def system_brain_authorized_emails(self) -> frozenset[str]:
        """Configured, verified-email operators allowed into System Brain."""
        values = (self.system_brain_username or "", self.system_brain_admin_emails)
        return frozenset(
            email.strip().casefold()
            for value in values
            for email in re.split(r"[,;\n]", value)
            if email.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
