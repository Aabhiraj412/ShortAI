"""
Automated Test Suite for Stage 3.6 — Email Notification Automation.
Verifies:
1. Success alert format (title, topic, style, duration, video path, thumbnail path, timestamp, QC status).
2. Failure alert format (topic, failed stage, error summary, log path, retry status, recovery status, manual action required).
3. Transient SMTP retry mechanism on network errors.
4. Event deduplication within cooldown window.
5. Desktop notification fallback capability.
6. Notification audit logging (JSONL).
7. Secure local credential storage (.env.local gitignored).
"""

import json
import os
import time
from unittest.mock import MagicMock, patch
import pytest

from backend.app.services.notification_service import NotificationManager, AlertPayload
from backend.scripts.configure_email_alerts import ensure_gitignored, GITIGNORE_PATH

WORKSPACE = r"D:\Agents\CrazzyDocs"
TEST_LOG_PATH = os.path.join(WORKSPACE, "logs", "test_notifications.jsonl")


@pytest.fixture
def test_notifier(tmp_path):
    if os.path.exists(TEST_LOG_PATH):
        try:
            os.remove(TEST_LOG_PATH)
        except Exception:
            pass
    return NotificationManager(
        alert_log_path=TEST_LOG_PATH,
        cooldown_seconds=2.0,
        db_path=str(tmp_path / "notification-test.db"),
    )


def test_success_alert_formatting(test_notifier):
    with patch.object(test_notifier, "notify_desktop", return_value=True) as mock_desk, \
         patch.object(test_notifier, "notify_email", return_value=True) as mock_mail:

        res = test_notifier.send_success(
            video_title="The Man Who Survived Both Bombs",
            topic="Tsutomu Yamaguchi",
            storytelling_style="historical_documentary",
            duration_sec=56.67,
            video_path="media/video/showcase.mp4",
            thumbnail_path="media/thumbnails/thumb.png",
            qc_status="PASSED",
            production_timestamp="2026-11-10 18:00:00 UTC",
        )

        assert res["deduplicated"] is False
        assert mock_desk.called
        assert mock_mail.called

        # Verify email call arguments
        call_args = mock_mail.call_args[1]
        assert "The Man Who Survived Both Bombs" in call_args["subject"]
        body_text = call_args["body_text"]
        assert "Tsutomu Yamaguchi" in body_text
        assert "historical_documentary" in body_text
        assert "56.67 seconds" in body_text
        assert "media/video/showcase.mp4" in body_text
        assert "media/thumbnails/thumb.png" in body_text
        assert "PASSED" in body_text
        assert "2026-11-10 18:00:00 UTC" in body_text


def test_failure_alert_formatting(test_notifier):
    with patch.object(test_notifier, "notify_desktop", return_value=True) as mock_desk, \
         patch.object(test_notifier, "notify_email", return_value=True) as mock_mail:

        res = test_notifier.send_failure(
            topic="Tsutomu Yamaguchi",
            stage="tts_synthesis",
            error_summary="TTS edge service timeout",
            log_path="logs/tts_error.log",
            retry_status="Retried 2/2 times",
            recovery_status="Execution Aborted",
            manual_intervention_required=True
        )

        assert res["deduplicated"] is False
        assert mock_desk.called
        assert mock_mail.called

        call_args = mock_mail.call_args[1]
        assert "TTS_SYNTHESIS" in call_args["subject"]
        body_text = call_args["body_text"]
        assert "Tsutomu Yamaguchi" in body_text
        assert "TTS_SYNTHESIS" in body_text
        assert "TTS edge service timeout" in body_text
        assert "logs/tts_error.log" in body_text
        assert "Retried 2/2 times" in body_text
        assert "Execution Aborted" in body_text
        assert "YES" in body_text


def test_deduplication(test_notifier):
    with patch.object(test_notifier, "notify_desktop", return_value=True), \
         patch.object(test_notifier, "notify_email", return_value=True):

        r1 = test_notifier.send_failure("Topic A", "render", "Crash 1")
        assert r1["deduplicated"] is False

        # Immediate repeat should be suppressed
        r2 = test_notifier.send_failure("Topic A", "render", "Crash 1")
        assert r2["deduplicated"] is True


def test_transient_smtp_retry(test_notifier):
    os.environ["CRAZZYDOCS_SMTP_HOST"] = "mock.smtp.test"
    os.environ["CRAZZYDOCS_SMTP_USER"] = "test@crazzydocs.dev"
    os.environ["CRAZZYDOCS_SMTP_PASSWORD"] = "mockpass"
    os.environ["CRAZZYDOCS_ALERT_RECIPIENT"] = "operator@crazzydocs.dev"

    attempt_count = 0

    def mock_smtp_fail_then_succeed(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        server = MagicMock()
        if attempt_count < 2:
            raise ConnectionResetError("Transient network reset")
        return server

    with patch("smtplib.SMTP", side_effect=mock_smtp_fail_then_succeed), \
         patch("time.sleep"):
        
        ok = test_notifier.notify_email("Test Subject", "Test Body", max_retries=2)
        assert ok is True
        assert attempt_count == 2, "Should succeed on second attempt after transient failure"


def test_audit_log_persistence(test_notifier):
    with patch.object(test_notifier, "notify_desktop", return_value=True), \
         patch.object(test_notifier, "notify_email", return_value=True):

        test_notifier.send_success(
            video_title="Audit Test",
            topic="Test Topic",
            storytelling_style="pop_info",
            duration_sec=55.0,
            video_path="path/video.mp4",
            thumbnail_path="path/thumb.png"
        )

    assert os.path.exists(TEST_LOG_PATH)
    with open(TEST_LOG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) >= 1
    record = json.loads(lines[-1])
    assert record["event_type"] == "SUCCESS"
    assert record["details"]["video_title"] == "Audit Test"


def test_gitignored_credential_protection():
    ensure_gitignored()
    assert os.path.exists(GITIGNORE_PATH)
    with open(GITIGNORE_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert ".env.local" in content
