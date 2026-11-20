"""Stages 3.32H/I/J: dashboard health monitoring, phased Autopilot activation, operational reports.

Temporary databases and log directories only; fake probes, fake intelligence; no network, no FFmpeg.
"""
from __future__ import annotations

import json
import socket
import urllib.error
from datetime import timedelta
from pathlib import Path

import pytest

from backend.app.autonomy import activation as A
from backend.app.autonomy import registry
from backend.app.autonomy import reports as R
from backend.app.autonomy.control_plane import ControlPlaneError, PermissionDenied
from backend.app.core import health_monitoring as hm
from test_autonomy_control_plane_332b import OP, Clock, events, make_cp, proposal, set_all


# ============================================================ 3.32H health monitoring
def live(ok=True, ms=20.0, reason=None):
    return hm.ProbeResult(ok, ms, reason or ("ok" if ok else "timeout"))


def test_slow_dashboard_is_degraded_and_never_restarted():
    ev = hm.HealthEvaluator(hm.MonitorConfig(slow_latency_ms=2500))
    for _ in range(10):
        d = ev.observe(live(True, 4000), live(True, 3000), process_alive=False)
        assert d.state == hm.DEGRADED and d.action == hm.ACTION_NONE


def test_not_ready_is_degraded_not_restarted():
    ev = hm.HealthEvaluator()
    d = ev.observe(live(True), hm.ProbeResult(False, 15, "not_ready"), process_alive=True)
    assert d.state == hm.DEGRADED and d.action == hm.ACTION_NONE and "not ready" in d.reason


def test_failures_below_threshold_are_unavailable_then_unhealthy():
    ev = hm.HealthEvaluator(hm.MonitorConfig(failure_threshold=3))
    states = [ev.observe(live(False), None, process_alive=False) for _ in range(4)]
    assert [s.state for s in states] == [hm.UNAVAILABLE, hm.UNAVAILABLE, hm.UNHEALTHY, hm.UNHEALTHY]
    assert [s.action for s in states] == [hm.ACTION_NONE, hm.ACTION_NONE, hm.ACTION_RESTART, hm.ACTION_RESTART]


def test_unhealthy_with_live_process_is_reported_not_restarted():
    ev = hm.HealthEvaluator(hm.MonitorConfig(failure_threshold=2))
    ev.observe(live(False), None, process_alive=True)
    d = ev.observe(live(False), None, process_alive=True)
    assert d.state == hm.UNHEALTHY and d.action == hm.ACTION_NONE and "process is alive" in d.reason


def test_recovery_hysteresis():
    ev = hm.HealthEvaluator(hm.MonitorConfig(failure_threshold=2, recovery_threshold=3))
    ev.observe(live(False), None, False)
    ev.observe(live(False), None, False)
    seq = [ev.observe(live(True), live(True), True).state for _ in range(4)]
    assert seq == [hm.DEGRADED, hm.DEGRADED, hm.HEALTHY, hm.HEALTHY]
    # a single failure in between does not flip a healthy dashboard to unhealthy
    assert ev.observe(live(False), None, True).state == hm.UNAVAILABLE


def test_config_is_clamped_and_tolerates_bad_files(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"liveness_timeout_sec": 0.01, "failure_threshold": 1, "max_restarts_per_hour": 999,
                             "slow_latency_ms": "abc"}), encoding="utf-8")
    c = hm.load_config(p)
    assert c.liveness_timeout_sec == 1.0 and c.failure_threshold == 2 and c.max_restarts_per_hour == 6
    assert c.slow_latency_ms == 2500.0
    p.write_text("{not json", encoding="utf-8")
    assert hm.load_config(p).source.startswith("defaults")
    assert hm.load_config(tmp_path / "missing.json").failure_threshold == 3


class _Resp:
    def __init__(self, status=200, body=b'{"status":"alive"}'):
        self.status, self.body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


