# STAGE 3.21M — PHASE ZERO CHANGE & ARCHITECTURE INVENTORY

- **Timestamp:** `2026-11-11 14:20:16 IST`
- **Git HEAD:** `main` @ `89d08c0` (`Stage 3.21K: Validation topic isolation, 4-state dashboard reconciliation, storage exposure & log throttling`)
- **Stage 3.21L Backup Preserved:** `backend/backups/studio_backup_pre_stage_3_21l_20261111_133859.db`
- **Phase Zero Backup:** `backend/backups/pre_stage_3_21m_20261111_142016/` (`studio.db` `PRAGMA integrity_check = ok`, `validation_topic_history.json`, `topic_history.json`, `production_progress_store.json`)
- **Scheduler State:** `CrazzyDocs-Production-Scheduler = Disabled`; `scheduler_daemon = STOPPED`
- **Preserved Stage 3.21L Production Workspace:** `media/productions/val_3_21l_20261111_135230/`

## 1. Stage 3.21L Uncommitted Changes Reconciled & Preserved
1. `backend/app/rendering/documentary_storyboard_builder.py` (+26/-?): tightened `compose_structured_script` word ceiling (`104-124`) and added symmetric FFmpeg `atempo` upper-bound calibration (`total_voice_dur > 55.6s`) to guarantee `50-58s` total video duration including the `2.0s` outro.
2. `backend/app/services/production_observability.py` (+?): added bounded Windows retry/fallback logic in `_load_file` and `_save_file` to prevent `PermissionError: [WinError 5]` during concurrent dashboard polling and pipeline writes.
3. `backend/scripts/run_stage_3_21l_validation.py`: controlled end-to-end validation runner with live `/api/dashboard/overview` + `/api/dashboard/progress` polling, visual relevance report, and 10-frame contact sheet extraction.
4. `docs/STAGE_3_21L_PREFLIGHT_20261111_133859.md` & `docs/PHASE_5_STAGE_3_21L_END_TO_END_FRESH_TOPIC_VALIDATION_REPORT.md`: pre-flight and verified Stage 3.21L reports.

## 2. Phase Zero Gap Audit Summary (Targets for Stage 3.21M)
1. **Category Bias (`curiosity_topic_engine.py`):** `_infer_category` currently collapses topics into 5 legacy buckets defaulting to `"Strange but True History"`; lacks a configurable 20+ category registry, category distribution history, and 8-dimension scoring metadata.
2. **Visual Acquisition Priority (`acquisition_manager.py`, `providers/`):** Lacks Priority A Internet Archive / optional Pexels & Pixabay providers; lacks Priority B open-source animation ingestion (GIF/WebM/MP4); `license_verifier.py` / `models.py` lack full provenance fields (`download_timestamp`, `original_filename`, `modification_permitted`, `usage_restrictions`, `attribution_requirements`, `share_alike_compatible`).
3. **Priority C Animation & Effects (`explainer_animation_engine.py`, `documentary_storyboard_builder.py`):** `ExplainerAnimationEngine` currently draws overlays over synthetic navy backgrounds instead of supporting authentic acquired photo/map backgrounds with animated routes, markers, highlights, timelines, callouts, particles, and glow. Priority D generated images need explicit illustrative-reconstruction labeling and selective fallback invocation only.
4. **Execution Context Parity (`crazzydocs_master_orchestrator.py`):** Lines `959` & `990` still hardcode `"historical_documentary"`; creative rules need an explicit shared `CreativeProductionPolicy` imported identically by both `VALIDATION` and `PRODUCTION`.
5. **Dashboard Creative Metrics (`production_observability.py`, `dashboard.html`):** Needs non-overlapping creative intelligence & visual sourcing metrics (`authentic_images_acquired`, `existing_animations_acquired`, `animated_authentic_composites`, `generated_images`, `bespoke_animations`, license/attribution completeness).
