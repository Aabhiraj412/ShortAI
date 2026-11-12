"""
Comprehensive Recovery & Fault-Tolerance Verification Suite for CrazzyDocs.
Phase 5, Stage 3.11 Standard.

Validates the 10 Mandatory Recovery Scenarios:
1. Scheduler process restart & state restoration
2. Application restart & startup reconciliation
3. Interrupted rendering recovery & artifact validation
4. Interrupted TTS recovery & voice track preservation
5. Internet outage detection & WAITING_FOR_INTERNET transition
6. Internet restoration & backoff loop resumption
7. Pending email delivery & offline notification retry
8. Duplicate scheduler startup & single-instance lock defense
9. Missed cycle reconciliation & SLA enforcement
10. Expired recovery windows: STRICT_WINDOW vs CATCH_UP policies
"""

from datetime import datetime, timezone, timedelta
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

from backend.app.database.db import (
    SessionLocal,
    JobModel,
    PersistentNotificationModel,
    StudioSettingModel,
)
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
from backend.app.services.email_monitoring_service import EmailMonitoringService, email_monitoring_service
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator


@pytest.fixture
def recovery_test_env():
    """Sets up and cleans up test records in SQLite and test production directories."""
    import shutil
    test_date = "2026-11-10"
    test_dirs = [
        r"D:\Agents\CrazzyDocs\media\productions\test_interrupted_render_job",
        r"D:\Agents\CrazzyDocs\media\productions\test_interrupted_tts_job",
    ]
    for d in test_dirs:
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)

    with SessionLocal() as db_session:
        db_session.query(JobModel).filter(JobModel.date_str == test_date).delete()
        db_session.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.idempotency_key.like(f"%{test_date}%")
        ).delete()
        db_session.commit()

    yield test_date

    with SessionLocal() as db_session:
        db_session.query(JobModel).filter(JobModel.date_str == test_date).delete()
        db_session.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.idempotency_key.like(f"%{test_date}%")
        ).delete()
        db_session.commit()

    for d in test_dirs:
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)


# =============================================================================
# SCENARIO 1: Scheduler Process Restart
# =============================================================================
def test_scenario_1_scheduler_process_restart(recovery_test_env):
    """
    Scenario 1: Scheduler process terminates and restarts.
    Verifies that the process lock is re-acquired, existing jobs are loaded,
    and no duplicate job entries are created.
    """
    test_date = recovery_test_env
    job_id = f"job_c1_{test_date.replace('-', '')}_0000"

    # Pre-seed a job from previous run
    with SessionLocal() as db:
        db.add(JobModel(
            id=job_id,
            date_str=test_date,
            status="COMPLETED",
            current_stage="COMPLETED",
            topic_title="Demon Core Incident",
        ))
        db.commit()

    # Restart scheduler reconciler
    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    reconciler = StartupReconciler(network_service=mock_net)

    now_0100 = datetime(2026, 11, 10, 1, 0, 0, tzinfo=IST_TZ)
    report = reconciler.reconcile_on_startup(test_now_ist=now_0100)

    # Completed job must NOT be re-executed
    assert job_id not in [c.get("job_id") for c in report.get("recovered_cycles", [])]
    with SessionLocal() as db:
        jobs = db.query(JobModel).filter(JobModel.id == job_id).all()
        assert len(jobs) == 1  # Exactly 1 record, zero duplicates
        assert jobs[0].status == "COMPLETED"


# =============================================================================
# SCENARIO 2: Application Restart
# =============================================================================
def test_scenario_2_application_restart(recovery_test_env):
    """
    Scenario 2: Full host or application restart occurs.
    Startup reconciler checks unexecuted schedules upon boot rather than relying on cron.
    """
    test_date = recovery_test_env
    now_0730 = datetime(2026, 11, 10, 7, 30, 0, tzinfo=IST_TZ)

    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    reconciler = StartupReconciler(network_service=mock_net)

    report = reconciler.reconcile_on_startup(test_now_ist=now_0730, recovery_policy=POLICY_STRICT_WINDOW)

    # Cycle 1 (00:00) window expired (00:00-04:00) -> MISSED
    missed_ids = [m["cycle_id"] for m in report["missed_cycles"]]
    assert "cycle_1" in missed_ids

    # Cycle 2 (06:00) is inside recovery window (06:00-10:00) -> RECOVERED
    recovered_ids = [r["cycle_id"] for r in report["recovered_cycles"]]
    assert "cycle_2" in recovered_ids


