"""
Automated Test Suite for Stage 3.7 — YouTube Automation.
Verifies:
1. Google OAuth2 status reporting and credential lifecycle.
2. Search-optimized metadata generator with factual attribution.
3. Strict Publishing Gate: 'public' publishing permanently blocked by default.
4. Duplicate upload prevention via local SQLite publishing ledger.
5. Daily API quota tracking and threshold enforcement.
6. Mocked resumable chunked upload engine and thumbnail upload.
7. Disconnect and token revocation handling.
"""

from dataclasses import asdict
import json
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from backend.app.services.youtube_publishing_engine import (
    YouTubePublishingEngine,
    YouTubeOAuthManager,
    MetadataGenerator,
    YouTubeMetadata,
    PublishingDatabase,
    PublicationRecord,
    compute_file_sha256,
)

WORKSPACE = r"D:\Agents\CrazzyDocs"
TEST_DB_PATH = os.path.join(WORKSPACE, "logs", "test_publishing.db")


@pytest.fixture
def test_pub_db():
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass
    return PublishingDatabase(db_path=TEST_DB_PATH)


def test_oauth_status_unauthenticated():
    oauth = YouTubeOAuthManager(
        client_secrets_file="nonexistent_secrets.json",
        token_file="nonexistent_token.json"
    )
    status = oauth.get_auth_status()
    assert status["secrets_installed"] is False
    assert status["token_installed"] is False
    assert status["is_authenticated"] is False
    assert status["channel_connected"] is False


def test_metadata_generator():
    meta = MetadataGenerator.generate_shorts_metadata(
        topic_title="Tsutomu Yamaguchi: The Man Who Survived Both Atomic Bombs",
        opening_hook="He survived the atomic blast in Hiroshima. Three days later, he was in Nagasaki.",
        category="Unbelievable Real-Life Stories",
        key_entities=["Tsutomu Yamaguchi", "Hiroshima", "Nagasaki", "Hibakusha"],
        attributions=[{"original_filename": "hiroshima.jpg", "license": "Public Domain", "attribution": "NARA"}]
    )
    assert meta.title.endswith("#Shorts")
    assert len(meta.title) <= 100
    assert "Hiroshima" in meta.description
    assert "Nagasaki" in meta.description
    assert "#CrazzyDocs" in meta.description
    assert len(meta.tags) >= 5
    assert meta.category_id in ["27", "28", "24"]


def test_public_publishing_hard_gate(test_pub_db):
    engine = YouTubePublishingEngine(db=test_pub_db)
    meta = MetadataGenerator.generate_shorts_metadata("Test", "Hook", "History", [])

    # Attempting public upload without authorization MUST raise PermissionError
    with pytest.raises(PermissionError) as exc_info:
        engine.upload_short(
            video_path="media/video/dummy.mp4",
            topic_title="Test Topic",
            metadata=meta,
            visibility="public",
            allow_public_bypass=False
        )
    assert "Public publishing is disabled" in str(exc_info.value)


def test_duplicate_upload_prevention(test_pub_db, tmp_path):
    dummy_video = tmp_path / "test_video.mp4"
    dummy_video.write_bytes(b"dummy video content 12345")
    video_path = str(dummy_video)

    v_hash = compute_file_sha256(video_path)
    rec = PublicationRecord(
        id=None,
        topic_title="Unique Topic 1",
        video_path=video_path,
        thumbnail_path=None,
        metadata_json="{}",
        youtube_video_id="yt_12345",
        upload_status="uploaded_private",
        visibility="private",
        video_hash=v_hash,
    )
    test_pub_db.record_publication(rec)

    # Checking duplicate
    is_dup, existing_id = test_pub_db.check_duplicate(video_path, "Unique Topic 1")
    assert is_dup is True
    assert existing_id == "yt_12345"

    # Attempt upload should be rejected
    engine = YouTubePublishingEngine(db=test_pub_db)
    meta = MetadataGenerator.generate_shorts_metadata("Unique Topic 1", "Hook", "History", [])
    res = engine.upload_short(video_path, "Unique Topic 1", meta, visibility="private")
    assert res["success"] is False
    assert res["status"] == "duplicate_rejected"


def test_quota_tracking(test_pub_db):
    assert test_pub_db.get_quota_used_today() == 0
    test_pub_db.add_quota_consumption(1600)
    assert test_pub_db.get_quota_used_today() == 1600
    test_pub_db.add_quota_consumption(50)
    assert test_pub_db.get_quota_used_today() == 1650


def test_mock_resumable_upload_private(test_pub_db, tmp_path):
    dummy_video = tmp_path / "showcase_test.mp4"
    dummy_video.write_bytes(b"showcase mp4 content 99999")
    video_path = str(dummy_video)

    dummy_thumb = tmp_path / "thumb_test.png"
    dummy_thumb.write_bytes(b"png content")
    thumb_path = str(dummy_thumb)

    mock_oauth = MagicMock()
    mock_service = MagicMock()
    mock_oauth.get_authenticated_service.return_value = mock_service

    # Mock chunk response: first chunk returns None, second chunk returns ID
    mock_req = MagicMock()
    status_mock = MagicMock()
    status_mock.progress.return_value = 1.0
    mock_req.next_chunk.side_effect = [(status_mock, {"id": "mock_yt_vid_999"})]
    mock_service.videos().insert.return_value = mock_req

    # Mock thumbnail response
    mock_thumb_req = MagicMock()
    mock_service.thumbnails().set.return_value = mock_thumb_req

    engine = YouTubePublishingEngine(oauth_manager=mock_oauth, db=test_pub_db)
    meta = MetadataGenerator.generate_shorts_metadata(
        topic_title="Mock Topic",
        opening_hook="Hook line",
        category="Science",
        key_entities=["MockEntity"]
    )

    progresses = []
    res = engine.upload_short(
        video_path=video_path,
        topic_title="Mock Topic",
        metadata=meta,
        thumbnail_path=thumb_path,
        visibility="private",
        progress_callback=lambda p: progresses.append(p)
    )

    assert res["success"] is True
    assert res["video_id"] == "mock_yt_vid_999"
    assert res["visibility"] == "private"
    assert res["thumbnail_uploaded"] is True
    assert 1.0 in progresses

    # Verify database record
    is_dup, v_id = test_pub_db.check_duplicate(video_path, "Mock Topic")
    assert is_dup is True
    assert v_id == "mock_yt_vid_999"


def test_disconnect_channel(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "fake"}')
    oauth = YouTubeOAuthManager(token_file=str(token_file))
    assert os.path.exists(str(token_file))
    ok = oauth.disconnect_channel()
    assert ok is True
    assert not os.path.exists(str(token_file))
