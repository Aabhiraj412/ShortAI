"""
Unit & Integration Tests for Stage 3.9F: Email Monitoring Interface & Four-Cycle Dashboard.
Validates:
1. Cycle success email formatting (production details, deliverables, QC matrix, publishing gate, embedded thumbnail).
2. Cycle failure email formatting (diagnostics, retry status, unapproved artifacts warning).
3. QC rejection email formatting.
4. Missed deadline email formatting.
5. Daily production dashboard accuracy backed by SQLite database.
6. Future cycles displayed as QUEUED/PENDING and midnight rollover calculation.
7. Idempotent deduplication preventing repeated cycle reports.
8. SMTP outage handling with persistent ledger storage and desktop fallback.
9. Notification recovery on application restart.
10. Credential redaction in error messages and payloads.
11. Optional consolidated daily digest behavior.
"""

from datetime import datetime, timezone, timedelta
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

from backend.app.database.db import SessionLocal, JobModel, PersistentNotificationModel
from backend.app.scheduler.schedule_profiles import IST_TZ
from backend.app.services.email_monitoring_service import (
    EmailMonitoringService,
    email_monitoring_service,
    ProductionDetails,
    DeliverablesDetails,
    QCDetails,
    PublishingDetails,
    FailureDetails,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_QC_REJECTED,
    STATUS_MISSED_DEADLINE,
    redact_sensitive_string,
    redact_sensitive_dict,
)


@pytest.fixture
def mock_dashboard_db():
    """Seeds test jobs in SQLite for a fixed date and cleans up afterwards."""
    test_date = "2026-11-10"
    with SessionLocal() as db_session:
        # Clear existing test entries
        db_session.query(JobModel).filter(JobModel.date_str == test_date).delete()
        db_session.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.idempotency_key.like(f"%{test_date}%")
        ).delete()

        # Seed Cycle 1: COMPLETED
        job_c1 = JobModel(
            id="cycle_1_20261110_0000",
            date_str=test_date,
            topic_title="Tsutomu Yamaguchi Atomic Bomb Survival",
            status="COMPLETED",
            current_stage="COMPLETED",
        )
        # Seed Cycle 2: FAILED
        job_c2 = JobModel(
            id="cycle_2_20261110_0600",
            date_str=test_date,
            topic_title="The Great Molasses Flood",
            status="FAILED",
            current_stage="RENDERING",
            error_details="FFmpeg render pipe buffer overflow",
        )
        # Seed Cycle 3: RUNNING
        job_c3 = JobModel(
            id="cycle_3_20261110_1200",
            date_str=test_date,
            topic_title="Voyager 1 Golden Record",
            status="RUNNING",
            current_stage="VOICEOVER",
        )
        # Cycle 4: not seeded (remains QUEUED)

        db_session.add_all([job_c1, job_c2, job_c3])
        db_session.commit()

    yield test_date

    # Teardown
    with SessionLocal() as db_session:
        db_session.query(JobModel).filter(JobModel.date_str == test_date).delete()
        db_session.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.idempotency_key.like(f"%{test_date}%")
        ).delete()
        db_session.commit()


