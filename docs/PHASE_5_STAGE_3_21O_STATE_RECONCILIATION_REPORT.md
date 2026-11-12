# Stage 3.21O — State Reconciliation Report

**Audit time:** 2026-11-11 (Asia/Kolkata)  
**Scope:** Forensic read-only reconciliation. The sole runtime action was starting the already configured `CrazzyDocs-Health-Dashboard` scheduled task after confirming it was stopped. No source, configuration, dependency, database, ledger, manifest, existing documentation, production asset, scheduler task configuration, or publishing record was changed.

Status terms in this report:

- **VERIFIED** — directly inspected in the current workspace/runtime.
- **REPORTED BUT NOT VERIFIED** — claimed by an existing artifact, but not independently reproduced here.
- **FAILED** — direct inspection contradicts the expected result.
- **NOT APPLICABLE** — no safe read-only check exists for the requested claim.

## A. Git forensic reconciliation

### Current revision and audit-start inventory

- **VERIFIED:** Branch: `main`.
- **VERIFIED:** HEAD: `1baa37a112beb694a694b4c1f84f3ad67110da6d` — `docs: Add Stage 3.21M execution report and machine-readable summary`.
- **VERIFIED:** At audit start, `git status --short` contained **33 changed filesystem entries**: 19 modified tracked files and 14 untracked files (the untracked `backend/backups/` directory contains three files). There were no staged, deleted, renamed, or added-to-index entries.
- **VERIFIED:** Tracked diff total at audit start: **999 additions / 68 deletions across 19 files**.
- **VERIFIED:** This report is the allowed new file. Final Git status therefore contains **34 changed filesystem entries**, with this report as the additional untracked file.

### Change attribution and ambiguity

The Stage 3.21N report names the adaptive style, source-photo identity, reuse policy, policy telemetry, manual-approval safeguards, Stage 3.21N runner/test, and validation artifacts. The Stage 3.21O renderer report and current renderer code name the per-direction plate work, style treatment module, animation composition work, Stage 3.21O runner/test, validation artifacts, and Lemo research report.

**VERIFIED limitation:** Git has no commit boundary between these uncommitted stages. Files changed by both stages cannot be split reliably line-by-line from the working tree alone. The `Stage 3.21N / 3.21O` labels below mean the file contains evidence of both scopes, not that either scope can be attributed exclusively.

