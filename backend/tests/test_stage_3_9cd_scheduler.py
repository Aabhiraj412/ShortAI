"""
Unit & Integration Tests for Stages 3.9C & 3.9D: Four Daily Cycles & Profile Switching.
Verifies Asia/Kolkata timezone awareness, 4 cycles with midnight date rollover,
deterministic job IDs, deadline SLA checks, duplicate prevention, and profile reload.
"""

from datetime import datetime, timedelta
import pytest
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.scheduler.schedule_profiles import (
    IST_TZ,
    PROFILE_FOUR_CYCLES_DAILY,
    PROFILE_SINGLE_DAILY_2300,
    get_active_profile,
    set_active_profile,
    list_profiles,
)
from backend.app.scheduler.scheduler import (
    configure_scheduler_jobs,
    get_scheduler_status,
    reload_scheduler,
    run_cycle_generation,
    run_cycle_publishing,
    scheduler,
)
from backend.app.core.config import settings
from backend.app.database.db import SessionLocal, JobModel
from backend.app.database.db import Base
from backend.app.scheduler import scheduler as scheduler_module
from backend.app.services import control_center_service


@pytest.fixture(autouse=True)
def isolate_scheduler_database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'scheduler-tests.db'}")
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(sys.modules[__name__], "SessionLocal", test_sessions)
    monkeypatch.setattr(scheduler_module, "SessionLocal", test_sessions)
    monkeypatch.setattr(control_center_service, "SessionLocal", test_sessions)
    monkeypatch.setattr(scheduler_module, "get_operating_mode", lambda: "PRODUCTION")
    yield
    engine.dispose()


def test_four_cycles_profile_definition():
    """Verifies that FOUR_CYCLES_DAILY is configured with exact times and 6h windows."""
    set_active_profile("FOUR_CYCLES_DAILY")
    profile = get_active_profile()
    assert profile.name == "FOUR_CYCLES_DAILY"
    assert len(profile.cycles) == 4

    c1, c2, c3, c4 = profile.cycles

    # Cycle 1: 00:00 -> 06:00 IST
    assert c1.cycle_id == "cycle_1"
    assert c1.start_hour_ist == 0
    assert c1.target_pub_hour_ist == 6
    assert c1.pub_rollover_days == 0
    assert c1.window_hours == 6.0

    # Cycle 2: 06:00 -> 12:00 IST
    assert c2.cycle_id == "cycle_2"
    assert c2.start_hour_ist == 6
    assert c2.target_pub_hour_ist == 12
    assert c2.pub_rollover_days == 0

    # Cycle 3: 12:00 -> 18:00 IST
    assert c3.cycle_id == "cycle_3"
    assert c3.start_hour_ist == 12
    assert c3.target_pub_hour_ist == 18
    assert c3.pub_rollover_days == 0

    # Cycle 4: 18:00 -> 00:00 IST (Next Calendar Day Rollover)
    assert c4.cycle_id == "cycle_4"
    assert c4.start_hour_ist == 18
    assert c4.target_pub_hour_ist == 0
    assert c4.pub_rollover_days == 1
    assert c4.window_hours == 6.0


def test_cycle_datetime_and_midnight_rollover():
    """Verifies date calculation and midnight rollover for Cycle 4."""
    profile = get_active_profile()
    c4 = next(c for c in profile.cycles if c.cycle_id == "cycle_4")

    # Fixed IST datetime: November 10, 2026 at 18:00 IST
    test_dt = datetime(2026, 11, 10, 18, 0, 0, tzinfo=IST_TZ)
    start_dt, pub_dt, date_str, pub_date_str = c4.calculate_datetimes(test_dt)

    assert date_str == "2026-11-10"
    assert pub_date_str == "2026-11-11"  # Next calendar day!
    assert start_dt.hour == 18
    assert pub_dt.hour == 0
    assert pub_dt.day == 11
    assert pub_dt - start_dt == timedelta(hours=6)

    # Job ID generation
    job_id = c4.format_job_id(test_dt)
    assert job_id == "cycle_4_20261110_1800"


