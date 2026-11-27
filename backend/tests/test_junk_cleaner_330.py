"""Stage 3.30: Junk Cleaner safety rules, dry-run, auditability and concurrency."""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.services import junk_cleaner as jc

NOW = datetime(2026, 12, 30, 12, 0, tzinfo=timezone.utc)
POLICY = {"policy_version": 1, "default_mode": "dry_run", "automatic_deletion": False,
          "retention_days": {"regeneratable_intermediates": 30, "temporary_caches": 7}}


def _file(root: Path, rel: str, days_old: int = 60, size: int = 1000) -> Path:
    p = root / "media" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    t = (NOW - timedelta(days=days_old)).timestamp()
    os.utime(p, (t, t))
    return p


def _db(root: Path, jobs, pubs=(), thumbs=()):
    path = root / "studio.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE persistent_jobs (id TEXT, status TEXT, updated_at TEXT)")
    c.execute("CREATE TABLE publishing_ledger (id INTEGER, video_path TEXT, thumbnail_path TEXT, "
              "youtube_video_id TEXT, upload_status TEXT)")
    c.execute("CREATE TABLE thumbnail_state (ledger_id INTEGER, verify_status TEXT)")
    c.executemany("INSERT INTO persistent_jobs VALUES (?,?,?)", jobs)
    c.executemany("INSERT INTO publishing_ledger VALUES (?,?,?,?,?)", pubs)
    c.executemany("INSERT INTO thumbnail_state VALUES (?,?)", thumbs)
    c.commit()
    c.close()
    return str(path)


def _cleaner(root, db, policy=POLICY):
    return jc.JunkCleaner(root=root, db_path=db, now=NOW, policy=dict(policy))


