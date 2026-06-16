cd "C:\Users\omar.mokhtar\Documents\New project"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    .\.venv\Scripts\Activate.ps1
}

Write-Host "Starting Streamlit Dashboard..." -ForegroundColor Green
streamlit run dashboard/streamlit_app.py --server.port 8509 --server.headless true
