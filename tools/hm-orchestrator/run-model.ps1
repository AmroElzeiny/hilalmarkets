param(
    [Parameter(Mandatory=$true)][string]$Model,
    [Parameter(Mandatory=$true)][string]$PromptFile,
    [ValidateSet("Read","Write")][string]$Mode = "Read",
    [string]$Variant = "",
    [string[]]$File = @()
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$promptPath = (Resolve-Path $PromptFile).Path

# Exact availability check from locally selectable Go models.
$available = @(& opencode models opencode-go | Where-Object { $_ -match '^opencode-go/' } | ForEach-Object { $_.Trim() })
$modelRef = if ($Model.StartsWith("opencode-go/")) { $Model } else { "opencode-go/$Model" }
$normalModels = @(
    "opencode-go/minimax-m3",
    "opencode-go/qwen3.8-flash",
    "opencode-go/deepseek-v4.1-flash",
    "opencode-go/deepseek-v4-flash-vision-exp"
)

if ($modelRef -notin $normalModels) {
    throw "Model '$modelRef' is outside the normal cost-controlled routing set. Use the configured agents or perform a documented cost escalation."
}
if ($modelRef -notin $available) {
    throw "Model is not currently selectable through OpenCode Go: $modelRef"
}

$agent = if ($Mode -eq "Write") { "hm-direct-write" } else { "hm-direct-read" }
$prompt = Get-Content -Raw $promptPath

# IMPORTANT: OpenCode's --file is variadic. The positional message must be
# placed before all --file options or it can be swallowed as a file path.
$args = @("run",$prompt,"--agent",$agent,"--model",$modelRef,"--auto","--dir",$repo)

if ($Variant) {
    # Caller is responsible for proving this variant exists in live metadata.
    $args += @("--variant",$Variant)
}
$args += @("--file",$promptPath)
foreach ($f in $File) {
    $args += @("--file",(Resolve-Path $f).Path)
}

$prevEAP = $ErrorActionPreference
try {
    # Protect Windows PowerShell 5.1 from treating native stderr as a
    # terminating PowerShell error. LASTEXITCODE is the process authority.
    $ErrorActionPreference = "Continue"
    & opencode @args
    $ocExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}
exit $ocExit
