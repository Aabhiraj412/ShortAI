"""
CrazzyDocs Autonomous Scheduler Daemon.
Runs the persistent Asia/Kolkata 4-cycle autonomous scheduler (00:00, 06:00, 12:00, 18:00 IST)
with single-instance process lock and startup reconciliation.
"""
import os
import sys

# pythonw.exe execution guard: sys.stdout and sys.stderr are None in windowless mode
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from datetime import datetime, timezone
import time
import signal
import asyncio
import logging
from pathlib import Path

PROJECT_ROOT = Path("D:/Agents/CrazzyDocs").resolve()
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from backend.app.core.process_utils import install_windows_hidden_subprocess_defaults

install_windows_hidden_subprocess_defaults()

from backend.app.scheduler.scheduler import (
    dispatch_eligible_recoveries,
    get_scheduler_status,
    reload_profile_from_db_if_changed,
    scheduler,
    start_scheduler,
)
from backend.app.scheduler.reconciliation_service import SchedulerProcessLock
from backend.app.services import deployment_diagnostics

logs_dir = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(logs_dir, exist_ok=True)
log_file = os.path.join(logs_dir, "scheduler_daemon.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8")
    ]
)
logger = logging.getLogger("crazzydocs_daemon")


class ControlledRestart(Exception):
    """Raised at a safe point to exit so the startup monitor relaunches current code."""


async def periodic_heartbeat_loop(lock: SchedulerProcessLock, interval_sec: int = 60):
    """Refreshes the lockfile and deployment heartbeats every 60 seconds while daemon is running."""
    while True:
        try:
            await asyncio.sleep(interval_sec)
            lock.update_heartbeat()
            deployment_diagnostics.record_daemon_heartbeat()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("[HEARTBEAT] Heartbeat update exception: %s", e)


async def periodic_recovery_and_sleep_loop(interval_sec: int = 45):
    """Detects Windows sleep/resume jumps, reloads DB schedule updates, and runs bounded recovery checks."""
    last_mono = time.monotonic()
    last_utc = datetime.now(timezone.utc)
    ticks = 0
    while True:
        try:
            await asyncio.sleep(10)
            ticks += 1
            reload_profile_from_db_if_changed()
            if ticks * 10 < interval_sec:
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
            # Controlled restart: only between dispatches, and only when no
            # production job is queued or running.
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


async def _run_daemon(lock: SchedulerProcessLock) -> int:
    """Starts APScheduler inside the active asyncio loop and runs heartbeat/recovery tasks."""
    started = start_scheduler(skip_lock_for_tests=True)
    if not started:
        logger.error("Failed to start scheduler.")
        return 1

    record = deployment_diagnostics.register_daemon_start()
    logger.info(
        "[DEPLOYMENT] PID %s loaded code revision %s",
        record["pid"],
        record["loaded_code_revision"],
    )
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

    hb_task = asyncio.create_task(periodic_heartbeat_loop(lock))
    rec_task = asyncio.create_task(periodic_recovery_and_sleep_loop())
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


def main():
    logger.info("==================================================")
    logger.info("STARTING CRAZZYDOCS AUTONOMOUS SCHEDULER DAEMON")
    logger.info("Project Root: %s", PROJECT_ROOT)
    logger.info("==================================================")

    lock = SchedulerProcessLock()
    if not lock.acquire():
        logger.error("Another scheduler daemon instance is already running. Exiting.")
        sys.exit(1)

    try:
        exit_code = asyncio.run(_run_daemon(lock))
        lock.release()
        if exit_code:
            sys.exit(exit_code)
    except (KeyboardInterrupt, SystemExit):
        lock.release()
    except Exception as e:
        logger.critical("Daemon encountered fatal exception: %s", e)
        lock.release()
        raise e

if __name__ == "__main__":
    main()
