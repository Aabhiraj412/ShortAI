# Phase 5, Stage 3.23.3 — System Diagnostic Report

**Project:** CrazzyDocs Autonomous Production Studio  
**Workspace:** `D:\Agents\CrazzyDocs`  
**Dashboard URL:** `http://127.0.0.1:8000/dashboard`  
**Timezone:** `Asia/Kolkata`  
**Audit Date:** `2026-11-12` (IST)

---

## 1. Pre-Change Backup & Integrity Baseline

Before any Stage 3.23.3 changes were applied, the SQLite database (`backend/studio.db`) was backed up and verified:

- **Pre-change backup:** `D:\Agents\CrazzyDocs\backend\backups\studio_stage_3_23_3_pre_20261112_120106.db`
- **SHA-256:** `0683c19d9dce30b1d67fca8d4809f6c80741305ceb73b3ec7959162680a1ffa3`
- **`PRAGMA integrity_check` (`studio.db`):** `ok`
- **Available disk headroom on `D:\`:** `151.0 GB` free / `374.93 GB` total (reported pre-activation).
- **Initial DB state observed:** `operating_mode = IDLE`, `production_concurrency = 1`, `AUTO_START_ON_WINDOWS_LOGIN = 'true'`, `publishing_ledger` active rows = `0`, `publishing_ledger_archive` rows = `253`, private/deleted YouTube video `4DCas1e4vHI` recorded in `deleted_video_audit` (`"Operator authorized permanent deletion of incorrectly titled/mismatched YouTube Short (Stage 3.21F)"`, `2026-11-11T03:58:53+05:30`).

---

## 2. Root Causes Identified & Fixed During Stage 3.23.3

### Issue 1 — Scheduler Daemon Startup Crash (`RuntimeError: no running event loop`)
- **Symptom:** `CrazzyDocs-Production-Scheduler` exited immediately on launch (`Last Result: 1` at `11:42:08` IST); `logs/scheduler_daemon.log` showed two `CRITICAL` crashes at `11:42:36` and `11:43:26` with `Daemon encountered fatal exception: no running event loop`.
- **Root Cause:** `backend/scripts/run_scheduler_daemon.py` called `start_scheduler(skip_lock_for_tests=True)` (which invoked `AsyncIOScheduler.start()` in `backend/app/scheduler/scheduler.py`) **before** creating or running an `asyncio` event loop (`asyncio.new_event_loop()` ran only later).
- **Fix:** Refactored `backend/scripts/run_scheduler_daemon.py` to enter `asyncio.run(_run_daemon(lock))` first so `start_scheduler()` runs inside an active asyncio event loop and immediately updates the heartbeat lockfile.

### Issue 2 — Nested `asyncio.run()` Crash Inside Autonomous Cycle Generation
- **Symptom:** Once the scheduler daemon ran, its first autonomous production recovery (`cycle_3_20261112_1200`) failed at `STORY_DEVELOPMENT` (`narration_generation`, `30.0%`) with `RuntimeError: asyncio.run() cannot be called from a running event loop`.
- **Root Cause:** `async def run_cycle_generation()` in `backend/app/scheduler/scheduler.py` called synchronous `master_orchestrator.execute_production_pipeline(job_id=job_id)` directly on the daemon's asyncio loop thread, and downstream pipeline stages (`documentary_storyboard_builder.py` lines 262 & 440, `crazzydocs_master_orchestrator.py` line 778) called `asyncio.run(...)` directly. Blocking the loop thread also risked starving `periodic_heartbeat_loop`.
- **Fix:**
  - Updated `run_cycle_generation()` in `backend/app/scheduler/scheduler.py` to execute `master_orchestrator.execute_production_pipeline` off the event loop via `await asyncio.to_thread(contextvars.copy_context().run, master_orchestrator.execute_production_pipeline, job_id=job_id)`, preserving `suppress_orchestrator_completion_email` while keeping `periodic_heartbeat_loop` unblocked.
  - Added `backend/app/core/async_helpers.py` (`run_coro_sync`) and replaced direct `asyncio.run(...)` calls at `documentary_storyboard_builder.py` and `crazzydocs_master_orchestrator.py` so coroutines safely execute even if invoked from an active event loop.

### Issue 3 — Identical Procedural Animation Hash Collision Violating Visual Diversity
- **Symptom:** `tests/test_stage_3_21m_creative_policy_and_acquisition.py::test_storyboard_priority_abcd_reuse_limits_and_integrity_parity` failed at `documentary_storyboard_builder.py:1746`: `Stage 3.21J VisualDiversityValidator rejected storyboard: Image/Asset repetition policy violation: canonical asset 'sha256:6b8987b4...' used 2 times (maximum allowed: 1)`.
- **Root Cause:** Inspection of the generated temp assets (`anim_topic_bioluminescence_b06_animated_map.mp4` and `anim_topic_bioluminescence_b08_animated_map.mp4`, both `470,317` bytes) showed `compose_direction_frame()` in `backend/app/rendering/style_treatments.py` cropped `y = h//5 .. h*13//20` (removing the top beat pill at `y = 244..320`) and overwrote headers/footers without encoding `beat_idx` across styles (`documentary`, `cinematic_explainer`, `data_story`, `chalk_talk`, `map_based_storytelling`). Two procedural beats inside the same script section therefore produced identical video bytes.
- **Fix:** Updated `compose_direction_frame()` in `backend/app/rendering/style_treatments.py` to include the scene index (`SCENE {beat_idx + 1:02d}`) across all seven visual styles and vary `map_based_storytelling` waypoint geometry by `beat_idx`. Also switched `ImageOps.fit(..., method=Image.Resampling.LANCZOS)` to `BILINEAR` in `style_treatments.py` to reduce per-frame compositing overhead.

