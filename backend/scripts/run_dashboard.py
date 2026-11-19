"""CrazzyDocs Production Dashboard Server Runner.

Starts the Uvicorn ASGI server hosting the FastAPI backend and browser dashboard.
Supports:
  --port <int> (default: 8000)
  --host <str> (default: 127.0.0.1)
  --open-browser: Polls for server readiness and automatically launches the dashboard in default browser
  --reload: Enable auto-reload for development

Logs output to stdout and logs/dashboard_service.log.
Handles duplicate instance detection: if server is already running on the target port,
--open-browser simply launches the browser and exits cleanly without port conflict.
"""

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_ROOT_HINT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_HINT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_HINT))

from backend.app.core.process_utils import install_windows_hidden_subprocess_defaults

install_windows_hidden_subprocess_defaults()
from typing import Any, Dict

# pythonw.exe execution guard: sys.stdout and sys.stderr are None in windowless mode
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
backend_path = str(PROJECT_ROOT / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
os.chdir(PROJECT_ROOT)
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "dashboard_service.log"
ACCESS_LOG_FILE = LOGS_DIR / "dashboard_access.log"
ERROR_LOG_FILE = LOGS_DIR / "dashboard_errors.log"


class RoutinePollingAccessFilter(logging.Filter):
    """
    Suppresses noisy routine HTTP 200/304 GET polling access lines on /api/dashboard/*
    and /static/* while ALWAYS preserving errors, POST/PUT/DELETE mutations, and
    non-200 responses so real issues are never hidden.
    """

    QUIET_PREFIXES = (
        "/api/dashboard/overview",
        "/api/dashboard/stats",
        "/api/dashboard/health",
        "/api/dashboard/history",
        "/api/dashboard/recovery",
        "/api/dashboard/activity",
        "/api/dashboard/progress",
        "/static/",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            if isinstance(args, tuple) and len(args) >= 5:
                method = str(args[1]).upper()
                path = str(args[2]).split("?")[0]
                status_code = int(args[4])
                if status_code >= 400 or method != "GET":
                    return True
                if path.startswith(self.QUIET_PREFIXES):
                    return False
                return True
            msg = record.getMessage()
            if '"GET /api/dashboard/' in msg and ('" 200' in msg or '" 304' in msg):
                return False
        except Exception:
            return True
        return True


def build_uvicorn_log_config(logs_dir: Path = LOGS_DIR) -> Dict[str, Any]:
    """Builds a structured rotating logging configuration for Uvicorn & CrazzyDocs."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "quiet_polling": {
                "()": "backend.scripts.run_dashboard.RoutinePollingAccessFilter",
            },
        },
        "formatters": {
            "standard": {
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(asctime)s [ACCESS] %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "level": "INFO",
                "formatter": "standard",
            },
            "app_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(logs_dir / "dashboard_service.log"),
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 5,
                "encoding": "utf-8",
                "level": "INFO",
                "formatter": "standard",
            },
            "error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(logs_dir / "dashboard_errors.log"),
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 5,
                "encoding": "utf-8",
                "level": "WARNING",
                "formatter": "standard",
            },
            "access_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(logs_dir / "dashboard_access.log"),
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 3,
                "encoding": "utf-8",
                "level": "INFO",
                "formatter": "access",
                "filters": ["quiet_polling"],
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["console", "app_file", "error_file"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["console", "app_file", "error_file"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access_file"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }


_err_handler = RotatingFileHandler(str(ERROR_LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
_err_handler.setLevel(logging.WARNING)
_app_handler = RotatingFileHandler(str(LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
_app_handler.setLevel(logging.INFO)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        _app_handler,
        _err_handler,
    ],
)
logger = logging.getLogger("crazzydocs.dashboard_runner")


def is_port_in_use(host: str, port: int) -> bool:
    """Check if the TCP port is already open/listening at the socket layer."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def is_server_ready(url: str, timeout: float = 2.0) -> bool:
    """Check if the dashboard server is responding with HTTP 200."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CrazzyDocs-HealthCheck/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def poll_and_open_browser(target_url: str, health_url: str, max_wait_sec: float = 180.0, poll_interval: float = 1.0):
    """Wait (bounded) for the server to answer its health check, then open the browser once per boot.
    A cold Windows boot needed ~28 s before the server answered (2026-11-14), so 30 s was too short."""
    from backend.app.core import dashboard_startup as ds
    logger.info(f"Waiting up to {max_wait_sec:.0f}s for dashboard health at {health_url}...")
    ds.write_status("waiting_for_health", source="run_dashboard", timeout_sec=max_wait_sec)
    if not ds.wait_until_healthy(max_wait_sec, poll_interval, probe=lambda: is_server_ready(health_url)):
        logger.warning(f"Dashboard server did not become ready within {max_wait_sec}s. Skipping automatic browser launch.")
        ds.write_status("failed", source="run_dashboard", reason=f"not healthy within {max_wait_sec:.0f}s")
        return
    ds.write_status("healthy", source="run_dashboard")
    if ds.open_browser_once(target_url, source="run_dashboard"):
        logger.info(f"Dashboard server is ready; opened browser to {target_url}")
    else:
        logger.info("Dashboard server is ready; browser already opened in this Windows session.")


def main():
    parser = argparse.ArgumentParser(description="Run CrazzyDocs Production Dashboard Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--open-browser", action="store_true", help="Automatically open browser once ready")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    args = parser.parse_args()

    dashboard_url = f"http://{args.host}:{args.port}/dashboard"
    health_url = f"http://{args.host}:{args.port}/api/dashboard/overview"

    from backend.app.core import dashboard_startup as ds

    # 1. Non-destructive port verification: check if CrazzyDocs dashboard is already responding
    if is_server_ready(health_url):
        logger.info(f"CrazzyDocs Dashboard server is ALREADY running and healthy at {dashboard_url}.")
        if args.open_browser:
            ds.open_browser_once(dashboard_url, source="run_dashboard(existing)")
        print(f"[OK] Dashboard is already active at: {dashboard_url}")
        return 0

    # 1b. Single instance: another launcher is already starting (or running) the server. Never start a
    # second one; at most wait for it and open the browser once.
    server_lock = ds.ServerLock()
    if not server_lock.acquire():
        logger.info("Another dashboard instance holds the server lock (starting or running); not launching a duplicate.")
        ds.write_status("duplicate_launch_skipped", source="run_dashboard")
        if args.open_browser:
            poll_and_open_browser(dashboard_url, health_url)
        return 0

    # 2. Check if the port is bound by another process (do not terminate, notify user)
    if is_port_in_use(args.host, args.port):
        logger.warning(
            f"Port {args.port} is currently in use on {args.host}, but not responding with HTTP 200 to {health_url}. "
            "Halting startup to prevent port conflict. Please verify port ownership rather than terminating processes."
        )
        print(f"[WARN] Port {args.port} is already occupied on {args.host}. Aborting launch without terminating processes.")
        return 1

    if args.open_browser:
        browser_thread = threading.Thread(
            target=poll_and_open_browser,
            args=(dashboard_url, health_url),
            daemon=True,
            name="BrowserOpenerThread",
        )
        browser_thread.start()

    logger.info(f"Starting CrazzyDocs Dashboard server on {args.host}:{args.port} (Root redirects to /dashboard)...")
    ds.write_status("launching", source="run_dashboard", port=args.port)

    try:
        import uvicorn
        uvicorn.run(
            "backend.app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
            log_config=build_uvicorn_log_config(LOGS_DIR),
        )
        return 0
    except Exception as e:
        logger.critical(f"Dashboard server encountered fatal error: {e}", exc_info=True)
        ds.write_status("failed", source="run_dashboard", reason=f"{type(e).__name__}: {str(e)[:300]}")
        return 1
    finally:
        server_lock.release()


if __name__ == "__main__":
    sys.exit(main())
