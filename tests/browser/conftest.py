from __future__ import annotations

import asyncio
import html
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from playwright.sync_api import Page, sync_playwright
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TEST_PASSWORD = "TraceEdge1!"
REPORTS: dict[str, Any] = {
    "started_at": datetime.now(UTC).isoformat(),
    "tests": {},
    "console_errors": [],
    "failed_network_calls": [],
    "page_errors": [],
    "artifacts": {},
}


@dataclass(frozen=True)
class RunningApp:
    base_url: str
    auto_started: bool
    database_url: str | None
    command: str


def _terminate_server_process(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        # A Windows virtualenv launcher starts the base Python interpreter as a
        # child. Terminating only the launcher leaves Uvicorn and SQLite alive.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--browser-base-url",
        action="store",
        default=None,
        help="Run browser E2E tests against an existing HilalMarkets server.",
    )
    parser.addoption(
        "--browser-headed",
        action="store_true",
        default=False,
        help="Run Chromium headed for browser E2E debugging.",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when != "call" or "tests/browser" not in report.nodeid.replace("\\", "/"):
        return
    REPORTS["tests"][report.nodeid] = {
        "outcome": report.outcome,
        "duration_seconds": round(report.duration, 3),
        "longrepr": str(report.longrepr) if report.failed else "",
    }


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    root = Path(str(session.config.rootpath))
    metadata = getattr(session.config, "_traceedge_e2e", {})
    reports_dir = root / "reports" / "playwright"
    html_dir = root / "playwright-report"
    reports_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)
    tests = REPORTS["tests"]
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "started_at": REPORTS["started_at"],
        "base_url": metadata.get("base_url") or os.environ.get("BROWSER_E2E_BASE_URL"),
        "browser": "chromium",
        "auto_started": metadata.get("auto_started", False),
        "command": _actual_pytest_command(),
        "app_command": metadata.get("command") or "not started by browser fixture",
        "exitstatus": exitstatus,
        "tests_run": len(tests),
        "passed": sum(1 for item in tests.values() if item["outcome"] == "passed"),
        "failed": sum(1 for item in tests.values() if item["outcome"] == "failed"),
        "skipped": sum(1 for item in tests.values() if item["outcome"] == "skipped"),
        "console_errors": REPORTS["console_errors"],
        "failed_network_calls": REPORTS["failed_network_calls"],
        "page_errors": REPORTS["page_errors"],
        "artifacts": REPORTS["artifacts"],
        "tests": tests,
        "remaining_browser_side_risks": [
            (
                "Live Telegram delivery is not exercised without "
                "dedicated staging credentials."
            ),
            (
                "Quick Scan interpretation is mocked for browser determinism; light-scan "
                "submission uses the backend fixture market-data provider."
            ),
            (
                "Long-running worker scans and production billing webhooks remain covered "
                "by non-browser tests/manual checks."
            ),
        ],
    }
    (reports_dir / "playwright-summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    (root / "PLAYWRIGHT_E2E_REPORT.md").write_text(
        _markdown_report(summary),
        encoding="utf-8",
    )
    (html_dir / "index.html").write_text(_html_report(summary), encoding="utf-8")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def browser_app(pytestconfig: pytest.Config, repo_root: Path) -> RunningApp:
    configured_url = (
        pytestconfig.getoption("--browser-base-url")
        or os.environ.get("BROWSER_E2E_BASE_URL")
        or ""
    ).strip()
    if configured_url:
        _guard_base_url(configured_url)
        _wait_for_health(configured_url, timeout_seconds=45)
        app = RunningApp(
            base_url=configured_url.rstrip("/"),
            auto_started=False,
            database_url=os.environ.get("DATABASE_URL"),
            command=f"existing server at {configured_url.rstrip('/')}",
        )
        pytestconfig._traceedge_e2e = app.__dict__
        yield app
        return

    artifacts_dir = repo_root / "test-results" / "browser"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    database_path = artifacts_dir / "browser-e2e.sqlite"
    if database_path.exists():
        database_path.unlink()
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    database_url = f"sqlite+aiosqlite:///{database_path.resolve().as_posix()}"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "APP_SECRET_KEY": "browser-e2e-secret-key-32-characters-minimum",
            "DATABASE_URL": database_url,
            "PUBLIC_BASE_URL": base_url,
            "ALLOW_MOCK_PROVIDERS": "true",
            "SCANNING_ENABLED": "false",
            "TRACEDGE_MARKET_DATA_MODE": "fixture",
            "TRACEDGE_FIXTURE_MARKET_DATA_ENABLED": "true",
            "MARKET_DATA_PROVIDER": "ccxt",
            "AI_INTERPRETER_PROVIDER": "rules",
            "OPENAI_EXPLANATION_ENABLED": "false",
            "AI_AGENT_CONTROL_ENABLED": "false",
            "CAPABILITY_EXTENSION_ENABLED": "false",
            "PUBLIC_CHAT_AI_ENABLED": "false",
            "VITE_ANALYTICS_ENABLED": "true",
            "VITE_GTM_ID": "GTM-KBBHH2FV",
            "VITE_GA4_MEASUREMENT_ID": "",
            "TELEGRAM_ENABLED": "false",
            "BILLING_ENABLED": "false",
            "BILLING_PROVIDER": "static",
            "EMAIL_ADAPTER": "memory",
            "AUTH_TEST_FIXED_CODE": "123456",
            "API_RATE_LIMITS": json.dumps(
                {
                    "authentication": {"limit": 200, "window_seconds": 900},
                    "ai_chat": {"limit": 100, "window_seconds": 60},
                    "market_check": {"limit": 100, "window_seconds": 60},
                    "checkout": {"limit": 50, "window_seconds": 300},
                    "portal": {"limit": 50, "window_seconds": 300},
                    "support": {"limit": 50, "window_seconds": 3600},
                    "passport_report": {"limit": 50, "window_seconds": 3600},
                    "telegram_test": {"limit": 50, "window_seconds": 300},
                    "whatsapp_test": {"limit": 50, "window_seconds": 300},
                    "public_chat": {"limit": 100, "window_seconds": 60},
                    "public_inquiry": {"limit": 50, "window_seconds": 3600},
                    "admin_mutation": {"limit": 100, "window_seconds": 60},
                },
                separators=(",", ":"),
            ),
            "LOG_LEVEL": "WARNING",
        }
    )
    env["PYTHONPATH"] = _with_pythonpath(repo_root)
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if migration.returncode != 0:
        pytest.fail(
            "Could not migrate browser E2E database.\n"
            f"STDOUT:\n{migration.stdout}\nSTDERR:\n{migration.stderr}"
        )

    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "ai_market_monitor.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    server_log_path = artifacts_dir / "browser-e2e-server.log"
    server_log = server_log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=repo_root,
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_health(base_url, timeout_seconds=75, process=process)
        app = RunningApp(
            base_url=base_url,
            auto_started=True,
            database_url=database_url,
            command=" ".join(command),
        )
        pytestconfig._traceedge_e2e = app.__dict__
        yield app
    finally:
        _terminate_server_process(process)
        server_log.close()


