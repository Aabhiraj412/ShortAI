"""
Unit and integration tests for Stage 3.21G:
- ExecutionContext isolation (PRODUCTION vs TEST/DEV/MAINTENANCE)
- Notification idempotency & suppression for test jobs
- Publishing queue deduplication and atomic concurrency protection
- Test isolation from genuine production databases and media
"""

import concurrent.futures
import json
import os
import sqlite3
import pytest
from dataclasses import asdict

from backend.app.core.execution_context import (
    ExecutionContext,
    require_production_context,
    get_current_execution_context,
)
from backend.app.services.youtube_publishing_engine import (
    PublishingDatabase,
    PublicationRecord,
    normalize_topic_title,
)
from backend.app.services.notification_service import NotificationManager


@pytest.fixture
def temp_db(tmp_path):
    """Provides an isolated temporary SQLite database for testing."""
    db_file = str(tmp_path / "test_isolated_studio.db")
    return db_file


def test_execution_context_parsing_and_fail_closed():
    """Verifies that ExecutionContext parses valid values and fails closed on invalid/missing contexts."""
    assert ExecutionContext.from_string("production") == ExecutionContext.PRODUCTION
    assert ExecutionContext.from_string("TEST") == ExecutionContext.TEST
    assert ExecutionContext.from_string("Development") == ExecutionContext.DEVELOPMENT
    assert ExecutionContext.from_string("maintenance") == ExecutionContext.MAINTENANCE

    with pytest.raises(ValueError):
        ExecutionContext.from_string("INVALID_CTX")

    with pytest.raises(ValueError):
        ExecutionContext.from_string("")

    # Test require_production_context fails closed
    require_production_context(ExecutionContext.PRODUCTION)  # should not raise

    with pytest.raises(PermissionError) as exc_info:
        require_production_context(ExecutionContext.TEST, "Ledger write")
    assert "strictly prohibited in TEST context" in str(exc_info.value)

    with pytest.raises(PermissionError):
        require_production_context(ExecutionContext.DEVELOPMENT)


def test_topic_title_normalization():
    """Verifies that topic titles are normalized consistently regardless of tags, casing, or punctuation."""
    t1 = "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived #Shorts"
    t2 = "the flight attendant who fell 33000 feet without a parachute and survived"
    t3 = "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived! #History #Shorts"

    n1 = normalize_topic_title(t1)
    n2 = normalize_topic_title(t2)
    n3 = normalize_topic_title(t3)

    assert n1 == n2 == n3
    assert "shorts" not in n1
    assert "history" not in n3


def test_test_jobs_cannot_insert_into_production_publishing_ledger(temp_db):
    """Verifies that TEST context is blocked from inserting into publishing_ledger."""
    pdb = PublishingDatabase(db_path=temp_db)
    rec = PublicationRecord(
        id=None,
        topic_title="Test Topic",
        video_path="test.mp4",
        thumbnail_path="thumb.png",
        metadata_json="{}",
        youtube_video_id=None,
        upload_status="pending_queue",
        visibility="public",
        video_hash="abc123hash",
        upload_timestamp=None,
        scheduled_timestamp="10:00 AM IST",
    )

    with pytest.raises(PermissionError) as exc:
        pdb.record_publication(rec, execution_context=ExecutionContext.TEST)
    assert "strictly prohibited in TEST context" in str(exc.value)

    # In PRODUCTION context, insertion succeeds
    row_id = pdb.record_publication(rec, execution_context=ExecutionContext.PRODUCTION)
    assert row_id is not None
    assert row_id > 0


def test_publishing_queue_deduplication_and_legitimate_retry(temp_db):
    """Verifies that publishing queue prevents duplicate active entries and handles legitimate retries."""
    pdb = PublishingDatabase(db_path=temp_db)

    rec1 = PublicationRecord(
        id=None,
        topic_title="The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
        video_path="media/video1.mp4",
        thumbnail_path="media/thumb1.png",
        metadata_json="{}",
        youtube_video_id=None,
        upload_status="pending_queue",
        visibility="public",
        video_hash="hash_video_1",
        upload_timestamp=None,
        scheduled_timestamp="10:00 AM IST",
    )

    # First insertion
    id1 = pdb.record_publication(rec1, execution_context=ExecutionContext.PRODUCTION)
    assert id1 == 1

    # Legitimate retry: same video path and hash
    id_retry = pdb.record_publication(rec1, execution_context=ExecutionContext.PRODUCTION)
    assert id_retry == id1, "Legitimate retry must return the existing record ID without creating a new record"

    # Duplicate topic attempt: same normalized topic but different path/hash
    rec2 = PublicationRecord(
        id=None,
        topic_title="The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived #Shorts",
        video_path="media/video2_duplicate.mp4",
        thumbnail_path="media/thumb2.png",
        metadata_json="{}",
        youtube_video_id=None,
        upload_status="pending_queue",
        visibility="public",
        video_hash="hash_video_2",
        upload_timestamp=None,
        scheduled_timestamp="10:00 AM IST",
    )
    id2 = pdb.record_publication(rec2, execution_context=ExecutionContext.PRODUCTION)
    assert id2 == id1, "Duplicate topic attempt must return existing record ID without creating a new row"

    # Verify only 1 record exists in DB
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM publishing_ledger")
    count = cur.fetchone()[0]
    conn.close()
    assert count == 1


