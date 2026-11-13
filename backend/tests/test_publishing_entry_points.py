"""
Audit tests for every code path that can upload or publish to YouTube.

Rule: nothing becomes public without an explicit user action (Publish Now) or a
user-selected publication time; nothing uploads in the background; existing YouTube
IDs and public/scheduled states are preserved. All YouTube calls are mocked.
"""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main as main_module
from backend.app.database.db import Base
from backend.app.scheduler import scheduler as sched_mod
from backend.app.services import control_center_service as control
from backend.app.services.youtube_publishing_engine import (
    PublicationRecord,
    PublishingDatabase,
    YouTubeMetadata,
    YouTubePublishingEngine,
    compute_file_sha256,
)
from backend.app.services.youtube_service import YouTubePublisherService

APP_DIR = Path(__file__).resolve().parents[1] / "app"
IST = ZoneInfo("Asia/Kolkata")

# Reviewed call sites (file -> count). A new upload/publish path must be reviewed
# and added here deliberately; this test fails otherwise.
REVIEWED_CALL_SITES = {
    r"\.upload_short\(": {
        # upload_video (private only; public raises), approve_and_upload, edit_and_schedule, publish_now,
        # publish_after_qc (private + publishAt, only under the explicit automatic_private_scheduled policy)
        "services/youtube_service.py": 5,
        "orchestration/crazzydocs_master_orchestrator.py": 1,  # process_publishing_queue, only reached with execute_upload=True (never by the scheduler)
    },
    r"allow_public_bypass=True": {
        "services/youtube_service.py": 1,  # publish_now (explicit user action)
    },
    r"make_public_now=True": {
        "services/youtube_service.py": 1,  # publish_now (explicit user action)
    },
    r"videos\(\)\.insert\(": {
        "services/youtube_publishing_engine.py": 1,  # inside upload_short
    },
    r"execute_upload=True\s*\)": {},  # no caller asks the background queue to upload
}


def test_inventory_of_upload_and_publish_call_sites():
    found: dict[str, dict[str, int]] = {p: {} for p in REVIEWED_CALL_SITES}
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(APP_DIR).as_posix()
        for pattern in REVIEWED_CALL_SITES:
            n = len(re.findall(pattern, text))
            if n:
                found[pattern][rel] = n
    assert found == REVIEWED_CALL_SITES


# --- fixtures --------------------------------------------------------------------

def _mock_youtube(privacy="private", publish_at=None, video_id="yt_1"):
    service = MagicMock()
    req = MagicMock()
    req.resumable_uri = None
    req.next_chunk.return_value = (None, {"id": video_id})
    service.videos.return_value.insert.return_value = req
    service.videos.return_value.update.return_value.execute.return_value = {"id": video_id}
    service.thumbnails.return_value.set.return_value.execute.return_value = {"items": []}

    def _list(**_):
        st = {"privacyStatus": privacy}
        if publish_at:
            st["publishAt"] = publish_at
        m = MagicMock()
        m.execute.return_value = {"items": [{"id": video_id, "status": st}]}
        return m

    service.videos.return_value.list.side_effect = _list
    return service


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(control, "SessionLocal", factory)
    monkeypatch.setattr(sched_mod, "SessionLocal", factory)
    control._write_settings({control.PUBLISHING_POLICY_KEY: "automatic"})  # legacy value must be inert

    video = tmp_path / "short.mp4"
    video.write_bytes(b"fake-video-bytes")
    pdb = PublishingDatabase(db_path=str(tmp_path / "pub.db"))
    yt = YouTubePublishingEngine(db=pdb)
    oauth = MagicMock()
    oauth.get_authenticated_service.return_value = _mock_youtube()
    yt.oauth = oauth
    svc = YouTubePublisherService()
    svc.engine = yt
    svc.oauth = oauth
    monkeypatch.setattr("backend.app.services.youtube_service.youtube_publishing_engine", yt)
    yield {"svc": svc, "pdb": pdb, "oauth": oauth, "video": video}
    engine.dispose()


