"""Named Production Schedule Profiles.

Every test runs against a temporary SQLite database (the control-center SessionLocal is redirected) with a fixed
clock. Activation goes through the real schedule_service / control_center_service path against that temporary
database; the scheduler daemon, workers and YouTube are never involved.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.db import Base, JobModel, ProductionScheduleProfileModel, StudioSettingModel
from backend.app.scheduler import schedule_profiles, scheduler as scheduler_module
from backend.app.services import control_center_service as cc
from backend.app.services import schedule_profile_library as spl
from backend.app.services import schedule_service

IST = ZoneInfo("Asia/Kolkata")
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=IST)          # outside every window used below
IN_WINDOW = datetime(2026, 11, 20, 6, 30, tzinfo=IST)     # inside cycle_2 (06:00, 60 min) of PLAN_A


def cyc(n, start, rec=60, target=None, enabled=True, days=None):
    return {"cycle_id": f"cycle_{n}", "name": f"C{n}", "start_time": start, "recovery_duration_minutes": rec,
            "target_publish_time": target, "enabled": enabled, "active_weekdays": days or [0, 1, 2, 3, 4, 5, 6]}


def spec(*cycles, tz="Asia/Kolkata"):
    return {"profile_name": "CUSTOM", "timezone_name": tz, "cycles": list(cycles)}


PLAN_A = spec(cyc(1, "00:00"), cyc(2, "06:00", target="10:00"))
PLAN_B = spec(cyc(1, "03:00", rec=90), cyc(3, "18:00", rec=120, target="22:00"))


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'profiles.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cc, "SessionLocal", factory)
    monkeypatch.setattr(scheduler_module, "SessionLocal", factory)
    original = schedule_profiles.get_active_profile()
    yield factory
    schedule_profiles.set_runtime_profile(original)
    engine.dispose()


def raw(factory, key):
    with factory() as s:
        row = s.get(StudioSettingModel, key)
        return row.value if row else None


def activate(plan):
    return schedule_service.activate(plan, True)


def add_job(factory, job_id, status, stage="SCRIPT"):
    with factory() as s:
        s.add(JobModel(id=job_id, date_str="2026-11-20", status=status, current_stage=stage))
        s.commit()


def history_kinds(factory):
    return [h["kind"] for h in json.loads(raw(factory, cc.HISTORY_KEY) or "[]")]


# -- save and restore ----------------------------------------------------------------------------------------
def test_save_and_restore(db):
    activate(PLAN_A)
    before = raw(db, cc.ACTIVE_PROFILE_KEY)
    saved = spl.save_current("Weekday plan", "two cycles")
    assert raw(db, cc.ACTIVE_PROFILE_KEY) == before                      # saving never changes the active schedule
    assert saved["name"] == "Weekday plan" and saved["description"] == "two cycles"
    assert saved["cycles_enabled"] == 2 and saved["timezone"] == "Asia/Kolkata" and saved["recovery_window"] == "60 min (all cycles)"
    assert saved["created_at_utc"] == saved["updated_at_utc"] and saved["schema_version"] == 1
    assert saved["config"] == spl.schedule_config(PLAN_A) and saved["status"] == "ACTIVE"
    activate(PLAN_B)
    assert spl.checksum(schedule_service.active_spec()) == spl.checksum(PLAN_B)
    pv = spl.preview(saved["id"], NOON)
    assert pv["can_activate"] and not pv["differences"]["identical"]
    out = spl.load(saved["id"], True, pv["checksum"], NOON)
    assert out["status"] == "ACTIVATED"
    active = schedule_service.active_spec()
    assert spl.checksum(active) == saved["checksum"]                     # restored exactly
    assert active["schedule_version"] == 4                               # A=2, B=3, restore=4 (normal activation path)
    assert [c.cycle_id for c in schedule_profiles.get_active_profile().cycles] == ["cycle_1", "cycle_2"]
    assert "schedule_profile_saved" in history_kinds(db) and "schedule_profile_loaded" in history_kinds(db)
    loaded = [h for h in json.loads(raw(db, cc.HISTORY_KEY)) if h["kind"] == "schedule_profile_loaded"][-1]
    assert loaded["new"]["profile_id"] == saved["id"] and loaded["previous"]["checksum"] == spl.checksum(PLAN_B)
    assert spl.active_indicator()["state"] == "PROFILE_ACTIVE"


def test_loading_identical_profile_does_not_reactivate(db):
    activate(PLAN_A)
    p = spl.save_current("A")
    version = schedule_service.active_spec()["schedule_version"]
    out = spl.load(p["id"], True, p["checksum"], NOON)
    assert out["status"] == "ALREADY_ACTIVE" and schedule_service.active_spec()["schedule_version"] == version


def test_load_requires_confirmation_and_matching_checksum(db):
    activate(PLAN_A)
    p = spl.save_current("A")
    activate(PLAN_B)
    with pytest.raises(spl.ProfileError):
        spl.load(p["id"], False, p["checksum"], NOON)
    with pytest.raises(spl.ProfileConflict):
        spl.load(p["id"], True, "0" * 64, NOON)
    assert spl.checksum(schedule_service.active_spec()) == spl.checksum(PLAN_B)


# -- names ---------------------------------------------------------------------------------------------------
def test_custom_names(db):
    activate(PLAN_A)
    p = spl.save_current("  Diwali   week — 4×/day ✨ ")
    assert p["name"] == "Diwali week — 4×/day ✨"
    for bad in ("", "   ", "x" * 81, "bad\x00name", "\t\n", "!!!", None, 42):
        with pytest.raises(spl.ProfileError):
            spl.save_current(bad)
    assert spl.list_profiles()["count"] == 1


def test_duplicate_names_are_refused(db):
    activate(PLAN_A)
    a = spl.save_current("Night Shift")
    with pytest.raises(spl.ProfileConflict):
        spl.save_current("night  shift")                               # case and spacing insensitive
    b = spl.duplicate(a["id"], "Day Shift")
    with pytest.raises(spl.ProfileConflict):
        spl.rename(b["id"], "NIGHT SHIFT")
    with pytest.raises(spl.ProfileConflict):
        spl.duplicate(a["id"], "Day shift")
    assert spl.rename(a["id"], "Night Shift")["name"] == "Night Shift"   # renaming to itself is fine


def test_rename_keeps_configuration(db, monkeypatch):
    activate(PLAN_A)
    p = spl.save_current("Old")
    monkeypatch.setattr(spl, "_utcnow", lambda: datetime(2030, 1, 1, tzinfo=ZoneInfo("UTC")))
    r = spl.rename(p["id"], "New name")
    assert r["name"] == "New name" and r["checksum"] == p["checksum"] and r["config"] == p["config"]
    assert r["created_at_utc"] == p["created_at_utc"] and r["updated_at_utc"] == "2030-01-01T00:00:00Z"
    assert spl.active_indicator()["profile_name"] == "New name"
    assert "schedule_profile_renamed" in history_kinds(db)


def test_duplicate_profile(db):
    activate(PLAN_A)
    p = spl.save_current("Original")
    d = spl.duplicate(p["id"], "Copy")
    assert d["id"] != p["id"] and d["checksum"] == p["checksum"] and d["config"] == p["config"]
    activate(PLAN_B)
    spl.delete(p["id"], True)
    assert spl.get_profile(d["id"])["name"] == "Copy"                    # independent of the original
    assert "schedule_profile_duplicated" in history_kinds(db)


# -- delete --------------------------------------------------------------------------------------------------
def test_delete_rules(db):
    activate(PLAN_A)
    p = spl.save_current("A")
    with pytest.raises(spl.ProfileError):
        spl.delete(p["id"], False)
    with pytest.raises(spl.ProfileConflict):                            # it is the active schedule
        spl.delete(p["id"], True)
    activate(PLAN_B)                                                    # another valid schedule is now active
    assert spl.delete(p["id"], True)["deleted"] is True
    assert spl.list_profiles()["count"] == 0
    assert raw(db, spl.ACTIVE_MARKER_KEY) is None                       # marker to the deleted profile cleared
    with pytest.raises(spl.ProfileNotFound):
        spl.get_profile(p["id"])
    with pytest.raises(spl.ProfileNotFound):
        spl.delete("sp_0123456789abcdef", True)
    assert "schedule_profile_deleted" in history_kinds(db)


# -- invalid configuration and checksum ------------------------------------------------------------------------
def _insert(factory, config, name="Injected", checksum=None, schema=1):
    with factory() as s:
        row = ProductionScheduleProfileModel(id="sp_" + "a" * 16, name=name, name_key=name.casefold(),
                                             config_json=json.dumps(config), checksum=checksum or spl.checksum(config),
                                             schema_version=schema, created_at_utc="2026-11-20T00:00:00Z",
                                             updated_at_utc="2026-11-20T00:00:00Z")
        s.add(row)
        s.commit()
        return row.id


def test_invalid_configurations_are_refused(db):
    with pytest.raises(spl.ProfileError):
        spl.save_current("nothing active")                             # no active schedule yet
    activate(PLAN_B)
    before = raw(db, cc.ACTIVE_PROFILE_KEY)
    pid = _insert(db, spl.schedule_config(spec(cyc(1, "01:00", enabled=False))))    # no enabled cycle
    pv = spl.preview(pid, NOON)
    assert pv["validation"]["valid"] is False and not pv["can_activate"]
    with pytest.raises(spl.ProfileError):
        spl.load(pid, True, pv["checksum"], NOON)
    assert raw(db, cc.ACTIVE_PROFILE_KEY) == before


def test_checksum_detects_tampering_and_schema(db):
    activate(PLAN_A)
    p = spl.save_current("A")
    with db() as s:
        row = s.get(ProductionScheduleProfileModel, p["id"])
        cfg = json.loads(row.config_json)
        cfg["cycles"][0]["recovery_duration_minutes"] = 30
        row.config_json = json.dumps(cfg)
        s.commit()
    listed = spl.list_profiles()["profiles"][0]
    assert listed["valid_data"] is False and "checksum" in listed["data_error"]
    with pytest.raises(spl.ProfileError, match="checksum"):
        spl.preview(p["id"], NOON)
    with pytest.raises(spl.ProfileError, match="checksum"):
        spl.load(p["id"], True, p["checksum"], NOON)
    bad_schema = _insert(db, spl.schedule_config(PLAN_B), name="Future", schema=2)
    with pytest.raises(spl.ProfileError, match="schema version"):
        spl.preview(bad_schema, NOON)


def test_checksum_properties():
    a = spl.checksum(PLAN_A)
    assert a == spl.checksum(spec(*reversed(PLAN_A["cycles"])))            # order-independent
    assert len(a) == 64 and a == spl.checksum(json.loads(json.dumps(PLAN_A)))
    alt = json.loads(json.dumps(PLAN_A))
    alt["cycles"][0]["target_publish_time"] = ""                         # empty == not set
    assert spl.checksum(alt) == a
    alt["cycles"][0]["recovery_duration_minutes"] = 61
    assert spl.checksum(alt) != a
    assert spl.checksum(spec(*PLAN_A["cycles"], tz="UTC")) != a


# -- active profile and unsaved modifications ---------------------------------------------------------------
def test_active_profile_identification(db):
    assert spl.active_indicator()["state"] == "NO_ACTIVE_SCHEDULE"
    activate(PLAN_A)
    ind = spl.active_indicator()
    assert ind["state"] == "UNSAVED" and ind["unsaved_changes"] is True
    p = spl.save_current("Plan A")
    ind = spl.active_indicator()
    assert ind["state"] == "PROFILE_ACTIVE" and ind["profile_name"] == "Plan A" and not ind["unsaved_changes"]
    with db() as s:                                                      # no marker, but the active schedule matches
        s.delete(s.get(StudioSettingModel, spl.ACTIVE_MARKER_KEY))
        s.commit()
    ind = spl.active_indicator()
    assert ind["state"] == "MATCHES_PROFILE" and ind["profile_id"] == p["id"]
    statuses = {x["name"]: x["status"] for x in spl.list_profiles()["profiles"]}
    assert statuses == {"Plan A": "ACTIVE"}


def test_unsaved_modifications_after_loading(db):
    activate(PLAN_A)
    p = spl.save_current("Plan A")
    modified = json.loads(json.dumps(PLAN_A))
    modified["cycles"][1]["recovery_duration_minutes"] = 90
    activate(modified)                                                   # manual change in the editor
    ind = spl.active_indicator()
    assert ind["state"] == "PROFILE_MODIFIED" and ind["unsaved_changes"] is True and ind["profile_name"] == "Plan A"
    row = spl.list_profiles()["profiles"][0]
    assert row["status"] == "ACTIVE_MODIFIED" and row["is_active"] is False
    activate(PLAN_A)                                                     # back to the saved configuration
    assert spl.active_indicator()["state"] == "PROFILE_ACTIVE"


# -- comparison ----------------------------------------------------------------------------------------------
def test_schedule_comparison():
    d = spl.compare(PLAN_A, PLAN_B)
    assert [c["cycle_id"] for c in d["added"]] == ["cycle_3"]
    assert [c["cycle_id"] for c in d["removed"]] == ["cycle_2"]
    ch = d["changed"][0]
    assert ch["cycle_id"] == "cycle_1" and ch["fields"]["start_time"] == {"from": "00:00", "to": "03:00"}
    assert ch["fields"]["recovery_duration_minutes"] == {"from": 60, "to": 90} and not ch.get("recovery_shortened")
    back = spl.compare(PLAN_B, PLAN_A)
    assert back["recovery_shortened"] == ["cycle_1"] and "recovery shortened" in back["summary"]
    same = spl.compare(PLAN_A, spec(*reversed(PLAN_A["cycles"])))
    assert same["identical"] and same["summary"] == "Identical to the active schedule."
    tz = spl.compare(PLAN_A, spec(*PLAN_A["cycles"], tz="UTC"))
    assert tz["timezone"] == {"from": "Asia/Kolkata", "to": "UTC"} and not tz["identical"]
    none = spl.compare(None, PLAN_A)
    assert not none["has_active_schedule"] and len(none["added"]) == 2


# -- safety --------------------------------------------------------------------------------------------------
def test_activation_refused_during_active_job(db):
    activate(PLAN_A)
    p = spl.save_current("A")
    activate(PLAN_B)
    before = raw(db, cc.ACTIVE_PROFILE_KEY)
    add_job(db, "cycle_3_20261120_1800", "RUNNING", "VOICEOVER")
    pv = spl.preview(p["id"], NOON)
    assert pv["can_activate"] is False and "RUNNING" in pv["blocked_reasons"][0]
    with pytest.raises(spl.ActivationUnsafe) as exc:
        spl.load(p["id"], True, p["checksum"], NOON)
    assert "cycle_3_20261120_1800" in exc.value.reasons[0] and "Try again after it finishes" in exc.value.reasons[0]
    assert raw(db, cc.ACTIVE_PROFILE_KEY) == before                     # nothing changed
    with db() as s:
        s.get(JobModel, "cycle_3_20261120_1800").status = "COMPLETED"
        s.commit()
    assert spl.load(p["id"], True, p["checksum"], NOON)["status"] == "ACTIVATED"


@pytest.mark.parametrize("status,allowed", [(None, False), ("FAILED", False), ("COMPLETED", True)])
def test_open_recovery_window_is_never_shortened(db, status, allowed):
    activate(PLAN_B)
    p = spl.save_current("B")                                            # B has no cycle_2
    activate(PLAN_A)                                                     # cycle_2 window 06:00-07:00 open at 06:30
    if status:
        add_job(db, "cycle_2_20261120_0600", status)
    before = raw(db, cc.ACTIVE_PROFILE_KEY)
    if allowed:
        assert spl.load(p["id"], True, p["checksum"], IN_WINDOW)["status"] == "ACTIVATED"
    else:
        with pytest.raises(spl.ActivationUnsafe) as exc:
            spl.load(p["id"], True, p["checksum"], IN_WINDOW)
        assert "recovery window" in exc.value.reasons[0] and "removes or disables" in exc.value.reasons[0]
        assert raw(db, cc.ACTIVE_PROFILE_KEY) == before


def test_window_shortening_detected_but_lengthening_allowed(db):
    activate(PLAN_A)
    shorter = json.loads(json.dumps(PLAN_A))
    shorter["cycles"][1]["recovery_duration_minutes"] = 30
    assert any("shortens its recovery window" in r for r in spl.safety_check(shorter, IN_WINDOW))
    longer = json.loads(json.dumps(PLAN_A))
    longer["cycles"][1]["recovery_duration_minutes"] = 120
    assert spl.safety_check(longer, IN_WINDOW) == []
    moved = json.loads(json.dumps(PLAN_A))
    moved["cycles"][1]["start_time"] = "06:15"
    assert any("moves its start time" in r for r in spl.safety_check(moved, IN_WINDOW))


def test_youtube_publication_schedule_preserved(db):
    pub = json.dumps({"slots": [{"slot_id": "slot_1", "publish_time": "10:00"}], "revision": "pubrev"})
    cc._write_settings({cc.PUB_SCHEDULE_ACTIVE_KEY: pub, cc.PUB_SCHEDULE_DRAFT_KEY: "draft-sentinel"})
    activate(PLAN_A)
    p = spl.save_current("A")
    spl.duplicate(p["id"], "A2")
    activate(PLAN_B)
    spl.load(p["id"], True, p["checksum"], NOON)
    spl.rename(p["id"], "A renamed")
    assert raw(db, cc.PUB_SCHEDULE_ACTIVE_KEY) == pub and raw(db, cc.PUB_SCHEDULE_DRAFT_KEY) == "draft-sentinel"


def test_profile_management_never_touches_active_schedule_or_jobs(db):
    activate(PLAN_A)
    add_job(db, "cycle_1_20261120_0000", "COMPLETED")
    before = (raw(db, cc.ACTIVE_PROFILE_KEY), raw(db, cc.DRAFT_PROFILE_KEY), raw(db, cc.PROD_SCHEDULE_HISTORY_KEY))
    runtime = schedule_profiles.get_active_profile()
    p = spl.save_current("A")
    d = spl.duplicate(p["id"], "B")
    spl.rename(d["id"], "C")
    spl.preview(d["id"], NOON)
    spl.list_profiles()
    with pytest.raises(spl.ProfileConflict):                            # an identical copy is the active schedule too
        spl.delete(d["id"], True)
    other = _insert(db, spl.schedule_config(PLAN_B), name="Other")
    spl.delete(other, True)
    assert (raw(db, cc.ACTIVE_PROFILE_KEY), raw(db, cc.DRAFT_PROFILE_KEY), raw(db, cc.PROD_SCHEDULE_HISTORY_KEY)) == before
    assert schedule_profiles.get_active_profile() is runtime
    with db() as s:
        assert [(j.id, j.status) for j in s.query(JobModel).all()] == [("cycle_1_20261120_0000", "COMPLETED")]


def test_existing_schedule_functionality_unchanged(db):
    res = activate(PLAN_A)
    assert res["status"] == "ACTIVATED"
    view = schedule_service.schedule_view(NOON)
    assert view["has_active_schedule"] and [c["cycle_id"] for c in view["cycles"]] == ["cycle_1", "cycle_2"]
    assert schedule_service.validate(PLAN_B)["valid"]
    with pytest.raises(PermissionError):
        schedule_service.activate(PLAN_B, False)


# -- API -----------------------------------------------------------------------------------------------------
def test_api_endpoints(db, monkeypatch):
    from backend.app import main
    monkeypatch.setattr(spl, "safety_check", lambda config, now_ist=None: [])
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    remote = TestClient(main.app)
    assert remote.get("/api/schedule/profiles").status_code == 403
    activate(PLAN_A)
    assert remote.post("/api/schedule/profiles", json={"name": "x"}).status_code == 403
    assert local.post("/api/schedule/profiles", json={"name": "x"}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert local.post("/api/schedule/profiles", json={"name": "x"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert local.post("/api/schedule/profiles", json={"name": "x", "evil": 1}).status_code == 422
    r = local.post("/api/schedule/profiles", json={"name": "API plan", "description": "d"})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert local.post("/api/schedule/profiles", json={"name": "api PLAN"}).status_code == 409
    listing = local.get("/api/schedule/profiles").json()
    assert listing["count"] == 1 and listing["active"]["state"] == "PROFILE_ACTIVE"
    activate(PLAN_B)
    pv = local.get(f"/api/schedule/profiles/{pid}/preview").json()
    assert pv["differences"]["summary"] and pv["can_activate"]
    assert local.post(f"/api/schedule/profiles/{pid}/load", json={"confirm": False, "expected_checksum": pv["checksum"]}).status_code == 400
    assert local.post(f"/api/schedule/profiles/{pid}/load", json={"confirm": True, "expected_checksum": "f" * 64}).status_code == 409
    ok = local.post(f"/api/schedule/profiles/{pid}/load", json={"confirm": True, "expected_checksum": pv["checksum"]})
    assert ok.status_code == 200 and ok.json()["status"] == "ACTIVATED"
    assert local.post(f"/api/schedule/profiles/{pid}/rename", json={"name": "Renamed"}).json()["name"] == "Renamed"
    dup = local.post(f"/api/schedule/profiles/{pid}/duplicate", json={"name": "Copy"}).json()
    assert local.post(f"/api/schedule/profiles/{pid}/delete", json={"confirm": True}).status_code == 409   # active
    assert local.post(f"/api/schedule/profiles/{dup['id']}/delete", json={"confirm": False}).status_code == 400
    assert local.get("/api/schedule/profiles/sp_nope").status_code == 404


def test_api_reports_unsafe_activation_reasons(db):
    from backend.app import main
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    activate(PLAN_A)
    p = spl.save_current("A")
    activate(PLAN_B)
    add_job(db, "cycle_3_20261120_1800", "RETRY_SCHEDULED")
    r = local.post(f"/api/schedule/profiles/{p['id']}/load", json={"confirm": True, "expected_checksum": p["checksum"]})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "unsafe" in detail["message"] and "RETRY_SCHEDULED" in detail["reasons"][0]


# -- dashboard -----------------------------------------------------------------------------------------------
def _dashboard_html():
    return (Path(cc.__file__).resolve().parents[1] / "templates" / "dashboard.html").read_text(encoding="utf-8")


def test_dashboard_integration():
    html = _dashboard_html()
    tab = html[html.index('id="tab-schedule"'):html.index('id="tab-prodconfig"')]
    for el in ("schedProfileIndicator", "schedProfilesCard", "schedProfilesBody", "schedProfilePreview"):
        assert f'id="{el}"' in tab, el
    assert "Save Current Schedule" in tab and "Saved Production Schedules" in tab
    js = "\n".join(re.findall(r"<script>([\s\S]*?)</script>", html))
    names = re.findall(r"^\s*(?:async\s+)?function\s+(\w+)", js, flags=re.M)
    assert len(names) == len(set(names)), "duplicate dashboard function names"
    for fn in ("refreshScheduleProfiles", "schedProfileSave", "schedProfilePreview", "schedProfileLoad",
               "schedProfileRename", "schedProfileDuplicate", "schedProfileDelete", "activateSchedule", "refreshSchedule"):
        assert fn in names, fn
    prof_js = js[js.index("// Named Production Schedule Profiles"):js.index("// Production Monitor + Overview cards")]
    assert "this.dataset.id" in prof_js and "expected_checksum: pv.checksum" in prof_js and "askConfirm" in prof_js
    assert 'if (tabId === "schedule") { refreshScheduleProfiles(); }' in html


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_dashboard_script_parses():
    js = "\n".join(re.findall(r"<script>([\s\S]*?)</script>", _dashboard_html()))
    res = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8')); console.log('ok')"],
                         input=js, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert res.returncode == 0 and res.stdout.strip() == "ok", res.stderr
