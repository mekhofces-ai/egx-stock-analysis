$ProjectRoot = "C:\Users\omar.mokhtar\Documents\New project"
$LogsDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

function Test-PythonProcess {
    param([string[]]$Patterns)
    return @(Get-CimInstance Win32_Process | Where-Object {
        if ($_.Name -ne "python.exe" -or -not $_.CommandLine) {
            return $false
        }
        foreach ($Pattern in $Patterns) {
            if ($_.CommandLine -notmatch [regex]::Escape($Pattern)) {
                return $false
            }
        }
        return $true
    }).Count -gt 0
}

function Start-EGXProcess {
    param(
        [string]$Name,
        [string[]]$Pattern,
        [string[]]$Arguments,
        [string]$OutFile,
        [string]$ErrFile
    )
    if (Test-PythonProcess $Pattern) {
        Write-Host "$Name already running." -ForegroundColor Yellow
        return
    }
    Write-Host "Starting $Name..." -ForegroundColor Green
    Start-Process `
        -FilePath $PythonExe `
        -ArgumentList $Arguments `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput (Join-Path $LogsDir $OutFile) `
        -RedirectStandardError (Join-Path $LogsDir $ErrFile) `
        -WindowStyle Hidden
}

Start-EGXProcess `
    -Name "EGX Streamlit Dashboard" `
    -Pattern @("dashboard/streamlit_app.py", "8509") `
    -Arguments @("-m", "streamlit", "run", "dashboard/streamlit_app.py", "--server.port", "8509", "--server.headless", "true") `
    -OutFile "streamlit.out.log" `
    -ErrFile "streamlit.err.log"

Start-EGXProcess `
    -Name "EGX Telegram Bot" `
    -Pattern "telegram_bot_service.py" `
    -Arguments @("telegram_bot_service.py") `
    -OutFile "telegram_bot.out.log" `
    -ErrFile "telegram_bot.err.log"

Start-EGXProcess `
    -Name "EGX Automation Runner" `
    -Pattern "automation_runner.py" `
    -Arguments @("-m", "app.services.automation_runner") `
    -OutFile "automation.out.log" `
    -ErrFile "automation.err.log"

Start-Sleep -Seconds 4
Start-Process "http://localhost:8509"
Write-Host "EGX platform is starting. Dashboard: http://localhost:8509" -ForegroundColor Cyan
