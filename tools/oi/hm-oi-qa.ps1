# Run the HilalMarkets adversarial QA harness (Windows).
#
#   tools\oi\hm-oi-qa.ps1 -Stage baseline    capture the browser failing set (run twice)
#   tools\oi\hm-oi-qa.ps1 -Stage corpus      the adversarial conversation corpus (free)
#   tools\oi\hm-oi-qa.ps1 -Stage boundaries  the deterministic boundary attacks (free)
#   tools\oi\hm-oi-qa.ps1 -Stage browser     the browser attack flows
#   tools\oi\hm-oi-qa.ps1 -Stage target      identify a target and refuse production
#   tools\oi\hm-oi-qa.ps1 -Stage all         everything above, in order
#
# Deliberately thin. It starts nothing: the isolated target comes from
# scripts\run_isolated_setup_chat_smoke.ps1, which already builds a throwaway database,
# a throwaway secret and mock providers, and restores the caller's environment when it
# finishes. A second launcher would get one of those wrong.
#
# This harness never fixes anything and never promotes a regression candidate. See
# docs\OI_ADVERSARIAL_QA.md.

param(
    [ValidateSet("baseline", "corpus", "boundaries", "browser", "target", "all")]
    [string]$Stage = "all",

    # Only used by -Stage target and -Stage browser. Must not be production; the harness
    # refuses any address that is not loopback or on its staging allowlist.
    [string]$BaseUrl = "http://127.0.0.1:8124",

    # The ceiling for the handful of attacks that call a paid provider. Nothing paid runs
    # unless -AllowPaid is given as well.
    [double]$BudgetUsd = 0.25,
    [switch]$AllowPaid
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$scratch = Join-Path $root "test-results\oi-qa"

if (-not (Test-Path $python)) {
    Write-Error "The project virtual environment is missing. See docs\LOCAL_DEVELOPMENT.md."
}
New-Item -ItemType Directory -Path $scratch -Force | Out-Null

$env:PYTHONIOENCODING = "utf-8"
$failed = @()

function Invoke-Stage([string]$Name, [scriptblock]$Body) {
    Write-Host ""
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        $script:failed += $Name
        Write-Host "$Name reported problems (exit $LASTEXITCODE)." -ForegroundColor Yellow
    }
}

if ($Stage -eq "target" -or $Stage -eq "all") {
    Invoke-Stage "target" {
        & $python -m hm_oi qa target $BaseUrl
    }
}

if ($Stage -eq "corpus" -or $Stage -eq "all") {
    Invoke-Stage "corpus" {
        & $python -m hm_oi qa corpus
        & $python -m pytest (Join-Path $root "tests\oi\test_invariant_adversarial_qa.py") -q -p no:randomly
    }
}

if ($Stage -eq "boundaries" -or $Stage -eq "all") {
    Invoke-Stage "boundaries" {
        & $python -m pytest (Join-Path $root "tests\oi") -q -p no:randomly
        & $python (Join-Path $root "scripts\check_oi_boundary.py")
        & $python (Join-Path $root "scripts\check_release_invariants.py")
    }
}

if ($Stage -eq "baseline") {
    # Two captures, because one capture cannot tell a stable failure from a flaky one,
    # and excusing a flaky test as "baseline" would hide a real regression behind it.
    Invoke-Stage "baseline" {
        foreach ($pass in 1, 2) {
            $report = Join-Path $scratch "baseline-$pass.xml"
            Write-Host "capture $pass -> $report"
            & $python -m pytest (Join-Path $root "tests\browser") -q -p no:randomly `
                --junitxml=$report
        }
        # pytest exits non-zero when the baseline has failures in it, which is expected
        # and is the thing being captured, not an error in this script.
        $global:LASTEXITCODE = 0
    }
}

if ($Stage -eq "browser" -or $Stage -eq "all") {
    Invoke-Stage "browser" {
        $env:BROWSER_E2E_BASE_URL = $null
        & $python -m pytest (Join-Path $root "tests\browser\test_adversarial_qa_e2e.py") `
            -q -p no:randomly
    }
}

if ($AllowPaid) {
    Write-Host ""
    Write-Host ("Paid attacks were requested with a `$${BudgetUsd} ceiling. " +
        "Run them through the isolated target:") -ForegroundColor Yellow
    Write-Host "  scripts\run_isolated_setup_chat_smoke.ps1 -PreflightOnly -BudgetUsd $BudgetUsd"
}

Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host ("Stages reporting problems: " + ($failed -join ", ")) -ForegroundColor Yellow
    Write-Host "Read them before treating any of it as a finding: a skipped attack is not a pass."
    exit 1
}
Write-Host "All requested stages completed." -ForegroundColor Green
exit 0
