# Phase 5, Stage 3.15 — Project Relocation & Multi-Agent Foundation Report

**Status:** COMPLETED & VERIFIED  
**Date:** 2026-11-10  
**Source Root (Preserved Rollback):** `D:\AI Arts\AI\`  
**Canonical Destination Root:** `D:\Agents\CrazzyDocs\`  
**Target Architecture:** Multi-Agent Foundation (`D:\Agents\{AgentName}\`)  
**Publishing Mode:** `manual_approval` (Private uploads only; zero public publishing)

---

## 1. Executive Summary

Stage 3.15 successfully executed the controlled, copy-first migration of the CrazzyDocs production system from `D:\AI Arts\AI\` to `D:\Agents\CrazzyDocs\`. 

The destination is now the **authoritative canonical location** of CrazzyDocs, establishing the foundation for future independent YouTube channel agents under `D:\Agents\`. The original project at `D:\AI Arts\AI\` has been preserved completely intact as an immutable rollback copy.

All 65 regression tests pass with 100% success from the new destination, YouTube OAuth credentials and refresh mechanisms remain fully functional, master branding assets match their exact SHA-256 ground truth, and zero active runtime dependencies on the old root exist.

---

## 2. Source and Destination Specifications

| Parameter | Source (Preserved Rollback) | Destination (New Canonical Root) |
|---|---|---|
| **Location** | `D:\AI Arts\AI\` | `D:\Agents\CrazzyDocs\` |
| **Agent Identity** | Legacy shared workspace | `agent_id: crazzydocs`, `name: CrazzyDocs` |
| **Python Virtualenv** | `python-env` (1,230 MB, machine-specific) | `.venv` (Clean Python 3.11.9, uv-managed) |
| **Total Files** | ~11,200 files (with old env/caches) | **9,915 files** (Cleaned, caches excluded) |
| **Total Size on Disk** | ~2,436 MB | **1,201.8 MB (~1.17 GB)** |
| **Underlying Tools** | `tools/python`, `tools/ffmpeg`, `tools/uv` | `tools/python`, `tools/ffmpeg`, `tools/uv` |
| **Database Path** | `backend/studio.db` | `backend/studio.db` |
| **Publishing Mode** | `manual_approval` | `manual_approval` |
| **Timezone** | `Asia/Kolkata` (IST) | `Asia/Kolkata` (IST) |

---

## 3. Critical Asset SHA-256 Hash Verification

Before and after the migration, all authoritative branding assets, reference style templates, private YouTube video deliverables, and configuration databases were verified to confirm 100% bit-for-bit parity:

| Critical Asset Relative Path | Source SHA-256 | Destination SHA-256 | Status |
|---|---|---|---|
| `backend/studio.db` | `026839284cfd...` | `026839284cfd...` | **VERIFIED IDENTICAL** |
| `backend/client_secrets.json` | `fc8892b51f4e...` | `fc8892b51f4e...` | **VERIFIED IDENTICAL** |
| `backend/token.json` | `f3164b490646...` | `f3164b490646...` | **VERIFIED IDENTICAL** |
| `.env.local` | `d4dc50be06af...` | `d4dc50be06af...` | **VERIFIED IDENTICAL** |
| `Youtube Channel Src/Youtube-Icon.png` | `65b14bb34837...` | `65b14bb34837...` | **VERIFIED IDENTICAL** |
| `media/source/branding/crazzydocs_outro_1080x1920.mp4` | `b244ba1141b8...` | `b244ba1141b8...` | **VERIFIED IDENTICAL** |
| `media/video/showcase_stage_3_4_tsutomu_yamaguchi.mp4` | `75ca472335dc...` | `75ca472335dc...` | **VERIFIED IDENTICAL** |
| `media/thumbnails/showcase_stage_3_9a_yamaguchi_poster.png` | `ec998f455254...` | `ec998f455254...` | **VERIFIED IDENTICAL** |
| `media/productions/prod_cycle_4_20261110_1800/video/showcase_cycle_4_20261110_1800.mp4` | `75ca472335dc...` | `75ca472335dc...` | **VERIFIED IDENTICAL** |
| `media/productions/prod_cycle_4_20261110_1800/thumbnails/thumb_cycle_4_20261110_1800.png` | `bb28cde1c3ad...` | `bb28cde1c3ad...` | **VERIFIED IDENTICAL** |
| `media/productions/prod_20261110_vesna_vulovic/video/showcase_vesna_vulovic_verified.mp4` | `0a172c987505...` | `0a172c987505...` | **VERIFIED IDENTICAL** |
| `media/productions/prod_20261110_vesna_vulovic/thumbnails/thumb_vesna_vulovic_verified.png` | `a4024b3f401b...` | `a4024b3f401b...` | **VERIFIED IDENTICAL** |
| `docs/reference_styles/pop_info.html` | `47080baf5cfd...` | `47080baf5cfd...` | **VERIFIED IDENTICAL** |
| `docs/reference_styles/chalk_talk.html` | `cab7c70ad3fc...` | `cab7c70ad3fc...` | **VERIFIED IDENTICAL** |
| `docs/reference_styles/creator_motion_graphics.html` | `a0145a30e8a9...` | `a0145a30e8a9...` | **VERIFIED IDENTICAL** |
| `docs/reference_styles/data_story.html` | `64748900d49f...` | `64748900d49f...` | **VERIFIED IDENTICAL** |

---

## 4. Centralized Path Resolution & Architecture

### Centralized Root Resolver: `backend/app/core/paths.py`
Instead of fragmented hardcoded path constants, all application paths now resolve dynamically relative to `PROJECT_ROOT`:
- **Default Root:** Automatically resolves via `Path(__file__).resolve().parents[3]`, returning `D:\Agents\CrazzyDocs`.
- **Environment Override:** Supports `CRAZZYDOCS_PROJECT_ROOT` if explicitly specified.
- **Key Resolvers:**
  - `get_project_root()` -> `Path("D:/Agents/CrazzyDocs")`
  - `DATABASE_PATH` -> `D:\Agents\CrazzyDocs\backend\studio.db`
  - `get_ffmpeg_path()` -> `D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffmpeg.exe`
  - `get_ffprobe_path()` -> `D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffprobe.exe`
  - `resolve_path(rel_path)` -> Absolute path normalized to the current root.

### Multi-Agent Identity: `backend/app/core/agent_identity.py`
Establishes the foundation for `D:\Agents\`:
- `agent_id`: `crazzydocs`
- `agent_name`: `CrazzyDocs`
- `agent_root`: `D:\Agents\CrazzyDocs`
- `timezone`: `Asia/Kolkata`
- `publishing_mode`: `manual_approval`
- `channel_handle`: `@crazzydocs`
- `channel_id`: `UCUOZOEBngXgt6W7fmus0bvw`

Future agents (`Agent_02`, `Agent_03`) will deploy as peer directories under `D:\Agents\` with zero shared databases or credential collisions.

---

## 5. Python Environment Recreation (`.venv`)

1. **Recreation:** Created fresh `.venv` using portable runtime `D:\Agents\CrazzyDocs\tools\python\python.exe` and `tools\uv\uv.exe`.
2. **Dependencies Installed:** 79 packages installed in 2.56 seconds from `backend/requirements.txt` (FastAPI, Uvicorn, SQLAlchemy, Pydantic, google-genai, edge-tts, pytest, etc.).
3. **AppLocker / WDAC Pure-Python Fallback:** SQLAlchemy Cython C-extensions (`_cache_key_cy.pyd`, `_result_cy.pyd`, etc.) were cleanly transitioned to their built-in pure Python fallbacks (`.pyd.disabled`), matching the existing production behavior and ensuring uninterrupted execution under Windows Application Control policies.
4. **Tool Verification:** FFmpeg and FFprobe validated successfully (`ffmpeg version 9.0.2-essentials_build`).

---

## 6. Database & Credential Migration

1. **Integrity:** `PRAGMA integrity_check` verified with `[('ok',)]`.
2. **Publishing Ledger Paths:** 47 rows updated to reference canonical paths in `D:\Agents\CrazzyDocs\`.
3. **Private YouTube Upload Row 32:**
   - Topic: *The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived*
   - Video ID: `4DCas1e4vHI`
   - Status: `uploaded_private`
   - Video Deliverable: `D:\Agents\CrazzyDocs\media\productions\prod_cycle_4_20261110_1800\video\showcase_cycle_4_20261110_1800.mp4` (Confirmed accessible)
   - Thumbnail: `D:\Agents\CrazzyDocs\media\productions\prod_cycle_4_20261110_1800\thumbnails\thumb_cycle_4_20261110_1800.png` (Confirmed accessible)
4. **OAuth Token Refresh:** Validated via YouTube Data API v3:
   - `is_authenticated: True`
   - `can_refresh: True`
   - `connected_channel: CrazzyDocs (@crazzydocs, UCUOZOEBngXgt6W7fmus0bvw)`
5. **Email / SMTP:** Verified `.env.local` retains local credentials with `CRAZZYDOCS_SMTP_HOST`, port, security, and alert recipient configured.

---

## 7. Scheduler & Startup Verification

1. **Windows Task Scheduler:** `CrazzyDocsStudioScheduler` status confirmed as `is_registered: false`. (Preserved as un-registered per instructions).
2. **Production Schedule:** Configured for `FOUR_CYCLES_DAILY`:
   - Cycle 1: 00:00 Production -> 06:00 IST Target
   - Cycle 2: 06:00 Production -> 12:00 IST Target
   - Cycle 3: 12:00 Production -> 18:00 IST Target
   - Cycle 4: 18:00 Production -> 00:00 IST Next-Day Target
3. **Timezone:** `Asia/Kolkata` (UTC+05:30) with automatic missed-cycle recovery.

---

## 8. Full 65-Test Regression Results

Ran from `D:\Agents\CrazzyDocs` using `.venv\Scripts\python.exe -m pytest`:
```
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.2.0, pluggy-1.7.0
rootdir: D:\Agents\CrazzyDocs
plugins: anyio-4.16.0, asyncio-1.4.1.post1
asyncio: mode=Mode.STRICT, debug=False
collected 65 items

