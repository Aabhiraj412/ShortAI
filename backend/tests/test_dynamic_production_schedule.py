"""Comprehensive regression tests for Dynamic Production Cycle Scheduling."""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main as main_module
from backend.app.database.db import Base, JobModel, StudioSettingModel
from backend.app.scheduler import schedule_profiles, scheduler as scheduler_module
from backend.app.services import control_center_service as control


@pytest.fixture
def isolated_prod_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'prod-sched.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(control, "SessionLocal", factory)
    monkeypatch.setattr(scheduler_module, "SessionLocal", factory)
    original_profile = schedule_profiles.get_active_profile()
    yield factory
    schedule_profiles.set_runtime_profile(original_profile)
    engine.dispose()


def _five_cycle_spec():
    return {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {"cycle_id": "cycle_1", "name": "C1", "start_time": "00:00", "recovery_duration_minutes": 120, "target_publish_time": "04:00", "active_weekdays": [0,1,2,3,4,5,6], "next_day": False, "enabled": True},
            {"cycle_id": "cycle_2", "name": "C2", "start_time": "04:30", "recovery_duration_minutes": 120, "target_publish_time": "08:30", "active_weekdays": [0,1,2,3,4,5,6], "next_day": False, "enabled": True},
            {"cycle_id": "cycle_3", "name": "C3", "start_time": "09:00", "recovery_duration_minutes": 120, "target_publish_time": "13:00", "active_weekdays": [0,1,2,3,4,5,6], "next_day": False, "enabled": True},
            {"cycle_id": "cycle_4", "name": "C4", "start_time": "13:30", "recovery_duration_minutes": 120, "target_publish_time": "17:30", "active_weekdays": [0,1,2,3,4,5,6], "next_day": False, "enabled": True},
            {"cycle_id": "cycle_5", "name": "C5", "start_time": "18:00", "recovery_duration_minutes": 240, "target_publish_time": "00:00", "active_weekdays": [0,1,2,3,4,5,6], "next_day": True, "enabled": True},
        ],
    }


def test_01_default_four_cycles_daily_profile(isolated_prod_db):
    preset = control.preset_schedule("FOUR_CYCLES_DAILY")
    assert preset["profile_name"] == "FOUR_CYCLES_DAILY"
    assert len(preset["cycles"]) == 4
    assert [c["start_time"] for c in preset["cycles"]] == ["00:00", "06:00", "12:00", "18:00"]
    assert all(c["recovery_duration_minutes"] == 240 for c in preset["cycles"])
    res = control.validate_schedule(preset)
    assert res["valid"] is True
    assert res["cycle_count"] == 4


def test_02_add_fifth_production_cycle_and_03_remove_future_cycle(isolated_prod_db):
    spec5 = _five_cycle_spec()
    res5 = control.validate_schedule(spec5)
    assert res5["cycle_count"] == 5
    assert len(res5["preview_next_cycles"]) == 5

    spec4 = json.loads(json.dumps(spec5))
    spec4["cycles"] = [c for c in spec4["cycles"] if c["cycle_id"] != "cycle_3"]
    res4 = control.validate_schedule(spec4)
    assert res4["cycle_count"] == 4


def test_04_edit_production_start_time_and_05_06_recovery_durations(isolated_prod_db):
    spec = {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {"cycle_id": "c_2h", "name": "Two Hour Rec", "start_time": "07:15", "recovery_duration_minutes": 120, "target_publish_time": "11:15", "active_weekdays": [0,1,2,3,4,5,6], "next_day": False, "enabled": True},
            {"cycle_id": "c_4h", "name": "Four Hour Rec", "start_time": "15:00", "recovery_duration_minutes": 240, "target_publish_time": "21:00", "active_weekdays": [0,1,2,3,4,5,6], "next_day": False, "enabled": True},
        ],
    }
    res = control.validate_schedule(spec)
    assert res["valid"] is True
    assert res["cycles"][0]["start_time"] == "07:15"
    assert res["cycles"][0]["recovery_duration_minutes"] == 120
    assert res["cycles"][1]["recovery_duration_minutes"] == 240


