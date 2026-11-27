"""Storage Lifecycle Manager (Junk Cleaner upgrade): retention categories, deliverable verification, test renders,
path safety, live-run authorization, configuration persistence and storage accounting."""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.services import junk_cleaner as jc

NOW = datetime.now(timezone.utc).replace(microsecond=0)
OLD = (NOW - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")


def _file(root: Path, rel: str, days_old: float = 90, size: int = 1000) -> Path:
    p = root / "media" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    t = (NOW - timedelta(days=days_old)).timestamp()
    os.utime(p, (t, t))
    return p


def _db(root: Path, jobs=(), pubs=(), thumbs=(), locs=(), queue=()):
    path = root / "studio.db"
    if path.exists():
        path.unlink()
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE persistent_jobs (id TEXT, status TEXT, updated_at TEXT);
        CREATE TABLE publishing_ledger (id INTEGER, video_path TEXT, thumbnail_path TEXT, youtube_video_id TEXT,
                                        upload_status TEXT);
        CREATE TABLE thumbnail_state (ledger_id INTEGER, verify_status TEXT, artifact_path TEXT);
        CREATE TABLE content_localizations (source_job_id TEXT, status TEXT);
        CREATE TABLE production_queue_jobs (id TEXT, state TEXT);""")
    c.executemany("INSERT INTO persistent_jobs VALUES (?,?,?)", jobs)
    c.executemany("INSERT INTO publishing_ledger VALUES (?,?,?,?,?)", pubs)
    c.executemany("INSERT INTO thumbnail_state VALUES (?,?,?)", thumbs)
    c.executemany("INSERT INTO content_localizations VALUES (?,?)", locs)
    c.executemany("INSERT INTO production_queue_jobs VALUES (?,?)", queue)
    c.commit()
    c.close()
    return str(path)


def _cleaner(root, db, lifecycle=None, verifier=None, now=None):
    return jc.JunkCleaner(root=root, db_path=db, now=now or NOW, policy={"policy_version": 1},
                          lifecycle=lifecycle, remote_verifier=verifier)


def _decision(root, db, rel, **kw):
    """Decision for one file (by its media-relative path) in a dry run."""
    rec = _cleaner(root, db, **kw).run(trigger="test")
    for f in rec["eligible_files"]:
        if f["path"].replace("\\", "/").endswith("media/" + rel):
            return "eligible", f["reason"], rec
    return "not_eligible", None, rec


def _published(root, job="j1", status="uploaded_public", vid="VID1", verify="verified", shorts=True):
    art = str(root / "media/productions" / job / "thumbnails" / (f"thumb_{job}" + ("_shorts_1280x720.jpg" if shorts else ".png")))
    return _db(root, jobs=[(job, "COMPLETED", OLD)],
               pubs=[(1, str(root / "media/productions" / job / "video" / "final.mp4"),
                      str(root / "media/productions" / job / "thumbnails" / f"thumb_{job}.png"), vid, status)],
               thumbs=[(1, verify, art)])


# -- 1. every retention category + age eligibility ----------------------------------------------------------------
TEMP_CASES = [("productions/j1/lemo_film/s1.mp4", "intermediate_clips"), ("productions/j1/narration/n1.mp3", "temp_audio"),
              ("asset_cache/a/b.bin", "cache"), ("productions/j1/thumbnails/variants/v1.png", "thumbnail_variants"),
              ("productions/j1/lemo_film/g1.png", "scene_graphics"), ("temp_render/x.tmp", "render_temp"),
              ("productions/j1/lemo_film/index.html", "browser_artifacts")]


@pytest.mark.parametrize("rel,cat", TEMP_CASES)
def test_temporary_categories_follow_their_retention_age(tmp_path, rel, cat):
    days = jc.RETENTION_DEFAULTS[cat]
    _file(tmp_path, "productions/j1/video/final.mp4")
    db = _published(tmp_path, verify="verified")
    assert jc.classify(list(Path(rel).parent.parts), Path(rel).name, "j1") == cat
    _file(tmp_path, rel, days_old=days + 0.5)
    assert _decision(tmp_path, db, rel)[0] == "eligible"
    _file(tmp_path, rel, days_old=days - 0.5)
    st, _, rec = _decision(tmp_path, db, rel)
    assert st == "not_eligible" and rec["categories"][cat]["waiting"] == 1          # forecast, not protected


def test_protected_and_permanent_categories_are_never_eligible(tmp_path):
    db = _db(tmp_path, jobs=[("j1", "COMPLETED", OLD)])
    for rel in ("source/branding/logo.png", "productions/j1/manifests/m.json", "productions/j1/production_manifest.json",
                "quarantine/junk_cleaner/r1/files/x.mp4", "mystery/unknown.bin", "productions/j1/assets/photo.jpg"):
        _file(tmp_path, rel, days_old=3000)
    rec = _cleaner(tmp_path, db).run(trigger="test")
    assert rec["eligible"] == 0 and rec["pending_remote"] == 0
    assert rec["categories"]["unknown"]["protected"] == 1                            # unknown: never automatic
    assert rec["categories"]["downloaded_media"]["protected"] == 1                   # downloaded media: protected default


# -- 2./3. final deliverables: published and verified only ---------------------------------------------------------
@pytest.mark.parametrize("status,vid,expect", [("uploaded_public", "VID1", "pending_remote"),
                                               ("uploaded_private", "VID1", "protected"),
                                               ("publication_scheduled", "VID1", "protected"),
                                               ("upload_failed", "VID1", "protected"),
                                               ("pending_queue", None, "protected")])
def test_final_mp4_needs_a_confirmed_public_publication(tmp_path, status, vid, expect):
    _file(tmp_path, "productions/j1/video/final.mp4", days_old=60)
    rec = _cleaner(tmp_path, _published(tmp_path, status=status, vid=vid)).run(trigger="test")
    c = rec["categories"]["final_mp4"]
    assert (c["pending_remote"] == 1) == (expect == "pending_remote") and c["eligible"] == 0   # dry run: never eligible
    assert (c["protected"] == 1) == (expect == "protected")


def test_unpublished_final_mp4_without_ledger_row_is_protected(tmp_path):
    _file(tmp_path, "productions/j1/video/final.mp4", days_old=400)
    rec = _cleaner(tmp_path, _db(tmp_path, jobs=[("j1", "COMPLETED", OLD)])).run(trigger="test")
    assert rec["categories"]["final_mp4"]["protected"] == 1
    assert "final deliverable without a publication record (unpublished)" in rec["skip_reasons"]


def test_thumbnail_and_audio_follow_the_video_and_the_backfill(tmp_path):
    _file(tmp_path, "productions/j1/video/final.mp4", days_old=60)
    _file(tmp_path, "productions/j1/thumbnails/thumb_j1.png", days_old=60)
    _file(tmp_path, "productions/j1/audio/mix.mp3", days_old=60)
    rec = _cleaner(tmp_path, _published(tmp_path, shorts=False)).run(trigger="test")
    assert rec["categories"]["final_thumbnail"]["protected"] == 1                    # backfill still needs it
    assert rec["categories"]["final_audio"]["pending_remote"] == 1
    rec = _cleaner(tmp_path, _published(tmp_path, shorts=True, verify="pending")).run(trigger="test")
    assert "thumbnail not remotely verified" in rec["skip_reasons"]


def _authorized_live(root, db, verifier):
    dry = _cleaner(root, db, verifier=verifier).run(trigger="test")
    return _cleaner(root, db, verifier=verifier).run(trigger="test", live=True, confirm=True, authorization=dry["run_id"])


def test_youtube_verification_failure_keeps_the_deliverable(tmp_path):
    p = _file(tmp_path, "productions/j1/video/final.mp4", days_old=60)
    db = _published(tmp_path)
    rec = _authorized_live(tmp_path, db, lambda ids: {i: {"ok": False, "reason": "processing failed"} for i in ids})
    assert rec["deleted"] == 0 and p.exists()
    assert "YouTube verification failed: processing failed" in rec["skip_reasons"]
    rec = _authorized_live(tmp_path, db, lambda ids: {})                              # video not returned by YouTube
    assert rec["deleted"] == 0 and p.exists()
    rec = _authorized_live(tmp_path, db, lambda ids: {i: {"ok": True, "reason": "verified"} for i in ids})
    assert rec["deleted"] == 1 and not p.exists()                                     # moved to quarantine, reversible
    assert list((tmp_path / "media/quarantine/junk_cleaner").rglob("final.mp4"))


# -- 4. active jobs and workflows -------------------------------------------------------------------------------------
def test_active_job_open_localization_and_queued_coordinator_protect_files(tmp_path):
    for j in ("run", "loc", "q"):
        _file(tmp_path, f"productions/{j}/lemo_film/s1.mp4", days_old=60)
        _file(tmp_path, f"productions/{j}/video/final.mp4", days_old=60)
    db = _db(tmp_path, jobs=[("run", "RUNNING", OLD), ("loc", "COMPLETED", OLD), ("q", "COMPLETED", OLD)],
             locs=[("loc", "WAITING")], queue=[("loc_hi_q_a0", "QUEUED")])
    rec = _cleaner(tmp_path, db).run(trigger="test")
    assert rec["eligible"] == 0
    assert rec["skip_reasons"]["active job (RUNNING)"] == 2
    assert rec["skip_reasons"]["Hindi localization still uses this production (WAITING)"] == 2
    assert rec["skip_reasons"]["a queued coordinator job references this production"] == 2


def test_missing_metadata_protects(tmp_path):
    _file(tmp_path, "productions/ghost/lemo_film/s1.mp4", days_old=60)
    rec = _cleaner(tmp_path, _db(tmp_path)).run(trigger="test")
    assert rec["eligible"] == 0 and rec["skip_reasons"]["job not in database (unknown state)"] == 1


# -- 5. test renders --------------------------------------------------------------------------------------------------
def _test_folder(root, name, status=None, days_old=10, report=None):
    _file(root, f"test_renders/{name}/render.mp4", days_old=days_old)
    base = root / "media/test_renders" / name
    for fname, doc in ((("TEST_STATUS.json", {"status": status}),) if status else ()) + \
                      ((("summary.json", report),) if report is not None else ()):
        (base / fname).write_text(json.dumps(doc), encoding="utf-8")
        t = (NOW - timedelta(days=days_old)).timestamp()
        os.utime(base / fname, (t, t))


def test_test_render_retention_by_outcome(tmp_path):
    db = _db(tmp_path)
    _test_folder(tmp_path, "ok4", "passed", days_old=4)                 # success: 3 days -> eligible
    _test_folder(tmp_path, "bad4", "failed", days_old=4)                # failed: 7 days -> waiting
    _test_folder(tmp_path, "bad8", report={"passed": False}, days_old=8)
    _test_folder(tmp_path, "run", "active", days_old=30)
    _test_folder(tmp_path, "fresh", "passed", days_old=0.05)            # changed 1 h ago: active
    _test_folder(tmp_path, "bug", "passed", days_old=30)
    _test_folder(tmp_path, "nometa", days_old=30)
    _test_folder(tmp_path, "baseline_qc", "passed", days_old=30)
    life = jc.validate_lifecycle({"protected_tests": ["bug"]})
    rec = _cleaner(tmp_path, db, lifecycle=life).run(trigger="test")
    el = {f["path"].replace("\\", "/").split("test_renders/")[1].split("/")[0] for f in rec["eligible_files"]}
    assert el == {"ok4", "bad8"}
    r = rec["skip_reasons"]
    assert r["younger than test_renders_failed retention (7 days)"] == 1
    assert r[f"active test (changed in the last {jc.TEST_ACTIVE_HOURS} h)"] == 2
    assert r["test evidence for an unresolved investigation"] == 1
    assert r["no TEST_STATUS.json and no result report"] == 1
    assert r["QC baseline / reference evidence"] == 1
    assert rec["categories"]["manifests_metadata"]["eligible"] == 0                   # reports are permanent


# -- 6. path safety ---------------------------------------------------------------------------------------------------
def test_symlinks_are_never_followed_or_moved(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.mp4").write_bytes(b"x" * 10)
    _file(tmp_path, "asset_cache/real.bin", days_old=30)
    try:
        os.symlink(outside, tmp_path / "media/asset_cache/linkdir", target_is_directory=True)
        os.symlink(outside / "precious.mp4", tmp_path / "media/asset_cache/link.bin")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges on this machine")
    rec = _cleaner(tmp_path, _db(tmp_path)).run(trigger="test")
    paths = {f["path"].replace("\\", "/") for f in rec["eligible_files"]}
    assert paths == {"media/asset_cache/real.bin"}
    assert rec["skip_reasons"]["symbolic link / junction: never followed or moved"] == 1
    assert (outside / "precious.mp4").exists()


def test_windows_junctions_are_never_followed(tmp_path):
    import subprocess
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.mp4").write_bytes(b"x" * 10)
    (tmp_path / "media/asset_cache").mkdir(parents=True)
    link = tmp_path / "media/asset_cache/junction"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
    if r.returncode != 0 or not link.exists():
        pytest.skip("directory junctions unavailable")
    t = (NOW - timedelta(days=60)).timestamp()
    os.utime(outside / "precious.mp4", (t, t))
    assert jc._is_link_or_reparse(link)
    rec = _cleaner(tmp_path, _db(tmp_path)).run(trigger="test")
    assert rec["scanned"] == 0 and rec["eligible"] == 0 and (outside / "precious.mp4").exists()


def test_move_refuses_changed_or_outside_files(tmp_path):
    p = _file(tmp_path, "asset_cache/a.bin", days_old=30)
    c = _cleaner(tmp_path, _db(tmp_path))
    ident = jc._identity(p)
    p.write_bytes(b"y" * 5)                                                          # changed after the scan
    with pytest.raises(ValueError, match="changed"):
        c._move_verified(p, ident, ["asset_cache"], "a.bin", None, "r1", (tmp_path / "media").resolve())
    other = tmp_path / "elsewhere.bin"
    other.write_bytes(b"z")
    with pytest.raises(ValueError, match="outside"):
        c._move_verified(other, jc._identity(other), ["asset_cache"], "x.bin", None, "r1", (tmp_path / "media").resolve())
    assert p.exists() and other.exists()


# -- 7. dry run versus live -------------------------------------------------------------------------------------------
def test_live_needs_confirmation_and_a_fresh_matching_dry_run(tmp_path):
    p = _file(tmp_path, "asset_cache/a.bin", days_old=30)
    db = _db(tmp_path)
    dry = _cleaner(tmp_path, db).run(trigger="test")
    assert dry["eligible"] == 1 and p.exists()                                       # dry run never moves
    assert _cleaner(tmp_path, db).run(trigger="t", live=True)["outcome"] == "refused"
    assert _cleaner(tmp_path, db).run(trigger="t", live=True, confirm=True)["outcome"] == "refused"
    assert _cleaner(tmp_path, db).run(trigger="t", live=True, confirm=True, authorization="nope")["outcome"] == "refused"
    changed = jc.validate_lifecycle({"retention_days": {"cache": 5}})
    r = _cleaner(tmp_path, db, lifecycle=changed).run(trigger="t", live=True, confirm=True, authorization=dry["run_id"])
    assert r["outcome"] == "refused" and "policy changed" in r["errors"][0] and p.exists()
    old = json.loads((tmp_path / "logs/junk_cleaner" / f"run_{dry['run_id']}.json").read_text(encoding="utf-8"))
    old["finished_at_utc"] = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "logs/junk_cleaner" / f"run_{dry['run_id']}.json").write_text(json.dumps(old), encoding="utf-8")
    r = _cleaner(tmp_path, db).run(trigger="t", live=True, confirm=True, authorization=dry["run_id"])
    assert r["outcome"] == "refused" and "older than one hour" in r["errors"][0] and p.exists()
    dry2 = _cleaner(tmp_path, db).run(trigger="test")
    r = _cleaner(tmp_path, db).run(trigger="t", live=True, confirm=True, authorization=dry2["run_id"])
    assert r["deleted"] == 1 and not p.exists()
    res = _cleaner(tmp_path, db).restore(r["run_id"])
    assert res["restored"] == 1 and p.exists()                                       # recovery preserved


def test_scheduled_job_stays_a_dry_run():
    import inspect
    from backend.app.scheduler import scheduler as sch
    src = inspect.getsource(sch.run_junk_cleaner_dry_run)
    assert "live=True" not in src and "confirm=True" not in src


# -- 8. configuration persistence ------------------------------------------------------------------------------------
def test_retention_configuration_persists_validates_and_is_audited(tmp_path):
    assert jc.load_lifecycle(tmp_path)["retention_days"] == jc.RETENTION_DEFAULTS
    jc.save_lifecycle({"retention_days": {"cache": 5, "downloaded_media": 60}, "protected_tests": ["inv_1"]}, tmp_path)
    again = jc.load_lifecycle(tmp_path)
    assert again["retention_days"]["cache"] == 5 and again["retention_days"]["downloaded_media"] == 60
    assert again["retention_days"]["final_mp4"] == 30 and again["protected_tests"] == ["inv_1"]
    jc.save_lifecycle({"retention_days": {"final_mp4": None}}, tmp_path)               # protect a category
    assert jc.load_lifecycle(tmp_path)["retention_days"]["final_mp4"] is None
    for bad in ({"retention_days": {"unknown": 1}}, {"retention_days": {"source_media": 5}},
                {"retention_days": {"cache": 0}}, {"retention_days": {"cache": "3"}}, {"retention_days": {"nope": 3}},
                {"protected_tests": ["../x"]}, {"other": 1}):
        with pytest.raises(ValueError):
            jc.save_lifecycle(bad, tmp_path)
    hist = (tmp_path / "logs/junk_cleaner/lifecycle_changes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(hist) == 2


# -- 9. storage accounting --------------------------------------------------------------------------------------------
def test_storage_breakdown_adds_up_and_forecasts(tmp_path):
    _file(tmp_path, "productions/j1/video/final.mp4", days_old=60, size=5000)
    _file(tmp_path, "productions/j1/lemo_film/s1.mp4", days_old=10, size=300)        # eligible (7 d)
    _file(tmp_path, "productions/j1/lemo_film/s2.mp4", days_old=5, size=200)         # waiting: eligible in 2 days
    _file(tmp_path, "asset_cache/c.bin", days_old=1, size=50)                        # waiting: eligible in 2 days
    _file(tmp_path, "source/logo.png", size=70)                                      # protected
    db = _published(tmp_path)
    rec = _cleaner(tmp_path, db).run(trigger="test")
    assert rec["scanned_bytes"] == 5620
    assert rec["protected_bytes"] + rec["waiting_bytes"] + rec["pending_remote_bytes"] + rec["eligible_bytes"] == 5620
    assert rec["eligible_bytes"] == 300 and rec["pending_remote_bytes"] == 5000 and rec["waiting_bytes"] == 250
    assert rec["protected_bytes"] == 70 and rec["temporary_bytes"] == 550
    day = (NOW + timedelta(days=2, hours=5, minutes=30)).date().isoformat()
    assert sum(rec["forecast"].values()) == 250 and rec["forecast"].get(day) == 250
    st = jc.status(root=tmp_path, now=datetime.now(timezone.utc))
    s = st["storage"]
    assert s["estimated_reclaimable_bytes"] == 5300 and s["eligible_bytes"] == 300 and s["occupied_bytes"] == 5620
    assert st["mode"] == "dry_run" and st["cumulative_bytes_freed"] == 0
    assert st["retention"]["days"] == jc.RETENTION_DEFAULTS


def test_retention_api_persists_and_keeps_csrf(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    monkeypatch.setattr(jc, "_project_root", lambda: tmp_path)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    h = {"Origin": "http://127.0.0.1:8000", main.AUTONOMY_CSRF_HEADER: main._AUTONOMY_CSRF_TOKEN}
    body = {"retention_days": {"cache": 4}, "protected_tests": ["inv_9"], "confirm": True}
    assert local.post("/api/junk-cleaner/retention", json=body).status_code == 403            # no CSRF token
    r = local.post("/api/junk-cleaner/retention", json=body, headers=h)
    assert r.status_code == 200 and r.json()["retention_days"]["cache"] == 4
    assert jc.load_lifecycle(tmp_path)["protected_tests"] == ["inv_9"]
    assert local.post("/api/junk-cleaner/retention", json={"retention_days": {"source_media": 1}, "confirm": True},
                      headers=h).status_code == 400
    live = local.post("/api/junk-cleaner/live", json={"authorization": "20260101T000000Z_abcdef", "confirm": True}, headers=h)
    assert live.status_code == 200 and live.json()["outcome"] == "refused"                    # unknown dry run
    html = (Path(main.__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
    for el in ("junkStorage", "junkForecast", "junkRetention", "junkProtectedTests"):
        assert f'id="{el}"' in html
