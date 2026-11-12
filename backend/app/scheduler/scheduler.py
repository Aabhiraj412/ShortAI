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

import asyncio
import contextvars
from datetime import datetime, timezone, timedelta
import logging
import os
import threading
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.app.core.config import settings
from backend.app.orchestration.crazzydocs_master_orchestrator import (
    master_orchestrator,
    suppress_orchestrator_completion_email,
)
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
    record_cycle_state,
)
from backend.app.database.db import SessionLocal, JobModel
from backend.app.services.control_center_service import get_operating_mode, production_capacity
from backend.app.services.lifecycle_events import record_lifecycle_event

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_last_db_revision_seen: Optional[str] = None


def reload_profile_from_db_if_changed() -> bool:
    """
    Checks persisted active schedule revision in SQLite and reloads APScheduler
    jobs in the current process if the dashboard persisted a newer revision.
    """
    global _last_db_revision_seen
    try:
        import json
        from backend.app.services.control_center_service import (
            ACTIVE_PROFILE_KEY,
            _read_setting,
            restore_active_schedule_profile,
        )
        raw = _read_setting(ACTIVE_PROFILE_KEY)
        if not raw:
            return False
        spec = json.loads(raw)
        db_rev = str(spec.get("revision") or "")
        db_ver = int(spec.get("schedule_version", 1))
        if not db_rev:
            return False
        current = get_active_profile()
        cur_rev = str(getattr(current, "revision", "") or "")
        cur_ver = int(getattr(current, "schedule_version", 1))
        if _last_db_revision_seen is None:
            _last_db_revision_seen = cur_rev
        if db_rev != _last_db_revision_seen or (db_rev != cur_rev and db_ver != cur_ver):
            logger.info(
                "[SCHEDULER] Detected updated schedule revision in DB (%s v%s -> %s v%s). Reloading...",
                cur_rev or "none",
                cur_ver,
                db_rev,
                db_ver,
            )
            restore_active_schedule_profile()
            _last_db_revision_seen = db_rev
            configure_scheduler_jobs()
            return True
    except Exception as exc:
        logger.debug("[SCHEDULER] DB schedule revision check skipped: %s", exc)
    return False


