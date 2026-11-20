# Named Production Schedule Profiles: Test Results

**Date:** 2026-11-20 (IST).
**Code under test:** the development worktree, uncommitted, on top of `ba1cc0c`.

**Import path.** Every run used Windows paths: `PYTHONPATH="<worktree>;<worktree>\backend;<worktree>\backend\tests"`.

**Isolation:**
- Every test gets its own temporary SQLite database. `control_center_service.SessionLocal` and the scheduler's SessionLocal are redirected to it.
- The fixed clocks are 12:00 IST (no window open) and 06:30 IST (inside a recovery window).
- Activation runs the real `schedule_service` / `control_center_service` path against that temporary database. The daemon, workers and YouTube are never involved.
- The suite's `conftest.py` also blocks any connection to a production `studio.db`.

## 1. New tests: `backend/tests/test_production_schedule_profiles.py`

There are 24 test functions, giving 26 test cases. **All pass.**

| Required coverage | Test(s) |
|---|---|
| Save and restore | `test_save_and_restore` (saving leaves the active record byte-identical; restore reproduces the exact checksum through the normal activation path, version 4; runtime profile updated; audit `schedule_profile_saved` / `_loaded` with previous and new); `test_loading_identical_profile_does_not_reactivate`; `test_load_requires_confirmation_and_matching_checksum` |
| Custom names | `test_custom_names` (Unicode and emoji, whitespace collapsing; empty, blank, 81 characters, control characters, punctuation-only, non-string refused) |
| Duplicate names | `test_duplicate_names_are_refused` (case- and spacing-insensitive on save, rename and duplicate) |
| Rename | `test_rename_keeps_configuration` (config and checksum unchanged, created kept, updated changed, active indicator follows) |
| Duplicate profile | `test_duplicate_profile` (new ID, same configuration, survives deletion of the original) |
| Delete | `test_delete_rules` (confirmation required; active profile refused; allowed after another schedule is activated; marker cleared; unknown ID 404) |
| Invalid configuration | `test_invalid_configurations_are_refused` (no active schedule to save; stored schedule with no enabled cycle: preview invalid, load refused, active unchanged) |
| Configuration checksum | `test_checksum_detects_tampering_and_schema` (edited `config_json` detected in list, preview and load; unsupported schema version refused); `test_checksum_properties` (order-independent, empty target = none, sensitive to recovery and timezone) |
| Active profile identification | `test_active_profile_identification` (NO_ACTIVE_SCHEDULE → UNSAVED → PROFILE_ACTIVE → MATCHES_PROFILE) |
| Unsaved modifications | `test_unsaved_modifications_after_loading` (manual change → PROFILE_MODIFIED with unsaved changes and row "ACTIVE_MODIFIED"; back to identical → PROFILE_ACTIVE) |
| Schedule comparison | `test_schedule_comparison` (added, removed, changed fields, recovery shortened, identical regardless of order, timezone change, no active schedule) |
| Activation during an active job | `test_activation_refused_during_active_job` (RUNNING job: preview blocked, load raises `ActivationUnsafe` naming the job, active unchanged; allowed once COMPLETED); `test_open_recovery_window_is_never_shortened` ×3 (window open with job missing / FAILED → refused, COMPLETED → allowed); `test_window_shortening_detected_but_lengthening_allowed` |
| YouTube publication schedule preserved | `test_youtube_publication_schedule_preserved` (active and draft publication-schedule records byte-identical after save, duplicate, load and rename) |
| Existing schedule functionality | `test_profile_management_never_touches_active_schedule_or_jobs` (active, draft and history records, runtime profile object and jobs unchanged by save, duplicate, rename, preview, list and delete); `test_existing_schedule_functionality_unchanged` |
| API | `test_api_endpoints` (remote 403; cross-origin and cross-site 403; extra field 422; duplicate 409; load without confirmation 400; stale checksum 409; load OK; rename; duplicate; delete active 409; delete without confirmation 400; unknown 404); `test_api_reports_unsafe_activation_reasons` (409 with `reasons[]`) |
| Dashboard | `test_dashboard_integration` (indicator, Save button, card, preview panel, all functions, no duplicate function names, `data-id`, checksum-bound load, tab hook); `test_dashboard_script_parses` (Node) |

**Related suites re-run together:** dynamic production schedule, unified schedule editor, schedule and auto-publish, 3.32B autonomy and 3.32A ops. Result: **156 passed**.

## 2. Full suite

| Run | Passed | Failed | Skipped | Runtime |
|---|---|---|---|---|
| Previous (3.32B, `ba1cc0c` content) | 995 | 5 | 173 | 9 min 38 s |
| **Schedule profiles** | **1021** | **5** | **173** | 8 min 59 s |

The run added +26 passing tests, exactly the new ones. **The same 5 failures, no new ones:**

| Test | Reason |
|---|---|
| `test_production_dashboard_browser.py::test_dashboard_html_page_served` | Pre-existing: expects an old page title |
| `test_youtube_branding.py::test_dashboard_html_contains_branding_elements` | Pre-existing: expects an old favicon link |
| `test_youtube_branding.py::test_source_youtube_icon_exists` | Pre-existing: untracked media absent in the worktree |
| `test_stage_3_8_integration.py::test_end_to_end_orchestration_run` | Pre-existing: QC blocks the non-Lemo-Opuscar test renderer |
| `test_dashboard_auth_status_cache.py::test_slow_youtube_status_does_not_block_other_dashboard_requests` | Timing margin under machine load (1.09 s against 1.0 s). It also fails on a clean `4ae426b` (see `STAGE_3_32B_TEST_RESULTS.md`). |

No existing test was modified.

## 3. Preview evidence (not a test)

**Setup.** The dashboard was rendered from a static copy on `127.0.0.1:8767` (stopped afterwards). It used real JSON produced by the profile service against a temporary database, with mocked `fetch`. Three profiles were saved: "Hourly (24 cycles)", "Weekday plan" and "Weekend plan". The active schedule was then modified, and a RUNNING job was injected.

**Observed:**
- Indicator: "Active profile: Weekend plan · unsaved changes: Loaded from 'Weekend plan', but the active schedule was changed afterwards".
- Table: 3 rows with created and modified times (IST), cycles, timezone and recovery window ("45 min (all cycles)", "90 min ×1, 120 min ×1"). Statuses were "Inactive", "Inactive" and "Active · modified".
- Preview of "Hourly (24 cycles)": "22 cycle(s) added; 2 cycle(s) changed; recovery shortened for cycle_1, cycle_3." The Load button was enabled.
- Preview of "Weekday plan" while a job was RUNNING: "⛔ Production job cycle_3_20261120_1800 is RUNNING (stage VOICEOVER) … Try again after it finishes." The Load button was disabled.
- Phone width (375 px): the card fits (335 px) and the table scrolls inside it.

## 4. Production unchanged

Read-only check of the live `backend/studio.db` after all runs:
- active schedule revision **`ccf0bd04f159`**, version 11, 24 cycles, activated `2026-11-20T03:14:23+05:30`; unchanged;
- no `production_schedule_profiles` table;
- no `control_center.schedule.named_profile` key.

No daemon or dashboard restart, no cycle triggered, no configuration, flag or `topic_history.json` change.
