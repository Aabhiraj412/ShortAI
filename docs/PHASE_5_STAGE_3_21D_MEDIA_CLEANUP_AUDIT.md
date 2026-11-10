# Phase 5: Stage 3.21D — Media Workspace Audit & Safe Cleanup Plan

**Project:** CrazzyDocs Autonomous Production Studio  
**Canonical Root:** `D:\Agents\CrazzyDocs\`  
**Stage:** 3.21D — Media Workspace Audit & Safe Cleanup (Audit-First / Non-Destructive)  
**Execution Timestamp:** 2026-11-11  
**Status:** Audit Completed & Candidates Inventoried  

---

## 1. Executive Summary

This audit provides a complete forensic inventory and dependency mapping of all physical media assets in `media/`. The audit was executed under strict non-destructive safety conditions: **zero physical files were moved, renamed, or deleted**.

All **324 media files** totaling **341.64 MB** across 10 top-level directories were scanned, hashed (SHA-256), cross-referenced against application source code, production manifests, test suites, and database records, and classified according to life-cycle utility.

### Key Metric Overview
- **Total Files Scanned:** 324
- **Total Media Footprint:** 341.64 MB (358,236,783 bytes)
- **Active / Reusable Assets to KEEP:** 64 files (32.01 MB)
- **Verified Files Recommended for External Archiving (SAFE_TO_ARCHIVE):** 225 files (277.84 MB)
- **Redundant Byte Duplicates / Prototypes (POSSIBLY_DISPOSABLE):** 35 files (31.79 MB)
- **Unresolved / Needs Operator Review:** 0 files (100% resolved)
- **Identical SHA-256 Duplicate Sets:** 41 sets (accounting for 106.33 MB of redundant storage)
- **Estimated Recoverable Storage from Archiving & Deduplication:** **up to 309.63 MB (90.6% of media footprint)**

---

## 2. Invariant & Pre-State Verification

Before beginning the audit, all critical project invariants were verified:

| Invariant | Target Requirement | Verified State | Status |
| :--- | :--- | :--- | :---: |
| **Git Working Tree** | Clean, main branch up to date | Commit `6719744` (YouTube Branding) | **PASS** |
| **Health Dashboard** | Active and running on port 8000 | Task `CrazzyDocs-Health-Dashboard` Running | **PASS** |
| **Production Scheduler** | Strictly DISABLED | Task `CrazzyDocs-Production-Scheduler` Disabled | **PASS** |
| **Publishing Ledger** | Clean slate (1 genuine private record) | Record #32 (`4DCas1e4vHI`, `uploaded_private`) | **PASS** |
| **Publishing Archive** | Preserved audit history | 253 records in `publishing_ledger_archive` | **PASS** |
| **Active Profile** | `FOUR_CYCLES_DAILY` active | Active profile verified | **PASS** |
| **Single Daily 23:00** | Inactive | Verified inactive | **PASS** |
| **Physical Media** | Zero deletions or moves | 324 files preserved intact | **PASS** |

---

## 3. Storage Usage by Directory

The media directory contains 10 subdirectories. The storage distribution is as follows:

| Directory | File Count | Storage (Bytes) | Storage (MB) | Percentage | Primary Contents |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `media/productions/` | 77 | 147,582,829 | 140.75 MB | 41.2% | Genuine productions, test runs, crash mocks |
| `media/quarantine/` | 69 | 97,478,415 | 92.96 MB | 27.2% | Pre-isolated archived files & quarantine manifest |
| `media/images/` | 36 | 52,581,903 | 50.15 MB | 14.7% | Historical research images (Molasses, Vesna, Yamaguchi) |
| `media/video/` | 4 | 23,598,527 | 22.51 MB | 6.6% | Standalone test renders & animation prototypes |
| `media/audit/` | 51 | 14,121,276 | 13.47 MB | 3.9% | Quality contact sheets, test reports, inventories |
| `media/thumbnails/` | 6 | 10,389,927 | 9.91 MB | 2.9% | Poster renders & candidate thumbnails |
| `media/audio/` | 34 | 8,172,707 | 7.79 MB | 2.3% | Style narration samples, telemetry, voiceover masters |
| `media/source/` | 7 | 4,159,894 | 3.97 MB | 1.2% | Official branding, YouTube banner/avatar, outro video |
| `media/email_previews/` | 8 | 111,671 | 0.11 MB | <0.1% | Notification HTML/text email previews |
| `media/subtitles/` | 32 | 39,634 | 0.04 MB | <0.1% | Subtitle style templates, transcripts, ASS/SRT files |
| **TOTAL** | **324** | **358,236,783** | **341.64 MB** | **100.0%** | |

---

## 4. File Classification (Categories A – H)

Every media file was assigned to a lifecycle category:

```
Category Distribution (by Storage):
  [D] Test & Development Artifacts: 102.27 MB (64 files)
  [C] Historical Production Archive: 97.41 MB (64 files)
  [F] Quarantined Assets:            92.96 MB (69 files)
  [G] Redundant Duplicates:          30.17 MB (33 files)
  [E] Audit & Verification Artifacts:13.57 MB (60 files)
  [B] Reusable Studio Assets:         5.26 MB (34 files)
