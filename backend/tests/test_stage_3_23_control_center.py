import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.db import Base, JobModel, StudioSettingModel
from backend.app import main as main_module
from backend.app.services import control_center_service as control


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'control-center.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(control, "SessionLocal", factory)
    yield factory
    engine.dispose()


def test_mode_requires_a_matching_confirmed_draft_and_persists(isolated_settings):
    assert control.get_operating_mode() == "IDLE"
    control.save_operating_mode_draft("gaming")

    with pytest.raises(ValueError, match="Explicit confirmation"):
        control.activate_operating_mode("GAMING", confirm=False)

    result = control.activate_operating_mode("GAMING", confirm=True)

    assert result == {"previous_mode": "IDLE", "active_mode": "GAMING"}
    assert control.get_operating_mode() == "GAMING"
    assert control.get_control_center_state()["operating_mode"]["pending"] is None
    assert control.get_control_center_state()["change_history"][-1]["kind"] == "operating_mode"


def test_schedule_draft_is_not_activated_until_validated_and_confirmed(
    isolated_settings, monkeypatch
):
    activated = []
    monkeypatch.setattr(control, "set_runtime_profile", lambda profile: activated.append(profile))
    spec = {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [{
            "cycle_id": "evening",
            "name": "Evening cycle",
            "start_time": "18:00",
            "target_publish_time": "00:00",
            "active_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "next_day": True,
            "enabled": True,
        }],
    }

    control.save_schedule_draft(spec)
    assert activated == []
    with pytest.raises(ValueError, match="Validate"):
        control.activate_schedule_draft(confirm=True)

    validation = control.validate_saved_schedule_draft()
    assert validation["valid"] is True
    with pytest.raises(ValueError, match="Explicit confirmation"):
        control.activate_schedule_draft(confirm=False)
    activated_result = control.activate_schedule_draft(confirm=True)

    assert activated_result["active_schedule"]["profile_name"] == "CUSTOM"
    assert activated[0].cycles[0].pub_rollover_days == 1  # legacy field preserved
    # Production window = recovery window (default 240 min), not the gap to a
    # publication time.
    assert activated[0].cycles[0].window_hours == 4
    assert activated[0].cycles[0].recovery_window_hours == 4
    control.restore_active_schedule_profile()
    assert len(activated) == 2
    saved = isolated_settings().get(StudioSettingModel, control.ACTIVE_PROFILE_KEY)
    assert json.loads(saved.value)["profile_name"] == "CUSTOM"


def test_schedule_validation_ignores_publication_gap_but_rejects_duplicate_starts(isolated_settings):
    short_gap = {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [{
            "cycle_id": "short",
            "name": "Short",
            "start_time": "10:00",
            "target_publish_time": "15:59",
            "active_weekdays": [0],
            "next_day": False,
            "enabled": True,
        }],
    }
    control.save_schedule_draft(short_gap)
    # A publication time no longer constrains production.
    assert control.validate_saved_schedule_draft()["valid"] is True

    conflict = {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {
                "cycle_id": "first", "name": "First", "start_time": "00:00",
                "target_publish_time": "06:00", "active_weekdays": [0],
                "next_day": False, "enabled": True,
            },
            {
                "cycle_id": "second", "name": "Second", "start_time": "00:00",
                "target_publish_time": "06:00", "active_weekdays": [0],
                "next_day": False, "enabled": True,
            },
        ],
    }
    with pytest.raises(ValueError, match="duplicate or conflicting production start times"):
        control.validate_schedule(conflict)


def test_four_cycle_schedule_cannot_be_changed(isolated_settings):
    spec = control.preset_schedule("FOUR_CYCLES_DAILY")
    spec["cycles"][0]["start_time"] = "00:30"

    with pytest.raises(ValueError, match="fixed"):
        control.validate_schedule(spec)


def test_preset_four_cycle_timings_validate_and_concurrency_is_enforced(isolated_settings):
    assert control.validate_schedule(
        control.preset_schedule("FOUR_CYCLES_DAILY")
    )["cycle_count"] == 4
    with isolated_settings() as session:
        session.add(JobModel(
            id="active-production",
            date_str="2026-11-12",
            status="RUNNING",
        ))
        session.commit()

    assert control.production_capacity()["available"] is False
    control.set_max_concurrency(2)
    assert control.production_capacity()["available"] is True
    assert control.get_max_concurrency() == 2


def test_invalid_mode_is_not_saved(isolated_settings):
    with pytest.raises(ValueError, match="Unsupported"):
        control.save_operating_mode_draft("AUTOMATIC")


def test_control_center_api_works_directly_from_localhost_and_persists_mode_draft(
    isolated_settings, monkeypatch
):
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    with TestClient(
        main_module.app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50000),
    ) as client:
        state = client.get("/api/control-center/state")
        draft = client.post(
            "/api/control-center/operating-mode/draft",
            json={"mode": "MAINTENANCE"},
        )
        activated = client.post(
            "/api/control-center/operating-mode/activate",
            json={"mode": "MAINTENANCE", "confirm": True},
        )

    assert state.status_code == 200
    assert state.json()["operating_mode"]["active"] == "IDLE"
    assert draft.status_code == 200
    assert activated.status_code == 200
    assert activated.json()["active_mode"] == "MAINTENANCE"
    assert control.get_operating_mode() == "MAINTENANCE"
