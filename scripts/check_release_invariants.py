from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ai_market_monitor.api.route_security import (
    audit_versioned_api_routes,
    iter_versioned_api_routes,
)
from ai_market_monitor.core.copy_rules import customer_copy_sources, scan_customer_copy
from ai_market_monitor.core.launch_stage import LaunchStage, resolve_launch_stage
from ai_market_monitor.core.plans import (
    PUBLIC_PLAN_CODES,
    PURCHASABLE_PLAN_CODES,
    plan_offer,
    visible_public_plan_codes,
)
from ai_market_monitor.core.product_boundaries import BOUNDARY_REGISTRY, refuse
from ai_market_monitor.main import app
from ai_market_monitor.observability.alerts import (
    ALERT_RULES,
    AlertRuleError,
    validate_alert_rules,
)
from ai_market_monitor.observability.slos import undeclared_metric_names

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PUBLIC_PLANS = ("demo", "trader", "pro")
EXPECTED_PURCHASABLE_PLANS = ("trader", "pro")
FORBIDDEN_TRACKED_PATTERNS = (
    re.compile(r"(^|/)\.venv/"),
    re.compile(r"(^|/)(reports|test-results|playwright-report|exports)/"),
    re.compile(r"(^|/)(?!VvvebJs/).*\.(db|sqlite|sqlite3|log)$", re.IGNORECASE),
    re.compile(r"^PLAYWRIGHT_E2E_REPORT\.md$"),
)
# The forbidden-phrase list, the copy sources and the spelling rule now live in
# `core/copy_rules.py`, and both readers import them: this gate and
# `tests/unit/test_launch_stage_and_boundaries.py`. They used to keep separate lists,
# which is the duplicate-vocabulary failure this repository keeps repeating — two
# guards, each understanding a different subset, each passing while the other would
# have failed.
ACTIVE_DISCORD_SCAN_ROOTS = (
    ROOT / "src" / "ai_market_monitor" / "api",
    ROOT / "src" / "ai_market_monitor" / "templates",
    ROOT / "src" / "ai_market_monitor" / "static",
    ROOT / "src" / "ai_market_monitor" / "telegram",
    ROOT / "src" / "ai_market_monitor" / "worker.py",
    ROOT / "src" / "ai_market_monitor" / "main.py",
)
ACTIVE_DISCORD_SUFFIXES = {".py", ".html", ".js", ".css"}


#: Keys that must exist in BOTH environment examples.
#:
#: A key added to one file only is a key an operator discovers is missing during a
#: deployment, which is the worst moment to learn what it does.
REQUIRED_KEY_PARITY = (
    "LAUNCH_STAGE",
    "PUBLIC_WAITLIST_MODE",
    "OBSERVABILITY_WINDOW_SECONDS",
    "OBSERVABILITY_FLUSH_INTERVAL_SECONDS",
    "OBSERVABILITY_ROLLUP_AFTER_HOURS",
    "OBSERVABILITY_RETENTION_HOURS",
    "OPERATIONAL_ALERT_TELEGRAM_CHAT_ID",
    "OPERATIONAL_ALERT_EMAIL",
    "OPERATIONAL_ALERT_REPEAT_MINUTES",
    "OPERATIONAL_ALERT_MAX_ATTEMPTS",
)


def _example_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _production_example() -> dict[str, str]:
    return _example_values(ROOT / ".env.production.example")