| Path | Status | Add / delete | Attribution | Concise inspected change |
|---|---:|---:|---|---|
| `backend/app/content/creative_identity_policy.py` | `M` | 146 / 5 | Stage 3.21N / 3.21O | Adds adaptive per-beat style selection, rationales, transition checks, and newer style directions. |
| `backend/app/content/creative_production_policy.py` | `M` | 2 / 1 | Stage 3.21N | Adds preferred source-image reuse setting and policy version change. |
| `backend/app/content/visual_diversity_policy.py` | `M` | 246 / 4 | Stage 3.21N | Adds source identity, perceptual image matching, and diversity validation. |
| `backend/app/core/agent_identity.py` | `M` | 1 / 1 | Stage 3.21N | Changes default publishing mode to manual approval. |
| `backend/app/core/config.py` | `M` | 2 / 1 | Stage 3.21N | Constrains configured publishing mode to manual approval. |
| `backend/app/main.py` | `M` | 2 / 2 | Stage 3.21N | Constrains settings updates to manual approval; guards embedded scheduler startup by environment flag. |
| `backend/app/orchestration/crazzydocs_master_orchestrator.py` | `M` | 31 / 7 | Stage 3.21N | Persists diversity, style, layout, and creative-direction telemetry. |
| `backend/app/rendering/documentary_storyboard_builder.py` | `M` | 389 / 24 | Stage 3.21N / 3.21O | Adds adaptive beat metadata and direction-specific still compositions/layout selection. |
| `backend/app/rendering/explainer_animation_engine.py` | `M` | 50 / 0 | Stage 3.21O | Routes animation frames through direction composition treatment. |
| `backend/app/rendering/styles/style_system.py` | `M` | 31 / 6 | Stage 3.21N / 3.21O | Extends approved styles and their validation/transition handling. |
| `backend/app/scheduler/schedule_profiles.py` | `M` | 3 / 3 | Stage 3.21N | Makes both profiles manual-approval only. |
| `backend/app/services/production_observability.py` | `M` | 15 / 0 | Stage 3.21N | Adds creative direction, layout, style, and diversity observability fields. |
| `backend/app/templates/dashboard.html` | `M` | 24 / 2 | Stage 3.21N | Displays visual source reuse, layout distribution, direction, and style mix. |
| `backend/data/creative_policy_config.json` | `M` | 1 / 1 | Stage 3.21N | Updates visual acquisition policy data. |
| `backend/tests/test_production_duration_standard.py` | `M` | 1 / 1 | Unclear / pre-existing | Small assertion change; no reliable stage boundary. |
| `backend/tests/test_stage_3_17_deployment.py` | `M` | 5 / 5 | Stage 3.21N | Updates deployment/safety expectations. |
| `backend/tests/test_stage_3_21j_visual_diversity_persistence_observability.py` | `M` | 27 / 0 | Stage 3.21N | Extends persistence/observability checks. |
| `backend/tests/test_stage_3_21m_creative_policy_and_acquisition.py` | `M` | 21 / 3 | Stage 3.21N | Updates creative policy and acquisition expectations. |
| `backend/tests/test_stage_3_8_integration.py` | `M` | 2 / 2 | Unclear / pre-existing | Small integration expectation change; no reliable stage boundary. |
| `backend/app/rendering/style_treatments.py` | `??` | New / n.a. | Stage 3.21O | Shared per-style still/animation treatment functions. |
| `backend/backups/pre_stage_3_21m_20261111_142016/production_progress_store.json` | `??` | New / n.a. | Pre-existing / unrelated | Preserved backup of runtime progress state. |
| `backend/backups/pre_stage_3_21m_20261111_142016/topic_history.json` | `??` | New / n.a. | Pre-existing / unrelated | Preserved topic-history backup. |
| `backend/backups/pre_stage_3_21m_20261111_142016/validation_topic_history.json` | `??` | New / n.a. | Pre-existing / unrelated | Preserved validation-topic-history backup. |
| `backend/scripts/run_stage_3_21n_multicategory_validation.py` | `??` | New / n.a. | Stage 3.21N | Four-category validation runner. |
| `backend/scripts/run_stage_3_21o_renderer_diversity_validation.py` | `??` | New / n.a. | Stage 3.21O | Four-render renderer-diversity validation runner. |
| `backend/tests/test_stage_3_21n_visual_policy.py` | `??` | New / n.a. | Stage 3.21N | Visual diversity policy tests. |
| `backend/tests/test_stage_3_21o_renderer_design.py` | `??` | New / n.a. | Stage 3.21O | Renderer design/layout tests. |
| `docs/GITHUB_COPILOT_HANDOVER_AUDIT.md` | `??` | New / n.a. | Pre-existing / unrelated | Handover audit preserved. |
| `docs/PHASE_5_STAGE_3_21N_VISUAL_DIVERSITY_ADAPTIVE_STYLE_REPORT.md` | `??` | New / n.a. | Stage 3.21N | Stage report with baseline, changes, and validation claims. |
| `docs/PHASE_5_STAGE_3_21O_LEMO_OPUSCAR_ARCHITECTURE_RESEARCH.md` | `??` | New / n.a. | Stage 3.21O research | Upstream architecture assessment. |
| `docs/PHASE_5_STAGE_3_21O_RENDERER_LEVEL_CREATIVE_DIVERSITY_REPORT.md` | `??` | New / n.a. | Stage 3.21O implementation | Renderer-level implementation report; it remains partly marked for completion. |
| `docs/STAGE_3_21N_VALIDATION_RESULTS.json` | `??` | New / n.a. | Stage 3.21N | Machine-readable validation results. |
| `docs/STAGE_3_21O_VALIDATION_RESULTS.json` | `??` | New / n.a. | Stage 3.21O | Machine-readable four-render validation results. |

