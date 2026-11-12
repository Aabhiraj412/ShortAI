# CrazzyDocs — Claude Project Audit (Phase 0 / Phase 1)

**Date:** 2026-11-13 (IST)  **Auditor:** Claude (takeover from Antigravity)
**Method:** read-only. Source files, `logs/scheduler_daemon.log`, and a point-in-time copy of `backend/studio.db` (mtime 2026-11-12 21:33 IST) were copied out and analysed. **No code, DB rows, processes or YouTube state were changed.**

> **Access limitation:** this session can read and write files under `D:\Agents` but has **no shell on the Windows machine**. `git status/diff`, pytest in `.venv`, process inspection and restarts were therefore **not performed**. Every claim below is grounded in file contents, file mtimes, DB rows or log lines.

---

## 1. Architecture (as actually executed)

| Layer | Module | Reality |
|---|---|---|
| Startup | `start_studio.ps1/.bat` → `backend/scripts/startup_manager.py` | Launches two long-lived processes |
| Dashboard / API | `backend/scripts/run_dashboard.py` → uvicorn `backend/app/main.py` | FastAPI + `app/templates/dashboard.html` (single 99 KB file) |
| Scheduler daemon | `backend/scripts/run_scheduler_daemon.py` | APScheduler + 45 s `periodic_recovery_and_sleep_loop` + 60 s heartbeat; holds `backend/studio_scheduler.lock` |
| Scheduling | `app/scheduler/scheduler.py`, `schedule_profiles.py`, `reconciliation_service.py` | Cycle cron jobs + recovery dispatch |
| "Worker" | `scheduler.run_cycle_generation → _execute_claimed_worker` | **A thread inside the scheduler daemon** (`asyncio.to_thread`). There is no separate worker process or task queue. |
| Pipeline | `app/orchestration/crazzydocs_master_orchestrator.py` (`execute_production_pipeline`) | Topic → story → TTS → media acquisition → render (Lemo‑Opuscar / HybridVisualEngine) → QC → integrity gate → ledger → publishing queue |
| Publishing | `app/services/youtube_publishing_engine.py`, `youtube_service.py` | `publishing_ledger` table, OAuth via `token.json`/DPAPI |
| Config/state | `app/services/control_center_service.py` | All schedule/policy state stored as JSON blobs in `studio_settings` |
| DB | `backend/studio.db` (SQLite, SQLAlchemy `app/database/db.py`) | Key tables: `persistent_jobs`, `publishing_ledger`, `publishing_ledger_archive` (253), `youtube_upload_state`, `studio_settings` |
| (Legacy/unused) | `app/workers/job_manager.py`, `app/workers/pipeline.py` | Not on the live production path observed in logs |

### Production execution path (observed)
`APScheduler cron OR 45s recovery loop → reconciliation_service (marks job "RUNNING"/STARTUP_RECONCILIATION) → run_cycle_generation → status QUEUED → thread claims → status RUNNING → orchestrator → on exception: status FAILED → next 45s loop re-dispatches FAILED job`

## 2. Git / working tree

* HEAD on `main`, last commit `docs: Add Stage 3.21M execution report…` (index mtime 2026-11-11 ~16:30 IST). `origin/main` is older.
* **Everything after 11-11 16:30 is uncommitted** — dozens of source files across rendering, services, scheduler, orchestrator, dashboard, tests (≈ 2 days of work). A real `git status`/`git diff` must be run on the machine.
* Tracked-secret risk: `backend/client_secrets.json`, `backend/token.json`, `.env.local` exist; confirm `.gitignore` covers them before any checkpoint commit.

## 3. Antigravity's final (interrupted) session — 2026-11-12 18:18–18:29 IST

Identified by mtime (no git diff available):

