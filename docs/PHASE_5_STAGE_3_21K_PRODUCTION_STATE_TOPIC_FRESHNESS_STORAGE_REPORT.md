# Phase 5 — Stage 3.21K Engineering Report
## Production State, Topic Freshness & Storage Reconciliation

**Project:** CrazzyDocs Autonomous Production Studio (`D:\Agents\CrazzyDocs`)  
**Date:** 2026-11-11 (IST)  
**Baseline Commit:** `6b56820` (`main -> origin/main`)  
**SQLite Pre-Change Backup:** `backend/backups/studio_backup_pre_stage_3_21k_20261111_124616.db` (`6,483,968` bytes, `PRAGMA integrity_check = ok`, SHA-256 `df8206203ce025201e33fd9df5741b4aed38af14029208880b139d3cc9a37879`)

---

## 1. Safety Verification & Operational Posture

| Safety Control | Verified State |
|---|---|
| `CrazzyDocs-Production-Scheduler` Windows Task | `Disabled` |
| Dedicated Scheduler Daemon Process | `STOPPED` (no scheduler lock / daemon PID) |
| Active Production Workers / FFmpeg | None (only dashboard `run_dashboard.py` on `127.0.0.1:8000`) |
| Active Publishing Queue (`publishing_ledger`) | `0` rows (`pending: 0`, `uploading: 0`, `published: 0`, `failed: 0`) |
| Publishing Archive (`publishing_ledger_archive`) | `253` rows untouched |
| Genuine Content Ledger (`content_ledger` / `topic_history.json`) | `9` genuine records / `9` entries — **0 validation/test pollution** |
| Active Schedule Profile | `FOUR_CYCLES_DAILY` (`SINGLE_DAILY_2300` inactive) |

---

## 2. Forensic Root Causes & Fixes Across All 5 Problem Areas

### Problem 1 & Problem 2 — Repeated Validation Topic & Repeated Thumbnail Headline (`"Land mines in the Falkland Islands"` / `"LAND MINES FALKLAND"`)
- **Verified Root Cause:**
  1. `TopicLedger.record_content()` (`backend/app/content/topic_ledger.py`) properly blocked non-`PRODUCTION` contexts (`VALIDATION`, `TEST`, `DEV`, `MAINTENANCE`) from writing into `content_ledger` and `topic_history.json`, **but no isolated validation topic history existed**, so consecutive validation runs (`val_3_21i_20261111_110449` and `val_3_21j_20261111_120906`) had zero memory of prior validation selections.
  2. `CuriosityTopicEngine.discover_live_candidates()` (`backend/app/content/curiosity_topic_engine.py`) queried only a single UTC date (`yesterday`, `2026/11/10`) on the Wikipedia Featured/OnThisDay feed, deterministically scoring the identical OnThisDay candidate `#1` (`9.20`), yielding byte-for-byte identical thumbnail PNG (`2,636,463` bytes, SHA-256 `237ca8ce...`), voiceover MP3 (`1,225,196` bytes), and script JSON (`2,301` bytes).
- **Implemented Fix:**
  1. Created **`backend/app/content/validation_topic_history.py`** (`ValidationTopicHistory`, storing isolated runtime history in `backend/data/validation_topic_history.json` with automatic `PYTEST_CURRENT_TEST` / `CRAZZYDOCS_TEST_MODE` test isolation). Tracks `topic_id`, `run_id`, `normalized_title`, `topic_fingerprint`, `research_fingerprint`, `script_fingerprint`, `storyboard_fingerprint`, `thumbnail_headline`, `discovery_source`, `execution_context`, `outcome`, `rejection_reason`, `selected_at`, and `expires_at` (`+30 days`).
  2. Added **semantic & headline-swap collision checks** (`compute_semantic_similarity`, `stem_token`, `semantic_token_set`) in both `ValidationTopicHistory` and `TopicLedger.check_content_collision()` covering exact titles, word-order permutations (`"Falkland Islands Land Mines"`), morphological stems (`"Mines in the Falklands"`), entity overlap, and shared research/script/storyboard fingerprints.
  3. Upgraded **`CuriosityTopicEngine`** for multi-source discovery diversity: rotates multi-date Wikipedia Featured feeds, adds live **Wikipedia Documentary Search** (`wikipedia_search:*`) across 8 factual topic domains, adds **Phys.org Earth/Space/Science RSS feeds**, pre-filters known collisions *before* hitting the Wikipedia enrichment cap, interleaves feeds round-robin with source/category novelty scoring, records full candidate audit metadata (`source`, `discovery_timestamp`, `publication_or_update_timestamp`, `research_availability`, `factual_confidence`, `novelty_score`, `semantic_similarity`, `curiosity_score`, `final_score`, `final_rank`), and **fails closed** (`RuntimeError`) when zero eligible fresh candidates exist.
  4. Updated **`CreativeThumbnailEngine.select_and_render_best_thumbnail()`** (`backend/app/rendering/engines/creative_thumbnail_engine.py`) to penalize `excluded_headlines` drawn from recent validation/production runs so repeated headlines are suppressed.
  5. Updated **`CrazzyDocsMasterOrchestrator.execute_production_pipeline()`** (`backend/app/orchestration/crazzydocs_master_orchestrator.py`) to record validation topic selection immediately upon choice and update completion fingerprints or `rejected` outcome (`24h` retry guard) automatically.

