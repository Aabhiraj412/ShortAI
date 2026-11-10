# Complete Node.js and FFmpeg installation

$ErrorActionPreference = "Stop"
$toolsDir = "D:\AI Arts\AI\tools"
$downloadsDir = Join-Path $toolsDir "downloads"

# Node.js
$nodeDir = Join-Path $toolsDir "nodejs"
$nodeExe = Join-Path $nodeDir "node.exe"

if (-not (Test-Path $nodeExe)) {
    Write-Host "Extracting Node.js..."
    $nodeZip = Join-Path $downloadsDir "node.zip"
    $tempNodeDir = Join-Path $downloadsDir "node_temp"
    
    if (Test-Path $nodeDir) {
        Remove-Item $nodeDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    
    if (-not (Test-Path $nodeZip)) {
        Write-Host "Downloading Node.js zip..."
        Invoke-WebRequest -Uri "https://nodejs.org/dist/v20.18.0/node-v20.18.0-win-x64.zip" -OutFile $nodeZip -UseBasicParsing
    }
    
    if (Test-Path $tempNodeDir) { Remove-Item $tempNodeDir -Recurse -Force }
    Expand-Archive -Path $nodeZip -DestinationPath $tempNodeDir -Force
    
    $extracted = Get-ChildItem -Path $tempNodeDir | Where-Object { $_.PSIsContainer } | Select-Object -First 1
    Move-Item -Path $extracted.FullName -Destination $nodeDir -Force
    Remove-Item $tempNodeDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Node.js successfully installed at $nodeExe"
}

# FFmpeg
$ffmpegDir = Join-Path $toolsDir "ffmpeg"
$ffmpegExe = Join-Path $ffmpegDir "bin\ffmpeg.exe"

if (-not (Test-Path $ffmpegExe)) {
    Write-Host "Downloading FFmpeg..."
    $ffmpegZip = Join-Path $downloadsDir "ffmpeg.zip"
    $ffmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    
    if (-not (Test-Path $ffmpegZip)) {
        Invoke-WebRequest -Uri $ffmpegUrl -OutFile $ffmpegZip -UseBasicParsing
    }
    
    $tempFfmpegDir = Join-Path $downloadsDir "ffmpeg_temp"
    if (Test-Path $tempFfmpegDir) { Remove-Item $tempFfmpegDir -Recurse -Force }
    if (Test-Path $ffmpegDir) { Remove-Item $ffmpegDir -Recurse -Force }
    
    Expand-Archive -Path $ffmpegZip -DestinationPath $tempFfmpegDir -Force
    $innerFfmpeg = Get-ChildItem -Path $tempFfmpegDir | Where-Object { $_.PSIsContainer } | Select-Object -First 1
    Move-Item -Path $innerFfmpeg.FullName -Destination $ffmpegDir -Force
    Remove-Item $tempFfmpegDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "FFmpeg successfully installed at $ffmpegExe"
}

# Cleanup downloads
Remove-Item $downloadsDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "=== VERIFICATION ==="
$uvExe = Join-Path $toolsDir "uv\uv.exe"
if (Test-Path $uvExe) { & $uvExe --version }
$py = Get-ChildItem -Path (Join-Path $toolsDir "python") -Filter "python.exe" -Recurse | Select-Object -First 1
if ($py) { & $py.FullName --version }
if (Test-Path $nodeExe) { & $nodeExe --version }
if (Test-Path $ffmpegExe) { & $ffmpegExe -version | Select-Object -First 1 }
Write-Host "All portable runtimes verified!"
