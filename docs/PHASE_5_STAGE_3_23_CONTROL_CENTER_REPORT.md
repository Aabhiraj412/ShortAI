# Stage 3.23 Control Center Report

## Outcome

The control-center foundation is implemented and focused tests pass. **Stage 3.23 is not ready for automatic publishing or production activation.** The readiness gate contains blocked items and one failed database-preservation item. Automatic publishing remains disabled; the scheduler remains disabled/stopped.

## Architecture and implementation

- Persistent control-center settings are stored in the existing `studio_settings` key/value table. No migration or parallel database was introduced.
- Operating modes support `PRODUCTION`, `GAMING`, `IDLE`, and `MAINTENANCE`, with a pending draft, explicit activation confirmation, and bounded IST change history. With no persisted choice, runtime mode resolves safely to `IDLE`.
- Schedule profiles support `FOUR_CYCLES_DAILY`, `SINGLE_DAILY`, and `CUSTOM` draft validation. Validation enforces timezone/time/weekday checks, explicit next-day rollover, a six-hour minimum, a 24-hour maximum, non-overlapping windows, and unique target slots. `FOUR_CYCLES_DAILY` is immutable in the validator.
- Only an explicitly validated and confirmed draft becomes active. Startup restores the active schedule only; it does not activate a pending draft.
- Production start paths check operating mode and the configurable concurrency limit. Scheduled starts are deferred outside `PRODUCTION`; no existing render is paused or killed.
- Resource status reads CPU and RAM using Windows APIs, disk use from the standard library, and counts active production jobs. GPU/render-process metrics are reported as `NOT AVAILABLE`.
- The existing dashboard now contains a control-center panel for mode selection, schedule JSON drafts, concurrency, resource snapshots, and the manual-approval/automatic-disabled status.
- Existing Stage 3.22 YouTube and email services remain the only publishing/notification implementation. The Stage 3.22 publishing API remains manual-approval-only; no automatic route or second ledger was added.

## API contracts

All `/api/control-center/...` routes are loopback-only and bearer-token protected.

| API | Contract |
|---|---|
| `GET /api/control-center/state` | Active/pending modes, active/draft schedules, concurrency, and recent change history |
| `POST /api/control-center/operating-mode/draft` | Save `{"mode":"PRODUCTION\|GAMING\|IDLE\|MAINTENANCE"}` |
| `POST /api/control-center/operating-mode/activate` | Activate the matching draft using `{"mode":"...","confirm":true}` |
| `DELETE /api/control-center/operating-mode/draft` | Cancel pending mode |
| `GET /api/control-center/schedule/preset/{profile_name}` | Load a built-in profile draft |
| `PUT /api/control-center/schedule/draft` | Save schedule JSON draft with profile, timezone, and cycle list |
| `POST /api/control-center/schedule/validate` | Validate the saved draft |
| `POST /api/control-center/schedule/activate` | Activate the validated draft using `{"confirm":true}` |
| `DELETE /api/control-center/schedule/draft` | Cancel pending schedule |
| `PUT /api/control-center/concurrency` | Set `{"maximum":1..4}` |
| `GET /api/control-center/publishing` | Read current manual policy and automatic-publishing gate |
| `GET /api/control-center/resources` | Read resource and concurrency snapshot |

Publishing queue, approval, OAuth, and notification contracts remain in Stage 3.22's `/api/stage-3-22/...` endpoints. They are not yet surfaced as complete controls in the Stage 3.23 dashboard.

## Dashboard and screenshot

The dashboard panel was reloaded and inspected in the browser. The captured session screenshot shows:

- `CONTROL AUTH REQUIRED`
- Active mode and active profile not loaded
- Draft mode and schedule actions visible
- `manual_approval` and automatic publishing disabled
- Resource values not loaded

This is the expected fail-closed state because `CRAZZYDOCS_CONTROL_TOKEN` is not configured. The screenshot was captured as a session attachment, not saved as a repository image. Protected control APIs were tested separately using synthetic tokens and isolated test databases.

