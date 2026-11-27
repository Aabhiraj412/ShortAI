"""Stage 3.32E: timezone-aware Community draft schedule (IANA zones, DST, duplicates, catch-up, attempts, API).

Temporary databases only (the World fixture of the main 3.32E suite); no production data, no network.
"""
from __future__ import annotations

import json
import socket
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.app.community import schedule as SCH
from backend.app.community import service as svc_mod
from backend.app.community import store as S
from test_community_posts_332e import OP, World, gen

UTC = timezone.utc

# (zone, observes DST, winter UTC offset hours, summer UTC offset hours)
PRESETS = [
    ("America/New_York", True, -5, -4), ("America/Chicago", True, -6, -5), ("America/Denver", True, -7, -6),
    ("America/Los_Angeles", True, -8, -7), ("America/Phoenix", False, -7, -7), ("America/Anchorage", True, -9, -8),
    ("Pacific/Honolulu", False, -10, -10), ("Asia/Kolkata", False, 5.5, 5.5),
]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def configure(world, svc, tz, hhmm, *, at, enabled=True, **extra):
    """Save the schedule with the clock at ``at`` (UTC), then leave the clock there."""
    world.now[0] = at
    svc.store.ensure_schema()
    return svc.update_config(OP, {"timezone": tz, "generation_time": hhmm, "enabled": enabled, **extra})


def ticks(world, svc, start, end, step_minutes=5):
    """Run the scheduler check every ``step_minutes`` from start to end (UTC); returns the tick results."""
    out, t = [], start
    while t <= end:
        world.now[0] = t
        out.append((t, svc.tick()))
        t += timedelta(minutes=step_minutes)
    return out


def scheduled_runs(world):
    conn = sqlite3.connect(world.db)
    rows = conn.execute("SELECT post_date, status, attempts FROM community_runs WHERE trigger='scheduled' "
                        "ORDER BY started_at_utc").fetchall()
    conn.close()
    return rows


def drafts_by_date(world):
    conn = sqlite3.connect(world.db)
    rows = conn.execute("SELECT post_date, COUNT(*) FROM community_drafts GROUP BY post_date").fetchall()
    conn.close()
    return dict(rows)


# == timezones ======================================================================================================
@pytest.mark.parametrize("tz,dst,winter,summer", PRESETS)
def test_every_preset_timezone(tz, dst, winter, summer):
    assert SCH.valid_timezone(tz) and tz in [p[0] for p in SCH.PRESET_TIMEZONES]
    z = ZoneInfo(tz)
    jan = SCH.resolve(date(2027, 1, 15), "09:00", z)
    jul = SCH.resolve(date(2027, 7, 15), "09:00", z)
    assert jan.start_utc == datetime(2027, 1, 15, 9, tzinfo=z).astimezone(UTC)
    assert jan.start_utc.astimezone(z).utcoffset() == timedelta(hours=winter)
    assert jul.start_utc.astimezone(z).utcoffset() == timedelta(hours=summer)
    assert jan.local(z).strftime("%H:%M") == jul.local(z).strftime("%H:%M") == "09:00"   # same wall clock all year
    d = SCH.tz_details(tz, datetime(2027, 1, 15, 12, tzinfo=UTC))
    assert d["observes_dst"] is dst and d["dst_active"] is False
    assert (d["next_transition_utc"] is not None) is dst
    sign = "+" if winter >= 0 else "-"
    hours, minutes = int(abs(winter)), int(round((abs(winter) % 1) * 60))
    assert d["utc_offset"] == f"UTC{sign}{hours:02d}:{minutes:02d}"


def test_presets_listing_and_zone_list():
    assert SCH.valid_timezone(None) is False
    p = SCH.presets(datetime(2026, 11, 20, 12, tzinfo=UTC))
    assert [x["id"] for x in p] == [z for z, _ in SCH.PRESET_TIMEZONES]
    ids = SCH.timezone_ids()
    assert len(ids) > 400 and "America/New_York" in ids and "Europe/London" in ids and "UTC" in ids
    assert not any(z.startswith("Etc/") or z in ("EST", "MST", "HST", "EST5EDT") for z in ids)


@pytest.mark.parametrize("bad", ["Mars/Base", "EST", "MST7MDT", "Etc/GMT+5", "UTC+5", "+05:30", "GMT-4", "",
                                 "america/new_york", "America/New_York ", "x" * 70, 5, ["America/New_York"]])