@pytest.fixture
def base_url(browser_app: RunningApp) -> str:
    return browser_app.base_url


@pytest.fixture
def page(
    browser_app: RunningApp,
    request: pytest.FixtureRequest,
    repo_root: Path,
):
    artifacts_root = repo_root / "test-results" / "browser" / _safe_name(request.node.nodeid)
    artifacts_root.mkdir(parents=True, exist_ok=True)
    headed = bool(request.config.getoption("--browser-headed")) or os.environ.get(
        "BROWSER_E2E_HEADED"
    ) in {"1", "true", "yes"}
    runtime_errors: dict[str, list[str]] = {"console": [], "page": [], "network": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(
            base_url=browser_app.base_url,
            viewport={"width": 1440, "height": 1000},
            record_video_dir=str(artifacts_root / "videos"),
        )
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()

        page.on(
            "console",
            lambda message: runtime_errors["console"].append(
                f"{message.type}: {message.text}"
            )
            if message.type == "error" and not _allowed_console_error(message.text)
            else None,
        )
        page.on("pageerror", lambda exc: runtime_errors["page"].append(str(exc)))
        page.on(
            "response",
            lambda response: runtime_errors["network"].append(
                f"{response.status} {response.url}"
            )
            if _critical_failed_response(browser_app.base_url, response)
            else None,
        )

        yield page

        call_report = getattr(request.node, "rep_call", None)
        call_failed = bool(call_report and call_report.failed)
        runtime_failed = any(runtime_errors.values())
        artifacts: dict[str, str] = {}
        if call_failed or runtime_failed:
            screenshot = artifacts_root / "failure.png"
            trace = artifacts_root / "trace.zip"
            try:
                page.screenshot(path=str(screenshot), full_page=True)
                artifacts["screenshot"] = _relative(repo_root, screenshot)
            except Exception:
                pass
            try:
                context.tracing.stop(path=str(trace))
                artifacts["trace"] = _relative(repo_root, trace)
            except Exception:
                pass
        else:
            context.tracing.stop()
        context.close()
        video = page.video.path() if page.video else None
        if video and Path(video).exists():
            if call_failed or runtime_failed:
                artifacts["video"] = _relative(repo_root, Path(video))
            else:
                Path(video).unlink(missing_ok=True)
        browser.close()

    REPORTS["artifacts"][request.node.nodeid] = artifacts
    for kind, messages in runtime_errors.items():
        for message in messages:
            REPORTS[
                "failed_network_calls" if kind == "network" else f"{kind}_errors"
            ].append({"test": request.node.nodeid, "message": message})
    if runtime_failed and not call_failed:
        pytest.fail(_format_runtime_errors(runtime_errors))


def assert_no_raw_traceback(page: Page) -> None:
    body = page.locator("body").inner_text(timeout=10_000)
    forbidden = [
        "Traceback (most recent call last)",
        "RuntimeConfigurationError",
        "Internal Server Error",
    ]
    found = [item for item in forbidden if item in body]
    assert not found, f"Raw server/runtime output visible in browser: {found}"


def assert_hilal_brand_palette(page: Page) -> None:
    """Fail when a visible product element escapes the approved brand palette."""

    # Sample the settled design rather than an intermediate color during a
    # purposeful 220ms hover/state transition.
    page.wait_for_timeout(300)
    approved = {
        "31,110,151",
        "32,35,41",
        "42,143,195",
        "43,46,53",
        "70,85,27",
        "80,85,94",
        "85,113,42",
        "91,98,107",
        "99,113,108",
        "108,39,31",
        "122,128,137",
        "123,164,40",
        "138,99,22",
        "141,48,41",
        "203,250,77",
        "208,214,222",
        "225,229,234",
        "226,241,249",
        "228,184,178",
        "232,251,191",
        "238,241,244",
        "241,250,223",
        "245,248,251",
        "250,251,252",
        "253,242,223",
        "255,245,243",
        "255,255,255",
    }
    unexpected = page.evaluate(
        """approvedValues => {
            const approved = new Set(approvedValues);
            const colorProperties = ['color', 'backgroundColor'];
            const borderSides = ['Top', 'Right', 'Bottom', 'Left'];
            const parse = (value) => {
                const match = String(value || '').match(
                    /^rgba?\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)(?:\\s*,\\s*([0-9.]+))?\\s*\\)$/
                );
                if (!match || (match[4] !== undefined && Number(match[4]) === 0)) {
                    return null;
                }
                return `${match[1]},${match[2]},${match[3]}`;
            };
            const found = new Map();
            for (const element of document.querySelectorAll('body *')) {
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                if (
                    rect.width === 0 || rect.height === 0 ||
                    style.display === 'none' || style.visibility === 'hidden'
                ) continue;
                const properties = [...colorProperties];
                for (const side of borderSides) {
                    if (
                        style[`border${side}Style`] !== 'none' &&
                        Number.parseFloat(style[`border${side}Width`]) > 0
                    ) properties.push(`border${side}Color`);
                }
                if (
                    style.outlineStyle !== 'none' &&
                    Number.parseFloat(style.outlineWidth) > 0
                ) properties.push('outlineColor');
                if (element instanceof SVGElement) properties.push('fill', 'stroke');
                for (const property of properties) {
                    const value = style[property];
                    const rgb = parse(value);
                    if (!rgb || approved.has(rgb)) continue;
                    const key = `${property}: ${value}`;
                    if (!found.has(key)) {
                        const identity = `${element.tagName.toLowerCase()}${
                            element.id ? `#${element.id}` : ''
                        }${[...element.classList].slice(0, 3).map(name => `.${name}`).join('')}`;
                        found.set(key, identity);
                    }
                }
            }
            return [...found].map(([value, element]) => ({value, element}));
        }""",
        sorted(approved),
    )
    assert unexpected == [], f"Visible elements use non-brand colors: {unexpected}"


def assert_no_horizontal_overflow(page: Page) -> None:
    # Responsive drawers and the sidebar use the shared 220ms motion token.
    # Measure after the layout has reached its final breakpoint state.
    page.wait_for_timeout(300)
    overflow = page.evaluate(
        """() => {
            const viewport = window.innerWidth;
            if (document.documentElement.scrollWidth <= viewport + 1) return [];
            return [...document.querySelectorAll('body *')]
                .filter(element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                        return (
                            style.display !== 'none' && style.visibility !== 'hidden' &&
                            rect.width > 0 && rect.height > 0 &&
                            rect.left < viewport && rect.right > viewport + 1
                        );
                })
                .slice(0, 12)
                .map(element => {
                    const rect = element.getBoundingClientRect();
                    return {
                        element: `${element.tagName.toLowerCase()}${
                            element.id ? `#${element.id}` : ''
                        }${[...element.classList].slice(0, 4).map(name => `.${name}`).join('')}`,
                        left: Math.round(rect.left),
                        right: Math.round(rect.right),
                        width: Math.round(rect.width),
                    };
                });
        }"""
    )
    assert overflow == [], f"Visible elements overflow the viewport: {overflow}"


def unique_email(prefix: str) -> str:
    return f"{prefix}+{uuid4().hex[:10]}@example.com"


def signup(page: Page, base_url: str, email: str | None = None) -> str:
    email = email or unique_email("browser-e2e")
    page.goto(f"{base_url}/signup", wait_until="domcontentloaded")
    page.get_by_test_id("auth-first-name").fill("Browser")
    page.get_by_test_id("auth-last-name").fill("Trader")
    page.get_by_test_id("auth-email").fill(email)
    page.get_by_test_id("auth-password").fill(TEST_PASSWORD)
    page.get_by_test_id("auth-repeat-password").fill(TEST_PASSWORD)
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url(re.compile(r".*/signup/verify(\?.*)?$"), timeout=20_000)
    page.locator("input[name='code']").fill("123456")
    page.get_by_role("button", name=re.compile("Verify and create account", re.I)).click()
    page.wait_for_url(re.compile(r".*/dashboard(\?.*)?$"), timeout=20_000)
    page.get_by_test_id("dashboard-root").wait_for(state="attached", timeout=10_000)
    assert_no_raw_traceback(page)
    return email


def seed_system_brain_reviewer(
    database_url: str,
    email: str,
) -> str:
    if not database_url:
        pytest.skip("System Brain browser coverage requires the auto-started database.")

    async def _seed() -> str:
        from ai_market_monitor.db.models import ReviewCase, User, UserIdentity
        from ai_market_monitor.db.models.enums import IdentityProvider, UserRole

        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            identity = await session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.normalized_identifier == email.lower(),
                )
            )
            if identity is None:
                raise AssertionError(f"No browser test identity found for {email}.")
            user = await session.get(User, identity.user_id)
            if user is None:
                raise AssertionError(f"No browser test user found for {email}.")
            user.role = UserRole.ADMIN
            case = ReviewCase(
                case_reference=f"REV-BROWSER-{uuid4().hex[:8]}",
                case_type="material_source_change",
                state="ready_for_review",
                publication_state="change_under_review",
                title="Review retained official-source change",
                priority="high",
                risk_severity="medium",
                human_review_reason=(
                    "A retained official-source snapshot changed after publication."
                ),
                assigned_reviewer_id=user.id,
                requested_evidence=[],
                admin_notes=[],
                idempotency_key=f"browser-system-brain-{uuid4()}",
            )
            session.add(case)
            await session.commit()
            case_id = str(case.id)
        await engine.dispose()
        return case_id

    return _run_async_in_thread(_seed)


