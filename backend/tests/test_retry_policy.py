"""Bounded retries: counters, backoff, classification, terminal state, no post-render regeneration."""
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.db import Base, JobModel
from backend.app.scheduler import retry_policy as rp
from backend.app.scheduler import scheduler as sched_mod
from backend.app.scheduler.reconciliation_service import startup_reconciler
from backend.app.scheduler import reconciliation_service as recon_mod
from backend.app.services import control_center_service as control

IST = ZoneInfo("Asia/Kolkata")


def _job(**kw):
    defaults = dict(id="cycle_1_20261113_0000", date_str="2026-11-13", status="RUNNING",
                    retry_count=0, max_retries=3, checkpoint_data_json="{}", current_stage="VOICEOVER")
    defaults.update(kw)
    return JobModel(**defaults)


# --- pure policy ---------------------------------------------------------------

@pytest.mark.parametrize("exc", [NameError("name 'Path' is not defined"), ImportError("x"), TypeError("x"), AttributeError("x")])
def test_code_defects_are_permanent(exc):
    assert rp.classify_error(exc)[0] == rp.PERMANENT


@pytest.mark.parametrize("exc", [ConnectionError("reset"), TimeoutError("slow"), OSError("[WinError 32] file in use"), RuntimeError("HTTP 503")])
def test_infrastructure_errors_are_transient(exc):
    assert rp.classify_error(exc)[0] == rp.TRANSIENT


def test_backoff_is_exponential_and_capped():
    assert [rp.backoff_seconds(n) for n in range(1, 7)] == [120, 240, 480, 960, 1800, 1800]


def test_permanent_error_is_terminal_on_first_attempt():
    job = _job()
    d = rp.record_failure(job, NameError("name 'Path' is not defined"), now_utc=datetime(2026, 11, 13))
    assert job.status == "FAILED" and job.retry_count == 1
    assert d["terminal"] is True and d["classification"] == rp.PERMANENT
    assert job.error_details.startswith("[PERMANENT] NameError: name 'Path' is not defined")
    assert json.loads(job.checkpoint_data_json)["cycle_state"]["retry_terminal"] is True


def test_transient_errors_back_off_then_become_terminal():
    job = _job()
    t0 = datetime(2026, 11, 13, 0, 0)
    d1 = rp.record_failure(job, ConnectionError("reset"), now_utc=t0)
    assert (job.status, job.retry_count, d1["retry_in_seconds"]) == ("RETRY_SCHEDULED", 1, 120)
    assert not rp.is_retry_due(job, t0 + timedelta(seconds=119))
    assert rp.is_retry_due(job, t0 + timedelta(seconds=120))
    d2 = rp.record_failure(job, ConnectionError("reset"), now_utc=t0)
    assert (job.status, job.retry_count, d2["retry_in_seconds"]) == ("RETRY_SCHEDULED", 2, 240)
    d3 = rp.record_failure(job, ConnectionError("reset"), now_utc=t0)
    assert (job.status, job.retry_count, d3["terminal"]) == ("FAILED", 3, True)
    assert "retries exhausted (3/3)" in job.error_details
    assert not rp.is_retry_due(job, t0 + timedelta(days=1))


def test_failure_after_render_is_never_retried_and_video_preserved(tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"\0" * 4096)
    job = _job(current_stage="THUMBNAIL", checkpoint_data_json=json.dumps({"video_path": str(video)}))
    d = rp.record_failure(job, ConnectionError("thumbnail CDN down"))
    assert d["classification"] == rp.POST_RENDER_FAILURE and d["terminal"] is True
    assert job.status == "FAILED"
    cs = json.loads(job.checkpoint_data_json)["cycle_state"]
    assert cs["preserved_artifacts"] == {"video_path": str(video)}
    assert video.exists()


def test_success_clears_stale_error():
    job = _job()
    rp.record_failure(job, ConnectionError("x"))
    rp.clear_failure_state(job)
    assert job.error_details is None
    assert "error_classification" not in json.loads(job.checkpoint_data_json)["cycle_state"]


# --- scheduler integration -----------------------------------------------------

