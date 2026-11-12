# Stage 3.23 Architecture Audit

## Scope and preservation

This audit covers the existing dashboard, FastAPI routes, local control authentication, SQLite settings and production records, APScheduler profiles, worker lifecycle, Stage 3.22 YouTube/email services, and the Stage 3.21S retention tooling. The Stage 3.23 changes extend those systems; they do not add a second dashboard, publishing ledger, or notification store.

The working tree was already heavily modified by Stages 3.21N-S and 3.22. At the audit snapshot it contained 35 tracked modified paths and 46 untracked paths. Those changes were preserved; no reset, stash, broad formatting, commit, or push was performed. The Stage 3.23-specific code and reports are additive.

## Existing architecture

| Area | Existing implementation | Stage 3.23 finding |
|---|---|---|
| Dashboard and API | FastAPI application in `backend/app/main.py`; the existing HTML dashboard is served from `backend/app/templates/dashboard.html`. The local dashboard task is separate from the production scheduler task. | Extend the existing dashboard and routes. The dashboard process is running locally; it does not imply that the production scheduler is running. |
| Settings and persistence | SQLAlchemy models in `backend/app/database/db.py`; `StudioSettingModel` is an existing key/value store. Production history and publishing records are in separate existing tables. | Mode, schedule drafts, schedule activation, and concurrency use the existing key/value store. No schema migration was added. |
| Scheduler | `backend/app/scheduler/scheduler.py` and `schedule_profiles.py`; the default profile is `FOUR_CYCLES_DAILY`. Embedded scheduler startup is opt-in. | New starts are gated on operating mode and configured capacity; cron jobs use per-cycle weekdays. Explicitly activated profiles can be restored at startup. |
| Worker | `backend/app/workers/job_manager.py` runs the persistent production state machine and checkpoints job progress. | A mode/capacity check defers a new job before pipeline work. This does not pause an already-running render or provide a graceful FFmpeg checkpoint/pause mechanism. |
| YouTube | Stage 3.22 `YouTubePublisherService` and `YouTubePublishingEngine` use the existing `publishing_ledger`, resumable upload-state sidecar, and explicit per-upload approval. | The Stage 3.22 service remains the only upload path. Its policy rejects automatic publishing; the Stage 3.23 status endpoint reports automatic publishing disabled. |
| Notifications | Stage 3.22 extends existing persistent notification services and history. | No parallel notification history was added. Email/provider calls were not made during this stage. |
| Retention | Stage 3.21S provides a dry-run-first retention utility and quarantine workflow. | No media cleanup, restore, or retention action was performed. |

## Stage 3.23 changes

- `backend/app/services/control_center_service.py` persists an active mode, a pending mode draft, schedule draft/validation/activation state, bounded change history, and production concurrency.
- `backend/app/scheduler/schedule_profiles.py` supports active weekdays and an explicitly installed runtime profile. The canonical `FOUR_CYCLES_DAILY` timing remains fixed by validation.
- `backend/app/scheduler/scheduler.py` defers scheduled work outside `PRODUCTION`, applies concurrency limits, and configures weekday-aware production and publish triggers.
- `backend/app/workers/job_manager.py` applies the mode and capacity gates before running a new production pipeline.
- `backend/app/main.py` restores only an explicitly activated schedule, adds control APIs and a resource snapshot, and applies the existing loopback/bearer-token guard to the new control routes.
- `backend/app/templates/dashboard.html` adds a mode selector, pending schedule editor, concurrency control, resource summary, and a visible manual-approval/automatic-publishing-disabled status.
- `backend/tests/test_stage_3_23_control_center.py` covers mode drafts, schedule validation/activation, concurrency, and authenticated control APIs. `backend/tests/test_stage_3_9cd_scheduler.py` now uses an isolated SQLite database and tests non-production scheduler deferral.

No schema migration was needed. Pending schedule drafts are not applied at startup; only a previously activated schedule is restored.

## API contract added

Every `/api/control-center/...` route uses `require_local_control`: loopback caller plus configured bearer token. The control token is not configured in the observed environment, so control operations fail closed until an operator configures it.

