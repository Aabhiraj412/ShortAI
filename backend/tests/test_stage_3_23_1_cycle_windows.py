import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.db import Base, JobModel
from backend.app.scheduler import reconciliation_service, scheduler as scheduler_module
from backend.app.services import email_monitoring_service as email_monitoring_module
from backend.app.services import notification_service as notification_module
from backend.app.scheduler.schedule_profiles import (
    IST_TZ,
    POLICY_CATCH_UP,
    POLICY_STRICT_WINDOW,
    PROFILE_FOUR_CYCLES_DAILY,
)


@pytest.fixture
def isolated_cycle_database(tmp_path, monkeypatch):
    db_file = tmp_path / "cycle-window-tests.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(reconciliation_service, "SessionLocal", test_sessions)
    monkeypatch.setattr(scheduler_module, "SessionLocal", test_sessions)
    monkeypatch.setattr(email_monitoring_module, "SessionLocal", test_sessions)
    monkeypatch.setattr(reconciliation_service, "get_active_profile", lambda: PROFILE_FOUR_CYCLES_DAILY)
    monkeypatch.setattr(scheduler_module, "get_active_profile", lambda: PROFILE_FOUR_CYCLES_DAILY)
    monkeypatch.setattr(email_monitoring_module, "get_active_profile", lambda: PROFILE_FOUR_CYCLES_DAILY)
    monkeypatch.setattr(notification_module.notification_service, "db_path", str(db_file))
    notification_module.notification_service._init_notification_db()
    monkeypatch.setattr(notification_module.notification_service, "notify_email", lambda *a, **kw: False)
    monkeypatch.setattr(notification_module.notification_service, "notify_desktop", lambda *a, **kw: True)
    yield test_sessions
    engine.dispose()


def test_recovery_window_is_half_open_at_expiration():
    cycle = PROFILE_FOUR_CYCLES_DAILY.cycles[0]
    start = datetime(2026, 11, 10, 0, 0, tzinfo=IST_TZ)
    expiration = datetime(2026, 11, 10, 4, 0, tzinfo=IST_TZ)
    just_before_expiration = datetime(2026, 11, 10, 3, 59, 59, tzinfo=IST_TZ)

    assert cycle.is_within_recovery_window(start, start)
    assert cycle.is_within_recovery_window(start, just_before_expiration)
    assert not cycle.is_within_recovery_window(start, expiration)
    assert cycle.is_past_recovery_window(start, expiration)


def test_reconciliation_expires_cycle_without_backfill(isolated_cycle_database):
    network = MagicMock()
    network.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    reconciler = reconciliation_service.StartupReconciler(network_service=network)

    report = reconciler.reconcile_on_startup(
        test_now_ist=datetime(2026, 11, 10, 4, 0, tzinfo=IST_TZ),
        recovery_policy=POLICY_CATCH_UP,
    )

    with isolated_cycle_database() as session:
        job = session.query(JobModel).filter_by(id="cycle_1_20261110_0000").one()
        cycle_state = json.loads(job.checkpoint_data_json)["cycle_state"]

    assert report["policy"] == POLICY_STRICT_WINDOW
    assert report["recovered_cycles"] == []
    assert report["expired_cycles"][0]["job_id"] == "cycle_1_20261110_0000"
    assert job.status == "RECOVERY_WINDOW_EXPIRED"
    assert cycle_state["recovery_expiration_at"] == "2026-11-10T04:00:00+05:30"
    assert cycle_state["finalization_start_at"] == cycle_state["recovery_expiration_at"]
    assert cycle_state["target_publication_at"] == "2026-11-10T06:00:00+05:30"


@pytest.mark.asyncio
async def test_scheduler_does_not_start_generation_at_recovery_expiration(
    isolated_cycle_database, monkeypatch
):
    monkeypatch.setattr(scheduler_module, "get_operating_mode", lambda: "PRODUCTION")
    monkeypatch.setattr(
        scheduler_module,
        "production_capacity",
        lambda excluding_job_id=None: {"available": True},
    )
    monkeypatch.setattr(
        scheduler_module.master_orchestrator,
        "execute_production_pipeline",
        lambda **_: pytest.fail("Expired cycle must not start production."),
    )

    result = await scheduler_module.run_cycle_generation(
        cycle_id="cycle_1",
        test_ist_now=datetime(2026, 11, 10, 4, 0, tzinfo=IST_TZ),
    )

    with isolated_cycle_database() as session:
        job = session.query(JobModel).filter_by(id="cycle_1_20261110_0000").one()
        cycle_state = json.loads(job.checkpoint_data_json)["cycle_state"]

    assert result["status"] == "RECOVERY_WINDOW_EXPIRED"
    assert job.status == "RECOVERY_WINDOW_EXPIRED"
    assert cycle_state["final_cycle_status"] == "RECOVERY_WINDOW_EXPIRED"