@pytest.fixture
def tree(tmp_path):
    old = (NOW - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    _file(tmp_path, "productions/done/video/showcase_done.mp4")
    _file(tmp_path, "productions/done/lemo_film/scene_01.mp4")
    _file(tmp_path, "productions/done/lemo_film/index.html")
    _file(tmp_path, "productions/done/narration/seg_01.mp3")
    _file(tmp_path, "productions/done/audio/final_mix.mp3")
    _file(tmp_path, "productions/done/thumbnails/thumb_done.png")
    _file(tmp_path, "productions/done/thumbnails/variants/v1.png")
    _file(tmp_path, "productions/done/assets/photo.jpg")
    _file(tmp_path, "productions/done/manifests/render_manifest_done.json")
    _file(tmp_path, "productions/done/production_manifest.json")
    _file(tmp_path, "productions/live/lemo_film/scene_01.mp4")
    _file(tmp_path, "productions/failed_recent/lemo_film/scene_01.mp4")
    _file(tmp_path, "productions/young/video/showcase_young.mp4", days_old=3)
    _file(tmp_path, "productions/young/lemo_film/scene_01.mp4", days_old=3)
    _file(tmp_path, "productions/pending/video/showcase_pending.mp4")
    _file(tmp_path, "productions/pending/lemo_film/scene_01.mp4")
    _file(tmp_path, "source/branding/logo.png")
    _file(tmp_path, "test_renders/val/a.mp4")
    _file(tmp_path, "mystery/thing.bin")
    db = _db(tmp_path, [
        ("done", "COMPLETED", old), ("live", "RUNNING", old), ("young", "COMPLETED", old),
        ("failed_recent", "FAILED", (NOW - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")),
        ("pending", "COMPLETED", old)],
        pubs=[(1, str(tmp_path / "media/productions/done/video/showcase_done.mp4"),
               str(tmp_path / "media/productions/done/thumbnails/thumb_done.png"), "vidDone", "publication_scheduled"),
              (2, str(tmp_path / "media/productions/pending/video/showcase_pending.mp4"), "", None, "pending_queue")],
        thumbs=[(1, "pending")])
    return tmp_path, db


def _live(root, db):
    """Storage lifecycle: every live run needs confirmation AND a successful dry run from the last hour."""
    dry = _cleaner(root, db).run(trigger="test")
    return _cleaner(root, db).run(trigger="test", live=True, confirm=True, authorization=dry["run_id"])


def _eligible(rec):
    return {f["path"].replace("\\", "/").split("media/")[1] for f in rec["eligible_files"]}


def test_dry_run_reports_without_touching_files(tree):
    root, db = tree
    before = sorted(p.as_posix() for p in (root / "media").rglob("*") if p.is_file())
    rec = _cleaner(root, db).run(trigger="test", live=False)
    after = sorted(p.as_posix() for p in (root / "media").rglob("*") if p.is_file())
    assert before == after and rec["deleted"] == 0 and rec["bytes_reclaimed"] == 0
    assert rec["outcome"] == "completed" and rec["scanned"] == len(before)


def test_eligibility_follows_safety_rules(tree):
    root, db = tree
    el = _eligible(_cleaner(root, db).run(trigger="test"))
    assert el == {"productions/done/lemo_film/scene_01.mp4", "productions/done/lemo_film/index.html",
                  "productions/done/narration/seg_01.mp3"}
    # never: final MP4 / thumbnail / audio, provenance media, manifests, active / recoverable / pending jobs,
    # young files, thumbnail variants without a verified upload, source media, test renders, unknown files


def test_skip_reasons_are_specific(tree):
    root, db = tree
    rec = _cleaner(root, db).run(trigger="test")
    r = rec["skip_reasons"]
    assert r["active job (RUNNING)"] == 1
    assert r["failed job may still be recoverable"] == 1
    assert r["publication not confirmed public (publication_scheduled): protected"] == 3   # mp4, thumbnail, audio
    assert r["final deliverable without a publication record (unpublished)"] == 1
    assert r["final deliverable not uploaded (no YouTube video id)"] == 1
    assert r["thumbnail upload not remotely verified"] == 1
    assert r["publishing still pending"] == 1
    assert r["permanent (user media / branding)"] == 1
    assert r["never deleted automatically"] == 1


def test_variants_eligible_only_after_verified_thumbnail(tree):
    root, db = tree
    c = sqlite3.connect(db)
    c.execute("UPDATE thumbnail_state SET verify_status='verified'")
    c.commit()
    c.close()
    assert "productions/done/thumbnails/variants/v1.png" in _eligible(_cleaner(root, db).run(trigger="test"))


def test_live_refused_without_policy_or_confirmation(tree):
    root, db = tree
    rec = _cleaner(root, db).run(trigger="test", live=True)
    assert rec["outcome"] == "refused" and rec["deleted"] == 0
    assert (root / "media/productions/done/lemo_film/scene_01.mp4").exists()


def _finish_live_job(db):
    c = sqlite3.connect(db)
    c.execute("UPDATE persistent_jobs SET status='COMPLETED' WHERE id='live'")
    c.commit()
    c.close()


def test_live_refused_while_any_production_job_is_active(tree):
    root, db = tree                                       # fixture has job "live" RUNNING
    rec = _live(root, db)
    assert rec["outcome"] == "refused" and rec["deleted"] == 0 and "active" in rec["errors"][0]
    assert (root / "media/productions/done/lemo_film/scene_01.mp4").exists()
    assert _cleaner(root, db).run(trigger="test")["outcome"] == "completed"     # dry run still allowed


def test_confirmed_live_run_quarantines_only_eligible_and_is_idempotent(tree):
    root, db = tree
    _finish_live_job(db)
    rec = _live(root, db)
    assert rec["deleted"] == 3 and rec["bytes_reclaimed"] == 3000
    assert not (root / "media/productions/done/lemo_film/scene_01.mp4").exists()
    q = list((root / "media/quarantine/junk_cleaner").rglob("scene_01.mp4"))
    assert len(q) == 1                                                   # moved, reversible
    assert (root / "media/productions/done/video/showcase_done.mp4").exists()
    assert (root / "media/productions/live/lemo_film/scene_01.mp4").exists()
    again = _live(root, db)
    assert again["deleted"] == 0                                         # nothing left to remove
    assert rec["categories"]["intermediate_clips"]["bytes_reclaimed"] == 1000


def test_concurrent_run_is_skipped(tree):
    root, db = tree
    from backend.app.core.dashboard_startup import ServerLock
    holder = ServerLock(root / "logs" / "junk_cleaner.lock")
    dry = _cleaner(root, db).run(trigger="test")
    assert holder.acquire()
    try:
        rec = _cleaner(root, db).run(trigger="test", live=True, confirm=True, authorization=dry["run_id"])
    finally:
        holder.release()
    assert rec["outcome"] == "skipped_concurrent_run" and rec["deleted"] == 0


def test_runs_are_persisted_and_health_needs_a_recent_success(tree):
    root, db = tree
    assert jc.status(root=root, now=NOW)["health"] == "NO_RUNS_RECORDED"
    _cleaner(root, db).run(trigger="test")
    lines = (root / "logs/junk_cleaner/runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["mode"] == "dry_run"
    st = jc.status(root=root, now=datetime.now(timezone.utc))
    assert st["health"] == "HEALTHY" and st["cumulative_bytes_reclaimed"] == 0
    assert "no live run" in st["cumulative_note"]
    later = datetime.now(timezone.utc) + timedelta(days=3)
    assert jc.status(root=root, now=later)["health"] == "STALE"


def test_database_unavailable_evaluates_nothing(tmp_path):
    _file(tmp_path, "productions/done/lemo_film/scene_01.mp4")
    rec = _cleaner(tmp_path, str(tmp_path / "missing" / "nope.db")).run(trigger="test")
    assert rec["outcome"] == "failed" and rec["deleted"] == 0
    live = _cleaner(tmp_path, str(tmp_path / "missing" / "nope.db")).run(trigger="test", live=True, confirm=True,
                                                                        authorization=rec["run_id"])
    assert live["outcome"] == "refused"                       # a failed dry run never authorizes a live run
    assert (tmp_path / "media/productions/done/lemo_film/scene_01.mp4").exists()


def test_quarantine_manifest_and_restore_round_trip(tree):
    root, db = tree
    _finish_live_job(db)
    rec = _live(root, db)
    assert rec["deleted"] == 3
    qdir = root / "media/quarantine/junk_cleaner" / rec["run_id"]
    lines = [json.loads(x) for x in (qdir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3 and all(e["bytes"] == 1000 for e in lines)
    # a file recreated at its original path is never overwritten
    _file(root, "productions/done/lemo_film/scene_01.mp4", size=7)
    res = _cleaner(root, db).restore(rec["run_id"])
    assert res["restored"] == 2 and res["conflicts"] == ["productions/done/lemo_film/scene_01.mp4"]
    assert (root / "media/productions/done/lemo_film/scene_01.mp4").stat().st_size == 7
    assert (root / "media/productions/done/lemo_film/index.html").exists()
    assert (root / "logs/junk_cleaner/restores.jsonl").exists()
    with pytest.raises(ValueError):
        _cleaner(root, db).restore("../escape")


def test_blocked_qc_follows_failed_rule_and_uploaded_private_counts_as_uploaded(tmp_path):
    old = (NOW - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (NOW - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    _file(tmp_path, "productions/qc_old/lemo_film/scene_01.mp4")
    _file(tmp_path, "productions/qc_new/lemo_film/scene_01.mp4")
    _file(tmp_path, "productions/priv/video/showcase_priv.mp4")
    _file(tmp_path, "productions/priv/lemo_film/scene_01.mp4")
    db = _db(tmp_path, [("qc_old", "BLOCKED_QC", old), ("qc_new", "BLOCKED_QC", recent), ("priv", "COMPLETED", old)],
             pubs=[(1, str(tmp_path / "media/productions/priv/video/showcase_priv.mp4"), "", "vid", "uploaded_private")])
    rec = _cleaner(tmp_path, db).run(trigger="test")
    elig = _eligible(rec)
    assert "productions/qc_old/lemo_film/scene_01.mp4" in elig
    assert "productions/qc_new/lemo_film/scene_01.mp4" not in elig          # may still be recoverable
    assert "productions/priv/lemo_film/scene_01.mp4" in elig
    assert "productions/priv/video/showcase_priv.mp4" not in elig           # deliverable: permanent
