# Production Cycle Manager Implementation

**Project:** CrazzyDocs Autonomous Production Studio  
**Workspace:** `D:\Agents\CrazzyDocs`

## 1. Backend & Scheduler Changes

- **`backend/app/scheduler/schedule_profiles.py`:**
  - Added `schedule_version: int = 1` and `revision: str = ""` to `ScheduleProfile`.
- **`backend/app/services/control_center_service.py`:**
  - Added `PROD_SCHEDULE_VALIDATION_KEY` and `PROD_SCHEDULE_HISTORY_KEY`.
  - Added `_compute_production_schedule_revision()`, `_sort_production_cycles_chronologically()`, and `compute_upcoming_production_cycles(spec, count=5)`.
  - Added `validate_production_schedule_detailed()` (with `validate_schedule()` delegating to it):
    - Enforces `120 <= recovery_duration_minutes <= 240`.
    - Enforces `target_publish_time >= recovery_deadline + 120 minutes`.
    - Rejects invalid `HH:MM` times, duplicate `cycle_id`, duplicate/conflicting `start_time`, conflicting `target_publish_time`, overlapping recovery windows, and overlapping production windows.
    - Preserves disabled cycles (`enabled: False`) sorted chronologically while skipping them in scheduling and overlap checks.
    - Preserves `FOUR_CYCLES_DAILY` as the immutable default preset while supporting `1..N` cycles under `CUSTOM`.
  - Updated `save_schedule_draft()`, `validate_saved_schedule_draft()`, `activate_schedule_draft()`, and `cancel_schedule_draft()` with revision hashing, validation invalidation on edits, version incrementation, and history archival.
  - Updated `get_live_cycle_schedule_status()` to query jobs generically (`JobModel.date_str == date_iso` or `id.like(f"%_{date_prefix}_%")`) and return `upcoming_cycles` (next 5 cycles with countdowns, recovery windows, deadlines, finalization buffers, and associated jobs).
- **`backend/app/main.py`:**
  - Extended `ActivationConfirmationRequest` with optional `spec`, updated `PUT /api/control-center/schedule/draft` and `POST /api/control-center/schedule/validate`, and added `GET /api/control-center/schedule/timeline`.
- **`backend/app/scheduler/scheduler.py`:**
  - Updated `start_scheduler()` to call `restore_active_schedule_profile()` before `configure_scheduler_jobs()` so restarts load only persisted active schedules (never uncommitted drafts).
- **`backend/app/orchestration/crazzydocs_master_orchestrator.py`:**
  - Stage 14 now persists `production_cycle_id`, `production_schedule_version`, `target_publish_time`, `publication_slot_id`, and `publication_schedule_version` into `PublicationRecord.metadata_json._schedule_context` and `JobModel.checkpoint_data_json` (`STAGE_14_SCHEDULE_CONTEXT`).

## 2. Dashboard UI Changes (`backend/app/templates/dashboard.html`)

- Added **Production Cycle Schedule Editor** card above the Independent YouTube Publication Schedule Editor with columns `Cycle`, `Production Start`, `Recovery Duration`, `Recovery Deadline`, `Target Publish`, `Enabled`, `Actions`, plus `+ Add Cycle`, `Save Draft`, `Validate & Preview`, `Activate Schedule`, `Cancel Draft`, and revision/stale validation status bar.
- Added **Next 5 Upcoming Production Cycles** preview and **Upcoming Production Timeline (Active Schedule)** table.
