# Stage 3.32B: Test Results

**Date:** 2026-11-20 (IST).
**Code under test:** the development worktree, uncommitted, on top of `4ae426b`.

**Import path.** Every run used Windows paths: `PYTHONPATH="<worktree>;<worktree>\backend;<worktree>\backend\tests"`. A smoke run printed `control_plane.__file__` to confirm the worktree file was loaded.

**Isolation:**
- Every test uses a temporary SQLite file, a temporary data directory, a fixed or mutable clock, a fake Operational Intelligence service and in-memory handlers.
- No production database, file, service or network was touched. The no-network test patches `socket.connect` to fail.
- After the runs, the live `studio.db` has **no** `autonomy_*` tables, and none of `backend/data/autonomy/`, `AUTONOMY_DISABLED` or `autonomy_startup.json` exists in the main checkout.

## 1. New tests: `backend/tests/test_autonomy_control_plane_332b.py`

There are 40 test functions, giving 47 test cases with the kill-switch matrix. **All pass** (about 16 s).

| # | Required coverage | Test(s) |
|---|---|---|
| 1 | All four autonomy levels | `test_default_is_off_everywhere`, `test_observe_level_records_but_cannot_propose`, `test_recommend_level_queues_but_never_executes`, `test_approved_execution_runs_and_verifies`, `test_levels_are_per_category_and_global_caps` |
| 2 | Unknown action rejection | `test_unknown_action_types_are_rejected` (unknown, empty, non-string, wrong case; payload: unknown field, bad choice, bool-as-int) |
| 3 | Prohibited action rejection | `test_prohibited_registry_covers_the_specification` (all 20 required IDs plus self-policy); `test_prohibited_actions_refused_everywhere` (propose, standing grant, handler registration, a database-injected request cannot be approved or executed) |
| 4 | Kill-switch combinations | `test_kill_switch_combinations` (all 8 combinations of A/B/C); `test_kill_switches_fail_closed` (missing, invalid or not-enabled startup file; env override; latch; database missing); `test_sentinel_check_error_fails_closed`; `test_kill_switch_activation_without_database_writes_sentinel`; `test_kill_switch_during_execution_stops_retries` |
| 5 | Approval and rejection | `test_approve_reject_cancel` (approve, reject pending and approved, idempotent reject, cancel, reserved and invalid operators, reason required, unknown id); `test_autonomy_cannot_change_its_own_policy` |
| 6 | Approval expiration | `test_expired_requests_cannot_be_approved_or_executed` |
| 7 | Modified payload invalidates approval | `test_payload_change_invalidates_approval`; `test_payload_tampered_in_database_is_not_executed`; `test_status_forged_in_database_is_not_a_valid_approval` (signature) |
| 8 | Duplicate approval prevention | `test_duplicate_approvals_and_proposals` (idempotent repeat, second approver refused, duplicate proposal returns the same request, database partial unique index) |
| 9 | Concurrent execution | `test_concurrent_execution_attempts_run_once` (8 threads; handler runs once) |
| 10 | Atomic claiming | `test_atomic_claim_across_control_plane_instances` (a second instance cannot claim mid-run) |
| 11 | Audit integrity | `test_audit_is_append_only_and_hash_chained` (UPDATE and DELETE refused by triggers; an edit with the trigger dropped is detected at the exact `seq`); `test_audit_redacts_secrets` |
| 12 | Unauthorized API requests | `test_unauthorized_api_requests` (remote client 403 on every GET and POST; no CSRF token 403; cross-origin session 403; no generic execution route; execute body is exactly `{operator, confirm}`) |
| 13 | CSRF protection | `test_csrf_protection` (wrong token, foreign Origin, `Sec-Fetch-Site` cross-site and same-site, `text/plain` body, extra field, typed deactivation confirmation); `test_api_approval_flow_and_errors` |
| 14 | Dashboard integration | `test_dashboard_autonomy_section` (all 8 tabs, element ids, no duplicate function names, CSRF header and JSON, ids never inlined into JS); `test_dashboard_script_parses` (Node) |
| 15 | Existing production functionality | `test_production_records_untouched_and_no_production_calls` (production rows unchanged through a full lifecycle and the kill switch; only the 4 new tables created; AST import allow-list; no production calls in executable modules); `test_community_draft_is_never_published` |
| 16 | Failure recovery | `test_bounded_retries_then_failure` (also secret redaction in errors), `test_transient_failure_recovers_on_retry`, `test_permanent_error_and_timeout_are_not_retried`, `test_verification_failure_compensates`, `test_interrupted_execution_is_reconciled_not_rerun`, `test_api_fails_closed_when_database_missing` |
| — | Standing approvals and engine | `test_standing_approval_execution_and_limits` (daily cap, revoke, expiry, below-level), `test_engine_recommendations_collapse_duplicates`, `test_no_network_during_control_plane_use` |

## 2. Full suite

| Run | Passed | Failed | Skipped | Runtime |
|---|---|---|---|---|
| Baseline (3.32A, `4ae426b` content) | 949 | 4 | 173 | 8 min 46 s |
| **3.32B** | **995** | **5** | **173** | 9 min 38 s |

The 3.32B run added the 47 new tests, all passing. 949 + 47 = 996, so one previously passing test failed.

### Failures

**The 4 known pre-existing failures are unchanged**, with the same assertions as in the 3.31 and 3.32A runs:

| Test | Reason |
|---|---|
| `test_production_dashboard_browser.py::test_dashboard_html_page_served` | Expects a `CrazzyDocs Shorts Studio` title the page no longer has |
| `test_youtube_branding.py::test_dashboard_html_contains_branding_elements` | Expects a `favicon.ico?v=20261111` link the page no longer has |
| `test_youtube_branding.py::test_source_youtube_icon_exists` | `media/source/branding/Youtube-Icon.png` is absent in the worktree (untracked media) |
| `test_stage_3_8_integration.py::test_end_to_end_orchestration_run` | QC correctly blocks the non-Lemo-Opuscar test renderer |

**One timing-sensitive test failed and is not caused by 3.32B:** `test_dashboard_auth_status_cache.py::test_slow_youtube_status_does_not_block_other_dashboard_requests`.
- It asserts that `/api/dashboard/health` answers in under 1.0 s. It measured 1.05 s.
- It fails the same way on a **clean checkout of `4ae426b`**: a temporary detached worktree, removed afterwards, measured 1.05–1.12 s in 3 of 3 runs.
- In the 3.32B worktree it failed 5 of 5 reruns, at the same margin.
- At the time, production cycle `cycle_11_20261120_1000` was running (stage VOICEOVER) on the same machine. The test passed in the earlier baseline runs.
- **Conclusion:** a machine-load timing margin, not a regression. Neither endpoint it calls was modified. Re-run it when no cycle is active to confirm.

**No newly introduced failures** are attributable to Stage 3.32B. No existing test was modified.

## 3. Manual and live checks (not tests)

- **Smoke run:** propose, approve, execute and verify; standing approval auto-run through the engine; audit chain verified. Run against a temporary database.
- **Dashboard tab** rendered in a static preview on `127.0.0.1:8766`, with real control-plane JSON from a temporary database and mocked `fetch`. The server was stopped afterwards.
  - Header pill: `Autonomy: APPROVED_EXECUTION · 5 pending`.
  - 5 queue rows, 26 permission rows, kill switches, levels and audit all rendered.
  - The only console error came from the pre-existing `refreshCreativeEngine` panel reacting to the empty mock.
- **Production:** no restart, no cycle, no configuration or flag change, no write to the live database.
