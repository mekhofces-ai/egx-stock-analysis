$ProjectPath = "C:\Users\omar.mokhtar\Documents\New project"
$LogDir = Join-Path $ProjectPath "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Tasks = @(
    @{ Name = "EGX Streamlit Dashboard"; Script = "run_dashboard.ps1"; Log = "egx_dashboard_task.log" },
    @{ Name = "EGX Telegram Bot Service"; Script = "run_bot.ps1"; Log = "egx_bot_task.log" },
    @{ Name = "EGX Automation Runner"; Script = "run_automation.ps1"; Log = "egx_automation_task.log" }
)

foreach ($Task in $Tasks) {
    $ScriptPath = Join-Path $ProjectPath $Task.Script
    $LogPath = Join-Path $LogDir $Task.Log
    if (-not (Test-Path $ScriptPath)) {
        Write-Warning "Missing script: $ScriptPath"
        continue
    }

    $Command = "Set-Location -Path '$ProjectPath'; & '$ScriptPath' *> '$LogPath'"
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$Command`"" -WorkingDirectory $ProjectPath
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

    Register-ScheduledTask -TaskName $Task.Name -Action $Action -Trigger $Trigger -Settings $Settings -Description "EGX stock analysis project service." -Force | Out-Null
    Write-Host "Installed scheduled task: $($Task.Name)"
}

Write-Host "Done. Tasks start at user login and write logs to $LogDir."

