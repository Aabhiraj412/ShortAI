"""Stage 3.36 Autopilot continuous production controller.

Operational state (is the controller running?)      Autonomy mode (what may it do?) = effective level of the
                                                     ``production`` category = min(global, production)
    OFF ──start──> STARTING ──checks ok──> ON          OFF                 -> cannot run (start is BLOCKED)
                      │  └──check failed──> BLOCKED    OBSERVE             -> OBSERVER: monitor and record decisions
    ON ──stop──> STOPPING ──in-flight done──> OFF       RECOMMEND           -> ADVISOR: + propose escalations
    ON ──mandatory safety failure──> BLOCKED            APPROVED_EXECUTION  -> EXECUTOR (only with active standing
    BLOCKED ──start──> STARTING;  BLOCKED ──stop──> OFF                        approvals for both production actions,
    STARTING ──stop──> OFF                                                    no kill switch, no open breaker):
                                                                              owns production and dispatches

Design rules (see docs/STAGE_3_36_AUTOPILOT.md):

* The controller is NOT a second scheduler. It runs as one tick inside the scheduler daemon's existing recovery
  loop. The 24-cycle cron jobs keep firing unchanged; while the controller owns production they hand each fired
  occurrence to it (``production_ownership``), and it dispatches through the SAME ``run_cycle_generation`` /
  ``dispatch_eligible_recoveries`` code with its owner token. It never creates cycles or jobs of its own.
* Every dispatch goes through the 3.32B control plane (closed registry, payload-bound signed approval, daily limit,
  execution lease, circuit breaker, hash-chained audit). Unknown or prohibited actions are refused there.
* One renderer: nothing is dispatched while any production job is in progress, a dispatch is running in this
  process, or a worker may still be alive. A stale heartbeat alone never reclaims a job (worker_registry checks
  the process).
* Ownership is released only when the controller's own dispatch has finished; occurrences handed to it but not
  dispatched are replayed through the normal scheduler path on release, inside their existing recovery windows.
* State, transitions, decisions and dispatches are persisted, so a daemon restart resumes safely (STARTING again,
  with every precondition re-checked); a claim of a dead process lapses to the scheduler automatically.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

from backend.app.autonomy import decision_engine as de
from backend.app.autonomy import registry
from backend.app.autonomy.control_plane import (APPROVED, ControlPlane, ControlPlaneError, PermissionDenied,
                                                canonical_json, redact, safe_error, validate_operator, _clean_reason)
from backend.app.autonomy.production_ownership import OWNER_AUTOPILOT, OWNER_SCHEDULER, OwnershipStore
from backend.app.autonomy.registry import Level

logger = logging.getLogger(__name__)

OFF, STARTING, ON, STOPPING, BLOCKED = "OFF", "STARTING", "ON", "STOPPING", "BLOCKED"
STATES = (OFF, STARTING, ON, STOPPING, BLOCKED)
TRANSITIONS: Dict[str, tuple] = {
    OFF: (STARTING,),
    STARTING: (ON, BLOCKED, OFF),
    ON: (STOPPING, BLOCKED),
    STOPPING: (OFF, BLOCKED),
    BLOCKED: (STARTING, OFF, STOPPING),
}
ROLE_NONE, OBSERVER, ADVISOR, EXECUTOR = "NONE", "OBSERVER", "ADVISOR", "EXECUTOR"
ACTOR = "autopilot-controller"
CATEGORY = "production"
SCHEDULED_ACTION = "production.scheduled_cycle.dispatch"
RECOVERY_ACTION = "production.recovery.dispatch"
PRODUCTION_ACTIONS = (SCHEDULED_ACTION, RECOVERY_ACTION)
ACTIVE_STATUSES = ("QUEUED", "RUNNING", "RETRYING", "PAUSED", "WAITING_FOR_INTERNET", "RETRY_SCHEDULED",
                   "HANDOFF_PENDING", "INTERRUPTED")
RECOVERABLE = ("QUEUED", "RETRYING", "RETRY_SCHEDULED", "INTERRUPTED", "PAUSED", "WAITING_FOR_INTERNET")
SAFE_JOB_STATES = ("COMPLETED", "PUBLISHED", "FAILED", "BLOCKED_QC", "RECOVERY_WINDOW_EXPIRED", "MISSED",
                   "RETRY_SCHEDULED", "HANDOFF_PENDING", "WAITING_FOR_INTERNET", "INTERRUPTED")
SAFE_RESULT_STATES = ("SKIPPED", "DEFERRED", "RECOVERY_WINDOW_EXPIRED", "WAITING_FOR_INTERNET")
CONTROLLER_STALE = timedelta(minutes=3)
OFF_HEARTBEAT_SECONDS = 60.0        # liveness evidence while Autopilot is OFF (one small UPDATE a minute)
DECISION_REPEAT = timedelta(minutes=30)
DECISION_MIN_GAP = timedelta(minutes=5)
ESCALATION_REPEAT = timedelta(hours=6)
AUDIT_VERIFY_EVERY = timedelta(minutes=30)
ERROR_LIMIT_PER_HOUR = 3
TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
REQUIRED_PROHIBITED = ("youtube.video.publish", "youtube.community.publish", "production.schedule.change",
                       "production.config.modify", "process.daemon.restart", "process.dashboard.restart",
                       "production.data.delete", "git.destructive", "git.branch.change", "render.worker.launch",
                       "production.cycle.create", "recovery.deadline.change", "quality_checks.bypass",
                       "duplicate_detection.bypass", "autonomy.self_policy.change", "external.communication.send")

SCHEMA = """
CREATE TABLE IF NOT EXISTS autopilot_controller_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'NONE',
    mode TEXT NOT NULL DEFAULT 'OFF',
    blocked_reason TEXT,
    state_since_utc TEXT NOT NULL,
    requested_by TEXT,
    request_reason TEXT,
    controller_pid INTEGER,
    heartbeat_at_utc TEXT,
    last_decision_signature TEXT,
    last_decision_outcome TEXT,
    last_decision_at_utc TEXT,
    last_audit_verify_utc TEXT,
    current_json TEXT NOT NULL DEFAULT '{}',
    last_success_json TEXT NOT NULL DEFAULT '{}',
    errors_json TEXT NOT NULL DEFAULT '[]',
    escalations_json TEXT NOT NULL DEFAULT '{}',
    backoff_until_utc TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autopilot_transitions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at_utc TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS autopilot_decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at_utc TEXT NOT NULL,
    state TEXT NOT NULL,
    role TEXT NOT NULL,
    mode TEXT NOT NULL,
    outcome TEXT NOT NULL,
    executed INTEGER NOT NULL DEFAULT 0,
    explanation TEXT NOT NULL,
    decision_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autopilot_decisions_at ON autopilot_decisions (at_utc);
