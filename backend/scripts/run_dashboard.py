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
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
logger = logging.getLogger("crazzydocs.dashboard_runner")


def is_server_ready(url: str, timeout: float = 2.0) -> bool:
    """Check if the dashboard server is responding with HTTP 200."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CrazzyDocs-HealthCheck/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def poll_and_open_browser(target_url: str, health_url: str, max_wait_sec: float = 30.0, poll_interval: float = 0.5):
    """Wait for server to become responsive, then open browser."""
    start_time = time.time()
    logger.info(f"Waiting for dashboard to become ready at {health_url}...")
    while time.time() - start_time < max_wait_sec:
        if is_server_ready(health_url):
            logger.info(f"Dashboard server is ready! Opening browser to: {target_url}")
            try:
                webbrowser.open(target_url)
                logger.info("Browser launch request issued successfully.")
            except Exception as e:
                logger.error(f"Failed to open browser automatically: {e}")
            return
        time.sleep(poll_interval)
    logger.warning(f"Dashboard server did not become ready within {max_wait_sec}s. Skipping automatic browser launch.")


def main():
    parser = argparse.ArgumentParser(description="Run CrazzyDocs Production Dashboard Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--open-browser", action="store_true", help="Automatically open browser once ready")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    args = parser.parse_args()

    dashboard_url = f"http://{args.host}:{args.port}/dashboard"
    health_url = f"http://{args.host}:{args.port}/api/dashboard/overview"

    # Duplicate instance detection
    if is_server_ready(health_url):
        logger.info(f"CrazzyDocs Dashboard server is ALREADY running at {dashboard_url}.")
        if args.open_browser:
            logger.info(f"Opening browser to active dashboard: {dashboard_url}")
            webbrowser.open(dashboard_url)
        print(f"[OK] Dashboard is already active at: {dashboard_url}")
        return 0

    if args.open_browser:
        browser_thread = threading.Thread(
            target=poll_and_open_browser,
            args=(dashboard_url, health_url),
            daemon=True,
            name="BrowserOpenerThread",
        )
        browser_thread.start()

    logger.info(f"Starting CrazzyDocs Dashboard server on {args.host}:{args.port} (Root redirects to /dashboard)...")

    try:
        import uvicorn
        uvicorn.run(
            "backend.app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
        )
        return 0
    except Exception as e:
        logger.critical(f"Dashboard server encountered fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
