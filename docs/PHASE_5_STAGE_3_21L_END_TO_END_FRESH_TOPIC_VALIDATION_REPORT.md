# PHASE 5 — STAGE 3.21L: CONTROLLED END-TO-END FRESH TOPIC PRODUCTION VALIDATION REPORT

- **Stage:** `3.21L` — Controlled End-to-End Fresh Topic Production Validation
- **Completed Run ID:** `val_3_21l_20261111_135230`
- **Execution Context:** `VALIDATION` (`ExecutionContext.VALIDATION`, `use_live_pipeline=True`)
- **Project Root:** `D:\Agents\CrazzyDocs`
- **Base Git Commit:** `main` @ `89d08c0` (Stage 3.21K)
- **Working Tree State:** Uncommitted targeted defect fixes & Stage 3.21L validation artifacts awaiting operator review (`backend/app/services/production_observability.py`, `backend/app/rendering/documentary_storyboard_builder.py`, `backend/scripts/run_stage_3_21l_validation.py`, `docs/STAGE_3_21L_PREFLIGHT_20261111_133859.md`, `docs/PHASE_5_STAGE_3_21L_END_TO_END_FRESH_TOPIC_VALIDATION_REPORT.md`)
- **Pre-Flight Backup:** `backend/backups/studio_backup_pre_stage_3_21l_20261111_133859.db` (`6,483,968` bytes, `PRAGMA integrity_check = ok`, SHA-256 `c9944dc550466b8b4a9ba268884cfd57e05e97f5316fe364f74f64b4561efbda`)

---

## 1. Executive Summary

Stage 3.21L executed a controlled live end-to-end `VALIDATION` production (`val_3_21l_20261111_135230`) under full operational safety isolation. During execution, live stress conditions uncovered and resolved two genuine runtime defects before completing one full end-to-end production for the fresh topic **"History of Antarctica"**:

- **Live Discovery & Freshness:** Selected **"History of Antarctica"** from live source `wikipedia_search:extreme` (`final_score: 9.44`, `semantic_similarity: 0.02`, `image_candidates: 14`, `extract_chars: 1210`, `factual_bullets: 8`), cleanly excluding prior validation topics (*Land mines in the Falkland Islands*, *Jensen Huang*, *Black Monday (1987)*, *John Bingham, 7th Earl of Lucan*, and *UPS Airlines*).
- **Script & Audio Calibration:** Generated a `120`-word, 5-stage documentary script and synthesized Edge Neural TTS voiceover calibrated cleanly to `49.20s` (+ `2.00s` CrazzyDocs icon-only outro = **`51.20s`** final MP4).
- **Visual Diversity & Asset Provenance:** Built a `20`-beat storyboard with **`20 / 20` distinct visual assets** (`max_reuse_observed: 1`, zero image reuse): `10` unique Wikimedia Commons historical portraits/maps/expedition plates, `4` generated analytical illustrations, and `6` bespoke 30 FPS explainer animations (`animated_map`, `animated_diagram`, `animated_infographic`, `motion_graphic`, `typography_sequence`).
- **Render, Thumbnail, QC & Integrity:** Rendered `1080x1920 @ 30 FPS` (`h264`/`aac`, `10.26 MB`), scored and rendered high-contrast topic thumbnail (`thumb_val_3_21l_20261111_135230.png`, `winner_concept: "Quantified Topic Fact"`, `winner_composite_score: 9.35`), passed all `16` Quality Control checks and all `14` Content Integrity checks (`CHECK_A`–`CHECK_N`).
- **Canonical Persistence & Live Dashboard:** Verified `34 / 34` manifest artifacts with `0` missing and `0` SHA-256 mismatches both on disk and via `/api/dashboard/productions/val_3_21l_20261111_135230/manifest`, streamed video/thumbnail over HTTP (`206 Partial Content` / `200 OK`), observed all live stage transitions (`VALIDATION ACTIVE` / `val_worker=RUNNING`) on the running Uvicorn dashboard (`PID 17740`), kept `logs/dashboard_errors.log` at `0` lines and `logs/dashboard_access.log` at `27` lines.
- **Database, Scheduler & Publishing Isolation:** Zero changes to SQLite production tables (`content_ledger=9`, `publishing_ledger=0`, `publishing_ledger_archive=253`, `persistent_jobs=4`, `media_assets=2`), Windows task `CrazzyDocs-Production-Scheduler` remained `Disabled`, `scheduler_daemon` remained `STOPPED`, and zero emails or YouTube uploads were triggered.