| File | mtime IST | Claimed change | Verified in code |
|---|---|---|---|
| `app/services/lifecycle_events.py` | 18:18 | New structured event logger | Present; creates `production_lifecycle_events` table lazily |
| `app/scheduler/scheduler.py` | 18:19 | Jobs created QUEUED, RUNNING on "worker claim" | Present (lines ~270–480) |
| `app/orchestration/crazzydocs_master_orchestrator.py` | 18:19 | `from pathlib import Path` | Present (line 31) |
| `app/services/control_center_service.py` | 18:29 | Removed publication-slot cross-validation | Not yet diffed |
| docs: `PRODUCTION_SCHEDULER_CORRECTION_REPORT.md`, `PUBLISHING_QUEUE_VERIFICATION.md`, `LIVE_EXECUTION_DIAGNOSTICS.md`, `FINAL_VERIFICATION.md` | 18:28–18:29 | | |

Earlier same-day (17:55–18:01) related edits: `run_scheduler_daemon.py`, `youtube_publishing_engine.py`, `youtube_service.py`, `main.py`, `dashboard.html`, `test_unified_schedule_editor.py`, `test_dynamic_production_schedule.py`, `test_user_controlled_publishing.py`.

**None of the 18:18–18:29 changes were ever loaded by the running daemon** (see §5). `production_lifecycle_events` table does **not exist** in the DB — no lifecycle event has ever been recorded live.
Antigravity's docs claim "Cycle marked RUNNING only after worker acknowledgement" and "Success Criteria Met" — **unverified**; the claims describe code on disk, not running behaviour.

## 4. Current active configuration (from `studio_settings`)

* Operating mode: **PRODUCTION**; production control state: **RUNNING**
* Active production schedule: **CUSTOM**, rev `70795aff65e6`, 1 cycle — `cycle_4` start **17:30 IST**, recovery 240 min (deadline 21:30), **`target_publish_time: 00:00` (still coupled)**
* Unactivated draft: 4-cycle schedule rev `d3550fbd996b` (saved 18:10)
* Publication schedule (legacy) still persisted: 4 slots 05:30/11:30/17:30/23:30, `wait_for_slot: true`; draft validation **invalid** (recovery-overlap errors)
* **Publishing policy: `automatic`** (switched from manual_approval at 2026-11-12 12:48 IST)
* Scheduler sync_state: daemon PID 2832, synced 18:02:29 IST

## 5. Historical failed cycle `cycle_4_20261112_1730` — exact status

| # | Question | Answer (evidence) |
|---|---|---|
| 1 | Cycle detected? | Yes — not by its 17:30 cron (daemon PID 8008 had stale FOUR_CYCLES profile), but by the **new daemon PID 2832 started 18:02** whose reconciler found it inside the recovery window. |
| 2 | Job created? | Yes, `persistent_jobs` row created 18:02:24 IST |
| 3 | Persisted? | Yes |
| 4 | Enqueued? | Yes (no real queue — status field only) |
| 5 | Worker claimed? | Yes — in-process thread, ~45 times |
| 6 | Worker started? | Yes, 18:03:21 first `STARTING AUTONOMOUS PRODUCTION RUN` |
| 7 | Orchestrator started? | Yes, every attempt |
| 8 | Stage reached? | Topic, story, TTS (69.39 s narration), media acquisition, then **STAGE 9 video_rendering** — fails immediately |
| 9 | Exception? | `NameError: name 'Path' is not defined` at orchestrator line 819, **identical on every attempt from 18:08 to 21:11** |
| 10 | Why RUNNING while video pending? | Each loop: recovery loop re-dispatches the FAILED job → RUNNING for ~3–4 min (TTS + downloads) → fails → FAILED for <1 min → re-dispatched. Snapshots almost always show RUNNING. DB `current_stage` stays VOICEOVER because stage isn't updated before rendering. At 21:33 the reconciler set `RECOVERY_WINDOW_EXPIRED`. |

**Why Antigravity's fix didn't help:** the fix was written at 18:19, but PID 2832 had imported the module at 18:02 and was never restarted. Proof: the 21:11 traceback prints source text for line 819 (`len(plan.scenes), PINNED_COMMIT[:10]`) that no longer matches the edited file — the process runs stale bytecode.

