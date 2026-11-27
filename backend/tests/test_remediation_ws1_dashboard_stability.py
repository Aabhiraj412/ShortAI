"""Remediation WS1: dashboard and daemon stability.

Root causes covered:
* the dashboard ran on the Windows Proactor loop, whose accept/connection-lost handling stalls after aborted
  browser connections (reproduced: 16-20 vs 152-156 served requests in 45 s against the Selector loop);
* 101 of 220 async routes never awaited, so their synchronous DB/subprocess work ran on the event loop and
  starved the liveness probe;
* the resources endpoint spawned nvidia-smi/tasklist and slept 100 ms on every poll of every tab.
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "backend" / "app" / "main.py"


def test_dashboard_uses_selector_loop_on_windows(monkeypatch):
    sys.path.insert(0, str(ROOT))
    from backend.scripts import run_dashboard as rd
    monkeypatch.setattr(rd.sys, "platform", "win32")
    monkeypatch.delenv("CRAZZYDOCS_DASHBOARD_LOOP", raising=False)
    assert rd.dashboard_loop_setting() == "asyncio:SelectorEventLoop"
    monkeypatch.setenv("CRAZZYDOCS_DASHBOARD_LOOP", "proactor")
    assert rd.dashboard_loop_setting() == "auto"
    monkeypatch.setattr(rd.sys, "platform", "linux")
    monkeypatch.delenv("CRAZZYDOCS_DASHBOARD_LOOP", raising=False)
    assert rd.dashboard_loop_setting() == "auto"


def test_uvicorn_accepts_the_selector_loop_string():
    import uvicorn
    cfg = uvicorn.Config("backend.app.main:app", loop="asyncio:SelectorEventLoop")
    factory = cfg.get_loop_factory()
    loop = factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_log_config_has_no_console_handler_without_stdout(monkeypatch):
    from backend.scripts import run_dashboard as rd
    monkeypatch.setattr(rd.sys, "stdout", None)
    cfg = rd.build_uvicorn_log_config(Path(os.environ.get("TEMP", ".")))
    assert "console" not in cfg["loggers"]["uvicorn"]["handlers"]
    assert "console" not in cfg["loggers"]["uvicorn.error"]["handlers"]


def test_run_dashboard_passes_loop_to_uvicorn():
    src = (ROOT / "backend" / "scripts" / "run_dashboard.py").read_text(encoding="utf-8")
    assert "loop=dashboard_loop_setting()" in src


def _async_routes_without_await():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    out = []
    for n in tree.body:
        if not isinstance(n, ast.AsyncFunctionDef):
            continue
        methods = [d.func.attr for d in n.decorator_list if isinstance(d, ast.Call) and hasattr(d.func, "attr")]
        if "get" not in methods:
            continue
        if not any(isinstance(x, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for x in ast.walk(n)):
            out.append(n.name)
    return out


def test_no_get_route_runs_blocking_work_on_the_event_loop():
    """A GET handler that never awaits must be a plain def (FastAPI runs it in the threadpool). Only the liveness
    probe stays on the loop, on purpose: it measures loop health."""
    assert _async_routes_without_await() == ["api_health_live"]


def test_resource_endpoint_is_cached(monkeypatch):
    from backend.app import main
    calls = []
    monkeypatch.setattr(main, "_resource_status_uncached", lambda: calls.append(1) or {"cpu": {}, "n": len(calls)})
    main._resource_status_cache.update(at=float("-inf"), value=None)
    a = main.control_center_resource_status(None)
    b = main.control_center_resource_status(None)
    assert calls == [1] and a == b
    main._resource_status_cache.update(at=float("-inf"), value=None)


def test_loop_diagnostics_measure_lag_and_sample_stacks(monkeypatch):
    from backend.app.core import loop_diagnostics as ld
    ld.reset_for_tests()
    monkeypatch.setattr(ld, "LAG_INTERVAL", 0.05)
    monkeypatch.setattr(ld, "SAMPLE_INTERVAL", 0.02)
    import time as _t

    async def go():
        ld.start()
        await asyncio.sleep(0.2)
        _t.sleep(0.3)                     # block the loop on purpose
        await asyncio.sleep(0.2)
        return ld.snapshot()

    snap = asyncio.run(go())
    assert snap["started"] and snap["lag_seconds"]["max"] >= 0.2
    assert snap["loop_thread_samples"] > 0 and snap["loop_thread_top_stacks"]
    ld.reset_for_tests()


def test_loop_health_route_is_local_only():
    from fastapi.testclient import TestClient
    from backend.app import main
    remote = TestClient(main.app, base_url="http://example.com", client=("10.1.2.3", 5000))
    assert remote.get("/api/health/loop").status_code in (403, 400)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r = local.get("/api/health/loop")
    assert r.status_code == 200 and "loop_thread_top_stacks" in r.json()


def test_liveness_still_answers_from_the_loop():
    from fastapi.testclient import TestClient
    from backend.app import main
    c = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    assert c.get("/api/health/live").json()["status"] == "alive"
    assert asyncio.iscoroutinefunction(main.api_health_live)


def test_concurrent_startup_status_writes_never_raise(tmp_path, monkeypatch):
    """Root cause of the dying Task Scheduler launches: the browser thread and the main thread wrote the startup
    status through one shared .tmp file at the same moment; the losing os.replace raised before uvicorn.run."""
    import json as _json
    import threading as _th
    from backend.app.core import dashboard_startup as ds
    monkeypatch.setattr(ds, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(ds, "STATUS_PATH", tmp_path / "dashboard_startup_status.json")
    errors = []
    barrier = _th.Barrier(8)

    def writer(i):
        barrier.wait()
        try:
            for _ in range(25):
                ds.write_status("launching" if i % 2 else "waiting_for_health", source=f"t{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [_th.Thread(target=writer, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    data = _json.loads((tmp_path / "dashboard_startup_status.json").read_text(encoding="utf-8"))
    assert data["phase"] in ("launching", "waiting_for_health") and len(data["history"]) <= 20
    assert not list(tmp_path.glob("*.tmp"))


def test_write_status_survives_an_unwritable_directory(tmp_path, monkeypatch):
    from backend.app.core import dashboard_startup as ds
    blocker = tmp_path / "file_not_dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(ds, "LOGS_DIR", blocker / "logs")
    monkeypatch.setattr(ds, "STATUS_PATH", blocker / "logs" / "status.json")
    assert ds.write_status("launching")["phase"] == "launching"      # informational: never raises


def test_run_dashboard_logs_unhandled_exceptions():
    src = (ROOT / "backend" / "scripts" / "run_dashboard.py").read_text(encoding="utf-8")
    assert "exited on an unhandled exception" in src


def test_worker_thread_sampler_finds_busy_app_code(monkeypatch):
    """Follow-up: CPU bursts after requests ran outside the event loop; the sampler now names busy worker threads."""
    import threading as _th
    import time as _t
    from backend.app.core import loop_diagnostics as ld
    ld.reset_for_tests()
    monkeypatch.setattr(ld, "SAMPLE_INTERVAL", 0.01)
    stop = _th.Event()

    def busy_app_work():                       # this file lives under backend/tests, so fake an app frame name
        code = compile("while not stop.is_set():\n    sum(range(500))\n", "D:/x/backend/app/services/hot.py", "exec")
        exec(code, {"stop": stop})

    t = _th.Thread(target=busy_app_work, name="hot-worker", daemon=True)
    t.start()

    async def go():
        ld.start()
        await asyncio.sleep(0.4)
        return ld.snapshot()

    try:
        snap = asyncio.run(go())
    finally:
        stop.set()
        t.join(2)
    assert snap["worker_busy_samples"] > 0
    assert any("hot-worker" in w["stack"] and "hot.py" in w["stack"] for w in snap["worker_top_stacks"])
    ld.reset_for_tests()
