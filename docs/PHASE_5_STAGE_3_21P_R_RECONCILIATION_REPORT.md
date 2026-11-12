# Stage 3.21P-R — Post-Implementation Reconciliation & Verification

**Audit date:** 2026-11-11 (Asia/Kolkata)  
**Scope:** Verify Stage 3.21P, reconcile stale validation presentation, and make only a narrowly scoped dashboard correction. No Stage 3.21Q work was started.

## 1. Git state and preservation

- Branch: `main`.
- At entry, the worktree already contained 19 modified tracked files and 20 untracked paths. All were preserved; no reset, stash, checkout, discard, reformat, stage, commit, or push was performed.
- This stage modified the existing `production_observability.py` and `dashboard.html`, added a regression test to the previously clean Stage 3.21K test module, and added this report. No other existing work was changed.
- After this stage, the worktree contains 20 modified tracked files and 21 untracked paths, including this report. No staged, deleted, or renamed entries were introduced.

**Tracked modifications preserved:**

`backend/app/content/creative_identity_policy.py`, `creative_production_policy.py`, `visual_diversity_policy.py`; `backend/app/core/agent_identity.py`, `config.py`; `backend/app/main.py`; `backend/app/orchestration/crazzydocs_master_orchestrator.py`; `backend/app/rendering/documentary_storyboard_builder.py`, `explainer_animation_engine.py`, `styles/style_system.py`; `backend/app/scheduler/schedule_profiles.py`; `backend/app/services/production_observability.py`; `backend/app/templates/dashboard.html`; `backend/data/creative_policy_config.json`; `backend/tests/test_production_duration_standard.py`, `test_stage_3_17_deployment.py`, `test_stage_3_21j_visual_diversity_persistence_observability.py`, `test_stage_3_21k_reconciliation_freshness_storage.py`, `test_stage_3_21m_creative_policy_and_acquisition.py`, and `test_stage_3_8_integration.py`.

**Untracked work preserved:**

- `backend/app/rendering/creative_direction.py`, `independent_composers.py`, `style_treatments.py`
- `backend/backups/`
- `backend/scripts/run_stage_3_21n_multicategory_validation.py`, `run_stage_3_21o_renderer_diversity_validation.py`, `run_stage_3_21p_black_hole_poc.py`
- `backend/tests/test_stage_3_21n_visual_policy.py`, `test_stage_3_21o_renderer_design.py`, `test_stage_3_21p_creative_architecture.py`
- Existing handover, Stage 3.21N/O/O-R/O-R2/P reports and Stage 3.21N/O validation JSON files under `docs/`

The initial and final repository state was checked with `git status --short`; `git diff --check` found no whitespace errors. Git emitted existing LF-to-CRLF working-copy warnings.

## 2. Stale validation runs

Process inspection found no worker matching the target IDs or validation runner and no `ffmpeg.exe`. The only scheduled Python process after dashboard refresh was the dashboard service, not a validation worker. The two matching records have no heartbeat within the 300-second freshness window, no terminal progress event, and no final video in their manifests.

| Requested run | Last heartbeat / event | Workspace and deliverable evidence | Classification |
|---|---|---|---|
| `val_3_21o_history_20261111_203000_1` — Bhopal disaster | `2026-11-11T14:11:59.855359+00:00` (**19:41:59 IST**); last event `ASSET_PROGRESS`, `animation_generation`, **45.0%**. No completion, failure, or abort event. | Manifest last updated at `14:11:59.732+00:00`; final-video deliverable says `exists: false`. Workspace contains intermediate animation assets, not the completed showcase video. | **STALE / worker no longer active.** The current process scan found no corresponding worker. Whether it was terminated or abandoned cannot be distinguished from retained evidence; it did not complete. |
| `val_3_21o_science_20261111_191500_2` — Wu experiment | `2026-11-11T13:42:02.895194+00:00` (**19:12:02 IST**); last event `ASSET_PROGRESS`, `image_generation`, **47.5%**. No terminal event. | Manifest last updated at `13:42:02.850+00:00`; final-video deliverable says `exists: false`. Four intermediate animation assets are present. | **STALE / worker no longer active.** No corresponding worker was found; completion is disproved by the absent final video and incomplete progress. |
| `val_3_21o_science_20261111_185349_1` | No matching record, progress event, manifest, or workspace was found in the current checkout; no worker matched this ID. | The nearby IDs are different runs: `val_3_21o_history_20261111_185349_1` and `val_3_21o_science_20261111_185349_2`. The latter is a separate Francis Halzen run, stale at **44.6%**, with no final video or terminal event. | **NOT FOUND — cannot classify the requested ID as completed, failed, or stale without its record.** The neighboring `_2` record was not substituted for it. |

