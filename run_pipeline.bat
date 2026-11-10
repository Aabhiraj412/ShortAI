@echo off
title Run Autonomous Shorts Pipeline
set BASEDIR=D:\Agents\CrazzyDocs
cd /d "%BASEDIR%"

echo Running autonomous Shorts production pipeline...
set UV_CACHE_DIR=%BASEDIR%\cache\uv
set NPM_CONFIG_CACHE=%BASEDIR%\cache\npm
set TEMP=%BASEDIR%\cache\temp
set TMP=%BASEDIR%\cache\temp
set PYTHONPATH=%BASEDIR%\backend
set PATH=%BASEDIR%\.venv\Scripts;%BASEDIR%\tools\nodejs;%BASEDIR%\tools\ffmpeg\bin;%BASEDIR%\tools\uv;%BASEDIR%\tools\python;%PATH%

"%BASEDIR%\tools\python\python.exe" -c "
import asyncio
from app.workers.pipeline import pipeline
asyncio.run(pipeline.run_pipeline())
"
pause