### Problem 3 — Dashboard `Pipeline Stage` Showing `IDLE` During Validation
- **Verified Root Cause:** `ProductionHealthDashboard.get_health_metrics()` (`backend/app/services/production_health_dashboard.py`) only inspected `persistent_jobs` (`JobModel.status == "RUNNING"`), whereas `VALIDATION` runs bypass `persistent_jobs` and write live state only to `ProductionObservabilityStore`.
- **Implemented Fix:**
  1. Updated `get_health_metrics()` so `pipeline_stage` reflects active `VALIDATION` runs (`VALIDATION: <STAGE> (<PCT>%)`) rather than misleadingly reporting `IDLE`.
  2. Added `ProductionHealthDashboard.get_reconciled_operational_states()`, explicitly separating and exposing across `/api/dashboard/overview`, `/api/dashboard/health`, and `dashboard.html`:
     - **`scheduler_state`**: `RUNNING` / `DISABLED` / `STANDBY` / `STOPPED`
     - **`production_worker_state`**: `IDLE` / `STARTING` / `RUNNING` / `COMPLETED` / `FAILED`
     - **`validation_worker_state`**: `IDLE` / `RUNNING` / `COMPLETED` / `FAILED`
     - **`overall_operational_state`**: `IDLE` / `VALIDATION ACTIVE` / `PRODUCTION ACTIVE` / `RECOVERY ACTIVE` / `DEGRADED` / `ERROR`

