"""
Final pre-integration safety regressions (audit round 2).

1. Existing public / scheduled videos keep their privacy and publishAt.
2. Publishing-queue integrity (exactly-once queueing, no re-uploads, explicit consent).
3. API security of every state-changing publication endpoint.
4. Retention never removes protected production artifacts.

Every YouTube call is a MagicMock; databases and media live under tmp_path.
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main as main_module
from backend.app.database.db import Base, JobModel
from backend.app.services.media_lifecycle_manager import (
    AssetCategory,
    ClassifiedFile,
    MediaLifecycleManager,
)
from backend.app.services.youtube_publishing_engine import (
    PublicationRecord,
    PublishingDatabase,
    YouTubeMetadata,
    compute_file_sha256,
)
from backend.app.services.youtube_service import parse_ist_to_utc_iso

# Shared mocked-YouTube fixtures (imported by name only; no tests are re-collected).
from test_publishing_entry_points import _future_ist, _mock_youtube, _row, env  # noqa: F401

PUBLIC_ID = "pub_vid_1"
SCHED_ID = "sched_vid_1"


def _yt(env):
    return env["oauth"].get_authenticated_service.return_value


def _ledger_rows(env):
    conn = sqlite3.connect(env["pdb"].db_path)
    try:
        return conn.execute("SELECT id, upload_status, youtube_video_id FROM publishing_ledger").fetchall()
    finally:
        conn.close()


def _future_utc(days=3):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _public_row(env):
    lid = _row(env, youtube_video_id=PUBLIC_ID, upload_status="uploaded_public", visibility="public")
    env["oauth"].get_authenticated_service.return_value = _mock_youtube(privacy="public", video_id=PUBLIC_ID)
    return lid


def _scheduled_row(env, publish_at):
    lid = _row(env, youtube_video_id=SCHED_ID, upload_status="publication_scheduled",
               visibility="private", scheduled_timestamp=publish_at)
    env["oauth"].get_authenticated_service.return_value = _mock_youtube(
        privacy="private", publish_at=publish_at, video_id=SCHED_ID)
    return lid


def _state(env, lid):
    r = env["pdb"].get_publication(lid)
    return (r["youtube_video_id"], r["upload_status"], r["visibility"], r["scheduled_timestamp"])


# =============================================================================
# 1. Existing public-video preservation
# =============================================================================

@pytest.mark.parametrize("edit", ["title", "description", "tags", "thumbnail"])
def test_public_video_metadata_edit_preserves_public_privacy(env, edit):
    lid = _public_row(env)
    before = _state(env, lid)
    kwargs = {
        "title": {"title": "A new title"},
        "description": {"description": "A new description"},
        "tags": {"tags": ["history", "shorts"]},
    }.get(edit)
    if edit == "thumbnail":
        thumb = env["video"].with_suffix(".png")
        thumb.write_bytes(b"\x89PNG\r\n\x1a\nthumb")
        kwargs = {"thumbnail_path": str(thumb)}
    env["svc"].update_queue_item(lid, **kwargs)

    yt = _yt(env)
    call = yt.videos.return_value.update.call_args.kwargs
    assert call["part"] == "snippet", "metadata edits must not send a status part"
    assert "status" not in call["body"]
    if edit == "thumbnail":
        assert yt.thumbnails.return_value.set.call_args.kwargs["videoId"] == PUBLIC_ID
    else:
        expected = {"title": "A new title", "description": "A new description",
                    "tags": ["history", "shorts"]}[edit]
        assert call["body"]["snippet"][edit] == expected
    yt.videos.return_value.insert.assert_not_called()
    after = _state(env, lid)
    assert after[:3] == before[:3] == (PUBLIC_ID, "uploaded_public", "public")


@pytest.mark.parametrize("kind", ["public", "scheduled"])
def test_failed_metadata_update_changes_neither_privacy_nor_status(env, kind):
    publish_at = _future_utc()
    lid = _public_row(env) if kind == "public" else _scheduled_row(env, publish_at)
    before = _state(env, lid)
    yt = _yt(env)
    yt.videos.return_value.update.return_value.execute.side_effect = RuntimeError("HTTP 500 backend error")

    env["svc"].update_queue_item(lid, title="Will fail remotely")

    after = _state(env, lid)
    assert after == before, "a failed remote update must leave id, status, visibility and publishAt unchanged"
    assert "update failed" in (env["pdb"].get_publication(lid)["error_details"] or "")
    yt.videos.return_value.insert.assert_not_called()
    # The video is not turned into an approvable 'failed' row.
    if kind == "public":
        with pytest.raises(ValueError, match="already public"):
            env["svc"].approve_and_upload(lid, confirmed=True)
    yt.videos.return_value.insert.assert_not_called()


def test_rescheduling_already_public_video_is_rejected(env):
    lid = _public_row(env)
    before = _state(env, lid)
    for call in (
        lambda: env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(), confirm=True),
        lambda: env["svc"].cancel_schedule(lid, confirm=True),
    ):
        with pytest.raises(ValueError, match="already public"):
            call()
    _yt(env).videos.return_value.update.assert_not_called()
    assert _state(env, lid) == before


def test_reschedule_refused_when_youtube_already_published_a_stale_scheduled_row(env):
    """Ledger still says publication_scheduled but publishAt passed and YouTube made it public."""
    publish_at = _future_utc()
    lid = _scheduled_row(env, publish_at)
    env["oauth"].get_authenticated_service.return_value = _mock_youtube(privacy="public", video_id=SCHED_ID)
    before = _state(env, lid)
    with pytest.raises(ValueError, match="already public"):
        env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(5), confirm=True)
    with pytest.raises(ValueError, match="already public"):
        env["svc"].cancel_schedule(lid, confirm=True)
    _yt(env).videos.return_value.update.assert_not_called()
    assert _state(env, lid) == before


def test_reschedule_refused_when_youtube_state_cannot_be_verified(env):
    publish_at = _future_utc()
    lid = _scheduled_row(env, publish_at)
    yt = _yt(env)
    yt.videos.return_value.list.side_effect = RuntimeError("network down")
    before = _state(env, lid)
    with pytest.raises(ValueError, match="Could not verify"):
        env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(5), confirm=True)
    yt.videos.return_value.update.assert_not_called()
    assert _state(env, lid) == before


def test_scheduled_video_keeps_publish_at_on_edits_and_failed_reschedule(env):
    publish_at = _future_utc(3)
    lid = _scheduled_row(env, publish_at)
    yt = _yt(env)

    env["svc"].update_queue_item(lid, title="Edited", description="d", tags=["x"])
    assert "status" not in yt.videos.return_value.update.call_args.kwargs["body"]
    assert _state(env, lid) == (SCHED_ID, "publication_scheduled", "private", publish_at)

    yt.videos.return_value.update.return_value.execute.side_effect = RuntimeError("HTTP 503")
    res = env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(6), confirm=True)
    assert res["result"]["success"] is False
    assert _state(env, lid) == (SCHED_ID, "publication_scheduled", "private", publish_at), \
        "a failed reschedule must keep the original publishAt in the ledger"


def test_explicit_reschedule_is_the_only_way_publish_at_changes(env):
    publish_at = _future_utc(3)
    lid = _scheduled_row(env, publish_at)
    new_ist = _future_ist(6)
    new_utc = parse_ist_to_utc_iso(new_ist)
    env["svc"].edit_and_schedule(lid, scheduled_ist=new_ist, confirm=True)
    call = _yt(env).videos.return_value.update.call_args.kwargs
    assert call["part"] == "snippet,status"
    assert call["body"]["status"]["privacyStatus"] == "private"
    assert call["body"]["status"]["publishAt"] == new_utc
    assert _state(env, lid)[3] == new_utc
    _yt(env).videos.return_value.insert.assert_not_called()


# =============================================================================
# 2. Publishing-queue integrity
# =============================================================================

def test_qc_approved_video_enters_queue_exactly_once(env):
    meta = YouTubeMetadata(title="Exactly once", description="D", tags=["t"], hashtags=["#Shorts"])
    first = env["svc"].queue_approved_production(1, str(env["video"]), None, meta)
    second = env["svc"].queue_approved_production(1, str(env["video"]), None,
                                                 YouTubeMetadata(**{**meta.__dict__}))
    assert first["id"] == second["id"]
    assert len(_ledger_rows(env)) == 1
    # A queue-handoff retry with the same rendered bytes also returns the same row,
    # even after it moved on to approved / uploaded.
    env["pdb"].update_queue_metadata_and_schedule(first["id"], upload_status="uploaded_private")
    again = env["pdb"].record_publication(PublicationRecord(
        id=None, topic_title="Retitled retry", video_path=str(env["video"]), thumbnail_path=None,
        metadata_json="{}", youtube_video_id=None, upload_status="pending_queue", visibility="private",
        video_hash=compute_file_sha256(str(env["video"])),
    ))
    assert again == first["id"]
    assert len(_ledger_rows(env)) == 1


def test_editing_metadata_never_creates_another_queue_entry(env):
    lid = _row(env)
    for i in range(3):
        env["svc"].update_queue_item(lid, title=f"Title v{i}", description=f"d{i}", tags=[f"t{i}"])
    assert [r[0] for r in _ledger_rows(env)] == [lid]
    _yt(env).videos.return_value.insert.assert_not_called()


def test_retrying_thumbnail_upload_does_not_reupload_video(env):
    lid = _row(env)
    yt = _yt(env)
    thumb = env["video"].with_suffix(".png")
    thumb.write_bytes(b"\x89PNG\r\n\x1a\nthumb")
    yt.thumbnails.return_value.set.return_value.execute.side_effect = RuntimeError("thumb 500")
    env["svc"].approve_and_upload(lid, confirmed=True)
    assert yt.videos.return_value.insert.call_count == 1
    assert env["pdb"].get_publication(lid)["youtube_video_id"] == "yt_1"

    yt.thumbnails.return_value.set.return_value.execute.side_effect = None
    env["svc"].update_queue_item(lid, thumbnail_path=str(thumb))   # thumbnail retry
    assert yt.thumbnails.return_value.set.call_count >= 1
    assert yt.videos.return_value.insert.call_count == 1
    assert [r[2] for r in _ledger_rows(env)] == ["yt_1"]


def test_retrying_scheduling_does_not_reupload_video(env):
    lid = _row(env)
    yt = _yt(env)
    env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(), confirm=True)
    yt.videos.return_value.update.return_value.execute.side_effect = RuntimeError("HTTP 500")
    env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(3), confirm=True)
    yt.videos.return_value.update.return_value.execute.side_effect = None
    env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(4), confirm=True)
    assert yt.videos.return_value.insert.call_count == 1
    assert len(_ledger_rows(env)) == 1


@pytest.mark.parametrize("status", ["pending_queue", "failed", "upload_interrupted", "awaiting_auth",
                                    "uploaded_private", "publication_scheduled"])
@pytest.mark.parametrize("action", ["approve", "schedule", "publish_now", "edit"])
def test_existing_youtube_id_never_triggers_duplicate_upload(env, status, action):
    lid = _row(env, youtube_video_id="existing_id", upload_status=status, visibility="private",
               scheduled_timestamp=_future_utc() if status == "publication_scheduled" else None)
    yt = _mock_youtube(privacy="private", video_id="existing_id")
    env["oauth"].get_authenticated_service.return_value = yt
    try:
        if action == "approve":
            env["svc"].approve_and_upload(lid, confirmed=True)
        elif action == "schedule":
            env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist(), confirm=True)
        elif action == "publish_now":
            env["svc"].publish_now(lid, confirm=True)
        else:
            env["svc"].update_queue_item(lid, title="x")
    except (ValueError, PermissionError):
        pass  # refusing is fine; uploading again is not
    yt.videos.return_value.insert.assert_not_called()
    assert [r[2] for r in _ledger_rows(env)] == ["existing_id"]


def test_publish_now_requires_explicit_confirmation(env):
    lid = _row(env)
    for call in (lambda: env["svc"].publish_now(lid), lambda: env["svc"].publish_now(lid, confirm=False),
                 lambda: env["svc"].publish_now(lid, confirm="true")):
        with pytest.raises(PermissionError):
            call()
    _yt(env).videos.return_value.insert.assert_not_called()
    _yt(env).videos.return_value.update.assert_not_called()


@pytest.mark.parametrize("bad", ["", "   ", "not-a-date", "2020-01-01T10:00",
                                 (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()])
def test_scheduled_publishing_requires_valid_future_timestamp(env, bad):
    lid = _row(env)
    with pytest.raises(ValueError):
        env["svc"].edit_and_schedule(lid, scheduled_ist=bad, confirm=True)
    _yt(env).videos.return_value.insert.assert_not_called()
    assert env["pdb"].get_publication(lid)["scheduled_timestamp"] is None


def test_scheduling_and_cancel_require_explicit_confirmation(env):
    lid = _row(env)
    with pytest.raises(PermissionError):
        env["svc"].edit_and_schedule(lid, scheduled_ist=_future_ist())
    with pytest.raises(PermissionError):
        env["svc"].cancel_schedule(lid)
    _yt(env).videos.return_value.insert.assert_not_called()


def test_no_background_path_can_bypass_manual_approval(env):
    # The engine itself refuses an unapproved ledger row unless a caller claims an
    # automatic-policy authorisation -- and no application code does.
    lid = _row(env)
    meta = YouTubeMetadata(title="T", description="D", tags=["t"], hashtags=["#Shorts"])
    with pytest.raises(PermissionError, match="explicitly approved"):
        env["svc"].engine.upload_short(str(env["video"]), "T", meta, ledger_id=lid)
    app_dir = Path(main_module.__file__).resolve().parent
    offenders = [p for p in app_dir.rglob("*.py")
                 if "auto_policy_authorized=True" in p.read_text(encoding="utf-8", errors="ignore")]
    assert offenders == []
    from backend.app.services import control_center_service as control
    authorized, info = control.is_automatic_publishing_authorized()
    assert authorized is False and info["policy"] == "manual_approval"  # even with a stored legacy 'automatic'
    _yt(env).videos.return_value.insert.assert_not_called()


# =============================================================================
# 3. API security
# =============================================================================

PUBLICATION_PATH_MARKERS = ("publish", "youtube", "queue", "upload", "publication", "stage-3-22")


def _routes():
    return [r for r in main_module.app.routes if isinstance(r, APIRoute)]


def _is_guarded(route):
    return any(getattr(d.call, "__name__", "") == "require_local_control" for d in route.dependant.dependencies)


def _write_routes():
    return [r for r in _routes() if r.methods - {"GET", "HEAD"}]


def _concrete(path):
    return path.replace("{ledger_id}", "1").replace("{production_id}", "x").replace("{job_id}", "x")


def test_every_state_changing_endpoint_requires_localhost():
    unguarded = sorted(f"{','.join(sorted(r.methods))} {r.path}" for r in _write_routes() if not _is_guarded(r))
    assert unguarded == []
    pub = [r for r in _write_routes() if any(m in r.path for m in PUBLICATION_PATH_MARKERS)]
    assert len(pub) >= 15, "publication endpoint inventory unexpectedly small"


@pytest.fixture
def api(env, monkeypatch):
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    # app.main imports the service as `app.services.*` (a second module object next to
    # `backend.app.services.*`), so the mocked engine is installed under both names.
    import app.services.youtube_publishing_engine as app_engine_mod
    import app.services.youtube_service as app_service_mod
    for mod in (app_engine_mod, app_service_mod):
        monkeypatch.setattr(mod, "youtube_publishing_engine", env["svc"].engine)
    monkeypatch.setattr("backend.app.services.youtube_publishing_engine.youtube_publishing_engine",
                        env["svc"].engine)
    monkeypatch.setattr(main_module, "_run_approved_upload",
                        lambda lid, c, p: env["svc"].approve_and_upload(lid, c, p))
    return env


@pytest.mark.parametrize("client_host,base_url", [
    ("203.0.113.7", "http://127.0.0.1:8000"),   # remote client
    ("127.0.0.1", "http://evil.example:8000"),  # DNS-rebinding style Host header
])
def test_publication_writes_rejected_from_non_localhost(api, client_host, base_url):
    lid = _row(api)
    yt = _yt(api)
    pub = [r for r in _write_routes() if any(m in r.path for m in PUBLICATION_PATH_MARKERS)]
    with TestClient(main_module.app, base_url=base_url, client=(client_host, 50000)) as client:
        for r in pub:
            for method in sorted(r.methods - {"GET", "HEAD"}):
                resp = client.request(method, _concrete(r.path).replace("/1/", f"/{lid}/"),
                                      json={"confirm": True, "scheduled_ist": _future_ist(), "mode": "manual_approval"})
                assert resp.status_code == 403, f"{method} {r.path} -> {resp.status_code}"
        # Read-only publication APIs are local-only too (they expose channel/OAuth state).
        assert client.get("/api/publishing/queue").status_code == 403
    yt.videos.return_value.insert.assert_not_called()
    yt.videos.return_value.update.assert_not_called()
    assert _state(api, lid) == (None, "pending_queue", "private", None)


CONFIRM_ENDPOINTS = [
    ("POST", "/api/publishing/queue/{lid}/publish-now", {}),
    ("POST", "/api/publishing/queue/{lid}/cancel-schedule", {}),
    ("POST", "/api/publishing/queue/{lid}/schedule", {"scheduled_ist": None}),
    ("POST", "/api/stage-3-22/publishing/{lid}/approve", {}),
    ("POST", "/api/stage-3-22/youtube/disconnect", {}),
    ("PUT", "/api/control-center/publishing/policy", {"mode": "manual_approval"}),
]


def _payload(extra):
    return {k: (_future_ist() if k == "scheduled_ist" else v) for k, v in extra.items()}


@pytest.mark.parametrize("method,path,extra", CONFIRM_ENDPOINTS)
def test_missing_or_false_confirmation_is_rejected(api, method, path, extra):
    lid = _row(api)
    yt = _yt(api)
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as c:
        for body in (_payload(extra), {**_payload(extra), "confirm": False}):
            resp = c.request(method, path.format(lid=lid), json=body)
            # 409 = policy endpoint's "confirmation required" conflict.
            assert resp.status_code in (400, 403, 409, 422), f"{method} {path} {body} -> {resp.status_code}"
            assert "confirm" in resp.text.lower() or resp.status_code == 422
    yt.videos.return_value.insert.assert_not_called()
    yt.videos.return_value.update.assert_not_called()
    assert _state(api, lid)[1] == "pending_queue"


@pytest.mark.parametrize("method,path,extra", CONFIRM_ENDPOINTS)
@pytest.mark.parametrize("wrong", ["confirmed", "confirmation", "approved"])
def test_misspelt_confirmation_field_gets_clear_validation_error(api, method, path, extra, wrong):
    lid = _row(api)
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as c:
        resp = c.request(method, path.format(lid=lid), json={**_payload(extra), wrong: True})
    assert resp.status_code == 422
    text = json.dumps(resp.json())
    assert wrong in text and '\\"confirm\\": true' in text
    _yt(api).videos.return_value.insert.assert_not_called()


def test_dashboard_sends_the_expected_confirmation_field():
    html = (Path(main_module.__file__).resolve().parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "confirmed:" not in html and '"confirmed"' not in html


def test_read_only_and_authorized_dashboard_operations_still_work(api):
    lid = _row(api)
    sched_lid = None
    yt = _yt(api)
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as c:
        q = c.get("/api/publishing/queue")
        assert q.status_code == 200 and q.json()["count"] == 1
        assert c.get(f"/api/stage-3-22/publishing/{lid}").status_code == 200
        assert c.get("/api/control-center/publishing").json()["automatic_enabled"] is False
        assert c.get("/api/control-center/deployment").status_code == 200

        assert c.put(f"/api/publishing/queue/{lid}", json={"title": "Edited by dashboard"}).status_code == 200
        assert yt.videos.return_value.insert.call_count == 0

        r = c.post(f"/api/stage-3-22/publishing/{lid}/approve", json={"confirm": True})
        assert r.status_code == 200 and r.json()["status"] == "approved"
        assert yt.videos.return_value.insert.call_count == 1
        assert yt.videos.return_value.insert.call_args.kwargs["body"]["status"]["privacyStatus"] == "private"

        r = c.post(f"/api/publishing/queue/{lid}/schedule", json={"scheduled_ist": _future_ist(), "confirm": True})
        assert r.status_code == 200
        r = c.post(f"/api/publishing/queue/{lid}/cancel-schedule", json={"confirm": True})
        assert r.status_code == 200
        r = c.post(f"/api/publishing/queue/{lid}/publish-now", json={"confirm": True})
        assert r.status_code == 200
        assert yt.videos.return_value.update.call_args.kwargs["body"]["status"]["privacyStatus"] == "public"
        assert c.post("/api/publishing/verify-scheduled").status_code == 200
    assert yt.videos.return_value.insert.call_count == 1, "the dashboard flow uploaded the video exactly once"
    assert sched_lid is None


# =============================================================================
# 4. Retention safety
# =============================================================================

def _retention_ws(tmp_path):
    ws = tmp_path / "ws"
    (ws / "media").mkdir(parents=True)
    db = tmp_path / "studio.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    engine.dispose()
    PublishingDatabase(db_path=str(db))  # creates publishing_ledger
    return ws, str(db)


def _video(ws, rel, payload):
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def _add_job(db, job_id, status, checkpoint):
    engine = create_engine(f"sqlite:///{db}")
    with sessionmaker(bind=engine)() as s:
        s.add(JobModel(id=job_id, date_str="2026-11-13", status=status,
                       checkpoint_data_json=json.dumps(checkpoint)))
        s.commit()
    engine.dispose()


def _add_ledger(db, video, status, yt_id=None):
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            "INSERT INTO publishing_ledger (topic_title, video_path, metadata_json, youtube_video_id, "
            "upload_status, visibility, video_hash, upload_timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (f"topic {video.name}", str(video), "{}", yt_id, status, "private",
             hashlib.sha256(video.read_bytes()).hexdigest(), "2026-11-13T00:00:00Z"),
        )
    conn.close()


def _protected_cases(ws, db):
    """Builds one file per protected scenario; each name also trips a cleanup heuristic."""
    cases = {}
    v = _video(ws, "media/video/postrender_failed_final.mp4", b"A" * 2048)
    _add_job(db, "cycle_1_20261113_0000", "FAILED",
             {"video_path": str(v), "cycle_state": {"preserved_artifacts": {"video_path": str(v)}}})
    cases["post_render_failed_preserved"] = v

    v = _video(ws, "media/video/handoff_pending_final.mp4", b"B" * 2048)
    _add_job(db, "cycle_2_20261113_0000", "HANDOFF_PENDING", {"video_path": str(v)})
    cases["handoff_pending"] = v

    v = _video(ws, "media/video/retry_scheduled_final.mp4", b"C" * 2048)
    # Preserved path recorded only in preserved_artifacts (not top-level video_path).
    _add_job(db, "cycle_3_20261113_0000", "RETRY_SCHEDULED",
             {"cycle_state": {"preserved_artifacts": {"video_path": str(v)}}})
    cases["retry_scheduled_preserved"] = v

    v = _video(ws, "media/video/queued_concat_final.mp4", b"D" * 2048)
    _add_ledger(db, v, "pending_queue")
    cases["publishing_queue_entry"] = v

    v = _video(ws, "media/video/uploaded_failed_meta.mp4", b"E" * 2048)
    _add_ledger(db, v, "failed", yt_id="has_youtube_id")
    cases["existing_youtube_id"] = v
    return cases


def test_retention_classifies_every_protected_case_as_protected(tmp_path):
    ws, db = _retention_ws(tmp_path)
    cases = _protected_cases(ws, db)
    mgr = MediaLifecycleManager(workspace_root=str(ws), db_path=db, quarantine_dir=str(ws / "media" / "quarantine"))
    deps = mgr.collect_dependencies()
    for name, path in cases.items():
        # Pre-seed an identical file "scanned earlier": the duplicate rule must not win.
        seen = {compute_file_sha256(str(path)): "media/earlier_identical_copy.mp4"}
        cf = mgr.classify_file(str(path), deps, seen)
        assert cf.is_protected and not cf.is_quarantinable, f"{name}: {cf.category} {cf.reason}"
        assert cf.category == AssetCategory.CATEGORY_D_APPROVED_DELIVERABLES, name


@pytest.mark.parametrize("routine", ["monthly_audit", "weekly_temp_quarantine"])
def test_cleanup_routines_never_move_protected_files(tmp_path, monkeypatch, routine):
    ws, db = _retention_ws(tmp_path)
    cases = _protected_cases(ws, db)
    # Identical unreferenced copies sit in the media root, which is walked first.
    for path in cases.values():
        (ws / "media" / f"aaa_dup_{path.name}").write_bytes(path.read_bytes())
    old = (datetime.now() - timedelta(days=60)).timestamp()
    for p in ws.rglob("*.mp4"):
        os.utime(p, (old, old))
    mgr = MediaLifecycleManager(workspace_root=str(ws), db_path=db, quarantine_dir=str(ws / "media" / "quarantine"))
    monkeypatch.setattr(mgr, "generate_storage_report", lambda **_: {})
    if routine == "monthly_audit":
        mgr.monthly_audit(dry_run=False)
    else:
        mgr.weekly_temp_quarantine(retention_days=7, dry_run=False)
    for name, path in cases.items():
        assert path.is_file(), f"{routine} moved protected file ({name})"


def test_quarantine_rechecks_database_even_for_a_stale_unprotected_classification(tmp_path):
    ws, db = _retention_ws(tmp_path)
    cases = _protected_cases(ws, db)
    mgr = MediaLifecycleManager(workspace_root=str(ws), db_path=db, quarantine_dir=str(ws / "media" / "quarantine"))
    stale = [
        ClassifiedFile(relative_path=f"media/video/{p.name}", absolute_path=str(p),
                       category=AssetCategory.CATEGORY_F_FAILED_AND_INCOMPLETE_OUTPUTS,
                       size_bytes=p.stat().st_size, modified_at="2020-01-01T00:00:00+00:00",
                       reason="stale", is_protected=False, is_quarantinable=True, referenced_by=[])
        for p in cases.values()
    ]
    res = mgr.quarantine_files(stale, dry_run=False)
    assert res["quarantined_count"] == 0
    assert res["skipped_protected_count"] == len(cases)
    assert all(p.is_file() for p in cases.values())