def test_07_reject_below_two_hours_and_08_above_four_hours(isolated_prod_db):
    # Recovery duration is user-configurable from 30 minutes to 24 hours (was fixed 2-4 h).
    base = _five_cycle_spec()["cycles"][0]
    too_short = {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [{**base, "recovery_duration_minutes": 29, "target_publish_time": "06:00"}]}
    with pytest.raises(ValueError, match="between 30 and 1440"):
        control.validate_schedule(too_short)

    too_long = {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [{**base, "recovery_duration_minutes": 1441, "target_publish_time": "07:00"}]}
    with pytest.raises(ValueError, match="between 30 and 1440"):
        control.validate_schedule(too_long)

    for ok in (30, 119, 241, 1440):
        spec = {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [{**base, "recovery_duration_minutes": ok, "target_publish_time": "07:00"}]}
        assert control.validate_schedule(spec)["cycles"][0]["recovery_duration_minutes"] == ok


def test_09_no_publication_buffer_is_required(isolated_prod_db):
    # Publication is user-controlled per video; production schedules carry no
    # publication buffer. A legacy publish time is kept but not validated against.
    spec = {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {"cycle_id": "c1", "name": "Short Buffer", "start_time": "06:00", "recovery_duration_minutes": 120, "target_publish_time": "09:59", "active_weekdays": [0], "next_day": False, "enabled": True},
        ],
    }
    res = control.validate_schedule(spec)
    assert res["valid"] is True
    assert res["cycles"][0]["recovery_duration_minutes"] == 120


def test_10_midnight_crossing_cycles(isolated_prod_db):
    spec = {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {"cycle_id": "c_night", "name": "Overnight", "start_time": "22:00", "recovery_duration_minutes": 240, "target_publish_time": "04:00", "active_weekdays": [0,1,2,3,4,5,6], "next_day": True, "enabled": True},
        ],
    }
    res = control.validate_schedule(spec)
    assert res["valid"] is True
    preview = res["preview_next_cycles"][0]
    assert preview["production_start_ist"].endswith("22:00 IST")
    assert preview["recovery_deadline_ist"].endswith("02:00 IST")
    # The target publication rolls past midnight automatically and does not change the
    # production start or the recovery deadline.
    assert preview["target_publish_ist"].endswith("04:00 IST")
    assert preview["target_publish_ist"][:10] > preview["production_start_ist"][:10]
    assert "finalization_buffer_minutes" not in preview


def test_11_overlapping_recovery_windows_and_12_duplicate_cycles_rejected(isolated_prod_db):
    overlap_rec = {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {"cycle_id": "c1", "name": "C1", "start_time": "08:00", "recovery_duration_minutes": 180, "target_publish_time": "14:00", "active_weekdays": [0], "next_day": False, "enabled": True},
            {"cycle_id": "c2", "name": "C2", "start_time": "10:00", "recovery_duration_minutes": 120, "target_publish_time": "16:00", "active_weekdays": [0], "next_day": False, "enabled": True},
        ],
    }
    # Overlapping recovery windows are allowed (production capacity runs them one at a
    # time) but reported.
    res_overlap = control.validate_production_schedule_detailed(overlap_rec)
    assert res_overlap["valid"] is True
    assert any("overlap" in w for w in res_overlap["warnings"])

    dup_id = {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {"cycle_id": "dup", "name": "A", "start_time": "00:00", "recovery_duration_minutes": 120, "target_publish_time": "04:00", "active_weekdays": [0], "next_day": False, "enabled": True},
            {"cycle_id": "dup", "name": "B", "start_time": "06:00", "recovery_duration_minutes": 120, "target_publish_time": "10:00", "active_weekdays": [0], "next_day": False, "enabled": True},
        ],
    }
    with pytest.raises(ValueError, match="unique non-empty cycle_id"):
        control.validate_schedule(dup_id)

    dup_start = {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {"cycle_id": "c1", "name": "A", "start_time": "06:00", "recovery_duration_minutes": 120, "target_publish_time": "10:00", "active_weekdays": [0], "next_day": False, "enabled": True},
            {"cycle_id": "c2", "name": "B", "start_time": "06:00", "recovery_duration_minutes": 120, "target_publish_time": "11:00", "active_weekdays": [0], "next_day": False, "enabled": True},
        ],
    }
    with pytest.raises(ValueError, match="duplicate or conflicting production start"):
        control.validate_schedule(dup_start)


