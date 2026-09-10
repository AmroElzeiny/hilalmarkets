param([int]$Minutes = 120)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$current = Join-Path $repo ".hm-orchestrator\current\RUN.json"
if (-not (Test-Path $current)) { throw "No current delegated run is recorded." }

$run = Get-Content -Raw $current | ConvertFrom-Json
$runDir = Join-Path $repo ($run.run_dir -replace '/', '\')
$escPath = Join-Path $runDir "ESCALATION.json"
if (-not (Test-Path $escPath)) {
    throw "The current OpenCode run did not emit ESCALATION.json."
}

$j = Get-Content -Raw $escPath | ConvertFrom-Json
if ($j.status -ne "ESCALATE_TO_CLAUDE") { throw "Current escalation has invalid status." }
if ($j.run_id -ne $run.run_id) { throw "Escalation run_id does not match the current run." }
if (-not $j.needs_claude_implementation) {
    throw "Supervisor asks for Claude reasoning/replanning, not direct code implementation. Do not unlock writes."
}
$allowed = @($j.allowed_files)
if ($allowed.Count -eq 0) { throw "Escalation did not authorize any files." }

$token = @{
    run_id = $j.run_id
    granted_utc = [DateTime]::UtcNow.ToString("o")
    expires_utc = [DateTime]::UtcNow.AddMinutes($Minutes).ToString("o")
    allowed_files = $allowed
    source_escalation = $escPath
}
$out = Join-Path $repo ".hm-orchestrator\current\CLAUDE_TAKEOVER.ok"
$token | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $out
Write-Host "Claude takeover granted for $Minutes minutes, only for:"
$allowed | ForEach-Object { Write-Host "  $_" }
