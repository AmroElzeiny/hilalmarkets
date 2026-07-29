param(
    [int]$Port = 8124,
    [double]$BudgetUsd = 0.25,
    [string]$RunId = "launch-v2-isolated-live-smoke",
    [string]$Topic = "operator_mapping",
    [ValidateSet("backend", "ui", "both")]
    [string]$Target = "both"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$baseUrl = "http://127.0.0.1:$Port"
$testResults = Join-Path $root "test-results"
$databasePath = Join-Path $testResults "evaluator-live-smoke.sqlite"
$serverLog = Join-Path $testResults "evaluator-live-smoke-server.log"
$serverErrorLog = "$serverLog.err"
$server = $null
$exitCode = 1

function Get-ConfiguredValue([string]$Name) {
    $environmentValue = [Environment]::GetEnvironmentVariable($Name)
    if ($environmentValue) {
        return $environmentValue
    }
    $line = Get-Content (Join-Path $root ".env") |
        Where-Object { $_ -like "$Name=*" } |
        Select-Object -First 1
    if (-not $line) {
        return ""
    }
    return ($line -split "=", 2)[1]
}

New-Item -ItemType Directory -Path $testResults -Force | Out-Null
Remove-Item -LiteralPath $databasePath -Force -ErrorAction SilentlyContinue

$evaluatorEmail = Get-ConfiguredValue "TARGET_BACKEND_EMAIL"
$evaluatorPassword = Get-ConfiguredValue "TARGET_BACKEND_PASSWORD"
if (-not $evaluatorEmail -or -not $evaluatorPassword) {
    throw "TARGET_BACKEND_EMAIL and TARGET_BACKEND_PASSWORD are required."
}

$env:APP_ENV = "test"
$env:APP_SECRET_KEY = "evaluator-live-smoke-secret-key-32-characters"
$env:DATABASE_URL = "sqlite+aiosqlite:///$($databasePath.Replace('\', '/'))"
$env:PUBLIC_BASE_URL = $baseUrl
$env:ALLOW_MOCK_PROVIDERS = "true"
$env:SCANNING_ENABLED = "false"
$env:TRACEDGE_MARKET_DATA_MODE = "fixture"
$env:TRACEDGE_FIXTURE_MARKET_DATA_ENABLED = "true"
$env:AI_INTERPRETER_PROVIDER = "openai"
$env:AI_AGENT_CONTROL_ENABLED = "false"
$env:CAPABILITY_EXTENSION_ENABLED = "false"
$env:SETUP_CHAT_LAUNCH_V2_ENABLED = "true"
$env:SETUP_CHAT_LEGACY_COMPATIBILITY_ENABLED = "false"
$env:AI_SETUP_EVALUATOR_ENABLED = "false"
$env:AI_SETUP_EVALUATOR_FAULTS_ENABLED = "false"
$env:PUBLIC_CHAT_ENABLED = "false"
$env:PUBLIC_CHAT_AI_ENABLED = "false"
$env:PUBLIC_FORMS_ENABLED = "false"
$env:TELEGRAM_ENABLED = "false"
$env:WHATSAPP_ENABLED = "false"
$env:BILLING_ENABLED = "false"
$env:EMAIL_ADAPTER = "memory"
$env:VITE_ANALYTICS_ENABLED = "false"
$env:LOG_LEVEL = "WARNING"
$env:PYTHONPATH = Join-Path $root "src"
$env:TARGET_BACKEND_BASE_URL = $baseUrl
$env:TARGET_UI_URL = "$baseUrl/dashboard/strategies/new"
$env:TARGET_BACKEND_EMAIL = $evaluatorEmail
$env:TARGET_BACKEND_PASSWORD = $evaluatorPassword
$env:TARGET_UI_EMAIL = $evaluatorEmail
$env:TARGET_UI_PASSWORD = $evaluatorPassword
$env:EVAL_MAX_CONCURRENCY = "1"

$seed = @'
import asyncio
import os
from datetime import UTC, datetime, timedelta

from ai_market_monitor.core.database import SessionFactory, engine
from ai_market_monitor.core.security import hash_password
from ai_market_monitor.db.models import Subscription, User, UserIdentity
from ai_market_monitor.db.models.enums import (
    IdentityProvider,
    SubscriptionStatus,
    UserRole,
    UserStatus,
)
from ai_market_monitor.services.entitlements import PlanCatalogService


async def main() -> None:
    now = datetime.now(UTC)
    email = os.environ["TARGET_BACKEND_EMAIL"].strip().lower()
    password = os.environ["TARGET_BACKEND_PASSWORD"]
    async with SessionFactory() as session:
        user = User(
            status=UserStatus.ACTIVE,
            role=UserRole.USER,
            display_name="Evaluator",
            timezone="UTC",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject=email,
                normalized_identifier=email,
                display_identifier=email,
                password_hash=hash_password(password),
                is_verified=True,
                is_primary=True,
                verified_at=now,
                profile_data={},
                created_at=now,
                updated_at=now,
            )
        )
        plan = await PlanCatalogService(session).get_or_sync("trader")
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider="evaluator",
                provider_subscription_id=f"live-smoke-{user.id}",
                current_period_start=now,
                current_period_end=now + timedelta(days=1),
                cancel_at_period_end=True,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    await engine.dispose()


asyncio.run(main())
'@

try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic migration failed."
    }
    $seed | & $python -
    if ($LASTEXITCODE -ne 0) {
        throw "Evaluator user seeding failed."
    }

    $server = Start-Process `
        -FilePath $python `
        -ArgumentList @(
            "-m", "uvicorn", "ai_market_monitor.main:app",
            "--host", "127.0.0.1", "--port", "$Port"
        ) `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $serverLog `
        -RedirectStandardError $serverErrorLog `
        -PassThru

    $healthy = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $response = Invoke-WebRequest `
                -UseBasicParsing `
                "$baseUrl/health" `
                -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $healthy) {
        throw "Isolated evaluator server did not become healthy."
    }

    & $python -m hm_chatbot_eval run `
        --mode smoke `
        --target $Target `
        --tests-per-topic 1 `
        --topics $Topic `
        --judge-mode deferred `
        --budget-usd $BudgetUsd `
        --selection-seed 20260729 `
        --run-id $RunId
    $exitCode = $LASTEXITCODE
} finally {
    if ($null -ne $server -and -not $server.HasExited) {
        & taskkill /PID $server.Id /T /F | Out-Null
    }
}

exit $exitCode
