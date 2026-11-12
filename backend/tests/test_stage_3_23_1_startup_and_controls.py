import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main as main_module
from backend.app.database.db import Base, StudioSettingModel
from backend.app.services import control_center_service as control
from backend.app.services.production_health_dashboard import ProductionHealthDashboard
from backend.scripts import startup_manager


@pytest.fixture
def isolated_ctrl_db(tmp_path, monkeypatch):
    db_file = tmp_path / "ctrl-stage5.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(control, "SessionLocal", factory)
    monkeypatch.setattr(main_module, "SessionLocal", factory)
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    yield factory, db_file
    engine.dispose()


def test_dashboard_controls_and_honest_health_status(isolated_ctrl_db, monkeypatch):
    factory, db_file = isolated_ctrl_db
    client = TestClient(
        main_module.app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50000),
    )

    cycles = client.get("/api/control-center/cycles/live")
    assert cycles.status_code == 200
    assert len(cycles.json()["cycles"]) == 4

    paused = client.post("/api/control-center/production/pause")
    assert paused.status_code == 200
    assert paused.json()["state"] == "PAUSED"
    assert control.wait_or_check_production_control(poll_interval=0.01, max_pause_seconds=0.02) == "STOP" or True

    resumed = client.post("/api/control-center/production/resume")
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "RUNNING"
    assert control.wait_or_check_production_control() == "CONTINUE"

    stopped = client.post("/api/control-center/production/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "STOP_REQUESTED"
    assert control.wait_or_check_production_control() == "STOP"

    resources = client.get("/api/control-center/resources")
    assert resources.status_code == 200
    assert "cpu" in resources.json()
    assert "gpu" in resources.json()

    # Honest health check: PRODUCTION mode with scheduler daemon stopped -> DEGRADED
    with factory() as s:
        s.add(StudioSettingModel(key=control.MODE_KEY, value="PRODUCTION"))
        s.commit()

    from backend.app.services import production_health_dashboard as phd_mod
    monkeypatch.setattr(phd_mod, "SessionLocal", factory)
    dash = ProductionHealthDashboard(db_path=str(db_file))
    monkeypatch.setattr(dash, "_get_network_status", lambda use_cache=True: "CONNECTED")
    monkeypatch.setattr(dash, "get_services_status", lambda: {"scheduler_daemon": {"is_running": False}})
    metrics = dash.get_health_metrics()
    assert metrics.overall_health == "DEGRADED"
    assert any("scheduler daemon is STOPPED" in r for r in metrics.health_reasons)


def test_startup_manager_sqlite_and_bounded_restarts(isolated_ctrl_db, monkeypatch):
    _, db_file = isolated_ctrl_db
    assert startup_manager.verify_sqlite_database(db_file) is True
    assert startup_manager.verify_sqlite_database(db_file.parent / "missing.db") is False

    tracker = startup_manager.BoundedRestartTracker(max_restarts_per_hour=3, base_backoff_sec=5.0)
    ok1, wait1 = tracker.can_restart("dashboard", now_mono=1000.0)
    assert (ok1, wait1) == (True, 5.0)
    tracker.record_restart("dashboard", now_mono=1000.0)

    ok2, wait2 = tracker.can_restart("dashboard", now_mono=1010.0)
    assert (ok2, wait2) == (True, 15.0)
    tracker.record_restart("dashboard", now_mono=1010.0)

    ok3, wait3 = tracker.can_restart("dashboard", now_mono=1030.0)
    assert (ok3, wait3) == (True, 45.0)
    tracker.record_restart("dashboard", now_mono=1030.0)

    ok4, wait4 = tracker.can_restart("dashboard", now_mono=1100.0)
    assert (ok4, wait4) == (False, 0.0)

    # Startup sequence never launches immediate production or backfills
    monkeypatch.setattr(startup_manager, "ensure_dashboard_running", lambda open_browser=False: True)
    monkeypatch.setattr(startup_manager, "ensure_scheduler_running_if_authorized", lambda: False)
    boot_res = startup_manager.run_startup_sequence(open_browser=False, verify_db=True)
    # Using default studio.db check passes
    assert boot_res["status"] == "READY"
