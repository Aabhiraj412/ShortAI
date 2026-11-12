# Stage 3.21O-R2 — Post-Fix Verification Report

**Scope:** Verification only. No feature work, rendering, scheduler activation, publishing, email, database/ledger mutation, Git staging, reset, stash, commit, or push was performed.

## Summary

The Stage 3.21O-R worker-state, IST-formatting, and publishing-mode fixes are serving from the live dashboard and passed the focused regression suite. The Bhopal record is now historical `stale` data rather than a live worker. The production scheduler remains disabled.

## Diff review

**PASSED:** `git diff --check` reports no whitespace errors for the three tracked source files.

| File | Review result |
|---|---|
| `backend/app/main.py` | **PASSED.** Adds `publishing_mode` to the dashboard overview payload and retains the restricted manual-approval settings type. |
| `backend/app/services/production_observability.py` | **PASSED.** Uses a 300-second UTC heartbeat freshness threshold for `starting`/`running` records. Stale records are projected as `stale` in dashboard views without updating persisted progress JSON. Fresh records remain active. |
| `backend/app/templates/dashboard.html` | **PASSED.** Adds `formatIstTimestamp()` using `Asia/Kolkata`, routes header/history/publishing/recovery/activity/archive fields through it, and takes the mode label from overview API data. No literal `Mode: Automatic Public` remains. |
| `docs/PHASE_5_STAGE_3_21O_R_VALIDATION_AND_IST_RECONCILIATION_REPORT.md` | **PASSED.** Contains Bhopal process evidence, root causes, change scope, runtime checks, and the reload incident. It accurately records the transient indentation failure and successful recovery. |

The full source-file diffs include pre-existing Stage 3.21N/O changes in the same files. The R-specific additions are limited to the heartbeat freshness projection, overview publishing mode, IST formatter/display calls, and reconciliation report.

## Syntax, import, and focused regression tests

**PASSED:** AST parsing of `production_observability.py` and `main.py` completed successfully without writing bytecode.

**PASSED:** The following focused suite completed: **5 passed** in 3.80 s.

```text
test_observability_tracks_all_12_stages_and_persists_across_restart
test_observability_isolates_test_runs_and_redacts_secrets
test_fastapi_observability_endpoints_and_dashboard_html
test_dashboard_four_state_reconciliation_during_validation
test_independent_scheduler_configuration
```

The selected tests use `tmp_path`, isolated `ProductionObservabilityStore` files, mocked state, and FastAPI test clients. They do not invoke pipeline, render, scheduler, upload, email, or publishing endpoints. Pytest emitted only existing `pytest-asyncio` and Pydantic deprecation warnings.

**PASSED:** An additional isolated temporary-state assertion created a stale validation record with Bhopal's last UTC heartbeat. It confirmed:

- `get_active_production()` returns `None`.
- Dashboard payload `has_active` is `false`.
- Displayed and history status are `stale`.

## Live dashboard verification

**PASSED:** `GET /dashboard` returned HTTP 200 from port 8000.

| Live property | Result |
|---|---|
| Validation worker | **PASSED:** `IDLE` |
| Bhopal active state | **PASSED:** progress payload `has_active: false`; displayed/history status `stale` |
| Overall operational state | **PASSED:** `IDLE` |
| Production worker | **PASSED:** `IDLE` |
| Scheduler | **PASSED:** overview `DISABLED`; health endpoint reports daemon `STOPPED` and Windows task `Disabled` |
| Publishing mode | **PASSED:** `manual_approval` |
| Embedded scheduler after dashboard restart | **PASSED:** dashboard health reports scheduler daemon `STOPPED`; no scheduler lock or production worker was observed |
| FFmpeg/render started by verification | **PASSED:** no `ffmpeg.exe` process observed; no render was invoked |

## IST timestamp audit

**PASSED — presentation source:** Header clock and visible dynamic timestamp columns use `formatIstTimestamp()`, which parses the source instant with `new Date`, formats it using `Asia/Kolkata`, and appends `IST`. Source search confirmed that history, publishing, recovery, activity, and archive table values use this formatter. Cycle, next-run, and target-publishing API strings already include `IST`.

**PASSED — conversion check:** `2026-11-11T14:11:59.855359+00:00` converts to `2026-11-11 19:41:59 IST`, matching the Bhopal evidence.

**PASSED — API timezone semantics:** Overview emits `timestamp_ist` as an offset-aware `+05:30` ISO value. Progress/history stores event times as offset-aware UTC ISO values, which are the correct transport representation for client-side conversion.

**UNVERIFIED:** A separate browser automation session was not used to inspect rendered DOM text. The served dashboard source and live API payloads were inspected directly, which verifies the implementation path but not a specific browser's locale/runtime behavior.

## Git preservation

**PASSED:** Final `git status --short` retains all original modified and untracked Stage 3.21N/O artifacts. The only additional artifact from this verification is this report. Nothing was staged, committed, pushed, reset, stashed, reverted, discarded, or deleted.

## Final conclusion

**PASSED with one unverified UI-runtime limitation.** The stale Bhopal state no longer appears as active, IST conversion is present at every identified dashboard presentation point, and the actual publishing mode is displayed from live API data. No further changes were made.
