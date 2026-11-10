# Stage 3.15 — Pre-Migration Project Audit Report

**Audit Date:** 2026-11-10  
**Current Root:** `D:\AI Arts\AI\`  
**Target Canonical Root:** `D:\Agents\CrazzyDocs\`  
**Target Future Topology:** `D:\Agents\{AgentName}\` (Multi-agent independent instances)  
**Status:** Audit Complete — Ready for Destination Preparation & Copy

---

## 1. Executive Summary

This pre-migration audit catalogs all environment configurations, absolute path dependencies, credentials, database structures, media manifests, schedulers, and runtime tools across the cleaned CrazzyDocs project. No modifications to the active production project were made during this audit phase.

### Migration Scope & Readiness Assessment
- **Portability:** Highly viable. Modular backend architecture with portable Python 3.11.9 runtime in `tools/python/` and FFmpeg in `tools/ffmpeg/`.
- **Primary Migration Task:** Consolidate disparate hardcoded `WORKSPACE = r"D:\AI Arts\AI"` constants into a centralized root path resolver (`D:\Agents\CrazzyDocs`), recreate a clean `.venv`, and update SQLite publishing ledger paths and root launch scripts.
- **Data Protection:** Zero database record loss. SQLite `backend/studio.db` verified with `integrity_check: ok`. Backups preserved. Private YouTube upload `4DCas1e4vHI` verified.

---

## 2. Absolute Path Inventory (`D:\AI Arts\AI`)

Across the project, 5,464 lines contain references to the old root (`D:\AI Arts\AI`):

| Scope / Category | Files Affected | Occurrences | Description / Remediation |
|---|---|---|---|
| **Backend Core & Services** | 18 files | 27 occurrences | Module-level `WORKSPACE` / `BASE_DIR` constants in orchestrator, workspace, config, database, and rendering engines. Transition to centralized resolver `backend/app/core/paths.py`. |
| **Backend Scripts & Tools** | 22 files | 38 occurrences | Operational tools (`configure_youtube_oauth.py`, `manage_windows_startup.py`, etc.). Transition to centralized resolver. |
| **Backend Test Suites** | 19 files | 34 occurrences | Test fixtures (`test_stage_3_13a_content_integrity.py`, `test_production_workspace.py`, etc.). Transition to centralized resolver. |
| **Root Shell & Batch Scripts** | 5 files | 5 occurrences | `start_studio.bat`, `start_studio.ps1`, `run_pipeline.bat`, `run_in_env.ps1`, `env.ps1`. Update to target path and portable directory resolution. |
| **Active SQLite Database** | 1 file | 10 occurrences | `publishing_ledger` rows (22, 23, 32, 33, 34, 36, 37, 38, 40, 42) store absolute file paths to canonical deliverables. Update via SQL migration script at destination. |
| **Documentation & Guidelines** | 18 files | 112 occurrences | Architecture docs and execution standards (`GEMINI.md`, `AUTONOMOUS_EXECUTION_POLICY.md`). Update canonical path references; preserve historical changelogs. |
| **Media Audits & JSON Caches** | 6 files | 5,191 occurrences | Historical scan files (`workspace_inventory.json`, `duplicate_media_inventory.json`). Historical audit logs preserved as historical records. |

---

## 3. Python Runtime & Virtual Environment Specification

### Current Environment
- **Virtual Environment Directory:** `D:\AI Arts\AI\python-env` (1,230 MB). Contains machine-specific pyvenv.cfg and hardcoded path scripts in `Scripts/`.
- **Underlying Portable Python:** `D:\AI Arts\AI\tools\python\cpython-3.11.11-windows-x86_64-none\python.exe` (Python 3.11.9).
- **Package Manager:** `D:\AI Arts\AI\tools\uv\uv.exe` (0.6.14).
- **Authoritative Dependency File:** `backend/requirements.txt` (20 top-level dependencies, including `fastapi`, `uvicorn`, `pydantic`, `google-genai`, `edge-tts`, `google-api-python-client`, `pytest`).

### Destination Strategy
1. Exclude the old `python-env` from copy.
2. At `D:\Agents\CrazzyDocs`, use `tools/uv/uv.exe` with `tools/python/python.exe` to provision a clean virtual environment at `.venv`.
3. Install dependencies directly from `backend/requirements.txt`.
4. Validate that all 78 installed distribution packages match exactly.

---

## 4. Database & Credential Audit

### SQLite Database (`backend/studio.db`)
- **File Size:** 6,483,968 bytes (~6.18 MB).
- **Journal Mode:** `delete` (No lingering `.wal` or `.shm` files; zero dangling transactions).
- **Integrity Check:** `PRAGMA integrity_check` -> `ok`.
- **Table Counts & Schema:**
  - `topics`: 15 records
  - `research_records`: 15 records
  - `publishing_ledger`: 47 records (Row 32: YouTube ID `4DCas1e4vHI`, `uploaded_private`)
  - `persistent_jobs`: Verified intact
  - `persistent_notifications`: Verified intact
  - `studio_settings`: Verified intact
- **Database Backups:** Preserved in `backend/backups/studio_backup_pre_stage_3_14b_20261110_222519.db` (6.18 MB).

### Local Configuration & Secrets
- **Local Environment:** `.env.local` (344 bytes) contains local SMTP host, port, security, user, and alert recipient. Zero hardcoded secrets in source code.
- **OAuth Client Secrets:** `backend/client_secrets.json` (481 bytes) - local OAuth credentials.
- **OAuth Authorized Token:** `backend/token.json` (837 bytes) - active user credentials with refresh capability verified (`authenticated=True, can_refresh=True`).
- **Security Rule:** Neither `.env.local`, `client_secrets.json`, nor `token.json` will be exposed or printed into output logs.

---

## 5. Media Deliverables & Production Manifests

### Canonical Production Workspaces (`media/productions/`)
- `prod_cycle_4_20261110_1800`:
  - `video/showcase_cycle_4_20261110_1800.mp4` (18.16 MB, 56.63s, 1080x1920)
  - `thumbnails/thumb_cycle_4_20261110_1800.png` (2.00 MB, 1080x1920)
  - `production_manifest.json` (Relative paths only)
- `prod_20261110_vesna_vulovic`:
  - `video/showcase_vesna_vulovic_verified.mp4` (14.23 MB, 52.0s, 1080x1920)
  - `thumbnails/thumb_vesna_vulovic_verified.png` (1.41 MB, 1080x1920)
  - `thumbnails/variants/*.png` (4 candidate variants)
  - `production_manifest.json` (Relative paths only)
- `test_checkpoint_job_001`: Test workspace checkpoint.
- `test_vesna_integrity_run`: Verification test workspace.

### Root Media Directories
- `media/video/`: Contains only `showcase_stage_3_4_tsutomu_yamaguchi.mp4` (18.16 MB historical fixture).
- `media/thumbnails/`: Contains only `showcase_stage_3_9a_yamaguchi_poster.png` (2.23 MB historical fixture).

### Master Branding & HTML Reference Styles
- `Youtube Channel Src/Youtube-Icon.png`: SHA-256 `65b14bb348376d3b91c62538f91eadfd5e5e36d4109a525af7bb63e817730060` (Strictly Immutable).
- `media/source/branding/crazzydocs_outro_1080x1920.mp4`: SHA-256 `b244ba1141b8622a654afca9014a63d5bd97d7630ddf4f3935a37980dec14945` (Strictly Immutable).
- `docs/reference_styles/*.html`: 4 immutable ground-truth templates (`pop_info.html`, `chalk_talk.html`, `creator_motion.html`, `data_story.html`).

---

## 6. Scheduler & Startup Status

- **Windows Task Scheduler:** `CrazzyDocsStudioScheduler` task status is `is_registered: false`. No existing Windows startup task is active.
- **In-App Production Scheduler:** Configured via `backend/app/scheduler/schedule_profiles.py` using `FOUR_CYCLES_DAILY` (4 cycles/day: 10:00, 14:00, 18:00, 22:00 IST).
- **Timezone:** `Asia/Kolkata` (UTC+05:30).
- **Catch-up & Missed Cycle Reconciliation:** Preserved through `ReconciliationService` and persistent SQLite checkpoints.

---

## 7. Migration Exclusion & Inclusion Manifest

### Excluded from Copy (Disposable / Generated Caches)
- `python-env/` (~1.23 GB) — Will be recreated cleanly at destination as `.venv`.
- `.pytest_cache/` (~1.5 MB) — Ephemeral test cache.
- `cache/uv/`, `cache/npm/`, `cache/temp/` — Runtime package and build caches.
- `__pycache__/` subdirectories — Bytecode caches.

### Mandatory Inclusions
- Complete Git repository (`.git/`) with branch `main` and history intact.
- Source code (`backend/app/`, `backend/scripts/`, `scripts/`).
- Authoritative tools (`tools/python/`, `tools/ffmpeg/`, `tools/uv/`, `tools/nodejs/`).
- Master branding (`Youtube Channel Src/`, `assets/branding/`, `media/source/branding/`).
- Reference styles (`docs/reference_styles/`).
- Active media & production workspaces (`media/productions/`, `media/images/`, `media/audio/`, `media/subtitles/`, `media/thumbnails/`, `media/video/`).
- Quarantined archive (`media/quarantine/`).
- Authoritative documentation (`docs/`, `GEMINI.md`, `README.md`).
- SQLite database & backups (`backend/studio.db`, `backend/backups/`).
- Configuration & OAuth secrets (`.env.local`, `backend/client_secrets.json`, `backend/token.json`).

Pre-migration audit is complete and validated. Ready to proceed to Phase 2 (Destination Preparation).
