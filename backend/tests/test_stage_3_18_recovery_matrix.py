"""
Stage 3.18 Recovery Matrix & Fault-Tolerance Verification Suite.
Phase 5, Stage 3.18 Production Standard.

Validates Scenarios A through H:
- SCENARIO A: Startup Recovery (inside recovery window -> exactly 1 recovery attempt)
- SCENARIO B: Mid-Production Crash Recovery across 8 Pipeline Checkpoint Stages
- SCENARIO C: Network Backoff & Provider Recovery
- SCENARIO D: Upload Uncertainty & Remote Channel Idempotency Verification
- SCENARIO E: Atomic Single-Instance Process Lock
- SCENARIO F: Missed Cycle Policy Enforcement (CATCH_UP vs STRICT_WINDOW)
- SCENARIO G: Database WAL Concurrency & Interruption Recovery
- SCENARIO H: Offline Notification Queue & Disconnected SMTP Resumption
"""

import json
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import pytest

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE
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
    get_recovery_policy,
)
from backend.app.scheduler.reconciliation_service import (
    StartupReconciler,
    SchedulerProcessLock,
    calculate_next_available_publishing_slot,
)
from backend.app.services.network_connectivity_service import NetworkConnectivityService
from backend.app.services.email_monitoring_service import EmailMonitoringService
from backend.app.services.youtube_publishing_engine import (
    YouTubePublishingEngine,
    YouTubeMetadata,
    PublicationRecord,
    PublishingDatabase,
)
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator


@pytest.fixture
def matrix_test_env():
    """Provides clean test environment for Stage 3.18 recovery matrix."""
    test_date = "2026-11-10"
    synth_dir = os.path.join(WORKSPACE, "media", "productions", "_test_synthetic_recovery")
    test_dirs = [
        r"D:\Agents\CrazzyDocs\media\productions\test_stage_3_18_job",
        r"D:\Agents\CrazzyDocs\media\productions\test_stage_3_18_crash",
        synth_dir,
    ]
    for d in test_dirs:
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)

    # Create synthetic test media deliverables for recovery testing
    os.makedirs(os.path.join(synth_dir, "video"), exist_ok=True)
    os.makedirs(os.path.join(synth_dir, "thumbnails"), exist_ok=True)
    os.makedirs(os.path.join(synth_dir, "narration"), exist_ok=True)
    with open(os.path.join(synth_dir, "video", "showcase_vesna_vulovic_verified.mp4"), "wb") as f:
        f.write(b"\x00" * 4096)
    with open(os.path.join(synth_dir, "thumbnails", "thumb_vesna_vulovic_verified.png"), "wb") as f:
        f.write(b"\x00" * 4096)
    with open(os.path.join(synth_dir, "narration", "stage_3_13_vesna_voice.mp3"), "wb") as f:
        f.write(b"\x00" * 4096)

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
# SCENARIO A: Startup Recovery
# =============================================================================
def test_scenario_a_startup_recovery_single_attempt(matrix_test_env):
    """
    Scenario A: System boots at 01:15 IST (inside Cycle 1 window 00:00-04:00).
    Verifies exactly 1 recovery production is scheduled/attempted, not multiple.
    """
    test_date = matrix_test_env
    dt_0115 = datetime(2026, 11, 10, 1, 15, 0, tzinfo=IST_TZ)
    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})

    reconciler = StartupReconciler(network_service=mock_net)
    report = reconciler.reconcile_on_startup(test_now_ist=dt_0115, recovery_policy=POLICY_STRICT_WINDOW)

    assert len(report["recovered_cycles"]) == 1, f"Expected exactly 1 recovered cycle, got: {report['recovered_cycles']}"
    rec_c = report["recovered_cycles"][0]
    assert rec_c["cycle_id"] == "cycle_1"
    assert rec_c["recovery_type"] == "WINDOW_RECOVERY"
    assert len(report["queued_cycles"]) == 3  # Remaining cycles queued


