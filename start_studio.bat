@echo off
title AI YouTube Shorts Studio
set BASEDIR=D:\Agents\CrazzyDocs
cd /d "%BASEDIR%"

echo ============================================================
echo       STARTING AI-POWERED YOUTUBE SHORTS STUDIO
echo ============================================================
echo Hardware Target: AMD Ryzen 7 3700U / 16 GB RAM
echo Production Resolution: 1080x1920 @ 30 FPS (9:16 Vertical)
echo Dashboard URL: http://127.0.0.1:8000
echo ============================================================

set UV_CACHE_DIR=%BASEDIR%\cache\uv
set NPM_CONFIG_CACHE=%BASEDIR%\cache\npm
set TEMP=%BASEDIR%\cache\temp
set TMP=%BASEDIR%\cache\temp
set PYTHONPATH=%BASEDIR%\backend
set PATH=%BASEDIR%\.venv\Scripts;%BASEDIR%\tools\nodejs;%BASEDIR%\tools\ffmpeg\bin;%BASEDIR%\tools\uv;%BASEDIR%\tools\python;%PATH%

"%BASEDIR%\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
