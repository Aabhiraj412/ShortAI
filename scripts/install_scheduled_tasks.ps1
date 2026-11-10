<#
.SYNOPSIS
    Installs the Windows Task Scheduler task for daily production at 11:00 PM IST.
#>
$taskName = "AIShortsStudio_DailyProduction"
$scriptPath = "D:\AI Arts\AI\scripts\start_daily_pipeline.ps1"
$actionCmd = "powershell.exe"
$actionArgs = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   REGISTERING DAILY SHORTS STUDIO TASK IN WINDOWS SCHEDULER" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Task Name: $taskName"
Write-Host "Trigger: Daily at 23:00 (11:00 PM IST)"
Write-Host "Script: $scriptPath"

# Use schtasks.exe for universal Windows 11 compatibility
$schArgs = @(
    "/Create",
    "/TN", $taskName,
    "/TR", "$actionCmd $actionArgs",
    "/SC", "DAILY",
    "/ST", "23:00",
    "/F"
)

$res = & schtasks.exe @schArgs 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[SUCCESS] Task '$taskName' successfully registered in Windows Task Scheduler!" -ForegroundColor Green
    Write-Host "To verify: schtasks.exe /Query /TN `"$taskName`"" -ForegroundColor Yellow
} else {
    Write-Host "`n[WARNING] Registration output: $res" -ForegroundColor Yellow
    Write-Host "Note: Administrator privileges may be required to register system-level tasks." -ForegroundColor Yellow
}