def test_invalid_timezones_are_rejected_without_saving(world, bad):
    """(None is not here: at the service level None means 'leave unchanged', like JSON null in the API.)"""
    assert SCH.valid_timezone(bad) is False
    svc = world.svc()
    svc.store.ensure_schema()
    before = svc.config()
    with pytest.raises(svc_mod.BadRequest):
        svc.update_config(OP, {"timezone": bad, "generation_time": "09:00", "enabled": True})
    assert svc.config() == before                                      # nothing saved, including the valid fields
    conn = sqlite3.connect(world.db)
    assert conn.execute("SELECT COUNT(*) FROM community_events WHERE event='CONFIG_CHANGED'").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("field,value", [("generation_time", "9:00"), ("generation_time", "24:00"),
                                         ("generation_time", "09:60"), ("generation_time", "9am"),
                                         ("catch_up_hours", 24), ("catch_up_hours", -1), ("catch_up_hours", "12"),
                                         ("max_auto_attempts", 0), ("max_auto_attempts", 4), ("max_auto_attempts", True),
                                         ("enabled", "yes")])
def test_invalid_settings_are_rejected_atomically(world, field, value):
    svc = world.svc()
    svc.store.ensure_schema()
    with pytest.raises(svc_mod.BadRequest):
        svc.update_config(OP, {"timezone": "America/Chicago", field: value})
    assert svc.config()["timezone"] == "Asia/Kolkata"                   # the valid timezone was not saved either


# == DST ============================================================================================================
def test_dst_transitions_keep_the_local_wall_clock():
    ny = ZoneInfo("America/New_York")
    before = SCH.resolve(date(2026, 3, 7), "09:00", ny)                # EST
    after = SCH.resolve(date(2026, 3, 9), "09:00", ny)                 # EDT
    assert before.start_utc.strftime("%H:%M") == "14:00" and after.start_utc.strftime("%H:%M") == "13:00"
    autumn_before = SCH.resolve(date(2026, 10, 31), "09:00", ny)
    autumn_after = SCH.resolve(date(2026, 11, 2), "09:00", ny)
    assert autumn_before.start_utc.strftime("%H:%M") == "13:00" and autumn_after.start_utc.strftime("%H:%M") == "14:00"
    assert all(r.note is None for r in (before, after, autumn_before, autumn_after))


def test_spring_forward_nonexistent_time_shifts_forward():
    ny = ZoneInfo("America/New_York")
    r = SCH.resolve(date(2026, 3, 8), "02:30", ny)                     # 02:00 -> 03:00 that night
    assert r.note == "nonexistent"
    assert r.start_utc == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    assert r.local(ny).strftime("%H:%M %Z") == "03:30 EDT"
    notes = SCH.dst_notes("02:30", ny, datetime(2026, 1, 1, tzinfo=UTC))
    assert notes[0]["date"] == "2026-03-08" and notes[0]["case"] == "nonexistent" and "does not exist" in notes[0]["explanation"]


def test_fall_back_ambiguous_time_uses_first_occurrence():
    ny = ZoneInfo("America/New_York")
    r = SCH.resolve(date(2026, 11, 1), "01:30", ny)                    # 01:00-02:00 happens twice
    assert r.note == "ambiguous"
    assert r.start_utc == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)     # 01:30 EDT, the first one
    assert r.local(ny).strftime("%Z") == "EDT"
    notes = SCH.dst_notes("01:30", ny, datetime(2026, 6, 1, tzinfo=UTC))
    assert notes[0]["date"] == "2026-11-01" and notes[0]["case"] == "ambiguous"


def test_spring_forward_day_generates_exactly_once(world):
    svc = world.svc()
    configure(world, svc, "America/New_York", "02:30", at=datetime(2026, 3, 7, 12, tzinfo=UTC))
    ticks(world, svc, datetime(2026, 3, 8, 5, 0, tzinfo=UTC), datetime(2026, 3, 8, 10, 0, tzinfo=UTC))
    runs = scheduled_runs(world)
    assert [r[0] for r in runs] == ["2026-03-08"] and runs[0][1] == "SUCCEEDED"
    conn = sqlite3.connect(world.db)
    started = conn.execute("SELECT started_at_utc FROM community_runs").fetchone()[0]
    conn.close()
    assert started.startswith("2026-03-08T07:30")                       # 03:30 EDT, not before


