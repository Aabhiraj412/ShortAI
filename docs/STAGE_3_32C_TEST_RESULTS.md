# Stage 3.32C: Test Results

**Date:** 2026-11-20
**Base:** c4e1cf5
**Worktree:** `crazzy-docs-verify-capabilities-ac634e`
**Interpreter:** `D:/Agents/CrazzyDocs/.venv/Scripts/python.exe`
**`PYTHONPATH`:** the worktree path, plus `backend` and `backend/tests`. Every import resolves from the worktree, not from another checkout.

**Database isolation:** `conftest.py` redirects `CRAZZYDOCS_DATABASE_PATH` to a throwaway database and blocks connections to the production `studio.db`.

## 1. Stage 3.32C suite (`backend/tests/test_recommendation_engine_332c.py`)

**Result:** 33 test functions, 35 cases. **All pass.**

| Area | Tests |
|---|---|
| Rule categories | production reliability; renderer (deterministic vs transient); topic; API (quota is never invented); recovery and content |
| Missing data | no historical data → no findings; insufficient-evidence thresholds; one broken section or rule never stops the others |
| Grouping | root-cause grouping through the real 3.32A `OperationalIntelligence` service: three FFmpeg messages that differ only in filter address → one recommendation |
| Scoring | confidence components and labels; priority bands, recency and the weighted sum |
| Lifecycle | dedupe updates instead of duplicating; expiry and later reappearance; supersession; operator transitions (reject needs a reason, resolve needs evidence); a rejected recommendation is suppressed for 7 days |
| Control plane | approval executes nothing; OFF writes nothing; OBSERVE records observations only; the global level caps the category level; no permission changes and no executable requests; each kill switch (dashboard, sentinel file, startup config) pauses the engine; recommendations stay readable while a kill switch is active; throttle and busy guard |
| Gemini | disabled by default; an unavailable Gemini never blocks recommendations; wording is bounded (≤ 3 per run) and cached |
| Isolation | no network or external calls during analysis; a source scan finds no forbidden calls (`requests`, `cp.execute(`, publishing, scheduling) |
| API and dashboard | endpoints, CSRF, a blocked engine reported correctly; Recommendation Center markup; the dashboard script parses under node |
| Production safety | production tables (jobs, schedule, publishing policy) are unchanged after a full run |

## 2. Full regression suite

```
5 failed, 1056 passed, 173 skipped, 3 warnings in 670.77s
```

| Run | Passed | Failed | Skipped |
|---|---|---|---|
| Baseline c4e1cf5 (schedule profiles) | 1021 | 5 | 173 |
| Stage 3.32C | **1056** (+35, exactly the new cases) | 5 | 173 |

**The same 5 tests fail in both runs. No new failures.** All 5 predate Stage 3.32C:

| Test | Cause |
|---|---|
| `test_production_dashboard_browser::test_dashboard_html_page_served` | Has failed on every recorded run since before 3.31 |
| `test_stage_3_8_integration::test_end_to_end_orchestration_run` | Has failed on every recorded run since before 3.31 |
| `test_youtube_branding::test_source_youtube_icon_exists` | Has failed on every recorded run since before 3.31 |
| `test_youtube_branding::test_dashboard_html_contains_branding_elements` | Has failed on every recorded run since before 3.31 |
| `test_dashboard_auth_status_cache::test_slow_youtube_status_does_not_block_other_dashboard_requests` | Timing: about 1.05 s against a 1.0 s limit under machine load. It also fails on a clean checkout of 4ae426b. |

## 3. Dry run on a scratch copy of production data (not the live database)

**Source:** the deployment backup `deploy_c4e1cf5_20261120_1046/studio.db`, copied to the scratchpad.

**First run:** 22 recommendations.

| Priority | Count |
|---|---|
| Critical | 1 |
| High | 5 |
| Medium | 15 |
| Low | 1 |

The four example situations in the specification were all reproduced from the data:
- recovery expirations;
- FFmpeg padding failures;
- provider concentration;
- the latest-run rejection of a whole provider.

**Second run:** 0 created, 22 updated (deduplication works). The audit hash chain verified.

**Timing:** 0.27 s cold, 0.14 s warm.

## 4. Live production checks

- The production `studio.db` has **no** `autonomy_recommendation*` tables. The engine has never run against it.
- `topic_history.json`, the active schedule, the Stage 3.31 feature flags and the autonomy configuration were not touched.
