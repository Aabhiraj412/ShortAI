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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("crazzydocs_daemon")

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
        logger.info("Scheduler Active: Profile='%s', Timezone='%s'", status.get("profile"), status.get("timezone"))
        for job in status.get("jobs", []):
            logger.info("  -> Registered Job: %s (Next Run: %s)", job.get("name"), job.get("next_run_time"))

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Run until interrupted
        try:
            loop.run_forever()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received. Stopping scheduler...")
        finally:
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