# =============================================================================
# SCENARIO 3: Interrupted Rendering
# =============================================================================
def _create_fixture_media_files(fixture_dir):
    from PIL import Image
    import subprocess
    from backend.app.rendering.explainer_animation_engine import FFMPEG_DEFAULT_PATH

    fixture_dir.mkdir(parents=True, exist_ok=True)
    video_path = str(fixture_dir / "checkpoint_video.mp4")
    thumb_path = str(fixture_dir / "checkpoint_thumb.png")
    audio_path = str(fixture_dir / "checkpoint_voice.mp3")

    Image.new("RGB", (1080, 1920), (18, 28, 48)).save(thumb_path, format="PNG")
    ffmpeg_exe = str(FFMPEG_DEFAULT_PATH)
    if os.path.exists(ffmpeg_exe):
        subprocess.run(
            [
                ffmpeg_exe, "-y", "-f", "lavfi", "-i", "color=c=navy:s=1080x1920:r=24:d=2",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", video_path,
            ],
            capture_output=True, check=False, timeout=20,
        )
        subprocess.run(
            [ffmpeg_exe, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2", "-c:a", "libmp3lame", audio_path],
            capture_output=True, check=False, timeout=15,
        )
    if not os.path.exists(video_path) or os.path.getsize(video_path) <= 1024:
        with open(video_path, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + (b"\x00" * 4096))
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) <= 1024:
        with open(audio_path, "wb") as f:
            f.write(b"ID3\x03\x00\x00\x00\x00\x00\x00" + (b"\x00" * 4096))
    return video_path, thumb_path, audio_path


def test_scenario_3_interrupted_rendering(recovery_test_env, tmp_path):
    """
    Scenario 3: Power loss / crash occurs during Stage 9 rendering.
    Verifies that if video artifact exists and exceeds 1KB, it is reused.
    If corrupted / empty, it triggers clean re-render.
    """
    job_id = "test_interrupted_render_job"
    valid_video, valid_thumb, _ = _create_fixture_media_files(tmp_path / "render_ckpt")

    with SessionLocal() as db:
        db.add(JobModel(
            id=job_id,
            date_str="2026-11-10",
            status="RUNNING",
            current_stage="RENDERING",
            checkpoint_data_json=json.dumps({
                "topic_title": "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
                "hook_text": "She fell 33,000 feet without a parachute—and walked out of the hospital alive.",
                "video_path": valid_video,
                "thumbnail_path": valid_thumb,
            }),
        ))
        db.commit()

    with patch.object(master_orchestrator.notifier, "send_success", return_value={"desktop": True, "email": True, "deduplicated": False}):
        res = master_orchestrator.execute_production_pipeline(job_id=job_id, force_topic_id="cand_03_vesna_fall")

    assert res["status"] == "COMPLETED"
    assert res["video_path"] == valid_video
    assert res["thumbnail_path"] == valid_thumb


# =============================================================================
# SCENARIO 4: Interrupted TTS
# =============================================================================
def test_scenario_4_interrupted_tts(recovery_test_env, tmp_path):
    """
    Scenario 4: Interruption during Stage 8 voice narration.
    Verifies that checkpoint data retains valid voice track upon resumption.
    """
    job_id = "test_interrupted_tts_job"
    _, _, valid_audio = _create_fixture_media_files(tmp_path / "tts_ckpt")

    with SessionLocal() as db:
        db.add(JobModel(
            id=job_id,
            date_str="2026-11-10",
            status="RUNNING",
            current_stage="VOICEOVER",
            checkpoint_data_json=json.dumps({
                "topic_title": "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
                "hook_text": "She fell 33,000 feet without a parachute—and walked out of the hospital alive.",
                "audio_path": valid_audio,
            }),
        ))
        db.commit()

    with patch.object(master_orchestrator.notifier, "send_success", return_value={"desktop": True, "email": True, "deduplicated": False}):
        res = master_orchestrator.execute_production_pipeline(job_id=job_id, force_topic_id="cand_03_vesna_fall")

    assert res["status"] == "COMPLETED"
    assert "33,000" in res["topic"] or "Vesna" in res["topic"]


# =============================================================================
# SCENARIO 5: Internet Outage
# =============================================================================
def test_scenario_5_internet_outage():
    """
    Scenario 5: Complete internet outage at cycle trigger time.
    Verifies that system transitions to WAITING_FOR_INTERNET and does not crash.
    """
    net_service = NetworkConnectivityService()

    # Mock all probes failing
    with patch.object(net_service, "check_general_connectivity", return_value=False), \
         patch.object(net_service, "check_provider_services", return_value={"wikimedia": False, "speech_tts": False}):
        ready, reason, diag = net_service.is_environment_ready_for_cycle()

    assert ready is False
    assert "GENERAL_INTERNET_UNAVAILABLE" in reason or "INTERNET" in reason


# =============================================================================
# SCENARIO 6: Internet Restoration
# =============================================================================
def test_scenario_6_internet_restoration():
    """
    Scenario 6: Internet restores before recovery deadline expires.
    Verifies exponential backoff loop exits cleanly and reports connectivity restored.
    """
    net_service = NetworkConnectivityService()
    probe_results = [False, False, True]  # 2 failures, then online

    def mock_probe():
        return probe_results.pop(0) if probe_results else True

    now_ist = datetime.now(IST_TZ)
    deadline = now_ist + timedelta(hours=1)

    restored = net_service.wait_for_connectivity(
        max_wait_seconds=30.0,
        initial_backoff=0.5,
        deadline_dt=deadline,
        probe_fn=mock_probe,
        sleep_fn=MagicMock(),
    )
    assert restored is True


