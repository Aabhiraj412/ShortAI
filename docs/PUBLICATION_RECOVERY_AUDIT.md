# Phase 5 Addendum — Publication Recovery Audit

**Project:** CrazzyDocs Autonomous Production Studio  
**Workspace:** `D:\Agents\CrazzyDocs`  
**Audit Date:** 2026-11-12 (IST)

## 1. Executive Summary

A focused audit of the Independent Publication Schedule and Publication Recovery subsystem (`backend/app/services/control_center_service.py`, `backend/app/main.py`, and `backend/app/templates/dashboard.html`) identified eight structural defects between the backend API, schedule validation state, job-to-slot attribution, and dashboard presentation.

## 2. Findings

| # | Area | Observed Defect | Root Cause |
|---|---|---|---|
| 1 | Recovery Window Overlap Management | Active schedule had `pub_5` (`15:30 IST`, 240m -> `19:30 IST`) overlapping `pub_3` (`17:30 IST` -> `21:30 IST`) by 120 minutes with no configurable overlap policy (`STRICT` vs `SHARED_RECOVERY`) or structured cause/suggestion output. | `validate_publication_schedule()` lacked a configurable `recovery_overlap_policy`, structured `overlap_details`, and per-slot `overlap_warnings`. |
| 2 | Chronological Slot Ordering | `Custom Slot 5` (`pub_5`, `15:30 IST`) rendered at the bottom of the Schedule Editor and Recovery Timeline after `23:30 IST` instead of between `11:30` and `17:30`. | Slots were iterated in insertion/`slot_id` order rather than sorted by parsed timezone-aware publication time (`time_ist`). |
| 3 | Job-to-Slot Association | `cycle_3_20261112_1200` appeared as the associated job on every single slot in `Publication Recovery Timeline`. | `get_publication_recovery_timeline()` fell back to `recent_jobs[0]` whenever no slot-specific job matched, broadcasting the latest job across all slots. |
| 4 | Recovery Lifecycle Semantics | Slots inside an open recovery window (e.g., `11:30`–`15:30` at `15:25 IST`) displayed `RECOVERING` even when no failed/interrupted publishing operation existed, and slots past deadline showed `RECOVERY_WINDOW_EXPIRED` without an unfinished job. | State transition logic checked only wall-clock timestamps against `[rec_start_dt, rec_deadline_dt)` instead of checking whether an eligible failed/retrying/unfinished publishing operation existed. |
| 5 | Validation State Management | Editing a draft schedule did not invalidate prior validation results or track draft/validated revisions. | No content hash revision (`revision`) or `PUB_SCHEDULE_VALIDATION_KEY` staleness check existed; `POST /api/control-center/publication-schedule/validate` did not persist the validated revision. |
| 6 | Active vs Draft Isolation | `PublicationScheduleSpecRequest` omitted `recovery_overlap_policy`, and UI allowed clicking Activate even after unsaved/unvalidated edits. | Missing Pydantic field in `main.py` and missing client-side dirty-state invalidation + Activate button gating in `dashboard.html`. |
| 7 | Recovery Timeline Columns & Worker Status | Timeline lacked `Preparation Window`, distinct `No associated job` indicator, overlap warning banner, and real backend `recovery_worker_status`. | `dashboard.html` rendered `--` for unassigned jobs and omitted `prep_start_ist` and worker status pills. |
| 8 | Resource & Concurrency Guard | No explicit claim/release guard prevented duplicate recovery attempts or honored `max_concurrency = 1` across overlapping recovery windows. | Missing `_ACTIVE_RECOVERY_CLAIMS` and `claim_recovery_operation()` / `release_recovery_operation()`. |
