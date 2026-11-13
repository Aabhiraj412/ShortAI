"""
Operator retry for a FAILED cycle, plus the characterisation tests from the
Cycle 3 retry investigation (2026-11-13).

They describe what the deployed code (c1fb65c) actually does with a cycle that is
FAILED with retries exhausted inside an open recovery window. Everything runs on a
temporary SQLite database with the pipeline and YouTube mocked; nothing touches the
live database, scheduler or YouTube.
"""
import asyncio
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.db import Base, JobModel
from backend.app.rendering.render_errors import RenderFailure
from backend.app.scheduler import reconciliation_service as recon_mod
from backend.app.scheduler import retry_policy as rp
from backend.app.scheduler import scheduler as sched_mod
from backend.app.scheduler import worker_registry as wr
from backend.app.services import control_center_service as control
from backend.app.services import operator_retry_service as ors

from test_schedule_and_auto_publish import _queue, pub, settings_db  # noqa: F401  (fixtures)

IST = ZoneInfo("Asia/Kolkata")
JOB3 = "cycle_3_20261113_1200"
JOB4 = "cycle_4_20261113_1800"
T = lambda h, m, s=0: datetime(2026, 11, 13, h, m, s, tzinfo=IST)  # noqa: E731

# Mirrors the live row (read-only inspection): FAILED, 3/3, TRANSIENT readiness error,
# topic checkpoint present, no target_publication_at_utc (claimed before the deploy).
LIVE_CP = {
    "topic_title": "Science fiction",
    "hook_text": "Science fiction (often shortened to sci-fi) ...",
    "category": "Science",
    "cycle_state": {
        "cycle_id": "cycle_3",
        "production_start_at": "2026-11-13T12:00:00+05:30",
        "recovery_expiration_at": "2026-11-13T16:00:00+05:30",
        "target_publication_at": "2026-11-13T16:00:00+05:30",
        "final_cycle_status": "FAILED",
        "error_classification": "TRANSIENT",
        "retry_terminal": True,
    },
}


@pytest.fixture
def sched_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'sched.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (sched_mod, recon_mod, control, ors):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    monkeypatch.setattr(sched_mod, "get_operating_mode", lambda: "PRODUCTION")
    monkeypatch.setattr(sched_mod, "reload_profile_from_db_if_changed", lambda: False)
    monkeypatch.setattr(control, "evaluate_resource_safety", lambda *a, **k: {"safe": True})
    monkeypatch.setattr(control, "get_max_concurrency", lambda: 1)   # live value
    monkeypatch.setattr(sched_mod.network_connectivity_service, "is_environment_ready_for_cycle", lambda: (True, "ok", {}))
    monkeypatch.setattr(recon_mod.network_connectivity_service, "is_environment_ready_for_cycle", lambda: (True, "ok", {}))
    monkeypatch.setattr(sched_mod.email_monitoring_service, "send_cycle_summary_report", lambda **_: {"status": "SENT"})
    sched_mod.set_active_profile("FOUR_CYCLES_DAILY")
    yield factory
    engine.dispose()


def _seed_failed(factory, **over):
    row = dict(id=JOB3, date_str="2026-11-13", status="FAILED", retry_count=3, max_retries=3,
               current_stage="VOICEOVER", progress_pct=55.0, topic_title="Science fiction",
               error_details="[TRANSIENT] RuntimeError: ... window.READY never became true ... (retries exhausted (3/3))",
               checkpoint_data_json=json.dumps(LIVE_CP), recovery_policy="STRICT_WINDOW")
    row.update(over)
    with factory() as s:
        s.add(JobModel(**row))
        s.commit()


def _requeue(factory, *, bump_max=True):
    """The hypothetical manual requeue: what a controlled operator retry would have to write."""
    with factory() as s:
        j = s.get(JobModel, JOB3)
        j.status = rp.STATUS_RETRY_SCHEDULED
        if bump_max:
            j.max_retries = j.retry_count + 1
        cp = json.loads(j.checkpoint_data_json)
        cp["cycle_state"]["next_retry_at_utc"] = "2026-11-13T00:00:00Z"
        j.checkpoint_data_json = json.dumps(cp)
        s.commit()


def _row(factory, job_id=JOB3):
    with factory() as s:
        return s.get(JobModel, job_id)


