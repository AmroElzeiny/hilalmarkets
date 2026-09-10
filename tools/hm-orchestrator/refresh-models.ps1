param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$outDir = Join-Path $repo ".hm-orchestrator\models"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    throw "OpenCode CLI not found. Install OpenCode first."
}

$stamp = [DateTime]::UtcNow.ToString("o")
$verbosePath = Join-Path $outDir "LIVE_MODELS_VERBOSE.txt"
$idsPath = Join-Path $outDir "LIVE_MODEL_IDS.txt"
$jsonPath = Join-Path $outDir "LIVE_ENDPOINT.json"
$mdPath = Join-Path $outDir "LIVE_MODELS.md"

# Refresh OpenCode's local catalog and query models. Native stderr is
# non-authoritative; LASTEXITCODE is the native process result.
$prevEAP = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"

    & opencode models --refresh 2>&1 | Out-Null

    # Exact locally selectable Go models + metadata as OpenCode currently sees them.
    $verbose = @(& opencode models opencode-go --verbose 2>&1)
    $verbose | ForEach-Object { "$_" } | Set-Content -Encoding UTF8 $verbosePath

    $ids = @(& opencode models opencode-go 2>&1)
    $ids | ForEach-Object { "$_" } | Set-Content -Encoding UTF8 $idsPath
} finally {
    $ErrorActionPreference = $prevEAP
}

$endpointOk = $false
try {
    $endpoint = Invoke-RestMethod -Method Get -Uri "https://opencode.ai/zen/go/v1/models" -TimeoutSec 30
    $endpoint | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $jsonPath
    $endpointIds = @($endpoint.data | ForEach-Object { "opencode-go/" + $_.id } | Sort-Object)
    $endpointOk = $true
} catch {
    $endpointIds = @()
    if (-not $Quiet) { Write-Warning "Could not refresh Go endpoint: $($_.Exception.Message)" }
}

$localIds = @($ids | Where-Object { $_ -match '^opencode-go/' } | ForEach-Object { $_.Trim() } | Sort-Object -Unique)

$md = @()
$md += "# Live OpenCode Go model snapshot"
$md += ""
$md += "Generated UTC: $stamp"
$md += ""
$md += "Authority: local `opencode models opencode-go --refresh --verbose`."
$md += "Do not invent variants. Inspect `LIVE_MODELS_VERBOSE.txt` for current metadata."
$md += ""
$md += "## Locally selectable"
$md += ""
foreach ($m in $localIds) { $md += "- ``$m``" }

if ($endpointOk) {
    $md += ""
    $md += "## Go endpoint inventory"
    $md += ""
    foreach ($m in $endpointIds) { $md += "- ``$m``" }
    $newVsLocal = @($endpointIds | Where-Object { $_ -notin $localIds })
    $localVsEndpoint = @($localIds | Where-Object { $_ -notin $endpointIds })
    $md += ""
    $md += "## Drift"
    $md += ""
    $md += "Endpoint but not locally selectable: " + ($(if($newVsLocal.Count){$newVsLocal -join ', '}else{'none'}))
    $md += "Local but not endpoint: " + ($(if($localVsEndpoint.Count){$localVsEndpoint -join ', '}else{'none'}))
}
$md | Set-Content -Encoding UTF8 $mdPath

if (-not $Quiet) {
    Write-Host "OpenCode Go model snapshot refreshed:"
    Write-Host "  $mdPath"
    Write-Host "  $verbosePath"
}
