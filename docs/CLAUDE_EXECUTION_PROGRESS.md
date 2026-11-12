# Claude Execution Progress

Branch: `claude/crazzy-docs-verify-capabilities-ac634e` (worktree `.claude/worktrees/crazzy-docs-verify-capabilities-ac634e`).
Not merged into `main`. Live daemon/dashboard were **not** restarted or modified.
Last updated: 2026-11-13 IST

## Status by phase (directive of 2026-11-13)

| # | Phase | Status |
|---|---|---|
| 1 | Resolve 3 known test failures | **Done** |
| 2 | Test database isolation | **Done** (media isolation partial, see Remaining) |
| 3 | Protect YouTube publishing | **Done in code**; live DB row still `automatic` → needs your approval (see below) |
| 4 | Bounded retries | **Done** |
| 5 | Completion boundary at queue handoff | **Done** (also fixed a crash that blocked every queue insert) |
| 6 | Remove publication coupling | **Backend/scheduler/validation done**; publication-slot API + dashboard UI still to remove |
| 7 | Stale-process diagnostics | **Done** (takes effect after the next controlled restart) |
| 8 | Testing | Full suite run after each phase; results below |
| 9 | Git integration | Analysis done; **not merged** (procedure below) |

## Phase 1: the three failures
- `test_unified_schedule_editor` #02 and #21: the **tests** were wrong. The 18:29 change had deliberately stopped activating/validating publication slots together with production ("publication is user-controlled"), which is what Phase 6 requires. Rewritten to assert the decoupled contract strictly (publication spec has no effect on activation/validation; invalid production still fails).
- `test_user_controlled_publishing`: it read `studio_settings` through the global `SessionLocal` → project `backend/studio.db`. Now has a per-test temporary DB fixture, and also asserts that a stored legacy `automatic` value cannot cause a public upload.

