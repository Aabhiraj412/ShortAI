# Stage 3.19 — Windows Startup Automation Audit & Forensics

**Project:** CrazzyDocs Shorts Studio  
**Canonical Directory:** `D:\Agents\CrazzyDocs\`  
**Timestamp:** 2026-11-11T01:08:00+05:30  
**Status:** AUDITED / LIMITED PRIVILEGE BOUNDARY VERIFIED  

---

## 1. Executive Summary

A forensic audit of Windows Task Scheduler and startup automation was conducted on the host machine. 
The utility `backend/scripts/manage_windows_startup.py` was evaluated against the Windows OS execution model.

**Current Registration State:**
```json
{
  "task_name": "CrazzyDocsStudioScheduler",
  "is_registered": false,
  "opt_in_enabled": false,
  "details": "Not registered in Windows Task Scheduler"
}
```

When attempting non-elevated task registration using `schtasks /Create /TN CrazzyDocsStudioScheduler ...`, Windows returns:
```text
ERROR: Access is denied.
```
On this Windows host, creating a task via `schtasks` requires elevated Administrator privileges (UAC elevation). In accordance with the prompt's explicit boundary instructions (*"If an existing installation method requires administrator privileges, explain why and stop before making system-level changes"*), the agent has not escalated privileges or made system-level registry modifications.

---

## 2. 10-Point Technical Determination

| # | Inspection Parameter | Determination | Technical Evidence |
|---|---|---|---|
| **1** | **Is scheduler registered?** | **NO** | `schtasks /Query /TN CrazzyDocsStudioScheduler` returns `ERROR: The system cannot find the file specified.` |
| **2** | **Does scheduled task exist?** | **NO** | Zero entries present under Task Scheduler library for `CrazzyDocsStudioScheduler`. |
| **3** | **Is it enabled?** | **NO** | Database key `AUTO_START_ON_WINDOWS_LOGIN` is `false`. |
| **4** | **Execution User Account** | **Operator User (`%USERNAME%`)** | Configured to run under the operator's Windows account without privilege elevation. |
| **5** | **Working Directory** | **`D:\Agents\CrazzyDocs`** | Launcher scripts (`start_studio.bat`, `start_studio.ps1`, `run_in_env.cmd`) explicitly execute `cd /d "D:\Agents\CrazzyDocs"`. |
| **6** | **Python Virtual Environment** | **VERIFIED (`.venv\Scripts\python.exe`)** | **Resolved Defect:** Prior scripts referenced `tools\python\python.exe` (which lacked `fastapi`). All batch and PowerShell scripts now resolve strictly to `D:\Agents\CrazzyDocs\.venv\Scripts\python.exe`. |
| **7** | **Trigger Event** | **User Logon (`ONLOGON`)** | Designed to trigger on operator interactive login, not system boot (which would lack user environment tokens). |
| **8** | **Duplicate Instance Prevention** | **VERIFIED** | `SchedulerProcessLock` acquires `backend/studio_scheduler.lock` using atomic `os.O_CREAT | os.O_EXCL` flags. Duplicate launches halt immediately. |
| **9** | **Startup Failure Logging** | **VERIFIED** | Launcher scripts write output to console and can redirect stdout/stderr to `logs/startup.log`. |
| **10** | **Interrupted Production Resumption** | **VERIFIED** | `StartupReconciler.reconcile_on_startup()` inspects active cycles, detects interrupted jobs, and resumes from valid checkpoints. |

---

## 3. Script Invariant Fixes Applied in Stage 3.19

During pre-flight inspection, several legacy launcher scripts were found pointing to obsolete environments:
1. `run_in_env.cmd`: Referenced old path `D:\AI Arts\AI` and `python-env`. **Updated to `D:\Agents\CrazzyDocs` and `.venv\Scripts`.**
2. `start_studio.bat`: Referenced `tools\python\python.exe` (which did not have `fastapi` installed). **Updated to `.venv\Scripts\python.exe`.**
3. `start_studio.ps1`: Referenced `tools\python\python.exe`. **Updated to `.venv\Scripts\python.exe`.**
4. `run_pipeline.bat`: Referenced `tools\python\python.exe`. **Updated to `.venv\Scripts\python.exe`.**

---

## 4. Operator Instructions for Optional Windows Startup Registration

If the operator wishes to enable automated launch on Windows logon:
1. Open PowerShell or Command Prompt as **Administrator** (Run as administrator).
2. Execute:
   ```cmd
   cd /d "D:\Agents\CrazzyDocs"
   .\.venv\Scripts\python.exe backend\scripts\manage_windows_startup.py --install
   ```
3. To disable/uninstall at any time:
   ```cmd
   .\.venv\Scripts\python.exe backend\scripts\manage_windows_startup.py --uninstall
   ```