def test_success_email_builder():
    """Verifies that success emails contain all required production, QC, and publishing fields."""
    service = email_monitoring_service
    dash = service.generate_daily_dashboard(date_str="2026-11-10")

    prod = ProductionDetails(
        date_str="2026-11-10",
        cycle_id="cycle_1",
        cycle_number=1,
        job_id="cycle_1_20261110_0000",
        topic="Tsutomu Yamaguchi",
        video_title="He Survived Both Atomic Bombs",
        storytelling_style="Cinematic Documentary",
        narrative_archetype="Triumph Over Catastrophe",
        duration_sec=42.15,
        start_time="00:00:10 IST",
        completion_time="00:00:55 IST",
        total_duration_sec=45.0,
    )
    deliv = DeliverablesDetails(
        video_path="media/video/showcase_stage_3_4_tsutomu_yamaguchi.mp4",
        thumbnail_path="media/thumbnails/showcase_stage_3_9a_yamaguchi_poster.png",
        subtitle_path="media/subtitles/editorial/yamaguchi_editorial.json",
        attribution_manifest_path="media/images/yamaguchi/attribution_manifest.json",
        thumbnail_base64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
    )
    qc = QCDetails()
    pub = PublishingDetails(target_pub_timestamp="06:00 IST")

    subject, plain_body, html_body = service.build_success_email(
        prod=prod, deliv=deliv, qc=qc, pub=pub, dashboard=dash
    )

    # Subject format
    assert subject == "CrazzyDocs | Cycle 1 | SUCCESS | He Survived Both Atomic Bombs"

    # Production details in plaintext and HTML
    assert "Tsutomu Yamaguchi" in plain_body
    assert "He Survived Both Atomic Bombs" in plain_body
    assert "42.15 seconds" in plain_body
    assert "Cinematic Documentary" in plain_body
    assert "Triumph Over Catastrophe" in plain_body

    # Deliverables
    assert "media/video/showcase_stage_3_4_tsutomu_yamaguchi.mp4" in plain_body
    assert "media/thumbnails/showcase_stage_3_9a_yamaguchi_poster.png" in plain_body

    # QC checks
    assert "1080x1920 @ 30 FPS" in plain_body
    assert "SYNCHRONIZED" in plain_body
    assert "Score: 9.76/10" in plain_body

    # Publishing gate
    assert "MANUAL APPROVAL GATE" in html_body
    assert "manual_approval - Withheld at safety gate" in plain_body

    # Thumbnail preview tag in HTML
    assert "data:image/png;base64," in html_body


def test_failure_email_builder():
    """Verifies that failure emails include diagnostics, retry count, logs, and redaction."""
    service = email_monitoring_service
    dash = service.generate_daily_dashboard(date_str="2026-11-10")

    prod = ProductionDetails(
        date_str="2026-11-10",
        cycle_id="cycle_2",
        cycle_number=2,
        job_id="cycle_2_20261110_0600",
        topic="The Great Molasses Flood",
        video_title="Wave of Death",
    )
    fail = FailureDetails(
        last_completed_stage="STAGE_8_TTS",
        failed_stage="RENDERING",
        error_message="FFmpeg process failed with error: password='mySecretPassword123' token=BearerXYZ",
        error_timestamp="2026-11-10 06:15:00 IST",
        retry_count=2,
        recovery_attempts=2,
        recovery_outcome="Halted at render gate",
        can_resume=True,
        deadline_missed=False,
        log_paths=["logs/ffmpeg_render.log"],
        recommended_next_action="Check FFmpeg pipe allocation.",
    )

    subject, plain_body, html_body = service.build_failure_email(
        prod=prod, fail=fail, dashboard=dash, terminal_status=STATUS_FAILED
    )

    assert subject == "CrazzyDocs | Cycle 2 | FAILED | Action Required"
    assert "STAGE_8_TTS" in plain_body
    assert "RENDERING" in plain_body
    assert "Check FFmpeg pipe allocation" in plain_body

    # Secret credentials must be redacted!
    assert "mySecretPassword123" not in plain_body
    assert "BearerXYZ" not in plain_body
    assert "[REDACTED]" in plain_body


def test_qc_rejection_email_with_unapproved_artifacts():
    """Verifies that QC rejection emails explicitly warn against unapproved artifacts."""
    service = email_monitoring_service
    dash = service.generate_daily_dashboard(date_str="2026-11-10")

    prod = ProductionDetails(
        date_str="2026-11-10",
        cycle_id="cycle_2",
        cycle_number=2,
        job_id="cycle_2_20261110_0600",
        topic="Disputed History",
        video_title="Unverified Claim",
    )
    fail = FailureDetails(
        last_completed_stage="RENDERING",
        failed_stage="QUALITY_CONTROL",
        error_message="QC Gate Rejected: A/V sync offset exceeds 400ms threshold.",
        can_resume=False,
        unapproved_artifacts=["media/video/scratch/unapproved_draft.mp4"],
    )

    subject, plain_body, html_body = service.build_failure_email(
        prod=prod, fail=fail, dashboard=dash, terminal_status=STATUS_QC_REJECTED
    )

    assert "QC_REJECTED" in subject
    assert "UNAPPROVED ARTIFACTS GENERATED" in plain_body
    assert "DO NOT PUBLISH OR USE THESE ARTIFACTS" in plain_body
    assert "media/video/scratch/unapproved_draft.mp4" in plain_body
    assert "UNAPPROVED ARTIFACTS GENERATED" in html_body


