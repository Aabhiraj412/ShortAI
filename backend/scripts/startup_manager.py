"""
CrazzyDocs Windows Startup Manager & Bounded Health Monitor.
Stage 3.23.1 Autonomous Readiness Standard.

Responsibilities:
  --boot:    Validates SQLite integrity, deduplicates & starts dashboard backend,
             waits for healthy API readiness before launching the browser, and starts
             the scheduler daemon ONLY when scheduler task is enabled & mode is PRODUCTION.
             Never starts immediate ad-hoc production or backfills expired cycles.
  --monitor: Runs a single-instance background health supervisor with bounded restarts,
             exponential backoff, and Windows sleep/resume detection.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, Callable, Dict, Optional

from backend.app.core.process_utils import install_windows_hidden_subprocess_defaults

install_windows_hidden_subprocess_defaults()
import urllib.request
import webbrowser

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
STARTUP_LOG = LOGS_DIR / "startup_manager.log"
MONITOR_LOCK = LOGS_DIR / "health_monitor.lock"

handler = RotatingFileHandler(str(STARTUP_LOG), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), handler],
)
logger = logging.getLogger("crazzydocs.startup_manager")


def _pythonw_exe() -> str:
    pyw = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if pyw.exists():
        return str(pyw)
    return str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")


def _detached_flags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def verify_sqlite_database(db_path: Optional[Path] = None) -> bool:
    """Validates SQLite DB existence and PRAGMA integrity_check == 'ok'."""
    target = db_path or (PROJECT_ROOT / "backend" / "studio.db")
    if not target.exists():
        logger.error("[STARTUP] SQLite database missing at %s", target)
        return False
    try:
        conn = sqlite3.connect(str(target), timeout=5.0)
        try:
            row = conn.execute("PRAGMA integrity_check;").fetchone()
            ok = bool(row and str(row[0]).lower() == "ok")
            if not ok:
                logger.error("[STARTUP] SQLite integrity check failed: %s", row)
            return ok
        finally:
            conn.close()
    except Exception as exc:
        logger.error("[STARTUP] Failed to verify SQLite database: %s", exc)
        return False


def is_backend_healthy(health_url: str = "http://127.0.0.1:8000/api/dashboard/health", timeout: float = 2.0) -> bool:
    """Checks that /api/dashboard/health responds HTTP 200 with healthy database status."""
    try:
        req = urllib.request.Request(health_url, headers={"User-Agent": "CrazzyDocs-StartupManager/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            payload = json.loads(resp.read().decode("utf-8"))
            db_status = (payload.get("database") or {}).get("status")
            return db_status == "HEALTHY"
    except Exception:
        return False


def ensure_dashboard_running(
    open_browser: bool = False,
    wait_timeout_sec: float = 25.0,
    dashboard_url: str = "http://127.0.0.1:8000/dashboard",
    health_url: str = "http://127.0.0.1:8000/api/dashboard/health",
) -> bool:
    """Starts dashboard runner if unhealthy and opens browser only after health probe passes."""
    if not is_backend_healthy(health_url=health_url):
        logger.info("[STARTUP] Launching dashboard service cleanly...")
        subprocess.Popen(
            [_pythonw_exe(), str(PROJECT_ROOT / "backend" / "scripts" / "run_dashboard.py")],
            cwd=str(PROJECT_ROOT),
            creationflags=_detached_flags(),
        )
        deadline = time.monotonic() + wait_timeout_sec
        while time.monotonic() < deadline:
            if is_backend_healthy(health_url=health_url):
                break
            time.sleep(0.5)

    healthy = is_backend_healthy(health_url=health_url)
    if healthy and open_browser:
        logger.info("[STARTUP] Backend healthy; opening browser at %s", dashboard_url)
        try:
            webbrowser.open(dashboard_url)
        except Exception as exc:
            logger.warning("[STARTUP] Failed to launch browser: %s", exc)
    elif not healthy:
        logger.warning("[STARTUP] Dashboard backend did not pass health gate within %.1fs; browser suppressed.", wait_timeout_sec)
    return healthy


def ensure_scheduler_running_if_authorized() -> bool:
    """Starts scheduler daemon only when scheduler Windows task is enabled AND operating mode is PRODUCTION."""
    from backend.app.scheduler.reconciliation_service import SchedulerProcessLock
    from backend.app.services.control_center_service import get_operating_mode
    from backend.scripts.manage_windows_startup import SCHEDULER_TASK_NAME, query_task_details

    mode = get_operating_mode()
    if mode != "PRODUCTION":
        logger.info("[STARTUP] Operating mode is %s (not PRODUCTION); leaving scheduler daemon stopped.", mode)
        return False

    task_info = query_task_details(SCHEDULER_TASK_NAME)
    task_state = str(task_info.get("task_state") or "").lower()
    if not task_info.get("is_registered") or "disabled" in task_state:
        logger.info("[STARTUP] Windows task %s is %s; not auto-starting daemon.", SCHEDULER_TASK_NAME, task_state)
        return False

    lock = SchedulerProcessLock()
    if not lock.acquire():
        logger.info("[STARTUP] Scheduler daemon already running; skipping duplicate launch.")
        return True
    lock.release()

    logger.info("[STARTUP] Starting autonomous scheduler daemon...")
    subprocess.Popen(
        [_pythonw_exe(), str(PROJECT_ROOT / "backend" / "scripts" / "run_scheduler_daemon.py")],
        cwd=str(PROJECT_ROOT),
        creationflags=_detached_flags(),
    )
    return True


def run_startup_sequence(open_browser: bool = True, verify_db: bool = True) -> Dict[str, Any]:
    """Full safe boot sequence: DB integrity -> Dashboard health -> optional Browser & Scheduler."""
    if verify_db and not verify_sqlite_database():
        return {"status": "DB_FAILED", "dashboard_healthy": False, "scheduler_started": False}

    healthy = ensure_dashboard_running(open_browser=open_browser)
    scheduler_started = ensure_scheduler_running_if_authorized() if healthy else False
    return {
        "status": "READY" if healthy else "DEGRADED",
        "dashboard_healthy": healthy,
        "scheduler_started": scheduler_started,
    }


class BoundedRestartTracker:
    """Prevents infinite restart storms by capping restarts inside a sliding 1-hour window."""

    def __init__(self, max_restarts_per_hour: int = 3, base_backoff_sec: float = 5.0):
        self.max_restarts_per_hour = max_restarts_per_hour
        self.base_backoff_sec = base_backoff_sec
        self._history: Dict[str, list[float]] = {}

    def can_restart(self, component: str, now_mono: Optional[float] = None) -> tuple[bool, float]:
        now = time.monotonic() if now_mono is None else now_mono
        window_start = now - 3600.0
        timestamps = [ts for ts in self._history.get(component, []) if ts >= window_start]
        self._history[component] = timestamps
        if len(timestamps) >= self.max_restarts_per_hour:
            return False, 0.0
        backoff = self.base_backoff_sec * (3 ** len(timestamps))
        return True, backoff

    def record_restart(self, component: str, now_mono: Optional[float] = None) -> None:
        now = time.monotonic() if now_mono is None else now_mono
        timestamps = self._history.setdefault(component, [])
        timestamps.append(now)


def run_health_monitor_loop(
    interval_sec: float = 30.0,
    max_iterations: Optional[int] = None,
    health_probe: Optional[Callable[[], bool]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
) -> Dict[str, int]:
    """Monitors dashboard health and optionally restarts crashed components with bounded backoff."""
    from backend.app.scheduler.reconciliation_service import SchedulerProcessLock

    mon_lock = SchedulerProcessLock(lock_path=str(MONITOR_LOCK))
    if not mon_lock.acquire():
        logger.warning("[MONITOR] Another health monitor instance is already active. Exiting.")
        return {"iterations": 0, "restarts": 0, "skipped_duplicate": 1}

    probe = health_probe or is_backend_healthy
    sleeper = sleep_fn or time.sleep
    tracker = BoundedRestartTracker(max_restarts_per_hour=3, base_backoff_sec=5.0)
    iterations = 0
    restarts = 0
    last_utc = datetime.now(timezone.utc)
    last_mono = time.monotonic()

    try:
        while max_iterations is None or iterations < max_iterations:
            iterations += 1
            now_mono = time.monotonic()
            now_utc = datetime.now(timezone.utc)
            wall_jump = (now_utc - last_utc).total_seconds() - (now_mono - last_mono)
            last_mono = now_mono
            last_utc = now_utc
            if wall_jump > 90.0:
                logger.info("[MONITOR] Sleep/resume clock jump detected (%.1fs); re-verifying health.", wall_jump)

            if not probe():
                allowed, backoff = tracker.can_restart("dashboard")
                if allowed:
                    logger.warning("[MONITOR] Dashboard unhealthy; backing off %.1fs before restart.", backoff)
                    sleeper(backoff)
                    tracker.record_restart("dashboard")
                    ensure_dashboard_running(open_browser=False)
                    restarts += 1
                else:
                    logger.error("[MONITOR] Dashboard restart cap reached (3/hour); suppressing restart storm.")

            ensure_scheduler_running_if_authorized()
            mon_lock.update_heartbeat()
            if max_iterations is not None and iterations >= max_iterations:
                break
            sleeper(interval_sec)
    finally:
        mon_lock.release()

    return {"iterations": iterations, "restarts": restarts, "skipped_duplicate": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CrazzyDocs Startup Manager & Health Monitor")
    parser.add_argument("--boot", action="store_true", help="Run startup sequence cleanly")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser window on boot")
    parser.add_argument("--monitor", action="store_true", help="Run continuous bounded health monitor loop")
    args = parser.parse_args()

    if args.monitor:
        run_health_monitor_loop()
    else:
        res = run_startup_sequence(open_browser=not args.no_browser)
        print(json.dumps(res, indent=2))
