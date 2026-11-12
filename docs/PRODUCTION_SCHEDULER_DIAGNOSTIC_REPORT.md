# Production Scheduler Diagnostic Report

**Incident:** Scheduled `17:30 IST` production cycle (`CUSTOM` profile) did not start on `2026-11-12`.

## 1. Execution Chain Trace

`Scheduled Time (17:30 IST) -> APScheduler CronTrigger -> run_cycle_generation() -> Job Creation -> Worker Queue -> Production Execution`

## 2. Root Cause Identified

1. **Stale In-Memory Profile in Daemon Process:** `CrazzyDocs-Production-Scheduler` (`run_scheduler_daemon.py`, PID `8008`) started at `11:42 IST` with `FOUR_CYCLES_DAILY` (`00:00, 06:00, 12:00, 18:00`) loaded in memory.
2. **Cross-Process Sync Illusion:** At `17:20:27 IST`, the dashboard process (`uvicorn`) activated `CUSTOM` (`17:30 IST`) in SQLite (`control_center.schedule.active`, revision `70795aff65e6`) and called `restore_active_schedule_profile()` + `record_scheduler_sync()` *inside `uvicorn`*. That updated `control_center.scheduler.sync_state` in SQLite even though PID `8008` never reloaded its APScheduler jobs.
3. **No DB Revision Polling in Daemon Loop:** `periodic_recovery_and_sleep_loop()` ran `dispatch_eligible_recoveries()` every 45 seconds using the stale in-memory `FOUR_CYCLES_DAILY` profile, so `17:30 IST` neither triggered nor recovered.

## 3. Fix Implemented

- Added `reload_profile_from_db_if_changed()` in `backend/app/scheduler/scheduler.py`, invoked on every 10-second tick in `run_scheduler_daemon.py`, at the start of `dispatch_eligible_recoveries()`, and at the start of `run_cycle_generation()`.
- Updated `configure_scheduler_jobs()` so `record_scheduler_sync()` (now recording `daemon_pid`) is only persisted when APScheduler is actually running (or in pytest), preventing `uvicorn` from spoofing daemon sync state.
- Updated `activate_unified_schedule()` in `backend/app/services/control_center_service.py` to report `is_synchronized: False` until the scheduler daemon reloads the new DB revision.
