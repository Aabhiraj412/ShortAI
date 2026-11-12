import asyncio
import json
import sqlite3
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import settings
from backend.app.core.secure_storage import read_protected_file
from backend.app.database.db import Base, PersistentNotificationModel, StudioSettingModel
from backend.app import main as main_module
from backend.app.services.email_monitoring_service import EmailMonitoringService
from backend.app.services.notification_service import notification_service
from backend.app.scheduler import scheduler as scheduler_module
from backend.app.services.youtube_publishing_engine import (
    PublicationRecord,
    PublishingDatabase,
    YouTubeMetadata,
    YouTubeOAuthManager,
    YouTubePublishingEngine,
    compute_file_sha256,
)


def test_oauth_credentials_are_dpapi_protected(tmp_path):
    token_path = tmp_path / "token.json"
    manager = YouTubeOAuthManager(token_file=str(token_path))
    credentials = Credentials(
        token="access-secret",
        refresh_token="refresh-secret",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="local-client",
        client_secret="client-secret",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )

    manager.save_credentials(credentials)

    assert b"access-secret" not in token_path.read_bytes()
    assert manager._load_credentials().refresh_token == "refresh-secret"
    assert b"access-secret" in read_protected_file(str(token_path))


def test_oauth_manager_uses_configured_credential_paths():
    manager = YouTubeOAuthManager()

    assert manager.client_secrets_file == settings.YOUTUBE_CLIENT_SECRETS_FILE
    assert manager.token_file == settings.YOUTUBE_TOKEN_FILE


def test_expired_oauth_token_status_requests_reconnection_without_details(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.touch()
    manager = YouTubeOAuthManager(token_file=str(token_path))
    credentials = MagicMock()
    credentials.valid = False
    credentials.expired = True
    credentials.expiry = None
    credentials.refresh_token = "opaque-refresh"
    credentials.refresh.side_effect = RuntimeError("sensitive provider response")

    with patch.object(manager, "_load_credentials", return_value=credentials):
        status = manager.get_auth_status()

    assert status["reconnect_required"] is True
    assert status["status"] == "refresh_failed"
    assert "sensitive provider response" not in json.dumps(status)
    assert str(token_path) not in json.dumps(status)


def test_expired_oauth_token_without_refresh_requires_reconnection(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.touch()
    manager = YouTubeOAuthManager(token_file=str(token_path))
    credentials = MagicMock()
    credentials.valid = False
    credentials.expired = True
    credentials.expiry = None
    credentials.refresh_token = None

    with patch.object(manager, "_load_credentials", return_value=credentials):
        status = manager.get_auth_status()

    assert status["status"] == "reconnect_required"
    assert status["reconnect_required"] is True


def test_upload_session_survives_restart_and_completes_from_ledger(tmp_path):
    database = PublishingDatabase(str(tmp_path / "publishing.db"))
    video_path = tmp_path / "approved.mp4"
    video_path.write_bytes(b"synthetic mocked video")
    session_url = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=opaque-session"
    metadata = YouTubeMetadata("Test #Shorts", "Mock upload", ["test"], [])
    publication_id = database.record_publication(
        PublicationRecord(
            id=None,
            topic_title=metadata.title,
            video_path=str(video_path),
            thumbnail_path=None,
            metadata_json=json.dumps(metadata.__dict__),
            youtube_video_id=None,
            upload_status="pending_queue",
            visibility="private",
            video_hash=compute_file_sha256(str(video_path)),
        )
    )
    database.approve_upload(publication_id)
    database.update_upload_state(publication_id, progress_pct=38, resumable_uri=session_url)
    database.set_upload_status(publication_id, "uploading")
    assert database.recover_interrupted_uploads() == 1
    assert database.get_publication(publication_id)["upload_status"] == "upload_interrupted"
    assert database.get_upload_state(publication_id)["resumable_uri"] == session_url
    database.approve_upload(publication_id)

    raw = sqlite3.connect(database.db_path)
    stored_uri = raw.execute(
        "SELECT resumable_uri_protected FROM youtube_upload_state WHERE ledger_id=?",
        (publication_id,),
    ).fetchone()[0]
    raw.close()
    assert session_url not in stored_uri

    oauth = MagicMock()
    api = MagicMock()
    oauth.get_authenticated_service.return_value = api
    api.channels().list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "uploads-playlist"}}}]
    }
    api.playlistItems().list.return_value.execute.return_value = {"items": []}
    request = MagicMock()
    request.next_chunk.return_value = (None, {"id": "mock-video-id"})
    request.resumable_uri = None
    request._in_error_state = False
    api.videos().insert.return_value = request
    api.videos().list.return_value.execute.return_value = {"items": [{"id": "mock-video-id"}]}

    result = YouTubePublishingEngine(oauth_manager=oauth, db=database).upload_short(
        str(video_path),
        metadata.title,
        metadata,
        visibility="private",
        ledger_id=publication_id,
    )

    assert result["success"] is True
    assert request.resumable_uri == session_url
    assert request._in_error_state is True
    assert database.get_publication(publication_id)["upload_status"] == "uploaded_private"
    state = database.get_upload_state(publication_id)
    assert state["progress_pct"] == 100
    assert state["resumable_uri"] == ""
    assert state["verification_status"] == "verified"