def seed_alert_proof(database_url: str, email: str) -> str:
    if not database_url:
        pytest.skip("Seeded proof test requires the auto-started browser database URL.")

    async def _seed() -> str:
        from ai_market_monitor.db.models import Alert, User, UserIdentity
        from ai_market_monitor.db.models.enums import AlertType, IdentityProvider

        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            identity = await session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.normalized_identifier == email.lower(),
                )
            )
            if identity is None:
                raise AssertionError(f"No browser test user identity found for {email}.")
            user = await session.get(User, identity.user_id)
            if user is None:
                raise AssertionError(f"No browser test user found for {email}.")
            now = datetime.now(UTC)
            alert = Alert(
                user_id=user.id,
                alert_type=AlertType.CONFIRMED,
                deduplication_key=f"browser-e2e-proof-{uuid4()}",
                title="SOL/USDT - Bullish Setup Confirmed",
                body="Deterministic proof receipt seeded for browser E2E.",
                proof_receipt={
                    "strategy_name": "Browser E2E Proof Strategy",
                    "strategy_version": 1,
                    "schema_hash": "e2e-proof-hash",
                    "symbol": "SOL/USDT",
                    "exchange": "binance",
                    "timeframe": "15m",
                    "evaluation_time": now.isoformat(),
                    "market_data_timestamp": now.isoformat(),
                    "conditions": [
                        {
                            "condition_id": "rsi_recovery",
                            "name": "RSI crosses above 30",
                            "required_value": {"operator": "crosses_above", "threshold": 30},
                            "actual_value": {"previous": 28.2, "current": 31.4},
                            "state": "passed",
                        },
                        {
                            "condition_id": "volume_ratio",
                            "name": "Volume at least 1.5x average",
                            "required_value": {"operator": "gte", "threshold": 1.5},
                            "actual_value": {"value": 1.72},
                            "state": "passed",
                        },
                    ],
                    "entry_zone": {"low": 100.0, "high": 101.0},
                    "reward_to_risk": 2.5,
                    "setup_completion_score": 100,
                },
                candle_timestamp=now,
            )
            session.add(alert)
            await session.commit()
            alert_id = str(alert.id)
        await engine.dispose()
        return alert_id

    return _run_async_in_thread(_seed)


