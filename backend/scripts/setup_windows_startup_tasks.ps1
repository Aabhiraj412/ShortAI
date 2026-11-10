# ==============================================================================
# CrazzyDocs - Windows Automatic Startup Tasks Setup & Manager
# Configures persistent Windows Task Scheduler tasks for:
#   1. CrazzyDocs-Production-Scheduler (Autonomous 4-Cycle Production Engine)
#   2. CrazzyDocs-Health-Dashboard     (FastAPI Server & Browser Dashboard Auto-Launch)
# ==============================================================================

param(
    [ValidateSet("Register", "Unregister", "Start", "Stop", "Status", "Enable-Scheduler", "Disable-Scheduler")]
    [string]$Action = "Register"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = "D:\Agents\CrazzyDocs"
$VenvPythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$VenvPython  = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPythonw)) {
    $PythonExe = $VenvPython
} else {
    $PythonExe = $VenvPythonw
}

$SchedulerScript = Join-Path $ProjectRoot "backend\scripts\run_scheduler_daemon.py"
$DashboardScript = Join-Path $ProjectRoot "backend\scripts\run_dashboard.py"

$SchedulerTaskName = "CrazzyDocs-Production-Scheduler"
$DashboardTaskName = "CrazzyDocs-Health-Dashboard"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "CRAZZYDOCS WINDOWS STARTUP TASKS MANAGER - Action: $Action" -ForegroundColor Cyan
Write-Host "Project Root: $ProjectRoot"
Write-Host "Python Executable: $PythonExe"
Write-Host "=========================================================="

function Get-TaskStatus([string]$TaskName) {
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
            return @{
                Found = $true
                State = $task.State.ToString()
                LastRunTime = $info.LastRunTime
                LastTaskResult = $info.LastTaskResult
                NextRunTime = $info.NextRunTime
            }
        }
    } catch {
        # ignore
    }
    return @{ Found = $false; State = "NotFound" }
}

if ($Action -eq "Status") {
    Write-Host "`n[TASK STATUS CHECK]" -ForegroundColor Yellow
    foreach ($name in @($SchedulerTaskName, $DashboardTaskName)) {
        $st = Get-TaskStatus $name
        if ($st.Found) {
            Write-Host "Task: $name" -ForegroundColor Green
            Write-Host "  State:           $($st.State)"
            Write-Host "  Last Run:        $($st.LastRunTime)"
            Write-Host "  Last Result:     $($st.LastTaskResult)"
            Write-Host "  Next Scheduled:  $($st.NextRunTime)"
        } else {
            Write-Host "Task: $name -> NOT REGISTERED" -ForegroundColor Red
        }
    }
    exit 0
}

if ($Action -eq "Stop") {
    Write-Host "`n[STOPPING ACTIVE TASKS]" -ForegroundColor Yellow
    foreach ($name in @($SchedulerTaskName, $DashboardTaskName)) {
        try {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Write-Host "Stopped task: $name" -ForegroundColor Green
        } catch {
            Write-Host "Failed to stop task $name : $_" -ForegroundColor DarkGray
        }
    }
    exit 0
}

if ($Action -eq "Disable-Scheduler") {
    Write-Host "`n[DISABLING PRODUCTION SCHEDULER TASK (SAFETY GUARD)]" -ForegroundColor Yellow
    try {
        Disable-ScheduledTask -TaskName $SchedulerTaskName | Out-Null
        Write-Host "Task '$SchedulerTaskName' has been DISABLED. It will NOT run on logon until re-enabled." -ForegroundColor Green
    } catch {
        Write-Host "Failed to disable task $SchedulerTaskName : $_" -ForegroundColor Red
    }
    exit 0
}

if ($Action -eq "Enable-Scheduler") {
    Write-Host "`n[ENABLING PRODUCTION SCHEDULER TASK]" -ForegroundColor Yellow
    try {
        Enable-ScheduledTask -TaskName $SchedulerTaskName | Out-Null
        Write-Host "Task '$SchedulerTaskName' has been ENABLED. Automatic logon execution is active." -ForegroundColor Green
    } catch {
        Write-Host "Failed to enable task $SchedulerTaskName : $_" -ForegroundColor Red
    }
    exit 0
}

if ($Action -eq "Unregister") {
    Write-Host "`n[UNREGISTERING SCHEDULED TASKS]" -ForegroundColor Yellow
    foreach ($name in @($SchedulerTaskName, $DashboardTaskName)) {
        try {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
            Write-Host "Unregistered task: $name" -ForegroundColor Green
        } catch {
            Write-Host "Task $name was not registered or could not be removed: $_" -ForegroundColor DarkGray
        }
    }
    exit 0
}

if ($Action -eq "Register") {
    Write-Host "`n[REGISTERING SCHEDULED TASKS]" -ForegroundColor Yellow

    # Define Settings: don't stop on battery, restart on failure, indefinite execution limit
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable

    # 1. Register Scheduler Daemon
    $schedulerAction = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument "`"$SchedulerScript`"" `
        -WorkingDirectory $ProjectRoot

    $schedulerTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

    try {
        Register-ScheduledTask `
            -TaskName $SchedulerTaskName `
            -Action $schedulerAction `
            -Trigger $schedulerTrigger `
            -Settings $settings `
            -Description "CrazzyDocs Autonomous Production Scheduler (Asia/Kolkata 4-Cycle)" `
            -Force | Out-Null
        Write-Host "Registered Task 1: $SchedulerTaskName [OK]" -ForegroundColor Green
    } catch {
        Write-Host "Failed to register $SchedulerTaskName : $_" -ForegroundColor Red
    }

    # 2. Register Health Dashboard Server (with --open-browser)
    $dashboardAction = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument "`"$DashboardScript`" --open-browser" `
        -WorkingDirectory $ProjectRoot

    $dashboardTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

    try {
        Register-ScheduledTask `
            -TaskName $DashboardTaskName `
            -Action $dashboardAction `
            -Trigger $dashboardTrigger `
            -Settings $settings `
            -Description "CrazzyDocs Health Dashboard Server and Browser Auto-Launch" `
            -Force | Out-Null
        Write-Host "Registered Task 2: $DashboardTaskName [OK]" -ForegroundColor Green
    } catch {
        Write-Host "Failed to register $DashboardTaskName : $_" -ForegroundColor Red
    }

    # Verify registration
    Write-Host "`n[VERIFYING REGISTRATION]" -ForegroundColor Yellow
    foreach ($name in @($SchedulerTaskName, $DashboardTaskName)) {
        $st = Get-TaskStatus $name
        if ($st.Found) {
            Write-Host "Task '$name' confirmed in Task Scheduler. State: $($st.State)" -ForegroundColor Green
        } else {
            Write-Host "WARNING: Task '$name' could not be verified." -ForegroundColor Red
        }
    }
}

if ($Action -eq "Start") {
    Write-Host "`n[STARTING SCHEDULED TASKS NOW]" -ForegroundColor Yellow
    foreach ($name in @($SchedulerTaskName, $DashboardTaskName)) {
        try {
            Start-ScheduledTask -TaskName $name
            Write-Host "Triggered start for task: $name" -ForegroundColor Green
        } catch {
            Write-Host "Failed to start task $name : $_" -ForegroundColor Red
        }
    }
    Start-Sleep -Seconds 3
    foreach ($name in @($SchedulerTaskName, $DashboardTaskName)) {
        $st = Get-TaskStatus $name
        Write-Host "Task '$name' Current State: $($st.State)" -ForegroundColor Cyan
    }
}