def test_control_apis_work_directly_from_localhost_and_keep_manual_policy():
    client = TestClient(
        main_module.app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50000),
    )
    allowed = client.get("/api/stage-3-22/publishing/config")
    auto_upload = client.post(
        "/api/pipeline/run",
        json={"auto_upload": True},
    )

    assert allowed.status_code == 200
    assert allowed.json()["mode"] == "manual_approval"
    assert allowed.json()["supported_modes"]["automatic"]["enabled"] is False
    assert auto_upload.status_code == 409


def test_notifications_persist_and_deduplicate_without_real_email(tmp_path, monkeypatch):
    test_engine = create_engine(f"sqlite:///{tmp_path / 'notifications.db'}")
    Base.metadata.create_all(test_engine)
    test_sessions = sessionmaker(bind=test_engine)
    monkeypatch.setattr(
        "backend.app.services.email_monitoring_service.SessionLocal",
        test_sessions,
    )
    service = EmailMonitoringService()
    monkeypatch.setattr(service, "is_smtp_configured", lambda: True)
    monkeypatch.delenv("CRAZZYDOCS_ALERT_RECIPIENT", raising=False)
    monkeypatch.delenv("NOTIFICATION_RECIPIENT_EMAIL", raising=False)
    monkeypatch.setattr(
        notification_service,
        "get_public_email_configuration",
        lambda: {"recipient": "operator@example.test"},
    )
    with patch.object(notification_service, "notify_email", return_value=True) as send:
        first = service.dispatch_stage_notification(
            "UPLOAD_COMPLETED",
            "upload-17-complete",
            "Upload complete",
            {"upload_id": 17, "timestamp_ist": "2026-11-12T10:00:00+05:30"},
        )
        duplicate = service.dispatch_stage_notification(
            "UPLOAD_COMPLETED",
            "upload-17-complete",
            "Upload complete",
            {"upload_id": 17},
        )

    assert first["email_sent"] is True
    assert duplicate["status"] == "DUPLICATE_SUPPRESSED"
    assert send.call_count == 1
    assert send.call_args.kwargs["recipient"] == "operator@example.test"
    with test_sessions() as session:
        record = session.query(PersistentNotificationModel).one()
        assert record.status == "SENT"
        assert record.notification_type == "UPLOAD_COMPLETED"
    history = service.get_notification_history()
    assert history[0]["created_at_ist"].endswith("+05:30")
    test_engine.dispose()


def test_notification_configuration_encrypts_password(tmp_path, monkeypatch):
    test_engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    Base.metadata.create_all(test_engine)
    test_sessions = sessionmaker(bind=test_engine)
    monkeypatch.setattr(
        "backend.app.database.db.SessionLocal",
        test_sessions,
    )

    public = notification_service.save_email_configuration(
        {
            "host": "smtp.example.test",
            "port": 587,
            "username": "operator@example.test",
            "password": "test-only-smtp-password",
            "recipient": "operator@example.test",
            "security": "STARTTLS",
        }
    )

    assert public["configured"] is True
    assert "password" not in public
    with test_sessions() as session:
        stored = session.query(StudioSettingModel).filter_by(
            key="stage322_smtp_password_protected"
        ).one().value
    assert "test-only-smtp-password" not in stored
    assert notification_service._smtp_configuration()["password"] == "test-only-smtp-password"
    test_engine.dispose()


def test_daily_summary_scheduler_failure_dispatches_notification(monkeypatch):
    from backend.app.services.daily_publishing_summary import daily_publishing_summary_service

    def fail_summary(date_str=None):
        raise RuntimeError("mock scheduler failure")

    monkeypatch.setattr(
        daily_publishing_summary_service,
        "send_daily_summary",
        fail_summary,
    )
    with patch.object(
        scheduler_module.email_monitoring_service,
        "dispatch_stage_notification",
        return_value={"status": "SAVED_LOCAL_PENDING"},
    ) as dispatch:
        result = asyncio.run(
            scheduler_module.run_daily_summary_dispatch(test_date_str="2026-11-12")
        )

    assert result["status"] == "ERROR"
    assert "mock scheduler failure" not in result["error"]
    assert dispatch.call_args.args[0] == "SCHEDULER_FAILED"
    assert dispatch.call_args.args[1] == "stage322-scheduler-daily-summary-2026-11-12"