def test_fall_back_day_generates_exactly_once(world):
    svc = world.svc()
    configure(world, svc, "America/New_York", "01:30", at=datetime(2026, 10, 31, 12, tzinfo=UTC))
    # every 5 minutes from 00:00 EDT through 03:00 EST: 01:30 occurs twice on the wall clock
    ticks(world, svc, datetime(2026, 11, 1, 4, 0, tzinfo=UTC), datetime(2026, 11, 1, 8, 0, tzinfo=UTC))
    assert [r[0] for r in scheduled_runs(world)] == ["2026-11-01"]
    assert drafts_by_date(world) == {"2026-11-01": 1}


def test_catch_up_window_is_elapsed_time_across_dst(world):
    ny = ZoneInfo("America/New_York")
    r, end = SCH.window(date(2026, 10, 31), "20:00", ny, 12)            # window crosses the fall-back night
    assert end - r.start_utc == timedelta(hours=12)
    assert end.astimezone(ny).strftime("%H:%M %Z") == "07:00 EST"       # 13 wall-clock hours later, 12 real hours


# == next run, duplicates, catch-up, attempts =======================================================================
def test_next_run_in_selected_timezone(world):
    svc = world.svc()
    configure(world, svc, "America/Los_Angeles", "09:00", at=datetime(2026, 11, 20, 15, tzinfo=UTC))   # 07:00 PST
    st = svc.schedule_status()
    assert st["state"] == "waiting" and st["next_run_utc"] == "2026-11-20T17:00:00.000000Z"
    assert st["next_run_local"].startswith("2026-11-20T09:00:00-08:00")
    assert st["timezone_details"]["abbreviation"] == "PST" and st["timezone_details"]["utc_offset"] == "UTC-08:00"
    world.now[0] = datetime(2026, 11, 20, 17, 3, tzinfo=UTC)
    assert svc.tick()["status"] == "generated"
    st = svc.schedule_status()
    assert st["state"] == "completed" and st["next_run_local"].startswith("2026-11-21T09:00:00-08:00")
    assert st["last_result"]["run_status"] == "SUCCEEDED" and st["last_config_change"]["by"] == f"operator:{OP}"


def test_duplicate_prevention_by_local_calendar_date(world):
    """Kolkata date 2026-11-21 spans two UTC dates; only one draft is produced for it however often ticks run."""
    svc = world.svc()
    configure(world, svc, "Asia/Kolkata", "02:00", at=datetime(2026, 11, 20, 12, tzinfo=UTC))
    ticks(world, svc, datetime(2026, 11, 20, 20, 0, tzinfo=UTC), datetime(2026, 11, 21, 12, 0, tzinfo=UTC), 15)
    assert [r[0] for r in scheduled_runs(world)] == ["2026-11-21"]
    assert drafts_by_date(world) == {"2026-11-21": 1}
    restarted = world.svc()                                               # a restart reads the same state
    world.now[0] = datetime(2026, 11, 21, 12, 5, tzinfo=UTC)
    assert restarted.tick()["status"] == "done"
    assert len(scheduled_runs(world)) == 1


def test_late_target_catch_up_crosses_local_midnight(world):
    """23:30 Los Angeles target; the scheduler is down until 00:20 the next day: the draft for the PREVIOUS local
    date is still generated (inside the 12 h window), exactly once, and the next day keeps its own run."""
    svc = world.svc()
    configure(world, svc, "America/Los_Angeles", "23:30", at=datetime(2026, 11, 20, 12, tzinfo=UTC))
    world.now[0] = datetime(2026, 11, 22, 8, 20, tzinfo=UTC)            # 00:20 PST on the 22nd
    res = svc.tick()
    assert res["status"] == "generated" and res["draft"]["draft"]["post_date"] == "2026-11-21"
    assert svc.tick()["status"] in ("not_due", "done")
    ticks(world, svc, datetime(2026, 11, 23, 7, 0, tzinfo=UTC), datetime(2026, 11, 23, 8, 0, tzinfo=UTC), 10)
    assert [r[0] for r in scheduled_runs(world)] == ["2026-11-21", "2026-11-22"]


