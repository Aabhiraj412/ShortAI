"""Stage 4.03: independent channel publishing.

A. channel identity and isolation (both upload checkpoints, every English entry point, localized media kept out)
B. Hindi QC and publication integrity (QC hash, path + SHA-256 before upload, re-render withdrawal, history kept)
C. authorization and per-video approval (operator only, audited, atomic, independent of each other)
D. quota: channel attribution in the one project-wide ledger, Hindi caps and the English reserve
"""
import ast
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.channels import publish_guard as pg
from backend.app.channels.registry import BLOCKED, ChannelError, ChannelRegistry
from backend.app.localization import publishing as hp
from backend.app.services import youtube_quota as yq

from test_hindi_publishing_and_thumbnails import HI_ID, JOB, NOW, FakeQuota, FakeService, authorize, env  # noqa: F401

APP = Path(__file__).resolve().parents[1] / "app"
EN_ID = "UC_english_master"


# ---------------------------------------------------------------------------------------------------------------
# A. identity and isolation
# ---------------------------------------------------------------------------------------------------------------
def _english(tmp_path, *, stored=EN_ID, live=EN_ID, status="ACTIVE", video_dir="productions"):
    from backend.app.services.youtube_publishing_engine import (PublicationRecord, PublishingDatabase,
                                                                YouTubeMetadata, YouTubePublishingEngine,
                                                                compute_file_sha256)
    db = PublishingDatabase(str(tmp_path / "publishing.db"))
    reg = ChannelRegistry(db.db_path, backend_dir=tmp_path, oauth_factory=lambda *a: None)
    reg.ensure()
    c = sqlite3.connect(db.db_path)
    c.execute("UPDATE channel_registry SET youtube_channel_id=?, status=? WHERE channel_key='en'", (stored, status))
    c.commit()
    c.close()
    video = tmp_path / "media" / video_dir / "job" / "final.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"synthetic video")
    meta = YouTubeMetadata("A Test Short #Shorts", "desc", ["t"], [])
    lid = db.record_publication(PublicationRecord(
        id=None, topic_title=meta.title, video_path=str(video), thumbnail_path=None,
        metadata_json=json.dumps(meta.__dict__), youtube_video_id=None, upload_status="pending_queue",
        visibility="private", video_hash=compute_file_sha256(str(video))))
    db.approve_upload(lid)
    api = MagicMock()
    api.channels().list.return_value.execute.return_value = {"items": [{"id": live, "contentDetails": {
        "relatedPlaylists": {"uploads": "UU"}}}]}
    api.playlistItems().list.return_value.execute.return_value = {"items": []}
    req = MagicMock()
    req.next_chunk.return_value = (None, {"id": "vid-1"})
    req.resumable_uri = None
    req._in_error_state = False
    api.videos().insert.return_value = req
    api.videos().list.return_value.execute.return_value = {"items": [{"id": "vid-1"}]}
    oauth = MagicMock()
    oauth.get_authenticated_service.return_value = api
    eng = YouTubePublishingEngine(oauth_manager=oauth, db=db)
    eng._registry = reg

    def upload(**kw):
        api.videos().insert.reset_mock()
        api.channels().list.reset_mock()
        return eng.upload_short(str(video), meta.title, meta, visibility="private", ledger_id=lid, **kw)
    return eng, reg, api, upload, video


def test_english_upload_with_matching_identity_uses_no_extra_call(tmp_path):
    eng, reg, api, upload, _ = _english(tmp_path)
    res = upload()
    assert res["success"] is True and api.videos().insert.call_count == 1
    assert api.channels().list.call_count == 1               # the duplicate check's call, reused for identity
    assert reg.get("en")["status"] == "ACTIVE"


def test_english_identity_mismatch_is_refused_and_blocks_english(tmp_path):
    eng, reg, api, upload, _ = _english(tmp_path, live="UC_someone_else")
    res = upload()
    assert res["success"] is False and res["status"] == "identity_refused"
    assert api.videos().insert.call_count == 0
    en = reg.get("en")
    assert en["status"] == BLOCKED and en["youtube_channel_id"] == EN_ID      # master never switched
    assert eng.db.get_publication(1)["upload_status"] == "approved"           # left queued, nothing recorded