backend\tests\test_branding_integrity.py .....                           [  7%]
backend\tests\test_production_workspace.py ...                           [ 12%]
backend\tests\test_stage_3_13b_lifecycle_audit.py .........              [ 26%]
backend\tests\test_stage_3_9b_media_lifecycle.py .....                   [ 33%]
backend\tests\test_stage_3_13a_content_integrity.py ..........           [ 49%]
backend\tests\test_stage_3_12_youtube.py .......                         [ 60%]
backend\tests\test_stage_3_9cd_scheduler.py ......                       [ 69%]
backend\tests\test_stage_3_9f_email_dashboard.py ..........              [ 84%]
backend\tests\test_stage_3_10_resilience.py ..........                   [100%]

======================= 65 passed, 2 warnings in 29.45s =======================
```

---

## 9. Controlled Minimal Rendering Test

A controlled 1.0-second 1080x1920 vertical video render was executed at `D:\Agents\CrazzyDocs` using the destination FFmpeg binary:
- **Output:** `media/productions/test_migration_render/test_1sec.mp4` (4,407 bytes, 1080x1920 @ 30 FPS, H.264/yuv420p).
- **Integrity:** Created and verified successfully; temporary artifacts immediately purged.
- **Zero Public Uploads:** Publishing mode was strictly maintained as `manual_approval`.

---

## 10. Audit of Remaining Old-Path References

| Scope | Classification | Count | Retention Justification |
|---|---|---|---|
| Historical Phase Reports (`docs/PHASE_5_STAGE_*.md`, `docs/WORKSPACE_*.md`) | Historical Records | 112 | Preserved unchanged to maintain immutable historical documentation audit trail. |
| Historical Media Scans (`media/audit/*.json`) | Historical Audit Logs | ~5,100 | Generated during Phase 5 Stages 3.13–3.14 prior to relocation. Retained for historical reference. |
| Migration Scripts (`apply_path_migration.py`, `migrate_copy_project.py`) | Migration Tools | 6 | Retain string patterns `D:\AI Arts\AI` to enable audit and reverse mapping if necessary. |
| Active Runtime Backend Code & Operational Scripts | Active Code | **0** | **Zero active runtime references remain.** All active code resolves dynamically via `backend.app.core.paths`. |

---

## 11. Rollback Instructions

If a rollback to the source project is ever required:
1. The original project at `D:\AI Arts\AI\` remains completely untouched and functional.
2. In `D:\AI Arts\AI\`, the existing virtual environment `python-env`, database `backend/studio.db`, and configuration remain ready for immediate execution.
3. To reactivate the old location:
   - Change working directory to `D:\AI Arts\AI\`.
   - Execute `.\run_in_env.ps1` or run `.\python-env\Scripts\python.exe`.
   - The database backup taken prior to Stage 3.14B is safely available at `backend/backups/studio_backup_pre_stage_3_14b_20261110_222519.db`.

---

## 12. Deliverable Manifests & Files

1. **Destination Migration Manifest:**
   [`D:\Agents\CrazzyDocs\migration_manifest.json`](file:///D:/Agents/CrazzyDocs/migration_manifest.json)
2. **Source Migration Manifest:**
   [`D:\AI Arts\AI\docs\STAGE_3_15_MIGRATION_MANIFEST.json`](file:///D:/AI%20Arts/AI/docs/STAGE_3_15_MIGRATION_MANIFEST.json)
3. **Pre-Migration Audit:**
   [`D:\AI Arts\AI\docs\STAGE_3_15_PRE_MIGRATION_AUDIT.md`](file:///D:/AI%20Arts/AI/docs/STAGE_3_15_PRE_MIGRATION_AUDIT.md)
4. **Canonical Agent Operational Rules:**
   [`D:\Agents\CrazzyDocs\GEMINI.md`](file:///D:/Agents/CrazzyDocs/GEMINI.md)
5. **Centralized Paths Resolver:**
   [`D:\Agents\CrazzyDocs\backend\app\core\paths.py`](file:///D:/Agents/CrazzyDocs/backend/app/core/paths.py)
6. **Agent Identity Configuration:**
   [`D:\Agents\CrazzyDocs\backend\app\core\agent_identity.py`](file:///D:/Agents/CrazzyDocs/backend/app/core/agent_identity.py)

Stage 3.15 is complete. `D:\Agents\CrazzyDocs` is verified, autonomous, and operational as the canonical home of CrazzyDocs.
