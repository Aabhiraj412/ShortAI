import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.scheduler import scheduler as sched_mod
from backend.app.scheduler.schedule_profiles import get_active_profile, set_active_profile
from backend.app.services import control_center_service as control
from backend.app.services.youtube_publishing_engine import (
    PublicationRecord,
    PublishingDatabase,
    YouTubeMetadata,
    YouTubePublishingEngine,
    compute_file_sha256,
    ui_queue_status,
)
from backend.app.services.youtube_service import YouTubePublisherService, parse_ist_to_utc_iso
from backend.app.core.paths import PRODUCTION_DATABASE_PATH
from backend.app.database.db import Base


@pytest.fixture(autouse=True)
def isolated_settings_db(tmp_path, monkeypatch):
    """
    Every test here gets its own SQLite file for studio_settings / persistent_jobs.
    YouTubePublisherService reads the publishing policy through control.SessionLocal,
    which previously pointed at backend/studio.db.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_file = tmp_path / "settings.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(control, "SessionLocal", factory)
    monkeypatch.setattr(sched_mod, "SessionLocal", factory)
    assert db_file.resolve() != PRODUCTION_DATABASE_PATH.resolve()
    yield db_file
    engine.dispose()


def _make_dummy_media(tmp_dir: str):
    vpath = os.path.join(tmp_dir, "short.mp4")
    tpath = os.path.join(tmp_dir, "thumb.png")
    with open(vpath, "wb") as f:
        f.write(b"fake-mp4-video-content")
    with open(tpath, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nfake-png")
    return vpath, tpath


def _make_mock_youtube_service(remote_privacy="private", remote_publish_at=None, video_id="yt_vid_123"):
    service = MagicMock()
    insert_req = MagicMock()
    insert_req.resumable_uri = None
    insert_req.next_chunk.return_value = (None, {"id": video_id})
    service.videos.return_value.insert.return_value = insert_req
    service.videos.return_value.update.return_value.execute.return_value = {"id": video_id}
    service.thumbnails.return_value.set.return_value.execute.return_value = {"items": [{"default": {"url": "ok"}}]}

    def _list_side_effect(**kwargs):
        mock_exec = MagicMock()
        st = {"privacyStatus": remote_privacy}
        if remote_publish_at:
            st["publishAt"] = remote_publish_at
        mock_exec.execute.return_value = {
            "items": [{"id": video_id, "status": st, "snippet": {"thumbnails": {"default": {"url": "ok"}}}}]
        }
        return mock_exec

    service.videos.return_value.list.side_effect = _list_side_effect
    return service


def test_ist_to_utc_conversion_and_past_rejection():
    future_ist = (datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=2)).replace(
        hour=18, minute=30, second=0, microsecond=0
    )
    ist_str = future_ist.strftime("%Y-%m-%dT%H:%M")
    utc_iso = parse_ist_to_utc_iso(ist_str)
    # 18:30 IST == 13:00 UTC
    assert utc_iso.endswith("T13:00:00Z")

    past_ist = (datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    with pytest.raises(ValueError):
        parse_ist_to_utc_iso(past_ist)


def test_scheduler_configures_only_generation_and_reconciliation_no_pub_cron():
    sched_mod.configure_scheduler_jobs()
    job_ids = {j.id for j in sched_mod.scheduler.get_jobs()}
    assert any(jid.startswith("gen_") for jid in job_ids)
    assert not any(jid.startswith("pub_") for jid in job_ids)
    assert "youtube_status_reconciliation_job" in job_ids
    assert "daily_publishing_summary_job" in job_ids


def test_scheduler_reloads_when_db_revision_changes():
    orig_profile = get_active_profile()
    try:
        set_active_profile("FOUR_CYCLES_DAILY")
        sched_mod._last_db_revision_seen = None
        spec = control.preset_schedule("SINGLE_DAILY")
        spec["schedule_version"] = 99
        spec["revision"] = "rev_test_99"
        with patch.object(control, "_read_setting", return_value=json.dumps(spec)):
            with patch.object(control, "restore_active_schedule_profile") as mock_restore:
                with patch.object(sched_mod, "configure_scheduler_jobs") as mock_cfg:
                    reloaded = sched_mod.reload_profile_from_db_if_changed()
                    assert reloaded is True
                    mock_restore.assert_called_once()
                    mock_cfg.assert_called_once()
    finally:
        set_active_profile(orig_profile.name)
        sched_mod._last_db_revision_seen = None


def test_user_controlled_schedule_reschedule_cancel_and_publish_now(isolated_settings_db):
    # A legacy 'automatic' value in settings must not change anything: scheduling is
    # always private + publishAt, and only explicit publish_now makes a video public.
    control._write_settings({control.PUBLISHING_POLICY_KEY: "automatic"})
    assert control.get_publishing_policy() == "manual_approval"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = os.path.join(tmp, "test_pub.db")
        vpath, tpath = _make_dummy_media(tmp)
        pdb = PublishingDatabase(db_path=db_path)
        engine = YouTubePublishingEngine(db=pdb)
        oauth = MagicMock()
        future_utc = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        mock_yt = _make_mock_youtube_service(remote_privacy="private", remote_publish_at=future_utc)
        oauth.get_authenticated_service.return_value = mock_yt
        engine.oauth = oauth

        svc = YouTubePublisherService()
        svc.engine = engine
        svc.oauth = oauth

        meta = YouTubeMetadata(title="Initial Title", description="Desc", tags=["history"], hashtags=["#Shorts"])
        lid = pdb.record_publication(
            PublicationRecord(
                id=None,
                topic_title="Initial Title",
                video_path=vpath,
                thumbnail_path=tpath,
                metadata_json=json.dumps(meta.__dict__),
                youtube_video_id=None,
                upload_status="pending_queue",
                visibility="private",
                video_hash=compute_file_sha256(vpath),
                scheduled_timestamp=None,
            )
        )
        assert ui_queue_status("pending_queue") == "Ready to Schedule"

        # 1. Edit & Schedule
        future_ist = (datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        res_sched = svc.edit_and_schedule(
            lid,
            scheduled_ist=future_ist,
            confirm=True,
            title="Updated Short Title",
            description="Updated description",
            tags=["science", "shorts"],
        )
        assert res_sched["result"]["success"] is True
        assert res_sched["result"]["video_id"] == "yt_vid_123"
        assert res_sched["item"]["ui_status"] == "Scheduled"
        assert res_sched["item"]["youtube_privacy_status"] == "private"
        assert mock_yt.videos.return_value.insert.call_count == 1
        insert_body = mock_yt.videos.return_value.insert.call_args.kwargs["body"]
        assert insert_body["status"]["privacyStatus"] == "private"
        assert insert_body["status"]["publishAt"].endswith("Z")

        # 2. Reschedule existing video (must NOT re-upload via insert!)
        future_ist_2 = (datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
        res_resched = svc.edit_and_schedule(lid, scheduled_ist=future_ist_2, confirm=True)
        assert res_resched["result"]["success"] is True
        assert mock_yt.videos.return_value.insert.call_count == 1
        assert mock_yt.videos.return_value.update.call_count >= 1

        # 3. Cancel schedule -> returns to Ready to Schedule without deleting video_id
        res_cancel = svc.cancel_schedule(lid, confirm=True)
        assert res_cancel["item"]["ui_status"] == "Ready to Schedule"
        assert res_cancel["item"]["scheduled_ist_display"] == "Not Scheduled"

        # 4. Publish Now -> flips to public and verifies
        mock_yt_pub = _make_mock_youtube_service(remote_privacy="public", remote_publish_at=None)
        oauth.get_authenticated_service.return_value = mock_yt_pub
        res_pub = svc.publish_now(lid, confirm=True)
        assert res_pub["item"]["ui_status"] == "Published"
        assert res_pub["item"]["youtube_privacy_status"] == "public"


def test_thumbnail_retry_does_not_duplicate_video_upload():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = os.path.join(tmp, "test_retry.db")
        vpath, tpath = _make_dummy_media(tmp)
        pdb = PublishingDatabase(db_path=db_path)
        engine = YouTubePublishingEngine(db=pdb)
        oauth = MagicMock()
        mock_yt = _make_mock_youtube_service(remote_privacy="private")
        mock_yt.thumbnails.return_value.set.return_value.execute.side_effect = RuntimeError("Thumb network hiccup")
        oauth.get_authenticated_service.return_value = mock_yt
        engine.oauth = oauth

        meta = YouTubeMetadata(title="Thumb Retry Test", description="Desc", tags=["test"], hashtags=["#Shorts"])
        lid = pdb.record_publication(
            PublicationRecord(
                id=None,
                topic_title="Thumb Retry Test",
                video_path=vpath,
                thumbnail_path=tpath,
                metadata_json=json.dumps(meta.__dict__),
                youtube_video_id=None,
                upload_status="approved",
                visibility="private",
                video_hash=compute_file_sha256(vpath),
            )
        )
        res1 = engine.upload_short(vpath, "Thumb Retry Test", meta, thumbnail_path=tpath, visibility="private", ledger_id=lid)
        assert res1["video_id"] == "yt_vid_123"
        assert mock_yt.videos.return_value.insert.call_count == 1

        # Retry thumbnail now succeeds without calling insert again!
        mock_yt.thumbnails.return_value.set.return_value.execute.side_effect = None
        mock_yt.thumbnails.return_value.set.return_value.execute.return_value = {"items": []}
        pdb.set_upload_status(lid, "approved")
        res2 = engine.upload_short(vpath, "Thumb Retry Test", meta, thumbnail_path=tpath, visibility="private", ledger_id=lid)
        assert res2["video_id"] == "yt_vid_123"
        assert res2["thumbnail_uploaded"] is True
        assert mock_yt.videos.return_value.insert.call_count == 1


def test_api_publishing_queue_endpoints():
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    resp = client.get("/api/publishing/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["timezone"] == "Asia/Kolkata"

    # Past schedule rejected
    bad_resp = client.post(
        "/api/publishing/queue/999999/schedule",
        json={"scheduled_ist": "2020-01-01T10:00", "confirm": True},
    )
    assert bad_resp.status_code == 400
