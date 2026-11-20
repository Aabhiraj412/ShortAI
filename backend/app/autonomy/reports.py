"""Stage 3.32J: daily operational summaries and weekly Autopilot reports from persisted data only.

Every report separates three kinds of content:

* ``observed_facts``     - values read from persisted state (production jobs via Stage 3.32A Operational Intelligence,
                           the health-monitor ledger, autonomy tables, the audit trail), each with its source;
* ``calculated_metrics`` - values derived from facts, each with its formula and inputs;
* ``interpretations``    - deterministic, rule-based readings of the metrics, each citing the metrics it uses.
                           ``ai_interpretation`` states explicitly that no AI text was generated.

Missing data is reported under ``missing`` with a reason; it is never replaced by zero.

Reports are persisted in ``autonomy_operational_reports`` (one row per kind and period; completed periods are
immutable unless regenerated explicitly). Generation is local and read-only towards production: no network, no AI
call, no notification. The existing scheduler daemon triggers it (``autopilot_tick``); operators can generate on demand.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.app.autonomy.control_plane import ControlPlane, ControlPlaneError, safe_error

IST = ZoneInfo("Asia/Kolkata")
TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
DAILY, WEEKLY = "daily", "weekly"
BLOCK_EVENTS = ("PROHIBITED_ACTION_BLOCKED", "BRIDGE_REFUSED", "EXECUTION_BLOCKED", "PROPOSAL_REFUSED",
                "APPROVAL_REFUSED", "ACTIVATION_REFUSED", "UNKNOWN_ACTION_REJECTED", "RECOMMENDATION_RUN_BLOCKED",
                "SELF_HEALING_SCAN_BLOCKED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS autonomy_operational_reports (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('daily', 'weekly')),
    period_key TEXT NOT NULL,
    period_start_utc TEXT NOT NULL,
    period_end_utc TEXT NOT NULL,
    complete INTEGER NOT NULL,
    generated_at_utc TEXT NOT NULL,
    generated_by TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    UNIQUE (kind, period_key)
);
"""


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def daily_period(day: str) -> Dict[str, Any]:
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
    return {"kind": DAILY, "key": day, "start": d.astimezone(timezone.utc),
            "end": (d + timedelta(days=1)).astimezone(timezone.utc), "label": f"{day} (IST day)"}


def weekly_period(week: str) -> Dict[str, Any]:
    """``week`` is an ISO week ``YYYY-Www``; the period is Monday 00:00 to the next Monday 00:00 IST."""
    try:
        year, wk = week.split("-W")
        monday = date_cls.fromisocalendar(int(year), int(wk), 1)
    except Exception as exc:
        raise ControlPlaneError("week must look like 2026-W47") from exc
    start = datetime(monday.year, monday.month, monday.day, tzinfo=IST)
    return {"kind": WEEKLY, "key": week, "start": start.astimezone(timezone.utc),
            "end": (start + timedelta(days=7)).astimezone(timezone.utc),
            "label": f"ISO week {week} ({monday.isoformat()} to {(monday + timedelta(days=6)).isoformat()}, IST)"}


def iso_week_of(dt: datetime) -> str:
    y, w, _ = dt.astimezone(IST).isocalendar()
    return f"{y}-W{w:02d}"


def _pct(n: Optional[float], d: Optional[float]) -> Optional[float]:
    if n is None or not d:
        return None
    return round(100.0 * n / d, 1)