def test_english_blocked_channel_is_refused_by_the_guard(tmp_path):
    eng, reg, api, upload, _ = _english(tmp_path, status=BLOCKED)
    res = upload()
    assert res["status"] == "publish_guard_refused" and "not active" in res["error"]
    assert api.videos().insert.call_count == 0


def test_english_channel_without_an_id_in_the_response_is_refused(tmp_path):
    eng, reg, api, upload, _ = _english(tmp_path)
    api.channels().list.return_value.execute.return_value = {"items": []}
    assert upload()["status"] == "identity_refused" and api.videos().insert.call_count == 0


def test_localized_media_never_enters_or_leaves_through_the_english_queue(tmp_path, monkeypatch):
    from backend.app.services.youtube_publishing_engine import YouTubeMetadata
    from backend.app.services.youtube_service import YouTubePublisherService as YouTubeService
    monkeypatch.setattr(pg, "_media_dir", lambda: tmp_path / "media")
    eng, reg, api, upload, video = _english(tmp_path, video_dir="localizations/hi")
    res = upload()
    assert res["status"] == "publish_guard_refused" and "localized media" in res["error"]
    assert api.videos().insert.call_count == 0
    svc = YouTubeService.__new__(YouTubeService)
    svc.engine = eng
    with pytest.raises(ValueError, match="Localized media"):
        svc.queue_approved_production(1, str(video), None, YouTubeMetadata("t", "d", [], []))


def _insert_calls():
    out = []
    for p in APP.rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
            if "videos().insert(" in line and not line.lstrip().startswith("#"):
                out.append((p.relative_to(APP).as_posix(), i))
    return out


def test_each_channel_has_one_insert_and_the_guard_runs_before_it():
    calls = _insert_calls()
    assert sorted(f for f, _ in calls) == ["localization/publishing.py", "services/youtube_publishing_engine.py"]
    eng = (APP / "services" / "youtube_publishing_engine.py").read_text(encoding="utf-8")
    body = eng[eng.index("    def upload_short("):]
    assert body.index("self._english_publish_guard(") < body.index("videos().insert(")
    pub = (APP / "localization" / "publishing.py").read_text(encoding="utf-8")
    up = pub[pub.index("    def upload(self, pid"):]
    assert up.index("self.eligibility(row)") < up.index("self._check_identity(svc)") < up.index("videos().insert(")


def test_every_english_entry_point_reaches_upload_short():
    """automatic (orchestrator), manual (approve / publish now / edit and schedule), retry and recovery all call
    upload_short; no other module uploads English video."""
    callers = []
    for p in APP.rglob("*.py"):
        if "upload_short(" in p.read_text(encoding="utf-8"):
            callers.append(p.relative_to(APP).as_posix())
    assert set(callers) == {"services/youtube_publishing_engine.py", "services/youtube_service.py",
                            "orchestration/crazzydocs_master_orchestrator.py"}


def test_hindi_token_that_is_the_english_channel_is_refused_and_blocked(env):
    c = sqlite3.connect(env.db)
    c.execute("UPDATE channel_registry SET youtube_channel_id=? WHERE channel_key='en'", (EN_ID,))
    c.commit()
    c.close()
    _ready(env)
    env.svc.channel_id = EN_ID
    with pytest.raises(hp.PublishingError, match="does not belong"):
        env.pub.tick()
    assert not env.svc.inserted and env.reg.get("hi")["status"] == BLOCKED


# ---------------------------------------------------------------------------------------------------------------
# B. QC hash, integrity, re-render withdrawal
# ---------------------------------------------------------------------------------------------------------------
def _ready(env, approve=True):
    authorize(env)
    env.pub.queue()
    env.pub.schedule_item(1)
    if approve:
        env.pub.approve(1, actor="test")
    env.clock.t = NOW + timedelta(hours=7)


def _video(env) -> Path:
    return Path(env.pub.get(1)["video_path"])


def test_unapproved_video_is_never_uploaded_even_when_authorized(env, monkeypatch):
    monkeypatch.setattr(hp.HindiPublisher, "_thumbnails", lambda self: [])
    _ready(env, approve=False)
    res = env.pub.tick()
    assert res["ran"] is True and res["upload"]["uploaded"] is False and not env.svc.inserted
    assert "approved" in " ".join(env.pub.queue()["items"][0]["eligibility"]["reasons"])


