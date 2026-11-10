# Setup portable tools inside D:\AI Arts\AI\tools

$ErrorActionPreference = "Stop"
$toolsDir = "D:\AI Arts\AI\tools"
$downloadsDir = Join-Path $toolsDir "downloads"

if (-not (Test-Path $downloadsDir)) {
    New-Item -ItemType Directory -Path $downloadsDir -Force | Out-Null
}

# 1. Download and extract UV (Fast, standalone Python manager)
$uvDir = Join-Path $toolsDir "uv"
$uvExe = Join-Path $uvDir "uv.exe"
if (-not (Test-Path $uvExe)) {
    Write-Host "Downloading UV..."
    $uvZip = Join-Path $downloadsDir "uv.zip"
    $uvUrl = "https://github.com/astral-sh/uv/releases/download/0.5.11/uv-x86_64-pc-windows-msvc.zip"
    Invoke-WebRequest -Uri $uvUrl -OutFile $uvZip -UseBasicParsing
    Expand-Archive -Path $uvZip -DestinationPath $uvDir -Force
    Remove-Item $uvZip -Force -ErrorAction SilentlyContinue
    Write-Host "UV installed at $uvExe"
}

# 2. Use UV to install local Python into D:\AI Arts\AI\tools\python
$pythonDir = "D:\AI Arts\AI\tools\python"
$pythonExe = Join-Path $pythonDir "python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Host "Installing portable Python 3.11 via UV..."
    $env:UV_PYTHON_INSTALL_DIR = $pythonDir
    & $uvExe python install 3.11 --install-dir $pythonDir
    # Locate installed python.exe inside the install dir
    $foundPython = Get-ChildItem -Path $pythonDir -Filter "python.exe" -Recurse | Select-Object -First 1
    if ($foundPython) {
        Write-Host "Python installed at: $($foundPython.FullName)"
    }
}

# 3. Download and extract Node.js LTS portable
$nodeDir = Join-Path $toolsDir "nodejs"
$nodeExe = Join-Path $nodeDir "node.exe"
if (-not (Test-Path $nodeExe)) {
    Write-Host "Downloading Node.js LTS portable..."
    $nodeZip = Join-Path $downloadsDir "node.zip"
    $nodeUrl = "https://nodejs.org/dist/v20.18.0/node-v20.18.0-win-x64.zip"
    Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeZip -UseBasicParsing
    
    $tempNodeDir = Join-Path $downloadsDir "node_temp"
    if (Test-Path $tempNodeDir) { Remove-Item $tempNodeDir -Recurse -Force }
    Expand-Archive -Path $nodeZip -DestinationPath $tempNodeDir -Force
    $innerDir = Get-ChildItem -Path $tempNodeDir | Where-Object { $_.PSIsContainer } | Select-Object -First 1
    Move-Item -Path "$($innerDir.FullName)\*" -Destination $nodeDir -Force
    Remove-Item $tempNodeDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $nodeZip -Force -ErrorAction SilentlyContinue
    Write-Host "Node.js installed at $nodeExe"
}

# 4. Download and extract FFmpeg essentials
$ffmpegDir = Join-Path $toolsDir "ffmpeg"
$ffmpegExe = Join-Path $ffmpegDir "bin\ffmpeg.exe"
if (-not (Test-Path $ffmpegExe)) {
    Write-Host "Downloading FFmpeg portable..."
    $ffmpegZip = Join-Path $downloadsDir "ffmpeg.zip"
    $ffmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    Invoke-WebRequest -Uri $ffmpegUrl -OutFile $ffmpegZip -UseBasicParsing
    
    $tempFfmpegDir = Join-Path $downloadsDir "ffmpeg_temp"
    if (Test-Path $tempFfmpegDir) { Remove-Item $tempFfmpegDir -Recurse -Force }
    Expand-Archive -Path $ffmpegZip -DestinationPath $tempFfmpegDir -Force
    $innerFfmpeg = Get-ChildItem -Path $tempFfmpegDir | Where-Object { $_.PSIsContainer } | Select-Object -First 1
    Move-Item -Path "$($innerFfmpeg.FullName)\*" -Destination $ffmpegDir -Force
    Remove-Item $tempFfmpegDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $ffmpegZip -Force -ErrorAction SilentlyContinue
    Write-Host "FFmpeg installed at $ffmpegExe"
}

Write-Host "=== VERIFICATION ==="
if (Test-Path $uvExe) { & $uvExe --version }
$py = Get-ChildItem -Path $pythonDir -Filter "python.exe" -Recurse | Select-Object -First 1
if ($py) { & $py.FullName --version }
if (Test-Path $nodeExe) { & $nodeExe --version }
if (Test-Path $ffmpegExe) { & $ffmpegExe -version | Select-Object -First 1 }
Write-Host "Portable tools setup completed!"
