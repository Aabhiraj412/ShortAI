"""Stage 3.37 multi-pipeline production and resource coordinator.

Temporary SQLite files, a fixed clock, fake resource probes, a fake Shorts view and fake executors only. Nothing
here touches the production database, the real scheduler, workers, rendering, YouTube or the network.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.scheduler import production_coordinator as pc
from backend.app.scheduler.production_coordinator import (BENCHMARKED_MAX_HEAVY, CANCELLED, COMPLETED, FAILED, HEAVY,
                                                          LIGHT, LONG_FORM, QUEUED, RUNNING, SHORTS, CoordinatorError,
                                                          ProductionCoordinator)

ME = 4242
OTHER = 999_991
T0 = datetime(2026, 11, 27, 3, 20, tzinfo=timezone.utc)          # 08:50 IST


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class World:
    def __init__(self):
        self.resources = {"cpu_used_percent": 20.0, "memory_used_percent": 50.0, "memory_available_gb": 7.0,
                          "memory_total_gb": 13.9, "disk_free_gb": 140.0, "gpu_used_percent": 0.0,
                          "ffmpeg_processes": 0, "ffmpeg_pids": []}
        self.shorts = {"known": True, "active_jobs": [], "next_start_utc": pc._ts(T0 + timedelta(minutes=40)),
                       "cycles": 24, "profile": "CUSTOM", "in_process_workers": 0}
        self.alive = {ME}


def make(tmp_path, world=None, clock=None, pid=ME, executors=None, db_name="coord.db", **kw):
    db = tmp_path / db_name
    if not db.exists():
        sqlite3.connect(str(db)).close()
    world = world or World()
    clock = clock or Clock()
    c = ProductionCoordinator(str(db), clock=clock, pid=pid, pid_alive=lambda p: p in world.alive,
                              resource_probe=lambda: dict(world.resources),
                              shorts_view=lambda now: json.loads(json.dumps(world.shorts)),
                              executors=executors if executors is not None else {}, sleep=kw.pop("sleep", lambda s: None))
    c.ensure()
    return c, world, clock


def enable(c):
    c.set_config({"long_form_enabled": True}, "op", "test")


STEPS = [{"name": "script", "weight": LIGHT, "estimate_minutes": 5},
         {"name": "render", "weight": HEAVY, "estimate_minutes": 20}]


def run(coro):
    return asyncio.run(coro)


def wait_threads(c, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with c._lock:
            if not any(t.is_alive() for t in c._running.values()):
                return
        time.sleep(0.01)
    raise AssertionError("step thread did not finish")


# -- defaults and Autopilot compatibility -------------------------------------------------------------------------

def test_defaults_are_safe(tmp_path):
    c, _, _ = make(tmp_path)
    cfg = c.config()
    assert cfg["long_form_enabled"] is False
    assert cfg["max_heavy_concurrency"] == 1 == BENCHMARKED_MAX_HEAVY
    assert len(c.slots()) == BENCHMARKED_MAX_HEAVY
    assert pc._EXECUTORS == {}, "no long-form executor may be registered in production code"


def test_disabled_queue_never_runs(tmp_path):
    ran = []
    c, _, _ = make(tmp_path, executors={"lf": lambda ctx: ran.append(1) or {}})
    c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op")
    res = run(c.tick())
    assert res["started"] is None and ran == []
    assert "disabled" in res["plan"]["decisions"][0]["reason"]


def test_missing_executor_waits_with_reason(tmp_path):
    c, _, _ = make(tmp_path)
    enable(c)
    c.enqueue(kind="long_form_video", title="doc", steps=STEPS, requested_by="op")
    res = run(c.tick())
    assert res["started"] is None
    assert "no executor registered" in res["plan"]["decisions"][0]["reason"]


def test_coordinator_is_not_reachable_from_autopilot_registry():
    from backend.app.autonomy import registry
    src = inspect.getsource(registry)
    assert "production_coordinator" not in src and "long-form" not in src.lower()
    own = inspect.getsource(pc)
    for forbidden in ("youtube", "publish(", "autonomy_startup", "reset --hard", "os.remove", "unlink(", "rmtree"):
        assert forbidden not in own.lower(), forbidden


def test_heavy_concurrency_cannot_exceed_benchmark(tmp_path):
    c, _, _ = make(tmp_path)
    with pytest.raises(CoordinatorError, match="benchmark"):
        c.set_config({"max_heavy_concurrency": 2}, "op", "more")
    with pytest.raises(CoordinatorError):
        c.set_config({"unknown": 1}, "op", "x")
    with pytest.raises(CoordinatorError):
        c.set_config({"long_form_enabled": "yes"}, "op", "x")


# -- queue isolation ----------------------------------------------------------------------------------------------

def test_shorts_queue_cannot_be_enqueued_or_paused_here(tmp_path):
    c, _, _ = make(tmp_path)
    with pytest.raises(CoordinatorError, match="scheduler"):
        c.enqueue(kind="x", title="t", steps=STEPS, requested_by="op", queue=SHORTS)
    with pytest.raises(CoordinatorError, match="long-form"):
        c.set_queue_paused(SHORTS, True, "op", "r")


def test_long_form_rows_never_enter_persistent_jobs(tmp_path):
    c, _, _ = make(tmp_path)
    c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op")
    conn = sqlite3.connect(c.db_path)
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    conn.close()
    assert "persistent_jobs" not in tables      # the Shorts queue is untouched by long-form work


def test_pausing_long_form_does_not_affect_shorts_lease(tmp_path):
    c, _, _ = make(tmp_path)
    c.set_queue_paused(LONG_FORM, True, "op", "maintenance")
    with c.shorts_render_slot("cycle_1_20261127_0900") as info:
        assert info["outcome"] == "LEASED"
    assert all(s["token"] is None for s in c.slots())


# -- one heavyweight at a time and duplicate claims ----------------------------------------------------------------

def test_single_heavy_slot_cas(tmp_path):
    c, w, _ = make(tmp_path)
    c2, _, _ = make(tmp_path, world=w, pid=OTHER)
    w.alive.add(OTHER)
    a = c.acquire_slot(LONG_FORM, "lf1", "render")
    assert a and c2.acquire_slot(SHORTS, "s1") is None
    c.release_slot(a)
    assert c2.acquire_slot(SHORTS, "s1")


def test_concurrent_slot_claims_only_one_wins(tmp_path):
    c, w, _ = make(tmp_path)
    results = []
    barrier = threading.Barrier(6)

    def grab(i):
        barrier.wait()
        results.append(c.acquire_slot(LONG_FORM, f"j{i}"))

    ts = [threading.Thread(target=grab, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for r in results if r) == 1


def test_duplicate_job_claim_prevented(tmp_path):
    c, w, _ = make(tmp_path)
    c2, _, _ = make(tmp_path, world=w, pid=OTHER)
    job = c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op")["job"]
    t1 = c._claim(job)
    t2 = c2._claim(job)                         # same stale version: loses
    assert t1 and t2 is None
    assert c.get_job(job["id"])["owner_pid"] == ME


def test_duplicate_enqueue_is_idempotent(tmp_path):
    c, _, _ = make(tmp_path)
    a = c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op", job_id="lf_fixed")
    b = c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op", job_id="lf_fixed")
    assert a["duplicate"] is False and b["duplicate"] is True
    assert len(c.list_jobs()) == 1


def test_step_validation(tmp_path):
    c, _, _ = make(tmp_path)
    for bad in ([], [{"name": "x", "weight": "MEDIUM", "estimate_minutes": 3}],
                [{"name": "x", "weight": HEAVY, "estimate_minutes": 0}], "render"):
        with pytest.raises(CoordinatorError):
            c.enqueue(kind="lf", title="t", steps=bad, requested_by="op")


# -- resource admission ---------------------------------------------------------------------------------------------

def _admit(c, w, step, now=T0, light_running=0):
    return c.admit(step, now=now, resources=w.resources, shorts=w.shorts, light_running=light_running)


def test_heavy_admission_rules(tmp_path):
    c, w, _ = make(tmp_path)
    heavy = {"name": "render", "weight": HEAVY, "estimate_minutes": 20}
    assert _admit(c, w, heavy)[0]
    w.resources["cpu_used_percent"] = 80
    ok, why = _admit(c, w, heavy)
    assert not ok and "CPU" in why
    w.resources["cpu_used_percent"] = 20
    w.resources["memory_available_gb"] = 2.0
    ok, why = _admit(c, w, heavy)
    assert not ok and "RAM" in why
    w.resources["memory_available_gb"] = 7.0
    w.resources["disk_free_gb"] = 4.0
    ok, why = _admit(c, w, heavy)
    assert not ok and "disk" in why


def test_unmeasurable_resources_fail_closed(tmp_path):
    c, w, _ = make(tmp_path)
    w.resources["memory_available_gb"] = None
    assert not _admit(c, w, {"name": "s", "weight": LIGHT, "estimate_minutes": 1})[0]


def test_light_overlaps_shorts_but_heavy_does_not(tmp_path):
    c, w, _ = make(tmp_path)
    w.shorts["active_jobs"] = [{"job_id": "cycle_9_20261127_0900", "status": "RUNNING", "stage": "RENDER"}]
    w.resources["cpu_used_percent"] = 60
    assert _admit(c, w, {"name": "script", "weight": LIGHT, "estimate_minutes": 3})[0]
    ok, why = _admit(c, w, {"name": "render", "weight": HEAVY, "estimate_minutes": 5})
    assert not ok and "Shorts needs the renderer" in why
    w.resources["cpu_used_percent"] = 95
    assert not _admit(c, w, {"name": "script", "weight": LIGHT, "estimate_minutes": 3})[0]


def test_light_concurrency_limit(tmp_path):
    c, w, _ = make(tmp_path)
    ok, why = _admit(c, w, {"name": "s", "weight": LIGHT, "estimate_minutes": 1}, light_running=1)
    assert not ok and "light concurrency" in why


def test_heavy_must_end_before_next_shorts_cycle(tmp_path):
    c, w, _ = make(tmp_path)
    ok, why = _admit(c, w, {"name": "render", "weight": HEAVY, "estimate_minutes": 36})   # 36 + 5 guard > 40
    assert not ok and "next Shorts cycle" in why
    assert _admit(c, w, {"name": "render", "weight": HEAVY, "estimate_minutes": 34})[0]


def test_unknown_shorts_state_blocks_heavy(tmp_path):
    c, w, _ = make(tmp_path)
    w.shorts = {"known": False, "error": "persistent_jobs unreadable"}
    assert not _admit(c, w, {"name": "render", "weight": HEAVY, "estimate_minutes": 5})[0]


# -- Shorts lease: priority, preemption, fail-open -------------------------------------------------------------------

def test_shorts_preempts_long_form_holder(tmp_path):
    c, w, _ = make(tmp_path)
    lf_token = c.acquire_slot(LONG_FORM, "lf1", "render")
    calls = []

    def sleep(_s):          # the long-form step notices the yield request and releases at its safe point
        calls.append(1)
        holder = [s for s in c.slots() if s["token"] == lf_token]
        if holder and holder[0]["preempt_requested"]:
            c.release_slot(lf_token)

    c.sleep = sleep
    with c.shorts_render_slot("cycle_10_20261127_1000") as info:
        assert info["outcome"] == "LEASED" and calls
        assert c.slots()[0]["holder_queue"] == SHORTS
    assert c.slots()[0]["token"] is None
    events = [e["event"] for e in c.status()["events"]]
    assert "PREEMPT_REQUESTED" in events


def test_shorts_proceeds_after_bounded_wait(tmp_path):
    c, w, _ = make(tmp_path)
    c.set_config({"shorts_slot_wait_seconds": 0}, "op", "fast test")
    c.acquire_slot(LONG_FORM, "lf1", "render")
    ran = []
    with c.shorts_render_slot("cycle_11") as info:
        ran.append(1)
    assert ran == [1] and info["outcome"] == "PROCEEDED_WITHOUT_SLOT"
    assert "SHORTS_OVERLAP" in [e["event"] for e in c.status()["events"]]


def test_shorts_runs_when_coordinator_broken(tmp_path):
    c = ProductionCoordinator(str(tmp_path / "missing" / "nope.db"), pid=ME)
    ran = []
    with c.shorts_render_slot("cycle_12") as info:
        ran.append(1)
    assert ran == [1] and info["outcome"] == "COORDINATOR_UNAVAILABLE"


def test_scheduler_wrapper_fails_open(monkeypatch):
    from backend.app.scheduler import scheduler as sch
    import backend.app.scheduler.production_coordinator as mod

    def boom():
        raise RuntimeError("no coordinator")

    monkeypatch.setattr(mod, "get_coordinator", boom)
    with sch.shorts_render_slot("cycle_1") as info:
        assert info["outcome"] == "COORDINATOR_UNAVAILABLE"


def test_scheduler_worker_takes_the_lease_inside_worker_registration():
    from backend.app.scheduler import scheduler as sch
    src = inspect.getsource(sch._run_cycle_generation)
    reg = src.index("worker_registry.registry.register(job_id, worker_id)")
    lease = src.index("with shorts_render_slot(job_id):")
    run_ = src.index("return _run_registered_worker(worker_id, _json)")
    assert reg < lease < run_


def test_schedule_and_retry_code_paths_unchanged():
    """The 24-cycle cron registration and the retry policy are not touched by Stage 3.37."""
    from backend.app.scheduler import scheduler as sch
    src = inspect.getsource(sch.configure_scheduler_jobs)
    assert "production_coordinator" not in src
    from backend.app.scheduler import retry_policy
    assert "production_coordinator" not in inspect.getsource(retry_policy)


# -- execution, checkpoints, pause, cancel ---------------------------------------------------------------------------

def test_tick_runs_steps_in_order_with_checkpoints(tmp_path):
    seen = []

    def ex(ctx):
        seen.append(ctx.step["name"])
        ctx.checkpoint[ctx.step["name"]] = "done"
        ctx.heartbeat()
        return {}

    c, w, clock = make(tmp_path, executors={"lf": ex})
    enable(c)
    jid = c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op")["job"]["id"]
    r1 = run(c.tick())
    assert r1["started"]["weight"] == LIGHT
    wait_threads(c)
    assert c.get_job(jid)["state"] == QUEUED and c.get_job(jid)["next_step"] == 1
    r2 = run(c.tick())
    assert r2["started"]["weight"] == HEAVY
    wait_threads(c)
    job = c.get_job(jid)
    assert job["state"] == COMPLETED and seen == ["script", "render"]
    assert job["checkpoint"] == {"script": "done", "render": "done"}
    assert all(s["token"] is None for s in c.slots())


def test_heavy_step_yields_when_shorts_arrives(tmp_path):
    gate = threading.Event()

    def ex(ctx):
        gate.wait(5)
        if ctx.should_yield():
            ctx.checkpoint["frames"] = 120
            return {"yielded": True}
        return {}

    c, w, _ = make(tmp_path, executors={"lf": ex})
    enable(c)
    jid = c.enqueue(kind="lf", title="doc", steps=[STEPS[1]], requested_by="op")["job"]["id"]
    assert run(c.tick())["started"]["weight"] == HEAVY
    w.shorts["active_jobs"] = [{"job_id": "cycle_13", "status": "RUNNING", "stage": "TOPIC"}]
    gate.set()
    wait_threads(c)
    job = c.get_job(jid)
    assert job["state"] == QUEUED and job["next_step"] == 0 and job["checkpoint"] == {"frames": 120}
    assert c.slots()[0]["token"] is None


def test_safe_pause_and_resume(tmp_path):
    gate = threading.Event()

    def ex(ctx):
        gate.wait(5)
        return {"yielded": True} if ctx.should_yield() else {}

    c, w, _ = make(tmp_path, executors={"lf": ex})
    enable(c)
    jid = c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op")["job"]["id"]
    run(c.tick())
    c.set_queue_paused(LONG_FORM, True, "op", "laptop needed")
    assert c.get_job(jid)["state"] == RUNNING          # pause never kills the running step
    gate.set()
    wait_threads(c)
    assert c.get_job(jid)["state"] == QUEUED
    res = run(c.tick())
    assert res["started"] is None and "paused" in res["plan"]["decisions"][0]["reason"]
    c.set_queue_paused(LONG_FORM, False, "op", "done")
    assert run(c.tick())["started"] is not None
    wait_threads(c)


def test_cancel_queued_and_running(tmp_path):
    gate = threading.Event()

    def ex(ctx):
        gate.wait(5)
        return {"yielded": True} if ctx.should_yield() else {}

    c, w, _ = make(tmp_path, executors={"lf": ex})
    enable(c)
    a = c.enqueue(kind="lf", title="a", steps=STEPS, requested_by="op", priority=90)["job"]["id"]
    b = c.enqueue(kind="lf", title="b", steps=STEPS, requested_by="op", priority=10)["job"]["id"]
    assert c.cancel(b, "op", "not needed")["job"]["state"] == CANCELLED
    run(c.tick())
    c.cancel(a, "op", "stop")
    gate.set()
    wait_threads(c)
    assert c.get_job(a)["state"] == CANCELLED
    assert len(c.list_jobs()) == 2                     # cancelled, never deleted


def test_failed_step_retries_with_backoff_then_fails(tmp_path):
    def ex(ctx):
        raise ValueError("boom")

    c, w, clock = make(tmp_path, executors={"lf": ex})
    enable(c)
    jid = c.enqueue(kind="lf", title="doc", steps=[STEPS[0]], requested_by="op")["job"]["id"]
    for i in range(3):
        res = run(c.tick())
        assert res["started"] is not None, res["plan"]["decisions"]
        wait_threads(c)
        if i < 2:
            assert c.get_job(jid)["state"] == QUEUED
            assert "backoff" in run(c.tick())["plan"]["decisions"][0]["reason"]
            clock.advance(minutes=11)
    job = c.get_job(jid)
    assert job["state"] == FAILED and "boom" in job["error"]


# -- recovery ----------------------------------------------------------------------------------------------------------

def test_recovery_requeues_job_of_dead_process_and_reclaims_slot(tmp_path):
    c, w, _ = make(tmp_path)
    other, _, _ = make(tmp_path, world=w, pid=OTHER)
    w.alive.add(OTHER)
    job = c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op")["job"]
    assert other._claim(job)
    other.acquire_slot(LONG_FORM, job["id"], "render")
    assert c.recover() == []                           # live owner: untouched
    w.alive.discard(OTHER)                             # owner process died
    acts = {a["action"] for a in c.recover()}
    assert acts == {"SLOT_RECLAIMED", "JOB_RECOVERED"}
    j = c.get_job(job["id"])
    assert j["state"] == QUEUED and j["attempts"] == 1 and j["next_step"] == 0
    assert c.slots()[0]["token"] is None


def test_recovery_marks_failed_after_max_attempts(tmp_path):
    c, w, _ = make(tmp_path)
    other, _, _ = make(tmp_path, world=w, pid=OTHER)
    jid = c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op")["job"]["id"]
    for _ in range(3):
        assert other._claim(c.get_job(jid))
        c.recover()
    assert c.get_job(jid)["state"] == FAILED


def test_recovery_releases_own_orphan_slot_but_not_held_one(tmp_path):
    c, w, _ = make(tmp_path)
    tok = c.acquire_slot(SHORTS, "cycle_1")
    assert c.recover() == []                           # held by this process: kept
    with c._lock:
        c._held_tokens.clear()                         # e.g. thread died without its finally
    assert [a["action"] for a in c.recover()] == ["SLOT_RECLAIMED"]
    c.release_slot(tok)


def test_state_survives_new_coordinator_instance(tmp_path):
    c, w, _ = make(tmp_path)
    enable(c)
    c.set_queue_paused(LONG_FORM, True, "op", "r")
    jid = c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op")["job"]["id"]
    c2, _, _ = make(tmp_path, world=w)
    assert c2.config()["long_form_enabled"] is True
    assert c2.status()["queues"][LONG_FORM]["paused"] is True
    assert c2.get_job(jid)["state"] == QUEUED


# -- starvation prevention -------------------------------------------------------------------------------------------

def test_aging_lets_old_low_priority_job_overtake(tmp_path):
    c, w, clock = make(tmp_path, executors={"lf": lambda ctx: {}})
    enable(c)
    old = c.enqueue(kind="lf", title="old", steps=[STEPS[0]], requested_by="op", priority=10)["job"]["id"]
    clock.advance(hours=24)                            # 24 h * 2 points/h = +48
    new = c.enqueue(kind="lf", title="new", steps=[STEPS[0]], requested_by="op", priority=50)["job"]["id"]
    p = c.plan()
    assert p["selected"] == old
    d = {x["job_id"]: x for x in p["decisions"]}
    assert d[old]["starving"] is True and d[new]["starving"] is False


def test_long_form_gets_gap_after_shorts_finishes(tmp_path):
    """A Shorts cycle takes about 12 of every 60 minutes; once it ends, a heavy step that fits is admitted."""
    c, w, clock = make(tmp_path, executors={"lf": lambda ctx: {}})
    enable(c)
    jid = c.enqueue(kind="lf", title="doc", steps=[STEPS[1]], requested_by="op")["job"]["id"]
    w.shorts["active_jobs"] = [{"job_id": "cycle_10", "status": "RUNNING", "stage": "RENDER"}]
    w.shorts["next_start_utc"] = pc._ts(T0 + timedelta(minutes=60))
    assert run(c.tick())["started"] is None
    clock.advance(minutes=12)
    w.shorts["active_jobs"] = []
    assert run(c.tick())["started"]["job_id"] == jid
    wait_threads(c)


# -- dashboard / API ----------------------------------------------------------------------------------------------------

def test_status_shape(tmp_path):
    c, w, _ = make(tmp_path)
    s = c.status()
    assert set(s["queues"]) == {SHORTS, LONG_FORM, pc.LOCALIZATION}   # Stage 4.02 added the localization queue
    assert s["queues"][SHORTS]["cycles"] == 24
    assert {"logical_jobs", "worker_threads", "os_processes"} <= set(s["execution_model"])
    assert s["renderer"]["heavy_slots"] == 1 and "gpu_used_percent" in s["resources"]
    assert "owner_token" not in json.dumps(s["queues"][LONG_FORM]["jobs"])


def test_daemon_tick_snapshot_feeds_status(tmp_path):
    c, w, _ = make(tmp_path)
    run(c.tick())
    s = c.status()
    assert s["recovery"]["daemon_responding"] is True
    assert s["execution_model"]["os_processes"]["daemon_pid"] == ME


def test_daemon_loop_calls_coordinator_after_autopilot():
    from backend.app.scheduler import daemon_runtime
    src = inspect.getsource(daemon_runtime.periodic_recovery_and_sleep_loop)
    assert src.index("autopilot_controller_tick()") < src.index("production_coordinator_tick()")


def test_dashboard_panel_only_calls_queue_endpoints():
    html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    start = html.index("// -- Stage 3.37 production queues")
    script = html[start: html.index("// -- Stage 3.32H/I/J operations: health", start)]
    urls = set(re.findall(r'["`](/api/[^"`$]+)', script))
    assert urls <= {"/api/production/queues", "/api/production/queues/long-form/"}, urls
    assert 'id="pqCard"' in html


def test_api_routes_require_local_and_csrf(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    c, w, _ = make(tmp_path)
    monkeypatch.setattr(main, "_coordinator", lambda: c)
    client = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r = client.get("/api/production/queues")
    assert r.status_code == 200 and set(r.json()["queues"]) == {SHORTS, LONG_FORM, pc.LOCALIZATION}   # Stage 4.02 added the localization queue
    body = {"reason": "r", "confirm": True}
    assert client.post("/api/production/queues/long-form/pause", json=body).status_code == 403   # no CSRF token
    tok = client.get("/api/autonomy/session", headers={"origin": "http://127.0.0.1:8000"}).json()
    h = {tok["csrf_header"]: tok["csrf_token"], "origin": "http://127.0.0.1:8000"}
    assert client.post("/api/production/queues/long-form/pause", json={**body, "confirm": False}, headers=h).status_code == 400
    assert client.post("/api/production/queues/long-form/pause", json=body, headers=h).json()["paused"] is True
    r = client.post("/api/production/queues/long-form/jobs", headers=h,
                    json={"kind": "lf", "title": "doc", "confirm": True,
                          "steps": [{"name": "render", "weight": "HEAVY", "estimate_minutes": 20}]})
    assert r.status_code == 200 and r.json()["job"]["state"] == QUEUED
    r = client.put("/api/production/queues/config", headers=h,
                   json={"reason": "r", "confirm": True, "changes": {"max_heavy_concurrency": 2}})
    assert r.status_code == 400


def test_idle_tick_does_not_sample_host(tmp_path):
    calls = []
    c, w, _ = make(tmp_path)
    c.resource_probe = lambda: calls.append(1) or dict(w.resources)
    run(c.tick())
    assert calls == []                                 # default (disabled, empty queue): no sampling in the daemon
    enable(c)
    run(c.tick())
    assert calls == [1]


def test_daemon_coordinator_tick_is_throttled(monkeypatch):
    from backend.app.scheduler import daemon_runtime as dr
    import backend.app.scheduler.production_coordinator as mod
    ticks = []

    class Fake:
        async def tick(self):
            ticks.append(1)

    monkeypatch.setattr(mod, "get_coordinator", lambda: Fake())
    monkeypatch.setattr(dr, "_last_coordinator_tick", [float("-inf")])
    monkeypatch.setattr(dr, "_coordinator_task", [None])

    async def go():
        for _ in range(5):
            await dr.production_coordinator_tick()
        await dr._coordinator_task[0]

    asyncio.run(go())
    assert ticks == [1]


def test_daemon_coordinator_tick_never_blocks_the_recovery_loop(monkeypatch):
    """Remediation: the recovery loop must not wait for a slow coordinator tick (restart/recovery checks first)."""
    from backend.app.scheduler import daemon_runtime as dr
    import backend.app.scheduler.production_coordinator as mod

    class Slow:
        async def tick(self):
            await asyncio.sleep(1.0)

    monkeypatch.setattr(mod, "get_coordinator", lambda: Slow())
    monkeypatch.setattr(dr, "_last_coordinator_tick", [float("-inf")])
    monkeypatch.setattr(dr, "_coordinator_task", [None])

    async def go():
        t0 = time.monotonic()
        await dr.production_coordinator_tick()
        took = time.monotonic() - t0
        dr._coordinator_task[0].cancel()
        return took

    assert asyncio.run(go()) < 0.1


def test_first_coordinator_tick_waits_for_daemon_warm_up(monkeypatch):
    """The first tick after start is deferred, so its one-time setup never competes with the first recovery-loop
    iterations (root cause of the intermittent test_daemon_does_not_exit_while_render_in_progress failure)."""
    from backend.app.scheduler import daemon_runtime as dr
    import backend.app.scheduler.production_coordinator as mod
    ticks = []

    class Fake:
        async def tick(self):
            ticks.append(1)

    monkeypatch.setattr(mod, "get_coordinator", lambda: Fake())
    monkeypatch.setattr(dr, "_last_coordinator_tick", [None])
    monkeypatch.setattr(dr, "_coordinator_task", [None])
    clock = [1000.0]
    monkeypatch.setattr(dr.time, "monotonic", lambda: clock[0])

    async def go():
        await dr.production_coordinator_tick()          # daemon start: nothing runs yet
        clock[0] += dr.COORDINATOR_FIRST_DELAY_SECONDS - 1
        await dr.production_coordinator_tick()
        assert ticks == []
        clock[0] += 2                                    # warm-up elapsed: the first tick runs
        await dr.production_coordinator_tick()
        await dr._coordinator_task[0]

    asyncio.run(go())
    assert ticks == [1]


def test_heavy_admission_fails_closed_without_next_shorts_start(tmp_path):
    """Stage 4.02 fix: an unknown next Shorts start used to skip the window check and admit a heavy step."""
    c, w, _ = make(tmp_path)
    w.shorts["next_start_utc"] = None
    ok, why = c.admit({"name": "render", "weight": HEAVY, "estimate_minutes": 5}, now=T0, resources=w.resources,
                      shorts=w.shorts, light_running=0)
    assert not ok and "next Shorts start unknown" in why


def _shorts_db(path, spec=None):
    import sqlite3
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE persistent_jobs (id TEXT, status TEXT, current_stage TEXT)")
    c.execute("CREATE TABLE studio_settings (key TEXT PRIMARY KEY, value TEXT)")
    if spec is not None:
        import json
        from backend.app.services.control_center_service import ACTIVE_PROFILE_KEY
        c.execute("INSERT INTO studio_settings VALUES (?,?)", (ACTIVE_PROFILE_KEY, json.dumps(spec)))
    c.commit()
    c.close()
    return str(path)


def test_shorts_view_reads_the_profile_from_its_own_database(monkeypatch, tmp_path):
    """A standalone process must not plan against the default profile, nor against another database's profile:
    the active profile comes from the coordinator's own database (Stage 4.02 dry-run finding)."""
    import backend.app.core.paths as paths
    from backend.app.services.control_center_service import preset_schedule
    spec = preset_schedule("SINGLE_DAILY")
    db = _shorts_db(tmp_path / "own.db", spec)
    v = pc._default_shorts_view(T0, db)
    assert v["known"] is True and v["cycles"] == len(spec["cycles"]) and v["next_start_utc"]
    other = _shorts_db(tmp_path / "other.db")                 # no active-profile row, not the process database
    v2 = pc._default_shorts_view(T0, other)
    assert v2["known"] is False and "schedule unreadable" in v2["error"]


