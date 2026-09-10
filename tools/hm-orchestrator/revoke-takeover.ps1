$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$p = Join-Path $repo ".hm-orchestrator\current\CLAUDE_TAKEOVER.ok"
if (Test-Path $p) { Remove-Item -LiteralPath $p -Force }
Write-Host "Claude takeover revoked."
