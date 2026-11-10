"""
Persistent Decoupled Scheduler for CrazzyDocs YouTube Shorts Studio.
Phase 5, Stages 3.8, 3.9C & 3.9D Production Standard.

Supports:
- Timezone-aware scheduling in Asia/Kolkata (IST).
- Four Daily Production Cycles (00:00, 06:00, 12:00, 18:00 IST start -> 6-hour pub targets).
- Dynamic Schedule Profiles (FOUR_CYCLES_DAILY vs SINGLE_DAILY_2300).
- Idempotent execution locks, deadline SLA tracking, and restart recovery.
- Strict manual approval publishing gate (settings.PUBLISHING_MODE = "manual_approval").
"""

from datetime import datetime, timezone, timedelta
import logging
import os
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.core.config import settings
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator
from backend.app.scheduler.schedule_profiles import (
    IST_TZ,
    ProductionCycle,
    ScheduleProfile,
    get_active_profile,
    set_active_profile,
    list_profiles,
)
from backend.app.services.email_monitoring_service import email_monitoring_service
from backend.app.services.network_connectivity_service import network_connectivity_service
from backend.app.scheduler.reconciliation_service import (
    SchedulerProcessLock,
    calculate_next_available_publishing_slot,
)
from backend.app.database.db import SessionLocal, JobModel

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def run_cycle_generation(cycle_id: str, test_ist_now: Optional[datetime] = None) -> Dict:
    """
    Executes automated content generation for a specific production cycle in Asia/Kolkata.
    Prevents duplicate runs, recovers from crashed jobs, and enforces 6h SLA windows.
    """
    profile = get_active_profile()
    cycle = next((c for c in profile.cycles if c.cycle_id == cycle_id), None)
    if not cycle:
        err = f"Unknown cycle_id '{cycle_id}' in profile '{profile.name}'."
        logger.error(err)
        return {"status": "ERROR", "error": err}

    now_ist = test_ist_now or datetime.now(IST_TZ)
    cycle_start, target_pub, date_str, pub_date_str = cycle.calculate_datetimes(now_ist)
    job_id = cycle.format_job_id(now_ist)

    logger.info(
        "\n[SCHEDULER] === TRIGGERING CYCLE GENERATION: %s (%s) ===",
        cycle.name,
        job_id,
    )
    logger.info(
        "Current IST: %s | Start: %s | Target Pub: %s",
        now_ist.isoformat(),
        cycle_start.isoformat(),
        target_pub.isoformat(),
    )

    # 1. State Checkpoint & Duplicate Verification
    with SessionLocal() as db_session:
        existing = db_session.query(JobModel).filter(JobModel.id == job_id).first()
        if existing:
            if existing.status in ("COMPLETED", "PUBLISHED"):
                msg = f"Job {job_id} already completed successfully. Skipping to avoid duplicate production."
                logger.info("[SCHEDULER] %s", msg)
                return {
                    "status": "SKIPPED",
                    "job_id": job_id,
                    "reason": "ALREADY_COMPLETED",
                    "cycle_id": cycle_id,
                }
            elif existing.status == "RUNNING":
                # Check for stale heartbeat (> 30 mins)
                stale_threshold = datetime.utcnow() - timedelta(minutes=30)
                if existing.heartbeat_at and existing.heartbeat_at < stale_threshold:
                    logger.warning(
                        "[SCHEDULER] Job %s has stale heartbeat (%s). Attempting restart recovery.",
                        job_id,
                        existing.heartbeat_at,
                    )
                    existing.retry_count = (existing.retry_count or 0) + 1
                    existing.status = "RETRYING"
                    db_session.commit()
                else:
                    msg = f"Job {job_id} is currently actively RUNNING. Skipping duplicate trigger."
                    logger.info("[SCHEDULER] %s", msg)
                    return {
                        "status": "SKIPPED",
                        "job_id": job_id,
                        "reason": "CONCURRENT_ACTIVE_RUN",
                        "cycle_id": cycle_id,
                    }

    # 2. Deadline Check (SLA Enforcement)
    if cycle.is_deadline_exceeded(cycle_start, now_ist):
        logger.error(
            "[SCHEDULER] Cycle %s generation exceeded deadline (Start: %s, Current: %s).",
            cycle_id,
            cycle_start.isoformat(),
            now_ist.isoformat(),
        )
        with SessionLocal() as db_session:
            missed_job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
            if not missed_job:
                missed_job = JobModel(
                    id=job_id,
                    date_str=date_str,
                    status="MISSED",
                    current_stage="DEADLINE_EXCEEDED",
                    error_details="Triggered after target publication SLA deadline",
                )
                db_session.add(missed_job)
            else:
                missed_job.status = "MISSED"
                missed_job.error_details = "Triggered after target publication SLA deadline"
            db_session.commit()
        missed_res = {
            "status": "MISSED",
            "job_id": job_id,
            "error": "DEADLINE_EXCEEDED",
            "cycle_id": cycle_id,
        }
        try:
            email_monitoring_service.send_cycle_summary_report(
                cycle_id=cycle_id, job_id=job_id, result=missed_res, test_now_ist=now_ist
            )
        except Exception as ne:
            logger.warning("[SCHEDULER] Could not send deadline summary notification: %s", ne)
        return missed_res

    # 3. Network Connectivity Check
    net_ready, net_reason, _ = network_connectivity_service.is_environment_ready_for_cycle()
    if not net_ready:
        logger.warning(
            "[SCHEDULER] Internet offline at cycle %s trigger (%s). Marking WAITING_FOR_INTERNET...",
            cycle_id,
            net_reason,
        )
        with SessionLocal() as db_session:
            w_job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
            if w_job:
                w_job.status = "WAITING_FOR_INTERNET"
                w_job.error_details = f"Awaiting network connectivity: {net_reason}"
                db_session.commit()
            else:
                w_job = JobModel(
                    id=job_id,
                    date_str=date_str,
                    status="WAITING_FOR_INTERNET",
                    current_stage="TOPIC_DISCOVERY",
                    error_details=f"Awaiting network connectivity: {net_reason}",
                )
                db_session.add(w_job)
                db_session.commit()

        recovery_end = cycle_start + timedelta(hours=cycle.recovery_window_hours)
        net_restored = network_connectivity_service.wait_for_connectivity(
            max_wait_seconds=300.0,
            initial_backoff=5.0,
            deadline_dt=recovery_end,
        )
        if not net_restored:
            logger.error("[SCHEDULER] Network connectivity could not be established within recovery window.")
            with SessionLocal() as db_session:
                w_job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                if w_job:
                    w_job.status = "MISSED"
                    w_job.error_details = "Recovery window expired during network outage"
                    db_session.commit()
            fail_res = {
                "status": "MISSED",
                "job_id": job_id,
                "error": "NETWORK_UNAVAILABLE_PAST_DEADLINE",
                "cycle_id": cycle_id,
            }
            try:
                email_monitoring_service.send_cycle_summary_report(
                    cycle_id=cycle_id, job_id=job_id, result=fail_res, test_now_ist=now_ist
                )
            except Exception:
                pass
            return fail_res

    # 4. Execute Autonomous Production Pipeline (Stages 1-13)
    try:
        res = master_orchestrator.execute_production_pipeline(job_id=job_id)
        logger.info(
            "[SCHEDULER] Cycle %s generation finished with status: %s",
            cycle_id,
            res.get("status"),
        )
        try:
            email_monitoring_service.send_cycle_summary_report(
                cycle_id=cycle_id, job_id=job_id, result=res, test_now_ist=now_ist
            )
        except Exception as ne:
            logger.warning("[SCHEDULER] Could not send success summary notification: %s", ne)
        return res
    except Exception as e:
        logger.exception("[SCHEDULER] Cycle %s generation failed: %s", cycle_id, e)
        fail_res = {
            "status": "FAILED",
            "job_id": job_id,
            "error": str(e),
            "cycle_id": cycle_id,
        }
        try:
            email_monitoring_service.send_cycle_summary_report(
                cycle_id=cycle_id, job_id=job_id, result=fail_res, test_now_ist=now_ist
            )
        except Exception as ne:
            logger.warning("[SCHEDULER] Could not send failure summary notification: %s", ne)
        return fail_res


