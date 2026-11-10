<#
.SYNOPSIS
    Checks the status of persistent production jobs and system health.
#>
$baseDir = "D:\AI Arts\AI"
Set-Location $baseDir

$env:PYTHONPATH = "$baseDir\backend"
$pyExe = "$baseDir\tools\python\python.exe"

$queryCmd = @"
import json
from app.database.db import SessionLocal, JobModel

with SessionLocal() as s:
    jobs = s.query(JobModel).order_by(JobModel.created_at.desc()).limit(10).all()
    print('=' * 85)
    print(f'{\"JOB ID\":<28} {\"DATE\":<12} {\"STATUS\":<12} {\"STAGE\":<18} {\"PROGRESS\":<10}')
    print('=' * 85)
    for j in jobs:
        print(f'{j.id:<28} {j.date_str:<12} {j.status:<12} {j.current_stage:<18} {j.progress_pct:5.1f}%')
        if j.error_details:
            print(f'   -> Error: {j.error_details[:100]}')
    print('=' * 85)
"@

& $pyExe -c $queryCmd
