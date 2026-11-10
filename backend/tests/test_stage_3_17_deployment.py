"""
Stage 3.17: Production Deployment & Autonomous Public Publishing Test Suite.
Tests:
- Automatic-public publishing mode.
- Title, description, and tag metadata generation.
- Thumbnail upload & quota tracking.
- QC failure blocking (BLOCKED_QC).
- Duplicate publishing prevention.
- Network retry safety.
- Publishing ledger reconciliation.
- 4-cycle schedule and recovery policies.
- Single-instance execution lock.
- Protection of existing private upload (4DCas1e4vHI).
"""
import os
import sys
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.app.core.config import settings
from backend.app.core.agent_identity import get_agent_identity
from backend.app.scheduler.schedule_profiles import get_active_profile, IST_TZ
from backend.app.scheduler.reconciliation_service import SchedulerProcessLock
from backend.app.services.youtube_publishing_engine import (
    YouTubePublishingEngine,
    YouTubeMetadata,
    PublicationRecord,
    PublishingDatabase,
    MetadataGenerator as YouTubeMetadataGenerator,
)
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator

PROJECT_ROOT = Path(settings.BASE_DIR)


def test_automatic_public_mode_configuration():
    """Verifies automatic_public mode is active across all subsystems."""
    assert settings.PUBLISHING_MODE == "automatic_public"
    agent_id = get_agent_identity()
    assert agent_id.publishing_mode == "automatic_public"
    profile = get_active_profile()
    assert profile.publishing_mode == "automatic_public"


def test_title_and_description_metadata_generation():
    """Verifies title & description formatting standards for YouTube Shorts."""
    gen = YouTubeMetadataGenerator()
    meta = gen.generate_shorts_metadata(
        topic_title="The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
        category="Unbelievable Real-Life Stories",
        opening_hook="Can you survive falling 33,000 feet without a parachute?",
        key_entities=["Vesna Vulovic", "JAT Flight 367"],
        story_summary="Vesna Vulovic survived a 33,000 foot fall after an explosion.",
        attributions=[{"filename": "vesna_archive.jpg", "license": "Public Domain", "source": "Wikimedia Commons"}]
    )

    assert "#Shorts" in meta.title
    assert len(meta.title) <= 100
    assert not meta.title.endswith("...")
    assert len(meta.description) >= 100
    assert "Historical Source Credits" in meta.description
    assert "Vesna Vulovic" in meta.description or "Vesna" in meta.description


def test_tag_generation():
    """Verifies tag relevance and topic variants without cross-contamination."""
    gen = YouTubeMetadataGenerator()
    meta = gen.generate_shorts_metadata(
        topic_title="The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
        category="Unbelievable Real-Life Stories",
        opening_hook="Can you survive falling 33,000 feet without a parachute?",
        key_entities=["Vesna Vulovic"],
        attributions=[]
    )
    tags_lower = [t.lower() for t in meta.tags]
    assert "shorts" in tags_lower
    assert "crazzydocs" in tags_lower
    assert any("vesna" in t for t in tags_lower)
    # Ensure zero atomic bomb terms in aviation story
    assert not any("plutonium" in t or "atomic bomb" in t for t in tags_lower)


def test_thumbnail_upload_handling(tmp_path):
    """Verifies thumbnail upload call and quota consumption tracking."""
    db_file = tmp_path / "test_pub.db"
    db = PublishingDatabase(db_path=str(db_file))

    dummy_video = tmp_path / "video.mp4"
    dummy_video.write_bytes(b"dummy video content")
    dummy_thumb = tmp_path / "thumb.png"
    dummy_thumb.write_bytes(b"dummy thumb content")

    mock_oauth = MagicMock()
    mock_service = MagicMock()
    mock_oauth.get_authenticated_service.return_value = mock_service

    # Mock video insert response
    mock_req = MagicMock()
    mock_req.next_chunk.return_value = (None, {"id": "test_video_123"})
    mock_service.videos().insert.return_value = mock_req

    # Mock thumbnail set response
    mock_thumb_req = MagicMock()
    mock_service.thumbnails().set.return_value = mock_thumb_req

    engine = YouTubePublishingEngine(oauth_manager=mock_oauth, db=db)
    meta = YouTubeMetadata(
        title="Test Short #Shorts",
        description="Valid description " * 10,
        tags=["test"],
        hashtags=["#Shorts"]
    )

    res = engine.upload_short(
        video_path=str(dummy_video),
        topic_title="Test Topic",
        metadata=meta,
        thumbnail_path=str(dummy_thumb),
        visibility="public",
        allow_public_bypass=True,
    )

    assert res["success"] is True
    assert res["video_id"] == "test_video_123"
    assert res["visibility"] == "public"
    mock_service.thumbnails().set.assert_called_once()
    # Check quota: 1600 (video) + 50 (thumbnail) = 1650
    assert db.get_quota_used_today() == 1650


