"""Comprehensive regression tests for the Unified Schedule Editor & Verified Schedule Activation."""
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
from backend.app.database.db import Base, JobModel
from backend.app.scheduler import schedule_profiles, scheduler as scheduler_module
from backend.app.services import control_center_service as control


IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def isolated_unified_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'unified-sched.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(control, "SessionLocal", factory)
    monkeypatch.setattr(scheduler_module, "SessionLocal", factory)
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    original_profile = schedule_profiles.get_active_profile()
    yield factory
    schedule_profiles.set_runtime_profile(original_profile)
    engine.dispose()


def _five_cycle_prod_spec():
    return {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {"cycle_id": "cycle_1", "name": "C1", "start_time": "00:00", "recovery_duration_minutes": 120, "target_publish_time": "04:00", "active_weekdays": [0, 1, 2, 3, 4, 5, 6], "next_day": False, "enabled": True},
            {"cycle_id": "cycle_2", "name": "C2", "start_time": "04:30", "recovery_duration_minutes": 120, "target_publish_time": "08:30", "active_weekdays": [0, 1, 2, 3, 4, 5, 6], "next_day": False, "enabled": True},
            {"cycle_id": "cycle_3", "name": "C3", "start_time": "09:00", "recovery_duration_minutes": 120, "target_publish_time": "13:00", "active_weekdays": [0, 1, 2, 3, 4, 5, 6], "next_day": False, "enabled": True},
            {"cycle_id": "cycle_4", "name": "C4", "start_time": "13:30", "recovery_duration_minutes": 120, "target_publish_time": "17:30", "active_weekdays": [0, 1, 2, 3, 4, 5, 6], "next_day": False, "enabled": True},
            {"cycle_id": "cycle_5", "name": "C5", "start_time": "18:00", "recovery_duration_minutes": 240, "target_publish_time": "00:00", "active_weekdays": [0, 1, 2, 3, 4, 5, 6], "next_day": True, "enabled": True},
        ],
    }


def _one_cycle_prod_spec():
    return {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {"cycle_id": "solo_1", "name": "Solo Cycle", "start_time": "10:00", "recovery_duration_minutes": 180, "target_publish_time": "15:00", "active_weekdays": [0, 1, 2, 3, 4, 5, 6], "next_day": False, "enabled": True},
        ],
    }


def _matching_pub_spec_for_one_cycle():
    return {
        "preset_name": "CUSTOM",
        "timezone": "Asia/Kolkata",
        "overlap_policy": "STRICT",
        "slots": [
            {
                "slot_id": "solo_1",
                "label": "Solo Publish",
                "time_ist": "15:00",
                "prep_window_minutes": 60,
                "recovery_duration_minutes": 240,
                "recovery_offset_minutes": 0,
                "mapped_cycle_id": "solo_1",
                "enabled": True,
            }
        ],
    }


def _sync_active_profile_to_db():
    prof = schedule_profiles.get_active_profile()
    control.record_scheduler_sync(prof)


def test_01_default_four_cycles_daily_unified_state(isolated_unified_db):
    state = control.get_unified_schedule_state(now_ist=datetime(2026, 11, 12, 8, 0, tzinfo=IST))
    assert state["active_schedule_name"] == "FOUR_CYCLES_DAILY"
    assert len(state["production"]["active"]["cycles"]) == 4
    assert [c["start_time"] for c in state["production"]["active"]["cycles"]] == ["00:00", "06:00", "12:00", "18:00"]
    assert len(state["mappings"]) == 4
    assert state["lifecycle_state"] in {"ACTIVE", "RUNNING", "STALE"}
    assert "unified_timeline" in state
    assert "scheduler_sync" in state


def test_02_one_cycle_custom_schedule(isolated_unified_db):
    # Production scheduling is decoupled from publication: activating a production
    # schedule must not activate, require or derive any publication slot.
    prod = _one_cycle_prod_spec()
    pub = _matching_pub_spec_for_one_cycle()
    pub_active_before = control.get_control_center_state()["publication_schedule"]["active"]
    res = control.activate_unified_schedule(
        confirm=True,
        prod_spec=prod,
        pub_spec=pub,
        sync_scheduler_fn=_sync_active_profile_to_db,
    )
    assert res["status"] == "ACTIVE"
    assert res["is_synchronized"] is True
    assert res["state"]["active_schedule_name"] == "CUSTOM"
    cycles = res["state"]["production"]["active"]["cycles"]
    assert [c["cycle_id"] for c in cycles] == ["solo_1"]
    assert cycles[0]["start_time"] == "10:00"
    assert res["publication_result"] is None
    pub_active_after = control.get_control_center_state()["publication_schedule"]["active"]
    assert pub_active_after == pub_active_before
    assert all(s.get("slot_id") != "solo_1" for s in pub_active_after.get("slots") or [])


