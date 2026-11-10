# AI YouTube Shorts Studio — Safe Project Cleanup Plan
**Project Directory:** `D:\AI Arts\AI\`  
**Date of Plan:** November 10, 2026  
**Auditor:** Senior Software Architect & Technical System Auditor  
**Status:** **PROPOSED & DRY-RUN ONLY (Zero Modifications Executed)**

---

## 1. Safety Principles & Guardrails

To ensure zero disruption to working features, zero risk of data loss, and absolute adherence to the user's constraints, the following safety guardrails govern this cleanup plan:

1. **Strict Non-Destructive Rule:** No files will be deleted, moved, or overwritten during this planning phase. All actions below require explicit user review and approval before execution.
2. **Channel Identity Preservation:** All files inside `D:\AI Arts\AI\Youtube Channel Src\` (`Youtube-Icon.png`, `Compressed-Youtube-Icon.png`, `Compressed-Youtube-Banner.png`) are permanent user assets and **MUST NEVER BE MODIFIED OR DELETED**.
3. **Reference Style Preservation:** All four HTML files in `D:\AI Arts\AI\docs\reference_styles\` (`chalk_talk.html`, `creator_motion_graphics.html`, `data_story.html`, `pop_info.html`) are independent ground-truth visual references and **MUST BE PRESERVED WITHOUT MODIFICATIONS**.
4. **Database Safeguard:** The active database `D:\AI Arts\AI\backend\studio.db` contains operational state and job history. Any cleanup or migration must first create a timestamped backup (`backend/studio.db.bak_<timestamp>`).
5. **Portable Toolchain Integrity:** The self-contained binaries in `tools/python/`, `tools/nodejs/`, `tools/ffmpeg/`, and `tools/uv/` are WDAC-compliant signed binaries and must remain untouched.
6. **Two-Tier Archival Before Purging:** Files slated for deprecation (such as the legacy facial sprite animation engine or temporary setup scripts) will first be moved into an isolated `D:\AI Arts\AI\archive\` directory rather than permanently deleted.

---

## 2. Categorization Taxonomy

Every file and directory in the project has been evaluated and assigned one of seven disposition categories:

| Category | Definition | Action Required |
| :--- | :--- | :--- |
| **A: KEEP (Core Production)** | Active production code, services, agents, API endpoints, and React frontend. | Preserve without changes. |
| **B: KEEP (Runtime & Tooling)** | Python runtime, virtual environment, Node.js, FFmpeg, UV, environment scripts. | Preserve without changes. |
| **C: KEEP (Assets & Deliverables)** | User branding assets, reference style HTMLs, audio beds, generated video proof samples. | Preserve without changes. |
| **D: CONSOLIDATE** | Partially overlapping or duplicate modules that should be merged into a single source of truth. | Refactor after user approval. |
| **E: ARCHIVE** | Working legacy code or prototype scripts that have been superseded by newer architectures. | Move to `D:\AI Arts\AI\archive\`. |
| **F: PURGE / REMOVE** | Temporary debug scripts, transient test video clips, downloaded install archives, and package caches. | Safe deletion after user approval. |
| **G: RESTRUCTURE** | Empty or misaligned directory trees that create visual noise without adding functionality. | Reorganize after user approval. |

---

## 3. Comprehensive File-by-File Disposition Table

### 3.1 Root Directory Files

| File Path | Size | Category | Description / Purpose | Potential Risk | Mitigation / Action |
| :--- | :---: | :---: | :--- | :--- | :--- |
| `README.md` | 6.0 KB | **A: KEEP** | Main project documentation and quickstart guide. | None | Keep as primary documentation. |
| `start_studio.ps1` | 1.2 KB | **A: KEEP** | Primary startup script for FastAPI + React Dashboard. | None | Keep as default launcher. |
| `start_studio.bat` | 897 B | **A: KEEP** | Double-clickable Windows batch launcher for the studio. | None | Keep for one-click launching. |
| `run_in_env.ps1` | 738 B | **B: KEEP** | Helper to execute PowerShell commands inside the portable environment. | None | Keep. |
| `run_in_env.cmd` | 423 B | **B: KEEP** | Helper to execute CMD commands inside the portable environment. | None | Keep. |
| `env.ps1` | 827 B | **B: KEEP** | Exports PATH and environment variables for local sessions. | None | Keep. |
| `run_pipeline.bat` | 596 B | **D: CONSOLIDATE** | Legacy batch script that calls in-memory `pipeline.py`. | May invoke legacy worker instead of persistent queue. | Update to invoke `scripts/start_daily_pipeline.ps1` (persistent job queue). |

---

### 3.2 Tools & Temporary Setup Scripts (`tools/`)

| File Path | Size | Category | Description / Purpose | Potential Risk | Mitigation / Action |
| :--- | :---: | :---: | :--- | :--- | :--- |
| `tools\python\` | ~45 MB | **B: KEEP** | Official signed PSF Python 3.11.9 runtime. | System failure if deleted. | **NEVER TOUCH.** |
| `tools\nodejs\` | ~55 MB | **B: KEEP** | Portable Node.js v20.18.0 & npm 10.8.2. | Vite builds will fail. | **NEVER TOUCH.** |
| `tools\ffmpeg\` | ~120 MB | **B: KEEP** | FFmpeg and FFprobe 9.0.2 essentials executables. | Rendering and QC will fail. | **NEVER TOUCH.** |
| `tools\uv\` | ~45 MB | **B: KEEP** | Portable UV 0.5.11 package manager. | Tooling breaks if deleted. | **NEVER TOUCH.** |
| `tools\downloads\python-embed.zip` | 11.2 MB | **F: PURGE** | Leftover zip archive from initial Python download. | None (already extracted). | Delete to free 11.2 MB. |
| `tools\setup_tools.ps1` | 3.8 KB | **E: ARCHIVE** | Initial one-off bootstrap script used to download portable runtimes. | Not needed during daily operations. | Move to `archive/setup/`. |
| `tools\finish_tools.ps1` | 2.8 KB | **E: ARCHIVE** | Second one-off bootstrap script for signed Python extraction. | Not needed during daily operations. | Move to `archive/setup/`. |
| `tools\diagnose_tts.py` | 4.0 KB | **E: ARCHIVE** | Standalone Edge-TTS diagnostic script used to fix timeout hanging. | One-off test utility. | Move to `archive/diagnostics/`. |
| `tools\test_render.py` | 3.9 KB | **E: ARCHIVE** | Standalone 5-second animation render test script. | Superseded by test suite. | Move to `archive/diagnostics/`. |
| `tools\render_sample_full.py` | 3.2 KB | **E: ARCHIVE** | Standalone 16-second animation render test script. | Superseded by test suite. | Move to `archive/diagnostics/`. |

---

### 3.3 Backend Services & Workers (`backend/`)

| File Path | Size | Category | Description / Purpose | Potential Risk | Mitigation / Action |
| :--- | :---: | :---: | :--- | :--- | :--- |
| `backend\app\main.py` | 7.5 KB | **A: KEEP** | FastAPI entry point, 20 REST endpoints, static mounts. | Core backend breaks. | Keep as primary API server. |
| `backend\app\database\db.py` | 5.6 KB | **A: KEEP** | SQLite models, `JobModel` state machine, connection pooling. | Data layer breaks. | Keep as single DB source. |
| `backend\studio.db` | 69.6 KB | **A: KEEP** | SQLite production database file with job checkpoints. | Job history and logs lost. | Keep and create auto-backup before updates. |
| `backend\app\workers\job_manager.py` | 15.8 KB | **A: KEEP** | Modern persistent job queue with stage checkpointing & recovery. | Pipeline stops working. | Keep as primary orchestrator. |
| `backend\app\workers\pipeline.py` | 13.2 KB | **D: CONSOLIDATE** | Older in-memory pipeline orchestrator. | Redundant state handling. | Deprecate in favor of `job_manager.py`; route REST endpoints to `job_manager`. |
| `backend\app\services\stick_animation_service.py` | 18.7 KB | **A: KEEP** | 2D Stick-Figure & Motion Graphics render engine (35 FPS). | Primary video engine breaks. | Keep; integrate into modular styles. |
| `backend\app\services\animation_service.py` | 14.5 KB | **E: ARCHIVE** | Facial-component sprite animation engine (superseded). | Breaking references if deleted. | Move to `archive/legacy_engines/` or keep as legacy fallback. |
| `backend\app\services\tts_service.py` | 4.5 KB | **A: KEEP** | Edge-TTS voice generation & millisecond ASS subtitle burn-in. | Audio/subtitles break. | Keep as primary TTS service. |
| `backend\app\services\qc_service.py` | 5.2 KB | **A: KEEP** | 15-Point automated quality control engine. | Validation fails. | Keep. |
| `backend\app\services\youtube_service.py` | 4.6 KB | **A: KEEP** | YouTube Data API v3 upload and scheduled publishing. | Uploads fail. | Keep. |
| `backend\app\agents\*` | ~23 KB | **A: KEEP** | 5 AI Agents (Trend, Selector, Script, Director, SEO). | Core generation breaks. | Keep. |
| `backend\tests\run_audit_tests.py` | 10.3 KB | **A: KEEP** | Comprehensive 13-point test suite for the entire backend. | Quality gate lost. | Keep as standard CI test. |
| `backend\tests\test_job_manager.py` | 2.6 KB | **A: KEEP** | Unit tests for state machine, heartbeats, and recovery. | None | Keep. |
| `backend\tests\test_stick_animation.py` | 4.8 KB | **A: KEEP** | Unit tests for stick figure poses and video rendering. | None | Keep. |

---

### 3.4 Animation Engine Prototype (`animation-engine/`)

| File Path | Size | Category | Description / Purpose | Potential Risk | Mitigation / Action |
| :--- | :---: | :---: | :--- | :--- | :--- |
| `animation-engine\package.json` | 287 B | **E: ARCHIVE** | Node package manifest containing `@resvg/resvg-js`. | Incomplete prototype. | Move `animation-engine/` to `archive/animation_engine_prototype/`. |
| `animation-engine\rasterize_assets.js` | 1.4 KB | **E: ARCHIVE** | Script used to rasterize facial character SVGs into PNG sprites. | No longer needed for stick/vector styles. | Move to `archive/animation_engine_prototype/`. |
| `animation-engine\src\characters\CharacterPose.ts` | 5.2 KB | **E: ARCHIVE** | TypeScript pose definitions for stick figures. | Logic already ported to Python. | Move to `archive/animation_engine_prototype/`. |
| `animation-engine\src\characters\StickFigure.ts` | 840 B | **E: ARCHIVE** | TypeScript interface definitions for stick figures. | Logic already ported to Python. | Move to `archive/animation_engine_prototype/`. |
| `animation-engine\src\backgrounds\`, etc. | 0 B | **G: RESTRUCTURE** | Empty directories (`assets`, `backgrounds`, `effects`, `scenes`, `templates`). | Clutters tree. | Move with prototype to `archive/`. |

---

### 3.5 Media, Thumbnails & Assets

| File Path | Size | Category | Description / Purpose | Potential Risk | Mitigation / Action |
| :--- | :---: | :---: | :--- | :--- | :--- |
| `Youtube Channel Src\*` | ~3.5 MB | **C: KEEP** | Critical official YouTube channel branding assets. | Irreversible brand loss. | **CRITICAL: NEVER MODIFY OR DELETE.** |
| `assets\audio\music\ambient_tech_theme.wav` | 11.4 MB | **C: KEEP** | Background ambient track ducked under speech. | Video audio loses music bed. | Keep. |
| `assets\audio\sfx\whoosh_transition.wav` | 141 KB | **C: KEEP** | Whoosh transition sound effect. | None | Keep. |
| `assets\audio\sfx\chime_pop.wav` | 158 KB | **C: KEEP** | Milestone chime pop sound effect. | None | Keep. |
| `assets\audio\generate_assets.py` | 4.7 KB | **A: KEEP** | Procedural waveform synthesizer for royalty-free music & SFX. | Audio regeneration lost. | Keep. |
| `assets\backgrounds\rendered\*.png` | ~580 KB | **C: KEEP** | 1080x1920 space and tech lab background graphics. | None | Keep. |
| `assets\characters\host\sprites\*.png` | ~350 KB | **E: ARCHIVE** | 14 PNG cutout sprites for legacy facial character. | Superseded by 2D stick-figure storytelling. | Move to `archive/legacy_sprites/`. |
| `assets\icons\` | 0 B | **G: RESTRUCTURE** | Empty directory. | None | Prune empty directory. |
| `media\video\sample_short.mp4` | 1.40 MB | **C: KEEP** | Verified working render of Engine 1 (Facial Rig). | Proof of functionality lost. | Keep as reference sample. |
| `media\video\stick_sample_short.mp4` | 1.55 MB | **C: KEEP** | Verified working render of Engine 2 (Stick Rig). | Proof of functionality lost. | Keep as primary reference sample. |
| `media\video\test_1sec.mp4` | 144 KB | **F: PURGE** | 1-second test render artifact from debugging. | None | Delete safely. |
| `media\video\stick_test_1sec.mp4` | 197 KB | **F: PURGE** | 1-second test render artifact from debugging. | None | Delete safely. |
| `thumbnails\output\sample_short_thumb.png` | 219 KB | **C: KEEP** | High-contrast thumbnail for sample short. | None | Keep. |
| `thumbnails\output\stick_sample_thumb.png` | 178 KB | **C: KEEP** | High-contrast thumbnail for stick short. | None | Keep. |
| `thumbnails\templates\` | 0 B | **G: RESTRUCTURE** | Empty directory. | None | Prune empty directory. |
| `models\` | 0 B | **G: RESTRUCTURE** | Empty directory intended for local ML models. | None | Prune empty directory. |

---

### 3.6 Cache Directories (`cache/`)

| File Path | Estimated Size | Category | Description / Purpose | Potential Risk | Mitigation / Action |
| :--- | :---: | :---: | :--- | :--- | :--- |
| `cache\uv\` | ~250 MB | **F: PURGE** | Cached wheel downloads from UV package installations. | None (packages already installed in `python-env`). | Safe to clear; UV re-fetches if ever needed. |
| `cache\npm\` | ~150 MB | **F: PURGE** | Cached tarballs from npm package installations. | None (frontend already built into `frontend/dist`). | Safe to clear. |
| `cache\temp\` | ~20 MB | **F: PURGE** | Temporary OS scratch files created during renders. | None | Safe to clear. |

**Total Cache Reclaimable Space:** **~420 MB**

---

### 3.7 Documentation & Ground-Truth Reference Styles (`docs/`)

| File Path | Size | Category | Description / Purpose | Potential Risk | Mitigation / Action |
| :--- | :---: | :---: | :--- | :--- | :--- |
| `docs\reference_styles\chalk_talk.html` | 56.5 KB | **C: KEEP** | Complete working reference for Chalk Talk style (Hand-Drawn). | Ground truth lost. | **PRESERVE UNCHANGED.** |
| `docs\reference_styles\creator_motion_graphics.html` | 73.9 KB | **C: KEEP** | Complete working reference for Creator Motion Graphics (Kinetic Type). | Ground truth lost. | **PRESERVE UNCHANGED.** |
| `docs\reference_styles\data_story.html` | 56.2 KB | **C: KEEP** | Complete working reference for Data Story style (Editorial Data). | Ground truth lost. | **PRESERVE UNCHANGED.** |
| `docs\reference_styles\pop_info.html` | 57.5 KB | **C: KEEP** | Complete working reference for Pop Info style (Illustrated Explainer). | Ground truth lost. | **PRESERVE UNCHANGED.** |
| `docs\PROJECT_PROGRESS_AUDIT.md` | 10.9 KB | **A: KEEP** | Earlier progress audit document. | Historical context lost. | Keep. |
| `docs\SYSTEM_ARCHITECTURE.md` | 3.5 KB | **A: KEEP** | High-level system architecture overview. | None | Keep. |
| `docs\AUTOMATION_SETUP.md` | 1.8 KB | **A: KEEP** | Windows Task Scheduler instructions. | None | Keep. |
| `docs\YOUTUBE_OAUTH_SETUP.md` | 1.9 KB | **A: KEEP** | YouTube Data API v3 OAuth setup guide. | None | Keep. |
| `docs\ANIMATION_ENGINE.md` | 2.1 KB | **A: KEEP** | Character rig documentation. | None | Keep. |
| `docs\TEST_RESULTS.md` | 3.0 KB | **A: KEEP** | Benchmark & test suite logs. | None | Keep. |
| `docs\audit_test_results.json` | 3.6 KB | **A: KEEP** | Machine-readable test execution report. | None | Keep. |
| `docs\PROJECT_AUDIT.md` | 26.8 KB | **A: KEEP** | Comprehensive technical audit report. | None | Primary audit document. |
| `docs\CLEANUP_PLAN.md` | 18.1 KB | **A: KEEP** | Safe cleanup plan and categorization. | None | This document. |
| `docs\PROPOSED_ARCHITECTURE.md` | 27.8 KB | **A: KEEP** | Evolved multi-style architecture specification. | None | Target architecture document. |

---

## 4. Proposed Phased Execution Plan (For User Approval)

When the user gives approval to execute cleanup, the operations should proceed in five atomic phases:

### Phase 1: Pre-Cleanup Safety Backup
1. Create a timestamped backup of the database:
   `Copy-Item "D:\AI Arts\AI\backend\studio.db" "D:\AI Arts\AI\backend\studio.db.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"`
2. Create the archive directory structure:
   `New-Item -ItemType Directory -Path "D:\AI Arts\AI\archive\setup", "D:\AI Arts\AI\archive\diagnostics", "D:\AI Arts\AI\archive\legacy_engines", "D:\AI Arts\AI\archive\legacy_sprites" -Force`

### Phase 2: Archive Superseded Files
1. Move one-off setup scripts (`tools/setup_tools.ps1`, `tools/finish_tools.ps1`) to `archive/setup/`.
2. Move one-off diagnostics (`tools/diagnose_tts.py`, `tools/test_render.py`, `tools/render_sample_full.py`) to `archive/diagnostics/`.
3. Move `animation-engine/` directory to `archive/animation_engine_prototype/`.
4. Move legacy character sprites (`assets/characters/host/sprites/`) to `archive/legacy_sprites/`.

### Phase 3: Purge Transient Caches & Test Media
1. Delete leftover install zip: `Remove-Item "D:\AI Arts\AI\tools\downloads\python-embed.zip" -Force`
2. Delete 1-second test video fragments: `Remove-Item "D:\AI Arts\AI\media\video\*test_1sec.mp4" -Force`
3. Clear package caches:
   `Remove-Item "D:\AI Arts\AI\cache\uv\*" -Recurse -Force`
   `Remove-Item "D:\AI Arts\AI\cache\npm\*" -Recurse -Force`
   `Remove-Item "D:\AI Arts\AI\cache\temp\*" -Recurse -Force`
4. Prune empty directories: `models/`, `assets/icons/`, `thumbnails/templates/`.

### Phase 4: Consolidate Code & Routing
1. Update `backend/app/main.py` pipeline trigger endpoints to route directly through `job_manager.py` (persistent state machine) rather than `pipeline.py`.
2. Update `run_pipeline.bat` to call `scripts/start_daily_pipeline.ps1`.
3. Deprecate `backend/app/workers/pipeline.py` by archiving it to `archive/workers/`.

### Phase 5: Verification & Integrity Test
1. Run `python backend/tests/run_audit_tests.py` to ensure all 13 core systems pass.
2. Confirm FastAPI server boots cleanly and serves the dashboard.
3. Verify that all 4 HTML reference styles remain untouched in `docs/reference_styles/`.
4. Verify that `Youtube Channel Src/` remains completely intact.

---

## 5. Summary of Storage Impact

| Action | Current Size | Projected Size | Space Reclaimed |
| :--- | :---: | :---: | :---: |
| **Package Caches (`cache/uv`, `cache/npm`)** | ~400 MB | ~0 MB | **+400 MB** |
| **Downloaded Installer Zips** | 11.2 MB | 0 MB | **+11.2 MB** |
| **Transient Test Renders** | ~350 KB | 0 KB | **+350 KB** |
| **Archived Prototypes & Scripts** | ~2 MB | Moved to `archive/` | 0 MB (preserves code) |
| **Net Storage Saved** | | | **~411.5 MB** |

---

## 6. Sign-off Request
This cleanup plan is **DRY-RUN ONLY**. No files have been deleted, moved, or altered. Execution will only begin once explicitly authorized by the user.
