"""Production completes at the Publishing Queue handoff; the handoff is idempotent and retried alone."""
import asyncio
import json
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.db import Base, JobModel
from backend.app.orchestration import crazzydocs_master_orchestrator as orch_mod
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator
from backend.app.scheduler import retry_policy as rp
from backend.app.scheduler import scheduler as sched_mod
from backend.app.services import control_center_service as control
from backend.app.services import lifecycle_events
from backend.app.services.youtube_publishing_engine import PublishingDatabase, compute_file_sha256

JOB = "cycle_1_20261113_0000"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (orch_mod, sched_mod, control):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    pdb = PublishingDatabase(db_path=str(tmp_path / "pub.db"))
    monkeypatch.setattr(master_orchestrator.publishing_engine, "db", pdb)
    events = []
    monkeypatch.setattr(lifecycle_events, "record_lifecycle_event",
                        lambda et, st, **kw: events.append(et) or {"event_type": et})

    video = tmp_path / "final.mp4"
    video.write_bytes(b"\x00" * 8192)
    handoff = {
        "cycle_id": "cycle_1",
        "topic_title": "Test Topic",
        "video_path": str(video),
        "thumbnail_path": None,
        "metadata_json": json.dumps({"title": "Test Topic", "description": "d", "tags": [], "hashtags": []}),
        "video_hash": compute_file_sha256(str(video)),
    }
    with factory() as db:
        db.add(JobModel(
            id=JOB, date_str="2026-11-13", status="RUNNING", current_stage="PUBLISHING_QUEUE_HANDOFF",
            retry_count=0, max_retries=3,
            checkpoint_data_json=json.dumps({"video_path": str(video), "queue_handoff": handoff}),
        ))
        db.commit()
    yield {"factory": factory, "pdb": pdb, "events": events, "video": video}
    engine.dispose()


def _job(env):
    with env["factory"]() as db:
        return db.query(JobModel).filter(JobModel.id == JOB).one()


def test_handoff_completes_job_in_required_order(env):
    ledger_id = master_orchestrator.complete_queue_handoff(JOB)
    job = _job(env)
    assert job.status == "COMPLETED" and job.current_stage == "COMPLETED" and job.completed_at
    assert json.loads(job.checkpoint_data_json)["queue_ledger_id"] == ledger_id
    row = env["pdb"].get_publication(ledger_id)
    assert row["upload_status"] == "pending_queue"
    assert row["visibility"] == "private"
    assert row["scheduled_timestamp"] is None
    assert row["youtube_video_id"] is None
    assert env["events"] == ["PUBLISHING_QUEUE_ENTRY_CREATED", "PRODUCTION_CYCLE_COMPLETED"]


def test_handoff_is_idempotent(env):
    first = master_orchestrator.complete_queue_handoff(JOB)
    with env["factory"]() as db:  # simulate crash after insert, before COMPLETED
        job = db.query(JobModel).filter(JobModel.id == JOB).one()
        job.status = "HANDOFF_PENDING"
        db.commit()
    env["pdb"].set_upload_status(first, "approved")  # user acted meanwhile
    second = master_orchestrator.complete_queue_handoff(JOB)
    assert second == first
    assert len(env["pdb"].list_publications(limit=50)) == 1


def test_handoff_failure_is_retried_without_regeneration(env, monkeypatch):
    job = _job(env)
    with env["factory"]() as db:
        job = db.query(JobModel).filter(JobModel.id == JOB).one()
        d = rp.record_failure(job, OSError("database is locked"))
        db.commit()
    assert d["classification"] == rp.QUEUE_HANDOFF_FAILURE and d["status"] == "HANDOFF_PENDING"

    # Backoff elapsed -> the recovery loop retries the handoff only.
    with env["factory"]() as db:
        job = db.query(JobModel).filter(JobModel.id == JOB).one()
        cp = json.loads(job.checkpoint_data_json)
        cp["cycle_state"]["next_retry_at_utc"] = "2000-01-01T00:00:00Z"
        job.checkpoint_data_json = json.dumps(cp)
        db.commit()
    monkeypatch.setattr(sched_mod, "get_operating_mode", lambda: "PRODUCTION")
    monkeypatch.setattr(sched_mod, "reload_profile_from_db_if_changed", lambda: False)
    from backend.app.scheduler.reconciliation_service import startup_reconciler
    monkeypatch.setattr(startup_reconciler, "reconcile_on_startup", lambda **_: {})
    with patch.object(master_orchestrator, "execute_production_pipeline") as pipeline:
        res = asyncio.run(sched_mod.dispatch_eligible_recoveries())
        pipeline.assert_not_called()
    assert res["queue_handoffs"][0]["status"] == "COMPLETED"
    assert _job(env).status == "COMPLETED"
    assert env["video"].exists()


def test_cycle_does_not_wait_for_user_or_youtube(env, monkeypatch):
    """Completion needs no publication time, upload, or YouTube call."""
    called = []
    monkeypatch.setattr(master_orchestrator.publishing_engine, "upload_short", lambda *a, **k: called.append("upload"))
    master_orchestrator.complete_queue_handoff(JOB)
    assert called == []
    assert _job(env).status == "COMPLETED"


def test_changed_video_is_not_queued(env):
    env["video"].write_bytes(b"\x01" * 8192)
    with pytest.raises(ValueError, match="changed after QC"):
        master_orchestrator.complete_queue_handoff(JOB)
    assert env["pdb"].list_publications(limit=50) == []
