"""Stage 3.37: multi-pipeline production and resource orchestration.

One shared, resource-aware coordinator over two LOGICAL production queues:

* ``SHORTS``: the existing production path, unchanged. Its queue IS the scheduler: the 24-cycle cron schedule, the
  recovery loop, the retry policy and ``persistent_jobs``. The coordinator never creates, reorders, delays beyond a
  bounded wait, or cancels a Shorts job; it only (a) leases the shared heavyweight render slot to each Shorts worker
  and (b) reads ``persistent_jobs`` to know when Shorts needs the machine.
* ``LONG_FORM``: a new persisted queue (``production_queue_jobs``) of operator-enqueued, step-based jobs. Each step
  is LIGHT (research, script, TTS, asset preparation) or HEAVY (rendering, encoding). Steps run one at a time per
  job, are checkpointed, and can be resumed after a crash or a pause. A job runs only through an executor registered
  for its ``kind``; none is registered in production yet (the long-form pipeline itself is not part of this stage),
  so long-form jobs wait with that reason instead of running anything.

Rules (all enforced here, not by convention):

1. **One heavyweight job at a time.** ``production_render_slots`` holds ``max_heavy_concurrency`` lease rows; a
   lease is taken by compare-and-swap, so two claimers can never both win. The ceiling is ``BENCHMARKED_MAX_HEAVY``
   (1): raising it needs a recorded benchmark first.
2. **Shorts first.** A HEAVY long-form step is admitted only when no Shorts job needs the machine and the step's
   estimate (plus a guard margin) ends before the next scheduled Shorts start. A Shorts worker that finds the slot
   held asks the holder to yield (``preempt_requested``) and waits at most ``shorts_slot_wait_seconds``; after that,
   or on any coordinator error, Shorts proceeds exactly as before Stage 3.37 (fail-open for Shorts, recorded).
3. **Resource admission.** HEAVY and LIGHT steps each have CPU, available-RAM and free-disk thresholds; LIGHT steps
   may overlap a Shorts render only while the host stays under the LIGHT thresholds and the LIGHT concurrency limit.
4. **No starvation.** Long-form candidates are ordered by priority plus age (one point per ``aging_minutes``), so an
   old job overtakes newer, higher-priority ones. Shorts occupy about 12 of every 60 minutes, so a gap opens every
   hour; a job whose next step never fits a gap is flagged ``starving`` with the exact reason.
5. **Persistence and duplicate-claim prevention.** Job claims are CAS updates (QUEUED -> RUNNING with a fresh token
   and the owner PID). Recovery (daemon only) returns a RUNNING job whose owner process or thread is gone to QUEUED at
   its checkpointed step (bounded attempts), and releases leases whose holder is gone.
6. **Safe pause / resume / cancel** of the long-form queue: never interrupts a running step; the running step is
   asked to yield at its next safe point and no new step is admitted.

The coordinator takes no autonomous action: it has no publishing, configuration, restart, deletion or git path, it
is not reachable from the Autopilot action registry, and Autopilot ownership (Stage 3.36) is untouched because
Autopilot dispatches go through the same ``run_cycle_generation`` -> Shorts render-slot lease.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

SHORTS = "SHORTS"
LONG_FORM = "LONG_FORM"
QUEUES = (SHORTS, LONG_FORM)
HEAVY = "HEAVY"
LIGHT = "LIGHT"

QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED = "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"
TERMINAL = (COMPLETED, FAILED, CANCELLED)

# Shorts statuses (persistent_jobs) that mean "Shorts needs or will shortly need the renderer".
SHORTS_DEMAND_STATUSES = ("QUEUED", "RUNNING", "RETRYING", "WAITING_FOR_INTERNET", "RETRY_SCHEDULED")

BENCHMARKED_MAX_HEAVY = 1      # raise only after a recorded rendering-concurrency benchmark (docs/STAGE_3_37_*.md)
MAX_LIGHT_LIMIT = 2
TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"

DEFAULT_CONFIG: Dict[str, Any] = {
    "long_form_enabled": False,           # operator switch; OFF by default
    "max_heavy_concurrency": 1,
    "max_light_concurrency": 1,
    "heavy_max_cpu_percent": 70.0,
    "heavy_min_ram_available_gb": 3.5,
    "heavy_min_disk_free_gb": 15.0,
    "light_max_cpu_percent": 85.0,
    "light_min_ram_available_gb": 2.0,
    "light_min_disk_free_gb": 5.0,
    "shorts_guard_minutes": 5,
    "shorts_slot_wait_seconds": 600,
    "aging_minutes": 30,
    "starvation_alert_hours": 6,
    "max_attempts": 3,
    "retry_backoff_minutes": 10,
}
_CONFIG_BOUNDS: Dict[str, Tuple[float, float]] = {
    "max_heavy_concurrency": (1, BENCHMARKED_MAX_HEAVY), "max_light_concurrency": (0, MAX_LIGHT_LIMIT),
    "heavy_max_cpu_percent": (10, 95), "heavy_min_ram_available_gb": (1, 12), "heavy_min_disk_free_gb": (2, 500),
    "light_max_cpu_percent": (10, 98), "light_min_ram_available_gb": (0.5, 12), "light_min_disk_free_gb": (1, 500),
    "shorts_guard_minutes": (0, 60), "shorts_slot_wait_seconds": (0, 1800), "aging_minutes": (1, 1440),
    "starvation_alert_hours": (1, 168), "max_attempts": (1, 10), "retry_backoff_minutes": (0, 240),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS production_queue_jobs (
    id TEXT PRIMARY KEY,
    queue TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    state TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    next_step INTEGER NOT NULL DEFAULT 0,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    owner_token TEXT,
    owner_pid INTEGER,
    heartbeat_at_utc TEXT,
    not_before_utc TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    enqueued_at_utc TEXT NOT NULL,
    started_at_utc TEXT,
    last_progress_at_utc TEXT,
    finished_at_utc TEXT,
    error TEXT,
    requested_by TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pqj_queue_state ON production_queue_jobs (queue, state);
CREATE TABLE IF NOT EXISTS production_render_slots (
    slot INTEGER PRIMARY KEY,
    holder_queue TEXT,
    holder_job_id TEXT,
    holder_step TEXT,
    holder_pid INTEGER,
    token TEXT,
    acquired_at_utc TEXT,
    preempt_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS production_coordinator_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    config_json TEXT NOT NULL,
    paused_json TEXT NOT NULL DEFAULT '{}',
    updated_at_utc TEXT NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS production_coordinator_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pid INTEGER,
    snapshot_json TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS production_queue_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    queue TEXT,
    job_id TEXT,
    event TEXT NOT NULL,
    actor TEXT,
    detail TEXT
);
"""


