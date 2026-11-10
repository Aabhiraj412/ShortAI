<#
.SYNOPSIS
    Uninstalls the Windows Task Scheduler task for Shorts Studio.
#>
$taskName = "AIShortsStudio_DailyProduction"
Write-Host "Unregistering task '$taskName'..." -ForegroundColor Cyan

$res = & schtasks.exe /Delete /TN $taskName /F 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[SUCCESS] Task '$taskName' removed." -ForegroundColor Green
} else {
    Write-Host "[INFO] $res"
}