def seed_telegram_connection(database_url: str, email: str) -> None:
    if not database_url:
        pytest.skip("Seeded Telegram connection requires the auto-started browser database URL.")

    async def _seed() -> None:
        from ai_market_monitor.db.models import TelegramConnection, UserIdentity
        from ai_market_monitor.db.models.enums import ConnectionStatus, IdentityProvider

        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            identity = await session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.normalized_identifier == email.lower(),
                )
            )
            if identity is None:
                raise AssertionError(f"No browser test user identity found for {email}.")
            suffix = uuid4().hex[:10]
            session.add(
                TelegramConnection(
                    user_id=identity.user_id,
                    telegram_user_id=f"browser-tg-{suffix}",
                    chat_id=f"browser-chat-{suffix}",
                    username="browser_trace_user",
                    status=ConnectionStatus.ACTIVE,
                )
            )
            await session.commit()
        await engine.dispose()

    _run_async_in_thread(_seed)


def seed_sharia_screened_market(database_url: str, email: str) -> dict[str, str]:
    if not database_url:
        pytest.skip("Screened Market visual QA requires the auto-started browser database.")

    async def _seed() -> dict[str, str]:
        from datetime import timedelta
        from decimal import Decimal

        from ai_market_monitor.db.models import (
            ApprovedWatchlist,
            ApprovedWatchlistAsset,
            AssetShariaAssessment,
            AssetShariaStatusHistory,
            ComplianceChange,
            ComplianceDriftNotification,
            SetupInstance,
            ShariaEvidenceSource,
            ShariaMethodology,
            Strategy,
            StrategyVersion,
            UserIdentity,
        )
        from ai_market_monitor.db.models.enums import (
            ComplianceChangeBehavior,
            ComplianceChangeSeverity,
            ComplianceChangeStatus,
            IdentityProvider,
            SetupLifecycleState,
            ShariaAssetStatus,
            ShariaMethodologyStatus,
            StrategyStatus,
            StrategyVersionStatus,
        )
        from tests.factories import (
            load_strategy,
            methodology_evidence_requirements,
            methodology_rules,
        )

        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            identity = await session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.normalized_identifier == email.lower(),
                )
            )
            if identity is None:
                raise AssertionError(f"No browser test user identity found for {email}.")
            now = datetime.now(UTC)
            methodology = ShariaMethodology(
                code=f"BROWSER_APPROVED_{uuid4().hex[:12].upper()}",
                name="Browser QA approved methodology",
                version="1.0-test",
                description=(
                    "Evidence-backed browser test methodology with explicit test governance."
                ),
                status=ShariaMethodologyStatus.ACTIVE,
                governing_body="Qualified browser-test governance",
                reviewer_group="Qualified browser-test reviewers",
                published_at=now - timedelta(days=2),
                effective_from=now - timedelta(days=2),
                rules_json=methodology_rules(source_family="browser_approved_test"),
                evidence_requirements_json=methodology_evidence_requirements(),
            )
            session.add(methodology)
            await session.flush()
            assessment = AssetShariaAssessment(
                canonical_asset="SOL",
                asset_name="Solana",
                methodology_id=methodology.id,
                status=ShariaAssetStatus.ELIGIBLE,
                summary=(
                    "A qualified browser-test reviewer recorded this test conclusion from "
                    "the retained evidence source."
                ),
                qualifications=[],
                exclusion_reasons=[],
                evidence_snapshot={
                    "reviewed_dimensions": [
                        {"name": "Primary activity", "result": "reviewed"}
                    ],
                    "methodology_result": {"passed": ["test rule"]},
                },
                reviewed_by="Qualified browser-test reviewer",
                reviewed_at=now - timedelta(days=1),
                valid_from=now - timedelta(days=1),
            )
            session.add(assessment)
            await session.flush()
            session.add_all(
                [
                    ShariaEvidenceSource(
                        assessment_id=assessment.id,
                        source_type="official_disclosure",
                        title="Official browser-test disclosure",
                        publisher="Project documentation",
                        source_url="https://example.com/browser-screening-evidence",
                        retrieved_at=now - timedelta(days=1),
                        evidence_category="primary_activity",
                        evidence_summary=(
                            "Retained evidence used only for deterministic browser QA."
                        ),
                        source_hash=uuid4().hex + uuid4().hex,
                    ),
                    AssetShariaStatusHistory(
                        canonical_asset="SOL",
                        methodology_id=methodology.id,
                        previous_status=None,
                        new_status=ShariaAssetStatus.ELIGIBLE,
                        reason_code="browser_test_review",
                        reason_summary="Qualified browser-test evidence review completed.",
                        assessment_id=assessment.id,
                        changed_at=assessment.valid_from,
                        approved_by="Qualified browser-test reviewer",
                    ),
                ]
            )
            strategy = Strategy(
                user_id=identity.user_id,
                name="SOL Browser Watchlist",
                status=StrategyStatus.ACTIVE,
                activated_at=now,
            )
            session.add(strategy)
            await session.flush()
            definition = load_strategy().model_copy(update={"name": strategy.name})
            version = StrategyVersion(
                strategy_id=strategy.id,
                version_number=1,
                status=StrategyVersionStatus.ACTIVE,
                source_type="browser_qa",
                source_text="Watch screened SOL market checks",
                schema_json=definition.model_dump(mode="json"),
                schema_hash=definition.canonical_hash(),
                approved_schema_hash=definition.canonical_hash(),
                approved_at=now,
                activated_at=now,
            )
            session.add(version)
            await session.flush()
            strategy.active_version_id = version.id
            setup = SetupInstance(
                user_id=identity.user_id,
                strategy_version_id=version.id,
                exchange="binance",
                symbol="SOL/USDT",
                timeframe="15m",
                direction="long",
                setup_key=f"screened-browser-{uuid4()}",
                state=SetupLifecycleState.NEAR_CONFIRMATION,
                completion_score=Decimal("80"),
                first_detected_at=now - timedelta(minutes=15),
                last_evaluated_at=now,
                expires_at=now + timedelta(hours=2),
                sharia_methodology_id=methodology.id,
                sharia_methodology_version=methodology.version,
                sharia_status_at_detection=ShariaAssetStatus.ELIGIBLE.value,
                sharia_assessment_id=assessment.id,
            )
            session.add(setup)
            watchlist = ApprovedWatchlist(
                user_id=identity.user_id,
                name="Browser Screened Assets",
                is_default=True,
            )
            session.add(watchlist)
            await session.flush()
            session.add(
                ApprovedWatchlistAsset(
                    watchlist_id=watchlist.id,
                    canonical_asset="SOL",
                    added_at=now,
                )
            )
            compliance_change = ComplianceChange(
                canonical_asset="SOL",
                change_type="reviewed_status_update",
                severity=ComplianceChangeSeverity.INFORMATIONAL,
                title="SOL screening review completed",
                summary="The retained review moved SOL from under review to eligible.",
                structured_change={
                    "reviewed_status": {
                        "before": "under_review",
                        "after": "eligible",
                    },
                    "evidence_scope": {
                        "before": "pending review",
                        "after": "reviewed source snapshot",
                    },
                },
                detected_at=now,
                effective_at=now,
                status=ComplianceChangeStatus.APPROVED,
                detection_method="browser_qa",
                confidence_label="reviewed",
                idempotency_key=f"browser-change-{uuid4().hex}",
            )
            session.add(compliance_change)
            await session.flush()
            session.add(
                ComplianceDriftNotification(
                    user_id=identity.user_id,
                    compliance_change_id=compliance_change.id,
                    strategy_id=strategy.id,
                    canonical_asset="SOL",
                    previous_status=ShariaAssetStatus.UNDER_REVIEW,
                    new_status=ShariaAssetStatus.ELIGIBLE,
                    behavior=ComplianceChangeBehavior.NOTIFY_ONLY,
                    impact={
                        "methodology_version": methodology.version,
                        "monitor_impact": "notify_only",
                        "review_state": "approved",
                        "policy_reason": "Reviewed source evidence was completed.",
                    },
                    idempotency_key=f"browser-drift-{uuid4().hex}",
                    created_at=now,
                )
            )
            await session.commit()
            result = {
                "methodology_id": str(methodology.id),
                "assessment_id": str(assessment.id),
                "setup_id": str(setup.id),
            }
        await engine.dispose()
        return result

    return _run_async_in_thread(_seed)