CREATE TABLE IF NOT EXISTS autopilot_dispatches (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    action_type TEXT NOT NULL,
    job_id TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    outcome TEXT NOT NULL,
    result_status TEXT,
    job_status TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_autopilot_dispatches_job ON autopilot_dispatches (job_id);
"""


class InvalidTransition(ControlPlaneError):
    code = 409


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(ts or "", TS_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _jl(raw: Optional[str], default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


# ---- process runtime (set only inside the scheduler daemon) -------------------------------------------------
_RUNTIME: Optional["ProductionController"] = None
_RUNTIME_LOCK = threading.Lock()


def runtime() -> Optional["ProductionController"]:
    return _RUNTIME


class ProductionController:
    def __init__(self, cp: Optional[ControlPlane] = None, *, ownership: Optional[OwnershipStore] = None,
                 clock: Optional[Callable[[], datetime]] = None, pid: Optional[int] = None,
                 pid_alive: Optional[Callable[[int], bool]] = None,
                 snapshot_fn: Optional[Callable[["ProductionController"], Dict[str, Any]]] = None,
                 scheduler_probe: Optional[Callable[[], Dict[str, Any]]] = None,
                 dispatch_scheduled: Optional[Callable[..., Any]] = None,
                 dispatch_recovery: Optional[Callable[..., Any]] = None,
                 replay: Optional[Callable[..., Any]] = None,
                 job_state: Optional[Callable[[str], Dict[str, Any]]] = None):
        if cp is None:
            from backend.app.autonomy.control_plane import get_control_plane
            cp = get_control_plane()
        self.cp = cp
        self.clock = clock or cp.clock
        self.pid = pid if pid is not None else os.getpid()
        self.pid_alive = pid_alive or cp.pid_alive
        self.ownership = ownership or OwnershipStore(cp.db_path, clock=self._now, pid_alive=self.pid_alive, pid=self.pid)
        self._snapshot_fn = snapshot_fn or gather_snapshot
        self._scheduler_probe = scheduler_probe or _default_scheduler_probe
        self._dispatch_scheduled = dispatch_scheduled or _default_dispatch_scheduled
        self._dispatch_recovery = dispatch_recovery or _default_dispatch_recovery
        self._replay = replay or _default_replay
        self._job_state = job_state or _default_job_state
        self._schema_ready = False
        self._off_heartbeat_at = float("-inf")
        self._schema_lock = threading.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.owner_token: Optional[str] = None
        self._inflight: Optional[asyncio.Task] = None
        self._inflight_info: Dict[str, Any] = {}
        self._replay_task: Optional[asyncio.Task] = None
        self._recovered = False
        self.last_tick: Dict[str, Any] = {}

    # -- time / storage -------------------------------------------------------------------------------------
    def _now(self) -> datetime:
        n = self.clock()
        return n if n.tzinfo else n.replace(tzinfo=timezone.utc)

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        self._ensure()
        with self.cp._tx() as conn:
            yield conn

    @contextmanager
    def _rd(self) -> Iterator[Any]:
        self._ensure()
        with self.cp._read() as conn:
            yield conn

    def _ensure(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            conn = self.cp._connect()
            try:
                conn.executescript(SCHEMA)
                conn.execute("INSERT OR IGNORE INTO autopilot_controller_state (id, state, state_since_utc, version, "
                             "updated_at_utc) VALUES (1, ?, ?, 0, ?)", (OFF, _ts(self._now()), _ts(self._now())))
            finally:
                conn.close()
            self.ownership.ensure()
            self._schema_ready = True

    def _state_row(self, conn) -> Dict[str, Any]:
        return dict(conn.execute("SELECT * FROM autopilot_controller_state WHERE id=1").fetchone())

    def load(self) -> Dict[str, Any]:
        with self._rd() as conn:
            return self._state_row(conn)

    def _update(self, conn, row: Dict[str, Any], **values: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in values)
        cur = conn.execute(f"UPDATE autopilot_controller_state SET {cols}, version=version+1, updated_at_utc=? "
                           f"WHERE id=1 AND version=?", (*values.values(), _ts(self._now()), row["version"]))
        if cur.rowcount != 1:
            raise InvalidTransition("controller state changed concurrently; re-read and retry")
        row.update(values)
        row["version"] += 1

    def _transition(self, conn, row: Dict[str, Any], to: str, actor: str, reason: str,
                    details: Optional[Dict[str, Any]] = None, **extra: Any) -> None:
        frm = row["state"]
        if to not in TRANSITIONS.get(frm, ()):
            self.cp._audit(conn, "AUTOPILOT_TRANSITION_REJECTED", actor, previous_state=frm, new_state=to,
                           reason=f"invalid transition {frm} -> {to}")
            raise InvalidTransition(f"invalid Autopilot transition {frm} -> {to}")
        now = _ts(self._now())
        values = dict(state=to, state_since_utc=now, **extra)
        if to == BLOCKED:
            values.setdefault("blocked_reason", reason[:500])
        elif to in (OFF, STARTING, ON):
            values.setdefault("blocked_reason", None)
        if to == OFF:
            values.setdefault("role", ROLE_NONE)
        self._update(conn, row, **values)
        conn.execute("INSERT INTO autopilot_transitions (at_utc, from_state, to_state, actor, reason, details_json) "
                     "VALUES (?,?,?,?,?,?)", (now, frm, to, str(actor)[:100], redact(reason)[:500],
                                              canonical_json(redact(details or {}))))
        self.cp._audit(conn, "AUTOPILOT_STATE_CHANGED", actor, previous_state=frm, new_state=to, reason=reason,
                       details=details or {})

    # -- operator requests (dashboard process) --------------------------------------------------------------
    def request_start(self, operator: str, reason: str) -> Dict[str, Any]:
        op = validate_operator(operator)
        reason = _clean_reason(reason)
        with self._tx() as conn:
            row = self._state_row(conn)
            if row["state"] not in (OFF, BLOCKED):
                self.cp._audit(conn, "AUTOPILOT_TRANSITION_REJECTED", f"operator:{op}", previous_state=row["state"],
                               new_state=STARTING, reason=f"start refused: Autopilot is {row['state']}")
                raise InvalidTransition(f"Autopilot is {row['state']}; start is possible only from OFF or BLOCKED")
            self._transition(conn, row, STARTING, f"operator:{op}", f"start requested: {reason}",
                             requested_by=op, request_reason=reason)
        return self.status()

    def request_stop(self, operator: str, reason: str) -> Dict[str, Any]:
        op = validate_operator(operator)
        reason = _clean_reason(reason)
        owned = self.ownership.read().get("owner") == OWNER_AUTOPILOT
        with self._tx() as conn:
            row = self._state_row(conn)
            st = row["state"]
            if st == ON or (st == BLOCKED and owned):
                to = STOPPING
            elif st in (STARTING, BLOCKED):
                to = OFF
            else:
                self.cp._audit(conn, "AUTOPILOT_TRANSITION_REJECTED", f"operator:{op}", previous_state=st,
                               new_state=STOPPING, reason=f"stop refused: Autopilot is {st}")
                raise InvalidTransition(f"Autopilot is {st}; nothing to stop")
            self._transition(conn, row, to, f"operator:{op}", f"stop requested: {reason}",
                             requested_by=op, request_reason=reason)
        return self.status()

    # -- permissions --------------------------------------------------------------------------------------------
    def mode_and_role(self) -> Dict[str, Any]:
        cfg, err = self.cp._safe_config()
        ks = self.cp.kill_switches(cfg, err)
        mode = self.cp._effective(cfg, CATEGORY)
        why: List[str] = []
        if err:
            why.append(f"autonomy configuration unreadable: {err}")
        grants = cfg.get("standing_approvals") or {}
        now_s = _ts(self._now())
        missing = [a for a in PRODUCTION_ACTIONS
                   if not grants.get(a) or str(grants[a].get("expires_at_utc", "")) <= now_s]
        breakers = {}
        try:
            with self.cp._read() as conn:
                for a in PRODUCTION_ACTIONS:
                    b = self.cp._breaker(conn, a)
                    if b.get("open"):
                        breakers[a] = b
        except Exception as exc:
            why.append(f"circuit-breaker state unreadable: {safe_error(exc)}")
        if mode >= Level.APPROVED_EXECUTION and not ks["execution_gate_closed"] and not missing and not breakers and not err:
            role = EXECUTOR
        else:
            if mode >= Level.APPROVED_EXECUTION:
                if ks["execution_gate_closed"]:
                    why.append(ks["execution_gate"])
                if missing:
                    why.append("no active standing approval for " + ", ".join(missing))
                if breakers:
                    why.append("circuit breaker open for " + ", ".join(breakers))
            elif mode < Level.APPROVED_EXECUTION:
                why.append(f"autonomy mode is {mode.name}; production dispatch needs APPROVED_EXECUTION")
            role = ADVISOR if mode >= Level.RECOMMEND else (OBSERVER if mode >= Level.OBSERVE else ROLE_NONE)
        return {"mode": mode.name, "role": role, "global_level": cfg.get("global_level"),
                "category_level": (cfg.get("category_levels") or {}).get(CATEGORY, "OFF"),
                "kill_switches": ks, "missing_standing_approvals": missing, "open_breakers": breakers,
                "not_executor_because": why}

    # -- preconditions -------------------------------------------------------------------------------------------
    def preconditions(self, *, starting: bool) -> List[Dict[str, Any]]:
        checks: List[Dict[str, Any]] = []

        def add(cid: str, ok: bool, detail: str, blocking: bool = True) -> None:
            checks.append({"id": cid, "ok": bool(ok), "blocking": blocking, "detail": detail})

        add("controller_in_scheduler_daemon", runtime() is self and self.loop is not None,
            "controller runs inside the scheduler daemon" if runtime() is self else
            "the controller is not running inside the scheduler daemon")
        try:
            probe = self._scheduler_probe()
            add("scheduler_running", bool(probe.get("running")),
                f"scheduler running with {probe.get('cycles')} production cycles" if probe.get("running")
                else "the production scheduler is not running")
            add("schedule_loaded", int(probe.get("cycles") or 0) > 0, f"{probe.get('cycles')} cycles in the active profile")
        except Exception as exc:
            add("scheduler_running", False, f"scheduler state unreadable: {safe_error(exc)}")
        mr = self.mode_and_role()
        cfg_ok = not any("configuration unreadable" in w for w in mr["not_executor_because"])
        add("autonomy_configuration", cfg_ok, "autonomy configuration readable" if cfg_ok else mr["not_executor_because"][0])
        add("autonomy_mode", Level.parse(mr["mode"]) >= Level.OBSERVE,
            f"production autonomy mode {mr['mode']} (global {mr['global_level']}, production {mr['category_level']})"
            + ("" if Level.parse(mr["mode"]) >= Level.OBSERVE else "; set both to OBSERVE or higher in Autonomy Control"))
        gated = [a for a in REQUIRED_PROHIBITED if not registry.REGISTRY.get(a) or not registry.REGISTRY[a].prohibited]
        add("prohibited_actions_gated", not gated, "publishing, schedule, configuration, restart, deletion and git "
            "actions are permanently prohibited" if not gated else "NOT prohibited: " + ", ".join(gated))
        row = self.ownership.read()
        eff = self.ownership.effective(row)
        competing = eff["owner"] == OWNER_AUTOPILOT and int(row.get("owner_pid") or 0) != self.pid
        add("no_competing_owner", not competing, eff["reason"])
        st = self.load()
        other = st.get("controller_pid")
        dup = bool(other) and int(other) != self.pid and self._alive(int(other)) and \
            (_parse(st.get("heartbeat_at_utc")) or datetime.min.replace(tzinfo=timezone.utc)) > self._now() - CONTROLLER_STALE
        add("single_controller", not dup, f"another controller (process {other}) is active" if dup else "one controller")
        try:
            snap = self._snapshot_fn(self)
            live = [j for j in snap.get("active_jobs") or [] if j.get("worker_alive")
                    and j.get("status") in ("RUNNING", "RETRYING", "PAUSED")]
            maximum = max(1, int(snap.get("max_concurrency") or 1))
            add("single_worker", len(live) <= maximum,
                f"{len(live)} active production worker(s), limit {maximum}" + (
                    "" if len(live) <= maximum else ": " + ", ".join(j["job_id"] for j in live)))
        except Exception as exc:
            add("single_worker", False, f"production state unreadable: {safe_error(exc)}")
        if starting:
            try:
                chain = self.cp.verify_audit_chain()
                add("audit_chain", bool(chain.get("ok")), f"audit chain verified ({chain.get('events')} events)"
                    if chain.get("ok") else f"audit chain broken at event {chain.get('first_bad_seq')}")
            except Exception as exc:
                add("audit_chain", False, f"audit chain unreadable: {safe_error(exc)}")
        return checks

    def _alive(self, pid: int) -> bool:
        try:
            return pid == self.pid or bool(self.pid_alive(pid))
        except Exception:
            return True                         # unknown: assume alive (never take over a possibly live owner)

    # -- daemon tick ---------------------------------------------------------------------------------------------
    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        global _RUNTIME
        with _RUNTIME_LOCK:
            self.loop = loop
            _RUNTIME = self

    def inflight(self) -> bool:
        return self._inflight is not None and not self._inflight.done()

    async def tick(self) -> Dict[str, Any]:
        """One controller step. Never raises; cheap when OFF."""
        try:
            if self.loop is None:
                self.attach(asyncio.get_running_loop())
            if not self._recovered:
                await asyncio.to_thread(self._recover_after_restart)
                self._recovered = True
            row = await asyncio.to_thread(self.load)
            state = row["state"]
            if state == OFF:
                # Operator-dependency reduction: the heartbeat is the controller's liveness evidence. It used to stop
                # whenever Autopilot was OFF, so the dashboard claimed "scheduler daemon down" for a healthy daemon.
                # When OFF it is written at most once a minute (one small UPDATE); it never claims ownership.
                now_m = time.monotonic()
                if now_m - self._off_heartbeat_at >= OFF_HEARTBEAT_SECONDS:
                    await asyncio.to_thread(self._heartbeat)
                    self._off_heartbeat_at = now_m
                await self._release_if_idle("Autopilot is OFF")
                self.last_tick = {"state": OFF}
                return self.last_tick
            await asyncio.to_thread(self._heartbeat)
            if state == STARTING:
                await self._starting()
            elif state == ON:
                await self._on()
            elif state == STOPPING:
                await self._stopping()
            elif state == BLOCKED:
                await self._release_if_idle("Autopilot is BLOCKED")
            self.last_tick = {"state": (await asyncio.to_thread(self.load))["state"]}
            return self.last_tick
        except Exception as exc:                               # noqa: BLE001 - the daemon loop must survive
            logger.warning("[AUTOPILOT] controller tick failed: %s", safe_error(exc))
            await asyncio.to_thread(self._record_error, exc)
            return {"error": safe_error(exc)}

    def _heartbeat(self) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE autopilot_controller_state SET controller_pid=?, heartbeat_at_utc=? WHERE id=1",
                         (self.pid, _ts(self._now())))
        if self.owner_token:
            self.ownership.heartbeat(self.owner_token)

    def _record_error(self, exc: BaseException) -> None:
        try:
            now = self._now()
            with self._tx() as conn:
                row = self._state_row(conn)
                errs = [e for e in _jl(row["errors_json"], []) if (_parse(e.get("at")) or now) > now - timedelta(hours=1)]
                errs.append({"at": _ts(now), "error": safe_error(exc)})
                self._update(conn, row, errors_json=canonical_json(errs[-10:]),
                             backoff_until_utc=_ts(now + timedelta(seconds=min(900, 60 * 2 ** (len(errs) - 1)))))
                if len(errs) >= ERROR_LIMIT_PER_HOUR and row["state"] in (STARTING, ON):
                    self._transition(conn, row, BLOCKED, ACTOR, f"{len(errs)} controller errors within an hour; last: "
                                     f"{safe_error(exc)}")
        except Exception:
            logger.exception("[AUTOPILOT] could not record controller error")

    def _recover_after_restart(self) -> None:
        """First tick in a new process: a previous controller may have been interrupted."""
        row = self.load()
        prev = row.get("controller_pid")
        own = self.ownership.read()
        if own.get("owner") == OWNER_AUTOPILOT and int(own.get("owner_pid") or 0) != self.pid:
            res = self.ownership.release(force_lapsed=True)
            if res.get("released"):
                self.cp._audit_now("AUTOPILOT_OWNERSHIP_RELEASED", ACTOR, reason=f"claim of process "
                                   f"{own.get('owner_pid')} lapsed (process not running); scheduler owns production",
                                   details={"replay": res.get("deferred")})
                self._schedule_replay(res.get("deferred") or [])
        if prev and int(prev) != self.pid and row["state"] in (STARTING, ON, STOPPING):
            if self._alive(int(prev)) and (_parse(row.get("heartbeat_at_utc")) or datetime.min.replace(
                    tzinfo=timezone.utc)) > self._now() - CONTROLLER_STALE:
                with self._tx() as conn:
                    r = self._state_row(conn)
                    if r["state"] in (ON, STARTING, STOPPING):
                        self._transition(conn, r, BLOCKED, ACTOR, f"another controller process {prev} is still active")
                return
            with self._tx() as conn:
                r = self._state_row(conn)
                if r["state"] == ON:
                    self._transition(conn, r, BLOCKED, ACTOR, f"controller restarted (process {prev} ended); "
                                     f"re-validating", details={"previous_pid": prev})
                    self._transition(conn, r, STARTING, ACTOR, "automatic restart recovery: every precondition is "
                                     "re-checked before ON", details={"previous_pid": prev})
                elif r["state"] == STOPPING:
                    self._transition(conn, r, OFF, ACTOR, f"stop completed after controller restart (process {prev} "
                                     f"ended; its dispatch, if any, is reconciled by the existing recovery path)")
                self.cp._audit(conn, "AUTOPILOT_CONTROLLER_RECOVERED", ACTOR, reason=f"previous process {prev}",
                               details={"state": r["state"]})

    async def _starting(self) -> None:
        checks = await asyncio.to_thread(self.preconditions, starting=True)
        failed = [c for c in checks if c["blocking"] and not c["ok"]]
        mr = await asyncio.to_thread(self.mode_and_role)

        def apply():
            with self._tx() as conn:
                row = self._state_row(conn)
                if row["state"] != STARTING:
                    return
                if failed:
                    self._transition(conn, row, BLOCKED, ACTOR, "start blocked: " + "; ".join(
                        f"{c['id']}: {c['detail']}" for c in failed), details={"checks": checks})
                else:
                    self._transition(conn, row, ON, ACTOR, f"all preconditions passed; role {mr['role']}",
                                     details={"checks": checks, "mode": mr["mode"]}, role=mr["role"], mode=mr["mode"],
                                     last_audit_verify_utc=_ts(self._now()))
        await asyncio.to_thread(apply)
        if not failed:
            await self._on()

    async def _on(self) -> None:
        checks = await asyncio.to_thread(self.preconditions, starting=False)
        row = await asyncio.to_thread(self.load)
        if (_parse(row.get("last_audit_verify_utc")) or datetime.min.replace(tzinfo=timezone.utc)) \
                < self._now() - AUDIT_VERIFY_EVERY:
            chain = await asyncio.to_thread(self.cp.verify_audit_chain)
            checks.append({"id": "audit_chain", "ok": bool(chain.get("ok")), "blocking": True,
                           "detail": "audit chain verified" if chain.get("ok") else "audit chain broken"})
            await asyncio.to_thread(self._set, last_audit_verify_utc=_ts(self._now()))
        failed = [c for c in checks if c["blocking"] and not c["ok"]]
        if failed:
            def block():
                with self._tx() as conn:
                    r = self._state_row(conn)
                    if r["state"] == ON:
                        self._transition(conn, r, BLOCKED, ACTOR, "mandatory safety condition failed: " + "; ".join(
                            f"{c['id']}: {c['detail']}" for c in failed), details={"checks": checks})
            await asyncio.to_thread(block)
            await self._release_if_idle("Autopilot BLOCKED")
            return
        mr = await asyncio.to_thread(self.mode_and_role)
        role = mr["role"]
        if role == ROLE_NONE:
            await asyncio.to_thread(self._block_if_on, f"autonomy mode for production is {mr['mode']}")
            await self._release_if_idle("autonomy mode OFF")
            return
        if row.get("role") != role or row.get("mode") != mr["mode"]:
            await asyncio.to_thread(self._set_role, role, mr)
        await self._ownership_for(role)
        snap = await asyncio.to_thread(self._snapshot_fn, self)
        decision = de.decide(snap)
        executed = False
        backoff = _parse(row.get("backoff_until_utc"))
        if role == EXECUTOR and self.owner_token and decision["proceed"] and not self.inflight() \
                and not (backoff and backoff > self._now()):
            self._start_dispatch(decision)
            executed = True
        await asyncio.to_thread(self._record_decision, decision, role, mr["mode"], executed, snap)
        if role in (ADVISOR, EXECUTOR) and decision["escalations"]:
            await asyncio.to_thread(self._escalate, decision)
        await asyncio.to_thread(self._cleanup_deferred, snap, decision)

    async def _stopping(self) -> None:
        if self.inflight():
            await asyncio.to_thread(self._set, current_json=canonical_json(
                dict(self._inflight_info, note="stopping: waiting for the in-flight production to reach a safe state")))
            return
        await self._release_if_idle("Autopilot stopped")

        def finish():
            with self._tx() as conn:
                r = self._state_row(conn)
                if r["state"] == STOPPING and self.ownership.read().get("owner") != OWNER_AUTOPILOT:
                    self._transition(conn, r, OFF, ACTOR, "no in-flight Autopilot production; ownership returned to "
                                     "the scheduler", current_json="{}")
        await asyncio.to_thread(finish)

    def _block_if_on(self, reason: str) -> None:
        with self._tx() as conn:
            r = self._state_row(conn)
            if r["state"] == ON:
                self._transition(conn, r, BLOCKED, ACTOR, reason)

    def _set(self, **values: Any) -> None:
        with self._tx() as conn:
            r = self._state_row(conn)
            self._update(conn, r, **values)

    def _set_role(self, role: str, mr: Dict[str, Any]) -> None:
        with self._tx() as conn:
            r = self._state_row(conn)
            prev = r.get("role")
            self._update(conn, r, role=role, mode=mr["mode"])
            self.cp._audit(conn, "AUTOPILOT_ROLE_CHANGED", ACTOR, previous_state=prev, new_state=role,
                           reason="; ".join(mr["not_executor_because"]) or f"mode {mr['mode']}",
                           details={"mode": mr["mode"]})

    # -- ownership --------------------------------------------------------------------------------------------------
    async def _ownership_for(self, role: str) -> None:
        if role == EXECUTOR:
            if self.owner_token and self.ownership.token_valid(self.owner_token):
                return
            token = await asyncio.to_thread(self.ownership.acquire)
            if token:
                self.owner_token = token
                await asyncio.to_thread(self.cp._audit_now, "AUTOPILOT_OWNERSHIP_ACQUIRED", ACTOR,
                                        reason="handoff complete: scheduled occurrences are now dispatched by the "
                                               "Autopilot controller; active jobs were not interrupted")
                record_event("AUTOPILOT_OWNERSHIP_ACQUIRED", {"pid": self.pid})
            else:
                await asyncio.to_thread(self._block_if_on, "could not acquire production ownership (held by another "
                                                           "live owner or lost a concurrent claim)")
        else:
            await self._release_if_idle(f"role {role}: execution not permitted")

    async def _release_if_idle(self, why: str) -> None:
        if self.inflight():
            return
        row = await asyncio.to_thread(self.ownership.read)
        if row.get("owner") != OWNER_AUTOPILOT:
            self.owner_token = None
            return
        if int(row.get("owner_pid") or 0) != self.pid:
            res = await asyncio.to_thread(self.ownership.release, None, force_lapsed=True)
        else:
            res = await asyncio.to_thread(self.ownership.release, row.get("owner_token"))
        if res.get("released"):
            self.owner_token = None
            await asyncio.to_thread(self.cp._audit_now, "AUTOPILOT_OWNERSHIP_RELEASED", ACTOR,
                                    reason=f"{why}; the scheduler owns production again",
                                    details={"replayed": [d.get("job_id") for d in res.get("deferred") or []]})
            record_event("AUTOPILOT_OWNERSHIP_RELEASED", {"why": why})
            self._schedule_replay(res.get("deferred") or [])

    def _schedule_replay(self, deferred: List[Dict[str, Any]]) -> None:
        """Occurrences the schedule handed to the controller but it did not dispatch go back through the normal
        scheduler path (inside their own recovery windows), so a handback never loses a cycle."""
        now = self._now()
        todo = [d for d in deferred if (de._dt(d.get("recovery_deadline_ist")) or now) > now]
        if not todo:
            return
        loop = self.loop
        if loop is None:
            return
        coro = self._replay(todo)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._replay_task = loop.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, loop)

    # -- dispatch -------------------------------------------------------------------------------------------------
    def _start_dispatch(self, decision: Dict[str, Any]) -> None:
        sel = decision["selected"]
        self._inflight_info = {"job_id": sel["job_id"], "kind": sel["kind"], "started_at_utc": _ts(self._now()),
                               "decision": decision["signature"]}
        self._inflight = asyncio.get_running_loop().create_task(self._dispatch(decision))

    async def _dispatch(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        sel = decision["selected"]
        if sel["kind"] == "scheduled":
            action = SCHEDULED_ACTION
            payload = {"cycle_id": sel["cycle_id"], "job_id": sel["job_id"],
                       "scheduled_start_ist": sel.get("scheduled_start_ist") or ""}
        else:
            action = RECOVERY_ACTION
            payload = {"job_id": sel["job_id"], "status": sel.get("status") or "QUEUED"}
        await asyncio.to_thread(self._set, current_json=canonical_json(dict(self._inflight_info, action=action)))
        seq = None
        try:
            prop = await asyncio.to_thread(
                self.cp.propose, action, payload, reason=decision["explanation"][:480],
                evidence={"decision": decision["signature"], "outcome": decision["outcome"],
                          "considered": [{k: c.get(k) for k in ("job_id", "accepted", "reason")}
                                         for c in decision["considered"][:10]]},
                proposed_by=ACTOR)
            req = prop["request"]
            if req["status"] != APPROVED:
                await asyncio.to_thread(self._dispatch_row, None, action, sel["job_id"], "AWAITING_APPROVAL",
                                        {"request_id": req["id"], "status": req["status"]}, req["id"])
                return {"executed": False, "status": req["status"]}
            seq = await asyncio.to_thread(self._dispatch_row, None, action, sel["job_id"], "RUNNING", {}, req["id"])
            record_event("AUTOPILOT_DISPATCH_STARTED", {"action": action, "request_id": req["id"]}, job_id=sel["job_id"])
            res = await asyncio.to_thread(self.cp.execute, req["id"], ACTOR)
            job = await asyncio.to_thread(self._job_state, sel["job_id"])
            result = (((res.get("request") or {}).get("execution_result") or {}).get("result") or {})
            outcome = "COMPLETED" if res.get("executed") else (res.get("status") or "FAILED")
            await asyncio.to_thread(self._dispatch_row, seq, action, sel["job_id"], outcome,
                                    {"error": res.get("error") or res.get("reason"), "request_id": req["id"]},
                                    req["id"], result.get("result_status"), job.get("status"))
            if action == SCHEDULED_ACTION:
                await asyncio.to_thread(self.ownership.remove_deferred, sel["job_id"])
            if job.get("status") == "COMPLETED":
                await asyncio.to_thread(self._set, last_success_json=canonical_json(
                    {"job_id": sel["job_id"], "at_utc": _ts(self._now()), "topic": job.get("topic")}))
            record_event("AUTOPILOT_DISPATCH_FINISHED", {"action": action, "outcome": outcome,
                                                         "job_status": job.get("status")}, job_id=sel["job_id"])
            return res
        except (PermissionDenied, ControlPlaneError, registry.ProhibitedActionError, registry.UnknownActionError) as exc:
            await asyncio.to_thread(self._dispatch_row, seq, action, sel["job_id"], "REFUSED", {"error": safe_error(exc)})
            return {"executed": False, "error": safe_error(exc)}
        except Exception as exc:                                       # noqa: BLE001
            await asyncio.to_thread(self._dispatch_row, seq, action, sel["job_id"], "ERROR", {"error": safe_error(exc)})
            await asyncio.to_thread(self._record_error, exc)
            return {"executed": False, "error": safe_error(exc)}
        finally:
            try:
                await asyncio.to_thread(self._set, current_json="{}")
            except Exception:
                pass

    def _dispatch_row(self, seq, action, job_id, outcome, details, request_id=None, result_status=None,
                      job_status=None) -> Optional[int]:
        now = _ts(self._now())
        with self._tx() as conn:
            if seq is None:
                cur = conn.execute("INSERT INTO autopilot_dispatches (request_id, action_type, job_id, started_at_utc, "
                                   "finished_at_utc, outcome, result_status, job_status, details_json) VALUES "
                                   "(?,?,?,?,?,?,?,?,?)", (request_id, action, job_id, now,
                                                           None if outcome == "RUNNING" else now, outcome,
                                                           result_status, job_status, canonical_json(redact(details))))
                return cur.lastrowid
            conn.execute("UPDATE autopilot_dispatches SET finished_at_utc=?, outcome=?, result_status=?, job_status=?, "
                         "details_json=? WHERE seq=?", (now, outcome, result_status, job_status,
                                                        canonical_json(redact(details)), seq))
            return seq

    def already_dispatched(self) -> List[str]:
        with self._rd() as conn:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT job_id FROM autopilot_dispatches WHERE action_type=? AND outcome IN "
                "('RUNNING','COMPLETED','FAILED','TIMED_OUT','VERIFY_FAILED') AND started_at_utc >= ?",
                (SCHEDULED_ACTION, _ts(self._now() - timedelta(days=2))))]

    # -- decisions / escalations -------------------------------------------------------------------------------
    def _record_decision(self, d: Dict[str, Any], role: str, mode: str, executed: bool, snap: Dict[str, Any]) -> None:
        now = self._now()
        with self._tx() as conn:
            row = self._state_row(conn)
            last = _parse(row.get("last_decision_at_utc"))
            changed_core = row.get("last_decision_outcome") != d["outcome"] or executed
            changed = row.get("last_decision_signature") != d["signature"]
            due = last is None or now - last >= DECISION_REPEAT
            if not (changed_core or due or (changed and now - last >= DECISION_MIN_GAP)):
                return
            body = {k: d[k] for k in ("outcome", "proceed", "selected", "considered", "reasons", "bottlenecks",
                                      "resource_pressure", "quota_constraints", "repeated_failures",
                                      "recommendations", "escalations", "signature")}
            body["snapshot"] = {"resources": snap.get("resources"), "disk_free_gb": snap.get("disk_free_gb"),
                                "quota": snap.get("quota"), "active_jobs": snap.get("active_jobs"),
                                "deferred": snap.get("deferred")}
            conn.execute("INSERT INTO autopilot_decisions (at_utc, state, role, mode, outcome, executed, explanation, "
                         "decision_json) VALUES (?,?,?,?,?,?,?,?)",
                         (_ts(now), row["state"], role, mode, d["outcome"], 1 if executed else 0, d["explanation"],
                          canonical_json(redact(body))))
            self._update(conn, row, last_decision_signature=d["signature"], last_decision_outcome=d["outcome"],
                         last_decision_at_utc=_ts(now))
            if executed or changed_core:
                self.cp._audit(conn, "AUTOPILOT_DECISION", ACTOR, reason=d["explanation"][:480],
                               details={"outcome": d["outcome"], "role": role, "executed": executed,
                                        "selected": (d.get("selected") or {}).get("job_id")})

    def _escalate(self, d: Dict[str, Any]) -> None:
        now = self._now()
        row = self.load()
        sent = _jl(row.get("escalations_json"), {})
        for e in d["escalations"]:
            last = _parse(sent.get(e["id"]))
            if last and now - last < ESCALATION_REPEAT:
                continue
            sev = e.get("severity") if e.get("severity") in ("info", "low", "medium", "high") else "high"
            try:
                self.cp.propose("operator.notify", {"subject": f"[Autopilot] {e['id']}"[:120], "message": e["text"][:2000],
                                                    "severity": sev},
                                reason=f"Autopilot escalation: {e['text']}"[:480], proposed_by=ACTOR,
                                evidence={"decision": d["signature"]})
                outcome = "proposed"
            except Exception as exc:                                   # noqa: BLE001 - recorded, never raised
                outcome = f"recorded only ({safe_error(exc)[:160]})"
            self.cp._audit_now("AUTOPILOT_ESCALATION", ACTOR, reason=e["text"][:480],
                               details={"id": e["id"], "outcome": outcome})
            sent[e["id"]] = _ts(now)
        self._set(escalations_json=canonical_json(sent))

    def _cleanup_deferred(self, snap: Dict[str, Any], d: Dict[str, Any]) -> None:
        for c in d.get("considered") or []:
            if c.get("kind") != "scheduled" or c.get("accepted"):
                continue
            if "expired" in str(c.get("reason")) or "already dispatched" in str(c.get("reason")) \
                    or "already exists" in str(c.get("reason")):
                self.ownership.remove_deferred(c["job_id"])
                if "expired" in str(c.get("reason")):
                    self.cp._audit_now("AUTOPILOT_OCCURRENCE_EXPIRED", ACTOR, reason=f"{c['job_id']}: {c.get('reason')}")
                    record_event("AUTOPILOT_OCCURRENCE_EXPIRED", {"reason": c.get("reason")}, job_id=c["job_id"])

    # -- status (dashboard) ---------------------------------------------------------------------------------------
    def status(self, *, include_live: bool = True) -> Dict[str, Any]:
        row = self.load()
        now = self._now()
        own_row = self.ownership.read()
        eff = self.ownership.effective(own_row)
        hb = _parse(row.get("heartbeat_at_utc"))
        responding = hb is not None and now - hb <= CONTROLLER_STALE
        mr = self.mode_and_role()
        perms = self.cp.permissions()
        with self._rd() as conn:
            transitions = [dict(r) for r in conn.execute(
                "SELECT * FROM autopilot_transitions ORDER BY seq DESC LIMIT 40")]
            decisions = [dict(r) for r in conn.execute(
                "SELECT seq, at_utc, state, role, mode, outcome, executed, explanation FROM autopilot_decisions "
                "ORDER BY seq DESC LIMIT 25")]
            latest = conn.execute("SELECT decision_json FROM autopilot_decisions ORDER BY seq DESC LIMIT 1").fetchone()
            dispatches = [dict(r) for r in conn.execute("SELECT * FROM autopilot_dispatches ORDER BY seq DESC LIMIT 25")]
            blocked = [dict(r) for r in conn.execute(
                "SELECT seq, ts_utc, event_type, actor, action_type, reason FROM autonomy_audit_events WHERE event_type IN "
                "('EXECUTION_BLOCKED','PROPOSAL_REFUSED','PROHIBITED_ACTION_BLOCKED','UNKNOWN_ACTION_REJECTED',"
                "'AUTOPILOT_TRANSITION_REJECTED','BRIDGE_REFUSED') ORDER BY seq DESC LIMIT 20")]
            events = [dict(r) for r in conn.execute(
                "SELECT seq, ts_utc, event_type, actor, previous_state, new_state, reason FROM autonomy_audit_events "
                "WHERE event_type LIKE 'AUTOPILOT_%' ORDER BY seq DESC LIMIT 40")]
        for t in transitions:
            t["details"] = _jl(t.pop("details_json", None), {})
        for x in dispatches:
            x["details"] = _jl(x.pop("details_json", None), {})
        pending = [r for r in self.cp.list_requests("PENDING", 50)]
        st = row["state"]
        owned = eff["owner"] == OWNER_AUTOPILOT
        out: Dict[str, Any] = {
            "state": st, "state_since_utc": row["state_since_utc"], "blocked_reason": row.get("blocked_reason"),
            "role": row.get("role") if st in (ON, STOPPING) else ROLE_NONE,
            "requested_by": row.get("requested_by"), "request_reason": row.get("request_reason"),
            "mode": mr["mode"], "global_level": perms.get("global_level"),
            "category_levels": perms.get("category_levels"), "effective_levels": perms.get("effective_levels"),
            "role_if_on": mr["role"], "not_executor_because": mr["not_executor_because"],
            "kill_switches": {"any_active": mr["kill_switches"]["any_active"], "active": mr["kill_switches"]["active"]},
            "control_state": mr["kill_switches"].get("control_state"),
            "startup_config": mr["kill_switches"].get("startup_config"),
            "missing_standing_approvals": mr["missing_standing_approvals"], "open_breakers": mr["open_breakers"],
            "controller": {"pid": row.get("controller_pid"), "heartbeat_at_utc": row.get("heartbeat_at_utc"),
                           "responding": responding,
                           "note": (("controller idle and responding (Autopilot OFF; the scheduler owns production)"
                                     if st == OFF else "controller heartbeat fresh") if responding else
                                    ("no controller tick yet" if hb is None else
                                     "controller has not ticked for more than 3 minutes (scheduler daemon down or "
                                     "running older code)"))},
            "ownership": {"owner": eff["owner"], "reason": eff["reason"], "owner_pid": own_row.get("owner_pid"),
                          "acquired_at_utc": own_row.get("acquired_at_utc"),
                          "heartbeat_at_utc": own_row.get("heartbeat_at_utc"),
                          "deferred_occurrences": own_row.get("deferred", [])},
            "current_dispatch": _jl(row.get("current_json"), {}),
            "last_autopilot_success": _jl(row.get("last_success_json"), {}),
            "recent_decisions": decisions, "latest_decision": _jl(latest[0], {}) if latest else {},
            "dispatches": dispatches, "transitions": transitions, "events": events, "blocked_actions": blocked,
            "pending_approvals": pending,
            "errors": _jl(row.get("errors_json"), []), "backoff_until_utc": row.get("backoff_until_utc"),
            "controls": {
                "can_start": st in (OFF, BLOCKED),
                "can_stop": st in (STARTING, ON, BLOCKED),
                "start_note": ("start re-checks every precondition in the scheduler daemon; the result is shown "
                               "only after the controller has verified it"),
            },
            "pending_confirmation": st in (STARTING, STOPPING),
            "generated_at_utc": _ts(now),
        }
        if include_live:
            out["live"] = live_view()
        return out


# ---- events -----------------------------------------------------------------------------------------------------
def record_event(event: str, details: Dict[str, Any], *, job_id: Optional[str] = None) -> None:
    try:
        from backend.app.services.lifecycle_events import record_lifecycle_event
        record_lifecycle_event(event, "AUTOPILOT", job_id=job_id, details=details)
    except Exception:
        pass


# ---- defaults wired to the existing production infrastructure ----------------------------------------------------
def _default_scheduler_probe() -> Dict[str, Any]:
    from backend.app.scheduler.scheduler import scheduler
    from backend.app.scheduler.schedule_profiles import get_active_profile
    prof = get_active_profile()
    gens = [j for j in scheduler.get_jobs() if str(j.id).startswith("gen_")] if scheduler.running else []
    return {"running": bool(scheduler.running), "cycles": len(prof.cycles), "generation_jobs": len(gens)}


def _default_job_state(job_id: str) -> Dict[str, Any]:
    from backend.app.database.db import JobModel, SessionLocal
    with SessionLocal() as db:
        j = db.query(JobModel).filter(JobModel.id == job_id).first()
        if not j:
            return {"exists": False, "status": None}
        return {"exists": True, "status": j.status, "stage": j.current_stage, "topic": j.topic_title}


async def _default_dispatch_scheduled(payload: Dict[str, Any], token: str) -> Dict[str, Any]:
    from backend.app.scheduler.scheduler import run_cycle_generation
    return await run_cycle_generation(payload["cycle_id"], owner_token=token)


async def _default_dispatch_recovery(payload: Dict[str, Any], token: str) -> Dict[str, Any]:
    from backend.app.scheduler.scheduler import dispatch_eligible_recoveries
    res = await dispatch_eligible_recoveries(max_dispatches=1, owner_token=token, only_job_id=payload["job_id"])
    hit = [d for d in res.get("dispatched") or [] if d.get("job_id") == payload["job_id"]]
    return {"job_id": payload["job_id"], "status": (hit[0].get("result_status") if hit else "DEFERRED"),
            "dispatched": bool(hit), "expired": [e.get("job_id") for e in res.get("expired") or []]}


async def _default_replay(deferred: List[Dict[str, Any]]) -> None:
    from backend.app.scheduler.scheduler import run_cycle_generation
    for d in deferred:
        try:
            logger.info("[AUTOPILOT] handback: replaying %s through the scheduler path", d.get("job_id"))
            await run_cycle_generation(d["cycle_id"])
        except Exception as exc:                                       # noqa: BLE001
            logger.warning("[AUTOPILOT] replay of %s failed: %s", d.get("job_id"), safe_error(exc))


def _disk_free_gb() -> Optional[float]:
    try:
        from backend.app.core.paths import MEDIA_DIR
        return round(shutil.disk_usage(str(MEDIA_DIR)).free / 1e9, 1)
    except Exception:
        return None


def _quota_view() -> Dict[str, Any]:
    try:
        from backend.app.core.paths import DATABASE_PATH
        from backend.app.services.youtube_quota import UPLOADS, QuotaLedger
        u = QuotaLedger(str(DATABASE_PATH)).usage().get(UPLOADS) or {}
        blk = u.get("blocked")
        return {"uploads_blocked": bool(blk), "reason": (blk or {}).get("reason"),
                "blocked_until_ist": (blk or {}).get("blocked_until_ist"),
                "used_estimate": u.get("used_estimate"), "remaining_estimate": u.get("remaining_estimate"),
                "daily_limit": u.get("daily_limit"), "unit": u.get("unit"), "source": "local estimate (Pacific day)"}
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}


def _jobs_view(now_utc: datetime) -> Dict[str, Any]:
    from backend.app.database.db import JobModel, SessionLocal
    from backend.app.scheduler import retry_policy, worker_registry
    naive_now = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
    active, recov, terminal, handoffs = [], [], [], 0
    with SessionLocal() as db:
        rows = db.query(JobModel).filter(JobModel.status.in_(ACTIVE_STATUSES)).all()
        for j in rows:
            if (j.id or "").startswith(("test_", "mock_")):
                continue
            dl = retry_policy.recovery_deadline_utc(j)
            dl_s = dl.replace(tzinfo=timezone.utc).isoformat() if dl else None
            if j.status == "HANDOFF_PENDING":
                handoffs += 1
                continue
            alive = worker_registry.worker_alive(j.id, worker_registry.claimed_worker_id(j))
            item = {"job_id": j.id, "status": j.status, "stage": j.current_stage,
                    "heartbeat_utc": j.heartbeat_at.replace(tzinfo=timezone.utc).isoformat() if j.heartbeat_at else None,
                    "worker_alive": bool(alive), "worker_id": worker_registry.claimed_worker_id(j),
                    "stalled": bool(worker_registry.is_stalled(j)) if j.status == "RUNNING" else False,
                    "started_utc": (j.actual_start_at.replace(tzinfo=timezone.utc).isoformat()
                                    if j.actual_start_at else None), "deadline_utc": dl_s}
            if j.status in ("RUNNING", "RETRYING", "PAUSED") or (j.status == "QUEUED" and alive):
                active.append(item)
            if j.status in RECOVERABLE or item["stalled"]:
                due = j.status != retry_policy.STATUS_RETRY_SCHEDULED or retry_policy.is_retry_due(j, naive_now)
                nxt = retry_policy.next_retry_at(j) if j.status == retry_policy.STATUS_RETRY_SCHEDULED else None
                if not (j.status in ("RUNNING", "RETRYING", "PAUSED") and alive):
                    recov.append({"job_id": j.id, "status": j.status, "due": bool(due),
                                  "next_retry_utc": nxt.isoformat() + "Z" if nxt else None, "deadline_utc": dl_s,
                                  "reason": j.recovery_reason or j.status})
        for j in (db.query(JobModel).filter(JobModel.status.in_(("COMPLETED", "PUBLISHED", "FAILED", "BLOCKED_QC",
                                                                  "RECOVERY_WINDOW_EXPIRED", "MISSED")))
                  .order_by(JobModel.updated_at.desc()).limit(10).all()):
            if (j.id or "").startswith(("test_", "mock_")):
                continue
            cls = None
            try:
                cls = ((json.loads(j.checkpoint_data_json or "{}").get("retry_policy") or {}).get("classification"))
            except Exception:
                pass
            terminal.append({"job_id": j.id, "status": j.status, "classification": cls,
                             "error": (j.error_details or "")[:300],
                             "updated_utc": j.updated_at.replace(tzinfo=timezone.utc).isoformat() if j.updated_at else None,
                             "topic": j.topic_title})
    return {"active": active, "recovery": recov, "terminal": terminal, "pending_handoffs": handoffs}


def gather_snapshot(ctrl: Optional[ProductionController] = None) -> Dict[str, Any]:
    from backend.app.services.control_center_service import (evaluate_resource_safety, get_max_concurrency,
                                                             get_operating_mode, get_production_control_state)
    now = ctrl._now() if ctrl else datetime.now(timezone.utc)
    jobs = _jobs_view(now)
    own = (ctrl.ownership if ctrl else OwnershipStore()).read()
    deferred = own.get("deferred", [])
    statuses = {}
    if deferred:
        from backend.app.database.db import JobModel, SessionLocal
        with SessionLocal() as db:
            for d in deferred:
                j = db.query(JobModel).filter(JobModel.id == d.get("job_id")).first()
                if j is not None:
                    statuses[d["job_id"]] = j.status
    try:
        from backend.app.scheduler.scheduler import dispatching_job_ids
        dispatching = dispatching_job_ids()
    except Exception:
        dispatching = []
    return {"now_utc": now, "operating_mode": get_operating_mode(),
            "production_control": get_production_control_state().get("state"),
            "max_concurrency": get_max_concurrency(), "active_jobs": jobs["active"], "dispatching": dispatching,
            "deferred": deferred, "job_status": statuses, "recovery_candidates": jobs["recovery"],
            "already_dispatched": ctrl.already_dispatched() if ctrl else [], "recent_terminal": jobs["terminal"],
            "pending_handoffs": jobs["pending_handoffs"], "resources": evaluate_resource_safety(),
            "disk_free_gb": _disk_free_gb(), "quota": _quota_view()}


def live_view() -> Dict[str, Any]:
    """Live production state for the dashboard (read-only; any part may be unavailable)."""
    out: Dict[str, Any] = {}
    now = datetime.now(timezone.utc)
    try:
        jobs = _jobs_view(now)
        out["active_jobs"] = jobs["active"]
        out["recovery_activity"] = jobs["recovery"]
        out["pending_handoffs"] = jobs["pending_handoffs"]
        out["recent_terminal"] = jobs["terminal"][:6]
        out["last_successful_production"] = next((j for j in jobs["terminal"] if j["status"] in ("COMPLETED", "PUBLISHED")), None)
        live = [j for j in jobs["active"] if j.get("worker_alive")]
        out["renderer"] = {"active": bool(live), "jobs": [j["job_id"] for j in live],
                           "note": "single renderer: at most one production worker"}
    except Exception as exc:
        out["jobs_error"] = safe_error(exc)
    try:
        from backend.app.services.control_center_service import evaluate_resource_safety, get_max_concurrency
        out["resources"] = dict(evaluate_resource_safety(), disk_free_gb=_disk_free_gb(),
                                max_concurrency=get_max_concurrency())
    except Exception as exc:
        out["resources"] = {"error": safe_error(exc)}
    out["quota"] = _quota_view()
    try:
        from backend.app.services.deployment_diagnostics import _worker_heartbeat
        out["worker_heartbeat"] = _worker_heartbeat()
    except Exception as exc:
        out["worker_heartbeat"] = {"error": safe_error(exc)}
    return out


_instance: Optional[ProductionController] = None


def get_controller() -> ProductionController:
    global _instance
    if _instance is None:
        _instance = ProductionController()
    return _instance
