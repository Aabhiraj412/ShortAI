# Phase 5, Stage 3.23.2 — Email Notification Idempotency & Content Fix Report

**Workspace:** `D:\Agents\CrazzyDocs`  
**Scope:** Backend schema extension, phase-aware SMTP delivery classification, single canonical completion notification path, dynamic metadata resolution, daily summary accuracy, and test isolation.

---

## 1. Summary of Implemented Fixes

### 1.1 Schema Extension Without Destructive Mutation (`backend/app/database/db.py`)
- Extended `PersistentNotificationModel` (`persistent_notifications`) with additive columns:
  - `delivery_state` (`CONFIRMED_SENT`, `FAILED_RETRYABLE`, `DELIVERY_UNKNOWN`, `PENDING`, `DISPATCHING`)
  - `error_category`, `provider_response`, `updated_at`
- Added `ensure_notification_ledger_schema(db_engine=None)` invoked automatically from `init_db()` and on-demand in `EmailMonitoringService`.
- Backfilled existing `studio.db` rows safely without deleting any record (`stage322_2d160511aa124f7b819262726f7a1856` transitioned cleanly to `status='SENT'`, `delivery_state='CONFIRMED_SENT'`).
- Extended `notification_delivery_ledger` in `NotificationManager._init_notification_db()` with `delivery_state` and `error_category` via safe `ALTER TABLE`.

### 1.2 Phase-Aware SMTP Transport & `DELIVERY_UNKNOWN` Protection (`backend/app/services/notification_service.py`)
- Added `NotificationManager.send_email_with_receipt(...)` tracking the SMTP transaction phase (`CONNECT_AUTH` vs `SEND_DATA`):
  - Transient connection/auth failures prior to `SEND_DATA` retry up to `max_retries` and settle as `FAILED_RETRYABLE` (`confirmed=False`, `safe_to_retry=True`).
  - Timeouts, `SMTPServerDisconnected`, `BrokenPipeError`, or connection drops inside `SEND_DATA` (`server.send_message(msg)`) immediately halt retries and record `DELIVERY_UNKNOWN` (`error_category="TIMEOUT_UNKNOWN"`, `safe_to_retry=False`).
  - Successful `server.send_message(msg)` with zero refused recipients records `CONFIRMED_SENT` (`confirmed=True`).
  - Note: explicit `isinstance(refused, dict) and len(refused) > 0` check handles partial SMTP recipient refusals cleanly while preserving compatibility with mocked SMTP transports in unit tests.
- Preserved `notify_email(...) -> bool` signature and stored `self.last_email_receipt`.

### 1.3 Atomic Claim Lock & Cross-Path Deduplication (`backend/app/services/email_monitoring_service.py`)
- Implemented `EmailMonitoringService._claim_notification_slot(...)`:
  - Checks both `idempotency_key` and canonical `(job_id, notification_type)` identity (`PRODUCTION_COMPLETED`, `PRODUCTION_FAILED`, `DAILY_SUMMARY`, `CYCLE_SUMMARY`, etc.), preventing duplicate dispatch across different entry points or midnight rollover date boundaries.
  - Handles `IntegrityError` races safely and uses an atomic compare-and-set `UPDATE ... SET status='DISPATCHING', delivery_state='DISPATCHING' WHERE id=:nid AND status IN ('PENDING','FAILED') AND delivery_state IN ('PENDING','FAILED_RETRYABLE','')`.
  - Immediately suppresses `CONFIRMED_SENT` (`DUPLICATE_SUPPRESSED`), active `DISPATCHING` (`DUPLICATE_SUPPRESSED`), and `DELIVERY_UNKNOWN` (`DELIVERY_UNKNOWN_SUPPRESSED`).
- Implemented `_execute_smtp_with_state(...)` and updated `dispatch_summary_notification`, `dispatch_stage_notification`, and `retry_pending_notifications` so `DELIVERY_UNKNOWN` records are **never** automatically re-sent on restart/retry (`skipped_delivery_unknown` counter returned).
- Mirrored confirmed cycle completions into `notification_service.record_event_delivered(...)` for legacy `notification_delivery_ledger` parity.

