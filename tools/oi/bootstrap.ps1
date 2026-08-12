# Install Open Interpreter into its own environment (Windows).
#
#   tools\oi\bootstrap.ps1
#
# Creates .oi-venv using Python 3.11 and installs the pinned set in requirements.txt.
# The project's own .venv is never touched: Open Interpreter needs Python < 3.12 and
# pulls in litellm, selenium and matplotlib, and putting any of that beside the product's
# pinned dependencies would change what the release gate's `pip check` and
# scripts/check_dependency_lock.py see.

param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$venv = Join-Path $root ".oi-venv"
$requirements = Join-Path $PSScriptRoot "requirements.txt"

if (-not $PythonExe) {
    # open-interpreter 0.4.3 declares python >=3.9,<3.12. 3.12 will resolve to nothing.
    $found = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $found) {
        Write-Error "Python 3.11 was not found. Open Interpreter 0.4.3 requires >=3.9,<3.12. Install it, or pass -PythonExe."
    }
    $PythonExe = $found.Trim()
}

Write-Host "Creating $venv with $PythonExe"
& $PythonExe -m venv $venv
$venvPython = Join-Path $venv "Scripts\python.exe"

& $venvPython -m pip install --upgrade pip --quiet

# Everything except wget, from wheels only. Left to build from source, litellm demands a
# Rust toolchain that is neither needed nor present.
Write-Host "Installing pinned dependencies (wheels only)"
$wheelOnly = Get-Content $requirements |
    Where-Object { $_ -notmatch '^\s*#' -and $_.Trim() -ne '' -and $_ -notmatch '^wget' }
& $venvPython -m pip install --only-binary=:all: @wheelOnly

# wget ships as a source archive only. It is pure Python and needs no compiler, but
# interpreter/terminal_interface/local_setup.py imports it at start-up, so it cannot be
# left out even though local models are never used.
Write-Host "Installing wget (source archive, no compiler needed)"
& $venvPython -m pip install "wget==3.2"

Write-Host ""
& $venvPython -c "import interpreter; print('Open Interpreter', interpreter.__version__ if hasattr(interpreter,'__version__') else 'installed')"

Write-Host ""
Write-Host "Done. Check the configuration with:"
Write-Host "    .venv\Scripts\python -m hm_oi doctor"
Write-Host "Then start a session with:"
Write-Host "    tools\oi\hm-oi.ps1"
Write-Host ""
Write-Host "Set HM_OI_API_KEY before the first session. It is separate from OPENAI_API_KEY"
Write-Host "on purpose, so engineering spend is never billed as customer spend."
