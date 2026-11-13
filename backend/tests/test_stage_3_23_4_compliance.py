"""Stage 3.23.4 compliance & integration test suite."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.app.content.creative_identity_policy import CreativeIdentityPolicy
from backend.app.core.process_utils import hidden_subprocess_kwargs
from backend.app.main import _redact_log_secrets, app
from backend.app.rendering.lemo_opuscar_adapter import PINNED_COMMIT
from backend.app.rendering.lemo_opuscar_film_builder import LemoOpuscarFilmBuilder
from backend.app.services import control_center_service
from backend.app.services.qc_service import QualityControlEngine
from backend.app.services.youtube_publishing_engine import (
    PublicationRecord,
    PublishingDatabase,
    YouTubeMetadata,
    YouTubePublishingEngine,
    build_publishing_timeline,
    normalize_lifecycle_state,
)


def test_lemo_opuscar_film_builder_creates_deterministic_package(tmp_path: Path):
    img = tmp_path / "sample.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    storyboard = [
        {"beat_number": i + 1, "visual_prompt": f"Scene {i+1}", "spoken_text": f"Line {i+1}", "image_path": str(img)}
        for i in range(4)
    ]
    manifest = LemoOpuscarFilmBuilder.build_film_package(
        prod_dir=tmp_path / "prod_1",
        job_id="job_lemo_1",
        topic="How Fiber Optics Bend Light",
        script_data={"title": "How Fiber Optics Bend Light", "hook": "Light trapped in glass."},
        storyboard=storyboard,
        duration_sec=52.0,
        category="Science",
    )
    assert manifest["renderer"] == "lemo-opuscar"
    assert manifest["pinned_commit"] == PINNED_COMMIT
    assert manifest["beat_count"] == 4
    html_text = Path(manifest["index_html"]).read_text(encoding="utf-8")
    assert "window.DUR" in html_text
    assert "window.render" in html_text
    assert "thumbnail" in html_text


def test_renderer_compliance_blocks_fallback_engines():
    ok, issues = CreativeIdentityPolicy.validate_production_renderer(
        "lemo-opuscar",
        execution_context="PRODUCTION",
        telemetry={"renderer": "lemo-opuscar", "fallback_used": False},
    )
    assert ok and not issues

    for bad in ("HybridVisualEngine", "Python Canvas (Pillow Ken Burns)", "Explainer Animation", "CleanSlateRenderer"):
        bad_ok, bad_issues = CreativeIdentityPolicy.validate_production_renderer(
            bad,
            execution_context="PRODUCTION",
        )
        assert not bad_ok
        assert bad_issues

    fb_ok, fb_issues = CreativeIdentityPolicy.validate_production_renderer(
        "lemo-opuscar",
        execution_context="PRODUCTION",
        telemetry={"renderer": "HybridVisualEngine", "fallback_used": True},
    )
    assert not fb_ok
    assert fb_issues


def test_qc_service_rejects_unapproved_renderer(tmp_path: Path):
    qc = QualityControlEngine()
    fake_vid = tmp_path / "video.mp4"
    fake_vid.write_bytes(b"0" * 100)
    passed, errors, checks = qc.run_qc_checks(
        video_path=str(fake_vid),
        thumbnail_path=str(tmp_path / "missing.png"),
        metadata={"title": "Test", "category_id": "27"},
        rendering_engine="HybridVisualEngine",
        execution_context="PRODUCTION",
    )
    assert not passed
    assert "FAIL" in str(checks.get("renderer_compliance", "")) or any("ffprobe" in e.lower() for e in errors)


def test_hidden_subprocess_kwargs_windows():
    if os.name == "nt":
        kw = hidden_subprocess_kwargs()
        assert kw.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW
        si = kw.get("startupinfo")
        assert si is not None
        assert si.dwFlags & subprocess.STARTF_USESHOWWINDOW


def test_scheduled_private_upload_and_timeline(tmp_path: Path):
    db_path = tmp_path / "pub_test.db"
    db = PublishingDatabase(db_path=str(db_path))
    vid = tmp_path / "short.mp4"
    vid.write_bytes(b"fake_mp4_bytes_for_hash")
    thumb = tmp_path / "thumb.png"
    thumb.write_bytes(b"fake_png_bytes")

    meta = YouTubeMetadata(
        title="Scheduled Test Short",
        description="Test description",
        tags=["shorts", "science"],
        hashtags=["#shorts"],
    )
    rec = PublicationRecord(
        id=None,
        topic_title="Scheduled Test Topic",
        video_path=str(vid),
        thumbnail_path=str(thumb),
        metadata_json=json.dumps(meta.__dict__),
        youtube_video_id=None,
        upload_status="pending_queue",
        visibility="private",
        video_hash="abc",
        scheduled_timestamp="2099-01-01T12:30:00Z",
    )
    # Fix video_hash to match actual file
    from backend.app.services.youtube_publishing_engine import compute_file_sha256
    rec.video_hash = compute_file_sha256(str(vid))
    lid = db.record_publication(rec)

    mock_auth = MagicMock()
    mock_service = MagicMock()
    mock_insert = MagicMock()
    mock_insert.resumable_uri = ""
    mock_insert.next_chunk.return_value = (None, {"id": "yt_sched_123"})
    mock_service.videos.return_value.insert.return_value = mock_insert
    mock_service.videos.return_value.list.return_value.execute.return_value = {
        "items": [{
            "id": "yt_sched_123",
            "status": {"privacyStatus": "private", "publishAt": "2099-01-01T12:30:00Z"},
            "snippet": {"thumbnails": {"default": {"url": "https://example.com/t.jpg"}}},
        }]
    }
    mock_service.channels.return_value.list.return_value.execute.return_value = {"items": []}
    mock_service.thumbnails.return_value.set.return_value.execute.return_value = {"items": [{"default": {}}]}
    mock_auth.get_authenticated_service.return_value = mock_service

    engine = YouTubePublishingEngine()
    engine.oauth = mock_auth
    engine.db = db

    # Without auto_policy_authorized, pending_queue still blocks
    with pytest.raises(PermissionError):
        engine.upload_short(
            video_path=str(vid),
            topic_title="Scheduled Test Topic",
            metadata=meta,
            thumbnail_path=str(thumb),
            visibility="private",
            ledger_id=lid,
            publish_at_iso="2099-01-01T12:30:00Z",
            auto_policy_authorized=False,
        )

    # With auto_policy_authorized=True, uploads private + publishAt + thumbnail
    res = engine.upload_short(
        video_path=str(vid),
        topic_title="Scheduled Test Topic",
        metadata=meta,
        thumbnail_path=str(thumb),
        visibility="private",
        ledger_id=lid,
        publish_at_iso="2099-01-01T12:30:00Z",
        auto_policy_authorized=True,
    )
    assert res["success"] is True
    assert res["status"] == "publication_scheduled"
    assert res["visibility"] == "private"
    assert res["thumbnail_uploaded"] is True

    # Verify normalized lifecycle state & 9-step timeline
    stored = db.get_publication(lid)
    assert normalize_lifecycle_state(stored["upload_status"], stored["visibility"], stored["scheduled_timestamp"]) == "PUBLICATION_SCHEDULED"
    tl = build_publishing_timeline(stored)
    assert len(tl) == 9

    # Verify scheduled promotion when YouTube reports public
    mock_service.videos.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "yt_sched_123", "status": {"privacyStatus": "public"}}]
    }
    prom = engine.verify_scheduled_publications()
    assert prom["promoted"] == 1
    assert db.get_publication(lid)["upload_status"] == "uploaded_public"


def test_publication_schedule_utc_ist_rollover_and_validation():
    spec = control_center_service.preset_publication_schedule("TWICE_DAILY")
    val = control_center_service.validate_publication_schedule(spec)
    assert val["valid"] is True
    assert len(val["preview_next_slots"]) == 5

    # Test midnight rollover at 23:55 IST -> next slot should be next day 09:00 IST = 03:30 UTC
    late_ist = datetime(2026, 11, 12, 23, 55, tzinfo=ZoneInfo("Asia/Kolkata"))
    slots = control_center_service.compute_upcoming_publication_slots(spec, count=1, now_ist=late_ist)
    assert slots[0]["ist_display"] == "2026-11-13 09:00 IST"
    assert slots[0]["utc_iso"] == "2026-11-13T03:30:00Z"


def test_storage_validation_rejects_duplicate_and_relative_paths(tmp_path: Path):
    bad_rel = {
        "production_output_root": "relative/path",
        "final_video_dir": str(tmp_path / "v"),
        "thumbnail_dir": str(tmp_path / "t"),
        "temp_render_dir": str(tmp_path / "r"),
        "archive_dir": str(tmp_path / "a"),
        "quarantine_dir": str(tmp_path / "q"),
    }
    res_rel = control_center_service.validate_storage_config(bad_rel)
    assert res_rel["valid"] is False

    good = {
        "production_output_root": str(tmp_path / "p"),
        "final_video_dir": str(tmp_path / "v"),
        "thumbnail_dir": str(tmp_path / "t"),
        "temp_render_dir": str(tmp_path / "r"),
        "archive_dir": str(tmp_path / "a"),
        "quarantine_dir": str(tmp_path / "q"),
    }
    res_good = control_center_service.validate_storage_config(good)
    assert res_good["valid"] is True


def test_secret_redaction_and_dashboard_endpoints():
    masked = _redact_log_secrets("token=Bearer eyJhbGciOiJIUzI1NiJ9.abc.def key=AIzaSyDummySecretKey1234567890123")
    assert "eyJhbGciOiJIUzI1NiJ9" not in masked
    assert "AIzaSyDummySecretKey" not in masked
    assert "***REDACTED***" in masked

    # Publication/control APIs are localhost-only: call them as the local dashboard does.
    assert TestClient(app).get("/api/control-center/publication-schedule").status_code == 403
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r_dash = client.get("/dashboard")
    assert r_dash.status_code == 200
    assert "Lemo-Opuscar" in r_dash.text
    # Simplified scheduler: production cycles with a target publish time; no publication-slot editor.
    assert "Production Schedule — Asia/Kolkata" in r_dash.text
    assert "Publication Schedule Editor" not in r_dash.text

    r_pub = client.get("/api/control-center/publication-schedule")
    assert r_pub.status_code == 200
    assert "upcoming_slots" in r_pub.json()

    r_pcfg = client.get("/api/control-center/production-config")
    assert r_pcfg.status_code == 200
    assert r_pcfg.json()["renderer"] == "lemo-opuscar"

    r_scfg = client.get("/api/control-center/storage-config")
    assert r_scfg.status_code == 200
    assert r_scfg.json()["validation"]["valid"] is True

    r_procs = client.get("/api/control-center/processes")
    assert r_procs.status_code == 200
    assert "processes" in r_procs.json()

    r_logs = client.get("/api/control-center/logs?limit=20")
    assert r_logs.status_code == 200
    assert "entries" in r_logs.json()