### Lemo-Opuscar report check

**VERIFIED:** `docs/PHASE_5_STAGE_3_21O_LEMO_OPUSCAR_ARCHITECTURE_RESEARCH.md` exists. It contains repository/core-renderer evidence, all ten requested styles, CrazzyDocs comparison, Windows/dependency/performance/licensing assessment, proposed architecture, proof-of-concept recommendation, and final `PARTIALLY ADOPT` recommendation.

## B. Stage 3.21O validation-output forensic review

### Located validated run set

**VERIFIED:** The four expected topics occur in the `20261111_192500` runs referenced by `docs/STAGE_3_21O_VALIDATION_RESULTS.json`, not the earlier incomplete/repeated directories. The current filesystem also contains earlier history/science attempts and an incomplete `val_3_21o_history_20261111_203000_1` run; none are substituted for the four reported results.

| Topic | Run ID | Video / bytes | Probe | Thumbnail / bytes | Contact sheet / bytes | Integrity / manifest |
|---|---|---:|---|---:|---:|---|
| Chernobyl disaster | `val_3_21o_history_20261111_192500_1` | `video/showcase_val_3_21o_history_20261111_192500_1.mp4` / 8,343,386 | 1080×1920, 30 fps, 56.400 s | `thumbnails/thumb_val_3_21o_history_20261111_192500_1.png` / 2,318,613 | `audit/contact_sheet_val_3_21o_history_20261111_192500_1.png` / 890,736 | **VERIFIED valid**; `production_manifest.json` exists |
| Higgs boson | `val_3_21o_science_20261111_192500_2` | `video/showcase_val_3_21o_science_20261111_192500_2.mp4` / 5,838,668 | 1080×1920, 30 fps, 51.200 s | `thumbnails/thumb_val_3_21o_science_20261111_192500_2.png` / 1,710,177 | `audit/contact_sheet_val_3_21o_science_20261111_192500_2.png` / 398,240 | **VERIFIED valid**; `production_manifest.json` exists |
| AlphaGo | `val_3_21o_technology_20261111_192500_3` | `video/showcase_val_3_21o_technology_20261111_192500_3.mp4` / 5,172,846 | 1080×1920, 30 fps, 56.000 s | `thumbnails/thumb_val_3_21o_technology_20261111_192500_3.png` / 2,575,518 | `audit/contact_sheet_val_3_21o_technology_20261111_192500_3.png` / 309,467 | **VERIFIED valid**; `production_manifest.json` exists |
| Bystander Effect | `val_3_21o_psychology_20261111_192500_4` | `video/showcase_val_3_21o_psychology_20261111_192500_4.mp4` / 5,544,038 | 1080×1920, 30 fps, 56.533 s | `thumbnails/thumb_val_3_21o_psychology_20261111_192500_4.png` / 35,081 | `audit/contact_sheet_val_3_21o_psychology_20261111_192500_4.png` / 306,856 | **VERIFIED valid**; `production_manifest.json` exists |

All four integrity files state `is_valid: true` and list 14 passing checks. That is **VERIFIED** as an existing integrity-artifact result. It does **not** independently establish composition diversity.

### Actual visual-composition review

**Method:** Existing contact sheets were opened and visually inspected. No new contact sheets, video frames, or renders were created. Therefore the results below apply to the sampled contact-sheet frames. A full frame-by-frame 20-beat composition count is **NOT VERIFIED**.

