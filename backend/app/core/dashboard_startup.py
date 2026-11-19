"""
Dashboard startup coordination (Stage 3.29).

Root cause this addresses (logs/dashboard_service.log, logs/startup_manager.log, 2026-11-14 15:53 logon):
three Task Scheduler entries start at the same logon — `run_dashboard.py --open-browser`,
`startup_manager.py --boot` and `startup_manager.py --monitor` — and each launched its own dashboard.
The duplicates died on `[Errno 10048]` (port already bound), the monitor treated the slow cold start
as a crash and launched again, and the real server needed ~28 s to import on a cold boot, so both
browser gates (30 s and 25 s) expired: the dashboard came up, but the browser never opened.

What this module provides, shared by every launcher:
* a server lock held for the lifetime of the dashboard process (OS file lock, released by the OS
  when the process dies, so a crash never leaves a stale lock): a second launch never starts a
  second server while one is starting or running;
* a bounded health wait (default 180 s) instead of 25-30 s;
* "open the browser once per Windows boot": an atomic marker per boot, so repeated logon tasks,
  restarts and monitor relaunches never open extra tabs;
* a persistent status file (logs/dashboard_startup_status.json) and log lines for every phase.
No credentials are read, logged or passed on command lines here.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOGS_DIR = PROJECT_ROOT / "logs"
STATUS_PATH = LOGS_DIR / "dashboard_startup_status.json"
SERVER_LOCK_PATH = LOGS_DIR / "dashboard_server.lock"
BROWSER_MARKER_DIR = LOGS_DIR / "dashboard_browser_markers"
HEALTH_URL = "http://127.0.0.1:8000/api/dashboard/health"
DASHBOARD_URL = "http://127.0.0.1:8000/dashboard"
STARTUP_TIMEOUT_SEC = 180.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ status
def write_status(phase: str, **details: Any) -> Dict[str, Any]:
    """Records the latest startup phase (launching, waiting, healthy, browser_opened, failed, ...)."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    history = (current.get("history") or [])[-19:]
    entry = {"phase": phase, "at_utc": _now_iso(), "pid": os.getpid(), **details}
    history.append(entry)
    status = {**entry, "boot_id": boot_id(), "history": history}
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
    os.replace(tmp, STATUS_PATH)
    return status


def read_status() -> Dict[str, Any]:
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"phase": "unknown"}
    data["current_boot"] = data.get("boot_id") == boot_id()
    return data


# ------------------------------------------------------------------ boot identity
def boot_id() -> str:
    """Identifier of the current Windows boot (boot time rounded to 2 minutes)."""
    try:
        if sys.platform == "win32":
            import ctypes
            uptime_ms = ctypes.windll.kernel32.GetTickCount64()
            boot = time.time() - uptime_ms / 1000.0
        else:
            with open("/proc/stat", encoding="utf-8") as f:
                boot = float(next(l for l in f if l.startswith("btime")).split()[1])
        return str(int(boot // 120) * 120)
    except Exception:
        return "unknown"


def claim_browser_open(current_boot: Optional[str] = None) -> bool:
    """True exactly once per boot across all processes (atomic exclusive create)."""
    current_boot = current_boot or boot_id()
    BROWSER_MARKER_DIR.mkdir(parents=True, exist_ok=True)
    # boot time is rounded to 2 min; accept the neighbouring bucket so rounding never re-opens a tab
    try:
        b = int(current_boot)
        for near in (str(b - 120), str(b + 120)):
            if (BROWSER_MARKER_DIR / f"{near}.marker").exists():
                return False
    except ValueError:
        pass
    marker = BROWSER_MARKER_DIR / f"{current_boot}.marker"
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(), "at_utc": _now_iso()}))
    for old in BROWSER_MARKER_DIR.glob("*.marker"):          # keep the directory small
        if old != marker and old.stat().st_mtime < time.time() - 14 * 86400:
            try:
                old.unlink()
            except OSError:
                pass
    return True


# ------------------------------------------------------------------ server lock
class ServerLock:
    """Exclusive OS-level lock held for the lifetime of the dashboard server process."""

    def __init__(self, path: Path = SERVER_LOCK_PATH):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"pid": os.getpid(), "at_utc": _now_iso()}))
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        if not self._fh:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


def server_instance_active(path: Path = SERVER_LOCK_PATH) -> bool:
    """True when a dashboard process holds the server lock (starting or running)."""
    probe = ServerLock(path)
    if probe.acquire():
        probe.release()
        return False
    return True


# ------------------------------------------------------------------ health
def is_healthy(url: str = HEALTH_URL, timeout: float = 2.0) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CrazzyDocs-Startup/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            if url.endswith("/api/dashboard/health"):
                payload = json.loads(resp.read().decode("utf-8") or "{}")
                return (payload.get("database") or {}).get("status") == "HEALTHY"
            return True
    except Exception:
        return False


def wait_until_healthy(timeout_sec: float = STARTUP_TIMEOUT_SEC, interval_sec: float = 1.0,
                       probe: Optional[Callable[[], bool]] = None,
                       sleep: Callable[[float], None] = time.sleep,
                       clock: Callable[[], float] = time.monotonic) -> bool:
    probe = probe or is_healthy
    deadline = clock() + timeout_sec
    while True:
        if probe():
            return True
        if clock() >= deadline:
            return False
        sleep(interval_sec)


def open_browser_once(url: str = DASHBOARD_URL, opener: Optional[Callable[[str], Any]] = None,
                      source: str = "") -> bool:
    """Opens the dashboard in the default browser if no launcher did so in this boot."""
    if not claim_browser_open():
        write_status("browser_already_opened_this_boot", source=source)
        return False
    try:
        if opener is None:
            import webbrowser
            opener = webbrowser.open
        opener(url)
        write_status("browser_opened", source=source, url=url)
        return True
    except Exception as exc:
        write_status("browser_failed", source=source, error=str(exc)[:300])
        return False