### Issue 4 — Stage 3.19 Style Rationale & Stage 3.11 Recovery Test Gaps
- **Stage 3.19 Creative Identity:** `validate_timeline_beats()` in `backend/app/content/creative_identity_policy.py` and scene generation in `backend/app/agents/visual_director.py` required and now populate `style_selection_rationale` / `style_transition_rationale`. Verified via `tests/test_stage_3_19_creative_identity_e2e.py` (`5 passed`).
- **Stage 3.11 Recovery Fixtures:** `tests/test_stage_3_11_real_recovery.py` scenarios 3 and 4 failed when referencing previously cleaned-up `prod_20261110_vesna_vulovic` media. Added `_create_fixture_media_files()` using `PIL` + `FFMPEG_DEFAULT_PATH` in `tmp_path` so recovery tests remain isolated and deterministic (`10 passed`).

### Issue 5 — Scheduler Cache Staleness, Sub-Signals, & Dashboard Worker Separation
- **Cache Staleness:** Enabling/disabling `CrazzyDocs-Production-Scheduler` updated Windows Task Scheduler immediately while `ProductionHealthDashboard.get_windows_task_status` and `manage_windows_startup.py` cached snapshots for up to 60s. Added `invalidate_tasks_cache()` (`manage_windows_startup.py`) and `invalidate_task_cache()` (`production_health_dashboard.py`), called on enable/disable endpoints.
- **Heartbeat State Mismatch:** `ProductionHealthDashboard.get_reconciled_operational_states()` checked `hb_state == "ACTIVE"`, whereas live heartbeats report `state: "RUNNING"`, causing `heartbeat_current` to report `false` when live PID `22236` was actively running. Updated check to `hb_state.upper() in {"ACTIVE", "RUNNING"}`.
- **Dashboard Context Separation:** Separated `active_production_run`, `latest_production_run`, `active_validation_run`, and `latest_validation_run` in `production_observability.py`, `main.py`, and `dashboard.html` (plus `#schedDetailSummary`, `#valWorkerBadge`, `#visualDiversity` field mapping, and quarantined/missing `#thumbFrame` labeling).

### Issue 6 — Stage 3.23.3 Gated Automatic Publishing Policy & Resource Safety Guard
- **Gated Automatic Policy:** Kept legacy `/api/stage-3-22/publishing/config` and `settings.PUBLISHING_MODE = "manual_approval"` intact for Stage 3.8/3.9/3.17/3.22 safety contracts, while implementing readiness-gated `control_center.publishing.policy` (`manual_approval` | `automatic`) in `control_center_service.py` (`evaluate_publishing_readiness()`, `is_automatic_publishing_authorized()`, `set_publishing_policy()`) and `PUT /api/control-center/publishing/policy` in `main.py`.
- **Resource Safety Guard:** Added `evaluate_resource_safety(max_cpu_pct=92.0, max_ram_pct=90.0)` in `control_center_service.py` and wired it into `scheduler.py::run_cycle_generation` so cycles defer rather than overload the host (observed live at `12:53:09` IST when CPU hit `96.9%`, deferring cleanly until calm and starting at `12:53:54` IST).

---

## 3. Environment & External Provider Audit

- **Configured in `.env.local` (key names only):** `CRAZZYDOCS_SMTP_HOST`, `CRAZZYDOCS_SMTP_PORT`, `CRAZZYDOCS_SMTP_SECURITY`, `CRAZZYDOCS_SMTP_USER`, `CRAZZYDOCS_SMTP_SENDER`, `CRAZZYDOCS_ALERT_RECIPIENT`, `CRAZZYDOCS_SMTP_PASSWORD` (`email_monitoring_service.is_smtp_configured() == True`).
- **Unconfigured optional keys in `.env.local`:** `GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `UNSPLASH_ACCESS_KEY`.
- **Verified Working Fallbacks:**
  - Topic discovery: `CuriosityTopicEngine.discover_and_rank_live_topics()` (live probe: `12` candidates, `0` failed providers across `physorg_*_rss` and `wikipedia_search`).
  - Research & script: `MultiCategoryScriptEngine`, `ScriptwriterAgent`, `SEOPublisherAgent` curated/factual fallbacks verified across tests and live Cycle 3 (`SoHo Weekly News`).
  - Media sourcing: `MediaAcquisitionEngine` public archives (Wikimedia Commons succeeded live on Cycle 3; `Internet Archive` hit local SSL chain warning and `Openverse` hit a `'NoneType' object has no attribute 'lower'` warning in Cycle 3 logs).
  - TTS & Rendering: `edge_tts` installed (`True`), FFmpeg present at `D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffmpeg.exe` (`True`).
  - YouTube OAuth: DPAPI-protected OAuth credentials valid (`oauth_connected: true`, `youtube_upload_scope: true`, `token_refreshable: true`, channel `CrazzyDocs` / `@crazzydocs` / `UCUOZOEBngXgt6W7fmus0bvw`).
