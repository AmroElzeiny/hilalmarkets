import re
from pathlib import Path
from urllib.parse import urlsplit

from ai_market_monitor.core.config import WHATSAPP_TEMPLATE_EVENTS, Settings

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
        if (
            settings.ai_setup_evaluator_enabled
            or settings.ai_setup_evaluator_faults_enabled
        ):
            errors.append(
                "AI Setup evaluator controls and fault injection are forbidden in "
                "staging and production"
            )
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
        if not settings.api_rate_limiting_enabled:
            errors.append("API_RATE_LIMITING_ENABLED must be true in staging and production")
        if not settings.api_rate_limit_fail_closed:
            errors.append("API_RATE_LIMIT_FAIL_CLOSED must be true in staging and production")
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
        if settings.scanning_enabled and not settings.sharia_screening_enforced:
            errors.append("SHARIA_SCREENING_ENFORCED must be true for deployed live scanning")
        if settings.sharia_screening_enforced and settings.sharia_allow_legacy_unscreened_local:
            errors.append(
                "SHARIA_ALLOW_LEGACY_UNSCREENED_LOCAL must be false in staging and production"
            )
        if settings.sharia_screening_enforced:
            if not settings.sharia_admin_telegram_chat_id:
                errors.append(
                    "SHARIA_ADMIN_TELEGRAM_CHAT_ID is required for deployed Sharia governance"
                )
            if not settings.telegram_enabled:
                errors.append(
                    "TELEGRAM_ENABLED must be true for deployed Sharia governance notifications"
                )
            if settings.openai_api_key is None:
                errors.append(
                    "OPENAI_API_KEY is required for deployed Sharia factual research"
                )
            if settings.sharia_ai_service_tier != "flex":
                errors.append(
                    "SHARIA_AI_SERVICE_TIER must be flex for the configured research workflow"
                )
            if not settings.sharia_scraper_obey_robots:
                errors.append(
                    "SHARIA_SCRAPER_OBEY_ROBOTS must be true in staging and production"
                )
            if settings.sharia_scraper_download_delay_seconds < 1:
                errors.append(
                    "SHARIA_SCRAPER_DOWNLOAD_DELAY_SECONDS must be at least 1 when deployed"
                )
        if settings.binance_derivatives_enabled and settings.derivatives_context_api_url is None:
            errors.append(
                "DERIVATIVES_CONTEXT_API_URL or a tested derivatives adapter is required "
                "when BINANCE_DERIVATIVES_ENABLED=true"
            )
        # Canonical Sharia identity discovery has a tested, read-only CoinGecko
        # adapter and does not require the optional generic metadata endpoint.
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
        if settings.ai_agent_control_enabled:
            if settings.ai_agent_shadow_mode:
                errors.append(
                    "AI_AGENT_SHADOW_MODE must be false when live agent control is enabled"
                )
            if settings.ai_agent_rollout_percent != 100:
                errors.append(
                    "AI_AGENT_ROLLOUT_PERCENT must be 100 when live agent control is enabled"
                )
            if not settings.capability_extension_enabled:
                errors.append(
                    "CAPABILITY_EXTENSION_ENABLED must be true when live agent control is enabled"
                )
            if settings.openai_api_key is None:
                errors.append("OPENAI_API_KEY is required when live agent control is enabled")
        if (
            settings.capability_extension_enabled
            and settings.capability_extension_preflight_exchange != "binance"
        ):
            errors.append(
                "CAPABILITY_EXTENSION_PREFLIGHT_EXCHANGE must be binance for private beta"
            )
        if settings.public_chat_enabled:
            if not settings.public_chat_ai_enabled:
                errors.append(
                    "PUBLIC_CHAT_AI_ENABLED must be true when deployed public chat is enabled"
                )
            if settings.openai_api_key is None:
                errors.append("OPENAI_API_KEY is required when the public chat is enabled")
            elif _looks_like_placeholder(settings.openai_api_key):
                errors.append("OPENAI_API_KEY must not use a placeholder value")
            if settings.email_adapter != "smtp":
                errors.append("EMAIL_ADAPTER=smtp is required when the public chat is enabled")
            required_public_chat_email = {
                "SMTP_HOST": settings.smtp_host,
                "SMTP_USERNAME": settings.smtp_username,
                "SMTP_PASSWORD": settings.smtp_password,
                "SMTP_FROM_EMAIL": settings.smtp_from_email,
            }
            for name, value in required_public_chat_email.items():
                if value is None or not (_secret_value(value) or "").strip():
                    errors.append(f"{name} is required when the public chat is enabled")
                elif _looks_like_placeholder(value):
                    errors.append(f"{name} must not use a placeholder value")
            if "@" not in settings.public_chat_inquiry_email:
                errors.append("PUBLIC_CHAT_INQUIRY_EMAIL must be a valid delivery address")
            if settings.public_chat_notion_enabled and not Path(
                settings.public_chat_notion_root
            ).is_dir():
                errors.append(
                    "PUBLIC_CHAT_NOTION_ROOT must be an existing directory when "
                    "PUBLIC_CHAT_NOTION_ENABLED=true"
                )
        if settings.public_forms_enabled:
            for name, value in {
                "CONTACT_FORM_SENDER_EMAIL": settings.contact_form_sender_email,
                "CONTACT_FORM_RECIPIENT_EMAIL": settings.contact_form_recipient_email,
            }.items():
                if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value.strip()):
                    errors.append(f"{name} must be a valid email address")
            if settings.email_adapter != "smtp":
                errors.append("EMAIL_ADAPTER=smtp is required when public forms are enabled")
        if settings.waitlist_google_sheets_enabled:
            configured = settings.waitlist_google_sheets_webhook_url
            endpoint = configured.get_secret_value().strip() if configured else ""
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "script.google.com"
                or not parsed.path.startswith("/macros/s/")
                or not parsed.path.endswith("/exec")
            ):
                errors.append(
                    "WAITLIST_GOOGLE_SHEETS_WEBHOOK_URL must be an HTTPS "
                    "Google Apps Script /exec URL"
                )
            secret = settings.waitlist_google_sheets_webhook_secret
            if secret is None or not secret.get_secret_value().strip():
                errors.append(
                    "WAITLIST_GOOGLE_SHEETS_WEBHOOK_SECRET is required when "
                    "Sheet delivery is enabled"
                )
        if settings.public_analytics_enabled:
            if not settings.public_gtm_id:
                errors.append("VITE_GTM_ID is required when analytics is enabled")
            if settings.public_gtm_id and not re.fullmatch(
                r"GTM-[A-Z0-9]+", settings.public_gtm_id
            ):
                errors.append("VITE_GTM_ID must be a valid GTM container ID")
            if settings.vite_ga4_measurement_id:
                errors.append(
                    "VITE_GA4_MEASUREMENT_ID must be empty; configure GA4 inside GTM"
                )
        if settings.vite_meta_pixel_enabled:
            if not settings.marketing_consent_enabled:
                errors.append(
                    "MARKETING_CONSENT_ENABLED must be true when Meta Pixel is enabled"
                )
            if not settings.vite_meta_pixel_id or not re.fullmatch(
                r"[0-9]{5,32}", settings.vite_meta_pixel_id.strip()
            ):
                errors.append("VITE_META_PIXEL_ID must be a valid numeric Pixel ID")
        if settings.vite_x_pixel_enabled:
            if not settings.marketing_consent_enabled:
                errors.append(
                    "MARKETING_CONSENT_ENABLED must be true when X Pixel is enabled"
                )
            if not settings.vite_x_pixel_id or not re.fullmatch(
                r"[A-Za-z0-9]{3,32}", settings.vite_x_pixel_id.strip()
            ):
                errors.append("VITE_X_PIXEL_ID must be a valid X Pixel ID")
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
        if settings.whatsapp_enabled:
            if settings.whatsapp_adapter != "http":
                errors.append("WHATSAPP_ADAPTER=http is required when WhatsApp is enabled")
            required_whatsapp_values = {
                "WHATSAPP_GRAPH_API_VERSION": settings.whatsapp_graph_api_version,
                "WHATSAPP_ACCESS_TOKEN": settings.whatsapp_access_token,
                "WHATSAPP_APP_SECRET": settings.whatsapp_app_secret,
                "WHATSAPP_VERIFY_TOKEN": settings.whatsapp_verify_token,
                "WHATSAPP_PHONE_NUMBER_ID": settings.whatsapp_phone_number_id,
                "WHATSAPP_BUSINESS_ACCOUNT_ID": settings.whatsapp_business_account_id,
                "WHATSAPP_BUSINESS_PHONE_E164": settings.whatsapp_business_phone_e164,
            }
            for name, value in required_whatsapp_values.items():
                secret_value = _secret_value(value)
                if not secret_value or not secret_value.strip():
                    errors.append(f"{name} is required when WhatsApp is enabled")
                elif _looks_like_placeholder(value):
                    errors.append(f"{name} must not use a placeholder value")
            required_template_events = set(WHATSAPP_TEMPLATE_EVENTS)
            if not settings.whatsapp_opportunity_alerts_enabled:
                required_template_events.discard("confirmed_research_event")
            missing_template_events = sorted(
                required_template_events - set(settings.whatsapp_template_names)
            )
            if missing_template_events:
                errors.append(
                    "WHATSAPP_TEMPLATE_NAMES must configure approved production templates for: "
                    + ", ".join(missing_template_events)
                )
            for event_type, template_config in settings.whatsapp_template_names.items():
                names = (
                    template_config.values()
                    if isinstance(template_config, dict)
                    else [template_config]
                )
                if any(_looks_like_placeholder(name) for name in names):
                    errors.append(
                        f"WHATSAPP_TEMPLATE_NAMES.{event_type} must not use a placeholder value"
                    )
        if settings.billing_enabled:
            configured_providers = {
                value
                for value in (
                    settings.billing_card_provider,
                    settings.billing_crypto_provider,
                )
                if value != "disabled"
            }
            if not configured_providers:
                configured_providers.add(settings.billing_provider)
            if "static" in configured_providers:
                errors.append("StaticBillingProvider is forbidden when billing is enabled")
            if "stripe" in configured_providers and settings.billing_webhook_secret is None:
                errors.append("BILLING_WEBHOOK_SECRET is required for Stripe billing")
            if "stripe" in configured_providers and _looks_like_placeholder(
                settings.billing_webhook_secret
            ):
                errors.append("BILLING_WEBHOOK_SECRET must not use a placeholder value")
            if "stripe" in configured_providers and settings.stripe_secret_key is None:
                errors.append("STRIPE_SECRET_KEY is required for Stripe billing")
            if "stripe" in configured_providers and _looks_like_placeholder(
                settings.stripe_secret_key
            ):
                errors.append("STRIPE_SECRET_KEY must not use a placeholder value")
            if "stripe" in configured_providers and not settings.stripe_price_ids:
                errors.append("STRIPE_PRICE_IDS must map internal plans to Stripe prices")
            if "creem" in configured_providers and settings.creem_api_key is None:
                errors.append("CREEM_API_KEY is required for Creem billing")
            if "creem" in configured_providers and _looks_like_placeholder(
                settings.creem_api_key
            ):
                errors.append("CREEM_API_KEY must not use a placeholder value")
            if "creem" in configured_providers and settings.creem_webhook_secret is None:
                errors.append("CREEM_WEBHOOK_SECRET is required for Creem billing")
            if "creem" in configured_providers and _looks_like_placeholder(
                settings.creem_webhook_secret
            ):
                errors.append("CREEM_WEBHOOK_SECRET must not use a placeholder value")
            if "creem" in configured_providers and not settings.creem_product_ids:
                errors.append("CREEM_PRODUCT_IDS must map plans and billing periods")
            if "creem" in configured_providers and settings.creem_product_ids:
                required_creem_products = {
                    "trader_monthly",
                    "trader_annual",
                    "pro_monthly",
                    "pro_annual",
                }
                missing_products = sorted(
                    required_creem_products - settings.creem_product_ids.keys()
                )
                if missing_products:
                    errors.append(
                        "CREEM_PRODUCT_IDS is missing required products: "
                        + ", ".join(missing_products)
                    )
                invalid_products = sorted(
                    key
                    for key, value in settings.creem_product_ids.items()
                    if key in required_creem_products
                    and not str(value).startswith("prod_")
                )
                if invalid_products:
                    errors.append(
                        "CREEM_PRODUCT_IDS contains invalid product IDs for: "
                        + ", ".join(invalid_products)
                    )
            if (
                settings.app_env == "production"
                and "creem" in configured_providers
                and urlsplit(str(settings.creem_api_base)).hostname != "api.creem.io"
            ):
                errors.append("CREEM_API_BASE must use api.creem.io in production")
            if "nowpayments" in configured_providers and settings.nowpayments_api_key is None:
                errors.append("NOWPAYMENTS_API_KEY is required for NOWPayments billing")
            if "nowpayments" in configured_providers and _looks_like_placeholder(
                settings.nowpayments_api_key
            ):
                errors.append("NOWPAYMENTS_API_KEY must not use a placeholder value")
            if (
                "nowpayments" in configured_providers
                and settings.nowpayments_ipn_secret is None
                and settings.billing_webhook_secret is None
            ):
                errors.append(
                    "NOWPAYMENTS_IPN_SECRET or BILLING_WEBHOOK_SECRET is required "
                    "for NOWPayments billing"
                )
    if errors:
        raise RuntimeConfigurationError("Unsafe runtime configuration:\n- " + "\n- ".join(errors))