@pytest.mark.parametrize("exc,reason", [
    (urllib.error.URLError(socket.timeout("timed out")), "timeout"),
    (socket.timeout("timed out"), "timeout"),
    (urllib.error.URLError(ConnectionRefusedError(10061, "refused")), "connection_refused"),
    (urllib.error.HTTPError("u", 500, "x", {}, None), "http_500"),
    (ValueError("weird"), "error:ValueError"),
])
def test_probe_failure_reasons(exc, reason):
    def opener(req, timeout):
        raise exc
    r = hm.http_probe("http://127.0.0.1:1/x", 1.0, opener=opener)
    assert not r.ok and r.reason == reason and r.latency_ms >= 0


def test_probe_payload_checks_and_legacy_fallback():
    assert hm.http_probe("http://127.0.0.1:1/u", 1, expect=lambda p: p.get("status") == "alive", opener=lambda r, timeout: _Resp()).ok
    assert hm.http_probe("http://127.0.0.1:1/u", 1, expect=lambda p: False, opener=lambda r, timeout: _Resp()).reason == "not_ready"
    assert hm.http_probe("http://127.0.0.1:1/u", 1, expect=lambda p: True, opener=lambda r, timeout: _Resp(body=b"<html>")).reason == "bad_payload"
    calls = []

    def opener(req, timeout):
        calls.append((req.full_url, timeout))
        if req.full_url.endswith("/api/health/live"):
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        return _Resp(body=b"{}")
    cfg = hm.MonitorConfig()
    res = hm.probe_liveness(cfg, opener=opener)
    assert res.ok and "legacy" in res.detail
    assert calls[-1] == (cfg.base_url + hm.LEGACY_HEALTH_PATH, cfg.readiness_timeout_sec)   # longer timeout


def test_ledger_and_summary(tmp_path):
    led = hm.HealthLedger(tmp_path)
    ev = hm.HealthEvaluator()
    cfg = hm.MonitorConfig()
    for ok, ms in [(True, 10), (True, 30), (False, 5000), (True, 20)]:
        led.record_decision(ev.observe(live(ok, ms), live(True) if ok else None, True), cfg, [])
    led.record_restart("restarted", "test", 5.0)
    st = hm.read_state(tmp_path)
    assert st["last"]["state"] in hm.STATES and st["config"]["failure_threshold"] == 3
    s = hm.summarize_events(hm.read_events(tmp_path))
    assert s["available"] and s["probes"] == 4 and s["restarts"] == 1 and s["availability_pct"] == 75.0
    assert s["failure_reasons"] == {"timeout": 1} and s["liveness_latency_ms"]["max"] == 30.0
    assert hm.summarize_events([])["available"] is False


def _monitor(tmp_path, monkeypatch, lives, alive, restart_ok=True, max_restarts=3):
    from backend.scripts import startup_manager as sm
    monkeypatch.setattr(sm, "MONITOR_LOCK", tmp_path / "monitor.lock")
    monkeypatch.setattr(sm, "ensure_scheduler_running_if_authorized", lambda: False)
    monkeypatch.setattr(hm, "load_config", lambda path=None: hm.MonitorConfig(max_restarts_per_hour=max_restarts))
    it = iter(lives)
    restarts, sleeps = [], []
    res = sm.run_health_monitor_loop(
        interval_sec=1, max_iterations=len(lives), sleep_fn=sleeps.append,
        live_probe=lambda: next(it), ready_probe=lambda: live(True, 10),
        process_alive=alive, restart_fn=lambda: restarts.append(1) or restart_ok, logs_dir=tmp_path)
    return res, restarts, sleeps


def test_monitor_never_restarts_a_slow_dashboard(tmp_path, monkeypatch):
    res, restarts, _ = _monitor(tmp_path, monkeypatch, [live(True, 9000)] * 6, lambda: False)
    assert res["restarts"] == 0 and restarts == []
    assert hm.read_state(tmp_path)["last"]["state"] == hm.DEGRADED


