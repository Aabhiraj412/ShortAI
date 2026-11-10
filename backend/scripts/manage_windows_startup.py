"""
Windows Startup & Scheduled Task Management Utility for CrazzyDocs.
Phase 5 Production Standard (Stage 3.20B).

Inspects, registers, and manages user-level Windows Scheduled Tasks for:
  - CrazzyDocs-Production-Scheduler (Autonomous 4-Cycle Scheduler Engine)
  - CrazzyDocs-Health-Dashboard     (FastAPI Server & Browser Dashboard Auto-Launch)
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional, Tuple

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from backend.app.database.db import SessionLocal, StudioSettingModel

logger = logging.getLogger(__name__)

SCHEDULER_TASK_NAME = "CrazzyDocs-Production-Scheduler"
DASHBOARD_TASK_NAME = "CrazzyDocs-Health-Dashboard"
LEGACY_TASK_NAME = "CrazzyDocsStudioScheduler"
TASK_NAME = SCHEDULER_TASK_NAME
DEFAULT_LAUNCHER_BAT = os.path.join(WORKSPACE_ROOT, "start_studio.bat")


def get_opt_in_setting() -> bool:
    """Reads opt-in setting from database."""
    try:
        with SessionLocal() as db_session:
            setting = db_session.query(StudioSettingModel).filter(
                StudioSettingModel.key == "AUTO_START_ON_WINDOWS_LOGIN"
            ).first()
            if setting:
                return setting.value.lower() in ("true", "1", "yes")
    except Exception:
        pass
    return False


def set_opt_in_setting(enabled: bool):
    """Saves opt-in preference to studio_settings."""
    try:
        with SessionLocal() as db_session:
            setting = db_session.query(StudioSettingModel).filter(
                StudioSettingModel.key == "AUTO_START_ON_WINDOWS_LOGIN"
            ).first()
            if not setting:
                setting = StudioSettingModel(
                    key="AUTO_START_ON_WINDOWS_LOGIN",
                    value="true" if enabled else "false"
                )
                db_session.add(setting)
            else:
                setting.value = "true" if enabled else "false"
            db_session.commit()
    except Exception as e:
        logger.warning("Could not persist startup setting: %s", e)


def _get_subprocess_flags() -> Dict[str, Any]:
    """Returns kwargs for subprocess calls on Windows to suppress console window creation."""
    kwargs: Dict[str, Any] = {}
    if sys.platform == "win32":
        # CREATE_NO_WINDOW (0x08000000) prevents creating a console window for CUI processes
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = startupinfo
    return kwargs


def query_task_details(task_name: str) -> Dict[str, Any]:
    """Queries schtasks /Query /TN <task_name> /FO LIST /V for granular task details."""
    cmd = ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST", "/V"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=8, **_get_subprocess_flags())
        if res.returncode != 0:
            return {
                "task_name": task_name,
                "is_registered": False,
                "status": "NOT_REGISTERED",
                "task_state": "Not Registered",
                "last_run_time": None,
                "last_result": None,
                "details": "Not registered in Windows Task Scheduler",
            }
        data = {}
        for line in res.stdout.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip()] = v.strip()

        status = data.get("Status", "Unknown")
        task_state = data.get("Scheduled Task State", "Unknown")
        last_run = data.get("Last Run Time")
        last_res = data.get("Last Result")
        has_never_run = "1999" in (last_run or "") or last_res == "267011"

        return {
            "task_name": task_name,
            "is_registered": True,
            "status": status,
            "task_state": task_state,
            "has_never_run": has_never_run,
            "last_run_time": "Never" if has_never_run else last_run,
            "last_result": last_res,
            "task_to_run": data.get("Task To Run"),
            "schedule_type": data.get("Schedule Type"),
            "details": res.stdout.strip(),
        }
    except Exception as e:
        return {
            "task_name": task_name,
            "is_registered": False,
            "status": "ERROR",
            "error": str(e),
            "details": f"Error querying task: {e}",
        }


def get_task_status(task_name: str = TASK_NAME) -> Dict:
    """Queries task status with backward compatibility for Stage 3.10 interface."""
    info = query_task_details(task_name)
    info["opt_in_enabled"] = get_opt_in_setting()
    return info


_tasks_cache: Dict[str, Any] = {}
_tasks_cache_time: float = 0.0


def get_all_tasks_status(ttl_seconds: float = 15.0) -> Dict[str, Any]:
    """Queries both CrazzyDocs production scheduled tasks with in-memory TTL caching."""
    global _tasks_cache, _tasks_cache_time
    now = time.time()
    if _tasks_cache and (now - _tasks_cache_time < ttl_seconds):
        return _tasks_cache

    s_info = query_task_details(SCHEDULER_TASK_NAME)
    d_info = query_task_details(DASHBOARD_TASK_NAME)

    # Check if either or both are registered
    any_registered = s_info.get("is_registered", False) or d_info.get("is_registered", False)

    res = {
        "task_name": SCHEDULER_TASK_NAME,
        "is_registered": any_registered,
        "opt_in_enabled": get_opt_in_setting(),
        "scheduler_task": s_info,
        "dashboard_task": d_info,
        "details": f"Scheduler: {s_info.get('status')}; Dashboard: {d_info.get('status')}",
    }
    _tasks_cache = res
    _tasks_cache_time = now
    return res


def install_startup_task(
    task_name: str = TASK_NAME,
    launcher_path: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Registers a user-level Scheduled Task that triggers when the operator logs into Windows.
    /SC ONLOGON /RL LIMITED does not require administrative elevation.
    """
    bat_path = launcher_path or DEFAULT_LAUNCHER_BAT
    if not os.path.exists(bat_path):
        return False, f"Launcher file missing at {bat_path}"

    cmd = [
        "schtasks", "/Create",
        "/TN", task_name,
        "/TR", f'"{bat_path}"',
        "/SC", "ONLOGON",
        "/RL", "LIMITED",
        "/F"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **_get_subprocess_flags())
        if res.returncode == 0:
            set_opt_in_setting(True)
            return True, f"Successfully registered Windows Scheduled Task '{task_name}'."
        else:
            return False, f"schtasks failed: {res.stderr.strip() or res.stdout.strip()}"
    except Exception as e:
        return False, f"Exception during task creation: {e}"


