$ErrorActionPreference = "Continue"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ok = $true

function Check($name, $cond, $detail="") {
    if ($cond) { Write-Host "[PASS] $name $detail" }
    else { Write-Host "[FAIL] $name $detail"; $script:ok = $false }
}

function Test-SafePowerShellSource([string]$Path) {
    try {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
        if ($bytes.Length -eq 0) { return $true }

        # Pure ASCII is safest across Windows PowerShell 5.1 and PowerShell 7.
        $hasNonAscii = $false
        foreach ($x in $bytes) {
            if ($x -gt 127) { $hasNonAscii = $true; break }
        }
        if (-not $hasNonAscii) { return $true }

        # Non-ASCII is acceptable only when a recognized BOM makes decoding explicit.
        $utf8Bom  = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
        $utf16Le  = $bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE
        $utf16Be  = $bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF
        $utf32Be  = $bytes.Length -ge 4 -and $bytes[0] -eq 0x00 -and $bytes[1] -eq 0x00 -and $bytes[2] -eq 0xFE -and $bytes[3] -eq 0xFF

        return ($utf8Bom -or $utf16Le -or $utf16Be -or $utf32Be)
    } catch {
        return $false
    }
}

function Test-PowerShellParse([string]$Path) {
    try {
        $tokens = $null
        $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $Path,
            [ref]$tokens,
            [ref]$parseErrors
        ) | Out-Null
        return (@($parseErrors).Count -eq 0)
    } catch {
        return $false
    }
}

function Invoke-NativeCapture([scriptblock]$Command) {
    $prev = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $out = @(& $Command 2>&1)
        $code = $LASTEXITCODE
        return @{
            Output = @($out | ForEach-Object { "$_" })
            ExitCode = $code
        }
    } finally {
        $ErrorActionPreference = $prev
    }
}

Check "OpenCode CLI" ([bool](Get-Command opencode -ErrorAction SilentlyContinue))

if (Get-Command opencode -ErrorAction SilentlyContinue) {
    $helpProbe = Invoke-NativeCapture { opencode run --help }
    $helpText = $helpProbe.Output -join "`n"
    Check "OpenCode run CLI contract" (($helpProbe.ExitCode -eq 0) -and ($helpText -match '--file') -and ($helpText -match 'message'))
}
Check "Claude CLI" ([bool](Get-Command claude -ErrorAction SilentlyContinue))

$editHook = Join-Path $repo ".claude\hooks\enforce-delegation.ps1"
$bashHook = Join-Path $repo ".claude\hooks\enforce-bash-readonly.ps1"
Check "Claude Edit/Write delegation hook" (Test-Path $editHook)
Check "Claude Bash mutation hook" (Test-Path $bashHook)
Check "OpenCode supervisor" (Test-Path (Join-Path $repo ".opencode\agents\hm-supervisor.md"))
Check "Routing policy" (Test-Path (Join-Path $repo ".hm-orchestrator\policy\ROUTING_POLICY.md"))
Check "Report contract" (Test-Path (Join-Path $repo ".hm-orchestrator\policy\SUPERVISOR_REPORT_CONTRACT.md"))

# Every orchestrator/hook PowerShell source must be safe for Windows PowerShell 5.1
# and must parse successfully before SYSTEM READY can be printed.
$psFiles = @()
$toolDir = Join-Path $repo "tools\hm-orchestrator"
$hookDir = Join-Path $repo ".claude\hooks"
if (Test-Path $toolDir) { $psFiles += Get-ChildItem -Path $toolDir -Filter "*.ps1" -File }
if (Test-Path $hookDir) { $psFiles += Get-ChildItem -Path $hookDir -Filter "*.ps1" -File }

$encodingFailures = @()
$parseFailures = @()
foreach ($f in $psFiles) {
    if (-not (Test-SafePowerShellSource $f.FullName)) { $encodingFailures += $f.FullName }
    if (-not (Test-PowerShellParse $f.FullName)) { $parseFailures += $f.FullName }
}
Check "PowerShell source encoding safe" ($encodingFailures.Count -eq 0) ($(if($encodingFailures.Count){"(" + ($encodingFailures -join ", ") + ")"}else{"($($psFiles.Count) files)"}))
Check "PowerShell scripts parse" ($parseFailures.Count -eq 0) ($(if($parseFailures.Count){"(" + ($parseFailures -join ", ") + ")"}else{"($($psFiles.Count) files)"}))

$nativeWrapperFailures = @()
foreach ($f in $psFiles) {
    $source = [System.IO.File]::ReadAllText($f.FullName)
    $hasNativeOpenCodeCall = ($source -match '(?im)^\s*&\s*opencode\b') -or ($source -match '(?im)^\s*opencode\b')
    if ($hasNativeOpenCodeCall -and ($source -match '\$ErrorActionPreference\s*=\s*"Stop"')) {
        if (-not ($source -match '\$ErrorActionPreference\s*=\s*"Continue"')) {
            $nativeWrapperFailures += $f.FullName
        }
    }
}
Check "OpenCode native stderr guarded" ($nativeWrapperFailures.Count -eq 0) ($(if($nativeWrapperFailures.Count){"(" + ($nativeWrapperFailures -join ", ") + ")"}else{"ok"}))

$stdoutOwnerFailures = @()
$agentDir = Join-Path $repo ".opencode\agents"
if (Test-Path $agentDir) {
    foreach ($f in Get-ChildItem -Path $agentDir -Filter "*.md" -File) {
        $s = [System.IO.File]::ReadAllText($f.FullName)
        if (($s -match 'SUPERVISOR_STDOUT\.txt') -or ($s -match 'SUPERVISOR_STDOUT_RAW\.txt')) {
            if (-not ($s -match 'NEVER write, append, truncate, rename, or delete')) {
                $stdoutOwnerFailures += $f.FullName
            }
        }
    }
}
Check "Supervisor transcript ownership declared" ($stdoutOwnerFailures.Count -eq 0) ($(if($stdoutOwnerFailures.Count){"(" + ($stdoutOwnerFailures -join ", ") + ")"}else{"ok"}))

if (Get-Command opencode -ErrorAction SilentlyContinue) {
    $modelProbe = Invoke-NativeCapture { opencode models opencode-go }
    $go = @($modelProbe.Output | Where-Object { $_ -match '^opencode-go/' })
    Check "OpenCode Go connected/models visible" (($modelProbe.ExitCode -eq 0) -and ($go.Count -gt 0)) "($($go.Count) models)"
    foreach ($need in @(
        "opencode-go/minimax-m3",
        "opencode-go/qwen3.8-flash",
        "opencode-go/deepseek-v4.1-flash",
        "opencode-go/deepseek-v4-flash-vision-exp"
    )) {
        Check "Default model $need" ($go -contains $need)
    }
}

$settings = Join-Path $repo ".claude\settings.local.json"
if (Test-Path $settings) {
    try {
        $s = Get-Content -Raw $settings | ConvertFrom-Json
        $hookText = $s.hooks.PreToolUse | ConvertTo-Json -Depth 10
        Check "Claude Edit/Write hook registered" ($hookText -match "enforce-delegation")
        Check "Claude Bash hook registered" ($hookText -match "enforce-bash-readonly")
    } catch {
        Check "Claude settings JSON parses" $false
    }
} else {
    Write-Host "[FAIL] .claude/settings.local.json does not exist."
    $ok = $false
}

if ($ok) { Write-Host "`nSYSTEM READY"; exit 0 }
Write-Host "`nSYSTEM NOT READY"; exit 1