The current UI is a minimal foundation, not the complete specification: the custom schedule editor is raw JSON rather than visual cycle cards; publishing history/approvals, OAuth status, email configuration/history, retry controls, and notification status are not integrated; control-center state is not pushed through WebSockets and requires manual refresh. No real credentials were entered.

## Testing

Focused command:

```powershell
$env:PYTHONPATH = 'backend'
python -m pytest backend\tests\test_stage_3_23_control_center.py backend\tests\test_stage_3_22_integrations.py backend\tests\test_stage_3_9cd_scheduler.py -q
```

Result: **25 passed, 2 warnings**. The warnings are the existing `pytest-asyncio` loop-scope deprecation and Pydantic class-based `Config` deprecation. These are focused results, not a complete repository test run.

Additional verification:

- Pylance syntax checks passed for all seven changed Python source/test files.
- `git diff --check` passed; Git reported existing LF/CRLF conversion warnings only.
- Subsequent focused scheduler runs used a temporary SQLite database and created no new application records.

The Stage 3.22 report records one broader regression failure in `test_scenario_3_interrupted_rendering`; it was not rerun or resolved here.

## Data integrity and unexpected test side effect

Read-only counts at final verification:

| Store | Rows |
|---|---:|
| `persistent_jobs` | 5 |
| `publishing_ledger` | 0 |
| `publishing_ledger_archive` | 253 |
| `youtube_upload_state` | 0 |
| `deleted_video_audit` | 1 |

The earlier Stage 3.22 report recorded four `persistent_jobs`. An initial run of the legacy scheduler tests used the live application database and inserted the synthetic row `cycle_2_20261110_0600` (`COMPLETED`, stage `RESEARCH`). The test fixture was corrected to isolate its database. The row remains untouched because database cleanup is prohibited; it must not be interpreted as a real production. The publishing ledger, archive, deleted-video audit, and media were not modified by the Stage 3.23 work.

## Safety state

| Control | Observed state |
|---|---|
| Windows production scheduler task | Disabled |
| Embedded scheduler | Disabled; in-process scheduler not running |
| Production worker processes | None found |
| Database jobs in `RUNNING`/`RETRYING` | 0 |
| Active schedule profile | `FOUR_CYCLES_DAILY` |
| Schedule timezone | `Asia/Kolkata` |
| `SINGLE_DAILY` | Inactive |
| Operating mode | `IDLE` (safe default; no active mode setting persisted) |
| Publishing policy | `manual_approval` |
| Automatic publishing | Disabled; not available through Stage 3.22 policy |
| Uploads, OAuth flows, external email | None initiated |
| Commits or pushes | None |

The dashboard health task remains running and was not restarted. No production schedule was activated, and no scheduler catch-up/start was triggered.

## Security, readiness, and limitations

- Control APIs fail closed without `CRAZZYDOCS_CONTROL_TOKEN`; the dashboard cannot presently load or modify protected state.
- Stage 3.22 APIs protect their local controls, but legacy endpoints remain outside the control-token dependency. Do not expose the service to untrusted networks.
- OAuth and provider behavior have not been live-verified. No credentials or secrets are included in this report.
- Upload duplicate rejection, QC-failure blocking, mode changes during an active upload, scheduler restart recovery, and complete failure-notification wiring still need focused tests.
- Gaming Mode only prevents new starts; it does not gracefully pause an active renderer or reprioritize interactive apps.
- Dashboard publishing controls, visual custom-cycle editing, and real-time control-center updates remain incomplete.
- The full repository test suite was not run.

The detailed 18-item decision is in `PHASE_5_STAGE_3_23_AUTOMATIC_PUBLISHING_READINESS.md`. Gate 18 fails due to the synthetic database row; other gates remain blocked. Accordingly, the final controlled activation sequence was not run. Stage 3.24 readiness is **not established**.

