# Windows Task Scheduler & Automation Setup Guide

**Target Window:** Daily Production at 11:00 PM IST (Asia/Kolkata)  
**Publication Time:** 10:00 AM IST (Asia/Kolkata)  
**Execution Environment:** `D:\AI Arts\AI\`  

---

## 1. Automatic Task Registration

To register the daily 11:00 PM IST production task in Windows Task Scheduler:

Open PowerShell as Administrator and run:
```powershell
powershell -ExecutionPolicy Bypass -File "D:\AI Arts\AI\scripts\install_scheduled_tasks.ps1"
```

This registers a daily task named:
`AIShortsStudio_DailyProduction`

Trigger time: **23:00 (11:00 PM IST)** every day.

---

## 2. Manual Task Verification

To check whether the task is registered:
```powershell
schtasks.exe /Query /TN "AIShortsStudio_DailyProduction" /V /FO LIST
```

To run the pipeline immediately on demand:
```powershell
powershell -ExecutionPolicy Bypass -File "D:\AI Arts\AI\scripts\start_daily_pipeline.ps1"
```

To view live job queue status:
```powershell
powershell -ExecutionPolicy Bypass -File "D:\AI Arts\AI\scripts\check_pipeline_status.ps1"
```

To recover interrupted or stalled jobs:
```powershell
powershell -ExecutionPolicy Bypass -File "D:\AI Arts\AI\scripts\recover_interrupted_jobs.ps1"
```

To remove the scheduled task:
```powershell
powershell -ExecutionPolicy Bypass -File "D:\AI Arts\AI\scripts\uninstall_scheduled_tasks.ps1"
```

---

## 3. Power & Sleep Configuration

Because production takes place between 11:00 PM and 3:00 AM:
1. Ensure the laptop is **plugged in to AC power**.
2. In Windows Settings > System > Power & battery:
   - When plugged in, turn off screen after: **30 minutes**.
   - When plugged in, put my device to sleep after: **Never** (or at least 4 hours).
3. The Task Scheduler task is configured with `ExecutionTimeLimit: 0` (completion-driven, allowing full multi-minute renders to finish cleanly without arbitrary termination).
