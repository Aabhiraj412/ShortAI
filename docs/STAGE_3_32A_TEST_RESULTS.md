# Stage 3.32A: Test Results

**Date:** 2026-11-20 (IST).
**Code under test:** the development worktree `D:\Agents\CrazzyDocs\.claude\worktrees\crazzy-docs-verify-capabilities-ac634e`, uncommitted, on top of `e14efc5`.

**Import path.** Every run used Windows paths:

```
PYTHONPATH="<worktree>;<worktree>\backend;<worktree>\backend\tests"
```

The smoke run printed the module path to confirm it was the worktree file (`...\worktrees\crazzy-docs-verify-capabilities-ac634e\backend\app\services\operational_intelligence.py`), not the main checkout.

**No real API requests were made.** Tests use temporary SQLite databases built from the live `CREATE TABLE` statements, a fixed clock, a synthetic progress store and a patched `urlopen` that fails if called.

## 1. New tests: `backend/tests/test_operational_intelligence_332a.py` (17 tests, all pass)

| # | Required coverage | Test(s) |
|---|---|---|
| 1 | Production statistics | `test_production_statistics`: counts, completion %, outlier-excluded mean, median, stage distribution with `NOT_RECORDED`, stage timings |
| 2 | Failure grouping | `test_failure_grouping_and_classes` (3 FFmpeg errors with different addresses become 1 pattern; file lock; PERMANENT topic exhaustion; EXPIRED class; most failure-prone stage; trend); `test_root_cause_catalog_and_templates` |
| 3 | Recovery metrics | `test_recovery_metrics`: needing recovery, recovered, failed after recovery, time to recover, expiry rate, re-arms |
| 4 | Renderer reliability | `test_renderer_reliability`: progress-store render outcomes and durations, renderer counts, error categories, acquisition failures |
| 5 | Candidate ledger analytics | `test_candidate_ledger_analytics`: runs, acceptance, reasons, providers, classifier v1 vs v2, selected categories, exhaustion |
| 6 | API quota calculations | `test_api_quota_calculations`: Pacific-day bucket usage, remaining estimate, active Google block, error categories, Gemini script counts, research providers, GNews usage, `estimated` / `unavailable` labels |
| 7 | Missing historical data | `test_missing_historical_data`: no ledger tables, no progress store, no GNews file; values are `null` with reasons; health UNKNOWN |
| 8 | Empty database | `test_empty_and_missing_database`: empty file works; a missing file is reported unavailable and **not created** |
| 9 | Concurrent production writes | `test_concurrent_production_writes`: a writer thread commits 150 jobs and events while 25 overviews run; zero writer errors; final counts consistent |
| 10 | Dashboard API responses | `test_dashboard_api_responses` (non-local client gets 403; 200 with every section; day bounds give 400; section endpoint; unknown section gives 404; daily-summary date validation); `test_daily_summary_anomalies_and_recommendations` |
| 11 | Read-only enforcement | `test_read_only_enforcement` (DB hash unchanged; INSERT and CREATE refused on the service connection; every `sqlite3.connect` uses `mode=ro`); `test_no_network_and_no_production_services` (source scan for forbidden calls; network blocked) |
| 12 | Dashboard regression | `test_dashboard_has_ops_section_and_keeps_existing_tabs` (all 6 existing tabs plus ops; element ids; no duplicate function names; existing refresh functions present; template text escaped); `test_dashboard_script_parses` (whole page script parses in Node) |
| — | Performance | `test_cache_is_bounded_and_reused`: one bundle per window, refresh within TTL re-reads nothing, cache bounded to 64 entries |

**Focused run:** with the related 3.30 dashboard-formatter and quota tests, 32 passed in 12.7 s.

## 2. Full suite

| Run | Passed | Failed | Skipped | Runtime |
|---|---|---|---|---|
| Baseline (after the 3.31 attempts fix, `e14efc5`) | 932 | 4 | 173 | 8 min 39 s |
| **3.32A** | **949** | **4** | **173** | 8 min 46 s |

The change is +17 passed, exactly the new tests. **No newly introduced failures.**

**The 4 failures are pre-existing and unchanged.** Their assertion messages are identical in both runs. The same 4 also failed on a clean checkout of `589c2b5` during Stage 3.31.

| Test | Pre-existing reason |
|---|---|
| `test_production_dashboard_browser.py::test_dashboard_html_page_served` | Expects a `CrazzyDocs Shorts Studio` title the page no longer has |
| `test_youtube_branding.py::test_dashboard_html_contains_branding_elements` | Expects a `favicon.ico?v=20261111` link the page no longer has |
| `test_youtube_branding.py::test_source_youtube_icon_exists` | `media/source/branding/Youtube-Icon.png` does not exist in the worktree (media is untracked) |
| `test_stage_3_8_integration.py::test_end_to_end_orchestration_run` | QC correctly blocks the non-Lemo-Opuscar test renderer |

No existing test was modified.

## 3. Coverage of the new component

`pytest-cov` is not installed and was not added to the production venv.

Behavioural coverage is complete for every section:
- each metric family is asserted with exact expected values on seeded data;
- every unavailable path (missing tables, missing files, missing database) is exercised.

## 4. Live read-only validation (not a test)

The service ran against the live `studio.db` (opened `mode=ro`) and the live data directory:
- 7-day overview in 150–180 ms cold and about 75 ms warm;
- the database file hash was identical before and after;
- real findings are listed in the implementation report.

The dashboard tab was rendered in a static preview: real JSON, mocked `fetch`, served on `localhost:8765`, which was stopped afterwards. The production dashboard was not restarted.
