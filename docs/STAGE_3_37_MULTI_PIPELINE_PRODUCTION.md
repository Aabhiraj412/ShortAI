# Stage 3.37: Multi-pipeline production and resource orchestration

**Status:** implemented and tested. Long-form production is **OFF** by default. No long-form executor is installed, because the long-form pipeline itself is not part of this stage. Shorts production, the 24-cycle schedule, the retry policy, publishing and Autopilot (3.36) are unchanged.

## 1. Architecture

```
                         ┌──────────────── ProductionCoordinator (one per process; ticks in the daemon) ────────────────┐
 24-cycle cron ─┐        │                                                                                               │
 recovery loop ─┼─► run_cycle_generation ─► worker registry ─► shorts_render_slot() ─┐                                   │
 Autopilot 3.36 ┘        │                                                           ├─► production_render_slots (1 lease)│
 manual production ──────┼──────────────────────────────────── shorts_render_slot() ─┤                                   │
                         │                                                           │                                   │
 operator API ─► production_queue_jobs (LONG_FORM) ─► tick: recover → plan/admit ─► step thread (executor by kind) ──────┘
                         │                 resource_monitor (CPU, RAM, GPU via PDH, disk, ffmpeg processes)              │
                         └───────────────────────────────────────────────────────────────────────────────────────────────┘
```

* **Shorts queue = the existing scheduler.** Its jobs remain in `persistent_jobs`, created only by the cron schedule, the recovery loop, Autopilot or the manual run. The coordinator adds a single thing: each Shorts worker leases the shared heavyweight render slot, inside the existing worker registration. The lease is fail-open: Shorts is never refused.
* **Long-form queue = `production_queue_jobs`.** It holds operator-enqueued, step-based jobs. Each step is LIGHT (research, script, TTS, asset preparation) or HEAVY (render, encode) and carries an `estimate_minutes`. Steps are checkpointed and run one at a time per job, in a daemon thread, through the executor registered for the job's `kind`.
* **No new scheduler, daemon, worker type or renderer.** The tick runs inside the existing daemon recovery loop, after the Autopilot tick, at most once every 60 s.

### Logical jobs, worker threads and OS processes

The dashboard reports these separately:

| Level | Shorts | Long-form |
|---|---|---|
| Logical job | `persistent_jobs` row in a demand status | `production_queue_jobs` row (QUEUED / RUNNING) |
| Worker thread | the scheduler worker (`worker_registry`, in the daemon) | one step thread per RUNNING job (in the daemon) |
| OS process | the daemon itself plus `ffmpeg.exe` children | same |

The thread counts come from the daemon's persisted snapshot (`production_coordinator_state`), because the dashboard runs in a different process.

### Capacity and cadence

These are separate settings:
- **Production capacity** is the render slot plus the coordinator thresholds.
- **Publishing cadence** is the unchanged schedule and the publishing queue and policy.

The Shorts logical concurrency setting (`get_max_concurrency`, default 1) is unchanged.

## 2. Modules

| File | Change |
|---|---|
| `backend/app/scheduler/production_coordinator.py` | **New.** Queues, render-slot leases (CAS), admission, aging, recovery, tick, status. |
| `backend/app/scheduler/resource_monitor.py` | **New.** CPU (`GetSystemTimes`), RAM (`GlobalMemoryStatusEx`), GPU (PDH `GPU Engine` counters), disk, `ffmpeg.exe` processes; 15 s cache; zero cost. |
| `backend/app/scheduler/scheduler.py` | `shorts_render_slot()` helper; Shorts worker leases the slot inside its worker registration. |
| `backend/app/scheduler/daemon_runtime.py` | `production_coordinator_tick()` in the recovery loop, throttled to 60 s. |
| `backend/app/main.py` | Manual production takes the same lease. New routes: `GET /api/production/queues`; `POST .../long-form/pause`, `.../long-form/resume`, `.../long-form/jobs`, `.../long-form/jobs/{id}/cancel`; `PUT /api/production/queues/config`. Writes need local origin, CSRF and `confirm: true`. |
| `backend/app/templates/dashboard.html` | Autopilot tab → **Production queues and resources** card. |
| `backend/scripts/benchmark_coordinator_337.py` | Overhead benchmark plus Shorts baseline (production is read only). |
| `backend/tests/test_production_coordinator_337.py` | 43 tests. |

New tables are created on first use: `production_queue_jobs`, `production_render_slots`, `production_coordinator_config`, `production_coordinator_state`, `production_queue_events`. No existing table is altered.

## 3. Admission rules (defaults)

| Rule | HEAVY step | LIGHT step |
|---|---|---|
| Concurrency | 1 (`BENCHMARKED_MAX_HEAVY`; config cannot exceed it) | 1 (max 2) |
| CPU | ≤ 70 % | ≤ 85 % |
| Available RAM | ≥ 3.5 GB | ≥ 2.0 GB |
| Free disk | ≥ 15 GB | ≥ 5 GB |
| Shorts | no Shorts job in QUEUED / RUNNING / RETRYING / WAITING_FOR_INTERNET / RETRY_SCHEDULED; the step plus 5 min must end before the next scheduled Shorts start | may overlap a Shorts render while under the LIGHT thresholds |
| Unknown state | resources or Shorts state unreadable → wait (fail closed for long-form) | resources unreadable → wait |