async def run_cycle_publishing(cycle_id: str, test_ist_now: Optional[datetime] = None) -> Dict:
    """
    Processes the publishing queue at target publication time.
    Adheres strictly to settings.PUBLISHING_MODE = 'manual_approval'.
    """
    profile = get_active_profile()
    cycle = next((c for c in profile.cycles if c.cycle_id == cycle_id), None)
    now_ist = test_ist_now or datetime.now(IST_TZ)

    logger.info(
        "\n[SCHEDULER] Processing target publishing queue for %s at %s...",
        cycle.name if cycle else cycle_id,
        now_ist.isoformat(),
    )
    is_auto_public = getattr(settings, "PUBLISHING_MODE", "") == "automatic_public"
    res = master_orchestrator.process_publishing_queue(execute_upload=is_auto_public)
    logger.info(
        "[SCHEDULER] Publishing queue processed for %s: status=%s",
        cycle_id,
        res.get("status"),
    )
    return res


def configure_scheduler_jobs():
    """Configures independent generation and publishing cron jobs for the active profile."""
    profile = get_active_profile()
    logger.info(
        "[SCHEDULER] Configuring jobs for active profile '%s' (%d cycles)...",
        profile.name,
        len(profile.cycles),
    )

    # Remove previously registered jobs if reloading
    for j in scheduler.get_jobs():
        scheduler.remove_job(j.id)

    for cycle in profile.cycles:
        # 1. Generation Job
        gen_job_id = f"gen_{cycle.cycle_id}"
        gen_trigger = CronTrigger(
            hour=cycle.start_hour_ist,
            minute=cycle.start_min_ist,
            timezone=profile.tz,
        )
        scheduler.add_job(
            run_cycle_generation,
            gen_trigger,
            args=[cycle.cycle_id],
            id=gen_job_id,
            replace_existing=True,
            name=f"Generation: {cycle.name}",
        )

        # 2. Publishing Queue Job
        pub_job_id = f"pub_{cycle.cycle_id}"
        pub_trigger = CronTrigger(
            hour=cycle.target_pub_hour_ist,
            minute=cycle.target_pub_min_ist,
            timezone=profile.tz,
        )
        scheduler.add_job(
            run_cycle_publishing,
            pub_trigger,
            args=[cycle.cycle_id],
            id=pub_job_id,
            replace_existing=True,
            name=f"Publishing Queue: {cycle.name}",
        )


