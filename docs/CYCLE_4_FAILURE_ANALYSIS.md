# Cycle 4 Failure Analysis: `cycle_4_20261113_1800`

| | |
|---|---|
| Investigated | 2026-11-13 18:56–19:05 IST, main `c19abbd` (running code fingerprint `5fb6e763701b6d39`) |
| Method | Read-only SQLite (`mode=ro`), logs, progress events, `ffprobe` on the output |
| Not done | No status change, no retry, no database write, no YouTube call |

## Summary
Cycle 4 is the **first cycle that rendered successfully** since the readiness fix. The page signalled READY, every frame rendered, and FFmpeg muxed the narration. The result is a valid 54.38 s, 1080×1920 video.

It then failed one line later, on a **latent code defect** dating from 11 Nov (`9ef46c3`): `prod_ws.save_manifest(...)` calls a method `ProductionWorkspace` has never had. Every earlier cycle failed before reaching that line, so it was never executed. The PERMANENT classification is technically right, because retrying the same code fails the same way. But the job threw away a finished video's progress: nothing was marked preserved, and nothing can resume from the render.

## Findings
1. **Failure time:** 2026-11-13 18:06:45.834 IST (`CYCLE_DISPATCH_FAILED`, `PRODUCTION_FAILED` progress event).
2. **Last completed stage:** video rendering, including the mux. The job row still says `VOICEOVER`/55 % because the stage indicator isn't updated during rendering (a separate cosmetic defect).
   - `STORY_DEVELOPMENT` 18:00:49 → `VOICEOVER` 18:02:47 → storyboard verified 18:02:47.
   - Film package built 18:02:47.
   - `video.mjs` render 18:02:47 → 18:05:44, about 3 min.
   - Mux 18:05:44 → 18:06:45.
3. **Exception and traceback** (`logs/scheduler_daemon.log`, lines 11146–11165):
   ```
   Master pipeline failed at video_rendering: 'ProductionWorkspace' object has no attribute 'save_manifest'
   scheduler.py:538 run_cycle_generation → :485 _execute_claimed_worker → :530 _run_registered_worker
   → crazzydocs_master_orchestrator.py:1533 execute_production_pipeline
   → crazzydocs_master_orchestrator.py:1002   prod_ws.save_manifest(manifest_data)
   AttributeError: 'ProductionWorkspace' object has no attribute 'save_manifest'
   ```
4. **Renderer started:** yes ("Rendering frames via Lemo-Opuscar video.mjs" at 18:02:47).
5. **`window.READY`:** succeeded; the render finished and wrote `lemo_film/silent_cycle_4_20261113_1800.mp4` (24.4 MB).
6. **Media assets loaded:** yes; 13 animation clips and the other assets are in `lemo_film/assets`, and the render completed.
7. **FFmpeg started and finished:** yes. `video/showcase_cycle_4_20261113_1800.mp4`, 7.32 MB, h264 1080×1920 at 30 fps plus AAC, 54.383 s. The mux return code was OK, because the code only reaches line 1002 after the mux check passes.
8. **Classification:** `retry_policy.classify_error` treats `AttributeError` as PERMANENT, which is correct for a deterministic code defect. The flaw is in what the classification loses:
   - the failure happened *after* a valid render;
   - `preserved_artifacts` is `null`;
   - there's no post-render classification, so no path can reuse the finished video.

   Retrying from scratch would also fail at the same line until the code is fixed.
9. **Artifacts are intact** (51 MB in `media/productions/cycle_4_20261113_1800/`): stage cache, voiceover, film pack, the silent render and the final MP4. Retention cleanup runs only from a manual script (`backend/scripts/execute_controlled_cleanup.py`), so nothing deletes them automatically. Note that a FAILED job without `preserved_artifacts` is classed as cleanup-eligible "failed output" by `media_lifecycle_manager`.
10. **No YouTube operation was attempted.** The failure came before QC and the queue entry; the publishing ledger is unchanged (latest #284).

## Secondary observations
- **The reconciler touched a live job.** At 18:00:29 the startup reconciler ran its "inside recovery window" branch on the *running* job: it set `recovery_reason=STARTUP_RECONCILIATION`, cleared `heartbeat_at` and recorded `RECOVERING`. The worker registry stopped a second worker (no second `WORKER_CLAIMED`), and the heartbeat pump restored the heartbeat, so there was no harm. Still, that branch should skip RUNNING jobs whose worker is alive.
- **Topic repetition:** all four cycles today used the topic "Science fiction" (cycles 1–3 via checkpoint reuse; cycle 4 as a new job). That points at topic freshness and de-duplication.
- **Processes restarted at 18:13:37** (monitor, scheduler PID 7752, dashboard PID 1968), after the failure. Deployment status is OK.

## Root cause
`backend/app/orchestration/crazzydocs_master_orchestrator.py:1002` calls the missing `ProductionWorkspace.save_manifest(dict)`. The class only has `_save_manifest()`, which *rebuilds* the manifest from registered artifacts and takes no argument. It was added in `9ef46c3` (11 Nov) and never ran, because rendering never succeeded before today.

## Recovery recommendation
1. **Fix (small, required before the 00:00 Cycle 1, which will otherwise fail at the same line).** Add `ProductionWorkspace.save_manifest(data: dict)`, which writes the given manifest atomically (temp file plus `os.replace`). Or write the renderer fields into `prod_ws.manifest` and call `_save_manifest()`. Add a test that runs the orchestrator's post-render block with a mocked render and mux, to cover the path tests never reached.
2. **Harden.** Treat any failure after the mux as `POST_RENDER_FAILURE`, with the final MP4, silent render and film pack recorded in `preserved_artifacts`, so a resume skips rendering. Make the reconciler skip RUNNING jobs whose worker is alive.
3. **Cycle 4 itself:** its recovery window ends 22:00 IST, and the operator retry deliberately refuses PERMANENT failures. Even after the fix, a retry would redo rendering (about 3 min) plus QC. The pre-render stages are cached, so the total is roughly 5–8 min, with the 00:00 publication target still reachable. Doing that needs either a code fix plus an explicit override of the PERMANENT block, or a new "resume from rendered video" operation; neither exists today. **Recommended:** deploy the fix, let Cycle 1 (00:00 → 06:00) be the first complete run, and keep Cycle 4's files for inspection. Retry Cycle 4 only with your explicit approval.
