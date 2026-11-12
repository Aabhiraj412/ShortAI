# Stage 3.21P — Lemo-Opuscar-Inspired Creative Architecture

**Decision: PARTIALLY ADOPT.** CrazzyDocs should adopt Lemo-Opuscar's treatment-first, executable-renderer-grammar model while retaining its FFmpeg finishing pipeline, provenance rules, and manual publishing controls. The controlled proof of concept demonstrates genuine visual divergence on the same narration without adding Node, Chromium, Playwright, Three.js, or a WebGL dependency.

## Scope and safety

This implementation started from the dirty Stage 3.21N / 3.21O / 3.21O-R checkout and preserved it. No reset, stash, checkout, dependency installation, scheduler start, uploader invocation, production ledger write, schedule change, or autonomous production run occurred. No `AGENTS.md`, `DIRECTOR.md`, or `TECHNIQUE.md` exists in this checkout; the Stage 3.21O upstream research report was used as the applicable Lemo-Opuscar direction source.

The scheduler task remains **Disabled** after the work. The POC's JSON records `network_acquisition: false`, `production_ledger_mutated: false`, `scheduler_started: false`, and `publishing: manual_approval`.

## What changed

### Treatment-first contract

[`creative_direction.py`](../backend/app/rendering/creative_direction.py) adds:

- `FilmTreatment`, `TreatmentScene`, and timestamped `TimelineCue` data structures;
- `FilmDirector`, which produces the story category, structure, emotional arc, required evidence, pacing, type, transition and sound-design decisions before rendering;
- six executable direction contracts: Documentary Investigation, Scientific Visualization, Data Storytelling, Cinematic Motion Graphics, Chalkboard/Blueprint, and Archive/Editorial Montage;
- `audit_composition_diversity`, which checks composition family, layout, camera, animation, transition and adjacent composition repetition rather than treating different assets as diversity.

Each contract owns a coordinate system, composition strategy, asset presentation, typography, camera, animation, transitions, density, background, purpose, and disallowed shared scaffolding. This is the local adaptation of the upstream source finding that a `STYLE.md` is meaningful only when it maps to a real renderer grammar.

[`documentary_storyboard_builder.py`](../backend/app/rendering/documentary_storyboard_builder.py) now accepts an optional `creative_direction`. When supplied, it creates the treatment before selecting beat layouts, makes the treatment's composition family the beat layout, and persists treatment/composition audit fields in both storyboard JSON and plan metadata. Existing callers keep their current behavior because the option defaults to `None`.

### Independent native composers

[`independent_composers.py`](../backend/app/rendering/independent_composers.py) contains two deliberately separate frame renderers used by the proof of concept:

| Direction | Renderer/coordinate space | Visual grammar |
|---|---|---|
| Scientific Visualization | `ScientificFieldComposer`; radial/diagram world coordinates | calibration field, event-horizon boundary, light paths, particle disk, model-space labels and scale state |
| Cinematic Documentary / Investigation | `CinematicDocumentaryComposer`; lens and image space | observatory silhouette, telescope aperture, warm full-frame night shots, grain, source/simulation treatment and sparse editorial type |

Neither uses `ExplainerAnimationEngine._draw_base_documentary_backdrop`, the legacy progress strip, a central visual well, or a generic stat card. They share only portrait output and the factual/narration timing contract.

### Timeline, observability and quality visibility

The film-treatment scenes have common start/end timestamps that align narration, visual state and outgoing transition intent. The proof script muxes the **same local narration WAV** into both videos; scene boundaries occur at the same four durations in each version.

[`production_observability.py`](../backend/app/services/production_observability.py) can now retain display-safe film-treatment summaries and composer distributions. [`crazzydocs_master_orchestrator.py`](../backend/app/orchestration/crazzydocs_master_orchestrator.py) passes those storyboard metadata fields to observability. [`dashboard.html`](../backend/app/templates/dashboard.html) adds selected direction, composer and treatment structure to the live production metadata when available. Existing renderer progress/FFmpeg fields, style/layout distribution, IST formatter, stale-worker logic, and manual-approval display remain in place.

The dashboard is an observer: this work did not activate the embedded scheduler or change any worker state.

## Controlled POC: “HOW A BLACK HOLE WORKS”

Output folder: [`val_3_21p_black_hole_20261111_202906`](../media/productions/val_3_21p_black_hole_20261111_202906)

