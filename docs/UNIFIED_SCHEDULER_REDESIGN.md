# UNIFIED SCHEDULER UI/UX REDESIGN — ARCHITECTURE & DESIGN PLAN

**Project:** CrazzyDocs Autonomous Studio  
**Workspace:** `D:\Agents\CrazzyDocs`  
**Timezone:** `Asia/Kolkata`  
**Frontend:** `backend/app/templates/dashboard.html`  
**Backend:** `backend/app/services/control_center_service.py`, `backend/app/main.py`, `backend/app/scheduler/scheduler.py`

## 1. Existing Code Inspection Summary

1. **Backend Persistence & APIs (`control_center_service.py`, `main.py`):**
   - Production schedules are persisted in `StudioSettingModel` under `ACTIVE_PROFILE_KEY`, `DRAFT_PROFILE_KEY`, `PROD_SCHEDULE_VALIDATION_KEY`, and `PROD_SCHEDULE_HISTORY_KEY`.
   - Publication schedules are persisted independently under `PUB_SCHEDULE_ACTIVE_KEY`, `PUB_SCHEDULE_DRAFT_KEY`, and `PUB_SCHEDULE_VALIDATION_KEY`.
   - `get_unified_schedule_state()` and `activate_unified_schedule()` coordinate both schedules and verify `SCHEDULER_SYNC_KEY` written by `scheduler.configure_scheduler_jobs()`.
   - Existing endpoints `/api/control-center/schedule/*`, `/api/control-center/publication-schedule/*`, `/api/control-center/unified-schedule`, and `/api/control-center/unified-schedule/activate` are operational and covered by 72 passing tests.
2. **Current UI Pain Points (`dashboard.html`):**
   - Exposes technical revision hashes, internal IDs (`cycle_1`, `pub_1`), standalone/unmapped slot boxes, and a large 7-column recovery timeline table in the primary operator view.
   - `refreshAll()` triggers `refreshControlAndCycles()` (which calls `refreshUnifiedSchedule()`) and also `refreshPublicationRecovery()` (an alias for `refreshUnifiedSchedule()`), causing duplicate `/api/control-center/unified-schedule` requests on load.
   - Lacks a visual 24-hour horizontal timeline and an intuitive Add/Edit/Reorder cycle workflow.

## 2. Proposed Backend Changes (Preserving Existing Architecture)

- **`backend/app/services/control_center_service.py`:**
  - Add `save_unified_schedule_draft(prod_spec, pub_spec)` to coordinate saving both drafts with explicit 1-to-1 cycle-to-publication slot mappings.
  - Add `validate_unified_schedule(prod_spec=None, pub_spec=None)` to validate both schedules together, check missing/duplicate publication mappings, production/publication recovery overlaps (`STRICT` vs `SHARED_RECOVERY`), midnight rollover, impossible execution windows, and return plain-language `errors`, `warnings`, and `successes`.
  - Extend `get_unified_schedule_state()` with `last_successful_execution`, `prod_cycles_per_day`, `pub_slots_per_day`, and `compact_execution` (one compact row per cycle with recovery flag shown only when needed).
- **`backend/app/main.py`:**
  - Add `POST /api/control-center/unified-schedule/draft` and `POST /api/control-center/unified-schedule/validate`.
  - Keep all existing `/api/control-center/schedule/*` and `/api/control-center/publication-schedule/*` endpoints intact.

## 3. Proposed UI/UX Redesign (`backend/app/templates/dashboard.html`)

- **Section A — Scheduler Overview:** Compact status cards (Scheduler Status, Active Schedule, Production Cycles/Day, Publication Slots/Day, Next Production, Next Publication, Last Successful Execution) with traffic-light indicators (`GREEN`, `YELLOW`, `RED`, `GRAY`), plus a collapsed `<details>` **System Diagnostics** drawer for revision hashes and internal IDs.
- **Section B — Unified Daily Schedule:** Single 8-column editable table (`Cycle`, `Production Start`, `Production Recovery`, `Recovery Deadline`, `Publish Time`, `Publication Prep`, `Publication Recovery`, `Enabled`) with auto-calculated deadlines, midnight rollover (`+1 day`), reorder, edit, and delete.
- **Section C — Add / Edit Production Cycle:** `+ Add Cycle` button and clean form for Cycle Name, Production Start, Production Recovery, Target Publication Time (+1d), Publication Prep, Publication Recovery, and Enabled.
- **Section D — Visual 24-Hour Timeline:** Interactive horizontal 24-hour (+ rollover) timeline showing Production, Production Recovery, Publication Prep, Publication Target, and Publication Recovery blocks, updating live on edit and supporting cycle selection.
- **Section E — Advanced Settings (Collapsed by Default):** Timezone, Enabled Weekdays, Missed Slot Behavior, Recovery Overlap Policy (`STRICT` / `SHARED_RECOVERY`), Wait For Publication Slot, and Preset loaders.
- **Section F — Validation and Activation:** `SAVE DRAFT`, `VALIDATE SCHEDULE`, `ACTIVATE SCHEDULE` (enabled only after valid backend validation), plain-language `ERROR`/`WARNING`/`SUCCESS` feedback, and verified post-activation sync indicator (`GREEN` / `YELLOW` / `RED`).
- **Compact Execution Monitoring:** 4-column table (`Cycle | Production | Publication | Status`) showing recovery only when needed, with expandable `Details`.
- **Performance:** Eliminate duplicate `/api/control-center/unified-schedule` calls in `refreshAll()`, add loading indicators and double-submit locks.
