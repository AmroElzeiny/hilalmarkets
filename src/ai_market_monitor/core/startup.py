from ai_market_monitor.core.config import Settings

DEFAULT_SECRET = "development-only-change-me-32-characters"


class RuntimeConfigurationError(RuntimeError):
    pass


def _secret_value(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    return str(value)


def _looks_like_placeholder(value) -> bool:
    raw = _secret_value(value)
    if raw is None:
        return False
    normalized = raw.strip().upper()
    placeholder_tokens = (
        "REPLACE_",
        "REPLACE-WITH",
        "CHANGE_ME",
        "CHANGEME",
        "YOUR_",
        "INSERT_",
    )
    return any(token in normalized for token in placeholder_tokens)


def validate_runtime_configuration(settings: Settings) -> None:
    errors: list[str] = []
    if settings.is_deployed:
        if settings.allow_mock_providers:
            errors.append("ALLOW_MOCK_PROVIDERS must be false in staging and production")
        if settings.app_secret_key.get_secret_value() == DEFAULT_SECRET:
            errors.append("APP_SECRET_KEY must not use the development default")
        if _looks_like_placeholder(settings.app_secret_key):
            errors.append("APP_SECRET_KEY must not use a placeholder value")
        if _looks_like_placeholder(settings.database_url):
            errors.append("DATABASE_URL must not contain placeholder credentials")
        if settings.database_url.startswith("sqlite"):
            errors.append("DATABASE_URL must use PostgreSQL in staging and production")
        if not str(settings.public_base_url).startswith("https://"):
            errors.append("PUBLIC_BASE_URL must use HTTPS in staging and production")
        if settings.app_base_url is not None and not str(settings.app_base_url).startswith(
            "https://"
        ):
            errors.append("APP_BASE_URL must use HTTPS in staging and production when set")
        if settings.scanning_enabled and settings.market_data_provider == "memory":
            errors.append("in-memory market data is forbidden for deployed live scanning")
        if settings.tracedge_market_data_mode == "fixture":
            errors.append(
                "TRACEDGE_MARKET_DATA_MODE=fixture is forbidden in staging and production"
            )
        if settings.tracedge_fixture_market_data_enabled:
            errors.append(
                "TRACEDGE_FIXTURE_MARKET_DATA_ENABLED must be false in staging and production"
            )
        if settings.scanning_enabled and not settings.binance_market_data_enabled:
            errors.append("BINANCE_MARKET_DATA_ENABLED must be true for deployed live scanning")
        if settings.binance_derivatives_enabled and settings.derivatives_context_api_url is None:
            errors.append(
                "DERIVATIVES_CONTEXT_API_URL or a tested derivatives adapter is required "
                "when BINANCE_DERIVATIVES_ENABLED=true"
            )
        if settings.coingecko_enabled and settings.market_metadata_api_url is None:
            errors.append(
                "MARKET_METADATA_API_URL or a tested CoinGecko adapter is required "
                "when COINGECKO_ENABLED=true"
            )
        if settings.alternative_me_enabled and settings.crypto_index_api_url is None:
            errors.append(
                "CRYPTO_INDEX_API_URL or a tested Alternative.me adapter is required "
                "when ALTERNATIVE_ME_ENABLED=true"
            )
        if settings.fred_enabled and (
            settings.macro_market_api_url is None or settings.fred_api_key is None
        ):
            errors.append(
                "MACRO_MARKET_API_URL and FRED_API_KEY are required when FRED_ENABLED=true"
            )
        if settings.ai_interpreter_provider == "openai" and settings.openai_api_key is None:
            errors.append("OPENAI_API_KEY is required when AI_INTERPRETER_PROVIDER=openai")
        if settings.ai_interpreter_provider == "openai" and _looks_like_placeholder(
            settings.openai_api_key
        ):
            errors.append("OPENAI_API_KEY must not use a placeholder value")
        if settings.telegram_enabled:
            if settings.telegram_adapter != "http":
                errors.append("TELEGRAM_ADAPTER=http is required when Telegram is enabled")
            if not settings.telegram_bot_username:
                errors.append("TELEGRAM_BOT_USERNAME is required when Telegram is enabled")
            if settings.telegram_bot_token is None:
                errors.append("TELEGRAM_BOT_TOKEN is required when Telegram is enabled")
            if _looks_like_placeholder(settings.telegram_bot_token):
                errors.append("TELEGRAM_BOT_TOKEN must not use a placeholder value")
            if not settings.telegram_polling_enabled and settings.telegram_webhook_secret is None:
                errors.append(
                    "TELEGRAM_WEBHOOK_SECRET is required when Telegram webhook mode is enabled"
                )
            if _looks_like_placeholder(settings.telegram_webhook_secret):
                errors.append("TELEGRAM_WEBHOOK_SECRET must not use a placeholder value")
        if settings.discord_enabled:
            if settings.discord_adapter == "noop":
                errors.append("NoopDiscordGateway is forbidden when Discord is enabled")
            if not settings.discord_client_id:
                errors.append("DISCORD_CLIENT_ID is required when Discord is enabled")
            if settings.discord_client_secret is None:
                errors.append("DISCORD_CLIENT_SECRET is required when Discord is enabled")
            if settings.discord_bot_token is None:
                errors.append("DISCORD_BOT_TOKEN is required when Discord is enabled")
            if settings.discord_webhook_public_key is None:
                errors.append("DISCORD_WEBHOOK_PUBLIC_KEY is required when Discord is enabled")
            if _looks_like_placeholder(settings.discord_client_secret):
                errors.append("DISCORD_CLIENT_SECRET must not use a placeholder value")
            if _looks_like_placeholder(settings.discord_bot_token):
                errors.append("DISCORD_BOT_TOKEN must not use a placeholder value")
            if _looks_like_placeholder(settings.discord_webhook_public_key):
                errors.append("DISCORD_WEBHOOK_PUBLIC_KEY must not use a placeholder value")
        if settings.billing_enabled:
            if settings.billing_provider == "static":
                errors.append("StaticBillingProvider is forbidden when billing is enabled")
            if settings.billing_webhook_secret is None:
                errors.append("BILLING_WEBHOOK_SECRET is required when billing is enabled")
            if _looks_like_placeholder(settings.billing_webhook_secret):
                errors.append("BILLING_WEBHOOK_SECRET must not use a placeholder value")
            if settings.billing_provider == "stripe" and settings.stripe_secret_key is None:
                errors.append("STRIPE_SECRET_KEY is required for Stripe billing")
            if settings.billing_provider == "stripe" and _looks_like_placeholder(
                settings.stripe_secret_key
            ):
                errors.append("STRIPE_SECRET_KEY must not use a placeholder value")
            if settings.billing_provider == "stripe" and not settings.stripe_price_ids:
                errors.append("STRIPE_PRICE_IDS must map internal plans to Stripe prices")
            if settings.billing_provider == "nowpayments" and settings.nowpayments_api_key is None:
                errors.append("NOWPAYMENTS_API_KEY is required for NOWPayments billing")
            if settings.billing_provider == "nowpayments" and _looks_like_placeholder(
                settings.nowpayments_api_key
            ):
                errors.append("NOWPAYMENTS_API_KEY must not use a placeholder value")
    if errors:
        raise RuntimeConfigurationError("Unsafe runtime configuration:\n- " + "\n- ".join(errors))
