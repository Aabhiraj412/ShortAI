"""Remediation section 4: one operator-attention feed; routine automatic work never asks the operator."""
from __future__ import annotations

from backend.app.services import operator_attention as oa
from test_autonomy_control_plane_332b import OP, make_cp, set_all


def _providers(state="HEALTHY"):
    return lambda: {"providers": [{"name": "Pexels", "state": state, "production": True, "env": "PEXELS_API_KEY",
                                   "fallback": "Pixabay", "state_reason": "credential missing"}]}


def _quiet():
    return {"proposals": []}


def test_routine_automatic_work_does_not_ask_the_operator(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)
    set_all(cp, "OBSERVE")
    cp.submit("diagnostic.readonly.run", {"diagnostic": "kill_switch_status"}, reason="t", proposed_by="autonomy-engine")
    a = oa.attention(cp=cp, providers=_providers(), creative=_quiet, topic_supply=lambda: {}, localizations=lambda: {})
    assert a["needs_you"] == []
    assert a["done_automatically"] and a["done_automatically"][0]["approval"] == "policy:RESTRICTED"
    assert a["control_state"]["state"] == "RESTRICTED"


def test_genuine_interventions_are_listed_once_each(tmp_path):
    cp = make_cp(tmp_path, startup=True, policy=True)
    set_all(cp, "RECOMMEND")
    for _ in range(3):   # duplicates collapse into one pending request
        cp.propose("community.draft.prepare", {"text": "a draft"}, reason="t", proposed_by="autonomy-engine")
    a = oa.attention(cp=cp, providers=_providers("KEY_MISSING"),
                     creative=lambda: {"proposals": [{"id": "weak_hook", "requires_operator": True,
                                                      "evidence": "hook weakest in 96%", "action": "run an experiment"},
                                                     {"id": "auto", "requires_operator": False}]},
                     topic_supply=lambda: {"decision_needed": True, "evidence": "9 of 24 runs", "action": "decide flags"},
                     localizations=lambda: {})
    kinds = sorted(n["kind"] for n in a["needs_you"])
    assert kinds == ["approval", "credential", "decision", "decision"]
    approval = [n for n in a["needs_you"] if n["kind"] == "approval"][0]
    assert approval["title"].startswith("1 action")


def test_emergency_stop_is_first(tmp_path):
    cp = make_cp(tmp_path, startup=True, policy=True)
    cp.set_dashboard_kill_switch(True, OP, "stop")
    a = oa.attention(cp=cp, providers=_providers("KEY_MISSING"), creative=_quiet, topic_supply=lambda: {}, localizations=lambda: {})
    assert a["needs_you"][0]["kind"] == "emergency_stop"


def test_unavailable_sources_never_hide_the_others(tmp_path):
    cp = make_cp(tmp_path, startup=False, policy=True)

    def boom():
        raise RuntimeError("down")

    a = oa.attention(cp=cp, providers=boom, creative=boom, topic_supply=boom, localizations=boom)
    assert len(a["unavailable"]) == 4 and a["control_state"]["state"] == "RESTRICTED"


def test_route_is_local_only():
    from fastapi.testclient import TestClient
    from backend.app import main
    remote = TestClient(main.app, base_url="http://example.com", client=("10.1.2.3", 5000))
    assert remote.get("/api/operations/attention").status_code in (400, 403)
