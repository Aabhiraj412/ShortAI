<#
.SYNOPSIS
    Starts the unified AI YouTube Shorts Studio server (FastAPI + React Dashboard).
#>
$ErrorActionPreference = "Stop"
$baseDir = "D:\Agents\CrazzyDocs"
Set-Location $baseDir

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "      STARTING AI-POWERED YOUTUBE SHORTS STUDIO" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Dashboard URL: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Hardware Target: AMD Ryzen 7 3700U (CPU-optimized ~25.5 FPS)" -ForegroundColor Yellow
Write-Host "Scheduler: 10:00 AM IST Daily Automated Publication" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan

$env:UV_CACHE_DIR = "$baseDir\cache\uv"
$env:NPM_CONFIG_CACHE = "$baseDir\cache\npm"
$env:TEMP = "$baseDir\cache\temp"
$env:TMP = "$baseDir\cache\temp"
$env:PYTHONPATH = "$baseDir\backend"
$env:PATH = "$baseDir\.venv\Scripts;$baseDir\tools\nodejs;$baseDir\tools\ffmpeg\bin;$baseDir\tools\uv;$baseDir\tools\python;$env:PATH"

& "$baseDir\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
