# Phase 5 Addendum — Publication Recovery Corrections

**Project:** CrazzyDocs Autonomous Production Studio  
**Workspace:** `D:\Agents\CrazzyDocs`

## 1. Backend Corrections (`backend/app/services/control_center_service.py` & `backend/app/main.py`)

1. **Recovery Overlap Policy (`STRICT` vs `SHARED_RECOVERY`):**
   - Added `recovery_overlap_policy` (default `"STRICT"`) to `default_publication_schedule()`, `_normalize_schedule_spec()`, and `PublicationScheduleSpecRequest` in `backend/app/main.py`.
   - Added `_detect_recovery_overlaps()` returning structured `overlap_details` (`slot_a_id`, `slot_a_label`, `slot_a_pub`, `slot_a_recovery_deadline`, `slot_b_id`, `slot_b_label`, `slot_b_pub`, `slot_b_recovery_start`, `overlap_minutes`, `cause`, `suggestion`).
   - Under `STRICT`, overlapping recovery windows fail validation; under `SHARED_RECOVERY`, validation succeeds with explicit warnings and per-slot `overlap_warnings`. Configured durations are never silently modified.

2. **Chronological Slot Ordering:**
   - Added `_sort_slots_chronologically()` and applied it in `_normalize_schedule_spec()`, `validate_publication_schedule()`, and `get_publication_recovery_timeline()`.

3. **Strict Job-to-Slot Association:**
   - Removed the `recent_jobs[0]` fallback broadcast in `get_publication_recovery_timeline()`.
   - Associated publications and jobs strictly by `(slot_id, scheduled_utc_iso)`, `scheduled_timestamp`, or `checkpoint_data_json` (`publication_slot_id` / `scheduled_utc_iso`). Unassigned slots return `associated_job_id: null` and `associated_ledger_id: null`.

4. **Exact 8-State Lifecycle Semantics:**
   - `SKIPPED`: disabled slot, disabled weekday, or passed window with no assigned job.
   - `UPCOMING`: before preparation window, or inside an open recovery window with no eligible failed/unfinished job.
   - `PREPARING`: within `[prep_start_dt, pub_dt)`.
   - `READY`: at/after `pub_dt` with a queued item not yet uploading.
   - `PUBLISHING`: active upload (`uploading` / `processing`); protected from premature expiration even past `rec_deadline_dt`.
   - `RECOVERING`: ONLY when an eligible failed/interrupted/retrying operation (or active claim) is within `[rec_start_dt, rec_deadline_dt)`.
   - `COMPLETED`: published or `publication_scheduled` with verified slot assignment.
   - `RECOVERY_WINDOW_EXPIRED`: ONLY when an eligible unfinished/failed operation remains past `rec_deadline_dt`.

5. **Validation Revision & Stale State Management:**
   - Added `_compute_schedule_revision()` and `PUB_SCHEDULE_VALIDATION_KEY`.
   - `save_publication_schedule_draft()` clears `PUB_SCHEDULE_VALIDATION_KEY` and marks `draft_validation.stale = True`.
   - `validate_saved_publication_schedule_draft(spec)` (wired to `POST /api/control-center/publication-schedule/validate`) persists the validated revision and timestamp (`validated_at_ist`).
   - `activate_publication_schedule_draft()` rejects activation unless `PUB_SCHEDULE_VALIDATION_KEY` matches the current draft revision and `valid === True`.

6. **Concurrency-Safe Recovery Claims:**
   - Added `_ACTIVE_RECOVERY_CLAIMS`, `claim_recovery_operation(ledger_id, slot_id)`, and `release_recovery_operation(ledger_id)` checking `(cap["active_jobs"] + len(_ACTIVE_RECOVERY_CLAIMS)) >= cap["max_concurrency"]`.
   - `get_publication_recovery_timeline()` reports `recovery_worker_status` (`ACTIVE` vs `IDLE`) and `active_recovery_claims`.

## 2. Dashboard UI Corrections (`backend/app/templates/dashboard.html`)

- Added `Recovery Overlap Policy` (`STRICT` / `SHARED_RECOVERY`) selector and Draft Revision / Validated Revision / Last Validated / Validation Status (`VALID`, `INVALID`, `STALE`, `NOT_VALIDATED`) status bar.
- Bound `markPubDraftDirty()` to every editor input so any edit invalidates prior validation and disables `Activate Schedule` until re-validated.
- Removed duplicate `Slot ID` / `Label` table headers in the Schedule Editor.
- Updated `Publication Recovery Timeline` with `Preparation Window`, `Recovery State`, `"No associated job"` fallback, `overlap_warnings`, `pubRecoveryOverlapBanner`, `Worker` pill, and `Policy` pill, sorted chronologically.
