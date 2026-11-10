"""
CrazzyDocs Autonomous Scheduler Daemon.
Runs the persistent Asia/Kolkata 4-cycle autonomous scheduler (00:00, 06:00, 12:00, 18:00 IST)
with single-instance process lock and startup reconciliation.
"""
import os
import sys
import time
import signal
import asyncio
import logging
from pathlib import Path

PROJECT_ROOT = Path("D:/Agents/CrazzyDocs").resolve()
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from backend.app.scheduler.scheduler import start_scheduler, scheduler, get_scheduler_status
from backend.app.scheduler.reconciliation_service import SchedulerProcessLock

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


async def periodic_heartbeat_loop(lock: SchedulerProcessLock, interval_sec: int = 60):
    """Refreshes the lockfile heartbeat every 60 seconds while daemon is running."""
    while True:
        try:
            await asyncio.sleep(interval_sec)
            lock.update_heartbeat()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("[HEARTBEAT] Heartbeat update exception: %s", e)


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
        # start_scheduler handles APScheduler configuration & startup reconciliation
        started = start_scheduler(skip_lock_for_tests=True)
        if not started:
            logger.error("Failed to start scheduler.")
            lock.release()
            sys.exit(1)

        status = get_scheduler_status()
        logger.info("Scheduler Active: Profile='%s', Timezone='%s'", status.get("active_profile"), status.get("timezone"))
        for cycle in status.get("cycles", []):
            logger.info("  -> Registered Cycle: %s (Start: %s | Pub: %s)", cycle.get("cycle_name"), cycle.get("generation", {}).get("cron_ist"), cycle.get("publishing", {}).get("cron_ist"))

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        hb_task = loop.create_task(periodic_heartbeat_loop(lock))

        # Run until interrupted
        try:
            loop.run_forever()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received. Stopping scheduler...")
        finally:
            hb_task.cancel()
            if scheduler.running:
                scheduler.shutdown(wait=False)
            lock.release()
            logger.info("Scheduler daemon shut down cleanly.")

    except Exception as e:
        logger.critical("Daemon encountered fatal exception: %s", e)
        lock.release()
        raise e

if __name__ == "__main__":
    main()