def test_changed_file_after_approval_is_refused(env):
    _ready(env)
    _video(env).write_bytes(b"\x01" * 64)                    # file replaced after approval
    res = env.pub.upload(1)
    assert res["uploaded"] is False and "SHA-256 mismatch" in res["reason"] and not env.svc.inserted
    assert env.pub.get(1)["state"] == "SCHEDULED" and env.pub.get(1)["attempts"] == 0   # not an attempt


def test_file_outside_the_hindi_folder_is_refused_even_via_symlink_or_dotdot(env, tmp_path):
    _ready(env)
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"\x00" * 64)
    for path in (str(outside), str(_video(env).parent / ".." / ".." / ".." / ".." / "elsewhere.mp4")):
        c = sqlite3.connect(env.db)
        c.execute("UPDATE hindi_publications SET video_path=? WHERE id=1", (path,))
        c.commit()
        c.close()
        assert "outside the Hindi media folder" in env.pub.upload(1)["reason"]
    link = _video(env).parent / "link.mp4"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        return                                                # symlinks need privileges on Windows
    assert pg.resolve_inside(link, env.pub.media_dir / "localizations" / "hi") is None


def test_qc_pass_records_the_output_hash(tmp_path):
    from backend.app.localization.pipeline import HindiLocalizer
    db = tmp_path / "studio.db"
    sqlite3.connect(db).close()
    reg = ChannelRegistry(str(db), backend_dir=tmp_path, oauth_factory=lambda *a: None)
    reg.ensure()
    media = tmp_path / "media"
    mp4 = media / "localizations" / "hi" / "j" / "hindi_j.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.write_bytes(b"rendered")
    c = sqlite3.connect(db)
    c.execute("INSERT INTO content_localizations (id, source_channel_key, source_job_id, source_identity, "
              "target_channel_key, language, status, artifacts_json, created_at_utc, updated_at_utc) "
              "VALUES (1,'en','j','j','hi','hi','IN_PROGRESS',?, 'x','x')", (json.dumps({"final_mp4": str(mp4)}),))
    c.commit()
    c.close()
    loc = HindiLocalizer(reg, media_dir=media, ffmpeg="x", ffprobe="x", workspace_root=tmp_path,
                         qc_fn=lambda l, wd, a: {"passed": True, "classification": "PASS", "creative": [],
                                                 "technical": []})
    loc.work_dir = lambda l: mp4.parent
    assert loc.run_step(1, "qc")["status"] == "QC_PASSED"
    row = loc.get(1)
    assert row["artifacts"]["final_mp4_sha256"] == pg.sha256_file(mp4)
    assert row["qc"]["final_mp4_sha256"] == pg.sha256_file(mp4)


def test_rerender_withdraws_pending_publication_and_needs_fresh_qc_and_approval(env):
    _ready(env)
    before = env.pub.get(1)
    assert before["approved_at_utc"] and before["scheduled_at_utc"]
    env.pub.withdraw_for_localization(1, reason="re-opened from render", actor="dashboard")
    with sqlite3.connect(env.db) as c:                        # what reopen() does next: back to PLANNED
        c.execute("UPDATE content_localizations SET status='PLANNED' WHERE id=1")
    w = env.pub.get(1)
    assert w["state"] == "WITHDRAWN" and w["approved_at_utc"] is None and w["scheduled_at_utc"] is None
    with sqlite3.connect(env.db) as c:
        ev = c.execute("SELECT detail FROM hindi_publication_events WHERE event='WITHDRAWN'").fetchone()[0]
    assert json.loads(ev)["previous_scheduled_at_utc"] == before["scheduled_at_utc"]   # kept in the record
    env.pub.tick()
    assert not env.svc.inserted and env.pub.get(1)["state"] == "WITHDRAWN"
    # new render passes QC again -> back as DRAFT/SCHEDULED with the new hash and NO approval
    _video(env).write_bytes(b"\x02" * 64)
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE content_localizations SET status='QC_PASSED', artifacts_json=? WHERE id=1",
                  (json.dumps({"final_mp4": str(_video(env)), "final_mp4_sha256": pg.sha256_file(_video(env))}),))
    env.pub.sync()
    r = env.pub.get(1)
    assert r["state"] in ("DRAFT", "SCHEDULED") and r["approved_at_utc"] is None
    assert r["qc_sha256"] == pg.sha256_file(_video(env))
    assert env.pub.upload(1)["uploaded"] is False and not env.svc.inserted