def test_13_disabled_cycles_preserved_and_skipped(isolated_prod_db):
    spec = _five_cycle_spec()
    spec["cycles"][2]["enabled"] = False
    res = control.validate_schedule(spec)
    assert res["total_cycles"] == 5
    assert res["cycle_count"] == 4
    prof = control._profile_from_spec(res)
    assert len(prof.cycles) == 4
    assert all(c.cycle_id != "cycle_3" for c in prof.cycles)


def test_14_version_persistence_15_active_job_protection_16_history(isolated_prod_db):
    with isolated_prod_db() as s:
        s.add(JobModel(id="cycle_2_20261112_0600", date_str="2026-11-12", status="RUNNING"))
        s.commit()

    spec = _five_cycle_spec()
    control.save_schedule_draft(spec)
    control.validate_saved_schedule_draft()
    act = control.activate_schedule_draft(confirm=True)
    assert act["active_schedule"]["schedule_version"] == 2
    assert len(act["active_schedule"]["revision"]) == 12

    # Active running job is untouched and historical schedule is archived
    with isolated_prod_db() as s:
        job = s.get(JobModel, "cycle_2_20261112_0600")
        assert job is not None and job.status == "RUNNING"
    state = control.get_control_center_state()
    assert state["schedule"]["active"]["schedule_version"] == 2
    assert len(state["schedule"]["history"]) >= 1


def test_17_scheduler_restart_and_18_draft_active_isolation(isolated_prod_db):
    # Activate custom 5-cycle schedule
    spec5 = _five_cycle_spec()
    control.validate_saved_schedule_draft(spec5)
    control.activate_schedule_draft(confirm=True)

    # Save an unactivated draft (SINGLE_DAILY) and simulate process restart
    draft_single = control.preset_schedule("SINGLE_DAILY")
    control.save_schedule_draft(draft_single)
    schedule_profiles.set_active_profile("FOUR_CYCLES_DAILY")  # reset in-memory process state

    control.restore_active_schedule_profile()
    restored = schedule_profiles.get_active_profile()
    assert restored.name == "CUSTOM"
    assert len(restored.cycles) == 5

    # Editing draft invalidates previous validation
    control.validate_saved_schedule_draft()
    edited = json.loads(json.dumps(draft_single))
    edited["cycles"][0]["start_time"] = "22:30"
    edited["cycles"][0]["target_publish_time"] = "04:30"
    control.save_schedule_draft(edited)
    with pytest.raises(ValueError, match="Validate"):
        control.activate_schedule_draft(confirm=True)


def test_19_independent_publication_schedule_preserved(isolated_prod_db):
    pub_before = control.get_publication_schedule()["active"]
    control.validate_saved_schedule_draft(_five_cycle_spec())
    control.activate_schedule_draft(confirm=True)
    pub_after = control.get_publication_schedule()["active"]
    assert pub_before["slots"] == pub_after["slots"]


def test_20_duplicate_execution_prevention(isolated_prod_db):
    import asyncio
    control.save_operating_mode_draft("PRODUCTION")
    control.activate_operating_mode("PRODUCTION", confirm=True)
    with isolated_prod_db() as s:
        s.add(JobModel(id="cycle_1_20261112_0000", date_str="2026-11-12", status="COMPLETED"))
        s.commit()

    res = asyncio.run(scheduler_module.run_cycle_generation(
        "cycle_1",
        test_ist_now=datetime(2026, 11, 12, 1, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    ))
    assert res["status"] == "SKIPPED"
    assert res["reason"] == "ALREADY_COMPLETED"


def test_dashboard_and_timeline_endpoint(isolated_prod_db, monkeypatch):
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        dash = client.get("/dashboard")
        assert dash.status_code == 200
        assert "Production Schedule — Asia/Kolkata" in dash.text
        assert "Publication Schedule" not in dash.text and "uniScheduleBody" not in dash.text
        tl = client.get("/api/control-center/schedule/timeline")
        assert tl.status_code == 200
        assert "upcoming_cycles" in tl.json()
