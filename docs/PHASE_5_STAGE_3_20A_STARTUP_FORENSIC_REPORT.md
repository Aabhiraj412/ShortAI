# PHASE 5 — STAGE 3.20A STARTUP FORENSIC AUDIT REPORT
**Forensic Investigation: Task Scheduler Registration, Heartbeat State, and Browser Auto-Launch Discrepancies**
**Project:** CrazzyDocs Autonomous Production Studio (`@crazzydocs` | Channel ID: `UCUOZOEBngXgt6W7fmus0bvw`)  
**Canonical Directory:** `D:\Agents\CrazzyDocs`  
**Investigation Timestamp:** 2026-11-11T01:46:00+05:30  
**Status:** DIAGNOSTIC COMPLETE — ZERO PRODUCTION MUTATIONS  

---

## 1. Executive Summary & Diagnostic Findings

Following Stage 3.20, the production dashboard at `http://127.0.0.1:8000/dashboard` displayed:
- **Scheduler Heartbeat:** `IDLE`
- **Windows Task Scheduler:** `OPTED-OUT / NOT REGISTERED`
- **Pipeline:** `IDLE`

A rigorous, non-mutating forensic investigation was conducted across Windows Scheduled Tasks, active operating system processes, network sockets, application logs, and backend source code. 

### Key Findings:
1. **The Windows Scheduled Tasks ARE successfully registered in Windows Task Scheduler:**
   - Both `CrazzyDocs-Production-Scheduler` and `CrazzyDocs-Health-Dashboard` exist in Windows Task Scheduler, are enabled, and have `State: Ready`.
   - Native Windows `schtasks /Query /TN CrazzyDocs-Production-Scheduler /FO LIST` returns **Exit Code 0** (`Status: Ready`).
   - Native Windows `schtasks /Query /TN CrazzyDocs-Health-Dashboard /FO LIST` returns **Exit Code 0** (`Status: Ready`).
2. **Root Cause of "OPTED-OUT / NOT REGISTERED" on Dashboard:**
   - In `backend/app/services/production_health_dashboard.py`, `get_windows_task_status()` delegates to legacy Stage 3.10 utility `backend/scripts/manage_windows_startup.py`.
   - `manage_windows_startup.py` has `TASK_NAME = "CrazzyDocsStudioScheduler"` hardcoded (an obsolete task name from Stage 3.10).
   - Running `schtasks /Query /TN CrazzyDocsStudioScheduler` returns **Exit Code 1** (`ERROR: The system cannot find the file specified`).
   - In addition, `studio_settings` in SQLite has `AUTO_START_ON_WINDOWS_LOGIN = "false"` (reset by Stage 3.10 regression tests).
   - In `backend/app/templates/dashboard.html` (line 618), because `win.is_registered` is `false`, the UI displays: `'OPTED-OUT / NOT REGISTERED'`.
   - **Conclusion:** The dashboard was querying an obsolete, non-existent task name rather than the Stage 3.20 registered tasks.
3. **Root Cause of "Scheduler Heartbeat: IDLE":**
   - The scheduler daemon (`run_scheduler_daemon.py`) is **not running**.
   - The registered task `CrazzyDocs-Production-Scheduler` is configured with `MSFT_TaskLogonTrigger` (`-AtLogOn`).
   - Windows Task Scheduler triggers logon tasks **only when a user logs on to Windows** (system reboot or sign-out/sign-in).
   - In Stage 3.20, `-Action Register` was executed, which created the task in the Windows repository, but `-Action Start` was not called, nor has the machine rebooted.
   - `LastRunTime` is `30-11-1999 00:00:00` and `LastTaskResult` is `267011` (`0x41303` = `SCHED_S_TASK_HAS_NOT_RUN`).
   - Because the daemon is not running, `backend/studio_scheduler.lock` does not exist, and the heartbeat check correctly returns `state: IDLE`.
4. **Root Cause of "Pipeline: IDLE":**
   - Normal and correct behavior: No job in `JobModel` currently has `status == "RUNNING"`.
