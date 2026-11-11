# PHASE 5 — STAGE 3.21I: CONTROLLED END-TO-END PRODUCTION VALIDATION REPORT

**Project:** CrazzyDocs Autonomous Production Studio (`D:\Agents\CrazzyDocs\`)  
**Baseline Git Commit:** `1a92d7d5c1af3df5a866fc0d69ecf9db4784c8a5` (`main`, Stage 3.21H)  
**Validation Execution ID:** `val_3_21i_20261111_110449`  
**Execution Context:** `ExecutionContext.VALIDATION`  
**Final Verdict:** **PASS**

---

## 1. Executive Summary

Stage 3.21I validated the complete CrazzyDocs autonomous documentary generation pipeline after Stage 3.21H in strict **non-publishing `VALIDATION` isolation**. Without using any pre-rendered showcase videos, hardcoded fallback stories, or production queue writes, the pipeline:

1. Independently discovered a genuinely fresh topic (`Land mines in the Falkland Islands`) from live Wikimedia `onthisday` sources while excluding all 9 prior `content_ledger` / `topic_history.json` entries.
2. Enriched the dossier from Wikipedia encyclopedic summaries and Wikimedia `/page/media-list/` API metadata, producing a 126-word, 5-stage original factual script with zero repeated sentences across sections.
3. Synthesized a `50.98s` neural voiceover via Edge TTS and acquired 2 topic-verified Wikimedia Commons photographs into an isolated per-run workspace (`media/productions/val_3_21i_20261111_110449/`).
4. Generated 20 visual documentary beats (`1.92s`–`3.39s` pacing) inside Ken Burns safe-zone margins (`x=140..940`), composited with normalized stereo audio ducking and the official `crazzydocs_outro_1080x1920.mp4` outro (`53.20s` total, `1080x1920`, `30.0 FPS`, H.264/AAC, `15,546,697` bytes), plus a `1080x1920` topic-specific thumbnail (`LAND MINES FALKLAND`).
5. Passed both the Quality Control suite (`qc_report_val_3_21i_20261111_110449.json`) and the 13-point Content Integrity Gate (`CHECK_A`–`CHECK_M`).
6. Preserved complete operational isolation: **zero** database mutations (`0` deltas across all 5 tracked SQLite tables), **zero** emails sent (`suppressed_test_mode: true`), **zero** YouTube uploads (`suppressed_validation_execution`), and `CrazzyDocs-Production-Scheduler` kept `Disabled`.

---

## 2. Pre-Flight Safety & Environment Baseline

| Check | Verified Value | Status |
|---|---|---|
| Git Branch & Head | `main` at `1a92d7d5c1af3df5a866fc0d69ecf9db4784c8a5` | VERIFIED |
| Pre-Run SQLite Backup | `backend\backups\studio_backup_pre_stage_3_21i_20261111_103602.db` (`6,483,968` bytes) | VERIFIED |
| Backup Integrity & SHA-256 | `PRAGMA integrity_check` = `ok`; SHA-256 `2489871519aadf26f7fb9d80c4890b221991b14a57d06f80883a65745a3b6e16` | VERIFIED |
| Windows Production Scheduler | `CrazzyDocs-Production-Scheduler` -> `Disabled` | VERIFIED |
| Scheduler Daemon | `STOPPED` | VERIFIED |
| Health Dashboard | `HEALTHY` on `http://localhost:8000` (`pythonw` PID `15832`) | VERIFIED |
| Active Schedule Profile | `FOUR_CYCLES_DAILY` (`SINGLE_DAILY_2300` inactive) | VERIFIED |

---

## 3. Pipeline Bugs Resolved During Stage 3.21I

Iterative inspection of early Stage 3.21I validation runs uncovered several defects in orchestration, compositing, thumbnail text generation, and media relevance filtering. All were fixed before the final single-video validation run (`val_3_21i_20261111_110449`):

1. **Isolated `VALIDATION` Execution Mode (`backend/app/core/execution_context.py`, `topic_ledger.py`, `crazzydocs_master_orchestrator.py`):**
   - Added `ExecutionContext.VALIDATION` (`"VALIDATION"`), blocked by `require_production_context`, ensuring persistent `content_ledger`, `topic_history.json`, `publishing_ledger`, `persistent_jobs`, YouTube uploads, and live SMTP notifications cannot fire during validation.
   - Enforced `filter_dups=True` regardless of `force_topic_id` in both `PRODUCTION` and `VALIDATION` contexts.
2. **Live Discovery & Deduplicated Factual Script Synthesis (`backend/app/content/curiosity_topic_engine.py`, `backend/app/rendering/documentary_storyboard_builder.py`):**
   - Implemented live external candidate discovery (`discover_live_candidates`, `discover_and_rank_live_topics`, `select_best_live_topic`) with fail-closed behavior when live providers return no candidates (no curated fallback).
   - Added Wikimedia `/page/media-list/` Commons enrichment (`enrich_wikipedia_candidate`) and enforced `>= 4` distinct factual sentences on Phys.org RSS candidates so script sections never repeat identical sentences.
3. **FFmpeg Filter Graph & Ken Burns Safe Margins (`backend/app/rendering/engine.py`, `backend/app/rendering/compositing/compositor.py`, `documentary_storyboard_builder.py`):**
   - Fixed FFmpeg argument conflict between `-vf ass=...` and `-filter_complex` during subtitle burn-in.
   - Normalized voice/music streams with `aformat=48000:stereo` before `sidechaincompress` audio ducking and fixed the `default_outro` fallback path to `media/source/branding/crazzydocs_outro_1080x1920.mp4`.
   - Adjusted beat plate framing so HUD/callout elements sit safely inside `x=140..940` during 1.15x Ken Burns zoom/pan.
4. **Media Relevance & Thumbnail Headline Generalization (`backend/app/media/*`, `creative_thumbnail_engine.py`, `content_integrity_gate.py`):**
   - Fixed `RelevanceScorer` so overlap is scored against actual `asset.title`/`source_url` rather than `search_query` keywords, filtering out off-topic space/rocket stock hits (`score < 0.20`).
   - Added Wikimedia Commons MIME filtering (`jpeg|png|webp`), `iiurlwidth=1280`, `fetch_files_by_titles()`, and Wikimedia-compliant `User-Agent` headers.
   - Replaced hardcoded fallback thumbnail headlines (`THE DEMON CORE`, etc.) in `CreativeThumbnailEngine` with dynamic topic-derived headline generation and added stale-headline detection to `CHECK_D_THUMBNAIL`.
5. **Cross-Drive Path & Import Resilience (`production_workspace.py`, `qc_service.py`):**
   - Fixed Windows cross-drive `os.path.relpath` `ValueError` in test environments and dual-mode `probe_video` imports in `qc_service.py`.

*(Prior exploratory validation directories generated while diagnosing the defects above were cleaned up so only `media/productions/val_3_21i_20261111_110449/` remains on disk.)*

---

## 4. Controlled Single-Video Validation Run (`val_3_21i_20261111_110449`)

### 4.1 Live Topic Discovery & Exclusion Proof
- **Selected Topic:** `Land mines in the Falkland Islands` (`live_land_mines_in_the_falkland_islands`)
- **Discovery Source:** `wikipedia_onthisday` (Curiosity Score: `9.2` / `9.20`)
- **Duplicate / Cooldown Exclusion:** Screened against all `9` active records in `content_ledger` (`studio.db`) and `backend/app/content/topic_history.json` (Yamaguchi, Vesna Vulović, 1904 Olympic Marathon, Great Molasses Flood, Moon orbital mechanics, Solar energy cost, Bank purchase, Infant amnesia) with zero overlap.
- **Historical & Encyclopedic Citations:** `research_val_3_21i_20261111_110449.json` contains 6 extracted factual claims and 2 Wikipedia source URLs backing the post-1982 Falklands demining and Ottawa Treaty timeline.

### 4.2 Script & Neural Narration Proof
- **Script (`scripts/script_val_3_21i_20261111_110449.json`):** `126` words structured across 5 distinct sections (`HOOK`, `CONTEXT`, `ESCALATION`, `REVELATION`, `PAYOFF`), with `script_fingerprint` `772a0484f4ab7fecfaa3a93095ad92bf92c678f4dd7dc418a780471e06c8f32f`.
- **Voiceover Audio (`voiceover_val_3_21i_20261111_110449.mp3`):**
  - Engine: Microsoft Edge Neural TTS
  - Probed Duration: `50.978s` (`50.98s`)
  - Size: `1,225,196` bytes
  - SHA-256: `c2af2c726afacbb2c4a42d313e31318881ee55dbadebfd8ba2a088096a1f069b`

### 4.3 Storyboard & Acquired Topic Assets
- **Acquired Openly Licensed Wikimedia Commons Images (`metadata/attribution_manifest_val_3_21i_20261111_110449.json`):**
  1. `Wikimedia_Commons_Falklands-Minefield_4dc3678ad2.jpg` — `1280x872`, `387,781` bytes, Public Domain (author `Apcbg`), SHA-256 `4dc3678ad2075652691d1e3c9113bb4b63db696c76dd171e2810f7d7624c610e`
  2. `Wikimedia_Commons_Minefield_road_sign_-_Falkland_I_b0829f0de0.jpg` — `800x524`, `100,947` bytes, CC BY (author `Christopher Michel`), SHA-256 `b0829f0de0e4c3faf5d67c6a52d45ad375f99bdb2537b6a9ef4738d69e82ddcf`
- **20-Beat Documentary Storyboard (`visual_timeline/storyboard_val_3_21i_20261111_110449.json`):**
  - `storyboard_fingerprint`: `b68b9c0b0998ba3fb78cb18fa7f89cae8e067a23ef26f99e817e6cd1a4c6c4cf`
  - Beats `b01`–`b04` (`HOOK`, `0.000s–9.832s`, `2.458s`/beat)
  - Beats `b05`–`b08` (`CONTEXT`, `9.832s–23.404s`, `3.393s`/beat)
  - Beats `b09`–`b12` (`ESCALATION`, `23.404s–33.208s`, `2.451s`/beat)
  - Beats `b13`–`b16` (`REVELATION`, `33.208s–40.876s`, `1.917s`/beat)
  - Beats `b17`–`b20` (`PAYOFF`, `40.876s–50.978s`, `2.525s`–`2.526s`/beat)
  - Outro (`50.978s–53.20s`): `media/source/branding/crazzydocs_outro_1080x1920.mp4`

### 4.4 Rendered Video, Thumbnail & Manifest Telemetry
- **Video Output:** `D:\Agents\CrazzyDocs\media\productions\val_3_21i_20261111_110449\video\showcase_val_3_21i_20261111_110449.mp4`
  - Resolution & Aspect: `1080x1920` (`9:16`) @ `30.0 FPS`
  - Codecs: `h264` video / `aac` audio
  - Duration: `53.20s` (`1536` main frames + outro)
  - File Size: `15,546,697` bytes (`14.83 MB`)
  - SHA-256: `1b176aea3a60fd16bb9b4d2ebad526bb6a6ae4e446f345e768d7e00d59bb591e`
- **Thumbnail Output:** `D:\Agents\CrazzyDocs\media\productions\val_3_21i_20261111_110449\thumbnails\thumb_val_3_21i_20261111_110449.png`
  - Resolution: `1080x1920`
  - Headline Rendered: `LAND MINES FALKLAND`
  - File Size: `2,636,463` bytes
  - SHA-256: `237ca8ce2a02d65ad875157359061471b20bef6e4764eacdb3cd1ee589ce6877`
- **Manifest Completeness (`production_manifest.json`):** `14` artifacts hashed and registered with zero external showcase/test-path references.
- **Latency (`reports/render_telemetry_val_3_21i_20261111_110449.json` & `validation_run_summary.json`):**
  - Composite Canvas Render Time: `280.95s` (`5.47` aggregate FPS; `8.93`–`11.40 FPS` per beat segment)
  - Total End-to-End Pipeline Wall Time: `345.83s`

---

## 5. Quality Control, Integrity Gate & Visual Inspection

### 5.1 Automated Quality Control (`reports/qc_report_val_3_21i_20261111_110449.json`)
- Overall Result: **`passed: true`** (`errors: []`)
- Verified Checks: `file_size`, `resolution` (`1080x1920`), `aspect_ratio` (`9:16`), `framerate` (`30.0 FPS`), `audio_presence` (`aac`), `duration` (`53.20s, target 50-60s`), `video_codec` (`H.264`), `thumbnail_file`, `title_seo` (`42 chars`), `description_seo`, `tags_seo` (`15 tags`), `category` (`Cat 27`), `copyright_safety`, `black_frames` (`Verified keyframes`) — all **PASS**.

### 5.2 13-Point Content Integrity Gate (`audit/integrity_report_val_3_21i_20261111_110449.json`)
- Overall Result: **`is_valid: true`**
- All 13 checks (`CHECK_A` through `CHECK_M`) returned **`passed: true`**.

### 5.3 Human-Reviewed Frame & Thumbnail Inspection
Inspected `reports/contact_sheet_3_21i.png`, `reports/inspected_frames/frame_01_hook.png` (`1.5s`), `frame_04_escalation.png` (`22.0s`), `frame_08_outro.png` (`52.4s`), and `thumbnails/thumb_val_3_21i_20261111_110449.png`:
- Documentary HUD framing and typography remain sharp and unclipped within safe vertical boundaries during Ken Burns motion.
- All inspected frames display topic-authentic Falkland Islands minefield warning signs and terrain photographs paired with synchronized lower subtitles.
- Outro frame at `52.4s` cleanly transitions to the official CrazzyDocs cartoon-icon emblem card.
- Thumbnail displays readable high-contrast headline text (`LAND MINES FALKLAND`) directly tied to the validated topic.
- **Fairness Observation:** Because Wikimedia search for this specific historical topic yielded 2 verified Commons photographs meeting strict relevance and license checks, the 20 beats varied visual presentation by alternating those 2 photographs across focal photo, stat card, and split-dual layouts with alternating Ken Burns motion rather than 20 distinct source photographs. Adding additional relevant Wikimedia category expansion could increase unique photo variety in future stages.

---

## 6. Post-Validation Isolation, Process & Test Verification

### 6.1 Database, Queue, Scheduler & Workspace State
Verified independently against `backend/studio.db`, `backend/app/content/topic_history.json`, Windows Task Scheduler, `http://localhost:8000/api/dashboard/health`, and OS processes after run completion:

| System Component | Pre-Run | Post-Run | Delta / State |
|---|---|---|---|
| `content_ledger` rows | `9` | `9` | `0` (`Land mines in the Falkland Islands` absent) |
| `publishing_ledger` rows | `0` | `0` | `0` (empty active queue) |
| `publishing_ledger_archive` rows | `253` | `253` | `0` |
| `persistent_jobs` rows | `4` | `4` | `0` (`val_3_21i_*` absent) |
| `media_assets` rows | `2` | `2` | `0` |
| `topic_history.json` entries | `9` | `9` | `0` |
| Emails / Notifications | Suppression enabled | `suppressed_test_mode: true` | `0` sent |
| YouTube Publishing | Disabled in `VALIDATION` | `suppressed_validation_execution` | `0` uploaded |
| `CrazzyDocs-Production-Scheduler` | `Disabled` | `Disabled` | Unchanged |
| Validation Workspaces on Disk | — | `1` (`val_3_21i_20261111_110449`) | Preserved locally |
| Running Python / FFmpeg Processes | Dashboard `pythonw` (`15832`) | Dashboard `pythonw` (`15832`, `31.1 MB`) | `0` orphan/render processes |

### 6.2 Regression & Validation Test Suite (Executed After Final Code Edits)
Executed `D:\Agents\CrazzyDocs\.venv\Scripts\python.exe -m pytest backend\tests\test_stage_3_21i_validation_pipeline.py backend\tests\test_stage_3_21h_content_freshness.py backend\tests\test_stage_3_21g_isolation_and_idempotency.py backend\tests\test_stage_3_8_integration.py -v`:
- `backend/tests/test_stage_3_21i_validation_pipeline.py`: `5 passed`
- `backend/tests/test_stage_3_21h_content_freshness.py`: `10 passed`
- `backend/tests/test_stage_3_21g_isolation_and_idempotency.py`: `7 passed`
- `backend/tests/test_stage_3_8_integration.py`: `4 passed`
- **Total:** **`26 passed, 1 warning in 17.28s`** (zero failures, zero skipped).

### 6.3 Secret & Repository Hygiene Scan
Executed `D:\Agents\CrazzyDocs\.venv\Scripts\python.exe backend\scripts\stage_3_17_secret_scanner.py`:
- **`PASSED: Zero secrets, tokens, private databases, or generated media found.`**

---

## 7. Conclusion

The Stage 3.21H/3.21I content generation, asset relevance, storyboard synthesis, neural narration, video compositing, QC, and isolation guardrails operate cohesively end-to-end. Based on observed test execution, rendered video inspection, manifest/integrity verification, and zero-delta isolation checks, Stage 3.21I passes with the production scheduler safely remaining **`Disabled`**.
