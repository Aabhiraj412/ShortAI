# Stage 3.21O — Renderer-Level Creative Diversity Report

## Baseline audit (before Stage 3.21O implementation)

**Audit date:** 2026-11-11  
**Branch / HEAD:** `main` / `1baa37a112beb694a694b4c1f84f3ad67110da6d`  
**Working tree:** Stage 3.21N is present as uncommitted work. It has 19 modified tracked files, 7 untracked paths, and 698 insertions / 68 deletions in the tracked diff. No Stage 3.21N changes were discarded or overwritten. The repository is two commits ahead of `origin/main`.

### Stage 3.21N handover and validation

Read `docs/PHASE_5_STAGE_3_21N_VISUAL_DIVERSITY_ADAPTIVE_STYLE_REPORT.md` and `docs/STAGE_3_21N_VALIDATION_RESULTS.json`. Stage 3.21N added source-photo reuse checks, adaptive per-beat labels across four styles, and style/reuse telemetry. It recorded 37 passing focused regression checks and two full 1080×1920, 30 FPS renders. The two other category runs were storyboard-only. Its report also records the safety state and the effective publishing mode as `manual_approval`; the Stage 3.21O baseline audit did not change runtime configuration or publishing state.

The working tree contents were not treated as proof that every Stage 3.21N behavior ran in the exact checked-out runtime. The prior report and result JSON are the evidence for those claims. Stage 3.21O starts from the current working tree and preserves that evidence.

### Frame-level evidence

Inspected the rendered-video contact sheets for the Science-requested Medical Science run and Technology run. Both are existing full renders:

- [Medical Science contact sheet](../media/productions/val_3_21n_science_20261111_171526_1/audit/contact_sheet_val_3_21n_science_20261111_171526_1.png)
- [Technology contact sheet](../media/productions/val_3_21n_technology_20261111_171526_2/audit/contact_sheet_val_3_21n_technology_20261111_171526_2.png)
- [Medical Science video](../media/productions/val_3_21n_science_20261111_171526_1/video/showcase_val_3_21n_science_20261111_171526_1.mp4)
- [Technology video](../media/productions/val_3_21n_technology_20261111_171526_2/video/showcase_val_3_21n_technology_20261111_171526_2.mp4)

Both contact sheets repeatedly show the same vertical frame grammar: a narrow segmented progress strip, a small stage/style header, a large dark outlined visual well, a dark lower headline card with a colored edge, and a subtitle band below it. The contents inside the visual well vary (map/network drawing, numbered cards, timelines, diagrams, typography, metric bars), so the current system has modality-level variation. The overall composition and typography placement remain strongly shared across both videos, including beats labeled Pop Info, Chalk Talk, and Creator Motion. This fails the request's distinction between changing visual content and changing creative composition.

### Root cause found in the renderer path

- `documentary_storyboard_builder.py::_render_documentary_plate` supplies the shared dark frame, progress strip, stage pill, centered visual frame, lower callout card, and shared text geometry for multiple styles and asset types.
- `draw_visual_style_treatment` adds style cues on top of that plate; it does not replace the base composition.
- Per-beat style scores and rationale are persisted, but the selected style does not consistently select an independent composition/rendering adapter for the storyboard plate.
- The separate `HybridVisualEngine` has browser adapters for four modalities, but it does not currently provide all seven requested directions and is not the only rendering path used by production.
- Existing reference styles are four HTML files under `docs/reference_styles` (Pop Info, Data Story, Chalk Talk, Creator Motion Graphics). They provide design references, not proof that production uses them as distinct scene adapters.

### Stage 3.21N artifacts and safeguards

The two full renders contain 20 beats each. Stage 3.21N reports 20 distinct beat assets in each, zero source-photo reuse, and valid diversity audits. The Science-requested run used five generated stills and 15 procedural animations with no acquired photos; the Technology run used authentic photos and procedural animations. Existing 3.21N validation workspaces and media assets were only inspected. The Stage 3.21N report states the scheduler remained disabled, its daemon stopped, `FOUR_CYCLES_DAILY` active, manual approval effective, publishing ledger empty, and no publishing attempted.

## Implementation

_To be completed after renderer changes._

## Validation and visual comparison

_To be completed after validation renders and frame inspection._

## Limitations, Git state, and operational safety

_To be completed after validation._
