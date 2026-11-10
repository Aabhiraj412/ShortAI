# CrazzyDocs Daily Publishing Summary Email

**Location:** `backend/app/services/daily_publishing_summary.py`  
**Trigger:** Autonomous End-of-Day Aggregator / Scheduled Job  
**Status:** Live & Tested in Production Environment  

---

## 1. Objective & Requirements

The Daily Publishing Summary Email compiles an accurate, consolidated 24-hour summary of channel operations across all daily cycles, ensuring operators have full transparency without inspecting logs.

---

## 2. Core Metrics Gathered

1. **Productions Attempted:** Total jobs recorded in `persistent_jobs` for the reporting date.
2. **Productions Completed:** Jobs reaching `status = "COMPLETED"`.
3. **Videos Successfully Published:** Count of Shorts publicly published to YouTube (`visibility = 'public'` and `upload_status IN ('uploaded_public', 'published')`).
4. **Videos Blocked by QC:** Count of deliverables failing technical or Content Integrity gates (`status IN ('BLOCKED_QC', 'FAILED_QC')`), with topics and failure reasons.
5. **Missed Production Cycles:** Count of cycles marked `status = "MISSED"` due to expired recovery windows.
6. **Recovered Production Cycles:** Count of cycles recovered via startup reconciliation (`recovery_type` in checkpoint).
7. **Storage Consumed:** Total size of `media/` directory in MB and remaining free disk space in GB.
8. **Errors Requiring Attention:** List of exceptions, unhandled stage failures, and failed email dispatches.

---

## 3. Deduplication & Resilience Invariants

- **Strict Idempotency:** Dispatched with idempotency key `daily_publishing_summary_{YYYY-MM-DD}`. Calling the function multiple times on the same calendar day suppresses duplicate dispatches (`DUPLICATE_SUPPRESSED`).
- **Offline Delivery Resilience:** If SMTP is unreachable, the notification is saved in `persistent_notifications` with status `FAILED` and `attempt_count` tracked. The service safely retries on the next execution without losing the report.
- **Production vs. Publication Separation:** The email clearly separates local successful rendering (`Productions Completed`) from remote YouTube publishing (`Videos Successfully Published`), preventing false assumptions of public release.

---

## 4. Verification

Tested via `backend/tests/test_daily_publishing_summary.py`:
- 4 / 4 automated tests passing (metric compilation, HTML rendering, persistent deduplication, offline persistence).
