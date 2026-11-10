# Stage 3.19 — Regression Test Suite Reconciliation & Discovery Report

**Project:** CrazzyDocs Shorts Studio  
**Canonical Directory:** `D:\Agents\CrazzyDocs\`  
**Timestamp:** 2026-11-11T01:08:15+05:30  
**Test Framework:** `pytest 9.2.0`, `Python 3.11.9`, `pytest-asyncio 1.4.1`  

---

## 1. Discrepancy Investigation & Root Cause

In Stage 3.18, reports cited two disparate regression counts:
- **122 tests passed** (reported in Stage 3.18 final pre-flight run).
- **104 tests passed** (cited from earlier deployment reports).

### Discovery Findings:
1. **122 Tests:** This number was produced by running a specific subset of **16 test files** selected during Stage 3.18 to verify recovery, deployment, branding, and scheduling invariants.
2. **104 Tests:** This number was produced in Stage 3.17 across an earlier subset of 14 test files before the Stage 3.18 recovery matrix, creative policy, and health dashboard tests were added.
3. **209 Total Discovered Tests (Stage 3.18 baseline):** When pytest was run against the entire `backend/tests/` directory (all 29 test files), 209 tests were collected. 198 passed, but 10 failed and 1 errored due to:
   - **Showcase Asset Purge Mismatch (6 tests):** `test_stage_3_3_1_corrections.py` and `test_stage_3_4_production.py` expected transient showcase assets (Boston Molasses Flood images and Tsutomu Yamaguchi subtitle/thumbnail files) that were intentionally removed during the Stage 3.13B and Stage 3.14 cleanups. Furthermore, `attribution_manifest.json` contained hardcoded `D:\AI Arts\AI\` paths.
   - **Async Fixture Syntax (3 tests):** `test_production_duration_standard.py` lacked `@pytest.mark.asyncio` and had an uninstantiated fixture parameter `script_data`.
   - **Publishing Mode Evolution (2 tests):** `test_stage_3_8_integration.py` and `test_production_duration_standard.py` hardcoded assertions for `"manual_approval"` mode, whereas Stage 3.17 explicitly authorized and activated `"automatic_public"`.

---

## 2. Definitive Test Discovery & Execution (Stage 3.19 Authoritative State)

With the addition of:
- `backend/tests/test_production_dashboard_browser.py` (7 tests)
- `backend/tests/test_stage_3_19_creative_identity_e2e.py` (5 tests)

The authoritative test suite across the entire project is:

| Category | Count | Status |
|---|---|---|
| **Total Test Files in `backend/tests/`** | **31 files** | Discovered |
| **Total Discovered Test Cases** | **221 tests** | Collected |
| **Executed & Passed** | **217 tests** | **PASSED (100% runnable)** |
| **Failures** | **0 tests** | **ZERO FAILURES** |
| **Errors** | **0 tests** | **ZERO ERRORS** |
| **Skipped** | **4 tests** | Explicitly marked `skipif` for purged showcase artifacts |

Execution Duration: **58.83s**

---

## 3. Comprehensive Test File Breakdown

| Test File | Discovered | Passed | Skipped | Failed | Scope |
|---|---|---|---|---|---|
| `test_branding_integrity.py` | 5 | 5 | 0 | 0 | Branding icons, reference CSS, hash lock |
| `test_chalk_talk_boundary_refinement.py` | 1 | 1 | 0 | 0 | Chalk talk layout safe bounds |
| `test_content_intelligence.py` | 14 | 14 | 0 | 0 | Duplicate topics, fact verification, scripts |
| `test_creative_identity_policy.py` | 7 | 7 | 0 | 0 | 4 approved styles, negative prompt injection |
| `test_daily_publishing_summary.py` | 4 | 4 | 0 | 0 | 8 daily metrics, deduplication, offline queue |
| `test_hybrid_engine.py` | 5 | 5 | 0 | 0 | Hybrid 2D/2.5D rendering orchestrator |
| `test_job_manager.py` | 3 | 3 | 0 | 0 | Job queue, checkpoint recovery, idempotency |
| `test_media_acquisition.py` | 15 | 15 | 0 | 0 | CC0, Public Domain licensing verification |
| `test_production_dashboard_browser.py` | 7 | 7 | 0 | 0 | Browser UI, API endpoints, secret protection |
| `test_production_duration_standard.py` | 4 | 4 | 0 | 0 | 50-60s duration standard & quality gate |
| `test_production_health_dashboard.py` | 2 | 2 | 0 | 0 | Real-time console metrics rendering |
| `test_production_workspace.py` | 5 | 5 | 0 | 0 | Canonical workspace structure & paths |
| `test_stage_3_3_1_corrections.py` | 9 | 9 | 0 | 0 | Text boundaries, attribution manifest |
| `test_stage_3_4_production.py` | 9 | 5 | 4 | 0 | Historic video ffprobe; 4 showcase files skipped |
| `test_stage_3_5_thumbnail.py` | 4 | 4 | 0 | 0 | 10-dimension thumbnail scoring & 9:16 layout |
| `test_stage_3_6_notifications.py` | 6 | 6 | 0 | 0 | Desktop & email alert deduplication |
| `test_stage_3_7_youtube.py` | 7 | 7 | 0 | 0 | YouTube API client, resumable upload mock |
| `test_stage_3_8_integration.py` | 4 | 4 | 0 | 0 | Autonomous pipeline orchestration |
| `test_stage_3_9a_thumbnail_redesign.py` | 5 | 5 | 0 | 0 | Mobile feed previews & concept diversity |
| `test_stage_3_9b_media_lifecycle.py` | 5 | 5 | 0 | 0 | Media classification, quarantine, purge |
| `test_stage_3_9cd_scheduler.py` | 6 | 6 | 0 | 0 | Four cycles daily cron, midnight rollover |
| `test_stage_3_9f_email_dashboard.py` | 10 | 10 | 0 | 0 | Email formatting, credential redaction |
| `test_stage_3_10_resilience.py` | 15 | 15 | 0 | 0 | Network interruption, exponential backoff |
| `test_stage_3_11_real_recovery.py` | 6 | 6 | 0 | 0 | Crash recovery from real pipeline checkpoints |
| `test_stage_3_12_youtube.py` | 7 | 7 | 0 | 0 | OAuth lifecycle, remote duplicate prevention |
| `test_stage_3_13a_content_integrity.py` | 10 | 10 | 0 | 0 | 11-point Content Integrity Gate (A through K) |
| `test_stage_3_13b_lifecycle_audit.py` | 3 | 3 | 0 | 0 | Media directory integrity after cleanup |
| `test_stage_3_17_deployment.py` | 11 | 11 | 0 | 0 | Deployment integrity, secret scanner checks |
| `test_stage_3_18_recovery_matrix.py` | 20 | 20 | 0 | 0 | 14-scenario failure & recovery matrix |
| `test_stage_3_19_creative_identity_e2e.py` | 5 | 5 | 0 | 0 | Astrophysics/space, history, survival styles |
| `test_stick_animation.py` | 1 | 1 | 0 | 0 | Programmatic lock on stick animation service |
| **TOTAL** | **221** | **217** | **4** | **0** | **100% Passed (0 Failures, 0 Errors)** |

---

## 4. Documentation of Skipped Tests

The only skipped tests are the 4 legacy showcase checks in `test_stage_3_4_production.py`:
1. `test_public_domain_historical_manifest`: Skipped because the old showcase manifest was consolidated into canonical productions in Stage 3.13B.
2. `test_editorial_ass_subtitles`: Skipped because showcase sample ASS file was consolidated in Stage 3.14.
3. `test_shorts_thumbnail_specs`: Skipped because showcase sample thumbnail was consolidated in Stage 3.14.
4. `test_contact_sheet_specs`: Skipped because showcase sample contact sheet was consolidated in Stage 3.14.

These skips are conditioned via `@pytest.mark.skipif(not os.path.exists(...))`, ensuring that if those legacy assets are ever restored, the tests will automatically re-engage.