| Method and route | Purpose | Request |
|---|---|---|
| `GET /api/control-center/state` | Read active mode, pending mode, active/draft schedule, concurrency, and recent change history | — |
| `POST /api/control-center/operating-mode/draft` | Save a pending mode | `{"mode":"IDLE"}` |
| `POST /api/control-center/operating-mode/activate` | Activate the matching pending mode after explicit confirmation | `{"mode":"IDLE","confirm":true}` |
| `DELETE /api/control-center/operating-mode/draft` | Cancel a pending mode | — |
| `GET /api/control-center/schedule/preset/{profile_name}` | Load a `FOUR_CYCLES_DAILY`, `SINGLE_DAILY`, or `CUSTOM` schedule draft template | — |
| `PUT /api/control-center/schedule/draft` | Save schedule JSON as an unvalidated draft | Profile name, timezone, and cycle list |
| `POST /api/control-center/schedule/validate` | Validate the saved draft | `{}` |
| `POST /api/control-center/schedule/activate` | Activate a validated draft after explicit confirmation | `{"confirm":true}` |
| `DELETE /api/control-center/schedule/draft` | Cancel the pending schedule | — |
| `PUT /api/control-center/concurrency` | Set production concurrency from 1 through 4 | `{"maximum":1}` |
| `GET /api/control-center/publishing` | Read policy and automatic-publishing gate state | — |
| `GET /api/control-center/resources` | Read CPU, RAM, disk, GPU/process availability, active jobs, and concurrency | — |

The existing Stage 3.22 APIs remain responsible for OAuth, upload queue/history/status, per-upload approval, and notification configuration/history. The Stage 3.23 publishing endpoint is status-only; it does not enable automatic uploads.

## Schedule validation

Draft validation rejects unknown timezones, invalid times/weekdays, duplicate cycle identifiers, publish-slot conflicts, overlapping production windows, and cycle windows shorter than six hours or longer than 24 hours. Midnight rollover is represented explicitly. The four-cycle preset cannot be edited through validation. Activation requires a previously saved and validated draft plus explicit confirmation. Startup does not activate a draft.

The interface currently uses a JSON schedule editor, not the requested visual cycle editor. Although the service accepts enabled cycles and preserves their display order in a draft, the dashboard does not yet provide dedicated add/remove/reorder/rename controls.

## Runtime and safety observations

Read-only checks observed:

- `CrazzyDocs-Production-Scheduler`: **Disabled**.
- Embedded scheduler environment setting: `false`; in-process scheduler: **not running**.
- No matching scheduler or production-worker process; no `RUNNING` or `RETRYING` production jobs.
- Active runtime profile: `FOUR_CYCLES_DAILY`, timezone `Asia/Kolkata`; `SINGLE_DAILY` is inactive.
- `PUBLISHING_MODE`: `manual_approval`.
- Operating mode resolves to `IDLE` because no explicit active mode is persisted.
- `CRAZZYDOCS_CONTROL_TOKEN` is not configured. The dashboard shows **CONTROL AUTH REQUIRED** and does not load protected control state.
- The separate `CrazzyDocs-Health-Dashboard` task remains running.

The resource endpoint uses Windows APIs for CPU and memory and reports disk usage. GPU and rendering-process metrics explicitly return `NOT AVAILABLE`; no synthetic GPU/process values are reported. Production concurrency is configurable, but Gaming Mode does not currently reprioritize interactive processes or pause active rendering. It only prevents new starts.

## Integration gaps

1. The dashboard does not yet expose OAuth status, upload queue/history/progress, notification history/configuration, publishing target-time settings, retry policy, upload concurrency, or approval actions. These remain in Stage 3.22 APIs and are not combined into the control-center UI.
2. The JSON editor is not a visual custom-cycle editor.
3. Control-center state is refreshed manually, not through the existing real-time/WebSocket path. No new WebSocket event integration was added.
4. No graceful render pause/checkpoint operation or interactive-process priority policy is implemented.
5. The local token is unset, preventing authenticated UI/API verification against the running dashboard.
6. Existing legacy APIs outside the local control dependency remain unauthenticated, as documented in the Stage 3.22 audit. Keep the service local; do not expose it through an untrusted interface.
7. Automatic publishing remains blocked by Stage 3.22 policy and the readiness findings in `PHASE_5_STAGE_3_23_AUTOMATIC_PUBLISHING_READINESS.md`.
8. A first, non-isolated scheduler test run created a synthetic `persistent_jobs` row. The test was corrected to use an isolated database, but the row was deliberately not deleted under the no-database-cleanup constraint. Details are in the control-center report and readiness assessment.

## Verification references

- Focused test command and results: `25 passed` across `backend/tests/test_stage_3_23_control_center.py`, `backend/tests/test_stage_3_22_integrations.py`, and `backend/tests/test_stage_3_9cd_scheduler.py`.
- Pylance syntax checks: all seven changed Python source/test files checked without syntax errors.
- `git diff --check`: passed; Git emitted only existing line-ending conversion warnings.
- No full repository test suite was run.
- A browser screenshot of the control-center panel was captured in the session. It shows the token-required state, the current static manual-approval status, and unloaded protected state; it is not a repository image file.