| Topic | Contact-sheet sample finding | Composition count | Layout metadata | Adapter routing | Legacy/shared-frame finding |
|---|---|---|---|---|---|
| Chernobyl | Geographic perimeter/map, document-like diagnostic panel, photo with overlaid type, full-photo view, layered photo/panel, and full-frame typography are visibly different. | **At least 6 sampled composition families — VERIFIED as sample only.** | 10 declared families across 20 beats. | 7 stills + 13 animations; source routing passes supported style directions to the new composition path. | **PRESENT:** dark diagnostic panels and subtitle band recur; the original complete shared plate renderer is not selected by the recorded beat styles. |
| Higgs | Pop-info photo/stat card, dark multi-card system diagram, dark metric panel, chalk diagram, typography, and outro differ; several dark card panels share the same geometry. | **At least 5 sampled families — VERIFIED as sample only.** | 9 declared families. | 7 stills + 13 animations; source routing supports new style composition. | **PRESENT:** progress/header, centered dark diagnostic card, lower caption/subtitle treatment visibly recur. |
| AlphaGo | Pop-info card, chalk/timeline board, system diagram, polygon mechanism diagram, kinetic typography, and outro differ; two diagnostics are visibly close in structure. | **At least 5 sampled families — VERIFIED as sample only.** | 10 declared families. | 6 stills + 14 animations; source routing supports new style composition. | **PRESENT:** repeated dark system-diagram card, footer subtitle band, and some common framing. |
| Bystander Effect | Pop-info card appears repeatedly, alongside a dark system diagram, a kinetic-type frame, a stat panel, and outro; strongest visible repetition of the four samples. | **At least 4 sampled families — VERIFIED as sample only.** | 8 declared families. | 5 stills + 15 animations; source routing supports new style composition. | **PRESENT:** repeated pop-info outer card, dark diagnostic/card treatment, progress/header, and subtitle band. |

**VERIFIED, implementation-level:** Every recorded full-run beat uses a supported visual style. `documentary_storyboard_builder.py` routes the seven styles to `_render_direction_plate`; `explainer_animation_engine.py` calls `compose_direction_frame` for its style-aware path. Thus the validation metadata records **20/20 beats per run** assigned to the direction-composition adapter, with **0/20** beats assigned to the legacy fallback branch of `_render_documentary_plate` by that dispatch criterion.

**VERIFIED limitation:** The animation engine still constructs `_draw_base_documentary_backdrop` before composing some animation frames. Contact-sheet inspection also shows shared dark diagnostic/card and subtitle-band motifs. The implementation routing claim must therefore not be interpreted as proof that all 20 final pixels use independent scene grammars.

**FAILED:** The requirement that all composition repetition be eliminated is not established. The contact sheets show meaningful variation but also recurrent visual shells. The Higgs, AlphaGo, and Bystander samples retain the original family of dark central diagnostic/card, progress/header, lower callout/caption, and subtitle-band treatment in multiple sampled frames.

## C. Dashboard diagnosis and recovery

### Pre-recovery diagnosis

- **VERIFIED:** `CrazzyDocs-Health-Dashboard` was enabled but `Ready` (not running), last run `2026-11-11 03:07:36`, last result `-1`; no port 8000 listener existed.
- **VERIFIED:** Its configured action is `D:\Agents\CrazzyDocs\.venv\Scripts\pythonw.exe "D:\Agents\CrazzyDocs\backend\scripts\run_dashboard.py" --open-browser` with start directory `D:\Agents\CrazzyDocs`.
- **VERIFIED:** Existing dashboard logs record a `WinError 10022` invalid-argument exception in the Proactor pipe callback at `17:24:41`. This is the only direct stop-related failure evidence. Earlier log failures also show prior import/logging configuration issues; they are historical and cannot be proven to be the immediate cause of this stop.

### Recovery result

