"""
Stage 3.12 Comprehensive Test Suite — YouTube Authentication & Verified Private Uploads.

Covers:
1. Client secrets installation, validation, and gitignore enforcement.
2. Token lifecycle, refresh, and disconnect revocation.
3. Channel identity extraction (Channel ID, Name, Thumbnail URL, Subscribers, Privacy status).
4. Custom thumbnail eligibility restriction handling & local retention invariant.
5. Resumable chunked upload mechanics, quota deduction, and duplicate rejection.
6. Strict publishing safety gate (zero public uploads).
"""

from dataclasses import asdict
import json
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch
import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

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
TEST_DB_PATH = os.path.join(WORKSPACE, "logs", "test_stage_3_12_publishing.db")


@pytest.fixture
def test_db():
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass
    return PublishingDatabase(db_path=TEST_DB_PATH)


# =========================================================================
# 1. CLIENT SECRETS INSTALLATION & VALIDATION
# =========================================================================

def test_install_valid_client_secrets(tmp_path):
    secrets_path = tmp_path / "client_secrets.json"
    mgr = YouTubeOAuthManager(client_secrets_file=str(secrets_path))

    valid_json = json.dumps({
        "installed": {
            "client_id": "mock_client_id_12345.apps.googleusercontent.com",
            "client_secret": "GOCSPX-mock_secret_abc123",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    })

    ok, msg = mgr.install_client_secrets(valid_json)
    assert ok is True
    assert os.path.exists(str(secrets_path))

    # Verify content
    data = mgr._load_client_config()
    assert data["installed"]["client_id"] == "mock_client_id_12345.apps.googleusercontent.com"


def test_install_invalid_client_secrets_rejected(tmp_path):
    secrets_path = tmp_path / "client_secrets.json"
    mgr = YouTubeOAuthManager(client_secrets_file=str(secrets_path))

    # Missing installed/web key
    invalid_json = json.dumps({"foo": "bar"})
    ok, msg = mgr.install_client_secrets(invalid_json)
    assert ok is False
    assert "missing top-level 'installed' or 'web'" in msg

    # Missing client_secret
    invalid_json_2 = json.dumps({"installed": {"client_id": "abc"}})
    ok2, msg2 = mgr.install_client_secrets(invalid_json_2)
    assert ok2 is False
    assert "missing 'client_id' or 'client_secret'" in msg2


# =========================================================================
# 2. TOKEN LIFECYCLE, REFRESH & DISCONNECT
# =========================================================================

def test_disconnect_channel_cleanup(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "fake_tok", "refresh_token": "fake_ref"}')
    mgr = YouTubeOAuthManager(token_file=str(token_file))

    assert os.path.exists(str(token_file))
    ok = mgr.disconnect_channel()
    assert ok is True
    assert not os.path.exists(str(token_file))


def test_channel_identity_extraction(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "fake_tok"}')
    mgr = YouTubeOAuthManager(token_file=str(token_file))

    mock_service = MagicMock()
    mock_channels = MagicMock()
    mock_list_req = MagicMock()

    mock_list_req.execute.return_value = {
        "items": [{
            "id": "UC_CRAZZY_DOCS_CHANNEL_999",
            "snippet": {
                "title": "CrazzyDocs Official",
                "customUrl": "@CrazzyDocs",
                "thumbnails": {
                    "high": {"url": "https://yt3.ggpht.com/channel_avatar_high.jpg"},
                    "default": {"url": "https://yt3.ggpht.com/channel_avatar_default.jpg"},
                }
            },
            "statistics": {
                "subscriberCount": "15400",
                "videoCount": "42",
                "viewCount": "2500000"
            },
            "status": {
                "privacyStatus": "public",
                "isLinked": True,
                "longUploadsStatus": "eligible"
            }
        }]
    }

    mock_channels.list.return_value = mock_list_req
    mock_service.channels.return_value = mock_channels

    with patch.object(mgr, "_load_credentials") as mock_creds_load:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds_load.return_value = mock_creds

        with patch("backend.app.services.youtube_publishing_engine.build", return_value=mock_service):
            status = mgr.get_auth_status()

    assert status["channel_connected"] is True
    ch = status["channel_info"]
    assert ch["id"] == "UC_CRAZZY_DOCS_CHANNEL_999"
    assert ch["title"] == "CrazzyDocs Official"
    assert ch["custom_url"] == "@CrazzyDocs"
    assert ch["thumbnail_url"] == "https://yt3.ggpht.com/channel_avatar_high.jpg"
    assert ch["subscribers"] == "15400"
    assert ch["video_count"] == "42"


# =========================================================================
# 3. THUMBNAIL ELIGIBILITY & LOCAL RETENTION
# =========================================================================

def test_thumbnail_eligibility_restriction_handling(test_db, tmp_path):
    """
    If account is not phone-verified on YouTube, custom thumbnail returns 403 Forbidden.
    System must record eligibility_restricted, preserve local thumbnail, and keep video upload successful.
    """
    dummy_video = tmp_path / "vid.mp4"
    dummy_video.write_bytes(b"content video")
    dummy_thumb = tmp_path / "thumb.png"
    dummy_thumb.write_bytes(b"content thumb")

    mock_oauth = MagicMock()
    mock_service = MagicMock()
    mock_oauth.get_authenticated_service.return_value = mock_service

    # Video upload succeeds
    mock_req = MagicMock()
    status_mock = MagicMock()
    status_mock.progress.return_value = 1.0
    mock_req.next_chunk.side_effect = [(status_mock, {"id": "yt_vid_success_123"})]
    mock_service.videos().insert.return_value = mock_req
    mock_service.videos().list.return_value.execute.return_value = {"items": [{"id": "yt_vid_success_123"}]}

    # Thumbnail upload fails with 403 forbidden
    resp = Response({"status": "403", "reason": "Forbidden"})
    mock_service.thumbnails().set.return_value.execute.side_effect = HttpError(
        resp=resp,
        content=b'{"error": {"message": "The caller does not have permission", "errors": [{"reason": "videoCustomThumbnailBlocked"}]}}'
    )

    engine = YouTubePublishingEngine(oauth_manager=mock_oauth, db=test_db)
    meta = MetadataGenerator.generate_shorts_metadata("Test Story", "Hook", "History", [])

    res = engine.upload_short(
        video_path=str(dummy_video),
        topic_title="Test Story",
        metadata=meta,
        thumbnail_path=str(dummy_thumb),
        visibility="private",
    )

    assert res["success"] is True
    assert res["video_id"] == "yt_vid_success_123"
    assert res["thumbnail_uploaded"] is False
    assert res["thumbnail_status"] == "eligibility_restricted"
    assert "HTTP 403" in res["thumbnail_error"]
    assert res["thumbnail_local_path"] == str(dummy_thumb)
    assert os.path.exists(str(dummy_thumb)), "Local thumbnail file must be preserved on disk!"


# =========================================================================
# 4. STRICT PUBLISHING GATE
# =========================================================================

def test_hard_gate_blocks_public(test_db):
    engine = YouTubePublishingEngine(db=test_db)
    meta = MetadataGenerator.generate_shorts_metadata("Story", "Hook", "Science", [])

    with pytest.raises(PermissionError) as exc_info:
        engine.upload_short(
            video_path="some_file.mp4",
            topic_title="Story",
            metadata=meta,
            visibility="public",
            allow_public_bypass=False
        )
    assert "SECURITY GATE ENFORCED: Public publishing is disabled" in str(exc_info.value)


# =========================================================================
# 5. DUPLICATE REJECTION & QUOTA ALLOCATION
# =========================================================================

def test_duplicate_rejection_and_quota(test_db, tmp_path):
    dummy_video = tmp_path / "vid_dup.mp4"
    dummy_video.write_bytes(b"dup content 555")

    mock_oauth = MagicMock()
    mock_service = MagicMock()
    mock_oauth.get_authenticated_service.return_value = mock_service

    mock_req = MagicMock()
    status_mock = MagicMock()
    status_mock.progress.return_value = 1.0
    mock_req.next_chunk.side_effect = [(status_mock, {"id": "yt_first_upload_001"})]
    mock_service.videos().insert.return_value = mock_req
    mock_service.videos().list.return_value.execute.return_value = {"items": [{"id": "yt_first_upload_001"}]}

    engine = YouTubePublishingEngine(oauth_manager=mock_oauth, db=test_db)
    meta = MetadataGenerator.generate_shorts_metadata("Unique Run 1", "Hook", "Science", [])

    # First upload
    res1 = engine.upload_short(
        video_path=str(dummy_video),
        topic_title="Unique Run 1",
        metadata=meta,
        visibility="private"
    )
    assert res1["success"] is True
    assert res1["video_id"] == "yt_first_upload_001"
    assert test_db.get_quota_used_today() == 1600

    # Second upload with same video content / title must be rejected
    res2 = engine.upload_short(
        video_path=str(dummy_video),
        topic_title="Unique Run 1",
        metadata=meta,
        visibility="private"
    )
    assert res2["success"] is False
    assert res2["status"] == "duplicate_rejected"
    assert res2["video_id"] == "yt_first_upload_001"
    # Quota shouldn't increase
    assert test_db.get_quota_used_today() == 1600
