# behavioral-auth – Windows installation script
# Run in PowerShell as Administrator:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\src\scripts\windows-install.ps1
#
# NOTE: On Windows only face verification (OpenCV LBPH) and inference on
#       pre-collected data are supported.  The behavioral-collector command
#       requires evdev and is Linux-only.

#Requires -RunAsAdministrator
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT_DIR  = (Resolve-Path "$PSScriptRoot\..\..").Path
$VENV_DIR  = "$ROOT_DIR\.venv"
$DATA_DIR  = "$env:LOCALAPPDATA\behavioral-auth"

Write-Host "`n=== behavioral-auth Windows installer ===" -ForegroundColor Cyan

# ── 1. Verify Python 3.11+ ─────────────────────────────────────────────────
Write-Host "`n[1/5] Checking Python version..."
$pyCmd = $null
foreach ($candidate in @("python3.11", "python3", "python")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "3\.(1[1-9]|[2-9]\d)") {
            $pyCmd = $candidate
            Write-Host "  Found: $ver  ($candidate)"
            break
        }
    } catch { }
}
if (-not $pyCmd) {
    Write-Host "  ERROR: Python 3.11+ not found." -ForegroundColor Red
    Write-Host "  Download from: https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}

# ── 2. Create virtual environment ─────────────────────────────────────────
Write-Host "`n[2/5] Creating virtual environment at $VENV_DIR ..."
if (-not (Test-Path $VENV_DIR)) {
    & $pyCmd -m venv $VENV_DIR
}
$pip  = "$VENV_DIR\Scripts\pip.exe"
$py   = "$VENV_DIR\Scripts\python.exe"

# ── 3. Install Python dependencies (Windows subset) ───────────────────────
Write-Host "`n[3/5] Installing Python dependencies..."
& $pip install --upgrade pip --quiet

$reqFile = "$ROOT_DIR\requirements-windows.txt"
if (Test-Path $reqFile) {
    & $pip install -r $reqFile
} else {
    Write-Host "  requirements-windows.txt not found, falling back to requirements.txt" -ForegroundColor Yellow
    # Filter out evdev on-the-fly
    $tmpReq = [System.IO.Path]::GetTempFileName() + ".txt"
    Get-Content "$ROOT_DIR\requirements.txt" |
        Where-Object { $_ -notmatch "^evdev" } |
        Set-Content $tmpReq
    & $pip install -r $tmpReq
    Remove-Item $tmpReq -ErrorAction SilentlyContinue
}

# Install the package itself
& $pip install -e $ROOT_DIR --quiet 2>$null
if ($LASTEXITCODE -ne 0) { & $pip install $ROOT_DIR --quiet }

# ── 4. Create data directory and bootstrap database ───────────────────────
Write-Host "`n[4/5] Creating data directory at $DATA_DIR ..."
New-Item -ItemType Directory -Force -Path $DATA_DIR | Out-Null

$env:BEHAVIORAL_DB_PATH = "$DATA_DIR\behavioral.db"
try {
    & $py "$ROOT_DIR\src\scripts\bootstrap_db.py"
    Write-Host "  Database initialised."
} catch {
    Write-Host "  Warning: database bootstrap failed (will retry on first run)." -ForegroundColor Yellow
}

# ── 5. Add Scripts folder to user PATH (optional) ─────────────────────────
Write-Host "`n[5/5] Updating user PATH..."
$scriptsDir = "$VENV_DIR\Scripts"
$userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*$scriptsDir*") {
    [System.Environment]::SetEnvironmentVariable(
        "PATH", "$userPath;$scriptsDir", "User")
    Write-Host "  Added $scriptsDir to PATH."
} else {
    Write-Host "  PATH already contains $scriptsDir."
}

Write-Host @"

✅  behavioral-auth installed.

Activate the virtual environment in a new terminal:
  $VENV_DIR\Scripts\Activate.ps1

Available commands on Windows:
  behavioral-face enroll   - Capture face samples (camera required)
  behavioral-face verify   - One-shot face verification
  behavioral-face info     - Show face model status
  behavioral-face delete   - Remove face model
  behavioral-features      - Extract features (requires pre-collected DB)
  behavioral-train         - Train autoencoder
  behavioral-infer         - Run inference
  behavioral-status        - Pipeline status report

NOTE: behavioral-collector is Linux-only (evdev).
      To collect data on Windows, transfer a DuckDB file from a Linux machine,
      or use a Linux VM / WSL2 for the collection step.

"@ -ForegroundColor Green

