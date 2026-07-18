from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ai_market_monitor.api.route_security import (
    audit_versioned_api_routes,
    iter_versioned_api_routes,
)
from ai_market_monitor.core.plans import (
    PUBLIC_PLAN_CODES,
    PURCHASABLE_PLAN_CODES,
    visible_public_plan_codes,
)
from ai_market_monitor.main import app

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PUBLIC_PLANS = ("demo", "trader", "pro")
EXPECTED_PURCHASABLE_PLANS = ("trader", "pro")
FORBIDDEN_TRACKED_PATTERNS = (
    re.compile(r"(^|/)\.venv/"),
    re.compile(r"(^|/)(reports|test-results|playwright-report|exports)/"),
    re.compile(r"(^|/)(?!VvvebJs/).*\.(db|sqlite|sqlite3|log)$", re.IGNORECASE),
    re.compile(r"^PLAYWRIGHT_E2E_REPORT\.md$"),
)
CUSTOMER_LANGUAGE_FILES = (
    ROOT / "src" / "ai_market_monitor" / "templates" / "dashboard_public.html",
    ROOT / "src" / "ai_market_monitor" / "templates" / "hilal",
    ROOT / "src" / "ai_market_monitor" / "core" / "site_content.py",
    ROOT / "src" / "ai_market_monitor" / "services" / "product_language.py",
)
FORBIDDEN_CUSTOMER_PHRASES = (
    "watchlist builder",
    "guided watchlist",
    "create a watchlist",
    "create your first watchlist",
    "watchlists follow approved",
)
ACTIVE_DISCORD_SCAN_ROOTS = (
    ROOT / "src" / "ai_market_monitor" / "api",
    ROOT / "src" / "ai_market_monitor" / "templates",
    ROOT / "src" / "ai_market_monitor" / "static",
    ROOT / "src" / "ai_market_monitor" / "telegram",
    ROOT / "src" / "ai_market_monitor" / "worker.py",
    ROOT / "src" / "ai_market_monitor" / "main.py",
)
ACTIVE_DISCORD_SUFFIXES = {".py", ".html", ".js", ".css"}


def _production_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in (ROOT / ".env.production.example").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def main() -> int:
    failures: list[str] = []
    if tuple(PUBLIC_PLAN_CODES) != EXPECTED_PUBLIC_PLANS:
        failures.append(f"Public plan allowlist changed: {PUBLIC_PLAN_CODES!r}")
    if tuple(PURCHASABLE_PLAN_CODES) != EXPECTED_PURCHASABLE_PLANS:
        failures.append(f"Purchasable plan allowlist changed: {PURCHASABLE_PLAN_CODES!r}")
    if visible_public_plan_codes(billing_enabled=False) != ("demo",):
        failures.append("Disabled billing must expose only the free private-beta plan")

    routes = iter_versioned_api_routes(app)
    if not routes:
        failures.append("Route-security audit discovered no /api/v1 routes")
    route_failures = audit_versioned_api_routes(app)
    failures.extend(f"Unprotected API route: {item}" for item in route_failures)

    production = _production_example()
    expected_values = {
        "ALLOW_MOCK_PROVIDERS": "false",
        "SHARIA_TEST_MARKET_ENABLED": "false",
        "SHARIA_PILOT_SYMBOLS": "btc,eth,sol",
        "TRACEDGE_FIXTURE_MARKET_DATA_ENABLED": "false",
        "TRACEDGE_MARKET_DATA_MODE": "ccxt",
        "MARKET_DATA_EXCHANGE": "binance",
        "BILLING_ENABLED": "false",
        "BILLING_PROVIDER": "static",
        "WHATSAPP_ENABLED": "false",
        "WHATSAPP_OPPORTUNITY_ALERTS_ENABLED": "false",
        "AI_AGENT_CONTROL_ENABLED": "true",
        "AI_AGENT_SHADOW_MODE": "false",
        "AI_AGENT_ROLLOUT_PERCENT": "100",
        "CAPABILITY_EXTENSION_ENABLED": "true",
        "PUBLIC_CHAT_ENABLED": "true",
        "PUBLIC_CHAT_AI_ENABLED": "true",
        "PUBLIC_CHAT_INQUIRY_EMAIL": "office@hilalmarkets.com",
        "EMAIL_ADAPTER": "smtp",
        "API_RATE_LIMITING_ENABLED": "true",
        "API_RATE_LIMIT_FAIL_CLOSED": "true",
    }
    for key, expected in expected_values.items():
        if production.get(key, "").casefold() != expected:
            failures.append(f"Production example requires {key}={expected}")
    discord_keys = sorted(key for key in production if key.startswith("DISCORD_"))
    if discord_keys:
        failures.append(
            "Retired Discord settings remain in production example: "
            + ", ".join(discord_keys)
        )

    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for path in tracked:
        normalized = path.replace("\\", "/")
        if any(pattern.search(normalized) for pattern in FORBIDDEN_TRACKED_PATTERNS):
            failures.append(f"Generated/runtime artifact is tracked: {path}")

    for source in CUSTOMER_LANGUAGE_FILES:
        candidates = source.rglob("*") if source.is_dir() else (source,)
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix not in {".html", ".py"}:
                continue
            content = candidate.read_text(encoding="utf-8").casefold()
            for phrase in FORBIDDEN_CUSTOMER_PHRASES:
                if phrase in content:
                    relative = candidate.relative_to(ROOT)
                    failures.append(
                        f"Deprecated Watch Plan terminology in {relative}: {phrase!r}"
                    )

    for source in ACTIVE_DISCORD_SCAN_ROOTS:
        candidates = source.rglob("*") if source.is_dir() else (source,)
        for candidate in candidates:
            if (
                not candidate.is_file()
                or candidate.suffix.casefold() not in ACTIVE_DISCORD_SUFFIXES
            ):
                continue
            if "discord" in candidate.read_text(encoding="utf-8").casefold():
                failures.append(
                    "Active Discord reference remains in "
                    f"{candidate.relative_to(ROOT)}"
                )

    if failures:
        print("Release invariants failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: release exposure, route security, provider, and artifact invariants hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
