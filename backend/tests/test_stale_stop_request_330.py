"""Stage 3.30: a graceful-stop request only stops the production that was running when it was requested.

Evidence: a stop requested on 2026-11-18 11:58 IST was never consumed (no job ran) and halted
cycle_1_20261120_0000 at its first stage boundary two days later.
"""
from datetime import datetime, timedelta

from backend.app.database.db import JobModel
from backend.app.services import control_center_service as control

from test_operator_retry import sched_db  # noqa: F401  (fixture: temp DB for jobs and settings)


def _job(factory, job_id, started_utc):
    with factory() as s:
        s.add(JobModel(id=job_id, date_str="2026-11-20", status="RUNNING", current_stage="TOPIC_DISCOVERY",
                       retry_count=0, max_retries=3, created_at=started_utc, actual_start_at=started_utc))
        s.commit()


def test_stop_requested_before_the_job_started_is_cleared(sched_db):
    control.request_production_stop()                                   # requested "now"
    _job(sched_db, "cycle_1_20261120_0000", datetime.utcnow() + timedelta(minutes=5))   # job starts later
    assert control.wait_or_check_production_control(job_id="cycle_1_20261120_0000") == "CONTINUE"
    st = control.get_production_control_state()
    assert st["state"] == "IDLE" and "Stale stop request" in st["reason"]
    assert any(h.get("new") == "IDLE (stale request cleared)" for h in control._history())


def test_stop_requested_while_the_job_runs_is_honoured(sched_db):
    _job(sched_db, "cycle_2_20261120_0600", datetime.utcnow() - timedelta(minutes=5))   # started before the request
    control.request_production_stop()
    assert control.wait_or_check_production_control(job_id="cycle_2_20261120_0600") == "STOP"
    assert control.get_production_control_state()["state"] == "STOP_REQUESTED"


def test_unknown_job_or_timestamp_keeps_the_stop(sched_db):
    control.request_production_stop()
    assert control.wait_or_check_production_control(job_id="no_such_job") == "STOP"
    assert control.wait_or_check_production_control(job_id=None) == "STOP"