async def run_cycle_generation(cycle_id: str, test_ist_now: Optional[datetime] = None) -> Dict:
    """
    Executes automated content generation for a specific production cycle in Asia/Kolkata.
    Prevents duplicate runs, recovers from crashed jobs, and enforces 6h SLA windows.
    """
    reload_profile_from_db_if_changed()
    operating_mode = get_operating_mode()
    if operating_mode != "PRODUCTION":
        logger.info(
            "[SCHEDULER] Deferring cycle %s because operating mode is %s.",
            cycle_id,
            operating_mode,
        )
        return {
            "status": "DEFERRED",
            "cycle_id": cycle_id,
            "reason": f"Operating mode is {operating_mode}.",
        }

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

    if now_ist < cycle_start:
        return {
            "status": "DEFERRED",
            "job_id": job_id,
            "cycle_id": cycle_id,
            "reason": "The scheduled production start has not been reached.",
        }

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
            elif existing.status == "RECOVERY_WINDOW_EXPIRED":
                return {
                    "status": "SKIPPED",
                    "job_id": job_id,
                    "reason": "RECOVERY_WINDOW_EXPIRED",
                    "cycle_id": cycle_id,
                }
            elif existing.status == "RUNNING":
                if (
                    existing.heartbeat_at is None
                    and existing.recovery_reason == "STARTUP_RECONCILIATION"
                ):
                    logger.info("[SCHEDULER] Dispatching reconciled job %s within active recovery window.", job_id)
                else:
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

    if cycle.is_past_recovery_window(cycle_start, now_ist):
        reason = (
            f"Recovery window expired at "
            f"{(cycle_start + timedelta(hours=cycle.recovery_window_hours)).isoformat()}."
        )
        with SessionLocal() as db_session:
            expired_job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
            if expired_job is None:
                expired_job = JobModel(
                    id=job_id,
                    date_str=date_str,
                    status="RECOVERY_WINDOW_EXPIRED",
                    current_stage="RECOVERY_WINDOW_EXPIRED",
                    recovery_reason="RECOVERY_WINDOW_EXPIRED",
                    recovery_policy="STRICT_WINDOW",
                    error_details=reason,
                )
                db_session.add(expired_job)
            else:
                expired_job.status = "RECOVERY_WINDOW_EXPIRED"
                expired_job.recovery_reason = "RECOVERY_WINDOW_EXPIRED"
                expired_job.recovery_policy = "STRICT_WINDOW"
                expired_job.error_details = reason
            record_cycle_state(
                expired_job,
                cycle,
                cycle_start,
                target_pub,
                "RECOVERY_WINDOW_EXPIRED",
                now_ist,
                reason,
            )
            db_session.commit()
        return {
            "status": "RECOVERY_WINDOW_EXPIRED",
            "job_id": job_id,
            "cycle_id": cycle_id,
            "error": reason,
        }

    capacity = production_capacity(excluding_job_id=job_id)
    if not capacity["available"]:
        return {
            "status": "DEFERRED",
            "job_id": job_id,
            "cycle_id": cycle_id,
            "reason": "Configured production concurrency limit is reached.",
        }

    from backend.app.services.control_center_service import (
        evaluate_resource_safety,
        get_publishing_policy,
    )
    res_safety = evaluate_resource_safety()
    if not res_safety["safe"]:
        logger.warning("[SCHEDULER] Deferring cycle %s (%s): %s", cycle_id, job_id, res_safety["reason"])
        return {
            "status": "DEFERRED",
            "job_id": job_id,
            "cycle_id": cycle_id,
            "reason": res_safety["reason"],
        }

    record_lifecycle_event(
        "CYCLE_DUE",
        "DUE",
        cycle_id=cycle_id,
        job_id=job_id,
        details={"scheduled_start_ist": cycle_start.isoformat()},
    )
    record_lifecycle_event(
        "CYCLE_DISPATCH_STARTED",
        "DISPATCHING",
        cycle_id=cycle_id,
        job_id=job_id,
    )

    with SessionLocal() as db_session:
        production_job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
        is_recovery = production_job is not None and (
            production_job.status in ("RETRYING", "FAILED", "WAITING_FOR_INTERNET", "INTERRUPTED", "PAUSED", "QUEUED")
            or (
                production_job.status == "RUNNING"
                and production_job.heartbeat_at is None
                and production_job.recovery_reason == "STARTUP_RECONCILIATION"
            )
        )
        created_new = False
        if production_job is None:
            created_new = True
            active_policy = get_publishing_policy()
            effective_pub_mode = (
                settings.PUBLISHING_MODE
                if getattr(settings, "PUBLISHING_MODE", "") == "automatic_public"
                else active_policy
            )
            production_job = JobModel(
                id=job_id,
                date_str=date_str,
                status="QUEUED",
                current_stage="QUEUED",
                progress_pct=5.0,
                heartbeat_at=None,
                recovery_policy="STRICT_WINDOW",
                publishing_mode=effective_pub_mode,
            )
            db_session.add(production_job)
        else:
            production_job.status = "QUEUED"
            production_job.heartbeat_at = None
        if is_recovery:
            production_job.recovery_reason = "CHECKPOINT_RESUMED"
        record_cycle_state(
            production_job,
            cycle,
            cycle_start,
            target_pub,
            "RECOVERY_PENDING" if is_recovery else "QUEUED",
            now_ist,
        )
        db_session.commit()

    if created_new:
        record_lifecycle_event("CYCLE_JOB_CREATED", "QUEUED", cycle_id=cycle_id, job_id=job_id)
    else:
        record_lifecycle_event("CYCLE_RECOVERED", "RECOVERY_PENDING", cycle_id=cycle_id, job_id=job_id)
    record_lifecycle_event("CYCLE_JOB_ENQUEUED", "QUEUED", cycle_id=cycle_id, job_id=job_id)

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
                record_cycle_state(
                    w_job,
                    cycle,
                    cycle_start,
                    target_pub,
                    "RECOVERING",
                    now_ist,
                    w_job.error_details,
                )
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
                record_cycle_state(
                    w_job,
                    cycle,
                    cycle_start,
                    target_pub,
                    "RECOVERING",
                    now_ist,
                    w_job.error_details,
                )
                db_session.commit()

        recovery_end = cycle_start + timedelta(hours=cycle.recovery_window_hours)
        net_restored = network_connectivity_service.wait_for_connectivity(
            max_wait_seconds=300.0,
            initial_backoff=5.0,
            deadline_dt=recovery_end,
        )
        if not net_restored:
            recovery_check_time = datetime.now(IST_TZ)
            recovery_expired = cycle.is_past_recovery_window(cycle_start, recovery_check_time)
            job_status = "RECOVERY_WINDOW_EXPIRED" if recovery_expired else "WAITING_FOR_INTERNET"
            error = (
                "Recovery window expired during network outage"
                if recovery_expired
                else "Network remains unavailable; production is waiting within its recovery window."
            )
            logger.warning("[SCHEDULER] %s", error)
            with SessionLocal() as db_session:
                w_job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                if w_job:
                    w_job.status = job_status
                    if recovery_expired:
                        w_job.recovery_reason = "RECOVERY_WINDOW_EXPIRED"
                    w_job.error_details = error
                    record_cycle_state(
                        w_job,
                        cycle,
                        cycle_start,
                        target_pub,
                        job_status,
                        recovery_check_time,
                        error,
                    )
                    db_session.commit()
            if not recovery_expired:
                return {
                    "status": "WAITING_FOR_INTERNET",
                    "job_id": job_id,
                    "error": error,
                    "cycle_id": cycle_id,
                }
            fail_res = {
                "status": "RECOVERY_WINDOW_EXPIRED",
                "job_id": job_id,
                "error": error,
                "cycle_id": cycle_id,
            }
            try:
                email_monitoring_service.send_cycle_summary_report(
                    cycle_id=cycle_id, job_id=job_id, result=fail_res, test_now_ist=now_ist
                )
            except Exception:
                pass
            return fail_res

    # 4. Execute Autonomous Production Pipeline (only mark RUNNING once worker thread claims it)
    def _execute_claimed_worker():
        import json as _json
        worker_id = f"worker-{os.getpid()}-t{threading.get_ident()}"
        claim_ist = datetime.now(IST_TZ)
        with SessionLocal() as db_session:
            j = db_session.query(JobModel).filter(JobModel.id == job_id).first()
            if j:
                j.status = "RUNNING"
                j.heartbeat_at = datetime.utcnow()
                if j.actual_start_at is None:
                    j.actual_start_at = datetime.utcnow()
                cp = _json.loads(j.checkpoint_data_json or "{}")
                cs = cp.setdefault("cycle_state", {})
                cs["worker_id"] = worker_id
                cs["worker_claim_time"] = claim_ist.isoformat()
                cs["actual_dispatch_time"] = cs.get("actual_dispatch_time") or claim_ist.isoformat()
                j.checkpoint_data_json = _json.dumps(cp, ensure_ascii=True)
                record_cycle_state(j, cycle, cycle_start, target_pub, "RUNNING", claim_ist)
                db_session.commit()
        record_lifecycle_event(
            "WORKER_CLAIMED",
            "RUNNING",
            cycle_id=cycle_id,
            job_id=job_id,
            details={"worker_id": worker_id},
        )
        record_lifecycle_event(
            "WORKER_STARTED",
            "RUNNING",
            cycle_id=cycle_id,
            job_id=job_id,
            details={"worker_id": worker_id},
        )
        return master_orchestrator.execute_production_pipeline(job_id=job_id)

    try:
        suppression_token = suppress_orchestrator_completion_email.set(True)
        try:
            ctx = contextvars.copy_context()
            res = await asyncio.to_thread(
                ctx.run,
                _execute_claimed_worker,
            )
        finally:
            suppress_orchestrator_completion_email.reset(suppression_token)

        enriched_res = dict(res or {})
        with SessionLocal() as db_session:
            completed_job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
            if completed_job:
                if completed_job.topic_title and not enriched_res.get("topic"):
                    enriched_res["topic"] = completed_job.topic_title
                if completed_job.youtube_video_id and not enriched_res.get("youtube_video_id"):
                    enriched_res["youtube_video_id"] = completed_job.youtube_video_id
                cycle_status = (
                    "COMPLETED"
                    if completed_job.status == "COMPLETED"
                    else "INTERRUPTED"
                    if completed_job.status == "INTERRUPTED"
                    else "FAILED"
                    if completed_job.status in ("FAILED", "BLOCKED_QC")
                    else "FINALIZING"
                )
                record_cycle_state(
                    completed_job,
                    cycle,
                    cycle_start,
                    target_pub,
                    cycle_status,
                    datetime.now(IST_TZ),
                    completed_job.error_details if cycle_status == "FAILED" else None,
                )
                db_session.commit()
        logger.info(
            "[SCHEDULER] Cycle %s generation finished with status: %s",
            cycle_id,
            enriched_res.get("status"),
        )
        if str(enriched_res.get("status") or "").upper() != "INTERRUPTED":
            try:
                email_monitoring_service.send_cycle_summary_report(
                    cycle_id=cycle_id, job_id=job_id, result=enriched_res, test_now_ist=now_ist
                )
            except Exception as ne:
                logger.warning("[SCHEDULER] Could not send success summary notification: %s", ne)
        return enriched_res
    except Exception as e:
        logger.exception("[SCHEDULER] Cycle %s generation failed: %s", cycle_id, e)
        record_lifecycle_event(
            "CYCLE_DISPATCH_FAILED",
            "FAILED",
            cycle_id=cycle_id,
            job_id=job_id,
            error_details=str(e),
        )
        with SessionLocal() as db_session:
            failed_job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
            if failed_job:
                if failed_job.status != "BLOCKED_QC":
                    failed_job.status = "FAILED"
                failed_job.error_details = failed_job.error_details or str(e)
                record_cycle_state(
                    failed_job,
                    cycle,
                    cycle_start,
                    target_pub,
                    "FAILED",
                    datetime.now(IST_TZ),
                    str(e),
                )
                db_session.commit()
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
    from backend.app.services.control_center_service import (
        is_automatic_publishing_authorized,
    )
    operating_mode = get_operating_mode()
    auto_ok, _ = is_automatic_publishing_authorized()
    is_auto_public = (
        getattr(settings, "PUBLISHING_MODE", "") == "automatic_public"
        or (operating_mode == "PRODUCTION" and auto_ok)
    )
    res = master_orchestrator.process_publishing_queue(execute_upload=is_auto_public)
    try:
        from backend.app.services.youtube_publishing_engine import youtube_publishing_engine
        youtube_publishing_engine.verify_scheduled_publications()
    except Exception:
        pass
    logger.info(
        "[SCHEDULER] Publishing queue processed for %s: status=%s",
        cycle_id,
        res.get("status"),
    )
    return res


