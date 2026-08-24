import re
from functools import lru_cache
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

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


def _is_optional_secret(field: Any) -> bool:
    """Whether a settings field is a credential that is allowed to be absent.

    Read off the annotation rather than a hand-written list of names, because a
    hand-written list is one more thing that goes stale the next time somebody adds a
    key and does not know the list exists.
    """

    annotation = getattr(field, "annotation", None)
    if get_origin(annotation) not in {Union, UnionType}:
        return False
    arguments = get_args(annotation)
    return SecretStr in arguments and type(None) in arguments


#: Every rate-limited scope in the product, named once.
#:
#: There used to be two lists: the rules in ``api/request_guards.py`` and a hand-written
#: set inside the settings validator. Adding a scope to one and not the other stopped the
#: application booting with a message that named neither — which is what happened the
#: first time a new public endpoint was given a ceiling. Both read this now, and
#: ``tests/unit/test_invariant_site_stats.py`` fails if they ever drift apart again.
RATE_LIMIT_SCOPES: tuple[str, ...] = (
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
    "site_analytics",
    "admin_mutation",
)

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

    app_name: str = "Hilal Markets"
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
    #: How long one provider price snapshot is reused, and — through
    #: `LiveMarketQuoteService.refresh_after_ms` — how often the Market page asks for a new
    #: one. At 0.75 the cache never served anybody: the page's own floor made it ask every
    #: two seconds, so every ask was a full round trip to the exchange for all symbols.
    #: This is a monitoring product, not a trading terminal; five seconds is honest, and
    #: the page always shows the time its prices were taken.
    sharia_live_quote_cache_seconds: float = Field(default=5.0, ge=0.5, le=10)
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
    #: How old retained evidence may be, in days, when a reviewer decides a case that
    #: came from the methodology import pack.
    #:
    #: The pack itself states no such number, so one has to be chosen here. The code used
    #: to write 1 — evidence had to be less than a day old — which no human review can
    #: meet: every case became undecidable the day after its research ran, and every
    #: approval failed with "Required evidence is unavailable or older than the
    #: methodology permits". 90 days is the value this repository governed for the SC
    #: Malaysia methodology before the pack replaced it.
    #:
    #: This is a Shariah-governance number. Change it deliberately, with the governance
    #: owner, not to make a stuck queue move.
    sharia_pack_evidence_max_age_days: int = Field(default=90, ge=1, le=3650)
    sharia_identity_discovery_batch_size: int = Field(default=250, ge=1, le=500)
    #: Whether the layered resolver may look for an asset's news and community pages.
    #: Turning it off stops new links being proposed; it withdraws nothing already
    #: proved, and the human tasks it raised stay open.
    sharia_source_resolution_enabled: bool = True
    #: How many assets one resolver sweep may work through. Each asset costs a handful
    #: of fetches against somebody else's site, so this is kept small on purpose and
    #: the sweep simply continues where it left off next time.
    sharia_source_resolution_batch_size: int = Field(default=25, ge=1, le=200)
    #: How long a proved link is trusted before it is fetched again. A source that
    #: went dead between sweeps is caught here rather than at review time.
    sharia_source_recheck_days: int = Field(default=30, ge=1, le=365)
    #: Whether the resolver may search the open web for a coin's own news channels.
    #: True by default, but it does nothing at all until a search key is set, so
    #: turning it on is the same act as configuring it.
    sharia_source_search_enabled: bool = True
    sharia_source_search_provider: Literal["google", "brave", "none"] = "google"
    #: How many answers one question asks for. Five questions are asked per coin, so
    #: this is not the number of links registered — almost every answer is thrown away
    #: for not being the project's own page.
    sharia_source_search_results: int = Field(default=10, ge=1, le=20)
    #: How many proved links each of news and community should end up with. One is a
    #: single point of failure: the day it moves, the coin has no way to hear about the
    #: project. The layers keep looking until a coin reaches this many.
    sharia_source_links_per_category: int = Field(default=3, ge=1, le=10)
    #: How low a source's activity score may fall before it stops counting as coverage
    #: and the layers go looking for a replacement alongside it. Activity says how alive
    #: and how relevant a page is; it is never a Shariah status and is never shown as one.
    sharia_source_activity_floor: float = Field(default=0.45, ge=0.0, le=1.0)
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
            # A visit beacon is tiny and frequent: one when the page opens, one every
            # fifteen seconds while somebody reads, one when they leave. The ceiling is
            # what one browser can honestly produce, not what a form submission costs.
            "site_analytics": {"limit": 120, "window_seconds": 60},
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
    #: Credentials for looking a coin's own news channels up on the open web. Both are
    #: needed together: the key authorises the call, the engine id says which
    #: Programmable Search Engine answers it. With either missing the search layer
    #: offers nothing and every other layer carries on unchanged.
    google_search_api_key: SecretStr | None = None
    google_search_engine_id: str | None = None
    #: The alternative engine. One key, no engine id.
    brave_search_api_key: SecretStr | None = None
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
    # ── The database connection pool ──────────────────────────────────────────────────
    #
    # These were never chosen. `create_async_engine` was called with no pool arguments at
    # all, so SQLAlchemy's defaults applied: 5 kept open plus 10 overflow, and a **thirty
    # second** wait for a free one. Every process gets its own pool — two API workers, the
    # Celery parent and its child, and the scheduler — so the deployment could reach 75
    # connections against PostgreSQL's default ceiling of 100, with nothing anywhere
    # saying so. Adding one more API worker would have crossed it.
    #
    # The thirty-second wait is the worse half. A saturated pool did not fail, it *froze*:
    # every page hung for half a minute and then broke, which reads as "the whole site is
    # down" and names nothing. Five seconds, matching `provider_pool_timeout_seconds`
    # above, turns that into a fast and legible failure.
    #
    # `tests/unit/test_invariant_database_pool.py` multiplies these by the number of
    # processes this deployment starts and fails if the total approaches the server's
    # limit, so the arithmetic cannot be forgotten when a worker is added.
    database_pool_size: int = Field(default=5, ge=1, le=100)
    #: Extra connections allowed above `database_pool_size` during a burst. They are
    #: closed again when the burst passes, so this is headroom, not a running cost.
    database_pool_overflow: int = Field(default=5, ge=0, le=100)
    #: How long a request waits for a free connection before failing. Short on purpose.
    database_pool_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    #: Drop and reopen a connection older than this. Idle TCP connections are silently
    #: dropped by routers and by PostgreSQL itself; `pool_pre_ping` then pays a failed
    #: round trip to discover it. Recycling first avoids the discovery.
    database_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)

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
    #: How many symbols the product asks the exchange about at the same time.
    #:
    #: One number for both kinds of scan, because it answers one question — how hard we
    #: may lean on the exchange at once — and the exchange does not care which of our
    #: containers is asking. It was `ON_DEMAND_SCAN_CONCURRENCY`, read only by the scan a
    #: person triggers by hand; the scheduled scan that runs all day did not overlap
    #: anything at all.
    #:
    #: That cost more than it sounds. Measured on the live server on 24 August 2026: a
    #: scheduled scan took **198 seconds**, while monitors watching one-minute candles were
    #: being checked about once an hour. Waiting for eight answers at once costs almost no
    #: extra memory, because waiting is not work.
    #:
    #: Raising it further does less than it looks like it should, and the reason is worth
    #: knowing: ccxt is configured with `enableRateLimit`, so it spaces requests by its own
    #: clock — 50 ms per unit of weight, shared by everything in the process. Concurrency
    #: overlaps the *waiting for an answer*; it cannot overlap the spacing. Past the point
    #: where the rate limiter is the slow part, more concurrency buys nothing. Sending
    #: fewer requests is what buys something, which is what `market_cache_enabled` is for.
    scan_symbol_concurrency: int = Field(default=8, ge=1, le=50)
    #: Share one reading of a market between every monitor that wants it.
    #:
    #: Fifty monitors on one-minute candles are fifty checks a minute, nearly all asking
    #: about the same markets. Measured against Binance on 24 August 2026, one request for
    #: 302 candles costs 100 ms of rate-limit sleep, and the unfiltered ticker endpoint
    #: costs 4,000 ms and 1.9 MB. Fifty monitors × 22 symbols × 100 ms is 110 seconds of
    #: sleeping to fill a 60-second minute: the work does not fit in the time, and no
    #: amount of concurrency changes that, because a rate limit is not a queue that goes
    #: faster when more callers join it.
    #:
    #: Off is the old behaviour, kept as a way back that needs no code change.
    market_cache_enabled: bool = True
    #: The longest one stored reading may be reused, in seconds.
    #:
    #: The real window is `min(one candle, this)`. The candle half is free — inside a
    #: single one-minute candle, every monitor asking about one-minute candles wants the
    #: same closed candles. This half is the guard for the other end: without it a daily
    #: monitor would hold one reading for a whole day and its forming candle would never
    #: move. 60 keeps the worst case at one minute on every timeframe.
    market_cache_max_age_seconds: int = Field(default=60, ge=1, le=3600)
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
    # The default was `contact@trace-edge.com`, an earlier product's inbox. Because the
    # field carried it, the property below never reached its own fallback: every
    # deployment that had not set SUPPORT_EMAIL showed a customer that address.
    support_email: str | None = "support@hilalmarkets.com"
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
    #: First-party visit counting on the public site: how many people, how long they
    #: stayed, and what they did next. Writes no cookie and stores no address, so it does
    #: not depend on the cookie banner. Off makes the collector accept and discard.
    site_visit_measurement_enabled: bool = True
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
    # -- Hilal, the assistant inside the dashboard ------------------------------
    #
    # A different assistant from the one on the public site, on purpose. That one
    # answers a visitor who has no account; this one answers a signed-in customer about
    # their own listings, Passports, methodologies and plan. Different knowledge,
    # different limits, different storage, so neither can drift into the other's job.
    #
    # It explains what is recorded. It never builds a strategy and never gives
    # financial advice — see `services/hilal_chat_agent.py`, which refuses both.
    hilal_chat_enabled: bool = True
    #: Falls back to `openai_model` when unset, like every other assistant here.
    hilal_chat_ai_model: str | None = None
    hilal_chat_ai_reasoning_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh"
    ] = "low"
    hilal_chat_ai_timeout_seconds: int = Field(default=30, ge=3, le=120)
    hilal_chat_ai_max_output_tokens: int = Field(default=900, ge=256, le=2400)
    hilal_chat_ai_max_estimated_cost_usd_per_turn: float = Field(
        default=0.02,
        gt=0,
        le=1,
    )
    hilal_chat_ai_provider_attempts: int = Field(default=2, ge=1, le=3)
    hilal_chat_ai_circuit_failure_threshold: int = Field(default=5, ge=1, le=20)
    hilal_chat_ai_circuit_reset_seconds: int = Field(default=60, ge=10, le=900)
    hilal_chat_ai_max_history_messages: int = Field(default=16, ge=2, le=40)
    hilal_chat_message_max_length: int = Field(default=800, ge=100, le=4000)
    hilal_chat_comment_max_length: int = Field(default=2000, ge=100, le=8000)
    #: What one person may spend on Hilal in a 24-hour cycle, in US dollars. The cycle
    #: is the UTC day, so it resets at 00:00 UTC — `ai_budget.day_window` decides that,
    #: and it is the only thing that decides it.
    hilal_chat_free_daily_usd: float = Field(default=0.10, gt=0, le=100)
    #: How much more a paying subscriber gets. Five times the free allowance.
    #:
    #: Only consulted when the paid allowance below is left unset. Because it is a whole
    #: number, the two allowances share one dial: a free figure of 0.15 can only buy a
    #: paid one of 0.15, 0.30, 0.45 and so on.
    hilal_chat_paid_daily_multiplier: int = Field(default=5, ge=1, le=100)
    #: The paying subscriber's allowance, stated outright. Wins over the multiplier when
    #: set, which is what lets the two figures be chosen independently. Left unset, the
    #: multiplier decides and nothing changes for an existing deployment.
    hilal_chat_paid_daily_usd: float | None = Field(default=None, gt=0, le=1000)
    #: How many evidence rows one turn may carry. A bound, so a large account cannot
    #: quietly turn one question into an expensive one.
    hilal_chat_max_evidence_assets: int = Field(default=24, ge=1, le=200)
    hilal_chat_retention_days: int = Field(default=365, ge=7, le=3650)
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
    # -- Where a page actually goes ---------------------------------------------
    #
    # A page-worthy alert names a primary and a fallback route in
    # `observability/alerts.py`. These say where those two routes land. Both are
    # optional and both default to nothing, so a deployment that has not set them
    # records the page in the operational issue queue and says plainly that it could
    # not be delivered — rather than pretending it was sent.
    #: The operations Telegram chat. Kept apart from the Sharia review chat: the two
    #: audiences are different, and mixing a page into a review queue buries both.
    operational_alert_telegram_chat_id: str | None = None
    #: Where an operations page is emailed when Telegram is the wrong path or fails.
    operational_alert_email: str | None = None
    #: How long one firing rule stays quiet after it has paged once. Without it an
    #: hour-long outage sends sixty identical messages and the channel gets muted.
    operational_alert_repeat_minutes: int = Field(default=30, ge=1, le=1440)
    operational_alert_max_attempts: int = Field(default=5, ge=1, le=20)
    #: How open the product is. The authority for what every public surface shows:
    #: advertised routes, calls to action, pricing and checkout exposure, offered
    #: channels, and what the public assistant may claim. See
    #: `core/launch_stage.py`, which holds the exposure table and the legal moves.
    #
    # Hilal Markets is open to the public. The default is the stage the product is
    # actually in, so a deployment that sets neither variable serves the live site
    # rather than a waitlist for a product anybody can already open an account on.
    launch_stage: LaunchStage = LaunchStage.PUBLIC_LAUNCH
    # The emergency ceiling, kept as an environment variable on purpose.
    #
    # It is no longer an independent authority: no surface reads it directly any
    # more, they read `waitlist_mode`, which is derived from the resolved stage.
    # What it still does is cap exposure. While it is true the product can be no more
    # open than `public_waitlist`, whatever LAUNCH_STAGE says, so one variable can
    # pull the site back without a deploy. It only ever narrows.
    #
    # Off by default now that the product has launched. Left in place because the one
    # thing it is for — pulling the public site back without a deploy — is needed more
    # after a launch than before one.
    public_waitlist_mode: bool = False
    contact_form_sender_email: str = "office@hilalmarkets.com"
    contact_form_recipient_email: str = "office@hilalmarkets.com"
    # How many support messages are accepted, and from whom.
    #
    # One rule for both doors — the public /contact form and the dashboard's support
    # form — enforced by `services/support_intake.py`. The first two are what one
    # person may send; the third is the flood ceiling that stops a crowd of fresh
    # addresses doing what one address cannot. All three are counted over the same
    # window.
    support_intake_max_per_email: int = Field(default=2, ge=1, le=100)
    support_intake_max_per_client: int = Field(default=2, ge=1, le=100)
    support_intake_max_per_hour: int = Field(default=20, ge=1, le=10_000)
    support_intake_window_seconds: int = Field(default=3600, ge=60, le=86_400)
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

    # --- What keeps the website up while a process is replaced ------------------------
    #
    # On 22 August 2026 the API process was killed by the kernel twice (`uvicorn`, 694 MB).
    # It ran as a single process, so each kill took the whole website down until Docker
    # restarted the container.
    #
    # More than one worker is what makes that survivable, and recycling is what stops a
    # worker ever reaching the ceiling. Both are built into the uvicorn already pinned
    # here — `--workers`, `--limit-max-requests`, and a parent that restarts a worker that
    # dies. Nothing extra is installed for this.
    #
    # `ai_market_monitor/serve.py` is the only reader of these three. The Docker command
    # calls it rather than spelling the numbers out, so there is one place they live.
    api_worker_processes: int = Field(default=2, ge=1, le=16)
    #: A worker retires after this many requests and the parent starts a fresh one. This
    #: is the cure for a slow leak: the process never lives long enough to grow into the
    #: ceiling, whatever is leaking and whether or not anybody has found it yet.
    #:
    #: 800 was too eager. Retiring is not free: the proxy holds keep-alive connections to
    #: the worker, and every one of them breaks when it goes. On 22 August 2026 the 502
    #: timestamps matched worker start times to the second. Two things changed together —
    #: the proxy now retries a dropped upstream (`deploy/Caddyfile`), and a worker lives
    #: long enough that retiring is rare rather than routine.
    api_worker_max_requests: int = Field(default=20_000, ge=50, le=1_000_000)
    #: Random extra requests added per worker before it retires.
    #:
    #: **This is what makes it seamless, and zero would undo the whole thing.** Workers
    #: start together, so without jitter they reach the same count at the same moment and
    #: all retire at once — which is an outage, just a tidier one. With jitter they retire
    #: at different times and the others keep serving.
    api_worker_max_requests_jitter: int = Field(default=5_000, ge=0, le=1_000_000)

    # --- What stops one background task taking the whole server down ------------------
    #
    # On 22 August 2026 the live server died twice. The kernel log says why:
    #
    #   Out of memory: Killed process 564376 (celery) anon-rss:1428256kB   15:48:53
    #   Out of memory: Killed process 597555 (celery) anon-rss:1428884kB   16:50:20
    #   systemd invoked oom-killer                                        16:50:19
    #   Total swap = 0kB, 1023866 pages RAM (about 3.9 GB)
    #
    # A Celery worker child had grown to 1.4 GB and nothing ever replaced it. Celery's
    # default worker count is one per CPU, so on the two-CPU server that was 2.8 GB of
    # workers on a machine with less than 3.8 GB usable and no swap at all. The kernel
    # then started killing whatever it could, including systemd itself, and the machine
    # stopped answering — SSH included.
    #
    # Celery has both limits built in and neither was set. They are set here, not on the
    # command line, so every way of starting a worker gets them: the compose file, a
    # `celery` command typed by hand, and a local run.
    #
    # These bound *growth between tasks*. A single task that allocates a gigabyte in one
    # go is not stopped by them — that is what the per-container memory limit in
    # docker-compose.prod.yml is for. Three layers, each catching what the one before
    # cannot: recycle a grown child, cap the container, and give the kernel swap.
    #: One child, not one per CPU.
    #:
    #: Two children was the first attempt and the server killed one of them at 890 MB
    #: (`Memory cgroup out of memory`, 17:20 on 22 August 2026). Two CPUs do not mean two
    #: children are affordable — what decides it is memory, and this machine has 3.9 GB
    #: shared with PostgreSQL, Redis, Caddy and the API. Background scans run one at a
    #: time now. That is slower and it stays up, which is the right trade for a monitoring
    #: product: a late alert is a problem, a dead server is a worse one.
    celery_worker_concurrency: int = Field(default=1, ge=1, le=32)
    #: A child is replaced once it has run this many tasks. Bounds slow leaks that no
    #: single task is responsible for. Lowered from 100 after the second outage: recycling
    #: is cheap — a few seconds of process start — and memory here is not.
    celery_worker_max_tasks_per_child: int = Field(default=50, ge=1, le=100000)
    #: Kilobytes. A child above this is replaced after it finishes its current task.
    #:
    #: The number has to fit *inside* the worker container's own ceiling, and the sum is
    #: not just the children: Celery's prefork pool is one parent process plus
    #: `celery_worker_concurrency` children, and the parent has the whole application
    #: loaded too. Measured on the live server, the parent is around 150-200 MB.
    #:
    #:   200 MB parent + 2 children x 350 MB = 900 MB, inside the 1024 MB container
    #:
    #: Getting this wrong in the other direction is silent and total: if the parent plus
    #: the children can exceed the container, Docker kills the container first and Celery
    #: never reaches the point where it would have recycled the grown child. The setting
    #: would look present and do nothing. `test_invariant_container_memory_limits.py`
    #: checks the arithmetic, parent included.
    celery_worker_max_memory_per_child_kb: int = Field(
        default=350_000, ge=50_000, le=8_000_000
    )

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
    # --- How long finished scan history is kept ---------------------------------
    # Scan rows are the fastest-growing thing this product writes: one job per monitor
    # per interval, and one result per symbol x timeframe x direction inside each job.
    # Nothing removed them, so the table only ever grew, and the storage a new customer
    # needs was being spent on evidence of scans nobody can act on any more.
    #
    # Deleting a job takes its results and evaluation cycles with it through the existing
    # CASCADE, and leaves incident records and capability-extension rows intact through
    # their SET NULL. That is why retention is expressed on the job alone.
    #
    # 3 days, decided on 24 August 2026, and the reason is arithmetic rather than taste.
    # Measured on the live server: 4.22 KB per scan row. Fifty monitors watching one-minute
    # candles write about 1.58 million rows a day — 6.7 GB a day — against a 40 GB disk.
    # Thirty days of that is roughly 200 GB, so the disk fills in under a week.
    #
    # What survives this: the alerts a person received, which have no retention rule at
    # all, and the setups that were found. What does not survive is the proof behind them —
    # the per-condition results and near-miss snapshots that answer "why did this fire?".
    # A person can see an alert from any time; they can see the working behind it for three
    # days. Raising this number is safe only while the disk can hold the answer above.
    scan_history_retention_days: int = Field(default=3, ge=1, le=3650)
    # A queued job whose dispatch message is gone can never run: nothing re-sends it, and
    # `recover_stale_or_retryable` only rescues queued rows that carry a retry time. Past
    # this age it is abandoned, not pending, and saying so is what lets retention reach it.
    # Must stay far above the claim timeout so a job in flight is never mistaken for one.
    scan_job_abandoned_after_hours: int = Field(default=24, ge=1, le=720)
    # An upper bound on rows removed per run, so a first run against years of history is a
    # series of short transactions rather than one long lock on a live table.
    scan_history_purge_batch: int = Field(default=5000, ge=100, le=100000)
    #: How many of those batches one nightly run may do before it stops.
    #:
    #: It exists so the loop has an end, not to limit how much is deleted: 200 batches of
    #: 5000 is a million jobs a night, which is far above anything this product writes.
    #: The number that used to limit deletion was the batch size itself, because the purge
    #: did exactly one batch and stopped — so it could never remove more in a night than it
    #: removed in a single transaction, whatever had arrived that day.
    scan_history_purge_max_batches: int = Field(default=200, ge=1, le=10000)
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

    @field_validator("hilal_chat_paid_daily_usd", mode="before")
    @classmethod
    def _blank_paid_allowance_means_unset(cls, value: object) -> object:
        """An empty line in an env file means "not set", for a number as much as for text.

        An env file has no way to write "absent" other than leaving the value empty, and
        every optional *text* setting in this project already reads a bare ``KEY=`` that
        way. A number did not: pydantic tried to parse ``""`` as a float and startup died
        on a template that was only saying "no opinion, use the multiplier".

        That is the same shape as the two list settings that made both example files
        unloadable, so it is fixed here rather than by writing a number into the template
        that no deployment actually wants.
        """

        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_hilal_chat_allowances(self) -> "Settings":
        """A paying subscriber must never get less than someone paying nothing.

        Stating the paid allowance outright removed a constraint the multiplier used to
        carry for free: with ``ge=1`` it could not express "less than the free tier". Now
        that the figure stands alone, nothing stops a typo turning a subscription into a
        downgrade, and it would show up as paying customers hitting a wall sooner than
        visitors rather than as anything resembling a configuration error.
        """

        stated = self.hilal_chat_paid_daily_usd
        if stated is not None and stated < self.hilal_chat_free_daily_usd:
            raise ValueError(
                "HILAL_CHAT_PAID_DAILY_USD must be at least HILAL_CHAT_FREE_DAILY_USD, "
                "otherwise a paying subscriber receives a smaller allowance than a free one"
            )
        return self

    @model_validator(mode="after")
    def validate_scan_retention_bounds(self) -> "Settings":
        """Refuse a pair of windows that would expire work still legitimately in flight.

        The two settings are independent knobs with overlapping ranges: the claim timeout
        may be set as high as a day, and the abandonment window as low as an hour. Nothing
        in either range is wrong on its own, and the wrong combination fails silently and
        invisibly — scans that a worker is still allowed to be holding get marked failed,
        then deleted by the purge, and the monitor simply produces less than it should.
        Checking the relationship is the only place that combination can be caught.
        """

        if self.scan_job_abandoned_after_hours * 3600 <= self.scan_job_claim_timeout_seconds:
            raise ValueError(
                "SCAN_JOB_ABANDONED_AFTER_HOURS must be longer than "
                "SCAN_JOB_CLAIM_TIMEOUT_SECONDS, otherwise scan cleanup would expire "
                "jobs a worker is still permitted to be running"
            )
        return self

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
        if set(self.api_rate_limits) != set(RATE_LIMIT_SCOPES):
            raise ValueError("API_RATE_LIMITS must define exactly the supported security scopes")
        for scope, values in self.api_rate_limits.items():
            if set(values) != {"limit", "window_seconds"}:
                raise ValueError(f"API rate limit {scope} has an invalid shape")
            if values["limit"] < 1 or values["window_seconds"] < 1:
                raise ValueError(f"API rate limit {scope} must use positive values")
        # A per-person allowance above the whole-product ceiling is not a stricter
        # setting, it is an incoherent one: the first person through the door would use
        # up everybody's allowance. Refused at startup rather than discovered by the
        # second customer of the hour.
        if self.support_intake_max_per_email > self.support_intake_max_per_hour:
            raise ValueError(
                "SUPPORT_INTAKE_MAX_PER_EMAIL cannot exceed SUPPORT_INTAKE_MAX_PER_HOUR"
            )
        if self.support_intake_max_per_client > self.support_intake_max_per_hour:
            raise ValueError(
                "SUPPORT_INTAKE_MAX_PER_CLIENT cannot exceed SUPPORT_INTAKE_MAX_PER_HOUR"
            )
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

    @model_validator(mode="before")
    @classmethod
    def blank_secret_means_unset(cls, values: object) -> object:
        """``KEY=`` in an env file means "not configured", not "configured as nothing".

        Every optional credential in this file is read by a caller that asks
        ``is None`` and takes the "no provider" path when it is. An empty line in
        ``.env`` produced ``SecretStr('')`` instead, which is not None — so the caller
        took the configured path and sent an empty key. The provider then answered 401
        or 403, and the failure looked like a bad credential rather than a missing one.
        The two examples in git ship exactly that empty line for several keys, so
        anybody copying one inherited the problem.

        The rule is applied to the whole family rather than to the key that happened to
        be reported: any optional secret, blank, is unset.
        """

        if not isinstance(values, dict):
            return values
        cleaned = dict(values)
        for name, field in cls.model_fields.items():
            for key in (name, field.alias, field.validation_alias):
                if not isinstance(key, str) or key not in cleaned:
                    continue
                value = cleaned[key]
                if isinstance(value, str) and not value.strip() and _is_optional_secret(field):
                    cleaned[key] = None
        return cleaned

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
    def fixed_auth_code(self) -> str | None:
        """The predictable one-time code, or ``None`` when one must not be issued.

        One owner for a decision two services were making separately. ``web_auth``
        checked ``app_env == "test"`` and the six-digit shape before honouring
        ``AUTH_TEST_FIXED_CODE``; the System Brain login read the raw setting with
        neither check. So the same variable meant "a convenience for the test suite"
        in one place and "the second factor on the governance console is now a value
        printed in a config file" in the other.

        The environment is part of the answer, not a caller's responsibility. A
        deployed process is refused the code outright at startup as well, so this can
        never be the only thing standing between an operator's mistake and a fixed
        admin OTP.
        """

        candidate = (self.auth_test_fixed_code or "").strip()
        if self.app_env != "test":
            return None
        if len(candidate) != 6 or not candidate.isdigit():
            return None
        return candidate

    @property
    def support_inbox_email(self) -> str:
        # The fallback used to be `contact@trace-edge.com`: the support address of an
        # earlier product, shown to a customer whenever SUPPORT_EMAIL was unset.
        return (self.support_email or "support@hilalmarkets.com").strip()

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