def test_03_five_cycle_custom_and_04_add_remove_cycles(isolated_unified_db):
    spec5 = _five_cycle_prod_spec()
    val5 = control.validate_schedule(spec5)
    assert val5["valid"] is True
    assert val5["cycle_count"] == 5

    spec4 = json.loads(json.dumps(spec5))
    spec4["cycles"] = [c for c in spec4["cycles"] if c["cycle_id"] != "cycle_3"]
    val4 = control.validate_schedule(spec4)
    assert val4["valid"] is True
    assert val4["cycle_count"] == 4


def test_05_prod_recovery_boundaries_120_and_240(isolated_unified_db):
    base = _one_cycle_prod_spec()["cycles"][0]
    ok_min = {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [{**base, "recovery_duration_minutes": 120, "target_publish_time": "14:00"}]}
    ok_max = {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [{**base, "recovery_duration_minutes": 240, "target_publish_time": "16:00"}]}
    assert control.validate_schedule(ok_min)["valid"] is True
    assert control.validate_schedule(ok_max)["valid"] is True

    # Recovery duration is configurable from 30 minutes to 24 hours.
    bad_min = {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [{**base, "recovery_duration_minutes": 29, "target_publish_time": "14:00"}]}
    bad_max = {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [{**base, "recovery_duration_minutes": 1441, "target_publish_time": "17:00"}]}
    with pytest.raises(ValueError, match="between 30 and 1440"):
        control.validate_schedule(bad_min)
    with pytest.raises(ValueError, match="between 30 and 1440"):
        control.validate_schedule(bad_max)


