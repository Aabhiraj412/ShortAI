<#
.SYNOPSIS
    Installs the Windows Task Scheduler task for CrazzyDocs 4-cycle autonomous scheduler.
#>
$taskName = "CrazzyDocs_AutonomousScheduler"
$scriptPath = "D:\Agents\CrazzyDocs\backend\scripts\run_scheduler_daemon.py"
$pyExe = "D:\Agents\CrazzyDocs\.venv\Scripts\python.exe"
$actionArgs = "`"$scriptPath`""

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   REGISTERING CRAZZYDOCS 4-CYCLE SCHEDULER IN WINDOWS TASK SCHEDULER" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Task Name: $taskName"
Write-Host "Trigger: At System Startup / User Logon"
Write-Host "Executable: $pyExe"
Write-Host "Script: $scriptPath"

# Register task to start on logon for user session context
$schArgs = @(
    "/Create",
    "/TN", $taskName,
    "/TR", "`"$pyExe`" $actionArgs",
    "/SC", "ONLOGON",
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
