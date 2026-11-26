# Stage 3.36: Autopilot and operational autonomy

**Status:** implemented and tested. Deployment and live verification are recorded in §13. Autopilot is **OFF** by default and stays OFF after deployment: no level was raised, no standing approval was granted, and no kill switch was changed.

## 1. Architecture audit (before implementation)

| Existing component | Where | Reused for |
|---|---|---|
| 3.32B control plane: closed action registry, min(global, category) levels, approval queue (PENDING … CANCELLED), HMAC-signed payload-bound approvals, standing approvals, kill switches, execution leases, circuit breaker, hash-chained append-only audit | `autonomy/control_plane.py`, `autonomy/registry.py` | Every Autopilot dispatch: propose → standing approval → `execute` |
| Phased activation (3.32I) and observation tick | `autonomy/activation.py`, `autonomy/reports.py` | Unchanged; it never touches the new `production` category |
| 3.32G Autopilot overview / recommendation bridge | `autonomy/autopilot.py` | Unchanged; the new controller panel sits beside it |
| 24-cycle cron jobs, `run_cycle_generation`, `dispatch_eligible_recoveries`, recovery windows | `scheduler/scheduler.py`, `schedule_profiles.py` | The only production execution path; Autopilot calls the same functions |
| Bounded retry policy (classification, backoff, re-arm, quota-deferred handoff) | `scheduler/retry_policy.py` | All recovery; Autopilot never retries on its own |
| Worker registry, heartbeat pump, PID-verified stall detection | `scheduler/worker_registry.py` | Single renderer; "old heartbeat alone is never proof" |
| Resource gate, concurrency limit (1), operating mode, production pause/stop | `services/control_center_service.py` | Decision engine inputs |
| YouTube quota ledger | `services/youtube_quota.py` | Quota constraints |
| Daemon recovery loop, controlled restart | `scheduler/daemon_runtime.py` | Hosts the controller tick |
| Community draft schedule (3.32E) | `community/service.py`, `community_draft_tick` | Untouched; independent |

**Finding:** before this stage, "Autopilot" (3.32G–J) was an observation, recommendation and approval layer. Nothing coordinated production itself, and the registry prohibited autonomy from triggering production. No duplicate scheduler, queue, permission system, worker or recovery mechanism was needed. Stage 3.36 adds one controller, one ownership record and one decision engine on top of these components.

## 2. New components

