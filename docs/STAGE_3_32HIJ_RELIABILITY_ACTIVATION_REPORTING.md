# Stages 3.32H, 3.32I and 3.32J: reliability, phased Autopilot activation and operational reporting

These stages build on the existing 3.32A–G components. There is no new scheduler, worker, approval system or control plane.

## 1. Stage 3.32H: production reliability

### 1.1 FFmpeg padding defect (rendering)

**Symptom:**
- `FFmpeg Priority B adaptation failed: [Parsed_pad_2] Padded dimensions cannot be smaller than input dimensions.`
- 9 occurrences in `logs/scheduler_daemon.log`.
- It was classified TRANSIENT, so every retry repeated it and burned recovery windows.

**Root cause:**
- The Priority B adapter (`rendering/explainer_animation_engine.py`) letterboxes a source animation into an **860 × 855** viewport: `scale=860:855:force_original_aspect_ratio=decrease,pad=860:855`.
- With a chroma-subsampled pixel format (yuv420p, negotiated for libx264), FFmpeg's `pad` filter rounds its target **down** to the chroma grid: 855 becomes 854.
- `scale` produced a frame 855 rows tall for every portrait or square source.
- 854 < 855, so FFmpeg refused the graph. The failure is deterministic for every such source; landscape sources happened to pass.

Reproduced with synthetic media (FFmpeg 9.0.2): the old filter fails for 320×480 and 400×400 sources and succeeds once the target is even.

**Fix (the renderer architecture, Lemo-Opuscar and all styles are unchanged):**
- New `rendering/ffmpeg_geometry.py`:
  - `validate_dimensions` rejects invalid geometry (zero, negative, fractional, boolean, above 8192);
  - `even_floor`;
  - `fit_and_pad_filter`: even targets, and `scale ... :force_divisible_by=2`, which rounds down, so the scaled frame always fits the pad;
  - `stderr_tail` bounds error text.
- Priority B adapter:
  - an even viewport (860 × 854) through `fit_and_pad_filter`;
  - a partial output is deleted on failure;
  - a timeout is reported as `timed out after 45s` (`subprocess.run` has already killed and reaped FFmpeg);
  - stderr is truncated to its tail.
- The two other scale/pad sites (`compositing/compositor.py`, `rendering/engine.py`, both 1080 × 1920) also force even scaling.
- `scheduler/retry_policy.py`: FFmpeg geometry errors (`padded dimensions cannot be smaller than input`, `not divisible by 2`, `invalid too big or non positive size`) are now **DETERMINISTIC**. They wait for a code change instead of burning retries; the existing change-required logic re-arms them after this deployment (new code revision).
- A test asserts that every `force_original_aspect_ratio=decrease ... pad=` in `backend/app/rendering` forces even scaling.

**Validated with synthetic media:**
- 7 sources (portrait, landscape, square, odd 333×501 and 501×333, 1081×1919, 2×2) × 5 targets (860×855, 1080×1920, 1920×1080, 1080×1080, 721×1281). The output is exactly the even target, in yuv420p.
- The real adapter with MP4 and GIF sources, including the overlay composition: always 1080 × 1920 yuv420p.
- Invalid media fails with a bounded error, leaves no partial output and no temp overlay; a simulated timeout is reported and cleaned up.

### 1.2 Dashboard health monitoring

**Symptom:**
- `startup_manager.log` held 534 "Dashboard not answering yet" events.
- Earlier logs (2026-11-17) show unhealthy restarts, and the restart cap was reached three times.

**Root cause:**
- The monitor probed `/api/dashboard/health`, a heavyweight aggregate: Windows scheduled-task queries, service status, database, disk, archive and ledger checks. It takes about 2 s and runs inside the event loop.
- The probe used a **2 s timeout**, and any timeout counted as "down".
- It also probed twice per loop.

**Fix:**

| Element | Implementation |
|---|---|
| Liveness | `GET /api/health/live`: answers from the event loop; no database, no Windows calls |
| Readiness | `GET /api/health/ready`: a read-only `SELECT 1` with a 3 s database timeout, run in the threadpool; returns 503 `not_ready` when it fails |
| Timeouts | liveness 5 s, readiness 10 s, configurable through `backend/data/health_monitor_config.json` (optional; values clamped) |
| States | `HEALTHY`; `DEGRADED` (slow, not ready or recovering); `UNAVAILABLE` (fewer than 3 consecutive liveness failures); `UNHEALTHY` (3 or more) |
| Hysteresis | 2 consecutive good probes before `HEALTHY` again |
| Restart rule | only when `UNHEALTHY` **and** no dashboard process holds the server lock, re-checked after the backoff. A slow or degraded dashboard, or a live but unresponsive process, is **never** restarted |
| Limits | max 3 restarts per hour with exponential backoff (5 s × 3ⁿ); suppressed restarts are recorded |
| Persistence | `logs/health_monitor_state.json` (current state, configuration, recent restarts) and `logs/health_monitor_events.jsonl` (every probe: latency, reasons, state, action; rotated at 5 MB) |
| Compatibility | a dashboard still on older code (404 on `/api/health/live`) is probed on the legacy endpoint with the 10 s timeout |

