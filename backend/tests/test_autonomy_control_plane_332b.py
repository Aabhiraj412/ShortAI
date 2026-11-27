"""Stage 3.32B Autonomy Control Plane.

Every test uses a temporary SQLite database, a temporary data directory, a fixed clock and a fake
Operational Intelligence service. No production database, file, service or network is touched.
"""

import dataclasses
import itertools
import json
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backend.app.autonomy.control_plane as cpm
from backend.app.autonomy import engine, handlers, registry
from backend.app.autonomy.control_plane import (
    APPROVED, CANCELLED, COMPLETED, EXECUTING, EXPIRED, FAILED, PENDING, REJECTED, Conflict, ControlPlane,
    PermissionDenied, Unavailable,
)
from backend.app.autonomy.registry import Level, ProhibitedActionError, UnknownActionError

T0 = datetime(2026, 11, 20, 6, 30, tzinfo=timezone.utc)
OP = "abhi"


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now = self.now + timedelta(**kw)


class FakeIntelligence:
    def __init__(self):
        self.calls = 0

    def daily_summary(self, date=None):
        self.calls += 1
        return {"date": date or "2026-11-20", "health": "DEGRADED",
                "anomalies": [{"text": "Repeated ffmpeg failure (3x)", "severity": "high"},
                              {"text": "Low pool run", "severity": "medium"}],
                "recommendations": ["Investigate the FFmpeg padding error", "Review wikipedia_search acceptance"]}

    def production_health(self, days=7, date=None):
        return {"status": "actual", "metrics": {"total_cycles": 4, "completed": 2}}

    def failure_intelligence(self, days=7, date=None):
        return {"status": "actual", "metrics": {"total_failures": 2}}