def seed_setup_observability(database_url: str, email: str) -> dict[str, str]:
    if not database_url:
        pytest.skip("Observability visual QA requires the auto-started browser database URL.")

    async def _seed() -> dict[str, str]:
        from decimal import Decimal

        from ai_market_monitor.db.models import (
            CandidateReadinessSnapshot,
            ConditionObservabilityAggregate,
            MonitorHealthSummary,
            ScanJob,
            ScanResult,
            SetupConditionResult,
            SetupInstance,
            SetupLifecycleEvent,
            Strategy,
            StrategyCondition,
            StrategyVersion,
            UserIdentity,
        )
        from ai_market_monitor.db.models.enums import (
            ConditionOutcome,
            ConditionType,
            IdentityProvider,
            ScanJobStatus,
            ScanOutcome,
            SetupLifecycleState,
            StrategyStatus,
            StrategyVersionStatus,
        )
        from tests.factories import load_strategy

        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            identity = await session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.normalized_identifier == email.lower(),
                )
            )
            if identity is None:
                raise AssertionError(f"No browser test user identity found for {email}.")
            now = datetime.now(UTC)
            definition = load_strategy().model_copy(update={"name": "SOL Readiness Monitor"})
            strategy = Strategy(
                user_id=identity.user_id,
                name="SOL Readiness Monitor",
                status=StrategyStatus.ACTIVE,
                activated_at=now,
            )
            session.add(strategy)
            await session.flush()
            version = StrategyVersion(
                strategy_id=strategy.id,
                version_number=3,
                status=StrategyVersionStatus.ACTIVE,
                source_type="browser_qa",
                schema_json=definition.model_dump(mode="json"),
                schema_hash=definition.canonical_hash(),
                approved_schema_hash=definition.canonical_hash(),
                approved_at=now,
                activated_at=now,
            )
            session.add(version)
            await session.flush()
            strategy.active_version_id = version.id
            condition = StrategyCondition(
                strategy_version_id=version.id,
                condition_key="volume_ratio",
                label="RVOL above 1.50x",
                node_type="condition",
                condition_type=ConditionType.INDICATOR,
                timeframe="15m",
                comparator="gte",
                left_operand={"kind": "indicator", "name": "volume_ratio"},
                right_operand={"kind": "constant", "value": 1.5},
                required_value=Decimal("1.5"),
                weight=Decimal("1"),
                sequence=0,
                is_required=True,
            )
            session.add(condition)
            job = ScanJob(
                strategy_version_id=version.id,
                idempotency_key=f"browser-observability-{uuid4()}",
                status=ScanJobStatus.PARTIAL,
                scheduled_for=now,
                started_at=now,
                completed_at=now,
                symbols_planned=86,
                symbols_scanned=84,
                matches_found=0,
            )
            session.add(job)
            await session.flush()
            scan = ScanResult(
                scan_job_id=job.id,
                strategy_version_id=version.id,
                exchange="binance",
                symbol="SOL/USDT",
                timeframe="15m",
                direction="long",
                outcome=ScanOutcome.NEAR_MISS,
                completion_score=Decimal("80"),
                candle_closed_at=now,
                evaluated_at=now,
                data_freshness_ms=850,
                is_candle_complete=True,
                proof_summary={},
            )
            session.add(scan)
            await session.flush()
            setup = SetupInstance(
                user_id=identity.user_id,
                strategy_version_id=version.id,
                latest_scan_result_id=scan.id,
                exchange="binance",
                symbol="SOL/USDT",
                timeframe="15m",
                direction="long",
                setup_key=f"browser-setup-{uuid4()}",
                state=SetupLifecycleState.NEAR_CONFIRMATION,
                completion_score=Decimal("80"),
                first_detected_at=now,
                last_evaluated_at=now,
                expires_at=now.replace(hour=23, minute=59),
            )
            session.add(setup)
            await session.flush()
            session.add_all(
                [
                    SetupConditionResult(
                        setup_instance_id=setup.id,
                        scan_result_id=scan.id,
                        strategy_condition_id=condition.id,
                        condition_key=condition.condition_key,
                        outcome=ConditionOutcome.FAILED,
                        required_value={"value": 1.5},
                        actual_value={"value": 1.27},
                        distance_to_pass=Decimal("0.23"),
                        contribution_score=Decimal("84.7"),
                        candle_timestamp=now,
                        evaluated_at=now,
                        data_freshness_ms=850,
                    ),
                    SetupLifecycleEvent(
                        setup_instance_id=setup.id,
                        from_state=SetupLifecycleState.FORMING,
                        to_state=SetupLifecycleState.NEAR_CONFIRMATION,
                        reason_code="one_required_condition_remaining",
                        evidence={"scan_result_id": str(scan.id)},
                        occurred_at=now,
                    ),
                ]
            )
            states = [
                ("SOL/USDT", "confirmation_pending", 3, 4, 5, "RVOL above 1.50x", "healthy"),
                ("ETH/USDT", "forming", 1, 2, 5, "15m candle close", "healthy"),
                ("LINK/USDT", "near_miss", 2, 3, 5, "Price above EMA 200", "healthy"),
                ("AVAX/USDT", "provider_data_error", -3, 0, 5, "Candle data unavailable", "error"),
            ]
            for index, (symbol, state, rank, passed, total, blocker, health) in enumerate(states):
                candidate_scan = scan
                candidate_setup_id = setup.id if index == 0 else None
                if index:
                    candidate_scan = ScanResult(
                        scan_job_id=job.id,
                        strategy_version_id=version.id,
                        exchange="binance",
                        symbol=symbol,
                        timeframe="15m",
                        direction="long",
                        outcome=ScanOutcome.ERROR if health == "error" else ScanOutcome.FORMING,
                        completion_score=Decimal(str(passed / total * 100)),
                        candle_closed_at=now,
                        evaluated_at=now,
                        data_freshness_ms=950 if health == "healthy" else 900000,
                        is_candle_complete=True,
                        proof_summary={},
                    )
                    session.add(candidate_scan)
                    await session.flush()
                session.add(
                    CandidateReadinessSnapshot(
                        user_id=identity.user_id,
                        strategy_id=strategy.id,
                        strategy_version_id=version.id,
                        setup_instance_id=candidate_setup_id,
                        scan_result_id=candidate_scan.id,
                        exchange="binance",
                        symbol=symbol,
                        timeframe="15m",
                        direction="long",
                        lifecycle_state=state,
                        stage_rank=rank,
                        required_total=total,
                        required_passed=passed,
                        optional_total=1,
                        optional_passed=1 if passed else 0,
                        blocker_key=f"blocker_{index}",
                        blocker_label=blocker,
                        blocker_outcome="unavailable" if health == "error" else "failed",
                        blocker_actual={"value": None if health == "error" else 1.27},
                        blocker_required={"value": 1.5},
                        blocker_distance=Decimal("0.23") if health == "healthy" else None,
                        blocker_unit="absolute" if health == "healthy" else None,
                        most_recent_change=f"{blocker} became the current blocker.",
                        last_changed_at=now,
                        last_evaluated_at=now,
                        data_freshness_ms=950 if health == "healthy" else 900000,
                        data_health=health,
                        next_candle_close_at=now,
                        notification_status="not_attempted",
                        condition_tree={},
                        latest_values=[
                            {
                                "key": f"blocker_{index}",
                                "label": blocker,
                                "role": "required_confirmation",
                                "required": True,
                                "outcome": "unavailable" if health == "error" else "failed",
                                "actual": None if health == "error" else 1.27,
                                "required_value": 1.5,
                                "timeframe": "15m",
                            }
                        ],
                    )
                )
            session.add(
                MonitorHealthSummary(
                    user_id=identity.user_id,
                    strategy_id=strategy.id,
                    strategy_version_id=version.id,
                    technical_status="degraded",
                    strategy_status="too_strict",
                    technical_causes=[
                        {
                            "code": "cycle_incomplete",
                            "message": "84/86 symbols were evaluated in the latest cycle.",
                        }
                    ],
                    strategy_causes=[
                        {
                            "code": "no_confirmations",
                            "message": (
                                "No confirmations in 14 days; RVOL blocked most "
                                "near-misses."
                            ),
                        }
                    ],
                    actions=[],
                    metrics={
                        "symbols_scanned": 84,
                        "symbols_expected": 86,
                        "provider_errors": 2,
                        "alerts_24h": 0,
                    },
                    calculated_at=now,
                )
            )
            session.add(
                ConditionObservabilityAggregate(
                    user_id=identity.user_id,
                    strategy_id=strategy.id,
                    strategy_version_id=version.id,
                    strategy_condition_id=condition.id,
                    condition_key="volume_ratio",
                    condition_label="RVOL above 1.50x",
                    rule_role="required_confirmation",
                    is_required=True,
                    timeframe="15m",
                    evaluation_count=120,
                    pass_count=31,
                    fail_count=89,
                    pending_count=0,
                    unavailable_count=0,
                    error_count=0,
                    final_blocker_count=61,
                    near_miss_blocker_count=61,
                    invalidation_count=8,
                    average_actual=Decimal("1.31"),
                    median_actual_when_blocked=Decimal("1.27"),
                    average_required=Decimal("1.50"),
                    average_distance=Decimal("0.23"),
                    co_occurrence={},
                    previous_version_delta={"pass_rate_points": -4.2, "previous_version": 2},
                    counterfactual_preview={
                        "preview_only": True,
                        "current_threshold": 1.5,
                        "proposed_threshold": 1.27,
                        "additional_historical_completions": 31,
                        "sample_count": 120,
                    },
                    sample_status="sufficient",
                    window_started_at=now.replace(day=1),
                    window_ended_at=now,
                    calculated_at=now,
                )
            )
            await session.commit()
            result = {"strategy_id": str(strategy.id), "setup_id": str(setup.id)}
        await engine.dispose()
        return result

    return _run_async_in_thread(_seed)


