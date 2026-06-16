cd "C:\Users\omar.mokhtar\Documents\New project"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    .\.venv\Scripts\Activate.ps1
}

Write-Host "Starting Automation Runner..." -ForegroundColor Green
python -u -m app.services.automation_runner