The boot readiness gate (`is_backend_healthy`) now uses a 10 s timeout.

**Process safety (verified, unchanged):**
- single-instance monitor and scheduler locks;
- 3.32G execution leases (owner pid, heartbeat; reclaim only when the owner is dead);
- FFmpeg runs with `subprocess.run(timeout=...)`, which kills and reaps on timeout.

A missing heartbeat never launches a renderer or restarts a production worker.

## 2. Stage 3.32I: phased Autopilot activation

Module: `autonomy/activation.py`. It is persisted in three tables:
- `autonomy_activation_state` (phase, start time, operator, reason, authorized actions, last readiness);
- `autonomy_activation_history` (every ADVANCED, RETREATED or REFUSED attempt, with blockers);
- `autonomy_observation_cycles`.

Every transition is also in the hash-chained audit as `ACTIVATION_PHASE_ADVANCED`, `ACTIVATION_PHASE_RETREATED` or `ACTIVATION_REFUSED`. Levels are changed only through `ControlPlane.set_level`, which audits `PERMISSION_CHANGED`.

| Phase | Control-plane levels | What happens |
|---|---|---|
| OFF | all OFF | nothing (default) |
| 1 OBSERVE | global and diagnostics, recommendations, reporting, self_healing, notifications at OBSERVE | an observation cycle every 30 minutes records observations (no proposals, no execution, no production change) |
| 2 RECOMMEND | the same categories at RECOMMEND | proposals queue for explicit approval; nothing executes |
| 3 RESTRICTED_EXECUTION | global APPROVED_EXECUTION; **diagnostics and reporting only** at APPROVED_EXECUTION; others RECOMMEND | approved read-only diagnostics and reports may execute, with every 3.32B/G gate |
| 4 EXPANSION | additionally the categories of explicitly authorized low-risk actions | only after evidence; approval-required and prohibited actions stay gated |

`community_drafts` is never touched: it keeps its own Community configuration and kill switch.

**Readiness checks** run automatically on every advance and on the dashboard.

Every phase:
- the transition is sequential;
- the audit chain verifies;
- the required permanently gated actions are still gated (publishing, external communication, financial, OAuth, destructive git, data deletion, process restarts, self-policy);
- `PRAGMA quick_check` passes;
- no open circuit breakers and no stale leases;
- a fresh (under 10 min) health-monitor state that is HEALTHY or DEGRADED;
- the 3.32H geometry fix is active;
- no operator kill switch.

**RECOMMEND** (in addition):
- at least 48 h in OBSERVE;
- at least 48 observation cycles, none with errors;
- dashboard liveness at least 95 % with no restarts;
- false-positive rate (rejected / decided recommendations) at most 50 % once 5 or more are decided; informational below that.

**RESTRICTED_EXECUTION** (in addition):
- at least 7 days in RECOMMEND;
- at least 5 human approve or reject decisions;
- the availability and false-positive checks;
- `autonomy_startup.json` present (operator-created; the startup kill switch inactive);
- no kill switch.

**EXPANSION** (in addition):
- at least 14 days in RESTRICTED_EXECUTION;
- at least 20 successful executions and none failed;
- the startup switch is inactive.

**Authorization:**
- **OBSERVE:** an operator, a reason and a confirmation.
- **RECOMMEND and above:** also the exact phrase `AUTHORIZE <PHASE>`.
- **Phases 3 and 4:** an explicit list of action types, limited to `diagnostic.readonly.run` and `report.operational.generate` for phase 3, or the eligible and approval-required actions of the phase categories for phase 4.

**Guarantees:**
- Activation never grants standing approvals and never creates `autonomy_startup.json`.
- Nothing advances automatically: the observation tick never calls `advance`.
- Retreat to any lower phase is always allowed.
- A manual level change made outside activation is reported as **level drift**.

**API** (local only; writes need same-origin, JSON, the CSRF token and a validated operator):
- `GET /api/autopilot/activation`
- `GET /api/autopilot/activation/readiness?phase=`
- `POST /api/autopilot/activation/advance`
- `POST /api/autopilot/activation/retreat`

**Observation cycle:** `ActivationManager.run_observation_cycle()`, from the existing scheduler daemon's `autopilot_tick` job at minutes :11 and :41. It runs:
- the 3.32B assessment;
- a 3.32C recommendation run;
- a 3.32D self-healing scan.

