# UNIFIED SCHEDULE EDITOR — IMPLEMENTATION

**Project:** CrazzyDocs Autonomous Studio  
**Workspace:** `D:\Agents\CrazzyDocs`

## 1. Scheduler Revision Sync (`backend/app/scheduler/scheduler.py`)

- Updated `configure_scheduler_jobs()` to call `control_center_service.record_scheduler_sync(profile)` after registering APScheduler jobs so the active profile name, `schedule_version`, `revision`, cycle count, and IST sync timestamp are persisted to `StudioSettingModel`.
- Updated `get_scheduler_status()` to expose `loaded_version` and `loaded_revision`.

## 2. Unified Schedule Service & Verified Activation (`backend/app/services/control_center_service.py`)

- Added `SCHEDULER_SYNC_KEY = "control_center.scheduler.sync_state"` and helpers:
  - `record_scheduler_sync(profile)` — persists scheduler-loaded profile/version/revision/cycles_count/timestamp.
  - `get_scheduler_sync_state(active_revision, active_version)` — computes `is_synchronized` (`bool(loaded_revision and loaded_revision == active_revision)`).
- Updated `get_live_cycle_schedule_status()` so `schedule_revision` falls back to `_compute_production_schedule_revision(active_spec)` if `profile.revision` is empty.
- Added `_map_cycles_to_publication_slots(prod_cycles, pub_slots)` returning `(mappings, unmapped_slots)` with explicit `mapping_mode` (`inherited`, `overridden`, or `unmapped`) without inventing hidden mappings.
- Added `get_unified_schedule_state(now_ist=None)` returning:
  - `lifecycle_state` (`DRAFT`, `VALID`, `STALE`, `RUNNING`, `ACTIVE`)
  - `active_schedule_name`, `active_schedule_version`, `active_revision`, `last_activated_at_ist`
  - `scheduler_sync` (`loaded_profile`, `loaded_version`, `loaded_revision`, `synced_at_ist`, `cycles_count`, `is_synchronized`, `active_revision`, `active_version`)
  - `next_production`, `next_publication`
  - `production`, `publication` (active + draft + validation state)
  - `mappings`, `unmapped_publication_slots`
  - `upcoming_cycles`, `upcoming_slots`
  - `unified_timeline` (combining mapped cycles and standalone publication slots with production recovery vs publication recovery details and job-based lifecycle status)
  - `publication_recovery_worker_status`, `publication_overlap_details`
- Added `activate_unified_schedule(confirm, prod_spec=None, pub_spec=None, sync_scheduler_fn=None)` implementing the verified activation workflow (`validate -> reject invalid -> persist active -> load in scheduler -> verify scheduler loaded_revision == active_revision -> verify timeline revision -> return explicit ACTIVE/FAILED result`). Fixed an issue caught during testing where passing `spec=` into `activate_schedule_draft` / `activate_publication_schedule_draft` after `validate_saved_*_draft` cleared the `validated` flag.

## 3. FastAPI Routes (`backend/app/main.py`)

- Added `UnifiedScheduleActivateRequest`, `GET /api/control-center/unified-schedule`, and `POST /api/control-center/unified-schedule/activate` (returning HTTP 409 when activation returns `status == "FAILED"`).
- Preserved all existing `/api/control-center/schedule/*` and `/api/control-center/publication-schedule/*` endpoints.

## 4. Unified Dashboard UI (`backend/app/templates/dashboard.html`)

- Renamed Tab 3 to **Schedule Editor** and consolidated the 3 legacy schedule/recovery cards into one unified card containing:
  - **A. Schedule Overview & Verification Status** (Active Schedule, Active Version, Active Revision Hash, Scheduler-Loaded Revision, Draft Revision Hash, Draft Validation Status, Last Activation, Last Scheduler Sync, Next Production/Publication events, and prominent `SCHEDULE NOT SYNCHRONIZED` warning banner when revisions differ).
  - **B. Unified Schedule Table** (10 columns: `Cycle`, `Production Start`, `Production Recovery`, `Recovery Deadline`, `Target Publish`, `Publication Prep`, `Publication Recovery`, `Publication Recovery Deadline`, `Enabled`, `Actions`) plus Standalone / Unmapped Publication Slots list.
  - **C. Schedule Actions** (`+ Add Cycle`, `Save Draft`, `Validate & Preview`, `Activate Schedule`, `Cancel Draft`, `Refresh Status`).
  - **D. Upcoming Timeline** (next 5 chronological events).
  - **E. Execution Status & Unified Recovery Timeline** (active schedule cycles and standalone slots, distinguishing production recovery vs publication recovery and displaying job-based state).
