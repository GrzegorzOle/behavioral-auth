# Build the self-contained Windows one-folder bundle and the Inno Setup installer.
#
# The Windows counterpart of packaging/build-linux.sh + build-appimage.sh in one
# script. Run on Windows from the repo root, in a venv that installed
# requirements.txt (pulls pynput + pywin32) and pyinstaller:
#
#     .\packaging\windows\build-windows.ps1 -Version 0.3.0
#
# Produces:
#   dist\behavioral-auth\                      the one-folder bundle (5 .exes + _internal\)
#   dist\behavioral-auth-setup-<version>.exe   the installer (if Inno Setup's iscc is on PATH)
#
# Not runtime-verified on a real Windows box yet — see Planned work, Stage 2.

param(
    [string]$Version = "0.3.0",
    [string]$PyInstaller = "pyinstaller",
    # Inno Setup's compiler. Skipped (bundle only) if not found.
    [string]$Iscc = "iscc"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

Write-Host ">> Building one-folder bundle with PyInstaller"
& $PyInstaller --noconfirm "packaging\windows\behavioral-auth-windows.spec"

$Dist = Join-Path $Root "dist\behavioral-auth"
if (-not (Test-Path (Join-Path $Dist "behavioral-auth-service.exe"))) {
    throw "Bundle is missing behavioral-auth-service.exe — spec build failed"
}
Write-Host ">> Bundle at: $Dist"

$IsccPath = Get-Command $Iscc -ErrorAction SilentlyContinue
if ($null -eq $IsccPath) {
    Write-Warning "Inno Setup compiler ($Iscc) not found — skipping the installer. " +
                  "Install it (choco install innosetup) and re-run, or ship the folder."
    exit 0
}

Write-Host ">> Building installer with Inno Setup"
& $Iscc "/DMyAppVersion=$Version" "packaging\windows\installer.iss"
Write-Host ">> Done. Installer in dist\"
