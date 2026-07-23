from __future__ import annotations

from pathlib import Path

import httpx

from .config import Settings


def checks(settings: Settings) -> list[tuple[str, bool, str]]:
    results = []
    results.append(("OpenAI API key", bool(settings.openai_api_key), "OPENAI_API_KEY is required"))
    results.append(("Target is AI Setup Chat", "support" not in settings.target_name.lower(), "TARGET_NAME must not identify support agent"))
    results.append(("Case count", 20 <= settings.eval_default_tests_per_topic <= 30, "must be 20..30"))
    schema_ok = bool(settings.target_schema_file and Path(settings.target_schema_file).exists())
    results.append(("Real target DSL schema", schema_ok, "set TARGET_SCHEMA_FILE to exported validated strategy schema"))
    results.append(("Canonical field map", bool(settings.target_field_map), "set TARGET_FIELD_MAP_JSON for deterministic semantic checks"))
    if settings.target_mode in {"backend", "both"} and settings.target_backend_health_url:
        try:
            r = httpx.get(settings.target_backend_health_url, timeout=5)
            results.append(("Backend health", r.is_success, f"HTTP {r.status_code}"))
        except Exception as exc:
            results.append(("Backend health", False, f"{type(exc).__name__}: {exc}"))
    if settings.test_ai_input_usd_per_1m <= 0 or settings.test_ai_output_usd_per_1m <= 0:
        results.append(("Current model prices", False, "set prices to measure real cost; evaluator will not guess"))
    else:
        results.append(("Current model prices", True, "configured"))
    results.append(("Drift variants", len(settings.target_variants) >= 2, "configure at least two TARGET_VARIANTS_JSON entries for model-version drift" if len(settings.target_variants) < 2 else "configured"))
    target_prices = settings.target_input_usd_per_1m > 0 and settings.target_output_usd_per_1m > 0
    results.append(("Target chatbot prices", target_prices, "set target token prices to measure real chatbot cost" if not target_prices else "configured"))
    return results
