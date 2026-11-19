"""
Stage 3.29 dashboard auto-start: single server instance, bounded health wait, browser once per boot,
persistent status, monitor never restarts a starting dashboard. Temporary paths only; no real server,
browser or Task Scheduler change.
"""
import subprocess
import sys
import textwrap

import pytest

from backend.app.core import dashboard_startup as ds


@pytest.fixture(autouse=True)
def _tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(ds, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(ds, "SERVER_LOCK_PATH", tmp_path / "server.lock")
    monkeypatch.setattr(ds, "BROWSER_MARKER_DIR", tmp_path / "markers")
    monkeypatch.setattr(ds, "boot_id", lambda: "1794600000")


def test_server_lock_is_exclusive_and_released(tmp_path):
    a, b = ds.ServerLock(tmp_path / "server.lock"), ds.ServerLock(tmp_path / "server.lock")
    assert a.acquire()
    assert not b.acquire()                        # a second launcher never starts another server
    assert ds.server_instance_active(tmp_path / "server.lock")
    a.release()
    assert not ds.server_instance_active(tmp_path / "server.lock")
    assert b.acquire()
    b.release()


def test_server_lock_held_by_another_process_blocks_a_duplicate(tmp_path):
    lock = tmp_path / "server.lock"
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, r"{ds.PROJECT_ROOT}")
        from backend.app.core.dashboard_startup import ServerLock
        l = ServerLock(__import__("pathlib").Path(r"{lock}"))
        assert l.acquire(); print("locked", flush=True); time.sleep(20)
    """)
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "locked"
        assert ds.server_instance_active(lock)
    finally:
        p.kill()
        p.wait()
    assert not ds.server_instance_active(lock)     # the OS releases the lock when the process dies


def test_browser_opens_once_per_boot():
    opened = []
    assert ds.open_browser_once("http://x/dashboard", opener=opened.append, source="a")
    assert not ds.open_browser_once("http://x/dashboard", opener=opened.append, source="b")
    assert not ds.claim_browser_open("1794600120")  # neighbouring rounding bucket counts as the same boot
    assert opened == ["http://x/dashboard"]
    assert ds.claim_browser_open("1794686400")       # a new boot opens again


def test_health_wait_is_bounded_and_returns_when_healthy():
    t = [0.0]
    calls = []

    def probe():
        calls.append(t[0])
        return t[0] >= 40.0

    def sleep(s):
        t[0] += s

    assert ds.wait_until_healthy(180, 5, probe=probe, sleep=sleep, clock=lambda: t[0])   # a 28-40 s cold start passes
    t[0] = 0.0
    assert not ds.wait_until_healthy(30, 5, probe=lambda: False, sleep=sleep, clock=lambda: t[0])
    assert t[0] <= 35


def test_status_file_records_phases_and_boot():
    ds.write_status("launching", source="t")
    ds.write_status("failed", source="t", reason="not healthy within 180s")
    st = ds.read_status()
    assert st["phase"] == "failed" and st["reason"].startswith("not healthy") and st["current_boot"] is True
    assert [h["phase"] for h in st["history"]] == ["launching", "failed"]


def test_startup_manager_waits_for_a_starting_instance_instead_of_launching(monkeypatch):
    from backend.scripts import startup_manager as sm
    launched, opened = [], []
    health = iter([False, True, True])
    monkeypatch.setattr(sm, "is_backend_healthy", lambda health_url=None: next(health))
    monkeypatch.setattr(ds, "server_instance_active", lambda *a: True)
    monkeypatch.setattr(sm.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(ds, "open_browser_once", lambda url, source="": opened.append(url) or True)
    assert sm.ensure_dashboard_running(open_browser=True, wait_timeout_sec=5)
    assert launched == [] and opened == ["http://127.0.0.1:8000/dashboard"]


def test_startup_manager_launches_when_nothing_runs_and_reports_failure(monkeypatch):
    from backend.scripts import startup_manager as sm
    launched = []
    monkeypatch.setattr(sm, "is_backend_healthy", lambda health_url=None: False)
    monkeypatch.setattr(ds, "server_instance_active", lambda *a: False)
    monkeypatch.setattr(sm.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(ds, "wait_until_healthy", lambda *a, **k: False)
    assert not sm.ensure_dashboard_running(open_browser=True, wait_timeout_sec=1)
    assert len(launched) == 1
    assert ds.read_status()["phase"] == "failed"


def test_launcher_uses_project_python_and_no_credentials_on_command_line():
    from backend.scripts import startup_manager as sm
    src = (ds.PROJECT_ROOT / "backend" / "scripts" / "run_dashboard.py").read_text(encoding="utf-8")
    assert "max_wait_sec: float = 180.0" in src and "ServerLock()" in src and "open_browser_once" in src
    exe = sm._pythonw_exe()
    assert ".venv" in exe
    for secret_word in ("API_KEY", "client_secret", "token"):
        assert secret_word not in src.replace("token_", "")