def _run_async_in_thread(factory):
    result: list[Any] = []
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            result.append(asyncio.run(factory()))
        except BaseException as exc:  # pragma: no cover - re-raised in caller.
            errors.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]


def _guard_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if (
        parsed.hostname not in LOCAL_HOSTS
        and os.environ.get("ALLOW_PROD_E2E", "").lower() != "true"
    ):
        pytest.exit(
            f"Refusing browser E2E against non-local URL {base_url}. "
            "Set ALLOW_PROD_E2E=true to override intentionally.",
            returncode=2,
        )


def _wait_for_health(
    base_url: str,
    *,
    timeout_seconds: int,
    process: subprocess.Popen[str] | None = None,
) -> None:
    deadline = time.time() + timeout_seconds
    url = f"{base_url.rstrip('/')}/health"
    last_error = ""
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"HilalMarkets server exited before health check.\n{output}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 300:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    output = ""
    if process is not None and process.stdout and process.poll() is not None:
        try:
            output = process.stdout.read()
        except Exception:
            output = ""
    raise RuntimeError(f"Timed out waiting for {url}. Last error: {last_error}\n{output}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _with_pythonpath(repo_root: Path) -> str:
    paths = [str(repo_root / "src")]
    if os.environ.get("PYTHONPATH"):
        paths.append(os.environ["PYTHONPATH"])
    return os.pathsep.join(paths)


def _allowed_console_error(message: str) -> bool:
    allow = [
        "favicon.ico",
        "api.iconify.design",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "cdn.jsdelivr.net",
    ]
    return any(item in message for item in allow)


def _critical_failed_response(base_url: str, response: Any) -> bool:
    if response.status < 500:
        return False
    base_host = urlparse(base_url).netloc
    parsed = urlparse(response.url)
    return parsed.netloc == base_host and "/api/" in parsed.path


def _format_runtime_errors(runtime_errors: dict[str, list[str]]) -> str:
    lines = ["Browser runtime errors were detected:"]
    for kind, messages in runtime_errors.items():
        if messages:
            lines.append(f"{kind}:")
            lines.extend(f"- {message}" for message in messages)
    return "\n".join(lines)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:150]


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _actual_pytest_command() -> str:
    executable = Path(sys.executable)
    display_executable = (
        r".venv\Scripts\python.exe" if ".venv" in executable.parts else str(executable)
    )
    return " ".join([display_executable, "-m", "pytest", *sys.argv[1:]])