def test_reopen_refused_while_uploading_or_after_upload_and_history_kept(env):
    _ready(env)
    for state in ("UPLOADING", "UPLOADED"):
        with sqlite3.connect(env.db) as c:
            c.execute("UPDATE hindi_publications SET state=?, youtube_video_id=? WHERE id=1",
                      (state, "hv9" if state == "UPLOADED" else None))
        with pytest.raises(hp.PublishingError):
            env.pub.withdraw_for_localization(1, reason="x")
        assert env.pub.get(1)["state"] == state
    assert env.pub.get(1)["youtube_video_id"] == "hv9"


def test_reopen_of_a_localization_withdraws_through_the_localizer(env):
    from backend.app.localization.pipeline import HindiLocalizer
    _ready(env)
    loc = HindiLocalizer(env.reg, media_dir=env.pub.media_dir, ffmpeg="x", ffprobe="x", publisher=env.pub,
                         workspace_root=env.db.parent)
    loc.work_dir = lambda l: env.db.parent / "wd"
    loc.reopen(1, actor="dashboard", from_step="render")
    assert env.pub.get(1)["state"] == "WITHDRAWN"
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE hindi_publications SET state='UPLOADED', youtube_video_id='hv1' WHERE id=1")
        c.execute("UPDATE content_localizations SET status='QC_PASSED' WHERE id=1")
    with pytest.raises(ValueError, match="already uploaded"):
        loc.reopen(1, actor="dashboard", from_step="render")


def test_needs_check_keeps_its_state_but_loses_approval(env):
    _ready(env)
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE hindi_publications SET state='NEEDS_CHECK' WHERE id=1")
    out = env.pub.withdraw_for_localization(1, reason="x")
    r = env.pub.get(1)
    assert out["state"] == "NEEDS_CHECK" and r["state"] == "NEEDS_CHECK" and r["approved_at_utc"] is None


def test_metadata_edit_clears_approval(env):
    _ready(env)
    env.pub.update_metadata(1, title="New title | Hinglish", description="d", tags=["a"])
    assert env.pub.get(1)["approved_at_utc"] is None


def test_migration_keeps_existing_rows_unchanged_and_unapproved(tmp_path):
    db = tmp_path / "studio.db"
    c = sqlite3.connect(db)
    c.executescript(hp.SCHEMA)                                  # the pre-4.03 table (no new columns)
    rows = [(i, 100 + i, f"cycle_{i}_20261128_1000", "hi", f"/m/{i}.mp4", f"T{i}", "", "[]", "27", None, "SCHEDULED",
             f"2026-11-29T0{i}:30:00Z", None, 0, None, "{}", "c", "u") for i in (1, 2, 3, 4, 5)]
    c.executemany("INSERT INTO hindi_publications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    before = [tuple(r) for r in c.execute("SELECT * FROM hindi_publications ORDER BY id")]
    c.close()
    reg = ChannelRegistry(str(db), backend_dir=tmp_path, oauth_factory=lambda *a: None)
    hp.HindiPublisher(str(db), registry=reg, media_dir=tmp_path, quota=FakeQuota())
    hp.HindiPublisher(str(db), registry=reg, media_dir=tmp_path, quota=FakeQuota())    # idempotent
    c = sqlite3.connect(db)
    after = [tuple(r) for r in c.execute("SELECT * FROM hindi_publications ORDER BY id")]
    cols = [r[1] for r in c.execute("PRAGMA table_info(hindi_publications)")]
    c.close()
    assert [a[:18] for a in after] == before                    # every old value unchanged
    assert all(v is None for a in after for v in a[18:])        # new columns empty: not approved, no hash
    assert cols[18:] == [n for n, _ in hp.PUBLICATION_COLUMNS]


# ---------------------------------------------------------------------------------------------------------------
# C. authorization and approval
# ---------------------------------------------------------------------------------------------------------------
def test_authorization_is_atomic_audited_and_revocable(env):
    authorize(env)
    with sqlite3.connect(env.db) as c:        # start unauthorized, channel active and verified
        c.execute("UPDATE channel_registry SET publishing_config_json=? WHERE channel_key='hi'",
                  (json.dumps({"schedule": env.pub.schedule()}),))
    with pytest.raises(ChannelError):
        env.reg.set_publishing_authorization("en", True, actor="op", reason="x")      # English: existing policy
    with pytest.raises(ChannelError):
        env.reg.set_publishing_authorization("hi", True, actor="op", reason="x" * 301)  # reason bounded
    orig = env.reg._event

    def broken(*a, **k):
        raise sqlite3.OperationalError("event write failed")
    env.reg._event = broken
    with pytest.raises(sqlite3.OperationalError):
        env.reg.set_publishing_authorization("hi", True, actor="op", reason="go")
    env.reg._event = orig
    assert env.reg.publishing_authorization("hi")["authorized"] is False               # no event, no change
    a = env.reg.set_publishing_authorization("hi", True, actor="op", reason="go live")
    assert a["authorized"] and a["authorized_by"] == "op" and a["events"][0]["event"] == "PUBLISHING_AUTHORIZED"
    assert env.pub.schedule()["enabled"] is True                                       # schedule untouched
    r = env.reg.set_publishing_authorization("hi", False, actor="op", reason="stop")
    assert r["authorized"] is False and r["events"][0]["event"] == "PUBLISHING_REVOKED"


def test_revoking_after_scheduling_blocks_the_next_tick(env, monkeypatch):
    monkeypatch.setattr(hp.HindiPublisher, "_thumbnails", lambda self: [])
    _ready(env)
    env.reg.set_publishing_authorization("hi", False, actor="op", reason="stop")
    assert env.pub.tick()["ran"] is False and not env.svc.inserted
    assert env.pub.get(1)["state"] == "SCHEDULED"


def test_approval_and_authorization_are_independent(env):
    authorize(env)
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE channel_registry SET publishing_config_json=? WHERE channel_key='hi'",
                  (json.dumps({"schedule": env.pub.schedule()}),))
    env.pub.queue()
    env.pub.schedule_item(1)
    env.pub.approve(1, actor="op")
    assert env.reg.publishing_authorization("hi")["authorized"] is False     # approving never authorizes
    env.pub.unapprove(1, actor="op")
    env.reg.set_publishing_authorization("hi", True, actor="op", reason="go")
    assert env.pub.get(1)["approved_at_utc"] is None                          # authorizing never approves
    assert "approved" in " ".join(env.pub.eligibility(env.pub.get(1))["reasons"])