def make_cp(tmp_path, *, startup=True, environ=None, clock=None, handlers_map=None, db=True, name="studio.db",
            policy=False):
    """policy=False: these tests exercise the explicit/standing approval machinery, so the Remediation WS2 built-in
    automatic policy is disabled here; test_remediation_ws2_autonomy_control_states.py covers it enabled."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    db_path = tmp_path / name
    if db and not db_path.exists():
        sqlite3.connect(db_path).close()
    if startup:
        (data / "autonomy_startup.json").write_text(json.dumps({"autonomy_execution": "enabled"}), encoding="utf-8")
    intel = FakeIntelligence()
    cp = ControlPlane(str(db_path), str(data), environ=environ if environ is not None else {},
                      intelligence=lambda: intel, clock=clock or Clock(), handlers=handlers_map,
                      automatic_policy=None if policy else {})
    cp._fake_intel = intel
    return cp


def set_all(cp, level):
    cp.set_level("global", level, OP, "test")
    for c in registry.CATEGORIES:
        cp.set_level(c, level, OP, "test")


def proposal(cp, action="diagnostic.readonly.run", payload=None, reason="test"):
    payload = payload if payload is not None else {"diagnostic": "kill_switch_status"}
    return cp.propose(action, payload, reason=reason, proposed_by="autonomy-engine")["request"]


def approved(cp, **kw):
    req = proposal(cp, **kw)
    cp.approve(req["id"], OP, req["payload_hash"])
    return req


def events(cp, request_id=None):
    return [e["event_type"] for e in reversed(cp.audit_events(limit=500, request_id=request_id))]


@pytest.fixture
def cp(tmp_path):
    c = make_cp(tmp_path)
    set_all(c, "APPROVED_EXECUTION")
    return c


# 1. Autonomy levels ---------------------------------------------------------------------------------------
def test_default_is_off_everywhere(tmp_path):
    c = make_cp(tmp_path)
    st = c.status()
    assert st["global_level"] == "OFF" and not st["autonomy_enabled"] and not st["execution_permitted"]
    assert set(st["effective_levels"].values()) == {"OFF"}
    assert c.record_observation("diagnostics", "x") == {"recorded": False, "reason": "diagnostics autonomy level is OFF"}
    with pytest.raises(PermissionDenied):
        proposal(c)
    assert engine.run_assessment(c)["ran"] is False
    assert c._fake_intel.calls == 0                                  # OFF: the engine does not even read
    assert "OBSERVATION_RECORDED" not in events(c)


def test_observe_level_records_but_cannot_propose(tmp_path):
    c = make_cp(tmp_path)
    set_all(c, "OBSERVE")
    assert c.record_observation("diagnostics", "queue is long", {"n": 3})["recorded"] is True
    with pytest.raises(PermissionDenied):
        proposal(c)
    out = engine.run_assessment(c)
    assert out["ran"] and out["observations"] == 2 and out["proposals"] == []
    assert "PROPOSAL_REFUSED" in events(c) and "OBSERVATION_RECORDED" in events(c)
    assert c.list_requests() == []


def test_recommend_level_queues_but_never_executes(tmp_path):
    c = make_cp(tmp_path)
    set_all(c, "RECOMMEND")
    req = approved(c)
    res = c.execute(req["id"], "operator:abhi")
    assert res["executed"] is False and "APPROVED_EXECUTION" in res["reason"]
    assert c.get_request(req["id"])["status"] == APPROVED                   # still waiting, not consumed
    assert c.list_executions() == []
    out = engine.run_assessment(c)
    assert {p["action_type"] for p in out["proposals"]} == {"recommendation.record", "report.operational.generate", "operator.notify"}
    assert all(p["status"] == PENDING and not p["executed"] for p in out["proposals"])


def test_approved_execution_runs_and_verifies(cp):
    req = approved(cp, action="report.operational.generate", payload={"date": "2026-11-20", "days": 2})
    res = cp.execute(req["id"], "operator:abhi")
    assert res["executed"] and res["status"] == COMPLETED
    final = cp.get_request(req["id"])
    path = Path(final["execution_result"]["result"]["path"])
    assert path.is_file() and path.parent == cp.autonomy_dir / "reports"
    assert final["execution_result"]["verification"]["ok"] is True
    assert events(cp, req["id"]) == ["ACTION_PROPOSED", "APPROVED", "EXECUTION_STARTED", "EXECUTION_COMPLETED"]


def test_levels_are_per_category_and_global_caps(tmp_path):
    c = make_cp(tmp_path)
    c.set_level("global", "APPROVED_EXECUTION", OP, "t")
    c.set_level("diagnostics", "APPROVED_EXECUTION", OP, "t")
    c.set_level("reporting", "RECOMMEND", OP, "t")
    eff = c.permissions()["effective_levels"]
    assert eff["diagnostics"] == "APPROVED_EXECUTION" and eff["reporting"] == "RECOMMEND" and eff["notifications"] == "OFF"
    with pytest.raises(PermissionDenied):
        proposal(c, action="operator.notify", payload={"subject": "s", "message": "m"})
    rep = approved(c, action="report.operational.generate", payload={"date": "2026-11-20"})
    assert c.execute(rep["id"], "operator:abhi")["executed"] is False
    diag = approved(c)
    assert c.execute(diag["id"], "operator:abhi")["executed"] is True
    c.set_level("global", "OBSERVE", OP, "lower the cap")                  # global caps every category
    assert set(c.permissions()["effective_levels"].values()) <= {"OBSERVE", "OFF"}
    with pytest.raises(cpm.ControlPlaneError):
        c.set_level("global", "TURBO", OP, "t")
    with pytest.raises(cpm.ControlPlaneError):
        c.set_level("not_a_category", "OFF", OP, "t")


# 2. Unknown actions ---------------------------------------------------------------------------------------
def test_unknown_action_types_are_rejected(cp):
    for bad in ("youtube.video.upload.now", "", None, 42, "REPORT.OPERATIONAL.GENERATE"):
        with pytest.raises(UnknownActionError):
            cp.propose(bad, {}, reason="x")
    with pytest.raises(UnknownActionError):
        cp.grant_standing_approval("made.up", OP, "x", max_per_day=1, valid_hours=1)
    assert events(cp).count("UNKNOWN_ACTION_REJECTED") >= 5
    with pytest.raises(registry.PayloadValidationError):
        proposal(cp, payload={"diagnostic": "kill_switch_status", "extra": 1})
    with pytest.raises(registry.PayloadValidationError):
        proposal(cp, payload={"diagnostic": "rm -rf"})
    with pytest.raises(registry.PayloadValidationError):
        proposal(cp, action="report.operational.generate", payload={"days": True})


# 3. Permanently prohibited actions -----------------------------------------------------------------------
REQUIRED_PROHIBITIONS = {
    "production.scheduler.start", "production.scheduler.stop", "production.cycle.create", "production.schedule.change",
    "render.worker.launch", "worker.lock.bypass", "recovery.deadline.change", "topic_history.modify",
    "duplicate_detection.bypass", "quality_checks.bypass", "oauth.credentials.change", "oauth.scopes.change",
    "youtube.video.publish", "youtube.community.publish", "production.config.modify", "process.daemon.restart",
    "process.dashboard.restart", "production.data.delete", "git.branch.change", "git.destructive",
}


def test_prohibited_registry_covers_the_specification():
    prohibited = {s.action_type for s in registry.PROHIBITED}
    assert REQUIRED_PROHIBITIONS <= prohibited
    for s in registry.PROHIBITED:
        assert s.prohibited and s.risk == registry.RISK_PROHIBITED and s.approval == registry.APPROVAL_NEVER
        assert s.action_type not in handlers.HANDLERS
    assert set(handlers.HANDLERS) == {s.action_type for s in registry.PERMITTED}


def test_prohibited_actions_refused_everywhere(cp, tmp_path):
    for spec in registry.PROHIBITED:
        with pytest.raises(ProhibitedActionError):
            cp.propose(spec.action_type, {}, reason="try", proposed_by="autonomy-engine")
        with pytest.raises(ProhibitedActionError):
            cp.grant_standing_approval(spec.action_type, OP, "try", max_per_day=1, valid_hours=1)
    assert events(cp).count("PROHIBITED_ACTION_BLOCKED") >= 2 * len(registry.PROHIBITED)
    with pytest.raises(ProhibitedActionError):                      # no handler can be registered for one
        make_cp(tmp_path, handlers_map={"youtube.video.publish": handlers.RecommendationHandler()}, name="other.db")
    with pytest.raises(UnknownActionError):
        make_cp(tmp_path, handlers_map={"made.up": handlers.RecommendationHandler()}, name="other2.db")
    # A prohibited request injected straight into the database can be neither approved nor executed.
    req = proposal(cp)
    conn = sqlite3.connect(cp.db_path)
    conn.execute("UPDATE autonomy_approval_requests SET action_type='process.daemon.restart' WHERE id=?", (req["id"],))
    conn.commit()
    with pytest.raises(PermissionDenied):
        cp.approve(req["id"], OP, req["payload_hash"])
    conn.execute("UPDATE autonomy_approval_requests SET status='APPROVED' WHERE id=?", (req["id"],))
    conn.commit()
    conn.close()
    res = cp.execute(req["id"], "operator:abhi")
    assert res["executed"] is False and "prohibited" in res["reason"]


# 4. Kill switches -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("dash,sentinel,startup", list(itertools.product([False, True], repeat=3)))
def test_kill_switch_combinations(tmp_path, dash, sentinel, startup):
    c = make_cp(tmp_path, startup=not startup)
    set_all(c, "APPROVED_EXECUTION")
    req = approved(c)
    if dash:
        c.set_dashboard_kill_switch(True, OP, "stop")
    if sentinel:
        c.sentinel_path.write_text("stop", encoding="utf-8")
    ks = c.kill_switches()
    # Operator-dependency reduction: the kill switches are the emergency stop only; the missing optional startup
    # configuration is reported separately (control state RESTRICTED) and still closes the execution gate.
    assert ks["any_active"] == ks["emergency_stop"] == any((dash, sentinel))
    assert sorted(ks["active"]) == sorted(n for n, on in (("dashboard", dash), ("sentinel_file", sentinel)) if on)
    assert ks["startup_config"]["enabled"] is (not startup)
    assert ks["execution_gate_closed"] == any((dash, sentinel, startup))
    assert ks["control_state"] == ("EMERGENCY_STOP" if (dash or sentinel) else "RESTRICTED" if startup else "NORMAL")
    res = c.execute(req["id"], "operator:abhi")
    assert res["executed"] is (not any((dash, sentinel, startup)))      # blocking behaviour unchanged
    if any((dash, sentinel, startup)):
        expected = "emergency stop" if (dash or sentinel) else "not an emergency stop"
        assert res["status"] == "BLOCKED" and expected in res["reason"]
        assert c.get_request(req["id"])["status"] == APPROVED              # blocked, not lost
        assert c.status()["execution_permitted"] is False
        assert proposal(c, payload={"diagnostic": "audit_chain_verify"})["status"] == PENDING   # queueing still allowed


def test_kill_switches_fail_closed(tmp_path):
    c = make_cp(tmp_path, startup=False)
    assert c.startup_kill_switch["active"] and "absent" in c.startup_kill_switch["detail"]
    (tmp_path / "data" / "autonomy_startup.json").write_text("{not json", encoding="utf-8")
    assert make_cp(tmp_path, startup=False, name="b.db").startup_kill_switch["active"]
    (tmp_path / "data" / "autonomy_startup.json").write_text('{"autonomy_execution": "yes please"}', encoding="utf-8")
    assert make_cp(tmp_path, startup=False, name="c.db").startup_kill_switch["active"]
    on = make_cp(tmp_path, name="d.db", environ={"CRAZZYDOCS_AUTONOMY": "off"})        # env forces off even with the file
    assert on.startup_kill_switch["active"] and "CRAZZYDOCS_AUTONOMY" in on.startup_kill_switch["detail"]
    good = make_cp(tmp_path, name="e.db")
    assert not good.kill_switches()["any_active"]
    # The startup switch is latched at process start: editing the file later changes nothing until a restart.
    (tmp_path / "data" / "autonomy_startup.json").unlink()
    assert not good.kill_switches()["any_active"]
    # Database unavailable -> the dashboard switch reads as active.
    missing = make_cp(tmp_path, name="missing.db", db=False)
    ks = missing.kill_switches()
    assert ks["switches"]["dashboard"]["active"] and "fail closed" in ks["switches"]["dashboard"]["detail"]
    assert not Path(missing.db_path).exists()                              # never created


def test_sentinel_check_error_fails_closed(cp, monkeypatch):
    monkeypatch.setattr(cpm.os.path, "lexists", lambda p: (_ for _ in ()).throw(OSError("denied")))
    assert cp.kill_switches()["switches"]["sentinel_file"]["active"] is True


def test_kill_switch_activation_without_database_writes_sentinel(tmp_path):
    c = make_cp(tmp_path, name="gone.db", db=False)
    out = c.set_dashboard_kill_switch(True, OP, "emergency")
    assert out["persisted_to"] == "sentinel_file" and c.sentinel_path.is_file()
    assert (c.autonomy_dir / "audit_fallback.jsonl").read_text(encoding="utf-8").count("KILL_SWITCH_ACTIVATED") == 1
    with pytest.raises(Unavailable):
        c.set_dashboard_kill_switch(False, OP, "resume")


def test_kill_switch_during_execution_stops_retries(cp, monkeypatch):
    calls = []

    class Flaky(handlers.ReadOnlyDiagnosticHandler):
        def run(self, ctx, payload):
            calls.append(1)
            cp.sentinel_path.write_text("stop", encoding="utf-8")        # operator pulls the plug mid-run
            raise RuntimeError("boom")
    cp.handlers["diagnostic.readonly.run"] = Flaky()
    req = approved(cp)
    res = cp.execute(req["id"], "operator:abhi")
    assert res["status"] == FAILED and "emergency stop" in res["error"] and len(calls) == 1


# 5. Approval and rejection --------------------------------------------------------------------------------
def test_approve_reject_cancel(cp):
    a = proposal(cp)
    out = cp.approve(a["id"], OP, a["payload_hash"], "looks right")
    assert out["request"]["status"] == APPROVED and out["request"]["approved_by"] == OP and out["request"]["signed"]
    assert out["request"]["approval_mode"] == "EXPLICIT"
    b = proposal(cp, payload={"diagnostic": "audit_chain_verify"})
    assert cp.reject(b["id"], OP, "not now")["request"]["status"] == REJECTED
    assert cp.reject(b["id"], OP, "again")["unchanged"] is True
    with pytest.raises(Conflict):
        cp.approve(b["id"], OP, b["payload_hash"])
    c = proposal(cp, payload={"diagnostic": "ops_overview"})
    assert cp.cancel(c["id"], OP, "superseded")["request"]["status"] == CANCELLED
    assert cp.reject(a["id"], OP, "changed my mind")["request"]["status"] == REJECTED       # approved -> rejected
    assert cp.execute(a["id"], "operator:abhi")["executed"] is False
    for bad in ("autonomy-engine", "standing-approval:x", "System", "", "a" * 65, "x;drop"):
        d = proposal(cp, payload={"diagnostic": "ops_daily_summary"})
        with pytest.raises(PermissionDenied):
            cp.approve(d["id"], bad, d["payload_hash"])
        cp.cancel(d["id"], OP, "cleanup")
    with pytest.raises(cpm.ControlPlaneError):
        cp.reject(a["id"], OP, "")                                      # a reason is required
    with pytest.raises(cpm.NotFound):
        cp.approve("apr_0000000000000000", OP, "0" * 64)


def test_autonomy_cannot_change_its_own_policy(cp):
    for actor in ("autonomy-engine", "engine", "system", "control-plane"):
        with pytest.raises(PermissionDenied):
            cp.set_level("global", "OFF", actor, "self change")
        with pytest.raises(PermissionDenied):
            cp.set_dashboard_kill_switch(False, actor, "self change")
        with pytest.raises(PermissionDenied):
            cp.grant_standing_approval("operator.notify", actor, "self", max_per_day=1, valid_hours=1)


# 6. Expiry ------------------------------------------------------------------------------------------------
def test_expired_requests_cannot_be_approved_or_executed(tmp_path):
    clock = Clock()
    c = make_cp(tmp_path, clock=clock)
    set_all(c, "APPROVED_EXECUTION")
    pending = proposal(c)
    done = approved(c, payload={"diagnostic": "audit_chain_verify"})
    clock.advance(hours=25)                                             # diagnostic TTL is 24 h
    with pytest.raises(Conflict):
        c.approve(pending["id"], OP, pending["payload_hash"])
    res = c.execute(done["id"], "operator:abhi")
    assert res["executed"] is False
    assert c.get_request(pending["id"])["status"] == EXPIRED and c.get_request(done["id"])["status"] == EXPIRED
    assert events(c, done["id"])[-2:] == ["EXPIRED", "EXECUTION_BLOCKED"]
    assert c.list_executions() == []


# 7. Payload binding ---------------------------------------------------------------------------------------
def test_payload_change_invalidates_approval(cp):
    req = approved(cp, action="operator.notify", payload={"subject": "a", "message": "m"})
    out = cp.amend_payload(req["id"], {"subject": "b", "message": "m"}, "autonomy-engine", "subject fix")
    assert out["request"]["status"] == PENDING and out["request"]["approved_by"] is None and not out["request"]["signed"]
    assert "APPROVAL_INVALIDATED" in events(cp, req["id"])
    with pytest.raises(Conflict):                                       # the old hash no longer approves it
        cp.approve(req["id"], OP, req["payload_hash"])
    fresh = cp.get_request(req["id"])
    assert cp.approve(req["id"], OP, fresh["payload_hash"])["request"]["status"] == APPROVED


def test_payload_tampered_in_database_is_not_executed(cp):
    req = approved(cp, action="operator.notify", payload={"subject": "a", "message": "hello"})
    conn = sqlite3.connect(cp.db_path)
    conn.execute("UPDATE autonomy_approval_requests SET payload_json=? WHERE id=?",
                 (json.dumps({"subject": "a", "message": "something else"}), req["id"]))
    conn.commit()
    conn.close()
    res = cp.execute(req["id"], "operator:abhi")
    assert res["executed"] is False and "invalidated" in res["reason"]
    assert cp.get_request(req["id"])["status"] == PENDING
    assert not (cp.autonomy_dir / "operator_inbox.jsonl").exists()


def test_status_forged_in_database_is_not_a_valid_approval(cp):
    req = proposal(cp)
    conn = sqlite3.connect(cp.db_path)
    conn.execute("UPDATE autonomy_approval_requests SET status='APPROVED', approved_by='abhi', approval_mode='EXPLICIT', "
                 "approved_payload_hash=payload_hash, approval_signature=? WHERE id=?", ("f" * 64, req["id"]))
    conn.commit()
    conn.close()
    res = cp.execute(req["id"], "operator:abhi")
    assert res["executed"] is False and "signature" in res["reason"]


# 8. Duplicate approvals -----------------------------------------------------------------------------------
def test_duplicate_approvals_and_proposals(cp):
    req = proposal(cp)
    first = cp.approve(req["id"], OP, req["payload_hash"])
    second = cp.approve(req["id"], OP, req["payload_hash"])
    assert first["already_approved"] is False and second["already_approved"] is True
    assert events(cp, req["id"]).count("APPROVED") == 1
    with pytest.raises(Conflict):
        cp.approve(req["id"], "someone else", req["payload_hash"])
    again = cp.propose("diagnostic.readonly.run", {"diagnostic": "kill_switch_status"}, reason="again")
    assert again["duplicate"] is True and again["request"]["id"] == req["id"]
    conn = sqlite3.connect(cp.db_path)                                  # the database enforces it too
    row = conn.execute("SELECT * FROM autonomy_approval_requests WHERE id=?", (req["id"],)).fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO autonomy_approval_requests SELECT 'apr_ffffffffffffffff', " + ",".join(["?"] * (len(row) - 1)),
                     row[1:])
    conn.close()


# 9 / 10. Concurrency and atomic claiming -----------------------------------------------------------------
def test_concurrent_execution_attempts_run_once(cp):
    runs = []
    gate = threading.Event()

    class Slow(handlers.ReadOnlyDiagnosticHandler):
        def run(self, ctx, payload):
            runs.append(1)
            gate.wait(2)
            return {"diagnostic": payload["diagnostic"], "status": "ok", "data": {}}
    cp.handlers["diagnostic.readonly.run"] = Slow()
    req = approved(cp)
    results = []

    def go():
        results.append(cp.execute(req["id"], "operator:abhi"))
    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.5)
    gate.set()
    for t in threads:
        t.join(10)
    assert len(runs) == 1
    assert sum(1 for r in results if r["executed"]) == 1
    assert all(r["status"] == "BLOCKED" for r in results if not r["executed"])
    assert cp.get_request(req["id"])["status"] == COMPLETED
    assert events(cp, req["id"]).count("EXECUTION_STARTED") == 1


def test_atomic_claim_across_control_plane_instances(tmp_path):
    a = make_cp(tmp_path)
    set_all(a, "APPROVED_EXECUTION")
    b = ControlPlane(a.db_path, str(a.data_dir), environ={}, intelligence=a.intelligence, clock=a.clock)
    inner = {}

    class Reentrant(handlers.ReadOnlyDiagnosticHandler):
        def run(self, ctx, payload):
            inner["second"] = b.execute(ctx.request_id, "operator:other")     # a second process tries mid-run
            return {"diagnostic": payload["diagnostic"], "status": "ok", "data": {}}
    a.handlers["diagnostic.readonly.run"] = Reentrant()
    req = approved(a)
    assert a.execute(req["id"], "operator:abhi")["executed"] is True
    assert inner["second"]["executed"] is False and "EXECUTING" in inner["second"]["reason"]


# 11. Audit integrity ----------------------------------------------------------------------------------------
def test_audit_is_append_only_and_hash_chained(cp):
    req = approved(cp)
    cp.execute(req["id"], "operator:abhi")
    cp.set_dashboard_kill_switch(True, OP, "drill")
    cp.set_dashboard_kill_switch(False, OP, "drill over")
    chain = cp.verify_audit_chain()
    assert chain["ok"] and chain["events"] >= 10
    evs = cp.audit_events(limit=500)
    for e in evs:
        assert e["ts_utc"] and e["actor"] and e["event_type"] and "reason" in e and "previous_state" in e and "new_state" in e
    kinds = {e["event_type"] for e in evs}
    assert {"PERMISSION_CHANGED", "ACTION_PROPOSED", "APPROVED", "EXECUTION_STARTED", "EXECUTION_COMPLETED",
            "KILL_SWITCH_ACTIVATED", "KILL_SWITCH_DEACTIVATED"} <= kinds
    conn = sqlite3.connect(cp.db_path)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE autonomy_audit_events SET reason='edited' WHERE seq=1")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM autonomy_audit_events WHERE seq=2")
    conn.rollback()
    # Even bypassing the triggers, an edit is detected by the hash chain.
    conn.execute("DROP TRIGGER trg_autonomy_audit_no_update")
    conn.execute("UPDATE autonomy_audit_events SET reason='edited' WHERE seq=3")
    conn.commit()
    conn.close()
    bad = cp.verify_audit_chain()
    assert bad["ok"] is False and bad["first_bad_seq"] == 3


def test_audit_redacts_secrets(cp):
    cp.record_observation("diagnostics", "key AIzaSyA1234567890abcdefghijklmnopq leaked", {"api_key": "x", "note": "token=abc123"})
    e = cp.audit_events(limit=1)[0]
    blob = json.dumps(e)
    assert "AIzaSyA1234567890" not in blob and "abc123" not in blob and "[redacted]" in blob


# 12 / 13. API authorization and CSRF -----------------------------------------------------------------------
@pytest.fixture
def api(cp, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    monkeypatch.setattr(cpm, "get_control_plane", lambda: cp)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    token = local.get("/api/autonomy/session").json()["csrf_token"]
    return cp, main, local, token


def _h(token, **extra):
    return {"X-CrazzyDocs-CSRF": token, **extra}


def test_unauthorized_api_requests(api):
    cp, main, local, token = api
    from fastapi.testclient import TestClient
    remote = TestClient(main.app)
    for path in ("/api/autonomy/status", "/api/autonomy/permissions", "/api/autonomy/registry", "/api/autonomy/approvals",
                 "/api/autonomy/audit", "/api/autonomy/kill-switches", "/api/autonomy/session", "/api/autonomy/executions"):
        assert remote.get(path).status_code == 403, path
        assert local.get(path).status_code == 200, path
    body = {"reason": "x"}
    assert remote.post("/api/autonomy/kill-switch/activate", json=body, headers=_h(token)).status_code == 403
    assert local.post("/api/autonomy/kill-switch/activate", json=body).status_code == 403          # no CSRF token
    assert local.get("/api/autonomy/session", headers={"Origin": "http://evil.example"}).status_code == 403
    assert local.get("/api/autonomy/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert not cp.kill_switches()["switches"]["dashboard"]["active"]
    # No generic execution endpoint: no autonomy route accepts an action type or payload to run.
    routes = [r for r in main.app.routes if getattr(r, "path", "").startswith("/api/autonomy")]
    assert not any(re.search(r"/(run|execute|actions?)$", r.path) and "{request_id}" not in r.path
                   and "assessment" not in r.path for r in routes)
    exec_model = main.AutonomyExecuteRequest.model_fields
    assert set(exec_model) == {"confirm"}                     # no operator identity field (removed from the API)


def test_csrf_protection(api):
    cp, main, local, token = api
    url, body = "/api/autonomy/kill-switch/activate", {"reason": "drill"}
    assert local.post(url, json=body, headers=_h("wrong-token")).status_code == 403
    assert local.post(url, json=body, headers=_h(token, Origin="http://evil.example")).status_code == 403
    assert local.post(url, json=body, headers=_h(token, **{"Sec-Fetch-Site": "cross-site"})).status_code == 403
    assert local.post(url, json=body, headers=_h(token, **{"Sec-Fetch-Site": "same-site"})).status_code == 403
    r = local.post(url, content=json.dumps(body), headers=_h(token, **{"Content-Type": "text/plain"}))
    assert r.status_code in (415, 422)
    assert not cp.kill_switches()["switches"]["dashboard"]["active"]
    ok = local.post(url, json=body, headers=_h(token, Origin="http://127.0.0.1:8000", **{"Sec-Fetch-Site": "same-origin"}))
    assert ok.status_code == 200 and cp.kill_switches()["switches"]["dashboard"]["active"]
    assert local.post("/api/autonomy/kill-switch/deactivate", json=body, headers=_h(token)).status_code == 400
    assert local.post("/api/autonomy/kill-switch/deactivate", json=dict(body, confirm_text="RESUME AUTONOMY"),
                      headers=_h(token)).status_code == 200
    assert local.post(url, json=dict(body, extra="x"), headers=_h(token)).status_code == 422


def test_api_approval_flow_and_errors(api):
    cp, main, local, token = api
    req = proposal(cp, action="operator.notify", payload={"subject": "hi", "message": "there", "severity": "info"})
    base = f"/api/autonomy/approvals/{req['id']}"
    assert local.post(base + "/approve", json={"payload_hash": req["payload_hash"]}, headers=_h(token)).status_code == 400
    assert local.post(base + "/approve", json={"operator": "autonomy-engine", "payload_hash": req["payload_hash"], "confirm": True},
                      headers=_h(token)).status_code == 422          # no identity can be supplied (field removed)
    assert local.post(base + "/approve", json={"payload_hash": "0" * 64, "confirm": True},
                      headers=_h(token)).status_code == 409
    r = local.post(base + "/approve", json={"payload_hash": req["payload_hash"], "confirm": True}, headers=_h(token))
    assert r.status_code == 200 and r.json()["request"]["status"] == APPROVED
    r = local.post(base + "/execute", json={"confirm": True}, headers=_h(token))
    assert r.status_code == 200 and r.json()["executed"] is True
    assert local.get(base).json()["status"] == COMPLETED
    assert local.get("/api/autonomy/approvals?status=BOGUS").status_code == 400
    assert local.get("/api/autonomy/approvals/apr_nothere").status_code == 404
    lv = local.put("/api/autonomy/permissions/levels", json={"scope": "global", "level": "OBSERVE",
                                                          "reason": "lower", "confirm": True}, headers=_h(token))
    assert lv.status_code == 200 and lv.json()["global_level"] == "OBSERVE"
    sa = local.post("/api/autonomy/permissions/standing-approvals", json={"action_type": "community.draft.prepare",
                    "max_per_day": 1, "valid_hours": 1, "reason": "x", "confirm": True}, headers=_h(token))
    assert sa.status_code == 403                                      # explicit-each action: no standing approval
    pr = local.post("/api/autonomy/permissions/standing-approvals", json={"action_type": "youtube.video.publish",
                    "max_per_day": 1, "valid_hours": 1, "reason": "x", "confirm": True}, headers=_h(token))
    assert pr.status_code == 403
    audit = local.get("/api/autonomy/audit?verify=true").json()
    assert audit["chain"]["ok"] and audit["events"]
    reg = local.get("/api/autonomy/registry").json()
    assert {p["action_type"] for p in reg["prohibited"]} >= REQUIRED_PROHIBITIONS


def test_api_fails_closed_when_database_missing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    c = make_cp(tmp_path, name="absent.db", db=False)
    monkeypatch.setattr(cpm, "get_control_plane", lambda: c)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    st = local.get("/api/autonomy/status").json()
    assert st["database_available"] is False and st["kill_switches"]["any_active"] and not st["execution_permitted"]
    token = local.get("/api/autonomy/session").json()["csrf_token"]
    r = local.post("/api/autonomy/approvals/apr_0123456789abcdef/execute", json={"confirm": True}, headers=_h(token))
    assert r.status_code == 503 and "blocked" in r.json()["detail"]
    assert not Path(c.db_path).exists()


# 14. Dashboard integration -----------------------------------------------------------------------------------
def _dashboard_html():
    return (Path(cpm.__file__).resolve().parents[1] / "templates" / "dashboard.html").read_text(encoding="utf-8")


def test_dashboard_autonomy_section():
    html = _dashboard_html()
    for tab in ("overview", "schedule", "queue", "ops", "controls", "prodconfig", "processes", "autonomy"):
        assert f'data-tab="{tab}"' in html and f'id="tab-{tab}"' in html
    for el in ("pillAutonomy", "autoEnabled", "autoGlobal", "autoKill", "autoKillBody", "autoLevelsBody", "autoQueueBody",
               "autoActionsBody", "autoFailuresBody", "autoPermBody", "autoAuditBody", "autoStandingBody"):
        assert f'id="{el}"' in html, el
    assert 'id="autoOperator"' not in html                    # the Operator field was removed (owner request)
    js = "\n".join(re.findall(r"<script>([\s\S]*?)</script>", html))
    names = re.findall(r"^\s*(?:async\s+)?function\s+(\w+)", js, flags=re.M)
    assert len(names) == len(set(names)), "duplicate dashboard function names"
    for fn in ("refreshAutonomy", "refreshAutonomyPill", "autoWrite", "autoApprove", "autoKill", "refreshOps", "switchTab"):
        assert fn in names
    auto_js = js[js.index("// Stage 3.32B Autonomy Control Plane"):js.index("refreshAll();\nrefreshDeployment();")]
    assert "/api/autonomy/session" in auto_js and "[autoState.header]: autoState.csrf" in auto_js
    assert '"Content-Type": "application/json"' in auto_js
    assert "this.dataset.id" in auto_js and "onclick=\"autoApprove('" not in auto_js      # ids never inlined into JS
    assert 'if (tabId === "autonomy") { refreshAutonomy(true); }' in html
    assert "production" in html[html.index('id="tab-autonomy"'):html.index("TAB 6: PROCESS MONITOR")].lower()


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_dashboard_script_parses():
    js = "\n".join(re.findall(r"<script>([\s\S]*?)</script>", _dashboard_html()))
    res = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8')); console.log('ok')"],
                         input=js, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert res.returncode == 0 and res.stdout.strip() == "ok", res.stderr


# 15. Existing production functionality ----------------------------------------------------------------------
PRODUCTION_DDL = ("CREATE TABLE persistent_jobs (id TEXT PRIMARY KEY, status TEXT, current_stage TEXT)",
                  "CREATE TABLE studio_settings (key TEXT PRIMARY KEY, value TEXT)")


def test_production_records_untouched_and_no_production_calls(tmp_path, monkeypatch):
    db = tmp_path / "studio.db"
    conn = sqlite3.connect(db)
    for ddl in PRODUCTION_DDL:
        conn.execute(ddl)
    conn.execute("INSERT INTO persistent_jobs VALUES ('cycle_7_20261120_0600', 'RUNNING', 'STORY_DEVELOPMENT')")
    conn.execute("INSERT INTO studio_settings VALUES ('operating_mode', 'PRODUCTION')")
    conn.commit()
    snapshot = lambda: (conn.execute("SELECT * FROM persistent_jobs").fetchall(),
                        conn.execute("SELECT * FROM studio_settings").fetchall())
    before = snapshot()
    c = make_cp(tmp_path)
    set_all(c, "APPROVED_EXECUTION")
    c.grant_standing_approval("report.operational.generate", OP, "t", max_per_day=3, valid_hours=2)
    engine.run_assessment(c)
    for req in c.list_requests(status=PENDING):
        c.approve(req["id"], OP, req["payload_hash"])
        c.execute(req["id"], "operator:abhi")
    c.set_dashboard_kill_switch(True, OP, "drill")
    assert snapshot() == before
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables - {"persistent_jobs", "studio_settings", "sqlite_sequence"} == {
        "autonomy_config", "autonomy_approval_requests", "autonomy_audit_events", "autonomy_executions", "autonomy_execution_leases"}
    conn.close()
    import ast
    pkg = Path(cpm.__file__).parent
    imported = set()
    # Stage 3.36 deliberately adds production COORDINATION (the user-authorized Autopilot controller). Its four
    # modules call the existing scheduler entry points by design and have their own, stricter source-safety test
    # (test_autopilot_controller_336.py::test_production_modules_call_only_allowed_entry_points). Every other module
    # of the package keeps this 3.32B guarantee unchanged.
    stage_336 = {"production_controller.py", "production_handlers.py", "production_ownership.py", "decision_engine.py"}
    modules = [p for p in pkg.glob("*.py") if p.name not in stage_336]
    for p in modules:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
    allowed_app = {"backend.app.autonomy", "backend.app.autonomy.registry", "backend.app.autonomy.handlers",
                   "backend.app.autonomy.control_plane", "backend.app.core.paths", "backend.app.services.operational_intelligence",
                   # Stage 3.32D: handlers.py registers the self-healing handlers (same contract, same gates); that
                   # package has its own source-safety test in test_self_healing_332d.py.
                   "backend.app.autonomy.self_healing.handlers",
                   # Stage 3.32G: autopilot.py imports these lazily ONLY to read their status for the overview (and
                   # the recommendation engine to read one recommendation for the bridge). Each has its own
                   # source-safety tests (3.32C, 3.32D, 3.32E, 3.32F); none of them is executed from autonomy.
                   "backend.app.autonomy.recommendations.engine", "backend.app.autonomy.self_healing.engine",
                   "backend.app.community.service", "backend.app.community.publishing",
                   # Stage 3.32I/J: activation.py and reports.py import each other and read persisted state only:
                   # the 3.32H health-monitor ledger (file reads), the FFmpeg geometry self-check (pure function) and
                   # the 3.32A read-only intelligence. Their own tests: test_reliability_activation_reports_332hij.py.
                   "backend.app.autonomy.activation", "backend.app.autonomy.reports",
                   "backend.app.core.health_monitoring", "backend.app.rendering.ffmpeg_geometry",
                   # Stage 3.36: handlers.py registers the two production handlers (same contract and gates); they
                   # refuse to run outside the scheduler daemon's Autopilot controller. Own test: 336 suite.
                   "backend.app.autonomy.production_handlers"}
    assert {m for m in imported if m.startswith(("backend", "app"))} <= allowed_app
    assert not imported & {"subprocess", "requests", "urllib.request", "socket", "shutil", "httpx"}
    # registry.py only *names* the guarded production functions (as data); the executable modules never mention them.
    src = "\n".join(p.read_text(encoding="utf-8") for p in modules if p.name != "registry.py")
    for forbidden in ("run_cycle_generation", "request_retry", "upload_short", "set_production_control_state",
                      "request_daemon_restart", "control_center_service", "youtube_service", "youtube_publishing_engine",
                      "urlopen", "requests.", "subprocess", "topic_history", "creative_policy_config", "scheduler.",
                      "shutil.rmtree", "git "):
        assert forbidden not in src, forbidden


def test_community_draft_is_never_published(cp):
    req = approved(cp, action="community.draft.prepare", payload={"text": "Did you know? A new Short is up.", "topic": "parks"})
    res = cp.execute(req["id"], "operator:abhi")
    assert res["executed"]
    draft = json.loads(Path(cp.get_request(req["id"])["execution_result"]["result"]["path"]).read_text(encoding="utf-8"))
    assert draft["status"] == "DRAFT" and draft["publishable"] is False
    with pytest.raises(PermissionDenied):                              # explicit approval every time
        cp.grant_standing_approval("community.draft.prepare", OP, "x", max_per_day=1, valid_hours=1)


# Standing approvals, limits and the engine ---------------------------------------------------------------------
def test_standing_approval_execution_and_limits(tmp_path):
    clock = Clock()
    c = make_cp(tmp_path, clock=clock)
    set_all(c, "APPROVED_EXECUTION")
    c.grant_standing_approval("diagnostic.readonly.run", OP, "routine", max_per_day=2, valid_hours=1)
    r1 = c.submit("diagnostic.readonly.run", {"diagnostic": "ops_overview"}, reason="routine")
    assert r1["proposal"]["request"]["approval_mode"] == "STANDING" and r1["execution"]["executed"]
    r2 = c.submit("diagnostic.readonly.run", {"diagnostic": "audit_chain_verify"}, reason="routine")
    assert r2["execution"]["executed"]
    r3 = c.submit("diagnostic.readonly.run", {"diagnostic": "kill_switch_status"}, reason="routine")
    assert r3["execution"]["executed"] is False and "daily limit" in r3["execution"]["reason"]
    c.revoke_standing_approval("diagnostic.readonly.run", OP, "done")
    assert c.execute(r3["proposal"]["request"]["id"], "engine")["reason"].startswith("standing approval revoked")
    clock.advance(hours=2)
    c.grant_standing_approval("operator.notify", OP, "t", max_per_day=5, valid_hours=1)
    clock.advance(hours=2)                                             # grant expired
    r4 = c.submit("operator.notify", {"subject": "s", "message": "m"}, reason="t")
    assert r4["proposal"]["request"]["status"] == PENDING and r4["execution"] is None
    c.set_level("notifications", "RECOMMEND", OP, "t")                 # standing approval never applies below execution
    c.grant_standing_approval("operator.notify", OP, "t", max_per_day=5, valid_hours=1)
    r5 = c.submit("operator.notify", {"subject": "s2", "message": "m"}, reason="t")
    assert r5["proposal"]["request"]["status"] == PENDING


def test_engine_recommendations_collapse_duplicates(tmp_path):
    c = make_cp(tmp_path)
    set_all(c, "RECOMMEND")
    first = engine.run_assessment(c)
    second = engine.run_assessment(c)
    assert len(first["proposals"]) == 4 and all(not p["duplicate"] for p in first["proposals"])
    assert all(p["duplicate"] for p in second["proposals"])
    assert len(c.list_requests(status=PENDING)) == 4


# 16. Failure recovery -------------------------------------------------------------------------------------------
def test_bounded_retries_then_failure(cp):
    calls = []

    class Broken(handlers.ReadOnlyDiagnosticHandler):
        def run(self, ctx, payload):
            calls.append(1)
            raise RuntimeError("disk hiccup with key=SECRETVALUE123")
    cp.handlers["diagnostic.readonly.run"] = Broken()
    req = approved(cp)
    res = cp.execute(req["id"], "operator:abhi")
    spec = registry.REGISTRY["diagnostic.readonly.run"]
    assert res["status"] == FAILED and len(calls) == spec.retry_limit + 1
    assert "SECRETVALUE123" not in json.dumps(res) and "SECRETVALUE123" not in json.dumps(cp.list_executions())
    assert [e["outcome"] for e in cp.list_executions()] == ["FAILED"] * (spec.retry_limit + 1)
    assert cp.execute(req["id"], "operator:abhi")["executed"] is False          # a failed request never re-runs


def test_transient_failure_recovers_on_retry(cp):
    calls = []

    class OnceBroken(handlers.ReadOnlyDiagnosticHandler):
        def run(self, ctx, payload):
            calls.append(1)
            if len(calls) == 1:
                raise cpm.TransientActionError("busy")
            return super().run(ctx, payload)
    cp.handlers["diagnostic.readonly.run"] = OnceBroken()
    req = approved(cp)
    res = cp.execute(req["id"], "operator:abhi")
    assert res["status"] == COMPLETED and res["request"]["attempts"] == 2


def test_permanent_error_and_timeout_are_not_retried(cp, monkeypatch):
    calls = []

    class Perm(handlers.ReadOnlyDiagnosticHandler):
        def run(self, ctx, payload):
            calls.append(1)
            raise cpm.PermanentActionError("bad input")
    cp.handlers["diagnostic.readonly.run"] = Perm()
    assert cp.execute(approved(cp)["id"], "operator:abhi")["status"] == FAILED and len(calls) == 1

    class Hang(handlers.ReadOnlyDiagnosticHandler):
        def run(self, ctx, payload):
            calls.append(1)
            time.sleep(1.0)
            return super().run(ctx, payload)
    spec = registry.REGISTRY["diagnostic.readonly.run"]
    monkeypatch.setitem(registry.REGISTRY, "diagnostic.readonly.run", dataclasses.replace(spec, timeout_seconds=0.2))
    cp.handlers["diagnostic.readonly.run"] = Hang()
    calls.clear()
    res = cp.execute(approved(cp, payload={"diagnostic": "ops_overview"})["id"], "operator:abhi")
    assert res["status"] == FAILED and "timed out" in res["error"] and len(calls) == 1


def test_verification_failure_compensates(cp):
    class Liar(handlers.CommunityDraftHandler):
        def verify(self, ctx, payload, result):
            return False, {"check": "forced", "ok": False}
    cp.handlers["community.draft.prepare"] = Liar()
    req = approved(cp, action="community.draft.prepare", payload={"text": "hello"})
    res = cp.execute(req["id"], "operator:abhi")
    assert res["status"] == FAILED and res["error"] == "verification failed"
    final = cp.get_request(req["id"])
    assert final["execution_result"]["verification"]["compensation"]["removed"] is True
    assert not list((cp.autonomy_dir / "community_drafts").glob("*.json"))


def test_interrupted_execution_is_reconciled_not_rerun(tmp_path):
    clock = Clock()
    c = make_cp(tmp_path, clock=clock)
    set_all(c, "APPROVED_EXECUTION")
    req = approved(c)
    conn = sqlite3.connect(c.db_path)                                   # simulate a crash right after the claim
    conn.execute("UPDATE autonomy_approval_requests SET status='EXECUTING', claim_token='t', claimed_at_utc=? WHERE id=?",
                 (c._ts(clock()), req["id"]))
    conn.execute("INSERT INTO autonomy_executions (request_id, action_type, attempt, claim_token, started_at_utc, outcome, executed_by) "
                 "VALUES (?, 'diagnostic.readonly.run', 1, 't', ?, 'RUNNING', 'operator:abhi')", (req["id"], c._ts(clock())))
    conn.commit()
    conn.close()
    c.run_maintenance()
    assert c.get_request(req["id"])["status"] == EXECUTING              # still inside its time budget
    clock.advance(minutes=10)
    c.run_maintenance()
    final = c.get_request(req["id"])
    assert final["status"] == FAILED and final["execution_status"] == "INTERRUPTED"
    assert c.list_executions()[0]["outcome"] == "INTERRUPTED"
    assert c.execute(req["id"], "operator:abhi")["executed"] is False
    assert events(c, req["id"])[-2:] == ["EXECUTION_FAILED", "EXECUTION_BLOCKED"]


def test_no_network_during_control_plane_use(cp, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    for action, payload in (("report.operational.generate", {"date": "2026-11-20"}), ("operator.notify", {"subject": "a", "message": "b"}),
                            ("recommendation.record", {"title": "t", "text": "x"}), ("community.draft.prepare", {"text": "d"})):
        req = approved(cp, action=action, payload=payload)
        assert cp.execute(req["id"], "operator:abhi")["executed"], action