def test_catch_up_and_missed(world):
    svc = world.svc()
    configure(world, svc, "America/Chicago", "08:00", at=datetime(2026, 11, 20, 20, tzinfo=UTC), catch_up_hours=3)   # after the 20th's window
    world.now[0] = datetime(2026, 11, 21, 16, 30, tzinfo=UTC)            # 10:30 CST: inside 3 h
    assert svc.tick()["status"] == "generated"
    world.now[0] = datetime(2026, 11, 22, 18, 30, tzinfo=UTC)            # 12:30 CST next day: window closed
    assert svc.tick()["status"] == "missed"
    assert svc.tick()["status"] == "done"
    assert any("missed" in s.lower() for s, _, _ in world.notes)
    assert [r[:2] for r in scheduled_runs(world)] == [("2026-11-21", "SUCCEEDED"), ("2026-11-22", "SKIPPED")]


@pytest.mark.parametrize("max_attempts,expected", [(1, 1), (2, 2), (3, 3)])
def test_maximum_daily_attempts(world, monkeypatch, max_attempts, expected):
    svc = world.svc()
    configure(world, svc, "America/Denver", "09:00", at=datetime(2026, 11, 20, 20, tzinfo=UTC),   # after the 20th's window
              max_auto_attempts=max_attempts)
    monkeypatch.setattr(svc.reader, "sources", lambda now: [])           # every attempt fails: no topic
    ticks(world, svc, datetime(2026, 11, 21, 16, 0, tzinfo=UTC), datetime(2026, 11, 21, 20, 0, tzinfo=UTC), 5)
    runs = scheduled_runs(world)
    assert len(runs) == 1 and runs[0][2] == expected and runs[0][1] == "FAILED"
    conn = sqlite3.connect(world.db)
    assert conn.execute("SELECT COUNT(*) FROM community_drafts").fetchone()[0] == expected
    conn.close()


# == settings, OFF behaviour, enabling ============================================================================
def test_settings_save_load_and_audit(world):
    svc = world.svc()
    configure(world, svc, "America/Phoenix", "07:45", at=datetime(2026, 11, 20, 12, tzinfo=UTC), enabled=False,
              catch_up_hours=6, max_auto_attempts=3)
    again = world.svc()                                                   # a new process loads the same settings
    cfg = again.config()
    assert (cfg["timezone"], cfg["generation_time"], cfg["catch_up_hours"], cfg["max_auto_attempts"], cfg["enabled"]) == \
        ("America/Phoenix", "07:45", 6, 3, False)
    lc = again.last_config_change()
    assert lc["by"] == f"operator:{OP}" and set(lc["changes"]) == {"timezone", "generation_time", "catch_up_hours",
                                                                   "max_auto_attempts"}
    conn = sqlite3.connect(world.db)
    keys = [r[0] for r in conn.execute("SELECT key FROM community_config")]
    n_events = conn.execute("SELECT COUNT(*) FROM community_events WHERE event='CONFIG_CHANGED'").fetchone()[0]
    conn.close()
    assert len(keys) == len(set(keys))                                    # one row per setting
    again.update_config(OP, {"timezone": "America/Phoenix", "generation_time": "07:45"})   # identical save
    conn = sqlite3.connect(world.db)
    assert conn.execute("SELECT COUNT(*) FROM community_events WHERE event='CONFIG_CHANGED'").fetchone()[0] == n_events
    conn.close()


def test_automation_off_generates_nothing(world, monkeypatch):
    svc = world.svc()
    configure(world, svc, "America/New_York", "09:00", at=datetime(2026, 11, 20, 12, tzinfo=UTC), enabled=False)
    results = ticks(world, svc, datetime(2026, 11, 20, 13, 0, tzinfo=UTC), datetime(2026, 11, 21, 15, 0, tzinfo=UTC), 30)
    assert {r["status"] for _, r in results} == {"blocked"}
    assert scheduled_runs(world) == [] and drafts_by_date(world) == {}
    st = svc.schedule_status()
    assert st["state"] == "blocked" and st["next_run_utc"] is None and st["next_run_if_enabled_utc"]


def test_enabling_after_todays_target_waits_for_tomorrow(world):
    svc = world.svc()
    configure(world, svc, "America/New_York", "09:00", at=datetime(2026, 11, 20, 16, tzinfo=UTC))   # 11:00 EST
    st = svc.schedule_status()
    assert st["state"] == "not_enabled_yet" and st["next_run_local"].startswith("2026-11-21T09:00:00-05:00")
    assert svc.tick()["status"] == "not_due" and scheduled_runs(world) == []
    world.now[0] = datetime(2026, 11, 21, 14, 2, tzinfo=UTC)
    assert svc.tick()["status"] == "generated"


