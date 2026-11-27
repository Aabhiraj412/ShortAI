"""Operator-dependency reduction: routine local work runs without the operator; only genuine external authorizations,
human decisions, security boundaries and irreversible actions ask for them. Every gate that blocked before still
blocks (the execution gate is unchanged); what changed is what is reported, and one bounded local recovery."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.autonomy import registry
from backend.app.autonomy.control_plane import EMERGENCY_STOP, NORMAL, POLICY_AUTOMATIC, RESTRICTED
from backend.app.autonomy.self_healing import outcomes
from backend.app.services import operator_attention as oa
from test_autonomy_control_plane_332b import OP, make_cp, set_all

NOW = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)


def _diag(cp):
    return cp.submit("diagnostic.readonly.run", {"diagnostic": "kill_switch_status"}, reason="t",
                     proposed_by="autonomy-engine")


# -- 1. control-plane concepts are separate -----------------------------------------------------------------------

def test_missing_startup_config_is_not_an_emergency_stop(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    ks = cp.kill_switches()
    assert ks["emergency_stop"] is False and ks["any_active"] is False and ks["active"] == []
    assert "startup_config" not in ks["switches"]
    assert ks["startup_config"] == {"enabled": False, "emergency": False, "state": "ABSENT",
                                    "detail": ks["startup_config"]["detail"], "path": ks["startup_config"]["path"]}
    assert ks["control_state"] == RESTRICTED and ks["execution_gate_closed"] is True
    assert "not an emergency stop" in ks["execution_gate"]
    st = cp.status()
    assert st["control_state"] == RESTRICTED and st["execution_permitted"] is False
    set_all(cp, "OBSERVE")
    assert cp.status()["autonomy_enabled"] is True          # RESTRICTED still runs its policy: not "OFF"/"BLOCKED"


def test_startup_environment_off_is_an_emergency_stop(tmp_path):
    cp = make_cp(tmp_path, startup=True, policy=True, environ={"CRAZZYDOCS_AUTONOMY": "off"})
    ks = cp.kill_switches()
    assert ks["emergency_stop"] is True and ks["active"] == ["startup_environment"] and ks["control_state"] == EMERGENCY_STOP


@pytest.mark.parametrize("how", ["dashboard", "sentinel"])
def test_emergency_stop_overrides_every_autonomous_action(tmp_path, how):
    cp = make_cp(tmp_path, startup=True, policy=True)
    set_all(cp, "APPROVED_EXECUTION")
    if how == "dashboard":
        cp.set_dashboard_kill_switch(True, OP, "drill")
    else:
        cp.sentinel_path.write_text("stop", encoding="utf-8")
    res = _diag(cp)
    assert res["execution"] is None or res["execution"]["executed"] is False
    assert cp.control_state()["state"] == EMERGENCY_STOP and cp.control_state()["automatic_actions"] == {}


# -- 2. restricted policy: local allowlisted work runs, everything else stays gated ------------------------------------

def test_local_allowlisted_action_runs_without_operator_approval_in_restricted(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    res = _diag(cp)
    req = res["proposal"]["request"]
    assert req["approval_mode"] == "POLICY" and req["approved_by"] == "policy:RESTRICTED"
    assert res["execution"]["executed"] is True and cp.list_requests("PENDING", 10) == []


def test_bounded_staged_reacquisition_is_policy_automatic_in_restricted(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    ok, why = cp.policy_allows("selfheal.asset.reacquire_staged", cp._safe_config()[0], RESTRICTED)
    assert ok, why
    spec = registry.REGISTRY["selfheal.asset.reacquire_staged"]
    assert spec.daily_limit and spec.rollback and spec.verification and "Never placed into a job" in spec.description
    cp.set_level("self_healing", "OFF", OP, "off")                     # a category OFF still disables it
    assert not cp.policy_allows("selfheal.asset.reacquire_staged", cp._safe_config()[0], RESTRICTED)[0]


def test_unauthorized_and_external_actions_stay_blocked(tmp_path):
    for state in (RESTRICTED, NORMAL):
        pol = POLICY_AUTOMATIC[state]
        assert not any(a.startswith(("production.", "youtube.", "community.", "external.", "oauth.", "git.",
                                     "process.", "autonomy.")) for a in pol)
    for a in ("youtube.video.publish", "external.communication.send", "external.financial.action",
              "oauth.credentials.change", "production.data.delete", "autonomy.self_policy.change"):
        assert registry.REGISTRY[a].prohibited
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "APPROVED_EXECUTION")
    res = cp.submit("community.draft.prepare", {"text": "a draft"}, reason="t", proposed_by="autonomy-engine")
    assert res["proposal"]["request"]["status"] == "PENDING" and res["execution"] is None
    perms = {a["action_type"]: a for a in cp.permissions()["actions"]}
    assert perms["production.scheduled_cycle.dispatch"]["can_execute"] is False     # scheduler behaviour unchanged


# -- 3. attention: only genuine, current interventions ------------------------------------------------------------

def _runs(n, eligible, flags_on):
    import json
    fj = json.dumps({"production_query_rotation": "on" if flags_on else "off",
                     "exhaustion_recovery": "on" if flags_on else "off"})
    return [(eligible, "PRODUCED", "", fj) for _ in range(n)]


def test_resolved_topic_flag_decision_disappears_when_both_flags_are_on():
    on = {"production_query_rotation": True, "exhaustion_recovery": True}
    history = _runs(7, 12, True) + _runs(17, 1, False)        # low supply only before the activation
    d = oa.topic_supply_decision(history, on)
    assert not d.get("decision_needed") and d["status"] == "healthy" and d["runs"] == 7
    off = oa.topic_supply_decision(_runs(24, 1, False), {})
    assert off["decision_needed"] and "production_query_rotation" in off["action"]
    still_low = oa.topic_supply_decision(_runs(8, 0, True), on)
    assert still_low["decision_needed"] and "Decide on the implemented but inactive" not in still_low["action"]
    early = oa.topic_supply_decision(_runs(3, 0, True), on)
    assert not early.get("decision_needed") and "collecting evidence" in early["status"]


def test_credential_with_working_fallback_is_optional_not_a_request(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    prov = lambda: {"providers": [
        {"name": "Pexels", "state": "KEY_MISSING", "production": True, "env": "PEXELS_API_KEY", "fallback": "Pixabay"},
        {"name": "YouTube", "state": "AUTHENTICATION_REQUIRED", "production": True, "env": None, "fallback": None}]}
    a = oa.attention(cp=cp, providers=prov, creative=lambda: {"proposals": [
        {"id": "weak_hook", "requires_operator": True, "evidence": "e", "action": "x"}]},
        topic_supply=lambda: {}, localizations=lambda: {})
    titles = [n["title"] for n in a["needs_you"]]
    assert any("YouTube" in t for t in titles) and not any("Pexels" in t for t in titles)
    assert [o["title"] for o in a["optional_improvements"]] == ["Pexels: key missing"]
    assert [n["kind"] for n in a["needs_you"] if "weak_hook" in n["title"]] == ["creative_decision"]


def test_restricted_state_is_not_listed_as_an_intervention(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    a = oa.attention(cp=cp, providers=lambda: {}, creative=lambda: {}, topic_supply=lambda: {}, localizations=lambda: {})
    assert a["needs_you"] == [] and a["control_state"]["state"] == RESTRICTED


# -- 4. self-healing outcomes need evidence --------------------------------------------------------------------------

def _inc(last_seen, job="cycle_1_20261127_0100"):
    return {"id": "inc_000000000001", "job_id": job, "last_seen_utc": last_seen.isoformat()}


def test_diagnostic_success_is_not_a_production_recovery():
    diag = [{"action_type": outcomes.DIAG, "status": "DIAGNOSED", "verification_result": "VERIFIED"}]
    a = outcomes.assess(_inc(NOW - timedelta(minutes=5)), diag, "RUNNING", NOW)
    assert a["diagnostic_succeeded"] and not a["recovery_executed"] and not a["issue_resolved"]
    assert a["stage"] == "DIAGNOSED" and a["resolved_by"] is None and a["current"] is True


def test_recovery_counts_only_with_verified_evidence_and_no_recurrence():
    seen = NOW - timedelta(hours=1)
    unverified = [{"action_type": "selfheal.asset.reacquire_staged", "status": "FAILED", "verification_result": "UNVERIFIED",
                   "started_at_utc": seen.isoformat()}]
    a = outcomes.assess(_inc(seen), unverified, "FAILED", NOW)
    assert a["recovery_executed"] and not a["issue_resolved"] and a["stage"] == "ACTION_EXECUTED"
    verified_before_recurrence = [{"action_type": "selfheal.asset.reacquire_staged", "status": "RECOVERED",
                                   "verification_result": "VERIFIED", "finished_at_utc": (seen - timedelta(minutes=10)).isoformat()}]
    a = outcomes.assess(_inc(seen), verified_before_recurrence, None, NOW)
    assert not a["issue_resolved"] and any("recurrence" in e for e in a["evidence"])
    verified = [dict(verified_before_recurrence[0], finished_at_utc=(seen + timedelta(minutes=5)).isoformat())]
    a = outcomes.assess(_inc(seen), verified, None, NOW)
    assert a["issue_resolved"] and a["resolved_by"] == "self-healing recovery" and a["current"] is False


def test_output_completed_by_the_scheduler_is_not_credited_to_self_healing():
    a = outcomes.assess(_inc(NOW - timedelta(hours=1)), [], "COMPLETED", NOW)
    assert a["output_completed"] and not a["recovery_executed"] and a["resolved_by"].startswith("scheduler")
    assert a["stage"] == "OUTPUT_COMPLETED" and a["current"] is False


def test_historical_incidents_are_not_current_blockers():
    old = outcomes.assess(_inc(NOW - timedelta(days=1)), [], "RECOVERY_WINDOW_EXPIRED", NOW)
    assert old["current"] is False and "no occurrence" in old["history_reason"]
    live = outcomes.assess(_inc(NOW - timedelta(days=1)), [], "RUNNING", NOW)
    assert live["current"] is True
    s = outcomes.summarize([old, live])
    assert s["current"] == 1 and s["historical"] == 1


# -- 5. Autopilot heartbeat is real liveness evidence even when OFF ------------------------------------------------------

def test_controller_heartbeat_continues_while_autopilot_is_off(tmp_path):
    from test_autopilot_controller_336 import World, build
    ctrl = build(tmp_path, World(), level="OBSERVE", standing=False)
    assert ctrl.load()["state"] == "OFF"
    asyncio.run(ctrl.tick())
    st = ctrl.status(include_live=False)
    assert st["controller"]["responding"] is True and "idle" in st["controller"]["note"]
    assert st["ownership"]["owner"] == "SCHEDULER"                    # never claims production while OFF
    assert st["kill_switches"]["any_active"] is False


# -- 6. availability is reported with its window and every outage ----------------------------------------------------

def test_availability_summary_states_window_and_outages():
    from backend.app.core.health_monitoring import summarize_events
    t = lambda m: (NOW + timedelta(minutes=m)).isoformat()
    ev = [{"type": "probe", "at_utc": t(i), "live_ok": i not in (3, 4), "live_latency_ms": 5, "state": "HEALTHY",
           "live_reason": "timeout" if i in (3, 4) else "ok"} for i in range(10)]
    s = summarize_events(ev)
    assert s["window"] == {"start_utc": t(0), "end_utc": t(9)} and s["availability_pct"] == 80.0
    assert s["outage_count"] == 1 and s["outages"][0]["start_utc"] == t(3) and s["outages"][0]["recovered_at_utc"] == t(5)


def test_dashboard_presents_the_separate_concepts():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "Startup configuration (optional, not a kill switch)" in html and "EMERGENCY STOP" in html
    assert "Autonomy: BLOCKED (" not in html and "creative_decision" in html and "optional_improvements" in html
