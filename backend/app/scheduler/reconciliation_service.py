"""
Startup Reconciliation, Crash Recovery & Slot Management Service for CrazzyDocs.
Phase 5, Stage 3.10 Production Standard.

Ensures the 4-cycle autonomous production studio is resilient to:
- Laptop shutdown and delayed startup.
- Power/OS restarts and mid-pipeline crashes.
- Internet outages and provider degradation.
- Scheduler process crashes and duplicate launches.
"""

import ctypes
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import json
import logging
import os
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from backend.app.core.config import settings
from backend.app.database.db import SessionLocal, JobModel, PersistentNotificationModel
from backend.app.scheduler.schedule_profiles import (
    IST_TZ,
    POLICY_STRICT_WINDOW,
    ProductionCycle,
    ScheduleProfile,
    get_active_profile,
    get_recovery_policy,
)
from backend.app.services.network_connectivity_service import network_connectivity_service
from backend.app.services.email_monitoring_service import email_monitoring_service
from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
LOCK_FILE_PATH = os.path.join(WORKSPACE_ROOT, "backend", "studio_scheduler.lock")


def record_cycle_state(
    job: JobModel,
    cycle: ProductionCycle,
    cycle_start: datetime,
    target_publication: datetime,
    status: str,
    now_ist: Optional[datetime] = None,
    failure_reason: Optional[str] = None,
) -> None:
    """Persist schedule boundaries and state transitions alongside existing checkpoints."""
    checkpoints = json.loads(job.checkpoint_data_json or "{}")
    if not isinstance(checkpoints, dict):
        raise ValueError(f"Job {job.id} has invalid checkpoint data.")

    now = now_ist or datetime.now(IST_TZ)
    recovery_expiration = cycle_start + timedelta(hours=cycle.recovery_window_hours)
    cycle_state = checkpoints.setdefault("cycle_state", {})
    if not isinstance(cycle_state, dict):
        raise ValueError(f"Job {job.id} has invalid persisted cycle state.")

    cycle_state.update({
        "cycle_id": cycle.cycle_id,
        "production_start_at": cycle_start.isoformat(),
        "recovery_start_at": cycle_start.isoformat(),
        "recovery_expiration_at": recovery_expiration.isoformat(),
        "finalization_start_at": recovery_expiration.isoformat(),
        "target_publication_at": target_publication.isoformat(),
        "current_pipeline_stage": job.current_stage,
        "recovery_attempt_count": job.retry_count or 0,
        "last_successful_checkpoint": cycle_state.get("last_successful_checkpoint"),
        "final_cycle_status": status,
    })

    transitions = cycle_state.setdefault("state_transitions", [])
    if not isinstance(transitions, list):
        raise ValueError(f"Job {job.id} has invalid cycle state transition history.")
    if not transitions or transitions[-1].get("status") != status:
        transitions.append({"status": status, "timestamp": now.isoformat()})

    if failure_reason:
        failure_history = cycle_state.setdefault("failure_history", [])
        if not isinstance(failure_history, list):
            raise ValueError(f"Job {job.id} has invalid cycle failure history.")
        failure_history.append({"reason": failure_reason, "timestamp": now.isoformat()})

    job.checkpoint_data_json = json.dumps(checkpoints, ensure_ascii=True)


