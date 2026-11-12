"""
Stage 3.23.3 — Full Autonomous Production & YouTube Publishing Restoration Tests.
Tests scheduler truthfulness, cache invalidation, validation/production separation,
resource safety deferrals, and readiness-gated automatic publishing controls.
"""

from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main as main_module
from backend.app.database.db import Base, JobModel
from backend.app.scheduler import scheduler as scheduler_mod
from backend.app.scheduler.schedule_profiles import IST_TZ
from backend.app.services import control_center_service as control
from backend.app.services.production_health_dashboard import ProductionHealthDashboard
from backend.app.services.production_observability import ProductionObservabilityStore


@pytest.fixture
def isolated_cc(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'studio_3233.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(control, "SessionLocal", session_factory)
    return session_factory


def test_scheduler_subsignals_and_cache_invalidation(monkeypatch):
    from backend.scripts import manage_windows_startup as mws

    mws._tasks_cache = {"stale": True}
    mws._tasks_cache_time = 999999.0
    mws.invalidate_tasks_cache()
    assert mws._tasks_cache == {}
    assert mws._tasks_cache_time == 0.0

    phd = ProductionHealthDashboard()
    phd._task_cache = {"value": {"stale": True}, "ts": 999999.0}
    phd.invalidate_task_cache()
    assert phd._task_cache["value"] is None

    metrics = phd.get_health_metrics()
    svc = {
        "scheduler_daemon": {"state": "STOPPED", "is_running": False, "pid": None},
        "scheduler_heartbeat": {"state": "STOPPED", "pid": None},
    }
    tasks = {
        "scheduler_task": {"is_registered": True, "task_state": "Ready", "status": "ENABLED"},
    }
    states = phd.get_reconciled_operational_states(metrics=metrics, services=svc, windows_tasks=tasks)
    sd = states["scheduler_details"]
    assert states["scheduler_state"] == "STANDBY"
    assert sd["task_registered"] is True
    assert sd["task_enabled"] is True
    assert sd["task_running"] is False
    assert sd["daemon_alive"] is False
    assert sd["heartbeat_current"] is False


def test_observability_separates_validation_and_production(tmp_path):
    store = ProductionObservabilityStore(
        store_path=str(tmp_path / "prod_store.json"),
        test_store_path=str(tmp_path / "test_store.json"),
    )
    store.start_production("val_run_1", topic="Validation Topic", execution_context="VALIDATION")
    store.complete_production("val_run_1")

    payload = store.get_dashboard_payload(include_tests=False)
    assert payload["latest_validation_run"]["production_id"] == "val_run_1"
    assert payload["latest_production_run"] is None
    assert payload["active_production_run"] is None

    store.start_production("cycle_1_20261112_0000", topic="Production Topic", execution_context="PRODUCTION")
    payload2 = store.get_dashboard_payload(include_tests=False)
    assert payload2["active_production_run"]["production_id"] == "cycle_1_20261112_0000"
    assert payload2["latest_production_run"]["production_id"] == "cycle_1_20261112_0000"
    assert payload2["latest_validation_run"]["production_id"] == "val_run_1"


def test_publishing_policy_requires_verified_readiness_gate(isolated_cc, monkeypatch):
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    blocked = {
        "publishing_capable": False,
        "autonomous_ready": False,
        "checks": {"oauth_connected": False},
        "blockers": ["YouTube OAuth channel is not connected."],
        "channel_info": None,
    }
    monkeypatch.setattr(control, "evaluate_publishing_readiness", lambda: blocked)

    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        res_fail = client.put(
            "/api/control-center/publishing/policy",
            json={"mode": "automatic", "confirm": True},
        )
        assert res_fail.status_code == 409
        assert control.get_publishing_policy() == "manual_approval"

        ready = {
            "publishing_capable": True,
            "autonomous_ready": True,
            "checks": {"oauth_connected": True, "youtube_upload_scope": True, "token_refreshable": True},
            "blockers": [],
            "channel_info": {"title": "CrazzyDocs", "custom_url": "@crazzydocs"},
        }
        monkeypatch.setattr(control, "evaluate_publishing_readiness", lambda: ready)
        res_ok = client.put(
            "/api/control-center/publishing/policy",
            json={"mode": "automatic", "confirm": True},
        )
        assert res_ok.status_code == 200
        assert res_ok.json()["mode"] == "automatic"
        assert res_ok.json()["automatic_enabled"] is True

        status_res = client.get("/api/control-center/publishing")
        assert status_res.status_code == 200
        assert status_res.json()["mode"] == "automatic"
        assert status_res.json()["autonomous_ready"] is True


@pytest.mark.asyncio
async def test_resource_guard_defers_cycle_on_high_load(isolated_cc, monkeypatch):
    control.save_operating_mode_draft("PRODUCTION")
    control.activate_operating_mode("PRODUCTION", confirm=True)
    monkeypatch.setattr(scheduler_mod, "SessionLocal", isolated_cc)
    monkeypatch.setattr(control, "evaluate_resource_safety", lambda: {
        "safe": False,
        "cpu_used_percent": 96.5,
        "memory_used_percent": 94.0,
        "reason": "CPU load 96.5% exceeds safe threshold 92.0%.",
    })
    now_0005 = datetime(2026, 11, 12, 0, 5, 0, tzinfo=IST_TZ)
    res = await scheduler_mod.run_cycle_generation("cycle_1", test_ist_now=now_0005)
    assert res["status"] == "DEFERRED"
    assert "96.5%" in res["reason"]


@pytest.mark.asyncio
async def test_cycle_publishing_respects_automatic_authorization(isolated_cc, monkeypatch):
    monkeypatch.setattr(scheduler_mod, "get_operating_mode", lambda: "IDLE")
    with patch.object(
        scheduler_mod.master_orchestrator, "process_publishing_queue", return_value={"status": "withheld_safety_gate"}
    ) as mock_proc:
        res_idle = await scheduler_mod.run_cycle_publishing("cycle_1")
        assert res_idle["status"] == "withheld_safety_gate"
        mock_proc.assert_called_once_with(execute_upload=False)

    monkeypatch.setattr(scheduler_mod, "get_operating_mode", lambda: "PRODUCTION")
    monkeypatch.setattr(control, "is_automatic_publishing_authorized", lambda: (True, {"reason": "ok"}))
    with patch.object(
        scheduler_mod.master_orchestrator, "process_publishing_queue", return_value={"status": "queue_empty"}
    ) as mock_proc:
        await scheduler_mod.run_cycle_publishing("cycle_1")
        mock_proc.assert_called_once_with(execute_upload=True)