5. **Why Browser Was Not Automatically Opened:**
   - The process currently serving port 8000 is PID 17092 (`.\.venv\Scripts\python.exe backend\scripts\run_dashboard.py`), started manually at `11-11-2026 01:15:00` during earlier Stage 3.19/3.20 development, **before** `--open-browser` was added.
   - The scheduled task `CrazzyDocs-Health-Dashboard` (which contains `--open-browser`) has also never run (`LastRunTime: 30-11-1999 00:00:00`, `LastTaskResult: 267011`) because no logon event has occurred.
   - Neither the task nor any operator invoked `run_dashboard.py --open-browser` in the current session.

---

## 2. Section 1 — Windows Scheduled Tasks Audit

Forensic inspection was executed using PowerShell `Get-ScheduledTask` and `Get-ScheduledTaskInfo`.

### 2.1 Task Configuration Evidence Matrix

| Property | Task 1: `CrazzyDocs-Production-Scheduler` | Task 2: `CrazzyDocs-Health-Dashboard` |
| :--- | :--- | :--- |
| **Registration Status** | **REGISTERED** (Confirmed in Root Folder) | **REGISTERED** (Confirmed in Root Folder) |
| **Current State** | `Ready` | `Ready` |
| **LastRunTime** | `30-11-1999 00:00:00` (Sentinel: Never Run) | `30-11-1999 00:00:00` (Sentinel: Never Run) |
| **LastTaskResult** | `267011` (`0x41303` / `SCHED_S_TASK_HAS_NOT_RUN`) | `267011` (`0x41303` / `SCHED_S_TASK_HAS_NOT_RUN`) |
| **NextRunTime** | None (Trigger is event-based, not calendar) | None (Trigger is event-based, not calendar) |
| **NumberOfMissedRuns** | `0` | `0` |
| **Executable (`Execute`)** | `D:\Agents\CrazzyDocs\.venv\Scripts\pythonw.exe` | `D:\Agents\CrazzyDocs\.venv\Scripts\pythonw.exe` |
| **Arguments (`Arguments`)**| `"D:\Agents\CrazzyDocs\backend\scripts\run_scheduler_daemon.py"` | `"D:\Agents\CrazzyDocs\backend\scripts\run_dashboard.py" --open-browser` |
| **Working Directory** | `D:\Agents\CrazzyDocs` | `D:\Agents\CrazzyDocs` |
| **Trigger Type** | `MSFT_TaskLogonTrigger` (`AtLogOn`) | `MSFT_TaskLogonTrigger` (`AtLogOn`) |
| **Trigger User** | `GOD\aabhi` (Enabled: True) | `GOD\aabhi` (Enabled: True) |
| **Principal UserId** | `aabhi` | `aabhi` |
| **Principal LogonType**| `Interactive` | `Interactive` |
| **Principal RunLevel** | `Limited` | `Limited` |
| **Restart Settings** | `RestartCount: 3`, `RestartInterval: PT1M` | `RestartCount: 3`, `RestartInterval: PT1M` |
| **Execution Time Limit**| `PT0S` (Indefinite / Zero Timeout) | `PT0S` (Indefinite / Zero Timeout) |
| **Multiple Instances** | `IgnoreNew` | `IgnoreNew` |
| **StartWhenAvailable** | `True` | `True` |

### 2.2 Execution Verification
Both tasks report `LastRunTime: 30-11-1999 00:00:00` and `LastTaskResult: 267011`.  
**Conclusion:** Neither task has ever executed since its registration.

---

## 3. Section 2 — Running Processes & Port Audit

Forensic inspection was executed using `Get-Process`, `Win32_Process`, and `Get-NetTCPConnection`.

### 3.1 Active Python Processes

```text
Id    ProcessName Path                                          StartTime           CommandLine
--    ----------- ----                                          ---------           -----------
17092 python      D:\Agents\CrazzyDocs\.venv\Scripts\python.exe 11-11-2026 01:15:00 .\.venv\Scripts\python.exe backend\scripts\run_dashboard.py
```

