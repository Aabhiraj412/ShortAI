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
    from backend.app.core.paths import DATABASE_PATH
    target = db_path or DATABASE_PATH
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


def is_backend_healthy(health_url: str = "http://127.0.0.1:8000/api/dashboard/health", timeout: float = 10.0) -> bool:
    """Checks that /api/dashboard/health responds HTTP 200 with healthy database status.

    Used only as the boot readiness gate. Stage 3.32H: the aggregate endpoint routinely takes about 2 s, so the old
    2 s timeout reported a working dashboard as down; the continuous monitor now uses the liveness/readiness probes.
    """
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
    wait_timeout_sec: float = 180.0,
    dashboard_url: str = "http://127.0.0.1:8000/dashboard",
    health_url: str = "http://127.0.0.1:8000/api/dashboard/health",
) -> bool:
    """Starts the dashboard only when no instance is starting or running, waits (bounded) for its health
    check, then opens the browser at most once per Windows boot."""
    from backend.app.core import dashboard_startup as ds
    if not is_backend_healthy(health_url=health_url):
        if ds.server_instance_active():
            logger.info("[STARTUP] A dashboard instance is already starting; waiting for it instead of launching another.")
            ds.write_status("waiting_for_existing_instance", source="startup_manager")
        else:
            logger.info("[STARTUP] Launching dashboard service cleanly...")
            ds.write_status("launching", source="startup_manager")
            subprocess.Popen(
                [_pythonw_exe(), str(PROJECT_ROOT / "backend" / "scripts" / "run_dashboard.py")],
                cwd=str(PROJECT_ROOT),
                creationflags=_detached_flags(),
            )
        ds.wait_until_healthy(wait_timeout_sec, 1.0, probe=lambda: is_backend_healthy(health_url=health_url))

    healthy = is_backend_healthy(health_url=health_url)
    if healthy:
        ds.write_status("healthy", source="startup_manager")
        if open_browser and ds.open_browser_once(dashboard_url, source="startup_manager"):
            logger.info("[STARTUP] Backend healthy; opened browser at %s", dashboard_url)
        elif open_browser:
            logger.info("[STARTUP] Backend healthy; browser already opened in this Windows session.")
    else:
        logger.warning("[STARTUP] Dashboard backend did not pass health gate within %.1fs; browser suppressed.", wait_timeout_sec)
        ds.write_status("failed", source="startup_manager", reason=f"not healthy within {wait_timeout_sec:.0f}s")
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
    interval_sec: Optional[float] = None,
    max_iterations: Optional[int] = None,
    health_probe: Optional[Callable[[], bool]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    *,
    live_probe: Optional[Callable[[], Any]] = None,
    ready_probe: Optional[Callable[[], Any]] = None,
    process_alive: Optional[Callable[[], bool]] = None,
    restart_fn: Optional[Callable[[], Any]] = None,
    logs_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """Monitors dashboard liveness/readiness (Stage 3.32H) and restarts it only when it is genuinely down.

    * a slow or not-ready dashboard is DEGRADED and never restarted;
    * liveness must fail ``failure_threshold`` consecutive times (UNHEALTHY) before a restart is considered;
    * a restart happens only when no dashboard process holds the server lock (a live process is never killed);
    * restarts are capped per hour with exponential backoff; every probe, state and restart is persisted.
    """
    from backend.app.scheduler.reconciliation_service import SchedulerProcessLock
    from backend.app.core import health_monitoring as hm

    mon_lock = SchedulerProcessLock(lock_path=str(MONITOR_LOCK))
    if not mon_lock.acquire():
        logger.warning("[MONITOR] Another health monitor instance is already active. Exiting.")
        return {"iterations": 0, "restarts": 0, "skipped_duplicate": 1}

    cfg = hm.load_config()
    interval = cfg.interval_sec if interval_sec is None else interval_sec
    if health_probe is not None:            # legacy boolean probe (tests): treat it as the liveness result
        def _legacy_live():
            ok = bool(health_probe())
            return hm.ProbeResult(ok, 0.0, "ok" if ok else "probe_failed")
        live_probe = live_probe or _legacy_live
        ready_probe = ready_probe or (lambda: None)
    live_probe = live_probe or (lambda: hm.probe_liveness(cfg))
    ready_probe = ready_probe or (lambda: hm.probe_readiness(cfg))
    if process_alive is None:
        def process_alive() -> bool:
            from backend.app.core import dashboard_startup as ds
            return ds.server_instance_active()
    restart_fn = restart_fn or (lambda: ensure_dashboard_running(open_browser=False))
    sleeper = sleep_fn or time.sleep
    ledger = hm.HealthLedger(logs_dir or Path(MONITOR_LOCK).parent)   # the lock lives in the logs directory
    evaluator = hm.HealthEvaluator(cfg)
    tracker = BoundedRestartTracker(max_restarts_per_hour=cfg.max_restarts_per_hour, base_backoff_sec=cfg.base_backoff_sec)
    iterations = 0
    restarts = 0
    restart_log: list = []
    last_utc = datetime.now(timezone.utc)
    last_mono = time.monotonic()
    logger.info("[MONITOR] Started (config: %s; interval %.0fs, liveness timeout %.1fs, readiness timeout %.1fs, "
                "unhealthy after %d failures, recovery after %d successes).", cfg.source, interval,
                cfg.liveness_timeout_sec, cfg.readiness_timeout_sec, cfg.failure_threshold, cfg.recovery_threshold)

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

            live = live_probe()
            ready = ready_probe() if live.ok else None
            alive = bool(process_alive()) if not live.ok else True
            decision = evaluator.observe(live, ready, alive)
            if decision.state != decision.previous_state:
                log = logger.info if decision.state in (hm.HEALTHY, hm.DEGRADED) else logger.warning
                log("[MONITOR] Dashboard %s -> %s: %s (liveness %.0f ms, %s).", decision.previous_state,
                    decision.state, decision.reason, live.latency_ms, live.reason)
            if decision.action == hm.ACTION_RESTART:
                allowed, backoff = tracker.can_restart("dashboard")
                if allowed:
                    logger.warning("[MONITOR] Dashboard unhealthy (%s); backing off %.1fs before restart.",
                                   decision.reason, backoff)
                    sleeper(backoff)
                    if process_alive():         # re-check: a process may have appeared during the backoff
                        restart_log.append(ledger.record_restart("skipped_process_appeared", decision.reason, backoff))
                    else:
                        tracker.record_restart("dashboard")
                        ok = bool(restart_fn())
                        restarts += 1
                        restart_log.append(ledger.record_restart("restarted" if ok else "restart_not_healthy",
                                                                 decision.reason, backoff))
                else:
                    decision.action = hm.ACTION_SUPPRESSED
                    logger.error("[MONITOR] Dashboard restart cap reached (%d/hour); suppressing restart storm.",
                                 cfg.max_restarts_per_hour)
                    restart_log.append(ledger.record_restart("suppressed_cap", decision.reason, 0.0))
            ledger.record_decision(decision, cfg, restart_log)

            ensure_scheduler_running_if_authorized()
            mon_lock.update_heartbeat()
            if max_iterations is not None and iterations >= max_iterations:
                break
            sleeper(interval)
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