| File | Purpose |
|---|---|
| `autonomy/production_controller.py` | State machine, preconditions, roles, ownership handoff, dispatch, restart recovery, dashboard status |
| `autonomy/production_ownership.py` | One-row production ownership record, changed only by compare-and-swap; deferred occurrences |
| `autonomy/decision_engine.py` | Pure decision function with explanations |
| `autonomy/production_handlers.py` | Handlers for the two production actions (they refuse outside the daemon's controller) |
| `registry.py` (changed) | New category `production`, two permitted actions; `production.cycle.create` reworded (still prohibited) |
| `scheduler.py` (changed) | Ownership handoff in `run_cycle_generation`; an in-process dispatch guard; `owner_token` / `only_job_id` on `dispatch_eligible_recoveries` |
| `daemon_runtime.py` (changed) | `autopilot_controller_tick()` after each recovery check (every 45 s) |
| `control_plane.py` (changed) | `autopilot` is a reserved (machine) identity |
| `main.py` (changed) | `GET /api/autopilot/controller`, `POST …/start`, `POST …/stop`; the generic execute endpoint refuses `production.*` |
| `templates/dashboard.html` (changed) | Autopilot tab → *Autopilot controller (production)* panel |
| `tests/test_autopilot_controller_336.py` | 40 focused tests |

New tables are created on first use, in the same `studio.db`:
- `autopilot_controller_state`
- `autopilot_transitions`
- `autopilot_decisions`
- `autopilot_dispatches`
- `autopilot_production_ownership`

## 3. State machine

| From | To | Trigger |
|---|---|---|
| OFF | STARTING | operator **Start** (dashboard, audited) |
| STARTING | ON | the daemon's controller verified every precondition |
| STARTING | BLOCKED | a precondition failed (the exact checks are stored in the transition) |
| STARTING | OFF | operator **Stop** before verification |
| ON | STOPPING | operator **Stop** |
| ON | BLOCKED | a mandatory safety check failed, the mode fell to OFF, ownership could not be acquired, or 3 controller errors occurred within an hour |
| STOPPING | OFF | no Autopilot dispatch in flight; ownership returned to the scheduler |
| BLOCKED | STARTING | operator **Start** (re-validates) |
| BLOCKED | OFF / STOPPING | operator **Stop** (STOPPING while Autopilot still holds ownership) |

Any other transition raises `InvalidTransition` (HTTP 409) and is audited as `AUTOPILOT_TRANSITION_REJECTED`. Every transition is written both to `autopilot_transitions` and to the hash-chained audit (`AUTOPILOT_STATE_CHANGED`), each in the same transaction. State changes use compare-and-swap on a version column.

**Start preconditions** (any failure → BLOCKED):
- `controller_in_scheduler_daemon`
- `scheduler_running`
- `schedule_loaded`
- `autonomy_configuration`
- `autonomy_mode` (production mode ≥ OBSERVE)
- `prohibited_actions_gated`
- `no_competing_owner`
- `single_controller`
- `single_worker` (live workers ≤ the concurrency limit)
- `audit_chain`

While ON, every tick re-checks the same conditions; the audit chain is re-checked every 30 minutes.

## 4. Autonomy permission model

- The operational state and the autonomy mode are separate.
- **Mode** = min(global level, `production` category level), using the 3.32B rule. The `production` category defaults to OFF. 3.32I phased activation never changes it; only an operator change in Autonomy Control does.

| Mode | Controller role | What it does |
|---|---|---|
| OFF | none | Start is BLOCKED |
| OBSERVE | OBSERVER | Monitors and records decisions. The scheduler keeps ownership. |
| RECOMMEND | ADVISOR | Also proposes `operator.notify` escalations (pending approval). Never executes. |
| APPROVED_EXECUTION | EXECUTOR, only with active standing approvals for **both** production actions, no kill switch and no open breaker; otherwise ADVISOR | Owns production and dispatches |

Two permitted actions (closed registry; risk LOW; STANDING_OR_EXPLICIT approval; 1 h request TTL; retry limit 0; 3 h timeout):
- `production.scheduled_cycle.dispatch`: payload `{cycle_id, job_id, scheduled_start_ist}`, daily limit 30.
- `production.recovery.dispatch`: payload `{job_id, status}`, daily limit 24.

Approval is signed over the payload hash: a changed payload invalidates it (back to PENDING). Expiry, the execution lease, the per-action circuit breaker (3 failures → 60 min), the daily limits and the audit all come from the control plane. Unknown action types are always denied.

## 5. Controller lifecycle and dispatch

1. Each daemon recovery-loop iteration (every 45 s) runs one `tick()`.
   - OFF costs one read; nothing else happens.
   - The tick never raises into the loop.
   - A dispatch runs as a background task, so the loop is never blocked.
2. An EXECUTOR tick:
   - acquires ownership (compare-and-swap);
   - builds a snapshot (jobs, workers, deferred occurrences, resources, disk, quota, operating mode, production control);
   - calls `decision_engine.decide`;
   - records the decision when it changes (at least every 30 minutes);
   - if the decision says proceed and nothing is in flight, starts one dispatch.
3. A dispatch:
   - `ControlPlane.propose` → standing approval (signed) → `ControlPlane.execute`;
   - the handler re-checks ownership and the occurrence (current, inside its recovery window);
   - it awaits the **existing** `run_cycle_generation(cycle_id, owner_token=…)`, or `dispatch_eligible_recoveries(owner_token=…, only_job_id=…)`, on the daemon's own event loop;
   - verification: the job reached a safe state and no worker for it is still running.
4. The result is recorded in `autopilot_dispatches` and as lifecycle events (`AUTOPILOT_DISPATCH_STARTED` / `FINISHED`); the last success is recorded too. Analytics evidence is recorded by the existing pipeline, which is unchanged.
5. The next job starts only after the previous one reaches a safe state. The single-renderer rule holds: nothing is dispatched while a job is RUNNING/RETRYING/PAUSED with a live worker, a quiet RUNNING job is unverified, or any `run_cycle_generation` is in progress in the process.

## 6. Scheduler ownership and handoff

- **Default owner: SCHEDULER.** Everything runs exactly as before 3.36.
- **Handoff (acquire).**
  - Only in the EXECUTOR role.
  - Active jobs are never interrupted: a running job is simply waited for.
  - The cron jobs keep firing on the unchanged 24-cycle schedule. When Autopilot owns production, `run_cycle_generation` (without a token) records the fired occurrence as *deferred* (lifecycle `CYCLE_DUE_HANDED_TO_AUTOPILOT`) and returns DEFERRED.
  - The recovery loop still reconciles, expires, re-arms and completes queue handoffs, but leaves recovery dispatch to the controller.
- **Release.**
  - Happens only when no Autopilot dispatch is in flight.
  - Deferred occurrences that were not dispatched are **replayed through the normal scheduler path** inside their own recovery windows, so a handback never loses a cycle.
- **Race and duplicate protection.**
  - Ownership changes by compare-and-swap.
  - A claim counts only while its owner process is alive (PID check) and, for the daemon's own process, while the controller heartbeat is fresh (10 min). A lapsed claim falls back to the scheduler.
  - An in-process guard allows only one `run_cycle_generation` per job id at a time, on both paths.
  - The existing worker registry and job-status checks still apply.
  - A dispatch token is accepted only from the live claim of the same process.
  - The handlers refuse to run outside the daemon's controller, and the dashboard's generic execute endpoint refuses `production.*`.
- **Fail safe.** Any error reading ownership means the scheduler keeps ownership. Production continues on the proven path.

## 7. Recovery behaviour

| Failure | Handling |
|---|---|
| Worker failure / stalled job | The existing `worker_registry.is_stalled`: a job counts as stalled only if its worker process is gone **and** its heartbeat is stale. The decision engine reports a quiet RUNNING job as a bottleneck and never reclaims it. A verified-stalled job is a recovery candidate resumed from its checkpoint by the existing dispatcher. |
| Renderer, asset-download, voiceover, script- or quality-validation failure | The existing `retry_policy` classification and backoff (deterministic failures need a change; transient ones are retried with a bound). The decision engine classifies repeated failures (RENDERER, ASSET_DOWNLOAD, VOICEOVER, SCRIPT_VALIDATION, QC_TOPIC_NARRATION, QUALITY_VALIDATION, RECOVERY_WINDOW, QUOTA). It recommends a corrective review after 3 failures in the last 5 jobs, and escalates after 3 consecutive failures. QC is never bypassed. |
| Quota exhaustion | Reported as a constraint. Renders continue, and the existing queue handoff waits for the Pacific reset. Uploads are never forced. |
| Resource exhaustion | CPU > 92 %, RAM > 90 % (the existing gate) or free disk < 3 GB → HOLD with backoff. Low disk is escalated. Autopilot never deletes data. |
| Interrupted controller | The first tick in a new process: a claim of a dead process is released and its deferred occurrences are replayed. A persisted ON → BLOCKED → STARTING (every precondition re-checked). STOPPING → OFF. A live other controller → BLOCKED. |
| Controller errors | Exponential backoff (60 s → 15 min); 3 errors within an hour → BLOCKED |
| Duplicate execution | Ownership compare-and-swap, the in-process dispatch guard, the worker registry, the control-plane lease and claim, and "dispatched once" tracking per occurrence |

All retries are bounded: production actions have retry limit 0, and recovery stays bounded by `retry_policy` and the recovery windows. The controller never extends either.

## 8. Resource and quota governance

- At most one renderer (Control Center concurrency 1 plus the controller's own single-renderer gate).
- The existing CPU and RAM thresholds apply, plus a free-disk floor.
- YouTube quota is read from the local `QuotaLedger` and Google's exhaustion signal. The controller makes no YouTube API call of its own.
- Retry storms are prevented by the decision cadence (45 s tick, at most one dispatch in flight), the controller backoff, the circuit breaker and the daily limits.
- No paid service, cloud rendering or new API is used.

## 9. External action restrictions

The following stay permanently prohibited for autonomous execution (registry `PROHIBITED`, enforced by the control plane at propose, approve and execute):
- public video and Community publishing (`youtube.video.publish`, `youtube.community.publish`);
- schedule and configuration changes (`production.schedule.change`, `production.config.modify`);
- daemon and dashboard restarts;
- data deletion;
- destructive git and branch changes;
- creating extra cycles (`production.cycle.create`) and launching extra workers (`render.worker.launch`);
- recovery-deadline changes and QC or duplicate-check bypasses;
- self-policy changes and external messages.

Autopilot dispatches the same pipeline as the scheduler. Uploads follow the operator's existing publishing policy (currently `automatic_private_scheduled`); Autopilot never changes visibility or the policy. Community publishing remains blocked by the YouTube Posts API pilot eligibility and the missing OAuth authorization; this stage did not touch it.

## 10. Community automation independence

The controller, the decision engine and the handlers do not import the Community package. The `community_draft_tick` APScheduler job is unchanged and runs whatever the Autopilot state is. Community failures are contained in `run_community_draft_tick`. Community state, retries and kill switch are separate. Tests: `test_controller_code_never_touches_community_scheduling`, `test_community_tick_runs_regardless_of_autopilot`.

## 11. Dashboard operation (Autopilot tab → *Autopilot controller (production)*)

**Shows:**
- operational state, autonomy mode, global and per-category levels, and role;
- controller ownership and the occurrences handed to it;
- current job, stage, worker heartbeat and renderer status;
- CPU, RAM, disk and concurrency;
- upload quota;
- the latest decision with reasons, rejected candidates, bottlenecks, recommendations and escalations;
- recent decisions, dispatches, recovery activity, blocked actions, pending approvals;
- last successful production;
- Autopilot event history.

**Controls:**
- **Start Autopilot** and **Stop Autopilot** (operator name, reason, confirmation; CSRF, same origin, localhost);
- **Set production level** (the existing Autonomy Control permission change).

Approvals are reviewed in Autonomy Control. STARTING and STOPPING are shown until the daemon has verified the transition, so the dashboard never implies success early.

## 12. Operator instructions

1. **Observe first.**
   - Set the global level ≥ OBSERVE (it is OBSERVE today) and *Set production level* = OBSERVE.
   - **Start Autopilot.** It becomes ON as OBSERVER: it records what it would do while the scheduler keeps producing.
2. **Execute.**
   - Create `backend/data/autonomy_startup.json` with `{"autonomy_execution": "enabled"}`. It is read at daemon start, so a daemon restart is needed.
   - Set global and production to APPROVED_EXECUTION.
   - Grant standing approvals for `production.scheduled_cycle.dispatch` (max 24–30 per day) and `production.recovery.dispatch` in Autonomy Control.
   - The role becomes EXECUTOR on the next tick, and ownership transfers when no Autopilot dispatch is in flight.
3. **Stop.**
   - **Stop Autopilot** lets an in-flight production finish, then returns ownership to the scheduler (STOPPING → OFF).
   - The dashboard kill switch, or lowering the level, demotes the role at once: ownership is released after the in-flight job, and production continues through the scheduler.
4. **BLOCKED** shows the exact reason. Fix the cause, then press Start again (all checks are re-run).

## 13. Test results, deployment and live verification

See the "Stage 3.36" row in `docs/PROJECT_HANDOVER.md` for commit, backup and deployment evidence.

- **Focused:** `backend/tests/test_autopilot_controller_336.py` has 42 tests covering all 22 required areas, plus source safety and the dashboard panel. All pass. The tests were verified to import the worktree modules, not the production checkout.
- **Existing suites:** the autonomy suites (3.32B/C/D/G/HIJ) pass.
  - The 3.32B source-safety test now excludes the four 3.36 coordination modules, which call scheduler entry points by design. They have their own stricter allow-list test.
  - Two tests were adjusted to code moves: the email test now inspects `_run_cycle_generation`; the 3.32G dashboard region is unchanged, because the new script sits after it.
  - The new in-process dispatch guard keeps the established `CONCURRENT_ACTIVE_RUN` reason code.
- **Full suite (2026-11-27):** 1788 passed, 173 skipped, 4 failed. All 4 are the known baseline: `test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, and two in `test_youtube_branding`.

## 14. Known limitations

- Autopilot adds coordination, not new production capability. With Autopilot OFF or OBSERVE, behaviour is identical to Stage 3.35.
- EXECUTOR needs the operator-created `autonomy_startup.json` and a daemon restart (the existing 3.32B startup kill switch). Neither was done in this stage.
- Manual operator runs (`/api/pipeline/run`, `/api/jobs/run`) are not routed through Autopilot. They stay subject to the existing concurrency limit.
- The decision log is deduplicated, not pruned (Autopilot never deletes data). It grows only when decisions change or every 30 minutes while ON.
- The controller heartbeat lapse (10 min) hands production back to the scheduler path if the daemon's loop stalls. Duplicate protection then relies on the in-process guard and the worker registry.
