#requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$installer = Join-Path $env:USERPROFILE "Downloads\DockerDesktopInstaller\Docker Desktop_4.74.0_Machine_X64_exe_en-US.exe"

if (-not (Test-Path -LiteralPath $installer)) {
    throw "Docker Desktop installer was not found at: $installer"
}

Write-Host "Enabling Windows features required by Docker Desktop..."
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

Write-Host "Installing or updating WSL..."
wsl.exe --install --no-distribution
wsl.exe --update

Write-Host "Installing Docker Desktop..."
& $installer install --quiet --accept-license --backend=wsl-2 --no-windows-containers

Write-Host ""
Write-Host "Docker Desktop installation command finished."
Write-Host "If Windows reports that a restart is required, restart before opening Docker Desktop."