class CoordinatorError(Exception):
    def __init__(self, message: str, code: int = 409):
        super().__init__(message)
        self.code = code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(ts or "", TS_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _default_pid_alive(pid: int) -> bool:
    from backend.app.services.deployment_diagnostics import is_pid_alive
    return bool(is_pid_alive(int(pid)))


def _default_db_path() -> str:
    from backend.app.core.paths import DATABASE_PATH
    return str(DATABASE_PATH)


def _default_resources() -> Dict[str, Any]:
    from backend.app.scheduler import resource_monitor
    return resource_monitor.sample()


def _default_shorts_view(now_utc: datetime, db_path: str) -> Dict[str, Any]:
    """Shorts demand from persistent_jobs (read-only) and the next scheduled Shorts start from the active profile.
    Any error -> ``known: False``, which blocks HEAVY long-form work (fail closed for long-form)."""
    view: Dict[str, Any] = {"known": True, "active_jobs": [], "next_start_utc": None, "cycles": None}
    try:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        try:
            marks = ",".join("?" * len(SHORTS_DEMAND_STATUSES))
            rows = conn.execute(f"SELECT id, status, current_stage FROM persistent_jobs WHERE status IN ({marks})",
                                SHORTS_DEMAND_STATUSES).fetchall()
        finally:
            conn.close()
        view["active_jobs"] = [{"job_id": r[0], "status": r[1], "stage": r[2]} for r in rows
                               if not str(r[0]).startswith(("test_", "mock_"))]
    except Exception as exc:  # noqa: BLE001
        view.update(known=False, error=f"persistent_jobs unreadable ({type(exc).__name__})")
    try:
        from backend.app.scheduler.schedule_profiles import get_active_profile
        profile = get_active_profile()
        tz = profile.tz
        local = now_utc.astimezone(tz)
        best = None
        for c in profile.cycles:
            for day in (0, 1, 2):
                cand = (local + timedelta(days=day)).replace(hour=c.start_hour_ist, minute=c.start_min_ist,
                                                             second=0, microsecond=0)
                if cand > local and cand.weekday() in c.active_weekdays:
                    best = cand if best is None or cand < best else best
                    break
        view["cycles"] = len(profile.cycles)
        view["profile"] = profile.name
        view["next_start_utc"] = _ts(best) if best else None
    except Exception as exc:  # noqa: BLE001
        view.update(known=False, error=f"schedule unreadable ({type(exc).__name__})")
    try:
        from backend.app.scheduler import worker_registry
        view["in_process_workers"] = len([1 for j in list(worker_registry.registry._active)
                                          if worker_registry.registry.is_active(j)])
    except Exception:
        view["in_process_workers"] = None
    return view


class StepContext:
    """Handed to a long-form executor for one step. ``should_yield()`` must be polled at safe points: when it is
    True the executor saves what it has into ``checkpoint`` and returns ``{"yielded": True}``."""

    def __init__(self, coordinator: "ProductionCoordinator", job: Dict[str, Any], step: Dict[str, Any],
                 slot_token: Optional[str]):
        self.coordinator, self.job, self.step, self.slot_token = coordinator, job, step, slot_token
        self.checkpoint: Dict[str, Any] = dict(job.get("checkpoint") or {})

    def should_yield(self) -> bool:
        return self.coordinator._should_yield(self.job, self.step, self.slot_token)

    def heartbeat(self) -> None:
        self.coordinator._heartbeat(self.job["id"], self.job["owner_token"])


Executor = Callable[[StepContext], Dict[str, Any]]
_EXECUTORS: Dict[str, Executor] = {}


def register_executor(kind: str, fn: Executor) -> None:
    """Register the runner for a long-form job kind (none is registered in production in Stage 3.37)."""
    _EXECUTORS[str(kind)] = fn


class ProductionCoordinator:
    def __init__(self, db_path: Optional[str] = None, *, clock: Callable[[], datetime] = _utcnow,
                 pid: Optional[int] = None, pid_alive: Optional[Callable[[int], bool]] = None,
                 resource_probe: Optional[Callable[[], Dict[str, Any]]] = None,
                 shorts_view: Optional[Callable[[datetime], Dict[str, Any]]] = None,
                 executors: Optional[Dict[str, Executor]] = None, sleep: Callable[[float], None] = time.sleep):
        self.db_path = str(db_path or _default_db_path())
        self.clock = clock
        self.pid = pid if pid is not None else os.getpid()
        self.pid_alive = pid_alive or _default_pid_alive
        self.resource_probe = resource_probe or _default_resources
        self.shorts_view = shorts_view or (lambda now: _default_shorts_view(now, self.db_path))
        self.executors = executors if executors is not None else _EXECUTORS
        self.sleep = sleep
        self._lock = threading.Lock()
        self._held_tokens: Dict[str, str] = {}          # slot token -> job id, held by THIS process
        self._running: Dict[str, threading.Thread] = {}  # long-form job id -> step thread in THIS process
        self._ensured = False
        self.last_decisions: List[Dict[str, Any]] = []

    # -- storage -------------------------------------------------------------------------------------------------
    def _connect(self, write: bool = True) -> sqlite3.Connection:
        if not os.path.isfile(self.db_path):
            raise FileNotFoundError("database not available")
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + ("?mode=rw" if write else "?mode=ro"),
                               uri=True, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def ensure(self) -> None:
        if self._ensured:
            return
        conn = self._connect(True)
        try:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO production_coordinator_config (id, config_json, paused_json, "
                         "updated_at_utc, updated_by) VALUES (1, ?, '{}', ?, 'default')",
                         (json.dumps(DEFAULT_CONFIG, sort_keys=True), _ts(self.clock())))
            for n in range(1, BENCHMARKED_MAX_HEAVY + 1):
                conn.execute("INSERT OR IGNORE INTO production_render_slots (slot) VALUES (?)", (n,))
        finally:
            conn.close()
        self._ensured = True

    def _event(self, conn: sqlite3.Connection, event: str, *, queue: Optional[str] = None,
               job_id: Optional[str] = None, actor: str = "coordinator", detail: Any = None) -> None:
        conn.execute("INSERT INTO production_queue_events (ts_utc, queue, job_id, event, actor, detail) "
                     "VALUES (?,?,?,?,?,?)", (_ts(self.clock()), queue, job_id, event, actor,
                                              json.dumps(detail, sort_keys=True, default=str)[:2000]
                                              if detail is not None else None))

    def record_event(self, event: str, **kw: Any) -> None:
        try:
            self.ensure()
            conn = self._connect(True)
            try:
                self._event(conn, event, **kw)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[COORDINATOR] could not record %s: %s", event, type(exc).__name__)

    # -- configuration and pause -----------------------------------------------------------------------------------
    def _config_row(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        self.ensure()
        conn = self._connect(False)
        try:
            row = conn.execute("SELECT config_json, paused_json FROM production_coordinator_config WHERE id=1").fetchone()
        finally:
            conn.close()
        cfg = dict(DEFAULT_CONFIG)
        paused: Dict[str, Any] = {}
        if row:
            try:
                cfg.update({k: v for k, v in json.loads(row["config_json"]).items() if k in DEFAULT_CONFIG})
                paused = json.loads(row["paused_json"] or "{}")
            except ValueError:
                pass
        cfg["max_heavy_concurrency"] = max(1, min(int(cfg["max_heavy_concurrency"]), BENCHMARKED_MAX_HEAVY))
        cfg["max_light_concurrency"] = max(0, min(int(cfg["max_light_concurrency"]), MAX_LIGHT_LIMIT))
        return cfg, paused

    def config(self) -> Dict[str, Any]:
        return self._config_row()[0]

    def set_config(self, changes: Dict[str, Any], operator: str, reason: str) -> Dict[str, Any]:
        """Operator-only change (dashboard API with CSRF + confirmation). Validated and audited."""
        if not operator or not reason:
            raise CoordinatorError("operator and reason are required", 400)
        cfg, _ = self._config_row()
        for k, v in changes.items():
            if k not in DEFAULT_CONFIG:
                raise CoordinatorError(f"unknown setting: {k}", 400)
            if k == "long_form_enabled":
                if not isinstance(v, bool):
                    raise CoordinatorError("long_form_enabled must be true or false", 400)
                cfg[k] = v
                continue
            lo, hi = _CONFIG_BOUNDS[k]
            try:
                num = float(v)
            except (TypeError, ValueError):
                raise CoordinatorError(f"{k} must be a number", 400)
            if not lo <= num <= hi:
                extra = " (raising heavy concurrency needs a recorded benchmark)" if k == "max_heavy_concurrency" else ""
                raise CoordinatorError(f"{k} must be between {lo:g} and {hi:g}{extra}", 400)
            cfg[k] = int(num) if isinstance(DEFAULT_CONFIG[k], int) else num
        conn = self._connect(True)
        try:
            conn.execute("UPDATE production_coordinator_config SET config_json=?, updated_at_utc=?, updated_by=? "
                         "WHERE id=1", (json.dumps(cfg, sort_keys=True), _ts(self.clock()), operator))
            self._event(conn, "CONFIG_CHANGED", actor=operator, detail={"changes": changes, "reason": reason})
        finally:
            conn.close()
        return cfg

    def set_queue_paused(self, queue: str, paused: bool, operator: str, reason: str) -> Dict[str, Any]:
        if queue != LONG_FORM:
            raise CoordinatorError("Only the long-form queue can be paused here; Shorts production is governed by "
                                   "the schedule and the operating mode.", 400)
        if not operator or not reason:
            raise CoordinatorError("operator and reason are required", 400)
        _, p = self._config_row()
        p[queue] = bool(paused)
        conn = self._connect(True)
        try:
            conn.execute("UPDATE production_coordinator_config SET paused_json=?, updated_at_utc=?, updated_by=? "
                         "WHERE id=1", (json.dumps(p, sort_keys=True), _ts(self.clock()), operator))
            self._event(conn, "QUEUE_PAUSED" if paused else "QUEUE_RESUMED", queue=queue, actor=operator,
                        detail={"reason": reason})
        finally:
            conn.close()
        return {"queue": queue, "paused": bool(paused)}

    # -- long-form jobs --------------------------------------------------------------------------------------------
    @staticmethod
    def _validate_steps(steps: Any) -> List[Dict[str, Any]]:
        if not isinstance(steps, list) or not 1 <= len(steps) <= 50:
            raise CoordinatorError("steps must be a list of 1 to 50 steps", 400)
        out = []
        for s in steps:
            if not isinstance(s, dict):
                raise CoordinatorError("each step must be an object", 400)
            name = str(s.get("name") or "").strip()
            weight = str(s.get("weight") or "").upper()
            try:
                est = float(s.get("estimate_minutes"))
            except (TypeError, ValueError):
                raise CoordinatorError("each step needs a numeric estimate_minutes", 400)
            if not name or len(name) > 80 or weight not in (HEAVY, LIGHT) or not 0 < est <= 240:
                raise CoordinatorError("each step needs a name, weight HEAVY or LIGHT and 0 < estimate_minutes <= 240", 400)
            out.append({"name": name, "weight": weight, "estimate_minutes": est})
        return out

    def enqueue(self, *, kind: str, title: str, steps: Any, requested_by: str, priority: int = 50,
                job_id: Optional[str] = None, queue: str = LONG_FORM) -> Dict[str, Any]:
        if queue != LONG_FORM:
            raise CoordinatorError("Shorts jobs are created only by the scheduler (24-cycle schedule).", 400)
        if not requested_by or not str(title).strip() or not str(kind).strip():
            raise CoordinatorError("kind, title and requested_by are required", 400)
        if not 0 <= int(priority) <= 100:
            raise CoordinatorError("priority must be 0..100", 400)
        clean = self._validate_steps(steps)
        cfg = self.config()
        jid = job_id or f"lf_{self.clock().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
        now = _ts(self.clock())
        self.ensure()
        conn = self._connect(True)
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO production_queue_jobs (id, queue, kind, title, priority, state, steps_json, "
                "max_attempts, enqueued_at_utc, requested_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (jid, queue, str(kind)[:60], str(title)[:200], int(priority), QUEUED, json.dumps(clean),
                 int(cfg["max_attempts"]), now, str(requested_by)[:64]))
            if cur.rowcount != 1:
                return {"duplicate": True, "job": self.get_job(jid)}
            self._event(conn, "ENQUEUED", queue=queue, job_id=jid, actor=requested_by,
                        detail={"kind": kind, "priority": int(priority), "steps": len(clean)})
        finally:
            conn.close()
        return {"duplicate": False, "job": self.get_job(jid)}

    def cancel(self, job_id: str, operator: str, reason: str) -> Dict[str, Any]:
        """QUEUED -> CANCELLED at once; RUNNING -> cancel requested (takes effect at the step's next safe point).
        Never deletes the row."""
        if not operator or not reason:
            raise CoordinatorError("operator and reason are required", 400)
        job = self.get_job(job_id)
        if job is None:
            raise CoordinatorError("job not found", 404)
        conn = self._connect(True)
        try:
            if job["state"] == QUEUED:
                cur = conn.execute("UPDATE production_queue_jobs SET state=?, finished_at_utc=?, version=version+1 "
                                   "WHERE id=? AND state=?", (CANCELLED, _ts(self.clock()), job_id, QUEUED))
                if cur.rowcount != 1:
                    raise CoordinatorError("job changed state; refresh and retry")
            elif job["state"] == RUNNING:
                conn.execute("UPDATE production_queue_jobs SET cancel_requested=1, version=version+1 WHERE id=?",
                             (job_id,))
            else:
                raise CoordinatorError(f"job is {job['state']}; nothing to cancel")
            self._event(conn, "CANCEL_REQUESTED", queue=job["queue"], job_id=job_id, actor=operator,
                        detail={"reason": reason, "state": job["state"]})
        finally:
            conn.close()
        return {"job": self.get_job(job_id)}

    @staticmethod
    def _row(r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        d["steps"] = json.loads(d.pop("steps_json") or "[]")
        d["checkpoint"] = json.loads(d.pop("checkpoint_json") or "{}")
        return d

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        self.ensure()
        conn = self._connect(False)
        try:
            r = conn.execute("SELECT * FROM production_queue_jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        return self._row(r) if r else None

    def list_jobs(self, *, states: Optional[Tuple[str, ...]] = None, limit: int = 50) -> List[Dict[str, Any]]:
        self.ensure()
        conn = self._connect(False)
        try:
            if states:
                marks = ",".join("?" * len(states))
                rows = conn.execute(f"SELECT * FROM production_queue_jobs WHERE state IN ({marks}) "
                                    "ORDER BY enqueued_at_utc LIMIT ?", (*states, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM production_queue_jobs ORDER BY enqueued_at_utc DESC LIMIT ?",
                                    (limit,)).fetchall()
        finally:
            conn.close()
        return [self._row(r) for r in rows]

    # -- render slots ------------------------------------------------------------------------------------------------
    def slots(self) -> List[Dict[str, Any]]:
        self.ensure()
        conn = self._connect(False)
        try:
            rows = conn.execute("SELECT * FROM production_render_slots ORDER BY slot").fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def acquire_slot(self, queue: str, job_id: str, step: Optional[str] = None) -> Optional[str]:
        """CAS-claim a free heavyweight slot (within the configured concurrency). Returns the lease token or None."""
        limit = self.config()["max_heavy_concurrency"]
        token = secrets.token_hex(16)
        conn = self._connect(True)
        try:
            for n in range(1, limit + 1):
                cur = conn.execute(
                    "UPDATE production_render_slots SET holder_queue=?, holder_job_id=?, holder_step=?, holder_pid=?, "
                    "token=?, acquired_at_utc=?, preempt_requested=0 WHERE slot=? AND token IS NULL",
                    (queue, job_id, step, self.pid, token, _ts(self.clock()), n))
                if cur.rowcount == 1:
                    self._event(conn, "SLOT_ACQUIRED", queue=queue, job_id=job_id, detail={"slot": n, "step": step})
                    with self._lock:
                        self._held_tokens[token] = job_id
                    return token
        finally:
            conn.close()
        return None

    def release_slot(self, token: Optional[str]) -> None:
        if not token:
            return
        with self._lock:
            job_id = self._held_tokens.pop(token, None)
        conn = self._connect(True)
        try:
            cur = conn.execute("UPDATE production_render_slots SET holder_queue=NULL, holder_job_id=NULL, "
                               "holder_step=NULL, holder_pid=NULL, token=NULL, acquired_at_utc=NULL, "
                               "preempt_requested=0 WHERE token=?", (token,))
            if cur.rowcount:
                self._event(conn, "SLOT_RELEASED", job_id=job_id)
        finally:
            conn.close()

    def _request_preempt(self, by_job: str) -> List[str]:
        conn = self._connect(True)
        try:
            rows = conn.execute("SELECT slot, holder_job_id FROM production_render_slots WHERE token IS NOT NULL "
                                "AND holder_queue=?", (LONG_FORM,)).fetchall()
            for r in rows:
                conn.execute("UPDATE production_render_slots SET preempt_requested=1 WHERE slot=?", (r["slot"],))
                self._event(conn, "PREEMPT_REQUESTED", queue=LONG_FORM, job_id=r["holder_job_id"],
                            detail={"for_shorts_job": by_job})
            return [r["holder_job_id"] for r in rows]
        finally:
            conn.close()

    @contextmanager
    def shorts_render_slot(self, job_id: str) -> Iterator[Dict[str, Any]]:
        """Lease the heavyweight slot for a Shorts worker. Shorts is never refused: a long-form holder is asked to
        yield and the worker waits at most ``shorts_slot_wait_seconds``; after that, or on any error, the worker
        proceeds as it always has, and the overlap is recorded."""
        info: Dict[str, Any] = {"job_id": job_id, "token": None, "waited_seconds": 0.0, "outcome": None}
        token = None
        try:
            try:
                cfg = self.config()
                token = self.acquire_slot(SHORTS, job_id, "render")
                if token is None:
                    self._request_preempt(job_id)
                    deadline = time.monotonic() + float(cfg["shorts_slot_wait_seconds"])
                    t0 = time.monotonic()
                    while token is None and time.monotonic() < deadline:
                        self.sleep(2.0)
                        token = self.acquire_slot(SHORTS, job_id, "render")
                    info["waited_seconds"] = round(time.monotonic() - t0, 1)
                info["token"] = token
                info["outcome"] = "LEASED" if token else "PROCEEDED_WITHOUT_SLOT"
                if token is None:
                    self.record_event("SHORTS_OVERLAP", queue=SHORTS, job_id=job_id,
                                      detail={"waited_seconds": info["waited_seconds"], "holders": self.slots()})
                    logger.warning("[COORDINATOR] Shorts %s proceeds without the render slot after %.0fs wait.",
                                   job_id, info["waited_seconds"])
            except Exception as exc:  # noqa: BLE001 - Shorts reliability wins over coordination
                info["outcome"] = "COORDINATOR_UNAVAILABLE"
                info["error"] = type(exc).__name__
                logger.warning("[COORDINATOR] render-slot lease unavailable for %s (%s); Shorts proceeds unchanged.",
                               job_id, type(exc).__name__)
            yield info
        finally:
            if token:
                try:
                    self.release_slot(token)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[COORDINATOR] slot release failed for %s: %s", job_id, type(exc).__name__)

    # -- admission ---------------------------------------------------------------------------------------------------
    def effective_priority(self, job: Dict[str, Any], now: datetime, cfg: Optional[Dict[str, Any]] = None) -> float:
        cfg = cfg or self.config()
        enq = _parse(job.get("enqueued_at_utc")) or now
        return round(float(job.get("priority") or 0) + (now - enq).total_seconds() / 60.0 / float(cfg["aging_minutes"]), 2)

    def admit(self, step: Dict[str, Any], *, now: datetime, resources: Dict[str, Any], shorts: Dict[str, Any],
              light_running: int, cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        cfg = cfg or self.config()
        weight = step.get("weight")
        pre = "heavy" if weight == HEAVY else "light"
        cpu, ram, disk = (resources.get("cpu_used_percent"), resources.get("memory_available_gb"),
                          resources.get("disk_free_gb"))
        if ram is None or disk is None:
            return False, "resources not measurable; long-form work waits (fail closed)"
        if cpu is not None and cpu > cfg[f"{pre}_max_cpu_percent"]:
            return False, f"CPU {cpu:.0f}% above the {pre} limit {cfg[f'{pre}_max_cpu_percent']:.0f}%"
        if ram < cfg[f"{pre}_min_ram_available_gb"]:
            return False, f"available RAM {ram:.1f} GB below the {pre} minimum {cfg[f'{pre}_min_ram_available_gb']:.1f} GB"
        if disk < cfg[f"{pre}_min_disk_free_gb"]:
            return False, f"free disk {disk:.1f} GB below the {pre} minimum {cfg[f'{pre}_min_disk_free_gb']:.1f} GB"
        if weight == LIGHT:
            if light_running >= cfg["max_light_concurrency"]:
                return False, f"light concurrency limit {cfg['max_light_concurrency']} reached"
            return True, "light step admitted (overlap allowed within light thresholds)"
        if not shorts.get("known"):
            return False, f"Shorts state unknown ({shorts.get('error', 'unreadable')}); heavy work waits"
        if shorts.get("active_jobs"):
            ids = ", ".join(j["job_id"] for j in shorts["active_jobs"][:3])
            return False, f"Shorts needs the renderer ({ids})"
        nxt = _parse(shorts.get("next_start_utc"))
        need = timedelta(minutes=float(step["estimate_minutes"]) + float(cfg["shorts_guard_minutes"]))
        if nxt is not None and now + need > nxt:
            gap = max(0.0, (nxt - now).total_seconds() / 60.0)
            return False, (f"step needs {need.total_seconds() / 60:.0f} min but the next Shorts cycle starts in "
                           f"{gap:.0f} min")
        return True, "heavy step admitted: renderer free and the step ends before the next Shorts cycle"

    # -- recovery (daemon only) -------------------------------------------------------------------------------------
    def recover(self) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        self.ensure()
        now = self.clock()
        conn = self._connect(True)
        try:
            for s in conn.execute("SELECT * FROM production_render_slots WHERE token IS NOT NULL").fetchall():
                pid = int(s["holder_pid"] or 0)
                if pid == self.pid:
                    with self._lock:
                        gone = s["token"] not in self._held_tokens
                else:
                    try:
                        gone = not self.pid_alive(pid)
                    except Exception:
                        gone = False
                if gone:
                    cur = conn.execute("UPDATE production_render_slots SET holder_queue=NULL, holder_job_id=NULL, "
                                       "holder_step=NULL, holder_pid=NULL, token=NULL, acquired_at_utc=NULL, "
                                       "preempt_requested=0 WHERE slot=? AND token=?", (s["slot"], s["token"]))
                    if cur.rowcount:
                        a = {"action": "SLOT_RECLAIMED", "slot": s["slot"], "job_id": s["holder_job_id"], "pid": pid}
                        actions.append(a)
                        self._event(conn, "SLOT_RECLAIMED", queue=s["holder_queue"], job_id=s["holder_job_id"], detail=a)
            for r in conn.execute("SELECT * FROM production_queue_jobs WHERE state=?", (RUNNING,)).fetchall():
                pid = int(r["owner_pid"] or 0)
                if pid == self.pid:
                    with self._lock:
                        t = self._running.get(r["id"])
                    gone = t is None or not t.is_alive()
                else:
                    try:
                        gone = not self.pid_alive(pid)
                    except Exception:
                        gone = False
                if not gone:
                    continue
                attempts = int(r["attempts"]) + 1
                if r["cancel_requested"]:
                    new_state = CANCELLED
                elif attempts >= int(r["max_attempts"]):
                    new_state = FAILED
                else:
                    new_state = QUEUED
                cur = conn.execute(
                    "UPDATE production_queue_jobs SET state=?, attempts=?, owner_token=NULL, owner_pid=NULL, "
                    "error=?, finished_at_utc=?, version=version+1 WHERE id=? AND state=? AND version=?",
                    (new_state, attempts, f"worker gone (process {pid}); recovered at step {r['next_step']}",
                     _ts(now) if new_state in TERMINAL else None, r["id"], RUNNING, r["version"]))
                if cur.rowcount:
                    a = {"action": "JOB_RECOVERED", "job_id": r["id"], "to": new_state, "step": r["next_step"],
                         "attempts": attempts}
                    actions.append(a)
                    self._event(conn, "JOB_RECOVERED", queue=r["queue"], job_id=r["id"], detail=a)
        finally:
            conn.close()
        return actions

    # -- dispatch ------------------------------------------------------------------------------------------------------
    def _claim(self, job: Dict[str, Any]) -> Optional[str]:
        token = secrets.token_hex(16)
        now = _ts(self.clock())
        conn = self._connect(True)
        try:
            cur = conn.execute(
                "UPDATE production_queue_jobs SET state=?, owner_token=?, owner_pid=?, heartbeat_at_utc=?, "
                "started_at_utc=COALESCE(started_at_utc, ?), version=version+1 "
                "WHERE id=? AND state=? AND version=?", (RUNNING, token, self.pid, now, now, job["id"], QUEUED,
                                                         job["version"]))
            if cur.rowcount != 1:
                return None
            self._event(conn, "STEP_CLAIMED", queue=job["queue"], job_id=job["id"],
                        detail={"step": job["next_step"], "pid": self.pid})
            return token
        finally:
            conn.close()

    def _heartbeat(self, job_id: str, token: str) -> None:
        conn = self._connect(True)
        try:
            conn.execute("UPDATE production_queue_jobs SET heartbeat_at_utc=? WHERE id=? AND owner_token=?",
                         (_ts(self.clock()), job_id, token))
        finally:
            conn.close()

    def _should_yield(self, job: Dict[str, Any], step: Dict[str, Any], slot_token: Optional[str]) -> bool:
        try:
            cur = self.get_job(job["id"]) or {}
            if cur.get("cancel_requested") or cur.get("owner_token") != job.get("owner_token"):
                return True
            _, paused = self._config_row()
            if paused.get(job["queue"]):
                return True
            if slot_token:
                for s in self.slots():
                    if s.get("token") == slot_token and s.get("preempt_requested"):
                        return True
                if self.shorts_view(self.clock()).get("active_jobs"):
                    return True
            return False
        except Exception:
            return True   # cannot tell -> yield (long-form never risks Shorts)

    def _finish_step(self, job: Dict[str, Any], token: str, result: Optional[Dict[str, Any]],
                     error: Optional[BaseException], checkpoint: Dict[str, Any]) -> str:
        cfg = self.config()
        now = self.clock()
        cur = self.get_job(job["id"]) or {}
        steps = job["steps"]
        if error is not None:
            attempts = int(job["attempts"]) + 1
            state = FAILED if attempts >= int(job["max_attempts"]) else QUEUED
            values = dict(state=state, attempts=attempts, error=f"{type(error).__name__}: {str(error)[:300]}",
                          not_before_utc=_ts(now + timedelta(minutes=float(cfg["retry_backoff_minutes"]))),
                          next_step=job["next_step"], event="STEP_FAILED")
        elif (result or {}).get("yielded"):
            state = CANCELLED if cur.get("cancel_requested") else QUEUED
            values = dict(state=state, attempts=job["attempts"], error=None, not_before_utc=None,
                          next_step=job["next_step"], event="STEP_YIELDED")
        else:
            nxt = int(job["next_step"]) + 1
            state = COMPLETED if nxt >= len(steps) else (CANCELLED if cur.get("cancel_requested") else QUEUED)
            values = dict(state=state, attempts=0, error=None, not_before_utc=None, next_step=nxt,
                          event="STEP_COMPLETED")
        conn = self._connect(True)
        try:
            conn.execute(
                "UPDATE production_queue_jobs SET state=?, attempts=?, error=?, not_before_utc=?, next_step=?, "
                "checkpoint_json=?, owner_token=NULL, owner_pid=NULL, last_progress_at_utc=CASE WHEN ? THEN ? "
                "ELSE last_progress_at_utc END, finished_at_utc=?, version=version+1 WHERE id=? AND owner_token=?",
                (values["state"], values["attempts"], values["error"], values["not_before_utc"], values["next_step"],
                 json.dumps(checkpoint, sort_keys=True, default=str)[:200000],
                 1 if values["event"] == "STEP_COMPLETED" else 0, _ts(now),
                 _ts(now) if values["state"] in TERMINAL else None, job["id"], token))
            self._event(conn, values["event"], queue=job["queue"], job_id=job["id"],
                        detail={"step": job["next_step"], "name": steps[job["next_step"]]["name"],
                                "state": values["state"], "error": values["error"]})
        finally:
            conn.close()
        return values["state"]

    def _run_step(self, job: Dict[str, Any], token: str, slot_token: Optional[str]) -> None:
        step = job["steps"][job["next_step"]]
        job = dict(job, owner_token=token)
        ctx = StepContext(self, job, step, slot_token)
        result, error = None, None
        try:
            fn = self.executors.get(job["kind"])
            if fn is None:
                raise RuntimeError(f"no executor registered for kind {job['kind']}")
            result = fn(ctx) or {}
        except BaseException as exc:  # noqa: BLE001
            error = exc
        finally:
            try:
                self._finish_step(job, token, result, error, ctx.checkpoint)
            finally:
                self.release_slot(slot_token)
                with self._lock:
                    self._running.pop(job["id"], None)

    def plan(self, *, now: Optional[datetime] = None, resources: Optional[Dict[str, Any]] = None,
             shorts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Admission decision for every waiting long-form job (no side effects)."""
        now = now or self.clock()
        cfg, paused = self._config_row()
        resources = resources if resources is not None else self.resource_probe()
        shorts = shorts if shorts is not None else self.shorts_view(now)
        jobs = self.list_jobs(states=(QUEUED, RUNNING), limit=200)
        running = [j for j in jobs if j["state"] == RUNNING and j["queue"] == LONG_FORM]
        light_running = sum(1 for j in running if j["steps"][j["next_step"]]["weight"] == LIGHT)
        heavy_free = any(s.get("token") is None for s in self.slots()[:cfg["max_heavy_concurrency"]])
        waiting = sorted((j for j in jobs if j["state"] == QUEUED and j["queue"] == LONG_FORM),
                         key=lambda j: (-self.effective_priority(j, now, cfg), j["enqueued_at_utc"]))
        decisions = []
        selected = None
        for j in waiting:
            step = j["steps"][j["next_step"]]
            nb = _parse(j.get("not_before_utc"))
            if not cfg["long_form_enabled"]:
                ok, why = False, "long-form production is disabled (operator setting, OFF by default)"
            elif paused.get(LONG_FORM):
                ok, why = False, "long-form queue is paused"
            elif j["kind"] not in self.executors:
                ok, why = False, f"no executor registered for kind '{j['kind']}' (long-form pipeline not installed)"
            elif nb and now < nb:
                ok, why = False, f"retry backoff until {j['not_before_utc']}"
            elif selected is not None:
                ok, why = False, f"behind {selected} (one step admitted per tick)"
            elif step["weight"] == HEAVY and not heavy_free:
                ok, why = False, "renderer occupied"
            else:
                ok, why = self.admit(step, now=now, resources=resources, shorts=shorts, light_running=light_running,
                                     cfg=cfg)
            waited_h = (now - (_parse(j.get("last_progress_at_utc")) or _parse(j["enqueued_at_utc"]) or now)
                        ).total_seconds() / 3600.0
            decisions.append({"job_id": j["id"], "step": j["next_step"], "step_name": step["name"],
                              "weight": step["weight"], "effective_priority": self.effective_priority(j, now, cfg),
                              "admitted": ok, "reason": why, "waiting_hours": round(waited_h, 2),
                              "starving": waited_h >= float(cfg["starvation_alert_hours"])})
            if ok and selected is None:
                selected = j["id"]
        return {"at_utc": _ts(now), "selected": selected, "decisions": decisions, "resources": resources,
                "shorts": shorts, "config": cfg, "paused": paused, "light_running": light_running,
                "heavy_slot_free": heavy_free}

    async def tick(self) -> Dict[str, Any]:
        """One coordinator step inside the daemon recovery loop: recover, decide, start at most one step (in a
        thread, so the loop never blocks), and persist a snapshot for the dashboard."""
        import asyncio
        recovered = await asyncio.to_thread(self.recover)
        open_jobs = await asyncio.to_thread(self.list_jobs, states=(QUEUED, RUNNING), limit=1)
        if open_jobs or self.config()["long_form_enabled"]:
            p = await asyncio.to_thread(self.plan)
        else:   # idle (the default): no host sampling in the daemon; the dashboard samples on demand
            p = await asyncio.to_thread(self.plan, resources={"sampled": False,
                                                              "note": "long-form idle; not sampled by the daemon"})
        started = None
        if p["selected"]:
            job = await asyncio.to_thread(self.get_job, p["selected"])
            if job and job["state"] == QUEUED:
                step = job["steps"][job["next_step"]]
                slot_token = None
                if step["weight"] == HEAVY:
                    slot_token = await asyncio.to_thread(self.acquire_slot, LONG_FORM, job["id"], step["name"])
                if step["weight"] == LIGHT or slot_token:
                    token = await asyncio.to_thread(self._claim, job)
                    if token:
                        job = await asyncio.to_thread(self.get_job, job["id"])
                        t = threading.Thread(target=self._run_step, args=(job, token, slot_token),
                                             name=f"longform-{job['id']}", daemon=True)
                        with self._lock:
                            self._running[job["id"]] = t
                        t.start()
                        started = {"job_id": job["id"], "step": job["next_step"], "weight": step["weight"]}
                    else:
                        await asyncio.to_thread(self.release_slot, slot_token)
        await asyncio.to_thread(self._persist_snapshot, p, recovered, started)
        return {"recovered": recovered, "started": started, "plan": p}

    def _persist_snapshot(self, p: Dict[str, Any], recovered: List[Dict[str, Any]],
                          started: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            threads = sum(1 for t in self._running.values() if t.is_alive())
        snap = {"pid": self.pid, "at_utc": p["at_utc"], "decisions": p["decisions"][:20], "selected": p["selected"],
                "started": started, "recovered": recovered, "long_form_threads": threads,
                "shorts_in_process_workers": (p.get("shorts") or {}).get("in_process_workers"),
                "resources": p["resources"]}
        conn = self._connect(True)
        try:
            conn.execute("INSERT INTO production_coordinator_state (id, pid, snapshot_json, updated_at_utc) "
                         "VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET pid=excluded.pid, "
                         "snapshot_json=excluded.snapshot_json, updated_at_utc=excluded.updated_at_utc",
                         (self.pid, json.dumps(snap, sort_keys=True, default=str), _ts(self.clock())))
        finally:
            conn.close()

    # -- status (dashboard; read-only) --------------------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        now = self.clock()
        p = self.plan(now=now)
        conn = self._connect(False)
        try:
            st = conn.execute("SELECT * FROM production_coordinator_state WHERE id=1").fetchone()
            events = [dict(r) for r in conn.execute(
                "SELECT ts_utc, queue, job_id, event, actor, detail FROM production_queue_events "
                "ORDER BY id DESC LIMIT 40").fetchall()]
        finally:
            conn.close()
        daemon = json.loads(st["snapshot_json"]) if st else {}
        daemon_age = (now - (_parse(st["updated_at_utc"]) or now)).total_seconds() if st else None
        slots = self.slots()
        res = p["resources"]
        shorts = p["shorts"]
        lf_jobs = self.list_jobs(limit=50)
        dec = {d["job_id"]: d for d in p["decisions"]}
        for j in lf_jobs:
            j.pop("owner_token", None)
            j["decision"] = dec.get(j["id"])
            j["step_count"] = len(j["steps"])
            j["checkpoint_keys"] = sorted(j.pop("checkpoint", {}).keys())
        try:
            from backend.app.services.control_center_service import get_max_concurrency
            shorts_conc = get_max_concurrency()
        except Exception:
            shorts_conc = None
        occupied = [s for s in slots[:p["config"]["max_heavy_concurrency"]] if s.get("token")]
        return {
            "at_utc": _ts(now),
            "queues": {
                SHORTS: {"source": "scheduler (24-cycle schedule, recovery loop, retry policy, persistent_jobs)",
                         "profile": shorts.get("profile"), "cycles": shorts.get("cycles"),
                         "next_start_utc": shorts.get("next_start_utc"), "active_jobs": shorts.get("active_jobs", []),
                         "state_known": shorts.get("known"), "logical_concurrency": shorts_conc,
                         "priority": "highest (always first for the renderer)",
                         "publishing_cadence": "separate: the publishing queue and policy are unchanged"},
                LONG_FORM: {"enabled": p["config"]["long_form_enabled"], "paused": bool(p["paused"].get(LONG_FORM)),
                            "executors": sorted(k for k in self.executors), "jobs": lf_jobs,
                            "waiting": sum(1 for j in lf_jobs if j["state"] == QUEUED),
                            "running": sum(1 for j in lf_jobs if j["state"] == RUNNING),
                            "starving": [d["job_id"] for d in p["decisions"] if d["starving"]],
                            "selected_next": p["selected"]},
            },
            "renderer": {"heavy_slots": p["config"]["max_heavy_concurrency"],
                         "benchmarked_max_heavy": BENCHMARKED_MAX_HEAVY, "occupied": len(occupied),
                         "holders": [{k: s.get(k) for k in ("slot", "holder_queue", "holder_job_id", "holder_step",
                                                            "holder_pid", "acquired_at_utc", "preempt_requested")}
                                     for s in occupied],
                         "ffmpeg_processes": res.get("ffmpeg_processes"),
                         "ffmpeg_working_set_mb": res.get("ffmpeg_working_set_mb")},
            "resources": res,
            "execution_model": {
                "logical_jobs": {"shorts_active": len(shorts.get("active_jobs") or []),
                                 "long_form_open": sum(1 for j in lf_jobs if j["state"] in (QUEUED, RUNNING))},
                "worker_threads": {"shorts": daemon.get("shorts_in_process_workers"),
                                   "long_form": daemon.get("long_form_threads"),
                                   "source": "scheduler daemon snapshot"},
                "os_processes": {"daemon_pid": daemon.get("pid"), "ffmpeg": res.get("ffmpeg_processes"),
                                 "ffmpeg_pids": res.get("ffmpeg_pids", [])},
            },
            "recovery": {"daemon_snapshot_age_seconds": round(daemon_age, 1) if daemon_age is not None else None,
                         "daemon_responding": daemon_age is not None and daemon_age < 300,
                         "last_recovered": daemon.get("recovered", []),
                         "last_started": daemon.get("started")},
            "config": p["config"],
            "decisions": p["decisions"],
            "events": events,
            "safety": "Operator-enqueued work only. No publishing, schedule, configuration, restart, deletion or "
                      "Autopilot action. Shorts proceeds unchanged if the coordinator is unavailable.",
        }


_singleton: Optional[ProductionCoordinator] = None
_singleton_lock = threading.Lock()


def get_coordinator() -> ProductionCoordinator:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = ProductionCoordinator()
        return _singleton
