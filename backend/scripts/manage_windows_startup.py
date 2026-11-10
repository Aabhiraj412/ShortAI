"""
Windows Startup & Scheduled Task Management Utility for CrazzyDocs.
Phase 5, Stage 3.10 Production Standard.

Registers or unregisters an opt-in user-level Windows Scheduled Task to launch
the autonomous studio scheduler on Windows user logon.
Does not require admin privileges, modify Windows Registry, or alter execution policies.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from typing import Dict, Optional, Tuple

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from backend.app.database.db import SessionLocal, StudioSettingModel

logger = logging.getLogger(__name__)

TASK_NAME = "CrazzyDocsStudioScheduler"
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


def get_task_status(task_name: str = TASK_NAME) -> Dict:
    """Queries Windows schtasks.exe for task registration status."""
    cmd = ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        is_registered = (res.returncode == 0)
        return {
            "task_name": task_name,
            "is_registered": is_registered,
            "opt_in_enabled": get_opt_in_setting(),
            "details": res.stdout.strip() if is_registered else "Not registered in Windows Task Scheduler",
        }
    except Exception as e:
        return {
            "task_name": task_name,
            "is_registered": False,
            "opt_in_enabled": get_opt_in_setting(),
            "error": str(e),
        }


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
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
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
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        set_opt_in_setting(False)
        if res.returncode == 0:
            return True, f"Successfully unregistered Windows Scheduled Task '{task_name}'."
        else:
            # If task was not found, treat as already uninstalled
            if "cannot find the file" in res.stderr.lower() or "not found" in res.stderr.lower():
                return True, f"Task '{task_name}' was not registered."
            return False, f"schtasks failed: {res.stderr.strip()}"
    except Exception as e:
        set_opt_in_setting(False)
        return False, f"Exception during task deletion: {e}"


if __name__ == "__main__":
    from typing import Tuple
    parser = argparse.ArgumentParser(description="Manage CrazzyDocs Windows Startup Scheduled Task")
    parser.add_argument("--status", action="store_true", help="Check current task status")
    parser.add_argument("--install", action="store_true", help="Opt-in and register task on Windows user logon")
    parser.add_argument("--uninstall", action="store_true", help="Opt-out and unregister task")
    args = parser.parse_args()

    if args.install:
        ok, msg = install_startup_task()
        print(f"Result: {msg}")
        exit(0 if ok else 1)
    elif args.uninstall:
        ok, msg = uninstall_startup_task()
        print(f"Result: {msg}")
        exit(0 if ok else 1)
    else:
        st = get_task_status()
        print(json.dumps(st, indent=2))