def test_process_database_falls_back_to_the_restored_profile_once(monkeypatch, tmp_path):
    import backend.app.core.paths as paths
    import backend.app.services.control_center_service as cc
    db = _shorts_db(tmp_path / "proc.db")
    monkeypatch.setattr(paths, "DATABASE_PATH", tmp_path / "proc.db")
    calls = []
    monkeypatch.setattr(pc, "_profile_loaded", [False])
    monkeypatch.setattr(cc, "restore_active_schedule_profile", lambda: calls.append(1))
    pc._default_shorts_view(T0, db)
    v = pc._default_shorts_view(T0, db)
    assert calls == [1] and v["known"] is True                # restored once per process


# -- deferral (Gemini quota resilience, 2026-11-27) -------------------------------------------------------------------

def test_deferred_step_waits_without_using_attempts(tmp_path):
    calls = []

    def ex(ctx):
        calls.append(ctx.step["name"])
        raise pc.StepDeferred("quota", pc._parse(pc._ts(T0 + timedelta(hours=3))))
    c, w, clock = make(tmp_path, executors={"lf": ex})
    enable(c)
    jid = c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op")["job"]["id"]
    for _ in range(4):                    # retried only after the provider's time, never failed
        run(c.tick())
        wait_threads(c)
        job = c.get_job(jid)
        assert job["state"] == pc.QUEUED and int(job["attempts"]) == 0 and job["error"].startswith("waiting:")
        clock.advance(hours=1)
    assert len(calls) == 2                # T0 and T0+3h only (not at T0+1h / +2h)


def test_deferral_is_bounded(tmp_path):
    def ex(ctx):
        raise pc.StepDeferred("quota", T0 + timedelta(days=30))
    c, w, clock = make(tmp_path, executors={"lf": ex})
    enable(c)
    jid = c.enqueue(kind="lf", title="doc", steps=STEPS, requested_by="op")["job"]["id"]
    run(c.tick())
    wait_threads(c)
    nb = pc._parse(c.get_job(jid)["not_before_utc"])
    assert nb <= T0 + timedelta(hours=pc.MAX_DEFER_HOURS, minutes=1)
