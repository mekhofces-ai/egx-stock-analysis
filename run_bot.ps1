cd "C:\Users\omar.mokhtar\Documents\New project"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    .\.venv\Scripts\Activate.ps1
}

Write-Host "Starting Telegram Bot..." -ForegroundColor Green
python -u .\telegram_bot_service.py
