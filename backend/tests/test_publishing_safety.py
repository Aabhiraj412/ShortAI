"""Automatic public release is permanently disabled; legacy 'automatic' settings are inert."""
import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.db import Base
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator
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


@pytest.fixture
def legacy_automatic_policy(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(control, "SessionLocal", factory)
    monkeypatch.setattr(sched_mod, "SessionLocal", factory)
    control._write_settings({control.PUBLISHING_POLICY_KEY: "automatic"})
    yield
    engine.dispose()


def test_legacy_automatic_value_is_preserved_but_inert(legacy_automatic_policy):
    assert control.get_stored_publishing_policy() == "automatic"
    assert control.get_publishing_policy() == "manual_approval"
    ok, info = control.is_automatic_publishing_authorized()
    assert ok is False
    assert info["stored_policy"] == "automatic"


def test_automatic_policy_cannot_be_selected(legacy_automatic_policy):
    with pytest.raises(ValueError, match="Automatic publishing has been removed"):
        control.set_publishing_policy("automatic", confirm=True)
    res = control.set_publishing_policy("manual_approval", confirm=True)
    assert res == {"mode": "manual_approval", "previous_mode": "automatic", "automatic_enabled": False}
    assert control.get_stored_publishing_policy() == "manual_approval"


def test_background_publishing_never_uploads(legacy_automatic_policy, monkeypatch):
    upload = MagicMock()
    monkeypatch.setattr(master_orchestrator.publishing_engine, "upload_short", upload)
    monkeypatch.setattr(master_orchestrator.publishing_engine.db, "get_quota_used_today", lambda: 0)
    import asyncio

    res = asyncio.run(sched_mod.run_cycle_publishing("cycle_1"))
    assert res["status"] == "withheld_safety_gate"
    assert res["publishing_mode"] == "manual_approval"
    upload.assert_not_called()


def test_approve_and_upload_is_private_even_with_legacy_automatic(legacy_automatic_policy, tmp_path):
    vpath = tmp_path / "short.mp4"
    vpath.write_bytes(b"fake-video")
    pdb = PublishingDatabase(db_path=str(tmp_path / "pub.db"))
    engine = YouTubePublishingEngine(db=pdb)
    engine.upload_short = MagicMock(return_value={"success": True, "video_id": "v1"})
    meta = YouTubeMetadata(title="T", description="D", tags=["t"], hashtags=["#Shorts"])
    lid = pdb.record_publication(
        PublicationRecord(
            id=None,
            topic_title="T",
            video_path=str(vpath),
            thumbnail_path=None,
            metadata_json=json.dumps(meta.__dict__),
            youtube_video_id=None,
            upload_status="pending_queue",
            visibility="private",
            video_hash=compute_file_sha256(str(vpath)),
        )
    )
    svc = YouTubePublisherService()
    svc.engine = engine
    svc.approve_and_upload(lid, confirmed=True)
    kwargs = engine.upload_short.call_args.kwargs
    assert kwargs["visibility"] == "private"
    assert kwargs["allow_public_bypass"] is False


def test_publishing_status_api_reports_legacy_value(legacy_automatic_policy, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main as main_module

    monkeypatch.setattr(main_module, "init_db", lambda: None)
    monkeypatch.setattr(
        control,
        "evaluate_publishing_readiness",
        lambda: {"publishing_capable": True, "autonomous_ready": True, "checks": {}, "blockers": []},
    )
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        body = client.get("/api/control-center/publishing").json()
        assert body["mode"] == "manual_approval"
        assert body["stored_mode"] == "automatic"
        assert body["legacy_automatic_ignored"] is True
        assert body["automatic_enabled"] is False
        assert "public" not in body["allowed_upload_privacy"]
        r = client.put("/api/control-center/publishing/policy", json={"mode": "automatic", "confirm": True})
        assert r.status_code == 409
