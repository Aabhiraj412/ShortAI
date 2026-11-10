<#
.SYNOPSIS
    Starts the FastAPI backend and unified React Dashboard server.
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
Write-Host "Starting Studio Backend on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
& $pyExe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
