# Phase 5, Stage 3.23.2 — Email Notification Architecture & Root Cause Audit

**Workspace:** `D:\Agents\CrazzyDocs`  
**Timezone:** `Asia/Kolkata`  
**Target Incident:** Duplicate and contradictory completion emails reported for `cycle_2_20261110_0600` (`Tsutomu Yamaguchi — The Man Who Survived Two Atomic Bombs`, completed `08:15 IST`).

---

## 1. End-to-End Email Dispatch Architecture (Pre-Fix Inventory)

CrazzyDocs previously had **four** partially overlapping email dispatch entry points backed by **two disconnected SQLite ledgers**:

| Trigger Path | Caller / Entry Method | Formatter / Template | Idempotency Ledger & Key | Risk Observed |
|---|---|---|---|---|
| Production Pipeline Stage 13 | `CrazzyDocsMasterOrchestrator.execute_production_pipeline` (`backend/app/orchestration/crazzydocs_master_orchestrator.py`) → `notification_service.send_success(...)` | Shortened `[CrazzyDocs Success] ...` plaintext + HTML card (`notification_service.py`) | `notification_delivery_ledger` (`event_key = "SUCCESS:{job_id}"`) | Dispatched independently from scheduler summary even during cycle runs; did not check `persistent_notifications`. |
| Scheduler Cycle Summary | `scheduler.run_cycle_generation` (`backend/app/scheduler/scheduler.py`) → `email_monitoring_service.send_cycle_summary_report(...)` | Full report `CrazzyDocs | Cycle N | SUCCESS | ...` with Deliverables, QC, Publishing & 4-Cycle Dashboard (`email_monitoring_service.py`) | `persistent_notifications` (`idempotency_key = "cycle_report_{date}_{cycle_id}_{status}"`) | Did not check `notification_delivery_ledger`; keyed by date+cycle+status rather than `(job_id, notification_type)`. |
| Stage 3.22 Event Dispatch | `EmailMonitoringService.dispatch_stage_notification` (`backend/app/services/email_monitoring_service.py`) | Key/value plaintext body for `PRODUCTION_COMPLETED`, `UPLOAD_COMPLETED`, `YOUTUBE_AUTH_PROBLEM`, etc. | `persistent_notifications` (`idempotency_key` string only) | Separate caller using same event family without checking `(job_id, notification_type)` across cycle reports. |
| Daily Consolidated Summary / Digest | `DailyPublishingSummaryService.send_daily_summary` (`backend/app/services/daily_publishing_summary.py`) / `EmailMonitoringService.send_daily_digest` | 24h summary / 4-cycle daily digest HTML | `persistent_notifications` (`daily_publishing_summary_{date}` / `daily_digest_{date}`) | Queried `publishing_ledger` for `uploaded_public` only; did not filter out synthetic `test_%` rows in `JobModel`. |

---

## 2. Proven Root Causes of Observed Failures

### Root Cause A — Two Separate Emails for One Production (`cycle_2_20261110_0600`)
1. `CrazzyDocsMasterOrchestrator.execute_production_pipeline()` invoked `self.notifier.send_success(...)` in Stage 13, emitting Email #2 (the shortened `[CrazzyDocs Success]` template) and writing only to `notification_delivery_ledger`.
2. Immediately afterward, `scheduler.run_cycle_generation()` invoked `email_monitoring_service.send_cycle_summary_report(...)`, emitting Email #1 (the full Cycle Report + Daily Dashboard) and writing only to `persistent_notifications`.
3. Neither ledger consulted the other, and there was no ContextVar or canonical dispatch coordinator suppressing Stage 13 during scheduler runs.

### Root Cause B — Hardcoded `Tsutomu Yamaguchi` Title & Stale Metrics
In `EmailMonitoringService.send_cycle_summary_report()` (`email_monitoring_service.py` pre-fix lines 1341–1365):
- `topic` defaulted directly to `"Tsutomu Yamaguchi — The Man Who Survived Two Atomic Bombs"` whenever `result.get("topic")` was omitted or falsy.
- `video_title` defaulted directly to `"He Survived Both Atomic Bombs"` whenever `result.get("video_title")` was omitted (and `crazzydocs_master_orchestrator.py` Stage 13 return dict omitted `video_title`, `storytelling_style`, `narrative_archetype`, `start_time`, `subtitle_path`, `attribution_manifest_path`, and `total_duration_sec`).
- `ProductionDetails` defaulted `narrative_archetype` to `"Triumph Over Catastrophe"`, and `QCDetails` defaulted `thumbnail_validation` to `"PASSED (10-Point Score: 9.76/10)"` while `generate_daily_dashboard()` fell back to `"storage_mb = 431.5"` and added `45.0s` per completed job regardless of actual runtime.

### Root Cause C — Contradictory Daily Dashboard & Test-to-Live SQLite Leak
- `EmailMonitoringService.generate_daily_dashboard()` read `persistent_jobs` independently without overlaying the completed `job_id`/`result` payload and left `uploaded_count = 0` and `youtube_scheduled_count = 0` hardcoded, while incrementing `awaiting_approval_count += 1` blindly on `COMPLETED`.
- Furthermore, `backend/tests/test_stage_3_23_1_cycle_windows.py::isolated_cycle_database` isolated `scheduler_module.SessionLocal` and `reconciliation_service.SessionLocal`, **but failed to isolate `email_monitoring_service.SessionLocal` or mock `notification_service.notify_email`**. When `run_cycle_generation("cycle_2", ...)` ran in that test, `email_monitoring_service` read the **live `studio.db`** (where `cycle_2` had no job row, hence `PENDING`, `Topic: Not selected`, `Generated: 0`) and persisted/dispatched cycle-2 notifications against the live ledger.

### Root Cause D — Non-Atomic Claim & Blind `SEND_DATA` Retry Risk
- `dispatch_summary_notification` performed a non-atomic `SELECT` → `INSERT` → `notify_email` sequence with no `DISPATCHING` compare-and-set transition, leaving concurrent callers vulnerable to double delivery.
- `NotificationManager.notify_email` retried up to `max_retries` times even if `server.send_message(msg)` timed out during the `SEND_DATA` phase (after DATA bytes may already have been accepted by the SMTP relay), risking duplicate email delivery under transient read timeouts.
- Existing `studio.db` state verified before fix: `PRAGMA integrity_check = ok`, `persistent_notifications` held `stage322_2d160511aa124f7b819262726f7a1856` (`YOUTUBE_AUTH_PROBLEM`, `SENT`), `notification_delivery_ledger` held `3` legacy showcase/failure rows (`SUCCESS:showcase_job`, `FAIL:autonomous_job:tts_synthesis`, `FAIL:autonomous_job:render`), and `persistent_jobs` held `4` historical/test rows (`test_vesna_integrity_run`, `test_auto_job_001`, `test_fail_job_002`, `cycle_1_20261111_0000`). All pre-existing rows were preserved untouched.
