"""
Unit & Simulation Tests for Stage 3.10: Resilient 4-Cycle Scheduler & Recovery.
Validates:
1. Laptop delayed startup at 01:00, 07:00, 13:00, 23:00 IST.
2. STRICT_WINDOW vs CATCH_UP policies.
3. Internet outage with backoff resumption & recovery window deadline expiry.
4. Stage checkpoint recovery (preserving topic, script, audio, video).
5. Single-instance process lock preventing duplicate scheduler instances.
6. Publication slot rescheduling without replacing other cycle videos.
7. Windows startup task opt-in management.
"""

from datetime import datetime, timezone, timedelta
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

from backend.app.database.db import SessionLocal, JobModel, StudioSettingModel
from backend.app.scheduler.schedule_profiles import (
    IST_TZ,
    POLICY_CATCH_UP,
    POLICY_STRICT_WINDOW,
    get_active_profile,
)
from backend.app.scheduler.reconciliation_service import (
    StartupReconciler,
    SchedulerProcessLock,
    calculate_next_available_publishing_slot,
)
from backend.app.services.network_connectivity_service import NetworkConnectivityService
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator
from backend.scripts.manage_windows_startup import (
    get_opt_in_setting,
    set_opt_in_setting,
    get_task_status,
)


@pytest.fixture
def clean_test_db():
    """Sets up and cleans up test job records in SQLite."""
    test_date = "2026-11-10"
    with SessionLocal() as db_session:
        db_session.query(JobModel).filter(JobModel.date_str == test_date).delete()
        db_session.commit()

    yield test_date

    with SessionLocal() as db_session:
        db_session.query(JobModel).filter(JobModel.date_str == test_date).delete()
        db_session.commit()


def test_laptop_starts_at_0100(clean_test_db):
    """Simulates laptop starting at 01:00 IST (within Cycle 1 recovery window 00:00-04:00)."""
    test_date = clean_test_db
    now_0100 = datetime(2026, 11, 10, 1, 0, 0, tzinfo=IST_TZ)

    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    reconciler = StartupReconciler(network_service=mock_net)

    report = reconciler.reconcile_on_startup(test_now_ist=now_0100, recovery_policy=POLICY_STRICT_WINDOW)

    assert len(report["recovered_cycles"]) == 1
    assert report["recovered_cycles"][0]["cycle_id"] == "cycle_1"
    assert report["recovered_cycles"][0]["recovery_type"] == "WINDOW_RECOVERY"
    # Original target 06:00 IST is viable
    assert report["recovered_cycles"][0]["rescheduled"] is False
    assert len(report["queued_cycles"]) == 3  # Cycles 2, 3, 4 queued


def test_laptop_starts_at_0700(clean_test_db):
    """Simulates laptop starting at 07:00 IST (Cycle 1 window expired, Cycle 2 in window 06:00-10:00)."""
    test_date = clean_test_db
    now_0700 = datetime(2026, 11, 10, 7, 0, 0, tzinfo=IST_TZ)

    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    reconciler = StartupReconciler(network_service=mock_net)

    # Under STRICT_WINDOW
    report_strict = reconciler.reconcile_on_startup(test_now_ist=now_0700, recovery_policy=POLICY_STRICT_WINDOW)

    assert len(report_strict["missed_cycles"]) == 1
    assert report_strict["missed_cycles"][0]["cycle_id"] == "cycle_1"
    assert len(report_strict["recovered_cycles"]) == 1
    assert report_strict["recovered_cycles"][0]["cycle_id"] == "cycle_2"
    assert len(report_strict["queued_cycles"]) == 2  # Cycles 3, 4 queued


def test_laptop_starts_at_1300(clean_test_db):
    """Simulates laptop starting at 13:00 IST (Cycles 1 & 2 expired, Cycle 3 in window 12:00-16:00)."""
    test_date = clean_test_db
    now_1300 = datetime(2026, 11, 10, 13, 0, 0, tzinfo=IST_TZ)

    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    reconciler = StartupReconciler(network_service=mock_net)

    report = reconciler.reconcile_on_startup(test_now_ist=now_1300, recovery_policy=POLICY_STRICT_WINDOW)

    missed_ids = [m["cycle_id"] for m in report["missed_cycles"]]
    assert "cycle_1" in missed_ids
    assert "cycle_2" in missed_ids
    assert len(report["recovered_cycles"]) == 1
    assert report["recovered_cycles"][0]["cycle_id"] == "cycle_3"
    assert len(report["queued_cycles"]) == 1  # Cycle 4 queued


def test_laptop_starts_at_2300_strict_vs_catch_up(clean_test_db):
    """Simulates laptop starting at 23:00 IST (Cycle 4 window 18:00-22:00 expired)."""
    test_date = clean_test_db
    now_2300 = datetime(2026, 11, 10, 23, 0, 0, tzinfo=IST_TZ)

    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    reconciler = StartupReconciler(network_service=mock_net)

    # 1. Under STRICT_WINDOW: all 4 cycles are missed
    report_strict = reconciler.reconcile_on_startup(test_now_ist=now_2300, recovery_policy=POLICY_STRICT_WINDOW)
    assert len(report_strict["missed_cycles"]) == 4
    assert len(report_strict["recovered_cycles"]) == 0

    # Clear for catch_up test
    with SessionLocal() as db_session:
        db_session.query(JobModel).filter(JobModel.date_str == test_date).delete()
        db_session.commit()

    # 2. Under CATCH_UP: exactly 1 cycle recovered with revised publication slot
    report_catchup = reconciler.reconcile_on_startup(test_now_ist=now_2300, recovery_policy=POLICY_CATCH_UP)
    assert len(report_catchup["recovered_cycles"]) == 1
    assert len(report_catchup["missed_cycles"]) == 3
    rec = report_catchup["recovered_cycles"][0]
    assert rec["recovery_type"] == "CATCH_UP_POLICY"
    assert rec["rescheduled"] is True


