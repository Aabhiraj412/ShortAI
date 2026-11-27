"""Remediation WS2 (control states), WS5 (automatic recommendation follow-ups), WS6 (self-healing gating).

Temporary SQLite databases, fake intelligence, the real handlers and the existing 3.32B/3.32D fixtures with the
built-in automatic policy ENABLED. Nothing touches production, the scheduler, rendering, YouTube or the network.
"""
from __future__ import annotations

import json

import pytest

from backend.app.autonomy import control_plane as cpm
from backend.app.autonomy.autopilot import Autopilot
from backend.app.autonomy.control_plane import EMERGENCY_STOP, NORMAL, POLICY_AUTOMATIC, RESTRICTED, PermissionDenied
from test_autonomy_control_plane_332b import OP, make_cp, set_all


def _diag(cp, payload=None):
    return cp.submit("diagnostic.readonly.run", payload or {"diagnostic": "kill_switch_status"},
                     reason="test", proposed_by="autonomy-engine")


# -- the three control states -------------------------------------------------------------------------------------

def test_missing_startup_config_is_restricted_not_emergency(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    cs = cp.control_state()
    assert cs["state"] == RESTRICTED and cs["emergency_stop"] is False
    assert "startup config file absent" in cs["reason"]


def test_enabled_startup_config_is_normal(tmp_path):
    cp = make_cp(tmp_path, startup=True, policy=True)
    assert cp.control_state()["state"] == NORMAL


@pytest.mark.parametrize("how", ["dashboard", "sentinel", "env"])
def test_emergency_stop_overrides_everything(tmp_path, how):
    env = {"CRAZZYDOCS_AUTONOMY": "off"} if how == "env" else None
    cp = make_cp(tmp_path, startup=True, policy=True, environ=env)
    set_all(cp, "APPROVED_EXECUTION")
    if how == "dashboard":
        cp.set_dashboard_kill_switch(True, OP, "stop now")
    elif how == "sentinel":
        cp.sentinel_path.write_text("stop", encoding="utf-8")
    cs = cp.control_state()
    assert cs["state"] == EMERGENCY_STOP and cs["automatic_actions"] == {}
    res = _diag(cp)
    assert res["execution"] is None or res["execution"]["executed"] is False


def test_invalid_startup_config_fails_safe_to_restricted(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    (cp.data_dir / "autonomy_startup.json").write_text("{not json", encoding="utf-8")
    cp2 = cpm.ControlPlane(cp.db_path, str(cp.data_dir), environ={}, intelligence=cp.intelligence, clock=cp.clock)
    assert cp2.control_state()["state"] == RESTRICTED


def test_unreadable_configuration_is_an_emergency_stop(tmp_path):
    cp = make_cp(tmp_path, startup=True, policy=True, db=False, name="missing.db")
    assert cp.control_state()["state"] == EMERGENCY_STOP


def test_emergency_stop_can_be_activated_without_database(tmp_path):
    cp = make_cp(tmp_path, startup=True, policy=True, db=False, name="missing.db")
    out = cp.set_dashboard_kill_switch(True, OP, "db down, stop")
    assert out["persisted_to"] == "sentinel_file" and cp.sentinel_path.exists()


# -- policy-automatic execution -------------------------------------------------------------------------------------

def test_restricted_runs_read_only_diagnostic_automatically_at_observe(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    res = _diag(cp)
    req = res["proposal"]["request"]
    assert req["approval_mode"] == "POLICY" and req["approved_by"] == "policy:RESTRICTED"
    assert res["execution"]["executed"] is True                     # verified by the handler's own postcondition
    ev = [e["event_type"] for e in cp.audit_events(limit=50)]
    assert "APPROVED" in ev and cp.verify_audit_chain()["ok"]


def test_restricted_never_runs_actions_outside_its_policy(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "APPROVED_EXECUTION")
    for action in ("selfheal.asset.reacquire_staged", "production.recovery.dispatch", "community.draft.prepare"):
        assert action not in POLICY_AUTOMATIC[RESTRICTED]
    res = cp.submit("community.draft.prepare", {"text": "a draft"}, reason="t", proposed_by="autonomy-engine")
    assert res["proposal"]["request"]["status"] == "PENDING" and res["execution"] is None


def test_production_dispatch_is_never_automatic():
    for state in POLICY_AUTOMATIC:
        assert not any(a.startswith("production.") for a in POLICY_AUTOMATIC[state])
        assert not any(a.startswith(("youtube.", "community.")) for a in POLICY_AUTOMATIC[state])


def test_category_off_disables_policy(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    cp.set_level("diagnostics", "OFF", OP, "operator turned diagnostics off")
    with pytest.raises(PermissionDenied):
        _diag(cp)


def test_policy_approval_rechecked_at_execution(tmp_path):
    """A policy approval made before an emergency stop is refused at execution time."""
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    req = cp.propose("diagnostic.readonly.run", {"diagnostic": "kill_switch_status"}, reason="t",
                     proposed_by="autonomy-engine")["request"]
    assert req["status"] == "APPROVED"
    cp.set_dashboard_kill_switch(True, OP, "stop")
    res = cp.execute(req["id"], actor="autonomy-engine")
    assert res["executed"] is False and "emergency stop" in res["reason"]


def test_policy_duplicates_collapse(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    a = cp.propose("diagnostic.readonly.run", {"diagnostic": "kill_switch_status"}, reason="t", proposed_by="autonomy-engine")
    b = cp.propose("diagnostic.readonly.run", {"diagnostic": "kill_switch_status"}, reason="t", proposed_by="autonomy-engine")
    assert b["duplicate"] is True and a["request"]["id"] == b["request"]["id"]


# -- WS5: automatic recommendation follow-ups ---------------------------------------------------------------------

class FakeRecs:
    def __init__(self, recs):
        self.recs = recs

    def overview(self, status="open", limit=100):
        return {"recommendations": self.recs}


def _rec(i, rule="prod.failure_rate", severity="medium"):
    return {"id": f"rec_{i:016x}", "rule_id": rule, "status": "NEW", "severity": severity,
            "title": f"finding {i}", "problem": "something happened"}


def test_auto_bridge_executes_policy_follow_ups_once(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    ap = Autopilot(cp, recommendations=FakeRecs([_rec(1)]))
    out = ap.auto_bridge()
    assert out["control_state"] == RESTRICTED and out["executed"] >= 1, out
    kinds = {r["action_type"] for r in out["results"]}
    assert "operator.notify" not in kinds                      # medium severity: shown, not pushed
    again = ap.auto_bridge()
    assert again["executed"] == 0 and again["skipped_duplicate"] >= 1       # no loop
    assert ap.auto_bridge_history()


def test_auto_bridge_cooldown_across_recommendations(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    Autopilot(cp, recommendations=FakeRecs([_rec(1)])).auto_bridge()
    out = Autopilot(cp, recommendations=FakeRecs([_rec(2)])).auto_bridge()   # same rule -> same payloads
    assert out["executed"] == 0 and out["skipped_duplicate"] >= 1


def test_auto_bridge_notifies_only_for_high_severity(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    out = Autopilot(cp, recommendations=FakeRecs([_rec(3, severity="high")])).auto_bridge()
    assert "operator.notify" in {r["action_type"] for r in out["results"]}


def test_auto_bridge_blocked_by_emergency_stop(tmp_path):
    cp = make_cp(tmp_path, startup=True, policy=True)
    set_all(cp, "OBSERVE")
    cp.set_dashboard_kill_switch(True, OP, "stop")
    out = Autopilot(cp, recommendations=FakeRecs([_rec(4)])).auto_bridge()
    assert out["control_state"] == EMERGENCY_STOP and out["executed"] == 0 and "emergency stop" in out["reason"]


def test_auto_bridge_classifies_informational_and_approval_required(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    out = Autopilot(cp, recommendations=FakeRecs([_rec(5, rule="unmapped.rule")])).auto_bridge()
    assert out["informational"] == 1 and out["executed"] == 0


def test_auto_bridge_limit_per_cycle(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    recs = [_rec(i, rule=r) for i, r in enumerate(("prod.a", "recovery.b", "render.c", "api.d", "quality.e"), start=10)]
    out = Autopilot(cp, recommendations=FakeRecs(recs)).auto_bridge(limit=2)
    assert out["executed"] + out["blocked"] <= 2


def test_overview_reports_control_state_and_automatic_actions(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    ov = Autopilot(cp, recommendations=FakeRecs([])).overview()
    assert ov["control_state"]["state"] == RESTRICTED
    assert "diagnostic.readonly.run" in ov["categories"]["diagnostics"]["automatic_actions"]
    assert ov["categories"]["production"]["automatic_actions"] == []


def test_observation_cycle_runs_auto_actions_step(tmp_path):
    from backend.app.autonomy import activation
    src = open(activation.__file__, encoding="utf-8").read()
    assert '"auto_actions"' in src and "auto_bridge()" in src