`cycle_4_20261112_1800` (FAILED, WinError 32 file lock) was a concurrent run of the old 18:00 profile racing the same cycle_4 — a duplicate-dispatch symptom.

## 6. Publishing state

* `publishing_ledger` has 1 live row: id 284 "SoHo Weekly News" (cycle_3) — **`uploaded_public`**, YouTube `gPcd3gXF984`, uploaded 13:48 IST, 25 min after production, under the `automatic` policy. The final render was 56.38 s and passed QC; the job's `error_details` still carries a stale QC message from earlier attempts.
* Publishing Queue APIs exist in `main.py` (`/api/publishing/queue`, PUT metadata, thumbnail, schedule, publish-now, cancel-schedule, verify-scheduled). Not exercised in this audit.
* Orchestrator Stage 14 (on disk) now inserts `pending_queue`/private — **but** `run_cycle_publishing` and `process_publishing_queue(execute_upload=…)` still upload public when policy is `automatic`.

## 7. Critical bugs

1. **Running daemon executes stale code.** No code reload/restart after edits; any on-disk fix is inert until restart. (Blocks all verification.)
2. **Unbounded retry storm.** FAILED jobs inside the recovery window are re-dispatched every ~45–60 s with `retry_count` never incremented → ~45 identical failures, repeated TTS/asset downloads, repeated failure-email attempts. Deterministic errors (NameError) are retried as if transient.
3. **Automatic public publishing is still active** (`policy=automatic`), contradicting the required user-controlled flow.
4. **Production still coupled to publication**: cycle spec carries `target_publish_time`; `is_deadline_exceeded` marks jobs MISSED against *publication* SLA; job IDs/logs/registration print "Pub: 00:00 IST"; legacy publication schedule + `wait_for_slot` still persisted.
5. **"Worker acknowledgement" is self-acknowledgement** — the claiming thread is in the dispatching process; there's no independent worker, so "worker unavailable/crashed" can't be distinguished from "scheduler crashed".
6. **Heartbeat ≠ progress.** cycle_4_1730 heartbeat kept advancing until 21:32 while no stage progressed; stall detection (30 min stale heartbeat) can't fire.
7. **Stale `error_details` on success** (cycle_3 COMPLETED with "QC Failure" text) → misleading dashboard/emails.
8. **Duplicate dispatch across profiles** (cycle_4_1730 vs cycle_4_1800 same evening, WinError 32 file lock).
9. **Recovery loop logs "EXECUTING STARTUP RECONCILIATION" every ~50 s** and reports `Queued: 1` permanently — noisy/misleading diagnostics.
10. `run_scheduler_daemon.py` defines `main()` twice (lines 96 and 140); the second silently overrides the first.
11. Lifecycle events table never created live; event writes swallow errors at DEBUG level.
12. Media providers failing (Internet Archive SSL self-signed chain; Openverse `NoneType.lower`) → only 2–6/10 assets acquired. Not in scope but affects quality.

## 8. Recommended implementation sequence

0. **Safety first (needs user decision):** set publishing policy to `manual_approval` via dashboard; decide on `gPcd3gXF984`.
1. Restart-aware deployment: daemon records code version (file hash) at start; dashboard shows "stale code" when on-disk hash differs.
2. Retry policy: increment `retry_count` on every failed attempt, cap at `max_retries`, classify non-retryable errors (NameError/ImportError/TypeError → FAILED immediately, alert once).
3. Stage-progress heartbeat (stage + timestamp of last stage change) → WORKER_STALLED detection.
4. Completion boundary: COMPLETED only after `publishing_ledger` row committed; clear `error_details` on success; on queue-insert failure keep video and retry handoff only.
5. Remove publication coupling from cycle spec, SLA check, registration, validation, APIs, dashboard (preserve legacy rows).
6. Make all upload paths require an explicit user-scheduled timestamp; delete the auto-public branch from `run_cycle_publishing`/Stage 14.
7. Publishing Queue: verify private upload → thumbnail → `status.publishAt` → remote verification; idempotent on `youtube_video_id`.
8. Dashboard corrections; regression tests; live verification after a controlled restart.
