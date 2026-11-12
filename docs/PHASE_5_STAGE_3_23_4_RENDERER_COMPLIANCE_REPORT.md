# Phase 5 — Stage 3.23.4: Renderer Compliance & Lemo-Opuscar Enforcement Report

## 1. Root Cause of Historical Non-Compliant Graphics (`gPcd3gXF984` / `SoHo Weekly News`)
- Inspection of `publishing_ledger` row `#284` (`SoHo Weekly News`, YouTube ID `gPcd3gXF984`, uploaded `2026-11-12T08:18:59.472951Z`) and `media/productions/cycle_3_20261112_1200/reports/render_telemetry_cycle_3_20261112_1200.json` confirmed the video was rendered by `HybridVisualEngine` (`Python Canvas (Pillow Ken Burns)` + `Explainer Animation`).
- Although `tools/dependencies/lemo-opuscar` (pinned commit `9eb7739a0fca4c6990fe966cb8b87b1930f7193a`) and `backend/app/rendering/lemo_opuscar_adapter.py` were present, `CrazzyDocsMasterOrchestrator.execute_production_pipeline()` bypassed `LemoOpuscarAdapter` and invoked `HybridVisualEngine`.

## 2. Implemented Fixes
- **`backend/app/rendering/lemo_opuscar_film_builder.py`**: Created `LemoOpuscarFilmBuilder.build_film_package()`, generating a deterministic 1080x1920 `<prod_dir>/lemo_film/index.html` exposing `window.DUR`, `window.render(t)`, `?thumbnail=1` support, local `assets/`, and `film_manifest.json`.
- **`backend/app/orchestration/crazzydocs_master_orchestrator.py`**:
  - Replaced `HybridVisualEngine` in Stage 9 (`is_full_live`) with `LemoOpuscarFilmBuilder.build_film_package()` + `LemoOpuscarAdapter().render_video()` + FFmpeg audio muxing (`hidden_subprocess_kwargs()`).
  - Updated workspace idempotency to reject cached MP4 deliverables whose manifest `renderer` is not `"lemo-opuscar"`.
  - Recorded `renderer: "lemo-opuscar"`, `pinned_commit`, and `fallback_used: False` in `production_manifest.json` and `render_telemetry_<job_id>.json`.
- **`backend/app/content/creative_identity_policy.py`**, **`backend/app/content/content_integrity_gate.py`**, & **`backend/app/services/qc_service.py`**:
  - Added `CreativeIdentityPolicy.validate_production_renderer()`, `CHECK_N_RENDERER_COMPLIANCE`, and QC Check 17 (`renderer_compliance`) to block `HybridVisualEngine`, `Python Canvas`, `Pillow Ken Burns`, `Explainer Animation`, and `CleanSlateRenderer` in `PRODUCTION` and `VALIDATION` contexts.
- **Historical Data Preservation**: `gPcd3gXF984` (`#284`) and `4DCas1e4vHI` remain untouched.