def test_only_the_registry_function_writes_publishing_authorized():
    writers = []
    for p in APP.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            src = ast.get_source_segment(p.read_text(encoding="utf-8"), node) or ""
            if ("publishing_authorized=" in src.replace(" ", "") or '["publishing_authorized"]=' in src.replace(" ", "")
                    or "'publishing_authorized':True" in src.replace(" ", "")
                    or '"publishing_authorized":True' in src.replace(" ", "")):
                writers.append((p.relative_to(APP).as_posix(), node.name))
    assert writers == [("channels/registry.py", "set_publishing_authorization")]


def test_authorization_route_needs_local_csrf_confirmation_and_phrase(env, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    import backend.app.channels.registry as reg_mod
    authorize(env)
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE channel_registry SET publishing_config_json='{}' WHERE channel_key='hi'")
    monkeypatch.setattr(reg_mod, "get_registry", lambda: env.reg)
    audits = []
    monkeypatch.setattr(main, "_audit_dashboard_action", lambda a, d: audits.append((a, d)))
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    hdr = {"Origin": "http://127.0.0.1:8000", main.AUTONOMY_CSRF_HEADER: main._AUTONOMY_CSRF_TOKEN}
    url = "/api/channels/hi/publishing-authorization"
    body = {"action": "authorize", "confirm": True, "phrase": "AUTHORIZE HINDI PUBLISHING", "reason": "go"}
    assert local.post(url, json=body, headers={"Origin": "http://127.0.0.1:8000"}).status_code == 403   # no CSRF
    assert local.post(url, json=dict(body, confirm=False), headers=hdr).status_code == 400
    assert local.post(url, json=dict(body, phrase="yes"), headers=hdr).status_code == 400
    remote = TestClient(main.app, base_url="http://example.com", client=("10.1.2.3", 5000))
    assert remote.post(url, json=body, headers=hdr).status_code in (400, 403)
    assert local.post("/api/channels/en/publishing-authorization", json=body, headers=hdr).status_code == 400
    assert env.reg.publishing_authorization("hi")["authorized"] is False and not audits
    r = local.post(url, json=body, headers=hdr)
    assert r.status_code == 200 and r.json()["authorized"] is True
    assert audits and audits[0][0] == "channel_publishing_authorize"
    r = local.post(url, json={"action": "revoke", "confirm": True, "reason": "stop"}, headers=hdr)
    assert r.status_code == 200 and r.json()["authorized"] is False


def test_unified_queue_shows_approval_and_blocking_reasons(env):
    from backend.app.services.unified_publishing_queue import hindi_row
    authorize(env)
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE content_localizations SET qc_json='{\"passed\": true}' WHERE id=1")
    item = env.pub.queue()["items"][0]
    row = hindi_row(item)
    assert "approve" in row["actions"] and row["approved"] is False and row["blocked_by"]
    env.pub.schedule_item(1)
    env.pub.approve(1, actor="op")
    row = hindi_row(env.pub.queue()["items"][0])
    assert "unapprove" in row["actions"] and row["approved"] is True


def test_dashboard_has_authorization_and_approval_controls():
    html = (APP / "templates" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="chAuth"', "function refreshChannelAuthorization()", "function chSetAuthorization(",
                   "AUTHORIZE HINDI PUBLISHING", "function hpApprove(", "Blocked by: "):
        assert needle in html, needle


# ---------------------------------------------------------------------------------------------------------------
# D. quota
# ---------------------------------------------------------------------------------------------------------------
class Ok:
    def execute(self):
        return {"ok": True}


def _ledger(tmp_path):
    clock = lambda: datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)  # noqa: E731
    return yq.QuotaLedger(str(tmp_path / "q.db"), clock=clock)