def test_concurrent_queue_insertion_atomicity(temp_db):
    """Verifies that concurrent threads attempting to enqueue the same topic result in exactly 1 active queue entry."""
    pdb = PublishingDatabase(db_path=temp_db)

    def enqueue_attempt(idx: int):
        rec = PublicationRecord(
            id=None,
            topic_title="Vesna Vulovic Survival Story",
            video_path=f"media/video_concurrent_{idx}.mp4",
            thumbnail_path="media/thumb.png",
            metadata_json="{}",
            youtube_video_id=None,
            upload_status="pending_queue",
            visibility="public",
            video_hash=f"hash_concurrent_{idx}",
            upload_timestamp=None,
            scheduled_timestamp="10:00 AM IST",
        )
        return pdb.record_publication(rec, execution_context=ExecutionContext.PRODUCTION)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(enqueue_attempt, i) for i in range(5)]
        results = [f.result() for f in futures]

    # All returned IDs must be identical (the first created ID)
    assert len(set(results)) == 1

    # Exactly 1 row in the ledger
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM publishing_ledger")
    count = cur.fetchone()[0]
    conn.close()
    assert count == 1


def test_test_jobs_cannot_send_production_emails(tmp_path, temp_db):
    """Verifies that TEST context or test_ job IDs suppress email notifications."""
    alert_log = str(tmp_path / "test_alerts.jsonl")
    notifier = NotificationManager(alert_log_path=alert_log, db_path=temp_db)

    # 1. Non-production context
    res1 = notifier.send_success(
        video_title="Test Title",
        topic="Test Topic",
        storytelling_style="historical",
        duration_sec=54.0,
        video_path="video.mp4",
        thumbnail_path="thumb.png",
        job_id="prod_job_123",
        execution_context=ExecutionContext.TEST,
    )
    assert res1["suppressed_test_mode"] is True
    assert res1["email"] is False

    # 2. test_ job ID in PRODUCTION context
    res2 = notifier.send_success(
        video_title="Test Title",
        topic="Test Topic",
        storytelling_style="historical",
        duration_sec=54.0,
        video_path="video.mp4",
        thumbnail_path="thumb.png",
        job_id="test_vesna_integrity_run",
        execution_context=ExecutionContext.PRODUCTION,
    )
    assert res2["suppressed_test_mode"] is True
    assert res2["email"] is False

    # 3. Failure alerts suppressed in TEST context
    res3 = notifier.send_failure(
        topic="Test Topic",
        stage="tts",
        error_summary="TTS synthesis failed",
        job_id="test_crash_job",
        execution_context=ExecutionContext.TEST,
    )
    assert res3["suppressed_test_mode"] is True
    assert res3["email"] is False


def test_notification_idempotency_persistent_deduplication(tmp_path, temp_db):
    """Verifies that repeated delivery attempts for the same production event are persistently deduplicated."""
    alert_log = str(tmp_path / "test_alerts.jsonl")
    notifier = NotificationManager(alert_log_path=alert_log, db_path=temp_db)

    event_key = "SUCCESS:prod_cycle_job_001"
    # Pre-record delivery in persistent ledger
    notifier.record_event_delivered(event_key, "SUCCESS", "prod_cycle_job_001", "operator@example.com")

    # Subsequent send_success for the same job must deduplicate persistently
    res = notifier.send_success(
        video_title="Genuine Production Title",
        topic="Genuine Topic",
        storytelling_style="historical",
        duration_sec=55.0,
        video_path="prod.mp4",
        thumbnail_path="thumb.png",
        job_id="prod_cycle_job_001",
        execution_context=ExecutionContext.PRODUCTION,
    )
    assert res["deduplicated"] is True
    assert res["email"] is False
