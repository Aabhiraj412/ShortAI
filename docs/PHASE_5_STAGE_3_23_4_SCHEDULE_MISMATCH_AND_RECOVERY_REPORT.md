# Phase 5 — Stage 3.23.4: Schedule Propagation, Dashboard Clarification & Publication Recovery Report

## 1. Root Cause of the Apparent Schedule Mismatch
- **Daily Production Cycles Table (`Target Publish: 18:00`)**: `GET /api/control-center/cycles/live` calls `control_center_service.get_live_cycle_schedule_status()`, which reads `FOUR_CYCLES_DAILY` (`Cycle 3: 12:00–16:00, Target Publish: 18:00 IST`).
- **Independent Publication Schedule (`Next Pub: 15:00 IST`)**: `GET /api/control-center/publication-schedule` and Stage 14 (`compute_next_publication_slot()`) read `PUB_SCHEDULE_ACTIVE_KEY`, which held the user's custom slots (`15:00`, `17:30`, `23:30`, `05:30`, `11:30`).
- **Conclusion**: The backend publishing pipeline already used `compute_next_publication_slot()` rather than `FOUR_CYCLES_DAILY`'s `target_publish_ist`. The confusion was caused by:
  1. The Daily Production Cycles table labeling the legacy column `Target Publish`.
  2. The Publishing Queue omitting `slot_id`, `slot_label`, `schedule_version`, and IST scheduled display alongside UTC `publishAt`.

## 2. Propagation & Existing Job Guarantees
- `compute_next_publication_slot()` now includes `schedule_version`, `slot_id`, `label`, `recovery_start_utc`, and `recovery_deadline_utc`.
- `CrazzyDocsMasterOrchestrator` Stage 14 persists `_schedule_context` in `metadata_json`; `YouTubePublisherService._safe_publication()` exposes `scheduled_ist_display`, `scheduled_utc_publish_at`, `slot_id`, `slot_label`, `schedule_version`, `youtube_privacy_status`, and `youtube_scheduled_state`.
- Existing `publication_scheduled` / `uploaded_private` / `uploaded_public` YouTube videos are never automatically altered when the publication schedule changes.

## 3. Independent Publication Slot Recovery Windows (Section 7)
- Every slot across all 5 presets (`FOLLOW_PRODUCTION_CYCLE`, `ONCE_DAILY`, `TWICE_DAILY`, `THREE_DAILY`, `FOUR_DAILY`) and `CUSTOM` schedules now includes independent `prep_window_minutes` and `recovery_duration_minutes`, with timezone-aware (`Asia/Kolkata` + `UTC`) `compute_slot_windows()` and midnight-rollover handling.
- `validate_publication_schedule()` blocks missing recovery durations, invalid times, slots <15 min apart, and overlapping recovery windows across consecutive slots.
- `get_publication_recovery_timeline()` and `GET /api/control-center/publication-recovery` expose real backend 8-state recovery lifecycle (`UPCOMING`, `PREPARING`, `READY`, `PUBLISHING`, `RECOVERING`, `COMPLETED`, `RECOVERY_WINDOW_EXPIRED`, `SKIPPED`), protecting active uploads (`PUBLISHING`) and keeping Production Recovery (`FOUR_CYCLES_DAILY`, 4-hour windows) completely independent.