def main() -> int:
    failures: list[str] = []
    if tuple(PUBLIC_PLAN_CODES) != EXPECTED_PUBLIC_PLANS:
        failures.append(f"Public plan allowlist changed: {PUBLIC_PLAN_CODES!r}")
    if tuple(PURCHASABLE_PLAN_CODES) != EXPECTED_PURCHASABLE_PLANS:
        failures.append(f"Purchasable plan allowlist changed: {PURCHASABLE_PLAN_CODES!r}")
    # Prices stay on the page whether or not checkout is switched on. What changes is the
    # button: `core/plans.plan_offer` marks a plan or an interval as not yet available and
    # the card says so, instead of the plan vanishing from the comparison.
    if visible_public_plan_codes(billing_enabled=False) != PUBLIC_PLAN_CODES:
        failures.append("Every public plan must stay visible with billing disabled")
    if plan_offer("pro").monthly_available:
        failures.append("The Pro plan is not on sale yet and must not offer checkout")
    if any(plan_offer(code).annual_available for code in PUBLIC_PLAN_CODES):
        failures.append("Annual billing is not open yet and must not offer checkout")

    routes = iter_versioned_api_routes(app)
    if not routes:
        failures.append("Route-security audit discovered no /api/v1 routes")
    route_failures = audit_versioned_api_routes(app)
    failures.extend(f"Unprotected API route: {item}" for item in route_failures)

    production = _production_example()
    expected_values = {
        "ALLOW_MOCK_PROVIDERS": "false",
        # `SHARIA_TEST_MARKET_ENABLED` was deliberately removed from `Settings`, and
        # `test_test_sharia_market_switch_is_not_a_runtime_setting` asserts it stays
        # removed. Demanding it here made this gate fail forever and asked an operator to
        # add a setting the application would ignore. Two guards disagreed; the test is
        # the one that matches the code.
        "SHARIA_PILOT_SYMBOLS": "btc,eth,sol",
        "TRACEDGE_FIXTURE_MARKET_DATA_ENABLED": "false",
        "TRACEDGE_MARKET_DATA_MODE": "ccxt",
        "MARKET_DATA_EXCHANGE": "binance",
        "BILLING_ENABLED": "false",
        "BILLING_PROVIDER": "static",
        "WHATSAPP_ENABLED": "false",
        "WHATSAPP_OPPORTUNITY_ALERTS_ENABLED": "false",
        # The old general agent coordinator stays out of production. Authenticated Setup
        # Chat is served by the Setup Agent, which has its own bounds
        # (`SETUP_AGENT_*`), and the coordinator has no authority over it. This gate used
        # to demand the coordinator be on at 100%, which contradicted both the shipped
        # `.env.production.example` and the config comments, and asked an operator to
        # switch on a path nothing routes to.
        "AI_AGENT_CONTROL_ENABLED": "false",
        "AI_AGENT_SHADOW_MODE": "false",
        "AI_AGENT_ROLLOUT_PERCENT": "0",
        "CAPABILITY_EXTENSION_ENABLED": "true",
        # The public site ships pre-launch: waitlist, no plans, no account entry. Turning
        # this off is a deliberate launch decision, not a deployment detail, so the
        # production example has to state it.
        "PUBLIC_WAITLIST_MODE": "true",
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

    copy_sources = customer_copy_sources(ROOT)
    if len(copy_sources) < 4:
        # A lint over an empty file list passes for the wrong reason. If a rename ever
        # moves these files, this gate must fail loudly rather than quietly stop
        # checking anything.
        failures.append(
            f"Customer copy sources not found: only {len(copy_sources)} located"
        )
    for violation in scan_customer_copy(ROOT):
        failures.append(f"Customer copy: {violation.describe(ROOT)}")

    # -- Launch stage coherence -------------------------------------------
    # The production example must ship a stage that exists, and one no wider than the
    # emergency ceiling beside it. A stage wider than the ceiling boots clamped, which
    # means the file says one thing and the product does another.
    configured_stage = production.get("LAUNCH_STAGE", "").strip()
    if configured_stage not in {stage.value for stage in LaunchStage}:
        failures.append(
            f"Production example LAUNCH_STAGE is missing or unknown: {configured_stage!r}"
        )
    else:
        stage = LaunchStage(configured_stage)
        ceiling_on = production.get("PUBLIC_WAITLIST_MODE", "").casefold() == "true"
        resolved = resolve_launch_stage(stage, waitlist_ceiling=ceiling_on)
        if resolved.clamped_by_environment:
            failures.append(
                f"Production example sets LAUNCH_STAGE={stage.value} above its own "
                "PUBLIC_WAITLIST_MODE ceiling"
            )
        exposure = resolved.exposure
        # Pricing exposure is the gate that matters here, not billing. A launched site
        # with billing off is a supported state: the prices show and the button says
        # the plan is not on sale yet.
        if exposure.advertises_pricing:
            failures.append(
                f"LAUNCH_STAGE={resolved.effective.value} advertises pricing; the "
                "product is not open yet"
            )

    # -- Every new key exists in both examples ----------------------------
    development = _example_values(ROOT / ".env.example")
    for key in REQUIRED_KEY_PARITY:
        if key not in development:
            failures.append(f".env.example is missing {key}")
        if key not in production:
            failures.append(f".env.production.example is missing {key}")

    # -- The operational-truth layer is coherent ---------------------------
    missing_metrics = undeclared_metric_names()
    if missing_metrics:
        failures.append(
            "Service-level objectives reference metrics nothing emits: "
            + ", ".join(missing_metrics)
        )
    try:
        validate_alert_rules()
    except AlertRuleError as exc:
        failures.append(str(exc))

    # -- Every page-worthy alert has somewhere to go -----------------------
    #
    # Not "is a chat id configured" — that is a deployment secret and cannot live in
    # a committed file. What is checked is that the rules themselves still name two
    # independent paths, so a rule cannot be added later that pages into nothing.
    for rule in ALERT_RULES:
        if not rule.delivered:
            continue
        if rule.primary_route is None or rule.fallback_route is None:
            failures.append(
                f"Alert {rule.name} is page-worthy but does not name both a primary "
                "and a fallback route"
            )

    # -- Measurements are bounded -----------------------------------------
    #
    # The stored measurements have exactly one thing stopping them growing for ever.
    # If the scheduled task that folds and deletes them is ever dropped from the beat
    # schedule, nothing fails until the table is too large to read.
    worker_source = (ROOT / "src" / "ai_market_monitor" / "worker.py").read_text(
        encoding="utf-8"
    )
    for task_name in (
        "ai_market_monitor.compact_operational_metrics",
        "ai_market_monitor.flush_operational_metrics",
        "ai_market_monitor.deliver_operational_alerts",
        "ai_market_monitor.retry_operational_alert_deliveries",
    ):
        if worker_source.count(task_name) < 2:
            failures.append(
                f"{task_name} is not both defined and scheduled in worker.py"
            )

    # -- The boundary registry is present and complete ---------------------
    if not BOUNDARY_REGISTRY:
        failures.append("The product boundary registry is empty")
    for entry in BOUNDARY_REGISTRY:
        if not entry.reason.strip():
            failures.append(f"Boundary {entry.key} has no customer-readable reason")
    for key in ("trade_execution", "brokerage_custody", "buy_sell_recommendations",
                "financial_advice"):
        try:
            if not refuse(key).is_permanent:
                failures.append(f"Boundary {key} must be permanently out of scope")
        except (KeyError, ValueError):
            failures.append(f"Boundary registry is missing the {key} statement")

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