def start_scheduler(skip_lock_for_tests: bool = False):
    """Initializes and starts APScheduler with active profile cycles and runs startup reconciliation."""
    if not skip_lock_for_tests:
        lock = SchedulerProcessLock()
        if not lock.acquire():
            logger.warning("Another scheduler instance is already active. Halting duplicate scheduler.")
            return False

    configure_scheduler_jobs()
    if not scheduler.running:
        scheduler.start()
    profile = get_active_profile()
    logger.info("APScheduler started: Profile='%s' (Asia/Kolkata).", profile.name)

    # Startup reconciliation
    try:
        from backend.app.scheduler.reconciliation_service import startup_reconciler
        reconcile_res = startup_reconciler.reconcile_on_startup()
        logger.info("[STARTUP] Reconciliation complete: %d recovered, %d missed", len(reconcile_res.get("recovered_cycles", [])), len(reconcile_res.get("missed_cycles", [])))
    except Exception as e:
        logger.warning("[STARTUP] Startup reconciliation exception: %s", e)

    try:
        email_monitoring_service.retry_pending_notifications()
    except Exception as e:
        logger.debug("Failed to retry pending notifications on startup: %s", e)
    return True


def reload_scheduler(profile_name: Optional[str] = None):
    """Dynamically reconfigures scheduler with a specified or updated profile."""
    if profile_name:
        set_active_profile(profile_name)
    configure_scheduler_jobs()
    profile = get_active_profile()
    logger.info("APScheduler reloaded with profile '%s'.", profile.name)


def get_scheduler_status() -> Dict:
    """Returns comprehensive running state, active profile, and next run times."""
    profile = get_active_profile()
    jobs_info = []

    for cycle in profile.cycles:
        gen_job = scheduler.get_job(f"gen_{cycle.cycle_id}")
        pub_job = scheduler.get_job(f"pub_{cycle.cycle_id}")

        gen_next_dt = getattr(gen_job, "next_run_time", None)
        pub_next_dt = getattr(pub_job, "next_run_time", None)

        gen_next = gen_next_dt.isoformat() if gen_next_dt else None
        pub_next = pub_next_dt.isoformat() if pub_next_dt else None

        jobs_info.append({
            "cycle_id": cycle.cycle_id,
            "cycle_name": cycle.name,
            "generation": {
                "job_id": f"gen_{cycle.cycle_id}",
                "cron_ist": f"{cycle.start_hour_ist:02d}:{cycle.start_min_ist:02d} IST",
                "next_run": gen_next,
            },
            "publishing": {
                "job_id": f"pub_{cycle.cycle_id}",
                "cron_ist": f"{cycle.target_pub_hour_ist:02d}:{cycle.target_pub_min_ist:02d} IST",
                "next_run": pub_next,
                "publishing_mode": settings.PUBLISHING_MODE,
            },
            "window_hours": cycle.window_hours,
            "pub_rollover_days": cycle.pub_rollover_days,
        })

    # Backward compatibility with Stage 3.8 single-cycle expectations
    legacy_gen = {
        "id": "daily_generation_job",
        "schedule_time_ist": "23:00 IST (11:00 PM)",
        "next_run": jobs_info[0]["generation"]["next_run"] if jobs_info else None,
    }
    legacy_pub = {
        "id": "daily_publishing_job",
        "schedule_time_ist": "10:00 IST (10:00 AM)",
        "next_run": jobs_info[0]["publishing"]["next_run"] if jobs_info else None,
        "publishing_mode": settings.PUBLISHING_MODE,
    }

    return {
        "is_running": scheduler.running,
        "active_profile": profile.name,
        "profile_description": profile.description,
        "timezone": profile.timezone_name,
        "cycles_count": len(profile.cycles),
        "publishing_mode": settings.PUBLISHING_MODE,
        "generation_job": legacy_gen,
        "publishing_job": legacy_pub,
        "cycles": jobs_info,
    }
