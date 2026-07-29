from __future__ import annotations

from pathlib import Path

import httpx

from .config import Settings, process_openai_key_overrides_dotenv


def _fault_control_check(health: httpx.Response) -> tuple[str, bool, str]:
    """Would the target accept an evaluator fault header?

    Any planned topic that injects a fault makes the readiness probe send one. The
    target refuses it unless it runs ``APP_ENV=test`` with both evaluator settings
    on, and the run then stops at `EVALUATOR_FAULT_CONTROL_UNAVAILABLE` with zero
    cases completed. `/health` already reports the target's environment, so the whole
    thing is knowable before a paid run starts instead of after it fails.
    """
    label = "Backend accepts evaluator fault control"
    try:
        environment = str((health.json() or {}).get("environment") or "")
    except ValueError:
        return (label, False, "/health did not return JSON, so APP_ENV is unknown")
    if not environment:
        return (label, False, "/health did not report an environment")
    if environment != "test":
        return (
            label,
            False,
            f"the target runs APP_ENV={environment}; fault-injection topics need a "
            "target started with APP_ENV=test, AI_SETUP_EVALUATOR_ENABLED=true and "
            "AI_SETUP_EVALUATOR_FAULTS_ENABLED=true, or plan only topics without faults",
        )
    return (label, True, "target runs APP_ENV=test")


def checks(settings: Settings) -> list[tuple[str, bool, str]]:
    results = []
    api_key_configured = bool(settings.openai_api_key)
    results.append(
        (
            "OpenAI API key configured",
            api_key_configured,
            "configured" if api_key_configured else "OPENAI_API_KEY is required for live runs",
        )
    )
    if api_key_configured:
        try:
            response = httpx.get(
                f"{settings.test_ai_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                timeout=5,
            )
            results.append(
                (
                    "OpenAI API authentication",
                    response.is_success,
                    (
                        "authenticated"
                        if response.is_success
                        else (
                            f"HTTP {response.status_code}; process OPENAI_API_KEY overrides "
                            "a different .env value"
                            if process_openai_key_overrides_dotenv()
                            else f"HTTP {response.status_code}"
                        )
                    ),
                )
            )
        except Exception as exc:
            results.append(
                (
                    "OpenAI API authentication",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            )
    results.append(
        (
            "Target is AI Setup Chat",
            "support" not in settings.target_name.lower(),
            "configured"
            if "support" not in settings.target_name.lower()
            else "TARGET_NAME must not identify the public Support agent",
        )
    )
    results.append(
        (
            "Target authentication configured",
            settings.target_authentication_configured,
            (
                "configured"
                if settings.target_authentication_configured
                else (
                    "set TARGET_BACKEND_EMAIL/PASSWORD for a dedicated test user "
                    "or provide TARGET_SESSION_COOKIE"
                )
            ),
        )
    )
    results.append(
        (
            "Case count",
            20 <= settings.eval_default_tests_per_topic <= 30,
            "configured"
            if 20 <= settings.eval_default_tests_per_topic <= 30
            else "must be 20..30",
        )
    )
    strategy_schema_ok = bool(
        settings.target_strategy_schema_file
        and Path(settings.target_strategy_schema_file).is_file()
    )
    results.append(
        (
            "Validated strategy DSL schema",
            strategy_schema_ok,
            "configured"
            if strategy_schema_ok
            else "set TARGET_STRATEGY_SCHEMA_FILE to the exported StrategyDefinition schema",
        )
    )
    contract_schema_ok = bool(
        settings.target_schema_file and Path(settings.target_schema_file).is_file()
    )
    results.append(
        (
            "Evaluator response schema",
            contract_schema_ok,
            "configured"
            if contract_schema_ok
            else "set TARGET_SCHEMA_FILE to the exported Setup Chat evaluation contract",
        )
    )
    field_map_configured = bool(settings.target_field_map)
    results.append(
        (
            "Canonical field map",
            field_map_configured,
            "configured"
            if field_map_configured
            else "set TARGET_FIELD_MAP_FILE or TARGET_FIELD_MAP_JSON",
        )
    )
    if settings.target_mode in {"backend", "both"} and settings.target_backend_health_url:
        try:
            r = httpx.get(settings.target_backend_health_url, timeout=5)
            results.append(("Backend health", r.is_success, f"HTTP {r.status_code}"))
            results.append(_fault_control_check(r))
        except Exception as exc:
            results.append(("Backend health", False, f"{type(exc).__name__}: {exc}"))
            results.append(
                (
                    "Backend accepts evaluator fault control",
                    False,
                    "the target did not answer /health, so its APP_ENV is unknown",
                )
            )
    try:
        _ = settings.test_ai_pricing
        test_prices = True
    except ValueError:
        test_prices = False
    if not test_prices:
        results.append(
            (
                "Current model prices",
                False,
                "configure prices for the selected evaluator model and service tier",
            )
        )
    else:
        results.append(("Current model prices", True, "configured"))
    results.append(
        (
            "Budget profile cap",
            0 < settings.eval_budget_profile_max_usd <= 3,
            f"${settings.eval_budget_profile_max_usd:.2f} all-in cap"
            if 0 < settings.eval_budget_profile_max_usd <= 3
            else "EVAL_BUDGET_PROFILE_MAX_USD must be greater than zero and no more than $3",
        )
    )
    results.append(
        (
            "Drift variants",
            True,
            (
                "single current variant; drift remains NOT_MEASURED until two evaluator-only "
                "TARGET_VARIANTS_JSON entries are configured"
                if len(settings.target_variants) < 2
                else "configured"
            ),
        )
    )
    try:
        settings.target_pricing()
        target_prices = True
    except ValueError:
        target_prices = False
    results.append(
        (
            "Target chatbot prices",
            target_prices,
            "set target token prices to measure real chatbot cost"
            if not target_prices
            else "configured",
        )
    )
    return results
