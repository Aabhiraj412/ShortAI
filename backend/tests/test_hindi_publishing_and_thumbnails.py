"""Hindi publishing queue / schedule and the Shorts portrait-safe thumbnail state machine."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app.channels.registry import ChannelRegistry
from backend.app.localization import publishing as hp
from backend.app.services import thumbnail_pipeline as tpl

HI_ID = "UC_hindi_channel"
NOW = datetime(2026, 11, 28, 6, 0, tzinfo=timezone.utc)        # 11:30 IST
JOB = "cycle_20_20261128_1900"                                  # English cycle 20: 19:00 production, 20:00 publish
ENGLISH = [{"cycle_id": "cycle_20", "name": "Cycle 20", "start_time": "19:00", "target_publish_time": "20:00"},
           {"cycle_id": "cycle_24", "name": "Cycle 24", "start_time": "23:00", "target_publish_time": "00:00"},
           {"cycle_id": "cycle_5", "name": "Cycle 5", "start_time": "04:00", "target_publish_time": None}]


class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeService:
    def __init__(self, channel_id=HI_ID, uploads=None, fail_insert=None):
        self.channel_id, self.uploads, self.fail_insert = channel_id, list(uploads or []), fail_insert
        self.inserted = []

    def channels(self):
        svc = self

        class C:
            def list(self, part, mine):
                return Req(lambda: {"items": [{"id": svc.channel_id, "contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]})
        return C()

    def playlistItems(self):
        svc = self

        class P:
            def list(self, **kw):
                return Req(lambda: {"items": [{"snippet": {"title": t, "resourceId": {"videoId": v}}} for t, v in svc.uploads]})
        return P()

    def videos(self):
        svc = self

        class V:
            def insert(self, part, body, media_body):
                def go():
                    if svc.fail_insert:
                        raise svc.fail_insert
                    svc.inserted.append(body)
                    return {"id": f"hv{len(svc.inserted)}"}
                return Req(go)
        return V()


class FakeQuota:
    def __init__(self):
        self.calls = []

    def execute(self, req, method, **kw):
        self.calls.append(method)
        return req.execute()

    def check(self, bucket, cost, **kw):
        return {"allowed": True}


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "studio.db"
    backend = tmp_path / "backend"
    backend.mkdir()
    sqlite3.connect(db).close()
    reg = ChannelRegistry(str(db), backend_dir=backend, oauth_factory=lambda *a: None)
    reg.ensure()
    media = tmp_path / "media"
    good = media / "localizations" / "hi" / JOB / f"hindi_{JOB}.mp4"
    good.parent.mkdir(parents=True)
    good.write_bytes(b"\x00" * 64)
    english = media / "productions" / "cycle_2" / "final.mp4"
    english.parent.mkdir(parents=True)
    english.write_bytes(b"\x00" * 64)
    c = sqlite3.connect(db)
    for i, (job, path, status) in enumerate([(JOB, good, "QC_PASSED"), ("cycle_21_20261128_2000", english, "QC_PASSED"),
                                             ("cycle_22_20261128_2100", good, "WAITING")], start=1):
        c.execute("INSERT INTO content_localizations (id, source_channel_key, source_job_id, source_identity, "
                  "target_channel_key, language, status, artifacts_json, created_at_utc, updated_at_utc) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (i, "en", job, job, "hi", "hi", status, json.dumps({"final_mp4": str(path)}), "x", "x"))
    c.commit()
    c.close()
    svc = FakeService()
    clock = SimpleNamespace(t=NOW)
    english_cycles = [dict(c) for c in ENGLISH]

    def make():
        return hp.HindiPublisher(str(db), registry=reg, media_dir=media, service_factory=lambda: svc, quota=FakeQuota(),
                                 clock=lambda: clock.t, english_cycles=lambda: english_cycles)
    return SimpleNamespace(pub=make(), make=make, reg=reg, db=db, svc=svc, clock=clock, english=english_cycles)


def authorize(env, enabled=True):
    c = sqlite3.connect(env.db)
    c.execute("UPDATE channel_registry SET status='ACTIVE', youtube_channel_id=?, publishing_config_json=? "
              "WHERE channel_key='hi'", (HI_ID, json.dumps({"publishing_authorized": True})))
    c.commit()
    c.close()
    env.pub.set_schedule(dict(env.pub.schedule(), enabled=enabled))


def test_queue_holds_only_qc_passed_hindi_files_once(env):
    q = env.pub.queue()
    assert [i["source_job_id"] for i in q["items"]] == [JOB]          # English file and non-QC rows never enter
    env.pub.queue()
    assert len(env.pub.queue()["items"]) == 1
    assert q["schedule"]["enabled"] is False and not q["gates"]["can_publish"]
    assert q["items"][0]["title"].endswith("| Hinglish")


def test_hindi_time_defaults_to_the_english_target_time(env):
    q = env.pub.queue()
    row = next(r for r in q["cycle_times"] if r["cycle_id"] == "cycle_20")
    assert row["same_as_english"] and row["effective_hindi_time"] == "20:00"
    item = q["items"][0]
    assert item["state"] == "SCHEDULED" and item["scheduled_at_utc"] == "2026-11-28T14:30:00Z"   # 20:00 IST
    env.english[0]["target_publish_time"] = "21:00"                     # English changes -> Hindi follows
    assert next(r for r in env.pub.cycle_times() if r["cycle_id"] == "cycle_20")["effective_hindi_time"] == "21:00"


def test_independent_hindi_time_persists_and_reloads(env):
    env.pub.set_cycle_times({"cycle_20": {"same_as_english": False, "time": "22:15"}})
    again = env.make()                                                  # a new process reads the same config
    row = next(r for r in again.cycle_times() if r["cycle_id"] == "cycle_20")
    assert row["same_as_english"] is False and row["effective_hindi_time"] == "22:15"
    assert row["english_target_publish_time"] == "20:00"                 # English untouched
    assert again.queue()["items"][0]["scheduled_at_utc"] == "2026-11-28T16:45:00Z"   # 22:15 IST
    env.pub.set_schedule(dict(env.pub.schedule(), enabled=True))         # other settings keep the cycle times
    assert env.make().schedule()["cycles"]["cycle_20"]["time"] == "22:15"
    with pytest.raises(hp.PublishingError):
        env.pub.set_cycle_times({"cycle_20": {"same_as_english": False, "time": None}})
    with pytest.raises(hp.PublishingError):
        hp.validate_schedule({"strategy": "ANALYTICAL"})                 # reserved, not available yet
    assert hp.validate_schedule({"times": ["19:30"]})["strategy"] == "MANUAL"   # legacy key ignored


def test_midnight_target_is_the_next_day(env):
    r = env.pub._plan_manual("cycle_24_20261128_2300", NOW)
    assert hp._ts(r["at"]) == "2026-11-28T18:30:00Z"                     # 00:00 IST on 29 Nov
    env.pub.set_cycle_times({"cycle_20": {"same_as_english": False, "time": "01:30"}})
    r = env.pub._plan_manual(JOB, NOW)                                   # 19:00 production, Hindi 01:30
    assert hp._ts(r["at"]) == "2026-11-28T20:00:00Z"                     # 01:30 IST on 29 Nov
    assert env.pub._plan_manual("cycle_5_20261128_0400", NOW)["at"] is None   # English has no time: must choose


def test_late_localization_uses_the_next_occurrence_never_now(env):
    env.clock.t = datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc)    # 20:30 IST: the 20:00 target has passed
    item = env.pub.queue()["items"][0]
    assert item["scheduled_at_utc"] == "2026-11-29T14:30:00Z"            # 20:00 IST the next day


def test_missed_time_is_replanned_not_published(env, monkeypatch):
    monkeypatch.setattr(hp.HindiPublisher, "_thumbnails", lambda self: [])
    env.pub.queue()                                                      # planned 20:00 IST today; not authorized
    env.clock.t = datetime(2026, 11, 28, 14, 40, tzinfo=timezone.utc)   # 20:10 IST: passed, never uploaded
    env.pub.tick()
    assert env.pub.get(1)["scheduled_at_utc"] == "2026-11-29T14:30:00Z" and not env.svc.inserted
    authorize(env)                                                       # authorized later: uploads for the next time
    env.pub.approve(1, actor="test")                                   # Stage 4.03: per-video approval
    env.clock.t = datetime(2026, 11, 29, 13, 0, tzinfo=timezone.utc)
    env.pub.tick()
    assert env.svc.inserted[0]["status"]["publishAt"] == "2026-11-29T14:30:00Z"


def test_daily_limit_and_individual_timestamps(env):
    authorize(env, enabled=False)
    env.pub.queue()
    with pytest.raises(hp.PublishingError, match="20 minutes"):
        env.pub.schedule_item(1, "2026-11-28T06:05:00Z")
    s = env.pub.schedule_item(1, "2026-11-28T10:07:00Z")                 # any individual time (future strategies)
    assert s["scheduled_at_utc"] == "2026-11-28T10:07:00Z"
    env.pub.set_schedule(dict(env.pub.schedule(), daily_limit=1))
    assert env.pub.plan(env.pub.get(1), exclude=None)["at"].date().isoformat() == "2026-11-29"   # day full -> next day
    assert env.pub.unschedule(1)["state"] == "DRAFT"


def test_english_schedule_is_never_written():
    src = Path(hp.__file__).read_text(encoding="utf-8")
    for forbidden in ("activate_schedule", "save_draft", "UPDATE production_schedule", "schedule_service.activate",
                      "publishing_ledger SET", "INSERT INTO publishing_ledger"):
        assert forbidden not in src, forbidden


def test_no_upload_without_every_gate(env):
    env.pub.queue()
    env.pub.schedule_item(1)
    env.clock.t = NOW + timedelta(hours=7)
    assert env.pub.tick()["ran"] is False and not env.svc.inserted       # not authorized, schedule off
    authorize(env, enabled=False)
    assert env.pub.tick()["ran"] is False and not env.svc.inserted       # authorized but schedule disabled


def test_upload_once_with_hindi_identity(env, monkeypatch):
    monkeypatch.setattr(hp.HindiPublisher, "_thumbnails", lambda self: [])
    authorize(env)
    env.pub.queue()
    env.pub.schedule_item(1)
    env.pub.approve(1, actor="test")                                   # Stage 4.03: per-video approval
    env.clock.t = NOW + timedelta(hours=7)                               # within the upload lead
    res = env.pub.tick()
    assert res["upload"]["uploaded"] and len(env.svc.inserted) == 1
    body = env.svc.inserted[0]
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"] == "2026-11-28T14:30:00Z"
    env.pub.tick()
    assert len(env.svc.inserted) == 1                                    # never uploaded twice
    assert env.pub.get(1)["state"] == "UPLOADED"


def test_wrong_identity_is_refused(env):
    authorize(env)
    env.svc.channel_id = "UC_english"
    env.pub.queue()
    env.pub.schedule_item(1)
    env.pub.approve(1, actor="test")                                   # Stage 4.03: per-video approval
    env.clock.t = NOW + timedelta(hours=7)
    with pytest.raises(hp.PublishingError, match="does not belong"):
        env.pub.tick()
    assert not env.svc.inserted and env.pub.get(1)["state"] == "FAILED"


def test_interrupted_upload_adopts_the_remote_video_instead_of_reuploading(env):
    authorize(env)
    env.pub.queue()
    env.pub.schedule_item(1)
    env.pub.approve(1, actor="test")                                   # Stage 4.03: per-video approval
    env.clock.t = NOW + timedelta(hours=7)
    env.svc.fail_insert = ConnectionError("network dropped")              # no answer from YouTube
    env.pub.tick()
    row = env.pub.get(1)
    assert row["state"] == "NEEDS_CHECK"
    env.svc.fail_insert = None
    env.svc.uploads = [(row["title"], "already_there")]
    r = env.pub.retry(1)
    assert r["state"] == "UPLOADED" and r["youtube_video_id"] == "already_there" and not env.svc.inserted


def test_metadata_rules(env):
    env.pub.queue()
    with pytest.raises(hp.PublishingError):
        env.pub.update_metadata(1, title="x" * 101, description="", tags=[])
    with pytest.raises(hp.PublishingError):
        env.pub.update_metadata(1, title="ok <b>", description="", tags=[])
    assert env.pub.update_metadata(1, title="Naya title", description="d", tags=["a"])["title"] == "Naya title"


# -- thumbnails -------------------------------------------------------------------------------------------------------

def _portrait(tmp_path):
    im = Image.new("RGB", (1080, 1920), (20, 40, 60))
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    for i in range(0, 1920, 160):
        d.rectangle((100, i, 980, i + 80), fill=(250, 200, 40))
    d.ellipse((200, 600, 880, 1280), fill=(200, 30, 30))
    p = tmp_path / "thumb_cycle_1.png"
    im.save(p)
    return str(p)


def test_shorts_artifact_centre_column_is_the_portrait(tmp_path):
    p = _portrait(tmp_path)
    art = tpl.artifact_for(p, tpl.SHORT)
    assert art.endswith(tpl.SHORTS_SUFFIX) and tpl.validate_thumbnail(art)["ok"]
    im = Image.open(art)
    centre = im.crop((640 - 202, 0, 640 + 203, 720))
    dist = sum(a != b for a, b in zip(tpl._dhash(centre), tpl._dhash(Image.open(p))))
    assert dist <= 12
    assert tpl.portrait_of(art) == p
    assert tpl.artifact_for(p, tpl.LONG).endswith(tpl.YOUTUBE_SUFFIX)          # long-form keeps 16:9


class ThumbService:
    def __init__(self, processing="succeeded", reject=None, sar=True):
        self.processing, self.reject, self.sar, self.sets = processing, reject, sar, 0

    def videos(self):
        svc = self

        class V:
            def list(self, part, id):
                th = {"maxres": {"url": "u", "width": 1280, "height": 720}}
                if svc.sar and svc.sets:
                    th["shortsAspectRatio"] = {"url": "sar", "width": 405, "height": 720}
                return Req(lambda: {"items": [{"processingDetails": {"processingStatus": svc.processing.upper()},
                                               "status": {"privacyStatus": "public"}, "snippet": {"thumbnails": th}}]})
        return V()

    def thumbnails(self):
        svc = self

        class T:
            def set(self, videoId, media_body):
                def go():
                    if svc.reject:
                        raise svc.reject
                    svc.sets += 1
                    return {"items": [{"default": {}, "shortsAspectRatio": {}}]}
                return Req(go)
        return T()


def test_thumbnail_waits_for_processing_then_sets_and_verifies(tmp_path):
    p = _portrait(tmp_path)
    fetch = lambda url: Path(p).read_bytes()                                    # YouTube shows our portrait
    svc, q = ThumbService(processing="processing"), FakeQuota()
    r = tpl.set_and_verify(svc, q, "v1", p, kind=tpl.SHORT, fetch=fetch)
    assert r["state"] == "awaiting_processing" and svc.sets == 0
    svc.processing = "succeeded"
    r = tpl.set_and_verify(svc, q, "v1", p, kind=tpl.SHORT, previous=r, fetch=fetch)
    assert r["state"] == "verified" and svc.sets == 1 and "shortsAspectRatio" in r["variants"]
    r = tpl.set_and_verify(svc, q, "v1", p, kind=tpl.SHORT, previous=r, fetch=fetch)
    assert svc.sets == 1                                                         # never uploaded twice


def test_rejection_and_missing_variant_are_reported(tmp_path):
    p = _portrait(tmp_path)

    class Rejected(Exception):
        resp = SimpleNamespace(status=403)
    r = tpl.set_and_verify(ThumbService(reject=Rejected("forbidden")), FakeQuota(), "v1", p, kind=tpl.SHORT)
    assert r["state"] == "rejected" and "403" in r["error"]
    r = tpl.set_and_verify(ThumbService(sar=False), FakeQuota(), "v1", p, kind=tpl.SHORT,
                           fetch=lambda url: Path(p).read_bytes())
    assert r["state"] == "set" and r["verify"]["status"] == "no_shorts_variant" and "not verified" in r["error"]