def _spend(q, method, n, **kw):
    for _ in range(n):
        q.execute(Ok(), method, **kw)


def test_requests_are_attributed_to_their_channel(tmp_path):
    q = _ledger(tmp_path)
    _spend(q, "channels.list", 3, purpose="upload_duplicate_check")
    _spend(q, "channels.list", 2, purpose="identity_check", channel_key="hi")
    _spend(q, "channels.list", 1, purpose="hindi_duplicate_check")               # historic Hindi purposes
    by = q.usage_by_channel()
    assert by["en"][yq.GENERAL] == 3 and by["hi"][yq.GENERAL] == 3
    assert q.usage()[yq.GENERAL]["used_estimate"] == 6                            # one project-wide ledger


def test_hindi_cap_and_english_reserve_defer_hindi_but_never_english(tmp_path):
    q = _ledger(tmp_path)
    _spend(q, "videos.insert", 6, purpose="hindi_upload")
    gate = q.check(yq.UPLOADS, 1, channel_key="hi")
    assert gate["allowed"] is False and gate["deferred"] and "cap" in gate["reason"]
    assert q.check(yq.UPLOADS, 1)["allowed"] is True                               # English unaffected
    q2 = yq.QuotaLedger(str(tmp_path / "q2.db"), clock=q.clock)
    _spend(q2, "videos.insert", 68, purpose="video_upload")                         # project nearly at the reserve
    lim = {"own_limits": {yq.UPLOADS: 100}}
    assert q2.check(yq.UPLOADS, 1, channel_key="hi", limits=lim)["allowed"] is True    # 100-68-1 >= 30-68
    q3 = yq.QuotaLedger(str(tmp_path / "q3.db"), clock=q.clock)
    _spend(q3, "videos.insert", 75, purpose="hindi_upload")
    _spend(q3, "videos.insert", 10, purpose="video_upload")
    g = q3.check(yq.UPLOADS, 1, channel_key="hi", limits=lim)
    assert g["allowed"] is False and "English reserve" in g["reason"]            # 100-85-1=14 < 30-10=20
    assert q3.check(yq.UPLOADS, 1)["allowed"] is True                             # English still uploads


def test_google_exhaustion_blocks_both_channels(tmp_path):
    q = _ledger(tmp_path)
    q.mark_exhausted(yq.UPLOADS, reason="quota_exceeded")
    assert q.check(yq.UPLOADS, 1)["allowed"] is False
    assert q.check(yq.UPLOADS, 1, channel_key="hi")["allowed"] is False


def test_quota_override_is_validated():
    lim = yq.channel_quota_limits("hi", {"own_limits": {yq.UPLOADS: 3, "bogus": 5, yq.GENERAL: -1},
                                         "english_reserve": {yq.UPLOADS: True}})
    assert lim["own_limits"] == {yq.UPLOADS: 3, yq.GENERAL: 1500}
    assert lim["english_reserve"][yq.UPLOADS] == 30