def _markdown_report(summary: dict[str, Any]) -> str:
    tests = summary["tests"]
    rows = "\n".join(
        f"| `{nodeid}` | {item['outcome']} | {item['duration_seconds']} |"
        for nodeid, item in sorted(tests.items())
    )
    return f"""# Playwright E2E Report

Generated: {summary['generated_at']}

Base URL tested: {summary.get('base_url') or 'not recorded'}
Browser: {summary['browser']}
App auto-started: {summary['auto_started']}
Command: `{summary['command']}`
App command: `{summary['app_command']}`

## Result

- Tests run: {summary['tests_run']}
- Passed: {summary['passed']}
- Failed: {summary['failed']}
- Skipped: {summary['skipped']}
- Screenshots/traces/videos: `test-results/browser`
- HTML report: `playwright-report/index.html`
- JUnit XML: `reports/playwright/playwright-results.xml`
- JSON summary: `reports/playwright/playwright-summary.json`

## Tests

| Test | Outcome | Seconds |
| --- | --- | ---: |
{rows or '| none | not run | 0 |'}

## Runtime Checks

- Critical console errors: {len(summary['console_errors'])}
- Critical API/network errors: {len(summary['failed_network_calls'])}
- Page errors: {len(summary['page_errors'])}

## Tested User Flow Summary

- Browser signup and session creation.
- Dashboard navigation and Strategy Builder entry.
- Prompt interpretation coverage preview for RSI, volume, and EMA.
- Strategy Board opening, condition drawer editing, draft save, reload, and metadata preservation.
- Provider-required prompt blocking for open-interest requirements.
- Publish flow for executable monitors and active monitor list visibility.
- Quick Scan/Finder prompt interpretation and backend fixture light-scan result rendering path.
- Seeded deterministic proof receipt API visibility.
- Screened Market search, Passport Quick View focus behavior, full Passport, and mobile layout.
- Server-catalog checkout review at desktop and mobile widths.
- System Brain customer isolation, responsive review workspace, and separate approve/publish flow.
- Setup observability, lifecycle, and integrations smoke screens.

## Remaining Browser-Side Risks

{chr(10).join(f'- {item}' for item in summary['remaining_browser_side_risks'])}

## Next Recommended Fixes

- Keep immutable proof and historical Passport routes in the browser regression set as alert UI
  evolves.
- Keep Quick Scan browser coverage on fixture market data; add a staging-only live-provider smoke
  test before using real exchange candles in browser E2E.
- Run controlled provider-sandbox checkout, SMTP, Telegram, and WhatsApp delivery tests in
  staging; CI remains fake/no-send.
- Run the same browser suite in CI after installing Chromium with
  `.venv\\Scripts\\python.exe -m playwright install chromium`.
"""


