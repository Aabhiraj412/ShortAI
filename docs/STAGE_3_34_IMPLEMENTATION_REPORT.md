# Stage 3.34: Controlled Production Validation and Reliability. Implementation report

**Date:** 2026-11-21 (IST). **Baseline:** `2bae573` (stages 3.31–3.33).
**Scope:** reliability only. No architecture change, new scheduler or worker, schedule change or feature activation.

## 1. Baseline audit (before any change)

| Item | Finding |
|---|---|
| Git | Worktree, local `main` and `origin/main` at `2bae573`. User-owned changes: `backend/app/content/topic_history.json`, `backend/data/creative_policy_config.json`; untracked `backend/backups/`, `backups/`, `media/`. All preserved |
| Processes | Monitor 22720, scheduler daemon 13688 (CURRENT), dashboard 22856 (current). Exactly one of each |
| Live job | `cycle_4_20261121_0300` RUNNING when the audit started: **observed, not touched** (§4 of the validation report) |
| Schedule | Profile CUSTOM, revision `ccf0bd04f159`, 24 hourly cycles, 45-minute recovery windows. Unchanged |
| Pre-change backup | `D:\Agents\CrazzyDocs_backups\predeploy_334_2bae573_20261121_030158` (studio.db with integrity `ok`, git bundle verified, uncommitted patch, creative-policy config, progress store, deployment metadata) |

## 2. Defects: evidence, root cause, fix

### D1. Dead processes counted as alive (worker recovery, monitor relaunch, daemon status)

- **Evidence:** on 2026-11-21 a killed monitor's lock blocked every relaunch, fixed for the process lock only in `523010a`. The same check was still used by `deployment_diagnostics.is_pid_alive` (worker liveness for stall recovery, daemon status) and by `production_health_dashboard` (daemon state).
- **Root cause:** on Windows, `OpenProcess` also succeeds for a process that has **exited** while another process still holds a handle to it.
- **Impact:** a crashed worker could look alive forever, so its job was never recovered and burned its recovery window, and the dashboard showed a dead daemon as RUNNING. The fail-safe direction (no duplicate worker) was preserved; recovery was what got blocked.
- **Fix:**
  - one shared `core/process_utils.pid_running` (requires `GetExitCodeProcess == STILL_ACTIVE`; fail-safe True if unreadable);
  - used by `deployment_diagnostics`, `production_health_dashboard` and `reconciliation_service`;
  - the control plane already did this.
- **Tests:** an exited-but-referenced child is not alive; a dead worker with a stale heartbeat is stalled; a live worker with a 10,000 s-old heartbeat is **not** stalled; the heartbeat pump never revives a non-RUNNING job.

### D2. WinError 32 in asset acquisition (2 production failures, 2026-11-20 01:32 and 2026-11-12)

- **Evidence:** traceback `downloader.py:130`, at that revision `os.remove(temp_path)` **inside** `with open(temp_path, "wb")` (the 25 MB size-limit branch). The source was a large Openverse pan/crop image.
- **Root cause:**
  - the temp file was deleted while the process's own write handle was open; Windows refuses with WinError 32, which is classified PERMANENT and failed the production;
  - compounding it, the hash, byte counter and header were **not reset between retries**, so a retry after a mid-stream drop double-counted bytes and could trip the size limit falsely;
  - connection resets and `IncompleteRead` were not retried, and they left orphan `.tmp` files.
- **Fix (`media/downloader.py`):**
  - every attempt starts clean;
  - an oversize download stops writing, closes the handle, then deletes;
  - every transport error type (`URLError`, `TimeoutError`, `ConnectionError`, `http.client.HTTPException`) is retried with cleanup;
  - every removal and the final replace go through `core/fs_utils` (retrying only WinError 32 and 5, never other errors; a lock left during cleanup is logged and never raised).
- **Tests:** oversize without a lock error and no orphan; retry after a mid-stream drop yields the correct hash; 4 network error types retried then failing cleanly; corrupt and wrong-type media rejected and cleaned; an identical asset is reused, not overwritten; and a Windows test documents that deleting an open file raises WinError 32.

### D3. WinError 32 replacing the voiceover after tempo or padding adjustment (2026-11-12)

- **Evidence:** `os.replace(voiceover_*_tempo.mp3 → voiceover_*.mp3)` failed right after FFmpeg and ffprobe had used the file.
- **Root cause:** a transient sharing violation on a file another process had just touched (one occurrence).
- **Fix:** `fs_utils.replace_with_retry` (bounded, about 4.7 s worst case, only WinError 32 and 5) for both the tempo and padding replacements.
- **Tests:** retries a transient lock with backoff; never retries other errors.

### D4. Partial voiceover file left after a final TTS failure

- **Fix:** `expressive_tts_engine` removes the partial `_raw.mp3` before raising.
- **Test:** a voiceover timeout makes 3 attempts, then fails as TRANSIENT with no partial file.

### D5. Status showed a stage the worker had left (observability)

- **Evidence (live, 03:00 cycle):** `persistent_jobs.current_stage` stayed `VOICEOVER` from 03:02 through asset acquisition, animations, the browser render and the mux (about 6 minutes). The deployment status (`worker.stage`) and the Overview "Stage" tile both showed it.
- **Root cause:** those displays read the job's coarse checkpoint stage, not the worker's live progress store.
- **Fix:**
  - `deployment_diagnostics._live_stage` reads the worker's progress store (fresh within 15 minutes) and returns stage, operation, stage progress and overall progress;
  - the checkpoint stage is kept as `checkpoint_stage`;
  - the Overview tile uses the live stage.
- **Test:** live, stale and missing store cases.