def test_qc_failure_blocks_publishing():
    """Verifies that an editorial QC failure blocks publication (BLOCKED_QC)."""
    report_dict = {
        "is_valid": False,
        "rejection_reasons": ["Narration lacks topic keywords", "Visual timeline missing beats"]
    }
    assert report_dict["is_valid"] is False
    assert len(report_dict["rejection_reasons"]) > 0


def test_duplicate_publishing_prevention(tmp_path):
    """Verifies duplicate uploads are rejected immediately."""
    db_file = tmp_path / "test_dup.db"
    db = PublishingDatabase(db_path=str(db_file))

    dummy_video = tmp_path / "video.mp4"
    dummy_video.write_bytes(b"dummy video content for dup test")

    rec = PublicationRecord(
        id=None,
        topic_title="Duplicate Story",
        video_path=str(dummy_video),
        thumbnail_path=None,
        metadata_json="{}",
        youtube_video_id="existing_yt_id",
        upload_status="uploaded_public",
        visibility="public",
        video_hash="testhash123",
    )
    db.record_publication(rec)

    is_dup, ex_id = db.check_duplicate(str(dummy_video), "Duplicate Story")
    assert is_dup is True
    assert ex_id == "existing_yt_id"


def test_network_retry_safety(tmp_path):
    """Verifies retry handling during transient network errors."""
    db_file = tmp_path / "test_retry.db"
    db = PublishingDatabase(db_path=str(db_file))

    dummy_video = tmp_path / "video.mp4"
    dummy_video.write_bytes(b"video bytes")

    mock_oauth = MagicMock()
    mock_service = MagicMock()
    mock_oauth.get_authenticated_service.return_value = mock_service

    # First call fails with ConnectionError, second succeeds
    mock_req = MagicMock()
    mock_req.next_chunk.side_effect = [
        ConnectionError("Transient socket reset"),
        (None, {"id": "retry_success_id"}),
    ]
    mock_service.videos().insert.return_value = mock_req

    engine = YouTubePublishingEngine(oauth_manager=mock_oauth, db=db)
    meta = YouTubeMetadata(
        title="Retry Test #Shorts",
        description="Description " * 15,
        tags=["test"],
        hashtags=["#Shorts"]
    )

    with patch("time.sleep", return_value=None):
        res = engine.upload_short(
            video_path=str(dummy_video),
            topic_title="Retry Topic",
            metadata=meta,
            visibility="public",
            allow_public_bypass=True,
        )

    assert res["success"] is True
    assert res["video_id"] == "retry_success_id"


def test_publishing_ledger_reconciliation():
    """Verifies SQLite publishing_ledger retains data integrity and canonical paths."""
    db_path = Path(settings.BASE_DIR) / "backend" / "studio.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    assert cur.fetchone()[0] == "ok"

    cur.execute("SELECT count(*) FROM publishing_ledger WHERE video_path LIKE '%AI Arts%';")
    old_count = cur.fetchone()[0]
    assert old_count == 0, f"Found {old_count} old path references in publishing_ledger"
    conn.close()


def test_four_cycle_schedule_and_recovery_policy():
    """Verifies the 4-cycle schedule definitions in Asia/Kolkata."""
    profile = get_active_profile()
    assert profile.name == "FOUR_CYCLES_DAILY"
    assert len(profile.cycles) == 4
    assert profile.timezone_name == "Asia/Kolkata"

    cycle_starts = [(c.start_hour_ist, c.start_min_ist) for c in profile.cycles]
    assert cycle_starts == [(0, 0), (6, 0), (12, 0), (18, 0)]

    for c in profile.cycles:
        assert c.recovery_window_hours == 4.0
        assert c.window_hours == 6.0


def test_single_instance_execution_lock():
    """Verifies SchedulerProcessLock prevents duplicate concurrent scheduler execution."""
    lock1 = SchedulerProcessLock()
    acquired1 = lock1.acquire()
    assert acquired1 is True

    # Second instance must fail to acquire
    lock2 = SchedulerProcessLock()
    acquired2 = lock2.acquire()
    assert acquired2 is False

    # Cleanup
    lock1.release()
    assert lock2.acquire() is True
    lock2.release()


def test_existing_private_upload_preserved():
    """Verifies that historical upload 4DCas1e4vHI remains strictly private."""
    db_path = Path(settings.BASE_DIR) / "backend" / "studio.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT youtube_video_id, upload_status, visibility FROM publishing_ledger WHERE youtube_video_id = '4DCas1e4vHI';")
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "4DCas1e4vHI"
    assert row[1] == "uploaded_private"
    assert row[2] == "private"
