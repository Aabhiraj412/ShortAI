# Stage 3.21O-R — Validation Worker and IST Reconciliation

## Outcome

**Bhopal classification: `PROCESS_TERMINATED_STALE_STATUS`.** The validation process is not alive. The old dashboard state came from a persisted JSON observability record, not a running worker.

This reconciliation made three minimal dashboard/observability changes:

1. A persisted `starting` or `running` record is active only if its UTC `last_updated` heartbeat is no more than 300 seconds old. Older records are exposed as `STALE` in dashboard views without changing the record on disk.
2. The dashboard now converts absolute API timestamps to `YYYY-MM-DD HH:mm:ss IST` before rendering history, publishing, recovery, activity, and archive rows. Storage/API transport remains UTC with offsets.
3. The dashboard publishing-mode label now comes from `/api/dashboard/overview`; the literal `Mode: Automatic Public` placeholder was removed.

No validation, production, FFmpeg, scheduler, upload, database, or publishing-ledger operation was started. No stale run was killed or rewritten.

## A. Bhopal validation forensics

| Evidence | Result |
|---|---|
| Run ID / topic | `val_3_21o_history_20261111_203000_1` / Bhopal disaster |
| Last persisted progress | `2026-11-11T14:11:59.855359+00:00` = `2026-11-11 19:41:59 IST` |
| Last progress event | Asset progress after animated map #1, overall 45%, 1 acquired image, 1 generated image, 1 animation |
| Last emitted file | `assets/animations/anim_live_bhopal_disaster_b04_animated_map.mp4`, modified `2026-11-11 19:42:08 IST` |
| Progress/event file | `reports/progress_events_val_3_21o_history_20261111_203000_1.jsonl`, modified `2026-11-11 19:41:59 IST`; no completion, failure, or abort event |
| Original worker/PID/parent/children | **Not recoverable after termination.** No current process command line contains the run ID or runner name. |
| Current Python worker | Only dashboard `pythonw.exe` PID 25960; no run worker child process. |
| FFmpeg | No `ffmpeg.exe` process. |
| Process progress/CPU/deadlock | **Not applicable after termination.** There is no original process to inspect. |

The run ceased after its first observed animation progress and a later Bhopal animation file, with no terminal observability update. It is not an active or stalled process. Its persisted status was incorrectly displayed as live because it was never reconciled against heartbeat freshness.

## B. Worker-state root cause and correction

Before this correction, `ProductionObservabilityStore.get_active_production()` returned the newest record whose `overall_status` literally equalled `running`, with no timestamp threshold or process evidence. `ProductionHealthDashboard.get_reconciled_operational_states()` then included that active/displayed/history record and converted raw `running` to `validation_worker_state: RUNNING`. The same stale record therefore produced all of the following consistent-but-wrong signals:

- `VALIDATION ACTIVE`
- production lifecycle `IDLE`
- validation worker `RUNNING`
- FFmpeg `IDLE`
- video rendering `0%`

Production and validation remain separate: production state comes from `persistent_jobs` and production observability; validation state comes only from validation-context observability records. The correction keeps that boundary. It makes freshness a prerequisite for the `RUNNING` label, marks stale historical records `STALE` in the response only, and lets the overall operational state return to `IDLE` when no fresh worker exists.

## C. IST audit

### Verified correct before changes

- Scheduler calculations use `Asia/Kolkata` and `FOUR_CYCLES_DAILY`.
- Header clock used `Intl.DateTimeFormat(... timeZone: 'Asia/Kolkata')`.
- Observability records use timezone-aware UTC ISO timestamps such as `2026-11-11T14:11:59.855359+00:00`.
- Dashboard overview already emits cycle and next-run strings with `IST`.

### Root cause and correction

The dashboard history, publishing, recovery, activity, and archive tables used string replacement and truncation (`replace('T', ' ').slice(0, 19)`). That displayed UTC wall-clock values without an offset/label and did not convert them to IST. `formatIstTimestamp()` now parses the timestamp instant, formats it with `Asia/Kolkata`, and appends `IST`; invalid/missing data becomes `--`. The header clock now also displays a complete date/time in that format.

The daily summary service still writes `datetime.utcnow()` to some database fields. That is legacy naive-UTC persistence and should be migrated separately to aware UTC objects; it is outside the narrowly scoped display correction. The dashboard boundary now treats ISO-offset values correctly and does not add 5:30 to an already-IST time.

## D. Publishing-mode discrepancy

**Actual configured/effective mode: `manual_approval`.** Verified from the live settings endpoint, schedule profile, dashboard publishing endpoint, and the restricted `Literal["manual_approval"]` settings model. The production scheduler's upload branch only calls `execute_upload=True` when the mode equals `automatic_public`; with the current restricted runtime setting it passes `False`.

The dashboard label `Mode: Automatic Public` was a literal HTML placeholder at `dashboard.html` line 452. It did not reflect API state. The overview response now includes `publishing_mode`, and the dashboard renders its value. Historical documentation/source references to `automatic_public` remain, as do defensive branches that check for it, but no current runtime setting or live API response selects it.

## E. Verification and safety

| Check | Result |
|---|---|
| Bhopal final classification | **VERIFIED:** `PROCESS_TERMINATED_STALE_STATUS` |
| Bhopal process/FFmpeg absent | **VERIFIED** |
| Stale-worker detection source behavior | **VERIFIED by direct read-only call:** stale run is no longer active and is returned as `stale` |
| Dashboard reload | **VERIFIED:** existing dashboard scheduled task was restarted only after the dashboard-only change; it is listening on port 8000 as PID 13268. |
| Dashboard runtime state | **VERIFIED:** overview now returns validation `IDLE`, overall operational state `IDLE`, production `IDLE`, scheduler `DISABLED`, and `publishing_mode: manual_approval`. |
| Progress API | **VERIFIED:** `has_active: false`; Bhopal is returned as `stale` in displayed/history data. |
| IST presentation source | **VERIFIED:** served dashboard contains `formatIstTimestamp`; the former Automatic Public literal is absent. |
| Scheduler task / daemon | **VERIFIED disabled / stopped** |
| Production worker | **VERIFIED idle** |
| Publishing | **VERIFIED manual approval; active ledger count 0** |
| New render, FFmpeg, upload | **VERIFIED none started by this reconciliation** |

## Minimal-change scope

Modified source files:

- `backend/app/services/production_observability.py`
- `backend/app/main.py`
- `backend/app/templates/dashboard.html`

This report is the required audit artifact. Existing run data and progress JSON were preserved unchanged. No database or publishing-ledger update was made.

### Reload incident

The first controlled dashboard reload exposed an indentation error in the new history branch and exited with task result `1`. It was corrected immediately, source syntax was checked with a non-writing AST parse, and the dashboard was restarted successfully. This did not start a production worker, FFmpeg, scheduler daemon, validation run, or upload.