| Version | Output | Contact sheet | Renderer evidence |
|---|---|---|---|
| A — Scientific Visualization | [MP4](../media/productions/val_3_21p_black_hole_20261111_202906/black_hole_scientific_visualization.mp4) | [PNG](../media/productions/val_3_21p_black_hole_20261111_202906/contact_sheet_scientific_visualization.png) | `scientific_field_composer`; model-space radial/science composition |
| B — Cinematic Documentary | [MP4](../media/productions/val_3_21p_black_hole_20261111_202906/black_hole_documentary_investigation.mp4) | [PNG](../media/productions/val_3_21p_black_hole_20261111_202906/contact_sheet_documentary_investigation.png) | `evidence_documentary_composer`; lens/image-space observatory composition |

The identical factual narration is 29.542 seconds long and was created with the local Windows speech engine only. Both videos are H.264, 1080×1920, 30 FPS, 29.533 seconds; their AAC tracks are 29.489 seconds, a 0.044-second mux difference within the normal 0.2-second sync tolerance.

Frame-level evidence is preserved as eight matched samples per direction and in [`stage_3_21p_results.json`](../media/productions/val_3_21p_black_hole_20261111_202906/stage_3_21p_results.json). At all eight matched timestamps, mean absolute RGB frame distances are 11.59–16.43 (0–255 scale), above the 10-point threshold selected for a dark-space subject. This metric is deliberately secondary to visual review because both directors correctly share black space.

Manual contact-sheet inspection finds visually distinct directors:

- Scientific frames use cyan radial geometry, concentric scale space, calibrated model labels, section/light-path diagrams and a lower instrument readout.
- Documentary frames use a warm aperture/observatory/sky sequence, silhouette and film-grain image space, asymmetric editorial type and an explicit simulated-visualization disclosure.

They do not become different merely by swapping colours or source images: their coordinate systems, subject placement, label placement, visual density, camera intent, and scene families differ. Each treatment also has four unique adjacent composition families and three transition intents.

## Performance and platform assessment

The isolated Python/Pillow-to-FFmpeg implementation completed at single-producer rates measured on this machine:

| Composer | Frames | Elapsed | Producer throughput |
|---|---:|---:|---:|
| Scientific field | 886 | 21.678 s | 40.87 FPS |
| Cinematic documentary | 886 | 28.980 s | 30.57 FPS |

This confirms the requested 1080×1920 / 30 FPS output is feasible for these CPU-efficient 2D/procedural directions on the named Ryzen 7 3700U/Vega 10/16 GB class of machine. It does **not** demonstrate that a multi-worker Chromium/WebGL or texture-pyramid renderer is affordable. The Stage 3.21O recommendation still applies: if a browser/WebGL direction is introduced, benchmark one worker first, cap worker/resource settings, and treat software WebGL as degraded mode.

FFmpeg remains the final H.264/AAC muxer. No Node.js runtime was available from the shell during the earlier verification, so browser-based Lemo components were intentionally not introduced. No upstream code was copied; therefore no new Lemo license notice is required. If source code is later adapted, Lemo-Opuscar's MIT notice must be retained, and individual asset/font/data licenses remain independent obligations.

## Validation

Passed:

- Python compilation for the new treatment/composer/POC modules and modified storyboard builder.
- `pytest -q backend/tests/test_stage_3_21p_creative_architecture.py backend/tests/test_stage_3_21o_renderer_design.py`: **4 passed** (only existing Pydantic/pytest-asyncio deprecation warnings).
- Both MP4s: H.264, 1080×1920, 30 FPS; same local narration source; target-duration compliance.
- Composition audits: four scenes per version, no adjacent composition-family repeat; distinct composition, camera, animation and transition distributions.
- Manual contact-sheet inspection and matched eight-frame visual comparison.
- Scheduler state remained Disabled.

Unverified / intentionally deferred:

- Full production routing of all six contracts to native renderers. Only the two POC directions have native implementations; existing production calls retain their safe legacy path unless a `creative_direction` is explicitly provided.
- Browser/WebGL performance, because Node/Chromium/Playwright were not installed or started.
- Live dashboard rendering of a new treatment record. The field plumbing and client display are implemented, but creating a live production record solely to populate it would violate the no-production-run safety boundary.
- Named transition effects beyond the POC's hard scene cuts. The treatment carries the intended transition language; a next controlled POC should execute and sample each effect.

## Recommended next increment

Keep this as a controlled composer registry, not a wholesale renderer replacement. Implement the next two native adapters (Data Storytelling and Chalkboard/Blueprint), add actual transition render functions to the native segment renderer, then run a low-resource browser benchmark only if a direction truly needs WebGL. Before any autonomous production use, require a contact-sheet/frame audit that tests treatment compliance and repeated composition patterns in addition to the existing asset/provenance and media integrity checks.