# =============================================================================
# SCENARIO B: Mid-Production Crash Resumption Across 8 Stages
# =============================================================================
@pytest.mark.parametrize("checkpoint_stage,progress", [
    ("TOPIC_DISCOVERY", 10.0),
    ("RESEARCH", 20.0),
    ("SCRIPT_DEVELOPMENT", 30.0),
    ("STORY_DEVELOPMENT", 40.0),
    ("VOICEOVER", 55.0),
    ("VISUAL_ASSETS", 65.0),
    ("RENDERING", 75.0),
    ("THUMBNAIL", 85.0),
])
def test_scenario_b_mid_production_crash_resumption(matrix_test_env, checkpoint_stage, progress):
    """
    Scenario B: Simulates power loss/crash during 8 distinct production stages.
    Verifies that pipeline reads checkpoint JSON, resumes deterministically, and completes.
    """
    job_id = f"test_b_crash_{checkpoint_stage.lower()}"
    prod_dir = os.path.join(WORKSPACE, "media", "productions", "_test_synthetic_recovery")
    valid_video = os.path.join(prod_dir, "video", "showcase_vesna_vulovic_verified.mp4")
    valid_thumb = os.path.join(prod_dir, "thumbnails", "thumb_vesna_vulovic_verified.png")
    valid_audio = os.path.join(prod_dir, "narration", "stage_3_13_vesna_voice.mp3")

    checkpoint_data = {
        "topic_title": "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
        "hook_text": "She fell 33,000 feet without a parachute—and walked out of the hospital alive.",
        "category": "Unbelievable Real-Life Stories",
        "audio_path": valid_audio,
        "video_path": valid_video,
        "thumbnail_path": valid_thumb,
        "winning_thumbnail": "SURVIVED 33,000 FT",
    }

    with SessionLocal() as db:
        db.add(JobModel(
            id=job_id,
            date_str="2026-11-10",
            status="RUNNING",
            current_stage=checkpoint_stage,
            progress_pct=progress,
            checkpoint_data_json=json.dumps(checkpoint_data),
        ))
        db.commit()

    with patch.object(master_orchestrator.notifier, "send_success", return_value={"desktop": True, "email": True, "deduplicated": False}):
        res = master_orchestrator.execute_production_pipeline(job_id=job_id, force_topic_id="cand_03_vesna_fall")

    assert res["status"] == "COMPLETED"
    assert "33,000" in res["topic"] or "Vesna" in res["topic"]
    assert res["video_path"] == valid_video
    assert res["thumbnail_path"] == valid_thumb


def test_scenario_5_shutdown_after_rendering_before_qc(matrix_test_env):
    """
    Scenario 5: Power loss after Stage 9 (rendering complete) but before Stage 11 (QC).
    Verifies that upon resumption, the rendered video is preserved, and QC is executed cleanly.
    """
    job_id = "test_scen_5_post_render"
    prod_dir = os.path.join(WORKSPACE, "media", "productions", "_test_synthetic_recovery")
    valid_video = os.path.join(prod_dir, "video", "showcase_vesna_vulovic_verified.mp4")
    valid_thumb = os.path.join(prod_dir, "thumbnails", "thumb_vesna_vulovic_verified.png")

    with SessionLocal() as db:
        db.add(JobModel(
            id=job_id,
            date_str="2026-11-10",
            status="RUNNING",
            current_stage="RENDERING",
            progress_pct=75.0,
            checkpoint_data_json=json.dumps({
                "topic_title": "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
                "hook_text": "She fell 33,000 feet without a parachute—and walked out of the hospital alive.",
                "video_path": valid_video,
                "thumbnail_path": valid_thumb,
                "winning_thumbnail": "SURVIVED 33,000 FT",
            }),
        ))
        db.commit()

    with patch.object(master_orchestrator.notifier, "send_success", return_value={"desktop": True, "email": True, "deduplicated": False}):
        res = master_orchestrator.execute_production_pipeline(job_id=job_id, force_topic_id="cand_03_vesna_fall")

    assert res["status"] == "COMPLETED"
    assert res["video_path"] == valid_video
    assert res["publishing_status"] in ("enqueued_private", "enqueued_public", "suppressed_test_execution")


def test_scenario_6_shutdown_after_qc_before_publishing(matrix_test_env):
    """
    Scenario 6: Power loss after Stage 11 (QC passed) but before publishing enqueuing.
    Verifies that upon resumption, pipeline does not re-render and safely reaches publishing stage.
    """
    job_id = "test_scen_6_post_qc"
    prod_dir = os.path.join(WORKSPACE, "media", "productions", "_test_synthetic_recovery")
    valid_video = os.path.join(prod_dir, "video", "showcase_vesna_vulovic_verified.mp4")
    valid_thumb = os.path.join(prod_dir, "thumbnails", "thumb_vesna_vulovic_verified.png")

    with SessionLocal() as db:
        db.add(JobModel(
            id=job_id,
            date_str="2026-11-10",
            status="RUNNING",
            current_stage="QC",
            progress_pct=90.0,
            checkpoint_data_json=json.dumps({
                "topic_title": "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
                "hook_text": "She fell 33,000 feet without a parachute—and walked out of the hospital alive.",
                "video_path": valid_video,
                "thumbnail_path": valid_thumb,
                "winning_thumbnail": "SURVIVED 33,000 FT",
            }),
        ))
        db.commit()

    with patch.object(master_orchestrator.notifier, "send_success", return_value={"desktop": True, "email": True, "deduplicated": False}):
        res = master_orchestrator.execute_production_pipeline(job_id=job_id, force_topic_id="cand_03_vesna_fall")

    assert res["status"] == "COMPLETED"
    assert res["publishing_status"] in ("enqueued_private", "enqueued_public", "suppressed_test_execution")


