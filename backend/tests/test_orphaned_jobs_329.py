"""
Stage 3.29: jobs orphaned by a schedule change never hold the production slot.

Live incident (2026-11-18 12:00 -> 2026-11-19): after the schedule was replaced, cycle_10_20261118_0900
(RETRYING, heartbeat 2026-11-18 03:35 UTC) and cycle_2_20261118_0600 (RUNNING) belonged to cycles that no
longer existed. The reconciler "resumed" them every pass, so they counted against the concurrency limit and
cycle_4 was silently DEFERRED until its window expired, two days running. Temporary DB only.
"""
import json
from datetime import datetime, timedelta

from backend.app.database.db import JobModel
from backend.app.scheduler import reconciliation_service as recon_mod
from backend.app.services import control_center_service as control

from test_operator_retry import JOB3, T, _row, sched_db  # noqa: F401  (fixture)

ORPHAN = "cycle_10_20261113_0900"      # FOUR_CYCLES_DAILY has cycle_1..cycle_4 only
ORPHAN2 = "cycle_7_20261113_0600"


def _cp(deadline):
    return json.dumps({"cycle_state": {"cycle_id": ORPHAN.rsplit("_", 2)[0], "recovery_expiration_at": deadline}})


def _seed(factory, job_id, status, heartbeat, deadline):
    with factory() as s:
        s.add(JobModel(id=job_id, date_str="2026-11-13", status=status, current_stage="STORY_DEVELOPMENT",
                       heartbeat_at=heartbeat, checkpoint_data_json=_cp(deadline)))
        s.commit()


def test_orphaned_stale_jobs_are_closed_and_free_the_slot(sched_db):
    old = datetime.utcnow() - timedelta(days=2)
    _seed(sched_db, ORPHAN, "RETRYING", old, "2026-11-13T13:00:00+05:30")
    _seed(sched_db, ORPHAN2, "RUNNING", old, "2026-11-13T23:00:00+05:30")
    assert control.production_capacity()["available"] is False          # the incident: slot held by the dead rows

    report = recon_mod.startup_reconciler.reconcile_on_startup(test_now_ist=T(18, 0))

    assert _row(sched_db, ORPHAN).status == "RECOVERY_WINDOW_EXPIRED"     # its own deadline (13:00) has passed
    assert _row(sched_db, ORPHAN2).status == "MISSED"                     # deadline later, but no cycle can run it
    for j in (ORPHAN, ORPHAN2):
        r = _row(sched_db, j)
        assert r.recovery_reason == "ORPHANED_BY_SCHEDULE_CHANGE" and "active schedule" in r.error_details
    assert {e["job_id"] for e in report["expired_cycles"]} >= {ORPHAN, ORPHAN2}
    # the orphans no longer count; at 18:00 the reconciler legitimately starts that day's cycle_4
    assert control.production_capacity(excluding_job_id="cycle_4_20261113_1800")["available"] is True


def test_a_second_pass_leaves_closed_orphans_alone(sched_db):
    _seed(sched_db, ORPHAN, "RETRYING", datetime.utcnow() - timedelta(days=2), "2026-11-13T13:00:00+05:30")
    recon_mod.startup_reconciler.reconcile_on_startup(test_now_ist=T(18, 0))
    recon_mod.startup_reconciler.reconcile_on_startup(test_now_ist=T(18, 1))
    assert _row(sched_db, ORPHAN).status == "RECOVERY_WINDOW_EXPIRED"


def test_an_orphan_with_a_live_heartbeat_is_not_touched(sched_db):
    _seed(sched_db, ORPHAN, "RUNNING", datetime.utcnow(), "2026-11-13T23:00:00+05:30")
    recon_mod.startup_reconciler.reconcile_on_startup(test_now_ist=T(18, 0))
    assert _row(sched_db, ORPHAN).status == "RUNNING"


def test_cycle_in_the_active_schedule_is_still_resumed_normally(sched_db):
    with sched_db() as s:
        s.add(JobModel(id=JOB3, date_str="2026-11-13", status="RUNNING", current_stage="VOICEOVER",
                       heartbeat_at=datetime.utcnow() - timedelta(hours=2),
                       checkpoint_data_json=json.dumps({"cycle_state": {"cycle_id": "cycle_3"}})))
        s.commit()
    recon_mod.startup_reconciler.reconcile_on_startup(test_now_ist=T(13, 0))   # inside cycle_3's window
    row = _row(sched_db, JOB3)
    assert row.status in ("RETRYING", "RUNNING")                  # resumed by the normal path
    assert row.recovery_reason != "ORPHANED_BY_SCHEDULE_CHANGE"