class SchedulerProcessLock:
    """
    Guarantees single-instance scheduler execution on Windows using PID tracking
    and kernel32 OpenProcess verification.
    """

    def __init__(self, lock_path: str = LOCK_FILE_PATH):
        self.lock_path = lock_path

    def _is_pid_alive(self, pid: int) -> bool:
        """Checks if a Windows process with the specified PID is currently running."""
        if pid <= 0:
            return False
        try:
            # 0x1000 = PROCESS_QUERY_LIMITED_INFORMATION
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False

    def acquire(self, allow_reentrant: bool = False) -> bool:
        """
        Attempts to acquire single-instance lock atomically using OS-level flags.
        Returns True if acquired; False if an active instance is already running.
        """
        cur_pid = os.getpid()

        # Check existing lock file if present
        if os.path.exists(self.lock_path):
            stale_lock_to_remove = False
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                locked_pid = data.get("pid")
                if locked_pid:
                    if locked_pid == cur_pid and allow_reentrant:
                        return True
                    if self._is_pid_alive(locked_pid):
                        logger.warning(
                            "[LOCK] Scheduler instance already running (PID %d). Halting duplicate launch.",
                            locked_pid,
                        )
                        return False
                    else:
                        logger.info(
                            "[LOCK] Found stale lock from dead PID %d. Reclaiming lock for PID %d.",
                            locked_pid,
                            cur_pid,
                        )
                        stale_lock_to_remove = True
                else:
                    stale_lock_to_remove = True
            except Exception as e:
                logger.warning("[LOCK] Could not read lockfile (%s); removing stale lock.", e)
                stale_lock_to_remove = True

            if stale_lock_to_remove:
                try:
                    os.remove(self.lock_path)
                except OSError:
                    pass

        # Atomic creation using OS-level flags
        try:
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "pid": cur_pid,
                        "acquired_at": datetime.utcnow().isoformat() + "Z",
                        "timezone": "Asia/Kolkata",
                    },
                    f,
                    indent=2,
                )
            return True
        except FileExistsError:
            logger.warning("[LOCK] Lock acquired concurrently by another process.")
            return False
        except Exception as e:
            logger.error("[LOCK] Failed to acquire lockfile: %s", e)
            return False

    def update_heartbeat(self) -> bool:
        """Updates the lock file with latest heartbeat timestamp."""
        if not os.path.exists(self.lock_path):
            return False
        try:
            with open(self.lock_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("pid") != os.getpid():
                return False
            data["last_heartbeat"] = datetime.utcnow().isoformat() + "Z"
            with open(self.lock_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            logger.warning("[LOCK] Could not update heartbeat: %s", e)
            return False

    def release(self):
        """Releases the lock if held by current process."""
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("pid") == os.getpid():
                        os.remove(self.lock_path)
            except Exception:
                try:
                    os.remove(self.lock_path)
                except Exception:
                    pass


def calculate_next_available_publishing_slot(
    current_dt_ist: datetime,
    cycle: ProductionCycle,
    original_target_pub_dt: datetime,
) -> Tuple[datetime, str, bool]:
    """
    Evaluates whether the original target publication slot is viable.
    If not, calculates the next available configured cycle publication slot.
    Returns (slot_datetime, slot_display_string, is_rescheduled: bool).
    """
    # Requires at least 30 minutes between current time and target publication
    min_lead_time = timedelta(minutes=30)
    if current_dt_ist + min_lead_time <= original_target_pub_dt:
        return original_target_pub_dt, f"{original_target_pub_dt.strftime('%H:%M IST (%Y-%m-%d)')}", False

    # Original target missed; assign next available cycle publishing time
    profile = get_active_profile()
    candidate_slots = []
    base_date = current_dt_ist.date()

    for day_offset in (0, 1):
        for c in profile.cycles:
            slot_dt = datetime(
                base_date.year,
                base_date.month,
                base_date.day,
                c.target_pub_hour_ist,
                c.target_pub_min_ist,
                tzinfo=IST_TZ,
            ) + timedelta(days=day_offset + c.pub_rollover_days)
            if slot_dt > current_dt_ist + min_lead_time:
                candidate_slots.append(slot_dt)

    candidate_slots.sort()
    next_slot = candidate_slots[0] if candidate_slots else (current_dt_ist + timedelta(hours=6))
    display_str = next_slot.strftime("%H:%M IST (%Y-%m-%d)")
    return next_slot, display_str, True


class StartupReconciler:
    """
    Executes startup reconciliation across missed cycles, crashed runs,
    network status, and recovery windows.
    """

    def __init__(
        self,
        lock: Optional[SchedulerProcessLock] = None,
        network_service = None,
    ):
        self.lock = lock or SchedulerProcessLock()
        self.network = network_service or network_connectivity_service

    def reconcile_on_startup(
        self,
        test_now_ist: Optional[datetime] = None,
        recovery_policy: Optional[str] = None,
        max_catch_up_cycles: int = 1,
    ) -> Dict:
        """
        Comprehensive startup reconciliation workflow:
        1. Read persistent scheduler state.
        2. Identify unexecuted scheduled cycles.
        3. Check execution and recovery windows.
        4. Inspect existing checkpoints.
        5. Check whether a job is actively running.
        6. Verify internet connectivity.
        7. Determine eligible recovery cycles.
        8. Resume or execute eligible cycles.
        9. Mark expired cycles without backfilling later slots.
        10. Send summary notifications.
        """
        now_ist = test_now_ist or datetime.now(IST_TZ)
        cur_date_str = now_ist.strftime("%Y-%m-%d")
        requested_policy = recovery_policy or get_recovery_policy()
        policy = POLICY_STRICT_WINDOW
        if requested_policy.upper() != POLICY_STRICT_WINDOW:
            logger.warning(
                "[RECONCILER] Ignoring unsupported recovery policy %s; expired cycles are never backfilled.",
                requested_policy,
            )
        profile = get_active_profile()

        logger.info("\n[RECONCILER] === EXECUTING STARTUP RECONCILIATION ===")
        logger.info("Current Time: %s | Profile: %s | Policy: %s", now_ist.isoformat(), profile.name, policy)

        report = {
            "reconciliation_time_ist": now_ist.isoformat(),
            "date": cur_date_str,
            "policy": policy,
            "profile": profile.name,
            "cycles_evaluated": len(profile.cycles),
            "recovered_cycles": [],
            "resumed_jobs": [],
            "expired_cycles": [],
            "missed_cycles": [],
            "queued_cycles": [],
            "network_status": "UNKNOWN",
        }

        # Check internet connectivity
        net_ready, net_reason, net_details = self.network.is_environment_ready_for_cycle()
        report["network_status"] = "CONNECTED" if net_ready else f"OFFLINE ({net_reason})"

        with SessionLocal() as db_session:
            # Check for any currently running, paused, interrupted, or waiting jobs in DB
            running_jobs = db_session.query(JobModel).filter(
                JobModel.status.in_(["RUNNING", "WAITING_FOR_INTERNET", "INTERRUPTED", "PAUSED", "RETRYING"])
            ).all()

            for r_job in running_jobs:
                is_interrupted_like = r_job.status in ("INTERRUPTED", "PAUSED", "RETRYING")
                # Heartbeat check (RUNNING/WAITING_FOR_INTERNET only time out on stale heartbeat unless already interrupted)
                stale_cutoff = datetime.utcnow() - timedelta(minutes=30)
                if is_interrupted_like or (r_job.heartbeat_at and r_job.heartbeat_at < stale_cutoff):
                    logger.warning("[RECONCILER] Stale crashed job detected: %s (Last heartbeat: %s).", r_job.id, r_job.heartbeat_at)
                    cycle = next(
                        (candidate for candidate in profile.cycles if r_job.id.startswith(candidate.cycle_id + "_")),
                        None,
                    )
                    cycle_start = None
                    target_publication = None
                    if cycle is not None:
                        cycle_date = datetime.strptime(r_job.date_str, "%Y-%m-%d")
                        cycle_start = cycle_date.replace(
                            hour=cycle.start_hour_ist,
                            minute=cycle.start_min_ist,
                            tzinfo=IST_TZ,
                        )
                        target_publication = cycle_start + timedelta(hours=cycle.window_hours)

                    if (
                        cycle is not None
                        and cycle_start is not None
                        and target_publication is not None
                        and not cycle.is_within_recovery_window(cycle_start, now_ist)
                    ):
                        r_job.status = "RECOVERY_WINDOW_EXPIRED"
                        r_job.recovery_reason = "RECOVERY_WINDOW_EXPIRED"
                        r_job.error_details = "Recovery window expired before startup recovery could resume this job."
                        record_cycle_state(
                            r_job,
                            cycle,
                            cycle_start,
                            target_publication,
                            "RECOVERY_WINDOW_EXPIRED",
                            now_ist,
                            r_job.error_details,
                        )
                        report["expired_cycles"].append({
                            "job_id": r_job.id,
                            "cycle_id": cycle.cycle_id,
                            "reason": "RECOVERY_WINDOW_EXPIRED",
                        })
                    else:
                        r_job.status = "RETRYING"
                        r_job.recovery_reason = "CHECKPOINT_RESUMED"
                        r_job.retry_count = (r_job.retry_count or 0) + 1
                        if cycle is not None and cycle_start is not None and target_publication is not None:
                            record_cycle_state(
                                r_job,
                                cycle,
                                cycle_start,
                                target_publication,
                                "RECOVERING",
                                now_ist,
                            )
                        report["resumed_jobs"].append(r_job.id)
                    db_session.commit()

            # Evaluate each cycle of the day
            for idx, cycle in enumerate(profile.cycles, start=1):
                cycle_start, target_pub, c_date, c_pub_date = cycle.calculate_datetimes(now_ist)
                job_id = cycle.format_job_id(now_ist)

                job = db_session.query(JobModel).filter(JobModel.id == job_id).first()

                # If job completed, nothing to reconcile
                if job and job.status in ("COMPLETED", "PUBLISHED"):
                    continue

                # Terminal cycles are never recovered or backfilled.
                if job and job.status in ("MISSED", "RECOVERY_WINDOW_EXPIRED"):
                    continue

                # If cycle is in the future
                if now_ist < cycle_start:
                    report["queued_cycles"].append({
                        "cycle_id": cycle.cycle_id,
                        "scheduled_start": cycle_start.strftime("%H:%M IST"),
                        "status": "QUEUED",
                    })
                    continue

                # Cycle start was in the past!
                recovery_end = cycle_start + timedelta(hours=cycle.recovery_window_hours)
                orig_target_str = target_pub.strftime("%H:%M IST")

                # Case A: Inside Recovery Window (e.g. 00-04, 06-10, 12-16, 18-22)
                if cycle.is_within_recovery_window(cycle_start, now_ist):
                    logger.info("[RECONCILER] Cycle %s is within recovery window (End: %s). Starting recovery.", cycle.cycle_id, recovery_end.strftime("%H:%M IST"))
                    slot_dt, slot_str, rescheduled = calculate_next_available_publishing_slot(now_ist, cycle, target_pub)

                    if not job:
                        job = JobModel(
                            id=job_id,
                            date_str=cur_date_str,
                            topic_title=f"Autonomous Recovery Cycle {cycle.cycle_id}",
                            status="RUNNING" if net_ready else "WAITING_FOR_INTERNET",
                            current_stage="TOPIC_DISCOVERY",
                            recovery_reason="STARTUP_RECONCILIATION",
                            recovery_policy=policy,
                            original_target_pub=orig_target_str,
                            revised_target_pub=slot_str if rescheduled else orig_target_str,
                            actual_start_at=datetime.utcnow(),
                            heartbeat_at=None,
                        )
                        db_session.add(job)
                    else:
                        job.status = "RUNNING" if net_ready else "WAITING_FOR_INTERNET"
                        job.recovery_reason = "STARTUP_RECONCILIATION"
                        job.recovery_policy = policy
                        job.revised_target_pub = slot_str if rescheduled else orig_target_str
                        job.heartbeat_at = None
                    record_cycle_state(
                        job,
                        cycle,
                        cycle_start,
                        target_pub,
                        "RECOVERING",
                        now_ist,
                    )
                    db_session.commit()

                    report["recovered_cycles"].append({
                        "cycle_id": cycle.cycle_id,
                        "job_id": job_id,
                        "recovery_type": "WINDOW_RECOVERY",
                        "original_target": orig_target_str,
                        "revised_target": slot_str,
                        "rescheduled": rescheduled,
                    })

                # Case B: Recovery has expired; never move this work into a later cycle.
                else:
                    reason = (
                        f"Recovery window expired at {recovery_end.isoformat()}; "
                        f"reconciliation occurred at {now_ist.isoformat()}."
                    )
                    logger.warning("[RECONCILER] Cycle %s recovery window expired.", cycle.cycle_id)
                    if not job:
                        job = JobModel(
                            id=job_id,
                            date_str=cur_date_str,
                            status="RECOVERY_WINDOW_EXPIRED",
                            current_stage="RECOVERY_WINDOW_EXPIRED",
                            recovery_reason="RECOVERY_WINDOW_EXPIRED",
                            recovery_policy=policy,
                            error_details=reason,
                        )
                        db_session.add(job)
                    else:
                        job.status = "RECOVERY_WINDOW_EXPIRED"
                        job.recovery_reason = "RECOVERY_WINDOW_EXPIRED"
                        job.recovery_policy = policy
                        job.error_details = reason
                    record_cycle_state(
                        job,
                        cycle,
                        cycle_start,
                        target_pub,
                        "RECOVERY_WINDOW_EXPIRED",
                        now_ist,
                        reason,
                    )
                    db_session.commit()

                    expired = {
                        "cycle_id": cycle.cycle_id,
                        "job_id": job_id,
                        "reason": "RECOVERY_WINDOW_EXPIRED",
                    }
                    report["expired_cycles"].append(expired)
                    report["missed_cycles"].append(expired)

        logger.info(
            "[RECONCILER] Reconciliation complete. Recovered: %d | Resumed: %d | Missed: %d | Queued: %d",
            len(report["recovered_cycles"]),
            len(report["resumed_jobs"]),
            len(report["missed_cycles"]),
            len(report["queued_cycles"]),
        )
        return report


startup_reconciler = StartupReconciler()