### D6. Shadow topic-intelligence evidence was not persisted (3.31K follow-up)

- **Evidence:** after the first shadow run, `topic_demand_signals` had 75 rows, but no v2 score was stored anywhere, and the run notes held only demand titles.
- **Impact:** the planned 48 h shadow period would have produced no evidence for the activation decision.
- **Fix:**
  - the run notes now hold the full demand summary (signals, candidates with demand index and velocity, skipped subjects, errors) and every candidate's v1, v2 and blended scores with dimensions;
  - ledger rows carry `_v2` and `_v1_score` in `score_breakdown_json`;
  - selection is unchanged (still shadow).
- **Test:** shadow evidence persisted in ledger and notes.

### D7 (Phase C). Final videos delivered as full-range BT.601 (`yuvj420p`, pc)

- **Evidence:** measured on production files, **97 % of frames reach luma 255** (above the 235 limited-range ceiling), and the matrix is tagged BT.601. This is genuine full-range data from the browser's JPEG frame pipe; the final `-pix_fmt yuv420p` keeps the range.
- **Why correct it:** players that ignore the range flag clip those highlights, and Pop Info designs are bright (mean luma about 183, many pixels at 255).
- **Fix:** both final muxes (`pop_info/pipeline.py`, and the orchestrator's Lemo film path) use the shared `DELIVERY_COLOR_VF`: limited range, BT.709 matrix, primaries and transfer, `yuv420p`. It is applied in the encode that already happens, so there is no extra generation.
- **Before/after on the real 03:00 film:**

| | Before | After |
|---|---|---|
| Pixel format / range / matrix / primaries / transfer | yuvj420p / pc / bt470bg / unknown / unknown | **yuv420p / tv / bt709 / bt709 / bt709** |
| Luma max (300 frames) | 255 (293 frames) | 236 |
| Frames / duration | 1536 / 51.2 s | 1536 / 51.2 s (identical) |
| Mux time (2 runs) | 44.8, 49.4 s | 51.8, 53.3 s (**about +5 s, +11 %**) |
| Size | 12.8 MB | 11.8 MB (−7 %) |
| PSNR after decoding each correctly | — | 40.0 dB average, 37.8 dB minimum (visually identical) |

- **Tests:** both muxes use the filter; a synthetic full-range capture comes out `yuv420p`/tv/bt709 with frames preserved and luma ≤ 240.

## 3. Investigated, no change needed (evidence-based)

| Item | Finding |
|---|---|
| Renderer readiness timeout ("page never signalled ready") | 2 failures, both 2026-11-13 ("still waiting for window.READY", no console errors). The root cause, a film page that never set `READY`, was fixed the same day (`c8d1c68`). The Pop Info page bounds every image load (20 s timeout and error handler) and font load, then always sets READY. **0 occurrences in about 100 productions since.** The timeout was not increased; a test guards the page's bounded readiness |
| Duration validation failures (`49.20 s outside 50-58 s`) | 18 occurrences, 2026-11-12 to 11-18, all before Stage 3.29 set the window to 30–58 s. **None since.** No stale 50–58 check remains in code (only comments). Recent final videos measured 51.2–54.2 s, all accepted |
| False `WORKER_STALLED` during long stages | `worker_registry.HeartbeatPump` beats every 30 s through every stage; stall requires both a dead worker and a heartbeat older than 180 s. **Live:** the heartbeat was at most 27.1 s old through the whole 03:00 cycle, including the render. The only real gap was the liveness defect D1, now fixed |
| One renderer at a time | Lemo-Opuscar with `RENDER_SLOTS=1` (a global slot) and `--workers 1`; `WorkerRegistry` refuses a second live worker per job; the scheduler skips a duplicate trigger for a RUNNING job. Live: one render tree (node plus Edge) during the cycle |
| Upload and publication | Existing gates unchanged: auto upload is private-only with YouTube-native `publishAt`; `ALREADY_PUBLIC` / `NEEDS_REVIEW` hand off to the operator; interrupted uploads are marked resumable at startup; quota exhaustion is PERMANENT with a quota re-arm limited to the recovery window (3.30). Covered by the existing publishing and quota suites (green) |
| Community publishing | Separate workflow (3.32E/F). API publishing stays OFF; it waits for Google pilot access and a `youtube.force-ssl` re-consent. Manual handoff unchanged |

## 4. Files

- **New:** `backend/app/core/fs_utils.py`, `backend/tests/test_production_reliability_334.py`, the three Stage 3.34 documents.
- **Modified:**
  - liveness: `core/process_utils.py`, `services/deployment_diagnostics.py`, `services/production_health_dashboard.py`, `scheduler/reconciliation_service.py`, `autonomy/control_plane.py` (docstring);
  - file handling: `media/downloader.py`, `services/expressive_tts_engine.py`, `rendering/documentary_storyboard_builder.py`;
  - delivery format: `rendering/ffmpeg_geometry.py`, `rendering/pop_info/pipeline.py`, `orchestration/crazzydocs_master_orchestrator.py`;
  - topic evidence: `content/curiosity_topic_engine.py`, `backend/tests/test_topic_demand_331k.py`;
  - docs: `docs/PROJECT_HANDOVER.md`, `docs/STAGE_3_32E_TEST_RESULTS.md`.

## 5. Deployment and rollback

- **Affected processes:** the scheduler daemon (all production code paths) and the dashboard (status displays, health). The monitor is unaffected.
- **Rollback:** `git revert` the Stage 3.34 commit, then restart the daemon through the restart endpoint (when it reports safe) and the dashboard. The pre-change backup above restores the database and config if ever needed. No schema change; no data migration.