def test_preview_does_not_save(world):
    svc = world.svc()
    world.now[0] = datetime(2026, 3, 1, 12, tzinfo=UTC)
    p = svc.preview("America/New_York", "02:30", 12)
    assert len(p["upcoming"]) == 3 and p["timezone"]["abbreviation"] == "EST"
    assert any(n["case"] == "nonexistent" for n in p["dst_notes"])
    with pytest.raises(svc_mod.BadRequest):
        svc.preview("EST", "02:30", 12)
    assert svc.store.tables_exist() is False                             # a preview never writes


# == API, isolation =================================================================================================
def test_schedule_api_security_and_validation(world, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    svc = world.svc()
    monkeypatch.setattr(svc_mod, "_instance", svc)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    remote = TestClient(main.app)
    h = {"X-CrazzyDocs-CSRF": local.get("/api/autonomy/session").json()["csrf_token"]}
    body = {"timezone": "America/New_York", "generation_time": "09:00", "max_auto_attempts": 2}
    for path in ("/api/community/timezones", "/api/community/schedule",
                 "/api/community/schedule/preview?timezone=America/New_York&generation_time=09:00"):
        assert remote.get(path).status_code == 403 and local.get(path).status_code == 200, path
    assert remote.post("/api/community/schedule", json=body, headers=h).status_code == 403
    assert local.post("/api/community/schedule", json=body).status_code == 403                       # no CSRF
    assert local.post("/api/community/schedule", json=body, headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert local.post("/api/community/schedule", content=json.dumps(body),
                      headers={**h, "Content-Type": "text/plain"}).status_code == 415
    assert local.post("/api/community/schedule", json={**body, "operator": "system"}, headers=h).status_code == 422
    assert local.post("/api/community/schedule", json={**body, "publish_at": "09:00"}, headers=h).status_code == 422
    assert local.post("/api/community/schedule", json={**body, "max_auto_attempts": 9}, headers=h).status_code == 422
    r = local.post("/api/community/schedule", json={**body, "timezone": "Etc/GMT+5"}, headers=h)
    assert r.status_code == 400 and "IANA" in r.json()["detail"]
    assert local.get("/api/community/schedule").json()["config"]["timezone"] == "Asia/Kolkata"   # nothing saved
    assert local.get("/api/community/schedule/preview?timezone=Mars/Base&generation_time=09:00").status_code == 400
    ok = local.post("/api/community/schedule", json=body, headers=h)
    assert ok.status_code == 200 and ok.json()["config"]["timezone"] == "America/New_York"
    assert ok.json()["config"]["enabled"] is False and ok.json()["youtube_publishing"]["available"] is False
    tz = local.get("/api/community/timezones").json()
    assert [p["id"] for p in tz["presets"]][:4] == ["America/New_York", "America/Chicago", "America/Denver",
                                                     "America/Los_Angeles"] and len(tz["zones"]) > 400


def test_scheduling_never_reaches_youtube_or_production(world, monkeypatch):
    from backend.app.scheduler import scheduler as sched_mod
    def no_network(*a, **kw):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    sched_mod.configure_scheduler_jobs()
    jobs_before = sorted((j.id, str(j.trigger)) for j in sched_mod.scheduler.get_jobs())
    profile_before = sched_mod.get_active_profile()
    before = world.production_hash()
    svc = world.svc()
    configure(world, svc, "America/New_York", "09:00", at=datetime(2026, 11, 20, 12, tzinfo=UTC))
    ticks(world, svc, datetime(2026, 11, 21, 13, 55, tzinfo=UTC), datetime(2026, 11, 21, 14, 15, tzinfo=UTC))
    sched_mod.configure_scheduler_jobs()
    assert sorted((j.id, str(j.trigger)) for j in sched_mod.scheduler.get_jobs()) == jobs_before
    profile_after = sched_mod.get_active_profile()
    assert (profile_after.name, len(profile_after.cycles)) == (profile_before.name, len(profile_before.cycles))
    assert [j for j, _ in jobs_before].count("community_draft_tick") == 1
    assert world.production_hash() == before
    src = (svc_mod.Path(svc_mod.__file__).parent / "schedule.py").read_text(encoding="utf-8")
    for bad in ("youtube", "googleapiclient", "oauth", "upload", "publish_at", "posts.insert", "urlopen", "requests"):
        assert bad not in src.lower(), bad