```

### Detailed Category Breakdown

| Category | File Count | Size (MB) | Retention Recommendation | Description & Examples |
| :--- | :---: | :---: | :---: | :--- |
| **A. ACTIVE PRODUCTION ASSETS** | 0 | 0.00 MB | N/A | No active in-progress job is rendering right now (pipeline idle). |
| **B. REUSABLE ASSETS** | 34 | 5.26 MB | **KEEP (CRITICAL/HIGH)** | Official YouTube branding (`Youtube-Icon.png`, banner), outro video (`crazzydocs_outro_1080x1920.mp4`), audio benchmark samples (`sample_excited.mp3`, etc.), subtitle templates (`chalk_talk`, `creator_motion`, `pop_info`, `data_story`). |
| **C. HISTORICAL PRODUCTION ARCHIVE** | 64 | 97.41 MB | **KEEP / SAFE_TO_ARCHIVE** | Genuine completed production (`prod_20261110_vesna_vulovic` — 30 files, 26.75 MB — **KEEP**), Cycle 4 showcase (`prod_cycle_4_20261110_1800` — 3 files, 20.16 MB — **SAFE_TO_ARCHIVE**), historical source images (`media/images/historical/`, `media/images/yamaguchi/`). |
| **D. TEST & DEVELOPMENT ARTIFACTS** | 64 | 102.27 MB | **SAFE_TO_ARCHIVE** | Test verification runs (`prod_stage_3_16_verification`, `test_auto_job_001`, `test_scen_11_corrupt_cp`, `test_vesna_integrity_run`), crash test placeholders (`test_b_crash_*`), stick figure prototypes (`stick_sample_short.mp4`, `stick_test_1sec.mp4`). |
| **E. AUDIT & VERIFICATION ARTIFACTS** | 60 | 13.57 MB | **SAFE_TO_ARCHIVE** | Contact sheets (`media/audit/contact_sheet_*.png`), crash integrity reports (`integrity_report_*.json`), audit inventories, and email preview samples. |
| **F. QUARANTINED ASSETS** | 69 | 92.96 MB | **SAFE_TO_ARCHIVE** | 68 archived files in `media/quarantine/archived_files/` plus `media/quarantine/manifest.json`. Isolated during Stage 3.14/3.15 stabilization. |
| **G. DUPLICATES** | 33 | 30.17 MB | **POSSIBLY_DISPOSABLE** | Files whose SHA-256 exactly matches a canonical file preserved in Category B or C. |
| **H. UNKNOWN / NEEDS REVIEW** | 0 | 0.00 MB | None | All files successfully identified and linked to pipeline sources. |

---

## 5. Major Duplicate Hash Inventory

Cross-directory SHA-256 hashing revealed **41 duplicate sets** accounting for **106.33 MB** of redundant duplicate data.

The largest duplicate clusters include:

| SHA-256 Prefix | File Size | Copies | Redundant MB | Instances | Canonical File (Preserved) |
| :--- | :---: | :---: | :---: | :--- | :--- |
| `75ca472335dc` | 18.16 MB | 3 | **36.33 MB** | • `media/productions/prod_cycle_4_20261110_1800/video/showcase_cycle_4_20261110_1800.mp4`<br>• `media/productions/test_auto_job_001/video/showcase_test_auto_job_001.mp4`<br>• `media/video/showcase_stage_3_4_tsutomu_yamaguchi.mp4` | `media/productions/prod_cycle_4_20261110_1800/video/...` |
| `0a172c987505` | 14.23 MB | 3 | **28.47 MB** | • `media/productions/prod_20261110_vesna_vulovic/video/showcase_vesna_vulovic_verified.mp4`<br>• `media/productions/test_scen_11_corrupt_cp/video/showcase_test_scen_11_corrupt_cp.mp4`<br>• `media/productions/test_vesna_integrity_run/video/showcase_test_vesna_integrity_run.mp4` | `media/productions/prod_20261110_vesna_vulovic/video/...` |
| `034f300785d5` | 2.07 MB | 7 | **12.44 MB** | • `media/productions/prod_20261110_vesna_vulovic/thumbnails/variants/cand_03_vesna_survived.png`<br>• `media/productions/prod_stage_3_16_verification/thumbnails/thumb_prod_stage_3_16_verification.png`<br>• Duplicates in `test_scen_11_corrupt_cp` and `test_vesna_integrity_run` | `media/productions/prod_20261110_vesna_vulovic/thumbnails/...` |
| `31f5bd938fbe` | 2.08 MB | 4 | **6.25 MB** | • `cand_01_vesna_fall.png` duplicated in `prod_vesna`, `prod_stage_3_16`, `test_scen_11`, `test_vesna_integrity` | `media/productions/prod_20261110_vesna_vulovic/thumbnails/...` |
| `97892b494882` | 1.52 MB | 4 | **4.57 MB** | • `cand_01_direct_survival.png` duplicated across `test_auto_job_001`, `media/thumbnails/showcase_stage_3_5...`, `media/thumbnails/candidates/...` | `media/thumbnails/candidates/cand_01_direct_survival.png` |
| `30e4199eb354` | 0.93 MB | 4 | **2.80 MB** | • `cand_02_vesna_no_chute.png` duplicated across 4 folders | `media/productions/prod_20261110_vesna_vulovic/thumbnails/...` |
| `4a59fce2f8cf` | 0.91 MB | 4 | **2.73 MB** | • `cand_04_vesna_record.png` duplicated across 4 folders | `media/productions/prod_20261110_vesna_vulovic/thumbnails/...` |
| `ec998f455254` | 2.23 MB | 2 | **2.23 MB** | • `media/audit/thumbnails_3_9a/concept_01_split_juxtaposition.png`<br>• `media/thumbnails/showcase_stage_3_9a_yamaguchi_poster.png` | `media/audit/thumbnails_3_9a/...` |
| `bb28cde1c3ad` | 2.00 MB | 2 | **2.00 MB** | • `media/productions/prod_cycle_4_20261110_1800/thumbnails/thumb_cycle_4_20261110_1800.png`<br>• `media/productions/test_auto_job_001/thumbnails/variants/cand_02_personal_impossibility.png` | `media/productions/prod_cycle_4_20261110_1800/thumbnails/...` |
| `1d14aee4cbe3` | 1.75 MB | 2 | **1.75 MB** | • `media/audit/workspace_inventory.json`<br>• `media/audit/latest/workspace_inventory.json` | `media/audit/workspace_inventory.json` |

---

## 6. Independent Inspection of Special Production Folders

Per Section 6 of the requirements, three production directories were independently audited:

### 1. `media/productions/prod_20261110_vesna_vulovic/` (PRESERVE STRICTLY)
- **Status:** **CANONICAL GENUINE PRODUCTION**
- **Associated Upload:** YouTube Private Video `4DCas1e4vHI` (Ledger Record #32).
- **Contents:** 30 files, 26.75 MB.
  - Video master: `video/showcase_vesna_vulovic_verified.mp4` (14.2 MB)
  - Verified thumbnail: `thumbnails/thumb_vesna_vulovic_verified.png` (1.41 MB) + 4 concept variants
  - Full voiceover master and 8 individual narration beats (`narration/`)
  - 6 historical source assets + `attribution_manifest.json` (`assets/`)
  - Complete manifest: `production_manifest.json`
- **Preservation Action:** **`KEEP` (CRITICAL)**. Absolutely no file in this folder may be deleted or modified.

### 2. `media/productions/prod_cycle_4_20261110_1800/` (HISTORICAL SHOWCASE)
- **Status:** **HISTORICAL AUTOMATED SHOWCASE**
- **Associated Ledger Entry:** Archived Record #246 (archived during Stage 3.21B cleanup).
- **Contents:** 3 files, 20.16 MB.
  - Video master: `video/showcase_cycle_4_20261110_1800.mp4` (18.16 MB)
  - Thumbnail: `thumbnails/thumb_cycle_4_20261110_1800.png` (2.00 MB)
  - Manifest: `production_manifest.json` (0.5 KB)
- **Preservation Action:** **`SAFE_TO_ARCHIVE` (MEDIUM RISK)**. This is a complete showcase production. Its video is identical to `showcase_stage_3_4_tsutomu_yamaguchi.mp4`. It should be retained in the external historical archive rather than deleted.

### 3. `media/productions/prod_stage_3_16_verification/` (TEST VERIFICATION RUN)
- **Status:** **VERIFICATION TEST RUN**
- **Origin:** Generated during Stage 3.16 to verify pipeline rendering and thumbnail composition.
- **Contents:** 10 files, 22.42 MB.
  - `showcase_prod_stage_3_16_verification.mp4` (14.34 MB)
  - 4 thumbnail candidates (exact duplicates of Vesna Vulovic candidates)
  - Reports: `stage_3_16_production_summary.json`, `stage_3_16_verification_audit.json`
- **Preservation Action:** **`SAFE_TO_ARCHIVE` (LOW RISK)**. Reports and manifest document test passing; video and duplicate thumbnails are redundant with canonical Vesna production.

---

## 7. Application Dependency Trace

A complete codebase scan across `backend/app/`, `backend/scripts/`, `backend/tests/`, and `docs/` mapped all media references:

1. **Official Branding Dependencies:**
   - `media/source/branding/Youtube-Icon.png` is directly mounted, served via `/favicon.ico` in [`backend/app/main.py`](file:///D:/Agents/CrazzyDocs/backend/app/main.py), and copied to `backend/app/static/branding/`.
   - `media/source/branding/crazzydocs_outro_1080x1920.mp4` is referenced in [`backend/app/workers/video_generator.py`](file:///D:/Agents/CrazzyDocs/backend/app/workers/video_generator.py) as the mandatory outro stitched onto every generated short.
   - `media/source/branding/Compressed-Youtube-Banner.png` is used by channel configuration and setup scripts.
   - **Recommendation:** Mandatory **`KEEP`**.

2. **Narration & TTS Style Templates:**
   - Files in `media/subtitles/` (`chalk_talk_narr.ass`, `creator_motion_narr.ass`, `pop_info_narr.ass`, `data_story_narr.ass`) and audio benchmarks in `media/audio/` are used as styling templates by the ASS subtitle generation and EdgeTTS voice services.
   - `media/source/reusable/audio_samples/` (`sample_excited.mp3`, `sample_neutral.mp3`, `sample_suspenseful.mp3`) are used by voice validation tests.
   - **Recommendation:** Mandatory **`KEEP`**.

3. **Database Ledger Paths:**
   - Active ledger Record #32 references:
     - `video_path`: `media/productions/prod_20261110_vesna_vulovic/video/showcase_vesna_vulovic_verified.mp4`
     - `thumbnail_path`: `media/productions/prod_20261110_vesna_vulovic/thumbnails/thumb_vesna_vulovic_verified.png`
   - Both files are physically present, valid, and confirmed intact.

4. **Quarantine Directory:**
   - `media/quarantine/` contains files isolated in prior stages. No active code path reads from `media/quarantine/archived_files/`.
   - **Recommendation:** **`SAFE_TO_ARCHIVE`**.

---

## 8. Proposed Cleanup Manifest & Candidate Summary

A complete machine-readable manifest has been compiled at:
[`docs/STAGE_3_21D_CLEANUP_CANDIDATES.json`](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21D_CLEANUP_CANDIDATES.json)

### Proposed Actions Summary:

| Proposed Action | File Count | Total Size (MB) | Policy / Execution Rule |
| :--- | :---: | :---: | :--- |
| **KEEP** | **64** | **32.01 MB** | **Do not touch.** Retained permanently in working repository. Includes official branding, outro, Vesna Vulovic canonical assets, reusable audio samples, and subtitle style templates. |
| **SAFE_TO_ARCHIVE** | **225** | **277.84 MB** | **Eligible for external archival compression (ZIP/tarball)** before removal from working tree. Includes quarantined assets (92.96 MB), test verification runs (102.27 MB), audit sheets (13.57 MB), and historical research images (50.15 MB). |
| **POSSIBLY_DISPOSABLE** | **35** | **31.79 MB** | **Eligible for deduplication/removal after operator authorization.** Consists entirely of exact SHA-256 byte duplicates where a canonical copy is already retained in `KEEP` or `SAFE_TO_ARCHIVE`, plus developmental stick animation prototypes. |
| **NEEDS_OPERATOR_REVIEW**| **0** | **0.00 MB** | Zero unresolved files. All 324 files have established lineage and classification. |

---

## 9. Estimated Recoverable Storage

| Cleanup Scenario | Scope | Files Affected | Recoverable Storage |
| :--- | :--- | :---: | :---: |
| **Phase 1: Pure Deduplication** | Delete identical duplicate copies only (preserving all unique assets) | 33 | **30.17 MB** |
| **Phase 2: Quarantine Purge** | Move or purge pre-isolated quarantine archive | 69 | **92.96 MB** |
| **Phase 3: Test Run Archival** | Archive and offload test job directories (`test_*`, `prod_stage_3_16`) | 64 | **102.27 MB** |
| **Phase 4: Full Safe Archival** | Move all `SAFE_TO_ARCHIVE` + `POSSIBLY_DISPOSABLE` to external cold storage | 260 | **309.63 MB** |

**Net Result:** The working tree media footprint can be reduced from **341.64 MB down to ~32.01 MB** without losing a single unique asset, manifest, production video, or test report.

---

## 10. Risks & Safety Guardrails for Future Cleanup

When operator authorization is provided for physical cleanup in a subsequent stage:

1. **Outro & Branding Protection:** `media/source/branding/*` must be strictly excluded from any cleanup regex or file filter.
2. **Canonical Production Locking:** `media/productions/prod_20261110_vesna_vulovic/` must remain read-only.
3. **Zip Archival Prior to Purge:** No files should be permanently deleted (`rm`/`Remove-Item`) without first creating a consolidated cold-storage archive (e.g. `media_archive_pre_stage_3_21e.zip`).
4. **Publishing Ledger Protection:** Database records in `publishing_ledger` and `publishing_ledger_archive` must not have file paths broken or invalidated.

---

## 11. Final Verification & Stop Condition

All verification criteria for Stage 3.21D have been met:
- [x] Zero physical files deleted or moved in `media/`.
- [x] Canonical root remains `D:\Agents\CrazzyDocs\`.
- [x] `CrazzyDocs-Production-Scheduler` remains `Disabled`.
- [x] `CrazzyDocs-Health-Dashboard` remains `Running`.
- [x] Active publishing ledger contains strictly 1 record (`4DCas1e4vHI`, private).
- [x] Archive contains 253 records.
- [x] Official YouTube icon, outro, and production assets verified intact.
- [x] 19 automated tests passed.
- [x] Manifest [`STAGE_3_21D_CLEANUP_CANDIDATES.json`](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21D_CLEANUP_CANDIDATES.json) compiled.

**STOP CONDITION MET:** Awaiting operator review and authorization before executing any physical cleanup or file relocation.
