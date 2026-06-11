# Install Fleet Fuel system as a Windows Service using NSSM.
# Run in an elevated PowerShell from the app folder.
#   1. Download NSSM from https://nssm.cc and put nssm.exe on PATH (or in this folder)
#   2. .\install_service_windows.ps1
param(
    [string]$ServiceName = "FleetFuel",
    [string]$PythonExe   = "python",
    [int]$Port           = 8050
)
$AppDir = $PSScriptRoot
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source
if (-not $nssm) { $nssm = Join-Path $AppDir "nssm.exe" }
if (-not (Test-Path $nssm)) {
    Write-Error "nssm.exe not found. Download from https://nssm.cc and place on PATH or in $AppDir"
    exit 1
}
& $nssm install $ServiceName $PythonExe (Join-Path $AppDir "serve.py")
& $nssm set $ServiceName AppDirectory $AppDir
& $nssm set $ServiceName AppEnvironmentExtra "BIND_HOST=127.0.0.1" "BIND_PORT=$Port"
& $nssm set $ServiceName Start SERVICE_AUTO_START
& $nssm set $ServiceName AppStdout (Join-Path $AppDir "service.log")
& $nssm set $ServiceName AppStderr (Join-Path $AppDir "service.log")
& $nssm start $ServiceName
Write-Host "Service '$ServiceName' installed and started on port $Port."
Write-Host "Manage with: nssm restart/stop/remove $ServiceName  or  services.msc"