- **VERIFIED:** Because it was stopped, only `schtasks /Run /TN "CrazzyDocs-Health-Dashboard"` was executed.
- **VERIFIED:** The task is now `Running`; listener PID `25960` is `pythonw.exe` running the configured dashboard command on `127.0.0.1:8000`.
- **VERIFIED:** `GET /dashboard` returned HTTP 200 (63,606 bytes).
- **VERIFIED:** `GET /api/dashboard/overview`, `/api/settings`, and `/api/publishing` returned HTTP 200. `/api/health` returned 404 and is not a dashboard health endpoint; `/api/dashboard/health` is the relevant route.
- **VERIFIED:** The live overview reports `FOUR_CYCLES_DAILY`, scheduler `DISABLED`, production worker `IDLE`, and manual approval. The application lifespan only invokes the embedded scheduler when `ENABLE_EMBEDDED_SCHEDULER` is true; the live API reports it disabled.
- **FAILED:** Dashboard health state is not fully accurate. It reports a validation worker as `RUNNING` at `animation_generation` for `val_3_21o_history_20261111_203000_1` / “Bhopal disaster”, while no matching production, scheduler, or FFmpeg process exists and that directory is incomplete. It is stale progress data, not live execution.

## D. Production and publishing safety

| Check | Result | Evidence |
|---|---|---|
| Production scheduled task disabled | **VERIFIED** | `CrazzyDocs-Production-Scheduler` state/status is `Disabled`. |
| Scheduler daemon absent | **VERIFIED** | No scheduler lock; live dashboard/API says disabled; no matching process found. |
| Production worker absent | **VERIFIED** | Dashboard reports `IDLE`; no matching worker process found. |
| FFmpeg render absent | **VERIFIED** | No `ffmpeg.exe` process found. |
| Active publishing queue | **VERIFIED empty** | Read-only SQLite count: `publishing_ledger=0`; dashboard `/api/publishing` returns `publications: []`. |
| New YouTube uploads during audit | **NOT APPLICABLE** | No audit action could upload. Channel data returned by the dashboard reports `video_count: 0`; it is an external API snapshot and cannot prove historical non-upload activity beyond its response. |
| Ledger/archive counts unchanged from Stage 3.21O report | **VERIFIED** | Current read-only counts equal its before/after values: `content_ledger=9`, `publishing_ledger=0`, `publishing_ledger_archive=253`, `persistent_jobs=4`, `media_assets=2`. |
| Active schedule profile | **VERIFIED** | Live overview and publishing API: `FOUR_CYCLES_DAILY`. |
| `SINGLE_DAILY_2300` inactive | **VERIFIED** | It is available in code, while live active profile is `FOUR_CYCLES_DAILY`. |
| Publishing under manual approval | **VERIFIED** | `/api/settings` reports `manual_approval`; profile/API report the same. |

## Unresolved issues and recommended next action

1. **Do not begin another implementation stage yet.** Reconcile the stale `val_3_21o_history_20261111_203000_1` validation-worker state through an approved maintenance procedure; this audit intentionally did not change progress data.
2. **Treat the four validated renders as partially successful evidence.** They verify output artifacts, format, integrity checks, and some composition variation. They do not verify elimination of template repetition.
3. **Before accepting Stage 3.21O, perform a separately authorized frame-level review against a concrete composition rubric.** It needs to reject recurrent progress/header + central diagnostic card + lower caption/subtitle geometry even when the image, color, or zoom differs.
4. **Preserve the current uncommitted tree.** Because Stage 3.21N and Stage 3.21O overlap in the same files without a commit boundary, attribution must remain explicitly ambiguous until an operator chooses a review/commit strategy.

## Final state

**VERIFIED:** No existing change was discarded, staged, committed, pushed, reset, reverted, stashed, overwritten, moved, renamed, or deleted. No database/ledger/publishing record was manually changed. The dashboard task was restarted under the narrow permission in the brief. This report is the only created audit artifact. Stop here; no new implementation stage was started.