def test_06_production_schedule_has_no_publication_constraint(isolated_unified_db):
    # Production is decoupled from publication: a publication time is neither required
    # nor constrained, and a legacy value never shortens the recovery window.
    base = _one_cycle_prod_spec()["cycles"][0]
    legacy_close = {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [{**base, "target_publish_time": "14:30"}]}
    res = control.validate_schedule(legacy_close)
    assert res["valid"] is True
    assert res["cycles"][0]["target_publish_time"] == "14:30"  # preserved verbatim

    no_target = {k: v for k, v in base.items() if k not in ("target_publish_time", "next_day")}
    res2 = control.validate_schedule({"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [no_target]})
    assert res2["valid"] is True
    assert "target_publish_time" not in res2["cycles"][0]
    assert res2["preview_next_cycles"], "cycles without a publication time must still be scheduled"
    assert res2["preview_next_cycles"][0]["target_publish_ist"] is None
    assert any("no target publication time" in w for w in res2["warnings"])

    profile = control._profile_from_spec({**res2, "cycles": res2["cycles"]})
    assert profile.cycles[0].recovery_window_hours == 3.0  # 180 min, not clipped
    assert profile.cycles[0].window_hours == 3.0


def test_07_pub_prep_and_recovery_validation_and_overlap_policy(isolated_unified_db):
    overlapping_strict = {
        "preset_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "recovery_overlap_policy": "STRICT",
        "slots": [
            {"slot_id": "s1", "label": "S1", "time_ist": "10:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
            {"slot_id": "s2", "label": "S2", "time_ist": "13:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
        ],
    }
    res_strict = control.validate_publication_schedule(overlapping_strict)
    assert res_strict["valid"] is False
    assert len(res_strict["errors"]) > 0

    overlapping_shared = {**overlapping_strict, "recovery_overlap_policy": "SHARED_RECOVERY"}
    res_shared = control.validate_publication_schedule(overlapping_shared)
    assert res_shared["valid"] is True
    assert res_shared["recovery_overlap_policy"] == "SHARED_RECOVERY"


def test_08_midnight_crossing_prod_and_pub(isolated_unified_db):
    prod = {
        "profile_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "cycles": [
            {"cycle_id": "late", "name": "Late", "start_time": "22:00", "recovery_duration_minutes": 240, "target_publish_time": "04:00", "active_weekdays": [0, 1, 2, 3, 4, 5, 6], "next_day": True, "enabled": True},
        ],
    }
    res = control.validate_schedule(prod)
    assert res["valid"] is True
    assert res["cycles"][0]["next_day"] is True


def test_09_duplicate_ids_and_conflicting_times_rejected(isolated_unified_db):
    c1 = _one_cycle_prod_spec()["cycles"][0]
    dup_id = {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [c1, {**c1, "start_time": "16:00", "target_publish_time": "21:00"}]}
    with pytest.raises(ValueError, match="unique non-empty cycle_id"):
        control.validate_schedule(dup_id)

    same_time = {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": [c1, {**c1, "cycle_id": "solo_2", "target_publish_time": "16:00"}]}
    with pytest.raises(ValueError, match="duplicate or conflicting production start times"):
        control.validate_schedule(same_time)


def test_10_disabled_cycles_handled(isolated_unified_db):
    prod = _five_cycle_prod_spec()
    prod["cycles"][2]["enabled"] = False
    res = control.validate_schedule(prod)
    assert res["valid"] is True
    assert all(item["cycle_id"] != "cycle_3" for item in res["preview_next_cycles"])


def test_11_independent_publication_schedule_compatibility(isolated_unified_db):
    preset = control.preset_publication_schedule("FOUR_DAILY")
    assert preset["preset_name"] == "FOUR_DAILY"
    assert len(preset["slots"]) == 4
    control.save_publication_schedule_draft(preset)
    pub_state = control.get_publication_schedule()
    assert len(pub_state["draft"]["slots"]) == 4


def test_12_explicit_prod_to_pub_mapping_inherited_overridden_unmapped(isolated_unified_db):
    prod_cycles = [
        {"cycle_id": "cycle_1", "name": "C1", "start_time": "00:00", "recovery_duration_minutes": 240, "target_publish_time": "06:00", "enabled": True},
        {"cycle_id": "cycle_2", "name": "C2", "start_time": "06:00", "recovery_duration_minutes": 240, "target_publish_time": "12:00", "enabled": True},
        {"cycle_id": "cycle_3", "name": "C3", "start_time": "12:00", "recovery_duration_minutes": 240, "target_publish_time": "18:00", "enabled": True},
    ]
    pub_slots = [
        {"slot_id": "cycle_1", "label": "Slot 1", "time_ist": "06:00", "prep_window_minutes": 60, "recovery_duration_minutes": 240, "mapped_cycle_id": "cycle_1", "enabled": True},
        {"slot_id": "cycle_2", "label": "Slot 2", "time_ist": "13:30", "prep_window_minutes": 60, "recovery_duration_minutes": 240, "mapped_cycle_id": "cycle_2", "enabled": True},
        {"slot_id": "extra_slot", "label": "Extra", "time_ist": "22:00", "prep_window_minutes": 60, "recovery_duration_minutes": 240, "enabled": True},
    ]
    mappings, unmapped = control._map_cycles_to_publication_slots(prod_cycles, pub_slots)
    statuses = {m["cycle_id"]: m["mapping_mode"] for m in mappings}
    assert statuses == {"cycle_1": "inherited", "cycle_2": "overridden", "cycle_3": "unmapped"}
    assert [u["slot_id"] for u in unmapped] == ["extra_slot"]


def test_13_draft_vs_active_isolation(isolated_unified_db):
    draft_prod = _one_cycle_prod_spec()
    control.save_schedule_draft(draft_prod)
    state = control.get_unified_schedule_state()
    assert state["active_schedule_name"] == "FOUR_CYCLES_DAILY"
    assert len(state["production"]["active"]["cycles"]) == 4
    assert len(state["production"]["draft"]["cycles"]) == 1
    assert state["lifecycle_state"] in {"DRAFT", "STALE"}


def test_14_activation_persistence_and_15_scheduler_revision_sync_and_mismatch(isolated_unified_db):
    prod = _one_cycle_prod_spec()
    pub = _matching_pub_spec_for_one_cycle()

    # Successful sync
    res_ok = control.activate_unified_schedule(
        confirm=True,
        prod_spec=prod,
        pub_spec=pub,
        sync_scheduler_fn=_sync_active_profile_to_db,
    )
    assert res_ok["status"] == "ACTIVE"
    assert res_ok["is_synchronized"] is True
    assert res_ok["scheduler_loaded_revision"] == res_ok["active_revision"]

    # Mismatch / FAILED detection when scheduler loads stale revision
    stale_profile = schedule_profiles.ScheduleProfile(
        name="STALE",
        description="Stale test profile",
        timezone_name="Asia/Kolkata",
        cycles=[],
        schedule_version=1,
        revision="deadbeef1234",
    )
    res_fail = control.activate_unified_schedule(
        confirm=True,
        prod_spec=_five_cycle_prod_spec(),
        pub_spec=pub,
        sync_scheduler_fn=lambda: control.record_scheduler_sync(stale_profile),
    )
    assert res_fail["status"] == "FAILED"
    assert "synchronization failed" in res_fail["reason"].lower()
    state_after = control.get_unified_schedule_state()
    assert state_after["scheduler_sync"]["is_synchronized"] is False


def test_16_scheduler_restart_restores_active_version(isolated_unified_db):
    prod = _one_cycle_prod_spec()
    pub = _matching_pub_spec_for_one_cycle()
    res = control.activate_unified_schedule(
        confirm=True,
        prod_spec=prod,
        pub_spec=pub,
        sync_scheduler_fn=_sync_active_profile_to_db,
    )
    assert res["status"] == "ACTIVE"

    control.restore_active_schedule_profile()
    active_prof = schedule_profiles.get_active_profile()
    assert active_prof.name == "CUSTOM"
    assert len(active_prof.cycles) == 1
    scheduler_module.configure_scheduler_jobs()
    sync_state = control.get_scheduler_sync_state(active_prof.revision, active_prof.schedule_version)
    assert sync_state["is_synchronized"] is True
    assert sync_state["loaded_revision"] == active_prof.revision
    assert sync_state["cycles_count"] == 1


def test_17_existing_job_version_retention_and_18_publish_timestamp_retention(isolated_unified_db):
    SessionFactory = isolated_unified_db
    preserved_publish = datetime(2026, 11, 12, 18, 0, tzinfo=IST).isoformat()
    with SessionFactory() as db:
        job = JobModel(
            id="cycle_3_20261112_1200",
            date_str="2026-11-12",
            topic_title="Existing Job",
            status="COMPLETED",
            checkpoint_data_json=json.dumps({"cycle_id": "cycle_3", "schedule_version": 1, "schedule_revision": "rev_old_1"}),
            scheduled_publication_time=preserved_publish,
        )
        db.add(job)
        db.commit()

    res = control.activate_unified_schedule(
        confirm=True,
        prod_spec=_one_cycle_prod_spec(),
        pub_spec=_matching_pub_spec_for_one_cycle(),
        sync_scheduler_fn=_sync_active_profile_to_db,
    )
    assert res["status"] == "ACTIVE"

    with SessionFactory() as db:
        refreshed = db.query(JobModel).filter(JobModel.id == "cycle_3_20261112_1200").first()
        assert refreshed is not None
        meta = json.loads(refreshed.checkpoint_data_json)
        assert meta["schedule_version"] == 1
        assert meta["schedule_revision"] == "rev_old_1"
        assert refreshed.scheduled_publication_time == preserved_publish


def test_19_unified_timeline_ordering_and_job_lifecycle_accuracy(isolated_unified_db):
    SessionFactory = isolated_unified_db
    now = datetime(2026, 11, 12, 14, 0, tzinfo=IST)
    with SessionFactory() as db:
        db.add(
            JobModel(
                id="cycle_2_20261112_0600",
                date_str="2026-11-12",
                topic_title="Completed Topic",
                status="COMPLETED",
                youtube_video_id="yt_123",
            )
        )
        db.commit()

    state = control.get_unified_schedule_state(now_ist=now)
    timeline = state["unified_timeline"]
    # 4 production cycles + 4 unmapped standalone publication slots
    assert len(timeline) == 8
    # Each row distinguishes production recovery vs publication recovery and attaches job status
    row_c2 = next(r for r in timeline if r["cycle_id"] == "cycle_2")
    assert row_c2["associated_production_job_id"] == "cycle_2_20261112_0600"
    assert "production_recovery_deadline_ist" in row_c2
    assert "publication_recovery_deadline_ist" in row_c2
    # No duplicate cycle_id rows for the same cycle
    cycle_ids = [r["cycle_id"] for r in timeline if r["cycle_id"]]
    assert len(cycle_ids) == len(set(cycle_ids))


def test_20_unified_api_endpoints_and_failed_activation_reporting(isolated_unified_db):
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        resp_get = client.get("/api/control-center/unified-schedule")
        assert resp_get.status_code == 200
        payload = resp_get.json()
        assert payload["active_schedule_name"] == "FOUR_CYCLES_DAILY"
        assert len(payload["mappings"]) == 4

        # Invalid prod spec should fail with HTTP 400 or 409
        bad_prod = _one_cycle_prod_spec()
        bad_prod["cycles"][0]["recovery_duration_minutes"] = 20
        resp_bad = client.post(
            "/api/control-center/unified-schedule/activate",
            json={"confirm": True, "production_spec": bad_prod, "publication_spec": _matching_pub_spec_for_one_cycle()},
        )
        assert resp_bad.status_code in {400, 409}

        # Dashboard HTML contains production scheduler
        resp_dash = client.get("/dashboard")
        assert resp_dash.status_code == 200
        # The dashboard now uses the simple Production Schedule editor (/api/schedule);
        # the unified/publication-slot editor is no longer part of the active UI.
        assert "Production Schedule — Asia/Kolkata" in resp_dash.text
        for legacy in ("uniScheduleBody", "uniVisualTimeline", "uniCompactExecBody", "Pub Prep", "Unmapped"):
            assert legacy not in resp_dash.text


def test_21_unified_draft_validate_missing_and_duplicate_mapping(isolated_unified_db):
    # Publication slots are user-controlled and no longer part of production
    # validation: missing or duplicate slot mappings must not invalidate a valid
    # production schedule, while an invalid production schedule still fails.
    prod = _one_cycle_prod_spec()
    pub_missing = {
        "preset_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "recovery_overlap_policy": "STRICT",
        "slots": [],
    }
    res_missing = control.validate_unified_schedule(prod_spec=prod, pub_spec=pub_missing)
    assert res_missing["valid"] is True
    assert res_missing["errors"] == []
    assert res_missing["publication_validation"] is None
    assert res_missing["mappings"] == []
    assert res_missing["production_validation"]["cycle_count"] == 1

    bad_prod = _one_cycle_prod_spec()
    bad_prod["cycles"][0]["recovery_duration_minutes"] = 20
    res_bad = control.validate_unified_schedule(prod_spec=bad_prod, pub_spec=pub_missing)
    assert res_bad["valid"] is False
    assert any("between 30 and 1440" in e for e in res_bad["errors"])

    pub_dup = {
        "preset_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "recovery_overlap_policy": "SHARED_RECOVERY",
        "slots": [
            {"slot_id": "s1", "label": "S1", "time_ist": "15:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "mapped_cycle_id": "solo_1", "enabled": True},
            {"slot_id": "s2", "label": "S2", "time_ist": "19:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "mapped_cycle_id": "solo_1", "enabled": True},
        ],
    }
    res_dup = control.validate_unified_schedule(prod_spec=prod, pub_spec=pub_dup)
    assert res_dup["valid"] is True
    assert res_dup["errors"] == []
    assert res_dup["publication_validation"] is None

    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        pub_ok = {
            "preset_name": "CUSTOM",
            "timezone_name": "Asia/Kolkata",
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "recovery_overlap_policy": "STRICT",
            "slots": [
                {"slot_id": "pub_solo_1", "label": "Solo Publish", "time_ist": "15:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "mapped_cycle_id": "solo_1", "enabled": True},
            ],
        }
        r_draft = client.post("/api/control-center/unified-schedule/draft", json={"confirm": False, "production_spec": prod, "publication_spec": pub_ok})
        assert r_draft.status_code == 200
        assert r_draft.json()["status"] == "DRAFT_SAVED"

        r_val = client.post("/api/control-center/unified-schedule/validate", json={"confirm": False, "production_spec": prod, "publication_spec": pub_ok})
        assert r_val.status_code == 200
        assert r_val.json()["valid"] is True
        assert r_val.json()["successes"] == ["All production cycles and recovery windows are valid."]
