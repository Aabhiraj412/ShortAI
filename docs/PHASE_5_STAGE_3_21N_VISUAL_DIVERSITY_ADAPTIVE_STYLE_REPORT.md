# Stage 3.21N — Visual Diversity and Adaptive Style Report

## Phase Zero: Baseline Audit (Before Implementation)

**Audit date:** 2026-11-11  
**Branch / HEAD:** `main` / `1baa37a112beb694a694b4c1f84f3ad67110da6d`  
**Prior implementation:** `fa91709db5a5e1fb84344c9ad3071392c913c4b5`  
**Prior documentation:** `1baa37a112beb694a694b4c1f84f3ad67110da6d`

### Git and operational handover

- The working tree began with the pre-existing untracked `backend/backups/` directory and the handover report `docs/GITHUB_COPILOT_HANDOVER_AUDIT.md`. Neither was discarded or altered.
- Stage 3.21L and 3.21M reports, summaries, validation JSON, manifests, storyboards, contact sheets, and rendered showcase MP4s exist.
- The running dashboard answered `/api/dashboard/overview` with HTTP 200 and reported `FOUR_CYCLES_DAILY`, scheduler `DISABLED`, daemon `STOPPED`, production worker `IDLE`, and validation worker `COMPLETED`.
- SQLite `PRAGMA integrity_check` returned `ok`; observed row counts were `content_ledger=9`, `publishing_ledger=0`, `publishing_ledger_archive=253`, `persistent_jobs=4`, and `media_assets=2`.
- The effective publishing mode was found to be `automatic_public`, contrary to the required manual-approval safety state. No production execution, upload, publish, or scheduler activation was performed during this baseline audit.

### Latest rendered-artifact inspection

Inspected the Stage 3.21L Antarctica run and both Stage 3.21M full-render workspaces, including storyboards, manifests, contact sheets, and showcase MP4 presence.

| Baseline run | Category | Photo beats | Unique source-photo hashes | Repeated source photos | Max source-photo reuse | Existing animation beats | Per-beat style |
|---|---|---:|---:|---:|---:|---:|---|
| `val_3_21m_science_20261111_152005_1` | Science | 4 | 2 | 2 images used twice each | 2 | 0 | Not recorded |
| `val_3_21m_technology_20261111_152005_2` | Technology | 4 | 2 | 2 images used twice each | 2 | 0 | Not recorded |

Both reports show 20 beats, 4 Priority A photo beats, 4 Priority C animated-authentic beats, 5 generated-image beats, and 7 bespoke animation beats. The storyboard's priority-C overlays use an underlying authentic photo, but the main diversity audit keys the rendered animation clip as its own asset; the separate photo-reuse counter permits each source image to be repeated twice. The validator therefore enforces the ceiling but does not prefer zero reuse or reliably account for source-photo identity across a generated crop, resize, tint, or overlay.

Contact-sheet inspection confirms a repeated documentary plate treatment. The Science sheet visibly reuses the same red-carpet photograph in multiple beats, including beats with different overlays. The Technology sheet demonstrates relevant archival material but still uses the same plate/card visual language across photo, illustration, and animation beats. These are concrete baseline symptoms rather than conclusions inferred from code alone.

### Existing style-selection architecture and cause

- `creative_identity_policy.py` defines documentary, Pop Info, Chalk Talk, and Creator Motion Graphics as approved style names, but its timeline validator currently rejects any beat whose style differs from the video's selected primary style.
- `rendering/styles/style_system.py` defines exclusive single-style validation and has no documentary theme. The production storyboard does not store per-beat style, rationale, or transition rationale.
- `crazzydocs_master_orchestrator.py` labels the resolved style using a category-derived `*_documentary` string. `documentary_storyboard_builder.py` assigns visual modalities and asset priorities, not adaptive creative styles.
- Four local adapter HTML files and four immutable reference templates exist. The production storyboard path does not choose among the adapters per beat; `HybridVisualEngine` dispatches by modality, while acquired-photo beats use the shared documentary plate and scripted animation beats use the shared explainer treatment.
- `visual_diversity_validator.py` is not present as a separate module; the implemented validator is `visual_diversity_policy.py`.

The root causes established before changes are therefore: (1) photo allocation intentionally spends the allowed second appearance on Priority C overlays, (2) duplicate identity is primarily byte-hash/source-ID based without image-level perceptual comparison, and (3) style metadata/selection is not connected to the production storyboard or its visuals.

## Implementation and validation

**Implementation date:** 2026-11-11  
**Validation runner:** `backend/scripts/run_stage_3_21n_multicategory_validation.py`  
**Results:** `docs/STAGE_3_21N_VALIDATION_RESULTS.json`

### Changes

