param(
    [int]$DurationSeconds = 120,
    [string]$OutputFile = "D:\Agents\CrazzyDocs\logs\process_creation_trace.log"
)

$outDir = Split-Path -Parent $OutputFile
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

$header = "=== PROCESS CREATION TRACE START: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff') (Duration: ${DurationSeconds}s) ==="
Write-Host $header
$header | Out-File -FilePath $OutputFile -Encoding utf8

$query = "SELECT * FROM __InstanceCreationEvent WITHIN 1 WHERE TargetInstance ISA 'Win32_Process'"
$watcher = New-Object System.Management.ManagementEventWatcher($query)
$opt = New-Object System.Management.EventWatcherOptions
$opt.Timeout = [TimeSpan]::FromSeconds(2)
$watcher.Options = $opt

$startTime = [DateTime]::UtcNow
$endTime = $startTime.AddSeconds($DurationSeconds)

Write-Host "Monitoring process creation events for $DurationSeconds seconds... Logging to $OutputFile"

try {
    while ([DateTime]::UtcNow -lt $endTime) {
        try {
            $e = $watcher.WaitForNextEvent()
            $target = $e.TargetInstance
            $procId = $target.ProcessId
            $parentProcId = $target.ParentProcessId
            $procName = $target.Name
            $cmdLine = $target.CommandLine
            $exePath = $target.ExecutablePath
            $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")
            
            $parentName = "UNKNOWN"
            try {
                $p = Get-Process -Id $parentProcId -ErrorAction SilentlyContinue
                if ($p) { $parentName = $p.ProcessName }
            } catch {}

            $line = "[$timestamp] PID=$procId | PPID=$parentProcId ($parentName) | Name=$procName | Exe=$exePath | Cmd=$cmdLine"
            Write-Host $line
            $line | Out-File -FilePath $OutputFile -Append -Encoding utf8
        } catch [System.Management.ManagementException] {
            # Expected timeout waiting for events within 2 seconds
        } catch {
            $errLine = "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff'))] ERROR: $($_.Exception.Message)"
            Write-Host $errLine
            $errLine | Out-File -FilePath $OutputFile -Append -Encoding utf8
        }
    }
} finally {
    $watcher.Stop()
    $watcher.Dispose()
    $footer = "=== PROCESS CREATION TRACE END: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff') ==="
    Write-Host $footer
    $footer | Out-File -FilePath $OutputFile -Append -Encoding utf8
    Write-Host "Trace complete."
}
