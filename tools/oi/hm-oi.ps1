# Start the HilalMarkets engineering assistant (Windows).
#
#   tools\oi\hm-oi.ps1                 normal tier, you approve each command
#   tools\oi\hm-oi.ps1 -Tier fast      cheap tier for lookups
#   tools\oi\hm-oi.ps1 -Tier deep      for architecture, security, hard bugs
#
# Deliberately thin. Credential scrubbing and everything else lives in
# src/hm_oi/launch.py, so Windows and Linux cannot drift apart.

param(
    [ValidateSet("fast", "normal", "deep")]
    [string]$Tier = "normal",

    [double]$BudgetUsd = 0,

    # Runs without stopping for approval. The permission policy still refuses everything
    # it would refuse, and additionally refuses anything that needs a person - because
    # there is no person to ask.
    [switch]$Unattended,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $root ".oi-venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Open Interpreter is not installed. Run tools\oi\bootstrap.ps1 first."
}

$env:HM_OI_REPO_ROOT = $root
$env:HM_OI_TIER = $Tier
if ($Unattended) { $env:HM_OI_AUTO_RUN = "1" } else { $env:HM_OI_AUTO_RUN = "0" }
if ($BudgetUsd -gt 0) { $env:HM_OI_SESSION_BUDGET_USD = $BudgetUsd }

& $python (Join-Path $root "tools\oi\launch.py") @Rest
exit $LASTEXITCODE