class ReportService:
    def __init__(self, cp: ControlPlane, *, intelligence_factory: Optional[Callable[..., Any]] = None,
                 health_events: Optional[Callable[[str, str], List[Dict[str, Any]]]] = None,
                 activation: Optional[Callable[[], Any]] = None, clock: Optional[Callable[[], datetime]] = None):
        self.cp = cp
        self.clock = clock or cp._now
        self.intelligence_factory = intelligence_factory or _default_intelligence
        self.health_events = health_events or _default_health_events
        self.activation = activation or _default_activation
        self._schema_ready = False
        self._lock = threading.Lock()

    # -- storage ---------------------------------------------------------------------------------------------
    def _have_table(self, conn) -> bool:
        return bool(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND "
                                 "name='autonomy_operational_reports'").fetchone()[0])

    def list_reports(self, kind: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
        try:
            with self.cp._read() as conn:
                if not self._have_table(conn):
                    return []
                q = ("SELECT id, kind, period_key, period_start_utc, period_end_utc, complete, generated_at_utc, "
                     "generated_by, payload_sha256 FROM autonomy_operational_reports")
                args: tuple = ()
                if kind:
                    q += " WHERE kind=?"
                    args = (kind,)
                rows = conn.execute(q + " ORDER BY period_start_utc DESC LIMIT ?", args + (int(limit),)).fetchall()
        except Exception:
            return []
        return [dict(r) | {"complete": bool(r["complete"])} for r in rows]

    def get(self, kind: str, key: str) -> Optional[Dict[str, Any]]:
        try:
            with self.cp._read() as conn:
                if not self._have_table(conn):
                    return None
                row = conn.execute("SELECT * FROM autonomy_operational_reports WHERE kind=? AND period_key=?",
                                   (kind, key)).fetchone()
        except Exception:
            return None
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["stored"] = {"generated_at_utc": row["generated_at_utc"], "generated_by": row["generated_by"],
                             "sha256": row["payload_sha256"],
                             "sha256_verified": hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest() == row["payload_sha256"]}
        return payload

    def latest(self, kind: str) -> Optional[Dict[str, Any]]:
        rows = self.list_reports(kind, 1)
        return self.get(kind, rows[0]["period_key"]) if rows else None

    # -- generation ------------------------------------------------------------------------------------------
    def generate(self, kind: str, key: Optional[str] = None, *, generated_by: str = "autopilot-reporting",
                 force: bool = False) -> Dict[str, Any]:
        now = self.clock()
        if kind == DAILY:
            key = key or now.astimezone(IST).date().isoformat()
            try:
                period = daily_period(key)
            except ValueError as exc:
                raise ControlPlaneError("date must look like 2026-11-20") from exc
        elif kind == WEEKLY:
            period = weekly_period(key or iso_week_of(now))
        else:
            raise ControlPlaneError("kind must be daily or weekly")
        if period["start"] > now:
            raise ControlPlaneError("the period has not started yet")
        complete = period["end"] <= now
        with self._lock:
            existing = self.get(kind, period["key"])
            if existing and existing.get("period", {}).get("complete") and not force:
                return existing | {"cached": True}
            report = self._build(period, complete, now, generated_by)
            raw = json.dumps(report, sort_keys=True, default=str)
            sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if not self._schema_ready:            # executescript() commits: run it outside the transaction
                c = self.cp._connect()
                try:
                    c.executescript(SCHEMA)
                finally:
                    c.close()
                self._schema_ready = True
            with self.cp._tx() as conn:
                conn.execute("INSERT INTO autonomy_operational_reports (id, kind, period_key, period_start_utc, "
                             "period_end_utc, complete, generated_at_utc, generated_by, payload_json, payload_sha256) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kind, period_key) DO UPDATE SET "
                             "complete=excluded.complete, generated_at_utc=excluded.generated_at_utc, "
                             "generated_by=excluded.generated_by, payload_json=excluded.payload_json, "
                             "payload_sha256=excluded.payload_sha256",
                             (report["id"], kind, period["key"], _ts(period["start"]), _ts(period["end"]), int(complete),
                              report["generated_at_utc"], generated_by[:64], raw, sha))
        return self.get(kind, period["key"]) or report

    def _build(self, period: Dict[str, Any], complete: bool, now: datetime, by: str) -> Dict[str, Any]:
        start, end = period["start"], min(period["end"], now)
        s, e = _ts(start), _ts(period["end"])   # queries cover the whole period; nothing is stored in the future
        facts: Dict[str, Any] = {}
        metrics: Dict[str, Any] = {}
        missing: List[Dict[str, str]] = []

        # 1. production / recovery / rendering / failures (Stage 3.32A, read-only)
        try:
            intel = self.intelligence_factory(end)
            if period["kind"] == DAILY:
                kw: Dict[str, Any] = {"date": period["key"]}
            else:   # complete week: exactly Monday..Monday; current week: whole days up to now (stated below)
                days = max(1, min(7, int(-(-(end - start).total_seconds() // 86400))))
                kw = {"days": days}
                facts["production_window"] = {"days": days, "ends_utc": _ts(end),
                                              "note": "the 7 days of the ISO week" if complete else
                                              f"the last {days} x 24 h up to generation (week in progress)"}
            for name, fn in (("production", intel.production_health), ("recovery", intel.recovery_analytics),
                             ("rendering", intel.renderer_reliability), ("failures", intel.failure_intelligence)):
                try:
                    sec = fn(**kw)
                except Exception as exc:
                    missing.append({"item": name, "reason": safe_error(exc)})
                    continue
                facts[name] = {"status": sec.get("status"), "values": sec.get("metrics") or {},
                               "sources": sec.get("sources"), "estimated": sec.get("estimated") or []}
                for k, why in (sec.get("unavailable") or {}).items():
                    missing.append({"item": f"{name}.{k}", "reason": str(why)})
        except Exception as exc:
            missing.append({"item": "production intelligence", "reason": safe_error(exc)})
        prod = (facts.get("production") or {}).get("values") or {}
        if prod:
            metrics["production_success_pct"] = {"value": _pct(prod.get("completed"), prod.get("finished") or prod.get("total_cycles")),
                                                 "formula": "completed / finished cycles x 100",
                                                 "inputs": {k: prod.get(k) for k in ("completed", "finished", "total_cycles")}}
            metrics["recovery_window_expiry_pct"] = {"value": _pct(prod.get("recovery_window_expired"), prod.get("finished") or prod.get("total_cycles")),
                                                     "formula": "recovery-window-expired / finished cycles x 100",
                                                     "inputs": {k: prod.get(k) for k in ("recovery_window_expired", "finished")}}
        rend = (facts.get("rendering") or {}).get("values") or {}
        if rend:
            metrics["render_success_pct"] = {"value": rend.get("render_success_pct"),
                                             "formula": "renders completed / (completed + failed) x 100 (3.32A)",
                                             "inputs": {k: rend.get(k) for k in ("renders_completed", "renders_failed")}}
        fail = (facts.get("failures") or {}).get("values") or {}
        if fail:
            causes = fail.get("by_root_cause") or {}
            facts["rendering_failure_trend"] = {"ffmpeg_failures": causes.get("ffmpeg", 0),
                                                "file_lock_failures": causes.get("file_lock", 0),
                                                "daily_trend": fail.get("daily_trend") or {},
                                                "source": "3.32A failure intelligence (job error messages)"}

        # 2. dashboard health monitor (3.32H ledger)
        try:
            from backend.app.core.health_monitoring import summarize_events
            hsum = summarize_events(self.health_events(s, e))
            facts["health_monitor"] = hsum | {"source": "logs/health_monitor_events.jsonl"}
            if not hsum.get("available"):
                missing.append({"item": "health_monitor", "reason": hsum.get("reason", "no events")})
            else:
                metrics["dashboard_availability_pct"] = {"value": hsum.get("availability_pct"),
                                                         "formula": "liveness probes ok / probes x 100",
                                                         "inputs": {"probes": hsum.get("probes")}}
        except Exception as exc:
            missing.append({"item": "health_monitor", "reason": safe_error(exc)})

        # 3. autonomy: recommendations, approvals, executions, breakers, blocked actions
        try:
            with self.cp._read() as conn:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                rec: Dict[str, Any] = {}
                if "autonomy_recommendations" in tables:
                    rec["created"] = conn.execute("SELECT COUNT(*) FROM autonomy_recommendations WHERE created_at >= ? "
                                                  "AND created_at < ?", (s, e)).fetchone()[0]
                    rec["decided_by_status"] = {r[0]: r[1] for r in conn.execute(
                        "SELECT status, COUNT(*) FROM autonomy_recommendations WHERE status_at >= ? AND status_at < ? "
                        "GROUP BY status", (s, e))}
                    rec["open_now"] = conn.execute("SELECT COUNT(*) FROM autonomy_recommendations WHERE status IN "
                                                   "('NEW','ACKNOWLEDGED','APPROVED')").fetchone()[0]
                    rec["runs"] = conn.execute("SELECT COUNT(*) FROM autonomy_recommendation_runs WHERE started_at >= ? "
                                               "AND started_at < ?", (s, e)).fetchone()[0] \
                        if "autonomy_recommendation_runs" in tables else None
                    top = conn.execute("SELECT id, title, severity, priority_label, action FROM autonomy_recommendations "
                                       "WHERE status IN ('NEW','ACKNOWLEDGED','APPROVED') ORDER BY priority DESC LIMIT 5").fetchall()
                    rec["top_open"] = [dict(r) for r in top]
                else:
                    missing.append({"item": "recommendations", "reason": "recommendation tables not created yet"})
                facts["recommendations"] = rec
                facts["approvals"] = {r[0]: r[1] for r in conn.execute(
                    "SELECT status, COUNT(*) FROM autonomy_approval_requests WHERE created_at_utc >= ? AND "
                    "created_at_utc < ? GROUP BY status", (s, e))} if "autonomy_approval_requests" in tables else {}
                ex_rows = conn.execute("SELECT outcome, COUNT(*), AVG((julianday(finished_at_utc) - julianday(started_at_utc)) * 86400000.0) FROM autonomy_executions WHERE "
                                       "started_at_utc >= ? AND started_at_utc < ? GROUP BY outcome", (s, e)).fetchall() \
                    if "autonomy_executions" in tables else []
                facts["executions"] = {r[0]: {"count": r[1], "avg_duration_ms": round(r[2], 1) if r[2] else None}
                                       for r in ex_rows}
                ev = conn.execute("SELECT event_type, action_type, reason FROM autonomy_audit_events WHERE ts_utc >= ? "
                                  "AND ts_utc < ? AND event_type IN (%s)" % ",".join("?" * len(BLOCK_EVENTS)),
                                  (s, e) + BLOCK_EVENTS).fetchall() if "autonomy_audit_events" in tables else []
                blocked: Dict[str, int] = {}
                reasons: Dict[str, int] = {}
                breaker_events = 0
                for r in ev:
                    blocked[r[0]] = blocked.get(r[0], 0) + 1
                    why = (r[2] or "")[:120]
                    if "circuit breaker" in why.lower():
                        breaker_events += 1
                    key = f"{r[0]}: {why}"
                    reasons[key] = reasons.get(key, 0) + 1
                facts["blocked_actions"] = {"by_event": blocked,
                                            "top_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])[:10])}
                facts["circuit_breakers"] = {"blocked_by_breaker_events": breaker_events}
                aud = conn.execute("SELECT COUNT(*) FROM autonomy_audit_events WHERE ts_utc >= ? AND ts_utc < ?",
                                   (s, e)).fetchone()[0] if "autonomy_audit_events" in tables else 0
                facts["audit_events_in_period"] = aud
        except Exception as exc:
            missing.append({"item": "autonomy tables", "reason": safe_error(exc)})
        try:
            safety = self.cp.safety_state()
            facts.setdefault("circuit_breakers", {})["open_now"] = sorted((safety.get("open_circuit_breakers") or {}).keys())
            facts["execution_leases_now"] = len(safety.get("leases") or [])
        except Exception as exc:
            missing.append({"item": "execution safety state", "reason": safe_error(exc)})
        exe = facts.get("executions") or {}
        total = sum(v["count"] for v in exe.values())
        if total:
            metrics["execution_success_pct"] = {"value": _pct((exe.get("SUCCEEDED") or {}).get("count", 0), total),
                                                "formula": "SUCCEEDED / all executions x 100", "inputs": {"executions": total}}

        # 4. autonomy phase and readiness
        try:
            act = self.activation()
            st = act.state()
            obs = act.observation_stats(s)
            facts["autonomy"] = {"phase": st.get("phase"), "phase_started_at_utc": st.get("phase_started_at_utc"),
                                 "observation_cycles_in_period": obs.get("cycles"),
                                 "observation_cycles_failed": obs.get("failed")}
            if period["kind"] == WEEKLY or not complete:
                nxt = act.assess() if st.get("phase") != "EXPANSION" else None
                if nxt:
                    facts["autonomy"]["next_phase"] = nxt["target_phase"]
                    facts["autonomy"]["next_phase_ready"] = nxt["ready"]
                    facts["autonomy"]["next_phase_blockers"] = nxt["blockers"]
            facts["autonomy"]["kill_switches_active"] = self.cp.kill_switches()["active"]
        except Exception as exc:
            missing.append({"item": "autonomy phase", "reason": safe_error(exc)})

        interp, actions = _interpret(facts, metrics)
        rid = f"rpt_{period['kind']}_{period['key']}".replace(":", "")
        return {
            "id": rid, "kind": period["kind"],
            "period": {"key": period["key"], "label": period["label"], "start_utc": _ts(start),
                       "end_utc": _ts(period["end"]), "covered_until_utc": _ts(end), "complete": complete,
                       "timezone": "Asia/Kolkata"},
            "generated_at_utc": _ts(now), "generated_by": by,
            "observed_facts": facts, "calculated_metrics": metrics,
            "interpretations": interp, "recommended_actions": actions,
            "ai_interpretation": {"generated": False,
                                  "note": "No AI-generated text. Interpretations above are deterministic rules over the "
                                          "metrics they cite."},
            "missing": missing,
            "provenance": {"observed_facts": "persisted production, health-monitor and autonomy records",
                           "calculated_metrics": "derived from observed_facts; formula and inputs listed",
                           "interpretations": "rule-based (no AI)", "notifications": "none sent"},
        }


def _interpret(facts: Dict[str, Any], metrics: Dict[str, Any]) -> tuple:
    out: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []

    def add(level: str, text: str, cites: List[str], action: Optional[str] = None) -> None:
        out.append({"level": level, "text": text, "cites": cites, "source": "rule"})
        if action:
            actions.append({"action": action, "because": text, "cites": cites, "executes_automatically": False})

    m = lambda k: (metrics.get(k) or {}).get("value")
    sp = m("production_success_pct")
    if sp is not None:
        if sp < 80:
            add("warning", f"Production success {sp}% is below 80%.", ["production_success_pct"],
                "Review the failure root causes in Operations Intelligence before changing any schedule.")
        else:
            add("info", f"Production success {sp}%.", ["production_success_pct"])
    rx = m("recovery_window_expiry_pct")
    if rx is not None and rx > 20:
        add("warning", f"{rx}% of cycles expired their recovery window.", ["recovery_window_expiry_pct"],
            "Investigate the dominant failure cause; expiries mean failures were not recovered in time.")
    trend = facts.get("rendering_failure_trend") or {}
    if trend.get("ffmpeg_failures"):
        add("warning", f"{trend['ffmpeg_failures']} FFmpeg failure(s) in the period.", ["rendering_failure_trend"],
            "Confirm they predate the 3.32H geometry fix; new ones after the fix need a fresh diagnosis.")
    av = m("dashboard_availability_pct")
    hm = facts.get("health_monitor") or {}
    if av is not None:
        if av < 99 or hm.get("restarts"):
            add("warning", f"Dashboard liveness {av}% with {hm.get('restarts', 0)} monitor restart(s).",
                ["dashboard_availability_pct", "health_monitor"], "Check the monitor's failure reasons and latency.")
        else:
            add("info", f"Dashboard liveness {av}%, no monitor restarts.", ["dashboard_availability_pct"])
    br = facts.get("circuit_breakers") or {}
    if br.get("open_now") or br.get("blocked_by_breaker_events"):
        add("warning", "Circuit breakers opened or blocked executions.", ["circuit_breakers"],
            "Inspect the failing action type in the Autopilot executions table.")
    blocked = (facts.get("blocked_actions") or {}).get("by_event") or {}
    if blocked.get("PROHIBITED_ACTION_BLOCKED"):
        add("warning", f"{blocked['PROHIBITED_ACTION_BLOCKED']} attempt(s) to propose a permanently gated action were blocked.",
            ["blocked_actions"], "Review who or what attempted them in the audit trail.")
    auto = facts.get("autonomy") or {}
    if auto.get("next_phase") and auto.get("next_phase_blockers"):
        add("info", f"Autopilot phase {auto.get('phase')}; {auto['next_phase']} is not ready "
                    f"({len(auto['next_phase_blockers'])} blocker(s)).", ["autonomy"])
    elif auto.get("next_phase_ready"):
        add("info", f"Autopilot phase {auto.get('phase')}; readiness checks for {auto['next_phase']} pass. Advancing "
                    "still needs explicit operator authorization.", ["autonomy"])
    return out, actions


def _default_intelligence(end: datetime):
    from backend.app.services.operational_intelligence import OperationalIntelligence
    return OperationalIntelligence(clock=lambda: end, cache_ttl=0.0)


def _default_health_events(since: str, until: str) -> List[Dict[str, Any]]:
    from backend.app.core.health_monitoring import read_events
    from backend.app.core.paths import LOGS_DIR
    return read_events(LOGS_DIR, since_utc=since, until_utc=until)


def _default_activation():
    from backend.app.autonomy.activation import get_activation
    return get_activation()


_instance: Optional[ReportService] = None
_instance_lock = threading.Lock()


def get_reports() -> ReportService:
    global _instance
    with _instance_lock:
        if _instance is None:
            from backend.app.autonomy.control_plane import get_control_plane
            _instance = ReportService(get_control_plane())
        return _instance


def autopilot_tick(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Entry point for the existing scheduler daemon (job ``autopilot_tick``, every 30 minutes).

    * runs one observation cycle when the Autopilot phase is OBSERVE or higher (never advances a phase);
    * keeps today's daily summary current and finalises yesterday's once;
    * finalises the previous ISO week's report once (and keeps the current week's current).
    """
    from backend.app.autonomy.activation import get_activation
    out: Dict[str, Any] = {}
    try:
        out["observation"] = get_activation().run_observation_cycle()
    except Exception as exc:
        out["observation"] = {"error": safe_error(exc)}
    svc = get_reports()
    now = now or svc.clock()
    today = now.astimezone(IST).date()
    jobs = [(DAILY, (today - timedelta(days=1)).isoformat()), (DAILY, today.isoformat()),
            (WEEKLY, iso_week_of(now - timedelta(days=7))), (WEEKLY, iso_week_of(now))]
    for kind, key in jobs:
        try:
            r = svc.generate(kind, key, generated_by="scheduler-autopilot-tick")
            out[f"{kind}:{key}"] = "cached" if r.get("cached") else "generated"
        except Exception as exc:
            out[f"{kind}:{key}"] = f"error: {safe_error(exc)}"
    return out