def test_monitor_restarts_only_after_threshold_and_dead_process(tmp_path, monkeypatch):
    res, restarts, sleeps = _monitor(tmp_path, monkeypatch, [live(False)] * 3, lambda: False)
    assert res["restarts"] == 1 and len(restarts) == 1
    assert 5.0 in sleeps                                             # backoff before restarting
    out = [e for e in hm.read_events(tmp_path) if e["type"] == "restart"]
    assert out[0]["outcome"] == "restarted"


def test_monitor_does_not_restart_live_process(tmp_path, monkeypatch):
    res, restarts, _ = _monitor(tmp_path, monkeypatch, [live(False)] * 5, lambda: True)
    assert res["restarts"] == 0 and restarts == []
    assert hm.read_state(tmp_path)["last"]["state"] == hm.UNHEALTHY


def test_monitor_restart_cap_and_process_appearing(tmp_path, monkeypatch):
    res, restarts, _ = _monitor(tmp_path, monkeypatch, [live(False)] * 6, lambda: False, max_restarts=1)
    assert res["restarts"] == 1
    outcomes = [e["outcome"] for e in hm.read_events(tmp_path) if e["type"] == "restart"]
    assert outcomes[0] == "restarted" and set(outcomes[1:]) == {"suppressed_cap"}
    seq = iter([False, True])                       # dead at decision time, alive again after the backoff
    res2, restarts2, _ = _monitor(tmp_path / "b", monkeypatch, [live(False)] * 3, lambda: next(seq, True))
    assert res2["restarts"] == 0 and restarts2 == []


