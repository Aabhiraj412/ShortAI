# User-Controlled YouTube Scheduled Publishing Architecture

**Date:** 2026-11-12  
**Timezone:** `Asia/Kolkata` (UI) / `UTC` (Storage & YouTube `publishAt`)  

## 1. Core Architectural Separation

CrazzyDocs now enforces strict separation between two independent systems:

1. **Production Scheduler (`scheduler.py`, `run_scheduler_daemon.py`)**
   - Runs automatically on the configured production schedule (`gen_{cycle_id}`).
   - Executes `SCHEDULED -> PRODUCTION_STARTED -> RESEARCH -> SCRIPT -> STORYBOARD -> RENDERING -> QUALITY_CONTROL -> VIDEO_READY -> PUBLISHING_QUEUE`.
   - Never waits on publication scheduling and never assigns publication timestamps from production cycle mappings.
   - Periodic YouTube status reconciliation (`youtube_status_reconciliation_job`, every 5 minutes) checks `publication_scheduled` records against YouTube Data API without auto-publishing unscheduled videos.

2. **User-Controlled YouTube Publishing Queue (`youtube_publishing_engine.py`, `youtube_service.py`, `main.py`, `dashboard.html`)**
   - QC-approved videos enter `publishing_ledger` with `upload_status = "pending_queue"`, `visibility = "private"`, and `scheduled_timestamp = None`.
   - Videos remain in the Publishing Queue (`Ready to Schedule`) until the user explicitly triggers:
     - `EDIT & SCHEDULE`: Edits title, description, tags, hashtags, custom thumbnail, selects future IST date/time -> converts to UTC `...Z`, uploads `private` + `publishAt` + custom thumbnail, and verifies via `videos().list`.
     - `PUBLISH NOW`: Explicit confirmation to publish publicly immediately.
     - `CANCEL SCHEDULE`: Removes `publishAt` on YouTube (keeping the video `private`) and returns the item to `Ready to Schedule`.
     - `VIEW ON YOUTUBE`: Opens `https://youtu.be/<youtube_video_id>`.

## 2. Removed Legacy Fixed-Time Publication Execution

- `configure_scheduler_jobs()` no longer registers `pub_{cycle_id}` (`run_cycle_publishing`) cron triggers.
- `crazzydocs_master_orchestrator.py` no longer calls `compute_next_publication_slot()` or `process_publishing_queue(execute_upload=True)` after QC approval.
- The **Production Scheduler** tab in `dashboard.html` displays only production cycles, start times, recovery durations, recovery deadlines, and enabled toggles.
