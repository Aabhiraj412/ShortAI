<#
.SYNOPSIS
    Continuous background worker daemon for job processing and recovery.
#>
$baseDir = "D:\AI Arts\AI"
Set-Location $baseDir

$env:UV_CACHE_DIR = "$baseDir\cache\uv"
$env:NPM_CONFIG_CACHE = "$baseDir\cache\npm"
$env:TEMP = "$baseDir\cache\temp"
$env:TMP = "$baseDir\cache\temp"
$env:PYTHONPATH = "$baseDir\backend"
$env:PATH = "$baseDir\python-env\Scripts;$baseDir\tools\nodejs;$baseDir\tools\ffmpeg\bin;$baseDir\tools\uv;$baseDir\tools\python;$env:PATH"

$pyExe = "$baseDir\tools\python\python.exe"

$workerCmd = @"
import asyncio
import time
from app.workers.job_manager import job_manager
from app.database.db import SessionLocal, JobModel

async def worker_loop():
    print('[WORKER] Persistent Job Queue Worker started.')
    while True:
        try:
            # 1. Recover stale jobs
            recovered = job_manager.recover_stale_jobs()
            if recovered:
                print(f'[WORKER] Recovered stale jobs: {recovered}')
                
            # 2. Check for PENDING or RETRYING jobs
            with SessionLocal() as s:
                pending = s.query(JobModel).filter(JobModel.status.in_(['PENDING', 'RETRYING'])).order_by(JobModel.created_at.asc()).first()
                target_id = pending.id if pending else None
                
            if target_id:
                print(f'[WORKER] Found pending job: {target_id}. Executing...')
                await job_manager.run_job(target_id)
            else:
                await asyncio.sleep(10)
        except Exception as e:
            print(f'[WORKER ERROR] {e}')
            await asyncio.sleep(10)

asyncio.run(worker_loop())
"@

& $pyExe -c $workerCmd