---

## 2. Pre-Flight State & Validation Failures Fixed

Pre-flight state was captured in [STAGE_3_21L_PREFLIGHT_20261111_133859.md](file:///D:/Agents/CrazzyDocs/docs/STAGE_3_21L_PREFLIGHT_20261111_133859.md). Two clean validation failures during Stage 3.21L surfaced direct runtime defects that were fixed minimally without restarting the running dashboard daemon (`PID 17740`):

1. **Windows Atomic Progress Store Contention (`val_3_21l_20261111_134222` — "John Bingham, 7th Earl of Lucan"):**
   - **Failure:** Failed cleanly at `research` (`outcome="rejected"`) with `PermissionError: [WinError 5] Access is denied` when `os.replace(tmp_path, path)` collided with the running dashboard reading `backend/data/production_progress_store.json`.
   - **Fix:** Added bounded retry/backoff and safe fallback handling to [`ProductionObservabilityStore._load_file`](file:///D:/Agents/CrazzyDocs/backend/app/services/production_observability.py#L120-L138) and [`ProductionObservabilityStore._save_file`](file:///D:/Agents/CrazzyDocs/backend/app/services/production_observability.py#L140-L176) in [production_observability.py](file:///D:/Agents/CrazzyDocs/backend/app/services/production_observability.py).
2. **Overshoot Above 58–60s Shorts Window (`val_3_21l_20261111_134455` — "UPS Airlines"):**
   - **Failure:** Failed cleanly at `quality_control` (`outcome="rejected"`), after rendering a `60.57s` video (`58.562s` Edge TTS narration + `2.0s` outro), because [`synthesize_and_probe_narration`](file:///D:/Agents/CrazzyDocs/backend/app/rendering/documentary_storyboard_builder.py#L178-L283) padded short tracks (`< 48.8s`) but had no upper-bound calibration when numeric/complex prose spoke slower than expected.
   - **Fix:** Tightened [`compose_structured_script`](file:///D:/Agents/CrazzyDocs/backend/app/rendering/documentary_storyboard_builder.py#L89-L175) trim/pad thresholds (`104–124` words) and added symmetric FFmpeg `atempo` calibration for `total_voice_dur > 55.6s` in [documentary_storyboard_builder.py](file:///D:/Agents/CrazzyDocs/backend/app/rendering/documentary_storyboard_builder.py).
3. **Post-Completion Helper Signature Fix (`run_stage_3_21l_validation.py`):**
   - `val_3_21l_20261111_135230` completed `CrazzyDocsMasterOrchestrator.execute_production_pipeline()` with `status="COMPLETED"`, after which the wrapper script hit `TypeError` calling `ProductionWorkspace(job_id)` without `(topic_id, topic_title)`. Fixed the wrapper constructor call in [run_stage_3_21l_validation.py](file:///D:/Agents/CrazzyDocs/backend/scripts/run_stage_3_21l_validation.py) and ran read-only artifact/frame/endpoint/SHA-256 verification directly against `val_3_21l_20261111_135230` without executing a second pipeline.

---

## 3. Topic Discovery, Freshness & Research Summary

- **Selected Topic:** `History of Antarctica` (`topic_id: live_history_of_antarctica`, category: `Science & Technology`)
- **Discovery Source:** `wikipedia_search:extreme` (discovered `2026-11-11T08:22:40Z`, source revision `2026-11-06T16:39:03Z`)
- **Freshness & Ranking Metrics (from `validation_topic_history.json`):**
  - `semantic_similarity`: `0.02`
  - `novelty_score`: `9.8`
  - `factual_confidence`: `9.6`
  - `curiosity_score`: `9.2`
  - `composite_score` / `final_score`: `9.44` (`final_rank: 1`)
  - `topic_fingerprint`: `3ce4d02ae286d2dd9d17c5c0a7c0baa60aba6525b79de3c3e49a45dc69f1399a`
  - `research_fingerprint`: `daaa0e3b929158ff8f9b5ae43098a98241c0e46ccd58c73a41ef39e9261efe9b`
- **Research Availability & Sources:** `extract_chars=1210`, `factual_bullets=8`, `image_candidates=14`, `2` authoritative references:
  - `https://en.wikipedia.org/wiki/History_of_Antarctica`
  - `https://en.wikipedia.org/api/rest_v1/page/summary/History_of_Antarctica`
- **Artifact:** [research_val_3_21l_20261111_135230.json](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/research/research_val_3_21l_20261111_135230.json)

---

## 4. Script, Storyboard & Visual Asset Provenance (`20/20` Distinct Assets)

- **Script ([script_val_3_21l_20261111_135230.json](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/scripts/script_val_3_21l_20261111_135230.json)):** `120` words (`script_fingerprint: 19180b152a96bcb5b4fb29246ed35e7afca2f20d265f207d1ad5b9a4b721bfa6`) structured as:
  - `HOOK` (`27` words), `CONTEXT` (`22` words), `ESCALATION` (`35` words), `REVELATION` (`15` words), `PAYOFF` (`21` words).
- **Storyboard ([storyboard_val_3_21l_20261111_135230.json](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/storyboards/storyboard_val_3_21l_20261111_135230.json)):** `20` beats spanning `49.20s` narration (`storyboard_fingerprint: 46f23e71e97be4295d7b5867d56e83224e59f16888e66cac62b91173edadea92`).
- **Visual Diversity Audit:** `is_valid=true`, `distinct_assets=20`, `unique_images=14`, `unique_animations=6`, **`max_reuse_observed=1`**.
- **Visual Relevance Mapping ([visual_relevance_report_val_3_21l_20261111_135230.json](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/reports/visual_relevance_report_val_3_21l_20261111_135230.json)):** `20 / 20` beats mapped to verified existing files with `20` distinct SHA-256 hashes:

| Beat | Stage | Duration | Visual Type | Asset Filename |
|---|---|---|---|---|
| 01 | `HOOK` | `2.53s` | `historical_photo` | `Wikimedia_Commons_Admiral_Faddey_Faddeyevich_Belli_fd8c2f35c8.jpg` |
| 02 | `HOOK` | `2.53s` | `generated_image` | `gen_live_history_of_antarctica_b02_early_western_theories_o.png` |
| 03 | `HOOK` | `2.53s` | `historical_photo` | `Wikimedia_Commons_Adrien_de_Gerlache_06bebfc325.jpg` |
| 04 | `HOOK` | `2.53s` | `animated_map` | `anim_live_history_of_antarctica_b04_animated_map.mp4` |
| 05 | `CONTEXT` | `2.20s` | `historical_photo` | `Wikimedia_Commons_Camp_and_Hut_Antarctica_Southern_a2e8135a37.jpg` |
| 06 | `CONTEXT` | `2.20s` | `generated_image` | `gen_live_history_of_antarctica_b06_the_opposite_of_the_arct.png` |
| 07 | `CONTEXT` | `2.20s` | `historical_photo` | `Wikimedia_Commons_Cook-1777_cf877ed169.jpg` |
| 08 | `CONTEXT` | `2.20s` | `animated_map` | `anim_live_history_of_antarctica_b08_animated_map.mp4` |
| 09 | `ESCALATION` | `3.25s` | `historical_photo` | `Wikimedia_Commons_Freti_Magellanici_ac_novi_freti__71c4f3df0f.jpg` |
| 10 | `ESCALATION` | `3.26s` | `generated_image` | `gen_live_history_of_antarctica_b10_and_cape_horn_in_the.png` |
| 11 | `ESCALATION` | `3.25s` | `historical_photo` | `Wikimedia_Commons_Geography_world_map_eecb4cece9.jpg` |
| 12 | `ESCALATION` | `3.25s` | `animated_diagram` | `anim_live_history_of_antarctica_b12_animated_diagram.mp4` |
| 13 | `REVELATION` | `1.75s` | `historical_photo` | `Wikimedia_Commons_div_class_fn_Brig_Jane_amp_Cutte_601166983c.jpg` |
| 14 | `REVELATION` | `1.75s` | `generated_image` | `gen_live_history_of_antarctica_b14_cook_and_his.png` |
| 15 | `REVELATION` | `1.75s` | `historical_photo` | `Wikimedia_Commons_div_class_fn_div_style_font-weig_f361e86872.jpg` |
| 16 | `REVELATION` | `1.75s` | `animated_infographic` | `anim_live_history_of_antarctica_b16_animated_infographic.mp4` |
| 17 | `PAYOFF` | `2.57s` | `historical_photo` | `Wikimedia_Commons_Polar_Regions_exploration_1911_e9c51cb1cf.png` |
| 18 | `PAYOFF` | `2.57s` | `motion_graphic` | `anim_live_history_of_antarctica_b18_motion_graphic.mp4` |
| 19 | `PAYOFF` | `2.57s` | `historical_photo` | `Wikimedia_Commons_SirJMurrayatwork_cc0dd9b3e8.jpg` |
| 20 | `PAYOFF` | `2.57s` | `typography_sequence` | `anim_live_history_of_antarctica_b20_typography_sequence.mp4` |

- **Attribution ([attribution_manifest_val_3_21l_20261111_135230.json](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/metadata/attribution_manifest_val_3_21l_20261111_135230.json)):** `10` Wikimedia Commons historical works credited with URL and license metadata.

---

## 5. Audio, Video Render, Thumbnail, QC & Visual Inspection

- **Master Video ([showcase_val_3_21l_20261111_135230.mp4](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/video/showcase_val_3_21l_20261111_135230.mp4)):**
  - `width=1080`, `height=1920`, `fps=30.0`, `v_codec=h264`, `a_codec=aac`
  - `duration=51.20s` (`49.20s` `narrative_short.mp4` / `1,476` frames rendered in `225.51s` + exact `2.00s` CrazzyDocs icon outro)
  - Size: `10,763,233` bytes (`10.26 MB`), SHA-256: `8b223f7d20d8a06d8cb0c67f8e0b82c7f5df8aabc3ec14ce61e40f266c2829aa`
- **Voiceover ([voiceover_val_3_21l_20261111_135230.mp3](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/audio/voiceover_val_3_21l_20261111_135230.mp3)):** `1,181,996` bytes, SHA-256 `1f4c8769f59d9922f73db959631e3f9fd2e7981e8f0dc56feddd2a649ca1cb88`.
- **Thumbnail ([thumb_val_3_21l_20261111_135230.png](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/thumbnails/thumb_val_3_21l_20261111_135230.png)):**
  - Size: `927,659` bytes, SHA-256: `a0b5afc511afe7c3fef8df8033fed93fc5264214a7161413ffeb073a2f0fe4a8`
  - Selected out of `3` candidates (`cand_03_topic_fact`, `"HISTORY ANTARCTICA 59"`, `winner_composite_score=9.35`, `safe_zone_compliance.passed=true`, `excluded_headline_penalty=false`).
- **QC & 14-Point Integrity Gate:**
  - `qc_report_val_3_21l_20261111_135230.json`: `passed=true`, `errors=[]`, all `16` checks `PASS`.
  - `integrity_report_val_3_21l_20261111_135230.json`: `is_valid=true`, `rejection_reasons=[]`, all `14` critical checks (`CHECK_A`..`CHECK_N`) `passed=true`.
- **Direct Visual Frame & Thumbnail Inspection (inspected via `read_file`):**
  - Inspected [contact_sheet_3_21l.png](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/reports/contact_sheet_3_21l.png), [frame_01_1.02s.png](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/reports/frames/frame_01_1.02s.png), [frame_10_50.40s.png](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/reports/frames/frame_10_50.40s.png), and [thumb_val_3_21l_20261111_135230.png](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/thumbnails/thumb_val_3_21l_20261111_135230.png):
  - Beat frames display crisp `1080x1920` documentary framing with synchronized beat progress bar (`1/20`..`20/20`), stage badges, high-contrast callout banners, readable bottom captions inside safe margins, authentic Wikimedia maps/engravings paired with vector maps/diagrams/motion graphics, zero black frames, and zero stick figures.
  - `frame_10_50.40s.png` confirms the clean centered circular CrazzyDocs icon-only outro during the `49.2s–51.2s` outro segment.
  - Mild creative note on `thumb_val_3_21l_20261111_135230.png`: authentic historical engraving of Admiral Bellingshausen with high-contrast `"HISTORY ANTARCTICA 59"` typography in safe zone (`59` derived from `59` metric token in research summary, reads slightly terse without a unit/year label).

---

## 6. Canonical Manifest, Dashboard Observability & HTTP Deliverable Checks

- **Canonical Manifest Verification ([production_manifest.json](file:///D:/Agents/CrazzyDocs/media/productions/val_3_21l_20261111_135230/production_manifest.json)):**
  - `34 / 34` registered artifacts verified directly on disk (`missing=0`, `bad_sha=0`).
  - `/api/dashboard/productions/val_3_21l_20261111_135230/manifest` (`HTTP 200`): `total_artifacts=34`, `missing_count=0`, `sha256_mismatch_count=0`, `all_verified=True`.
- **Live Observability During Execution (PID `17740` on `127.0.0.1:8000`):**
  - Poller captured `op_state=VALIDATION ACTIVE`, `val_worker=RUNNING` transitioning through `topic_discovery (1.2%)` → `narration_generation (27.1%)` → `asset_acquisition (35.0%)` → `image_generation (44.6%)` → `animation_generation (45.4%)` → `video_rendering (66.7%)` → `quality_control (91.7%)`.
  - Event journal (`progress_events_val_3_21l_20261111_135230.jsonl`) recorded `96` persisted events (`PRODUCTION_STARTED` through `PRODUCTION_COMPLETED`).
- **Post-Run Live Dashboard & Deliverable Endpoints:**
  - `/api/dashboard/overview`: `overall_operational_state=IDLE`, `scheduler_state=DISABLED`, `production_worker_state=IDLE`, `validation_worker_state=COMPLETED`.
  - `/api/dashboard/progress?job_id=val_3_21l_20261111_135230`: `overall_status=completed`, `overall_progress=100.0`, `current_stage=finalization`, `execution_context=VALIDATION`.
  - `/api/dashboard/productions/val_3_21l_20261111_135230/video`: `HTTP 206` (`video/mp4`, `bytes_sampled=1024`).
  - `/api/dashboard/productions/val_3_21l_20261111_135230/thumbnail`: `HTTP 206` (`image/png`, `bytes_sampled=1024`).
  - `logs/dashboard_errors.log`: `0` lines; `logs/dashboard_access.log`: `27` lines total.

---

## 7. Regression Tests & Final Isolation Verification

- **Pytest Regression Scope (`backend/tests/test_stage_3_21h_content_freshness.py`, `test_stage_3_21i_validation_pipeline.py`, `test_stage_3_21j_visual_diversity_persistence_observability.py`, `test_stage_3_21k_reconciliation_freshness_storage.py`):**
  - **`35 passed, 0 failed, 0 skipped`** (`2` Pydantic class-config deprecation warnings in `114.79s`).
- **Final SQLite & Scheduler State (verified post-validation and post-pytest):**
  - `content_ledger`: `9` (delta `0`)
  - `publishing_ledger`: `0` (delta `0`)
  - `publishing_ledger_archive`: `253` (delta `0`)
  - `persistent_jobs`: `4` (delta `0`)
  - `media_assets`: `2` (delta `0`)
  - `CrazzyDocs-Production-Scheduler`: `Disabled`
  - `scheduler_daemon`: `STOPPED`

**Stage 3.21L Verdict: PASS** — Ready for operator review. Production scheduler remains `Disabled`, and no commits or pushes were made after base commit `89d08c0`.
