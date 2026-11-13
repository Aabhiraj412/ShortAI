"""
Simple cycle schedule (authoritative view, editing, validation, next-cycle and midnight
rules) and automatic private upload + YouTube-native scheduling right after QC.

All YouTube calls are mocked; settings, jobs and the publishing ledger use temp DBs.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main as main_module
from backend.app.database.db import Base, JobModel
from backend.app.orchestration import crazzydocs_master_orchestrator as orch_mod
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator
from backend.app.rendering.render_errors import PublishingStepError
from backend.app.scheduler import retry_policy as rp
from backend.app.scheduler import scheduler as sched_mod
from backend.app.scheduler.schedule_profiles import ProductionCycle, get_active_profile
from backend.app.services import control_center_service as control
from backend.app.services import lifecycle_events
from backend.app.services import schedule_service as ss
from backend.app.services.youtube_publishing_engine import (
    PublicationRecord,
    PublishingDatabase,
    YouTubeMetadata,
    YouTubePublishingEngine,
    compute_file_sha256,
)
from backend.app.services.youtube_service import YouTubePublisherService

IST = ZoneInfo("Asia/Kolkata")


def cyc(cid, start, target=None, rec=240, enabled=True, days=None, name=None):
    c = {"cycle_id": cid, "name": name or cid.title(), "start_time": start, "recovery_duration_minutes": rec,
         "enabled": enabled, "active_weekdays": days if days is not None else [0, 1, 2, 3, 4, 5, 6]}
    if target:
        c["target_publish_time"] = target
    return c


def spec(*cycles):
    return {"profile_name": "CUSTOM", "timezone_name": "Asia/Kolkata", "cycles": list(cycles)}


@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (control, orch_mod, sched_mod):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    monkeypatch.setattr("backend.app.database.db.SessionLocal", factory)
    yield factory
    sched_mod.set_active_profile("FOUR_CYCLES_DAILY")
    engine.dispose()


# =============================================================================
# Schedule: editing, validation, authoritative view
# =============================================================================

def test_dynamic_cycles_create_edit_delete_and_daemon_profile_matches(settings_db):
    r = ss.activate(spec(cyc("dawn", "05:00", "08:00"), cyc("noon", "12:30", "15:00", rec=90),
                         cyc("late", "21:00", "00:30")), confirm=True)
    assert r["status"] == "ACTIVATED"
    view = ss.schedule_view(datetime(2026, 11, 13, 4, 0, tzinfo=IST))
    assert [c["cycle_id"] for c in view["cycles"]] == ["dawn", "noon", "late"]
    prof = get_active_profile()   # what the scheduler daemon loads
    assert [(c.cycle_id, c.start_hour_ist, c.start_min_ist, c.recovery_window_hours, c.target_publish_time) for c in prof.cycles] == [
        ("dawn", 5, 0, 4.0, "08:00"), ("noon", 12, 30, 1.5, "15:00"), ("late", 21, 0, 4.0, "00:30")]
    v1 = view["version"]

    # Edit one cycle and delete another.
    ss.activate(spec(cyc("dawn", "05:15", "09:00", name="Dawn edited"), cyc("late", "21:00", "00:30")), confirm=True)
    view2 = ss.schedule_view(datetime(2026, 11, 13, 4, 0, tzinfo=IST))
    assert [(c["cycle_id"], c["start_time"], c["name"]) for c in view2["cycles"]] == [
        ("dawn", "05:15", "Dawn edited"), ("late", "21:00", "Late")]
    assert view2["version"] == v1 + 1
    assert [c.cycle_id for c in get_active_profile().cycles] == ["dawn", "late"]


def test_disabled_cycles_are_kept_but_never_run(settings_db):
    ss.activate(spec(cyc("a", "06:00", "09:00"), cyc("b", "10:00", "13:00", enabled=False)), confirm=True)
    now = datetime(2026, 11, 13, 9, 0, tzinfo=IST)
    view = ss.schedule_view(now)
    assert {c["cycle_id"]: c["status"] for c in view["cycles"]}["b"] == "Disabled"
    assert all(u["cycle_id"] != "b" for u in view["upcoming"])
    assert [c.cycle_id for c in get_active_profile().cycles] == ["a"]


@pytest.mark.parametrize("bad, message", [
    (spec(cyc("a", "25:00", "06:00")), "start_time"),
    (spec(cyc("a", "06:00", "6pm")), "target_publish_time"),
    (spec(cyc("a", "06:00", "09:00"), cyc("a", "08:00", "11:00")), "unique"),
    (spec(cyc("a", "06:00", "09:00", rec=10)), "between 30 and 1440"),
    (spec(cyc("a", "06:00", "09:00", days=[])), "active_weekdays"),
    (spec(cyc("a", "06:00", "09:00", enabled=False)), "At least one cycle"),
])
def test_invalid_schedules_are_rejected_and_never_activated(settings_db, bad, message):
    res = ss.validate(bad)
    assert res["valid"] is False and any(message in e for e in res["errors"]), res["errors"]
    with pytest.raises(ValueError):
        ss.activate(bad, confirm=True)
    assert ss.active_spec() is None


def test_activation_requires_confirmation(settings_db):
    with pytest.raises(PermissionError):
        ss.activate(spec(cyc("a", "06:00", "09:00")), confirm=False)
    assert ss.active_spec() is None


def test_overlapping_recovery_windows_are_allowed_with_a_warning(settings_db):
    res = ss.validate(spec(cyc("a", "22:00", "04:00", rec=240), cyc("b", "00:00", "06:00", rec=240)))
    assert res["valid"] is True and any("overlap" in w for w in res["warnings"])


# =============================================================================
# Next cycle, midnight rollover, independence of target time
# =============================================================================

@pytest.mark.parametrize("cycles, now, expect_next, expect_start", [
    # one cycle, before and after it today
    ([cyc("only", "17:30", "00:00")], datetime(2026, 11, 13, 9, 0), "only", "2026-11-13 17:30"),
    ([cyc("only", "17:30", "00:00")], datetime(2026, 11, 13, 23, 0), "only", "2026-11-14 17:30"),
    # two cycles
    ([cyc("m", "08:00", "10:00"), cyc("e", "20:00", "22:00")], datetime(2026, 11, 13, 9, 0), "e", "2026-11-13 20:00"),
    # four cycles (default preset)
    (None, datetime(2026, 11, 13, 6, 30), "cycle_3", "2026-11-13 12:00"),
    (None, datetime(2026, 11, 13, 23, 59), "cycle_1", "2026-11-14 00:00"),
    # unevenly spaced
    ([cyc("x", "01:15", "03:00"), cyc("y", "02:00", "05:00"), cyc("z", "19:45", "23:00")], datetime(2026, 11, 13, 1, 30), "y", "2026-11-13 02:00"),
    # weekday restriction: Monday only; 2026-11-13 is a Friday
    ([cyc("mon", "09:00", "12:00", days=[0])], datetime(2026, 11, 13, 8, 0), "mon", "2026-11-16 09:00"),
])
def test_next_cycle_uses_current_date_and_time(settings_db, cycles, now, expect_next, expect_start):
    if cycles is None:
        ss.activate(ss.preset("FOUR_CYCLES_DAILY"), confirm=True)
    else:
        ss.activate(spec(*cycles), confirm=True)
    nxt = ss.next_production_cycle(now.replace(tzinfo=IST))
    assert nxt["cycle_id"] == expect_next
    assert nxt["production_start_ist"] == expect_start + " IST"


def test_midnight_rollover_keeps_yesterdays_cycle_in_its_recovery_window(settings_db):
    ss.activate(spec(cyc("night", "22:00", "06:00", rec=240)), confirm=True)
    now = datetime(2026, 11, 14, 1, 0, tzinfo=IST)
    cur = ss.current_cycle_window(now)
    assert cur and cur["production_start_ist"] == "2026-11-13 22:00 IST"
    assert cur["recovery_deadline_ist"] == "2026-11-14 02:00 IST"
    assert ss.next_production_cycle(now)["production_start_ist"] == "2026-11-14 22:00 IST"
    pc = get_active_profile().cycles[0]
    # The scheduler addresses the same occurrence (job id carries yesterday's date).
    assert pc.format_job_id(now) == "night_20261113_2200"
    start, deadline, _, _ = pc.calculate_datetimes(now)
    assert start == datetime(2026, 11, 13, 22, 0, tzinfo=IST) and not pc.is_past_recovery_window(start, now)
    # After the window closes, the next occurrence is today's.
    assert pc.format_job_id(datetime(2026, 11, 14, 2, 30, tzinfo=IST)) == "night_20261114_2200"


def test_midnight_publication_rolls_to_next_day():
    c = ProductionCycle("c4", "Evening", 18, 0, 0, 0, recovery_window_hours=4.0, window_hours=4.0, target_publish_time="00:00")
    start = datetime(2026, 11, 13, 18, 0, tzinfo=IST)
    assert c.target_publication_at(start) == datetime(2026, 11, 14, 0, 0, tzinfo=IST)


FIXED_NOW = datetime(2026, 11, 13, 5, 0, tzinfo=IST)


def _first(s):
    return control.compute_upcoming_production_cycles(ss.clean_spec(s), count=1, now_ist=FIXED_NOW)[0]


def test_target_time_never_changes_production_start_or_recovery(settings_db):
    a = _first(spec(cyc("a", "06:00", "07:00", rec=240)))
    b = _first(spec(cyc("a", "06:00", "23:00", rec=240)))
    for k in ("production_start_ist", "recovery_deadline_ist"):
        assert a[k] == b[k]
    assert a["target_publish_ist"] != b["target_publish_ist"]
    # A target inside the recovery window does not shorten it.
    assert a["recovery_deadline_ist"].endswith("10:00 IST") and a["target_publish_ist"].endswith("07:00 IST")


def test_recovery_duration_never_changes_target_time(settings_db):
    a = _first(spec(cyc("a", "06:00", "12:00", rec=60)))
    b = _first(spec(cyc("a", "06:00", "12:00", rec=600)))
    assert a["production_start_ist"] == b["production_start_ist"] == "2026-11-13 06:00 IST"
    assert a["target_publish_ist"] == b["target_publish_ist"]
    assert a["recovery_deadline_ist"] != b["recovery_deadline_ist"]


def test_schedule_api_is_local_only_and_consistent(settings_db, monkeypatch):
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    body = {"confirm": True, "spec": spec(cyc("a", "06:00", "12:00"), cyc("b", "18:00", "00:00"))}
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("203.0.113.9", 1)) as remote:
        assert remote.get("/api/schedule").status_code == 403
        assert remote.post("/api/schedule/activate", json=body).status_code == 403
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as c:
        assert c.post("/api/schedule/activate", json={**body, "confirm": False}).status_code == 403
        assert c.post("/api/schedule/activate", json={"confirmed": True, "spec": body["spec"]}).status_code == 422
        assert c.post("/api/schedule/activate", json=body).status_code == 200
        view = c.get("/api/schedule").json()
        assert [x["cycle_id"] for x in view["cycles"]] == ["a", "b"]
        # Same schedule as the daemon's runtime profile.
        assert [p.cycle_id for p in get_active_profile().cycles] == ["a", "b"]
        # No publication-slot concepts in the simplified API.
        text = json.dumps(view).lower()
        for legacy in ("slot", "pub_prep", "unmapped", "overlap_policy", "wait_for"):
            assert legacy not in text
        preset = c.get("/api/schedule/presets/FOUR_CYCLES_DAILY").json()
        assert [(x["start_time"], x["target_publish_time"], x["recovery_duration_minutes"]) for x in preset["cycles"]] == [
            ("00:00", "06:00", 240), ("06:00", "12:00", 240), ("12:00", "18:00", 240), ("18:00", "00:00", 240)]


# =============================================================================
# Automatic private upload + scheduled publication after QC (mocked YouTube)
# =============================================================================

def _yt(video_id="yt_new", privacy="private", publish_at=None):
    service = MagicMock()
    req = MagicMock()
    req.resumable_uri = None
    req.next_chunk.return_value = (None, {"id": video_id})
    state = {"privacy": privacy, "publish_at": publish_at}

    def _insert(part=None, body=None, media_body=None):
        state["publish_at"] = (body or {}).get("status", {}).get("publishAt")   # as YouTube does
        return req

    service.videos.return_value.insert.side_effect = _insert

    def _list(**_):
        st = {"privacyStatus": state["privacy"]}
        if state["publish_at"]:
            st["publishAt"] = state["publish_at"]
        m = MagicMock()
        m.execute.return_value = {"items": [{"id": video_id, "status": st}]}
        return m

    service.videos.return_value.list.side_effect = _list

    def _update(part=None, body=None):
        if "status" in (body or {}) and body["status"].get("publishAt"):
            state["publish_at"] = body["status"]["publishAt"]
        return MagicMock(execute=MagicMock(return_value={"id": video_id}))

    service.videos.return_value.update.side_effect = _update
    service._state = state
    return service


@pytest.fixture
def pub(tmp_path, settings_db, monkeypatch):
    pdb = PublishingDatabase(db_path=str(tmp_path / "pub.db"))
    engine = YouTubePublishingEngine(db=pdb)
    oauth = MagicMock()
    yt = _yt()
    oauth.get_authenticated_service.return_value = yt
    engine.oauth = oauth
    monkeypatch.setattr("backend.app.services.youtube_service.youtube_publishing_engine", engine)
    monkeypatch.setattr(master_orchestrator.publishing_engine, "db", pdb)
    # The upload sees its own fresh, unique title (no remote duplicate on the channel).
    monkeypatch.setattr(engine, "check_remote_channel_duplicate", lambda **_: (False, None))
    video = tmp_path / "final.mp4"
    video.write_bytes(b"\x00" * 8192)
    thumb = tmp_path / "thumb.png"
    thumb.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    control.set_publishing_policy("automatic_private_scheduled", confirm=True)
    yield {"pdb": pdb, "engine": engine, "oauth": oauth, "yt": yt, "video": video, "thumb": thumb,
           "svc": YouTubePublisherService()}


def _queue(pub, thumbnail=True):
    meta = YouTubeMetadata(title="Black holes", description="desc", tags=["space"], hashtags=["#Shorts"])
    return pub["pdb"].record_publication(PublicationRecord(
        id=None, topic_title="Black holes", video_path=str(pub["video"]),
        thumbnail_path=str(pub["thumb"]) if thumbnail else None, metadata_json=json.dumps(meta.__dict__),
        youtube_video_id=None, upload_status="pending_queue", visibility="private",
        video_hash=compute_file_sha256(str(pub["video"])),
    ))


def _future(hours=5):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_immediate_private_upload_with_youtube_native_publish_at(pub):
    lid = _queue(pub)
    target = _future(5)
    res = pub["svc"].publish_after_qc(lid, target)
    assert res["state"] == "SCHEDULED"
    body = pub["yt"].videos.return_value.insert.call_args.kwargs["body"]
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"] == target
    row = pub["pdb"].get_publication(lid)
    assert (row["youtube_video_id"], row["upload_status"], row["visibility"], row["scheduled_timestamp"]) == (
        "yt_new", "publication_scheduled", "private", target)
    pub["yt"].thumbnails.return_value.set.assert_called_once()


def test_manual_policy_never_auto_uploads(pub):
    control.set_publishing_policy("manual_approval", confirm=True)
    lid = _queue(pub)
    with pytest.raises(PermissionError):
        pub["svc"].publish_after_qc(lid, _future())
    pub["yt"].videos.return_value.insert.assert_not_called()


@pytest.mark.parametrize("target", [
    (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),   # passed
    (datetime.now(timezone.utc) + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),   # too close
    None,                                                                                  # no target
])
def test_passed_target_is_never_published_immediately(pub, target):
    lid = _queue(pub)
    res = pub["svc"].publish_after_qc(lid, target)
    assert res["state"] == "NEEDS_PUBLICATION_TIME"
    pub["yt"].videos.return_value.insert.assert_not_called()
    pub["yt"].videos.return_value.update.assert_not_called()
    row = pub["pdb"].get_publication(lid)
    assert row["visibility"] == "private" and row["upload_status"] == "pending_queue"
    assert "needs a new publication time" in row["error_details"].lower()


def test_thumbnail_retry_never_uploads_a_second_copy(pub):
    lid = _queue(pub)
    target = _future()
    pub["yt"].thumbnails.return_value.set.return_value.execute.side_effect = RuntimeError("thumb 500")
    r1 = pub["svc"].publish_after_qc(lid, target)
    assert r1["state"] == "THUMBNAIL_PENDING" and r1["video_id"] == "yt_new"
    pub["yt"].thumbnails.return_value.set.return_value.execute.side_effect = None
    r2 = pub["svc"].publish_after_qc(lid, target)
    assert r2["state"] == "SCHEDULED"
    assert pub["yt"].videos.return_value.insert.call_count == 1
    assert pub["pdb"].get_publication(lid)["youtube_video_id"] == "yt_new"


def test_scheduling_retry_never_uploads_a_second_copy(pub):
    lid = _queue(pub, thumbnail=False)
    target = _future()
    yt = pub["yt"]
    # Upload succeeds but YouTube does not report the schedule (e.g. processing), and the
    # reschedule call fails once.
    yt._state["publish_at"] = None
    real_update = yt.videos.return_value.update.side_effect
    calls = {"n": 0}

    def flaky_update(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("HTTP 503")
        return real_update(**kw)

    yt.videos.return_value.update.side_effect = flaky_update
    orig_insert = yt.videos.return_value.insert.side_effect

    def insert_schedule_not_reported(**kw):
        r = orig_insert(**kw)
        yt._state["publish_at"] = None   # YouTube has not applied/reported publishAt yet
        return r

    yt.videos.return_value.insert.side_effect = insert_schedule_not_reported
    r1 = pub["svc"].publish_after_qc(lid, target)
    assert r1["state"] == "SCHEDULE_PENDING"
    r2 = pub["svc"].publish_after_qc(lid, target)
    assert r2["state"] == "SCHEDULED"
    assert yt.videos.return_value.insert.call_count == 1
    assert pub["pdb"].get_publication(lid)["scheduled_timestamp"] == target


def test_existing_public_video_is_never_touched(pub):
    lid = _queue(pub)
    pub["pdb"].update_queue_metadata_and_schedule(lid, upload_status="uploaded_public", visibility="public")
    res = pub["svc"].publish_after_qc(lid, _future())
    assert res["state"] == "ALREADY_PUBLIC"
    pub["yt"].videos.return_value.insert.assert_not_called()
    pub["yt"].videos.return_value.update.assert_not_called()


def test_matching_video_already_on_channel_needs_review(pub, monkeypatch):
    monkeypatch.setattr(pub["engine"], "check_remote_channel_duplicate", lambda **_: (True, "old_remote"))
    lid = _queue(pub)
    res = pub["svc"].publish_after_qc(lid, _future())
    assert res["state"] == "NEEDS_REVIEW"
    pub["yt"].videos.return_value.update.assert_not_called()  # never reschedules someone else's video


# --- completion boundary with automatic private scheduling ----------------------

JOB = "cycle_1_20261113_0000"


@pytest.fixture
def handoff_job(pub, settings_db, monkeypatch):
    events = []
    monkeypatch.setattr(lifecycle_events, "record_lifecycle_event",
                        lambda et, st, **kw: events.append(et) or {"event_type": et})
    target = _future(6)
    handoff = {
        "cycle_id": "cycle_1", "topic_title": "Black holes", "video_path": str(pub["video"]),
        "thumbnail_path": str(pub["thumb"]),
        "metadata_json": json.dumps({"title": "Black holes", "description": "d", "tags": ["t"], "hashtags": ["#Shorts"]}),
        "video_hash": compute_file_sha256(str(pub["video"])),
    }
    with settings_db() as db:
        db.add(JobModel(id=JOB, date_str="2026-11-13", status="RUNNING", current_stage="PUBLISHING_QUEUE_HANDOFF",
                        retry_count=2, max_retries=3,
                        checkpoint_data_json=json.dumps({"video_path": str(pub["video"]), "queue_handoff": handoff,
                                                         "cycle_state": {"target_publication_at_utc": target}})))
        db.commit()
    return {"events": events, "target": target}


def _job(settings_db):
    with settings_db() as db:
        return db.query(JobModel).filter(JobModel.id == JOB).one()


def test_production_completes_only_after_schedule_is_confirmed(pub, settings_db, handoff_job):
    lid = master_orchestrator.complete_queue_handoff(JOB)
    job = _job(settings_db)
    assert job.status == "COMPLETED"
    assert handoff_job["events"] == [
        "PUBLISHING_QUEUE_ENTRY_CREATED", "YOUTUBE_UPLOAD_CONFIRMED",
        "PUBLICATION_SCHEDULE_CONFIRMED", "PRODUCTION_CYCLE_COMPLETED",
    ]
    publishing = json.loads(job.checkpoint_data_json)["cycle_state"]["publishing"]
    assert publishing["state"] == "SCHEDULED" and publishing["video_id"] == "yt_new"
    assert pub["pdb"].get_publication(lid)["scheduled_timestamp"] == handoff_job["target"]


def test_publishing_failure_retries_only_publishing_and_keeps_production_attempts(pub, settings_db, handoff_job):
    pub["yt"].thumbnails.return_value.set.return_value.execute.side_effect = RuntimeError("thumb 500")
    with pytest.raises(PublishingStepError):
        master_orchestrator.complete_queue_handoff(JOB)
    with settings_db() as db:
        job = db.query(JobModel).filter(JobModel.id == JOB).one()
        d = rp.record_failure(job, PublishingStepError("Publishing step pending (THUMBNAIL_PENDING)"))
        db.commit()
    job = _job(settings_db)
    cs = json.loads(job.checkpoint_data_json)["cycle_state"]
    assert d["classification"] == rp.QUEUE_HANDOFF_FAILURE and job.status == "HANDOFF_PENDING"
    assert job.retry_count == 2, "publishing failures must not consume production attempts"
    assert cs["publish_attempts"] == 1
    assert "PRODUCTION_CYCLE_COMPLETED" not in handoff_job["events"]
    # The retry (recovery loop) only re-runs the publishing step.
    pub["yt"].thumbnails.return_value.set.return_value.execute.side_effect = None
    master_orchestrator.complete_queue_handoff(JOB)
    assert _job(settings_db).status == "COMPLETED"
    assert pub["yt"].videos.return_value.insert.call_count == 1
    assert pub["pdb"].get_publication(1)["youtube_video_id"] == "yt_new"


def test_manual_policy_keeps_previous_completion_behaviour(pub, settings_db, handoff_job):
    control.set_publishing_policy("manual_approval", confirm=True)
    lid = master_orchestrator.complete_queue_handoff(JOB)
    assert _job(settings_db).status == "COMPLETED"
    assert handoff_job["events"] == ["PUBLISHING_QUEUE_ENTRY_CREATED", "PRODUCTION_CYCLE_COMPLETED"]
    assert pub["pdb"].get_publication(lid)["youtube_video_id"] is None
    pub["yt"].videos.return_value.insert.assert_not_called()