def _row(env, **over):
    meta = YouTubeMetadata(title="T", description="D", tags=["t"], hashtags=["#Shorts"])
    rec = dict(
        id=None, topic_title="T", video_path=str(env["video"]), thumbnail_path=None,
        metadata_json=json.dumps(meta.__dict__), youtube_video_id=None, upload_status="pending_queue",
        visibility="private", video_hash=compute_file_sha256(str(env["video"])),
    )
    rec.update(over)
    lid = env["pdb"].record_publication(PublicationRecord(**rec))
    if over.get("youtube_video_id") or over.get("upload_status") not in (None, "pending_queue"):
        env["pdb"].update_queue_metadata_and_schedule(
            lid, upload_status=rec["upload_status"], visibility=rec["visibility"],
            scheduled_timestamp=rec.get("scheduled_timestamp"),
        )
        conn = __import__("sqlite3").connect(env["pdb"].db_path)
        conn.execute("UPDATE publishing_ledger SET youtube_video_id=? WHERE id=?", (rec["youtube_video_id"], lid))
        conn.commit()
        conn.close()
    return lid


def _future_ist(days=2):
    return (datetime.now(IST) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


# --- entry points ----------------------------------------------------------------

def test_upload_video_cannot_publish_public(env):
    with pytest.raises(PermissionError, match="SECURITY GATE"):
        env["svc"].upload_video(str(env["video"]), "T", "D", ["t"], privacy_status="public")


def test_approve_and_upload_is_private_without_time(env):
    lid = _row(env)
    env["svc"].approve_and_upload(lid, confirmed=True)
    body = env["oauth"].get_authenticated_service.return_value.videos.return_value.insert.call_args.kwargs["body"]
    assert body["status"]["privacyStatus"] == "private"
    assert "publishAt" not in body["status"]


def test_approve_and_upload_rejects_public_row_without_time(env):
    lid = _row(env)
    env["pdb"].update_queue_metadata_and_schedule(lid, visibility="public")
    with pytest.raises(PermissionError, match="private only"):
        env["svc"].approve_and_upload(lid, confirmed=True)


def test_edit_and_schedule_requires_confirmation_and_uploads_private_with_publish_at(env):
    lid = _row(env)
    with pytest.raises(PermissionError):
        env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(), confirm=False)
    env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(), confirm=True)
    body = env["oauth"].get_authenticated_service.return_value.videos.return_value.insert.call_args.kwargs["body"]
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"].endswith("Z")


def test_publish_now_requires_confirmation(env):
    lid = _row(env)
    with pytest.raises(PermissionError):
        env["svc"].publish_now(lid, confirm=False)
    env["oauth"].get_authenticated_service.return_value.videos.return_value.insert.assert_not_called()


def test_api_empty_body_never_schedules_or_publishes(env, monkeypatch):
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    lid = _row(env)
    yt = env["oauth"].get_authenticated_service.return_value
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        assert client.post(f"/api/publishing/queue/{lid}/publish-now", json={}).status_code == 403
        assert client.post(f"/api/publishing/queue/{lid}/schedule", json={"scheduled_ist": _future_ist()}).status_code == 403
        assert client.post(f"/api/publishing/queue/{lid}/cancel-schedule", json={}).status_code == 403
        assert client.post("/api/pipeline/run", json={"auto_upload": True}).status_code == 409
    yt.videos.return_value.insert.assert_not_called()
    yt.videos.return_value.update.assert_not_called()


def test_legacy_pipeline_auto_upload_is_refused():
    from backend.app.workers.pipeline import pipeline
    with pytest.raises(PermissionError):
        asyncio.run(pipeline.run_pipeline(auto_upload=True))


def test_background_queue_never_uploads_even_when_called_by_scheduler(env, monkeypatch):
    from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator
    upload = MagicMock()
    monkeypatch.setattr(master_orchestrator.publishing_engine, "upload_short", upload)
    monkeypatch.setattr(master_orchestrator.publishing_engine.db, "get_quota_used_today", lambda: 0)
    for cycle in ("cycle_1", "cycle_2", "cycle_3", "cycle_4"):
        assert asyncio.run(sched_mod.run_cycle_publishing(cycle))["status"] == "withheld_safety_gate"
    upload.assert_not_called()