Original progress percentages and event files were preserved. The dashboard now projects stale status without rewriting the retained event history or hiding historical runs. The similarly named `_2` science run remains accessible in history as its own record.

## 3. Dashboard correction

Changes in [production_observability.py](../backend/app/services/production_observability.py) and [dashboard.html](../backend/app/templates/dashboard.html):

- The existing 300-second heartbeat rule still determines whether a `starting`/`running` record is active. Stale records are excluded from active work.
- Stale dashboard views now set `progress_is_historical`, retain the last-known percentages, and project any `starting`/`running` substages to `stale`. The persisted stage/event history is not changed.
- Both list/history views and the per-run progress API return the stale dashboard projection, so looking up a run by ID cannot expose its old `running` value as live work.
- The live panel labels the percentage as historical, explains that the worker is no longer active, and shows its last heartbeat. Stale stage cards use a warning treatment and retain their percentages as “last” values. Historical rows remain visible and label stale percentages “Last known”.
- With no fresh worker, pipeline/worker status remains `IDLE`; stale history does not trigger live polling.

After a dashboard-only task restart, live checks returned `has_active: false`; Bhopal was `stale` at 45%, with stale storyboard/image/animation substages; and `/api/dashboard/progress/{id}` also returned `stale` with `progress_is_historical: true`. The dashboard’s history displayed the matching stale records.

The dashboard task restart briefly had no HTTP response while the server started. Subsequent direct API requests returned HTTP 200 and browser checks confirmed the corrected display. Only `CrazzyDocs-Health-Dashboard` was restarted; the production scheduler task was not touched. Before restart, the database schema was checked read-only: all SQLAlchemy tables and migration columns already existed, so startup required no schema change. `ENABLE_EMBEDDED_SCHEDULER` was unset (application default `false`).

## 4. IST timestamp verification

`formatIstTimestamp()` now parses explicit-offset ISO timestamps as instants and normalizes unzoned ISO date-times as UTC before formatting in `Asia/Kolkata`. This prevents a naive UTC timestamp from being formatted as though its original wall-clock value were already IST.

Browser-side execution verified both:

- `2026-11-11T14:11:59.855359+00:00` → **2026-11-11 19:41:59 IST**
- `2026-11-11T14:11:59` (legacy naive UTC) → **2026-11-11 19:41:59 IST**

The live history row for Bhopal rendered `2026-11-11 19:41:59 IST`. History, publishing, recovery, activity, and archive timestamp cells use the shared formatter; API UTC values retain their offsets.

## 5. Stage 3.21P video and frame verification

Inspected the actual MP4s, contact sheets, and matched rendered frames in [the POC output folder](../media/productions/val_3_21p_black_hole_20261111_202906/), including [the scientific contact sheet](../media/productions/val_3_21p_black_hole_20261111_202906/contact_sheet_scientific_visualization.png), [the documentary contact sheet](../media/productions/val_3_21p_black_hole_20261111_202906/contact_sheet_documentary_investigation.png), and [the comparison results](../media/productions/val_3_21p_black_hole_20261111_202906/stage_3_21p_results.json).

**Media verification**

- Both files exist, probe successfully with FFprobe, and fully decode with FFmpeg: [Scientific Visualization MP4](../media/productions/val_3_21p_black_hole_20261111_202906/black_hole_scientific_visualization.mp4), [Documentary Investigation MP4](../media/productions/val_3_21p_black_hole_20261111_202906/black_hole_documentary_investigation.mp4).
- Both are H.264, **1080×1920**, **30/1 FPS**, **29.533333 seconds**. Each AAC track is 29.489342 seconds; the source WAV is 29.542404 seconds.
- The decoded audio streams have the same MD5 (`fda38ad8bcbf3d195d8e2884f554e433`), confirming identical narration audio.
- No render or overwrite was performed during this reconciliation.

**Frame-level comparison**

