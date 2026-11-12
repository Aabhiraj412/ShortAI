# Stage 3.23.1 — Recovery Window & Startup Automation Report

**Status:** Four-Cycle Window Recovery & Windows Startup Tasks Implemented & Isolated-Tested (`Scheduler Production Dispatch Kept Disabled`)  
**Schedule Profile:** `FOUR_CYCLES_DAILY` (`Asia/Kolkata`)  
**Effective Policy:** `STRICT_WINDOW` (Zero Catch-Up / Zero Backfill)  
**Verification Date:** 2026-11-12 (IST)

---

## 1. Four-Cycle Daily Schedule & Strict Recovery Windows

Every daily cycle runs strictly inside its bounded recovery window (`[start, recovery_expiration)` half-open interval):

| Cycle | Job ID Format | Generation Start (IST) | Recovery Cutoff (IST) | Target Publish (IST) |
|---|---|---:|---:|---:|
| `cycle_1` | `cycle_1_YYYYMMDD_0000` | `00:00 IST` | `04:00 IST` | `06:00 IST` |
| `cycle_2` | `cycle_2_YYYYMMDD_0600` | `06:00 IST` | `10:00 IST` | `12:00 IST` |
| `cycle_3` | `cycle_3_YYYYMMDD_1200` | `12:00 IST` | `16:00 IST` | `18:00 IST` |
| `cycle_4` | `cycle_4_YYYYMMDD_1800` | `18:00 IST` | `22:00 IST` | `00:00 IST (+1d)` |

---

## 2. Recovery Dispatcher & Crash/Sleep Handling Implemented

### `backend/app/scheduler/reconciliation_service.py`
- Expanded stale/crashed job reconciliation to inspect `RUNNING`, `INTERRUPTED`, `PAUSED`, and `RETRYING` jobs against active locks and fresh heartbeats.
- Jobs eligible inside `[start, recovery_expiration)` transition to `RUNNING` with `heartbeat_at=None` and recovery history persisted inside `persistent_jobs.checkpoint_data_json` so `dispatch_eligible_recoveries()` does not mistake newly reconciled work for an already-live worker.
- Expired cycles (`now_ist >= recovery_expiration`) are closed strictly as `RECOVERY_WINDOW_EXPIRED` without transferring into later cycles.

### `backend/app/scheduler/scheduler.py`
- Updated `run_cycle_generation` to resume reconciled `STARTUP_RECONCILIATION` (`heartbeat_at is None`), `INTERRUPTED`, and `PAUSED` jobs from checkpoint without generating duplicate runs.
- Handled `INTERRUPTED` (graceful stop) cleanly without overwriting cycle metadata.
- Added `dispatch_eligible_recoveries()` to dispatch bounded `WAITING_FOR_INTERNET` retries and reconciled crash-recovery jobs while strictly respecting half-open expiration boundaries (`< recovery_expiration`).
- Normalized any legacy `CATCH_UP` arguments/records at runtime to `STRICT_WINDOW` (no backfill).

### `backend/scripts/run_scheduler_daemon.py`
- Added `periodic_recovery_and_sleep_loop` detecting wall-clock vs monotonic sleep/hibernate jumps (`> 90s`), dispatching `await dispatch_eligible_recoveries()` only when inside active recovery windows and in `PRODUCTION` mode with scheduler enabled.

---

## 3. Windows Automatic Startup & Health Monitoring (`backend/scripts/startup_manager.py` & `manage_windows_startup.py`)

- Created `backend/scripts/startup_manager.py`:
  - Runs SQLite `PRAGMA integrity_check` before launching dashboard/monitoring services.
  - Launches and verifies FastAPI dashboard health (`http://127.0.0.1:8000/api/dashboard/overview`) before opening browser tabs (preventing premature error tabs).
  - Enforces single-instance locking via `health_monitor.lock` and bounded worker restarts (`BoundedRestartTracker`: max `3` restarts/hour, exponential backoff `5s` / `15s` / `45s`).
  - Only starts the scheduler daemon if `operating_mode == "PRODUCTION"` AND `CrazzyDocs-Production-Scheduler` is explicitly enabled; never initiates ad-hoc production or expired-cycle backfill at boot.
  - Includes monotonic sleep/resume clock-jump monitoring loop.
- Updated `backend/scripts/manage_windows_startup.py`:
  - Added `_register_user_logon_task` using PowerShell `Register-ScheduledTask` (`-LogonType Interactive -RunLevel Limited`) after verifying `schtasks /Create /SC ONLOGON /RL LIMITED` returned `Access is denied` in a non-elevated session.
  - Executed `--install-all` (`"all_ok": true`) and verified live Windows Task Scheduler states:
    - `CrazzyDocs-Startup-Manager`: **Ready (Enabled)** (`startup_manager.py --boot`)
    - `CrazzyDocs-Health-Dashboard`: **Ready (Enabled)** (`run_dashboard.py`)
    - `CrazzyDocs-Health-Monitoring`: **Ready (Enabled)** (`startup_manager.py --monitor`)
    - `CrazzyDocs-Production-Scheduler`: **Disabled** (explicitly kept disabled pending Stage 9 readiness)

---

## 4. Verification Results & Pre-Existing Caveats

- **Consolidated Stage 3.23.1 Verification Suite** (`pytest` across `test_stage_3_23_1_dashboard_auth.py`, `test_stage_3_23_1_cycle_windows.py`, `test_stage_3_23_1_startup_and_controls.py`, `test_stage_3_23_control_center.py`, `test_stage_3_22_integrations.py`, `test_stage_3_12_youtube.py`, `test_daily_publishing_summary.py`): **36 passed** in `16.14s`.
  - Covers boundary transitions across all 4 cycles, offline -> `WAITING_FOR_INTERNET` -> online recovery resume, active upload continuity across `10:00 IST` recovery cutoff, strict expiration with zero backfill, Windows startup boot-sequence gating, bounded restart tracker, and Control Center pause/resume/stop controls.
- **Stage 3.21C + Stage 3.9CD Suite** (`test_stage_3_21c_dashboard_accuracy.py`, `test_stage_3_9cd_scheduler.py`): **15 passed** in `7.02s`.
- **Stage 3.11 Real Recovery + Stage 3.9CD + Daily Summary** (`task-11778`): **22 passed, 1 failed** (`test_stage_3_11_real_recovery.py::test_scenario_3_interrupted_rendering` failed because Stage 3.21H prohibits cross-job reuse of external `prod_20261110_vesna_vulovic` video artifacts and cleanly renders inside `test_interrupted_render_job`).
- **Live SQLite (`backend/studio.db`) Caveat:** Read-only inspection confirms `PRAGMA integrity_check = ok`, `publishing_ledger = 0`, `publishing_ledger_archive = 253`, and 4 preserved `persistent_jobs` rows (`test_vesna_integrity_run`, `test_auto_job_001`, `test_fail_job_002`, `cycle_1_20261111_0000`) whose legacy column values still store `recovery_policy = 'CATCH_UP'` (overridden to `STRICT_WINDOW` at runtime). Live restart/sleep-resume of a running autonomous `PRODUCTION` cycle was not triggered because production automation remained safely disabled.