def _html_report(summary: dict[str, Any]) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(nodeid)}</td>"
        f"<td>{html.escape(item['outcome'])}</td>"
        f"<td>{item['duration_seconds']}</td>"
        "</tr>"
        for nodeid, item in sorted(summary["tests"].items())
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>HilalMarkets Playwright E2E Report</title>
  <style>
    body {{
      font-family: Inter, Segoe UI, sans-serif;
      margin: 2rem;
      background: #0a0a0a;
      color: #f5f5f5;
    }}
    .card {{
      border: 1px solid #3b2a5f;
      border-radius: 18px;
      padding: 1rem;
      margin-bottom: 1rem;
      background: #111114;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #2f2544; padding: .75rem; text-align: left; }}
    strong, a {{ color: #c4b5fd; }}
  </style>
</head>
<body>
  <h1>HilalMarkets Playwright E2E Report</h1>
  <section class="card">
    <p><strong>Generated:</strong> {html.escape(str(summary['generated_at']))}</p>
    <p><strong>Base URL:</strong> {html.escape(str(summary.get('base_url') or 'not recorded'))}</p>
    <p><strong>Browser:</strong> {html.escape(summary['browser'])}</p>
    <p><strong>Auto-started:</strong> {summary['auto_started']}</p>
    <p><strong>Command:</strong> <code>{html.escape(summary['command'])}</code></p>
    <p><strong>App command:</strong> <code>{html.escape(summary['app_command'])}</code></p>
  </section>
  <section class="card">
    <h2>Summary</h2>
    <p>Run {summary['tests_run']} / passed {summary['passed']} /
      failed {summary['failed']} / skipped {summary['skipped']}</p>
    <p>Console errors: {len(summary['console_errors'])};
      API/network errors: {len(summary['failed_network_calls'])};
      page errors: {len(summary['page_errors'])}</p>
  </section>
  <section class="card">
    <h2>Tests</h2>
    <table>
      <thead><tr><th>Test</th><th>Outcome</th><th>Seconds</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </section>
  <section class="card">
    <h2>Artifacts</h2>
    <p>Failure screenshots, traces, and videos are written under
      <code>test-results/browser</code>.</p>
  </section>
</body>
</html>
"""
