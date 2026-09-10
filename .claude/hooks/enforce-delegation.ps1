# Blocks Claude Code Edit/Write on product files unless a valid, unexpired,
# file-scoped takeover token exists. OpenCode is not affected by this hook.

$ErrorActionPreference = "Stop"
$raw = [Console]::In.ReadToEnd()
try { $evt = $raw | ConvertFrom-Json } catch { exit 0 }

if ($evt.tool_name -notin @("Edit","Write")) { exit 0 }

$project = $env:CLAUDE_PROJECT_DIR
if (-not $project) { $project = (Get-Location).Path }

$path = $evt.tool_input.file_path
if (-not $path) { exit 0 }

try {
    $full = [System.IO.Path]::GetFullPath($path)
    $projectFull = [System.IO.Path]::GetFullPath($project)
} catch { exit 0 }

function Deny([string]$reason) {
    $o = @{
        hookSpecificOutput = @{
            hookEventName = "PreToolUse"
            permissionDecision = "deny"
            permissionDecisionReason = $reason
        }
    }
    $o | ConvertTo-Json -Depth 6 -Compress
    exit 0
}

# Files Claude is allowed to author as architect.
$architectAllowed = @(
    ".hm-orchestrator\current\MISSION.md",
    ".hm-orchestrator\current\VISUAL_CONTRACT.md",
    ".hm-orchestrator\current\CLAUDE_DECISION.md"
)

$rel = $null
if ($full.StartsWith($projectFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    $rel = $full.Substring($projectFull.Length).TrimStart('\','/')
} else {
    Deny "Delegation gate: Claude may not write outside the Hilal Markets project."
}

foreach ($a in $architectAllowed) {
    if ($rel -ieq $a) { exit 0 }
}

# Governance files are never writable by Claude through takeover.
$alwaysProtected = @(
    "CLAUDE.md",
    "AGENTS.md",
    ".claude\*",
    ".opencode\*",
    ".hm-orchestrator\policy\*",
    ".hm-orchestrator\models\*",
    "tools\hm-orchestrator\*"
)
foreach ($pat in $alwaysProtected) {
    if ($rel -like $pat) {
        Deny "Delegation gate: governance/orchestrator file '$rel' must be changed manually, not by Claude."
    }
}

$tokenPath = Join-Path $projectFull ".hm-orchestrator\current\CLAUDE_TAKEOVER.ok"
if (-not (Test-Path $tokenPath)) {
    Deny "Delegation gate: product edits belong to OpenCode Go. Create MISSION.md and run tools/hm-orchestrator/delegate.ps1. Claude may edit product code only after a valid supervisor escalation."
}

try {
    $token = Get-Content -Raw -LiteralPath $tokenPath | ConvertFrom-Json
    $expires = [DateTime]::Parse($token.expires_utc).ToUniversalTime()
    if ([DateTime]::UtcNow -gt $expires) {
        Deny "Delegation gate: Claude takeover token expired. Re-run the OpenCode supervisor or grant a new takeover from a valid escalation."
    }
    $allowed = @($token.allowed_files)
    if ($allowed.Count -eq 0) {
        Deny "Delegation gate: takeover token has no allowed files."
    }

    $ok = $false
    foreach ($pat in $allowed) {
        $p = ($pat -replace '/', '\')
        if ($rel -like $p) { $ok = $true; break }
    }
    if (-not $ok) {
        Deny "Delegation gate: escalation does not authorize Claude to edit '$rel'. Authorized: $($allowed -join ', ')"
    }
} catch {
    Deny "Delegation gate: takeover token is invalid."
}

exit 0