def test_runtime_profile_switching():
    """Verifies dynamic switching between profiles without modifying source code."""
    # Switch to SINGLE_DAILY_2300
    reload_scheduler("SINGLE_DAILY_2300")
    st_single = get_scheduler_status()
    assert st_single["active_profile"] == "SINGLE_DAILY_2300"
    assert st_single["cycles_count"] == 1
    assert st_single["cycles"][0]["cycle_id"] == "cycle_daily"

    # Switch back to FOUR_CYCLES_DAILY
    reload_scheduler("FOUR_CYCLES_DAILY")
    st_four = get_scheduler_status()
    assert st_four["active_profile"] == "FOUR_CYCLES_DAILY"
    assert st_four["cycles_count"] == 4
    assert len(st_four["cycles"]) == 4

    # Profile listing
    profs = list_profiles()
    assert len(profs) == 2
    names = [p["name"] for p in profs]
    assert "FOUR_CYCLES_DAILY" in names
    assert "SINGLE_DAILY_2300" in names


@pytest.mark.asyncio
async def test_recovery_window_expiration_prevents_late_generation():
    """A trigger after recovery expiration must not start a new production run."""
    reload_scheduler("FOUR_CYCLES_DAILY")
    profile = get_active_profile()
    c1 = profile.cycles[0]  # Start 00:00, Deadline 06:00

    # Simulate triggering at 07:30 IST (1.5 hours past deadline)
    late_dt = datetime(2026, 11, 10, 7, 30, 0, tzinfo=IST_TZ)

    res = await run_cycle_generation(cycle_id="cycle_1", test_ist_now=late_dt)
    assert res["status"] == "RECOVERY_WINDOW_EXPIRED"
    assert "Recovery window expired" in res["error"]

    # Verify SQLite status
    job_id = c1.format_job_id(late_dt)
    with SessionLocal() as db_session:
        saved_job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
        assert saved_job is not None
        assert saved_job.status == "RECOVERY_WINDOW_EXPIRED"


@pytest.mark.asyncio
async def test_duplicate_run_prevention():
    """Verifies that an already COMPLETED job is skipped without re-executing pipeline."""
    reload_scheduler("FOUR_CYCLES_DAILY")
    test_dt = datetime(2026, 11, 10, 6, 15, 0, tzinfo=IST_TZ)
    profile = get_active_profile()
    c2 = profile.cycles[1]
    job_id = c2.format_job_id(test_dt)

    # Pre-seed a COMPLETED job in DB
    with SessionLocal() as db_session:
        job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
        if not job:
            job = JobModel(id=job_id, date_str="2026-11-10", status="COMPLETED")
            db_session.add(job)
        else:
            job.status = "COMPLETED"
        db_session.commit()

    # Attempt to run generation for the same cycle
    res = await run_cycle_generation(cycle_id="cycle_2", test_ist_now=test_dt)
    assert res["status"] == "SKIPPED"
    assert res["reason"] == "ALREADY_COMPLETED"
    assert res["job_id"] == job_id


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["GAMING", "IDLE", "MAINTENANCE"])
async def test_generation_is_deferred_outside_production_mode(monkeypatch, mode):
    monkeypatch.setattr(scheduler_module, "get_operating_mode", lambda: mode)

    result = await run_cycle_generation(
        cycle_id="cycle_1",
        test_ist_now=datetime(2026, 11, 12, 0, 0, tzinfo=IST_TZ),
    )

    assert result["status"] == "DEFERRED"
    assert mode in result["reason"]


@pytest.mark.asyncio
async def test_cycle_publishing_safety_gate(monkeypatch):
    """Verifies that run_cycle_publishing executes queue processing with upload disabled in manual_approval mode."""
    monkeypatch.setattr(settings, "PUBLISHING_MODE", "manual_approval")
    test_dt = datetime(2026, 11, 10, 12, 0, 0, tzinfo=IST_TZ)
    res = await run_cycle_publishing(cycle_id="cycle_2", test_ist_now=test_dt)
    assert res["status"] == "withheld_safety_gate"
    assert res["publishing_mode"] == "manual_approval"