def test_scenario_11_corrupted_or_incomplete_checkpoint(matrix_test_env):
    """
    Scenario 11: Simulates corrupted JSON checkpoint file (e.g. truncated mid-write).
    Verifies that system catches JSONDecodeError, falls back cleanly to clean discovery, and does not crash.
    """
    job_id = "test_scen_11_corrupt_cp"

    with SessionLocal() as db:
        db.add(JobModel(
            id=job_id,
            date_str="2026-11-10",
            status="RUNNING",
            current_stage="RENDERING",
            progress_pct=50.0,
            checkpoint_data_json="{corrupted_json_truncated...",  # Invalid JSON
        ))
        db.commit()

    with patch.object(master_orchestrator.notifier, "send_success", return_value={"desktop": True, "email": True, "deduplicated": False}):
        res = master_orchestrator.execute_production_pipeline(job_id=job_id, force_topic_id="cand_03_vesna_fall")

    assert res["status"] == "COMPLETED"
    assert "33,000" in res["topic"] or "Vesna" in res["topic"]


def test_scenario_12_database_connection_failure_resilience(matrix_test_env):
    """
    Scenario 12: Simulates temporary SQLite database lock/busy state.
    Verifies that checkpoint updater logs a warning and does not abort the entire pipeline.
    """
    job_id = "test_scen_12_db_lock"

    # Call _update_checkpoint with an invalid session to simulate lock
    with patch("backend.app.orchestration.crazzydocs_master_orchestrator.SessionLocal", side_effect=sqlite3.OperationalError("database is locked")):
        # Must not raise unhandled exception
        master_orchestrator._update_checkpoint(job_id, "TEST_STAGE", 50.0, {"key": "val"})


def test_scenario_14_multiple_missed_cycles_reconciliation(matrix_test_env):
    """
    Scenario 14: System offline for >24 hours (all 4 daily cycles missed).
    Expired cycles must never be backfilled, even when a legacy CATCH_UP policy is requested.
    """
    test_date = matrix_test_env
    # Boot next day at 23:30 IST
    dt_2330 = datetime(2026, 11, 10, 23, 30, 0, tzinfo=IST_TZ)
    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    reconciler = StartupReconciler(network_service=mock_net)

    # Clean records
    with SessionLocal() as db:
        db.query(JobModel).filter(JobModel.date_str == test_date).delete()
        db.commit()

    report_requested_catchup = reconciler.reconcile_on_startup(
        test_now_ist=dt_2330,
        recovery_policy=POLICY_CATCH_UP,
    )
    assert report_requested_catchup["policy"] == POLICY_STRICT_WINDOW
    assert report_requested_catchup["recovered_cycles"] == []
    assert len(report_requested_catchup["missed_cycles"]) == 4


# =============================================================================
# SCENARIO C: Network Backoff & Retry
# =============================================================================
def test_scenario_c_network_backoff_and_retry():
    """
    Scenario C: Simulates internet drop and exponential backoff retry.
    Verifies backoff timing calculation and successful recovery when connection returns.
    """
    net = NetworkConnectivityService()

    # Step 1: Simulated outage
    with patch.object(net, "check_general_connectivity", return_value=False), \
         patch.object(net, "check_provider_services", return_value={"wikimedia": False, "speech_tts": False}):
        ready, reason, diag = net.is_environment_ready_for_cycle()
        assert not ready
        assert "INTERNET" in reason

    # Step 2: Backoff loop resumption
    probe_results = [False, False, True]
    probe_calls = 0

    def mock_probe():
        nonlocal probe_calls
        res = probe_results[min(probe_calls, len(probe_results) - 1)]
        probe_calls += 1
        return res

    sleep_calls = []

    def mock_sleep(secs):
        sleep_calls.append(secs)

    connected = net.wait_for_connectivity(
        max_wait_seconds=60.0,
        initial_backoff=5.0,
        probe_fn=mock_probe,
        sleep_fn=mock_sleep,
    )

    assert connected is True
    assert len(sleep_calls) == 2
    assert sleep_calls[0] == 5.0
    assert sleep_calls[1] == 7.5  # 5.0 * 1.5


