"""Stage 3.32I: phased Autopilot activation lifecycle on top of the 3.32B control plane.

Phases (each maps to control-plane levels, applied through ``ControlPlane.set_level`` so every change is audited):

=====  =======================  ==========================================================================
0      OFF                      every level OFF (the default)
1      OBSERVE                  diagnostics, recommendations, reporting, self_healing, notifications: OBSERVE
2      RECOMMEND                the same categories at RECOMMEND (proposals queue; nothing executes)
3      RESTRICTED_EXECUTION     diagnostics + reporting at APPROVED_EXECUTION, others RECOMMEND
4      EXPANSION                additionally the categories of explicitly authorized low-risk actions
=====  =======================  ==========================================================================

Rules enforced here:

* **One step at a time.** ``advance`` only moves to ``current + 1``; nothing ever advances automatically (the
  observation tick never calls ``advance``).
* **Readiness first.** Every advance re-runs the readiness checks for the target phase; any blocker refuses it and
  the refusal is persisted (``ACTIVATION_REFUSED``).
* **Authorization.** OBSERVE needs an operator and ``confirm``. RECOMMEND and above also need the exact phrase
  ``AUTHORIZE <PHASE>``. Phases 3 and 4 need the operator-created ``autonomy_startup.json`` (this module never
  creates it) and an explicit list of authorized action types, limited to the policy tier allowed for that phase.
* **No standing approvals** are granted by activation. Approvals stay in Autonomy Control.
* **Retreat is always allowed** (to any lower phase) and only ever lowers levels.
* Category ``community_drafts`` is never touched (it has its own Community configuration and kill switch).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.app.autonomy import registry
from backend.app.autonomy.control_plane import (ControlPlane, ControlPlaneError, PermissionDenied, safe_error,
                                                validate_operator)
from backend.app.autonomy.registry import Level

OFF, OBSERVE, RECOMMEND, RESTRICTED_EXECUTION, EXPANSION = (
    "OFF", "OBSERVE", "RECOMMEND", "RESTRICTED_EXECUTION", "EXPANSION")
PHASES = (OFF, OBSERVE, RECOMMEND, RESTRICTED_EXECUTION, EXPANSION)

PHASE_CATEGORIES = ("diagnostics", "recommendations", "reporting", "self_healing", "notifications")
RESTRICTED_EXECUTION_CATEGORIES = ("diagnostics", "reporting")
RESTRICTED_EXECUTION_ACTIONS = ("diagnostic.readonly.run", "report.operational.generate")

# Evidence thresholds (documented in docs/STAGE_3_32HIJ_RELIABILITY_ACTIVATION_REPORTING.md).
OBSERVE_MIN_HOURS = 48
OBSERVE_MIN_CYCLES = 48                      # observation cycles run every 30 minutes => about 24 hours of cycles
RECOMMEND_MIN_DAYS = 7
RECOMMEND_MIN_REVIEWED = 5                   # human approve/reject decisions on proposals
RESTRICTED_MIN_DAYS = 14
RESTRICTED_MIN_SUCCESSES = 20
MIN_AVAILABILITY_PCT = 95.0
MAX_DISMISSAL_PCT = 50.0

REQUIRED_PROHIBITED = ("youtube.community.publish", "youtube.video.publish", "external.communication.send",
                       "external.financial.action", "oauth.credentials.change", "oauth.scopes.change",
                       "git.destructive", "production.data.delete", "process.daemon.restart",
                       "process.dashboard.restart", "autonomy.self_policy.change")

SCHEMA = """
CREATE TABLE IF NOT EXISTS autonomy_activation_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    phase TEXT NOT NULL,
    phase_started_at_utc TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    authorized_actions_json TEXT NOT NULL DEFAULT '[]',
    readiness_json TEXT NOT NULL DEFAULT '{}',
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autonomy_activation_history (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at_utc TEXT NOT NULL,
    from_phase TEXT NOT NULL,
    to_phase TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('ADVANCED', 'RETREATED', 'REFUSED')),
    operator TEXT NOT NULL,
    reason TEXT NOT NULL,
    blockers_json TEXT NOT NULL DEFAULT '[]',
    readiness_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS autonomy_observation_cycles (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    phase TEXT NOT NULL,
    outcome TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autonomy_obs_cycles_at ON autonomy_observation_cycles (started_at_utc);
"""

TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(ts or "", TS_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def phase_levels(phase: str, authorized_actions: Optional[List[str]] = None) -> Dict[str, str]:
    """Control-plane levels a phase implies: ``{"global": ..., <category>: ...}``. community_drafts never appears."""
    if phase == OFF:
        return {"global": "OFF", **{c: "OFF" for c in PHASE_CATEGORIES}}
    if phase == OBSERVE:
        return {"global": "OBSERVE", **{c: "OBSERVE" for c in PHASE_CATEGORIES}}
    if phase == RECOMMEND:
        return {"global": "RECOMMEND", **{c: "RECOMMEND" for c in PHASE_CATEGORIES}}
    levels = {"global": "APPROVED_EXECUTION", **{c: "RECOMMEND" for c in PHASE_CATEGORIES}}
    for c in RESTRICTED_EXECUTION_CATEGORIES:
        levels[c] = "APPROVED_EXECUTION"
    if phase == EXPANSION:
        for a in authorized_actions or []:
            spec = registry.REGISTRY.get(a)
            if spec is not None and spec.category in PHASE_CATEGORIES:
                levels[spec.category] = "APPROVED_EXECUTION"
    return levels


def allowed_actions_for(phase: str) -> List[str]:
    if phase == RESTRICTED_EXECUTION:
        return list(RESTRICTED_EXECUTION_ACTIONS)
    if phase == EXPANSION:
        return sorted(a for a, s in registry.REGISTRY.items()
                      if registry.policy_tier(s) in (registry.TIER_AUTONOMOUS_ELIGIBLE, registry.TIER_APPROVAL_REQUIRED)
                      and s.category in PHASE_CATEGORIES)
    return []


class ActivationRefused(PermissionDenied):
    def __init__(self, message: str, blockers: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.blockers = blockers or []


class ActivationManager:
    def __init__(self, cp: ControlPlane, *, health_state: Optional[Callable[[], Dict[str, Any]]] = None,
                 health_summary: Optional[Callable[[datetime], Dict[str, Any]]] = None,
                 clock: Optional[Callable[[], datetime]] = None):
        self.cp = cp
        self.clock = clock or cp._now
        self.health_state = health_state or _default_health_state
        self.health_summary = health_summary or _default_health_summary
        self._schema_ready = False
        self._lock = threading.Lock()

    # -- storage ---------------------------------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        """Creates the activation tables (write paths only). executescript() commits implicitly, so it must run on
        its own connection, never inside an open BEGIN IMMEDIATE transaction."""
        if not self._schema_ready:
            conn = self.cp._connect()
            try:
                conn.executescript(SCHEMA)
            finally:
                conn.close()
            self._schema_ready = True

    def _tables_exist(self, conn) -> bool:
        return conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN "
                            "('autonomy_activation_state','autonomy_activation_history','autonomy_observation_cycles')"
                            ).fetchone()[0] == 3

    def state(self) -> Dict[str, Any]:
        """Persisted phase (reads never create tables)."""
        try:
            with self.cp._read() as conn:
                if not self._tables_exist(conn):
                    return {"phase": OFF, "phase_started_at_utc": None, "changed_by": None, "reason": "never activated",
                            "authorized_actions": [], "last_readiness": {}, "persisted": False}
                row = conn.execute("SELECT * FROM autonomy_activation_state WHERE id=1").fetchone()
        except Exception as exc:
            return {"phase": OFF, "error": safe_error(exc), "persisted": False, "authorized_actions": []}
        if row is None:
            return {"phase": OFF, "phase_started_at_utc": None, "changed_by": None, "reason": "never activated",
                    "authorized_actions": [], "last_readiness": {}, "persisted": False}
        return {"phase": row["phase"], "phase_started_at_utc": row["phase_started_at_utc"],
                "changed_by": row["changed_by"], "reason": row["reason"],
                "authorized_actions": json.loads(row["authorized_actions_json"] or "[]"),
                "last_readiness": json.loads(row["readiness_json"] or "{}"), "updated_at_utc": row["updated_at_utc"],
                "persisted": True}

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            with self.cp._read() as conn:
                if not self._tables_exist(conn):
                    return []
                rows = conn.execute("SELECT * FROM autonomy_activation_history ORDER BY seq DESC LIMIT ?",
                                    (max(1, min(500, int(limit))),)).fetchall()
        except Exception:
            return []
        return [{"seq": r["seq"], "at_utc": r["at_utc"], "from_phase": r["from_phase"], "to_phase": r["to_phase"],
                 "outcome": r["outcome"], "operator": r["operator"], "reason": r["reason"],
                 "blockers": json.loads(r["blockers_json"] or "[]")} for r in rows]

    def observation_stats(self, since_utc: Optional[str] = None) -> Dict[str, Any]:
        try:
            with self.cp._read() as conn:
                if not self._tables_exist(conn):
                    return {"cycles": 0, "ok": 0, "failed": 0, "first_at_utc": None, "last_at_utc": None}
                args = (since_utc or "",)
                r = conn.execute("SELECT COUNT(*), SUM(outcome='OK'), MIN(started_at_utc), MAX(started_at_utc) "
                                 "FROM autonomy_observation_cycles WHERE started_at_utc >= ?", args).fetchone()
                obs = conn.execute("SELECT COUNT(*) FROM autonomy_audit_events WHERE event_type='OBSERVATION_RECORDED' "
                                   "AND ts_utc >= ?", args).fetchone()[0]
        except Exception as exc:
            return {"cycles": 0, "ok": 0, "failed": 0, "error": safe_error(exc)}
        n = r[0] or 0
        return {"cycles": n, "ok": int(r[1] or 0), "failed": n - int(r[1] or 0), "first_at_utc": r[2],
                "last_at_utc": r[3], "observations_recorded": obs}

    def recent_cycles(self, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            with self.cp._read() as conn:
                if not self._tables_exist(conn):
                    return []
                rows = conn.execute("SELECT * FROM autonomy_observation_cycles ORDER BY seq DESC LIMIT ?",
                                    (int(limit),)).fetchall()
        except Exception:
            return []
        return [{"started_at_utc": r["started_at_utc"], "finished_at_utc": r["finished_at_utc"], "phase": r["phase"],
                 "outcome": r["outcome"], "summary": json.loads(r["summary_json"] or "{}")} for r in rows]

    # -- readiness -------------------------------------------------------------------------------------------
    def _check(self, out: List[Dict[str, Any]], cid: str, ok: bool, detail: str, evidence: Any = None,
               blocking: bool = True) -> None:
        out.append({"id": cid, "ok": bool(ok), "blocking": blocking, "detail": detail, "evidence": evidence})

    def assess(self, target: Optional[str] = None) -> Dict[str, Any]:
        """Readiness of ``target`` (default: the next phase). Pure read: never changes anything."""
        st = self.state()
        cur = st.get("phase", OFF)
        idx = PHASES.index(cur) if cur in PHASES else 0
        if target is None:
            target = PHASES[min(idx + 1, len(PHASES) - 1)]
        if target not in PHASES:
            raise ControlPlaneError(f"unknown phase; one of {', '.join(PHASES)}")
        now = self.clock()
        checks: List[Dict[str, Any]] = []
        self._check(checks, "sequential", PHASES.index(target) == idx + 1,
                    f"current phase {cur}; phases advance one at a time" if PHASES.index(target) != idx + 1
                    else f"{cur} -> {target} is the next step", {"current": cur, "target": target})

        # -- safety foundations (every phase) --
        try:
            chain = self.cp.verify_audit_chain()
            self._check(checks, "audit_chain", bool(chain.get("ok")), "audit hash chain verified" if chain.get("ok")
                        else f"audit chain broken: {chain.get('error') or chain}", {"events": chain.get("events")})
        except Exception as exc:
            self._check(checks, "audit_chain", False, f"audit chain could not be verified ({safe_error(exc)})")
        prohibited = {s.action_type for s in registry.PROHIBITED}
        missing = [a for a in REQUIRED_PROHIBITED if a not in prohibited or a in {s.action_type for s in registry.PERMITTED}]
        self._check(checks, "prohibited_actions_gated", not missing,
                    "publishing, external communication, financial, OAuth, destructive and process actions are "
                    "permanently gated" if not missing else f"not prohibited: {missing}", {"missing": missing})
        try:
            with self.cp._read() as conn:
                qc = conn.execute("PRAGMA quick_check").fetchone()[0]
            self._check(checks, "database_integrity", qc == "ok", f"PRAGMA quick_check: {qc}")
        except Exception as exc:
            self._check(checks, "database_integrity", False, f"database unavailable ({safe_error(exc)})")
        safety = {}
        try:
            safety = self.cp.safety_state()
        except Exception as exc:
            self._check(checks, "execution_safety", False, f"safety state unavailable ({safe_error(exc)})")
        if safety:
            breakers = sorted((safety.get("open_circuit_breakers") or {}).keys())
            stale = [l for l in safety.get("leases") or [] if l.get("heartbeat_stale")]
            self._check(checks, "no_open_circuit_breakers", not breakers,
                        "no open circuit breakers" if not breakers else f"{len(breakers)} open circuit breaker(s)",
                        breakers)
            self._check(checks, "no_stale_leases", not stale,
                        "no stale execution leases" if not stale else f"{len(stale)} stale lease(s)", stale)
        hs = self.health_state() or {}
        last = hs.get("last") or {}
        if not last:
            self._check(checks, "dashboard_health_monitor", False,
                        "the 3.32H health monitor has not recorded any state yet (monitor not running on 3.32H code)")
        else:
            age = None
            at = _parse(last.get("at_utc"))
            if at:
                age = (now - at).total_seconds()
            fresh = age is not None and age < 600
            good = last.get("state") in ("HEALTHY", "DEGRADED")
            self._check(checks, "dashboard_health_monitor", fresh and good,
                        f"dashboard {last.get('state')} ({last.get('reason')}), last probe {int(age or 0)} s ago"
                        if fresh else "the health monitor's last probe is older than 10 minutes",
                        {"state": last.get("state"), "age_sec": age, "live_latency_ms": last.get("live_latency_ms")})
        try:
            from backend.app.rendering.ffmpeg_geometry import self_check
            ok = bool(self_check().get("ok"))
            self._check(checks, "render_geometry_fix", ok,
                        "FFmpeg viewport geometry is even and padding errors classify as DETERMINISTIC" if ok
                        else "the 3.32H FFmpeg geometry fix is not active")
        except Exception as exc:
            self._check(checks, "render_geometry_fix", False, f"geometry self-check failed ({safe_error(exc)})")
        ks = self.cp.kill_switches()
        operator_switches = [s for s in ks["active"] if s != "startup_config"]
        if target in (OBSERVE, RECOMMEND):
            self._check(checks, "kill_switches", not operator_switches,
                        "no operator kill switch active (the startup switch does not affect "
                        f"{target}: nothing executes)" if not operator_switches
                        else f"operator kill switch active: {operator_switches}", ks["active"])

        # -- evidence for later phases --
        started = _parse(st.get("phase_started_at_utc"))
        in_phase_hours = (now - started).total_seconds() / 3600.0 if started else 0.0
        if target == RECOMMEND:
            obs = self.observation_stats(st.get("phase_started_at_utc"))
            self._check(checks, "observation_period", in_phase_hours >= OBSERVE_MIN_HOURS,
                        f"OBSERVE active {in_phase_hours:.1f} h of the required {OBSERVE_MIN_HOURS} h",
                        {"hours": round(in_phase_hours, 1)})
            self._check(checks, "observation_cycles", obs.get("cycles", 0) >= OBSERVE_MIN_CYCLES and obs.get("failed", 0) == 0,
                        f"{obs.get('cycles', 0)} observation cycles ({obs.get('failed', 0)} failed); need "
                        f">= {OBSERVE_MIN_CYCLES} with none failed", obs)
            self._availability_check(checks, started)
            self._false_positive_check(checks, st.get("phase_started_at_utc"))
        if target == RESTRICTED_EXECUTION:
            self._check(checks, "recommend_period", in_phase_hours >= RECOMMEND_MIN_DAYS * 24,
                        f"RECOMMEND active {in_phase_hours / 24:.1f} of the required {RECOMMEND_MIN_DAYS} days")
            reviewed = self._count_events(("APPROVED", "REJECTED"), st.get("phase_started_at_utc"))
            self._check(checks, "human_reviewed_proposals", reviewed >= RECOMMEND_MIN_REVIEWED,
                        f"{reviewed} proposals approved or rejected by a human; need >= {RECOMMEND_MIN_REVIEWED}")
            self._availability_check(checks, started)
            self._false_positive_check(checks, st.get("phase_started_at_utc"))
        if target in (RESTRICTED_EXECUTION, EXPANSION):
            sk = ks["switches"]["startup_config"]
            self._check(checks, "startup_execution_enabled", not sk["active"],
                        "autonomy_startup.json enables execution (operator-created)" if not sk["active"]
                        else "the startup kill switch is active: the operator must create autonomy_startup.json and "
                             "restart the dashboard (activation never creates it)", sk.get("detail"))
            others = [s for s in ks["active"] if s != "startup_config"]
            self._check(checks, "kill_switches", not others, "no operator kill switch active" if not others
                        else f"kill switch active: {others}")
        if target == EXPANSION:
            self._check(checks, "restricted_period", in_phase_hours >= RESTRICTED_MIN_DAYS * 24,
                        f"RESTRICTED_EXECUTION active {in_phase_hours / 24:.1f} of {RESTRICTED_MIN_DAYS} days")
            ex = self._execution_stats(st.get("phase_started_at_utc"))
            self._check(checks, "execution_track_record", ex["succeeded"] >= RESTRICTED_MIN_SUCCESSES and ex["failed"] == 0,
                        f"{ex['succeeded']} succeeded, {ex['failed']} failed executions in phase; need >= "
                        f"{RESTRICTED_MIN_SUCCESSES} and none failed", ex)
        blockers = [c for c in checks if c["blocking"] and not c["ok"]]
        return {"current_phase": cur, "target_phase": target, "ready": not blockers, "checks": checks,
                "blockers": [{"id": b["id"], "detail": b["detail"]} for b in blockers],
                "requires_authorization_phrase": target not in (OFF, OBSERVE),
                "authorization_phrase": f"AUTHORIZE {target}" if target not in (OFF, OBSERVE) else None,
                "allowed_actions": allowed_actions_for(target), "levels": phase_levels(target),
                "assessed_at_utc": _ts(now)}

    def _availability_check(self, checks, started: Optional[datetime]) -> None:
        summ = self.health_summary(started or self.clock() - timedelta(days=1)) or {}
        if not summ.get("available"):
            self._check(checks, "dashboard_availability", False,
                        f"no health-monitor evidence for the period ({summ.get('reason', 'unavailable')})")
            return
        pct = summ.get("availability_pct") or 0.0
        self._check(checks, "dashboard_availability", pct >= MIN_AVAILABILITY_PCT and not summ.get("restarts"),
                    f"liveness {pct}% over {summ.get('probes')} probes, {summ.get('restarts', 0)} restart(s); need "
                    f">= {MIN_AVAILABILITY_PCT}% and no restarts", {k: summ.get(k) for k in ("availability_pct", "probes", "restarts")})

    def _false_positive_check(self, checks, since: Optional[str]) -> None:
        try:
            with self.cp._read() as conn:
                have = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='autonomy_recommendations'").fetchone()[0]
                rows = conn.execute("SELECT status, COUNT(*) FROM autonomy_recommendations WHERE status_at >= ? "
                                    "GROUP BY status", (since or "",)).fetchall() if have else []
        except Exception as exc:
            self._check(checks, "recommendation_false_positives", False, f"unavailable ({safe_error(exc)})")
            return
        counts = {r[0]: r[1] for r in rows}
        rejected, resolved = counts.get("REJECTED", 0), counts.get("RESOLVED", 0) + counts.get("APPROVED", 0)
        decided = rejected + resolved
        if decided < 5:
            self._check(checks, "recommendation_false_positives", True,
                        f"only {decided} recommendation decision(s) in the period: false-positive rate not yet "
                        "measurable (informational)", counts, blocking=False)
            return
        pct = round(100.0 * rejected / decided, 1)
        self._check(checks, "recommendation_false_positives", pct <= MAX_DISMISSAL_PCT,
                    f"{pct}% of decided recommendations were rejected (limit {MAX_DISMISSAL_PCT}%)", counts)

    def _count_events(self, types: tuple, since: Optional[str]) -> int:
        try:
            with self.cp._read() as conn:
                q = "SELECT COUNT(*) FROM autonomy_audit_events WHERE ts_utc >= ? AND event_type IN (%s)" % ",".join("?" * len(types))
                return int(conn.execute(q, (since or "",) + tuple(types)).fetchone()[0])
        except Exception:
            return 0

    def _execution_stats(self, since: Optional[str]) -> Dict[str, int]:
        try:
            with self.cp._read() as conn:
                rows = conn.execute("SELECT outcome, COUNT(*) FROM autonomy_executions WHERE started_at_utc >= ? "
                                    "GROUP BY outcome", (since or "",)).fetchall()
        except Exception:
            rows = []
        c = {r[0]: r[1] for r in rows}
        return {"succeeded": c.get("SUCCEEDED", 0),
                "failed": sum(v for k, v in c.items() if k in ("FAILED", "TIMED_OUT", "VERIFY_FAILED", "INTERRUPTED")),
                "by_outcome": c}

    # -- transitions -----------------------------------------------------------------------------------------
    def advance(self, target: str, operator: str, reason: str, *, confirm: bool = False,
                authorization: str = "", authorized_actions: Optional[List[str]] = None) -> Dict[str, Any]:
        op = validate_operator(operator)
        reason = (reason or "").strip()[:500]
        if not reason:
            raise ControlPlaneError("a reason is required")
        if target not in PHASES or target == OFF:
            raise ControlPlaneError(f"advance target must be one of {', '.join(PHASES[1:])}")
        self._ensure_schema()
        with self._lock:
            readiness = self.assess(target)
            cur = readiness["current_phase"]
            blockers = list(readiness["blockers"])
            if not confirm:
                blockers.append({"id": "confirm", "detail": "the operator must confirm the activation"})
            if readiness["requires_authorization_phrase"] and authorization.strip() != readiness["authorization_phrase"]:
                blockers.append({"id": "authorization",
                                 "detail": f"type the exact phrase '{readiness['authorization_phrase']}' to authorize"})
            actions = sorted(set(authorized_actions or []))
            if target in (RESTRICTED_EXECUTION, EXPANSION):
                allowed = set(allowed_actions_for(target))
                bad = [a for a in actions if a not in allowed]
                if not actions:
                    blockers.append({"id": "authorized_actions", "detail": "list the action types you authorize"})
                if bad:
                    blockers.append({"id": "authorized_actions", "detail": f"not allowed in {target}: {bad}"})
            elif actions:
                blockers.append({"id": "authorized_actions", "detail": f"{target} does not execute actions"})
            if blockers:
                self._persist(cur, target, "REFUSED", op, reason, blockers, readiness, None)
                raise ActivationRefused(f"{target} refused: " + "; ".join(b["detail"] for b in blockers)[:600], blockers)
            self._apply_levels(target, actions, op, reason)
            self._persist(cur, target, "ADVANCED", op, reason, [], readiness, actions)
        return {"phase": target, "state": self.state(), "readiness": readiness}

    def retreat(self, target: str, operator: str, reason: str) -> Dict[str, Any]:
        """Moves to any lower phase (always allowed; only lowers levels)."""
        op = validate_operator(operator)
        reason = (reason or "").strip()[:500]
        if not reason:
            raise ControlPlaneError("a reason is required")
        with self._lock:
            cur = self.state().get("phase", OFF)
            if target not in PHASES or PHASES.index(target) >= PHASES.index(cur):
                raise ControlPlaneError(f"retreat target must be below the current phase {cur}")
            keep = [a for a in self.state().get("authorized_actions", []) if a in allowed_actions_for(target)]
            self._apply_levels(target, keep, op, reason)
            self._persist(cur, target, "RETREATED", op, reason, [], {}, keep)
        return {"phase": target, "state": self.state()}

    def _apply_levels(self, phase: str, actions: List[str], op: str, reason: str) -> None:
        levels = phase_levels(phase, actions)
        why = f"activation {phase}: {reason}"[:500]
        current = self.cp.permissions()
        cats = current.get("category_levels") or {}
        lowering = PHASES.index(phase) < PHASES.index(self.state().get("phase", OFF))
        # Order matters for safety: when raising, categories first then global; when lowering, global first.
        order = ["global"] + list(PHASE_CATEGORIES) if lowering else list(PHASE_CATEGORIES) + ["global"]
        for scope in order:
            want = levels[scope]
            have = current.get("global_level") if scope == "global" else cats.get(scope, "OFF")
            if have != want:
                self.cp.set_level(scope, want, op, why)

    def _persist(self, frm: str, to: str, outcome: str, op: str, reason: str, blockers: List[Dict[str, Any]],
                 readiness: Dict[str, Any], actions: Optional[List[str]]) -> None:
        now = _ts(self.clock())
        slim = {"ready": readiness.get("ready"), "assessed_at_utc": readiness.get("assessed_at_utc"),
                "checks": [{k: c[k] for k in ("id", "ok", "blocking", "detail")} for c in readiness.get("checks", [])]}
        self._ensure_schema()
        with self.cp._tx() as conn:
            conn.execute("INSERT INTO autonomy_activation_history (at_utc, from_phase, to_phase, outcome, operator, "
                         "reason, blockers_json, readiness_json) VALUES (?,?,?,?,?,?,?,?)",
                         (now, frm, to, outcome, op, reason, json.dumps(blockers), json.dumps(slim)))
            if outcome != "REFUSED":
                conn.execute("INSERT INTO autonomy_activation_state (id, phase, phase_started_at_utc, changed_by, reason, "
                             "authorized_actions_json, readiness_json, updated_at_utc) VALUES (1,?,?,?,?,?,?,?) "
                             "ON CONFLICT(id) DO UPDATE SET phase=excluded.phase, phase_started_at_utc=excluded.phase_started_at_utc, "
                             "changed_by=excluded.changed_by, reason=excluded.reason, authorized_actions_json=excluded.authorized_actions_json, "
                             "readiness_json=excluded.readiness_json, updated_at_utc=excluded.updated_at_utc",
                             (to, now, op, reason, json.dumps(actions or []), json.dumps(slim), now))
            self.cp._audit(conn, {"ADVANCED": "ACTIVATION_PHASE_ADVANCED", "RETREATED": "ACTIVATION_PHASE_RETREATED",
                                  "REFUSED": "ACTIVATION_REFUSED"}[outcome], op, previous_state=frm, new_state=to,
                           reason=reason, details={"blockers": blockers[:10], "authorized_actions": actions or []})

    # -- observation cycle (called by the existing scheduler daemon; never advances a phase) -------------------
    def run_observation_cycle(self, *, assessment: Optional[Callable[[], Dict[str, Any]]] = None,
                              recommendations: Optional[Callable[[], Dict[str, Any]]] = None,
                              self_healing: Optional[Callable[[], Dict[str, Any]]] = None,
                              auto_actions: Optional[Callable[[], Dict[str, Any]]] = None) -> Dict[str, Any]:
        st = self.state()
        phase = st.get("phase", OFF)
        if phase == OFF:
            return {"ran": False, "phase": OFF, "reason": "Autopilot phase is OFF"}
        started = _ts(self.clock())
        summary: Dict[str, Any] = {"phase": phase}
        errors: Dict[str, str] = {}
        steps = {
            "assessment": assessment or (lambda: __import__("backend.app.autonomy.engine", fromlist=["x"]).run_assessment(self.cp)),
            "recommendations": recommendations or (lambda: __import__("backend.app.autonomy.recommendations.engine",
                                                                      fromlist=["x"]).get_engine().run(triggered_by="autopilot-observation")),
            "self_healing": self_healing or (lambda: __import__("backend.app.autonomy.self_healing.engine",
                                                                fromlist=["x"]).get_engine().scan(triggered_by="autopilot-observation")),
            # Remediation WS5: policy-automatic follow-ups of open recommendations (deduplicated, audited).
            "auto_actions": auto_actions or (lambda: __import__("backend.app.autonomy.autopilot",
                                                                fromlist=["x"]).Autopilot(self.cp).auto_bridge()),
        }
        for name, fn in steps.items():
            try:
                res = fn() or {}
                summary[name] = {k: res.get(k) for k in ("ran", "mode", "observations", "reason", "findings",
                                                         "control_state", "executed", "blocked", "skipped_duplicate",
                                                         "approval_required", "informational", "proposals")
                                 if k in res and not isinstance(res.get(k), (list, dict))}
                if isinstance(res.get("findings"), list):
                    summary[name]["findings"] = len(res["findings"])
                if isinstance(res.get("proposals"), list):
                    summary[name]["proposals"] = len(res["proposals"])
            except Exception as exc:
                errors[name] = safe_error(exc)
        hs = (self.health_state() or {}).get("last") or {}
        summary["dashboard_state"] = hs.get("state")
        summary["errors"] = errors
        outcome = "OK" if not errors else "PARTIAL"
        self._ensure_schema()
        with self.cp._tx() as conn:
            conn.execute("INSERT INTO autonomy_observation_cycles (started_at_utc, finished_at_utc, phase, outcome, "
                         "summary_json) VALUES (?,?,?,?,?)", (started, _ts(self.clock()), phase, outcome,
                                                              json.dumps(summary, default=str)[:8000]))
        return {"ran": True, "phase": phase, "outcome": outcome, "summary": summary}

    def overview(self) -> Dict[str, Any]:
        st = self.state()
        perms = self.cp.permissions()
        expected = phase_levels(st.get("phase", OFF), st.get("authorized_actions"))
        actual = {"global": perms.get("global_level"), **{c: (perms.get("category_levels") or {}).get(c, "OFF")
                                                          for c in PHASE_CATEGORIES}}
        drift = {k: {"expected": v, "actual": actual.get(k)} for k, v in expected.items() if actual.get(k) != v}
        nxt = None
        if st.get("phase") != EXPANSION:
            try:
                nxt = self.assess()
            except Exception as exc:
                nxt = {"error": safe_error(exc)}
        started = _parse(st.get("phase_started_at_utc"))
        recommended_until = None
        if st.get("phase") == OBSERVE and started:
            recommended_until = _ts(started + timedelta(hours=OBSERVE_MIN_HOURS))
        return {"state": st, "phases": list(PHASES), "level_drift": drift, "next": nxt,
                "observation": self.observation_stats(st.get("phase_started_at_utc")),
                "recent_cycles": self.recent_cycles(5), "history": self.history(20),
                "observe_min_hours": OBSERVE_MIN_HOURS, "observe_recommended_until_utc": recommended_until,
                "notes": ["Phases never advance automatically; each advance re-validates readiness and needs an operator.",
                          "Activation never grants standing approvals and never creates autonomy_startup.json."]}


def _logs_dir():
    from backend.app.core.paths import LOGS_DIR
    return LOGS_DIR


def _default_health_state() -> Dict[str, Any]:
    import backend.app.core.health_monitoring as hm
    return hm.read_state(_logs_dir())


def _default_health_summary(since: datetime) -> Dict[str, Any]:
    import backend.app.core.health_monitoring as hm
    return hm.summarize_events(hm.read_events(_logs_dir(), since_utc=_ts(since)))


_instance: Optional[ActivationManager] = None
_instance_lock = threading.Lock()


def get_activation() -> ActivationManager:
    global _instance
    with _instance_lock:
        if _instance is None:
            from backend.app.autonomy.control_plane import get_control_plane
            _instance = ActivationManager(get_control_plane())
        return _instance
