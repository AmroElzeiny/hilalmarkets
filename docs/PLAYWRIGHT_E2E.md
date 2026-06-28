# Playwright Browser E2E

TraceEdge uses Python Playwright for dashboard browser tests. The repo has FastAPI,
Jinja templates, and static JavaScript, so no Node frontend package is required.

## Install

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m playwright install chromium
```

## Run Locally

The browser fixture auto-starts the dashboard with:

```powershell
.venv\Scripts\python.exe -m uvicorn ai_market_monitor.main:app --host 127.0.0.1 --port <free-port>
```

It runs Alembic against a disposable SQLite database under `test-results/browser`.

```powershell
.venv\Scripts\python.exe -m pytest tests\browser --junitxml=reports\playwright\playwright-results.xml
```

Headed debugging:

```powershell
.venv\Scripts\python.exe -m pytest tests\browser --browser-headed
```

Run against an already-started local server:

```powershell
$env:BROWSER_E2E_BASE_URL="http://127.0.0.1:8000"
.venv\Scripts\python.exe -m pytest tests\browser --junitxml=reports\playwright\playwright-results.xml
```

Production/staging guard:

```powershell
$env:ALLOW_PROD_E2E="true"
```

Only set that when intentionally testing a non-local URL.

## Reports

The test harness writes:

- `reports/playwright/playwright-results.xml`
- `reports/playwright/playwright-summary.json`
- `PLAYWRIGHT_E2E_REPORT.md`
- `playwright-report/index.html`
- failure artifacts under `test-results/browser`

Traces and screenshots are captured on failure. Video is recorded per test and kept
only when a failure/runtime error occurs.

## Current Coverage

- Signup/login and dashboard navigation.
- Strategy Builder prompt interpretation and coverage preview.
- Strategy Board opening.
- Condition drawer edit/save/reload metadata preservation.
- Provider-required blocking for open-interest prompts.
- Validate and publish executable monitor.
- Quick Scan/Finder UI path with browser-layer mocked interpretation and scan response.
- Seeded deterministic proof receipt through authenticated Cockpit proof API.
- Strategy Cockpit and integration cards smoke tests.

Live Telegram/Discord delivery and live exchange scans are intentionally not performed
by this browser suite unless separate test credentials/providers are configured.