Further rules:
- Long-form is admitted only when `long_form_enabled` is true (default **false**), the queue is not paused, an executor exists for the kind and any retry backoff has passed.
- At most one step is admitted per tick.

## 4. Priority, preemption and starvation

* **Shorts is always first.** If a long-form step holds the slot when a Shorts worker starts, the coordinator sets `preempt_requested`. The step sees `ctx.should_yield()` become true and must checkpoint and return `{"yielded": True}`. `should_yield()` is also true when Shorts demand appears, the queue is paused, a cancel is requested or the claim is lost. The Shorts worker waits at most `shorts_slot_wait_seconds` (600). After that, or on any coordinator error, it proceeds exactly as before Stage 3.37, and a `SHORTS_OVERLAP` or `COORDINATOR_UNAVAILABLE` event is recorded.
* **Aging.** Effective priority = priority + minutes waited / `aging_minutes` (30). An old low-priority job overtakes newer ones.
* **Structural gap.** The last 100 Shorts took a median of 12.4 min (p95 16.8 min), which leaves about 43 min of every hour for long-form work.
* **Starvation alert.** A job with no step progress for `starvation_alert_hours` (6) is flagged `starving`. The flag comes with the exact reason, for example "step needs 50 min but the next Shorts cycle starts in 40 min". The remedy is to split the step; Shorts is never displaced.

## 5. Persistence and recovery

* Claims are compare-and-swap updates (`QUEUED → RUNNING` on a matching `version`, with a fresh token and the owner PID). Duplicate enqueues of the same id are idempotent. Slots are taken with `UPDATE … WHERE token IS NULL`.
* Recovery runs only in the daemon tick:
  - A slot is released when its holder process is dead, or when the holder is this process and the lease token is no longer held.
  - A RUNNING job whose owner process or step thread is gone returns to QUEUED at its checkpointed step (attempts + 1). It becomes FAILED once `max_attempts` (3) is reached, or CANCELLED if a cancel was requested.
* Failed steps are retried after `retry_backoff_minutes` (10).
* Nothing is ever deleted: cancel sets CANCELLED.

## 6. Benchmarks (2026-11-27 03:40 IST, Ryzen 7 3700U, 13.95 GB RAM, Vega 10)

Commands:
- `python backend/scripts/benchmark_coordinator_337.py`
- production read-only: `?mode=ro`

| Measure | Result |
|---|---|
| Resource probe, cold (CPU and GPU sampled in parallel) | median 503 ms (first version, sequential: 2.7 s) |
| Resource probe, cached | < 0.1 ms |
| Shorts lease overhead per production run | median 39 ms |
| Slot acquire + release | median 36 ms |
| Plan with 50 queued jobs | median 8 ms (before passing the config through: 208 ms) |
| Status with 50 jobs | median 20 ms |
| Daemon tick while idle (default) | no host sampling |
| Shorts baseline, last 100 completed jobs | median 12.4 min, p95 16.8 min, max 77.5 min |
| Host at sample time | CPU 18.8 %, RAM 62 % (5.2 GB available), disk 140.9 GB free, GPU 0.5 % (3D engine) |

**Rendering concurrency was not benchmarked, and heavy concurrency stays at 1.**
- The 3.34 live cycle peaked at about 2.9 GB with a single renderer.
- Two concurrent renders would leave too little of the 13.95 GB for the laptop.
- Encoding runs on the CPU (`libx264`).

Before raising `BENCHMARKED_MAX_HEAVY`, run two test renders concurrently in an operator-scheduled window with no production job, record peak RAM, CPU and wall time against the single-render baseline, and change the constant in code with that record.

## 7. Autopilot and safety compatibility

* Autopilot (3.36) is unchanged. Its dispatches go through `run_cycle_generation`, so they take the same Shorts lease. No action was added to the autonomy registry, and the coordinator cannot be reached from it.
* The coordinator has no publishing, schedule, OAuth, restart, deletion or git path. It runs only work an operator enqueued, behind an OFF-by-default switch, and only through a registered executor.
* Manual production keeps its existing checks (operating mode, capacity, no auto-upload) and additionally takes the lease.

## 8. Executor contract (for the future long-form pipeline)

```python
from backend.app.scheduler.production_coordinator import register_executor

def run_step(ctx):                        # ctx.job, ctx.step, ctx.checkpoint (dict, persisted)
    for chunk in work_from(ctx.checkpoint):
        do(chunk); ctx.checkpoint["done"] = chunk.id; ctx.heartbeat()
        if ctx.should_yield():
            return {"yielded": True}      # resumes at the same step from the checkpoint
    return {}                             # step complete
register_executor("long_form_video", run_step)
```

Rules for executors:
- Keep each step's real duration within its `estimate_minutes`.
- Poll `should_yield()` at least every 30 s.
- Launch FFmpeg at below-normal priority so the laptop stays responsive.

## 9. Operator steps (each needs explicit approval)

1. Deploy (handover §8).
2. When a long-form executor exists: `PUT /api/production/queues/config {"changes": {"long_form_enabled": true}}`.
3. Enqueue jobs.
4. Pause or resume from the dashboard card.
