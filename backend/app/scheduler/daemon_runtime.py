"""
Scheduler daemon runtime loops (importable and testable; no process-level side effects).

backend/scripts/run_scheduler_daemon.py owns process setup (paths, logging, the
single-instance lock) and calls run_daemon(). Controlled restart: the recovery loop
exits only through deployment_diagnostics.should_exit_for_restart(), i.e. when a
restart was requested and no production job is QUEUED/RUNNING/RETRYING. The check and
the exit happen without an await in between, so no dispatch can start in the gap.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from backend.app.scheduler.reconciliation_service import SchedulerProcessLock
from backend.app.scheduler.scheduler import (
    dispatch_eligible_recoveries,
    get_scheduler_status,
    reload_profile_from_db_if_changed,
    scheduler,
    start_scheduler,
)
from backend.app.services import deployment_diagnostics

logger = logging.getLogger("crazzydocs_daemon")


class ControlledRestart(Exception):
    """Raised at a safe point to exit so the startup monitor relaunches current code."""


async def periodic_heartbeat_loop(lock: SchedulerProcessLock, interval_sec: float = 60):
    """Refreshes the lockfile and deployment heartbeats while the daemon is running."""
    while True:
        try:
            await asyncio.sleep(interval_sec)
            lock.update_heartbeat()
            deployment_diagnostics.record_daemon_heartbeat()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("[HEARTBEAT] Heartbeat update exception: %s", e)


async def periodic_recovery_and_sleep_loop(interval_sec: float = 45, tick_seconds: float = 10):
    """Detects sleep/resume jumps, reloads DB schedule updates, runs bounded recovery checks."""
    last_mono = time.monotonic()
    last_utc = datetime.now(timezone.utc)
    ticks = 0
    while True:
        try:
            await asyncio.sleep(tick_seconds)
            ticks += 1
            reload_profile_from_db_if_changed()
            if ticks * tick_seconds < interval_sec:
                continue
            ticks = 0
            now_mono = time.monotonic()
            now_utc = datetime.now(timezone.utc)
            wall_jump = (now_utc - last_utc).total_seconds() - (now_mono - last_mono)
            last_mono = now_mono
            last_utc = now_utc
            if wall_jump > 90.0:
                logger.warning(
                    "[SLEEP_RESUME] Detected %.1fs system sleep/clock jump; reconciling active recovery windows.",
                    wall_jump,
                )
            exit_now, why = deployment_diagnostics.should_exit_for_restart()
            if exit_now:
                deployment_diagnostics.clear_restart_request()
                logger.warning("[RESTART] Controlled restart at safe point (%s). Exiting for relaunch.", why)
                raise ControlledRestart(why)
            if deployment_diagnostics.pending_restart_request():
                logger.info("[RESTART] Restart requested; %s.", why)
            await dispatch_eligible_recoveries()
        except asyncio.CancelledError:
            break
        except ControlledRestart:
            raise
        except Exception as e:
            logger.warning("[RECOVERY_LOOP] Cycle recovery check warning: %s", e)


async def run_daemon(
    lock: SchedulerProcessLock,
    *,
    heartbeat_interval: float = 60,
    recovery_interval: float = 45,
    tick_seconds: float = 10,
) -> int:
    """Starts APScheduler in the running loop and runs heartbeat/recovery tasks. Returns an exit code."""
    started = start_scheduler(skip_lock_for_tests=True)
    if not started:
        logger.error("Failed to start scheduler.")
        return 1

    record = deployment_diagnostics.register_daemon_start()
    logger.info("[DEPLOYMENT] PID %s loaded code revision %s", record["pid"], record["loaded_code_revision"])
    lock.update_heartbeat()
    status = get_scheduler_status()
    logger.info("Scheduler Active: Profile='%s', Timezone='%s'", status.get("active_profile"), status.get("timezone"))
    for cycle in status.get("cycles", []):
        logger.info(
            "  -> Registered Cycle: %s (Start: %s | Recovery: %s min)",
            cycle.get("cycle_name"),
            cycle.get("generation", {}).get("cron_ist"),
            cycle.get("recovery_duration_minutes"),
        )

    hb_task = asyncio.create_task(periodic_heartbeat_loop(lock, heartbeat_interval))
    rec_task = asyncio.create_task(periodic_recovery_and_sleep_loop(recovery_interval, tick_seconds))
    try:
        await asyncio.gather(hb_task, rec_task)
    except ControlledRestart:
        logger.info("Controlled restart: stopping scheduler cleanly...")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received. Stopping scheduler...")
    finally:
        rec_task.cancel()
        hb_task.cancel()
        if scheduler.running:
            scheduler.shutdown(wait=False)
        logger.info("Scheduler daemon shut down cleanly.")
    return 0