async def dispatch_eligible_recoveries(
    test_ist_now: Optional[datetime] = None,
    max_dispatches: int = 1,
) -> Dict:
    """
    Dispatches checkpoint-resumable or waiting cycle recoveries strictly within active windows.
    Expires any overdue jobs cleanly without backfilling later cycles, and honors bounded
    internet-wait retries until recovery_expiration.
    """
    from backend.app.scheduler.reconciliation_service import startup_reconciler

    reload_profile_from_db_if_changed()
    now_ist = test_ist_now or datetime.now(IST_TZ)
    operating_mode = get_operating_mode()
    if operating_mode != "PRODUCTION":
        return {
            "status": "DEFERRED",
            "reason": f"Operating mode is {operating_mode}.",
            "dispatched": [],
            "expired": [],
            "waiting_for_internet": [],
        }

    reconcile_report = startup_reconciler.reconcile_on_startup(test_now_ist=now_ist)
    profile = get_active_profile()
    dispatched: List[Dict] = []
    expired: List[Dict] = list(reconcile_report.get("expired_cycles", []))
    waiting: List[str] = []

    with SessionLocal() as db_session:
        candidates = (
            db_session.query(JobModel)
            .filter(
                JobModel.status.in_(
                    ["QUEUED", "RETRYING", "INTERRUPTED", "PAUSED", "WAITING_FOR_INTERNET", "RUNNING"]
                )
            )
            .order_by(JobModel.created_at.asc())
            .all()
        )
        candidate_ids = [
            (j.id, j.date_str, j.status, j.heartbeat_at, j.recovery_reason)
            for j in candidates
            if not (j.id or "").startswith(("test_", "mock_"))
        ]

    for job_id, date_str, status, heartbeat_at, recovery_reason in candidate_ids:
        if status == "RUNNING":
            stale_hb = heartbeat_at is not None and (datetime.utcnow() - heartbeat_at) > timedelta(minutes=5)
            reconciled_unclaimed = heartbeat_at is None and recovery_reason == "STARTUP_RECONCILIATION"
            if stale_hb:
                record_lifecycle_event(
                    "WORKER_STALLED",
                    "WORKER_STALLED",
                    cycle_id=job_id.split("_")[0] + "_" + job_id.split("_")[1] if "_" in job_id else None,
                    job_id=job_id,
                    error_details=f"No heartbeat since {heartbeat_at.isoformat()}",
                )
            elif not reconciled_unclaimed:
                continue

        cycle = next((c for c in profile.cycles if job_id.startswith(c.cycle_id + "_")), None)
        if cycle is None:
            continue
        try:
            cycle_date = datetime.strptime(date_str, "%Y-%m-%d")
        except (TypeError, ValueError):
            cycle_date = now_ist
        cycle_start = cycle_date.replace(
            hour=cycle.start_hour_ist,
            minute=cycle.start_min_ist,
            second=0,
            microsecond=0,
            tzinfo=IST_TZ,
        )
        target_pub = cycle_start + timedelta(hours=cycle.window_hours)

        if cycle.is_past_recovery_window(cycle_start, now_ist):
            with SessionLocal() as db_session:
                job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                if job and job.status != "RECOVERY_WINDOW_EXPIRED":
                    reason = (
                        f"Recovery window expired at "
                        f"{(cycle_start + timedelta(hours=cycle.recovery_window_hours)).isoformat()}."
                    )
                    job.status = "RECOVERY_WINDOW_EXPIRED"
                    job.recovery_reason = "RECOVERY_WINDOW_EXPIRED"
                    job.error_details = reason
                    record_cycle_state(
                        job, cycle, cycle_start, target_pub, "RECOVERY_WINDOW_EXPIRED", now_ist, reason
                    )
                    db_session.commit()
            expired.append({"job_id": job_id, "cycle_id": cycle.cycle_id, "reason": "RECOVERY_WINDOW_EXPIRED"})
            continue

        if len(dispatched) >= max_dispatches:
            continue

        net_ready, net_reason, _ = network_connectivity_service.is_environment_ready_for_cycle()
        if not net_ready:
            with SessionLocal() as db_session:
                job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                if job:
                    job.status = "WAITING_FOR_INTERNET"
                    job.error_details = f"Bounded retry awaiting internet inside recovery window: {net_reason}"
                    record_cycle_state(
                        job, cycle, cycle_start, target_pub, "RECOVERING", now_ist, job.error_details
                    )
                    db_session.commit()
            waiting.append(job_id)
            continue

        if not production_capacity(excluding_job_id=job_id)["available"]:
            continue

        with SessionLocal() as db_session:
            job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
            if job and job.status in ("INTERRUPTED", "PAUSED", "WAITING_FOR_INTERNET"):
                job.status = "RETRYING"
                job.recovery_reason = "CHECKPOINT_RESUMED"
                job.retry_count = (job.retry_count or 0) + 1
                db_session.commit()

        result = await run_cycle_generation(cycle.cycle_id, test_ist_now=now_ist)
        dispatched.append({"job_id": job_id, "cycle_id": cycle.cycle_id, "result_status": result.get("status")})

    return {
        "status": "OK",
        "reconcile_report": reconcile_report,
        "dispatched": dispatched,
        "expired": expired,
        "waiting_for_internet": waiting,
    }