def test_all_four_cycles_boundaries_and_rollover():
    now_date = datetime(2026, 11, 10, 0, 0, tzinfo=IST_TZ)
    expected = [
        ("cycle_1", 0, 4, 6, 0),
        ("cycle_2", 6, 10, 12, 0),
        ("cycle_3", 12, 16, 18, 0),
        ("cycle_4", 18, 22, 0, 1),
    ]
    for cycle, (cid, start_h, exp_h, pub_h, rollover) in zip(PROFILE_FOUR_CYCLES_DAILY.cycles, expected):
        c_start, t_pub, _, _ = cycle.calculate_datetimes(now_date)
        assert cycle.cycle_id == cid
        assert c_start.hour == start_h
        assert t_pub.hour == pub_h
        assert (t_pub.date() - c_start.date()).days == rollover
        inside = c_start.replace(hour=exp_h - 1, minute=59, second=59)
        at_exp = c_start.replace(hour=exp_h, minute=0, second=0)
        assert cycle.is_within_recovery_window(c_start, inside)
        assert not cycle.is_within_recovery_window(c_start, at_exp)


@pytest.mark.asyncio
async def test_dispatch_eligible_recoveries_resumes_interrupted_and_waits_for_internet(
    isolated_cycle_database, monkeypatch, tmp_path
):
    from backend.app.services.youtube_publishing_engine import PublishingDatabase

    monkeypatch.setattr(scheduler_module, "get_operating_mode", lambda: "PRODUCTION")
    monkeypatch.setattr(
        scheduler_module,
        "production_capacity",
        lambda excluding_job_id=None: {"available": True},
    )

    with isolated_cycle_database() as session:
        session.add(
            JobModel(
                id="cycle_2_20261110_0600",
                date_str="2026-11-10",
                status="INTERRUPTED",
                current_stage="VOICEOVER",
                progress_pct=55.0,
                checkpoint_data_json=json.dumps({"topic_title": "Preserved Checkpoint Topic"}),
            )
        )
        session.commit()

    # 1. Offline inside Cycle 2 recovery window (07:30 IST): marks WAITING_FOR_INTERNET without launching pipeline
    offline_net = MagicMock()
    offline_net.is_environment_ready_for_cycle.return_value = (False, "OFFLINE", {})
    monkeypatch.setattr(scheduler_module, "network_connectivity_service", offline_net)
    monkeypatch.setattr(reconciliation_service.startup_reconciler, "network", offline_net)
    called_jobs = []
    monkeypatch.setattr(
        scheduler_module.master_orchestrator,
        "execute_production_pipeline",
        lambda **kwargs: called_jobs.append(kwargs["job_id"]) or {"status": "COMPLETED", "job_id": kwargs["job_id"]},
    )

    waiting_res = await scheduler_module.dispatch_eligible_recoveries(
        test_ist_now=datetime(2026, 11, 10, 7, 30, tzinfo=IST_TZ)
    )
    assert called_jobs == []
    assert "cycle_2_20261110_0600" in waiting_res["waiting_for_internet"]

    # 2. Internet restored at 08:15 IST: dispatches resume from checkpoint
    online_net = MagicMock()
    online_net.is_environment_ready_for_cycle.return_value = (True, "READY", {})
    monkeypatch.setattr(scheduler_module, "network_connectivity_service", online_net)
    monkeypatch.setattr(reconciliation_service.startup_reconciler, "network", online_net)

    resumed_res = await scheduler_module.dispatch_eligible_recoveries(
        test_ist_now=datetime(2026, 11, 10, 8, 15, tzinfo=IST_TZ)
    )
    assert called_jobs == ["cycle_2_20261110_0600"]
    assert resumed_res["dispatched"][0]["job_id"] == "cycle_2_20261110_0600"

    # 3. Active YouTube upload stays uninterrupted even after recovery window expires at 10:00 IST
    from backend.app.services.youtube_publishing_engine import PublicationRecord
    pub_db = PublishingDatabase(db_path=str(tmp_path / "pub_continuity.db"))
    pub_id = pub_db.record_publication(
        PublicationRecord(
            id=None,
            topic_title="Preserved Checkpoint Topic",
            video_path="v.mp4",
            thumbnail_path=None,
            metadata_json="{}",
            youtube_video_id=None,
            upload_status="uploading",
            visibility="private",
            video_hash="abc123",
        )
    )
    assert pub_db.get_publication(pub_id)["upload_status"] == "uploading"

    # 4. At 10:00 IST expiration boundary, an incomplete waiting job transitions cleanly to RECOVERY_WINDOW_EXPIRED
    with isolated_cycle_database() as session:
        j = session.query(JobModel).filter_by(id="cycle_2_20261110_0600").one()
        j.status = "WAITING_FOR_INTERNET"
        session.commit()
    expired_res = await scheduler_module.dispatch_eligible_recoveries(
        test_ist_now=datetime(2026, 11, 10, 10, 0, tzinfo=IST_TZ)
    )
    with isolated_cycle_database() as session:
        j = session.query(JobModel).filter_by(id="cycle_2_20261110_0600").one()
        assert j.status == "RECOVERY_WINDOW_EXPIRED"
    assert len(called_jobs) == 1
    assert any(item["job_id"] == "cycle_2_20261110_0600" for item in expired_res["expired"])
    assert pub_db.get_publication(pub_id)["upload_status"] == "uploading"