def test_missed_deadline_email():
    """Verifies missed deadline alerts report deadline exceeded accurately."""
    service = email_monitoring_service
    dash = service.generate_daily_dashboard(date_str="2026-11-10")

    prod = ProductionDetails(
        date_str="2026-11-10",
        cycle_id="cycle_1",
        cycle_number=1,
        job_id="cycle_1_20261110_0000",
        topic="Overdue Render",
        video_title="Late Video",
    )
    fail = FailureDetails(
        last_completed_stage="TOPIC_SELECTION",
        failed_stage="SCHEDULER_DEADLINE",
        error_message="Production trigger exceeded 6-hour publication SLA window.",
        deadline_missed=True,
    )

    subject, plain_body, html_body = service.build_failure_email(
        prod=prod, fail=fail, dashboard=dash, terminal_status=STATUS_MISSED_DEADLINE
    )

    assert "MISSED_DEADLINE" in subject
    assert "Deadline Missed:         YES" in plain_body


def test_daily_dashboard_accuracy_from_db(mock_dashboard_db):
    """Verifies that daily dashboard accurately reflects real SQLite database state."""
    service = email_monitoring_service
    test_date = mock_dashboard_db

    # Test at 14:00 IST on test date
    fixed_now_ist = datetime(2026, 11, 10, 14, 0, 0, tzinfo=IST_TZ)
    dash = service.generate_daily_dashboard(date_str=test_date, test_now_ist=fixed_now_ist)

    assert dash["date"] == test_date
    assert len(dash["cycles"]) == 4

    c1, c2, c3, c4 = dash["cycles"]

    # Cycle 1: Database has COMPLETED -> Dashboard shows SUCCESS / PASS
    assert c1["cycle_number"] == 1
    assert c1["status"] == "SUCCESS"
    assert c1["qc"] == "PASS"
    assert "Tsutomu Yamaguchi" in c1["topic"]

    # Cycle 2: Database has FAILED -> Dashboard shows FAILED / FAIL
    assert c2["cycle_number"] == 2
    assert c2["status"] == "FAILED"
    assert c2["qc"] == "FAIL"

    # Cycle 3: Database has RUNNING -> Dashboard shows RUNNING / IN_PROGRESS
    assert c3["cycle_number"] == 3
    assert c3["status"] == "RUNNING"
    assert c3["qc"] == "IN_PROGRESS"

    # Cycle 4: Not started yet, start time is 18:00 (after 14:00) -> QUEUED / PENDING
    assert c4["cycle_number"] == 4
    assert c4["status"] == "QUEUED"
    assert c4["qc"] == "PENDING"
    assert "(+1d)" in c4["target_pub_ist"]  # Midnight rollover!

    # Summary metrics
    sm = dash["summary"]
    assert sm["total_cycles_scheduled"] == 4
    assert sm["total_cycles_started"] == 3
    assert sm["successfully_generated_videos"] == 1
    assert sm["failed_cycles"] == 1
    assert sm["videos_awaiting_approval"] == 1


def test_idempotent_duplicate_prevention(mock_dashboard_db):
    """Verifies that duplicate final summary reports for the same cycle are suppressed."""
    service = email_monitoring_service
    test_date = mock_dashboard_db

    with patch.object(service, "is_smtp_configured", return_value=True), \
         patch("backend.app.services.notification_service.NotificationManager.notify_email", return_value=True) as mock_email:

        # 1. First dispatch
        res1 = service.dispatch_summary_notification(
            cycle_id="cycle_1",
            job_id="cycle_1_20261110_0000",
            final_status="SUCCESS",
            subject="Test Subject",
            body_plain="Test Body Plain",
            body_html="<p>Test HTML</p>",
            date_str=test_date,
        )
        assert res1["status"] == "SENT"
        assert res1["email_sent"] is True

        # 2. Second dispatch with identical idempotency key
        res2 = service.dispatch_summary_notification(
            cycle_id="cycle_1",
            job_id="cycle_1_20261110_0000",
            final_status="SUCCESS",
            subject="Test Subject",
            body_plain="Test Body Plain",
            body_html="<p>Test HTML</p>",
            date_str=test_date,
        )
        assert res2["status"] == "DUPLICATE_SUPPRESSED"

        # Email must have been dispatched exactly once
        assert mock_email.call_count == 1