@pytest.fixture
def sched_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (sched_mod, recon_mod, control):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    monkeypatch.setattr(sched_mod, "get_operating_mode", lambda: "PRODUCTION")
    monkeypatch.setattr(sched_mod, "reload_profile_from_db_if_changed", lambda: False)
    monkeypatch.setattr(sched_mod, "production_capacity", lambda **_: {"available": True})
    monkeypatch.setattr(control, "evaluate_resource_safety", lambda *a, **k: {"safe": True})
    monkeypatch.setattr(sched_mod.network_connectivity_service, "is_environment_ready_for_cycle", lambda: (True, "ok", {}))
    monkeypatch.setattr(recon_mod.network_connectivity_service, "is_environment_ready_for_cycle", lambda: (True, "ok", {}))
    monkeypatch.setattr(sched_mod.email_monitoring_service, "send_cycle_summary_report", lambda **_: {"status": "SENT"})
    sched_mod.set_active_profile("FOUR_CYCLES_DAILY")
    yield factory
    engine.dispose()


def test_permanent_failure_runs_pipeline_exactly_once(sched_db):
    """Regression for cycle_4_20261112_1730: NameError retried ~45 times."""
    now = datetime(2026, 11, 13, 0, 5, tzinfo=IST)
    calls = []

    def boom(job_id=None, **_):
        calls.append(job_id)
        raise NameError("name 'Path' is not defined")

    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline", side_effect=boom):
        r1 = asyncio.run(sched_mod.run_cycle_generation("cycle_1", test_ist_now=now))
        assert r1["status"] == "FAILED" and r1["retry"]["classification"] == "PERMANENT"
        # Simulate many 45 s recovery-loop passes inside the recovery window.
        for minute in range(1, 30):
            asyncio.run(sched_mod.dispatch_eligible_recoveries(test_ist_now=now + timedelta(minutes=minute)))
            asyncio.run(sched_mod.run_cycle_generation("cycle_1", test_ist_now=now + timedelta(minutes=minute)))
    assert len(calls) == 1
    with sched_db() as db:
        job = db.query(JobModel).one()
        assert job.status == "FAILED" and job.retry_count == 1


def test_transient_failure_respects_backoff_and_max_retries(sched_db):
    now = datetime(2026, 11, 13, 0, 5, tzinfo=IST)
    calls = []

    def flaky(job_id=None, **_):
        calls.append(job_id)
        raise ConnectionError("upstream reset")

    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline", side_effect=flaky):
        asyncio.run(sched_mod.run_cycle_generation("cycle_1", test_ist_now=now))
        assert len(calls) == 1
        # Within backoff: deferred, no new attempt.
        r = asyncio.run(sched_mod.run_cycle_generation("cycle_1", test_ist_now=now))
        assert r["status"] == "DEFERRED" and r["reason"] == "RETRY_BACKOFF"
        asyncio.run(sched_mod.dispatch_eligible_recoveries(test_ist_now=now))
        assert len(calls) == 1
        # Make backoff elapse for each remaining attempt.
        for _ in range(5):
            with sched_db() as db:
                job = db.query(JobModel).one()
                cp = json.loads(job.checkpoint_data_json)
                cp.setdefault("cycle_state", {})["next_retry_at_utc"] = "2000-01-01T00:00:00Z"
                job.checkpoint_data_json = json.dumps(cp)
                db.commit()
            asyncio.run(sched_mod.dispatch_eligible_recoveries(test_ist_now=now))
    assert len(calls) == 3
    with sched_db() as db:
        job = db.query(JobModel).one()
        assert job.status == "FAILED" and job.retry_count == 3


def test_reconciler_does_not_resurrect_failed_job(sched_db):
    now = datetime(2026, 11, 13, 0, 30, tzinfo=IST)
    with sched_db() as db:
        db.add(_job(id="cycle_1_20261113_0000", status="FAILED", retry_count=1))
        db.commit()
    startup_reconciler.reconcile_on_startup(test_now_ist=now)
    with sched_db() as db:
        job = db.query(JobModel).one()
        assert job.status == "FAILED" and job.retry_count == 1