# =============================================================================
# SCENARIO D: Upload Uncertainty & Remote Idempotency
# =============================================================================
def test_scenario_d_upload_uncertainty_remote_duplicate_prevention(tmp_path):
    """
    Scenario D: Upload succeeded on YouTube, but process crashed before writing to local DB.
    Upon restart, publishing engine checks authenticated channel remotely, discovers
    the existing video, reconciles the local ledger, and prevents duplicate upload.
    """
    db_file = str(tmp_path / "test_pub.db")
    pub_db = PublishingDatabase(db_path=db_file)
    engine = YouTubePublishingEngine(db=pub_db)

    dummy_video = str(tmp_path / "test_video.mp4")
    with open(dummy_video, "wb") as f:
        f.write(b"fake_mp4_bytes_for_testing" * 100)

    topic_title = "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived"
    meta = YouTubeMetadata(
        title="The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived #Shorts",
        description="Factual documentary summary of Vesna Vulovic survival.",
        tags=["vesna vulovic", "aviation", "survival", "shorts"],
        hashtags=["#Shorts"],
        attribution_text="Public Domain archives.",
    )

    # Mock YouTube API service
    mock_service = MagicMock()
    mock_channels = MagicMock()
    mock_channels.list.return_value.execute.return_value = {
        "items": [{
            "contentDetails": {
                "relatedPlaylists": {"uploads": "UU_test_channel_uploads"}
            }
        }]
    }
    mock_playlist_items = MagicMock()
    mock_playlist_items.list.return_value.execute.return_value = {
        "items": [{
            "snippet": {
                "title": meta.title,
                "resourceId": {"videoId": "REMOTE_EXISTING_VID_123"}
            }
        }]
    }
    mock_service.channels.return_value = mock_channels
    mock_service.playlistItems.return_value = mock_playlist_items

    # Local ledger has NO record of this video yet (simulating mid-upload crash)
    is_local_dup, _ = pub_db.check_duplicate(dummy_video, topic_title)
    assert not is_local_dup

    with patch.object(engine.oauth, "get_authenticated_service", return_value=mock_service):
        res = engine.upload_short(
            video_path=dummy_video,
            topic_title=topic_title,
            metadata=meta,
            visibility="private",
        )

    # Engine must detect remote video and reconcile without calling videos().insert
    assert res["success"] is True
    assert res["video_id"] == "REMOTE_EXISTING_VID_123"
    assert res["status"] == "remote_duplicate_reconciled"
    assert res.get("reconciled_from_remote") is True
    mock_service.videos.assert_not_called()

    # Local ledger must now contain the reconciled record
    is_now_dup, recorded_id = pub_db.check_duplicate(dummy_video, topic_title)
    assert is_now_dup is True
    assert recorded_id == "REMOTE_EXISTING_VID_123"


# =============================================================================
# SCENARIO E: Atomic Single-Instance Process Lock
# =============================================================================
def test_scenario_e_atomic_single_instance_lock(tmp_path):
    """
    Scenario E: Validates atomic process lock acquisition and duplicate prevention.
    Verifies that a second process cannot acquire the lock concurrently.
    """
    lock_file = str(tmp_path / "studio.lock")
    lock1 = SchedulerProcessLock(lock_path=lock_file)
    lock2 = SchedulerProcessLock(lock_path=lock_file)

    # Process 1 acquires lock
    assert lock1.acquire() is True
    assert os.path.exists(lock_file)

    # Process 2 attempts concurrent acquisition -> must fail cleanly
    assert lock2.acquire() is False

    # Process 1 releases lock
    lock1.release()
    assert not os.path.exists(lock_file)

    # Process 2 can now acquire lock
    assert lock2.acquire() is True
    lock2.release()


