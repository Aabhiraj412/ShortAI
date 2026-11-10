<#
.SYNOPSIS
    Scans for interrupted or stalled jobs and resets them for safe resumption.
#>
$baseDir = "D:\Agents\CrazzyDocs"
Set-Location $baseDir

$env:PYTHONPATH = "$baseDir\backend"
$pyExe = "$baseDir\tools\python\python.exe"

$recCmd = @"
from app.workers.job_manager import job_manager

print('[MANUAL RECOVERY] Checking for interrupted or stalled jobs...')
recovered = job_manager.recover_stale_jobs()
if recovered:
    print(f'[SUCCESS] Recovered {len(recovered)} job(s) for resumption: {recovered}')
else:
    print('[OK] No stale or hung jobs detected. All jobs are healthy.')
"@

& $pyExe -c $recCmd