Each respects its category level (at OBSERVE they only record). Errors are captured per step.

### Activation runbook

| Task | How |
|---|---|
| See the phase, readiness and blockers | Dashboard → **Autopilot** → *Autopilot activation* |
| Advance one phase | Enter an operator and a reason (plus the phrase and actions where shown) → **Advance**. The server re-checks readiness |
| Stop autonomy immediately | Kill switch (Autonomy Control) or the `backend/data/AUTONOMY_DISABLED` sentinel; production is not affected |
| Step back | Choose a lower phase → **Retreat** (always allowed) |
| Enable restricted execution (phase 3) | The operator creates `backend/data/autonomy_startup.json` `{"autonomy_execution":"enabled"}`, restarts the dashboard, then advances with `AUTHORIZE RESTRICTED_EXECUTION` and the action list. Each request still needs an approval |
| Kill-switch drill | Activate the dashboard kill switch, confirm Autopilot shows execution BLOCKED and readiness shows the `kill_switches` blocker, then deactivate it |

## 3. Stage 3.32J: operational reporting

Module: `autonomy/reports.py`, table `autonomy_operational_reports` (one row per kind and period; a payload SHA-256 is verified on every read).

**Reports:**
- **Daily operational summary** for one IST day.
- **Weekly Autopilot report** for one ISO week, Monday to Monday IST. A week in progress covers the last whole days up to generation, as stated in `production_window`.

**Content.** Each report keeps these apart:
- **`observed_facts`**, from persisted records:
  - production, recovery, rendering and failure statistics (the 3.32A Operational Intelligence, read-only);
  - the rendering failure trend (FFmpeg and file-lock failures by day);
  - health-monitor latency percentiles, availability, failure reasons and restarts;
  - recommendations created, decided by status and open, plus the top 5 open;
  - approval requests by status;
  - executions by outcome, with average duration;
  - blocked actions by event and top reasons;
  - circuit-breaker blocks and open breakers;
  - leases;
  - audit events;
  - the Autopilot phase, observation cycles, next-phase readiness and blockers, and the kill switches.
- **`calculated_metrics`:** production success, recovery-window expiry, render success, dashboard availability and execution success, each with its formula and inputs.
- **`interpretations`:** deterministic rules; each cites the metrics it uses.
- **`recommended_actions`:** for the operator; `executes_automatically: false`.
- **`ai_interpretation`:** `generated: false` (no AI text).
- **`missing`:** every unavailable item with its reason. Missing data is never shown as zero.

**Generation:**
- The existing scheduler daemon's `autopilot_tick` (every 30 minutes) keeps today and this week current, and finalises yesterday and last week once. Completed periods are immutable unless regenerated with `force`.
- Operators can update a report from the dashboard: `POST /api/autopilot/reports/generate`.
- `GET /api/autopilot/reports` lists them, and `GET /api/autopilot/reports/{daily|weekly}/{key}` reads one.
- No notification is sent and no external message.

## 4. Dashboard

The **Autopilot** tab adds four panels, loaded with one request (`GET /api/autopilot/operations`) on the tab's existing refresh. There is no new polling.
- **Dashboard health:** state, last probe latency and reasons, 24 h availability and latency percentiles, failure reasons, restarts, transitions, thresholds.
- **Rendering and FFmpeg:** geometry fix status, 7-day render statistics, Lemo-Opuscar output, FFmpeg errors by day, unavailable data.
- **Autopilot activation:** phase, observation statistics, recommended observation end, level drift, readiness checks and blockers, advance and retreat controls, history.
- **Operational reports:** list, a viewer with facts, metrics, interpretations, missing data and the integrity status, and update buttons.

The panel never approves or executes requests and never grants standing approvals.

## 5. Tests

- `backend/tests/test_render_geometry_332h.py` (55 cases, real FFmpeg on synthetic media; FFmpeg cases are skipped if the binary is absent).
- `backend/tests/test_reliability_activation_reports_332hij.py` (40 cases: evaluator, probes, configuration, ledger, monitor loop, endpoints, every phase transition and refusal, retreat, drift, observation cycle, reports, API, dashboard and scheduler wiring).

## 6. Limitations

- Readiness evidence thresholds are fixed constants in `activation.py`; changing them is a code change.
- The weekly report for a week in progress uses whole 24-hour days back from generation, which can start slightly before Monday (the report states its window).
- The monitor cannot tell a hung dashboard from a busy one while the process is alive. It reports UNHEALTHY and leaves the restart to the operator, by design.
- FFmpeg failures recorded before this deployment remain in the 7-day statistics until they age out.
