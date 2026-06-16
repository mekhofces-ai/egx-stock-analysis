cd "C:\Users\omar.mokhtar\Documents\New project"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    .\.venv\Scripts\Activate.ps1
}

Write-Host "===================================" -ForegroundColor Cyan
Write-Host " Running One-Time System Tests" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan

if (Test-Path ".\app\services\strategies\cli_v6_egx.py") {
    Write-Host "Running CLI v6 strategy for COMI..." -ForegroundColor Yellow
    python -u .\app\services\strategies\cli_v6_egx.py --symbol COMI
}
else {
    Write-Host "Skipped strategy test: app\services\strategies\cli_v6_egx.py not found." -ForegroundColor Red
}

Write-Host ""

if (Test-Path ".\app\services\backtest_cli_v6.py") {
    Write-Host "Running backtest for COMI 1d..." -ForegroundColor Yellow
    python -u .\app\services\backtest_cli_v6.py --symbol COMI --timeframe 1d
}
else {
    Write-Host "Skipped backtest: app\services\backtest_cli_v6.py not found." -ForegroundColor Red
}

Write-Host ""

if (Test-Path ".\app\services\automation_runner.py") {
    Write-Host "Running automation once..." -ForegroundColor Yellow
    python -u .\app\services\automation_runner.py --once
}
else {
    Write-Host "Skipped automation once: app\services\automation_runner.py not found." -ForegroundColor Red
}

Write-Host ""
Write-Host "One-time tests finished." -ForegroundColor Green
Read-Host "Press Enter to close"
