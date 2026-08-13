# Install Open Interpreter into its own environment (Windows).
#
#   tools\oi\bootstrap.ps1
#
# Creates .oi-venv and installs the pinned set in requirements.txt.
#
# The project's own .venv is never touched, because Open Interpreter pulls in litellm,
# selenium and matplotlib, and putting any of that beside the product's pinned
# dependencies would change what the release gate's `pip check` and
# scripts/check_dependency_lock.py see.
#
# Python 3.11 is preferred because that is the version this set was installed and
# verified on. It is not a hard requirement: open-interpreter 0.4.3 declares
# `>=3.9,<4`. An earlier version of this script claimed `<3.12` and refused to run
# without 3.11, which sent people hunting for an interpreter they did not need.

param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$venv = Join-Path $root ".oi-venv"
$requirements = Join-Path $PSScriptRoot "requirements.txt"

if (-not $PythonExe) {
    # 3.11 first: the known-good version. Anything >=3.9 satisfies the package.
    $found = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $found) {
        $found = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $found) {
            Write-Error "No Python interpreter was found. Open Interpreter 0.4.3 needs >=3.9. Install one, or pass -PythonExe."
        }
        $version = (& $found.Trim() -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
        Write-Host "Python 3.11 was not found; using $version instead."
        Write-Host "That satisfies the package, but 3.11 is the version this set was verified on."
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
