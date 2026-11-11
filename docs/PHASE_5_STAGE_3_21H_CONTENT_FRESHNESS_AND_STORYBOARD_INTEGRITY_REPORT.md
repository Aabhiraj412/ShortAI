# STAGE 3.21H — CONTENT FRESHNESS AND STORYBOARD INTEGRITY RESET REPORT

**Project:** CrazzyDocs Autonomous Production Studio  
**Canonical Root:** `D:\Agents\CrazzyDocs\`  
**Execution Date:** 2026-11-11  
**Author:** Antigravity Autonomous Engineering System  

---

## 1. Executive Summary

Stage 3.21H investigated and eliminated systemic root causes behind repeated topic selection, hardcoded historical scripts, repeated storyboards, and cross-topic video frame reuse. The content discovery, script generation, storyboard construction, video rendering, and content integrity gate subsystems have been refactored so that every new production independently discovers an eligible fresh topic, builds a unique 5-stage script and storyboard with distinct cryptographic fingerprints, renders fresh visuals exclusively inside isolated production directories, and detects reused frame sequences before entering the publishing queue.

All 16 test-generated pollution records in `content_ledger` and 6 obsolete files (including `thumbnails/output/stick_sample_thumb.png`) were permanently removed following an online SQLite database backup.

---

## 2. Root Causes of Repeated Content

Forensic investigation across topic discovery, orchestration, rendering, and database persistence uncovered four compounding failure modes:

1. **Binary Hardcoded Branch in Master Orchestrator (`crazzydocs_master_orchestrator.py`):**  
   Lines 243–360 contained a hardcoded `if is_vesna: ... else: ...` branch. Any selected topic that did not match Vesna Vulović (including science, economics, or unrelated historical subjects) silently routed into the `else:` branch, which hardcoded **Tsutomu Yamaguchi** narration (`stage_3_4_yamaguchi_voice.mp3`), `YAMAGUCHI_BEAT_CONFIGS`, and executed `shutil.copy2` of `showcase_stage_3_4_tsutomu_yamaguchi.mp4` / `showcase_cycle_4_20261110_1800.mp4` directly into `final_video_path`. This directly caused unrelated topics (such as attention-span stories) to render Hiroshima/Nagasaki explosion frames.
2. **Pre-Rendered Video File Copying:**  
   Instead of dynamically rendering frames for the current topic, Stage 9 executed `shutil.copy2(source_video, final_video_path)` whenever a prior showcase file existed on disk.
3. **Bypassed Duplicate Filtering & Ledger Overrides:**  
   In `crazzydocs_master_orchestrator.py`, passing `force_topic_id="cand_03_vesna_fall"` (used by recovery and integration tests) set `filter_duplicates = False`, completely bypassing topic diversity gates. Furthermore, Stage 12 called `self.ledger.record_content(rec, allow_override=True)` regardless of execution context (`TEST` vs `PRODUCTION`), computing the fingerprint on `selected_topic.summary` rather than the actual generated script.
4. **Test Ledger Pollution & Missing Cooldown Policy:**  
   `TopicLedger` held 25 rows in `content_ledger`, 16 of which were `topic_test_*` entries created during tests. Additionally, `TopicLedger` lacked a configurable production cooldown window and normalized numeric handling (`33,000` vs `33000`).

---

## 3. Topic Discovery Architecture & Freshness Engine

### Topic Discovery Source Inventory
- **Curated Candidate Pool (`backend/app/content/curiosity_topic_engine.py`):** 10 verified, multi-sourced documentary topics across 5 categories (`CURATED_CURIOSITY_CANDIDATES`).
- **Content Memory (`backend/app/content/topic_ledger.py`):** SQLite table `content_ledger` and synchronized mirror `backend/app/content/topic_history.json`.
- **Dynamic Script Generator (`backend/app/content/script_generator.py`):** 5-stage narrative synthesizer (`Hook`, `Context`, `Main Explanation`, `Payoff`, `Closing`).

### Topic Freshness Engine Upgrades
1. **Title Normalization (`normalize_topic_title`):** Strips punctuation without injecting spaces inside comma-separated numbers (preserving `33,000` as `33000`), lowercases, and normalizes whitespace.
2. **Configurable Cooldown Policy (`cooldown_days: int = 30`):** Evaluates `ContentRecord.created_at` against current UTC time. Topics matching normalized titles or overlapping entities/keywords within 30 days are strictly rejected with timestamped cooldown reasons.
3. **Multi-Dimensional Similarity & Entity Collision:**
   - **Exact Script Fingerprint Match:** SHA-256 comparison of normalized spoken script text.
   - **Key-Entity Jaccard Overlap:** Threshold lowered to `0.40` to reject alternate retellings of the same historical event.
   - **Title & Hook Word-Level Overlap:** Stopword-filtered Jaccard overlap (`threshold=0.45`).
4. **Fail-Closed Policy:** If all candidate topics fail cooldown/diversity checks or if research providers return zero eligible candidates, `CuriosityTopicEngine.select_best_topic()` raises `RuntimeError("No fresh topics available; stopping safely without old-topic fallback.")`. Silent fallback to hardcoded showcase topics is eliminated.
5. **Custom Hook Synthesis:** Expanded `generate_five_hooks` across all curated candidates (Demon Core, Midgley, Pepsi Submarines, Mary Celeste, Roy Sullivan, Dancing Plague, Mizuho Typo, Kola Superdeep Borehole).

---

## 4. Historical Data Dependencies & Test Isolation

| Data Category | Storage / Artifact | Dependency & Isolation Design |
|---|---|---|
| **Operational History** | `publishing_ledger`, `publishing_ledger_archive` (253 historical rows), `persistent_jobs`, `persistent_notifications` | Strictly preserved for operational audit. Decoupled from topic discovery (`CuriosityTopicEngine` queries `TopicLedger`, never `publishing_ledger_archive`). |
| **Content Memory** | `content_ledger` (9 genuine rows), `topic_history.json` | Cleaned of 16 `topic_test_*` records. Protected against test writes via `CRAZZYDOCS_TEST_MODE=1` and `execution_context` gating. |
| **Test Fixtures** | `backend/tests/conftest.py`, `test_stage_3_18_recovery_matrix.py`, `test_stage_3_21h_content_freshness.py` | Replaced references to deleted `prod_20261110_vesna_vulovic` media with isolated synthetic files created and torn down inside `_test_synthetic_recovery` and `tmp_path`. |

---

## 5. Architectural Changes Made

1. **`backend/app/content/topic_ledger.py`:**
   - Added `normalize_topic_title()` and `compute_storyboard_fingerprint()`.
   - Added 30-day `cooldown_days` evaluation inside `check_content_collision()`.
   - Enforced test isolation inside `record_content()` (`not allow_override and (is_ctx_test or is_env_test)`).
2. **`backend/app/content/curiosity_topic_engine.py`:**
   - Wired `cooldown_days=30` into `score_candidate()`.
   - Implemented explicit fail-closed `RuntimeError` when zero topics pass gates.
   - Added tailored 5-direction hook matrices for all curated categories.
3. **`backend/app/orchestration/crazzydocs_master_orchestrator.py`:**
   - Removed the hardcoded binary `if is_vesna: ... else: ...` script, voice, and video copying blocks.
   - Implemented `_synthesize_topic_script()` and `_build_topic_visual_beats()` for dynamic, topic-matched 5-stage script and storyboard synthesis.
   - Recorded genuine `script_fingerprint` and `storyboard_fingerprint` into `ProductionWorkspace` (`production_manifest.json`).
   - Replaced `allow_override=True` with `allow_override=False, execution_context=str(execution_context)` in ledger recording.
4. **`backend/app/orchestration/production_workspace.py`:**
   - Added `script_fingerprint` and `storyboard_fingerprint` tracking to `production_manifest.json`.
5. **`backend/app/content/content_integrity_gate.py`:**
   - Added **CHECK_L_FRAME_SEQUENCE_INTEGRITY**: Extracts sample frames via FFmpeg across timestamps (`5s, 15s, 25s, 35s, 45s`) and compares SHA-256 digests against known historical frame hashes to reject reused footage in unrelated stories.
   - Added **CHECK_M_ASSET_OWNERSHIP**: Verifies visual assets in manifests belong strictly to the active production workspace or approved branding (`media/source/branding/`).
6. **`backend/tests/conftest.py` & `backend/tests/test_stage_3_18_recovery_matrix.py`:**
   - Enabled `CRAZZYDOCS_TEST_MODE=1` globally in `conftest.py`.
   - Updated recovery matrix tests to use isolated synthetic media fixtures instead of historical deleted production directories.

---

## 6. Exact Deleted Data Inventory

**Pre-Cleanup Database Backup:**  
`backend/backups/studio_backup_pre_stage_3_21h_20261111_051134.db` (6,483,968 bytes, SHA-256: `bd73eaf06f330970d57c2a998377c49903eb226f9791fbfea4e367b47204b740`)

### A. Deleted Database Rows (`content_ledger` & `topic_history.json`)
Removed **16 test rows** (`topic_test_*`), leaving **9 genuine historical/production rows**:
- `topic_test_b_crash_thumbnail`
- `topic_test_scen_5_post_render`
- `topic_test_scen_6_post_qc`
- `topic_test_scen_11_corrupt_cp`
- `topic_test_auto_job_001`
- `topic_test_interrupted_render_job`
- `topic_test_interrupted_tts_job`
- `topic_test_b_crash_topic_discovery`
- `topic_test_b_crash_research`
- `topic_test_b_crash_script_development`
- `topic_test_b_crash_story_development`
- `topic_test_b_crash_voiceover`
- `topic_test_b_crash_visual_assets`
- `topic_test_b_crash_rendering`
- `topic_test_checkpoint_job_001`
- `topic_test_vesna_integrity_run`

### B. Deleted Obsolete Files (6 files, 131,793 bytes reclaimed)
- `thumbnails/output/stick_sample_thumb.png` (85,108 bytes — remaining stick-figure asset)
- `logs/ffmpeg_stick_render.log` (0 bytes)
- `logs/test_publishing.db` (20,480 bytes)
- `logs/test_stage_3_12_publishing.db` (20,480 bytes)
- `logs/test_notifications.jsonl` (528 bytes)
- `logs/_test_notif_dedup.jsonl` (5,197 bytes)

**Preserved Assets:**
- `media/source/branding/` (4 authoritative files, 3.88 MB)
- `publishing_ledger_archive` (253 historical records)

---

## 7. Verification & Test Results

### A. Stage 3.21H Freshness & Integrity Suite (`test_stage_3_21h_content_freshness.py`)
**Result:** **10/10 PASSED**
- `test_requirement_a_cooldown_rejection`: PASSED (30-day cooldown rejection verified; >30 days eligible)
- `test_requirement_b_semantic_equivalence_rejection`: PASSED (alternate wording of Vesna Vulović rejected)
- `test_requirement_c_fresh_unrelated_topic_selected`: PASSED (Demon Core selected cleanly after Vesna/Yamaguchi exclusions)
- `test_requirement_d_unavailable_provider_no_fallback`: PASSED (`RuntimeError` fail-closed verified)
- `test_requirement_e_different_script_fingerprints`: PASSED (64-char SHA-256 script hashes distinct)
- `test_requirement_f_different_storyboard_fingerprints`: PASSED (64-char SHA-256 storyboard hashes distinct)
- `test_requirement_g_storyboard_unrelated_visuals_rejection`: PASSED (`CHECK_C_ASSET_TOPIC_MAPPING` rejected Hiroshima blast in Attention Span story)
- `test_requirement_h_renderer_rejects_mismatched_manifest`: PASSED (`CHECK_M_ASSET_OWNERSHIP` rejected foreign production directory asset)
- `test_requirement_i_repeated_frame_sequences_detected`: PASSED (`CHECK_L_FRAME_SEQUENCE_INTEGRITY` validated frame sampling gate)
- `test_requirement_j_test_jobs_cannot_pollute_production_ledger`: PASSED (verified `execution_context='test'` and `CRAZZYDOCS_TEST_MODE=1` skip ledger writes)

### B. Full Core Verification Suite
Executed after all final edits across core subsystems (`test_stage_3_21h_content_freshness.py`, `test_stage_3_21g_isolation_and_idempotency.py`, `test_stage_3_21c_dashboard_accuracy.py`, `test_stage_3_10_resilience.py`, `test_stage_3_13a_content_integrity.py`, `test_stage_3_18_recovery_matrix.py`, `test_content_intelligence.py`, `test_creative_identity_policy.py`, `test_production_workspace.py`):
- **Total Tests Executed:** 89
- **Passed:** 89 (100%)
- **Failed:** 0

### C. Secret & Security Scan
- `stage_3_17_secret_scanner.py`: **PASSED** — Zero secrets, tokens, private databases, or generated media detected in tracked/untracked workspace files.

---

## 8. Remaining Risks

1. **Curated Topic Pool Exhaustion:** With 30-day cooldown active across 10 curated candidates and 2 recently used (`Yamaguchi`, `Vesna`), 8 fresh candidates remain (`Demon Core`, `Thomas Midgley Jr.`, `Pepsi Navy`, `Mary Celeste`, `Roy Sullivan`, `Dancing Plague`, `Mizuho Typo`, `Kola Borehole`). Live automated topic research providers (Wikipedia / News / Trends) should be connected in a future stage to expand candidate supply continuously beyond the curated pool.
2. **Perceptual Frame Hashing:** `CHECK_L_FRAME_SEQUENCE_INTEGRITY` currently validates exact SHA-256 hashes of extracted frames. Adding perceptual hashing (pHash/dHash) will further harden detection against slight re-encodes or brightness shifts.

---

## 9. Operational Invariants Status

| Invariant / Component | Status | Verification |
|---|---|---|
| `CrazzyDocs-Production-Scheduler` Task | `Disabled` | Verified via `Get-ScheduledTask` |
| Scheduler Daemon | `STOPPED` | Zero autonomous scheduler workers running |
| Health Dashboard | `RUNNING` | Port 8000 active (PID 15832) |
| Active Schedule Profile | `FOUR_CYCLES_DAILY` | Preserved (`SINGLE_DAILY_2300` inactive) |
| Live YouTube Uploads / Queue Dispatch | `0` | Suppressed in Stage 3.21H |
