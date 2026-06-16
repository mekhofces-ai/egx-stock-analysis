cd "C:\Users\omar.mokhtar\Documents\New project"

Write-Host "Watching automation log..." -ForegroundColor Cyan
Get-Content .\data\automation_safe.log -Wait -Tail 80