### Problem 4 — Storage Reconciliation Audit & Dashboard Deliverable Exposure
- **Verified Disk Audit Findings:**
  - Legacy flat folders `media/video/` (`0` files) and `media/thumbnails/` (`0` files) are empty; all generated deliverables live inside canonical isolated workspaces under `D:\Agents\CrazzyDocs\media\productions\<production_id>\`.
  - Audited **`val_3_21i_20261111_110449`** (`14` registered manifest artifacts, `0` missing, `0` SHA-256 mismatches):
    - Video: `media/productions/val_3_21i_20261111_110449/video/showcase_val_3_21i_20261111_110449.mp4` (`15,546,697` bytes, SHA-256 `1b176aea...`)
    - Thumbnail: `media/productions/val_3_21i_20261111_110449/thumbnails/thumb_val_3_21i_20261111_110449.png` (`2,636,463` bytes, SHA-256 `237ca8ce...`)
  - Audited **`val_3_21j_20261111_120906`** (`31` registered manifest artifacts, `0` missing, `0` SHA-256 mismatches):
    - Video: `media/productions/val_3_21j_20261111_120906/video/showcase_val_3_21j_20261111_120906.mp4` (`8,121,913` bytes, SHA-256 `dde9fd99...`)
    - Thumbnail: `media/productions/val_3_21j_20261111_120906/thumbnails/thumb_val_3_21j_20261111_120906.png` (`2,636,463` bytes, SHA-256 `237ca8ce...`)
- **Implemented Fix:**
  1. Upgraded `ProductionWorkspace._save_manifest()` (`backend/app/orchestration/production_workspace.py`) to expose top-level `workspace_abs_dir`, `canonical_video_path`, `canonical_thumbnail_path`, and structured `deliverables` summary (`final_video`, `thumbnail`, `voiceover`, `storyboard`, `qc_report`, `integrity_report`) while preserving `manifest_version: "3.21J"` and adding `schema_revision: "3.21K"`.
  2. Added verified deliverable APIs in `backend/app/main.py`:
     - `GET /api/dashboard/productions/{production_id}/video`
     - `GET /api/dashboard/productions/{production_id}/manifest` (with live disk & SHA-256 verification)
     - `GET /api/dashboard/productions/{production_id}/artifacts`
     - Enriched `/api/dashboard/progress*` records with resolved `storage` links and rendered clickable links + Windows workspace paths in `dashboard.html`.

### Problem 5 — Dashboard Log Flood (>2,440 lines in 15 min) & Excessive Polling
- **Verified Root Cause:** Pre-fix `task-10767.log` accumulated **2,699 lines**, of which **2,698 were unthrottled Uvicorn `GET 200` access logs** generated by `dashboard.html` fetching all **7 endpoints** (`/overview`, `/stats`, `/health`, `/history`, `/recovery`, `/activity`, `/progress`) every `2.5s` while active (`168 req/min` -> `~2,520 lines` in 15 min) and every `10s` while idle, AND `/api/dashboard/health` spawned `schtasks.exe` subprocesses on every tick (`324 * 2 = 648` subprocesses).
- **Implemented Fix:**
  1. **Backend TTL Caching (`production_health_dashboard.py`):** Added `60s` TTL cache on `get_windows_task_status()` and `30s` TTL cache on `_get_network_status()` (auto-bypassed under `PYTEST_CURRENT_TEST`), cutting warm `/api/dashboard/health` latency to `82.9 ms` with zero routine subprocess spawning.
  2. **Structured Rotating Logging (`backend/scripts/run_dashboard.py`):** Configured separate `RotatingFileHandler` logs (`5 MB`, `backupCount=3–5`) for `logs/dashboard_service.log`, `logs/dashboard_access.log`, and `logs/dashboard_errors.log` (`WARNING+` always recorded), plus `RoutinePollingAccessFilter` suppressing routine `200/304 GET /api/dashboard/*` heartbeat noise while keeping all errors (`>=400`) and mutating requests (`POST/PUT/DELETE`) logged.
  3. **Split Visibility-Aware Frontend Polling (`dashboard.html`):** Separated fast ticks (`5s` active / `20s` idle on `/overview` + `/progress`) from heavy panel refreshes (`60s` or manual/filter interaction), added single-flight `isFetchingDashboard` lock + single-init guard, and paused background polling whenever `document.hidden === true`.

---

## 3. Controlled Validation Results (After Final Edits)

### A. Live Two-Selection Topic Discovery Validation
Seeded prior `val_3_21i` and `val_3_21j` records into `backend/data/validation_topic_history.json` (`"Land mines in the Falkland Islands"`):
- **Validation Test A (`val_3_21k_test_a`):** Selected **`Jensen Huang`** (`source=wikipedia_mostread_1110`, `composite_score=9.44`, `novelty_score=9.8`, `semantic_similarity=0.02`, `final_rank=1`) and recorded in `validation_topic_history.json`.
- **Validation Test B (`val_3_21k_test_b`):** Excluded both `"Land mines in the Falkland Islands"` and `"Jensen Huang"`, selecting **`Black Monday (1987)`** (`source=wikipedia_search:historic`, `composite_score=9.03`, `novelty_score=10.0`, `semantic_similarity(A, B)=0.0`, `final_rank=1`).
- **Database Isolation Verification After Test A + Test B:**
  - `content_ledger`: `9` (unchanged)
  - `topic_history.json`: `9` (unchanged)
  - `publishing_ledger`: `0` (unchanged)
  - `publishing_ledger_archive`: `253` (unchanged)
  - `validation_topic_history.json`: `4` isolated validation entries

### B. Live Dashboard & Log Growth Verification
- `GET /api/dashboard/overview`: `200 OK` -> `scheduler_state: DISABLED`, `production_worker_state: IDLE`, `validation_worker_state: COMPLETED`, `overall_operational_state: IDLE`, `active_profile: FOUR_CYCLES_DAILY`, `overall_health: HEALTHY`
- `GET /api/dashboard/productions/val_3_21j_20261111_120906/{artifacts,manifest,thumbnail,video}`: all `200 OK`
- **30 consecutive routine `GET 200` polls** across `/overview`, `/progress`, `/health` yielded **`delta=0` lines** across `dashboard_service.log`, `dashboard_access.log`, `dashboard_errors.log`, and daemon console stdout.

### C. Automated Test Suite Results
- Stage 3.21H, 3.21I, 3.21J, 3.21K + `test_stage_3_4_production` (selected non-legacy tests): **38 passed, 4 skipped** (existing missing-legacy-media skips), **0 failed** in `86.91s`.
