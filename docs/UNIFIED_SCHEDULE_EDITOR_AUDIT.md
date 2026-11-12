# UNIFIED SCHEDULE EDITOR — AUDIT

**Project:** CrazzyDocs Autonomous Studio  
**Workspace:** `D:\Agents\CrazzyDocs`  
**Timezone:** `Asia/Kolkata`  
**Audit Date:** 2026-11-12

## 1. Pre-Consolidation State

Prior to this change, the dashboard (`backend/app/templates/dashboard.html`) exposed three separate schedule-related sections:

1. **Production Cycle Schedule Editor** — managed `ACTIVE_PROFILE_KEY` / `DRAFT_PROFILE_KEY` via `/api/control-center/schedule/*`.
2. **Independent YouTube Publication Schedule Editor** — managed `PUB_SCHEDULE_ACTIVE_KEY` / `PUB_SCHEDULE_DRAFT_KEY` via `/api/control-center/publication-schedule/*`.
3. **Publication Recovery Timeline** — displayed publication slot recovery windows separately from production cycle recovery windows.

### Observed Issues
- Users had to cross-reference three separate UI panels to determine which production cycle fed which YouTube publication slot and which schedule revision was actually active.
- The separate scheduler daemon (`CrazzyDocs-Production-Scheduler`, `run_scheduler_daemon.py`) ran in its own `pythonw.exe` process; the dashboard had no persisted cross-process scheduler-loaded revision (`loaded_revision`) to compare against `active_revision`.
- Activation did not perform an explicit end-to-end verification sequence confirming that the scheduler loaded the newly activated revision and that timeline recalculation matched it.

## 2. Backend & Scheduler Architecture Audit

- **Production Schedule Storage:** `StudioSettingModel` (`ACTIVE_PROFILE_KEY`, `DRAFT_PROFILE_KEY`, `PROD_SCHEDULE_VALIDATION_KEY`, `PROD_SCHEDULE_HISTORY_KEY`) in `backend/app/services/control_center_service.py`.
- **Publication Schedule Storage:** `StudioSettingModel` (`PUB_SCHEDULE_ACTIVE_KEY`, `PUB_SCHEDULE_DRAFT_KEY`, etc.) in `backend/app/services/control_center_service.py`.
- **Scheduler Process:** `backend/app/scheduler/scheduler.py` (`configure_scheduler_jobs()`, `start_scheduler()`, `reload_scheduler()`, `get_scheduler_status()`).
- **Dashboard Endpoints:** `backend/app/main.py` exposes `/dashboard` and `/api/control-center/*`.

## 3. Audit Conclusions & Constraints Preserved

- Backend independence between production schedule (`ACTIVE_PROFILE_KEY`) and YouTube publication schedule (`PUB_SCHEDULE_ACTIVE_KEY`) is preserved.
- Existing `/api/control-center/schedule/*` and `/api/control-center/publication-schedule/*` endpoints remain intact for backward compatibility.
- Active live `FOUR_CYCLES_DAILY` schedule, job history, scheduled YouTube publication timestamps, and `concurrency = 1` were preserved without reset or test uploads.
