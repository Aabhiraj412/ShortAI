<#
.SYNOPSIS
    Runs a script or command with all isolated portable tools and caches configured.
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Command
)

$baseDir = "D:\Agents\CrazzyDocs"
$env:UV_CACHE_DIR = "$baseDir\cache\uv"
$env:NPM_CONFIG_CACHE = "$baseDir\cache\npm"
$env:TEMP = "$baseDir\cache\temp"
$env:TMP = "$baseDir\cache\temp"
$env:PYTHONPATH = "$baseDir\backend"
$env:PATH = "$baseDir\.venv\Scripts;$baseDir\tools\nodejs;$baseDir\tools\ffmpeg\bin;$baseDir\tools\uv;$baseDir\tools\python\cpython-3.11.11-windows-x86_64-none;$env:PATH"

if ($Command) {
    $cmdStr = $Command -join " "
    Invoke-Expression $cmdStr
} else {
    Write-Host "AI Studio Environment Ready." -ForegroundColor Cyan
}