def _dispatch(now):
    return asyncio.run(sched_mod.dispatch_eligible_recoveries(test_ist_now=now))


# 1. Failed Cycle 3, recovery window open --------------------------------------
def test_1_failed_cycle_is_terminal_even_with_open_window(sched_db):
    _seed_failed(sched_db)
    before = _row(sched_db)
    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline") as pipe:
        r = asyncio.run(sched_mod.run_cycle_generation("cycle_3", test_ist_now=T(14, 40)))
        d = _dispatch(T(14, 41))
    assert (r["status"], r["reason"]) == ("SKIPPED", "TERMINAL_FAILURE")
    assert d["dispatched"] == []
    pipe.assert_not_called()
    after = _row(sched_db)
    assert (after.status, after.retry_count, after.checkpoint_data_json) == (
        "FAILED", 3, before.checkpoint_data_json)


# 2. Failed Cycle 3, recovery window expired ------------------------------------
def test_2_failed_cycle_after_window_stays_failed_and_is_not_run(sched_db):
    _seed_failed(sched_db)
    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline") as pipe:
        r = asyncio.run(sched_mod.run_cycle_generation("cycle_3", test_ist_now=T(16, 5)))
        _dispatch(T(16, 6))
    assert r["reason"] == "TERMINAL_FAILURE"
    pipe.assert_not_called()
    assert _row(sched_db).status == "FAILED"   # FAILED is checked before the window


# 3. Retry with preserved artifacts (hypothetical requeue) -----------------------
def test_3a_setting_retry_scheduled_alone_does_not_dispatch_when_attempts_exhausted(sched_db):
    _seed_failed(sched_db)
    _requeue(sched_db, bump_max=False)
    assert rp.is_retry_due(_row(sched_db)) is False
    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline") as pipe:
        _dispatch(T(14, 41))
    pipe.assert_not_called()


def test_3b_requeue_with_one_extra_attempt_dispatches_once_with_18_00_target_and_saved_topic(sched_db):
    _seed_failed(sched_db)
    _requeue(sched_db)
    seen = {}

    def pipeline(job_id=None, **_):
        j = _row(sched_db, job_id)
        cp = json.loads(j.checkpoint_data_json)
        seen.update(job_id=job_id, status=j.status, topic=cp.get("topic_title"),
                    target=cp["cycle_state"].get("target_publication_at_utc"))
        return {"status": "COMPLETED", "job_id": job_id}

    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline", side_effect=pipeline):
        d = _dispatch(T(14, 41))
    assert [x["job_id"] for x in d["dispatched"]] == [JOB3]
    assert seen == {"job_id": JOB3, "status": "RUNNING", "topic": "Science fiction",
                    "target": "2026-11-13T12:30:00Z"}   # 18:00 IST


# 4. Publication target no longer valid ----------------------------------------
@pytest.mark.parametrize("now_ist, expected", [(T(17, 40), "SCHEDULED"), (T(17, 50), "NEEDS_PUBLICATION_TIME"),
                                               (T(18, 5), "NEEDS_PUBLICATION_TIME")])
def test_4_publish_after_qc_respects_the_18_00_target_and_15_minute_lead(pub, now_ist, expected):
    lid = _queue(pub)
    res = pub["svc"].publish_after_qc(lid, "2026-11-13T12:30:00Z", now_utc=now_ist.astimezone(timezone.utc))
    assert res["state"] == expected
    inserts = pub["yt"].videos.return_value.insert.call_count
    assert inserts == (1 if expected == "SCHEDULED" else 0)
    if expected == "SCHEDULED":
        body = pub["yt"].videos.return_value.insert.call_args.kwargs["body"]
        assert body["status"] == {**body["status"], "privacyStatus": "private", "publishAt": "2026-11-13T12:30:00Z"}


# 5. Close to the recovery deadline ---------------------------------------------
def test_5_window_is_checked_at_dispatch_only(sched_db):
    _seed_failed(sched_db)
    _requeue(sched_db)
    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline",
                      return_value={"status": "COMPLETED"}) as pipe:
        _dispatch(T(15, 59, 30))
    assert pipe.call_count == 1   # 30 s before the deadline still dispatches

    with sched_db() as s:   # same request, one minute too late
        s.query(JobModel).delete()
        s.commit()
    _seed_failed(sched_db)
    _requeue(sched_db)
    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline") as pipe:
        d = _dispatch(T(16, 0, 0))
    pipe.assert_not_called()
    assert _row(sched_db).status == "RECOVERY_WINDOW_EXPIRED" and d["expired"]


