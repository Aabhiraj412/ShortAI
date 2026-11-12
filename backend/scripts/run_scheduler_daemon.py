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

import asyncio
import logging
from pathlib import Path

PROJECT_ROOT = Path("D:/Agents/CrazzyDocs").resolve()
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from backend.app.core.process_utils import install_windows_hidden_subprocess_defaults

install_windows_hidden_subprocess_defaults()

from backend.app.scheduler.daemon_runtime import run_daemon
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
        exit_code = asyncio.run(run_daemon(lock))
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
