cd "C:\Users\omar.mokhtar\Documents\New project"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    .\.venv\Scripts\Activate.ps1
}

Write-Host "Running full daily dynamic EGX refresh..." -ForegroundColor Green
python -u -m app.services.daily_dynamic_refresh --force --limit 250