# 6. Cycle 4 approaching while the retry still runs ----------------------------
def test_6_cycle_4_is_deferred_not_lost_while_cycle_3_holds_the_only_slot(sched_db):
    worker = f"worker-{os.getpid()}-t{threading.get_ident()}"
    cp = json.loads(json.dumps(LIVE_CP))
    cp["cycle_state"]["worker_id"] = worker
    _seed_failed(sched_db, status="RUNNING", retry_count=3, max_retries=4, heartbeat_at=datetime.utcnow(),
                 checkpoint_data_json=json.dumps(cp))
    assert wr.registry.register(JOB3, worker)
    try:
        with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline",
                          return_value={"status": "COMPLETED"}) as pipe:
            r = asyncio.run(sched_mod.run_cycle_generation("cycle_4", test_ist_now=T(18, 0)))
            assert r["status"] == "DEFERRED" and "concurrency" in r["reason"].lower()
            _dispatch(T(18, 1))
            assert [c.kwargs.get("job_id") for c in pipe.call_args_list] == []
    finally:
        wr.registry.unregister(JOB3, worker)
    with sched_db() as s:
        s.get(JobModel, JOB3).status = "COMPLETED"
        s.commit()
    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline",
                      return_value={"status": "COMPLETED"}) as pipe:
        _dispatch(T(18, 2))
    assert [c.kwargs.get("job_id") for c in pipe.call_args_list] == [JOB4]


# 7. Duplicate retry requests --------------------------------------------------
def test_7_duplicate_requests_never_start_a_second_worker(sched_db):
    _seed_failed(sched_db)
    _requeue(sched_db)
    started, release, calls = threading.Event(), threading.Event(), []

    def slow(job_id=None, **_):
        calls.append(job_id)
        started.set()
        release.wait(10)
        return {"status": "COMPLETED", "job_id": job_id}

    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline", side_effect=slow):
        th = threading.Thread(target=lambda: _dispatch(T(14, 41)))
        th.start()
        assert started.wait(5)
        r = asyncio.run(sched_mod.run_cycle_generation("cycle_3", test_ist_now=T(14, 42)))
        _dispatch(T(14, 43))
        release.set()
        th.join(10)
    assert r["reason"] == "CONCURRENT_ACTIVE_RUN"
    assert calls == [JOB3]


# 8. Retry fails again ----------------------------------------------------------
def test_8_a_failed_extra_attempt_is_terminal_and_not_retried_again(sched_db):
    _seed_failed(sched_db)
    _requeue(sched_db)
    boom = RenderFailure("window.READY never became true", attempts=2, readiness=True, deterministic=True,
                         preserved={"lemo_film_dir": "x"})
    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline", side_effect=boom) as pipe:
        _dispatch(T(14, 41))
        _dispatch(T(14, 50))
        asyncio.run(sched_mod.run_cycle_generation("cycle_3", test_ist_now=T(15, 0)))
    assert pipe.call_count == 1
    row = _row(sched_db)
    assert row.status == "FAILED" and row.retry_count == 4


# =============================================================================
# The guarded operator retry (operator_retry_service + endpoints)
# =============================================================================
def test_operator_retry_grants_one_attempt_and_scheduler_runs_it_once(sched_db):
    _seed_failed(sched_db)
    ev = ors.evaluate(JOB3, T(14, 45))
    assert ev["eligible"], ev["reasons"]
    assert ev["recovery_ends_ist"] == "2026-11-13 16:00 IST" and ev["target_publication_ist"] == "2026-11-13 18:00 IST"
    res = ors.request_retry(JOB3, True, T(14, 45))
    assert res["status"] == "RETRY_SCHEDULED" and res["attempt"] == 4
    row = _row(sched_db)
    cs = json.loads(row.checkpoint_data_json)["cycle_state"]
    assert (row.status, row.retry_count, row.max_retries, row.recovery_reason) == ("RETRY_SCHEDULED", 3, 4, "OPERATOR_RETRY")
    assert cs["operator_retries"] == 1 and json.loads(row.checkpoint_data_json)["topic_title"] == "Science fiction"
    assert rp.is_retry_due(row)

    def pipeline(job_id=None, **_):   # like the real pipeline: always leaves a final status
        with sched_db() as s:
            s.get(JobModel, job_id).status = "COMPLETED"
            s.commit()
        return {"status": "COMPLETED", "job_id": job_id}

    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline", side_effect=pipeline) as pipe:
        _dispatch(T(14, 46))
        _dispatch(T(14, 47))
    assert [c.kwargs.get("job_id") for c in pipe.call_args_list] == [JOB3]
    assert any(h.get("kind") == "operator_retry" for h in control._history())