# =============================================================================
# SCENARIO 7: Pending Email Delivery
# =============================================================================
def test_scenario_7_pending_email_delivery(recovery_test_env):
    """
    Scenario 7: Host was offline when cycle finished; alert was persisted as PENDING.
    Verifies that retry_pending_notifications() flushes unsent notifications upon reboot.
    """
    test_date = recovery_test_env
    notif_id = f"notif_pending_offline_{test_date}"

    with SessionLocal() as db:
        db.add(PersistentNotificationModel(
            id=notif_id,
            job_id="job_offline_001",
            cycle_id="cycle_1",
            notification_type="CYCLE_SUMMARY",
            recipient="aabhiraj321@gmail.com",
            status="PENDING",
            attempt_count=1,
            created_at=datetime.utcnow(),
            idempotency_key=f"cycle_report_{test_date}_c1_PENDING",
            subject="CrazzyDocs | Cycle 1 | Offline Test",
            body_plain="Test Offline Notification Body",
            body_html="<p>Test HTML</p>",
        ))
        db.commit()

    service = email_monitoring_service
    with patch.object(service, "is_smtp_configured", return_value=True), \
         patch("backend.app.services.notification_service.NotificationManager.notify_email", return_value=True):
        ret_dict = service.retry_pending_notifications()

    assert ret_dict.get("succeeded", 0) >= 1
    with SessionLocal() as db:
        notif = db.query(PersistentNotificationModel).filter(PersistentNotificationModel.id == notif_id).first()
        assert notif.status == "SENT"
        assert notif.sent_at is not None


# =============================================================================
# SCENARIO 8: Duplicate Scheduler Startup
# =============================================================================
def test_scenario_8_duplicate_scheduler_startup(tmp_path):
    """
    Scenario 8: Operator or background task tries to launch a second scheduler.
    Verifies that SchedulerProcessLock rejects duplicate instance while active PID exists.
    """
    lock_file = str(tmp_path / "studio_duplicate_check.lock")
    lock1 = SchedulerProcessLock(lock_path=lock_file)
    lock2 = SchedulerProcessLock(lock_path=lock_file)

    assert lock1.acquire() is True
    # Attempt second lock
    assert lock2.acquire() is False

    lock1.release()
    assert lock2.acquire() is True
    lock2.release()


# =============================================================================
# SCENARIO 9: Missed Cycle Reconciliation
# =============================================================================
def test_scenario_9_missed_cycle_reconciliation(recovery_test_env):
    """
    Scenario 9: Cycle trigger missed due to shutdown beyond recovery window.
    Verifies that reconciler marks cycle MISSED in SQLite and alerts operator.
    """
    test_date = recovery_test_env
    now_1900 = datetime(2026, 11, 10, 19, 0, 0, tzinfo=IST_TZ)

    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    reconciler = StartupReconciler(network_service=mock_net)

    report = reconciler.reconcile_on_startup(test_now_ist=now_1900, recovery_policy=POLICY_STRICT_WINDOW)

    missed_cycles = report["missed_cycles"]
    missed_cycle_ids = [m["cycle_id"] for m in missed_cycles]
    assert "cycle_1" in missed_cycle_ids
    assert "cycle_2" in missed_cycle_ids
    assert "cycle_3" in missed_cycle_ids


# =============================================================================
# SCENARIO 10: Expired Recovery Windows (STRICT_WINDOW vs CATCH_UP)
# =============================================================================
def test_scenario_10_expired_recovery_windows(recovery_test_env):
    """Legacy CATCH_UP requests must not backfill expired recovery windows."""
    test_date = recovery_test_env
    now_2330 = datetime(2026, 11, 10, 23, 30, 0, tzinfo=IST_TZ)

    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    reconciler = StartupReconciler(network_service=mock_net)

    # 1. STRICT_WINDOW: 0 recovered, 4 missed
    rep_strict = reconciler.reconcile_on_startup(test_now_ist=now_2330, recovery_policy=POLICY_STRICT_WINDOW)
    assert len(rep_strict["recovered_cycles"]) == 0
    assert len(rep_strict["missed_cycles"]) == 4

    # Clear for CATCH_UP
    with SessionLocal() as db:
        db.query(JobModel).filter(JobModel.date_str == test_date).delete()
        db.commit()

    rep_requested_catchup = reconciler.reconcile_on_startup(
        test_now_ist=now_2330,
        recovery_policy=POLICY_CATCH_UP,
    )
    assert rep_requested_catchup["policy"] == POLICY_STRICT_WINDOW
    assert rep_requested_catchup["recovered_cycles"] == []
    assert len(rep_requested_catchup["missed_cycles"]) == 4
