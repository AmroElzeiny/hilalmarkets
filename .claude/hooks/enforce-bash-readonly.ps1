# Prevent Claude Code from bypassing the Edit/Write delegation gate by mutating
# project files through Bash/PowerShell shell commands.
# Code changes after a valid takeover must still use Edit/Write so file-scope
# authorization can be enforced precisely.

$ErrorActionPreference = "Stop"
$raw = [Console]::In.ReadToEnd()
try { $evt = $raw | ConvertFrom-Json } catch { exit 0 }
if ($evt.tool_name -ne "Bash") { exit 0 }

$cmd = [string]$evt.tool_input.command
if (-not $cmd) { exit 0 }

function Deny([string]$reason) {
    @{
        hookSpecificOutput = @{
            hookEventName = "PreToolUse"
            permissionDecision = "deny"
            permissionDecisionReason = $reason
        }
    } | ConvertTo-Json -Depth 6 -Compress
    exit 0
}

# Approved orchestration entry points. They may create evidence/control files.
$approved = @(
    "tools\hm-orchestrator\delegate.ps1",
    "tools/hm-orchestrator/delegate.ps1",
    "tools\hm-orchestrator\refresh-models.ps1",
    "tools/hm-orchestrator/refresh-models.ps1",
    "tools\hm-orchestrator\verify-system.ps1",
    "tools/hm-orchestrator/verify-system.ps1",
    "tools\hm-orchestrator\grant-takeover.ps1",
    "tools/hm-orchestrator/grant-takeover.ps1",
    "tools\hm-orchestrator\revoke-takeover.ps1",
    "tools/hm-orchestrator/revoke-takeover.ps1",
    "tools\hm-orchestrator\run-model.ps1",
    "tools/hm-orchestrator/run-model.ps1"
)
foreach ($a in $approved) {
    if ($cmd -like "*$a*") { exit 0 }
}

# Block common direct mutation paths. Claude must use Edit/Write after an
# authorized takeover, because those tools are checked against allowed_files.
$patterns = @(
    '(?i)\bSet-Content\b',
    '(?i)\bAdd-Content\b',
    '(?i)\bOut-File\b',
    '(?i)\bRemove-Item\b',
    '(?i)\bCopy-Item\b',
    '(?i)\bMove-Item\b',
    '(?i)\bRename-Item\b',
    '(?i)\bNew-Item\b',
    '(?i)(^|[;&|]\s*)rm(\s|$)',
    '(?i)(^|[;&|]\s*)del(\s|$)',
    '(?i)(^|[;&|]\s*)erase(\s|$)',
    '(?i)(^|[;&|]\s*)cp(\s|$)',
    '(?i)(^|[;&|]\s*)mv(\s|$)',
    '(?i)(^|[;&|]\s*)touch(\s|$)',
    '(?i)(^|[;&|]\s*)tee(\s|$)',
    '(?i)\bsed\s+-i\b',
    '(?i)\bperl\s+-pi\b',
    '(?i)\bgit\s+(apply|checkout|restore|reset|clean|commit|merge|rebase|cherry-pick|am)\b',
    '(?i)\b(pip|pip3)\s+install\b',
    '(?i)\bpython\s+-m\s+pip\s+install\b',
    '(?i)\b(npm|pnpm|yarn|bun)\s+(install|add|remove|uninstall)\b',
    '(?i)\bfs\.(writeFile|writeFileSync|appendFile|appendFileSync)\b',
    '(?i)\bwrite_text\s*\(',
    '(?i)\bwrite_bytes\s*\('
)

foreach ($pat in $patterns) {
    if ($cmd -match $pat) {
        Deny "Delegation gate: Claude shell is read/test/orchestration-only. Product file mutations must be delegated to OpenCode, or after a valid escalation must use Claude Edit/Write so the authorized file scope can be enforced."
    }
}

# Block obvious stdout redirection to a file, but allow stderr/stdout descriptor
# plumbing such as 2>&1 and comparison operators used inside quoted test code.
$redirectionNormalized = $cmd -replace '\d+>&\d+', ''
if ($redirectionNormalized -match '(?<![<>=])>>?\s*["'']?[^&\s]') {
    Deny "Delegation gate: shell file redirection is blocked for Claude. Use OpenCode for implementation or Edit/Write after a valid file-scoped takeover."
}

exit 0