## Phase 2: test database isolation strategy
1. `paths.DATABASE_PATH` honours `CRAZZYDOCS_DATABASE_PATH`; every app module that hardcoded `backend/studio.db` now uses it (`db.py`, `topic_ledger.py` (had the absolute `D:\Agents\CrazzyDocs\backend\studio.db`), `youtube_publishing_engine`, `notification_service`, `daily_publishing_summary`, `production_health_dashboard`, `media_lifecycle_manager`, `lifecycle_events`, `startup_manager`).
2. `tests/conftest.py`, before any app import: puts its own checkout first on `sys.path` (the venv's `crazzydocs.pth` otherwise makes `import backend` load the main checkout), sets `CRAZZYDOCS_DATABASE_PATH` to a throwaway file, creates the schema once per session, and **aborts the session** if `backend.app` or `DATABASE_PATH` resolve anywhere else.
3. Session-wide guard: `sqlite3.connect` raises `TEST ISOLATION VIOLATION` for any production `studio.db` (this checkout's, and every `sys.path` entry's). SQLAlchemy goes through the same function.
4. Lifecycle events in TEST context are only persisted with an explicit `db_path`.
5. Live-DB audit tests (`3_21b`, `3_21c`, two in `3_17`) are skipped with a reason; they assert production row counts and belong in a read-only audit script.
6. Outside the main checkout, tests that hardcode `D:\Agents\CrazzyDocs` are skipped (191), so a worktree run never touches production media. Worktrees borrow main's gitignored `tools/` read-only via `CRAZZYDOCS_TOOLS_DIR`.
7. `backend.__path__` is pinned to this checkout (legacy tests insert `D:\Agents\CrazzyDocs` into `sys.path` at import, which otherwise made later `backend.*` imports load main's code mid-run); collection and session end fail if any `backend.*` module came from elsewhere.
8. Removed destructive cleanup from `test_stage_3_10` / `3_13a` (DELETEs on the live DB and `rmtree` of the whole main `media/thumbnails` and `media/audit` folders).

**Verification (2026-11-13):** read-only content fingerprint of the live `studio.db` (`sha256 ce566169…`) identical before and after every full run; the main checkout's `media/` tree (327 files) unchanged by the final runs. Evidence that earlier test runs *did* reach production: `test_auto_job_001` and `test_fail_job_002` exist in live `persistent_jobs`; an earlier run in this session (before the media containment) overwrote two obsolete test renders in main `media/audio|video` (`prod_55sec_narr.mp3`, `production_55sec_short.mp4`, both classified obsolete test artifacts by Stage 3.21E; no production file affected).

## Phase 3: publishing safety
Code paths that could publish publicly (all now closed except explicit user actions):
| Path | Before | Now |
|---|---|---|
| `youtube_service.approve_and_upload` | public upload when policy `automatic` and row not private | always private, `allow_public_bypass=False` |
| `scheduler.run_cycle_publishing` → `process_publishing_queue` | uploaded when `automatic` authorised | never uploads in background |
| Orchestrator Stage 14 | computed `enqueued_public` label | private `pending_queue` only |
| `set_publishing_policy` / API PUT | could enable `automatic` | `automatic` rejected (HTTP 409) |
| `get_publishing_policy` | returned stored value | always `manual_approval`; stored value exposed read-only as `stored_mode` |
| `youtube_service.publish_now` | explicit user action, confirm=True | unchanged (intended) |
| `edit_and_schedule` | private + user-selected `publishAt` | unchanged (intended) |

Live state (read-only check, 01:55 IST): `control_center.publishing.policy = automatic`; publishing ledger has **one** row (#284, `gPcd3gXF984`, already `uploaded_public`), **no scheduled or pending videos**, so no YouTube schedule is affected. History and YouTube IDs untouched.

**Required action (needs your approval):** the running daemon (PID 2832) still executes pre-18:02 code that reads `automatic`. Until it is restarted on this branch's code, the only way to stop automatic publishing in the live process is to set the stored policy to `manual_approval` (one `studio_settings` row; e.g. dashboard Publishing → Manual approval, or `PUT /api/control-center/publishing/policy {"mode":"manual_approval","confirm":true}`). Next production cycle: `cycle_4` at 17:30 IST.

## Phase 4: retry policy (`backend/app/scheduler/retry_policy.py`)
- Every failed attempt increments `retry_count` once (`record_failure`); resumes after interruptions/network waits no longer count; the reconciler no longer adds +1 every 45 s.
- PERMANENT (NameError, ImportError, AttributeError, TypeError, …): terminal `FAILED` on the first attempt.
- TRANSIENT: `RETRY_SCHEDULED` with backoff 2, 4, 8, 16, 30 (cap) min; terminal `FAILED` at `max_retries` (3).
- Failure after a valid render: `POST_RENDER_FAILURE`, terminal, video path recorded under `cycle_state.preserved_artifacts`; never re-run (would regenerate).
- Reconciler no longer resurrects `FAILED` jobs (root cause of the 45× `NameError` storm on `cycle_4_20261112_1730`).
- Per-attempt orchestrator failure emails suppressed for scheduled runs; one report for the terminal outcome.
- Error details are prefixed `[CLASSIFICATION] Type: message (summary)`; cleared on success.

## Phase 5: completion boundary
`VIDEO_GENERATED → QC_PASSED → PUBLISHING_QUEUE_ENTRY_CREATED → PRODUCTION_CYCLE_COMPLETED`.
- Production jobs are no longer marked `COMPLETED` at Stage 12; Stage 14 saves a `queue_handoff` checkpoint and `complete_queue_handoff()` inserts the queue entry, then completes the job. No YouTube call, upload, or user input involved.
- Idempotent: same video hash returns the existing ledger row in any state; rechecks the video hash before queuing.
- Handoff failure → `HANDOFF_PENDING`; the recovery loop retries only the handoff (bounded, backoff), never the pipeline.
- **Bug found:** Stage 14 called `self._save_checkpoint`, which does not exist → every production run on the current main code would have crashed after QC and never reached the queue. Fixed by the rewrite.

## Phase 6: publication coupling
Done: production validation no longer requires/constrains `target_publish_time` (kept verbatim as legacy; old revisions stable); publication buffer, 24 h cap, publish-slot conflict and "production window" (start→publish) checks removed; recovery window is no longer clipped by a publish time; scheduler no longer marks jobs `MISSED` against a publication SLA; preview/logs show recovery deadline instead of publish target; legacy ledger slot context exposed read-only as `legacy_schedule_context`.
Not done yet: the publication-slot schedule subsystem (`control_center_publication_schedule_*` settings, `/api/control-center/publication-schedule*`, mappings/unified timeline, publication recovery timeline, reconciler's `calculate_next_available_publishing_slot`) and the dashboard's publication-slot UI. These still exist but no longer influence production.

## Phase 7: deployment diagnostics
- `core/code_revision.py`: SHA-256 over backend source (line-ending normalised); `LOADED_CODE_REVISION` fixed at import.
- Daemon records PID, start time, loaded revision, heartbeat (`control_center.daemon.deployment`).
- `GET /api/control-center/deployment`: daemon/dashboard PID, start, loaded vs on-disk revision, `STALE_CODE` / `UNKNOWN_REVISION` (a daemon that never reported, i.e. the current PID 2832, counts as stale), schedule sync, worker heartbeat, last successful production, restart state. Dashboard shows a red **STALE CODE** banner.
- `POST /api/control-center/deployment/restart {"confirm":true}` only sets a flag; the daemon exits at its next loop **only when no job is QUEUED/RUNNING/RETRYING**, and the startup monitor relaunches it (≤30 s). Nothing kills processes.
- Removed the dead duplicate `main()` in `run_scheduler_daemon.py`.

## Phase 8: test results
| Run | Passed | Failed | Skipped | Notes |
|---|---|---|---|---|
| Baseline: `702c610` + isolation only | 362 | 64 | 4 | before the media containment |
| This branch, phases 1–5 | 260 | 5 | 191 | 0 regressions vs baseline; 191 = main-path tests skipped in worktree |
| This branch, all phases (final) | 272 | 1 | 191 | live DB fingerprint unchanged; main `media/` unchanged; no `backend.*` module loaded from another checkout |

Remaining failure (pre-existing, also on baseline): `test_stage_3_8_integration::test_end_to_end_orchestration_run`, blocked by the Stage 3.23.4 renderer-compliance gate (test pipeline uses `CleanSlateRenderer`; only Lemo-Opuscar is allowed). Needs a test that renders via Lemo-Opuscar or mocks it.
New tests: `test_lifecycle_events`, `test_publishing_safety`, `test_retry_policy`, `test_completion_boundary`, `test_deployment_diagnostics`.

## Phase 9: integration analysis (not executed)
- All 157 uncommitted files in the main checkout are byte-identical to `21e6fd8`; none changed since. The branch = main's current state + this work. No duplicate or conflicting edits.
- Git refuses to fast-forward over a dirty tree even with identical content, so integration = (1) re-run the comparison, (2) reset main's working copy to `HEAD` (all content is preserved in `21e6fd8`), (3) `git merge --ff-only refs/heads/claude/crazzy-docs-verify-capabilities-ac634e`, (4) controlled daemon restart via the new endpoint. Requires your go-ahead.

## Remaining issues / next steps
1. **Approval:** set live publishing policy to `manual_approval` before 17:30 IST (one settings row).
2. **Approval:** integrate into `main`, then controlled restart of the scheduler daemon and dashboard so the fixes run live.
3. Remove the publication-slot subsystem + dashboard UI (Phase 6 remainder), verified in the browser.
4. Media isolation: ~15 app modules and ~20 tests still hardcode `D:\Agents\CrazzyDocs` (media/compositor/downloader/tts …).
5. Make the live pipeline resumable per stage (TTS and media acquisition re-run on retry; video reuse depends on duration match).
6. Convert live-DB audit tests into a read-only snapshot audit script.
7. Fix `test_stage_3_8_integration` for the Lemo-Opuscar-only renderer policy.

## Commits on this branch
- `21e6fd8` checkpoint snapshot of main's uncommitted tree
- `702c610` lifecycle events import fix
- (this session) test DB isolation; publishing safety, retries, completion boundary, decoupling, diagnostics
