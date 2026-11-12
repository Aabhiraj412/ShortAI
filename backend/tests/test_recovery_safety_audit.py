"""Phase B audit: preserved artifacts stay protected; handoffs never duplicate queue entries."""
import json
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.db import Base, JobModel
from backend.app.orchestration import crazzydocs_master_orchestrator as orch_mod
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator
from backend.app.scheduler import retry_policy as rp
from backend.app.services import control_center_service as control
from backend.app.services import lifecycle_events
from backend.app.services.media_lifecycle_manager import AssetCategory, MediaLifecycleManager
from backend.app.services.youtube_publishing_engine import PublishingDatabase, compute_file_sha256


def _jobs_db(tmp_path, rows):
    path = tmp_path / "jobs.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        for r in rows:
            s.add(JobModel(**r))
        s.commit()
    engine.dispose()
    return str(path)


@pytest.mark.parametrize("status,preserved,expected_protected", [
    ("FAILED", True, True),            # post-render / handoff-exhausted failure
    ("HANDOFF_PENDING", False, True),  # queue handoff outstanding
    ("RETRY_SCHEDULED", False, True),
    ("FAILED", False, False),          # ordinary failed artifact stays quarantinable
])
def test_retention_never_quarantines_preserved_videos(tmp_path, status, preserved, expected_protected):
    ws = tmp_path / "ws"
    video = ws / "media" / "productions" / "p1" / "final.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"\0" * 4096)
    cp = {"video_path": str(video)}
    if preserved:
        cp["cycle_state"] = {"preserved_artifacts": {"video_path": str(video)}}
    db = _jobs_db(tmp_path, [dict(id="cycle_1_20261113_0000", date_str="2026-11-13", status=status,
                                  checkpoint_data_json=json.dumps(cp))])
    mgr = MediaLifecycleManager(workspace_root=str(ws), db_path=db, quarantine_dir=str(tmp_path / "q"))
    classified = mgr.classify_file(str(video), mgr.collect_dependencies(), {})
    assert classified.is_protected is expected_protected
    assert classified.is_quarantinable is (not expected_protected)
    if expected_protected:
        assert classified.category != AssetCategory.CATEGORY_F_FAILED_AND_INCOMPLETE_OUTPUTS


ALL_LEDGER_STATUSES = [
    "pending_queue", "approved", "uploading", "upload_interrupted", "awaiting_auth",
    "publication_scheduled", "uploaded_unverified", "uploaded_private", "uploaded_unlisted",
    "uploaded_public", "published", "failed", "quota_exceeded", "cancelled",
]


@pytest.mark.parametrize("existing_status", ALL_LEDGER_STATUSES)
def test_repeated_handoff_never_duplicates_queue_entry(tmp_path, monkeypatch, existing_status):
    engine = create_engine(f"sqlite:///{tmp_path / 'j.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(orch_mod, "SessionLocal", factory)
    monkeypatch.setattr(control, "SessionLocal", factory)
    monkeypatch.setattr(lifecycle_events, "record_lifecycle_event", lambda *a, **k: {})
    pdb = PublishingDatabase(db_path=str(tmp_path / "pub.db"))
    monkeypatch.setattr(master_orchestrator.publishing_engine, "db", pdb)

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x07" * 8192)
    handoff = {"cycle_id": "cycle_1", "topic_title": "Topic", "video_path": str(video), "thumbnail_path": None,
               "metadata_json": json.dumps({"title": "Topic", "description": "", "tags": [], "hashtags": []}),
               "video_hash": compute_file_sha256(str(video))}
    with factory() as s:
        s.add(JobModel(id="cycle_1_20261113_0000", date_str="2026-11-13", status="HANDOFF_PENDING",
                       current_stage="PUBLISHING_QUEUE_HANDOFF",
                       checkpoint_data_json=json.dumps({"video_path": str(video), "queue_handoff": handoff})))
        s.commit()

    first = master_orchestrator.complete_queue_handoff("cycle_1_20261113_0000")
    conn = sqlite3.connect(pdb.db_path)
    conn.execute("UPDATE publishing_ledger SET upload_status=?, youtube_video_id=? WHERE id=?",
                 (existing_status, "yt_existing", first))
    conn.commit()
    conn.close()
    with factory() as s:  # simulate the job being retried again after a crash
        j = s.query(JobModel).one()
        j.status = "HANDOFF_PENDING"
        cp = json.loads(j.checkpoint_data_json)
        cp.pop("queue_ledger_id", None)
        j.checkpoint_data_json = json.dumps(cp)
        s.commit()
    for _ in range(3):
        assert master_orchestrator.complete_queue_handoff("cycle_1_20261113_0000") == first
    rows = pdb.list_publications(limit=100)
    assert len(rows) == 1
    assert rows[0]["upload_status"] == existing_status
    assert rows[0]["youtube_video_id"] == "yt_existing"
    engine.dispose()


def test_permanent_error_count_is_bounded_even_if_status_is_tampered():
    """is_retry_due refuses once attempts reach max_retries, whatever the status says."""
    job = JobModel(id="j", date_str="d", status="RETRY_SCHEDULED", retry_count=3, max_retries=3,
                   checkpoint_data_json=json.dumps({"cycle_state": {"next_retry_at_utc": "2000-01-01T00:00:00Z"}}))
    assert rp.is_retry_due(job) is False