### 1.4 Single Canonical Production-Success Email & Dynamic Metadata (`crazzydocs_master_orchestrator.py`, `scheduler.py`, `email_monitoring_service.py`)
- Added `suppress_orchestrator_completion_email` (`ContextVar[bool]`) in `backend/app/orchestration/crazzydocs_master_orchestrator.py`.
- `scheduler.run_cycle_generation` wraps `execute_production_pipeline(job_id=job_id)` with `suppress_orchestrator_completion_email=True`, commits the completed `JobModel` and `record_cycle_state` first, enriches missing result metadata from `completed_job`, skips success summary dispatch if status is `INTERRUPTED`, and calls `email_monitoring_service.send_cycle_summary_report(...)` **once**.
- `execute_production_pipeline` now captures and returns live `video_title` (`meta.title`), `storytelling_style`, `narrative_archetype`, `start_time`, `completion_time`, `total_duration_sec`, `subtitle_path`, `attribution_manifest_path`, and `thumbnail_score`, and persists them into `JobModel.checkpoint_data_json`.
- For non-scheduler `PRODUCTION` runs (where `suppress_orchestrator_completion_email` is `False` and `notifier.send_success` is unmocked), Stage 13 routes directly through `email_monitoring_service.send_cycle_summary_report(...)` so only the single canonical template is ever used.
- Removed hardcoded `"Tsutomu Yamaguchi — The Man Who Survived Two Atomic Bombs"`, `"He Survived Both Atomic Bombs"`, `"Triumph Over Catastrophe"`, `"10-Point Score: 9.76/10"`, `42.0`, `45.2`, `45.0s`, and `431.5 MB` fallbacks from `EmailMonitoringService`.

### 1.5 Daily Dashboard & Publishing Summary Accuracy + Test Isolation Fix
- Updated `EmailMonitoringService.generate_daily_dashboard(...)` to accept `current_job_overlay` (eliminating stale-read contradictions between header and embedded 4-cycle table), recognize `BLOCKED_QC` / `FAILED_QC` / `QC_REJECTED` / `INTERRUPTED` / `PAUSED` / `WAITING_FOR_INTERNET` / `RECOVERY_WINDOW_EXPIRED` / `ABORTED_PRE_RESET`, read real runtime duration from checkpoints, and query actual `publishing_ledger` counts (`uploaded_public`, `scheduled`, `pending_queue`) instead of hardcoding `0`.
- Updated `DailyPublishingSummaryService` (`backend/app/services/daily_publishing_summary.py`) to exclude synthetic `test_*` / `mock_*` rows from real metrics, break out `videos_uploaded_private`, `videos_awaiting_approval`, and `videos_scheduled`, and route `send_daily_summary` through the atomic claim lock and `DELIVERY_UNKNOWN` protection.
- Fixed the live-DB leak in `backend/tests/test_stage_3_23_1_cycle_windows.py::isolated_cycle_database` by isolating `email_monitoring_service.SessionLocal`, `get_active_profile`, `notification_service.db_path`, `notify_email`, and `notify_desktop`.
- Updated `backend/tests/test_stage_3_9f_email_dashboard.py::test_success_email_builder` to pass an explicit `thumbnail_validation` string instead of relying on a hardcoded `"9.76/10"` dataclass default.

---

## 2. Automated Verification Summary

Created `backend/tests/test_stage_3_23_2_email_idempotency.py` (10 tests covering all 11 Stage 3.23.2 scenarios in isolated temp SQLite DBs with mocked SMTP — zero real emails or uploads):
1. Single scheduler cycle completion sends exactly 1 canonical report email (`[CrazzyDocs Success]` never emitted).
2. Repeated completion callbacks across `send_cycle_summary_report`, cross-midnight calls, and `dispatch_stage_notification` are suppressed (`DUPLICATE_SUPPRESSED`).
3. Concurrent threads racing on the same `job_id` send at most 1 email.
4. Restart reconciliation (`retry_pending_notifications`) never resends `CONFIRMED_SENT`.
5. Pre-DATA SMTP failures mark `FAILED_RETRYABLE` and recover cleanly on retry.
6. `SEND_DATA` SMTP timeouts mark `DELIVERY_UNKNOWN` and block both duplicate dispatches and automatic retry sweeps.
7. Daily dashboard & `DailyPublishingSummaryService` accurately show completed Cycle 2 with real topic and exclude synthetic `test_*` rows.
8. Live `video_title` populates subject and omits all hardcoded `Tsutomu Yamaguchi` / `9.76/10` strings.
9. `manual_approval` production completion clearly distinguishes local production success (`Withheld at safety gate`) from live YouTube upload/public status.
10. Reconciled operational states match real Windows Scheduled Task (`Disabled`/`Ready`) and scheduler daemon (`STOPPED`/`RUNNING`) state.
