# WINDOWS AUTOMATIC STARTUP GUIDE — STAGE 3.20
**Project:** CrazzyDocs Autonomous Production Studio (`@crazzydocs` | Channel ID: `UCUOZOEBngXgt6W7fmus0bvw`)  
**Canonical Path:** `D:\Agents\CrazzyDocs`  
**Execution Python:** `D:\Agents\CrazzyDocs\.venv\Scripts\pythonw.exe`  

---

## 1. Overview & Architecture

CrazzyDocs runs autonomously 24/7 without requiring manual terminal supervision. Upon Windows startup and user login, two decoupled services launch automatically in the background:

1. **`CrazzyDocs-Production-Scheduler`**
   - **Purpose:** Executes the Asia/Kolkata 4-cycle autonomous publishing schedule (00:00, 06:00, 12:00, 18:00 IST).
   - **Executable:** `D:\Agents\CrazzyDocs\.venv\Scripts\pythonw.exe`
   - **Target Script:** `D:\Agents\CrazzyDocs\backend\scripts\run_scheduler_daemon.py`
   - **Safety / Invariants:** Enforces single-instance execution via lockfile (`scheduler.lock`), maintains a 60-second active heartbeat, automatically reconciles missed cycles upon reboot, and logs to `logs/scheduler_daemon.log`.

2. **`CrazzyDocs-Health-Dashboard`**
   - **Purpose:** Starts the FastAPI Uvicorn ASGI server and automatically launches the dashboard in the default browser.
   - **Executable:** `D:\Agents\CrazzyDocs\.venv\Scripts\pythonw.exe`
   - **Target Script:** `D:\Agents\CrazzyDocs\backend\scripts\run_dashboard.py --open-browser`
   - **Safety / Invariants:** Includes port collision detection (if port 8000 is already active, simply opens browser and exits cleanly), logs to `logs/dashboard_service.log`, and operates completely independently of the scheduler.

---

## 2. Decoupled Resilience Invariant

| Failure Scenario | Impact on Scheduler | Impact on Dashboard | Recovery Action |
| :--- | :--- | :--- | :--- |
| **Browser Closed** | None (Scheduler runs in background) | None (Server continues listening on 8000) | Reopen browser to `http://127.0.0.1:8000/dashboard` |
| **Dashboard Crashed** | None (Scheduler independent process) | Server stops | Task Scheduler restarts task (configured up to 3 restarts) |
| **Scheduler Stopped** | Daemon exits; lock released | None (Dashboard displays `Scheduler: OFFLINE`) | Task Scheduler restarts daemon; daemon acquires lock and reconciles |
| **Workstation Reboot** | Restarts at user logon; runs missed cycle audit | Restarts at user logon; pops browser dashboard | Automatic via Task Scheduler triggers |

---

## 3. Management Commands

### 3.1 Via PowerShell Manager Script
```powershell
# Check current task status
powershell -ExecutionPolicy Bypass -File "D:\Agents\CrazzyDocs\backend\scripts\setup_windows_startup_tasks.ps1" -Action Status

# Re-register tasks with latest settings
powershell -ExecutionPolicy Bypass -File "D:\Agents\CrazzyDocs\backend\scripts\setup_windows_startup_tasks.ps1" -Action Register

# Start both tasks manually
powershell -ExecutionPolicy Bypass -File "D:\Agents\CrazzyDocs\backend\scripts\setup_windows_startup_tasks.ps1" -Action Start

# Stop both running tasks
powershell -ExecutionPolicy Bypass -File "D:\Agents\CrazzyDocs\backend\scripts\setup_windows_startup_tasks.ps1" -Action Stop

# Unregister tasks (if decommissioning)
powershell -ExecutionPolicy Bypass -File "D:\Agents\CrazzyDocs\backend\scripts\setup_windows_startup_tasks.ps1" -Action Unregister
```

### 3.2 Via Python CLI
```bash
# Check status
.\.venv\Scripts\python.exe backend/scripts/setup_windows_startup.py Status

# Re-register
.\.venv\Scripts\python.exe backend/scripts/setup_windows_startup.py Register

# Start
.\.venv\Scripts\python.exe backend/scripts/setup_windows_startup.py Start
```

---

## 4. Task Scheduler Configuration Settings

Both tasks are configured with high-reliability Windows Task Scheduler parameters:
- **Trigger:** `AtLogOn` (`-User $env:USERNAME`)
- **AllowStartIfOnBatteries:** `True` (prevents failure on laptops / power transitions)
- **DontStopIfGoingOnBatteries:** `True`
- **ExecutionTimeLimit:** `0` (Infinite / No timeout kill)
- **RestartCount:** `3` attempts
- **RestartInterval:** `1 Minute`
- **StartWhenAvailable:** `True` (runs immediately if scheduled time was missed while machine was powered off)

---

## 5. Verification Checklist

1. Open PowerShell and run:
   ```powershell
   Get-ScheduledTask -TaskName "CrazzyDocs*"
   ```
   Both tasks should be displayed with `State: Ready` or `State: Running`.
2. Inspect log files:
   - `D:\Agents\CrazzyDocs\logs\scheduler_daemon.log`
   - `D:\Agents\CrazzyDocs\logs\dashboard_service.log`
3. Verify process manager:
   - Two `pythonw.exe` processes active under user context.
