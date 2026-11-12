"""Stale-code detection and controlled (safe-point) daemon restart."""
import json
import os
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core import code_revision
from backend.app.database.db import Base, JobModel
from backend.app.services import control_center_service as control
from backend.app.services import deployment_diagnostics as dd


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'deploy.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(control, "SessionLocal", factory)
    yield factory
    engine.dispose()


def test_revision_is_stable_and_changes_with_source(tmp_path, monkeypatch):
    app = tmp_path / "backend" / "app"
    app.mkdir(parents=True)
    (app / "m.py").write_text("x = 1\n")
    monkeypatch.setattr(code_revision, "APP_DIR", app)
    monkeypatch.setattr(code_revision, "BACKEND_DIR", tmp_path / "backend")
    r1 = code_revision.compute_source_revision()
    assert r1 == code_revision.compute_source_revision()
    (app / "m.py").write_text("x = 2\n")
    assert code_revision.compute_source_revision() != r1
    (app / "m.py").write_bytes(b"x = 2\r\n")  # line endings alone do not count
    assert code_revision.compute_source_revision() == code_revision.compute_source_revision()


def test_fingerprint_covers_all_runtime_code():
    files = {p.relative_to(code_revision.BACKEND_DIR).as_posix() for p in code_revision._source_files()}
    for required in (
        "app/scheduler/scheduler.py",
        "app/scheduler/retry_policy.py",
        "app/scheduler/daemon_runtime.py",
        "app/scheduler/reconciliation_service.py",
        "app/orchestration/crazzydocs_master_orchestrator.py",
        "app/services/youtube_publishing_engine.py",
        "app/services/control_center_service.py",
        "app/main.py",
        "scripts/run_scheduler_daemon.py",
        "scripts/startup_manager.py",
    ):
        assert required in files, required
    assert all(f.endswith(".py") for f in files)
    assert not any("__pycache__" in f for f in files)


def test_editing_any_runtime_file_changes_revision(tmp_path, monkeypatch):
    import shutil
    src_app = code_revision.APP_DIR
    app = tmp_path / "backend" / "app"
    shutil.copytree(src_app / "scheduler", app / "scheduler", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(code_revision, "APP_DIR", app)
    monkeypatch.setattr(code_revision, "BACKEND_DIR", tmp_path / "backend")
    before = code_revision.compute_source_revision()
    target = app / "scheduler" / "retry_policy.py"
    target.write_text(target.read_text(encoding="utf-8").replace("DEFAULT_MAX_RETRIES = 3", "DEFAULT_MAX_RETRIES = 4"), encoding="utf-8")
    assert code_revision.compute_source_revision() != before


def test_current_daemon_reports_ok(db, monkeypatch):
    monkeypatch.setattr(code_revision, "current_source_revision", lambda *a, **k: code_revision.LOADED_CODE_REVISION)
    dd.register_daemon_start()
    st = dd.get_deployment_status()
    assert st["status"] == "OK"
    assert st["daemon"]["state"] == "CURRENT"
    assert st["daemon"]["pid"] == os.getpid()
    assert st["warnings"] == []


def test_stale_daemon_reports_stale_code(db, monkeypatch):
    dd.register_daemon_start()
    monkeypatch.setattr(code_revision, "current_source_revision", lambda *a, **k: "ffffffffffffffff")
    st = dd.get_deployment_status()
    assert st["status"] == "STALE_CODE"
    assert st["daemon"]["state"] == "STALE_CODE"
    assert any(w.startswith("STALE CODE: scheduler daemon") for w in st["warnings"])


def test_daemon_without_revision_is_treated_as_stale(db, monkeypatch):
    """The currently running daemon predates diagnostics: it must not look healthy."""
    monkeypatch.setattr(code_revision, "current_source_revision", lambda *a, **k: code_revision.LOADED_CODE_REVISION)
    control._write_settings({control.SCHEDULER_SYNC_KEY: json.dumps({"daemon_pid": os.getpid()})})
    st = dd.get_deployment_status()
    assert st["status"] == "STALE_CODE"
    assert st["daemon"]["state"] == "UNKNOWN_REVISION"


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "RETRYING"])
def test_restart_request_rejected_while_job_in_flight(db, status):
    with db() as s:
        s.add(JobModel(id="cycle_1_20261113_0000", date_str="2026-11-13", status=status, current_stage="RENDERING"))
        s.commit()
    with pytest.raises(ValueError, match="Restart rejected.*cycle_1_20261113_0000"):
        dd.request_daemon_restart(confirm=True, reason="deploy fix")
    assert dd.pending_restart_request() == {}


def test_daemon_rechecks_before_exit_when_job_starts_after_request(db):
    assert dd.should_exit_for_restart() == (False, "no request")
    dd.request_daemon_restart(confirm=True, reason="deploy fix")
    with db() as s:  # a cycle starts rendering after the request was accepted
        s.add(JobModel(id="cycle_1_20261113_0000", date_str="2026-11-13", status="RUNNING", current_stage="RENDERING"))
        s.commit()
    exit_now, why = dd.should_exit_for_restart()
    assert exit_now is False and "cycle_1_20261113_0000" in why
    with db() as s:
        s.query(JobModel).one().status = "COMPLETED"
        s.commit()
    assert dd.should_exit_for_restart() == (True, "no active production job")


def test_restart_requires_confirmation(db):
    with pytest.raises(ValueError):
        dd.request_daemon_restart(confirm=False)


def test_pid_check_never_signals(monkeypatch):
    killed = []
    monkeypatch.setattr(os, "kill", lambda *a: killed.append(a))
    assert dd.is_pid_alive(os.getpid()) is True
    assert dd.is_pid_alive(0) is False
    if os.name == "nt":
        assert killed == []


def test_deployment_api(db, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main as main_module

    monkeypatch.setattr(main_module, "init_db", lambda: None)
    dd.register_daemon_start()
    monkeypatch.setattr(code_revision, "current_source_revision", lambda *a, **k: "ffffffffffffffff")
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        body = client.get("/api/control-center/deployment").json()
        assert body["status"] == "STALE_CODE"
        r = client.post("/api/control-center/deployment/restart", json={"confirm": False})
        assert r.status_code == 409
        r = client.post("/api/control-center/deployment/restart", json={"confirm": True, "reason": "deploy"})
        assert r.status_code == 200 and r.json()["requested"] is True
        assert client.get("/api/control-center/deployment").json()["restart"]["requested"] is True
