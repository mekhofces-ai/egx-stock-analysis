$TaskNames = @(
    "EGX Streamlit Dashboard",
    "EGX Telegram Bot Service",
    "EGX Automation Runner"
)

foreach ($TaskName in $TaskNames) {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($Task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task: $TaskName"
    }
    else {
        Write-Host "Task not found: $TaskName"
    }
}

