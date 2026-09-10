param(
    [Parameter(Mandatory=$true)][string]$MissionFile,
    [ValidateSet("Standard","Deep")][string]$Tier = "Standard"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$mission = (Resolve-Path $MissionFile).Path

if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    throw "OpenCode CLI not found."
}

# Refresh live model knowledge if missing or older than 24h.
$live = Join-Path $repo ".hm-orchestrator\models\LIVE_MODELS.md"
$refresh = $true
if (Test-Path $live) {
    $age = (Get-Date) - (Get-Item $live).LastWriteTime
    if ($age.TotalHours -lt 24) { $refresh = $false }
}
if ($refresh) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "refresh-models.ps1") -Quiet
}

$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" + ([guid]::NewGuid().ToString("N").Substring(0,8))
$runDir = Join-Path $repo ".hm-orchestrator\runs\$runId"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$currentDir = Join-Path $repo ".hm-orchestrator\current"
New-Item -ItemType Directory -Force -Path $currentDir | Out-Null
@{
    run_id = $runId
    run_dir = ".hm-orchestrator/runs/$runId"
    started_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $currentDir "RUN.json")

Copy-Item -LiteralPath $mission -Destination (Join-Path $runDir "MISSION.md")
$visual = Join-Path $repo ".hm-orchestrator\current\VISUAL_CONTRACT.md"
if (Test-Path $visual) { Copy-Item $visual (Join-Path $runDir "VISUAL_CONTRACT.md") }

Push-Location $repo
try {
    git rev-parse HEAD | Set-Content -Encoding UTF8 (Join-Path $runDir "BASELINE_COMMIT.txt")
    git status --porcelain=v1 | Set-Content -Encoding UTF8 (Join-Path $runDir "BASELINE_STATUS.txt")
    git diff -- | Set-Content -Encoding UTF8 (Join-Path $runDir "BASELINE_DIFF.patch")
} finally {
    Pop-Location
}

$agent = if ($Tier -eq "Deep") { "hm-supervisor-deep" } else { "hm-supervisor" }

$launch = @"
RUN_ID: $runId
RUN_DIR: .hm-orchestrator/runs/$runId
MISSION_FILE: .hm-orchestrator/runs/$runId/MISSION.md

Execute this mission under the Hilal orchestration policies.
You own execution. Claude Code must not be asked for routine progress.
Write final evidence to:
- .hm-orchestrator/runs/$runId/SUPERVISOR_REPORT.json
- .hm-orchestrator/runs/$runId/SUPERVISOR_REPORT.md

If escalation is required, also write:
- .hm-orchestrator/runs/$runId/ESCALATION.json

Read:
- CLAUDE.md
- AGENTS.md
- .hm-orchestrator/policy/ROUTING_POLICY.md
- .hm-orchestrator/policy/SUPERVISOR_REPORT_CONTRACT.md
- .hm-orchestrator/models/LIVE_MODELS.md
- .hm-orchestrator/models/LIVE_MODELS_VERBOSE.txt
- .hm-orchestrator/models/MODEL_METADATA_RULES.md
Do not claim completion without evidence.
"@

$launchPath = Join-Path $runDir "LAUNCH_PROMPT.txt"
$launch | Set-Content -Encoding UTF8 $launchPath

Push-Location $repo
$prevEAP = $ErrorActionPreference
try {
    # Windows PowerShell 5.1 can promote native stderr redirected with 2>&1
    # into PowerShell error records. Harmless terminal output can then abort
    # the script under ErrorActionPreference=Stop before LASTEXITCODE is read.
    # Native process success/failure is decided by LASTEXITCODE instead.
    $ErrorActionPreference = "Continue"

    # IMPORTANT: OpenCode's --file is variadic. Keep the positional message
    # before every --file option so it can never be consumed as another file.
    $missionAttachment = Join-Path $runDir "MISSION.md"
    $ocOutput = @(
        & opencode run $launch --agent $agent --auto --dir $repo --title "Hilal-$runId" --file $launchPath --file $missionAttachment 2>&1
    )
    $ocExit = $LASTEXITCODE

    # delegate.ps1 is the sole owner of transcript artifacts.
    # Preserve one raw transcript and one ANSI-stripped readable transcript.
    $rawTranscript = Join-Path $runDir "SUPERVISOR_STDOUT_RAW.txt"
    $cleanTranscript = Join-Path $runDir "SUPERVISOR_STDOUT.txt"

    if ($ocOutput.Count -gt 0) {
        $rawLines = @($ocOutput | ForEach-Object { "$_" })
        $rawLines | Set-Content -Encoding UTF8 $rawTranscript

        # ANSI CSI escape sequences: ESC [ ... final-byte
        $cleanLines = @($rawLines | ForEach-Object {
            [regex]::Replace($_, ([char]27).ToString() + '\[[0-?]*[ -/]*[@-~]', '')
        })
        $cleanLines | Set-Content -Encoding UTF8 $cleanTranscript
        $cleanLines | ForEach-Object { Write-Host $_ }
    } else {
        "" | Set-Content -Encoding UTF8 $rawTranscript
        "" | Set-Content -Encoding UTF8 $cleanTranscript
    }
} finally {
    $ErrorActionPreference = $prevEAP
    git status --porcelain=v1 | Set-Content -Encoding UTF8 (Join-Path $runDir "FINAL_STATUS.txt")
    git diff -- | Set-Content -Encoding UTF8 (Join-Path $runDir "FINAL_DIFF.patch")
    Pop-Location
}

if ($ocExit -ne 0) {
    Write-Error "OpenCode exited with code $ocExit. Evidence: $runDir"
    exit $ocExit
}

$report = Join-Path $runDir "SUPERVISOR_REPORT.json"
$reportMd = Join-Path $runDir "SUPERVISOR_REPORT.md"
if (-not (Test-Path $report) -or -not (Test-Path $reportMd)) {
    Write-Error "Supervisor did not produce both required report files. Evidence: $runDir"
    exit 20
}

$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py (Join-Path $PSScriptRoot "validate-report.py") $report (Join-Path $repo ".hm-orchestrator\policy\supervisor-report.schema.json")
if ($LASTEXITCODE -ne 0) {
    Write-Error "Supervisor report failed structural validation. Evidence: $runDir"
    exit 21
}

$r = Get-Content -Raw $report | ConvertFrom-Json
$pointer = @"
# Claude review pointer

Run: $runId
Report: .hm-orchestrator/runs/$runId/SUPERVISOR_REPORT.md
JSON: .hm-orchestrator/runs/$runId/SUPERVISOR_REPORT.json
Diff: .hm-orchestrator/runs/$runId/FINAL_DIFF.patch
Verdict: $($r.final_verdict)
"@
$pointer | Set-Content -Encoding UTF8 (Join-Path $repo ".hm-orchestrator\current\CLAUDE_READ_THIS.md")

if ($r.final_verdict -eq "ESCALATE_TO_CLAUDE") {
    if (-not (Test-Path (Join-Path $runDir "ESCALATION.json"))) {
        Write-Error "Report requests escalation but ESCALATION.json is missing."
        exit 22
    }
    Write-Host "ESCALATE_TO_CLAUDE - see $runDir"
    exit 10
}

Write-Host "Delegated run complete. Claude should review: $runDir"
exit 0