- Scientific Visualization uses cyan radial/calibration geometry, an event-horizon model, orbiting-disk particles, model labels, and a lower instrument readout.
- Documentary Investigation uses a warm observatory silhouette, aperture/ring imagery, night-sky fields, and asymmetric editorial type with a simulation disclosure.
- These are materially different compositions and visual grammars, not one shared card/layout recolored. The eight matched frame samples have mean absolute RGB differences of **11.59, 11.70, 16.43, 16.31, 13.64, 13.64, 12.28, and 12.28** (0–255 scale). This metric supports, but does not replace, the contact-sheet inspection.

**Shortcomings recorded**

- The narration track is identical and communicates the same core spoken facts. The on-screen claims are not identical: Scientific Visualization adds “TIME RUNS DIFFERENTLY,” which is not stated in the shared narration or documentary captions. Thus the “same factual information” criterion is **partial**, not a strict pass.
- The scientific frames are coherent schematic illustrations but do not show cited sources, measured values, or scale units.
- The documentary treatment is recognizably distinct but remains synthetic vector art, not documentary evidence or photographic footage. It is sparse, largely static within its four scenes, and repeats a disclosure strip. Treatment transition names are metadata; the POC render uses hard cuts rather than executing those transition effects.

## 6. Creative architecture regression

- `run_stage_3_21p_black_hole_poc.py` calls `composer_for()` for each direction and passes each returned composer to the frame-render loop; both native composers are therefore invoked by the isolated POC render path. The path pipes raw RGB frames to the existing FFmpeg H.264/AAC mux pipeline.
- The POC composers do not call the legacy shared documentary backdrop. The storyboard builder’s `creative_direction` remains optional, preserving the existing Stage 3.21N/O default path.
- **Scope limitation:** `composer_for()` is used by the Stage 3.21P POC and its tests, not by the production orchestrator. Full production routing for these native composers remains intentionally deferred; the POC does not silently fall back to the old shell.
- The POC writes to its isolated `val_3_21p_black_hole_*` workspace and reports network acquisition, production-ledger mutation, and scheduler start as false. It uses local Pillow, Windows speech, and FFmpeg. No dependency manifest was changed; no mandatory Chromium, WebGL, Node, or paid cloud dependency was added.

## 7. Focused tests and syntax checks

**Focused tests: 6 passed** (9.82 seconds):

- `test_stage_3_21p_creative_architecture.py`
- `test_stage_3_21o_renderer_design.py`
- `test_dashboard_four_state_reconciliation_during_validation`
- `test_stale_progress_is_historical_without_mutating_persisted_stages`

The stale projection test confirms no active run is returned, historical progress and stale substages are exposed in dashboard payload/history and per-run views, and persisted `running` values remain unchanged. Two existing Pydantic/pytest-asyncio deprecation warnings remain.

AST syntax checks passed for the observability service, treatment/composer modules, storyboard builder, and POC runner. `git diff --check` passed. A complete repository test suite was **not run**; these results are focused, not a full-suite certification.

## 8. Remaining limitations

1. The requested `val_3_21o_science_20261111_185349_1` record is absent; the nearby `_2` record is distinct and was not substituted.
2. The POC’s shared narration matches, but the on-screen factual claim sets differ slightly; visual evidence is synthetic, and transitions are not implemented beyond hard cuts.
3. Native POC composers are not wired into the production orchestrator. No full repository test suite was run.
4. The dashboard runner logged a readiness timeout during its first restart attempt, although the server subsequently served HTTP 200 and the browser showed the corrected UI.

## 9. Final operational safety state

| Check | Final evidence |
|---|---|
| Production scheduler task | **Disabled** in Windows Task Scheduler and dashboard health |
| Scheduler daemon | **Stopped**; no scheduler lock file |
| Production worker | **Idle** in `/api/dashboard/overview`; no nonterminal `persistent_jobs` |
| Validation worker | **Idle**; progress API has `has_active: false`; stale runs remain historical |
| Publishing mode | **`manual_approval`** in settings and live overview |
| Active publishing ledger | **0 rows** at entry and final read-only check |
| Archived publishing history | **253 rows** at entry and final read-only check |
| Schedule profile | **`FOUR_CYCLES_DAILY`** active; `SINGLE_DAILY_2300` inactive |
| YouTube uploads / production activation | **None performed** |
| Database changes | **None**; database checks were read-only and startup found no missing tables/columns |
| Dashboard service | **Running** on port 8000; distinct from the disabled production scheduler |

No validation or production worker, FFmpeg render, uploader, or autonomous production schedule was started by this stage. No commit or push was made.