async def run_daily_summary_dispatch(test_date_str: Optional[str] = None) -> Dict:
    """Dispatches the consolidated 24h publishing summary email in Asia/Kolkata timezone."""
    from backend.app.services.daily_publishing_summary import daily_publishing_summary_service
    try:
        res = daily_publishing_summary_service.send_daily_summary(date_str=test_date_str)
        logger.info("[SCHEDULER] Daily publishing summary dispatched: %s (date=%s)", res.get("status"), res.get("date"))
        return res
    except Exception as e:
        logger.error(
            "[SCHEDULER] Daily publishing summary dispatch error (%s).",
            type(e).__name__,
        )
        try:
            email_monitoring_service.dispatch_stage_notification(
                "SCHEDULER_FAILED",
                f"stage322-scheduler-daily-summary-{test_date_str or datetime.now(IST_TZ).strftime('%Y-%m-%d')}",
                "CrazzyDocs daily summary scheduler failed",
                {
                    "job": "daily_publishing_summary",
                    "error_type": type(e).__name__,
                    "timestamp_ist": datetime.now(IST_TZ).isoformat(),
                },
            )
        except Exception:
            logger.exception("Could not persist scheduler failure notification.")
        return {"status": "ERROR", "error": f"Daily summary dispatch failed ({type(e).__name__})."}