# ---------------------------------------------------------------------------------------------------------------
# Refinement: automatic per-video approval after a QC pass (never channel authorization)
# ---------------------------------------------------------------------------------------------------------------
def _qc_pass(env, loc_id=1, passed=True, record_hash=True):
    mp4 = Path(json.loads(sqlite3.connect(env.db).execute(
        "SELECT artifacts_json FROM content_localizations WHERE id=?", (loc_id,)).fetchone()[0])["final_mp4"])
    h = pg.sha256_file(mp4)
    arts = {"final_mp4": str(mp4), **({"final_mp4_sha256": h} if record_hash else {})}
    qc = {"passed": passed, **({"final_mp4_sha256": h} if record_hash else {})}
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE content_localizations SET status=?, artifacts_json=?, qc_json=? WHERE id=?",
                  ("QC_PASSED" if passed else "FAILED", json.dumps(arts), json.dumps(qc), loc_id))
    return mp4, h


def test_qc_pass_auto_approves_the_new_row_bound_to_the_qc_hash(env):
    mp4, h = _qc_pass(env)
    env.pub.sync()
    r = env.pub.get(1)
    assert r["approved_by"] == hp.AUTO_APPROVER and r["approved_sha256"] == h == r["qc_sha256"]
    assert env.reg.publishing_authorization("hi")["authorized"] is False           # never authorizes
    assert (env.reg.get("hi").get("publishing_config") or {}).get("publishing_authorized") is None
    with sqlite3.connect(env.db) as c:
        assert c.execute("SELECT COUNT(*) FROM hindi_publication_events WHERE event='AUTO_APPROVED_AFTER_QC'"
                         ).fetchone()[0] == 1
    v = env.pub.eligibility(r)
    assert v["eligible"] is False and v["checks"]["approved"] and not v["checks"]["publishing_authorized"]


def test_no_auto_approval_without_a_passed_qc_with_recorded_hash(env):
    _qc_pass(env, record_hash=False)                       # QC passed before hashes existed: no approval
    env.pub.sync()
    assert env.pub.get(1)["approved_at_utc"] is None
    with sqlite3.connect(env.db) as c:                     # a QC result that did not pass never approves
        c.execute("UPDATE content_localizations SET qc_json=? WHERE id=1", (json.dumps({"passed": False}),))
    env.pub._auto_approve(1, env.reg.localizations("hi")[-1])
    assert env.pub.get(1)["approved_at_utc"] is None


def test_file_mismatch_at_qc_time_prevents_auto_approval(env):
    mp4, h = _qc_pass(env)
    mp4.write_bytes(b"\x07" * 64)                          # changed between QC and queueing
    env.pub.sync()
    assert env.pub.get(1)["approved_at_utc"] is None


def test_existing_rows_are_never_approved_retroactively(env):
    env.pub.sync()                                         # row queued without QC hash (pre-refinement)
    env.pub.schedule_item(1)
    before = env.pub.get(1)
    _qc_pass(env)                                          # even if the localization now shows a hash
    env.pub.sync()
    after = env.pub.get(1)
    assert after["approved_at_utc"] is None and after["state"] == before["state"]
    assert after["scheduled_at_utc"] == before["scheduled_at_utc"]


def test_auto_approval_is_invalidated_by_file_change_qc_failure_rerender_and_metadata(env):
    mp4, h = _qc_pass(env)
    env.pub.sync()
    assert env.pub.get(1)["approved_at_utc"]
    # file change
    mp4.write_bytes(b"\x08" * 64)
    os.utime(mp4, None)
    env.clock.t = datetime.now(timezone.utc) - timedelta(minutes=5)   # approval older than the file change
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE hindi_publications SET approved_at_utc=? WHERE id=1",
                  ((datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),))
    env.pub.sync()
    assert env.pub.get(1)["approved_at_utc"] is None
    # QC failure: an approval matching the current file is cleared once the localization is no longer QC_PASSED
    mp4, h = _qc_pass(env)
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE hindi_publications SET approved_by='x', approved_at_utc='2099-01-01T00:00:00Z', "
                  "approved_sha256=? WHERE id=1", (h,))
        c.execute("UPDATE content_localizations SET status='FAILED' WHERE id=1")
    env.pub.sync()
    assert env.pub.get(1)["approved_at_utc"] is None
    with sqlite3.connect(env.db) as c:
        ev = [r[0] for r in c.execute("SELECT event FROM hindi_publication_events ORDER BY id")]
    assert "APPROVAL_CLEARED_FILE_CHANGED" in ev and "APPROVAL_CLEARED_QC_NOT_PASSED" in ev