def test_smtp_outage_persistence_and_desktop_fallback(mock_dashboard_db):
    """Verifies that SMTP failure logs to persistent ledger and triggers desktop fallback."""
    service = email_monitoring_service
    test_date = mock_dashboard_db

    with patch.object(service, "is_smtp_configured", return_value=True), \
         patch("backend.app.services.notification_service.NotificationManager.notify_email", return_value=False), \
         patch("backend.app.services.notification_service.NotificationManager.notify_desktop", return_value=True) as mock_desktop:

        res = service.dispatch_summary_notification(
            cycle_id="cycle_2",
            job_id="cycle_2_20261110_0600",
            final_status="FAILED",
            subject="Test Failure",
            body_plain="Failure body",
            body_html="<p>Failure</p>",
            date_str=test_date,
        )
        assert res["status"] == "SAVED_LOCAL_PENDING"
        assert res["email_sent"] is False

        # Desktop fallback triggered
        assert mock_desktop.called

        # Verified in SQLite
        with SessionLocal() as db_session:
            rec = db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.id == res["notification_id"]
            ).first()
            assert rec is not None
            assert rec.status == "FAILED"
            assert rec.attempt_count >= 1


def test_notification_recovery_after_restart(mock_dashboard_db):
    """Verifies that unsent notifications in SQLite are retried successfully."""
    service = email_monitoring_service
    test_date = mock_dashboard_db

    # Pre-seed a FAILED notification
    with SessionLocal() as db_session:
        notif = PersistentNotificationModel(
            id="notif_recovery_test_01",
            job_id="cycle_3_20261110_1200",
            cycle_id="cycle_3",
            recipient="test@example.com",
            status="FAILED",
            attempt_count=1,
            idempotency_key=f"cycle_report_{test_date}_cycle_3_SUCCESS",
            subject="Recovery Test Subject",
            body_plain="Recovery Body",
            body_html="<p>Recovery Body</p>",
        )
        db_session.add(notif)
        db_session.commit()

    # Retry with working SMTP
    with patch.object(service, "is_smtp_configured", return_value=True), \
         patch("backend.app.services.notification_service.NotificationManager.notify_email", return_value=True):

        retry_res = service.retry_pending_notifications()
        assert retry_res["status"] == "COMPLETED"
        assert retry_res["retried"] >= 1
        assert retry_res["succeeded"] >= 1

    # Verify updated in database
    with SessionLocal() as db_session:
        updated = db_session.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.id == "notif_recovery_test_01"
        ).first()
        assert updated.status == "SENT"
        assert updated.sent_at is not None


def test_credential_redaction():
    """Verifies that passwords, tokens, and secrets are stripped from text and payloads."""
    raw_str = "Connecting with smtp_password='SuperSecretPass123' and token: 'ghp_abc123xyz' failed."
    clean_str = redact_sensitive_string(raw_str)
    assert "SuperSecretPass123" not in clean_str
    assert "ghp_abc123xyz" not in clean_str
    assert "[REDACTED]" in clean_str

    raw_dict = {
        "job_id": "job_01",
        "api_key": "AIzaSySecretApiKey",
        "client_secret": "SuperSecretClientSecret",
        "nested": {
            "password": "NestedPassword456",
            "normal_field": "safe_value",
        }
    }
    clean_dict = redact_sensitive_dict(raw_dict)
    assert clean_dict["api_key"] == "[REDACTED]"
    assert clean_dict["client_secret"] == "[REDACTED]"
    assert clean_dict["nested"]["password"] == "[REDACTED]"
    assert clean_dict["nested"]["normal_field"] == "safe_value"


def test_optional_daily_digest():
    """Verifies that daily digest is only sent when enabled or forced."""
    service = email_monitoring_service

    # When disabled by default
    with patch.dict(os.environ, {"CRAZZYDOCS_ENABLE_DAILY_DIGEST": "false"}):
        res_disabled = service.send_daily_digest(date_str="2026-11-10", force=False)
        assert res_disabled["status"] == "SKIPPED"
        assert res_disabled["reason"] == "DIGEST_DISABLED_IN_CONFIG"

    # When forced
    with patch.object(service, "dispatch_summary_notification", return_value={"status": "SENT"}):
        res_forced = service.send_daily_digest(date_str="2026-11-10", force=True)
        assert res_forced["status"] == "SENT"