def uninstall_startup_task(task_name: str = TASK_NAME) -> Tuple[bool, str]:
    """Removes the Windows Scheduled Task and updates opt-in setting to False."""
    cmd = ["schtasks", "/Delete", "/TN", task_name, "/F"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **_get_subprocess_flags())
        set_opt_in_setting(False)
        if res.returncode == 0:
            return True, f"Successfully unregistered Windows Scheduled Task '{task_name}'."
        else:
            if "cannot find the file" in res.stderr.lower() or "not found" in res.stderr.lower():
                return True, f"Task '{task_name}' was not registered."
            return False, f"schtasks failed: {res.stderr.strip()}"
    except Exception as e:
        set_opt_in_setting(False)
        return False, f"Exception during task deletion: {e}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage CrazzyDocs Windows Startup Scheduled Tasks")
    parser.add_argument("--status", action="store_true", help="Check current task status")
    parser.add_argument("--install", action="store_true", help="Opt-in and register task on Windows user logon")
    parser.add_argument("--uninstall", action="store_true", help="Opt-out and unregister task")
    args = parser.parse_args()

    if args.install:
        ok, msg = install_startup_task()
        print(f"Result: {msg}")
        sys.exit(0 if ok else 1)
    elif args.uninstall:
        ok, msg = uninstall_startup_task()
        print(f"Result: {msg}")
        sys.exit(0 if ok else 1)
    else:
        st = get_all_tasks_status()
        print(json.dumps(st, indent=2))
