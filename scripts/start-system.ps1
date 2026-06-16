$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$nodeDir = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.15.0-win-x64"
$npm = Join-Path $nodeDir "npm.cmd"
if (!(Test-Path $npm)) {
  $npm = "npm.cmd"
}

function Test-Http {
  param([string]$Url)
  try {
    Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 4 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Start-AppService {
  param(
    [string]$Name,
    [string]$Script,
    [string]$HealthUrl
  )

  if (Test-Http -Url $HealthUrl) {
    Write-Host "$Name already running: $HealthUrl"
    return
  }

  $stdout = Join-Path $logDir "$Name.out.log"
  $stderr = Join-Path $logDir "$Name.err.log"
  $process = Start-Process -FilePath $npm `
    -ArgumentList @("run", $Script) `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

  Write-Host "Started $Name with PID $($process.Id). Logs: $stdout"
}

Start-AppService -Name "backend" -Script "backend:dev" -HealthUrl "http://localhost:8788/api/data-status"
Start-AppService -Name "frontend" -Script "dev" -HealthUrl "http://localhost:5173/analyst-bot"
Start-AppService -Name "webhook" -Script "webhook" -HealthUrl "http://localhost:8787/api/health"

Start-Sleep -Seconds 5
& $npm run system:health