- Added source-photo identity grouping across authentic stills and authentic-image animation composites. Reuse now defaults to one use per source photograph, with explicit narrative/alternative-exhaustion evidence required for exceptions.
- Added SHA-256, canonical source identity, and multi-crop perceptual hashing to detect exact copies and likely crop/resize/tint variants. Low-texture solid images are excluded from perceptual matches to avoid flat-color false positives.
- Deduplicated acquired imagery before allocation and split distinct photographs between still-photo and authentic-image-composite beats. Validation and production share the same allocator and creative policy.
- Added topic-, narrative-, and asset-aware selection among Documentary, Pop Info, Chalk Talk, and Creator Motion Graphics. Every beat records style-selection rationale and every transition records its narrative rationale; shared visual treatments apply these cues to stills and animations.
- Added storyboard and dashboard telemetry for source-image reuse, near-duplicate groups, unique photographic assets, and per-style beat distributions.
- Restored the required publishing safety invariant: application defaults, schedule profiles, and settings requests now permit only `manual_approval`. No scheduler was activated.

### Automated verification

The focused Stage 3.21N and safety regression command completed with **37 passed**:

```text
backend/tests/test_creative_identity_policy.py
backend/tests/test_stage_3_21j_visual_diversity_persistence_observability.py
backend/tests/test_stage_3_21m_creative_policy_and_acquisition.py
backend/tests/test_stage_3_21n_visual_policy.py
test_manual_approval_mode_configuration
test_independent_scheduler_configuration
test_decoupled_publishing_queue
```

Coverage includes transformed-image matching and flat-color false-positive prevention, source reuse enforcement, justified style transitions, policy parity, dashboard telemetry persistence/API shape, and publishing/scheduler gates. Changed Python modules and the Stage 3.21N runner compile successfully; `git diff --check` reports no whitespace errors.

The first validation-runner attempt completed both full renders but stopped at the first upstream report because the runner looked for the audit in plan metadata rather than the persisted storyboard. The runner was corrected to read the canonical storyboard audit, and the full renders were independently checked before resuming the remaining validations. The final four-run report was written successfully.

### Controlled validation results

Four fresh topics in four distinct actual categories completed under `ExecutionContext.VALIDATION`. All four storyboards have 20 beats, 20 distinct visual assets, a valid diversity audit, and zero source-photo reuse. Two runs produced full 1080×1920, 30 FPS H.264/AAC renders. Both full-render integrity reports passed all 14 checks.

| Requested category | Actual category | Topic | Mode | Distinct assets / beats | Unique source photos | Reused photos | Style distribution |
|---|---|---|---|---:|---:|---:|---|
| Science | Medical Science | Mental health app may help unemployed people find jobs, six-week trial suggests | Full render | 20 / 20 | 0 | 0 | Pop Info 8, Creator Motion Graphics 11, Chalk Talk 1 |
| Technology | Technology | Computer network | Full render | 20 / 20 | 5 | 0 | Documentary 2, Pop Info 6, Chalk Talk 4, Creator Motion Graphics 8 |
| Psychology | Psychology | Frequency illusion | Upstream storyboard | 20 / 20 | 1 | 0 | Documentary 1, Pop Info 8, Chalk Talk 2, Creator Motion Graphics 9 |
| Nature and Wildlife | Nature and Wildlife | Bioluminescence | Upstream storyboard | 20 / 20 | 5 | 0 | Documentary 10, Pop Info 2, Chalk Talk 1, Creator Motion Graphics 7 |

| Full render | Duration | Frame size / rate | Video size | Integrity |
|---|---:|---|---:|---|
| `val_3_21n_science_20261111_171526_1` | 56.93 s | 1080×1920 / 30 FPS | 5.43 MB | 14/14 pass |
| `val_3_21n_technology_20261111_171526_2` | 50.97 s | 1080×1920 / 30 FPS | 6.14 MB | 14/14 pass |

Inspected the rendered-video contact sheets for both full runs. The Science-requested discovery resolved to the supported **Medical Science** category, which is reported as selected rather than misrepresented as Science. It had no usable authentic photo acquisition and consequently used generated/procedural visuals; the Technology, Psychology, and Nature runs exercised authentic images, including animated authentic composites where allocated.

Baseline comparison: both Stage 3.21M full renders had two distinct source photographs repeated twice each (four photo beats, max source reuse 2). The Stage 3.21N runs report zero repeated source photographs; where photographs were acquired, each source was used once. Per-beat style distributions are now present in each storyboard and in the dashboard telemetry payload.

### Safety and operational state

- `PUBLISHING_MODE`: `manual_approval` in the running settings endpoint.
- Publishing ledger: 0 rows before and after; no upload or publish was attempted.
- Protected database counts were unchanged: `content_ledger=9`, `publishing_ledger=0`, `publishing_ledger_archive=253`, `persistent_jobs=4`, `media_assets=2`.
- Scheduled task `CrazzyDocs-Production-Scheduler`: `Disabled`.
- Active schedule profile: `FOUR_CYCLES_DAILY`; production worker `IDLE`; live dashboard healthy. No scheduler/profile activation or dashboard restart occurred.
- `SINGLE_DAILY_2300` remains configured but is not the active profile.

### Limitations and follow-up

- The already-running dashboard process was intentionally not restarted. Its live dashboard served the updated template, and `/api/dashboard/progress` returned the new per-run style distribution and diversity metrics. The overview's static policy snapshot still reports the process-loaded Stage 3.21M policy version.
- The Stage 3.21N changes and reports are uncommitted. The pre-existing untracked `backend/backups/` directory was preserved.
