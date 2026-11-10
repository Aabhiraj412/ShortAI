@echo off
set BASEDIR=D:\Agents\CrazzyDocs
set UV_CACHE_DIR=%BASEDIR%\cache\uv
set NPM_CONFIG_CACHE=%BASEDIR%\cache\npm
set TEMP=%BASEDIR%\cache\temp
set TMP=%BASEDIR%\cache\temp
set PYTHONPATH=%BASEDIR%\backend
set PATH=%BASEDIR%\.venv\Scripts;%BASEDIR%\tools\nodejs;%BASEDIR%\tools\ffmpeg\bin;%BASEDIR%\tools\uv;%BASEDIR%\tools\python;%PATH%

if "%~1"=="" (
    cmd /k
) else (
    %*
)
