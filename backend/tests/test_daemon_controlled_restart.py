"""Phase E audit: the daemon exits only at a safe point and the startup monitor relaunches it."""
import asyncio
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.db import Base, JobModel
from backend.app.scheduler import daemon_runtime
from backend.app.services import control_center_service as control
from backend.app.services import deployment_diagnostics as dd


@pytest.fixture
def daemon_env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'daemon.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(control, "SessionLocal", factory)
    dispatches = []

    async def fake_dispatch():
        dispatches.append(1)
        return {}

    monkeypatch.setattr(daemon_runtime, "start_scheduler", lambda **_: True)
    monkeypatch.setattr(daemon_runtime, "get_scheduler_status", lambda: {"cycles": []})
    monkeypatch.setattr(daemon_runtime, "reload_profile_from_db_if_changed", lambda: False)
    monkeypatch.setattr(daemon_runtime, "dispatch_eligible_recoveries", fake_dispatch)
    monkeypatch.setattr(daemon_runtime.scheduler, "shutdown", MagicMock())
    yield {"db": factory, "dispatches": dispatches, "lock": MagicMock()}
    engine.dispose()


def _run(env, timeout):
    """Runs the daemon until it exits by itself (returns its exit code) or `timeout` elapses
    (returns None, i.e. it was still running). run_daemon swallows cancellation as its
    shutdown path, so wait_for cannot tell the two apart; check task.done() instead."""
    async def go():
        task = asyncio.create_task(
            daemon_runtime.run_daemon(env["lock"], heartbeat_interval=0.01, recovery_interval=0.02, tick_seconds=0.01)
        )
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if task in done:
            return task.result()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return None
    return asyncio.run(go())


def test_idle_daemon_exits_cleanly_on_request(daemon_env):
    dd.register_daemon_start()
    dd.request_daemon_restart(confirm=True, reason="deploy")
    assert _run(daemon_env, timeout=5) == 0
    assert dd.pending_restart_request() == {}  # request consumed exactly once


def test_daemon_does_not_exit_while_render_in_progress(daemon_env):
    dd.request_daemon_restart(confirm=True, reason="deploy")
    with daemon_env["db"]() as s:  # render started after the request was accepted
        s.add(JobModel(id="cycle_4_20261113_1730", date_str="2026-11-13", status="RUNNING", current_stage="RENDERING"))
        s.commit()
    assert _run(daemon_env, timeout=0.5) is None  # still running: did not exit mid-render
    assert len(daemon_env["dispatches"]) >= 3  # kept serving the running job
    assert dd.pending_restart_request() != {}  # request still pending, not dropped

    with daemon_env["db"]() as s:
        s.query(JobModel).one().status = "COMPLETED"
        s.commit()
    assert _run(daemon_env, timeout=5) == 0


def test_daemon_without_request_keeps_running(daemon_env):
    assert _run(daemon_env, timeout=0.3) is None
    assert len(daemon_env["dispatches"]) >= 2


def test_startup_monitor_relaunches_exited_daemon(tmp_path, monkeypatch):
    from backend.scripts import startup_manager as sm
    from backend.scripts import manage_windows_startup as mws
    from backend.app.scheduler import reconciliation_service as rs

    monkeypatch.setattr(sm, "MONITOR_LOCK", tmp_path / "monitor.lock")
    monkeypatch.setattr(rs, "LOCK_FILE_PATH", str(tmp_path / "scheduler.lock"))
    monkeypatch.setattr(rs.SchedulerProcessLock.__init__, "__defaults__", (str(tmp_path / "scheduler.lock"),))
    monkeypatch.setattr(control, "get_operating_mode", lambda: "PRODUCTION")
    monkeypatch.setattr(mws, "query_task_details", lambda name: {"is_registered": True, "task_state": "Ready"})
    popen = MagicMock()
    monkeypatch.setattr(sm.subprocess, "Popen", popen)

    res = sm.run_health_monitor_loop(max_iterations=1, health_probe=lambda: True, sleep_fn=lambda s: None)
    assert res["iterations"] == 1
    popen.assert_called_once()
    argv = popen.call_args.args[0]
    assert argv[-1].replace("\\", "/").endswith("backend/scripts/run_scheduler_daemon.py")


def test_startup_monitor_does_not_launch_duplicate(tmp_path, monkeypatch):
    from backend.scripts import startup_manager as sm
    from backend.scripts import manage_windows_startup as mws
    from backend.app.scheduler import reconciliation_service as rs

    lock_path = str(tmp_path / "scheduler.lock")
    monkeypatch.setattr(rs.SchedulerProcessLock.__init__, "__defaults__", (lock_path,))
    monkeypatch.setattr(control, "get_operating_mode", lambda: "PRODUCTION")
    monkeypatch.setattr(mws, "query_task_details", lambda name: {"is_registered": True, "task_state": "Ready"})
    held = rs.SchedulerProcessLock(lock_path)
    assert held.acquire()  # a live daemon (this process) holds the lock
    popen = MagicMock()
    monkeypatch.setattr(sm.subprocess, "Popen", popen)
    try:
        assert sm.ensure_scheduler_running_if_authorized() is True
        popen.assert_not_called()
    finally:
        held.release()
