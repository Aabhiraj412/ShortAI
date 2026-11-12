"""Automated tests for Schedule Propagation, Dashboard Clarification & Publication Slot Recovery Windows."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import control_center_service
from backend.app.services.youtube_service import YouTubePublisherService


def test_all_predefined_presets_have_valid_recovery_windows():
    for name in ("FOLLOW_PRODUCTION_CYCLE", "ONCE_DAILY", "TWICE_DAILY", "THREE_DAILY", "FOUR_DAILY"):
        spec = control_center_service.preset_publication_schedule(name)
        res = control_center_service.validate_publication_schedule(spec)
        assert res["valid"] is True, f"Preset {name} failed: {res['errors']}"
        assert len(res["preview_next_slots"]) == 5
        for slot in spec["slots"]:
            assert slot["recovery_duration_minutes"] >= 15
            assert slot["prep_window_minutes"] >= 0


def test_custom_multiple_slots_and_midnight_crossing_windows():
    tz = ZoneInfo("Asia/Kolkata")
    spec = {
        "preset_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "missed_slot_behavior": "next_slot",
        "min_prep_buffer_minutes": 10,
        "wait_for_slot": True,
        "slots": [
            {"slot_id": "c1", "label": "Early", "time_ist": "05:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
            {"slot_id": "c2", "label": "Midday", "time_ist": "11:30", "prep_window_minutes": 30, "recovery_duration_minutes": 180, "enabled": True},
            {"slot_id": "c3", "label": "Afternoon", "time_ist": "15:00", "prep_window_minutes": 20, "recovery_duration_minutes": 120, "enabled": True},
            {"slot_id": "c4", "label": "Evening", "time_ist": "17:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
            {"slot_id": "c5", "label": "Night", "time_ist": "23:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
        ],
    }
    res = control_center_service.validate_publication_schedule(spec)
    assert res["valid"] is True, res["errors"]

    win = control_center_service.compute_slot_windows(spec["slots"][4], datetime(2026, 11, 12, tzinfo=tz).date(), tz)
    assert win["crosses_midnight"] is True
    assert win["recovery_start_ist"] == "2026-11-12 23:30 IST"
    assert win["recovery_deadline_ist"] == "2026-11-13 03:30 IST"
    assert win["recovery_deadline_utc"] == "2026-11-12T22:00:00Z"


def test_overlapping_recovery_windows_and_missing_duration_rejected():
    bad_overlap = {
        "preset_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "missed_slot_behavior": "next_slot",
        "min_prep_buffer_minutes": 10,
        "wait_for_slot": True,
        "slots": [
            {"slot_id": "s1", "label": "Slot A", "time_ist": "15:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
            {"slot_id": "s2", "label": "Slot B", "time_ist": "17:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
        ],
    }
    res_ov = control_center_service.validate_publication_schedule(bad_overlap)
    assert res_ov["valid"] is False
    assert any("overlap" in e.lower() for e in res_ov["errors"])

    bad_missing = {
        "preset_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "weekdays": ["Mon"],
        "slots": [{"slot_id": "s1", "label": "Missing Dur", "time_ist": "15:00", "enabled": True}],
    }
    res_miss = control_center_service.validate_publication_schedule(bad_missing)
    assert res_miss["valid"] is False
    assert any("recovery_duration_minutes" in e for e in res_miss["errors"])


def test_disabled_slots_skipped_and_production_recovery_independent():
    cycles_before = control_center_service.get_live_cycle_schedule_status(
        datetime(2026, 11, 12, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    )
    assert all(c["recovery_window_hours"] == 4 for c in cycles_before["cycles"])

    spec = control_center_service.preset_publication_schedule("TWICE_DAILY")
    spec["slots"][0]["enabled"] = False
    slots = control_center_service.compute_upcoming_publication_slots(
        spec, count=2, now_ist=datetime(2026, 11, 12, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    )
    assert all(s["slot_id"] == "pub_2" for s in slots)

    cycles_after = control_center_service.get_live_cycle_schedule_status(
        datetime(2026, 11, 12, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    )
    assert cycles_before["cycles"][2]["target_publish_ist"] == cycles_after["cycles"][2]["target_publish_ist"]
    assert all(c["recovery_window_hours"] == 4 for c in cycles_after["cycles"])


def test_schedule_propagation_and_existing_upload_retention():
    record = {
        "id": 999,
        "topic_title": "Test Propagation",
        "upload_status": "publication_scheduled",
        "visibility": "private",
        "youtube_video_id": "vid_999",
        "scheduled_timestamp": "2026-11-12T09:30:00Z",
        "upload_timestamp": "2026-11-12T08:00:00Z",
        "metadata_json": json.dumps({
            "title": "Test Propagation",
            "_schedule_context": {
                "slot_id": "pub_custom_1",
                "slot_label": "Custom 15:00",
                "schedule_version": 3,
                "scheduled_ist_display": "2026-11-12 15:00 IST",
                "scheduled_utc_iso": "2026-11-12T09:30:00Z",
            },
        }),
        "upload_state": {"verification_status": "verified"},
    }
    safe = YouTubePublisherService._safe_publication(record)
    assert safe["scheduled_ist_display"] == "2026-11-12 15:00 IST"
    assert safe["scheduled_utc_publish_at"] == "2026-11-12T09:30:00Z"
    # Publication slots no longer exist; a legacy row's slot context is preserved
    # verbatim and read-only, never as live slot fields.
    assert "slot_id" not in safe
    assert safe["legacy_schedule_context"]["slot_id"] == "pub_custom_1"
    assert safe["legacy_schedule_context"]["schedule_version"] == 3
    assert safe["youtube_privacy_status"] == "private"
    assert safe["youtube_scheduled_state"] == "PUBLICATION_SCHEDULED"


def test_recovery_timeline_lifecycle_states_and_active_upload_protection():
    fake_sched = {
        "active": {
            "schedule_version": 2,
            "preset_name": "CUSTOM",
            "timezone_name": "Asia/Kolkata",
            "recovery_overlap_policy": "SHARED_RECOVERY",
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "slots": [
                {"slot_id": "s_exp", "label": "Expired", "time_ist": "06:00", "prep_window_minutes": 30, "recovery_duration_minutes": 120, "enabled": True},
                {"slot_id": "s_act", "label": "Active Upload", "time_ist": "12:00", "prep_window_minutes": 30, "recovery_duration_minutes": 120, "enabled": True},
                {"slot_id": "s_rec", "label": "Recovering", "time_ist": "14:30", "prep_window_minutes": 30, "recovery_duration_minutes": 120, "enabled": True},
                {"slot_id": "s_skip", "label": "Disabled", "time_ist": "18:00", "prep_window_minutes": 30, "recovery_duration_minutes": 120, "enabled": False},
                {"slot_id": "s_up", "label": "Upcoming", "time_ist": "22:00", "prep_window_minutes": 30, "recovery_duration_minutes": 120, "enabled": True},
            ],
        }
    }
    fake_pubs = [
        {"id": 9, "topic_title": "Expired Failed", "upload_status": "failed", "scheduled_timestamp": "2026-11-12T00:30:00Z"},
        {"id": 10, "topic_title": "Uploading Now", "upload_status": "uploading", "scheduled_timestamp": "2026-11-12T06:30:00Z"},
        {"id": 11, "topic_title": "Recovering Failed", "upload_status": "failed", "scheduled_timestamp": "2026-11-12T09:00:00Z"},
    ]
    with patch.object(control_center_service, "get_publication_schedule", return_value=fake_sched):
        with patch("backend.app.services.youtube_publishing_engine.youtube_publishing_engine.db.list_publications", return_value=fake_pubs):
            tl = control_center_service.get_publication_recovery_timeline(
                now_ist=datetime(2026, 11, 12, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
            )
    by_id = {s["slot_id"]: s for s in tl["slots"]}
    assert by_id["s_exp"]["status"] == "RECOVERY_WINDOW_EXPIRED"
    assert by_id["s_act"]["status"] == "PUBLISHING"  # Active upload protected even past 14:00 deadline!
    assert by_id["s_rec"]["status"] == "RECOVERING"
    assert by_id["s_skip"]["status"] == "SKIPPED"
    assert by_id["s_up"]["status"] == "UPCOMING"


def test_strict_vs_shared_recovery_overlap_policies_and_4h_with_2h_spacing():
    base_slots = [
        {"slot_id": "s1", "label": "Custom Slot 5", "time_ist": "15:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
        {"slot_id": "s2", "label": "Evening", "time_ist": "17:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
    ]
    strict_spec = {
        "preset_name": "CUSTOM",
        "timezone_name": "Asia/Kolkata",
        "recovery_overlap_policy": "STRICT",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "slots": base_slots,
    }
    res_strict = control_center_service.validate_publication_schedule(strict_spec)
    assert res_strict["valid"] is False
    assert len(res_strict["overlap_details"]) == 1
    ov = res_strict["overlap_details"][0]
    assert ov["overlap_minutes"] == 120
    assert ov["slot_a_id"] == "s1" and ov["slot_b_id"] == "s2"
    assert "Suggested" in ov["suggestion"] or "Reduce" in ov["suggestion"]

    shared_spec = dict(strict_spec, recovery_overlap_policy="SHARED_RECOVERY")
    res_shared = control_center_service.validate_publication_schedule(shared_spec)
    assert res_shared["valid"] is True
    assert len(res_shared["warnings"]) >= 1
    assert len(res_shared["overlap_details"]) == 1


def test_chronological_slot_ordering_across_schedule_and_timeline():
    # Custom slot pub_5 at 15:30 inserted last in array must sort between 11:30 and 17:30
    unsorted_slots = [
        {"slot_id": "pub_4", "label": "Late Night", "time_ist": "23:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
        {"slot_id": "pub_1", "label": "Morning", "time_ist": "05:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
        {"slot_id": "pub_3", "label": "Evening", "time_ist": "17:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
        {"slot_id": "pub_2", "label": "Midday", "time_ist": "11:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
        {"slot_id": "pub_5", "label": "Custom Slot 5", "time_ist": "15:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
    ]
    fake_sched = {
        "active": {
            "schedule_version": 4,
            "preset_name": "CUSTOM",
            "timezone_name": "Asia/Kolkata",
            "recovery_overlap_policy": "SHARED_RECOVERY",
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "slots": unsorted_slots,
        }
    }
    with patch.object(control_center_service, "get_publication_schedule", return_value=fake_sched):
        with patch("backend.app.services.youtube_publishing_engine.youtube_publishing_engine.db.list_publications", return_value=[]):
            tl = control_center_service.get_publication_recovery_timeline(
                now_ist=datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
            )
    ordered_ids = [s["slot_id"] for s in tl["slots"]]
    assert ordered_ids == ["pub_1", "pub_2", "pub_5", "pub_3", "pub_4"]


def _fake_session_factory(job_rows):
    class _FakeQuery:
        def order_by(self, *args, **kwargs):
            return self
        def limit(self, *args, **kwargs):
            return self
        def all(self):
            return job_rows

    class _FakeSession:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def query(self, *args, **kwargs):
            return _FakeQuery()

    return lambda: _FakeSession()


def test_job_to_slot_association_and_no_broadcast():
    from types import SimpleNamespace
    fake_sched = {
        "active": {
            "schedule_version": 2,
            "preset_name": "TWICE_DAILY",
            "timezone_name": "Asia/Kolkata",
            "recovery_overlap_policy": "STRICT",
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "slots": [
                {"slot_id": "pub_1", "label": "Morning", "time_ist": "09:00", "prep_window_minutes": 30, "recovery_duration_minutes": 180, "enabled": True},
                {"slot_id": "pub_2", "label": "Evening", "time_ist": "18:00", "prep_window_minutes": 30, "recovery_duration_minutes": 180, "enabled": True},
            ],
        }
    }
    # Only pub_2 has an associated publication; pub_1 must NOT inherit it!
    fake_pubs = [
        {
            "id": 77,
            "topic_title": "Only Evening Topic",
            "upload_status": "publication_scheduled",
            "scheduled_timestamp": "2026-11-12T12:30:00Z",
            "metadata_json": json.dumps({"_schedule_context": {"slot_id": "pub_2", "scheduled_utc_iso": "2026-11-12T12:30:00Z"}}),
        }
    ]
    fake_jobs = [
        SimpleNamespace(
            id="cycle_3_20261112_1200",
            topic_title="Only Evening Topic",
            status="COMPLETED",
            checkpoint_data_json=json.dumps({"publication_slot_id": "pub_2", "scheduled_utc_iso": "2026-11-12T12:30:00Z"}),
        )
    ]
    with patch.object(control_center_service, "get_publication_schedule", return_value=fake_sched):
        with patch("backend.app.services.youtube_publishing_engine.youtube_publishing_engine.db.list_publications", return_value=fake_pubs):
            with patch.object(control_center_service, "SessionLocal", _fake_session_factory(fake_jobs)):
                tl = control_center_service.get_publication_recovery_timeline(
                    now_ist=datetime(2026, 11, 12, 13, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
                )
    by_id = {s["slot_id"]: s for s in tl["slots"]}
    assert by_id["pub_1"]["associated_job_id"] is None
    assert by_id["pub_1"]["associated_ledger_id"] is None
    assert by_id["pub_2"]["associated_job_id"] == "cycle_3_20261112_1200"
    assert by_id["pub_2"]["associated_ledger_id"] == 77


def test_open_recovery_window_without_failed_job_never_recovering():
    fake_sched = {
        "active": {
            "schedule_version": 2,
            "preset_name": "CUSTOM",
            "timezone_name": "Asia/Kolkata",
            "recovery_overlap_policy": "STRICT",
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "slots": [
                {"slot_id": "pub_open", "label": "Open Window No Job", "time_ist": "14:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "enabled": True},
            ],
        }
    }
    with patch.object(control_center_service, "get_publication_schedule", return_value=fake_sched):
        with patch("backend.app.services.youtube_publishing_engine.youtube_publishing_engine.db.list_publications", return_value=[]):
            with patch.object(control_center_service, "SessionLocal", _fake_session_factory([])):
                tl = control_center_service.get_publication_recovery_timeline(
                    now_ist=datetime(2026, 11, 12, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
                )
    assert tl["slots"][0]["status"] != "RECOVERING"
    assert tl["slots"][0]["status"] == "UPCOMING"
    assert tl["recovery_worker_status"] == "IDLE"


def test_validation_state_invalidation_and_draft_active_isolation():
    store: dict[str, str] = {}

    def fake_read(key: str):
        return store.get(key)

    def fake_write(updates):
        for k, v in updates.items():
            if v is None:
                store.pop(k, None)
            else:
                store[k] = v

    with patch.object(control_center_service, "_read_setting", side_effect=fake_read), \
         patch.object(control_center_service, "_write_settings", side_effect=fake_write):
        active_before = control_center_service.get_publication_schedule()["active"]
        draft_spec = control_center_service.preset_publication_schedule("TWICE_DAILY")
        saved = control_center_service.save_publication_schedule_draft(draft_spec)
        assert saved["draft_validation"]["stale"] is True

        # Activating without validating current revision must fail!
        with pytest.raises(ValueError, match="must be validated"):
            control_center_service.activate_publication_schedule_draft(confirm=True)

        # Validate draft -> valid
        val = control_center_service.validate_saved_publication_schedule_draft(draft_spec)
        assert val["valid"] is True
        assert val["draft_validation"]["stale"] is False

        # Active schedule must remain untouched while only draft exists
        assert control_center_service.get_publication_schedule()["active"]["preset_name"] == active_before["preset_name"]

        # Edit draft -> validation becomes STALE again!
        edited = json.loads(json.dumps(draft_spec))
        edited["slots"][0]["time_ist"] = "08:45"
        saved2 = control_center_service.save_publication_schedule_draft(edited)
        assert saved2["draft_validation"]["stale"] is True
        assert saved2["draft_validation"]["draft_revision"] != val["revision"]


def test_concurrent_recovery_claim_guard_and_resource_awareness():
    control_center_service._ACTIVE_RECOVERY_CLAIMS.clear()
    try:
        with patch.object(control_center_service, "production_capacity", return_value={"max_concurrency": 1, "active_jobs": 0}):
            assert control_center_service.claim_recovery_operation(101, "pub_1") is True
            # Same or second claim while concurrency=1 is occupied must be rejected
            assert control_center_service.claim_recovery_operation(101, "pub_1") is False
            assert control_center_service.claim_recovery_operation(102, "pub_2") is False
            control_center_service.release_recovery_operation(101)
            assert control_center_service.claim_recovery_operation(102, "pub_2") is True
    finally:
        control_center_service._ACTIVE_RECOVERY_CLAIMS.clear()


def test_dashboard_and_publication_recovery_endpoints():
    # Control-center routes are local-only; call them as the local dashboard does.
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r_dash = client.get("/dashboard")
    assert r_dash.status_code == 200
    assert "Original Cycle Target" in r_dash.text
    assert "Execution Monitoring" in r_dash.text
    assert "Recovery Overlap Policy" in r_dash.text

    r_rec = client.get("/api/control-center/publication-recovery")
    assert r_rec.status_code == 200
    data = r_rec.json()
    assert "slots" in data
    assert "schedule_version" in data
    assert "recovery_worker_status" in data
    assert "recovery_overlap_policy" in data