- **`run_scheduler_daemon.py`:** **NOT RUNNING** (0 processes).
- **`run_dashboard.py`:** **RUNNING** under PID 17092.
- **`pythonw.exe`:** **NOT RUNNING** (0 processes).
- **Port 8000 Ownership:** Bound exclusively to PID 17092 in `Listen` state (`127.0.0.1:8000`).
- **Observation:** PID 17092 was started at 01:15:00 without `--open-browser` and before Stage 3.20 added file logging.

---

## 4. Section 3 — Application Logs Audit

Inspected `D:\Agents\CrazzyDocs\logs`:

1. `logs/scheduler_daemon.log`: **Does not exist.**
   - Root Cause: `run_scheduler_daemon.py` has never been launched by the OS.
2. `logs/dashboard_service.log`: **Does not exist.**
   - Root Cause: PID 17092 was booted under the legacy runner before file logging was introduced in Stage 3.20. Task `CrazzyDocs-Health-Dashboard` has never executed.

---

## 5. Section 4 — Dashboard Status Discrepancy Code Forensics

### 5.1 Call Chain Analysis for "Windows Task Scheduler: OPTED-OUT / NOT REGISTERED"

```
[dashboard.html (L617-619)]
       |
       |  Calls GET /api/dashboard/health
       v
[main.py (L267)]
       |  "windows_task": production_health_dashboard.get_windows_task_status()
       v
[production_health_dashboard.py (L267-274)]
       |  from backend.scripts.manage_windows_startup import get_task_status
       |  return get_task_status()
       v
[manage_windows_startup.py (L26, L64-83)]
          TASK_NAME = "CrazzyDocsStudioScheduler"   <-- OBSOLETE STAGE 3.10 TASK NAME!
          cmd = ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST"]
```

### 5.2 Forensic Verification Commands

```powershell
# Querying the legacy task name that the health-check queries:
schtasks /Query /TN CrazzyDocsStudioScheduler /FO LIST
# OUTPUT: ERROR: The system cannot find the file specified. (Exit Code 1) -> is_registered = False

# Querying the actual Stage 3.20 registered tasks:
schtasks /Query /TN CrazzyDocs-Production-Scheduler /FO LIST
# OUTPUT: HostName: GOD | TaskName: \CrazzyDocs-Production-Scheduler | Status: Ready (Exit Code 0)

schtasks /Query /TN CrazzyDocs-Health-Dashboard /FO LIST
# OUTPUT: HostName: GOD | TaskName: \CrazzyDocs-Health-Dashboard | Status: Ready (Exit Code 0)
```

In addition:
- `manage_windows_startup.py` calls `get_opt_in_setting()`, which queries `studio_settings` in SQLite for `AUTO_START_ON_WINDOWS_LOGIN`.
- `AUTO_START_ON_WINDOWS_LOGIN` was set to `"false"` by `test_windows_startup_task_settings()` in `backend/tests/test_stage_3_10_resilience.py`.
- In `dashboard.html` (line 618):
  ```javascript
  document.getElementById('healthWindowsTask').textContent = win.is_registered ? 'REGISTERED (ONLOGON)' : 'OPTED-OUT / NOT REGISTERED';
  ```
  Since `win.is_registered` is `false`, the UI displays `OPTED-OUT / NOT REGISTERED`.

### 5.3 Call Chain Analysis for "Scheduler Heartbeat: IDLE"
1. In `production_health_dashboard.py` (lines 71–75):
   ```python
   def _get_scheduler_heartbeat(self) -> Dict[str, Any]:
       if not os.path.exists(LOCK_PATH):
           return {"state": "IDLE", "is_running": False, "pid": None, "last_heartbeat": None}
   ```
2. `LOCK_PATH = D:\Agents\CrazzyDocs\backend\studio_scheduler.lock`.
3. `Test-Path "D:\Agents\CrazzyDocs\backend\studio_scheduler.lock"` returns `False`.
4. The lockfile is created only when `run_scheduler_daemon.py` acquires `SchedulerProcessLock()`.
5. Because `run_scheduler_daemon.py` is not running, no lockfile exists, and the dashboard reports `IDLE`.

---

## 6. Section 5 — Scheduler Daemon Initialization & Profile Verification

A non-invoking verification of the scheduler configuration was executed:

