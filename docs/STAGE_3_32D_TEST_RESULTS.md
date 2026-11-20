# Stage 3.32D: Test Results

| | |
|---|---|
| **Date** | 2026-11-20 |
| **Base** | 5b2a4ad |
| **Worktree** | `crazzy-docs-verify-capabilities-ac634e` |
| **Interpreter** | `D:/Agents/CrazzyDocs/.venv/Scripts/python.exe` |
| **`PYTHONPATH`** | the worktree, plus `backend` and `backend/tests` |
| **Databases** | temporary SQLite files only (`conftest.py` blocks the production database) |
| **External services** | network, ffprobe and the providers are faked; no real recovery is invoked |

## 1. Stage 3.32D suite (`backend/tests/test_self_healing_332d.py`)

**53 cases, all pass.** They run on a seeded temporary database through the real Stage 3.32A Operational Intelligence service.

| # | Required area | Test(s) |
|---|---|---|
| 1 | All six failure classifications | `test_all_six_failure_classes` (17 parametrized cases) |
| 2 | Deterministic versus transient | `test_deterministic_versus_transient_uses_signature_and_recurrence`: padding error is deterministic though the retry policy said TRANSIENT; unknown is not transient; recurrence escalation; a recovered job keeps TRANSIENT; expiry notes never escalate |
| 3 | Historical correlation | `test_historical_correlation_and_evidence`: three addresses give one signature; occurrences, jobs, stages; retries never succeeded; recurring dimensions |
| 4 | Diagnostic evidence | same test: message, exception type, stage, job, attempt, timestamps, log context, related failures, renderer, recovery history |
| 5 | Recovery planning | `test_recovery_planning` |
| 6 | Recovery-window checks | `test_recovery_window_checks` (expired, and unknown deadline) |
| 7 | Duplicate prevention | `test_duplicate_recovery_prevention` |
| 8 | Concurrent execution | `test_concurrent_execution_prevention` (4 threads, exactly 1 execution) |
| 9 | Kill switches | `test_kill_switch_enforcement` × dashboard / sentinel / startup |
| 10 | Approval enforcement | `test_approval_enforcement`, `test_standing_approval_runs_only_low_risk_diagnostics` |
| 11 | Self-approval prevention | `test_self_approval_and_identity_protection` (10 identity variants × approve / reject / execute / control-plane approve / set_level / standing grant) |
| 12 | Action timeout | `test_action_timeout` (TIMED_OUT gives FAILED, "timed out") |
| 13 | Retry limits | `test_retry_limits` (diagnostic 2 attempts, provider 1; per-incident failure cap) |
| 14 | Post-action verification | `test_post_action_verification` (both layers; job left FAILED, nothing placed into the job) |
| 15 | Failed verification | `test_failed_verification_is_never_success` × download error / corrupted file / ffprobe unavailable (UNVERIFIED); `test_independent_check_catches_tampering` |
| 16 | Resource-lock protection | `test_resource_lock_protection` (all-or-nothing, engine pre-check, handler refusal, expiry) |
| 17 | Recommendation integration | `test_recommendation_integration_and_learning`, `test_recommendation_engine_reads_learning_without_creating_tables` |
| 18 | Audit trail integrity | `test_audit_trail_integrity` (events present, hash chain verifies, UPDATE/DELETE refused) |
| 19 | Missing diagnostic data | `test_missing_diagnostic_data_is_reported_not_invented` |
| 20 | Existing production functionality | `test_production_tables_and_files_untouched` (production rows byte-identical, no network sockets, only `selfheal_*` tables added); full suite below |
| — | Levels, notifications, Gemini | `test_level_off_and_observe`, `test_notifications_dedup_and_rate_limit`, `test_gemini_disabled_by_default_bounded_and_optional` |
| — | Incremental scans, live preconditions | `test_incremental_scan_and_throttle`, `test_execute_rechecks_job_preconditions` |
| — | Registry and handler safety | `test_registry_permitted_and_prohibited_actions`, `test_handlers_refuse_urls_and_queries_not_in_evidence` |
| — | API | `test_api_endpoints`, `test_api_reports_blocked_engine` |
| — | Dashboard | `test_dashboard_self_healing_tab` (node parses the script) |
| — | Source safety | `test_source_has_no_generic_execution_and_no_production_writes` |

**Change to an existing test:** `test_autonomy_control_plane_332b.py::test_production_records_untouched_and_no_production_calls`. Its import allow-list now includes `backend.app.autonomy.self_healing.handlers`, because `handlers.py` registers the self-healing handlers. The safety of that package is covered by the 3.32D source-safety test.

## 2. Full regression suite

```
5 failed, 1109 passed, 173 skipped, 3 warnings in 766.52s
```

| Run | Passed | Failed | Skipped |
|---|---|---|---|
| Baseline 5b2a4ad (Stage 3.32C) | 1056 | 5 | 173 |
| Stage 3.32D | **1109** (+53, exactly the new cases) | 5 | 173 |

**The same 5 tests fail in both runs. No new failures.**

| Test | Cause |
|---|---|
| `test_production_dashboard_browser::test_dashboard_html_page_served` | Fails on every recorded run since before 3.31 |
| `test_stage_3_8_integration::test_end_to_end_orchestration_run` | Fails on every recorded run since before 3.31 |
| `test_youtube_branding::test_source_youtube_icon_exists` | Fails on every recorded run since before 3.31 |
| `test_youtube_branding::test_dashboard_html_contains_branding_elements` | Fails on every recorded run since before 3.31 |
| `test_dashboard_auth_status_cache::test_slow_youtube_status_does_not_block_other_dashboard_requests` | Timing under load; also fails on a clean checkout of 4ae426b |

## 3. Dry run on a scratch copy of the backup (not live)

**Setup:** the `deploy_5b2a4ad_20261120_111917` backup at level RECOMMEND. Providers and downloader were faked; 0 network calls.

**Cold scan (1.3–2.2 s):**
- 13 failure records, 10 incidents, 7 diagnostic proposals, 0 executions;
- audit chain verified.

**Incremental rescan (0.12–0.35 s):** 0 new incidents.

**Classes:** DETERMINISTIC 1, RESOURCE 2, UNKNOWN 7. Of the UNKNOWN incidents, most are recovery-window expiry notes, correctly treated as outcomes rather than errors.

**Issues the dry run found, all fixed before the final run:**
1. The quota message was unmatched.
2. Expiry notes were escalated to "suspected deterministic" across 27 jobs.
3. Incidents with requests already queued were relabelled "no action".

**Dashboard preview** (static, fed by the scratch data): renders with no console errors.

## 4. Live production checks

- The production `studio.db` has no `selfheal_*` tables.
- The autonomy configuration is unchanged.
- `topic_history.json`, the schedule and the Stage 3.31 flags were not touched.
- The engine was never run against production.
