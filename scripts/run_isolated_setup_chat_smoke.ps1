param(
    [int]$Port = 8124,
    [double]$BudgetUsd = 0.25,
    # An empty run ID lets the evaluator allocate a timestamped directory, so a
    # new run cannot overwrite evidence from an earlier smoke or full corpus run.
    [string]$RunId = "",
    [string]$Topic = "operator_mapping",
    [switch]$AllTopics,
    [ValidateSet("smoke", "budget", "standard", "full")]
    [string]$Mode = "smoke",
    # Zero selects the conservative profile default: 1 for smoke/budget, 5 for
    # standard, and 20 for full.
    [ValidateRange(0, 30)]
    [int]$TestsPerTopic = 0,
    [ValidateSet("online", "deferred")]
    [string]$JudgeMode = "",
    [switch]$EnableFaults,
    [switch]$PreflightOnly,
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

# This wrapper runs inside the caller's PowerShell process.  Process environment
# mutations therefore outlive the script unless every value is restored.  Keep
# an exact snapshot so a completed (or failed) isolated run cannot leave the
# normal evaluator pointed at the temporary port/database after that target has
# been stopped.
$isolatedEnvironmentNames = @(
    "APP_ENV",
    "APP_SECRET_KEY",
    "DATABASE_URL",
    "PUBLIC_BASE_URL",
    "ALLOW_MOCK_PROVIDERS",
    "SCANNING_ENABLED",
    "TRACEDGE_MARKET_DATA_MODE",
    "TRACEDGE_FIXTURE_MARKET_DATA_ENABLED",
    "AI_INTERPRETER_PROVIDER",
    "AI_AGENT_CONTROL_ENABLED",
    "CAPABILITY_EXTENSION_ENABLED",
    "SETUP_CHAT_LAUNCH_V2_ENABLED",
    "SETUP_CHAT_LEGACY_COMPATIBILITY_ENABLED",
    "AI_SETUP_EVALUATOR_ENABLED",
    "AI_SETUP_EVALUATOR_FAULTS_ENABLED",
    "PUBLIC_CHAT_ENABLED",
    "PUBLIC_CHAT_AI_ENABLED",
    "PUBLIC_FORMS_ENABLED",
    "TELEGRAM_ENABLED",
    "WHATSAPP_ENABLED",
    "BILLING_ENABLED",
    "EMAIL_ADAPTER",
    "VITE_ANALYTICS_ENABLED",
    "LOG_LEVEL",
    "PYTHONPATH",
    "TARGET_BACKEND_BASE_URL",
    "TARGET_BACKEND_HEALTH_URL",
    "TARGET_UI_URL",
    "TARGET_BACKEND_EMAIL",
    "TARGET_BACKEND_PASSWORD",
    "TARGET_UI_EMAIL",
    "TARGET_UI_PASSWORD",
    "EVAL_MAX_CONCURRENCY",
    "HM_ISOLATED_EVALUATOR_ACTIVE",
    "HM_ISOLATED_READINESS_TARGETS"
)
$originalProcessEnvironment = @{}
foreach ($name in $isolatedEnvironmentNames) {
    $originalProcessEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

if ($AllTopics -and $PSBoundParameters.ContainsKey("Topic") -and -not [string]::IsNullOrWhiteSpace($Topic)) {
    throw "Use either -AllTopics or -Topic, not both."
}
if ($AllTopics) {
    $Topic = ""
}
if ($Mode -eq "full" -and -not $AllTopics) {
    throw "A full corpus run requires -AllTopics; use -Mode standard with -Topic for a focused run."
}
if ($Mode -eq "full" -and -not $EnableFaults) {
    throw "A full corpus includes fault scenarios and requires -EnableFaults on this isolated APP_ENV=test target."
}
if ($Mode -eq "full" -and -not $PSBoundParameters.ContainsKey("BudgetUsd")) {
    throw "Set an explicit -BudgetUsd ceiling before a full corpus run. Run 'python -m hm_chatbot_eval plan --mode full --tests-per-topic 20 --target both' first."
}

$effectiveTestsPerTopic = if ($TestsPerTopic -gt 0) {
    $TestsPerTopic
} elseif ($Mode -eq "standard") {
    5
} elseif ($Mode -eq "full") {
    20
} else {
    1
}
$effectiveJudgeMode = if ($JudgeMode) {
    $JudgeMode
} elseif ($Mode -eq "full") {
    "online"
} else {
    "deferred"
}
if ($Mode -eq "full" -and ($effectiveTestsPerTopic -lt 20 -or $effectiveTestsPerTopic -gt 30)) {
    throw "Full mode requires -TestsPerTopic from 20 to 30."
}

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
# Fault injection is strictly scoped to this isolated APP_ENV=test server.  The
# switch is intentionally opt-in so ordinary smoke runs cannot exercise a fault
# path by accident; fault topics must pass -EnableFaults.
$env:AI_SETUP_EVALUATOR_ENABLED = if ($EnableFaults) { "true" } else { "false" }
$env:AI_SETUP_EVALUATOR_FAULTS_ENABLED = if ($EnableFaults) { "true" } else { "false" }
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
$env:HM_ISOLATED_EVALUATOR_ACTIVE = "1"
$env:TARGET_BACKEND_BASE_URL = $baseUrl
$env:TARGET_BACKEND_HEALTH_URL = "$baseUrl/health"
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

    if ($PreflightOnly) {
        # First check static configuration, then exercise the same authenticated
        # backend/UI fault boundary that a full run uses.  The probe fault replaces
        # the model call, so this cannot spend on the challenger, planner or judge.
        & $python -m hm_chatbot_eval doctor
        if ($LASTEXITCODE -ne 0) {
            throw "Evaluator configuration preflight failed."
        }
        $targetKinds = if ($Target -eq "both") {
            @("backend", "ui")
        } else {
            @($Target)
        }
        $env:HM_ISOLATED_READINESS_TARGETS = $targetKinds | ConvertTo-Json -Compress
        $readiness = @'
import asyncio
import json
import os
import sys
from uuid import uuid4

from hm_chatbot_eval.config import Settings
from hm_chatbot_eval.runner import EvaluationRunner
from hm_chatbot_eval.scenarios import build_scenario
from hm_chatbot_eval.topics import TOPIC_BY_ID


async def main() -> int:
    settings = Settings()
    targets = json.loads(os.environ["HM_ISOLATED_READINESS_TARGETS"])
    scenario = build_scenario(TOPIC_BY_ID["partial_invalid_recovery"], 1, 20260723)
    runner = EvaluationRunner(settings, f"isolated-readiness-{uuid4().hex}", 0.01)
    try:
        ok, records, failure = await runner._readiness_gate(
            [(scenario, str(kind), {"name": "current"}) for kind in targets]
        )
        print(json.dumps({
            "status": "PASS" if ok else "FAIL",
            "records": records,
            "failure": failure.to_dict() if failure else None,
            "model_calls": 0,
        }, ensure_ascii=False))
        return 0 if ok else 1
    finally:
        await runner.close()


raise SystemExit(asyncio.run(main()))
'@
        $readiness | & $python -
        Remove-Item Env:HM_ISOLATED_READINESS_TARGETS -ErrorAction SilentlyContinue
    } else {
        $runArguments = @(
            "-m", "hm_chatbot_eval", "run",
            "--mode", $Mode,
            "--target", $Target,
            "--tests-per-topic", "$effectiveTestsPerTopic",
            "--judge-mode", $effectiveJudgeMode,
            "--budget-usd", "$BudgetUsd",
            "--selection-seed", "20260729"
        )
        if (-not $AllTopics) {
            $runArguments += @("--topics", $Topic)
        }
        if ($RunId) {
            $runArguments += @("--run-id", $RunId)
        }
        & $python @runArguments
    }
    $exitCode = $LASTEXITCODE
} finally {
    if ($null -ne $server -and -not $server.HasExited) {
        & taskkill /PID $server.Id /T /F | Out-Null
    }
    foreach ($name in $isolatedEnvironmentNames) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $originalProcessEnvironment[$name],
            "Process"
        )
    }
}

exit $exitCode
