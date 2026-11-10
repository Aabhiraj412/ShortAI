<#
.SYNOPSIS
    Daily Autonomous Production Pipeline Runner.
    Triggered daily at 11:00 PM IST by Windows Task Scheduler or manually.
#>
param(
    [string]$DateStr = (Get-Date -Format "yyyy-MM-dd"),
    [string]$ManualTopic = ""
)

$baseDir = "D:\AI Arts\AI"
Set-Location $baseDir

$logFile = "$baseDir\logs\daily_pipeline_$DateStr.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$ts] DAILY PIPELINE TRIGGERED for date $DateStr" | Out-File -FilePath $logFile -Append

$env:UV_CACHE_DIR = "$baseDir\cache\uv"
$env:NPM_CONFIG_CACHE = "$baseDir\cache\npm"
$env:TEMP = "$baseDir\cache\temp"
$env:TMP = "$baseDir\cache\temp"
$env:PYTHONPATH = "$baseDir\backend"
$env:PATH = "$baseDir\python-env\Scripts;$baseDir\tools\nodejs;$baseDir\tools\ffmpeg\bin;$baseDir\tools\uv;$baseDir\tools\python;$env:PATH"

$pyExe = "$baseDir\tools\python\python.exe"

$scriptCmd = @"
import asyncio
import os
import sys
from app.workers.job_manager import job_manager

async def run():
    job, is_new = job_manager.get_or_create_daily_job('$DateStr')
    topic_arg = '$ManualTopic' if '$ManualTopic' else None
    result = await job_manager.run_job(job.id, manual_topic=topic_arg)
    print(f'[PIPELINE FINISHED] Status: {result.get(\"status\")}')

asyncio.run(run())
"@

& $pyExe -c $scriptCmd 2>&1 | Tee-Object -FilePath $logFile -Append
$endTs = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$endTs] DAILY PIPELINE RUN COMPLETED." | Out-File -FilePath $logFile -Append