def test_operator_retry_requires_confirmation(sched_db):
    _seed_failed(sched_db)
    with pytest.raises(PermissionError):
        ors.request_retry(JOB3, False, T(14, 45))
    assert _row(sched_db).status == "FAILED"


def test_second_request_and_second_failure_are_refused(sched_db):
    _seed_failed(sched_db)
    ors.request_retry(JOB3, True, T(14, 45))
    with pytest.raises(ors.RetryRefused) as e:
        ors.request_retry(JOB3, True, T(14, 45))
    assert "only FAILED" in str(e.value)
    boom = RenderFailure("window.READY never became true", attempts=2, readiness=True, deterministic=True, preserved={})
    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline", side_effect=boom):
        _dispatch(T(14, 46))
    assert _row(sched_db).status == "FAILED"
    with pytest.raises(ors.RetryRefused) as e:
        ors.request_retry(JOB3, True, T(14, 55))
    assert "already had its one operator retry" in str(e.value)


@pytest.mark.parametrize("now, fragment", [
    (T(15, 45), "of the recovery window remains"),
    (T(16, 5), "of the recovery window remains"),
    (T(17, 30), "less than 45 min away"),           # target 18:00 and next cycle 18:00
])
def test_retry_refused_near_deadline_target_or_next_cycle(sched_db, now, fragment):
    _seed_failed(sched_db)
    with pytest.raises(ors.RetryRefused) as e:
        ors.request_retry(JOB3, True, now)
    assert fragment in str(e.value)
    assert _row(sched_db).status == "FAILED"


@pytest.mark.parametrize("over, fragment", [
    ({"cs": {"error_classification": "PERMANENT"}}, "not retryable"),
    ({"cs": {"error_classification": "POST_RENDER_FAILURE"}}, "not retryable"),
    ({"cp": {"queue_handoff": {"video_path": "v.mp4"}}}, "already produced"),
    ({"youtube_video_id": "abc"}, "already produced"),
    ({"status": "RECOVERY_WINDOW_EXPIRED"}, "only FAILED"),
])
def test_retry_refused_for_unsafe_failures(sched_db, over, fragment):
    cp = json.loads(json.dumps(LIVE_CP))
    cp["cycle_state"].update(over.pop("cs", {}))
    cp.update(over.pop("cp", {}))
    _seed_failed(sched_db, checkpoint_data_json=json.dumps(cp), **over)
    with pytest.raises(ors.RetryRefused) as e:
        ors.request_retry(JOB3, True, T(14, 45))
    assert fragment in str(e.value)


def test_retry_refused_while_another_production_is_active(sched_db):
    _seed_failed(sched_db)
    with sched_db() as s:
        s.add(JobModel(id="cycle_2_20261113_0600", date_str="2026-11-13", status="RUNNING", retry_count=0,
                       max_retries=3, checkpoint_data_json="{}"))
        s.commit()
    with pytest.raises(ors.RetryRefused) as e:
        ors.request_retry(JOB3, True, T(14, 45))
    assert "Another production is active" in str(e.value)


def test_retry_endpoints_are_local_and_confirmed(sched_db):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    assert TestClient(app).post(f"/api/production/jobs/{JOB3}/retry", json={"confirm": True}).status_code == 403
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    assert client.post("/api/production/jobs/nope/retry", json={}).status_code == 403
    r = client.post("/api/production/jobs/nope/retry", json={"confirm": True})
    assert r.status_code == 409 and "Job not found" in r.json()["detail"]
    g = client.get("/api/production/jobs/nope/retry")
    assert g.status_code == 200 and g.json()["eligible"] is False
    assert "Retry once" in client.get("/dashboard").text