def test_health_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    from backend.app.core import paths
    c = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r = c.get("/api/health/live")
    assert r.status_code == 200 and r.json()["status"] == "alive"
    db = tmp_path / "s.db"
    import sqlite3
    sqlite3.connect(db).close()
    monkeypatch.setattr(paths, "DATABASE_PATH", db)
    assert c.get("/api/health/ready").json()["status"] == "ready"
    monkeypatch.setattr(paths, "DATABASE_PATH", tmp_path / "missing" / "none.db")
    r = c.get("/api/health/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready"


# ============================================================ 3.32I phased activation
def healthy_state(clock):
    return lambda: {"last": {"state": "HEALTHY", "reason": "live and ready", "live_latency_ms": 9.0,
                             "at_utc": clock().strftime(A.TS_FMT)}}


def summary_ok(_since):
    return {"available": True, "availability_pct": 100.0, "probes": 500, "restarts": 0}


def mgr(tmp_path, *, startup=False, health=None, summary=summary_ok, clock=None):
    clock = clock or Clock()
    tmp_path.mkdir(parents=True, exist_ok=True)
    cp = make_cp(tmp_path, startup=startup, clock=clock)
    return A.ActivationManager(cp, health_state=health or healthy_state(clock), health_summary=summary), cp, clock


def test_default_phase_off_and_reads_create_no_tables(tmp_path):
    m, cp, _ = mgr(tmp_path)
    assert m.state()["phase"] == A.OFF and m.history() == [] and m.observation_stats()["cycles"] == 0
    with cp._read() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not names & {"autonomy_activation_state", "autonomy_activation_history", "autonomy_observation_cycles"}


def test_observe_activation_sets_only_observe_levels(tmp_path):
    m, cp, _ = mgr(tmp_path)
    r = m.assess(A.OBSERVE)
    assert r["ready"], r["blockers"]
    m.advance(A.OBSERVE, OP, "start observing", confirm=True)
    perms = cp.permissions()
    assert perms["global_level"] == "OBSERVE"
    assert all(perms["effective_levels"][c] == "OBSERVE" for c in A.PHASE_CATEGORIES)
    assert perms["effective_levels"]["community_drafts"] == "OFF"
    assert m.state()["phase"] == A.OBSERVE and m.state()["phase_started_at_utc"]
    assert "ACTIVATION_PHASE_ADVANCED" in events(cp)
    assert not cp.startup_config_path.exists()                                # never created
    assert cp.permissions().get("standing_approvals", {}) in ({}, None) or not any(
        (cp.permissions().get("standing_approvals") or {}).values())
    with pytest.raises(PermissionDenied):                                     # OBSERVE: proposals refused
        proposal(cp)


def test_observe_needs_confirmation_and_operator(tmp_path):
    m, cp, _ = mgr(tmp_path)
    with pytest.raises(A.ActivationRefused):
        m.advance(A.OBSERVE, OP, "no confirm")
    assert m.state()["phase"] == A.OFF and m.history()[0]["outcome"] == "REFUSED"
    with pytest.raises(ControlPlaneError):
        m.advance(A.OBSERVE, "autonomy-engine", "reserved identity", confirm=True)
    with pytest.raises(ControlPlaneError):
        m.advance(A.OBSERVE, OP, "", confirm=True)


def test_phases_cannot_be_skipped(tmp_path):
    m, cp, _ = mgr(tmp_path, startup=True)
    for target in (A.RECOMMEND, A.RESTRICTED_EXECUTION, A.EXPANSION):
        with pytest.raises(A.ActivationRefused) as exc:
            m.advance(target, OP, "skip", confirm=True, authorization=f"AUTHORIZE {target}",
                      authorized_actions=["diagnostic.readonly.run"] if target != A.RECOMMEND else None)
        assert any(b["id"] == "sequential" for b in exc.value.blockers)
    assert cp.permissions()["global_level"] == "OFF"


def test_observe_blocked_without_health_monitor_or_with_kill_switch(tmp_path):
    m, cp, _ = mgr(tmp_path, health=lambda: {})
    assert {b["id"] for b in m.assess(A.OBSERVE)["blockers"]} == {"dashboard_health_monitor"}
    m2, cp2, clock = mgr(tmp_path / "b")
    cp2.set_dashboard_kill_switch(True, OP, "drill")
    assert "kill_switches" in {b["id"] for b in m2.assess(A.OBSERVE)["blockers"]}
    stale = A.ActivationManager(cp2, health_state=lambda: {"last": {"state": "HEALTHY", "at_utc": (clock() - timedelta(hours=1)).strftime(A.TS_FMT)}})
    assert "dashboard_health_monitor" in {b["id"] for b in stale.assess(A.OBSERVE)["blockers"]}


def test_unhealthy_dashboard_blocks_activation(tmp_path):
    clock = Clock()
    m, cp, _ = mgr(tmp_path, clock=clock, health=lambda: {"last": {"state": "UNHEALTHY", "at_utc": clock().strftime(A.TS_FMT)}})
    assert "dashboard_health_monitor" in {b["id"] for b in m.assess(A.OBSERVE)["blockers"]}


def _observe_for(m, clock, hours, cycles):
    m.advance(A.OBSERVE, OP, "observe", confirm=True)
    for _ in range(cycles):
        clock.advance(minutes=30)
        m.run_observation_cycle(assessment=lambda: {"ran": True, "observations": 1},
                                recommendations=lambda: {"ran": True, "mode": "OBSERVE", "findings": []},
                                self_healing=lambda: {"ran": True, "mode": "OBSERVE"})
    clock.advance(hours=max(0, hours - cycles * 0.5))


def test_recommend_needs_evidence_and_phrase(tmp_path):
    m, cp, clock = mgr(tmp_path)
    _observe_for(m, clock, hours=10, cycles=4)
    blockers = {b["id"] for b in m.assess(A.RECOMMEND)["blockers"]}
    assert {"observation_period", "observation_cycles"} <= blockers
    clock.advance(hours=48)
    for _ in range(48):
        m.run_observation_cycle(assessment=lambda: {}, recommendations=lambda: {}, self_healing=lambda: {})
    r = m.assess(A.RECOMMEND)
    assert r["ready"], r["blockers"]
    with pytest.raises(A.ActivationRefused) as exc:
        m.advance(A.RECOMMEND, OP, "go", confirm=True, authorization="authorize recommend")
    assert any(b["id"] == "authorization" for b in exc.value.blockers)
    assert m.state()["phase"] == A.OBSERVE
    m.advance(A.RECOMMEND, OP, "go", confirm=True, authorization="AUTHORIZE RECOMMEND")
    assert cp.permissions()["effective_levels"]["diagnostics"] == "RECOMMEND"
    assert proposal(cp)["status"] == "PENDING"                                # proposals queue ...
    assert not cp.status()["execution_permitted"]                             # ... nothing executes


def test_recommend_blocked_by_poor_availability_or_failed_cycles(tmp_path):
    m, cp, clock = mgr(tmp_path, summary=lambda s: {"available": True, "availability_pct": 90.0, "probes": 100, "restarts": 2})
    _observe_for(m, clock, hours=60, cycles=50)
    assert "dashboard_availability" in {b["id"] for b in m.assess(A.RECOMMEND)["blockers"]}
    m2, cp2, clock2 = mgr(tmp_path / "b")
    m2.advance(A.OBSERVE, OP, "observe", confirm=True)
    clock2.advance(hours=50)

    def boom():
        raise RuntimeError("engine failure")
    for _ in range(50):
        m2.run_observation_cycle(assessment=boom, recommendations=lambda: {}, self_healing=lambda: {})
    assert "observation_cycles" in {b["id"] for b in m2.assess(A.RECOMMEND)["blockers"]}


def test_observation_cycle_off_is_noop_and_never_advances(tmp_path):
    m, cp, clock = mgr(tmp_path)
    assert m.run_observation_cycle()["ran"] is False
    m.advance(A.OBSERVE, OP, "observe", confirm=True)
    clock.advance(days=30)
    for _ in range(100):
        m.run_observation_cycle(assessment=lambda: {}, recommendations=lambda: {}, self_healing=lambda: {})
    assert m.state()["phase"] == A.OBSERVE                                    # never advances by itself
    assert m.observation_stats()["cycles"] == 100


def test_restricted_execution_needs_startup_file_and_allowed_actions(tmp_path):
    m, cp, clock = mgr(tmp_path)          # no autonomy_startup.json
    _observe_for(m, clock, hours=60, cycles=50)
    m.advance(A.RECOMMEND, OP, "rec", confirm=True, authorization="AUTHORIZE RECOMMEND")
    clock.advance(days=8)
    blockers = {b["id"] for b in m.assess(A.RESTRICTED_EXECUTION)["blockers"]}
    assert {"startup_execution_enabled", "human_reviewed_proposals"} <= blockers
    with pytest.raises(A.ActivationRefused) as exc:
        m.advance(A.RESTRICTED_EXECUTION, OP, "x", confirm=True, authorization="AUTHORIZE RESTRICTED_EXECUTION",
                  authorized_actions=["community.draft.prepare", "youtube.community.publish"])
    ids = [b["id"] for b in exc.value.blockers]
    assert "authorized_actions" in ids and "startup_execution_enabled" in ids
    assert cp.permissions()["global_level"] == "RECOMMEND"


def test_restricted_execution_with_evidence_limits_categories_and_grants_nothing(tmp_path):
    m, cp, clock = mgr(tmp_path, startup=True)
    _observe_for(m, clock, hours=60, cycles=50)
    m.advance(A.RECOMMEND, OP, "rec", confirm=True, authorization="AUTHORIZE RECOMMEND")
    for i in range(5):
        req = proposal(cp, payload={"diagnostic": "kill_switch_status", "days": i + 1})
        cp.approve(req["id"], OP, req["payload_hash"], "reviewed")
    clock.advance(days=8)
    r = m.assess(A.RESTRICTED_EXECUTION)
    assert r["ready"], r["blockers"]
    m.advance(A.RESTRICTED_EXECUTION, OP, "restricted", confirm=True, authorization="AUTHORIZE RESTRICTED_EXECUTION",
              authorized_actions=["diagnostic.readonly.run"])
    eff = cp.permissions()["effective_levels"]
    assert eff["diagnostics"] == eff["reporting"] == "APPROVED_EXECUTION"
    assert eff["notifications"] == eff["self_healing"] == eff["recommendations"] == "RECOMMEND"
    assert eff["community_drafts"] == "OFF"
    assert not any(e == "STANDING_APPROVAL_GRANTED" for e in events(cp))
    with pytest.raises(registry.ProhibitedActionError):
        cp.propose("youtube.community.publish", {}, reason="x", proposed_by="autonomy-engine")
    # retreat is always allowed and lowers levels
    m.retreat(A.OBSERVE, OP, "back off")
    assert cp.permissions()["effective_levels"]["diagnostics"] == "OBSERVE"
    with pytest.raises(ControlPlaneError):
        m.retreat(A.RECOMMEND, OP, "not lower")


def test_level_drift_is_reported(tmp_path):
    m, cp, _ = mgr(tmp_path)
    m.advance(A.OBSERVE, OP, "observe", confirm=True)
    cp.set_level("diagnostics", "OFF", OP, "manual change")
    assert m.overview()["level_drift"] == {"diagnostics": {"expected": "OBSERVE", "actual": "OFF"}}


def test_activation_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    m, cp, _ = mgr(tmp_path)
    monkeypatch.setattr(main, "_activation", lambda: m)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    remote = TestClient(main.app)
    h = {"X-CrazzyDocs-CSRF": local.get("/api/autonomy/session").json()["csrf_token"]}
    assert remote.get("/api/autopilot/activation").status_code == 403
    assert local.get("/api/autopilot/activation").json()["state"]["phase"] == A.OFF
    assert local.get("/api/autopilot/activation/readiness?phase=OBSERVE").json()["ready"] is True
    body = {"operator": OP, "target": "OBSERVE", "reason": "go", "confirm": True}
    assert local.post("/api/autopilot/activation/advance", json=body).status_code == 403          # no CSRF
    assert local.post("/api/autopilot/activation/advance", json=body | {"grant": True}, headers=h).status_code == 422
    r = local.post("/api/autopilot/activation/advance", json=body | {"target": "RECOMMEND"}, headers=h)
    assert r.status_code == 403 and "refused" in r.json()["detail"]
    assert local.post("/api/autopilot/activation/advance", json=body, headers=h).json()["phase"] == "OBSERVE"
    r = local.post("/api/autopilot/activation/retreat", json={"operator": OP, "target": "OFF", "reason": "r"}, headers=h)
    assert r.json()["phase"] == "OFF" and cp.permissions()["global_level"] == "OFF"


# ============================================================ 3.32J reports
class FakeIntel:
    def __init__(self, missing=False):
        self.missing = missing

    def _sec(self, metrics, unavailable=None):
        return {"status": "actual", "metrics": metrics, "sources": ["fake"], "unavailable": unavailable or {}}

    def production_health(self, **kw):
        if self.missing:
            return {"status": "unavailable", "metrics": {}, "unavailable": {"all": "database not found"}}
        return self._sec({"total_cycles": 10, "finished": 10, "completed": 6, "failed": 1, "recovery_window_expired": 3})

    def recovery_analytics(self, **kw):
        return self._sec({"recovered": 1}, {"avg_time_to_recover_minutes": "no recoveries"})

    def renderer_reliability(self, **kw):
        return self._sec({"render_success_pct": 100.0, "renders_completed": 6, "renders_failed": 0})

    def failure_intelligence(self, **kw):
        raise RuntimeError("intelligence offline")


def svc(tmp_path, *, missing=False, health=None):
    clock = Clock()
    cp = make_cp(tmp_path, clock=clock)
    m = A.ActivationManager(cp, health_state=healthy_state(clock), health_summary=summary_ok)
    s = R.ReportService(cp, intelligence_factory=lambda end: FakeIntel(missing), activation=lambda: m,
                        health_events=health or (lambda a, b: []))
    return s, cp, clock, m


def test_daily_report_separates_facts_metrics_interpretations(tmp_path):
    s, cp, clock, m = svc(tmp_path, health=lambda a, b: [
        {"type": "probe", "at_utc": a, "live_ok": True, "live_latency_ms": 12, "state": "HEALTHY"},
        {"type": "probe", "at_utc": a, "live_ok": False, "live_latency_ms": 5000, "live_reason": "timeout", "state": "UNAVAILABLE"}])
    day = clock().astimezone(R.IST).date().isoformat()
    rep = s.generate("daily", day)
    assert rep["id"] == f"rpt_daily_{day}" and rep["period"]["complete"] is False
    assert set(rep) >= {"observed_facts", "calculated_metrics", "interpretations", "ai_interpretation", "missing", "provenance"}
    assert rep["calculated_metrics"]["production_success_pct"] == {"value": 60.0, "formula": "completed / finished cycles x 100",
                                                                   "inputs": {"completed": 6, "finished": 10, "total_cycles": 10}}
    assert rep["calculated_metrics"]["dashboard_availability_pct"]["value"] == 50.0
    assert rep["ai_interpretation"]["generated"] is False
    assert all(i["source"] == "rule" and i["cites"] for i in rep["interpretations"])
    assert any("below 80%" in i["text"] for i in rep["interpretations"])
    assert all(a["executes_automatically"] is False for a in rep["recommended_actions"])
    items = {x["item"] for x in rep["missing"]}
    assert "failures" in items and "recovery.avg_time_to_recover_minutes" in items
    assert "rendering_failure_trend" not in rep["observed_facts"]                 # not invented from missing data
    assert rep["stored"]["sha256_verified"] is True


def test_missing_data_is_never_zero(tmp_path):
    s, cp, clock, m = svc(tmp_path, missing=True)
    rep = s.generate("daily")
    assert "production_success_pct" not in rep["calculated_metrics"]
    assert any(x["item"] == "production.all" for x in rep["missing"])
    assert any(x["item"] == "health_monitor" for x in rep["missing"])
    assert "dashboard_availability_pct" not in rep["calculated_metrics"]


def test_report_counts_blocked_actions_and_executions(tmp_path):
    s, cp, clock, m = svc(tmp_path)
    set_all(cp, "RECOMMEND")
    with pytest.raises(registry.ProhibitedActionError):
        cp.propose("youtube.community.publish", {}, reason="x", proposed_by="autonomy-engine")
    proposal(cp)
    rep = s.generate("weekly")
    f = rep["observed_facts"]
    assert f["blocked_actions"]["by_event"].get("PROHIBITED_ACTION_BLOCKED") == 1
    assert f["approvals"].get("PENDING") == 1 and f["executions"] == {}
    assert any("permanently gated" in i["text"] for i in rep["interpretations"])
    assert f["autonomy"]["phase"] == "OFF" and f["autonomy"]["next_phase"] == "OBSERVE"


def test_completed_periods_are_immutable_unless_forced(tmp_path):
    s, cp, clock, m = svc(tmp_path)
    day = (clock() - timedelta(days=2)).astimezone(R.IST).date().isoformat()
    first = s.generate("daily", day)
    assert first["period"]["complete"] is True
    clock.advance(hours=3)
    again = s.generate("daily", day)
    assert again.get("cached") and again["generated_at_utc"] == first["generated_at_utc"]
    forced = s.generate("daily", day, force=True)
    assert forced["generated_at_utc"] != first["generated_at_utc"]
    with pytest.raises(ControlPlaneError):
        s.generate("daily", (clock() + timedelta(days=3)).astimezone(R.IST).date().isoformat())   # future period
    with pytest.raises(ControlPlaneError):
        s.generate("monthly")
    assert [r["period_key"] for r in s.list_reports("daily")] == [day]


def test_weekly_period_boundaries():
    p = R.weekly_period("2026-W47")
    assert p["start"].astimezone(R.IST).strftime("%Y-%m-%d %H:%M %a") == "2026-11-16 00:00 Mon"
    assert (p["end"] - p["start"]) == timedelta(days=7)
    with pytest.raises(ControlPlaneError):
        R.weekly_period("2026-47")


def test_reports_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    s, cp, clock, m = svc(tmp_path)
    monkeypatch.setattr(main, "_reports", lambda: s)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    h = {"X-CrazzyDocs-CSRF": local.get("/api/autonomy/session").json()["csrf_token"]}
    assert local.post("/api/autopilot/reports/generate", json={"operator": OP, "kind": "daily"}).status_code == 403
    assert local.post("/api/autopilot/reports/generate", json={"operator": OP, "kind": "yearly"}, headers=h).status_code == 422
    r = local.post("/api/autopilot/reports/generate", json={"operator": OP, "kind": "daily"}, headers=h).json()
    key = r["period"]["key"]
    assert r["generated_by"] == OP
    assert local.get(f"/api/autopilot/reports/daily/{key}").json()["id"] == r["id"]
    assert local.get("/api/autopilot/reports/daily/..%2F..").status_code == 404
    assert local.get("/api/autopilot/reports/weekly/2020-W01").status_code == 404
    assert len(local.get("/api/autopilot/reports").json()["reports"]) == 1


def test_autopilot_tick_runs_observation_and_report_upkeep(tmp_path, monkeypatch):
    s, cp, clock, m = svc(tmp_path)
    monkeypatch.setattr(R, "get_reports", lambda: s)
    monkeypatch.setattr(A, "get_activation", lambda: m)
    out = R.autopilot_tick(now=clock())
    assert out["observation"]["ran"] is False                                   # phase OFF: no observation
    assert sum(1 for k, v in out.items() if k != "observation" and v == "generated") == 4
    assert cp.permissions()["global_level"] == "OFF"                            # tick never changes levels


# ============================================================ dashboard / scheduler consistency
def test_dashboard_wires_hij_panels_without_execution_controls():
    html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="apHealth"', 'id="apRender"', 'id="apActivation"', 'id="apReportList"', "/api/autopilot/operations",
                   "function refreshAutopilotOps", "refreshAutopilotOps(force);"):
        assert needle in html, needle
    block = html[html.index("// -- Stage 3.32H/I/J operations"):html.index("</script>", html.index("// -- Stage 3.32H/I/J operations"))]
    assert "/execute" not in block and "/approve" not in block and "standing" not in block.lower().replace("no standing approval is granted", "")
    assert "setInterval" not in block                                             # no extra polling


def test_scheduler_registers_autopilot_tick_off_cycle_minutes():
    src = (Path(__file__).resolve().parents[1] / "app" / "scheduler" / "scheduler.py").read_text(encoding="utf-8")
    i = src.index('id="autopilot_tick"')
    chunk = src[i - 300:i + 200]
    assert 'minute="11,41"' in chunk and "max_instances=1" in chunk and "coalesce=True" in chunk
    from backend.app.scheduler import scheduler as sch
    monkey = {"called": False}

    def boom():
        monkey["called"] = True
        raise RuntimeError("x")
    import backend.app.autonomy.reports as rmod
    orig = rmod.autopilot_tick
    rmod.autopilot_tick = boom
    try:
        assert sch.run_autopilot_tick() == {"error": "RuntimeError"} and monkey["called"]
    finally:
        rmod.autopilot_tick = orig
