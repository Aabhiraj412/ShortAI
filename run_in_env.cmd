@echo off
set BASEDIR=D:\AI Arts\AI
set UV_CACHE_DIR=%BASEDIR%\cache\uv
set NPM_CONFIG_CACHE=%BASEDIR%\cache\npm
set TEMP=%BASEDIR%\cache\temp
set TMP=%BASEDIR%\cache\temp
set PYTHONPATH=%BASEDIR%\backend
set PATH=%BASEDIR%\python-env\Scripts;%BASEDIR%\tools\nodejs;%BASEDIR%\tools\ffmpeg\bin;%BASEDIR%\tools\uv;%BASEDIR%\tools\python\cpython-3.11.11-windows-x86_64-none;%PATH%

if "%~1"=="" (
    cmd /k
) else (
    %*
)