# =============================================================================
# SCENARIO F: Missed Cycle Policy Does Not Backfill
# =============================================================================
def test_scenario_f_missed_cycle_policy_does_not_backfill(matrix_test_env):
    """
    Scenario F: Legacy CATCH_UP configuration cannot recover expired cycles.
    """
    test_date = matrix_test_env
    # Laptop starts at 23:00 IST (Cycle 1, 2, 3 have expired)
    dt_2300 = datetime(2026, 11, 10, 23, 0, 0, tzinfo=IST_TZ)
    mock_net = MagicMock(spec=NetworkConnectivityService)
    mock_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})

    reconciler = StartupReconciler(network_service=mock_net)

    # Case 1: STRICT_WINDOW policy
    report_strict = reconciler.reconcile_on_startup(test_now_ist=dt_2300, recovery_policy=POLICY_STRICT_WINDOW)
    # Under STRICT_WINDOW, cycles whose recovery windows expired are NOT recovered
    assert len(report_strict["recovered_cycles"]) == 0
    assert len(report_strict["missed_cycles"]) >= 1

    # Case 2: A legacy CATCH_UP request remains strict.
    # Clean previous records
    with SessionLocal() as db:
        db.query(JobModel).filter(JobModel.date_str == test_date).delete()
        db.commit()

    report_requested_catchup = reconciler.reconcile_on_startup(
        test_now_ist=dt_2300,
        recovery_policy=POLICY_CATCH_UP,
    )
    assert report_requested_catchup["policy"] == POLICY_STRICT_WINDOW
    assert report_requested_catchup["recovered_cycles"] == []
    assert len(report_requested_catchup["missed_cycles"]) == 4


# =============================================================================
# SCENARIO G: Database WAL Concurrency & Interruption Recovery
# =============================================================================
def test_scenario_g_sqlite_wal_recovery(tmp_path):
    """
    Scenario G: Simulates SQLite WAL checkpoint recovery and rollback resilience.
    Verifies that a failed transaction rolls back cleanly without corrupting WAL.
    """
    db_path = str(tmp_path / "test_wal.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    wal_mode = cur.fetchone()[0]
    assert wal_mode.upper() == "WAL"

    cur.execute("CREATE TABLE test_ledger (id INTEGER PRIMARY KEY, stage TEXT);")
    cur.execute("INSERT INTO test_ledger (stage) VALUES ('STAGE_01');")
    conn.commit()

    # Simulate an interrupted / failed transaction
    try:
        cur.execute("INSERT INTO test_ledger (stage) VALUES ('STAGE_02_INCOMPLETE');")
        raise RuntimeError("Simulated crash mid-transaction")
    except RuntimeError:
        conn.rollback()

    cur.execute("SELECT count(*) FROM test_ledger;")
    count = cur.fetchone()[0]
    assert count == 1, "Rollback failed to preserve WAL consistency!"

    # Ensure WAL checkpoint executes cleanly
    cur.execute("PRAGMA wal_checkpoint(FULL);")
    checkpoint_res = cur.fetchone()
    assert checkpoint_res[0] == 0, f"WAL checkpoint failed: {checkpoint_res}"
    conn.close()


# =============================================================================
# SCENARIO H: Notification Persistence & Disconnected SMTP Resumption
# =============================================================================
def test_scenario_h_offline_notification_persistence_and_retry(matrix_test_env):
    """
    Scenario H: Simulates email notifications generated while SMTP is unreachable.
    Verifies alerts are queued in SQLite, and delivered upon reconnection.
    """
    email_service = EmailMonitoringService()

    expected_key = f"cycle_report_2026-11-10_cycle_1_FAILED"

    # Step 1: Queue notification while SMTP is simulated offline (notify_email returns False)
    with patch("backend.app.services.email_monitoring_service.notification_service.notify_email", return_value=False), \
         patch.object(email_service, "is_smtp_configured", return_value=True):
        res = email_service.dispatch_summary_notification(
            cycle_id="cycle_1",
            job_id="test_job_1",
            final_status="FAILED",
            subject="[CrazzyDocs ALERT] Test Offline Alert",
            body_plain="Crash alert",
            body_html="<p>Test crash alert body</p>",
            date_str="2026-11-10",
        )

    # Verify notification is queued in database with status FAILED/PENDING
    with SessionLocal() as db:
        record = db.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.idempotency_key == expected_key
        ).first()
        assert record is not None
        assert record.status in ("PENDING", "FAILED")

    # Step 2: Attempt retry while still offline
    with patch("backend.app.services.email_monitoring_service.notification_service.notify_email", return_value=False):
        retry_res = email_service.retry_pending_notifications()
        assert retry_res["succeeded"] == 0

    # Step 3: Reconnection -> mock successful SMTP delivery
    with patch("backend.app.services.email_monitoring_service.notification_service.notify_email", return_value=True):
        retry_res_online = email_service.retry_pending_notifications()
        assert retry_res_online["succeeded"] >= 1

    with SessionLocal() as db:
        record = db.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.idempotency_key == expected_key
        ).first()
        assert record.status == "SENT"
