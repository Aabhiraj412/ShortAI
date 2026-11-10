# Session Environment Configuration for AI Studio
# Dot-source this in any PowerShell terminal: . .\env.ps1

$baseDir = "D:\AI Arts\AI"
$toolsDir = Join-Path $baseDir "tools"
$pyBase = Join-Path $toolsDir "python\cpython-3.11.11-windows-x86_64-none"

$pathsToAdd = @(
    (Join-Path $baseDir "python-env\Scripts"),
    $pyBase,
    (Join-Path $pyBase "Scripts"),
    (Join-Path $toolsDir "uv"),
    (Join-Path $toolsDir "nodejs"),
    (Join-Path $toolsDir "ffmpeg\bin")
)

$currentPath = $env:PATH -split ';'
foreach ($p in $pathsToAdd) {
    if ((Test-Path $p) -and ($currentPath -notcontains $p)) {
        $env:PATH = "$p;$env:PATH"
    }
}

$env:PYTHONPATH = "$baseDir\backend"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $toolsDir "python"
$env:PATH
Write-Host "AI Studio Session Environment Activated." -ForegroundColor Green