def reconcile_youtube_publications() -> Dict:
    """Periodically reconciles scheduled YouTube videos via YouTube Data API without auto-publishing."""
    try:
        from backend.app.services.youtube_publishing_engine import youtube_publishing_engine
        return youtube_publishing_engine.verify_scheduled_publications()
    except Exception as exc:
        logger.debug("[SCHEDULER] YouTube status reconciliation skipped: %s", exc)
        return {"checked": 0, "promoted": 0, "error": str(exc)}


def configure_scheduler_jobs():
    """Configures independent production generation, YouTube status reconciliation, and daily summary jobs."""
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
        # 1. Production Generation Job (independent of user-controlled YouTube publishing)
        gen_job_id = f"gen_{cycle.cycle_id}"
        gen_trigger = CronTrigger(
            hour=cycle.start_hour_ist,
            minute=cycle.start_min_ist,
            day_of_week=",".join(("mon", "tue", "wed", "thu", "fri", "sat", "sun")[day] for day in cycle.active_weekdays),
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

    # 2. Periodic YouTube Status Reconciliation Job (every 5 minutes)
    scheduler.add_job(
        reconcile_youtube_publications,
        IntervalTrigger(minutes=5),
        id="youtube_status_reconciliation_job",
        replace_existing=True,
        name="YouTube Status Reconciliation",
    )

    # 3. Daily Publishing Summary Email Job (default 06:00 IST, configurable)
    summary_hour = int(os.getenv("DAILY_SUMMARY_HOUR_IST", "6"))
    summary_min = int(os.getenv("DAILY_SUMMARY_MIN_IST", "0"))
    summary_job_id = "daily_publishing_summary_job"
    summary_trigger = CronTrigger(
        hour=summary_hour,
        minute=summary_min,
        timezone=profile.tz,
    )
    scheduler.add_job(
        run_daily_summary_dispatch,
        summary_trigger,
        id=summary_job_id,
        replace_existing=True,
        name="Daily Publishing Summary Email",
    )

    if scheduler.running or os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            from backend.app.services.control_center_service import record_scheduler_sync
            record_scheduler_sync(profile)
        except Exception as exc:
            logger.debug("[SCHEDULER] Could not persist scheduler sync state: %s", exc)


def start_scheduler(skip_lock_for_tests: bool = False):
    """Initializes and starts APScheduler with active profile cycles and runs startup reconciliation."""
    if not skip_lock_for_tests:
        lock = SchedulerProcessLock()
        if not lock.acquire():
            logger.warning("Another scheduler instance is already active. Halting duplicate scheduler.")
            return False

    try:
        from backend.app.services.control_center_service import restore_active_schedule_profile
        restore_active_schedule_profile()
    except Exception as exc:
        logger.warning("[SCHEDULER] Failed to restore persisted active schedule profile: %s", exc)

    configure_scheduler_jobs()
    if not scheduler.running:
        scheduler.start()
    profile = get_active_profile()
    global _last_db_revision_seen
    _last_db_revision_seen = str(getattr(profile, "revision", "") or "")
    try:
        from backend.app.services.control_center_service import record_scheduler_sync
        record_scheduler_sync(profile)
    except Exception as exc:
        logger.debug("[SCHEDULER] Could not persist initial scheduler sync state: %s", exc)
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

    summary_job = scheduler.get_job("daily_publishing_summary_job")
    summary_next_dt = getattr(summary_job, "next_run_time", None)
    summary_next = summary_next_dt.isoformat() if summary_next_dt else None

    return {
        "is_running": scheduler.running,
        "active_profile": profile.name,
        "loaded_version": int(getattr(profile, "schedule_version", 1)),
        "loaded_revision": str(getattr(profile, "revision", "")),
        "profile_description": profile.description,
        "timezone": profile.timezone_name,
        "cycles_count": len(profile.cycles),
        "publishing_mode": settings.PUBLISHING_MODE,
        "generation_job": legacy_gen,
        "publishing_job": legacy_pub,
        "daily_summary_job": {
            "cron_ist": f"{int(os.getenv('DAILY_SUMMARY_HOUR_IST', '6')):02d}:{int(os.getenv('DAILY_SUMMARY_MIN_IST', '0')):02d} IST",
            "next_run": summary_next,
        },
        "cycles": jobs_info,
    }