1. **Active Profile:** Confirmed `FOUR_CYCLES_DAILY` (Standby profile `SINGLE_DAILY_2300` remains strictly inactive).
2. **Timezone:** Confirmed `Asia/Kolkata` (`+05:30`).
3. **Cron Triggers Calculated:**
   - `gen_cycle_2`: 2026-11-11 06:00:00 IST
   - `pub_cycle_1`: 2026-11-11 06:00:00 IST
   - `gen_cycle_3`: 2026-11-11 12:00:00 IST
   - `pub_cycle_2`: 2026-11-11 12:00:00 IST
   - `gen_cycle_4`: 2026-11-11 18:00:00 IST
   - `pub_cycle_3`: 2026-11-11 18:00:00 IST
   - `gen_cycle_1`: 2026-11-12 00:00:00 IST
   - `pub_cycle_4`: 2026-11-12 00:00:00 IST
4. **Duplicate Prevention:** `SchedulerProcessLock` atomically checks for existing live PIDs via `OpenProcess` and rejects duplicate instances.

---

## 7. Section 6 — Browser Auto-Launch Feasibility Analysis

1. **Task Scheduler Interactive Session Support:**
   - Both tasks are registered with `Principal.LogonType = Interactive` and `UserId = aabhi`.
   - In Windows, logon-triggered tasks configured with interactive logon run in the active user's desktop session (`WinSta0\Default`).
   - Python's `webbrowser.open()` uses `ShellExecuteW(0, L"open", url, ...)`, which successfully invokes the user's default browser when running in an interactive session.
2. **Why It Did Not Open in Stage 3.20:**
   - `CrazzyDocs-Health-Dashboard` has never run because the machine has not logged out or rebooted since registration.
   - PID 17092 was running from before `--open-browser` was added.
   - Nobody called `Start-ScheduledTask` or `run_dashboard.py --open-browser`.

---

## 8. Root Cause Summary Table

| Observed Symptom | Perceived Cause | Actual Forensic Root Cause |
| :--- | :--- | :--- |
| **Windows Task Scheduler: OPTED-OUT / NOT REGISTERED** | Registration failed in Stage 3.20 | Registration **succeeded** for `CrazzyDocs-Production-Scheduler` & `CrazzyDocs-Health-Dashboard`. Health check was querying legacy `CrazzyDocsStudioScheduler` which does not exist. |
| **Scheduler Heartbeat: IDLE** | Scheduler crashed or broken | Task has `-AtLogOn` trigger and has not executed yet because no logon event has occurred. No daemon process is running. |
| **Pipeline: IDLE** | Orchestrator failure | Normal: No production job is actively rendering or generating. |
| **Dashboard Did Not Open Browser** | Browser auto-launch failed | Old process (PID 17092) was running without `--open-browser`. The scheduled task with `--open-browser` has not executed yet. |

---

## 9. Minimal Corrective Actions (For Subsequent Implementation)

The following minimal, non-disruptive corrective actions will resolve the discrepancies:

1. **Update Health Check Query Target (`production_health_dashboard.py`):**
   - Point `get_windows_task_status()` to query `CrazzyDocs-Production-Scheduler` (and `CrazzyDocs-Health-Dashboard`) via `schtasks /Query /TN ...` rather than the obsolete `CrazzyDocsStudioScheduler`.
2. **Update Legacy Script (`manage_windows_startup.py`):**
   - Update `TASK_NAME` to default to `CrazzyDocs-Production-Scheduler`.
3. **Start Current Session Services via Task Scheduler / Script:**
   - Stop stale PID 17092.
   - Execute `Start-ScheduledTask -TaskName 'CrazzyDocs-Production-Scheduler'` (or start via `setup_windows_startup_tasks.ps1 -Action Start`).
   - Execute `Start-ScheduledTask -TaskName 'CrazzyDocs-Health-Dashboard'`, which boots Uvicorn and automatically launches the browser to `http://127.0.0.1:8000/dashboard`.
4. **Preserve Scheduling Invariants:**
   - Maintain `FOUR_CYCLES_DAILY` as active.
   - Keep `SINGLE_DAILY_2300` strictly inactive.