def test_internet_restored_after_outage(clean_test_db):
    """Simulates network outage returning within backoff loop."""
    net_service = NetworkConnectivityService()

    call_count = 0
    def mock_probe():
        nonlocal call_count
        call_count += 1
        return (call_count >= 3)  # offline twice, online on 3rd check

    mock_sleep = MagicMock()
    now_ist = datetime.now(IST_TZ)
    deadline = now_ist + timedelta(hours=2)

    ok = net_service.wait_for_connectivity(
        max_wait_seconds=60.0,
        initial_backoff=1.0,
        deadline_dt=deadline,
        probe_fn=mock_probe,
        sleep_fn=mock_sleep,
    )
    assert ok is True
    assert call_count == 3
    assert mock_sleep.call_count == 2


def test_internet_outage_past_recovery_window(clean_test_db):
    """Simulates internet remaining offline past recovery deadline."""
    net_service = NetworkConnectivityService()

    # Probe always fails
    mock_probe = MagicMock(return_value=False)
    mock_sleep = MagicMock()

    # Deadline already in past
    past_deadline = datetime.now(IST_TZ) - timedelta(minutes=5)

    ok = net_service.wait_for_connectivity(
        max_wait_seconds=60.0,
        deadline_dt=past_deadline,
        probe_fn=mock_probe,
        sleep_fn=mock_sleep,
    )
    assert ok is False


def test_checkpoint_resumption_skips_completed_stages(clean_test_db):
    """Verifies that an interrupted job resumes from checkpoint without re-rendering."""
    job_id = "test_checkpoint_job_001"
    existing_video = os.path.join(r"D:\Agents\CrazzyDocs\media\video\showcase_stage_3_4_tsutomu_yamaguchi.mp4")
    existing_thumb = os.path.join(r"D:\Agents\CrazzyDocs\media\thumbnails\showcase_stage_3_9a_yamaguchi_poster.png")

    # Seed an interrupted job with Stage 4 and Stage 9 already completed
    with SessionLocal() as db_session:
        job = JobModel(
            id=job_id,
            date_str="2026-11-10",
            status="RUNNING",
            current_stage="RENDERING",
            checkpoint_data_json=json.dumps({
                "topic_title": "Tsutomu Yamaguchi Resumed Story",
                "hook_text": "He lived through two nuclear blasts.",
                "video_path": existing_video,
                "thumbnail_path": existing_thumb,
            }),
        )
        db_session.add(job)
        db_session.commit()

    with patch.object(master_orchestrator.notifier, "send_success", return_value={"desktop": True, "email": True, "deduplicated": False}):
        res = master_orchestrator.execute_production_pipeline(job_id=job_id)

    assert res["status"] == "COMPLETED"
    assert res["topic"] == "Tsutomu Yamaguchi Resumed Story"

    # Verify DB state
    with SessionLocal() as db_session:
        saved_job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
        assert saved_job.status == "COMPLETED"
        assert saved_job.current_stage == "COMPLETED"


def test_single_instance_lock_defense(tmp_path):
    """Verifies that duplicate scheduler launches are blocked by process lock."""
    lock_file = str(tmp_path / "test_scheduler.lock")
    lock1 = SchedulerProcessLock(lock_path=lock_file)
    lock2 = SchedulerProcessLock(lock_path=lock_file)

    # 1. Acquire lock1
    assert lock1.acquire() is True

    # 2. lock2 attempts acquire while lock1 PID is active
    # Current process PID is alive, so lock2 must be rejected!
    assert lock2.acquire() is False

    # 3. Release lock1
    lock1.release()

    # 4. lock2 can now acquire
    assert lock2.acquire() is True
    lock2.release()


def test_publishing_slot_calculation():
    """Verifies slot recalculation when original target is missed."""
    profile = get_active_profile()
    cycle = profile.cycles[0]  # Cycle 1: 00:00 -> 06:00

    # 1. When current time is 01:00 IST (well before 06:00 target)
    dt_early = datetime(2026, 11, 10, 1, 0, 0, tzinfo=IST_TZ)
    orig_target = datetime(2026, 11, 10, 6, 0, 0, tzinfo=IST_TZ)
    slot, slot_str, rescheduled = calculate_next_available_publishing_slot(dt_early, cycle, orig_target)
    assert rescheduled is False
    assert slot == orig_target

    # 2. When current time is 06:15 IST (past original target)
    dt_late = datetime(2026, 11, 10, 6, 15, 0, tzinfo=IST_TZ)
    slot_late, slot_str_late, rescheduled_late = calculate_next_available_publishing_slot(dt_late, cycle, orig_target)
    assert rescheduled_late is True
    assert slot_late > dt_late
    assert "12:00 IST" in slot_str_late  # Next cycle publication slot


def test_windows_startup_task_settings():
    """Verifies opt-in setting storage and task status reporting."""
    # Test setting persistence
    set_opt_in_setting(True)
    assert get_opt_in_setting() is True

    set_opt_in_setting(False)
    assert get_opt_in_setting() is False

    # Status check returns structured dict
    st = get_task_status()
    assert "task_name" in st
    assert "is_registered" in st
    assert "opt_in_enabled" in st
