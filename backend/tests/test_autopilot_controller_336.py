"""Stage 3.36 Autopilot continuous production controller.

Temporary SQLite databases, a fixed clock, fake production probes and fake dispatchers only. Nothing here touches
the production database, the real scheduler, workers, rendering, YouTube or the network.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from backend.app.autonomy import decision_engine as de
from backend.app.autonomy import production_controller as pcm
from backend.app.autonomy import production_handlers as ph
from backend.app.autonomy import registry
from backend.app.autonomy.control_plane import PermanentActionError, PermissionDenied
from backend.app.autonomy.handlers import HANDLERS
from backend.app.autonomy.production_controller import (ADVISOR, BLOCKED, EXECUTOR, OBSERVER, OFF, ON,
                                                        RECOVERY_ACTION, SCHEDULED_ACTION, STARTING, STOPPING,
                                                        InvalidTransition, ProductionController)
from backend.app.autonomy.production_ownership import OWNER_AUTOPILOT, OWNER_SCHEDULER, OwnershipStore
from backend.app.autonomy.registry import ProhibitedActionError, UnknownActionError
from test_autonomy_control_plane_332b import OP, Clock, make_cp

ME = os.getpid()
OTHER = 999_991


class World:
    """Mutable fake production state the snapshot reads."""

    def __init__(self):
        self.active_jobs = []
        self.recovery = []
        self.terminal = []
        self.resources = {"safe": True, "cpu_used_percent": 20.0, "memory_used_percent": 40.0, "reason": "ok"}
        self.disk = 50.0
        self.quota = {"uploads_blocked": False}
        self.dispatching = []
        self.mode = "PRODUCTION"
        self.control = "RUNNING"
        self.statuses = {}
        self.scheduler_running = True
        self.dispatched = []
        self.replayed = []
        self.gate = None          # asyncio.Event to hold a dispatch "in flight"


class FakeScheduledHandler:
    """Same contract as the real handler (refuses outside the controller), without the wall-clock occurrence check."""

    def run(self, ctx, payload):
        ctrl, token = ph._controller_or_refuse(ctx, payload)
        res = ph._await_on(ctrl, ctrl._dispatch_scheduled(payload, token), timeout=30)
        return {"job_id": res.get("job_id"), "result_status": res.get("status")}

    def verify(self, ctx, payload, result):
        return True, {"ok": True}

    def compensate(self, ctx, payload, result):
        return {}


class FailingHandler(FakeScheduledHandler):
    def run(self, ctx, payload):
        raise RuntimeError("transient renderer crash")


def build(tmp_path, world, *, level="APPROVED_EXECUTION", standing=True, handler=None, pid=ME, alive=(ME,),
          clock=None, global_level=None):
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    handlers = dict(HANDLERS)
    handlers[SCHEDULED_ACTION] = handler or FakeScheduledHandler()
    cp = make_cp(tmp_path, clock=clock or Clock(), handlers_map=handlers)
    cp.set_level("global", global_level or level, OP, "test")
    cp.set_level("production", level, OP, "test")
    cp.set_level("notifications", "RECOMMEND" if level != "OBSERVE" else "OBSERVE", OP, "test")
    if standing:
        for a in (SCHEDULED_ACTION, RECOVERY_ACTION):
            cp.grant_standing_approval(a, OP, "test", max_per_day=24, valid_hours=24)
    alive_set = set(alive)

    async def dispatch_scheduled(payload, token):
        if world.gate is not None:
            await world.gate.wait()
        world.dispatched.append(("scheduled", payload["job_id"], token))
        world.statuses[payload["job_id"]] = "COMPLETED"
        return {"status": "COMPLETED", "job_id": payload["job_id"]}

    async def dispatch_recovery(payload, token):
        world.dispatched.append(("recovery", payload["job_id"], token))
        return {"status": "COMPLETED", "job_id": payload["job_id"], "dispatched": True}

    async def replay(items):
        world.replayed.extend(d["job_id"] for d in items)

    def snapshot(ctrl):
        own = ctrl.ownership.read()
        return {"now_utc": ctrl._now(), "operating_mode": world.mode, "production_control": world.control,
                "max_concurrency": 1, "active_jobs": list(world.active_jobs), "dispatching": list(world.dispatching),
                "deferred": own.get("deferred", []), "job_status": dict(world.statuses),
                "recovery_candidates": list(world.recovery), "already_dispatched": ctrl.already_dispatched(),
                "recent_terminal": list(world.terminal), "pending_handoffs": 0, "resources": dict(world.resources),
                "disk_free_gb": world.disk, "quota": dict(world.quota)}

    ctrl = ProductionController(
        cp, pid=pid, pid_alive=lambda p: p in alive_set, snapshot_fn=snapshot,
        scheduler_probe=lambda: {"running": world.scheduler_running, "cycles": 24},
        dispatch_scheduled=dispatch_scheduled, dispatch_recovery=dispatch_recovery, replay=replay,
        job_state=lambda jid: {"exists": jid in world.statuses, "status": world.statuses.get(jid), "topic": "t"})
    ctrl._alive_set = alive_set
    return ctrl


@pytest.fixture(autouse=True)
def _reset_runtime():
    pcm._RUNTIME = None
    yield
    pcm._RUNTIME = None


def run(coro):
    return asyncio.run(coro)


def deferred_entry(ctrl, job="cycle_3_20261120_1200", cycle="cycle_3", minutes=45):
    start = ctrl._now()
    return {"cycle_id": cycle, "job_id": job, "scheduled_start_ist": start.isoformat(),
            "recovery_deadline_ist": (start + timedelta(minutes=minutes)).isoformat(), "fired_at_ist": start.isoformat()}


def audit_types(ctrl):
    return [e["event_type"] for e in reversed(ctrl.cp.audit_events(limit=1000))]


# 1. Default OFF -------------------------------------------------------------------------------------------------
def test_default_is_off_and_scheduler_owns(tmp_path):
    ctrl = build(tmp_path, World())
    st = ctrl.status(include_live=False)
    assert st["state"] == OFF and st["role"] == "NONE"
    assert st["ownership"]["owner"] == OWNER_SCHEDULER
    assert st["controls"]["can_start"] and not st["controls"]["can_stop"]
    res = run(ctrl.tick())
    assert res == {"state": OFF}
    assert ctrl.ownership.read()["owner"] == OWNER_SCHEDULER


def test_production_category_defaults_off_and_activation_never_touches_it(tmp_path):
    cp = make_cp(tmp_path)
    assert cp.permissions()["effective_levels"]["production"] == "OFF"
    from backend.app.autonomy import activation
    assert "production" not in activation.PHASE_CATEGORIES
    for phase in activation.PHASES:
        assert "production" not in activation.phase_levels(phase, [])


# 2. State transitions ---------------------------------------------------------------------------------------------
def test_start_then_stop_cycle_through_states(tmp_path):
    ctrl = build(tmp_path, World(), level="OBSERVE", standing=False)
    assert ctrl.request_start(OP, "go")["state"] == STARTING
    run(ctrl.tick())
    st = ctrl.status(include_live=False)
    assert st["state"] == ON and st["role"] == OBSERVER
    assert ctrl.request_stop(OP, "done")["state"] == STOPPING
    run(ctrl.tick())
    assert ctrl.load()["state"] == OFF
    path = [(t["from_state"], t["to_state"]) for t in reversed(ctrl.status(include_live=False)["transitions"])]
    assert path == [(OFF, STARTING), (STARTING, ON), (ON, STOPPING), (STOPPING, OFF)]


# 3. Invalid transitions ------------------------------------------------------------------------------------------
def test_invalid_transitions_are_rejected_and_audited(tmp_path):
    ctrl = build(tmp_path, World(), level="OBSERVE", standing=False)
    with pytest.raises(InvalidTransition):
        ctrl.request_stop(OP, "nothing running")
    ctrl.request_start(OP, "go")
    with pytest.raises(InvalidTransition):
        ctrl.request_start(OP, "again")
    with ctrl._tx() as conn:
        row = ctrl._state_row(conn)
        row["state"] = OFF                         # pretend OFF and try to jump straight to ON
        with pytest.raises(InvalidTransition):
            ctrl._transition(conn, row, ON, "test", "skip STARTING")
    assert "AUTOPILOT_TRANSITION_REJECTED" in audit_types(ctrl)
    with pytest.raises(PermissionDenied):
        ctrl.request_start("autopilot-controller", "machine identity")   # reserved identity


# 4. Startup precondition failures ---------------------------------------------------------------------------------
def test_start_blocked_when_autonomy_mode_off(tmp_path):
    ctrl = build(tmp_path, World(), level="OFF", standing=False)
    ctrl.request_start(OP, "go")
    run(ctrl.tick())
    st = ctrl.load()
    assert st["state"] == BLOCKED and "autonomy_mode" in st["blocked_reason"]


def test_start_blocked_when_scheduler_not_running(tmp_path):
    w = World()
    w.scheduler_running = False
    ctrl = build(tmp_path, w, level="OBSERVE", standing=False)
    ctrl.request_start(OP, "go")
    run(ctrl.tick())
    assert ctrl.load()["state"] == BLOCKED and "scheduler_running" in ctrl.load()["blocked_reason"]


def test_start_blocked_with_two_active_workers(tmp_path):
    w = World()
    w.active_jobs = [{"job_id": "cycle_1_20261120_0000", "status": "RUNNING", "worker_alive": True},
                     {"job_id": "cycle_2_20261120_0100", "status": "RUNNING", "worker_alive": True}]
    ctrl = build(tmp_path, w, level="OBSERVE", standing=False)
    ctrl.request_start(OP, "go")
    run(ctrl.tick())
    assert ctrl.load()["state"] == BLOCKED and "single_worker" in ctrl.load()["blocked_reason"]


def test_start_blocked_by_competing_live_owner(tmp_path):
    ctrl = build(tmp_path, World(), alive=(ME, OTHER))
    other = OwnershipStore(ctrl.cp.db_path, clock=ctrl._now, pid_alive=lambda p: True, pid=OTHER)
    assert other.acquire()
    ctrl.request_start(OP, "go")
    run(ctrl.tick())
    assert ctrl.load()["state"] == BLOCKED and "no_competing_owner" in ctrl.load()["blocked_reason"]
    assert ctrl.ownership.read()["owner_pid"] == OTHER          # never taken over


# 5. Controller ownership -------------------------------------------------------------------------------------------
def test_executor_acquires_ownership_and_observer_does_not(tmp_path):
    ctrl = build(tmp_path / "a", World())
    ctrl.request_start(OP, "go")
    run(ctrl.tick())
    assert ctrl.load()["role"] == EXECUTOR
    eff = ctrl.ownership.effective()
    assert eff["owner"] == OWNER_AUTOPILOT and ctrl.ownership.token_valid(ctrl.owner_token)
    assert "AUTOPILOT_OWNERSHIP_ACQUIRED" in audit_types(ctrl)
    obs = build(tmp_path / "b", World(), level="OBSERVE", standing=False)
    obs.request_start(OP, "go")
    run(obs.tick())
    assert obs.load()["role"] == OBSERVER and obs.ownership.read()["owner"] == OWNER_SCHEDULER


def test_ownership_claim_is_compare_and_swap(tmp_path):
    ctrl = build(tmp_path, World())
    a = OwnershipStore(ctrl.cp.db_path, clock=ctrl._now, pid_alive=lambda p: True, pid=ME)
    b = OwnershipStore(ctrl.cp.db_path, clock=ctrl._now, pid_alive=lambda p: True, pid=OTHER)
    assert a.acquire()
    assert b.acquire() is None                                 # live owner: refused
    assert b.release(force_lapsed=True)["released"] is False   # cannot release a live owner's claim


def test_dead_owner_claim_lapses_to_scheduler(tmp_path):
    ctrl = build(tmp_path, World())
    dead = OwnershipStore(ctrl.cp.db_path, clock=ctrl._now, pid_alive=lambda p: True, pid=OTHER)
    assert dead.acquire()
    assert ctrl.ownership.effective()["owner"] == OWNER_SCHEDULER          # OTHER not alive for ctrl
    assert ctrl.ownership.autopilot_owns() is False


# 6. Scheduler handoff -------------------------------------------------------------------------------------------
def test_scheduler_cron_hands_fired_occurrence_to_owner(tmp_path, monkeypatch):
    from backend.app.autonomy import production_ownership
    from backend.app.scheduler import scheduler as sch
    from backend.app.scheduler.schedule_profiles import get_active_profile
    ctrl = build(tmp_path, World())
    store = OwnershipStore(ctrl.cp.db_path, pid=ME, pid_alive=lambda p: True)
    monkeypatch.setattr(production_ownership, "get_store", lambda: store)
    monkeypatch.setattr(sch, "record_lifecycle_event", lambda *a, **k: None)
    cycle_id = get_active_profile().cycles[0].cycle_id
    assert sch._autopilot_handoff(cycle_id, None, None) is None          # scheduler owns: dispatch normally
    token = store.acquire()
    res = sch._autopilot_handoff(cycle_id, None, None)
    assert res["status"] == "DEFERRED" and "Autopilot" in res["reason"]
    assert [d["cycle_id"] for d in store.read()["deferred"]] == [cycle_id]
    assert sch._autopilot_handoff(cycle_id, None, token) is None         # the controller's own dispatch proceeds
    assert sch._autopilot_handoff(cycle_id, None, "forged")["status"] == "DEFERRED"


def test_handoff_check_fails_safe_to_scheduler(tmp_path, monkeypatch):
    from backend.app.autonomy import production_ownership
    from backend.app.scheduler import scheduler as sch

    def broken():
        raise RuntimeError("db gone")
    monkeypatch.setattr(production_ownership, "get_store", broken)
    assert sch._autopilot_handoff("cycle_1", None, None) is None


def test_executor_dispatches_fired_occurrence_exactly_once(tmp_path):
    w = World()
    ctrl = build(tmp_path, w)

    async def scenario():
        ctrl.request_start(OP, "go")
        await ctrl.tick()
        ctrl.ownership.record_deferred(deferred_entry(ctrl))
        await ctrl.tick()
        assert ctrl.inflight() or w.dispatched
        await ctrl._inflight
        await ctrl.tick()
        await ctrl.tick()
    run(scenario())
    assert [d[:2] for d in w.dispatched] == [("scheduled", "cycle_3_20261120_1200")]
    assert ctrl.ownership.read()["deferred"] == []
    rows = ctrl.status(include_live=False)["dispatches"]
    assert rows[0]["outcome"] == "COMPLETED" and rows[0]["job_status"] == "COMPLETED"
    assert json.loads(ctrl.load()["last_success_json"])["job_id"] == "cycle_3_20261120_1200"


# 7. Safe shutdown -----------------------------------------------------------------------------------------------
def test_stop_waits_for_inflight_then_releases_and_replays(tmp_path):
    w = World()
    ctrl = build(tmp_path, w)

    async def scenario():
        w.gate = asyncio.Event()
        ctrl.request_start(OP, "go")
        await ctrl.tick()
        ctrl.ownership.record_deferred(deferred_entry(ctrl))
        await ctrl.tick()                                   # dispatch starts and blocks on the gate
        assert ctrl.inflight()
        ctrl.ownership.record_deferred(deferred_entry(ctrl, job="cycle_4_20261120_1300", cycle="cycle_4"))
        ctrl.request_stop(OP, "stop")
        await ctrl.tick()
        assert ctrl.load()["state"] == STOPPING               # in flight: no release, no new dispatch
        assert ctrl.ownership.read()["owner"] == OWNER_AUTOPILOT
        w.gate.set()
        await ctrl._inflight
        await ctrl.tick()
        if ctrl._replay_task:
            await ctrl._replay_task
    run(scenario())
    assert ctrl.load()["state"] == OFF
    assert ctrl.ownership.read()["owner"] == OWNER_SCHEDULER
    assert [d[1] for d in w.dispatched] == ["cycle_3_20261120_1200"]       # nothing new after stop
    assert w.replayed == ["cycle_4_20261120_1300"]                        # handed back to the scheduler path


# 8. Single-worker enforcement ------------------------------------------------------------------------------------
def test_decision_waits_while_a_job_is_running():
    s = {"active_jobs": [{"job_id": "cycle_1_x", "status": "RUNNING", "worker_alive": True, "stage": "RENDERING"}],
         "deferred": [{"job_id": "cycle_2_x", "cycle_id": "cycle_2", "recovery_deadline_ist": "2099-01-01T00:00:00+00:00"}]}
    d = de.decide(s)
    assert d["outcome"] == de.WAIT and not d["proceed"]
    assert "single renderer" in d["reasons"][0]
    assert any(c["job_id"] == "cycle_2_x" and not c["accepted"] for c in d["considered"])
    d2 = de.decide({"dispatching": ["cycle_1_x"], "deferred": s["deferred"]})
    assert d2["outcome"] == de.WAIT


def test_no_second_dispatch_while_inflight(tmp_path):
    w = World()
    ctrl = build(tmp_path, w)

    async def scenario():
        w.gate = asyncio.Event()
        ctrl.request_start(OP, "go")
        await ctrl.tick()
        ctrl.ownership.record_deferred(deferred_entry(ctrl))
        await ctrl.tick()
        first = ctrl._inflight
        ctrl.ownership.record_deferred(deferred_entry(ctrl, job="cycle_4_20261120_1300", cycle="cycle_4"))
        await ctrl.tick()
        assert ctrl._inflight is first                       # no new task while one is in flight
        w.gate.set()
        await first
    run(scenario())
    assert len(w.dispatched) == 1


# 9. Duplicate job prevention -------------------------------------------------------------------------------------
def test_already_dispatched_occurrence_is_rejected():
    d = de.decide({"deferred": [{"job_id": "cycle_2_x", "cycle_id": "cycle_2",
                                 "recovery_deadline_ist": "2099-01-01T00:00:00+00:00"}],
                   "already_dispatched": ["cycle_2_x"]})
    assert d["outcome"] == de.IDLE
    assert "never twice" in d["considered"][0]["reason"]


def test_scheduler_in_process_dispatch_guard(monkeypatch):
    from backend.app.scheduler import scheduler as sch
    from backend.app.scheduler.schedule_profiles import get_active_profile
    monkeypatch.setattr(sch, "_autopilot_handoff", lambda *a: None)
    calls = []

    async def slow(cycle_id, now=None):
        calls.append(cycle_id)
        await asyncio.sleep(0.05)
        return {"status": "COMPLETED"}
    monkeypatch.setattr(sch, "_run_cycle_generation", slow)
    cid = get_active_profile().cycles[0].cycle_id

    async def both():
        return await asyncio.gather(sch.run_cycle_generation(cid), sch.run_cycle_generation(cid))
    a, b = asyncio.run(both())
    assert sorted([a["status"], b["status"]]) == ["COMPLETED", "SKIPPED"]
    assert len(calls) == 1 and sch.dispatching_job_ids() == []


# 10. Controller restart recovery ----------------------------------------------------------------------------------
def test_restart_recovery_revalidates_and_reclaims(tmp_path):
    w = World()
    first = build(tmp_path, w, pid=OTHER, alive=(OTHER, ME))
    first.request_start(OP, "go")
    run(first.tick())
    assert first.load()["state"] == ON and first.ownership.read()["owner_pid"] == OTHER
    first.ownership.record_deferred(deferred_entry(first))
    pcm._RUNTIME = None
    # the old process died: a new controller (this pid) sees OTHER as dead
    second = ProductionController(first.cp, pid=ME, pid_alive=lambda p: p == ME, snapshot_fn=first._snapshot_fn,
                                  scheduler_probe=first._scheduler_probe, dispatch_scheduled=first._dispatch_scheduled,
                                  dispatch_recovery=first._dispatch_recovery, replay=first._replay,
                                  job_state=first._job_state)

    async def scenario():
        await second.tick()
        if second._replay_task:
            await second._replay_task
        await asyncio.sleep(0.05)
    run(scenario())
    states = [(t["from_state"], t["to_state"]) for t in reversed(second.status(include_live=False)["transitions"])]
    assert (ON, BLOCKED) in states and (BLOCKED, STARTING) in states and states[-1] == (STARTING, ON)
    own = second.ownership.read()
    assert own["owner"] == OWNER_AUTOPILOT and own["owner_pid"] == ME
    assert "AUTOPILOT_CONTROLLER_RECOVERED" in audit_types(second)


def test_restart_with_other_live_controller_blocks(tmp_path):
    w = World()
    first = build(tmp_path, w, pid=OTHER, alive=(OTHER, ME), level="OBSERVE", standing=False)
    first.request_start(OP, "go")
    run(first.tick())
    pcm._RUNTIME = None
    second = ProductionController(first.cp, pid=ME, pid_alive=lambda p: True, snapshot_fn=first._snapshot_fn,
                                  scheduler_probe=first._scheduler_probe)
    run(second.tick())
    assert second.load()["state"] == BLOCKED and "another controller" in second.load()["blocked_reason"]


# 11. Permission enforcement -------------------------------------------------------------------------------------
def test_lower_of_global_and_category_and_requirements_for_executor(tmp_path):
    a = build(tmp_path / "a", World(), level="APPROVED_EXECUTION", global_level="RECOMMEND")
    assert a.mode_and_role()["role"] == ADVISOR and a.mode_and_role()["mode"] == "RECOMMEND"
    b = build(tmp_path / "b", World(), standing=False)
    mr = b.mode_and_role()
    assert mr["role"] == ADVISOR and set(mr["missing_standing_approvals"]) == {SCHEDULED_ACTION, RECOVERY_ACTION}
    c = build(tmp_path / "c", World())
    assert c.mode_and_role()["role"] == EXECUTOR
    c.cp.set_dashboard_kill_switch(True, OP, "halt")
    assert c.mode_and_role()["role"] == ADVISOR


def test_advisor_never_dispatches(tmp_path):
    w = World()
    ctrl = build(tmp_path, w, level="RECOMMEND", standing=False)

    async def scenario():
        ctrl.request_start(OP, "go")
        await ctrl.tick()
        ctrl.ownership.ensure()
        await ctrl.tick()
    run(scenario())
    assert ctrl.load()["role"] == ADVISOR and w.dispatched == []
    assert ctrl.ownership.read()["owner"] == OWNER_SCHEDULER


def test_unknown_and_prohibited_actions_denied(tmp_path):
    ctrl = build(tmp_path, World())
    with pytest.raises(UnknownActionError):
        ctrl.cp.propose("production.render.anything", {}, reason="x", proposed_by="autopilot-controller")
    for a in ("youtube.video.publish", "youtube.community.publish", "production.schedule.change",
              "production.config.modify", "process.daemon.restart", "process.dashboard.restart",
              "production.data.delete", "git.destructive", "production.cycle.create", "render.worker.launch"):
        with pytest.raises(ProhibitedActionError):
            ctrl.cp.propose(a, {}, reason="x", proposed_by="autopilot-controller")


# 12. Exact-payload approval binding ------------------------------------------------------------------------------
def test_dispatch_approval_binds_to_exact_payload(tmp_path):
    ctrl = build(tmp_path, World())
    payload = {"cycle_id": "cycle_3", "job_id": "cycle_3_20261120_1200", "scheduled_start_ist": "x"}
    req = ctrl.cp.propose(SCHEDULED_ACTION, payload, reason="t", proposed_by="autopilot-controller")["request"]
    assert req["status"] == "APPROVED" and req["approval_mode"] == "STANDING"
    with ctrl.cp._tx() as conn:
        conn.execute("UPDATE autonomy_approval_requests SET payload_json=? WHERE id=?",
                     (json.dumps(dict(payload, job_id="cycle_4_20261120_1300")), req["id"]))
    res = ctrl.cp.execute(req["id"], "autopilot-controller")
    assert res["executed"] is False and "invalidated" in res["reason"]
    assert ctrl.cp.get_request(req["id"])["status"] == "PENDING"


# 13. Approval expiry --------------------------------------------------------------------------------------------
def test_dispatch_approval_expires_after_one_hour(tmp_path):
    clock = Clock()
    ctrl = build(tmp_path, World(), clock=clock)
    req = ctrl.cp.propose(SCHEDULED_ACTION, {"cycle_id": "cycle_3", "job_id": "cycle_3_20261120_1200",
                                             "scheduled_start_ist": "x"}, reason="t",
                          proposed_by="autopilot-controller")["request"]
    clock.advance(minutes=61)
    res = ctrl.cp.execute(req["id"], "autopilot-controller")
    assert res["executed"] is False
    assert ctrl.cp.get_request(req["id"])["status"] == "EXPIRED"


# 14. Retry limits -----------------------------------------------------------------------------------------------
def test_production_actions_never_retry_and_breaker_demotes_executor(tmp_path):
    for a in (SCHEDULED_ACTION, RECOVERY_ACTION):
        assert registry.REGISTRY[a].retry_limit == 0
    w = World()
    ctrl = build(tmp_path, w, handler=FailingHandler())

    async def scenario():
        ctrl.request_start(OP, "go")
        await ctrl.tick()
        for i in range(3):
            ctrl.ownership.record_deferred(deferred_entry(ctrl, job=f"cycle_{5 + i}_20261120_1{i}00", cycle=f"cycle_{5 + i}"))
            await ctrl.tick()
            if ctrl._inflight:
                await ctrl._inflight
    run(scenario())
    with ctrl.cp._read() as conn:
        attempts = conn.execute("SELECT request_id, COUNT(*) FROM autonomy_executions WHERE action_type=? "
                                "GROUP BY request_id", (SCHEDULED_ACTION,)).fetchall()
    assert attempts and all(n == 1 for _, n in attempts)          # one attempt per request: no retry storm
    mr = ctrl.mode_and_role()
    assert mr["role"] != EXECUTOR and SCHEDULED_ACTION in mr["open_breakers"]


# 15. Stalled-worker handling ------------------------------------------------------------------------------------
def test_old_heartbeat_alone_never_reclaims():
    quiet = {"job_id": "cycle_1_x", "status": "RUNNING", "worker_alive": False, "stalled": False}
    d = de.decide({"active_jobs": [quiet]})
    assert d["outcome"] == de.WAIT and any(b["id"].startswith("quiet_worker") for b in d["bottlenecks"])
    stalled = dict(quiet, stalled=True)
    d2 = de.decide({"active_jobs": [stalled],
                    "recovery_candidates": [{"job_id": "cycle_1_x", "status": "RUNNING", "due": True,
                                             "deadline_utc": "2099-01-01T00:00:00+00:00"}]})
    assert d2["outcome"] == de.DISPATCH_RECOVERY and d2["selected"]["job_id"] == "cycle_1_x"


def test_worker_registry_never_calls_live_worker_stalled():
    from datetime import datetime
    from backend.app.scheduler import worker_registry as wr

    class J:
        id, status = "cycle_1_x", "RUNNING"
        checkpoint_data_json = json.dumps({"cycle_state": {"worker_id": f"worker-{OTHER}-t1"}})
        heartbeat_at = datetime(2000, 1, 1)
    assert wr.is_stalled(J(), pid_alive=lambda p: True) is False
    assert wr.is_stalled(J(), pid_alive=lambda p: False) is True


# 16. Renderer failure handling -----------------------------------------------------------------------------------
def test_repeated_renderer_failures_recommend_and_escalate():
    term = [{"job_id": f"cycle_{i}_x", "status": "FAILED", "error": "FFmpeg exited with code 1"} for i in range(3)]
    d = de.decide({"recent_terminal": term})
    assert d["repeated_failures"]["consecutive_failures"] == 3
    assert any(r["id"] == "failure_class:RENDERER" for r in d["recommendations"])
    assert any(e["id"] == "repeated_failures" for e in d["escalations"])


def test_escalation_proposes_notification_once(tmp_path):
    w = World()
    w.terminal = [{"job_id": f"cycle_{i}_x", "status": "BLOCKED_QC", "error": "CHECK_A_TOPIC_NARRATION"} for i in range(3)]
    ctrl = build(tmp_path, w, level="RECOMMEND", standing=False)

    async def scenario():
        ctrl.request_start(OP, "go")
        await ctrl.tick()
        await ctrl.tick()
    run(scenario())
    notes = [r for r in ctrl.cp.list_requests("PENDING", 50) if r["action_type"] == "operator.notify"]
    assert len(notes) == 1 and notes[0]["proposed_by"] == "autopilot-controller"


# 17. Resource-limit handling -------------------------------------------------------------------------------------
def test_resource_pressure_holds_dispatch(tmp_path):
    w = World()
    w.resources = {"safe": False, "reason": "RAM usage 95.0% exceeds safe threshold 90.0%."}
    ctrl = build(tmp_path, w)

    async def scenario():
        ctrl.request_start(OP, "go")
        await ctrl.tick()
        ctrl.ownership.record_deferred(deferred_entry(ctrl))
        await ctrl.tick()
    run(scenario())
    assert w.dispatched == [] and not ctrl.inflight()
    assert ctrl.status(include_live=False)["recent_decisions"][0]["outcome"] == de.HOLD
    d = de.decide({"disk_free_gb": 1.2, "deferred": [deferred_entry(ctrl)]})
    assert d["outcome"] == de.HOLD and any(e["id"] == "disk_low" for e in d["escalations"])


# 18. API quota handling -----------------------------------------------------------------------------------------
def test_quota_exhaustion_is_reported_and_never_forces_retries():
    d = de.decide({"quota": {"uploads_blocked": True, "reason": "quotaExceeded"},
                   "deferred": [{"job_id": "cycle_2_x", "cycle_id": "cycle_2",
                                 "recovery_deadline_ist": "2099-01-01T00:00:00+00:00"}]})
    assert d["quota_constraints"][0]["id"] == "uploads_blocked"
    assert "no upload retry is forced" in d["quota_constraints"][0]["effect"]
    assert d["outcome"] == de.DISPATCH_SCHEDULED          # renders continue; upload waits in the existing queue


# 19. Community scheduler independence ----------------------------------------------------------------------------
def test_controller_code_never_touches_community_scheduling():
    for mod in (pcm, de, ph):
        src = inspect.getsource(mod)
        assert "backend.app.community" not in src and "community_draft" not in src, mod.__name__
        assert "scheduler_tick" not in src and "community_draft_tick" not in src, mod.__name__


def test_community_tick_runs_regardless_of_autopilot(tmp_path, monkeypatch):
    from backend.app.community import service as cs
    from backend.app.scheduler import scheduler as sch
    calls = []
    monkeypatch.setattr(cs, "scheduler_tick", lambda: calls.append(1) or {"status": "not_due"})
    ctrl = build(tmp_path, World())
    assert sch.run_community_draft_tick()["status"] == "not_due"
    ctrl.request_start(OP, "go")
    run(ctrl.tick())
    assert ctrl.load()["role"] == EXECUTOR
    assert sch.run_community_draft_tick()["status"] == "not_due"
    monkeypatch.setattr(cs, "scheduler_tick", lambda: (_ for _ in ()).throw(RuntimeError("community down")))
    assert sch.run_community_draft_tick()["status"] == "error"         # contained: never reaches production
    run(ctrl.tick())
    assert ctrl.load()["state"] == ON and len(calls) == 2


# 20. Prohibited external actions ------------------------------------------------------------------------------------
def test_real_production_handlers_refuse_outside_the_daemon():
    payload = {"cycle_id": "cycle_1", "job_id": "cycle_1_20261120_0000", "scheduled_start_ist": "x"}
    with pytest.raises(PermanentActionError):
        ph.ScheduledCycleDispatchHandler().run(None, payload)
    with pytest.raises(PermanentActionError):
        ph.RecoveryDispatchHandler().run(None, {"job_id": "cycle_1_20261120_0000", "status": "QUEUED"})


def test_dashboard_execute_endpoint_refuses_production_actions():
    src = Path(__file__).resolve().parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    i = src.index("async def api_autonomy_execute")
    assert 'startswith("production.")' in src[i:i + 1500]


def test_production_category_has_no_publishing_or_schedule_action():
    acts = [s.action_type for s in registry.PERMITTED if s.category == "production"]
    assert sorted(acts) == sorted([SCHEDULED_ACTION, RECOVERY_ACTION])
    for s in registry.PERMITTED:
        if s.category == "production":
            assert set(s.payload_fields) <= {"cycle_id", "job_id", "scheduled_start_ist", "status"}


# 21. Dashboard state accuracy ------------------------------------------------------------------------------------
def test_status_reports_pending_until_controller_confirms(tmp_path):
    ctrl = build(tmp_path, World())
    st = ctrl.request_start(OP, "go")
    assert st["state"] == STARTING and st["pending_confirmation"] is True
    assert st["role"] == "NONE" and st["ownership"]["owner"] == OWNER_SCHEDULER
    assert st["controller"]["responding"] is False
    run(ctrl.tick())
    st = ctrl.status(include_live=False)
    assert st["state"] == ON and st["pending_confirmation"] is False and st["role"] == EXECUTOR
    assert st["ownership"]["owner"] == OWNER_AUTOPILOT and st["controller"]["responding"] is True
    assert st["mode"] == "APPROVED_EXECUTION" and st["effective_levels"]["production"] == "APPROVED_EXECUTION"


# 22. Audit trail completeness ------------------------------------------------------------------------------------
def test_audit_trail_records_every_transition_and_stays_verified(tmp_path):
    w = World()
    ctrl = build(tmp_path, w)

    async def scenario():
        ctrl.request_start(OP, "go")
        await ctrl.tick()
        ctrl.ownership.record_deferred(deferred_entry(ctrl))
        await ctrl.tick()
        await ctrl._inflight
        ctrl.request_stop(OP, "stop")
        await ctrl.tick()
    run(scenario())
    types = audit_types(ctrl)
    assert types.count("AUTOPILOT_STATE_CHANGED") == 4
    for t in ("AUTOPILOT_OWNERSHIP_ACQUIRED", "AUTOPILOT_DECISION", "ACTION_PROPOSED", "APPROVED", "EXECUTION_STARTED",
              "EXECUTION_COMPLETED", "AUTOPILOT_OWNERSHIP_RELEASED"):
        assert t in types, t
    assert ctrl.cp.verify_audit_chain()["ok"] is True
    assert len(ctrl.status(include_live=False)["transitions"]) == 4


def test_production_modules_call_only_allowed_entry_points():
    """Stage 3.36 source safety: the controller reaches production ONLY through the existing scheduler entry points
    (run_cycle_generation / dispatch_eligible_recoveries / dispatching_job_ids / scheduler status) and read-only
    state. It never publishes, uploads, restarts, changes configuration or schedules, deletes, or runs commands."""
    import ast
    pkg = Path(pcm.__file__).parent
    files = [pkg / n for n in ("production_controller.py", "production_handlers.py", "production_ownership.py",
                               "decision_engine.py")]
    src = "\n".join(f.read_text(encoding="utf-8") for f in files)
    for forbidden in ("upload_short", "youtube_service", "youtube_publishing_engine", "set_production_control_state",
                      "request_production_", "request_daemon_restart", "deployment/restart", "activate_schedule",
                      "save_production_config", "set_publishing_policy", "set_max_concurrency", "reload_scheduler",
                      "configure_scheduler_jobs", "add_job", "remove_job", "subprocess", "os.system", "eval(", "exec(",
                      "rmtree", "os.remove", "unlink(", "topic_history", "creative_policy_config", "git reset", "git push",
                      "git clean", "git checkout", "urlopen",
                      "requests.", "httpx", "set_level(", "grant_standing_approval", "set_dashboard_kill_switch",
                      "complete_queue_handoff", "Stop-Process", "taskkill"):
        assert forbidden not in src, forbidden
    imported = set()
    for f in files:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("backend.app.scheduler"):
                imported |= {a.name for a in node.names}
    assert imported <= {"run_cycle_generation", "dispatch_eligible_recoveries", "dispatching_job_ids", "scheduler",
                        "get_active_profile", "IST_TZ", "retry_policy", "worker_registry"}, imported


def test_dashboard_controller_panel_only_requests_start_stop_and_levels():
    html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    start = html.index("// -- Stage 3.36 Autopilot controller:")
    script = html[start: html.index("async function refreshAutopilotController")]
    assert "/execute" not in script and "/approve" not in script and "standing-approvals" not in script
    assert "kill-switch" not in script
    urls = set(re.findall(r'"(/api/[^"]+)"', script))
    assert urls == {"/api/autopilot/controller/start", "/api/autopilot/controller/stop",
                    "/api/autonomy/permissions/levels"}, urls
    assert 'scope: "production"' in script
    assert "waiting for the scheduler daemon to verify" in html