def test_rerender_then_fresh_qc_pass_gets_a_fresh_auto_approval_for_the_new_hash(env):
    mp4, h1 = _qc_pass(env)
    env.pub.sync()
    env.pub.withdraw_for_localization(1, reason="re-opened from render")
    assert env.pub.get(1)["approved_at_utc"] is None and env.pub.get(1)["state"] == "WITHDRAWN"
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE content_localizations SET status='PLANNED' WHERE id=1")
    env.pub.sync()
    assert env.pub.get(1)["state"] == "WITHDRAWN" and env.pub.get(1)["approved_at_utc"] is None
    mp4.write_bytes(b"\x09" * 64)                          # the new render
    mp4, h2 = _qc_pass(env)
    env.pub.sync()
    r = env.pub.get(1)
    assert h2 != h1 and r["approved_sha256"] == h2 and r["approved_by"] == hp.AUTO_APPROVER


def test_metadata_edit_clears_auto_approval_and_it_is_not_reapplied(env):
    _qc_pass(env)
    env.pub.sync()
    env.pub.update_metadata(1, title="Edited | Hinglish", description="d", tags=["a"])
    env.pub.sync()
    assert env.pub.get(1)["approved_at_utc"] is None       # an edit needs a manual re-approval


# ---------------------------------------------------------------------------------------------------------------
# Scheduling fixes: English metadata link, internal-only Scheduled label, operator times kept on re-plan
# ---------------------------------------------------------------------------------------------------------------
def test_hindi_row_takes_the_english_ledger_metadata_by_topic(env):
    with sqlite3.connect(env.db) as c:
        c.execute("CREATE TABLE persistent_jobs (id TEXT PRIMARY KEY, topic_title TEXT)")
        c.execute("CREATE TABLE publishing_ledger (id INTEGER PRIMARY KEY, topic_title TEXT, metadata_json TEXT, "
                  "thumbnail_path TEXT)")
        c.execute("INSERT INTO persistent_jobs VALUES (?, 'Water on Mars')", (JOB,))
        c.execute("INSERT INTO publishing_ledger VALUES (1, 'Water on Mars', ?, NULL)",
                  (json.dumps({"title": "Water on Mars #Shorts", "description": "English description.",
                               "tags": ["mars", "space"], "category_id": "28"}),))
    env.pub.sync()
    r = env.pub.get(1)
    assert r["title"] == "Water on Mars #Shorts | Hinglish"
    assert r["description"] == "Hinglish version. English description." and r["tags"][:2] == ["mars", "space"]
    assert r["category_id"] == "28"


def test_unauthorized_hindi_row_is_labelled_internal_and_has_no_publish_now(env):
    from backend.app.services.unified_publishing_queue import hindi_row
    with sqlite3.connect(env.db) as c:
        c.execute("UPDATE content_localizations SET qc_json='{\"passed\": true}' WHERE id=1")
    env.pub.sync()
    env.pub.schedule_item(1)
    row = hindi_row(env.pub.queue()["items"][0])
    assert "publish_now" not in row["actions"] and "schedule" in row["actions"]
    assert row["youtube_status"].startswith("Scheduled internally")
    authorize(env)
    row = hindi_row(env.pub.queue()["items"][0])
    assert "publish_now" in row["actions"] and row["youtube_status"].startswith("Scheduled (uploads")


def test_replan_keeps_an_operator_chosen_time_of_day(env):
    env.pub.sync()
    want = NOW + timedelta(hours=3, minutes=7)                       # 14:37 IST, not the cycle's 20:00
    env.pub.schedule_item(1, want.strftime("%Y-%m-%dT%H:%M:%SZ"))
    env.clock.t = want + timedelta(minutes=5)                        # missed (not authorized)
    env.pub.tick()
    assert env.pub.get(1)["scheduled_at_utc"] == (want + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    env.clock.t = want + timedelta(days=1, minutes=5)                # missed again: still the operator's time
    env.pub.tick()
    assert env.pub.get(1)["scheduled_at_utc"] == (want + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