# --- preservation of existing YouTube state ------------------------------------

def test_metadata_edit_keeps_public_video_public(env):
    lid = _row(env, youtube_video_id="gPcd3gXF984", upload_status="uploaded_public", visibility="public")
    yt = _mock_youtube(privacy="public", video_id="gPcd3gXF984")
    env["oauth"].get_authenticated_service.return_value = yt
    env["svc"].update_queue_item(lid, title="New title")
    call = yt.videos.return_value.update.call_args.kwargs
    # Snippet-only update: YouTube keeps the current privacy; no status is sent at all.
    assert call["part"] == "snippet" and "status" not in call["body"]
    row = env["pdb"].get_publication(lid)
    assert (row["youtube_video_id"], row["upload_status"], row["visibility"]) == ("gPcd3gXF984", "uploaded_public", "public")


@pytest.mark.parametrize("action", ["reschedule", "cancel"])
def test_public_video_cannot_be_rescheduled_or_unscheduled(env, action):
    lid = _row(env, youtube_video_id="gPcd3gXF984", upload_status="uploaded_public", visibility="public")
    yt = env["oauth"].get_authenticated_service.return_value
    with pytest.raises(ValueError, match="already public"):
        if action == "reschedule":
            env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(), confirm=True)
        else:
            env["svc"].cancel_schedule(lid, confirm=True)
    yt.videos.return_value.update.assert_not_called()
    row = env["pdb"].get_publication(lid)
    assert (row["upload_status"], row["visibility"]) == ("uploaded_public", "public")


def test_metadata_edit_keeps_existing_schedule(env):
    publish_at = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lid = _row(env, youtube_video_id="yt_sched", upload_status="publication_scheduled",
               visibility="private", scheduled_timestamp=publish_at)
    yt = _mock_youtube(privacy="private", publish_at=publish_at, video_id="yt_sched")
    env["oauth"].get_authenticated_service.return_value = yt
    env["svc"].update_queue_item(lid, title="Edited")
    call = yt.videos.return_value.update.call_args.kwargs
    # Snippet-only update: YouTube keeps the existing privacy and publishAt untouched.
    assert call["part"] == "snippet" and "status" not in call["body"]
    row = env["pdb"].get_publication(lid)
    assert (row["youtube_video_id"], row["scheduled_timestamp"]) == ("yt_sched", publish_at)
    yt.videos.return_value.insert.assert_not_called()


# --- duplicate-upload protection -----------------------------------------------

def test_thumbnail_failure_then_reschedule_does_not_reupload(env):
    lid = _row(env)
    yt = env["oauth"].get_authenticated_service.return_value
    yt.thumbnails.return_value.set.return_value.execute.side_effect = RuntimeError("thumb down")
    thumb = env["video"].with_suffix(".png")
    thumb.write_bytes(b"\x89PNG\r\n\x1a\nx")
    env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(), confirm=True, thumbnail_path=str(thumb))
    assert yt.videos.return_value.insert.call_count == 1
    yt.thumbnails.return_value.set.return_value.execute.side_effect = None
    env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(3), confirm=True)
    assert yt.videos.return_value.insert.call_count == 1


def test_scheduling_update_failure_then_retry_does_not_reupload(env):
    lid = _row(env)
    yt = env["oauth"].get_authenticated_service.return_value
    env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(), confirm=True)
    yt.videos.return_value.update.return_value.execute.side_effect = RuntimeError("API 500")
    res = env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(3), confirm=True)
    assert res["result"]["success"] is False
    yt.videos.return_value.update.return_value.execute.side_effect = None
    res2 = env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(4), confirm=True)
    assert res2["result"]["success"] is True
    assert yt.videos.return_value.insert.call_count == 1
    assert env["pdb"].get_publication(lid)["youtube_video_id"] == "yt_1"
